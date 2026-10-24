import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concierge.risk.config import DEFAULT_CONFIG as CFG
from concierge.risk.explain import contributions, factor_text, top_factors
from concierge.risk.features import PremiseInput, circuit_stats, raw_score

small = st.floats(0, 50, allow_nan=False)
chain = st.integers(0, 10)


# ---- 3.1 / 3.3 score --------------------------------------------------------------------------------------
@settings(deadline=None)
@given(r=small, p=chain, h=chain, m1=st.floats(0.25, 3), m2=st.floats(0.25, 3))
def test_score_non_decreasing_in_severity(r, p, h, m1, m2):
    lo, hi = sorted((m1, m2))
    assert raw_score(r, "Overhead", p, h, lo) <= raw_score(r, "Overhead", p, h, hi) + 1e-12


@settings(deadline=None)
@given(r1=small, r2=small, p=chain, h=chain)
def test_score_non_decreasing_in_history(r1, r2, p, h):
    lo, hi = sorted((r1, r2))
    assert raw_score(lo, "Overhead", p, h, 1) <= raw_score(hi, "Overhead", p, h, 1) + 1e-12


@settings(deadline=None)
@given(r=small, p=chain, h=chain)
def test_score_non_decreasing_in_devices_and_hops(r, p, h):
    base = raw_score(r, "Overhead", p, h, 1)
    assert raw_score(r, "Overhead", p + 1, h, 1) >= base - 1e-12
    assert raw_score(r, "Overhead", p, h + 1, 1) >= base - 1e-12


@settings(deadline=None)
@given(r=st.floats(0.01, 50), p=chain, h=chain, m=st.floats(0.25, 3))
def test_overhead_is_2_5x_underground(r, p, h, m):
    oh, ug = raw_score(r, "Overhead", p, h, m), raw_score(r, "Underground", p, h, m)
    assert oh / ug == pytest.approx(2.5)


def test_circuit_mean_score_equals_circuit_rate():
    premises = [PremiseInput(f"a{i}", 7, ["Overhead", "Underground", "Unknown"][i % 3], i % 4, 1 + i % 5)
                for i in range(90)] + [PremiseInput("b", 8, "Overhead", 1, 2)] * 0
    stats = circuit_stats(premises, {7: 18}, n_storms=2)
    c = stats[7]
    assert c.rate == pytest.approx(1000 * (18 / 2) / 90)
    mean = sum(raw_score(c.rate / c.k_bar, p.service, p.protective, p.hops, 1.0) for p in premises) / 90
    assert mean == pytest.approx(c.rate)


def test_circuit_stats_skips_premises_without_circuit():
    stats = circuit_stats([PremiseInput("x", None, "Overhead", None, None)], {}, 2)
    assert stats == {}


def test_shrinkage_pulls_small_circuits_to_regional_rate():
    from dataclasses import replace
    premises = [PremiseInput(f"a{i}", 1, "Overhead", 0, 1) for i in range(10)] + \
               [PremiseInput(f"b{i}", 2, "Overhead", 0, 1) for i in range(1000)]
    plain = circuit_stats(premises, {1: 10, 2: 0}, 1)
    shrunk = circuit_stats(premises, {1: 10, 2: 0}, 1, replace(CFG, shrinkage_k=100))
    assert shrunk[1].rate < plain[1].rate and shrunk[2].rate > plain[2].rate


# ---- 5.3 / 5.4 attribution ---------------------------------------------------------------------------------
REF = {"r_eff": 5.0, "protective": 2.0, "hops": 3.0}


@settings(deadline=None)
@given(r=st.floats(0.1, 100), p=chain, h=chain, m=st.floats(0.25, 3), svc=st.sampled_from(["Overhead", "Underground", "Unknown"]))
def test_contributions_sum_to_log_score_ratio(r, p, h, m, svc):
    c = contributions(r_eff=r, service=svc, protective=p, hops=h, multiplier=m, ref=REF)
    s = raw_score(r, svc, p, h, m)
    s_ref = raw_score(REF["r_eff"], "Overhead", REF["protective"], REF["hops"], 1.0)
    assert sum(c.values()) == pytest.approx(math.log(s / s_ref), abs=1e-9)


def test_reference_premise_has_no_contributions():
    c = contributions(r_eff=5.0, service="Overhead", protective=2, hops=3, multiplier=1.0, ref=REF)
    assert all(abs(v) < 1e-12 for v in c.values())


def test_top_factors_ranked_limited_and_labelled():
    contribs = {"hist": 1.2, "type": -0.9, "dev": 0.3, "hops": 0.04, "sev": -0.5}
    f = top_factors(contribs, rate=12.34, protective=3)
    assert [x["id"] for x in f] == ["hist", "type", "sev"]            # top 3 by |c|, hops dropped as negligible
    assert [x["direction"] for x in f] == ["raises", "lowers", "lowers"]
    assert f[0]["weight"] > f[1]["weight"] > f[2]["weight"]
    assert "12.3 reports per 1,000 homes" in f[0]["text"]


def test_negligible_factors_dropped():
    assert top_factors({"hist": 0.01, "type": 0, "dev": 0, "hops": 0, "sev": -0.04}, rate=1, protective=0) == []


def test_underground_lowers():
    c = contributions(r_eff=5, service="Underground", protective=2, hops=3, multiplier=1, ref=REF)
    f = top_factors(c, rate=5, protective=2)
    assert f and f[0]["id"] == "type" and f[0]["direction"] == "lowers"


@pytest.mark.parametrize("factor", ["hist", "type", "dev", "hops", "sev"])
@pytest.mark.parametrize("direction", ["raises", "lowers"])
def test_every_template_is_identifier_free(factor, direction):
    import re
    text = factor_text(factor, direction, rate=8.7, protective=1)
    assert not re.search(r"CIR|STATION|\d{6,}", text)


def test_zero_history_text_and_singular_device():
    assert "no storm damage reports" in factor_text("hist", "lowers", rate=0, protective=0)
    assert "1 protective device;" in factor_text("dev", "raises", rate=1, protective=1)
