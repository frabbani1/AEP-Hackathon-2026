"""Synthetic backtest data: 12 circuits, two storms, repair days with a backlog tail, and unrepaired rows.

Every number the tests assert on can be checked by hand from the tables below.
"""
from __future__ import annotations

from collections.abc import Sequence

from concierge.backtest.rest_eval import ForecastDamage, OracleDamage
from concierge.backtest.split import Damage, Split, split_records

SIZES = {1: 60, 2: 80, 3: 100, 4: 120, 5: 150, 6: 200, 7: 250, 8: 300, 9: 400, 10: 500, 11: 700, 12: 300}
ASSETS = ("primary_line", "pole", "fuse_bank", "transformer")

# Feb 2025: repair days per circuit (None = not repaired, so censored).
S2_DAYS: dict[int, list[int | None]] = {
    1: [0], 2: [1, 2, None], 3: [4], 4: [0, 1, 5], 5: [2, 6], 6: [1, 1, 2, 4, None], 7: [0, 0, 1, 3, 5],
    8: [2, 2, 3, 4, 6, 6, None], 9: [1, 1, 1, 2, 2, 3, 4, 9],
    10: [0] * 4 + [1] * 4 + [2] * 3 + [3],
    11: [1] * 5 + [2] * 5 + [3] * 5 + [15, 16, None],
    12: [0] * 10 + [1] * 10,
}
S2_UNMAPPED_DAYS = [1, 2]
# Sep 2024: mapped damage rows per circuit; the first six rows were repaired quickly, the rest are backlog.
S1_COUNTS = {5: 1, 7: 1, 8: 2, 9: 3, 10: 5, 11: 6, 12: 12}
S1_QUICK_DAYS = [0, 1, 1, 2, 3, 3]


def _row(storm, circuit, days, asset, status="mapped") -> Damage:
    return Damage(storm, circuit, status, days is not None, days, asset)


def s1_rows(counts=S1_COUNTS, quick=S1_QUICK_DAYS) -> list[Damage]:
    rows, i = [], 0
    for c in sorted(counts):
        for _ in range(counts[c]):
            days = quick[i] if i < len(quick) else 22 + i % 4
            rows.append(_row("S1", c, days, ASSETS[i % 4]))
            i += 1
    rows += [_row("S1", None, 23, "pole", "label_unknown") for _ in range(2)]
    return rows


def s2_rows(days_by_circuit=S2_DAYS) -> list[Damage]:
    rows, i = [], 0
    for c in sorted(days_by_circuit):
        for d in days_by_circuit[c]:
            rows.append(_row("S2", c, d, ASSETS[i % 4]))
            i += 1
    rows += [_row("S2", None, d, "pole", "label_unknown") for d in S2_UNMAPPED_DAYS]
    return rows


def background_rows() -> list[Damage]:
    return [_row(None, 3, 40, "pole") for _ in range(4)]


def make_split(s1: Sequence[Damage] | None = None, s2: Sequence[Damage] | None = None) -> Split:
    records = list(s1 if s1 is not None else s1_rows()) + list(s2 if s2 is not None else s2_rows()) + background_rows()
    return split_records(records, SIZES)


class StubEngine:
    """Deterministic stand-in for the spec-03 circuit engine. 100 trials spread 0.5x to 1.5x around the mean."""
    engine_version = "stub-1"

    def __init__(self):
        self.calls: list[tuple[int, int, object]] = []

    def completion_hours(self, circuit, *, crews, damage, seed):
        self.calls.append((circuit, crews, damage))
        n = sum(damage.counts.values()) if isinstance(damage, OracleDamage) else damage.expected_events
        if n <= 0:
            return []
        base = 24.0 * (1 + n) / crews
        return [base * (0.5 + i / 99) for i in range(100)]


class FixedHoursEngine:
    """Returns the same list for every call, for hand-checking percentiles."""
    def __init__(self, hours):
        self.hours = list(hours)

    def completion_hours(self, circuit, *, crews, damage, seed):
        return self.hours


__all__ = ["make_split", "s1_rows", "s2_rows", "StubEngine", "FixedHoursEngine", "SIZES", "S2_DAYS", "ForecastDamage"]
