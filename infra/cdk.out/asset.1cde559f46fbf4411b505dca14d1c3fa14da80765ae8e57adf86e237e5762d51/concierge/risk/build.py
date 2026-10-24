"""Offline build: source files -> premise_risk.jsonl + calibration.json (design D1, D5, D6, D10).

Artifacts hold opaque tokens and numeric features only. No premise numbers, circuit labels, device IDs,
addresses, coordinates, or life-support flags are ever written.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from . import calibrate as cal
from .config import BANDS, DEFAULT_CONFIG, RiskConfig
from .confidence import assess
from .features import PremiseInput, circuit_stats, raw_score
from .load import load_arcos, load_crosswalk, load_customers, load_topology
from .storms import count_days, detect_storms, storm_index
from .tokens import get_salt, make_token


def join_premises(topology: dict, customers: dict) -> list[PremiseInput]:
    """Topology is the source of truth for circuit and chain. Customers add service type.

    Premises only in the customer file have no chain (circuit=None) and will fall back.
    """
    out: list[PremiseInput] = []
    for pid, t in topology.items():
        service = customers.get(pid, (None, "Unknown"))[1]
        out.append(PremiseInput(pid, t.circuit, service, t.protective, t.hops))
    for pid, (_, service) in customers.items():
        if pid not in topology:
            out.append(PremiseInput(pid, None, service, None, None))
    return out


def _evidence(report: dict, storms, cfg: RiskConfig) -> dict:
    label = f"{storms[0].label} -> {storms[1].label}"
    tau = report["tau_per_1k"]
    definition = f"line had at least {tau:g} damage reports per 1,000 homes"
    out: dict[str, dict | None] = {}
    for b in BANDS:
        r = report["bands"][b]
        if r["hit_rate"] is None:
            out[b] = None
            continue
        p, lo, hi = round(r["hit_rate"], 2), round(r["ci95"][0], 2), round(r["ci95"][1], 2)
        out[b] = {
            "backtest": label,
            "hit_rate": p,
            "ci95": [lo, hi],
            "n_circuits": r["n_circuits"],
            "hit_definition": definition,
            "sentence": (f"In the {storms[1].label} storm, about {round(p * 100)}% of homes in areas we'd rate {b} "
                         f"were on lines with significant damage (95% range {round(lo * 100)}% to {round(hi * 100)}%)."),
        }
    return out


def build_artifacts(
    *, customer_dbf, arcos_dbf, topology_txt, device_dbfs, out_dir, salt: str | bytes | None = None,
    cfg: RiskConfig = DEFAULT_CONFIG, allow_non_monotone: bool = False,
) -> dict:
    """device_dbfs: the GIS device layers (fuse, transformer, switch, recloser) used to build the label crosswalk."""
    salt_b = get_salt(salt)
    customers = load_customers(customer_dbf)
    topology, topo_skipped = load_topology(topology_txt)
    arcos = load_arcos(arcos_dbf)
    crosswalk = load_crosswalk(device_dbfs)
    premises = join_premises(topology, customers)

    # Storms (D5)
    storms = detect_storms(count_days(d for d, _ in arcos), cfg)
    n_storms = len(storms)

    sizes = Counter(p.circuit for p in premises if p.circuit is not None)
    # A topology circuit that no label maps to cannot receive damage records, so its history is unknowable.
    unmapped_circuits = frozenset(sizes) - set(crosswalk.values())
    per_storm: list[Counter[int]] = [Counter() for _ in storms]
    excluded = Counter()
    for d, label in arcos:
        i = storm_index(d, storms)
        c = crosswalk.get(label) if label else None
        if i is None:
            excluded["outside_storm_windows"] += 1
        elif label is None:
            excluded["no_circuit_key"] += 1
        elif c is None:
            excluded["label_not_in_crosswalk"] += 1
        elif c not in sizes:
            excluded["circuit_not_in_topology"] += 1
        else:
            per_storm[i][c] += 1
    damage_total: Counter[int] = Counter()
    for ctr in per_storm:
        damage_total.update(ctr)

    stats = circuit_stats(premises, damage_total, n_storms, cfg)

    # Per-premise rows (D2, D7)
    rows: list[dict] = []
    baseline: list[float] = []
    tokens: set[str] = set()
    fb_counts, conf_counts = Counter(), Counter()
    for p in premises:
        tok = make_token(salt_b, p.premise_id)
        if tok in tokens:
            raise RuntimeError("premise token collision; change the salt")
        tokens.add(tok)
        if p.circuit is None:
            st = None
            reason, level = assess(has_circuit=False, circuit_mapped=True, n_premises=0, records=0,
                                   unknown_service=False, cfg=cfg)
        else:
            st = stats[p.circuit]
            reason, level = assess(has_circuit=True, circuit_mapped=p.circuit not in unmapped_circuits,
                                   n_premises=st.n_premises, records=st.records,
                                   unknown_service=p.service == "Unknown", cfg=cfg)
        if reason:
            fb_counts[reason] += 1
            rows.append({"t": tok, "st": "fb", "why": reason})
            continue
        r_eff = st.rate / st.k_bar
        conf_counts[level] += 1
        rows.append({"t": tok, "st": "ok", "why": None, "R": st.rate, "Re": r_eff, "svc": p.service,
                     "P": p.protective, "H": p.hops, "n": st.records, "c": level})
        baseline.append(raw_score(r_eff, p.service, p.protective, p.hops, 1.0, cfg))
    if not baseline:
        raise RuntimeError("no premise could be scored; check the input files")

    thresholds = cal.compute_thresholds(baseline, cfg)
    ok_rows = [r for r in rows if r["st"] == "ok"]
    reference = {
        "r_eff": statistics.median(r["Re"] for r in ok_rows),
        "protective": statistics.median(r["P"] for r in ok_rows),
        "hops": statistics.median(r["H"] for r in ok_rows),
    }

    # Severity presets (D4)
    presets = cal.severity_presets([s.records for s in storms], cfg)
    max_observed = max(presets["sep-2024-like"], presets["feb-2025-like"], 1.0)

    # Backtest (D6): first storm calibrates, second evaluates
    circuit_sizes = {c: s.n_premises for c, s in stats.items()}
    forward = cal.backtest(circuit_sizes, per_storm[0], per_storm[-1], cfg=cfg)
    reverse = cal.backtest(circuit_sizes, per_storm[-1], per_storm[0], cfg=cfg)
    sensitivity = {f"{t:g}": cal.backtest(circuit_sizes, per_storm[0], per_storm[-1], tau=t, cfg=cfg)
                   for t in cfg.tau_sensitivity}
    gate = cal.check_monotone(forward, allow_non_monotone=allow_non_monotone)

    calibration = {
        "engine_version": "risk-v1",
        "config": cfg.as_dict(),
        "n_storms": n_storms,
        "storms": [{"label": s.label, "start": s.start.isoformat(), "end": s.end.isoformat(), "records": s.records}
                   for s in storms],
        "presets": presets,
        "max_observed_multiplier": max_observed,
        "thresholds": thresholds,
        "reference": reference,
        "band_evidence": _evidence(forward, storms, cfg),
        "backtest": {"forward": forward, "reverse": reverse, "sensitivity": sensitivity, "gate": gate},
        "counts": {
            "premises": len(premises),
            "scored": len(ok_rows),
            "fallbacks": dict(sorted(fb_counts.items())),
            "confidence": dict(sorted(conf_counts.items())),
            "circuits": len(sizes),
            "arcos_records": len(arcos),
            "excluded_records": dict(sorted(excluded.items())),
            "topology_lines_skipped": topo_skipped,
            "customer_rows": len(customers),
            "crosswalk_labels": len(crosswalk),
            "unmapped_circuits": len(unmapped_circuits),
        },
    }
    calibration["config_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(calibration, sort_keys=True).encode()).hexdigest()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: r["t"])
    with open(out / "premise_risk.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    (out / "calibration.json").write_text(json.dumps(calibration, indent=2, sort_keys=True) + "\n")
    return calibration
