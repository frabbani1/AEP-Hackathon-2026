"""Tasks 2.1 to 2.5: spec-02 reproduction, base rate and lift, size baseline, permutation, AUC, sensitivity."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from concierge.backtest import risk_eval as re_, stats
from concierge.backtest.config import DEFAULT_CONFIG
from concierge.risk import calibrate as cal

from .fixtures import make_split

CAL = Path(__file__).resolve().parents[3] / "artifacts" / "risk" / "calibration.json"
TABLES = CAL.parents[1] / "curated" / "tables"
FAST = replace(DEFAULT_CONFIG, n_permutations=300, n_bootstrap=100)


def rows(spec):
    """spec: list of (premises, calibration reports, evaluation reports)."""
    return [re_.CircuitRow(i + 1, n, c, e) for i, (n, c, e) in enumerate(spec)]


# ---- 2.1 -------------------------------------------------------------------------------------------------------------
def test_reproduction_passes_on_matching_tables_and_fails_on_a_changed_one():
    split = make_split()
    stored = json.loads(json.dumps(re_.spec02_tables(split)))
    re_.reproduce_spec02(split, stored)
    bad = copy.deepcopy(stored)
    bad["forward"]["bands"]["High"]["hit_rate"] += 0.001
    with pytest.raises(re_.ReproductionError):
        re_.reproduce_spec02(split, bad)


def test_primary_result_carries_the_spec02_band_numbers():
    split = make_split()
    primary = re_.run_risk(split, FAST)["primary"]
    re_.check_primary_matches_spec02(primary, re_.spec02_tables(split)["forward"])
    broken = copy.deepcopy(primary)
    broken["bands"]["Low"]["premises"] += 1
    with pytest.raises(re_.ReproductionError):
        re_.check_primary_matches_spec02(broken, re_.spec02_tables(split)["forward"])


@pytest.mark.skipif(not (CAL.exists() and (TABLES / "damage_fact.parquet").exists()), reason="artifacts not built")
def test_real_data_equals_stored_calibration_json():
    from concierge.backtest.split import load_split
    stored = json.loads(CAL.read_text())["backtest"]
    split = load_split(TABLES)
    re_.reproduce_spec02(split, stored)
    primary = re_.run_risk(split, replace(DEFAULT_CONFIG, n_permutations=200, n_bootstrap=50))["primary"]
    re_.check_primary_matches_spec02(primary, stored["forward"])
    assert primary["base_rate"]["premise_weighted"] == pytest.approx(0.5358, abs=1e-3)
    assert primary["base_rate"]["per_circuit"] == pytest.approx(47 / 104)


# ---- 2.2 base rate, lift, the "beats base rate" rule ---------------------------------------------------------------
def test_base_rate_is_weighted_and_per_circuit():
    # tau 2: hit when 1000*eval >= 2*n. Circuits: (100, hit), (300, miss), (600, hit), (1000, miss)
    r = re_.evaluate(rows([(100, 0, 1), (300, 0, 0), (600, 0, 2), (1000, 0, 0)]), tau=2, seed=1, name="t", cfg=FAST, min_size=50)
    assert r["base_rate"]["premise_weighted"] == pytest.approx(700 / 2000)
    assert r["base_rate"]["per_circuit"] == pytest.approx(0.5)


def _bands_for(spec, tau=2.0):
    return re_.evaluate(rows(spec), tau=tau, seed=1, name="t", cfg=FAST, min_size=50)


def synthetic_clear_separation():
    """40 equal circuits. Calibration rates rise strictly, so the bands are Low = first 20 (rate 0), Medium = next 14,
    High = last 6. Hits: Low 3 of 20, Medium 7 of 14, High 6 of 6."""
    spec = [(100, 0, 1 if i < 3 else 0) for i in range(20)]
    spec += [(100, i + 1, 1 if i < 7 else 0) for i in range(14)]
    spec += [(100, 15 + i, 1) for i in range(6)]
    return spec


def test_each_band_has_hit_rate_interval_lift_and_difference():
    out = _bands_for(synthetic_clear_separation())
    b, base = out["bands"], out["base_rate"]["premise_weighted"]
    assert [b[k]["n_circuits"] for k in ("Low", "Medium", "High")] == [20, 14, 6]
    assert base == pytest.approx(16 / 40)
    assert b["Low"]["hit_rate"] == pytest.approx(3 / 20) and b["High"]["hit_rate"] == pytest.approx(1.0)
    for name in ("Low", "Medium", "High"):
        x = b[name]
        assert x["lift"] == pytest.approx(x["hit_rate"] / base) and x["difference"] == pytest.approx(x["hit_rate"] - base)
        assert x["ci95"][0] <= x["hit_rate"] + 1e-9 and x["hit_rate"] <= x["ci95"][1] + 1e-9


def test_high_beats_base_only_when_its_lower_bound_clears_it():
    out = _bands_for(synthetic_clear_separation())
    high = out["bands"]["High"]
    assert high["ci95"][0] > out["base_rate"]["premise_weighted"] and high["beats_base_rate"] is True
    assert out["bands"]["Low"]["vs_base"] == "below" and out["bands"]["Low"]["beats_base_rate"] is True
    assert out["bands"]["Medium"]["vs_base"] == "overlaps" and out["bands"]["Medium"]["beats_base_rate"] is False


def test_high_does_not_beat_base_when_its_interval_reaches_it():
    # 30 equal circuits: Low = 18 zero-rate (4 hits), Medium = 8 (6 hits), High = 4 (3 hits). Base = 13/30.
    spec = [(100, 0, 1 if i < 4 else 0) for i in range(18)]
    spec += [(100, i + 1, 1 if i < 6 else 0) for i in range(8)]
    spec += [(100, 9 + i, 1 if i < 3 else 0) for i in range(4)]
    out = _bands_for(spec)
    high = out["bands"]["High"]
    assert high["n_circuits"] == 4 and high["hit_rate"] == pytest.approx(0.75)
    assert high["ci95"][0] <= out["base_rate"]["premise_weighted"] and high["beats_base_rate"] is False
    assert high["vs_base"] == "overlaps"


def test_the_beats_base_rule_per_band_on_explicit_labels():
    # Medium: 30 circuits, all misses, against a base of 0.5. Wilson upper bound is about 0.114, so it is clearly below.
    labels = ["Low"] * 10 + ["Medium"] * 30 + ["High"] * 10
    rs = rows([(100, 0, 0)] * 50)
    hits = [1] * 10 + [0] * 30 + [1] * 10
    g = re_._group_stats(labels, rs, hits, 0.5, 1.96)
    assert g["Medium"]["vs_base"] == "below" and g["Medium"]["beats_base_rate"] is True
    assert g["Low"]["vs_base"] == "above" and g["Low"]["beats_base_rate"] is False      # Low must be below to count
    assert g["High"]["vs_base"] == "above" and g["High"]["beats_base_rate"] is True
    # Medium above the base rate also counts (it excludes b in either direction).
    g2 = re_._group_stats(labels, rs, [0] * 10 + [1] * 30 + [0] * 10, 0.5, 1.96)
    assert g2["Medium"]["vs_base"] == "above" and g2["Medium"]["beats_base_rate"] is True
    assert g2["High"]["beats_base_rate"] is False


# ---- 2.3 size baseline ---------------------------------------------------------------------------------------------
def test_size_baseline_uses_the_same_group_sizes_and_ranks_by_premises():
    out = re_.run_risk(make_split(), FAST)["primary"]
    for b in ("Low", "Medium", "High"):
        assert out["size_baseline"]["bands"][b]["n_circuits"] == out["bands"][b]["n_circuits"]
    # Small circuits first: the Low size group holds the smallest circuits, so it has the fewest premises per circuit.
    sb = out["size_baseline"]["bands"]
    assert sb["Low"]["premises"] / max(sb["Low"]["n_circuits"], 1) < sb["High"]["premises"] / max(sb["High"]["n_circuits"], 1)


def test_size_baseline_can_match_the_model_when_bands_follow_size():
    # The calibration rate 1000*i/(60+10i) rises with i, and so does size, so the model bands and size groups coincide.
    spec = [(60 + 10 * i, i, 1 if i >= 8 else 0) for i in range(24)]
    out = _bands_for(spec)
    assert out["spread"]["value"] == pytest.approx(out["size_baseline"]["spread"]["value"])


# ---- 2.4 permutation, AUC, Spearman -------------------------------------------------------------------------------
def test_stats_on_hand_computed_examples():
    assert stats.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert stats.auc([4, 3, 2, 1], [0, 0, 1, 1]) == 0.0
    assert stats.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5
    assert stats.auc([1, 2, 2, 3], [0, 0, 1, 1]) == pytest.approx(0.875)     # pairs: 1 + 0.5 (tie) + 1 + 1 = 3.5 of 4
    assert stats.auc([1, 2], [1, 1]) is None
    assert stats.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert stats.spearman([1, 1, 1], [1, 2, 3]) is None
    assert stats.average_ranks([5, 1, 1, 3]) == [4.0, 1.5, 1.5, 3.0]
    assert stats.nearest_rank(list(range(1, 11)), 0.5) == 5 and stats.nearest_rank(list(range(1, 11)), 0.9) == 9
    assert stats.median([3, 1, 2]) == 2 and stats.median([1, 2, 3, 4]) == 2.5


def test_permutation_p_value_matches_the_exact_value_on_a_tiny_example():
    # 6 circuits, 2 per band; hits: the two High and one Medium. Exact one-sided p = 9 of 90 arrangements = 0.1.
    spec = [(100, 0, 0), (100, 0, 0), (100, 1, 1), (100, 1, 0), (100, 50, 1), (100, 50, 1)]
    # Force the bands with thresholds-free labeling by calling the internal permutation directly.
    rs = rows(spec)
    hits = [1 if 1000 * r.eval >= 2 * r.n else 0 for r in rs]
    labels = ["Low", "Low", "Medium", "Medium", "High", "High"]
    out = re_._permutation(rs, hits, labels, 4000, stats.rng(5, "x"))
    assert out["observed_gap"] == pytest.approx(1.0)
    assert out["p_value"] == pytest.approx(0.1, abs=0.02)


def test_permutation_and_auc_are_reproducible_with_a_seed():
    spec = synthetic_clear_separation()
    a = re_.evaluate(rows(spec), tau=2, seed=11, name="n", cfg=FAST, min_size=50)
    b = re_.evaluate(rows(spec), tau=2, seed=11, name="n", cfg=FAST, min_size=50)
    c = re_.evaluate(rows(spec), tau=2, seed=12, name="n", cfg=FAST, min_size=50)
    assert a == b
    assert a["permutation"]["p_value"] == b["permutation"]["p_value"]
    assert a["auc"]["ci95"] != c["auc"]["ci95"] or a["permutation"]["p_value"] != c["permutation"]["p_value"]
    assert 0.0 < a["permutation"]["p_value"] < 0.05 and a["auc"]["value"] > 0.7
    assert a["auc"]["ci95"][0] <= a["auc"]["ci95"][1]


def test_a_perfectly_random_pairing_is_not_significant():
    import random
    gen = random.Random(3)
    spec = [(100, c, gen.choice([0, 1])) for c in gen.sample(range(1, 400), 60)]
    out = re_.evaluate(rows(spec), tau=2, seed=1, name="r", cfg=FAST, min_size=50)
    assert out["permutation"]["p_value"] > 0.05


# ---- 2.5 sensitivity -----------------------------------------------------------------------------------------------
def shape(x):
    if isinstance(x, dict):
        return {k: shape(v) for k, v in x.items()}
    return "leaf"                       # numbers, intervals, or None when a value cannot be computed


def test_every_sensitivity_run_has_the_structure_of_the_primary_run():
    out = re_.run_risk(make_split(), FAST)
    want = shape(out["primary"])
    sens = out["sensitivity"]
    assert set(sens) == {"tau", "reverse", "drop_largest_3", "min_size_100"}
    assert set(sens["tau"]) == {"1", "2", "5"} and sens["tau"]["2"] is out["primary"]
    for name, run in [("tau1", sens["tau"]["1"]), ("tau5", sens["tau"]["5"]), ("reverse", sens["reverse"]),
                      ("drop", sens["drop_largest_3"]), ("min100", sens["min_size_100"])]:
        assert shape(run) == want, name


def test_sensitivity_runs_change_the_circuit_set_as_described():
    out = re_.run_risk(make_split(), FAST)["sensitivity"]
    assert out["drop_largest_3"]["n_circuits"] == 12 - 3
    assert out["min_size_100"]["n_circuits"] == 10 and out["min_size_100"]["min_circuit_premises"] == 100
    assert out["tau"]["1"]["tau_per_1k"] == 1 and out["tau"]["5"]["tau_per_1k"] == 5


def test_reverse_direction_swaps_the_storms():
    split = make_split()
    out = re_.run_risk(split, FAST)["sensitivity"]["reverse"]
    stored = cal.backtest(split.circuit_sizes, split.s2_counts(), split.s1_counts())
    assert out["thresholds"] == stored["thresholds"]
    for b in ("Low", "Medium", "High"):
        assert out["bands"][b]["hit_rate"] == stored["bands"][b]["hit_rate"]


def test_bands_under_ten_circuits_are_hidden_in_the_published_copy():
    out = re_.run_risk(make_split(), FAST)["primary"]       # 12 circuits: every band has fewer than 10
    public = re_.redact(out, 10)
    for b in public["bands"].values():
        assert b["suppressed"] is True and b["hit_rate"] is None and b["n_circuits"] is None
    assert public["spread"]["value"] is None and public["size_baseline"]["spread"]["value"] is None
    assert out["bands"]["Low"]["hit_rate"] is not None       # the working copy is untouched
