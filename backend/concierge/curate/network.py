"""Topology -> device tree + premise table, with salted keys and hex cells."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from concierge.risk.load import PROTECTIVE_CLASS, PROTECTIVE_TYPES, normalize_circuit
from concierge.risk.tokens import make_token

from .ids import device_key

CLASS_NAMES = {"eo_power_xfrmr_inst": "transformer", "eo_isolating_eqpt_inst": "isolating_device",
               "eo_circuit": "breaker"}
MIN_GROUP = 10


def parse_chain(line: str) -> tuple[str, list[tuple[str, str, str]]] | None:
    """premise, [(class, id, type), ...] ordered from the transformer up to the breaker."""
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < 3 or not parts[0].strip():
        return None
    chain = []
    for dev in parts[1:]:
        f = dev.split("*")
        if len(f) < 4:
            return None
        chain.append((f[0], f[1], f[2]))
    if chain[-1][0] != "eo_circuit" or normalize_circuit(parts[-1].split("*")[-1]) is None:
        return None
    return parts[0].strip(), chain


def build_network(
    topology_path: str | Path, customers: Mapping[str, dict], salt: bytes,
) -> tuple[list[dict], list[dict], dict]:
    """Return (premise rows, device rows, quality). `customers` maps raw premise id -> {circuit, service, cell, res}.

    Raw premise numbers and raw device ids are replaced by salted keys before leaving this function.
    """
    meta: dict[str, dict] = {}                 # device_key -> class/type/circuit
    votes: dict[str, Counter] = {}             # device_key -> Counter((parent_key, depth))
    served: Counter[str] = Counter()
    pending: list[tuple[str, list[str], dict]] = []   # (premise id, device keys, partial premise row)
    q = Counter()
    seen: set[str] = set()

    with open(topology_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            parsed = parse_chain(line)
            if parsed is None:
                q["malformed_lines"] += 1
                continue
            pid, chain = parsed
            circuit = normalize_circuit(line.rstrip().split("\t")[-1].split("*")[-1])
            keys = [device_key(salt, c, i) for c, i, _ in chain]
            n = len(chain)
            for pos, ((cls, _, typ), k) in enumerate(zip(chain, keys)):
                served[k] += 1
                meta.setdefault(k, {"device_key": k, "device_class": CLASS_NAMES.get(cls, cls), "device_type": typ,
                                    "circuit": circuit,
                                    "is_protective": cls == PROTECTIVE_CLASS and typ in PROTECTIVE_TYPES})
                votes.setdefault(k, Counter())[(keys[pos + 1] if pos + 1 < n else None, n - 1 - pos)] += 1
            if pid in seen:
                q["duplicate_premises"] += 1
                continue
            seen.add(pid)
            cust = customers.get(pid)
            if cust and cust["circuit"] is not None and cust["circuit"] != circuit:
                q["circuit_mismatch_customer_vs_topology"] += 1
            pending.append((pid, keys, {
                "premise_token": make_token(salt, pid),
                "transformer_key": keys[0],
                "circuit": circuit,
                "service_type": cust["service"] if cust else "Unknown",
                "protective_count": sum(1 for c, _, t in chain if c == PROTECTIVE_CLASS and t in PROTECTIVE_TYPES),
                "hops": n - 1,
                "hex_cell": cust["cell"] if cust else None,
                "hex_res": cust["res"] if cust else None,
                "in_topology": True,
                "in_customer_file": cust is not None,
            }))

    # A few devices appear under more than one parent (alternate feed paths). The tree keeps the majority parent;
    # premises whose own chain disagrees are flagged so downstream logic can treat them carefully.
    parent_of: dict[str, str | None] = {}
    for k, c in votes.items():
        parent_of[k] = c.most_common(1)[0][0][0]
        if len({parent for parent, _ in c}) > 1:
            q["devices_with_multiple_parents"] += 1

    # Depth comes from the resolved tree (0 at the breaker) so parent and depth can never disagree.
    depth_of: dict[str, int] = {}

    def depth(k: str) -> int:
        chain = []
        while k not in depth_of and parent_of[k] is not None:
            chain.append(k)
            k = parent_of[k]
            if len(chain) > 64:
                raise ValueError("cycle in device tree")
        base = depth_of.get(k, 0)
        depth_of.setdefault(k, base)
        for node in reversed(chain):
            base += 1
            depth_of[node] = base
        return depth_of[chain[0]] if chain else base

    for k in parent_of:
        depth(k)
    premises = []
    for pid, keys, row in pending:
        alt = any(parent_of[k] != (keys[i + 1] if i + 1 < len(keys) else None) for i, k in enumerate(keys))
        row["alternate_path"] = alt
        q["premises_on_alternate_path"] += alt
        premises.append(row)

    for pid, cust in customers.items():       # customers with no upstream chain
        if pid not in seen:
            premises.append({
                "premise_token": make_token(salt, pid), "transformer_key": None, "circuit": None,
                "service_type": cust["service"], "protective_count": None, "hops": None,
                "hex_cell": cust["cell"], "hex_res": cust["res"], "in_topology": False, "in_customer_file": True,
                "alternate_path": False,
            })
            q["customers_without_topology"] += 1

    rows = []
    for k, m in meta.items():
        n = served[k]
        rows.append({**m, "parent_key": parent_of[k], "depth": depth_of[k], "n_premises_served": n,
                     "small_group": n < MIN_GROUP})
    q["premises"], q["devices"] = len(premises), len(rows)
    return premises, rows, dict(q)
