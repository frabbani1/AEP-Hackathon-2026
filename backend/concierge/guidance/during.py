"""During-outage actions. Reviewed fixed text, selected by code. The model may only rephrase the core items.

No numbers anywhere in this file (the guard and a test enforce it), and no food-safety timings: items point to the local
health authority instead. General advice only, not professional advice. The wording needs team review before release.
"""
from __future__ import annotations

from collections.abc import Mapping

from .prepare import _DUR_RANK, clean_needs, duration_bucket

LIBRARY_VERSION = "during-v1"

# (id, category, text, minimum duration rank). Duration rank: short 0, unknown/medium 1, long 2, extended 3.
CORE_ITEMS = [
    ("keep_cold", "food", "Keep the refrigerator and freezer doors closed so food stays cold as long as possible.", 0),
    ("light_safely", "power", "Use flashlights or battery lanterns for light, and avoid candles.", 0),
    ("save_phone", "communication", "Save phone battery: lower the screen brightness, close apps, and send texts "
                                    "instead of making calls.", 0),
    ("unplug_during", "power", "Unplug sensitive electronics so a surge does not damage them when power returns.", 0),
    ("check_neighbors", "community", "Check on neighbors who may need help, but only if it is safe to go outside.", 1),
    ("stay_comfortable", "shelter", "Gather in one room and dress for the conditions to stay warm or cool. "
                                   "Open windows only if it is safe.", 1),
    ("food_guidance", "food", "Follow your local health authority's guidance on which food to keep and which to "
                              "throw away.", 1),
    ("plan_shelter", "shelter", "If the outage runs long, plan to stay with friends or family, or at a community "
                                "shelter.", 2),
]
NEED_ITEMS = {
    "medical_equipment": [
        ("med_during", "medical", "If medical equipment that needs power stops working, use your backup plan. "
                                  "If anyone is in danger, call 911.", 0),
    ],
    "mobility_limited": [
        ("mob_during", "mobility", "Stay where you can leave safely, and contact your check-in person to let them know "
                                   "your situation.", 0),
    ],
    "electric_heat_or_cooling": [
        ("heat_during", "shelter", "If your electric heating or cooling is out, move to a safe place that is warm or "
                                   "cool enough. Do not use a gas oven or outdoor heater to warm your home.", 0),
    ],
}


def select(*, typical_hours: int | None) -> list[tuple[str, str, str]]:
    """Core items chosen by code: (id, category, text)."""
    rank = _DUR_RANK[duration_bucket(typical_hours)]
    return [(i, cat, text) for i, cat, text, min_dur in CORE_ITEMS if rank >= min_dur]


def need_items(needs: Mapping | None) -> list[tuple[str, str, str]]:
    n = clean_needs(needs)
    return [(i, cat, text) for need, on in n.items() if on for i, cat, text, _ in NEED_ITEMS[need]]
