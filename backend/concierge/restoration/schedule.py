"""Step restoration for one simulated trial (spec-03 design D3).

Crews are one pooled resource that works down a single priority list, so time is the running total of job hours
(a "fluid" model). Rules:

1. A node's jobs are available only when no strict ancestor node has an unfinished job (a lateral cannot restore
   anyone while the main line above it is still down).
2. Among available jobs, pick the highest `served(node) / job hours` (customers restored per crew-hour). Ties go to
   the shallower node, then the lower node index, then the lower job index.
3. Scheduling stops when the customer's last path job is done; later jobs do not add to their time.

The functions here return crew-hours. The caller divides by the number of crews at work and converts working hours
to clock hours.
"""
from __future__ import annotations

from collections.abc import Sequence
from heapq import heappop, heappush

from .tree import CircuitTree


class PathInfo:
    """The customer's place in a circuit: the nodes from their own node up to the breaker, and each node's level."""

    __slots__ = ("pn", "nodes", "node_set", "level")

    def __init__(self, circuit: CircuitTree, pn: int):
        self.pn = pn
        self.nodes = circuit.path(pn)
        self.node_set = frozenset(self.nodes)
        self.level = {v: circuit.level[v] for v in self.nodes}


def schedule_trial(circuit: CircuitTree, path: PathInfo, job_nodes: Sequence[int], job_hours: Sequence[float],
                   order: list[int] | None = None) -> tuple[float, float, float]:
    """Crew-hours until the customer's path repairs are done, as (feeder, lateral, service) milestones.

    Each milestone is the running total when the last path job of that level or any higher level finished, so the
    three numbers never decrease and the last one is the time the customer's power returns. A trial with no job on
    the path returns (0, 0, 0). If `order` is a list, the job indexes are appended in the order they were scheduled.
    """
    path_set, path_level = path.node_set, path.level
    left = 0
    groups: dict[int, list[int]] = {}
    for j, v in enumerate(job_nodes):
        g = groups.get(v)
        if g is None:
            groups[v] = [j]
        else:
            g.append(j)
        if v in path_set:
            left += 1
    if left == 0:
        return 0.0, 0.0, 0.0

    parent, depth, served = circuit.parent, circuit.depth, circuit.served
    # Link every node that has jobs to its nearest ancestor that also has jobs. Only that ancestor can block it,
    # because that ancestor is itself blocked until everything above it is done.
    kids: dict[int, list[int]] = {}
    heap: list[tuple[float, int, int, int]] = []
    for v, g in groups.items():
        a = parent[v]
        while a != -1 and a not in groups:
            a = parent[a]
        if a == -1:
            s, d = served[v], depth[v]
            for j in g:
                heap.append((-s / job_hours[j], d, v, j))
        else:
            kids.setdefault(a, []).append(v)
    heap.sort()          # a sorted list is already a valid heap
    remaining = {v: len(g) for v, g in groups.items()}

    done = [0.0, 0.0, 0.0]
    cum = 0.0
    while heap:
        _, _, v, j = heappop(heap)
        cum += job_hours[j]
        if order is not None:
            order.append(j)
        if v in path_set:
            done[path_level[v]] = cum
            left -= 1
            if left == 0:
                break
        remaining[v] -= 1
        if remaining[v] == 0:
            for c in kids.get(v, ()):
                s, d = served[c], depth[c]
                for k in groups[c]:
                    heappush(heap, (-s / job_hours[k], d, c, k))
    return done[0], max(done[0], done[1]), max(done[0], done[1], done[2])
