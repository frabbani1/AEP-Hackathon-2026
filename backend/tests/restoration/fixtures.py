"""Synthetic inputs for the restoration tests: a labor sheet, a one-circuit tree, and premise rows.

The labor numbers are round on purpose so expected values can be worked out by hand:
every unit takes 1.0 install + 0.5 removal hours, the adders and multiplier match the real sheet.
"""
from __future__ import annotations

# ---- labor sheet (same shape as artifacts/curated/labor_standards.json) --------------------------------------------
_UNITS = [
    ("crossarm", None, None), ("insulator", None, None), ("pin", None, None), ("dead_end", None, None),
    ("secondary_conductor_attachment", None, None), ("service", None, None),
    ("pole", "Small", None), ("pole", "Medium", None), ("pole", "Large", None),
    ("fuse_cutout", None, None), ("recloser", None, None), ("switch", None, None),
    ("equipment_lead", None, None), ("bracket", None, None),
    ("transformer", None, 50), ("transformer", None, 167),
]


def make_labor(multiplier: float = 4.5, drop: str | None = None) -> dict:
    units = [{"category": c, "size_group": g, "height_range": None, "size_kva": k, "cu_count": None,
              "install_hours": 1.0, "remove_hours": 0.5}
             for c, g, k in _UNITS if c != drop]
    return {"source": "synthetic", "indirect_labor_multiplier": multiplier, "units": units,
            "adders": [{"key": "access_constraint_adder", "description": "x", "hours": 0.5, "unit": "per job"},
                       {"key": "remote_location_adder", "description": "x", "hours": 1.312, "unit": "per job"}],
            "cu_groups": []}


LABOR = make_labor()

# Hours of one job with the synthetic sheet, normal access (direct hours x 4.5):
#   pole 1.5 -> 6.75   fuse_bank 1.5 -> 6.75   transformer 1.5 -> 6.75   primary_line 4 x 1.5 = 6 -> 27.0
#   secondary_line 2 x 1.5 = 3 -> 13.5   recloser 1.5 -> 6.75
POLE_HOURS = 6.75
PRIMARY_HOURS = 27.0

# ---- one circuit, 8 nodes, depths 0 to 3 ---------------------------------------------------------------------------
#
#   0 breaker      (served 100, feeder)
#   |-- 1 fuse     (served 60,  feeder)
#   |   |-- 3 fuse (served 30,  lateral)
#   |   |   `-- 6 transformer (served 12, service)
#   |   `-- 4 transformer     (served 20, service)
#   `-- 2 fuse     (served 40,  feeder)
#       `-- 5 fuse (served 25,  lateral)
#           `-- 7 transformer (served 15, service)
TREE_DICT = {
    "version": "tree-v1",
    "circuits": {
        "0": {
            "parent": [-1, 0, 0, 1, 1, 2, 3, 5],
            "depth": [0, 1, 1, 2, 2, 2, 3, 3],
            "served": [100, 60, 40, 30, 20, 25, 12, 15],
            "level": ["feeder", "feeder", "feeder", "lateral", "service", "lateral", "service", "service"],
        }
    },
}

# ---- premise rows (fields as written by the risk build and the line-context step) --------------------------------------
BASE = {"st": "ok", "why": None, "R": 4.0, "Re": 4.0, "svc": "Overhead", "P": 3, "H": 4, "n": 12, "c": "Medium",
        "cn": 100, "pc": 0}
ROWS = {
    # token -> row. Customer under transformer node 6 (path 6, 3, 1, 0)
    "p_00000000000000a1": {**BASE, "pn": 6, "n0": 12, "nl": 30, "nf": 60},
    # under transformer node 4 (path 4, 1, 0)
    "p_00000000000000a2": {**BASE, "pn": 4, "n0": 20, "nl": 60, "nf": 60},
    # under transformer node 7 (path 7, 5, 2, 0)
    "p_00000000000000a3": {**BASE, "pn": 7, "n0": 15, "nl": 25, "nf": 40},
    # attached to the lateral node 3 itself (smaller transformers below it were merged)
    "p_00000000000000a4": {**BASE, "pn": 3, "n0": 30, "nl": 30, "nf": 60},
}
TOKENS = list(ROWS)

SEVERITY = {"preset": "typical", "multiplier": 1.0, "extrapolated": False}


# ---- device_dim / premise_dim rows that build into TREE_DICT (for the tree builder tests) -------------------------------
def _dev(key, cls, typ, parent, circuit, depth, served, prot=False):
    return {"device_key": key, "device_class": cls, "device_type": typ, "parent_key": parent, "circuit": circuit,
            "depth": depth, "is_protective": prot, "n_premises_served": served}


def make_devices() -> list[dict]:
    """Circuit 6060 builds into TREE_DICT plus 4 small devices that must be merged away.
    Circuit 7071 is a tiny two-node circuit (breaker key sorts last, so it gets circuit index 1).
    Circuit 7070 serves 4 premises and is dropped."""
    f, t = ("isolating_device", "Fuse"), ("transformer", "Distribution")
    return [
        _dev("d_00", "breaker", "Circuit Breaker", None, 6060, 0, 100),
        _dev("d_01", *f, "d_00", 6060, 1, 60, True), _dev("d_02", *f, "d_00", 6060, 1, 40, True),
        _dev("d_03", *f, "d_01", 6060, 2, 30, True), _dev("d_04", *t, "d_01", 6060, 2, 20),
        _dev("d_05", *f, "d_02", 6060, 2, 25, True),
        _dev("d_06", *t, "d_03", 6060, 3, 12), _dev("d_07", *t, "d_05", 6060, 3, 15),
        # small devices, merged into their parent
        _dev("d_08", *t, "d_03", 6060, 3, 6), _dev("d_09", *t, "d_03", 6060, 3, 4),
        _dev("d_0a", *f, "d_05", 6060, 3, 8, True), _dev("d_0b", *t, "d_0a", 6060, 4, 8),
        # second circuit
        _dev("d_ff", "breaker", "Circuit Breaker", None, 7071, 0, 20), _dev("d_fe", *t, "d_ff", 7071, 1, 20),
        # third circuit: too small, dropped
        _dev("d_a0", "breaker", "Circuit Breaker", None, 7070, 0, 4), _dev("d_a1", *t, "d_a0", 7070, 1, 4),
    ]


def make_premises() -> list[dict]:
    mapping = {"p_01": "d_06", "p_02": "d_04", "p_03": "d_07", "p_04": "d_08", "p_05": "d_0b", "p_06": "d_09",
               "p_07": "d_fe", "p_08": "d_a1", "p_09": None}
    return [{"premise_token": p, "transformer_key": k} for p, k in mapping.items()]
