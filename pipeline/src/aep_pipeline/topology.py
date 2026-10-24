"""Premise-to-breaker topology: streaming parser, edge list and downstream counts.

Each line of the topology file is a premise number followed by tab-separated upstream
devices written ``type*objectid*subtype*devicekey``, nearest to the premise first and
ending at the circuit breaker. Lines that cannot be read are rejected and counted. They
are never repaired by guessing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from .contracts import TopologyContract
from .errors import ContractError

NodeKey = tuple[str, str]  # (object_type, object_id)
MAX_REJECT_LINES_KEPT = 20


@dataclass(frozen=True)
class Node:
    object_type: str
    object_id: str
    subtype: str
    device_key: str

    @property
    def key(self) -> NodeKey:
        return (self.object_type, self.object_id)


@dataclass
class Topology:
    """Parsed premise chains. ``chains`` maps a raw premise number to its devices, nearest first."""

    chains: dict[str, tuple[Node, ...]] = field(default_factory=dict)
    rejected_lines: int = 0
    rejected_line_numbers: list[int] = field(default_factory=list)
    duplicate_premises: int = 0
    attribute_conflicts: int = 0


@dataclass
class TopologyGraph:
    nodes: dict[NodeKey, Node]
    edges: list[tuple[NodeKey, NodeKey]]  # (child, parent), unique and sorted
    parents: dict[NodeKey, tuple[NodeKey, ...]]
    multi_parent: frozenset[NodeKey]
    downstream: dict[NodeKey, int]
    components: int
    chain_count: int
    circuit_of: dict[NodeKey, str] = field(default_factory=dict)  # device -> breaker key (numeric circuit id)
    circuit_conflicts: int = 0  # devices seen under more than one breaker


def parse_topology(path: Path, contract: TopologyContract) -> Topology:
    """Stream the topology file and return the accepted chains and rejection counts."""
    premise_pattern = re.compile(contract.premise_pattern)
    topology = Topology()
    seen_nodes: dict[NodeKey, Node] = {}

    def reject(line_number: int) -> None:
        topology.rejected_lines += 1
        if len(topology.rejected_line_numbers) < MAX_REJECT_LINES_KEPT:
            topology.rejected_line_numbers.append(line_number)

    with Path(path).open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                line = raw.rstrip(b"\r\n").decode("ascii")
            except UnicodeDecodeError:
                reject(line_number)
                continue
            if not line.strip():
                continue
            parts = line.split(contract.field_delimiter)
            premise, entries = parts[0].strip(), parts[1:]
            if not premise_pattern.match(premise) or not entries:
                reject(line_number)
                continue
            nodes: list[Node] = []
            valid = True
            for entry in entries:
                pieces = entry.split(contract.node_delimiter)
                if len(pieces) != len(contract.node_parts) or not pieces[0].strip() or not pieces[1].strip():
                    valid = False
                    break
                nodes.append(Node(*(piece.strip() for piece in pieces)))
            if not valid:
                reject(line_number)
                continue
            if premise in topology.chains:
                topology.duplicate_premises += 1
                continue
            for node in nodes:
                previous = seen_nodes.setdefault(node.key, node)
                if previous != node:
                    topology.attribute_conflicts += 1
            topology.chains[premise] = tuple(nodes)
    return topology


def build_graph(topology: Topology) -> TopologyGraph:
    """Build child-to-parent edges, multi-parent flags, downstream premise counts and checks."""
    nodes: dict[NodeKey, Node] = {}
    edges: set[tuple[NodeKey, NodeKey]] = set()
    downstream: Counter[NodeKey] = Counter()
    circuit_of: dict[NodeKey, str] = {}
    circuit_conflicts = 0
    for chain in topology.chains.values():
        breaker_key = chain[-1].device_key
        for node in chain:
            nodes.setdefault(node.key, node)
            known = circuit_of.setdefault(node.key, breaker_key)
            if known != breaker_key:
                circuit_conflicts += 1
        for child, parent in zip(chain, chain[1:]):
            edges.add((child.key, parent.key))
        for key in {node.key for node in chain}:
            downstream[key] += 1

    parent_sets: dict[NodeKey, set[NodeKey]] = {}
    for child, parent in edges:
        parent_sets.setdefault(child, set()).add(parent)

    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from(edges)
    if not nx.is_directed_acyclic_graph(graph):
        cycle = nx.find_cycle(graph)
        raise ContractError(
            f"The topology contains a cycle involving {len(cycle)} device(s); "
            "upstream chains must not loop back on themselves."
        )

    return TopologyGraph(
        nodes=nodes,
        edges=sorted(edges),
        parents={child: tuple(sorted(ps)) for child, ps in parent_sets.items()},
        multi_parent=frozenset(child for child, ps in parent_sets.items() if len(ps) > 1),
        downstream=dict(downstream),
        components=nx.number_weakly_connected_components(graph),
        chain_count=len(topology.chains),
        circuit_of=circuit_of,
        circuit_conflicts=circuit_conflicts,
    )
