"""Build the restoration tree artifact and the per-premise line context (spec-03 design D4).

Inputs are the clean `device_dim` and `premise_dim` tables (salted keys only). Outputs:

* `restoration_tree.json`: arrays only (parent, depth, served, level per circuit). No device keys, circuit labels,
  coordinates or node counts below 10.
* `pc` and `pn` on each scored premise row of `premise_risk.jsonl`: the opaque circuit index and the node index of the
  premise's transformer (or of the nearest node that survived merging). `ap` marks a premise whose chain differs from
  the tree (an alternate feed).
* `cn`, `n0`, `nl`, `nf` on those rows: the exposure counts the v1 restoration engine and the fallback path use.

Build time only (needs pyarrow). The API never imports this module.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from concierge.restoration import config

TREE_FILE = "restoration_tree.json"
_MAX_CHAIN = 64


def node_level(device_class: str, device_type: str, depth: int) -> str:
    """tree-v1 node-level rule (docs/restoration-tree.md).

    service: a distribution transformer (the last device before the homes).
    feeder:  a line device (breaker, fuse, recloser, sectionalizer, step transformer) at depth 0 or 1, the trunk
             leaving the substation and the first protected sections.
    lateral: every other line device, deeper in the tree.

    `is_protective` is not needed: every isolating device in device_dim is protective, and the only non-protective
    line device is the step transformer, which the depth rule already places.
    """
    if device_class == "transformer" and device_type != "Step":
        return "service"
    return "feeder" if depth <= config.FEEDER_MAX_DEPTH else "lateral"


def build_tree(devices: Iterable[Mapping], premises: Iterable[Mapping],
               min_served: int = config.MIN_SERVED) -> tuple[dict, dict[str, tuple[int, int]], dict]:
    """Return (tree document, {premise_token: (circuit index, node index)}, stats).

    A device that serves fewer than `min_served` premises is merged into its parent: it does not appear in the tree
    and its premises attach to the nearest ancestor that does. A circuit whose breaker serves fewer than `min_served`
    premises is dropped, and its premises get no position.
    """
    by_key = {d["device_key"]: d for d in devices}
    circuits: dict[int, list[Mapping]] = defaultdict(list)
    for d in by_key.values():
        circuits[d["circuit"]].append(d)

    stats = {"circuits": 0, "circuits_suppressed": 0, "devices": len(by_key), "nodes": 0, "merged_devices": 0}
    kept: list[tuple[str, list[Mapping]]] = []                # (root device_key, devices of the circuit)
    for devs in circuits.values():
        roots = [d for d in devs if d["parent_key"] is None]
        if len(roots) != 1 or roots[0]["device_class"] != "breaker":
            raise ValueError("every circuit needs exactly one breaker at the top of its device tree")
        if roots[0]["n_premises_served"] < min_served:
            stats["circuits_suppressed"] += 1
            continue
        kept.append((roots[0]["device_key"], devs))
    kept.sort(key=lambda t: t[0])             # opaque order: salted device keys, unrelated to the circuit number

    doc: dict = {"version": config.TREE_VERSION, "circuits": {}}
    anchor: dict[str, tuple[int, int]] = {}                  # device_key -> (circuit index, node index) when it survives
    nearest: dict[str, str] = {}                             # device_key -> nearest surviving ancestor-or-self
    for pc, (_, devs) in enumerate(kept):
        merged_depth: dict[str, int] = {}
        survivors: list[Mapping] = []
        for d in sorted(devs, key=lambda x: (x["depth"], x["device_key"])):
            k, pk = d["device_key"], d["parent_key"]
            if pk is not None:
                if pk not in by_key or by_key[pk]["circuit"] != d["circuit"]:
                    raise ValueError("a device's parent must be on the same circuit")
                if by_key[pk]["depth"] >= d["depth"]:
                    raise ValueError("a device must be deeper than its parent")
            if d["n_premises_served"] >= min_served and (pk is None or pk in nearest):
                nearest[k] = k
                merged_depth[k] = 0 if pk is None else merged_depth[nearest[pk]] + 1
                survivors.append(d)
            else:
                stats["merged_devices"] += 1
                if pk is not None and pk in nearest:
                    nearest[k] = nearest[pk]
        survivors.sort(key=lambda x: (merged_depth[x["device_key"]], x["device_key"]))
        index = {d["device_key"]: i for i, d in enumerate(survivors)}
        parent, depth, served, level = [], [], [], []
        for d in survivors:
            k, pk = d["device_key"], d["parent_key"]
            parent.append(-1 if pk is None else index[nearest[pk]])
            depth.append(merged_depth[k])
            served.append(int(d["n_premises_served"]))
            level.append(node_level(d["device_class"], d["device_type"], d["depth"]))
        for k in index:
            anchor[k] = (pc, index[k])
        doc["circuits"][str(pc)] = {"parent": parent, "depth": depth, "served": served, "level": level}
        stats["nodes"] += len(survivors)
    stats["circuits"] = len(kept)

    assigned: dict[str, tuple[int, int]] = {}
    for p in premises:
        tk = p.get("transformer_key")
        if tk is not None and tk in nearest:
            assigned[p["premise_token"]] = anchor[nearest[tk]]
    stats["premises_positioned"] = len(assigned)
    return doc, assigned, stats


def check_tree_doc(doc: Mapping, min_served: int = config.MIN_SERVED) -> None:
    """Raise if the document breaks a rule of the artifact: structure, order, level names, small groups."""
    if doc.get("version") != config.TREE_VERSION or set(doc) != {"version", "circuits"}:
        raise ValueError("unexpected tree document keys or version")
    for key, c in doc["circuits"].items():
        if not key.isdigit() or set(c) != {"parent", "depth", "served", "level"}:
            raise ValueError("unexpected circuit entry")
        n = len(c["parent"])
        if not (n and len(c["depth"]) == len(c["served"]) == len(c["level"]) == n):
            raise ValueError("array lengths differ")
        if c["parent"][0] != -1 or any(not (0 <= c["parent"][i] < i) for i in range(1, n)):
            raise ValueError("parents must precede children and node 0 must be the root")
        if any(c["depth"][i] != c["depth"][c["parent"][i]] + 1 for i in range(1, n)) or c["depth"][0] != 0:
            raise ValueError("depth must equal parent depth + 1")
        if any(s < min_served for s in c["served"]):
            raise ValueError(f"a node serves fewer than {min_served} premises")
        if any(lv not in config.LEVELS for lv in c["level"]):
            raise ValueError("unknown level name")


# ---- reading the clean tables and updating the risk rows ---------------------------------------------------------------
def line_context(devices: Mapping[str, Mapping], key: str | None, cache: dict) -> tuple[int, int, int, int] | None:
    """(cn, n0, nl, nf) for the chain above a transformer: homes on the circuit, on its transformer, on its nearest
    protective device and on its farthest protective device before the breaker."""
    if key is None:
        return None
    if key not in cache:
        chain, k = [], key
        while k is not None and len(chain) < _MAX_CHAIN:
            chain.append(devices[k])
            k = devices[k]["parent_key"]
        if not chain or chain[-1]["device_class"] != "breaker":
            cache[key] = None
        else:
            prot = [d["n_premises_served"] for d in chain if d["is_protective"]]
            cn = chain[-1]["n_premises_served"]
            cache[key] = (cn, chain[0]["n_premises_served"], prot[0] if prot else cn, prot[-1] if prot else cn)
    return cache[key]


def enrich_risk_rows(tables_dir: str | Path, risk_dir: str | Path) -> dict:
    """Add line context and tree position to every scored premise_risk row and write restoration_tree.json.

    Server-side use only: cn, n0, nl, nf, pc and pn are weights and positions, and the API never returns them.
    Premises without a chain, or on fallback, get no context and no position.
    """
    import pyarrow.parquet as pq

    tables, risk = Path(tables_dir), Path(risk_dir)
    devices = pq.read_table(
        tables / "device_dim.parquet",
        columns=["device_key", "device_class", "device_type", "parent_key", "circuit", "depth", "is_protective",
                 "n_premises_served"]).to_pylist()
    dev = {d["device_key"]: d for d in devices}
    pd = pq.read_table(tables / "premise_dim.parquet",
                       columns=["premise_token", "transformer_key", "alternate_path"]).to_pylist()
    prem = {p["premise_token"]: p for p in pd}

    doc, positions, stats = build_tree(devices, pd)
    check_tree_doc(doc)

    cache: dict = {}
    out, n_ctx, n_pos = [], 0, 0
    for line in open(risk / "premise_risk.jsonl", encoding="utf-8"):
        row = json.loads(line)
        p = prem.get(row["t"]) if row.get("st") == "ok" else None
        ctx = line_context(dev, p["transformer_key"], cache) if p else None
        if ctx:
            row.update(cn=ctx[0], n0=ctx[1], nl=ctx[2], nf=ctx[3])
            n_ctx += 1
            pos = positions.get(row["t"])
            if pos:
                row.update(pc=pos[0], pn=pos[1])
                n_pos += 1
                if p["alternate_path"]:
                    row["ap"] = 1
        out.append(json.dumps(row, sort_keys=True))
    (risk / "premise_risk.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8")
    (risk / TREE_FILE).write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"rows": len(out), "with_line_context": n_ctx, "with_tree_position": n_pos,
            "tree_circuits": stats["circuits"], "tree_nodes": stats["nodes"],
            "circuits_suppressed": stats["circuits_suppressed"], "merged_devices": stats["merged_devices"]}
