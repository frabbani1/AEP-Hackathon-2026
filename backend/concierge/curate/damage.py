"""Clean ARCOS damage records into damage_fact rows."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date

from concierge.risk.storms import StormWindow, storm_index

from . import geo

BACKLOG_DAYS = 7   # real data: repairs are 0-6 days, then a gap, then a 12-25+ day backlog tail

ASSET_TYPES = {
    "primary": "primary_line", "secondary": "secondary_line", "pole": "pole", "fuse bank": "fuse_bank",
    "transformer": "transformer", "recloser bank": "recloser", "switch": "switch", "capacitor bank": "capacitor_bank",
    "light": "light", "step transformer": "step_transformer",
}
DAMAGE_TYPES = {
    "damaged wire": "wire", "damaged switch": "switch", "damaged pole": "pole",
    "damaged equipment": "equipment", "damaged light": "light",
}

ARCOS_COLUMNS = ["Circuit", "Report_Tim", "Repaired", "Repaired_D", "Asset_Type", "Inform_Wor",
                 "Access_Iss", "Special_Eq", "Media"]


def _slug(v) -> str | None:
    t = re.sub(r"[^a-z0-9]+", "_", str(v or "").strip().lower()).strip("_")
    return t or None


def _yes_no(v) -> bool | None:
    t = str(v or "").strip().lower()
    return True if t == "yes" else False if t == "no" else None


def clean_record(
    idx: int, rec: Mapping, point: tuple[float, float] | None, *, crosswalk: Mapping[str, int],
    topology_circuits: set[int], storms: list[StormWindow],
) -> dict:
    report: date | None = rec["Report_Tim"] if isinstance(rec["Report_Tim"], date) else None
    repaired_on: date | None = rec["Repaired_D"] if isinstance(rec["Repaired_D"], date) else None
    repaired = _yes_no(rec["Repaired"]) is True

    # An unrepaired record carries a placeholder Repaired_D, so its duration is unknown (censored), not zero.
    repair_days = None
    if repaired and report and repaired_on and repaired_on >= report:
        repair_days = (repaired_on - report).days

    label = str(rec["Circuit"] or "").strip()
    if not label:
        circuit, status = None, "blank_label"
    elif label not in crosswalk:
        circuit, status = None, "label_unknown"
    elif crosswalk[label] not in topology_circuits:
        circuit, status = crosswalk[label], "not_in_topology"
    else:
        circuit, status = crosswalk[label], "mapped"

    asset = ASSET_TYPES.get(str(rec["Asset_Type"] or "").strip().lower())
    dtype = DAMAGE_TYPES.get(str(rec["Inform_Wor"] or "").strip().lower())
    i = storm_index(report, storms)
    media = rec["Media"]
    return {
        "damage_id": idx,
        "storm_id": f"S{i + 1}" if i is not None else None,
        "report_date": report,
        "repaired": repaired,
        "repair_days": repair_days,
        "is_backlog": (repair_days >= BACKLOG_DAYS) if repair_days is not None else None,
        "asset_type": asset,
        "damage_type": dtype,
        "access_issue": _yes_no(rec["Access_Iss"]),
        "special_equipment": _slug(rec["Special_Eq"]),
        "media_count": int(media) if isinstance(media, (int, float)) else None,
        "circuit": circuit,
        "circuit_status": status,
        "hex_cell": geo.to_cell(point[0], point[1], 8) if point else None,
    }


def clean_all(rows: Iterable[tuple[Mapping, tuple[float, float] | None]], **kw) -> list[dict]:
    return [clean_record(i, rec, pt, **kw) for i, (rec, pt) in enumerate(rows)]
