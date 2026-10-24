"""Tasks 4.1 to 4.3: availability, priority order, early stop, per-level milestones, properties.

All expected values are worked out by hand on the fixture tree (see fixtures.py):

    node   0   1   2   3   4   5   6   7
    served 100 60  40  30  20  25  12  15
    parent -   0   0   1   1   2   3   5
    depth  0   1   1   2   2   2   3   3
    level  f   f   f   l   s   l   s   s          (f feeder, l lateral, s service)
"""
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concierge.restoration import jobs
from concierge.restoration.montecarlo import crews_at_work, draw_jobs, run_trials, trial_streams
from concierge.restoration.schedule import PathInfo, schedule_trial

from .fixtures import LABOR


@pytest.fixture()
def circuit(tree):
    return tree.circuit(0)


def run(circuit, pn, spec):
    """spec: list of (node, hours). Returns (milestones, order of job indexes)."""
    order = []
    got = schedule_trial(circuit, PathInfo(circuit, pn), [v for v, _ in spec], [h for _, h in spec], order)
    return got, order


# ---- 4.1 availability and priority -----------------------------------------------------------------------------------
def test_chain_with_branches_by_hand(circuit):
    # jobs: 0 -> node1 6h (ratio 10), 1 -> node3 3h (10), 2 -> node6 2h, 3 -> node4 4h (5, off the customer's path)
    # Only node1 is available at first. After it: node3 (10) beats node4 (5). After node3: node6 (6) beats node4 (5).
    got, order = run(circuit, 6, [(1, 6.0), (3, 3.0), (6, 2.0), (4, 4.0)])
    assert order == [0, 1, 2]
    assert got == (6.0, 9.0, 11.0)        # main line done at 6, branch at 9, power back at 11 crew-hours


def test_upstream_job_blocks_a_downstream_job_with_a_better_ratio(circuit):
    # main line node1: 30h (ratio 2). lateral node3: 1h (ratio 30). The lateral cannot start first.
    got, order = run(circuit, 3, [(3, 1.0), (1, 30.0)])
    assert order == [1, 0]
    assert got == (30.0, 31.0, 31.0)


def test_blocking_is_transitive_through_a_node_without_jobs(circuit):
    # node0 has a job, node1 has none, node6 is two levels below: node6 waits for node0 even though node1 is idle
    got, order = run(circuit, 6, [(6, 0.1), (0, 9.0)])
    assert order == [1, 0] and got == (9.0, 9.0, 9.1)


def test_higher_ratio_goes_first_among_available_jobs(circuit):
    # node3 (30 homes) 6h -> 5/h; node4 (20 homes) 2h -> 10/h. Both are available; node4 first.
    got, order = run(circuit, 3, [(3, 6.0), (4, 2.0)])
    assert order == [1, 0]
    assert got == (0.0, 8.0, 8.0)


def test_scheduling_stops_at_the_customers_last_path_job(circuit):
    # node3 (15/h) beats node4 (10/h). The customer sits at node3, so node4 is never reached and adds nothing.
    got, order = run(circuit, 3, [(3, 2.0), (4, 2.0)])
    assert order == [0] and got == (0.0, 2.0, 2.0)


def test_equal_ratio_goes_to_the_shallower_node_then_the_lower_index(circuit):
    # node2 (depth 1): 40/4 = 10. node3 (depth 2): 30/3 = 10. Tie: the shallower node2 first, so the customer waits 4h.
    got, order = run(circuit, 3, [(3, 3.0), (2, 4.0)])
    assert order == [1, 0] and got[2] == 7.0
    # node3 and node4 are both depth 2 with ratio 10: the lower index (node3) goes first
    got, order = run(circuit, 4, [(4, 2.0), (3, 3.0)])
    assert order == [1, 0] and got[2] == 5.0


def test_equal_ratio_same_node_goes_to_the_lower_job_index(circuit):
    got, order = run(circuit, 3, [(3, 4.0), (3, 4.0)])
    assert order == [0, 1] and got[2] == 8.0


def test_jobs_at_one_node_are_all_available_together_and_ordered_by_ratio(circuit):
    got, order = run(circuit, 3, [(3, 4.0), (3, 2.0), (3, 8.0)])
    assert order == [1, 0, 2] and got[2] == 14.0


def test_unrelated_high_ratio_job_runs_first_and_adds_to_the_customers_time(circuit):
    # node7 (15 homes) 0.5h = 30/h beats the customer's node3 job (5/h): 0.5 + 6 = 6.5 crew-hours
    got, order = run(circuit, 3, [(3, 6.0), (7, 0.5)])
    assert order == [1, 0] and got[2] == 6.5


def test_jobs_below_the_customers_last_path_job_never_run(circuit):
    got, order = run(circuit, 3, [(3, 6.0), (6, 0.1), (4, 0.1)])
    assert 1 not in order                      # node6 is below node3 and waits for it; the customer stops first
    assert got[2] <= 6.2


def test_no_job_on_the_path_means_no_time(circuit):
    assert run(circuit, 6, [(7, 3.0), (5, 3.0)]) == ((0.0, 0.0, 0.0), [])
    assert run(circuit, 6, []) == ((0.0, 0.0, 0.0), [])


# ---- 4.2 early stop and per-level milestones --------------------------------------------------------------------------
def test_milestones_by_level_on_the_path(circuit):
    # feeder job at node0 (4h), lateral job at node3 (2h), service job at node6 (1h). Customer at node6.
    got, order = run(circuit, 6, [(6, 1.0), (3, 2.0), (0, 4.0)])
    assert order == [2, 1, 0] and got == (4.0, 6.0, 7.0)


def test_a_level_without_path_jobs_takes_the_previous_milestone(circuit):
    got, _ = run(circuit, 6, [(0, 4.0), (6, 1.0)])           # no lateral job on the path
    assert got == (4.0, 4.0, 5.0)
    got, _ = run(circuit, 6, [(3, 2.0)])                     # lateral only
    assert got == (0.0, 2.0, 2.0)


def test_milestones_never_decrease_and_the_last_is_the_total(circuit):
    got, order = run(circuit, 7, [(7, 1.0), (5, 2.0), (2, 3.0), (0, 4.0), (3, 9.0), (4, 9.0)])
    assert got[0] <= got[1] <= got[2]
    assert order[-1] in (0, 1, 2, 3)                          # last scheduled job is on the path
    assert got[2] == 10.0                                    # 4 + 3 + 2 + 1; the unrelated jobs have a lower ratio


def test_per_trial_minimum_is_the_path_work_and_maximum_is_all_work(circuit):
    spec = [(7, 1.0), (5, 2.0), (2, 3.0), (0, 4.0), (3, 9.0), (4, 9.0)]
    got, _ = run(circuit, 7, spec)
    assert 1.0 + 2.0 + 3.0 + 4.0 <= got[2] <= sum(h for _, h in spec)


# ---- 4.3 properties ------------------------------------------------------------------------------------------------------
job_specs = st.lists(st.tuples(st.integers(0, 7), st.floats(0.25, 40.0, allow_nan=False, allow_infinity=False)),
                     min_size=1, max_size=14)


@settings(max_examples=200, deadline=None)
@given(pn=st.integers(0, 7), spec=job_specs, k=st.sampled_from([1, 2, 4, 8, 16]))
def test_elapsed_time_does_not_fall_when_all_job_hours_grow(tree, pn, spec, k):
    circuit = tree.circuit(0)
    base, _ = run(circuit, pn, spec)
    scaled, _ = run(circuit, pn, [(v, h * k) for v, h in spec])      # powers of two keep every ratio exact
    assert scaled[2] >= base[2] and scaled[2] == pytest.approx(base[2] * k)


@settings(max_examples=200, deadline=None)
@given(pn=st.integers(0, 7), spec=job_specs)
def test_schedule_respects_dependencies_and_bounds(tree, pn, spec):
    circuit = tree.circuit(0)
    path = PathInfo(circuit, pn)
    got, order = run(circuit, pn, spec)
    nodes = [v for v, _ in spec]
    pos = {j: i for i, j in enumerate(order)}
    # a job that ran had every job on its strict ancestors done before it
    for j in order:
        for i, v in enumerate(nodes):
            if v in circuit.path(nodes[j])[1:]:
                assert i in pos and pos[i] < pos[j]
    path_work = sum(h for v, h in spec if v in path.node_set)
    if path_work:
        assert path_work - 1e-9 <= got[2] <= sum(h for _, h in spec) + 1e-9
        assert got[0] <= got[1] <= got[2]
    else:
        assert got == (0.0, 0.0, 0.0)


@settings(max_examples=60, deadline=None)
@given(seed=st.integers(0, 2**32), crews=st.integers(1, 12), mean=st.floats(0.5, 15.0), pn=st.sampled_from([3, 4, 6, 7]))
def test_elapsed_time_does_not_rise_when_crews_are_added(tree, seed, crews, mean, pn):
    plan = jobs.make_plan(circuit=tree.circuit(0), pn=pn, labor=LABOR, access="normal", source="simulated",
                          mean_jobs=mean)
    few = run_trials(plan, crews=crews, seed=seed, trials=25, sigma_shared=0.3)
    more = run_trials(plan, crews=crews + 1, seed=seed, trials=25, sigma_shared=0.3)
    assert all(b <= a + 1e-9 for a, b in zip(few.total, more.total))
    assert all(b <= a + 1e-9 for a, b in zip(few.feeder, more.feeder))


@settings(max_examples=60, deadline=None)
@given(seed=st.integers(0, 2**32), crews=st.integers(1, 100))
def test_crews_at_work_never_falls_when_crews_are_added(seed, crews):
    a, b = random.Random(), random.Random()
    trial_streams(seed, 0, a, b)
    few = crews_at_work(b, crews)
    trial_streams(seed, 0, a, b)
    assert crews_at_work(b, crews + 1) >= few >= 1


@settings(max_examples=150, deadline=None)
@given(seed=st.integers(0, 2**32), mean=st.floats(0.0, 4.0), pn=st.integers(0, 7), count=st.integers(0, 3))
def test_every_trial_puts_a_repair_on_the_customers_path(tree, seed, mean, pn, count):
    circuit = tree.circuit(0)
    sources = [dict(source="simulated", mean_jobs=mean)]
    if count:
        sources.append(dict(source="count", count=count))
    for kw in sources:
        plan = jobs.make_plan(circuit=circuit, pn=pn, labor=LABOR, access="normal", **kw)
        rng_a, rng_b = random.Random(), random.Random()
        for t in range(10):
            trial_streams(seed, t, rng_a, rng_b)
            nodes, assets, hours = draw_jobs(plan, rng_a, 0.3)
            assert any(v in plan.path.node_set for v in nodes)
            if kw["source"] == "count":
                assert len(nodes) == count
            path_jobs = [i for i, v in enumerate(nodes) if v in plan.path.node_set]
            for i in path_jobs:
                assert hours[i] > 0
            schedule, _ = run(circuit, pn, list(zip(nodes, hours)))
            assert schedule[2] > 0
