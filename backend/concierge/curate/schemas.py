"""Explicit Parquet schemas so empty/None-heavy columns keep a stable type."""
import pyarrow as pa

S = pa.string()
I = pa.int64()
F = pa.float64()
B = pa.bool_()

SCHEMAS: dict[str, pa.Schema] = {
    "premise_dim": pa.schema([
        ("premise_token", S), ("transformer_key", S), ("circuit", I), ("service_type", S),
        ("protective_count", I), ("hops", I), ("hex_cell", S), ("hex_res", I),
        ("in_topology", B), ("in_customer_file", B), ("alternate_path", B)]),
    "device_dim": pa.schema([
        ("device_key", S), ("device_class", S), ("device_type", S), ("parent_key", S), ("circuit", I),
        ("depth", I), ("is_protective", B), ("n_premises_served", I), ("small_group", B)]),
    "damage_fact": pa.schema([
        ("damage_id", I), ("storm_id", S), ("report_date", pa.date32()), ("repaired", B), ("repair_days", I),
        ("is_backlog", B), ("asset_type", S), ("damage_type", S), ("access_issue", B), ("special_equipment", S),
        ("media_count", I), ("circuit", I), ("circuit_status", S), ("hex_cell", S)]),
    "circuit_dim": pa.schema([
        ("circuit", I), ("n_premises", I), ("n_overhead", I), ("n_underground", I), ("n_unknown_service", I),
        ("n_transformers", I), ("n_protective_devices", I), ("max_hops", I),
        ("primary_overhead_miles", F), ("primary_underground_miles", F), ("secondary_miles", F),
        ("gis_fuse_banks", I), ("gis_reclosers", I), ("gis_switches", I), ("gis_transformers", I),
        ("damage_s1", I), ("damage_s2", I), ("damage_background", I)]),
    "hex_context": pa.schema([
        ("hex_cell", S), ("hex_res", I), ("center_lat", F), ("center_lon", F), ("n_premises", I),
        ("overhead_share", F), ("damage_s1", I), ("damage_s2", I),
        ("hospital_nearby", B), ("fire_nearby", B), ("ambulance_nearby", B)]),
    "circuit_crosswalk": pa.schema([("circuit_label", S), ("circuit", I)]),
}

# Column names that must never appear in any curated table.
BANNED_OUTPUT_COLUMNS = frozenset({
    "premise_nu", "premise_id", "critical_c", "life_support", "address", "road_name", "city", "name",
    "latitude", "longitude", "lat", "lon", "outage_num", "incident_w", "substation", "near_fid", "scada_cont",
    "owner_name", "global_id",
})
