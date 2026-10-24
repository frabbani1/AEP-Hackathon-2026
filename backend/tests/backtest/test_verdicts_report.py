"""Tasks 4.1 to 4.4: verdict rules, SVG plots, report files, and the losing-model case."""
import copy
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest
from concierge.backtest import evidence, report as rp, rest_eval as rv, risk_eval as re_, svg, verdicts as vd
from concierge.backtest.config import DEFAULT_CONFIG, LIMITS

from .fixtures import StubEngine, make_split
from .test_rest_eval import rich_s1

CFG = replace(DEFAULT_CONFIG, n_permutations=200, n_bootstrap=100, k_bootstrap=30)


def band(rate, lo, hi, n=20):
    return {"n_circuits": n, "hit_rate": rate, "ci95": [lo, hi], "suppressed": False}


def risk(**over):
    r = {"base_rate": {"premise_weighted": 0.5, "per_circuit": 0.45},
         "bands": {"Low": band(0.2, 0.1, 0.35), "Medium": band(0.5, 0.35, 0.65), "High": band(0.8, 0.6, 0.9)},
         "spread": {"value": 0.6, "ci95": [0.3, 0.8]},
         "size_baseline": {"bands": {}, "spread": {"value": 0.2, "ci95": [-0.1, 0.45]}},
         "permutation": {"p_value": 0.001}}
    r.update(over)
    return r


# ---- 4.1 one test per rule and level -----------------------------------------------------------------------------------
@pytest.mark.parametrize("band_ci,level", [([0.1, 0.35], "supports"), ([0.2, 0.55], "mixed")])
def test_low_vs_base(band_ci, level):
    r = risk(bands={**risk()["bands"], "Low": band(0.3, *band_ci)})
    assert vd.risk_low_vs_base(r)["level"] == level


@pytest.mark.parametrize("band_ci,level", [([0.6, 0.9], "supports"), ([0.45, 0.9], "mixed")])
def test_high_vs_base(band_ci, level):
    r = risk(bands={**risk()["bands"], "High": band(0.8, *band_ci)})
    assert vd.risk_high_vs_base(r)["level"] == level


def test_medium_vs_high_levels():
    mk = lambda m, h: risk(bands={**risk()["bands"], "Medium": band(*m), "High": band(*h)})  # noqa: E731
    assert vd.risk_medium_vs_high(mk((0.3, 0.2, 0.4), (0.8, 0.6, 0.9)))["level"] == "supports"
    out = vd.risk_medium_vs_high(mk((0.7, 0.5, 0.8), (0.75, 0.6, 0.9)))
    assert out["level"] == "mixed" and "not distinguishable" in out["sentence"]
    assert vd.risk_medium_vs_high(mk((0.8, 0.7, 0.9), (0.3, 0.2, 0.4)))["level"] == "does_not_support"


def test_vs_size_levels():
    win = risk(spread={"value": 0.6, "ci95": [0.3, 0.8]}, size_baseline={"bands": {}, "spread": {"value": 0.1, "ci95": [-0.1, 0.25]}})
    assert vd.risk_vs_size(win)["level"] == "supports"
    mixed = vd.risk_vs_size(risk())                      # model 0.6 with low 0.3 against size high 0.45
    assert mixed["level"] == "mixed" and "do not beat" in mixed["sentence"]
    lose = vd.risk_vs_size(risk(spread={"value": 0.2, "ci95": [0.0, 0.4]}, size_baseline={"bands": {}, "spread": {"value": 0.3, "ci95": [0.1, 0.5]}}))
    assert lose["level"] == "does_not_support" and "do not beat" in lose["sentence"]


def test_permutation_levels():
    assert vd.risk_permutation(risk(), CFG)["level"] == "supports"
    assert vd.risk_permutation(risk(permutation={"p_value": 0.2}), CFG)["level"] == "does_not_support"
    assert vd.risk_permutation(risk(permutation={"p_value": None}), CFG)["level"] == "not_testable"


def test_a_hidden_band_makes_its_verdict_not_testable():
    hidden = {"suppressed": True, "hit_rate": None}
    r = risk(bands={**risk()["bands"], "High": hidden})
    assert vd.risk_high_vs_base(r)["level"] == "not_testable" and vd.risk_medium_vs_high(r)["level"] == "not_testable"


def test_rest_calibrated_levels():
    fitted = {"fit": {"status": "fitted", "crews": 4}}
    sparse = {"fit": {"status": "not_identifiable", "crews": 3, "records": 30, "circuits_with_enough_records": 2}}
    assert vd.rest_calibrated(fitted)["level"] == "supports"
    out = vd.rest_calibrated(sparse)
    assert out["level"] == "not_testable" and "uncalibrated" in out["sentence"]
    assert vd.rest_calibrated(None)["level"] == "not_testable"


def test_rest_coverage_levels():
    def run(c90):
        return {"runs": {"forecast": {"coverage_p90": c90, "coverage_p90_backlog_as_miss": c90 - 0.05}}}
    assert vd.rest_coverage_p90(run(0.9), CFG)["level"] == "supports"
    assert vd.rest_coverage_p90(run(0.79), CFG)["level"] == "does_not_support"
    assert vd.rest_coverage_p90(run(0.98), CFG)["level"] == "does_not_support"
    assert vd.rest_coverage_p90({"runs": {}}, CFG)["level"] == "not_testable"


def test_rest_vs_baseline_levels():
    def run(*beats):
        names = ["constant", "circuit_median", "one_day"]
        return {"runs": {"forecast": {"baselines": {n: {"model_vs_baseline": {"beats": b}} for n, b in zip(names, beats)}}}}
    assert vd.rest_vs_baseline(run(True, True, True))["level"] == "supports"
    none = vd.rest_vs_baseline(run(False, False, False))
    assert none["level"] == "does_not_support" and "does not beat" in none["sentence"]
    some = vd.rest_vs_baseline(run(True, False, True))
    assert some["level"] == "mixed" and "does not beat the Sep 2024 circuit median" in some["sentence"]
    assert vd.rest_vs_baseline({"runs": {}})["level"] == "not_testable"


def test_resolution_verdict_is_always_present():
    out = vd.build_verdicts(risk(), None, CFG)
    codes = [v["code"] for v in out]
    assert "rest_resolution" in codes and len(codes) == len(set(codes)) == 9
    assert next(v for v in out if v["code"] == "rest_resolution")["sentence"].endswith("about one day.")


def test_supports_never_appears_without_its_rule(monkeypatch):
    bad = risk(bands={"Low": band(0.5, 0.4, 0.6), "Medium": band(0.5, 0.4, 0.6), "High": band(0.5, 0.4, 0.6)},
               spread={"value": 0.0, "ci95": [-0.2, 0.2]}, permutation={"p_value": 0.5})
    out = vd.build_verdicts(bad, None, CFG)
    assert all(v["level"] != "supports" for v in out)


# ---- 4.2 plots --------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def built():
    split = make_split(s1=rich_s1())
    return rp.build_report(split, CFG, engine=StubEngine(), check_counts=False, git_hash="a" * 40,
                           input_hashes={"damage_fact.parquet": "sha256:" + "b" * 64})


@pytest.fixture(scope="module")
def built_no_engine():
    return rp.build_report(make_split(), CFG, engine=None, check_counts=False)


FORBIDDEN = re.compile(r"CIR\d+|STATION|\d{6,}|\bd_[0-9a-f]{12}\b|\bp_[0-9a-f]{16}\b")


@pytest.mark.parametrize("fixture_name", ["built", "built_no_engine"])
def test_plots_parse_as_xml_and_hold_no_identifiers(request, fixture_name):
    plots = rp.render_plots(request.getfixturevalue(fixture_name))
    assert set(plots) == {"risk_bands.svg", "restoration_scatter.svg", "repair_hist.svg"}
    for name, text in plots.items():
        root = ET.fromstring(text)
        assert root.tag.endswith("svg") and root.find("{http://www.w3.org/2000/svg}title") is not None, name
        assert not FORBIDDEN.search(text), name
        assert "circuit id" not in text.lower()


def test_scatter_draws_one_marker_per_point_for_each_run(built):
    text = rp.render_plots(built)["restoration_scatter.svg"]
    cut = built["restoration"]["cutoffs"]["3"]["runs"]
    n = sum(len(r["points"]) for r in cut.values())
    assert n > 0 and text.count("fill-opacity=\"0.45\"") == n


def test_scatter_says_not_run_without_an_engine(built_no_engine):
    assert "Not run" in rp.render_plots(built_no_engine)["restoration_scatter.svg"]


def test_risk_plot_hides_bands_under_ten_circuits(built):
    assert "hidden" in rp.render_plots(built)["risk_bands.svg"]          # the fixture has 12 circuits in total


def test_svg_writers_accept_empty_input():
    ET.fromstring(svg.restoration_scatter_svg(None))
    ET.fromstring(svg.repair_hist_svg(None))


# ---- 4.3 report files --------------------------------------------------------------------------------------------------
def test_report_carries_hashes_seed_and_limits(built):
    assert built["config_hash"].startswith("sha256:") and built["seed"] == CFG.seed and built["git_hash"] == "a" * 40
    assert built["inputs"] == {"damage_fact.parquet": "sha256:" + "b" * 64} and built["limits"] == list(LIMITS)
    assert built["risk"]["primary"]["base_rate"]["premise_weighted"] > 0
    assert set(built["risk"]["sensitivity"]) == {"tau", "reverse", "drop_largest_3", "min_size_100"}
    assert built["restoration"]["engine_version"] == "stub-1"


def test_write_outputs_creates_every_file_and_the_markdown_has_all_six_limits(built, tmp_path):
    paths = rp.write_outputs(built, tmp_path / "bt", tmp_path / "docs" / "backtest.md")
    assert set(paths) == {"backtest.json", "backtest.md", "risk_bands.svg", "restoration_scatter.svg", "repair_hist.svg"}
    assert all(p.exists() for p in paths.values())
    md = paths["backtest.md"].read_text()
    assert len(LIMITS) == 6 and all(limit in md for limit in LIMITS) and "What this does not show" in md
    again = json.loads(paths["backtest.json"].read_text())
    assert again["verdicts"] == built["verdicts"]


def test_output_files_pass_the_identifier_scan(built, tmp_path):
    paths = rp.write_outputs(built, tmp_path, tmp_path / "b.md")
    for name, p in paths.items():
        if name != "backtest.json":
            assert evidence.find_identifiers(p.read_text()) == [], name
    evidence.scan_value(json.loads(paths["backtest.json"].read_text()))


def test_the_report_holds_no_per_circuit_lists_or_small_counts(built):
    text = json.dumps(built)
    assert '"circuit":' not in text and '"circuits": [' not in text    # no circuit key or per-circuit list
    for run in built["restoration"]["cutoffs"]["3"]["runs"].values():
        assert all(p["records"] >= 10 for p in run["points"])
    for cut in built["restoration"]["cutoffs"].values():
        assert all(b["count"] is None or b["count"] == 0 or b["count"] >= 10 for b in cut["histogram"]["bins"])


def test_the_report_is_identical_on_a_second_run():
    a = rp.build_report(make_split(s1=rich_s1()), CFG, engine=StubEngine(), check_counts=False)
    b = rp.build_report(make_split(s1=rich_s1()), CFG, engine=StubEngine(), check_counts=False)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_circuit_label_in_any_text_blocks_the_write(built, tmp_path):
    bad = copy.deepcopy(built)
    bad["verdicts"][0]["sentence"] = "Circuit CIR60 did badly."
    with pytest.raises(evidence.PrivacyError):
        rp.write_outputs(bad, tmp_path, tmp_path / "x.md")
    assert not (tmp_path / "backtest.json").exists()


def test_report_fails_when_the_spec02_table_does_not_match(tmp_path):
    split = make_split()
    stored = json.loads(json.dumps(re_.spec02_tables(split)))
    stored["forward"]["bands"]["Low"]["hit_rate"] = 0.99
    with pytest.raises(re_.ReproductionError):
        rp.build_report(split, CFG, stored_backtest=stored, check_counts=False)


def test_report_fails_on_unexpected_row_counts():
    from concierge.backtest.split import DataMismatch
    with pytest.raises(DataMismatch):
        rp.build_report(make_split(), CFG)


# ---- 4.4 a losing model ------------------------------------------------------------------------------------------------
class BadEngine(StubEngine):
    """Predicts 40 days for every circuit, far from the observed 1 to 2 days."""
    def completion_hours(self, circuit, *, crews, damage, seed):
        return [960.0] * 50


def test_a_losing_model_is_reported_plainly(tmp_path):
    rep = rp.build_report(make_split(), CFG, engine=BadEngine(), check_counts=False)
    v = next(x for x in rep["verdicts"] if x["code"] == "rest_vs_baseline")
    assert v["level"] == "does_not_support" and "does not beat" in v["sentence"]
    assert next(x for x in rep["verdicts"] if x["code"] == "rest_coverage_p90")["level"] in ("supports", "does_not_support")
    md = rp.render_markdown(rep)
    assert "does not beat" in md
    # no `supports` verdict may appear for the restoration-vs-baseline comparison
    assert not any(x["code"] == "rest_vs_baseline" and x["level"] == "supports" for x in rep["verdicts"])


def test_a_model_that_ties_the_baseline_does_not_get_a_supports_verdict():
    class OneDay(StubEngine):
        def completion_hours(self, circuit, *, crews, damage, seed):
            return [24.0] * 50           # exactly the constant one-day baseline
    rep = rp.build_report(make_split(), CFG, engine=OneDay(), check_counts=False)
    v = next(x for x in rep["verdicts"] if x["code"] == "rest_vs_baseline")
    assert v["level"] != "supports"
    row = rep["restoration"]["cutoffs"]["3"]["runs"]["forecast"]["baselines"]["one_day"]["model_vs_baseline"]
    assert row["beats"] is False
