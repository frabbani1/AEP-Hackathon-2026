"""Loads and validates the alert rules table (data, not code). Returns a hash so every decision can name the exact table."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

TABLE_PATH = Path(__file__).with_name("rules_table.json")
BANDS = ("Low", "Medium", "High")
DURATIONS = ("short", "medium", "long", "extended", "unknown")
CHANNELS = ("sms", "push", "email", "voice")
ESCALATION_IDS = ("E1", "E2", "E3", "E4", "E5")
NEEDS_RULE_IDS = ("N1", "N2", "N3")
ELIGIBILITY_IDS = ("K-pre", "K-pre-unrated", "K-pre-optin", "K-out", "K-upd", "K-restored")


class TableError(ValueError):
    """The rules table is incomplete or invalid."""


@dataclass(frozen=True)
class RulesTable:
    data: dict[str, Any]
    hash: str

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def tiers(self) -> list[str]:
        return self.data["tiers"]

    def tier_rank(self, tier: str) -> int:
        return self.tiers.index(tier)


def _need(cond: bool, message: str) -> None:
    if not cond:
        raise TableError(message)


def _reason(obj: Any, where: str) -> None:
    _need(isinstance(obj, dict) and isinstance(obj.get("reason"), str) and obj["reason"].strip(),
          f"missing plain-language reason: {where}")


def validate(data: dict[str, Any]) -> None:
    for key in ("version", "tiers", "duration_buckets", "base_tier", "needs_rules", "tier_behavior", "eligibility",
                "escalation", "assumptions"):
        _need(key in data, f"missing section: {key}")
    tiers = data["tiers"]
    _need(len(tiers) == len(set(tiers)) and len(tiers) >= 2, "tiers must be a list of distinct names")

    base = data["base_tier"]
    for band in BANDS:
        for dur in DURATIONS:
            cell = base.get(band, {}).get(dur)
            _need(cell is not None, f"missing base tier cell {band}/{dur}")
            _need(cell.get("tier") in tiers, f"unknown tier in base tier cell {band}/{dur}")
            _reason(cell, f"base tier cell {band}/{dur}")
    cell = base.get("Unrated", {}).get("unknown")
    _need(cell is not None, "missing base tier cell Unrated/unknown")
    _need(cell.get("tier") in tiers, "unknown tier in base tier cell Unrated/unknown")
    _reason(cell, "base tier cell Unrated/unknown")

    for rid in NEEDS_RULE_IDS:
        _need(rid in data["needs_rules"], f"missing needs rule {rid}")
        _reason(data["needs_rules"][rid], f"needs rule {rid}")
    for rid in ("N2", "N3"):
        _need(data["needs_rules"][rid].get("min_tier") in tiers, f"unknown tier in needs rule {rid}")

    for tier in tiers:
        beh = data["tier_behavior"].get(tier)
        _need(beh is not None, f"missing tier behavior for {tier}")
        _reason(beh, f"tier behavior {tier}")
        for key in ("pre_storm_lead_hours", "outage_confirmed_within_minutes", "estimate_update_min_gap_hours",
                    "channel_order"):
            _need(key in beh, f"tier behavior {tier} missing {key}")
        _need(all(c in CHANNELS for c in beh["channel_order"]), f"tier behavior {tier} has an unknown channel")
        leads = beh["pre_storm_lead_hours"]
        _need(leads == sorted(leads, reverse=True), f"tier behavior {tier}: lead hours must be largest first")

    for rid in ELIGIBILITY_IDS:
        _need(rid in data["eligibility"], f"missing eligibility rule {rid}")
    for rid in ("K-pre", "K-pre-unrated", "K-out", "K-restored"):
        _reason(data["eligibility"][rid], f"eligibility rule {rid}")
    for rid in ESCALATION_IDS:
        _need(rid in data["escalation"], f"missing escalation rule {rid}")
        _reason(data["escalation"][rid], f"escalation rule {rid}")
        _need(isinstance(data["escalation"][rid].get("action"), str), f"escalation rule {rid} needs an action")
    _need(len(data["assumptions"]) >= 3, "assumptions must list at least three statements")


def table_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_table_from(data: dict[str, Any]) -> RulesTable:
    validate(data)
    return RulesTable(data=data, hash=table_hash(data))


@lru_cache(maxsize=1)
def load_table() -> RulesTable:
    with open(TABLE_PATH, encoding="utf-8") as f:
        return load_table_from(json.load(f))
