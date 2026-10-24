from pathlib import Path

import pytest

from aep_pipeline.contracts import load_topology_contract
from aep_pipeline.errors import ContractError
from aep_pipeline.topology import build_graph, parse_topology
from tests.fixtures.builder import CHAINS, DEVICES, PREMISE_NUMBER, FixtureOptions


def _node(oid, subtype="Fuse", key="K", kind="eo_isolating_eqpt_inst"):
    return f"{kind}*{oid}*{subtype}*{key}"


def _write(tmp_path: Path, lines: list[str], newline: bytes = b"\r\n") -> Path:
    path = tmp_path / "topology.txt"
    path.write_bytes(newline.join(line.encode("ascii") for line in lines) + newline)
    return path


@pytest.fixture()
def contract(test_config_dir):
    return load_topology_contract(test_config_dir)


# ---------------------------------------------------------------- parser (5.1)
def test_valid_four_device_line_becomes_an_ordered_chain(tmp_path, contract):
    line = "\t".join(
        [
            "123456789",
            _node(1, "Distribution", "TX1", "eo_power_xfrmr_inst"),
            _node(2, "Fuse", "FU1"),
            _node(3, "Recloser (Hydraulic)", "RC1"),
            _node(4, "Circuit Breaker", "0000027", "eo_circuit"),
        ]
    )
    topology = parse_topology(_write(tmp_path, [line]), contract)
    chain = topology.chains["123456789"]
    assert [n.subtype for n in chain] == ["Distribution", "Fuse", "Recloser (Hydraulic)", "Circuit Breaker"]
    assert chain[0].object_type == "eo_power_xfrmr_inst" and chain[-1].object_type == "eo_circuit"
    assert topology.rejected_lines == 0


def test_crlf_and_lf_line_endings_both_parse_without_stray_characters(tmp_path, contract):
    line = "\t".join(["123456789", _node(1, "Distribution", "TX1", "eo_power_xfrmr_inst"), _node(4, "Circuit Breaker", "0000027", "eo_circuit")])
    for newline in (b"\r\n", b"\n"):
        topology = parse_topology(_write(tmp_path, [line], newline), contract)
        assert topology.chains["123456789"][-1].device_key == "0000027"


def test_malformed_device_entry_rejects_only_that_line_and_counts_it(tmp_path, contract):
    good = "\t".join(["111111111", _node(1, "Distribution", "TX1", "eo_power_xfrmr_inst"), _node(9, "Circuit Breaker", "01", "eo_circuit")])
    bad = "\t".join(["222222222", "eo_power_xfrmr_inst*2*Distribution", _node(9, "Circuit Breaker", "01", "eo_circuit")])
    topology = parse_topology(_write(tmp_path, [good, bad, good.replace("111111111", "333333333")]), contract)
    assert set(topology.chains) == {"111111111", "333333333"}
    assert topology.rejected_lines == 1 and topology.rejected_line_numbers == [2]


def test_bad_premise_numbers_and_empty_chains_are_rejected(tmp_path, contract):
    node = _node(1, "Distribution", "TX1", "eo_power_xfrmr_inst")
    lines = ["12345\t" + node, "abcdefghi\t" + node, "444444444", "555555555\t" + node]
    topology = parse_topology(_write(tmp_path, lines), contract)
    assert list(topology.chains) == ["555555555"]
    assert topology.rejected_lines == 3


def test_blank_lines_are_ignored_and_duplicates_counted(tmp_path, contract):
    node = _node(1, "Distribution", "TX1", "eo_power_xfrmr_inst")
    topology = parse_topology(_write(tmp_path, ["666666666\t" + node, "", "666666666\t" + node]), contract)
    assert len(topology.chains) == 1 and topology.duplicate_premises == 1 and topology.rejected_lines == 0


def test_conflicting_attributes_for_one_device_are_counted_not_hidden(tmp_path, contract):
    a = "\t".join(["111111111", _node(7, "Fuse", "KEY-A")])
    b = "\t".join(["222222222", _node(7, "Fuse", "KEY-B")])
    assert parse_topology(_write(tmp_path, [a, b]), contract).attribute_conflicts == 1


# ---------------------------------------------------------------- graph (5.2)
def test_shared_device_with_two_parents_keeps_both_edges_and_is_flagged(tmp_path, contract):
    shared = _node(50, "Fuse", "F")
    a = "\t".join(["111111111", _node(1, "Distribution", "T1", "eo_power_xfrmr_inst"), shared, _node(60, "Recloser (Hydraulic)", "R1")])
    b = "\t".join(["222222222", _node(2, "Distribution", "T2", "eo_power_xfrmr_inst"), shared, _node(61, "Recloser (Electronic)", "R2")])
    graph = build_graph(parse_topology(_write(tmp_path, [a, b]), contract))
    fuse = ("eo_isolating_eqpt_inst", "50")
    assert graph.multi_parent == {fuse}
    assert set(graph.parents[fuse]) == {("eo_isolating_eqpt_inst", "60"), ("eo_isolating_eqpt_inst", "61")}
    assert len([e for e in graph.edges if e[0] == fuse]) == 2


def test_a_cycle_raises_a_clear_contract_error(tmp_path, contract):
    x, y = _node(1, "Fuse", "X"), _node(2, "Fuse", "Y")
    lines = ["\t".join(["111111111", x, y]), "\t".join(["222222222", y, x])]
    with pytest.raises(ContractError) as error:
        build_graph(parse_topology(_write(tmp_path, lines), contract))
    assert "cycle" in str(error.value)
    assert error.value.exit_code == 1


def test_component_count_is_one_per_connected_circuit(tmp_path, contract):
    a = "\t".join(["111111111", _node(1, "Distribution", "T1", "eo_power_xfrmr_inst"), _node(9, "Circuit Breaker", "01", "eo_circuit")])
    b = "\t".join(["222222222", _node(2, "Distribution", "T2", "eo_power_xfrmr_inst"), _node(8, "Circuit Breaker", "02", "eo_circuit")])
    assert build_graph(parse_topology(_write(tmp_path, [a, b]), contract)).components == 2


def test_edges_are_unique_and_sorted(tmp_path, contract):
    node_t, node_b = _node(1, "Distribution", "T", "eo_power_xfrmr_inst"), _node(9, "Circuit Breaker", "01", "eo_circuit")
    lines = ["\t".join([f"11111111{i}", node_t, node_b]) for i in range(5)]
    graph = build_graph(parse_topology(_write(tmp_path, lines), contract))
    assert graph.edges == [(("eo_power_xfrmr_inst", "1"), ("eo_circuit", "9"))]


# ---------------------------------------------------------------- downstream counts (5.3)
def test_downstream_counts_match_the_fixture_network(fixture_raw, contract):
    graph = build_graph(parse_topology(fixture_raw.raw_dir / "matched_upstream" / "matched_upstream.txt", contract))

    def key(name):
        d = DEVICES[name]
        return (d[0], str(d[1]))

    expected = {"T1": 3, "T2": 4, "T3": 5, "F1": 7, "F2": 5, "F3": 2, "R1": 13, "R2": 1, "B1": 14, "B2": 2, "B3": 1}
    for name, count in expected.items():
        assert graph.downstream[key(name)] == count, name
    assert graph.chain_count == len(CHAINS)


def test_breaker_count_equals_the_number_of_chains_ending_there(fixture_raw, contract):
    topology = parse_topology(fixture_raw.raw_dir / "matched_upstream" / "matched_upstream.txt", contract)
    graph = build_graph(topology)
    ending = {}
    for chain in topology.chains.values():
        ending[chain[-1].key] = ending.get(chain[-1].key, 0) + 1
    breakers = {k: v for k, v in graph.downstream.items() if k[0] == "eo_circuit"}
    assert breakers == ending


def test_single_parent_devices_never_exceed_their_parent_count(fixture_raw, contract):
    graph = build_graph(parse_topology(fixture_raw.raw_dir / "matched_upstream" / "matched_upstream.txt", contract))
    for child, parents in graph.parents.items():
        if len(parents) == 1:
            assert graph.downstream[parents[0]] >= graph.downstream[child]


def test_multi_parent_device_is_flagged_in_the_fixture(fixture_raw, contract):
    graph = build_graph(parse_topology(fixture_raw.raw_dir / "matched_upstream" / "matched_upstream.txt", contract))
    f3 = (DEVICES["F3"][0], str(DEVICES["F3"][1]))
    assert graph.multi_parent == {f3}


def test_topology_for_the_fixture_options_reports_rejects(build_fixture, contract):
    info = build_fixture(FixtureOptions(malformed_topology_lines=2, topology_only_premises=1))
    topology = parse_topology(info.raw_dir / "matched_upstream" / "matched_upstream.txt", contract)
    assert topology.rejected_lines == 2
    assert len(topology.chains) == len(CHAINS) + 1
    assert PREMISE_NUMBER["P01"] in topology.chains


# ---------------------------------------------------------------- real file (5.4)
@pytest.mark.real_data
def test_real_topology_matches_the_profiled_structure(real_data_dir):
    contract = load_topology_contract()
    topology = parse_topology(real_data_dir / contract.path, contract)
    graph = build_graph(topology)
    assert topology.rejected_lines == 0 and topology.duplicate_premises == 0
    assert len(topology.chains) == 117599
    assert len(graph.nodes) == 32600
    assert graph.components == 110
    assert len(graph.multi_parent) == 60
    assert len(graph.edges) == 32551
    breakers = [k for k in graph.nodes if k[0] == "eo_circuit"]
    assert len(breakers) == 110
    assert max(graph.downstream[k] for k in breakers) == 3527
    assert sum(graph.downstream[k] for k in breakers) == 117599
