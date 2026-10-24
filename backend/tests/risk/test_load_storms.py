from datetime import date, timedelta

import pytest

from concierge.risk.load import (CrosswalkError, ForbiddenColumnError, load_arcos, load_crosswalk, load_customers, load_topology, normalize_circuit,
                                 normalize_service, parse_topology_line, read_dbf)
from concierge.risk.storms import StormDetectionError, count_days, detect_storms, storm_index


# ---- 2.1 whitelisted loaders -----------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["critical_c", "address", "road_name", "city"])
def test_sensitive_columns_are_refused(synth, bad):
    with pytest.raises(ForbiddenColumnError):
        list(read_dbf(synth.customer_dbf, {"premise_nu", bad}, "customer"))


def test_life_support_flag_is_not_loaded(synth):
    customers = load_customers(synth.customer_dbf)
    assert synth.life_support_ids, "fixture should contain life-support rows"
    # values are (circuit, service) only: nothing from critical_c or address can be present
    for pid, value in customers.items():
        assert len(value) == 2 and all("LS" not in str(v) and "MAIN ST" not in str(v) for v in value)


def test_customer_loader_reads_only_three_columns(synth):
    rows = list(read_dbf(synth.customer_dbf, {"premise_nu", "circuit_id", "kind_of_se"}, "customer"))
    assert set(rows[0]) == {"premise_nu", "circuit_id", "kind_of_se"}


def test_arcos_loader_returns_raw_labels_not_circuit_ids(synth):
    recs = load_arcos(synth.arcos_dbf)
    assert any(c is None for _, c in recs)       # blank label
    assert any(c == "CIR18" for _, c in recs)     # label for circuit 17: kept as text, never parsed
    assert all(d is None or isinstance(d, date) for d, _ in recs)


def test_device_loader_refuses_other_columns(synth):
    with pytest.raises(ForbiddenColumnError):
        list(read_dbf(synth.device_dbfs[0], {"circuit_id", "scada_cont"}, "gis_device"))


def test_crosswalk_maps_label_to_circuit_id(synth):
    cw = load_crosswalk(synth.device_dbfs)
    assert cw["CIR18"] == 17 and cw["CIR12"] == 11          # shifted: label number != circuit id
    assert "TINKER" not in cw


def test_crosswalk_conflict_is_fatal(tmp_path):
    from .fixtures import write_dbf
    fields = [("circuit_id", "C", 32), ("aep_circui", "C", 64)]
    write_dbf(tmp_path / "a.dbf", fields, [{"circuit_id": "0000005", "aep_circui": "CIR6"}])
    write_dbf(tmp_path / "b.dbf", fields, [{"circuit_id": "0000006", "aep_circui": "CIR6"}])
    with pytest.raises(CrosswalkError):
        load_crosswalk([tmp_path / "a.dbf", tmp_path / "b.dbf"])


# ---- 2.2 topology parser ---------------------------------------------------------------------------------
REAL_LINE = (
    "551102222\teo_power_xfrmr_inst*107301290*Distribution*37800368000008"
    "\teo_isolating_eqpt_inst*106806731*Recloser (Hydraulic)*A"
    "\teo_isolating_eqpt_inst*106806741*Recloser (Hydraulic)*B"
    "\teo_isolating_eqpt_inst*106806734*Recloser (Hydraulic)*C"
    "\teo_isolating_eqpt_inst*308527994*Recloser (Electronic)*D"
    "\teo_circuit*157965269*Circuit Breaker*0000027\n"
)


def test_parse_real_topology_line():
    row = parse_topology_line(REAL_LINE)
    assert (row.premise_id, row.circuit, row.protective, row.hops) == ("551102222", 27, 4, 5)


def test_step_transformer_is_not_protective():
    line = ("1\teo_power_xfrmr_inst*1*Distribution*a\teo_power_xfrmr_inst*2*Step*b"
            "\teo_isolating_eqpt_inst*3*Fuse*c\teo_circuit*4*Circuit Breaker*0000005\n")
    row = parse_topology_line(line)
    assert (row.protective, row.hops, row.circuit) == (1, 3, 5)


@pytest.mark.parametrize("line", ["", "only-one-field", "1\tx*1*Distribution*a\tnot-a-breaker*1*x*0000001"])
def test_malformed_topology_lines_are_rejected(line):
    assert parse_topology_line(line) is None


def test_load_topology_counts(synth):
    rows, skipped = load_topology(synth.topology_txt)
    assert skipped == 0 and len(rows) == len(synth.premise_ids)
    pid = synth.premise_ids[5]
    assert (rows[pid].protective, rows[pid].hops) == synth.chain_of[pid]


# ---- 2.3 circuit key normalisation -----------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("CIR60", 60), ("0000060", 60), (" CIR2 ", 2), ("", None), (None, None),
                                          ("STATION", None), (7, 7)])
def test_normalize_circuit(raw, expected):
    assert normalize_circuit(raw) == expected


def test_arcos_and_topology_keys_meet():
    assert normalize_circuit("CIR60") == normalize_circuit("0000060")


@pytest.mark.parametrize("raw,expected", [("Overhead", "Overhead"), ("underground", "Underground"),
                                          ("Unknown", "Unknown"), ("", "Unknown"), (None, "Unknown")])
def test_normalize_service(raw, expected):
    assert normalize_service(raw) == expected


# ---- 2.4 storm detection ---------------------------------------------------------------------------------
def _counts(spec):
    out = {}
    for start, per_day in spec:
        for i, n in enumerate(per_day):
            out[start + timedelta(days=i)] = n
    return out


def test_detects_two_storms_and_ignores_background():
    counts = _counts([(date(2024, 9, 26), [1, 47, 36, 52, 38, 5, 15]),      # window should be 27..Oct 2
                      (date(2025, 2, 12), [25, 489, 403, 153, 41, 137]),
                      (date(2025, 1, 6), [21, 25]),                          # background burst, < 100 total
                      (date(2024, 4, 12), [17])])
    storms = detect_storms(counts)
    assert [(s.start, s.end) for s in storms] == [(date(2024, 9, 27), date(2024, 10, 2)),
                                                  (date(2025, 2, 12), date(2025, 2, 17))]
    assert storms[0].records == 47 + 36 + 52 + 38 + 5 + 15
    assert storms[0].label == "Sep 2024"


def test_wrong_number_of_storms_fails_loudly():
    with pytest.raises(StormDetectionError, match="expected 2 storms, found 1"):
        detect_storms(_counts([(date(2025, 2, 12), [200, 200])]))


def test_synthetic_fixture_storms(synth):
    storms = detect_storms(count_days(d for d, _ in load_arcos(synth.arcos_dbf)))
    assert [s.start for s in storms] == [date(2024, 9, 27), date(2025, 2, 12)]
    assert storm_index(date(2025, 2, 13), storms) == 1
    assert storm_index(date(2025, 3, 1), storms) is None
    assert storm_index(None, storms) is None
