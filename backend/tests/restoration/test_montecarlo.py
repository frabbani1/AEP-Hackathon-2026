"""Tasks 5.1 to 5.4: seeded draws, percentiles, trial rule, sensitivity, confidence."""
import json
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concierge.restoration import config, jobs
from concierge.restoration import model as m
from concierge.restoration import montecarlo as mc

from .fixtures import LABOR, ROWS, SEVERITY, TOKENS


def est(tree, token=TOKENS[0], **kw):
    base = dict(token=token, row=ROWS[token], severity=SEVERITY, fallback=None, base_confidence="Medium",
                labor=LABOR, tree=tree, engine="v2")
    base.update(kw)
    return m.estimate(**base)


# ---- 5.1 seeded draws ------------------------------------------------------------------------------------------------
def test_seed_is_eight_bytes_of_sha256_and_depends_on_the_premise():
    a = mc.derive_seed(engine_version="restoration-v2", token="p_00000000000000a1")
    assert a == mc.derive_seed(engine_version="restoration-v2", token="p_00000000000000a1")
    assert 0 <= a < 2**64
    assert a != mc.derive_seed(engine_version="restoration-v2", token="p_00000000000000a2")
    assert a != mc.derive_seed(engine_version="restoration-v3", token="p_00000000000000a1")


def test_streams_are_a_pure_function_of_seed_and_trial():
    a1, b1, a2, b2 = (random.Random() for _ in range(4))
    mc.trial_streams(42, 5, a1, b1)
    mc.trial_streams(42, 5, a2, b2)
    assert [a1.random() for _ in range(5)] == [a2.random() for _ in range(5)]
    mc.trial_streams(42, 6, a2, b2)
    mc.trial_streams(42, 5, a1, b1)
    assert [a1.random() for _ in range(3)] != [a2.random() for _ in range(3)]


def test_known_stream_values_do_not_drift():
    """Pins the generator and the draw order. If this fails, every stored estimate changes."""
    a, b = random.Random(), random.Random()
    mc.trial_streams(123456789, 0, a, b)
    assert round(a.random(), 12) == 0.450526203751
    assert round(b.random(), 12) == 0.455152411026


def test_poisson_inversion():
    assert mc.poisson_from_uniform(0.0, 0.5) == 0
    assert mc.poisson_from_uniform(1.0, 0.0) == 0 and mc.poisson_from_uniform(1.0, 0.30) == 0
    assert mc.poisson_from_uniform(1.0, 0.40) == 1 and mc.poisson_from_uniform(1.0, 0.75) == 2
    assert mc.poisson_from_uniform(100.0, 0.5) == 100


@settings(max_examples=100, deadline=None)
@given(mean=st.floats(0.01, 300.0), u1=st.floats(0.0, 0.999), u2=st.floats(0.0, 0.999), d=st.floats(0.0, 50.0))
def test_poisson_draw_is_monotone_in_the_uniform_and_in_the_mean(mean, u1, u2, d):
    lo, hi = sorted((u1, u2))
    assert mc.poisson_from_uniform(mean, lo) <= mc.poisson_from_uniform(mean, hi)
    assert mc.poisson_from_uniform(mean, lo) <= mc.poisson_from_uniform(mean + d, lo)


# ---- 5.2 percentiles, trials, response block ---------------------------------------------------------------------------
def test_nearest_rank_percentiles():
    ten = list(map(float, range(1, 11)))
    assert (mc.nearest_rank(ten, 50), mc.nearest_rank(ten, 90)) == (5.0, 9.0)
    eleven = list(map(float, range(1, 12)))
    assert (mc.nearest_rank(eleven, 50), mc.nearest_rank(eleven, 90)) == (6.0, 10.0)
    assert (mc.nearest_rank([7.0], 50), mc.nearest_rank([7.0], 90)) == (7.0, 7.0)
    two_hundred = list(map(float, range(1, 201)))
    assert (mc.nearest_rank(two_hundred, 50), mc.nearest_rank(two_hundred, 90)) == (100.0, 180.0)
    assert mc.percentiles([3.0, 1.0, 2.0]) == (2.0, 3.0)


@pytest.mark.parametrize("x, h", [(0.0, 1), (0.2, 1), (1.49, 1), (1.5, 2), (2.49, 2), (2.5, 3), (13.4, 13)])
def test_hours_round_half_up_with_a_floor_of_one(x, h):
    assert mc.whole_hours(x) == h


@pytest.mark.parametrize("jobs_expected, trials", [(0, 1000), (1, 1000), (60, 1000), (61, 983), (200, 300),
                                                   (300, 200), (1000, 200), (10_000, 200)])
def test_trial_rule(jobs_expected, trials):
    assert mc.trial_count(jobs_expected) == trials
    assert config.TRIALS_MIN <= mc.trial_count(jobs_expected) <= config.TRIALS_MAX


@pytest.mark.parametrize("jobs_expected, trials, n", [(1, 1000, 200), (100, 1000, 200), (200, 1000, 100),
                                                      (400, 1000, 50), (5000, 1000, 50), (1, 200, 200), (1, 150, 150)])
def test_sensitivity_trial_rule(jobs_expected, trials, n):
    assert mc.sensitivity_trial_count(jobs_expected, trials) == n


def test_response_reports_trials_and_simulation_block(tree):
    out = est(tree)
    assert out["simulation"] == {"trials": 1000, "seed_policy": "derived from inputs", "model_path": "tree"}
    out = est(tree, damage_count=400)
    assert out["simulation"]["trials"] == mc.trial_count(400) == 200


def test_same_inputs_give_identical_output_including_trials(tree):
    for kw in (dict(), dict(damage_count=17, crews=5), dict(damage=[("pole", 2)], access="remote")):
        assert json.dumps(est(tree, **kw)) == json.dumps(est(tree, **kw))


def test_range_ordering_for_every_premise_and_scenario(tree):
    for token in TOKENS:
        for kw in (dict(), dict(crews=1), dict(crews=40), dict(damage_count=1), dict(damage_count=400),
                   dict(access="remote"), dict(damage=[("primary_line", 20)])):
            e = est(tree, token=token, **kw)["estimate"]
            assert 1 <= e["p50_hours"] <= e["p90_hours"]


@settings(max_examples=40, deadline=None)
@given(crews=st.integers(1, 100), count=st.one_of(st.none(), st.integers(1, 400)),
       access=st.sampled_from(["normal", "restricted", "remote"]), token=st.sampled_from(TOKENS),
       level=st.sampled_from(["High", "Medium", "Low", None]))
def test_range_ordering_property(tree, crews, count, access, token, level):
    out = est(tree, token=token, crews=crews, damage_count=count, access=access, base_confidence=level)
    e = out["estimate"]
    assert 1 <= e["p50_hours"] <= e["p90_hours"]
    assert [s["done_by_hours"] for s in out["steps"]] == sorted(s["done_by_hours"] for s in out["steps"])
    assert out["steps"][-1]["done_by_hours"] == e["p50_hours"]


def test_steps_are_stage_medians_ending_at_the_p50(tree):
    out = est(tree, damage_count=30)
    stages = [s["stage"] for s in out["steps"]]
    assert stages[-1] == "service" and stages == sorted(stages, key=["feeder", "lateral", "service"].index)
    assert len(set(stages)) == len(stages)
    assert all(s["done_by_hours"] <= out["estimate"]["p50_hours"] for s in out["steps"])


# ---- more crews, restricted access, more damage ---------------------------------------------------------------------
def test_more_crews_never_raise_the_p50_or_the_p90(tree):
    for token in TOKENS:
        for kw in (dict(), dict(damage_count=25), dict(damage=[("pole", 3), ("primary_line", 2)]), dict(access="remote")):
            prev = None
            for crews in (1, 2, 3, 4, 6, 10, 25, 60, 100):
                e = est(tree, token=token, crews=crews, **kw)["estimate"]
                if prev:
                    assert e["p50_hours"] <= prev["p50_hours"] and e["p90_hours"] <= prev["p90_hours"]
                prev = e


def test_restricted_and_remote_access_never_lower_the_p50(tree):
    for token in TOKENS:
        for kw in (dict(), dict(damage_count=25), dict(damage=[("pole", 3)])):
            n, r, rm = (est(tree, token=token, access=a, **kw)["estimate"]["p50_hours"]
                        for a in ("normal", "restricted", "remote"))
            assert n <= r <= rm


def test_more_damage_raises_the_estimate_within_noise(tree):
    for token in TOKENS:
        low = est(tree, token=token, damage_count=5)["estimate"]["p50_hours"]
        high = est(tree, token=token, damage_count=40)["estimate"]["p50_hours"]
        assert high >= low - 1
        assert est(tree, token=token, damage_count=120)["estimate"]["p50_hours"] > low


def test_a_stronger_storm_does_not_shorten_the_estimate(tree):
    sev = lambda x: {"preset": "custom", "multiplier": x, "extrapolated": False}
    p = [est(tree, severity=sev(x))["estimate"]["p50_hours"] for x in (0.25, 1.0, 2.0, 3.0)]
    assert p == sorted(p) and p[-1] > p[0]


# ---- 5.3 sensitivity ---------------------------------------------------------------------------------------------------
def sens(out):
    return {s["change"]: s["p50_hours"] for s in out["sensitivity"]}


def test_one_more_crew_never_raises_p50_and_one_fewer_never_lowers_it(tree):
    for token in TOKENS:
        for crews in (1, 2, 3, 7, 30, 99):
            for kw in (dict(), dict(damage_count=20), dict(damage=[("pole", 2)])):
                out = est(tree, token=token, crews=crews, **kw)
                s, p50 = sens(out), out["estimate"]["p50_hours"]
                assert s["One more crew"] <= p50
                if crews > 1:
                    assert s["One fewer crew"] >= p50
                if out["scenario"]["access"] == "normal":
                    assert s["Hard-to-reach work sites"] >= p50


def test_sensitivity_list_contents_by_scenario(tree):
    out = est(tree, crews=3)
    assert [s["change"] for s in out["sensitivity"]] == ["One more crew", "One fewer crew", "Hard-to-reach work sites",
                                                         "25% more damage"]
    assert all(set(s) == {"change", "p50_hours"} for s in out["sensitivity"])
    assert "One fewer crew" not in sens(est(tree, crews=1))
    assert "One more crew" not in sens(est(tree, crews=100))
    assert "Hard-to-reach work sites" not in sens(est(tree, access="restricted"))
    assert "25% more damage" not in sens(est(tree, damage=[("pole", 1)]))
    assert "25% more damage" in sens(est(tree, damage_count=399))
    assert "25% more damage" not in sens(est(tree, damage_count=400))


def test_what_if_runs_reuse_the_base_seed(tree):
    """The headline number and a what-if that changes nothing agree: same seed, same draws."""
    plan = jobs.make_plan(circuit=tree.circuit(0), pn=6, labor=LABOR, access="normal", source="count", count=10)
    seed = mc.derive_seed(engine_version="restoration-v2", token="x")
    base = mc.run_trials(plan, crews=3, seed=seed, trials=400, sigma_shared=0.3)
    same = mc.run_trials(plan, crews=3, seed=seed, trials=200, sigma_shared=0.3)
    assert same.total == base.total[:200]
    assert mc.paired_p50(base, same, mc.percentiles(base.total)[0]) == mc.whole_hours(mc.percentiles(base.total)[0])


def test_more_crews_lower_every_trial(tree):
    plan = jobs.make_plan(circuit=tree.circuit(0), pn=7, labor=LABOR, access="normal", source="simulated",
                          mean_jobs=8.0)
    seed = mc.derive_seed(engine_version="restoration-v2", token="x")
    a = mc.run_trials(plan, crews=4, seed=seed, trials=300, sigma_shared=0.3)
    b = mc.run_trials(plan, crews=5, seed=seed, trials=300, sigma_shared=0.3)
    assert all(y <= x + 1e-9 for x, y in zip(a.total, b.total)) and sum(a.total) > sum(b.total)


# ---- 5.4 confidence ----------------------------------------------------------------------------------------------------
def width(out):
    return out["estimate"]["p90_hours"] - out["estimate"]["p50_hours"]


def test_low_confidence_gives_a_range_at_least_as_wide_as_high(tree):
    for token in TOKENS:
        for kw in (dict(), dict(crews=1), dict(crews=8), dict(damage_count=15), dict(damage_count=60, crews=6),
                   dict(damage=[("primary_line", 3), ("pole", 2)])):
            high = est(tree, token=token, base_confidence="High", **kw)
            low = est(tree, token=token, base_confidence="Low", **kw)
            assert width(low) >= width(high), (token, kw)
            assert low["confidence"]["level"] == "Low" and high["confidence"]["level"] == "High"
            # Medium sits between them, give or take the one-hour rounding of each end
            med = est(tree, token=token, base_confidence="Medium", **kw)
            assert width(high) - 1 <= width(med) <= width(low) + 1, (token, kw)


def test_unknown_confidence_is_treated_as_low(tree):
    assert est(tree, base_confidence=None)["estimate"] == est(tree, base_confidence="Low")["estimate"]


def test_a_line_without_damage_history_is_low_confidence_with_a_note(tree):
    quiet = {**ROWS[TOKENS[0]], "R": 0.0}
    out = m.estimate(token=TOKENS[0], row=quiet, severity=SEVERITY, fallback=None, base_confidence="High", labor=LABOR,
                     tree=tree, engine="v2")
    assert out["confidence"]["level"] == "Low"
    assert any("no damage history" in t for t in out["missing_data"])
    assert out["estimate"]["p50_hours"] >= 1                  # the on-path repair still gives an answer


def test_shared_sigma_values_are_the_documented_assumptions():
    assert config.SHARED_SIGMA == {"High": 0.25, "Medium": 0.30, "Low": 0.40}
    assert config.JOB_SIGMA == 0.25 and config.CREW_PRESENT_PROBABILITY == 0.85
    assert config.CLOCK_FACTOR == 1.5 and config.WORKING_HOURS_PER_DAY == 16.0
