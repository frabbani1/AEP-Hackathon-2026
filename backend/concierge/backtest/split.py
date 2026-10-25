"""S1 / S2 separation and the leak guard (design D1).

S1 (Sep 2024) damage rows are open. S2 (Feb 2025) damage rows live in a SealedStorm that refuses to give them out
while a forecast is being made. Forecast code gets S1 rows, the static network tables, and the regional S2 storm size
(a row count). It never gets an S2 damage value. The oracle run and the scoring step read S2 rows outside that scope.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..risk import calibrate as cal

S1, S2 = "S1", "S2"
MAPPED = "mapped"

# Row counts from docs/data_findings.md (ETL run 20261024T204255Z).
EXPECTED_COUNTS = {"total": 1659, S1: 193, S2: 1254, "background": 212}


class LeakError(RuntimeError):
    """Code tried to read S2 damage while a forecast was being made."""


class DataMismatch(RuntimeError):
    """Loaded data does not match the documented row counts."""


@dataclass(frozen=True)
class Damage:
    """One ARCOS damage row after curation. No coordinates, no ids beyond the numeric circuit."""
    storm_id: str | None
    circuit: int | None
    circuit_status: str | None
    repaired: bool
    repair_days: int | None
    asset_type: str | None

    @property
    def mapped(self) -> bool:
        return self.circuit is not None and self.circuit_status == MAPPED


class SealedStorm:
    """The held-out storm. `rows()` raises LeakError while a forecast scope is open."""

    def __init__(self, records: Iterable[Damage]):
        self._records = tuple(records)
        self._guard = 0

    @property
    def regional_size(self) -> int:
        """Rows in the storm window. A regional storm size, not a per-circuit outcome (allowed in a forecast)."""
        return len(self._records)

    def rows(self) -> tuple[Damage, ...]:
        if self._guard:
            raise LeakError("S2 damage rows were read inside a forecast scope")
        return self._records

    def _enter(self) -> None:
        self._guard += 1

    def _exit(self) -> None:
        self._guard -= 1


class Split:
    def __init__(self, s1: Iterable[Damage], s2: Iterable[Damage], circuit_sizes: Mapping[int, int],
                 background: int = 0, devices_per_circuit: Mapping[int, int] | None = None):
        self.s1: tuple[Damage, ...] = tuple(s1)
        self.s2 = SealedStorm(s2)
        self.circuit_sizes: dict[int, int] = dict(circuit_sizes)
        self.background = background
        self.devices_per_circuit: dict[int, int] = dict(devices_per_circuit or {})

    @contextmanager
    def forecast_scope(self):
        """Inside this block S2 damage rows cannot be read."""
        self.s2._enter()
        try:
            yield self
        finally:
            self.s2._exit()

    # ---- allowed inside a forecast --------------------------------------------------------------------------
    def s1_counts(self) -> Counter[int]:
        """S1 damage rows per circuit, mapped rows only (the spec-02 definition)."""
        return Counter(r.circuit for r in self.s1 if r.mapped)

    def severity_ratio(self) -> float:
        """Multiplier of the feb-2025-like preset over the sep-2024-like preset: S2 storm size / S1 storm size.

        Uses the two regional storm sizes only, the way the UI preset does. No circuit's S2 outcome is involved.
        """
        p = cal.severity_presets([len(self.s1), self.s2.regional_size])
        return p["feb-2025-like"] / p["sep-2024-like"]

    # ---- outside a forecast ---------------------------------------------------------------------------------
    def s2_counts(self) -> Counter[int]:
        return Counter(r.circuit for r in self.s2.rows() if r.mapped)

    def counts(self) -> dict[str, int]:
        return {"total": len(self.s1) + self.s2.regional_size + self.background, S1: len(self.s1),
                S2: self.s2.regional_size, "background": self.background}


def verify_counts(split: Split, expected: Mapping[str, int] = EXPECTED_COUNTS) -> None:
    got = split.counts()
    bad = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    if bad:
        raise DataMismatch(f"row counts differ from docs/data_findings.md (got, expected): {bad}")


def split_records(records: Sequence[Damage], circuit_sizes: Mapping[int, int],
                  devices_per_circuit: Mapping[int, int] | None = None) -> Split:
    s1 = [r for r in records if r.storm_id == S1]
    s2 = [r for r in records if r.storm_id == S2]
    return Split(s1, s2, circuit_sizes, background=len(records) - len(s1) - len(s2),
                 devices_per_circuit=devices_per_circuit)


# ---- loading the curated tables ---------------------------------------------------------------------------------
def load_damage(path: str | Path) -> list[Damage]:
    import pyarrow.parquet as pq
    cols = ["storm_id", "circuit", "circuit_status", "repaired", "repair_days", "asset_type"]
    t = pq.read_table(path, columns=cols).to_pydict()
    return [Damage(t["storm_id"][i], t["circuit"][i], t["circuit_status"][i], bool(t["repaired"][i]),
                   t["repair_days"][i], t["asset_type"][i]) for i in range(len(t["storm_id"]))]


def load_circuit_sizes(path: str | Path) -> dict[int, int]:
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["circuit", "n_premises"]).to_pydict()
    return {int(c): int(n) for c, n in zip(t["circuit"], t["n_premises"])}


def load_devices_per_circuit(path: str | Path) -> dict[int, int]:
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["circuit"]).to_pydict()
    return dict(Counter(int(c) for c in t["circuit"] if c is not None))


def load_split(tables_dir: str | Path) -> Split:
    d = Path(tables_dir)
    return split_records(load_damage(d / "damage_fact.parquet"), load_circuit_sizes(d / "circuit_dim.parquet"),
                         load_devices_per_circuit(d / "device_dim.parquet"))
