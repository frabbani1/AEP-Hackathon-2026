import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concierge.risk import calibrate as cal
from concierge.risk.confidence import assess, downgrade, reason_text


# ---- Wilson ----------------------------------------------------------------------------------------------
def test_wilson_known_value():
    lo, hi = cal.wilson_interval(0.62, 32)
    assert lo == pytest.approx(0.448, abs=0.005) and hi == pytest.approx(0.767, abs=0.005)


def test_wilson_degenerate():
    assert cal.wilson_interval(0.5, 0) == (0.0, 1.0)
    lo, hi = cal.wilson_interval(0.0, 10)
    assert lo == 0.0 and 0 < hi < 0.4


@settings(deadline=None)
@given(p=st.floats(0, 1), n=st.integers(1, 5000))
def test_wilson_bounds_and_contains_p(p, n):
    lo, hi = cal.wilson_interval(p, n)
    assert 0.0 <= lo <= hi <= 1.0
    assert lo - 1e-9 <= p <= hi + 1e-9


@settings(deadline=None)
@given(p=st.floats(0.05, 0.95), n=st.integers(5, 2000))
def test_wilson_narrows_as_n_grows(p, n):
    w = lambda k: cal.wilson_interval(p, k)[1] - cal.wilson_interval(p, k)[0]  # noqa: E731
    assert w(n * 4) < w(n)


# ---- thresholds and bands ----------------------------------------------------------------------------------
def test_weighted_percentile():
    assert cal.weighted_percentile([1, 2, 3, 4], [1, 1, 1, 1], 0.5) == 2
    assert cal.weighted_percentile([1, 2, 3, 4], [1, 1, 1, 7], 0.5) == 4
    with pytest.raises(ValueError):
        cal.weighted_percentile([], [], 0.5)


def test_band_follows_inclusive_upper_thresholds():
    t = {"medium": 2.0, "high": 5.0}
    assert [cal.band_for(s, t) for s in (0, 2.0, 2.01, 5.0, 5.01)] == ["Low", "Low", "Medium", "Medium", "High"]


def test_thresholds_come_from_p50_p85():
    scores = list(range(1, 101))
    t = cal.compute_thresholds(scores)
    assert t == {"medium": 50, "high": 85}


def test_severity_presets_anchor_to_storm_sizes():
    p = cal.severity_presets([193, 1254])
    assert p["typical"] == 1.0
    assert p["sep-2024-like"] == pytest.approx(193 / 723.5)
    assert p["feb-2025-like"] == pytest.approx(1254 / 723.5)
    assert p["beyond-observed"] == 2.5


# ---- backtest ---------------------------------------------------------------------------------------------
SIZES = {i: 100 for i in range(1, 21)}
SIZES[21] = 10                                    # below the size floor: must be excluded
S1 = {i: (0 if i <= 10 else i - 10 if i <= 17 else 20 + 10 * (i - 18)) for i in range(1, 21)}
S2 = {i: (1 if i in (3, 4) else 0 if i <= 10 else 3 if i <= 17 else 50) for i in range(1, 21)}


def test_backtest_assigns_bands_and_hit_rates():
    rep = cal.backtest(SIZES, {**S1, 21: 99}, {**S2, 21: 0})
    assert rep["n_circuits"] == 20                     # circuit 21 excluded (size floor)
    assert [rep["bands"][b]["n_circuits"] for b in ("Low", "Medium", "High")] == [10, 7, 3]
    low, med, high = (rep["bands"][b] for b in ("Low", "Medium", "High"))
    assert (low["hit_rate"], med["hit_rate"], high["hit_rate"]) == (0.2, 1.0, 1.0)
    assert low["ci95"][0] <= low["hit_rate"] <= low["ci95"][1]
    assert cal.is_monotone(rep)


def test_hit_requires_density_not_just_any_damage():
    # 1 report on 100 premises = 10 per 1k (hit); 1 report on 1000 premises = 1 per 1k (miss at tau=2)
    rep = cal.backtest({1: 100, 2: 1000}, {1: 0, 2: 0}, {1: 1, 2: 1})
    total_hits = sum(b["hit_circuits"] for b in rep["bands"].values())
    assert total_hits == 1


def test_monotone_gate_pass_fail_and_override():
    good = {"bands": {"Low": {"hit_rate": 0.1}, "Medium": {"hit_rate": 0.5}, "High": {"hit_rate": 0.9}}}
    bad = {"bands": {"Low": {"hit_rate": 0.6}, "Medium": {"hit_rate": 0.5}, "High": {"hit_rate": 0.9}}}
    empty = {"bands": {"Low": {"hit_rate": None}, "Medium": {"hit_rate": 0.5}, "High": {"hit_rate": 0.9}}}
    assert cal.check_monotone(good) == {"monotone": True, "override": False}
    with pytest.raises(cal.BacktestError):
        cal.check_monotone(bad)
    with pytest.raises(cal.BacktestError):
        cal.check_monotone(empty)
    assert cal.check_monotone(bad, allow_non_monotone=True) == {"monotone": False, "override": True}


# ---- confidence table (one test per row) ----------------------------------------------------------------
def A(**kw):
    base = dict(has_circuit=True, circuit_mapped=True, n_premises=1000, records=30, unknown_service=False)
    base.update(kw)
    return assess(**base)


def test_row_no_circuit():
    assert A(has_circuit=False) == ("no_circuit", None)


def test_row_unmapped():
    assert A(circuit_mapped=False) == ("circuit_unmapped", None)


def test_row_too_small_beats_history():
    assert A(n_premises=49, records=100) == ("circuit_too_small", None)
    assert A(n_premises=50, records=100)[0] is None


def test_row_zero_history_small():
    assert A(n_premises=300, records=0) == ("no_history_small_circuit", None)


def test_row_zero_history_big_is_low():
    assert A(n_premises=500, records=0) == (None, "Low")


@pytest.mark.parametrize("records,level", [(20, "High"), (19, "Medium"), (5, "Medium"), (4, "Low"), (1, "Low")])
def test_record_thresholds(records, level):
    assert A(records=records) == (None, level)


def test_unknown_service_lowers_one_level():
    assert A(records=30, unknown_service=True) == (None, "Medium")
    assert A(records=2, unknown_service=True) == (None, "Low")


def test_downgrade_floor():
    assert [downgrade(x) for x in ("High", "Medium", "Low")] == ["Medium", "Low", "Low"]


def test_reason_text():
    assert reason_text(12, 2) == "12 damage reports on your line in the 2 past storms we use."
    assert reason_text(1, 2).startswith("1 damage report ")
    assert reason_text(0, 2).startswith("No damage reports")
