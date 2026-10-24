"""Synthetic data that mimics the real file schemas (DBF, topology TXT) for fast, deterministic tests."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# (circuit id, premises, storm-1 damage, storm-2 damage)
MAIN_S1 = [0, 0, 0, 0, 0, 1, 1, 2, 3, 5, 8, 12]
MAIN_S2 = [0, 0, 0, 0, 0, 4, 4, 8, 12, 20, 32, 48]
MAIN_IDS = list(range(11, 23))        # 12 circuits x 100 premises
SMALL_ID, SMALL_N = 30, 30            # too small -> circuit_too_small
QUIET_SMALL_ID, QUIET_SMALL_N = 31, 300   # zero history, < 500 -> no_history_small_circuit
QUIET_BIG_ID, QUIET_BIG_N = 32, 600   # zero history, >= 500 -> Low confidence
FILLER_ID = 99                        # damage on a circuit that is not in the topology


def write_dbf(path: Path, fields: list[tuple[str, str, int]], rows: list[dict]) -> None:
    n_fields = len(fields)
    header_len = 32 + 32 * n_fields + 1
    record_len = 1 + sum(length for _, _, length in fields)
    out = bytearray()
    out += struct.pack("<BBBBIHH20x", 3, 126, 1, 1, len(rows), header_len, record_len)
    for name, ftype, length in fields:
        out += name.encode().ljust(11, b"\0") + ftype.encode() + b"\0" * 4 + bytes([length, 0]) + b"\0" * 14
    out += b"\r"
    for r in rows:
        out += b" "
        for name, _, length in fields:
            out += str(r.get(name, "")).encode("latin-1").ljust(length)[:length]
    out += b"\x1a"
    path.write_bytes(bytes(out))


@dataclass
class Synth:
    customer_dbf: Path
    arcos_dbf: Path
    topology_txt: Path
    premise_ids: list[str]
    life_support_ids: list[str]
    no_chain_ids: list[str]
    unknown_service_ids: list[str]
    circuit_of: dict[str, int]
    chain_of: dict[str, tuple[int, int]]   # premise -> (protective, hops)
    service_of: dict[str, str]


def _topology_line(pid: str, circuit: int, protective: int, hops: int) -> str:
    extra = hops - 1 - protective
    assert extra >= 0
    devices = ["eo_power_xfrmr_inst*1*Distribution*X1"]
    devices += [f"eo_isolating_eqpt_inst*{i}*Fuse*F{i}" for i in range(protective)]
    devices += [f"eo_power_xfrmr_inst*{i}*Step*S{i}" for i in range(extra)]
    devices.append(f"eo_circuit*9*Circuit Breaker*{circuit:07d}")
    return "\t".join([pid, *devices])


def make_synth(tmp: Path, *, reverse_s2: bool = False) -> Synth:
    tmp.mkdir(parents=True, exist_ok=True)
    s2 = list(reversed(MAIN_S2)) if reverse_s2 else MAIN_S2

    sizes = {c: 100 for c in MAIN_IDS}
    sizes.update({SMALL_ID: SMALL_N, QUIET_SMALL_ID: QUIET_SMALL_N, QUIET_BIG_ID: QUIET_BIG_N})

    customers, topo_lines = [], []
    premise_ids, circuit_of, chain_of, service_of = [], {}, {}, {}
    life_support, unknown_service = [], []
    k = 0
    for c, n in sizes.items():
        for i in range(n):
            k += 1
            pid = f"{900000000 + k}"
            protective, hops = i % 4, (i % 4) + 1 + (i % 3)
            service = "Underground" if i % 3 == 0 else "Overhead"
            if i % 17 == 0:
                service = "Unknown"
                unknown_service.append(pid)
            if k % 29 == 0:
                life_support.append(pid)
            customers.append({"premise_nu": pid, "circuit_id": f"{c:07d}", "kind_of_se": service,
                              "critical_c": "LS Life Support" if k % 29 == 0 else "",
                              "address": f"{k} MAIN ST", "road_name": "MAIN ST", "city": "ROANOKE"})
            topo_lines.append(_topology_line(pid, c, protective, hops))
            premise_ids.append(pid)
            circuit_of[pid], chain_of[pid], service_of[pid] = c, (protective, hops), service

    # Premises that exist only in the customer file (no upstream chain)
    no_chain = []
    for j in range(5):
        pid = f"{800000000 + j}"
        customers.append({"premise_nu": pid, "circuit_id": f"{MAIN_IDS[0]:07d}", "kind_of_se": "Overhead",
                          "critical_c": "", "address": "1 NOWHERE RD", "road_name": "NOWHERE RD", "city": "ROANOKE"})
        no_chain.append(pid)
    # Premises only in the topology (no customer row -> service type Unknown)
    for j in range(3):
        pid = f"{700000000 + j}"
        topo_lines.append(_topology_line(pid, MAIN_IDS[1], 1, 3))
        premise_ids.append(pid)
        circuit_of[pid], chain_of[pid], service_of[pid] = MAIN_IDS[1], (1, 3), "Unknown"
        unknown_service.append(pid)

    cust_fields = [("premise_nu", "C", 12), ("circuit_id", "C", 10), ("kind_of_se", "C", 12),
                   ("critical_c", "C", 20), ("address", "C", 30), ("road_name", "C", 20), ("city", "C", 15)]
    write_dbf(tmp / "Customer_Clip.dbf", cust_fields, customers)
    (tmp / "matched_upstream.txt").write_text("\n".join(topo_lines) + "\n")

    # ARCOS: two storms (needs >=100 records each, >=10 per day) plus background noise
    storm_days = [[date(2024, 9, 27), date(2024, 9, 28)], [date(2025, 2, 12), date(2025, 2, 13)]]
    rows = []

    def add(circuit_label: str, days: list[date], count: int, offset: int = 0):
        for j in range(count):
            rows.append({"Circuit": circuit_label, "Report_Tim": days[(j + offset) % len(days)].strftime("%Y%m%d")})

    for idx, c in enumerate(MAIN_IDS):
        add(f"CIR{c}", storm_days[0], MAIN_S1[idx])
        add(f"CIR{c}", storm_days[1], s2[idx])
    add(f"CIR{FILLER_ID}", storm_days[0], 80)
    add(f"CIR{FILLER_ID}", storm_days[1], 60)
    add("", storm_days[0], 3)                         # no circuit key, inside a storm
    for d in (date(2024, 6, 1), date(2024, 6, 2), date(2025, 5, 31)):
        add(f"CIR{MAIN_IDS[2]}", [d], 2)              # background noise outside storm windows
    write_dbf(tmp / "arcos.dbf", [("Circuit", "C", 20), ("Report_Tim", "D", 8)], rows)

    return Synth(tmp / "Customer_Clip.dbf", tmp / "arcos.dbf", tmp / "matched_upstream.txt", premise_ids,
                 life_support, no_chain, unknown_service, circuit_of, chain_of, service_of)
