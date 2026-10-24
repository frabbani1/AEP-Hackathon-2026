"""Builds a small synthetic raw data directory that mirrors the real inputs.

Nothing here is real data. Shapefiles are generated at test time from the input
contracts (which were derived from the real layers' metadata), so every fixture layer
has exactly the real field names and types. Generating at test time also avoids
committing binary ``.dbf`` files, which this repository tracks with Git LFS.

Mini network (circuit numbers are synthetic)::

    B1 (0000001) -- R1 -- F1 -- T1: P01 P02 P03
                     |      '--- T2: P04 P05 P06 P07
                     |---- F2 -- T3: P08 P09 P10 P11 P12
                     '---- F3 -- STEP1 -- T5: P13
    B1 (0000001) -- R2 -- F3 -- T6: P14            (F3 has two parents: R1 and R2)
    B2 (0000002) -- T7: P15 P16
    B3 (0000003) -- T8: P17
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import openpyxl
import pandas as pd
import yaml
from pyproj import CRS, Transformer
from pyproj.crs import CompoundCRS
from shapely.geometry import LineString, Point

from aep_pipeline.contracts import LayerContract, default_config_dir, load_layer_contracts

BASE_LON = -80.0
BASE_LAT = 37.0
CANARY_PREFIX = "CANARY"

# --------------------------------------------------------------------------- network definition
TRANSFORMER = "eo_power_xfrmr_inst"
ISOLATING = "eo_isolating_eqpt_inst"
CIRCUIT = "eo_circuit"

# name -> (object type, object id, subtype, device key, circuit numeric id, alias, lon index, lat index)
DEVICES: dict[str, tuple] = {
    "T1": (TRANSFORMER, 4001, "Distribution", "LOC-T1", "0000001", "CIR1", 0, 0),
    "T2": (TRANSFORMER, 4002, "Distribution", "LOC-T2", "0000001", "CIR1", 1, 0),
    "T3": (TRANSFORMER, 4003, "Distribution", "LOC-T3", "0000001", "CIR1", 2, 0),
    "T5": (TRANSFORMER, 4005, "Distribution", "LOC-T5", "0000001", "CIR1", 3, 0),
    "T6": (TRANSFORMER, 4006, "Distribution", "LOC-T6", "0000001", "CIR1", 4, 0),
    "T7": (TRANSFORMER, 4007, "Distribution", "LOC-T7", "0000002", "CIR3", 5, 0),  # alias differs: ambiguous circuit
    "T8": (TRANSFORMER, 4008, "Distribution", "LOC-T8", "0000003", "CIR4", 6, 0),
    "STEP1": (TRANSFORMER, 4101, "Step", "LOC-S1", "0000001", "CIR1", 7, 0),
    "F1": (ISOLATING, 5001, "Fuse", "LOC-F1", "0000001", "CIR1", 0, 1),
    "F2": (ISOLATING, 5002, "Fuse", "LOC-F2", "0000001", "CIR1", 1, 1),
    "F3": (ISOLATING, 5003, "Fuse", "LOC-F3", "0000001", "CIR1", 2, 1),
    "R1": (ISOLATING, 7001, "Recloser (Hydraulic)", "LOC-R1", "0000001", "CIR1", 0, 2),
    "R2": (ISOLATING, 7002, "Recloser (Electronic)", "LOC-R2", "0000001", "CIR1", 1, 2),
    "B1": (CIRCUIT, 9001, "Circuit Breaker", "0000001", "0000001", "CIR1", 0, 3),
    "B2": (CIRCUIT, 9002, "Circuit Breaker", "0000002", "0000002", "CIR2", 1, 3),
    "B3": (CIRCUIT, 9003, "Circuit Breaker", "0000003", "0000003", "CIR4", 2, 3),
}
# GIS ids of reclosers differ from their topology object ids; only the location key matches.
RECLOSER_GIS_IDS = {"R1": 6101, "R2": 6102}
GIS_ONLY_TRANSFORMER = (4999, "LOC-T99", "0000002", "CIR2")
GIS_ONLY_FUSE = (5999, "LOC-F99", "0000002", "CIR2")

CHAINS: dict[str, list[str]] = {
    **{f"P{n:02d}": ["T1", "F1", "R1", "B1"] for n in (1, 2, 3)},
    **{f"P{n:02d}": ["T2", "F1", "R1", "B1"] for n in (4, 5, 6, 7)},
    **{f"P{n:02d}": ["T3", "F2", "R1", "B1"] for n in (8, 9, 10, 11, 12)},
    "P13": ["T5", "STEP1", "F3", "R1", "B1"],
    "P14": ["T6", "F3", "R2", "B1"],
    "P15": ["T7", "B2"],
    "P16": ["T7", "B2"],
    "P17": ["T8", "B3"],
}
# 9-digit synthetic premise numbers
PREMISE_NUMBER = {name: f"9000000{int(name[1:]):02d}" for name in CHAINS}
LIFE_SUPPORT_PREMISE = "P02"


def xy(lon_i: int, lat_i: int, dx: float = 0.0, dy: float = 0.0) -> tuple[float, float]:
    return BASE_LON + 0.002 * lon_i + dx, BASE_LAT + 0.002 * lat_i + dy


def device_xy(name: str) -> tuple[float, float]:
    d = DEVICES[name]
    return xy(d[6], d[7])


# --------------------------------------------------------------------------- options
@dataclass
class FixtureOptions:
    customers_without_chain: int = 0
    topology_only_premises: int = 0
    malformed_topology_lines: int = 0
    unlinked_fuse_damage: bool = False
    extra_topology_device_not_in_gis: bool = False


@dataclass
class FixtureInfo:
    raw_dir: Path
    canary_values: set[str] = field(default_factory=set)
    premise_numbers: dict[str, str] = field(default_factory=dict)
    life_support_marker: str = "LS Life Support"
    metadata_canaries: tuple[str, ...] = ("CANARY-METADATA-AUTHOR", "CANARY-METADATA-PATH")


# --------------------------------------------------------------------------- generic frame helpers
def _blank_columns(contract: LayerContract, n: int, canaries: set[str]) -> dict[str, object]:
    data: dict[str, object] = {}
    for name, spec in contract.fields.items():
        if spec.dtype == "int64":
            data[name] = np.zeros(n, dtype="int64")
        elif spec.dtype == "int32":
            data[name] = np.zeros(n, dtype="int32")
        elif spec.dtype == "float64":
            data[name] = np.zeros(n, dtype="float64")
        elif spec.dtype == "datetime64[D]":
            data[name] = pd.Series([pd.NaT] * n, dtype="datetime64[ms]")
        else:
            values = []
            for i in range(n):
                if spec.sensitivity == "public":
                    values.append(f"pub_{name}_{i}")
                else:
                    value = f"{CANARY_PREFIX}_{name}_{i}"
                    canaries.add(value)
                    values.append(value)
            data[name] = values
    return data


def _write_layer(
    raw_dir: Path,
    contract: LayerContract,
    n: int,
    geometries: list,
    overrides: dict[str, object],
    canaries: set[str],
    source_crs: object = 4326,
    compound_prj: str | None = None,
) -> None:
    data = _blank_columns(contract, n, canaries)
    for name, values in overrides.items():
        if name not in contract.fields:
            raise KeyError(f"{contract.name}: override for unknown field {name}")
        spec = contract.fields[name]
        if spec.dtype == "datetime64[D]":
            values = pd.Series(pd.to_datetime(values), dtype="datetime64[ms]")
        elif spec.dtype in ("int64", "int32", "float64"):
            values = np.asarray(values, dtype=spec.dtype)
        data[name] = values
        # overridden values are intentional, remove any canary that they replaced
    frame = gpd.GeoDataFrame(data, geometry=geometries, crs=source_crs)
    path = raw_dir / contract.path
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(path, driver="ESRI Shapefile", engine="pyogrio")
    if compound_prj:
        path.with_suffix(".prj").write_text(compound_prj, encoding="ascii")


def _metadata_xml(path: Path, info: FixtureInfo) -> None:
    path.write_text(
        "<metadata><Esri><CreaDate>20260101</CreaDate></Esri>"
        f"<idinfo><Host>{info.metadata_canaries[1]}</Host>"
        f"<Author>{info.metadata_canaries[0]}</Author></idinfo></metadata>",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- individual layers
def build_raw_dir(
    dest: Path,
    config_dir: Path | None = None,
    options: FixtureOptions | None = None,
) -> FixtureInfo:
    """Write the synthetic raw directory into ``dest`` and return what was written."""
    options = options or FixtureOptions()
    contracts = load_layer_contracts(config_dir)
    info = FixtureInfo(raw_dir=dest)
    dest.mkdir(parents=True, exist_ok=True)
    canaries = info.canary_values

    # ---- Transformer_Clip
    tnames = [n for n, d in DEVICES.items() if d[0] == TRANSFORMER]
    rows = [DEVICES[n] for n in tnames]
    n = len(rows) + 1
    t_over = {
        "id": [r[1] for r in rows] + [GIS_ONLY_TRANSFORMER[0]],
        "location_n": [r[3] for r in rows] + [GIS_ONLY_TRANSFORMER[1]],
        "circuit_id": [r[4] for r in rows] + [GIS_ONLY_TRANSFORMER[2]],
        "aep_circui": [r[5] for r in rows] + [GIS_ONLY_TRANSFORMER[3]],
        "aep_statio": [f"STATION{r[4][-1]}" for r in rows] + ["STATION2"],
        "device_typ": ["Distribution"] * n,
        "source_nom": ["12.47 kV"] * n,
        "load_nomin": ["240.00 V"] * n,
        "structure_": ["Pole"] * n,
        "bank_confi": ["Single Phase"] * n,
        "mounting": ["Overhead"] * n,
        "status": ["Existing"] * n,
        "owner_type": ["Company Owned"] * n,
        "aep_custom": [3, 4, 5, 1, 1, 2, 1, 1, 0],
    }
    t_geoms = [Point(*xy(r[6], r[7]), 10.0) for r in rows] + [Point(*xy(9, 9), 10.0)]
    _write_layer(dest, contracts["Transformer_Clip"], n, t_geoms, t_over, canaries)

    # ---- Fuse_Bank_Clip
    fnames = ["F1", "F2", "F3"]
    frows = [DEVICES[x] for x in fnames]
    n = len(frows) + 1
    f_over = {
        "id": [r[1] for r in frows] + [GIS_ONLY_FUSE[0]],
        "location_n": [r[3] for r in frows] + [GIS_ONLY_FUSE[1]],
        "circuit_id": [r[4] for r in frows] + [GIS_ONLY_FUSE[2]],
        "aep_circui": [r[5] for r in frows] + [GIS_ONLY_FUSE[3]],
        "aep_statio": ["STATION1"] * 3 + ["STATION2"],
        "device_typ": ["Fuse"] * n,
        "normal_sta": ["Closed"] * n,
        "status": ["Existing"] * n,
        "owner_type": ["Company Owned"] * n,
        "mounting": ["Overhead"] * n,
        "network_ty": ["MV"] * n,
        "nominal_vo": ["12.47 kV"] * n,
        "aep_custom": [7, 5, 2, 0],
    }
    f_geoms = [Point(*xy(r[6], r[7]), 10.0) for r in frows] + [Point(*xy(9, 8), 10.0)]
    _write_layer(dest, contracts["Fuse_Bank_Clip"], n, f_geoms, f_over, canaries)

    # ---- Recloser_Bank_Clip
    rnames = ["R1", "R2"]
    rrows = [DEVICES[x] for x in rnames]
    n = len(rrows)
    r_over = {
        "id": [RECLOSER_GIS_IDS[x] for x in rnames],
        "location_n": [r[3] for r in rrows],
        "circuit_id": [r[4] for r in rrows],
        "aep_circui": [r[5] for r in rrows],
        "aep_statio": ["STATION1"] * n,
        "type": ["Recloser"] * n,
        "device_typ": [r[2] for r in rrows],
        "normal_sta": ["Closed"] * n,
        "status": ["Existing"] * n,
        "owner_type": ["Company Owned"] * n,
        "nominal_vo": ["12.47 kV"] * n,
        "aep_custom": [13, 1],
    }
    r_geoms = [Point(*xy(r[6], r[7]), 10.0) for r in rrows]
    _write_layer(dest, contracts["Recloser_Bank_Clip"], n, r_geoms, r_over, canaries)

    # ---- Switch_Clip (not part of the topology)
    n = 2
    s_over = {
        "id": [8001, 8002],
        "location_n": ["LOC-W1", "LOC-W2"],
        "circuit_id": ["0000001", "0000001"],
        "aep_circui": ["CIR1", "CIR1"],
        "aep_statio": ["STATION1"] * n,
        "device_typ": ["Switch"] * n,
        "normal_sta": ["Closed", "Open"],
        "status": ["Existing"] * n,
        "owner_type": ["Company Owned"] * n,
        "mounting": ["Overhead"] * n,
        "network_ty": ["MV"] * n,
        "nominal_vo": ["12.47 kV"] * n,
        "aep_custom": [0, 0],
    }
    s_geoms = [Point(*xy(4, 2), 10.0), Point(*xy(5, 2), 10.0)]
    _write_layer(dest, contracts["Switch_Clip"], n, s_geoms, s_over, canaries)

    # ---- Pole_Clip
    n = 4
    heights = ["40 Feet", "Unknown", "35 Feet", "60 Feet"]
    p_over = {
        "id": [3001, 3002, 3003, 3004],
        "status": ["Existing"] * n,
        "usage": ["Pole"] * n,
        "material": ["Pine"] * n,
        "height": heights,
        "class": ["4", "Unknown", "5", "2"],
        "date_insta": ["1995-01-01", None, "2001-06-15", None],
        "date_manuf": [None, None, "1999-01-01", None],
        "owner_type": ["Company Owned"] * n,
    }
    p_geoms = [Point(*xy(i, 4), 20.0) for i in range(n)]
    _write_layer(dest, contracts["Pole_Clip"], n, p_geoms, p_over, canaries)

    # ---- Primary / Secondary lines
    def line_over(count: int, base_id: int, mv: bool) -> dict:
        return {
            "id": [base_id + i for i in range(count)],
            "status": ["Existing"] * count,
            "network_ty": ["MV" if mv else "LV"] * count,
            "nominal_vo": ["12.47 kV" if mv else "240.00 V"] * count,
            "usage": ["Lateral"] * count,
            "phase_orde": ["A"] * count,
            "circuit_id": ["0000001"] * count,
            "aep_statio": ["STATION1"] * count,
            "aep_circui": ["CIR1"] * count,
            "aep_constr": ["Pin Top"] * count,
            "mounting": ["Overhead"] * count,
            "report_len": [100.0 + i for i in range(count)],
            "aep_date_i": ["1900-01-01", "2010-05-05", None][:count],
            "owner_type": ["Company Owned"] * count,
            "cable_coun": [1] * count,
        }

    prim_geoms = [LineString([xy(0, 5), xy(1, 5)]), LineString([xy(1, 5), xy(2, 5)]), LineString([xy(2, 5), xy(3, 5)])]
    _write_layer(dest, contracts["Primary_ExportFeatures"], 3, prim_geoms, line_over(3, 1000, True), canaries)
    sec_over = line_over(2, 2000, False)
    sec_over["spec_name"] = ["4/0 AL XLPE", "2 AL LDPE"]
    sec_geoms = [LineString([xy(0, 6), xy(1, 6)]), LineString([xy(1, 6), xy(2, 6)])]
    _write_layer(dest, contracts["Secondary_ExportFeatures"], 2, sec_geoms, sec_over, canaries)

    # ---- Customer_Clip
    premises = list(CHAINS)
    extra_no_chain = [f"X{idx:02d}" for idx in range(options.customers_without_chain)]
    all_customers = premises + extra_no_chain
    n = len(all_customers)
    numbers = {name: PREMISE_NUMBER[name] for name in premises}
    for idx, name in enumerate(extra_no_chain):
        numbers[name] = f"9100000{idx:02d}"
    info.premise_numbers = dict(numbers)
    circuits = []
    xfmr_keys = []
    geoms = []
    for k, name in enumerate(all_customers):
        if name in CHAINS:
            chain = CHAINS[name]
            circuits.append(DEVICES[chain[-1]][3])
            xfmr_keys.append(DEVICES[chain[0]][3])
            tx = device_xy(chain[0])
        else:
            circuits.append("0000001")
            xfmr_keys.append("LOC-T1")
            tx = device_xy("T1")
        geoms.append(Point(tx[0] + 0.0002 * (k % 3), tx[1] + 0.0002 * (k % 2), 0.0))
    for k, number in enumerate(numbers.values()):
        canaries.add(number)
    c_over = {
        "premise_nu": [numbers[name] for name in all_customers],
        "circuit_id": circuits,
        "aep_xfmr_l": xfmr_keys,
        "customer_c": [1 + (k % 2) for k in range(n)],
        "kind_of_se": ["Overhead" if k % 3 else "Underground" for k in range(n)],
        "critical_c": [
            info.life_support_marker if name == LIFE_SUPPORT_PREMISE else "N/A" for name in all_customers
        ],
        "address": [f"{CANARY_PREFIX} ADDRESS {k}" for k in range(n)],
        "road_name": [f"{CANARY_PREFIX} ROAD {k}" for k in range(n)],
        "city": [f"{CANARY_PREFIX} CITY {k}" for k in range(n)],
        "esri_defau": ["NORMAL"] * n,
    }
    canaries.update(c_over["address"])
    canaries.update(c_over["road_name"])
    canaries.update(c_over["city"])
    canaries.add(info.life_support_marker)
    _write_layer(dest, contracts["Customer_Clip"], n, geoms, c_over, canaries)

    # ---- ARCOS damage (coordinates sit on or near assets)
    f1x, f1y = device_xy("F1")
    t1x, t1y = device_xy("T1")
    r1x, r1y = device_xy("R1")
    meters_3 = 3.0 / 111_320.0
    damage = [
        # asset_type, damage type, x, y, circuit, station, access, equipment, report, repaired
        ("Fuse Bank", "Damaged Switch", f1x, f1y + meters_3, "CIR1", "STATION1", "No", None, "2025-02-13", "2025-02-14"),
        ("Transformer", "Damaged Equipment", t1x, t1y, "CIR1", "STATION1", "Yes", "Backyard Bucket", "2025-02-13", "2025-02-13"),
        ("Pole", "Damaged Pole", *xy(1, 4), "CIR1", "STATION1", "No", None, "2025-02-14", "2025-02-16"),
        ("primary", "Damaged Wire", *xy(0, 5, 0.0005, 0.0), "CIR1", "STATION1", "No", None, "2025-02-14", "2025-02-14"),
        ("secondary", "Damaged Wire", *xy(1, 6, 0.0005, 0.0), "CIR1", "STATION1", "Yes", "Track Hoe", "2025-02-15", "2025-02-18"),
        ("Recloser Bank", "Damaged Switch", r1x, r1y, "CIR1", "STATION1", "No", None, "2025-02-13", "2025-02-13"),
        ("Switch", "Damaged Switch", *xy(4, 2), "CIR1", "STATION1", "No", None, "2025-02-15", "2025-02-15"),
        ("Capacitor Bank", "Damaged Equipment", *xy(8, 8), "CIR1", "STATION1", "No", None, "2025-02-15", "2025-02-16"),
        ("Light", "Damaged Light", *xy(8, 9), "CIR1", "STATION1", "No", None, "2025-02-16", "2025-02-16"),
        ("Fuse Bank", "Damaged Switch", *xy(9, 8), "CIR2", "STATION2", "No", None, "2025-02-16", "2025-02-17"),  # GIS-only fuse
    ]
    if options.unlinked_fuse_damage:
        damage.append(("Fuse Bank", "Damaged Switch", f1x + 0.01, f1y, "CIR1", "STATION1", "No", None, "2025-02-17", "2025-02-17"))
    n = len(damage)
    a_over = {
        "Substation": [d[5] for d in damage],
        "Circuit": [d[4] for d in damage],
        "Report_Tim": [d[8] for d in damage],
        "Repaired_D": [d[9] for d in damage],
        "Repaired": ["Yes"] * n,
        "Asset_Type": [d[0] for d in damage],
        "Inform_Wor": [d[1] for d in damage],
        "Media": [1.0] * n,
        "Access_Iss": [d[6] for d in damage],
        "Special_Eq": [d[7] for d in damage],
    }
    a_geoms = [Point(d[2], d[3], 0.0) for d in damage]
    _write_layer(dest, contracts["arcos_prod_damage_09_28_Clip"], n, a_geoms, a_over, canaries)

    # ---- Priority facilities in a projected system with a height component
    compound = CompoundCRS(
        "WGS 84 / Pseudo-Mercator + NAVD88 height", [CRS.from_epsg(3857), CRS.from_epsg(5703)]
    )
    to_mercator = Transformer.from_crs(4326, 3857, always_xy=True)
    facility_sites = {
        "Ambulance_Servi_Pairwis_Clip": [xy(0, 0, 0.001, 0.001), xy(6, 6, 0.0, 0.0)],
        "Fire_Stations_E_Pairwis_Clip": [xy(2, 2, 0.001, 0.0), xy(7, 3, 0.0, 0.0), xy(1, 5, 0.0, 0.0)],
        "Hospitals_Medic_Pairwis_Clip": [xy(5, 1, 0.0, 0.0)],
    }
    for layer, sites in facility_sites.items():
        n = len(sites)
        names = [f"{CANARY_PREFIX} FACILITY {layer[:4]} {i}" for i in range(n)]
        addrs = [f"{CANARY_PREFIX} FACILITY ADDRESS {layer[:4]} {i}" for i in range(n)]
        canaries.update(names)
        canaries.update(addrs)
        geoms = [Point(*to_mercator.transform(lon, lat), 250.0) for lon, lat in sites]
        _write_layer(
            dest,
            contracts[layer],
            n,
            geoms,
            {"NAME": names, "ADDRESS": addrs, "FTYPE": [740] * n, "FCODE": [74001] * n},
            canaries,
            source_crs=3857,
            compound_prj=compound.to_wkt(version="WKT1_GDAL"),
        )

    # ---- metadata files that carry canary author / path strings
    for shp in sorted(dest.rglob("*.shp")):
        _metadata_xml(shp.with_name(shp.name + ".xml"), info)
    (dest / "structure_drawings.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj<</Author(" + info.metadata_canaries[0].encode() + b")>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    )

    _write_topology(dest, options, premises, numbers, info)
    _write_workbook(dest)
    return info


# --------------------------------------------------------------------------- topology text
def _node_text(name: str) -> str:
    d = DEVICES[name]
    return f"{d[0]}*{d[1]}*{d[2]}*{d[3]}"


def _write_topology(dest: Path, options: FixtureOptions, premises: list[str], numbers: dict[str, str], info: FixtureInfo) -> None:
    lines = []
    for name in premises:
        nodes = [_node_text(n) for n in CHAINS[name]]
        lines.append("\t".join([numbers[name], *nodes]))
    for idx in range(options.topology_only_premises):
        number = f"9200000{idx:02d}"
        info.canary_values.add(number)
        lines.append("\t".join([number, *[_node_text(n) for n in ("T8", "B3")]]))
    if options.extra_topology_device_not_in_gis:
        lines.append("\t".join(["930000001", f"{TRANSFORMER}*4777*Distribution*LOC-T77", _node_text("B3")]))
    for idx in range(options.malformed_topology_lines):
        lines.append("\t".join([f"94000000{idx}", f"{TRANSFORMER}*4001*Distribution", _node_text("B1")]))
    path = dest / "matched_upstream" / "matched_upstream.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


# --------------------------------------------------------------------------- workbook
def _write_workbook(dest: Path) -> None:
    wb = openpyxl.Workbook()

    cnc = wb.active
    cnc.title = "CNC"
    cnc.append(["CD_CU", "INSTALL_HOURS", "REMOVE_HOURS", None, "Connector CU Average Hours"])
    cnc.append(["CNC-TEST-10", 0.101, 0.011])
    cnc.append(["CNC-TEST-20", 0.202, 0.022])
    cnc.append(["CNC-TEST-30", 0.303, 0.033])
    cnc.append(["CNC-ZERO-0", 0, 0])  # a valid zero-hour record, not a missing value

    ltm = wb.create_sheet("Labor time material related")
    ltm.append(["Crossarm CU Average Hours"])
    ltm.append(["Avg Install Hours", "Avg Removal Hours"])
    ltm.append([0.31, 0.21])
    ltm.append([])
    ltm.append(["Pole CU Average Hours by Size"])
    ltm.append(["Size Group", "Height Range", "Avg Install Hours", "Avg Removal Hours"])
    ltm.append(["Small", "25\u201335 ft", 1.5, 0.5])
    ltm.append(["Medium", "40\u201355 ft", 2.5, 0.9])
    ltm.append(["Large", "60 ft and above", 3.5, 1.2])
    ltm.append([])
    ltm.append(["Transformer CU Average Hours by Size"])
    ltm.append(["Size (kVA)", "CU Count", "Avg Install Hours", "Avg Removal Hours"])
    ltm.append([10, 35, 1.0, 0.8])
    ltm.append([25, 47, 1.2, 0.9])
    ltm.append([50, 47, 1.4, 1.0])
    ltm.append([])
    ltm.append(["Recloser CU Average Hours"])
    ltm.append(["Avg Install Hours", "Avg Removal Hours"])
    ltm.append([0.6, 0.5])
    ltm.append([])
    ltm.append(["Switch CU Average Hours"])
    ltm.append(["Avg Install Hours", "Avg Removal Hours"])
    ltm.append([1.7, 1.0])
    ltm.append([])
    ltm.append(["Fuse Cutout Average Hours"])
    ltm.append(["Avg Install Hours", "Avg Removal Hours"])
    ltm.append([0.25, 0.125])

    low = wb.create_sheet("Labor only work")
    low.append(["Original Intent", "Anonymized Work Type", "Anonymized Labor Description", "INSTALL_HOURS"])
    low.append(["Difficult Location", "Restricted Access Work", "Extra labor for limited access.", 0.5])
    low.append(["Backyard Adder", "Confined Property Access", "Extra labor for confined property.", 0.5])
    low.append(["Off-Road / Track Access", "Remote Site Access", "Extra labor for non-standard routes.", 0.5])
    low.append(["Hand Dig", "Manual Excavation", "Extra labor when digging by hand.", 1.5, "per cubic foot"])
    low.append(["Rock Hole", "Difficult Excavation", "Extra labor for rock.", 4, "per cubic foot"])
    low.append(["Junction Pole Work", "Complex Work Location", "Extra labor where assets converge."])
    low.append(["Access Constraint Adder", "Apply when normal equipment access is obstructed or limited.", None, 0.5])
    low.append(["Remote Location Adder", "Apply when a work location cannot be reached by standard methods.", None, 1.0])
    low.append(["Specialized Handling Adder", "Apply when extra lifting or positioning resources are needed.", None, 0.5])
    low.append(["Site Complexity Adder", "Apply when site conditions increase setup effort.", None, 0.5])

    ilm = wb.create_sheet("Indirect Labor Multiplier")
    ilm.append(["Indirect labor multipler (ILM)", 4.0])
    ilm.append([])
    ilm.append(["The ILM should be mutiplied time any labor hours to take into account drive time."])

    cu = wb.create_sheet("CU Group descriptions")
    cu.append(["Category", "Suggested Anonymous Description"])
    for code, text in (
        ("ARM", "Install, remove, or relocate structural support components."),
        ("POL", "Perform work involving a primary supporting structure."),
        ("REC", "Install, remove, or relocate reclosers."),
        ("SWI", "Install, remove, or relocate switching equipment."),
        ("XCO", "Install, remove, or relocate protective interruption devices."),
        ("XRA", "Additional labor associated with restricted access locations."),
    ):
        cu.append([code, text])

    path = dest / "Install_Labor_HackOHIO.xlsx"
    wb.save(path)


# --------------------------------------------------------------------------- test configuration
def make_test_config(dest: Path, **threshold_overrides: object) -> Path:
    """Copy the real configuration and relax minimum record counts for the tiny fixture."""
    source = default_config_dir()
    shutil.copytree(source, dest)
    for path in (dest / "contracts").glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "min_records" in data:
            data["min_records"] = 1
        if "min_rows" in data:
            data["min_rows"] = {k: 1 for k in data["min_rows"]}
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    thresholds_path = dest / "thresholds.yaml"
    if thresholds_path.exists():
        data = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
        for key, value in threshold_overrides.items():
            data[key] = value
        thresholds_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return dest
