"""After-outage actions. Reviewed fixed text, selected by code. The model may only rephrase the core items.

No numbers (a test enforces it) and no food-safety timings: the item points to the local health authority.
General advice only, not professional advice. The wording needs team review before release.
"""
from __future__ import annotations

from collections.abc import Mapping

from .prepare import clean_needs

LIBRARY_VERSION = "after-v1"

# (id, category, text)
CORE_ITEMS = [
    ("restart_slowly", "power", "Turn appliances back on one at a time instead of all at once."),
    ("check_food", "food", "Check refrigerated and frozen food before you eat it. Follow your local health "
                           "authority's guidance on what to throw away."),
    ("reset_devices", "power", "Reset clocks and devices that need it, and check that heating or cooling equipment "
                               "works normally."),
    ("still_out", "communication", "If your power is still out when nearby homes have power, report it through your "
                                   "utility's official channel."),
    ("recharge", "power", "Recharge phones and battery packs now, in case power goes out again."),
    ("check_in_after", "community", "Check on neighbors and relatives to see whether they are safe and have power."),
]
NEED_ITEMS = {
    "medical_equipment": [
        ("med_after", "medical", "Check that your medical equipment is working, and recharge its backup batteries."),
    ],
    "mobility_limited": [
        ("mob_after", "mobility", "Let your check-in person know that power is back and that you are safe."),
    ],
    "electric_heat_or_cooling": [
        ("heat_after", "shelter", "Give heating or cooling equipment time to restart, and check that it runs "
                                  "normally before you rely on it."),
    ],
}


def select() -> list[tuple[str, str, str]]:
    """Core items chosen by code: (id, category, text). After power returns every item applies."""
    return list(CORE_ITEMS)


def need_items(needs: Mapping | None) -> list[tuple[str, str, str]]:
    n = clean_needs(needs)
    return [item for need, on in n.items() if on for item in NEED_ITEMS[need]]
