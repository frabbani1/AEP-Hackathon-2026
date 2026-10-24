"""Counts-only profiling report.

The report contains counts, null rates, distributions of non-sensitive categorical fields,
link coverage and known data gaps. It never contains row-level values of sensitive fields,
and it formats large numbers with separators so none of them looks like a raw identifier.
"""

from __future__ import annotations

from collections.abc import Mapping

SAFE_CATEGORICALS: dict[str, list[str]] = {
    "arcos_prod_damage_09_28_Clip": ["Asset_Type", "Inform_Wor", "Repaired", "Access_Iss", "Special_Eq"],
    "Transformer_Clip": ["structure_", "mounting", "bank_confi"],
    "Fuse_Bank_Clip": ["normal_sta", "mounting"],
    "Recloser_Bank_Clip": ["device_typ", "normal_sta"],
    "Switch_Clip": ["normal_sta"],
    "Pole_Clip": ["material", "usage", "owner_type"],
    "Primary_ExportFeatures": ["usage", "mounting", "nominal_vo"],
    "Secondary_ExportFeatures": ["mounting", "nominal_vo"],
    "Customer_Clip": ["kind_of_se"],
}
TOP_VALUES = 8


def n(value: int | float | None) -> str:
    """Count with thousands separators."""
    return "n/a" if value is None else f"{int(value):,}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def known_gaps(context: Mapping) -> list[str]:
    """Data limitations that later work must disclose. Statements that can be checked are checked."""
    damage = context["damage"]
    time_text = (
        "Damage report and repair dates carry no time of day, so durations are in whole days."
        if not damage["has_time_component"]
        else "Damage dates carry a time of day in this extract; the contract expects date-only values."
    )
    switches = context["switch_in_topology"]
    aliases = context["crosswalk"]
    alias_gap = []
    if aliases.get("nonconforming_circuit_aliases") or aliases.get("nonconforming_substation_aliases"):
        alias_gap.append(
            f"{n(aliases['nonconforming_circuit_aliases'])} circuit alias value(s) and "
            f"{n(aliases['nonconforming_substation_aliases'])} substation alias value(s) do not follow the anonymized "
            "CIR<n> / STATION<n> form and may be real names. They stay in server artifacts only and need review "
            "before any customer-facing use."
        )
    return alias_gap + [
        "No weather or storm characteristics are provided in any input. Storm severity cannot be learned from this data and has to be a scenario input.",
        time_text,
        "Damage records have no outage identifier and no damage quantity. Each record is treated as one damage location.",
        (
            f"Switches are absent from the topology file ({n(switches['in_topology'])} of {n(switches['total'])} switch records appear in it), "
            "so switching and step restoration around switches cannot be traced."
        ),
        "No data dictionary is provided. Field meanings in the input contracts are inferred from names and values.",
        "The restoration labor workbook is organized by compatible unit, not by damage type. The mapping from damage to labor is a documented assumption.",
        "The workbook has no conductor repair hours, so damaged wire has no labor hours here.",
        "The transformer layer has no kVA rating field.",
        (
            "Installation dates recorded as placeholders (set to missing and flagged): "
            + ", ".join(f"{label} {pct(rate)}" for label, rate in context["placeholder_rates"].items())
            + "."
        ),
        (
            f"{pct(damage['busiest_week_share'])} of damage records fall within a single 7-day window, "
            "so storm-to-storm generalization is limited."
        ),
    ]


def render_report(context: Mapping) -> str:
    out: list[str] = ["# Data profile report", ""]
    out += [
        f"Pipeline version {context['pipeline_version']}. Counts only: no row-level sensitive values appear here.",
        "",
        "## Inputs",
        "",
    ]
    rows = []
    for layer in context["layers"]:
        rows.append(
            [
                layer["name"],
                n(layer["records"]),
                str(layer["kept_public"]),
                str(layer["kept_internal"]),
                str(layer["dropped"]),
                str(layer["undocumented"]),
            ]
        )
    out += _table(["Layer", "Records", "Public fields kept", "Internal fields (tokenized)", "Fields never read", "Undocumented fields"], rows)
    out += [
        "",
        f"Topology file: {n(context['topology']['chains'])} premise chains. Labor workbook: {n(context['labor']['items'])} labor items.",
        "",
        "## Null rates of kept public fields",
        "",
    ]
    for layer in context["layers"]:
        nulls = layer["null_rates"]
        if not nulls:
            continue
        out.append(f"**{layer['name']}**: " + ", ".join(f"{field} {pct(rate)}" for field, rate in nulls.items()))
        out.append("")
    out += ["## Categorical summaries (non-sensitive fields only)", ""]
    for name, fields in context["categoricals"].items():
        for field, values in fields.items():
            text = ", ".join(f"{value}: {n(count)}" for value, count in values.items())
            out.append(f"- {name} / {field}: {text}")
    out += ["", "## Link coverage", ""]
    rows = []
    for link, entry in sorted(context["coverage"].items()):
        rows.append(
            [
                link,
                n(entry["matched"]),
                n(entry["primary_total"]),
                pct(entry["rate"]),
                "not enforced" if entry["minimum"] is None else pct(entry["minimum"]),
                n(entry["unmatched_other"]),
            ]
        )
    out += _table(["Link", "Matched", "Primary side total", "Rate", "Minimum", "Unmatched on other side"], rows)
    topo = context["topology"]
    out += [
        "",
        "## Topology",
        "",
        f"- Premise chains: {n(topo['chains'])}; rejected lines: {n(topo['rejected_lines'])}; duplicate premises: {n(topo['duplicate_premises'])}",
        f"- Devices (graph nodes): {n(topo['nodes'])}; edges: {n(topo['edges'])}; connected components: {n(topo['components'])}",
        f"- Devices with more than one parent: {n(topo['multi_parent_nodes'])}",
        f"- Attribute conflicts between lines: {n(topo['attribute_conflicts'])}; devices under more than one breaker: {n(topo['circuit_conflicts'])}",
        "",
        "## Circuits",
        "",
        f"- Circuits in the crosswalk: {n(context['crosswalk']['circuits'])}; circuits with more than one alias: {n(context['crosswalk']['ambiguous_circuits'])}",
        f"- Alias values not in the anonymized label form: circuits {n(context['crosswalk']['nonconforming_circuit_aliases'])}, substations {n(context['crosswalk']['nonconforming_substation_aliases'])}",
        "",
        "## Protection zones",
        "",
        f"Minimum premises per published zone: {n(context['zone_minimum'])}.",
        "",
    ]
    rows = [
        [level, n(info["premises"]), n(info["zones"])]
        for level, info in sorted(context["zone_distribution"].items(), key=lambda kv: -kv[1]["premises"])
    ]
    out += _table(["Zone level", "Premises assigned", "Zones"], rows)
    out += [
        "",
        f"Published zones: {n(context['published_zones'])}. Premises in zones below the minimum (not published): {n(context['premises_unpublished'])}.",
        "",
        "## Damage records",
        "",
    ]
    damage = context["damage"]
    out += [
        f"- Records: {n(damage['records'])}; report dates {damage['date_min']} to {damage['date_max']}",
        f"- Linked to an asset: {n(damage['linked'])}; unlinked: {n(damage['unlinked'])}",
    ]
    for reason, count in sorted(damage["unlinked_reasons"].items()):
        out.append(f"  - {reason}: {n(count)}")
    out += [
        f"- Linked records with a known downstream premise count: {n(damage['downstream_known'])}",
        "",
        "## Labor reference",
        "",
        f"- Labor items: {n(context['labor']['items'])} in {n(context['labor']['groups'])} groups",
        f"- Indirect labor multiplier: {context['labor']['indirect_multiplier']} (one combined value)",
        f"- Mapping review status: {context['labor']['review_status']}",
        f"- Assumptions in the register: {n(context['labor']['assumptions'])}",
        "",
        "## Known data gaps",
        "",
    ]
    out += [f"- {gap}" for gap in known_gaps(context)]
    out += [
        "",
        "## Inputs not processed",
        "",
        "- Structure diagrams (PDF): image-only drawings with no text layer. Not processed in this pipeline.",
        "",
    ]
    return "\n".join(out)
