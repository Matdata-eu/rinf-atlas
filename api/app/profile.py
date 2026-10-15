"""Line profile: order sections of line into a continuous sequence (POST /api/profile).

Pure functions, no database: the API loads sections and operational points from
PostGIS and hands them here. A section is an edge between its start and end
operational point (the `opStart` / `opEnd` link facets).
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Edge:
    key: str                  # section IRI
    a: str | None             # start operational point IRI
    b: str | None             # end operational point IRI
    length: float             # km (> 0)

    def ends(self) -> tuple[str, str]:
        # A section without a start or end OP still needs distinct nodes.
        return (self.a or f"?a:{self.key}", self.b or f"?b:{self.key}")


@dataclass
class Step:
    edge: Edge
    reversed: bool            # traversed from end OP to start OP
    gap_before: bool = False  # not connected to the previous step

    @property
    def start(self) -> str:
        a, b = self.edge.ends()
        return b if self.reversed else a

    @property
    def end(self) -> str:
        a, b = self.edge.ends()
        return a if self.reversed else b


@dataclass
class Run:
    steps: list[Step] = field(default_factory=list)

    @property
    def start(self) -> str:
        return self.steps[0].start

    @property
    def end(self) -> str:
        return self.steps[-1].end

    @property
    def length(self) -> float:
        return sum(s.edge.length for s in self.steps)

    def flipped(self) -> Run:
        return Run([Step(s.edge, not s.reversed) for s in reversed(self.steps)])


def haversine_km(p: tuple[float, float], q: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _adjacency(edges: list[Edge]) -> dict[str, list[tuple[Edge, str]]]:
    adj: dict[str, list[tuple[Edge, str]]] = defaultdict(list)
    for e in edges:
        a, b = e.ends()
        adj[a].append((e, b))
        if a != b:
            adj[b].append((e, a))
    return adj


def _dijkstra(adj, src: str, target: str | None = None):
    dist = {src: 0.0}
    prev: dict[str, tuple[str, Edge]] = {}
    heap = [(0.0, src)]
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, math.inf):
            continue
        if n == target:
            break
        for e, m in sorted(adj.get(n, ()), key=lambda x: (x[0].key, x[1])):
            nd = d + max(e.length, 1e-6)
            if nd < dist.get(m, math.inf):
                dist[m] = nd
                prev[m] = (n, e)
                heapq.heappush(heap, (nd, m))
    return dist, prev


def _path(prev, src: str, dst: str) -> list[Step]:
    steps: list[Step] = []
    n = dst
    while n != src:
        p, e = prev[n]
        a, _ = e.ends()
        steps.append(Step(e, reversed=(a != p)))
        n = p
    steps.reverse()
    return steps


def _components(edges: list[Edge]) -> list[list[Edge]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        a, b = e.ends()
        parent[find(a)] = find(b)
    comps: dict[str, list[Edge]] = defaultdict(list)
    for e in edges:
        comps[find(e.ends()[0])].append(e)
    return list(comps.values())


def _longest_run(edges: list[Edge]) -> Run:
    """The longest shortest path through one connected group of sections (exact for a
    tree, a good approximation with loops): two Dijkstra sweeps."""
    adj = _adjacency(edges)
    leaves = sorted(n for n, xs in adj.items() if len(xs) == 1)
    start = leaves[0] if leaves else min(adj)
    dist, _ = _dijkstra(adj, start)
    u = max(sorted(dist), key=lambda n: dist[n])
    dist, prev = _dijkstra(adj, u)
    v = max(sorted(dist), key=lambda n: dist[n])
    if u == v:  # a single loop section
        e = min(edges, key=lambda x: x.key)
        return Run([Step(e, False)])
    return Run(_path(prev, u, v))


def order_sections(edges: list[Edge], coords: dict[str, tuple[float, float]]) -> list[Step]:
    """Order sections into one sequence: each connected group contributes its longest
    path; what is left (branches, parallel sections) becomes further runs. Runs are then
    chained greedily, attaching each to the nearest end of the sequence, with
    `gap_before` set where two runs meet."""
    remaining = {e.key: e for e in edges}
    runs: list[Run] = []
    while remaining:
        comps = _components(list(remaining.values()))
        comp = max(comps, key=lambda c: (sum(e.length for e in c), -len(c), min(e.key for e in c)))
        run = _longest_run(comp)
        runs.append(run)
        for s in run.steps:
            remaining.pop(s.edge.key, None)
    if not runs:
        return []

    def dist(p: str, q: str) -> float:
        if p == q:
            return 0.0
        if p in coords and q in coords:
            return haversine_km(coords[p], coords[q])
        return math.inf

    runs.sort(key=lambda r: -r.length)
    chain = runs[0]
    rest = runs[1:]
    while rest:
        best = None
        for i, r in enumerate(rest):
            for cand in (r, r.flipped()):
                for at_tail, d in ((True, dist(chain.end, cand.start)), (False, dist(cand.end, chain.start))):
                    if best is None or d < best[0]:
                        best = (d, i, cand, at_tail)
        _, i, cand, at_tail = best
        rest.pop(i)
        if at_tail:
            cand.steps[0].gap_before = cand.start != chain.end
            chain = Run(chain.steps + cand.steps)
        else:
            chain.steps[0].gap_before = cand.end != chain.start
            chain = Run(cand.steps + chain.steps)
    chain.steps[0].gap_before = False
    return chain.steps


def default_orientation(steps: list[Step], coords: dict[str, tuple[float, float]]) -> list[Step]:
    """Orient a sequence the way most of its sections are recorded (start -> end
    operational point usually follows the line's kilometres); on a tie, run west to
    east or north to south, whichever axis the sequence mostly follows."""
    if not steps:
        return steps
    backwards = sum(s.reversed for s in steps)
    if backwards * 2 > len(steps):
        return reverse_steps(steps)
    if backwards * 2 == len(steps) and steps[0].start in coords and steps[-1].end in coords:
        (x0, y0), (x1, y1) = coords[steps[0].start], coords[steps[-1].end]
        if (x0 > x1) if abs(x1 - x0) >= abs(y1 - y0) else (y0 < y1):
            return reverse_steps(steps)
    return steps


def reverse_steps(steps: list[Step]) -> list[Step]:
    out = [Step(s.edge, not s.reversed) for s in reversed(steps)]
    # gap flags belong between the same two sections, now on the other one
    gaps = [s.gap_before for s in steps]
    for i, s in enumerate(out):
        j = len(steps) - 1 - i  # index of this section in the original order
        s.gap_before = gaps[j + 1] if j + 1 < len(steps) else False
    return out


def route(edges: list[Edge], waypoints: list[str]) -> list[Step] | None:
    """Shortest sequence of sections through the given operational points, or None."""
    adj = _adjacency(edges)
    steps: list[Step] = []
    for src, dst in zip(waypoints, waypoints[1:], strict=False):
        if src == dst:
            continue
        if src not in adj or dst not in adj:
            return None
        _, prev = _dijkstra(adj, src, dst)
        if dst not in prev:
            return None
        steps += _path(prev, src, dst)
    return steps


def summarize(kind: str, values: list) -> dict:
    """One strip cell from the values of a section and its tracks.
    range -> {min, max}; terms/boolean -> {values: [{value, n}]} (n = how many tracks)."""
    if kind == "range":
        nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return {"min": min(nums), "max": max(nums)} if nums else {}
    counts: dict = {}
    for v in values:
        k = (type(v).__name__, v)
        counts[k] = counts.get(k, 0) + 1
    return {"values": [{"value": v, "n": n} for (_, v), n in sorted(counts.items(), key=lambda x: (-x[1], str(x[0][1])))]}
