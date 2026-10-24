"""Tasks 3.1 to 3.6: observed statistics, engine calls, crews fit, coverage, baselines, both cutoffs."""
from dataclasses import replace

import pytest
from concierge.backtest import rest_eval as rv, stats
from concierge.backtest.config import DEFAULT_CONFIG
from concierge.backtest.split import Damage

from .fixtures import FixedHoursEngine, StubEngine, make_split, s1_rows, s2_rows

FAST = replace(DEFAULT_CONFIG, n_bootstrap=300, k_bootstrap=60)


# ---- 3.1 observed statistics and exclusion counters ------------------------------------------------------------------
def test_exclusion_counters_at_the_primary_cutoff():
    obs = rv.observe(make_split().s2.rows(), 3, 10)
    assert obs.counts == {"rows": 87, "censored": 4, "unmapped": 2, "backlog": 12, "usable": 69}
    assert sorted(obs.included) == [10, 11, 12] and obs.excluded_small == 8     # c3 has no usable record at all


def test_exclusion_counters_at_the_curated_cutoff():
    obs = rv.observe(make_split().s2.rows(), 7, 10)
    assert obs.counts == {"rows": 87, "censored": 4, "unmapped": 2, "backlog": 3, "usable": 78}
    assert obs.excluded_small == 9


def test_per_circuit_median_p90_and_backlog_count():
    inc = rv.observe(make_split().s2.rows(), 3, 10).included
    assert (inc[10].n, inc[10].median, inc[10].p90, inc[10].backlog) == (12, 1.0, 2, 0)
    assert (inc[11].n, inc[11].median, inc[11].p90, inc[11].backlog) == (15, 2.0, 3, 2)
    assert (inc[12].n, inc[12].median, inc[12].p90) == (20, 0.5, 1)


def test_unrepaired_rows_are_censored_not_zero_days():
    rows = [Damage("S2", 1, "mapped", False, None, "pole")] * 12 + [Damage("S2", 1, "mapped", True, 2, "pole")] * 10
    obs = rv.observe(rows, 3, 10)
    assert obs.counts["censored"] == 12 and obs.counts["usable"] == 10
    assert obs.included[1].median == 2.0                      # a zero for the unrepaired rows would pull this down


def test_rows_with_an_unknown_circuit_are_counted_but_not_scored():
    rows = [Damage("S2", None, "label_unknown", True, 1, "pole")] * 15
    obs = rv.observe(rows, 3, 10)
    assert obs.counts["unmapped"] == 15 and obs.included == {} and obs.excluded_small == 0


def test_a_circuit_under_ten_records_is_excluded_and_counted():
    rows = [Damage("S2", 1, "mapped", True, 1, "pole")] * 9 + [Damage("S2", 2, "mapped", True, 1, "pole")] * 10
    obs = rv.observe(rows, 3, 10)
    assert list(obs.included) == [2] and obs.excluded_small == 1


def test_public_summary_has_shares():
    pub = rv.observe(make_split().s2.rows(), 3, 10).public()
    assert pub["backlog_share"] == pytest.approx(12 / 81) and pub["excluded_share"] == pytest.approx(1 - 69 / 87)
    assert pub["circuits_included"] == 3 and pub["excluded_small"] == 8 and pub["records_in_included_circuits"] == 47


# ---- 3.2 / 3.3 engine calls ------------------------------------------------------------------------------------------
def test_predict_uses_nearest_rank_on_the_pooled_trials_and_converts_to_days():
    p = rv.predict(FixedHoursEngine(range(1, 101)), 1, crews=3, damage=rv.OracleDamage({"pole": 1}), seed=1)
    assert p.p50_days == pytest.approx(50 / 24) and p.p90_days == pytest.approx(90 / 24)


def test_predict_returns_none_for_a_circuit_with_no_jobs():
    assert rv.predict(FixedHoursEngine([]), 1, crews=3, damage=rv.ForecastDamage(0.0), seed=1) is None


def test_forecast_damage_is_the_s1_count_scaled_to_s2_severity():
    split, engine = make_split(), StubEngine()
    rv.forecast_predictions(split, engine, crews=3, seed=1)
    seen = {c: d for c, _, d in engine.calls}
    ratio = 87 / 32
    assert seen[12].expected_events == pytest.approx(12 * ratio) and seen[1].expected_events == 0.0
    assert all(isinstance(d, rv.ForecastDamage) for d in seen.values()) and len(seen) == 12


def test_oracle_damage_is_the_actual_s2_asset_counts():
    split, engine = make_split(), StubEngine()
    rv.oracle_predictions(split, engine, [10], crews=3, seed=1)
    (_, crews, damage), = engine.calls
    assert crews == 3 and sum(damage.counts.values()) == 12           # circuit 10 had 12 damaged assets in Feb 2025
    assert set(damage.counts) <= {"primary_line", "pole", "fuse_bank", "transformer"}


def test_seeds_are_stable_and_differ_by_circuit():
    assert rv._seed(1, "forecast", 5) == rv._seed(1, "forecast", 5) != rv._seed(1, "forecast", 6)


# ---- 3.4 crews fit -----------------------------------------------------------------------------------------------------
def rich_s1():
    """10 circuits with 4 usable repairs each (median 1.5 days): identifiable."""
    rows = []
    for c in range(1, 11):
        rows += [Damage("S1", c, "mapped", True, d, "pole") for d in (1, 1, 2, 2)]
    return rows


def test_sparse_s1_is_not_identifiable_and_falls_back_to_default_crews():
    fit = rv.fit_crews(make_split().s1, StubEngine(), DEFAULT_CONFIG, 3)
    assert fit["status"] == "not_identifiable" and fit["crews"] == 3 and fit["label"] == "uncalibrated"
    assert fit["records"] == 6 and fit["circuits_with_enough_records"] == 0


def test_identifiable_s1_fits_crews_that_match_the_observed_median():
    # Stub P50 days for 4 jobs is about 24*5/crews/24 = 5/crews scaled by the 0.5..1.5 spread; the best k is the one
    # whose P50 is closest to 1.5 days.
    engine = StubEngine()
    fit = rv.fit_crews(rich_s1(), engine, FAST, 3)
    assert fit["status"] == "fitted" and fit["label"] == "fitted on Sep 2024 only" and fit["circuits"] == 10
    errs = fit["mae_by_crews"]
    assert errs[str(fit["crews"])] == min(errs.values())
    assert 1 <= fit["crews"] <= 20 and fit["ci95"][0] <= fit["crews"] <= fit["ci95"][1]


def test_fit_is_unchanged_when_s2_is_permuted():
    a = rv.run_restoration(make_split(s1=rich_s1()), StubEngine(), FAST)
    scrambled = [replace(r, repair_days=(r.repair_days or 0) + 1, circuit=((r.circuit or 1) % 12) + 1) for r in s2_rows()]
    b = rv.run_restoration(make_split(s1=rich_s1(), s2=scrambled), StubEngine(), FAST)
    assert a["cutoffs"]["3"]["fit"] == b["cutoffs"]["3"]["fit"] and a["cutoffs"]["3"]["fit"]["status"] == "fitted"


def test_fit_without_an_engine_is_marked_not_run():
    fit = rv.fit_crews(rich_s1(), None, FAST, 3)
    assert fit["status"] == "not_run" and fit["crews"] == 3 and fit["label"] == "uncalibrated"


def test_s2_ceiling_is_labeled_not_a_test():
    c = rv.s2_ceiling(make_split(), StubEngine(), FAST, 3)
    assert "not a test" in c["label"] and 1 <= c["crews"] <= 20
    assert rv.s2_ceiling(make_split(), None, FAST, 3) is None


# ---- 3.5 coverage, MAE, within one day, baselines -----------------------------------------------------------------
def test_coverage_mae_and_within_one_day_by_hand():
    obs = rv.observe(make_split().s2.rows(), 3, 10)
    preds = {10: rv.Prediction(1.0, 2.0), 11: rv.Prediction(2.0, 3.0), 12: rv.Prediction(2.5, 3.0)}
    m = rv.score_run(preds, obs, FAST)
    # c10 days <=1.0: 8 of 12; c11 <=2.0: 10 of 15; c12 <=2.5: 20 of 20 -> P50 coverage 38/47
    assert m["records"] == 47 and m["coverage_p50"] == pytest.approx(38 / 47)
    # P90: c10 <=2.0: 11; c11 <=3.0: 15; c12 <=3.0: 20 -> 46/47
    assert m["coverage_p90"] == pytest.approx(46 / 47)
    # backlog (2 rows on c11) counts as misses
    assert m["coverage_p90_backlog_as_miss"] == pytest.approx(46 / 49) and m["coverage_p50_backlog_as_miss"] == pytest.approx(38 / 49)
    # errors |1.0-1.0|=0, |2.0-2.0|=0, |2.5-0.5|=2.0
    assert m["mae_days"] == pytest.approx(2.0 / 3) and m["within_one_day"] == pytest.approx(2 / 3)
    assert m["circuits"] == 3 and m["no_prediction"] == 0


def test_circuits_without_a_prediction_are_counted_and_skipped():
    obs = rv.observe(make_split().s2.rows(), 3, 10)
    m = rv.score_run({10: rv.Prediction(1.0, 2.0), 11: None}, obs, FAST)
    assert m["circuits"] == 1 and m["no_prediction"] == 2


def test_scatter_points_are_anonymous_rounded_and_sorted():
    obs = rv.observe(make_split().s2.rows(), 3, 10)
    m = rv.score_run({10: rv.Prediction(1.04, 2.0), 11: rv.Prediction(0.51, 3.0), 12: rv.Prediction(2.5, 3.0)}, obs, FAST)
    assert m["points"] == [{"predicted_p50_days": 0.5, "observed_median_days": 2.0, "records": 15},
                           {"predicted_p50_days": 1.0, "observed_median_days": 1.0, "records": 12},
                           {"predicted_p50_days": 2.5, "observed_median_days": 0.5, "records": 20}]


def test_baselines_are_built_from_s1_only():
    base = rv.baseline_predictions(make_split().s1, [10, 11, 12], 3)
    # S1 usable rows at B=3 are the six quick repairs [0,1,1,2,3,3], median 1.5, on circuits 5, 7, 8, 9
    assert base["constant"] == {10: 1.5, 11: 1.5, 12: 1.5} and base["one_day"] == {10: 1.0, 11: 1.0, 12: 1.0}
    assert base["circuit_median"] == {10: 1.5, 11: 1.5, 12: 1.5}            # none of these circuits has S1 repairs


def test_circuit_median_baseline_uses_the_circuit_when_it_has_data():
    rows = [Damage("S1", 10, "mapped", True, d, "pole") for d in (0, 0, 4)] + [Damage("S1", 11, "mapped", True, 2, "pole")]
    base = rv.baseline_predictions(rows, [10, 11, 12], 7)
    assert base["circuit_median"][10] == 0.0 and base["circuit_median"][11] == 2.0 and base["circuit_median"][12] == 1.0


def test_a_tie_with_a_baseline_reports_does_not_beat():
    obs = rv.observe(make_split().s2.rows(), 3, 10)
    preds = {c: rv.Prediction(1.0, 2.0) for c in (10, 11, 12)}
    table = rv.baseline_table(obs, {"one_day": {c: 1.0 for c in (10, 11, 12)}}, preds, FAST, "t")
    row = table["one_day"]["model_vs_baseline"]
    assert row["mae_difference"] == 0.0 and row["beats"] is False and row["ci95"] == [0.0, 0.0]


def test_a_clear_win_over_a_baseline_is_reported():
    # 20 circuits all observed at 3 days. Model says 3, baseline says 1.
    rows = []
    for c in range(1, 21):
        rows += [Damage("S2", c, "mapped", True, 3, "pole")] * 10
    obs = rv.observe(rows, 3, 10)
    preds = {c: rv.Prediction(3.0, 4.0) for c in range(1, 21)}
    table = rv.baseline_table(obs, {"one_day": {c: 1.0 for c in range(1, 21)}}, preds, FAST, "t")
    row = table["one_day"]["model_vs_baseline"]
    assert row["mae_difference"] == pytest.approx(-2.0) and row["beats"] is True and row["ci95"][1] < 0


def test_a_worse_model_does_not_beat():
    rows = []
    for c in range(1, 21):
        rows += [Damage("S2", c, "mapped", True, 1, "pole")] * 10
    obs = rv.observe(rows, 3, 10)
    preds = {c: rv.Prediction(5.0, 6.0) for c in range(1, 21)}
    row = rv.baseline_table(obs, {"one_day": {c: 1.0 for c in range(1, 21)}}, preds, FAST, "t")["one_day"]["model_vs_baseline"]
    assert row["mae_difference"] > 0 and row["beats"] is False


# ---- 3.6 both cutoffs --------------------------------------------------------------------------------------------------
def test_run_reports_both_cutoffs_with_backlog_shares_for_both_storms():
    out = rv.run_restoration(make_split(), StubEngine(), FAST)
    assert list(out["cutoffs"]) == ["3", "7"] and out["status"] == "ok" and out["resolution_days"] == 1
    for b, backlog in (("3", 12), ("7", 3)):
        res = out["cutoffs"][b]
        assert res["backlog"]["S2"]["backlog"] == backlog and "backlog_share" in res["backlog"]["S1"]
        assert set(res["runs"]) == {"forecast", "oracle"}
        for run in res["runs"].values():
            assert {"coverage_p50", "coverage_p90", "mae_days", "within_one_day", "baselines"} <= set(run)
    assert out["cutoffs"]["3"]["backlog"]["S1"]["backlog"] == 24      # 30 mapped rows, 6 of them quick


def test_without_an_engine_everything_unmodelled_is_still_reported():
    out = rv.run_restoration(make_split(), None, FAST)
    assert out["status"] == "not_run" and "restoration-v2" in out["reason"]
    res = out["cutoffs"]["3"]
    assert res["runs"] == {} and res["baselines"]["one_day"]["mae"] is not None
    assert res["backlog"]["S2"]["usable"] == 69 and res["fit"]["status"] == "not_identifiable"


def test_histogram_bins_never_publish_a_count_under_ten():
    h = {0: 468, 1: 399, 2: 222, 3: 54, 4: 28, 5: 26, 6: 8, 80: 2, 137: 1, 142: 1}
    bins = rv.merge_small_bins(h, 10, 10)
    assert bins[-1] == {"from_day": 6, "to_day": None, "count": 12}
    assert all(b["count"] is None or b["count"] == 0 or b["count"] >= 10 for b in bins)
    assert sum(b["count"] for b in bins) == sum(h.values())
    assert [b["from_day"] for b in bins] == [0, 1, 2, 3, 4, 5, 6]


def test_histogram_with_a_tiny_total_is_suppressed_not_published():
    assert rv.merge_small_bins({1: 4, 2: 3}, 5, 10) == [{"from_day": 0, "to_day": None, "count": None}]


def test_nearest_rank_matches_the_documented_rule():
    assert stats.nearest_rank([1, 2, 3, 4], 0.5) == 2 and stats.nearest_rank([1, 2, 3, 4], 0.9) == 4
