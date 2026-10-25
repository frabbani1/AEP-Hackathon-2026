"""Tasks 2.1 to 2.4: node-level rule, tree builder, merge of small groups, artifact privacy, runtime loader."""
import copy
import json
import re
from pathlib import Path

import pytest

from concierge.curate import restoration_tree as rt
from concierge.restoration import config
from concierge.restoration.tree import CircuitTree, RestorationTree, TreeError, levels_tree

from .fixtures import TREE_DICT, make_devices, make_premises


# ---- 2.1 node-level rule ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cls, typ, depth, level", [
    ("breaker", "Circuit Breaker", 0, "feeder"),
    ("isolating_device", "Fuse", 1, "feeder"),
    ("isolating_device", "Recloser (Hydraulic)", 1, "feeder"),
    ("isolating_device", "Fuse", 2, "lateral"),
    ("isolating_device", "Sectionalizer", 2, "lateral"),
    ("isolating_device", "Recloser (Electronic)", 7, "lateral"),
    ("transformer", "Step", 1, "feeder"),
    ("transformer", "Step", 5, "lateral"),
    ("transformer", "Distribution", 1, "service"),
    ("transformer", "Distribution", 9, "service"),
])
def test_node_level_rule(cls, typ, depth, level):
    assert rt.node_level(cls, typ, depth) == level


def test_builder_matches_hand_built_tree():
    doc, positions, stats = rt.build_tree(make_devices(), make_premises())
    assert doc["circuits"]["0"] == TREE_DICT["circuits"]["0"]
    assert doc["version"] == "tree-v1"
    assert stats["circuits"] == 2 and stats["circuits_suppressed"] == 1


# ---- 2.2 merging small groups -----------------------------------------------------------------------------------------
def test_no_node_below_ten_survives_and_totals_are_preserved():
    devices, premises = make_devices(), make_premises()
    doc, positions, stats = rt.build_tree(devices, premises)
    rt.check_tree_doc(doc)
    assert all(s >= 10 for c in doc["circuits"].values() for s in c["served"])
    assert sum(len(c["parent"]) for c in doc["circuits"].values()) == 8 + 2      # surviving nodes on both circuits
    assert stats["merged_devices"] == 4          # the four small devices on the surviving circuit
    # every premise on a surviving circuit is positioned; none is lost or duplicated
    on_surviving = [p for p in premises if p["transformer_key"] not in (None, "d_a1")]
    assert set(positions) == {p["premise_token"] for p in on_surviving}
    # surviving nodes keep their own served count: merging moves premises, it does not change the nodes above
    assert doc["circuits"]["0"]["served"][3] == 30


def test_premises_under_merged_devices_attach_to_nearest_surviving_ancestor():
    _, positions, _ = rt.build_tree(make_devices(), make_premises())
    assert positions["p_01"] == (0, 6)           # own transformer survives
    assert positions["p_02"] == (0, 4)
    assert positions["p_04"] == (0, 3)           # transformer d_08 (6 homes) merged into fuse d_03
    assert positions["p_06"] == (0, 3)
    assert positions["p_05"] == (0, 5)           # d_0b under d_0a, both merged into fuse d_05
    assert positions["p_07"] == (1, 1)
    assert "p_08" not in positions               # circuit serving 4 premises is dropped
    assert "p_09" not in positions               # no transformer


def test_child_with_merged_parent_is_reattached_to_nearest_surviving_ancestor():
    """A device on two feeds can serve 10+ premises while its resolved parent serves fewer."""
    devices = make_devices()
    # d_0a serves 8, but give its child 12 (alternate feed pushes more premises through the child)
    for d in devices:
        if d["device_key"] == "d_0b":
            d["n_premises_served"] = 12
    doc, positions, _ = rt.build_tree(devices, make_premises())
    c = doc["circuits"]["0"]
    assert len(c["parent"]) == 9
    # the new node (d_0b) hangs under d_05 (node 5), and its depth is the depth in the merged tree
    assert c["served"][-1] == 12 and c["parent"][-1] == 5 and c["depth"][-1] == 3
    rt.check_tree_doc(doc)
    assert positions["p_05"] == (0, 8)


def test_circuit_with_small_breaker_is_dropped():
    devices = [d for d in make_devices() if d["circuit"] == 7070]
    doc, positions, stats = rt.build_tree(devices, make_premises())
    assert doc["circuits"] == {} and positions == {} and stats["circuits_suppressed"] == 1


def test_builder_rejects_bad_device_trees():
    devices = make_devices()
    devices[1]["parent_key"] = "d_ff"                     # parent on another circuit
    with pytest.raises(ValueError):
        rt.build_tree(devices, [])
    devices = [d for d in make_devices() if d["device_key"] != "d_00"]      # circuit without a breaker
    with pytest.raises(ValueError):
        rt.build_tree(devices, [])


# ---- 2.4 artifact contents -----------------------------------------------------------------------------------------
def test_tree_document_has_no_identifiers():
    devices = make_devices()
    doc, _, _ = rt.build_tree(devices, make_premises())
    text = json.dumps(doc)
    assert not re.search(r"d_[0-9a-f]{2,}", text)                      # no device keys
    assert not re.search(r"CIR\d|\b(6060|7071|7070)\b", text, re.I)    # no circuit labels or numbers
    assert set(doc["circuits"]) == {"0", "1"}                          # opaque indices
    assert all(set(c) == {"parent", "depth", "served", "level"} for c in doc["circuits"].values())


def test_circuit_index_does_not_follow_circuit_number():
    """The order comes from the breaker keys, so reordering circuit numbers cannot change an index."""
    devices = make_devices()
    for d in devices:
        d["circuit"] = {6060: 7071, 7071: 6060}.get(d["circuit"], d["circuit"])
    doc, _, _ = rt.build_tree(devices, make_premises())
    assert len(doc["circuits"]["0"]["parent"]) == 8


@pytest.mark.parametrize("mutate", [
    lambda d: d["circuits"]["0"]["served"].__setitem__(3, 9),
    lambda d: d["circuits"]["0"]["parent"].__setitem__(2, 5),
    lambda d: d["circuits"]["0"]["level"].__setitem__(1, "trunk"),
    lambda d: d["circuits"]["0"]["depth"].__setitem__(3, 1),
    lambda d: d["circuits"]["0"].__setitem__("device", ["d_00"]),
    lambda d: d.__setitem__("version", "tree-v0"),
    lambda d: d["circuits"].__setitem__("six", copy.deepcopy(d["circuits"]["0"])),
])
def test_check_tree_doc_rejects_rule_violations(mutate):
    doc = copy.deepcopy(TREE_DICT)
    rt.check_tree_doc(doc)
    mutate(doc)
    with pytest.raises(ValueError):
        rt.check_tree_doc(doc)


# ---- runtime loader --------------------------------------------------------------------------------------------------
def test_runtime_tree_loads_and_finds_paths():
    tree = RestorationTree.from_dict(TREE_DICT)
    circuit, node = tree.locate({"pc": 0, "pn": 6})
    assert circuit.path(node) == (6, 3, 1, 0)
    assert circuit.path(0) == (0,)
    assert tree.locate({"pc": 1, "pn": 0}) is None and tree.locate({"pc": 0, "pn": 8}) is None
    assert tree.locate({"pc": 0}) is None and tree.locate({"pc": True, "pn": 0}) is None
    assert circuit.nodes_at(0) == (0, 1, 2) and circuit.nodes_at(1) == (3, 5) and circuit.nodes_at(2) == (4, 6, 7)


def test_level_without_nodes_uses_the_next_level_up():
    t = CircuitTree([-1, 0], [0, 1], [10, 10], [0, 0])          # only feeder nodes
    assert t.nodes_at(2) == (0, 1) and t.nodes_at(1) == (0, 1)


def test_runtime_loader_refuses_bad_documents():
    for mutate in (lambda d: d["circuits"]["0"]["served"].__setitem__(0, 9),
                   lambda d: d["circuits"]["0"]["parent"].__setitem__(1, 4),
                   lambda d: d.__setitem__("version", "tree-v9"),
                   lambda d: d["circuits"]["0"]["level"].__setitem__(0, "x")):
        doc = copy.deepcopy(TREE_DICT)
        mutate(doc)
        with pytest.raises(TreeError):
            RestorationTree.from_dict(doc)


def test_levels_tree_rebuilds_the_three_level_path():
    row = {"cn": 100, "nf": 60, "nl": 30, "n0": 12}
    circuit, node = levels_tree(row)
    assert circuit.path(node) == (3, 2, 1, 0)
    assert [circuit.served[v] for v in circuit.path(node)] == [12, 30, 60, 100]
    assert [circuit.level[v] for v in circuit.path(node)] == [2, 1, 0, 0]
    assert len(circuit) == 5 and circuit.served[4] == 40 and circuit.parent[4] == 0     # rest of the line
    # no protective device: the line collapses, no sibling
    circuit, node = levels_tree({"cn": 50, "nf": 50, "nl": 50, "n0": 5})
    assert len(circuit) == 4 and circuit.path(node) == (3, 2, 1, 0)


def test_tree_fields_survive_the_dynamodb_loader_on_a_synthetic_row():
    import importlib.util
    spec = importlib.util.spec_from_file_location("load_dynamodb", Path(__file__).resolve().parents[3] / "scripts/load_dynamodb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = {"t": "p_0000000000000001", "st": "ok", "why": None, "R": 1.5, "cn": 100, "n0": 12, "nl": 30, "nf": 60,
           "pc": 3, "pn": 17, "ap": 1}
    item = mod.to_item(row)
    assert item["pc"] == 3 and item["pn"] == 17 and item["ap"] == 1 and "t" not in item and "why" not in item


# ---- 2.3 / 2.4 on the real artifacts (skipped when they have not been built) -----------------------------------------
ART = Path(__file__).resolve().parents[3] / "artifacts" / "risk"
needs_artifacts = pytest.mark.skipif(not (ART / "restoration_tree.json").exists(),
                                     reason="run scripts/curate.py and scripts/build_risk_artifacts.py first")


@needs_artifacts
@pytest.mark.realdata
def test_real_tree_artifact_is_clean_and_every_scored_premise_has_a_valid_position():
    text = (ART / "restoration_tree.json").read_text()
    doc = json.loads(text)
    rt.check_tree_doc(doc)
    assert not re.search(r"d_[0-9a-f]{12}|CIR\d|\b0000\d{3}\b|p_[0-9a-f]{16}", text)
    tree = RestorationTree.from_dict(doc)
    n = ok = 0
    for line in open(ART / "premise_risk.jsonl", encoding="utf-8"):
        row = json.loads(line)
        n += 1
        if row["st"] != "ok":
            assert "pn" not in row and "pc" not in row and "cn" not in row
            continue
        ok += 1
        assert tree.locate(row) is not None, "scored premise without a valid tree position"
        circuit, node = tree.locate(row)
        assert circuit.served[0] == row["cn"]                 # the circuit root serves every home on the line
        assert circuit.served[node] >= config.MIN_SERVED
    assert ok > 100_000 and n > ok


@needs_artifacts
@pytest.mark.realdata
def test_tree_fields_survive_the_dynamodb_loader():
    import importlib.util
    spec = importlib.util.spec_from_file_location("load_dynamodb", Path(__file__).resolve().parents[3] / "scripts/load_dynamodb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = next(json.loads(l) for l in open(ART / "premise_risk.jsonl") if '"pn"' in l)
    item = mod.to_item(row)
    assert item["pn"] == row["pn"] and item["pc"] == row["pc"] and isinstance(item["pn"], int)
