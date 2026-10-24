"""Preparation guidance. Rule-based and deterministic: the LLM never writes or changes these items.

Inputs are the risk band, the restoration estimate, and needs the customer chose to declare. Needs are never inferred.
"""
from __future__ import annotations

from collections.abc import Mapping

ENGINE_VERSION = "guidance-v1"
NEEDS = ("medical_equipment", "mobility_limited", "electric_heat_or_cooling")

EMERGENCY_NOTICE = ("In an emergency, call 911. Utility alerts and this tool do not replace emergency services.")

SAFETY_ITEMS = [
    ("safety_downed_lines", "Stay far away from downed power lines. Assume they are live, keep others away, and call 911."),
    ("safety_generator", "Never run a generator, grill or camp stove inside your home or garage. Carbon monoxide can kill."),
]

# (id, category, text, minimum band rank, minimum duration rank). Band rank: Low 0, Medium 1, High 2, unknown 2.
# Duration rank: unknown 1, short 0, medium 1, long 2, extended 3.
CORE_ITEMS = [
    ("charge_devices", "power", "Charge your phones and battery packs now, and keep a charger in your car.", 0, 0),
    ("light", "power", "Put flashlights and spare batteries where you can find them in the dark. Avoid candles.", 0, 0),
    ("know_outage_report", "communication",
     "Save your utility's outage-reporting method in your phone, and report your outage so crews know.", 0, 0),
    ("fridge", "food", "Keep the refrigerator and freezer doors closed to hold the cold as long as possible.", 1, 1),
    ("water_food", "food", "Set aside drinking water and food that needs no cooking.", 1, 1),
    ("unplug", "power", "Unplug sensitive electronics, and leave one lamp on so you notice when power returns.", 1, 0),
    ("cash_fuel", "supplies", "Keep some cash on hand and do not let your car's fuel tank run low.", 1, 2),
    ("check_in", "community", "Arrange to check on neighbors, and ask someone to check on you.", 1, 2),
    ("shelter_plan", "shelter", "Know where you could stay warm or cool if the outage runs long, "
                                "such as a friend's home or a community shelter.", 2, 2),
]
NEED_ITEMS = {
    "medical_equipment": [
        ("med_battery", "medical", "Charge any medical devices and backup batteries now, and know how long they last.", 0),
        ("med_plan", "medical", "Ask your care provider about a backup plan if your equipment needs power. "
                                "Do not wait for an alert if you have a medical emergency: call 911.", 0),
        ("med_register", "medical", "Tell your utility about your medical need through its official channel so it can be on file.", 0),
    ],
    "mobility_limited": [
        ("mob_exit", "mobility", "Plan how you would leave your home safely if elevators or electric door openers stop working.", 0),
        ("mob_helper", "mobility", "Ask a neighbor or family member to be your check-in contact during the storm.", 0),
    ],
    "electric_heat_or_cooling": [
        ("heat_plan", "shelter", "If you rely on electric heating or cooling, plan a safe place to stay before the storm "
                                 "and do not use a gas oven or outdoor heater to warm your home.", 0),
    ],
}


def duration_bucket(typical_hours: int | None) -> str:
    if typical_hours is None:
        return "unknown"
    if typical_hours < 8:
        return "short"
    if typical_hours < 24:
        return "medium"
    if typical_hours < 72:
        return "long"
    return "extended"


_DUR_RANK = {"short": 0, "unknown": 1, "medium": 1, "long": 2, "extended": 3}
_BAND_RANK = {"Low": 0, "Medium": 1, "High": 2, None: 2}


def clean_needs(needs: Mapping | None) -> dict[str, bool]:
    needs = needs or {}
    unknown = set(needs) - set(NEEDS)
    if unknown:
        raise ValueError(f"unknown needs: {sorted(unknown)}")
    out = {}
    for k in NEEDS:
        v = needs.get(k, False)
        if not isinstance(v, bool):
            raise ValueError(f"needs.{k} must be true or false")
        out[k] = v
    return out


def build(*, band: str | None, typical_hours: int | None, estimate_display: str | None,
          needs: Mapping | None, insufficient_data: bool) -> dict:
    n = clean_needs(needs)
    dur = duration_bucket(typical_hours)
    b_rank, d_rank = _BAND_RANK[band], _DUR_RANK[dur]
    items = []
    for i, text in SAFETY_ITEMS:
        items.append({"id": i, "category": "safety", "priority": 1, "safety": True, "text": text})
    for need, enabled in n.items():
        if enabled:
            for i, cat, text, _ in NEED_ITEMS[need]:
                items.append({"id": i, "category": cat, "priority": 2, "safety": False, "text": text})
    for i, cat, text, min_band, min_dur in CORE_ITEMS:
        if b_rank >= min_band and d_rank >= min_dur:
            items.append({"id": i, "category": cat, "priority": 3, "safety": False, "text": text})

    if insufficient_data:
        headline = "We can't rate your outage risk, so prepare as if an outage is possible."
    elif band == "High":
        headline = "Your area has a higher risk of losing power in this scenario. Start preparing now."
    elif band == "Medium":
        headline = "Your area has a moderate risk of losing power in this scenario. A few steps now will help."
    else:
        headline = "Your area has a lower risk of losing power in this scenario. Basic readiness is still wise."
    if estimate_display:
        headline += f" If you lose power, our model estimates it could take {estimate_display}."
    return {
        "engine_version": ENGINE_VERSION,
        "headline": headline,
        "duration_bucket": dur,
        "declared_needs": [k for k, v in n.items() if v],
        "items": items,
        "emergency_notice": EMERGENCY_NOTICE,
        "notes": ["Guidance is general and based on the scenario you chose. It is not a weather forecast.",
                  "Needs shown here are only the ones you declared."],
    }
