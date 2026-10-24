"""Runtime view of the restoration tree artifact (spec-03 design D4). Pure Python, no third-party imports.

The artifact (`serving/risk/restoration_tree.json`) holds arrays only: for each circuit, the parent index, depth,
customers served and level of every node. It has no device keys, circuit labels or coordinates. A premise row carries
`pc` (opaque circuit index) and `pn` (its node index) to find its place. Both are only used inside the engine.

Node 0 is the circuit breaker (the root). A parent always has a lower index than its children.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from . import config

LEVEL_INDEX = {name: i for i, name in enumerate(config.LEVELS)}   # feeder 0, lateral 1, service 2


class TreeError(ValueError):
    """The tree artifact is malformed. Raised when it is loaded, never while answering a request."""


class CircuitTree:
    """One circuit: parallel arrays indexed by node number."""

    __slots__ = ("parent", "depth", "served", "level", "_paths", "_by_level")

    def __init__(self, parent: Sequence[int], depth: Sequence[int], served: Sequence[int], level: Sequence[int]):
        n = len(parent)
        if n == 0 or not (len(depth) == len(served) == len(level) == n):
            raise TreeError("tree arrays must be non-empty and the same length")
        if parent[0] != -1 or any(not (0 <= parent[i] < i) for i in range(1, n)):
            raise TreeError("node 0 must be the root and every parent must come before its children")
        if any(not (0 <= lv < len(config.LEVELS)) for lv in level):
            raise TreeError("unknown level")
        if any(s < 1 for s in served):
            raise TreeError("every node must serve at least one premise")
        self.parent = tuple(parent)
        self.depth = tuple(depth)
        self.served = tuple(served)
        self.level = tuple(level)
        self._paths: dict[int, tuple[int, ...]] = {}
        by_level: list[list[int]] = [[] for _ in config.LEVELS]
        for v, lv in enumerate(self.level):
            by_level[lv].append(v)
        self._by_level = by_level

    def __len__(self) -> int:
        return len(self.parent)

    def path(self, node: int) -> tuple[int, ...]:
        """Nodes from `node` up to the root, inclusive. Cached."""
        p = self._paths.get(node)
        if p is None:
            if not (0 <= node < len(self.parent)):
                raise TreeError("node index out of range")
            chain, v = [], node
            while v != -1:
                chain.append(v)
                v = self.parent[v]
            p = self._paths[node] = tuple(chain)
        return p

    def nodes_at(self, level: int) -> tuple[int, ...]:
        """Nodes where a repair of this level can land. A level with no nodes uses the next level up (the root always
        qualifies as a feeder-level node)."""
        for lv in range(level, -1, -1):
            if self._by_level[lv]:
                return tuple(self._by_level[lv])
        return (0,)


class RestorationTree:
    """All circuits, keyed by the opaque circuit index."""

    def __init__(self, circuits: Mapping[int, CircuitTree], version: str = config.TREE_VERSION):
        self.circuits = dict(circuits)
        self.version = version

    def circuit(self, pc) -> CircuitTree | None:
        return self.circuits.get(pc) if isinstance(pc, int) and not isinstance(pc, bool) else None

    def locate(self, row: Mapping) -> tuple[CircuitTree, int] | None:
        """The circuit tree and node of a premise row, or None when the row has no (valid) position."""
        c = self.circuit(row.get("pc"))
        pn = row.get("pn")
        if c is None or isinstance(pn, bool) or not isinstance(pn, int) or not (0 <= pn < len(c)):
            return None
        return c, pn

    @classmethod
    def from_dict(cls, d: Mapping) -> "RestorationTree":
        if d.get("version") != config.TREE_VERSION:
            raise TreeError(f"unsupported tree version; expected {config.TREE_VERSION}")
        circuits = {}
        for key, c in d["circuits"].items():
            try:
                level = [LEVEL_INDEX[name] for name in c["level"]]
            except KeyError:
                raise TreeError("unknown level name") from None
            if any(s < config.MIN_SERVED for s in c["served"]):
                raise TreeError(f"a node serves fewer than {config.MIN_SERVED} premises")
            circuits[int(key)] = CircuitTree(c["parent"], c["depth"], c["served"], level)
        return cls(circuits, d["version"])


def levels_tree(row: Mapping) -> tuple[CircuitTree, int]:
    """Fallback for a premise with no position in the artifact (`model_path = "v1_levels"`).

    Rebuilds the three-level path of restoration-v1 from the row's exposure counts: breaker (cn homes), the farthest
    protective device (nf), the nearest protective device (nl) and the transformer (n0), plus the rest of the line
    as a sibling main-line section when the main line does not cover every home. Returns the tree and the node of
    the customer's transformer. Never returned to a caller, so small counts are not a privacy issue here.
    """
    cn, nf, nl, n0 = (max(1, int(row[k])) for k in ("cn", "nf", "nl", "n0"))
    nf, nl, n0 = min(nf, cn), min(nl, nf), min(n0, nl)
    parent, served, level, depth = [-1, 0, 1, 2], [cn, nf, nl, n0], [0, 0, 1, 2], [0, 1, 2, 3]
    if cn > nf:
        parent.append(0)
        served.append(cn - nf)
        level.append(0)
        depth.append(1)
    return CircuitTree(parent, depth, served, level), 3
