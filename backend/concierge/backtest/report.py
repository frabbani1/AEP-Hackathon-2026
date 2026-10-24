"""Assemble backtest.json and docs/backtest.md, and write the three plots (design D7)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from ..risk.config import BANDS
from . import evidence, risk_eval, rest_eval, svg, verdicts
from .config import BACKTEST_VERSION, LIMITS, NOTE, PLOT_FILES, BacktestConfig
from .split import Split, verify_counts


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def build_report(split: Split, cfg: BacktestConfig, *, engine: rest_eval.CircuitEngine | None = None,
                 stored_backtest: Mapping | None = None, spec02_config_hash: str | None = None,
                 input_hashes: Mapping[str, str] | None = None, git_hash: str | None = None,
                 check_counts: bool = True) -> dict:
    """Run both evaluations and return the full report. Raises if the data or the spec-02 table do not match."""
    if check_counts:
        verify_counts(split)
    fresh = risk_eval.spec02_tables(split)
    if stored_backtest is not None:
        risk_eval.reproduce_spec02(split, stored_backtest)
    risk = risk_eval.run_risk(split, cfg)
    risk_eval.check_primary_matches_spec02(risk["primary"], fresh["forward"])

    mg = cfg.min_group
    public_risk = {
        "primary": risk_eval.redact(risk["primary"], mg),
        "sensitivity": {
            "tau": {t: risk_eval.redact(r, mg) for t, r in risk["sensitivity"]["tau"].items()},
            **{k: risk_eval.redact(v, mg) for k, v in risk["sensitivity"].items() if k != "tau"},
        },
    }
    restoration = rest_eval.run_restoration(split, engine, cfg)
    restoration["engine_version"] = getattr(engine, "engine_version", None) if engine is not None else None

    report = {
        "backtest_version": BACKTEST_VERSION,
        "sample": cfg.sample,
        "seed": cfg.seed,
        "config": cfg.as_dict(),
        "config_hash": cfg.config_hash(),
        "spec02_config_hash": spec02_config_hash,
        "spec02_check": "passed" if stored_backtest is not None else "skipped",
        "git_hash": git_hash,
        "inputs": dict(input_hashes or {}),
        "protocol": {
            "calibrate_on": "Sep 2024 (S1)", "test_on": "Feb 2025 (S2)", "unit": "circuit",
            "hit_definition": (f"A circuit is a hit when it had at least {risk['primary']['tau_per_1k']:g} damage reports "
                               "per 1,000 homes in Feb 2025."),
            "severity": "The Feb 2025 forecast uses the feb-2025-like preset: the regional storm size, not any circuit's outcome.",
            "rows": split.counts(),
        },
        "risk": public_risk,
        "restoration": restoration,
        "limits": list(LIMITS),
        "note": NOTE,
        "plots": list(PLOT_FILES),
    }
    report["verdicts"] = verdicts.build_verdicts(public_risk["primary"], restoration, cfg)
    evidence.scan_value(report)
    return report


# ---- plots ---------------------------------------------------------------------------------------------------------
def render_plots(report: Mapping) -> dict[str, str]:
    cut = next(iter(report["restoration"]["cutoffs"].values()))
    runs = cut["runs"] or None
    return {
        "risk_bands.svg": svg.risk_bands_svg(report["risk"]["primary"]),
        "restoration_scatter.svg": svg.restoration_scatter_svg(runs),
        "repair_hist.svg": svg.repair_hist_svg(cut["histogram"]),
    }


# ---- markdown ------------------------------------------------------------------------------------------------------
def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _p(x, nd=3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _rng(ci) -> str:
    return "n/a" if not ci else f"{100 * ci[0]:.0f}% to {100 * ci[1]:.0f}%"


def _pair(ci, nd: int = 2) -> str:
    return "n/a" if not ci else f"{ci[0]:.{nd}f} to {ci[1]:.{nd}f}"


def _band_rows(bands: Mapping) -> list[str]:
    rows = ["| Band | Circuits | Hit rate | 95% range | Lift | Difference | Versus base rate |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for name in BANDS:
        b = bands[name]
        if b.get("suppressed"):
            rows.append(f"| {name} | under 10 (hidden) | hidden | hidden | hidden | hidden | hidden |")
        elif b["hit_rate"] is None:
            rows.append(f"| {name} | 0 | n/a | n/a | n/a | n/a | n/a |")
        else:
            rows.append(f"| {name} | {b['n_circuits']} | {_pct(b['hit_rate'])} | {_rng(b['ci95'])} | "
                        f"{_p(b['lift'], 2)} | {100 * b['difference']:+.0f} points | {b['vs_base']} |")
    return rows


def render_markdown(report: Mapping) -> str:
    risk = report["risk"]["primary"]
    sens = report["risk"]["sensitivity"]
    rest = report["restoration"]
    L: list[str] = []
    L += ["# Backtest: did the model work?", "",
          "Generated by `scripts/run_backtest.py`. Do not edit by hand. " + NOTE, ""]
    if report["sample"]:
        L += ["**Sample run:** fewer shuffles and resamples than the real run. Do not publish these numbers.", ""]
    L += ["## Verdicts", "", "| Check | Result | What it says |", "| --- | --- | --- |"]
    for v in report["verdicts"]:
        L.append(f"| `{v['code']}` | {v['level']} | {v['sentence']} |")
    L += ["", "Each sentence comes from a fixed rule applied to the numbers below. A `supports` result appears only when its rule fired.", ""]

    L += ["## Risk bands", "", f"Calibrated on Sep 2024, tested on Feb 2025, {risk['n_circuits']} circuits with at least "
          f"{risk['min_circuit_premises']} homes. {report['protocol']['hit_definition']}", "",
          f"Base rate: {_pct(risk['base_rate']['premise_weighted'])} of homes (weighted by homes), "
          f"{_pct(risk['base_rate']['per_circuit'])} of circuits.", ""]
    L += _band_rows(risk["bands"]) + [""]
    L += ["Same groups when circuits are ranked by size only (the size baseline):", ""] + _band_rows(risk["size_baseline"]["bands"]) + [""]
    perm, auc = risk["permutation"], risk["auc"]
    L += [f"- Permutation test on the High minus Low gap: p = {_p(perm['p_value'], 4)} ({perm['n_shuffles']:,} shuffles, seed {report['seed']}).",
          f"- AUC of the Sep 2024 score against Feb 2025 hits: {_p(auc['value'], 2)} (95% range {_pair(auc['ci95'])}).",
          f"- Spearman correlation of Sep 2024 and Feb 2025 damage rates: {_p(risk['spearman']['value'], 2)}.", ""]

    L += ["### Sensitivity", "", "| Run | Circuits | Base rate | Low | Medium | High | Permutation p | AUC |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]

    def srow(label: str, r: Mapping) -> str:
        cells = [("hidden" if r["bands"][b].get("suppressed") else _pct(r["bands"][b]["hit_rate"])) for b in BANDS]
        return (f"| {label} | {r['n_circuits']} | {_pct(r['base_rate']['premise_weighted'])} | " + " | ".join(cells)
                + f" | {_p(r['permutation']['p_value'], 4)} | {_p(r['auc']['value'], 2)} |")

    for t, r in sens["tau"].items():
        L.append(srow(f"Hit threshold {t} per 1,000 homes", r))
    names = {"reverse": "Reverse (Feb 2025 to Sep 2024)", "drop_largest_3": "Without the 3 largest circuits",
             "min_size_100": "Circuits with 100+ homes"}
    for k, label in names.items():
        if k in sens:
            L.append(srow(label, sens[k]))
    L.append("")

    L += ["## Restoration range", ""]
    if rest["status"] != "ok":
        L += [f"**Not scored.** {rest['reason']} The sections below show what can be measured without the model.", ""]
    L += [f"Repair times are whole days, so the finest claim is about {rest['resolution_days']} day.", ""]
    for b, res in rest["cutoffs"].items():
        s1, s2 = res["backlog"]["S1"], res["backlog"]["S2"]
        L += [f"### Backlog cutoff: repairs taking more than {b} days", "",
              "| Storm | Rows | Not repaired (censored) | No circuit | Backlog | Usable | Backlog share of mapped repairs |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for name, s in (("Sep 2024", s1), ("Feb 2025", s2)):
            L.append(f"| {name} | {s['rows']} | {s['censored']} | {s['unmapped']} | {s['backlog']} | {s['usable']} | {_pct(s['backlog_share'])} |")
        fit = res["fit"]
        L += ["", f"- Feb 2025 circuits with at least {report['config']['min_circuit_records']} usable records: {s2['circuits_included']} "
                  f"({s2['excluded_small']} smaller circuits excluded).",
              f"- Crews: {fit['status'].replace('_', ' ')}. Using {res['crews_used']} crews ({res['calibration']}). "
              f"Sep 2024 gives {fit['records']} usable records on {fit['circuits_with_enough_records']} circuits with "
              f"{fit['required']['min_records_per_circuit']}+ records; the fit needs {fit['required']['min_records']} records "
              f"and {fit['required']['min_circuits']} circuits."]
        if fit.get("reason"):
            L.append(f"- {fit['reason']}")
        L += ["", "Baselines (Sep 2024 only), mean absolute error against the observed Feb 2025 circuit median:", "",
              "| Baseline | Circuits | MAE (days) |", "| --- | --- | --- |"]
        for name, row in res["baselines"].items():
            L.append(f"| {verdicts.BASELINE_NAMES[name]} | {row['circuits']} | {_p(row['mae'], 2)} |")
        L.append("")
        for run, m in res["runs"].items():
            L += [f"**{run.capitalize()} damage run** ({'actual Feb 2025 asset counts' if run == 'oracle' else 'Sep 2024 rate scaled to Feb 2025 severity'}): "
                  f"{m['circuits']} circuits, {m['records']} records. Coverage P50 {_pct(m['coverage_p50'])} (target about 50%), "
                  f"P90 {_pct(m['coverage_p90'])} (target about 90%); with backlog as misses P90 {_pct(m['coverage_p90_backlog_as_miss'])}. "
                  f"MAE {_p(m['mae_days'], 2)} days; within one day {_pct(m['within_one_day'])}.", ""]
            L += ["| Baseline | Model minus baseline MAE | 95% range | Model beats it |", "| --- | --- | --- | --- |"]
            for name, row in m["baselines"].items():
                mv = row.get("model_vs_baseline")
                if mv:
                    verdict = "yes" if mv["beats"] else "no, the model does not beat it"
                    L.append(f"| {verdicts.BASELINE_NAMES[name]} | {_p(mv['mae_difference'], 2)} | "
                             f"{_pair(mv['ci95'])} | {verdict} |")
            L.append("")
        if res.get("ceiling"):
            c = res["ceiling"]
            L += [f"Ceiling ({c['label']}): best crews on Feb 2025 = {c['crews']}.", ""]

    L += ["## What this does not show", ""] + [f"- {x}" for x in LIMITS] + [""]
    L += ["## Reproducibility", "", f"- Seed: {report['seed']}. Config hash: `{report['config_hash']}`.",
          f"- Git commit: `{report['git_hash'] or 'unknown'}`. Spec-02 table check: {report['spec02_check']}.",
          "- Input files (SHA-256):"] + [f"  - `{k}`: `{v}`" for k, v in sorted(report["inputs"].items())]
    L += ["", "Plots (written next to `backtest.json`, never committed): " + ", ".join(f"`{p}`" for p in report["plots"]) + ".", ""]
    return "\n".join(L)


# ---- writing -------------------------------------------------------------------------------------------------------
def write_outputs(report: Mapping, out_dir: str | Path, docs_path: str | Path) -> dict[str, Path]:
    """Scan everything first, then write. Nothing is written if any file looks like it holds an identifier."""
    plots = render_plots(report)
    md = render_markdown(report)
    js = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for name, text in plots.items():
        evidence.scan_text(text, name)
    evidence.scan_text(md, "backtest.md")
    evidence.scan_value(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"backtest.json": out / "backtest.json", "backtest.md": Path(docs_path)}
    paths["backtest.json"].write_text(js)
    for name, text in plots.items():
        (out / name).write_text(text)
        paths[name] = out / name
    paths["backtest.md"].parent.mkdir(parents=True, exist_ok=True)
    paths["backtest.md"].write_text(md)
    return paths
