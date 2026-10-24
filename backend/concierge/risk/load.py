"""Whitelisted readers for the source files (design D10).

Only the columns listed in ALLOWED_COLUMNS can ever be decoded. Sensitive columns such as the
life-support flag (critical_c) and address fields are refused, not just ignored.
"""
from __future__ import annotations

import re
import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "customer": frozenset({"premise_nu", "circuit_id", "kind_of_se"}),
    "arcos": frozenset({"Circuit", "Report_Tim"}),
    # Fuse/Transformer/Switch/Recloser layers: used only to translate ARCOS circuit labels to circuit ids.
    "gis_device": frozenset({"circuit_id", "aep_circui"}),
}

PROTECTIVE_TYPES = frozenset(
    {"Fuse", "Recloser (Hydraulic)", "Recloser (Electronic)", "Sectionalizer"}
)
PROTECTIVE_CLASS = "eo_isolating_eqpt_inst"
BREAKER_CLASS = "eo_circuit"

_DIGITS = re.compile(r"\d+")


class ForbiddenColumnError(ValueError):
    """Raised when code asks a loader for a column outside the whitelist."""


def read_dbf(path: str | Path, columns: set[str] | frozenset[str], kind: str) -> Iterator[dict[str, str]]:
    """Yield one dict per live record with only `columns` decoded.

    `kind` selects the whitelist ("customer" or "arcos"). Any other column raises before the file is opened.
    """
    allowed = ALLOWED_COLUMNS[kind]
    bad = set(columns) - allowed
    if bad:
        raise ForbiddenColumnError(f"columns not allowed for {kind}: {sorted(bad)}")

    with open(path, "rb") as f:
        header = f.read(32)
        n_records = struct.unpack("<I", header[4:8])[0]
        header_len, record_len = struct.unpack("<HH", header[8:12])
        fields: list[tuple[str, int]] = []
        while True:
            desc = f.read(32)
            if not desc or desc[0] == 0x0D:
                break
            fields.append((desc[:11].split(b"\0")[0].decode("ascii", "ignore"), desc[16]))
        missing = set(columns) - {name for name, _ in fields}
        if missing:
            raise KeyError(f"{path}: missing columns {sorted(missing)}")

        # Precompute byte slices for wanted columns only.
        slices: list[tuple[str, int, int]] = []
        pos = 1  # byte 0 is the deletion flag
        for name, length in fields:
            if name in columns:
                slices.append((name, pos, pos + length))
            pos += length

        f.seek(header_len)
        for _ in range(n_records):
            rec = f.read(record_len)
            if len(rec) < record_len:
                break
            if rec[0:1] == b"*":
                continue
            yield {name: rec[a:b].decode("latin-1").strip() for name, a, b in slices}


def normalize_circuit(value: str | int | None) -> int | None:
    """Extract the integer from a circuit_id such as '0000060' (-> 60). Values without digits become None.

    Only use this for numeric circuit ids (customer and topology files, and the circuit_id column of the GIS
    layers). Do NOT use it on ARCOS labels: 'CIR2' is circuit 16, not 2. See load_crosswalk().
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    m = _DIGITS.search(str(value))
    return int(m.group()) if m else None


def normalize_service(kind: str | None) -> str:
    k = (kind or "").strip().lower()
    if k == "overhead":
        return "Overhead"
    if k == "underground":
        return "Underground"
    return "Unknown"


def load_customers(path: str | Path) -> dict[str, tuple[int | None, str]]:
    """premise_nu -> (customer-file circuit, service type). Reads only the three whitelisted columns."""
    out: dict[str, tuple[int | None, str]] = {}
    for r in read_dbf(path, ALLOWED_COLUMNS["customer"], "customer"):
        pid = r["premise_nu"]
        if pid:
            out[pid] = (normalize_circuit(r["circuit_id"]), normalize_service(r["kind_of_se"]))
    return out


def load_arcos(path: str | Path) -> list[tuple[date | None, str | None]]:
    """(report date, raw circuit label) per damage record. Labels like 'CIR60' are NOT circuit ids.

    Use load_crosswalk() to translate a label into the numeric circuit id used by customers and topology.
    Blank labels become None; unparseable dates become None.
    """
    out: list[tuple[date | None, str | None]] = []
    for r in read_dbf(path, ALLOWED_COLUMNS["arcos"], "arcos"):
        try:
            d = datetime.strptime(r["Report_Tim"], "%Y%m%d").date()
        except ValueError:
            d = None
        out.append((d, r["Circuit"] or None))
    return out


class CrosswalkError(ValueError):
    """The device layers disagree about which circuit a label belongs to."""


def load_crosswalk(device_dbfs: Iterable[str | Path]) -> dict[str, int]:
    """Map ARCOS-style circuit labels (e.g. 'CIR2') to numeric circuit ids (e.g. 16).

    The label is NOT the circuit number in the device layers: CIR2 is circuit 16, CIR6 is circuit 5, and so on.
    Every device layer (fuse, transformer, switch, recloser) carries both fields; across all four they agree 1:1 on
    the real data. Any disagreement raises, so a bad mapping can never be used silently.
    """
    label_to_ids: dict[str, set[int]] = {}
    id_to_labels: dict[int, set[str]] = {}
    for path in device_dbfs:
        for r in read_dbf(path, ALLOWED_COLUMNS["gis_device"], "gis_device"):
            cid, label = normalize_circuit(r["circuit_id"]), r["aep_circui"]
            if cid is None or not label:
                continue
            label_to_ids.setdefault(label, set()).add(cid)
            id_to_labels.setdefault(cid, set()).add(label)
    bad = {k: v for k, v in label_to_ids.items() if len(v) > 1}
    bad_rev = {k: v for k, v in id_to_labels.items() if len(v) > 1}
    if bad or bad_rev:
        raise CrosswalkError(f"circuit label crosswalk is not 1:1: labels={dict(list(bad.items())[:5])} "
                             f"ids={dict(list(bad_rev.items())[:5])}")
    return {label: next(iter(ids)) for label, ids in label_to_ids.items()}


@dataclass(frozen=True)
class TopologyRow:
    premise_id: str
    circuit: int
    protective: int   # P: fuses, reclosers, sectionalizers on the way to the breaker
    hops: int         # H: devices in the chain minus one


def parse_topology_line(line: str) -> TopologyRow | None:
    """Parse `premise<TAB>class*id*type*name<TAB>...<TAB>eo_circuit*id*Circuit Breaker*<circuit>`."""
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < 3:
        return None
    premise_id, chain = parts[0].strip(), parts[1:]
    breaker = chain[-1].split("*")
    if not premise_id or breaker[0] != BREAKER_CLASS:
        return None
    circuit = normalize_circuit(breaker[-1])
    if circuit is None:
        return None
    protective = 0
    for dev in chain:
        f = dev.split("*")
        if len(f) >= 3 and f[0] == PROTECTIVE_CLASS and f[2] in PROTECTIVE_TYPES:
            protective += 1
    return TopologyRow(premise_id, circuit, protective, len(chain) - 1)


def load_topology(path: str | Path) -> tuple[dict[str, TopologyRow], int]:
    """Return (premise -> row, number of malformed lines skipped)."""
    rows: dict[str, TopologyRow] = {}
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            row = parse_topology_line(line)
            if row is None:
                skipped += 1
            else:
                rows[row.premise_id] = row
    return rows, skipped
