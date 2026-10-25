"""The circuit-level hook into the spec-03 engine (tasks 3.2 and 3.3 of spec-04), on spec-03's own fixture tree.

Fixture tree (tests/restoration/fixtures.py), levels f feeder, l lateral, s service:

    node   0   1   2   3   4   5   6   7
    level  f   f   f   l   s   l   s   s
"""
import importlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from concierge.backtest import adapter
from concierge.restoration import circuit as circ, config
from concierge.restoration.jobs import ASSET_LEVEL
from concierge.restoration.schedule import PathInfo, schedule_trial
from concierge.restoration.tree import RestorationTree

from ..restoration.fixtures import LABOR, TREE_DICT, make_devices

LEVEL_OF_NODE = TREE_DICT["circuits"]["0"]["level"]
ROOT = Path(__file__).resolve().parents[3]
REAL = ROOT / "data" / "curated" / "tables"


@pytest.fixture()
def engine():
    return circ.CircuitEngine(RestorationTree.from_dict(TREE_DICT), {6060: 0}, LABOR)


def oracle(**counts):
    return SimpleNamespace(counts=counts)


def forecast(n):
    return SimpleNamespace(expected_events=n)


@pytest.fixture()
def captured(monkeypatch):
    """Record every trial's job nodes and hours handed to the scheduler."""
    calls = []

    def spy(tree, path, nodes, hours, order=None):
        calls.append((list(nodes), list(hours)))
        return schedule_trial(tree, path, nodes, hours, order)

    monkeypatch.setattr(circ, "schedule_trial", spy)
    return calls


# ---- the whole circuit as the path --------------------------------------------------------------------------------------
def test_whole_circuit_scheduling_runs_every_job_and_returns_their_total(engine):
    tree = engine._tree.circuit(0)
    whole = circ.WholeCircuit(tree)
    spec = [(7, 1.0), (5, 2.0), (2, 3.0), (0, 4.0), (3, 9.0), (4, 9.0), (6, 0.5)]
    order = []
    got = schedule_trial(tree, whole, [v for v, _ in spec], [h for _, h in spec], order)
    assert got[2] == pytest.approx(28.5) and sorted(order) == list(range(7))        # nothing is skipped
    # upstream first: a node's job never runs before a job on a strict ancestor
    pos = {j: i for i, j in enumerate(order)}
    for j, (v, _) in enumerate(spec):
        for i, (u, _) in enumerate(spec):
            if u in tree.path(v)[1:]:
                assert pos[i] < pos[j]


def test_customer_path_would_have_stopped_early(engine):
    tree = engine._tree.circuit(0)
    spec = [(3, 6.0), (7, 0.5)]
    one_home = schedule_trial(tree, PathInfo(tree, 3),
                              [v for v, _ in spec], [h for _, h in spec])
    whole = schedule_trial(tree, circ.WholeCircuit(tree), [v for v, _ in spec], [h for _, h in spec])
    assert one_home[2] == 6.5 and whole[2] == 6.5            # node 7 had the better ratio, so it ran first either way
    spec = [(3, 2.0), (4, 2.0)]
    one_home = schedule_trial(tree, PathInfo(tree, 3),
                              [v for v, _ in spec], [h for _, h in spec])
    whole = schedule_trial(tree, circ.WholeCircuit(tree), [v for v, _ in spec], [h for _, h in spec])
    assert one_home[2] == 2.0 and whole[2] == 4.0            # the circuit total includes the job the customer skips


# ---- oracle damage ---------------------------------------------------------------------------------------------------
def test_oracle_places_each_asset_on_a_node_of_its_level(engine, captured):
    hours = engine.completion_hours(6060, crews=3, damage=oracle(pole=2, primary_line=1, transformer=3), seed=4)
    assert len(hours) == 1000 and all(h > 0 for h in hours)
    want = Counter({"lateral": 2, "feeder": 1, "service": 3})
    for nodes, _ in captured:
        assert len(nodes) == 6
        assert Counter(LEVEL_OF_NODE[v] for v in nodes) == want


def test_oracle_job_hours_come_from_the_labor_sheet(engine, captured):
    engine.completion_hours(6060, crews=1, damage=oracle(pole=1), seed=1)
    hrs = sorted(h[0] for _, h in captured)
    assert 6.75 * 0.3 < hrs[len(hrs) // 2] < 6.75 * 1.7           # pole job = 1.5 h x 4.5, times the shared and job factors


def test_oracle_ignores_unknown_assets_and_returns_nothing_for_an_empty_list(engine):
    assert engine.completion_hours(6060, crews=3, damage=oracle(), seed=1) == []
    assert engine.completion_hours(6060, crews=3, damage=oracle(not_an_asset=4), seed=1) == []


# ---- forecast damage ---------------------------------------------------------------------------------------------------
def test_forecast_draws_a_poisson_count_with_at_least_one_repair(engine, captured):
    engine.completion_hours(6060, crews=3, damage=forecast(5.0), seed=2)
    counts = [len(n) for n, _ in captured]
    assert min(counts) >= 1 and 4.5 < sum(counts) / len(counts) < 5.6       # mean of a Poisson(5) cut at 1 is about 5.03


def test_forecast_with_no_history_uses_the_engine_floor(engine, captured):
    hours = engine.completion_hours(6060, crews=3, damage=forecast(0.0), seed=2)
    assert len(hours) == 1000 and all(len(n) >= 1 for n, _ in captured)
    assert sum(len(n) for n, _ in captured) / len(captured) < 1.2           # almost always a single repair


def test_trial_count_follows_the_spec03_rule(engine):
    assert len(engine.completion_hours(6060, crews=3, damage=forecast(5.0), seed=1)) == 1000
    assert len(engine.completion_hours(6060, crews=3, damage=oracle(pole=300), seed=1)) == config.TRIALS_MIN


# ---- determinism and direction -----------------------------------------------------------------------------------------
def test_same_seed_same_answer_and_a_new_seed_changes_it(engine):
    a = engine.completion_hours(6060, crews=3, damage=forecast(4.0), seed=9)
    assert a == engine.completion_hours(6060, crews=3, damage=forecast(4.0), seed=9)
    assert a != engine.completion_hours(6060, crews=3, damage=forecast(4.0), seed=10)


def test_more_crews_never_slow_any_trial(engine):
    for dmg in (forecast(6.0), oracle(pole=3, fuse_bank=2, transformer=2)):
        few = engine.completion_hours(6060, crews=2, damage=dmg, seed=3)
        more = engine.completion_hours(6060, crews=3, damage=dmg, seed=3)
        assert len(few) == len(more) and all(b <= a + 1e-9 for a, b in zip(few, more))


def test_more_damage_raises_the_median(engine):
    low = engine.p50_p90_hours(6060, crews=3, damage=oracle(pole=2), seed=3)
    high = engine.p50_p90_hours(6060, crews=3, damage=oracle(pole=20), seed=3)
    assert high[0] > low[0] and low[0] <= low[1] and high[0] <= high[1]


def test_unknown_circuit_and_bad_crews(engine):
    assert engine.completion_hours(1, crews=3, damage=forecast(3.0), seed=1) == []
    assert not engine.has_circuit(1) and engine.has_circuit(6060)
    with pytest.raises(ValueError):
        engine.completion_hours(6060, crews=0, damage=forecast(3.0), seed=1)


# ---- circuit numbers to tree indexes -------------------------------------------------------------------------------------
def test_circuit_index_matches_the_tree_builder():
    from concierge.curate.restoration_tree import build_tree
    devices = make_devices()
    index = circ.circuit_index(devices)
    assert index == {6060: 0, 7071: 1}                      # 7070 serves 4 premises and is dropped
    doc, _, _ = build_tree(devices, [])
    assert doc["circuits"]["0"]["served"][0] == 100 and doc["circuits"]["1"]["served"][0] == 20


def test_load_engine_reads_parquet_and_labor(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    tables = tmp_path / "tables"
    tables.mkdir()
    pq.write_table(pa.Table.from_pylist(make_devices()), tables / "device_dim.parquet")
    (tmp_path / "labor_standards.json").write_text(json.dumps(LABOR))
    e = circ.load_engine(tables)
    assert e.has_circuit(6060) and e.has_circuit(7071) and not e.has_circuit(7070)
    assert e.engine_version == "restoration-v2"
    assert len(e.completion_hours(6060, crews=3, damage=forecast(3.0), seed=1)) > 0


# ---- the adapter -----------------------------------------------------------------------------------------------------------
def test_adapter_returns_none_only_when_the_hook_module_is_missing(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(importlib, "import_module", missing)
    assert adapter.load_engine("anywhere") is None

    def broken(name):
        raise ModuleNotFoundError("No module named 'pyarrow'", name="pyarrow")
    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        adapter.load_engine("anywhere")


@pytest.mark.skipif(not (REAL / "device_dim.parquet").exists(), reason="data/curated/tables not built")
def test_real_tables_build_an_engine_whose_roots_match_circuit_sizes():
    import pyarrow.parquet as pq
    e = adapter.load_engine(REAL)
    assert e is not None and len(e._index) == 105
    sizes = {r["circuit"]: r["n_premises"] for r in pq.read_table(REAL / "circuit_dim.parquet").to_pylist()}
    for circuit, pc in e._index.items():
        assert e._tree.circuit(pc).served[0] == sizes[circuit]
