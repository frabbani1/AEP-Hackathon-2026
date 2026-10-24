"""Storm window detection from ARCOS report dates (design D5)."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from .config import DEFAULT_CONFIG, RiskConfig


class StormDetectionError(RuntimeError):
    """The data did not contain exactly the expected number of storms."""


@dataclass(frozen=True)
class StormWindow:
    start: date
    end: date
    records: int

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def label(self) -> str:
        return self.start.strftime("%b %Y")


def count_days(dates: Iterable[date | None]) -> Counter[date]:
    return Counter(d for d in dates if d is not None)


def detect_storms(day_counts: Mapping[date, int], cfg: RiskConfig = DEFAULT_CONFIG) -> list[StormWindow]:
    """Find storm windows.

    1. candidate days have >= storm_candidate_min_day records; candidates <= storm_merge_gap_days apart merge
    2. each group grows to adjacent days with >= storm_extend_min_day records
    3. groups with >= storm_min_total records are storms
    Raises StormDetectionError unless exactly cfg.expected_storms storms are found.
    """
    one = timedelta(days=1)
    candidates = sorted(d for d, c in day_counts.items() if c >= cfg.storm_candidate_min_day)

    groups: list[list[date]] = []
    for d in candidates:
        if groups and (d - groups[-1][-1]).days <= cfg.storm_merge_gap_days:
            groups[-1].append(d)
        else:
            groups.append([d])

    windows: list[tuple[date, date]] = []
    for g in groups:
        lo, hi = g[0], g[-1]
        while day_counts.get(lo - one, 0) >= cfg.storm_extend_min_day:
            lo -= one
        while day_counts.get(hi + one, 0) >= cfg.storm_extend_min_day:
            hi += one
        # Extension can make neighbouring groups touch; fuse them.
        if windows and lo <= windows[-1][1] + one:
            windows[-1] = (windows[-1][0], max(hi, windows[-1][1]))
        else:
            windows.append((lo, hi))

    storms = []
    for lo, hi in windows:
        total = sum(c for d, c in day_counts.items() if lo <= d <= hi)
        if total >= cfg.storm_min_total:
            storms.append(StormWindow(lo, hi, total))

    if len(storms) != cfg.expected_storms:
        found = ", ".join(f"{s.start}..{s.end} ({s.records})" for s in storms) or "none"
        raise StormDetectionError(
            f"expected {cfg.expected_storms} storms, found {len(storms)}: {found}"
        )
    return storms


def storm_index(d: date | None, storms: list[StormWindow]) -> int | None:
    if d is None:
        return None
    for i, s in enumerate(storms):
        if s.contains(d):
            return i
    return None
