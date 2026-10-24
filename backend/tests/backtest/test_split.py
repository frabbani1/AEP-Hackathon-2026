"""Tasks 1.1 to 1.3: the fixture, the leak guard, and the documented row counts."""
import random
from dataclasses import replace
from pathlib import Path

import pytest
from concierge.backtest import rest_eval, split as sp

from .fixtures import S1_COUNTS, SIZES, StubEngine, make_split, s1_rows, s2_rows

TABLES = Path(__file__).resolve().parents[3] / "artifacts" / "curated" / "tables"


def test_fixture_shape():
    s = make_split()
    assert len(SIZES) == 12 and s.counts() == {"total": 32 + 87 + 4, "S1": 32, "S2": 87, "background": 4}
    assert sum(S1_COUNTS.values()) == 30 and sum(s.s1_counts().values()) == 30


def random_s2(seed=1):
    """Every S2 damage row replaced with random values (circuit, asset, repair days, repaired flag)."""
    rng = random.Random(seed)
    return [replace(r, circuit=rng.randint(1, 12), asset_type=rng.choice(["pole", "fuse_bank", "light"]),
                    repair_days=rng.choice([None, 0, 3, 40]), repaired=rng.random() < 0.5,
                    circuit_status="mapped") for r in s2_rows()]


def test_forecast_predictions_ignore_s2_damage_rows():
    base = rest_eval.forecast_predictions(make_split(), StubEngine(), crews=3, seed=7)
    scrambled = rest_eval.forecast_predictions(make_split(s2=random_s2()), StubEngine(), crews=3, seed=7)
    assert base == scrambled and any(p is not None for p in base.values())


def test_oracle_predictions_do_use_s2_asset_counts():
    base = rest_eval.oracle_predictions(make_split(), StubEngine(), [10, 11], crews=3, seed=7)
    changed = rest_eval.oracle_predictions(make_split(s2=random_s2()), StubEngine(), [10, 11], crews=3, seed=7)
    assert base != changed          # the oracle run is allowed to differ, which is why it is labeled


def test_guard_raises_when_an_engine_reads_s2_rows_in_a_forecast():
    split = make_split()

    class Snooper(StubEngine):
        def completion_hours(self, circuit, *, crews, damage, seed):
            split.s2.rows()
            return [1.0]

    with pytest.raises(sp.LeakError):
        rest_eval.forecast_predictions(split, Snooper(), crews=3, seed=1)
    assert len(split.s2.rows()) == 87        # the scope closed even though the call failed


def test_guard_covers_s2_counts_but_not_the_regional_size():
    split = make_split()
    with split.forecast_scope():
        assert split.s2.regional_size == 87 and split.severity_ratio() == pytest.approx(87 / 32)
        with pytest.raises(sp.LeakError):
            split.s2_counts()
    assert split.s2_counts()[12] == 20


def test_split_separates_storms_and_counts_background():
    s = make_split()
    assert {r.storm_id for r in s.s1} == {"S1"} and s.background == 4


def test_verify_counts_flags_a_mismatch():
    with pytest.raises(sp.DataMismatch):
        sp.verify_counts(make_split())     # the fixture is not the real 1,659 rows


@pytest.mark.skipif(not (TABLES / "damage_fact.parquet").exists(), reason="curated tables not built")
def test_real_tables_match_data_findings():
    split = sp.load_split(TABLES)
    sp.verify_counts(split)
    assert split.counts() == {"total": 1659, "S1": 193, "S2": 1254, "background": 212}
    assert len(split.circuit_sizes) >= 105 and sum(split.devices_per_circuit.values()) == 32600
    assert sum(1 for n in split.circuit_sizes.values() if n >= 50) == 104
