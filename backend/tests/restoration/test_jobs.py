"""Tasks 3.1 to 3.3: pricing from the labor sheet, job generation by mode, input validation."""
import json
import random
from pathlib import Path

import pytest

from concierge.restoration import config, jobs
from concierge.restoration import model as m
from concierge.restoration.common import RestorationError
from concierge.restoration.montecarlo import draw_jobs, trial_streams
from concierge.restoration.tree import LEVEL_INDEX

from .fixtures import LABOR, POLE_HOURS, PRIMARY_HOURS, ROWS, SEVERITY, make_labor


# ---- 3.1 pricing -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("asset, direct", [
    ("pole", 1.5), ("fuse_bank", 1.5), ("transformer", 1.5), ("recloser", 1.5), ("switch", 1.5), ("light", 1.5),
    ("step_transformer", 1.5), ("primary_line", 6.0), ("secondary_line", 3.0), ("capacitor_bank", 3.0),
])
def test_job_hours_is_direct_hours_times_the_multiplier(asset, direct):
    assert jobs.job_hours(asset, LABOR, "normal") == pytest.approx(direct * 4.5)
    assert jobs.job_hours("pole", LABOR, "normal") == pytest.approx(POLE_HOURS)
    assert jobs.job_hours("primary_line", LABOR, "normal") == pytest.approx(PRIMARY_HOURS)


def test_adder_applies_once_per_job_before_the_multiplier():
    for asset in jobs.ASSETS:
        base = jobs.job_hours(asset, LABOR, "normal")
        assert jobs.job_hours(asset, LABOR, "restricted") == pytest.approx(base + 0.5 * 4.5)
        assert jobs.job_hours(asset, LABOR, "remote") == pytest.approx(base + 1.312 * 4.5)


def test_multiplier_is_read_from_the_sheet():
    six = make_labor(multiplier=6.0)
    assert jobs.multiplier(six) == 6.0
    assert jobs.job_hours("pole", six, "normal") == pytest.approx(1.5 * 6.0)
    assert jobs.job_hours("pole", six, "restricted") == pytest.approx((1.5 + 0.5) * 6.0)


@pytest.mark.parametrize("bad", [None, 0, -1, True])
def test_a_sheet_without_a_usable_multiplier_is_an_error(bad):
    sheet = {**LABOR, "indirect_labor_multiplier": bad}
    with pytest.raises(RestorationError):
        jobs.job_hours("pole", sheet, "normal")


def test_missing_labor_unit_is_an_error_and_no_estimate(tree):
    sheet = make_labor(drop="fuse_cutout")
    with pytest.raises(RestorationError):
        jobs.job_hours("fuse_bank", sheet, "normal")
    row = ROWS["p_00000000000000a1"]
    with pytest.raises(RestorationError):            # simulated damage uses the whole mix, so it needs every unit
        m.estimate(token="p_00000000000000a1", row=row, severity=SEVERITY, fallback=None, base_confidence="High",
                   labor=sheet, tree=tree, engine="v2")


def test_missing_adder_is_an_error():
    sheet = {**LABOR, "adders": [a for a in LABOR["adders"] if a["key"] != "remote_location_adder"]}
    assert jobs.job_hours("pole", sheet, "restricted") > 0
    with pytest.raises(RestorationError):
        jobs.job_hours("pole", sheet, "remote")


REAL_LABOR = Path(__file__).resolve().parents[3] / "artifacts" / "curated" / "labor_standards.json"


@pytest.mark.skipif(not REAL_LABOR.exists(), reason="run scripts/curate.py first")
@pytest.mark.realdata
def test_adders_match_the_real_labor_sheet():
    sheet = json.loads(REAL_LABOR.read_text())
    adders = {a["key"]: a["hours"] for a in sheet["adders"]}
    ilm = sheet["indirect_labor_multiplier"]
    assert ilm == 4.5 and adders["access_constraint_adder"] == 0.5 and adders["remote_location_adder"] == 1.312
    pole = next(u for u in sheet["units"] if u["category"] == "pole" and u["size_group"] == "Medium")
    direct = pole["install_hours"] + pole["remove_hours"]
    assert jobs.job_hours("pole", sheet, "normal") == pytest.approx(direct * ilm)
    assert jobs.job_hours("pole", sheet, "restricted") == pytest.approx((direct + 0.5) * ilm)
    assert jobs.job_hours("pole", sheet, "remote") == pytest.approx((direct + 1.312) * ilm)
    for asset in jobs.ASSETS:                       # the real sheet can price every asset type
        assert jobs.job_hours(asset, sheet, "normal") > 0


# ---- 3.2 job generation ------------------------------------------------------------------------------------------------
def plan_for(tree, pn=6, source="simulated", **kw):
    circuit = tree.circuit(0)
    return jobs.make_plan(circuit=circuit, pn=pn, labor=LABOR, access=kw.pop("access", "normal"), source=source, **kw)


def draws(plan, n=300, sigma=0.3, seed=7):
    rng_a, rng_b = random.Random(), random.Random()
    for t in range(n):
        trial_streams(seed, t, rng_a, rng_b)
        yield draw_jobs(plan, rng_a, sigma)


def test_damage_count_gives_exactly_that_many_jobs_every_trial(tree):
    plan = plan_for(tree, source="count", count=12)
    for nodes, assets, hours in draws(plan):
        assert len(nodes) == len(assets) == len(hours) == 12
        assert any(v in plan.path.node_set for v in nodes)          # always at least one repair on the path


def test_count_of_one_always_lands_on_the_path(tree):
    plan = plan_for(tree, source="count", count=1)
    for nodes, assets, _ in draws(plan):
        assert len(nodes) == 1 and nodes[0] in plan.path.node_set


def test_simulated_job_count_follows_the_poisson_mean(tree):
    plan = plan_for(tree, source="simulated", mean_jobs=20.0)
    counts = [len(n) for n, _, _ in draws(plan, n=1500)]
    mean = sum(counts) / len(counts)
    var = sum((c - mean) ** 2 for c in counts) / len(counts)
    assert mean == pytest.approx(20.0, abs=0.5) and var == pytest.approx(20.0, rel=0.2)


def test_large_poisson_mean_uses_the_normal_approximation(tree):
    plan = plan_for(tree, source="simulated", mean_jobs=120.0)
    counts = [len(n) for n, _, _ in draws(plan, n=800)]
    assert sum(counts) / len(counts) == pytest.approx(120.0, abs=2.0)


def test_simulated_jobs_are_capped(tree):
    plan = plan_for(tree, source="simulated", mean_jobs=5000.0)
    assert max(len(n) for n, _, _ in draws(plan, n=20)) <= config.SIMULATED_JOBS_CAP + 1


def test_each_job_lands_on_a_node_of_its_assets_level(tree):
    plan = plan_for(tree, source="count", count=60)
    circuit = tree.circuit(0)
    placed = set()
    for nodes, assets, _ in draws(plan, n=100):
        for v, a in zip(nodes, assets):
            level = LEVEL_INDEX[jobs.ASSET_LEVEL[plan.assets[a]]]
            assert circuit.level[v] == level, "job placed on the wrong level"
            placed.add(v)
    assert placed == set(range(8))                                    # every node is reachable, none is skipped


def test_placement_is_uniform_over_the_nodes_of_a_level(tree):
    plan = plan_for(tree, pn=7, source="count", count=40)
    hits = {3: 0, 5: 0}                                               # the two lateral nodes
    for nodes, assets, _ in draws(plan, n=400):
        for v, a in zip(nodes, assets):
            if jobs.ASSET_LEVEL[plan.assets[a]] == "lateral":
                hits[v] += 1
    total = sum(hits.values())
    assert hits[3] / total == pytest.approx(0.5, abs=0.04)


def test_asset_mix_follows_the_history(tree):
    plan = plan_for(tree, source="count", count=100)
    counts = {}
    for _, assets, _ in draws(plan, n=300):
        for a in assets:
            counts[plan.assets[a]] = counts.get(plan.assets[a], 0) + 1
    total = sum(counts.values())
    share = jobs.ASSET_COUNTS["primary_line"] / sum(jobs.ASSET_COUNTS.values())
    assert counts["primary_line"] / total == pytest.approx(share, abs=0.02)


def test_one_repair_is_added_on_the_path_when_none_was_drawn(tree):
    plan = plan_for(tree, source="simulated", mean_jobs=0.0001)       # almost never any damage
    levels = set()
    for nodes, assets, hours in draws(plan, n=300):
        assert len(nodes) == 1 and nodes[0] in plan.path.node_set and hours[0] > 0
        assert nodes[0] == plan.path_nodes[assets[0]]                 # at the path node of the repair's own level
        levels.add(jobs.ASSET_LEVEL[plan.assets[assets[0]]])
    assert levels == {"feeder", "lateral", "service"}                 # drawn from the whole historical mix


def test_every_trial_has_a_repair_on_the_customers_path(tree):
    for pn in (0, 1, 3, 4, 6, 7):
        for kw in (dict(source="simulated", mean_jobs=0.5), dict(source="simulated", mean_jobs=6.0),
                   dict(source="count", count=1), dict(source="count", count=7)):
            plan = plan_for(tree, pn=pn, **kw)
            for nodes, assets, hours in draws(plan, n=120):
                assert any(v in plan.path.node_set for v in nodes)
                assert all(h > 0 for h in hours) and len(nodes) == len(assets) == len(hours)


def test_a_trial_without_a_path_repair_moves_one_drawn_repair_onto_the_path(tree):
    """Count of one: the single repair is the cause, so it sits on the path at the path node of its own level."""
    plan = plan_for(tree, pn=6, source="count", count=1)               # path 6 (service), 3 (lateral), 1, 0 (feeder)
    seen = set()
    for nodes, assets, _ in draws(plan, n=600):
        level = jobs.ASSET_LEVEL[plan.assets[assets[0]]]
        assert nodes[0] in (6, 3, 1, 0) and len(nodes) == 1
        if nodes[0] == plan.path.pn or nodes[0] in (3, 1):             # the only nodes of each level on this path
            seen.add((level, nodes[0]))
    assert {("service", 6), ("lateral", 3), ("feeder", 1)} <= seen


def test_relocation_keeps_the_job_count_and_every_job_on_its_level_or_above(tree):
    circuit = tree.circuit(0)
    for pn in (3, 7):                         # pn 3 has no service node on its path, so service repairs move up a level
        plan = plan_for(tree, pn=pn, source="count", count=8)
        for nodes, assets, _ in draws(plan, n=300):
            assert len(nodes) == 8
            for v, a in zip(nodes, assets):
                assert circuit.level[v] <= LEVEL_INDEX[jobs.ASSET_LEVEL[plan.assets[a]]]


def test_customer_path_mode_places_every_job_on_the_customers_path(tree):
    damage = [("pole", 2), ("primary_line", 1), ("transformer", 1)]
    plan = plan_for(tree, pn=6, source="path", damage=damage)         # path 6 (service), 3 (lateral), 1, 0 (feeder)
    for nodes, assets, hours in draws(plan, n=50):
        assert sorted(nodes) == sorted([3, 3, 1, 6])
        assert len(hours) == 4
    # no lateral or service node on this path: the repair moves up to the nearest level the path has
    plan = plan_for(tree, pn=1, source="path", damage=[("transformer", 1), ("pole", 1)])
    assert sorted(plan.fixed_nodes) == [1, 1]
    plan = plan_for(tree, pn=3, source="path", damage=[("transformer", 1)])        # no service node on the path
    assert plan.fixed_nodes == (3,)


def test_customer_path_mode_adds_nothing_elsewhere(tree):
    plan = plan_for(tree, pn=6, source="path", damage=[("pole", 1)])
    assert all(len(n) == 1 for n, _, _ in draws(plan, n=50))


def test_same_seed_gives_the_same_jobs_and_a_longer_list_only_appends(tree):
    small = plan_for(tree, source="count", count=5)
    big = plan_for(tree, source="count", count=9)
    a = list(draws(small, n=30))
    b = list(draws(big, n=30))
    assert a == list(draws(small, n=30))
    for (na, aa, ha), (nb, ab, hb) in zip(a, b):
        # unless a repair was moved onto the path, the first four jobs are unchanged
        if len(na) and na[:4] == nb[:4]:
            assert aa[:4] == ab[:4] and ha[:4] == hb[:4]


# ---- 3.3 input validation --------------------------------------------------------------------------------------------
def call(**over):
    kw = dict(token="p_00000000000000a1", row=ROWS["p_00000000000000a1"], severity=SEVERITY, fallback=None,
              base_confidence="Medium", labor=LABOR, engine="v2")
    kw.update(over)
    return m.estimate(**kw)


def test_both_damage_inputs_are_rejected(tree):
    with pytest.raises(RestorationError) as e:
        call(tree=tree, damage=[("pole", 1)], damage_count=5)
    assert str(e.value) == "send either damage_count or damage, not both"


@pytest.mark.parametrize("value", [0, -3, 401, 987654, True, 5.0, "5", [5]])
def test_damage_count_range_is_checked_without_echo(tree, value):
    with pytest.raises(RestorationError) as e:
        call(tree=tree, damage_count=value)
    assert str(e.value) == "damage_count must be a whole number from 1 to 400"
    assert "987654" not in str(e.value) and str(value) not in str(e.value).replace("1 to 400", "")


@pytest.mark.parametrize("bad, message", [
    (dict(crews=0), "crews must be between 1 and 100"),
    (dict(crews=101), "crews must be between 1 and 100"),
    (dict(access="muddy"), "access must be one of ['normal', 'restricted', 'remote']"),
    (dict(damage=[("meteor", 1)]), "damage items need a known asset_type and a quantity from 1 to 20"),
    (dict(damage=[("pole", 21)]), "damage items need a known asset_type and a quantity from 1 to 20"),
])
def test_other_validation_messages_are_plain(tree, bad, message):
    with pytest.raises(RestorationError) as e:
        call(tree=tree, **bad)
    assert str(e.value) == message


def test_boundary_values_are_accepted(tree):
    for kw in (dict(damage_count=1), dict(damage_count=400), dict(crews=1), dict(crews=100)):
        assert call(tree=tree, **kw)["status"] == "ok"


def test_validation_runs_before_the_fallback(tree):
    with pytest.raises(RestorationError):
        call(tree=tree, row=None, token=None, crews=0)
