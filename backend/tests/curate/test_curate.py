import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from concierge.curate import damage, facilities, geo
from concierge.curate.ids import device_key
from concierge.curate.labor import parse_labor, validate_labor
from concierge.curate.network import build_network, parse_chain
from concierge.curate.pipeline import PrivacyError, curate, write_table
from concierge.curate.schemas import SCHEMAS
from concierge.curate.shapes import FORBIDDEN_COLUMNS, ForbiddenColumnError, iter_records
from concierge.risk.storms import StormWindow

SALT = b"test-salt"
DATA = Path(__file__).resolve().parents[3] / "data"
DOCS = Path(__file__).resolve().parents[3] / "docs"
HAS_DATA = (DATA / "Data1" / "Customer_Clip.dbf").exists() and (DATA / "Data1" / "Customer_Clip.dbf").stat().st_size > 10_000
needs_data = pytest.mark.skipif(not HAS_DATA, reason="real data (git lfs pull) not available")


# ---- forbidden columns ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("col", ["critical_c", "address", "NAME", "Outage_Num", "Latitude", "scada_cont"])
@needs_data
def test_forbidden_columns_are_refused(col):
    path = DATA / "Data1/Customer_Clip.dbf"
    with pytest.raises(ForbiddenColumnError):
        next(iter_records(path, [col]))


@needs_data
def test_premise_id_needs_explicit_allow():
    path = DATA / "Data1/Customer_Clip.dbf"
    with pytest.raises(ForbiddenColumnError):
        next(iter_records(path, ["premise_nu"]))
    assert next(iter_records(path, ["premise_nu"], allow=["premise_nu"]))["premise_nu"]


def test_banned_output_columns_cannot_be_written(tmp_path):
    bad = pa.schema([("latitude", pa.float64())])
    SCHEMAS["_bad"] = bad
    try:
        with pytest.raises(PrivacyError):
            write_table("_bad", [{"latitude": 1.0}], tmp_path)
    finally:
        del SCHEMAS["_bad"]


# ---- ARCOS cleaning -------------------------------------------------------------------------------------------
STORMS = [StormWindow(date(2024, 9, 27), date(2024, 10, 2), 193), StormWindow(date(2025, 2, 12), date(2025, 2, 18), 1254)]


def rec(**kw):
    base = {"Circuit": "CIR2", "Report_Tim": date(2025, 2, 13), "Repaired": "Yes", "Repaired_D": date(2025, 2, 14),
            "Asset_Type": "primary", "Inform_Wor": "Damaged Wire", "Access_Iss": "No", "Special_Eq": "", "Media": 1.0}
    base.update(kw)
    return base


def clean(r, point=(-79.9, 37.2)):
    return damage.clean_record(0, r, point, crosswalk={"CIR2": 16, "CIR9": 8}, topology_circuits={16}, storms=STORMS)


def test_label_goes_through_crosswalk():
    assert clean(rec())["circuit"] == 16 and clean(rec())["circuit_status"] == "mapped"
    assert clean(rec(Circuit="CIR9"))["circuit_status"] == "not_in_topology"
    assert clean(rec(Circuit="TINKER"))["circuit_status"] == "label_unknown"
    assert clean(rec(Circuit=""))["circuit_status"] == "blank_label" and clean(rec(Circuit=""))["circuit"] is None


def test_repair_days_and_backlog():
    assert clean(rec())["repair_days"] == 1 and clean(rec())["is_backlog"] is False
    r = clean(rec(Repaired_D=date(2025, 3, 10)))
    assert r["repair_days"] == 25 and r["is_backlog"] is True
    assert clean(rec(Repaired_D=date(2025, 2, 13)))["repair_days"] == 0


def test_unrepaired_is_censored_not_zero():
    r = clean(rec(Repaired="No", Repaired_D=date(2025, 12, 29)))     # placeholder date in the real data
    assert r["repaired"] is False and r["repair_days"] is None and r["is_backlog"] is None


def test_categorical_cleaning():
    r = clean(rec(Asset_Type="Fuse Bank", Inform_Wor="Damaged Switch", Access_Iss="Yes", Special_Eq="Backyard Bucket"))
    assert (r["asset_type"], r["damage_type"], r["access_issue"], r["special_equipment"]) == (
        "fuse_bank", "switch", True, "backyard_bucket")
    r = clean(rec(Access_Iss="", Asset_Type="Mystery"))
    assert r["access_issue"] is None and r["asset_type"] is None and r["special_equipment"] is None


def test_storm_assignment_and_no_exact_coordinates():
    assert clean(rec())["storm_id"] == "S2" and clean(rec(Report_Tim=date(2024, 9, 28), Repaired_D=date(2024, 9, 28)))["storm_id"] == "S1"
    assert clean(rec(Report_Tim=date(2025, 5, 31), Repaired_D=date(2025, 5, 31)))["storm_id"] is None
    r = clean(rec())
    assert "latitude" not in {k.lower() for k in r} and r["hex_cell"].startswith("88")


# ---- hex suppression ---------------------------------------------------------------------------------------------
def test_small_cells_fall_back_to_coarser_or_none():
    dense = [(-79.9 + i * 1e-5, 37.2) for i in range(12)]             # 12 points in one res-8 cell
    sparse = [(-79.5, 37.0)] * 3                                      # 3 points alone
    out = geo.assign_cells(dense + sparse + [None], min_group=10)
    assert all(res == 8 for _, res in out[:12])
    assert all(cell is None for cell, _ in out[12:])                  # never a cell with < 10 points
    assert out[-1] == (None, None)


def test_sparse_res8_cell_uses_parent_when_parent_is_big_enough():
    # 6 points in each of two neighbouring res-8 cells that share a res-7 parent => 12 at res 7
    cell_a = geo.h3.cell_to_children(geo.h3.cell_to_parent(geo.to_cell(-79.9, 37.2, 8), 7), 8)[:2]
    pts = []
    for c in cell_a:
        lat, lon = geo.h3.cell_to_latlng(c)
        pts += [(lon, lat)] * 6
    out = geo.assign_cells(pts, min_group=10)
    assert {res for _, res in out} == {7}


def test_leftovers_are_never_published_in_a_group_smaller_than_the_threshold():
    """12 dense points plus 3 stragglers in the same coarse cell: the stragglers must not form a group of 3."""
    dense = [(-79.9 + i * 1e-5, 37.2) for i in range(12)]
    straggler_cells = [c for c in geo.h3.cell_to_children(geo.h3.cell_to_parent(geo.to_cell(-79.9, 37.2, 8), 7), 8)
                       if c != geo.to_cell(-79.9, 37.2, 8)][:3]
    stragglers = [(geo.h3.cell_to_latlng(c)[1], geo.h3.cell_to_latlng(c)[0]) for c in straggler_cells]
    out = geo.assign_cells(dense + stragglers, min_group=10)
    groups = {}
    for cell, res in out:
        if cell:
            groups[(cell, res)] = groups.get((cell, res), 0) + 1
    assert groups and all(n >= 10 for n in groups.values())
    assert [c for c, _ in out[12:]] == [None, None, None]


def test_facility_flags_use_neighbour_cells_only():
    home = geo.to_cell(-79.9, 37.2, 8)
    near = next(iter(geo.nearby_cells(home, 1) - {home}))
    far = geo.to_cell(-79.0, 38.0, 8)
    flags = facilities.nearby_flags(home, {"hospital": {near}, "fire": {far}, "ambulance": set()})
    assert flags == {"hospital": True, "fire": False, "ambulance": False}


# ---- network ------------------------------------------------------------------------------------------------------
def line(pid, *devs, circuit="0000027"):
    return "\t".join([pid, *devs, f"eo_circuit*900*Circuit Breaker*{circuit}"]) + "\n"


X = lambda n: f"eo_power_xfrmr_inst*{n}*Distribution*n{n}"          # noqa: E731
F = lambda n: f"eo_isolating_eqpt_inst*{n}*Fuse*n{n}"                # noqa: E731
R = lambda n: f"eo_isolating_eqpt_inst*{n}*Recloser (Hydraulic)*n{n}"  # noqa: E731


def test_parse_chain():
    pid, chain = parse_chain(line("55", X(1), F(2)))
    assert pid == "55" and [c[0] for c in chain] == ["eo_power_xfrmr_inst", "eo_isolating_eqpt_inst", "eo_circuit"]
    assert parse_chain("garbage") is None and parse_chain("1\ta*b\tc*d*e*f") is None


def test_network_tree_majority_parent_and_alternate_path(tmp_path):
    lines = [line(str(100 + i), X(1), F(2), R(3)) for i in range(12)]          # 12 premises: X1 -> F2 -> R3 -> breaker
    lines += [line("200", X(1), F(2), R(4))]                                   # one premise sees F2 under R4 instead
    path = tmp_path / "topo.txt"
    path.write_text("".join(lines))
    customers = {"100": {"circuit": 27, "service": "Overhead", "cell": "8844c0a1b3fffff", "res": 8},
                 "999": {"circuit": 27, "service": "Underground", "cell": None, "res": None}}
    premises, devices, q = build_network(path, customers, SALT)
    by_key = {d["device_key"]: d for d in devices}
    f2 = by_key[device_key(SALT, "eo_isolating_eqpt_inst", "2")]
    assert f2["parent_key"] == device_key(SALT, "eo_isolating_eqpt_inst", "3")        # majority wins
    assert f2["n_premises_served"] == 13 and f2["is_protective"] and not f2["small_group"]
    assert by_key[device_key(SALT, "eo_circuit", "900")]["depth"] == 0 and by_key[device_key(SALT, "eo_circuit", "900")]["parent_key"] is None
    assert q["premises_on_alternate_path"] == 1 and q["devices_with_multiple_parents"] >= 1
    alt = [p for p in premises if p["alternate_path"]]
    assert len(alt) == 1 and alt[0]["service_type"] == "Unknown"                       # topology-only premise
    only_customer = [p for p in premises if not p["in_topology"]]
    assert len(only_customer) == 1 and only_customer[0]["circuit"] is None and q["customers_without_topology"] == 1
    p0 = next(p for p in premises if p["in_customer_file"] and p["in_topology"])
    assert (p0["circuit"], p0["protective_count"], p0["hops"]) == (27, 2, 3)
    text = json.dumps([premises, devices])
    assert "100" not in {p["premise_token"] for p in premises} and '"id": 900' not in text


def test_device_keys_are_salted():
    assert device_key(b"a", "c", "1") != device_key(b"b", "c", "1") and device_key(b"a", "c", "1").startswith("d_")


# ---- labor workbook ---------------------------------------------------------------------------------------------------
LABOR_XLSX = DATA / "Install_Labor_HackOHIO.xlsx"


@pytest.mark.skipif(not LABOR_XLSX.exists(), reason="labor workbook not available")
def test_labor_workbook_parses():
    d = parse_labor(LABOR_XLSX)
    assert d["indirect_labor_multiplier"] == 4.5
    by_cat = {}
    for u in d["units"]:
        by_cat.setdefault(u["category"], []).append(u)
    assert [round(u["install_hours"], 2) for u in by_cat["pole"]] == [1.94, 2.5, 3.37]
    assert round(by_cat["recloser"][0]["install_hours"], 2) == 0.57 and round(by_cat["fuse_cutout"][0]["install_hours"], 2) == 0.26
    assert len(by_cat["transformer"]) == 12
    adders = {a["key"]: a for a in d["adders"]}
    assert adders["remote_location_adder"]["hours"] == 1.312 and adders["difficult_excavation"]["unit"] == "per cubic foot"


def test_labor_validation_rejects_bad_data():
    with pytest.raises(ValueError, match="missing categories"):
        validate_labor({"units": [], "indirect_labor_multiplier": 4.5})
    with pytest.raises(ValueError):
        validate_labor({"units": [{"category": c, "install_hours": 1.0} for c in
                                  ("pole", "transformer", "recloser", "switch", "fuse_cutout", "crossarm", "connector")],
                        "indirect_labor_multiplier": None})


# ---- end to end on the real data --------------------------------------------------------------------------------------
@pytest.mark.realdata
@needs_data
def test_real_data_curation(tmp_path):
    res = curate(data_dir=DATA, docs_dir=DOCS, out_dir=tmp_path, salt=SALT, drawings=False)
    q = res["quality"]
    assert q["rows"]["premise_dim"] == 117646 and q["rows"]["circuit_dim"] == 110 and q["rows"]["damage_fact"] == 1659
    assert "malformed_lines" not in q["network"]
    assert [s["records"] for s in q["storms"]] == [193, 1254]
    assert q["damage"]["unrepaired_censored"] == 76 and q["damage"]["circuit_status"]["mapped"] == 1645

    # every published table: no banned column, no raw premise ids, no exact coordinates
    prem = pq.read_table(tmp_path / "tables/premise_dim.parquet").to_pylist()
    assert all(p["premise_token"].startswith("p_") for p in prem)
    no_location = sum(p["hex_cell"] is None for p in prem)
    topology_only = sum(not p["in_customer_file"] for p in prem)          # no customer row, so no coordinates
    assert no_location == q["hex_resolution_counts"]["none"] + topology_only
    assert min(h["n_premises"] for h in pq.read_table(tmp_path / "tables/hex_context.parquet").to_pylist()) >= 10
    for f in (tmp_path / "tables").glob("*.parquet"):
        cols = {c.lower() for c in pq.read_schema(f).names}
        assert not cols & {"premise_nu", "critical_c", "address", "latitude", "longitude", "name"}
    raw_ids = {l.split("\t", 1)[0] for _, l in zip(range(200), open(DATA / "matched_upstream/matched_upstream.txt"))}
    blob = b"".join(f.read_bytes() for f in (tmp_path / "tables").glob("*.parquet"))
    assert not any(i.encode() in blob for i in raw_ids)

    # device tree is consistent: every parent exists, depths decrease towards the breaker
    dev = {d["device_key"]: d for d in pq.read_table(tmp_path / "tables/device_dim.parquet").to_pylist()}
    for d in dev.values():
        if d["parent_key"] is None:
            assert d["device_class"] == "breaker" and d["depth"] == 0
        else:
            assert d["parent_key"] in dev and dev[d["parent_key"]]["depth"] == d["depth"] - 1
    assert sum(d["device_class"] == "breaker" for d in dev.values()) == 110

    # circuit_dim agrees with the risk engine's inputs
    cd = {r["circuit"]: r for r in pq.read_table(tmp_path / "tables/circuit_dim.parquet").to_pylist()}
    assert sum(r["n_premises"] for r in cd.values()) == 117599 and sum(r["damage_s2"] for r in cd.values()) > 1000
    assert cd[16]["damage_s2"] > cd[2]["damage_s2"] or cd[16]["damage_s1"] + cd[16]["damage_s2"] > 0

    # manifest lists published files only, with correct hashes; internal files are not published
    files = {f["path"] for f in res["manifest"]["files"]}
    assert "labor_standards.json" in files and not any(p.startswith("internal") for p in files)
    from concierge.curate.pipeline import sha256
    assert all(sha256(tmp_path / f["path"]) == f["sha256"] for f in res["manifest"]["files"])
    assert (tmp_path / "internal/circuit_crosswalk.parquet").exists()
