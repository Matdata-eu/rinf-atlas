"""Line profile ordering, no database: `python -m pytest api/tests`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.profile import Edge, default_orientation, order_sections, reverse_steps, route, summarize  # noqa: E402

COORDS = {"A": (4.0, 50.0), "B": (4.1, 50.0), "C": (4.2, 50.0), "D": (4.3, 50.0), "E": (4.5, 50.0), "F": (4.6, 50.0)}


def seq(steps):
    return [(s.start, s.end) for s in steps]


def test_shuffled_and_mixed_direction_sections_are_chained():
    edges = [Edge("s3", "D", "C", 1), Edge("s1", "A", "B", 1), Edge("s2", "B", "C", 1)]
    steps = default_orientation(order_sections(edges, COORDS), COORDS)
    assert seq(steps) == [("A", "B"), ("B", "C"), ("C", "D")]
    assert [s.reversed for s in steps] == [False, False, True]
    assert not any(s.gap_before for s in steps)


def test_gap_between_runs_is_flagged_and_runs_are_ordered_by_distance():
    edges = [Edge("s1", "A", "B", 1), Edge("s2", "B", "C", 1), Edge("s4", "E", "F", 1)]
    steps = default_orientation(order_sections(edges, COORDS), COORDS)
    assert seq(steps) == [("A", "B"), ("B", "C"), ("E", "F")]
    assert [s.gap_before for s in steps] == [False, False, True]


def test_branch_becomes_a_separate_run():
    # A-B-C-D main line, B-E short branch
    edges = [Edge("s1", "A", "B", 5), Edge("s2", "B", "C", 5), Edge("s3", "C", "D", 5), Edge("b", "B", "E", 1)]
    steps = order_sections(edges, COORDS)
    assert len(steps) == 4
    assert sum(s.gap_before for s in steps) == 1
    main = [s.edge.key for s in steps if s.edge.key != "b"]
    assert main in (["s1", "s2", "s3"], ["s3", "s2", "s1"])


def test_missing_operational_points_do_not_break_ordering():
    edges = [Edge("s1", "A", "B", 1), Edge("x", None, None, 1)]
    steps = order_sections(edges, COORDS)
    assert {s.edge.key for s in steps} == {"s1", "x"}
    assert sum(s.gap_before for s in steps) == 1


def test_reverse_keeps_gaps_between_the_same_sections():
    edges = [Edge("s1", "A", "B", 1), Edge("s2", "B", "C", 1), Edge("s4", "E", "F", 1)]
    steps = default_orientation(order_sections(edges, COORDS), COORDS)
    rev = reverse_steps(steps)
    assert seq(rev) == [("F", "E"), ("C", "B"), ("B", "A")]
    assert [s.gap_before for s in rev] == [False, True, False]


def test_route_takes_the_shortest_path():
    edges = [Edge("s1", "A", "B", 1), Edge("s2", "B", "C", 1), Edge("long", "A", "C", 5), Edge("s3", "D", "C", 1)]
    steps = route(edges, ["A", "D"])
    assert [s.edge.key for s in steps] == ["s1", "s2", "s3"]
    assert steps[-1].reversed
    assert route(edges, ["A", "E"]) is None


def test_route_via():
    edges = [Edge("s1", "A", "B", 1), Edge("s2", "B", "C", 1), Edge("long", "A", "C", 1.5)]
    assert [s.edge.key for s in route(edges, ["A", "C"])] == ["long"]
    assert [s.edge.key for s in route(edges, ["A", "B", "C"])] == ["s1", "s2"]


def test_summarize():
    assert summarize("range", [160, 120, True]) == {"min": 120, "max": 160}
    assert summarize("range", []) == {}
    assert summarize("terms", ["x", "y", "x"]) == {"values": [{"value": "x", "n": 2}, {"value": "y", "n": 1}]}
    assert summarize("boolean", [True, False, True]) == {"values": [{"value": True, "n": 2}, {"value": False, "n": 1}]}


def test_orientation_follows_how_sections_are_recorded():
    # recorded east to west (start OP is the eastern one): keep that direction
    edges = [Edge("s1", "B", "A", 1), Edge("s2", "C", "B", 1), Edge("s3", "D", "C", 1)]
    steps = default_orientation(order_sections(edges, COORDS), COORDS)
    assert seq(steps) == [("D", "C"), ("C", "B"), ("B", "A")]
    assert not any(s.reversed for s in steps)
