import math

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from downcut import api, cleanup, importers, shapes
from downcut.gcode import export, origin_shift
from downcut.geometry import polygon_from
from downcut.model import Operation, Vector, job_to_dict
from downcut.simulate import simulate
from downcut.toolpaths import build_all
from downcut.toolpaths.base import CUT, RAPID
from downcut.verify import check, limits_for


def add(job, kind, tool_id, vectors, **params):
    job.vectors.extend(vectors)
    op = Operation(kind, tool_id, [v.id for v in vectors], params)
    job.operations.append(op)
    return op


def cuts(path):
    previous = None
    for move in path.moves:
        if previous is not None and move[0] == CUT:
            yield previous[1:4], move[1:4]
        previous = move


def feed_span(gcode):
    xs, ys = [], []
    for line in gcode.splitlines():
        if line.startswith("G1") and "Z" not in line:
            for token in line.split():
                if token[0] == "X":
                    xs.append(float(token[1:]))
                if token[0] == "Y":
                    ys.append(float(token[1:]))
    return min(xs), max(xs), min(ys), max(ys)


def rapids_below_top(path):
    count = 0
    for move in path.moves:
        if move[0] == RAPID and move[3] > 2:
            count += 1
    return count


@pytest.mark.parametrize(
    "origin,dx,dy",
    [("lower_left", 0, 0), ("lower_right", 300, 0), ("upper_left", 0, 200), ("upper_right", 300, 200), ("center", 150, 100)],
)
def test_every_xy_zero_makes_the_same_cut(job, run, origin, dx, dy):
    add(job, "pocket", "em-250", [shapes.rectangle(40, 50, 100, 60)], depth=3.0)
    job.xy_origin = origin
    assert origin_shift(job)[:2] == (dx, dy)
    _, files, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    span = feed_span(files[0].text)
    assert span[0] == pytest.approx(40 + 3.175 - dx, abs=0.01)
    assert span[2] == pytest.approx(50 + 3.175 - dy, abs=0.01)
    assert heights[int(80 / res), int(90 / res)] == pytest.approx(-3.0, abs=0.01)
    assert heights[int(150 / res), int(200 / res)] == 0.0


def test_upper_left_zero_works_with_imported_posts(job):
    from downcut.ppfile import STARTER

    job.xy_origin = "upper_left"
    job.custom_posts = [{"id": "mine", "name": "Mine", "source": STARTER}]
    add(job, "pocket", "em-250", [shapes.rectangle(40, 50, 100, 60)], depth=3.0)
    text = export(job, build_all(job), "mine")[0].text
    report = check(text, limits_for(job, job.tools[0], False, True))
    assert report.passed, report.errors
    assert feed_span(text)[3] < 0


def test_continuous_ramp_never_levels_off_until_the_bottom(job, run):
    add(job, "profile", "em-250", [shapes.rectangle(60, 60, 100, 80)], side="outside", depth=9.0, spiral=True)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    rows = [(a, b) for a, b in cuts(paths[0]) if a[2] < 0 or b[2] < 0]
    levels = [b[2] for _, b in rows]
    assert all(later <= earlier + 1e-9 for earlier, later in zip(levels, levels[1:]))
    assert min(levels) == pytest.approx(-9.0)
    flat = [a for a, b in rows if abs(a[2] - b[2]) < 1e-9]
    assert all(a[2] == pytest.approx(-9.0) for a in flat)
    perimeter = 2 * (106.35 + 86.35)
    assert sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in rows if a[2] == -9.0 and b[2] == -9.0) == pytest.approx(perimeter, rel=0.05)
    assert sum(1 for m in paths[0].moves if m[0] == RAPID and m[3] > 0) <= 3
    assert heights[int(100 / res), int(57 / res)] == pytest.approx(-9.0, abs=0.02)


def test_continuous_ramp_keeps_tabs(job, run):
    add(job, "profile", "em-250", [shapes.rectangle(60, 60, 100, 80)], side="outside", depth=12.0, spiral=True,
        tab_count=4, tab_length=10.0, tab_thickness=3.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    row = heights[int(100 / res)]
    assert row[int(57 / res)] < -11.9 or heights[int(57 / res), int(110 / res)] < -11.9
    assert (heights >= -9.01).sum() > 0
    assert any(abs(b[2] + 9.0) < 1e-6 for _, b in cuts(paths[0]))


def test_continuous_ramp_on_an_open_line_goes_there_and_back(job, run):
    line = Vector([[50, 100], [200, 100]], False)
    add(job, "profile", "em-125", [line], side="on", depth=4.0, spiral=True)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    down = [m for m in paths[0].moves if m[0] == RAPID and 0 < m[3]]
    assert len(down) <= 3
    assert heights[int(100 / res), int(120 / res)] == pytest.approx(-4.0, abs=0.02)


def test_keep_tool_down_stays_in_the_cut_on_a_pocket(job, run):
    pocket = shapes.rectangle(60, 60, 60, 40)
    add(job, "pocket", "em-250", [pocket], depth=9.0)
    apart = run(job)
    job.operations.clear()
    job.vectors.clear()
    add(job, "pocket", "em-250", [pocket], depth=9.0, keep_down=True)
    together = run(job)
    assert together[2][0].passed, together[2][0].errors
    assert rapids_below_top(together[0][0]) < rapids_below_top(apart[0][0])
    assert np.abs(together[3] - apart[3]).max() < 0.05


def test_keep_tool_down_with_a_ramp_entry_still_ramps(job, run):
    add(job, "pocket", "em-250", [shapes.rectangle(60, 60, 60, 40)], depth=9.0, keep_down=True, entry="ramp", entry_angle=5.0)
    paths, _, reports, *_ = run(job)
    assert reports[0].passed, reports[0].errors
    for (x0, y0, z0), (x1, y1, z1) in cuts(paths[0]):
        if z1 < -3.3 and z1 < z0 - 1e-6:
            assert math.hypot(x1 - x0, y1 - y0) > 0


def test_keep_tool_down_on_an_open_line_cuts_back_and_forth(job, run):
    line = Vector([[50, 100], [200, 100]], False)
    add(job, "profile", "em-250", [line], side="on", depth=9.0, keep_down=True)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    assert rapids_below_top(paths[0]) <= 3
    assert heights[int(100 / res), int(120 / res)] == pytest.approx(-9.0, abs=0.02)


def test_keep_tool_down_shortens_leads(job, run):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "profile", "em-250", [plate], side="outside", depth=9.0, lead="arc", lead_length=6.0, keep_down=True)
    paths, _, reports, *_ = run(job)
    assert reports[0].passed, reports[0].errors
    assert rapids_below_top(paths[0]) <= 3


def test_last_pass_cleans_the_wall_at_full_depth(job, run):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "profile", "em-250", [plate], side="outside", depth=9.0, finish_allowance=0.5,
        tab_count=4, tab_length=8.0, tab_thickness=2.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    shape = polygon_from(plate.points)
    distances = [LineString([a[:2], b[:2]]).distance(shape) for a, b in cuts(paths[0]) if min(a[2], b[2]) < 0]
    assert min(distances) == pytest.approx(3.175, abs=0.03)
    moves = list(cuts(paths[0]))
    half = len(moves) // 2
    assert min(LineString([a[:2], b[:2]]).distance(shape) for a, b in moves[:half]) >= 3.175 + 0.5 - 0.03
    assert heights[int(100 / res), int(59 / res)] <= -6.99


def test_last_pass_does_not_cut_through_the_tabs(job, run):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "profile", "em-250", [plate], side="outside", depth=12.0, finish_allowance=0.5,
        tab_count=4, tab_length=8.0, tab_thickness=3.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    edge = heights[int(57.0 / res), int(60 / res) : int(160 / res)]
    assert edge.max() >= -9.05
    assert edge.min() <= -11.95


def test_last_pass_warns_on_a_through_cut_with_no_tabs(job):
    add(job, "profile", "em-250", [shapes.rectangle(60, 60, 100, 80)], side="outside", depth=19.2, finish_allowance=0.5)
    assert any("may move" in w for w in build_all(job)[0].warnings)


def test_rest_machining_clears_corners_with_a_smaller_tool(job, run):
    pocket = shapes.rectangle(60, 60, 60, 40)
    add(job, "pocket", "em-500", [pocket], depth=3.0)
    _, _, _, plain, res = run(job)
    corner = (int(60.8 / res), int(60.8 / res))
    assert plain[corner] == 0.0
    job.operations.clear()
    job.vectors.clear()
    add(job, "pocket", "em-500", [pocket], depth=3.0, rest_tool_id="em-125")
    paths, files, reports, heights, res = run(job)
    assert [p.tool.id for p in paths] == ["em-500", "em-125"]
    assert len(files) == 2 and all(r.passed for r in reports), [r.errors for r in reports]
    assert heights[corner] == pytest.approx(-3.0, abs=0.02)
    assert heights[int(59 / res), int(59 / res)] == 0.0
    assert heights[int(80 / res), int(90 / res)] == pytest.approx(-3.0, abs=0.02)


def test_rest_tool_must_be_smaller(job):
    add(job, "pocket", "em-125", [shapes.rectangle(60, 60, 60, 40)], depth=3.0, rest_tool_id="em-500")
    paths = build_all(job)
    assert len(paths) == 1 and any("must be smaller" in w for w in paths[0].warnings)


def test_changing_the_rest_tool_recomputes(job):
    from dataclasses import replace

    pocket = shapes.rectangle(60, 60, 60, 40)
    add(job, "pocket", "em-500", [pocket], depth=3.0, rest_tool_id="em-125")
    first = api.compute({"job": job_to_dict(job)})
    job.tools = [replace(t, stepdown=0.5) if t.id == "em-125" else t for t in job.tools]
    second = api.compute({"job": job_to_dict(job)})
    assert first["programs"][1]["line_count"] != second["programs"][1]["line_count"]


def test_display_carries_depth_for_each_point(job):
    add(job, "pocket", "em-250", [shapes.rectangle(60, 60, 60, 40)], depth=6.0)
    data = api.compute({"job": job_to_dict(job)})
    points = [p for run in data["paths"][0]["runs"] if run["kind"] == "cut" for p in run["points"]]
    assert all(len(p) == 3 for p in points)
    assert {-3.0, -6.0} <= {p[2] for p in points}


def test_svg_paths_that_return_to_the_start_are_closed():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="6in" viewBox="0 0 6 6">'
        '<path d="M 1,1 L 3,1 L 3,3 L 1,3 L 1,1"/><path d="M 4,4 L 5,5"/></svg>'
    )
    vectors = importers.import_svg(svg)
    assert sorted(v.closed for v in vectors) == [False, True]
    closed = next(v for v in vectors if v.closed)
    assert polygon_from(closed.points).area == pytest.approx(50.8**2, rel=1e-6)


R12 = """  0
SECTION
  2
HEADER
  9
$ACADVER
  1
AC1009
  9
$INSUNITS
 70
6
  0
ENDSEC
  0
SECTION
  2
BLOCKS
  0
BLOCK
  8
0
  2
*Model_Space
 70
0
 10
0.0
 20
0.0
 30
0.0
  3
*Model_Space
  0
ENDBLK
  8
0
  0
ENDSEC
  0
SECTION
  2
ENTITIES
  0
POLYLINE
  5
70
  8
LAYER_0
 66
1
 10
0.0
 20
0.0
 30
0.0
 70
1
  0
VERTEX
  8
LAYER_0
 10
1.0
 20
1.0
 30
0.0
  0
VERTEX
  8
LAYER_0
 10
3.0
 20
1.0
 30
0.0
  0
VERTEX
  8
LAYER_0
 10
3.0
 20
3.0
 30
0.0
  0
SEQEND
  8
LAYER_0
  0
ENDSEC
  0
EOF
"""


def test_old_r12_dxf_loads_and_ignores_its_false_units():
    vectors = importers.import_dxf(R12, "in")
    assert len(vectors) == 1 and vectors[0].closed
    assert polygon_from(vectors[0].points).area == pytest.approx(0.5 * 50.8**2, rel=1e-6)


def test_scan_finds_copies_in_any_direction_and_start():
    a = shapes.rectangle(0, 0, 50, 30)
    copy = Vector(a.points[2:] + a.points[:2], True)
    backwards = Vector(list(reversed(a.points)), True)
    result = cleanup.scan([a, copy, backwards])
    assert {row["vector_id"] for row in result["duplicates"]} == {copy.id, backwards.id}
    assert all(row["keep_id"] == a.id for row in result["duplicates"])


def test_scan_finds_a_near_copy_and_a_line_on_top_of_a_shape():
    a = shapes.rectangle(0, 0, 50, 30)
    near = shapes.rectangle(0.02, 0.01, 50, 30)
    line = Vector([[5, 0], [45, 0]], False)
    result = cleanup.scan([a, near, line], tolerance=0.05)
    labels = {row["vector_id"]: row["label"] for row in result["duplicates"]}
    assert labels[near.id] == "Exact copy"
    assert labels[line.id] == "Line on top of another"


def test_scan_finds_tiny_rectangles_and_slivers_but_keeps_real_art():
    plate = shapes.rectangle(0, 0, 100, 100)
    speck = shapes.rectangle(40, 40, 0.3, 0.2)
    dot = shapes.circle(60, 60, 0.15)
    sliver = shapes.rectangle(10, 10, 30, 0.02)
    hole = shapes.circle(70, 30, 5)
    result = cleanup.scan([plate, speck, dot, sliver, hole], tiny=0.5, tolerance=0.05)
    labels = {row["vector_id"]: row["label"] for row in result["tiny"]}
    assert labels[speck.id] == "Tiny rectangle"
    assert labels[dot.id] == "Tiny shape"
    assert labels[sliver.id] == "Thin sliver"
    assert plate.id not in labels and hole.id not in labels
    assert result["duplicates"] == []


def test_scan_does_not_flag_neighbors_or_touching_corners():
    a = shapes.rectangle(0, 0, 50, 50)
    far = shapes.rectangle(0, -70, 50, 50)
    corner = shapes.rectangle(50, 50, 20, 20)
    result = cleanup.scan([a, far, corner])
    assert result["duplicates"] == [] and result["overlaps"] == [] and result["tiny"] == []


def test_shared_edges_are_reported_but_not_removed():
    left = shapes.rectangle(0, 0, 50, 50)
    right = shapes.rectangle(50, 0, 50, 50)
    result = cleanup.scan([left, right])
    assert result["duplicates"] == []
    assert len(result["overlaps"]) == 1 and result["overlaps"][0]["fixable"] is False


def test_overlapping_open_line_can_be_trimmed():
    edge = Vector([[0, 0], [100, 0]], False)
    lapping = Vector([[60, 0], [140, 0]], False)
    result = cleanup.scan([edge, lapping])
    row = result["overlaps"][0]
    assert row["fixable"] is True
    target = lapping if row["vector_id"] == lapping.id else edge
    other = edge if target is lapping else lapping
    pieces = cleanup.trim_overlap(target, other)
    assert sum(LineString(p.points).length for p in pieces) == pytest.approx(target_length(target) - 40, abs=0.2)


def target_length(vector):
    return LineString(vector.points).length


def test_scan_route_answers():
    a = shapes.rectangle(0, 0, 50, 30)
    b = Vector(list(a.points), True)
    from dataclasses import asdict

    data = api.scan_vectors({"vectors": [asdict(a), asdict(b)], "tiny": 0.5, "tolerance": 0.05})
    assert len(data["duplicates"]) == 1
    assert Point(0, 0)


@pytest.mark.parametrize("feed,plunge", [(2000.0, 500.0), (1000.0, 300.0), (1500.0, 100.0), (2540.0, 760.0), (1829.0, 508.0)])
def test_round_metric_feeds_keep_their_zeros(job, feed, plunge):
    from dataclasses import replace

    job.tools = [replace(t, feed=feed, plunge=plunge) if t.id == "em-250" else t for t in job.tools]
    add(job, "profile", "em-250", [shapes.rectangle(60, 60, 100, 80)], side="outside", depth=3.0)
    text = export(job, build_all(job), "grbl")[0].text
    assert f"F{feed:.0f}" in text and f"F{plunge:.0f}" in text
    report = check(text, limits_for(job, job.tool("em-250")))
    assert report.cut_length_mm / feed * 60 < report.seconds < report.cut_length_mm / plunge * 60


def test_even_odd_matches_one_at_a_time():
    from functools import reduce

    from downcut.geometry import region

    rng = np.random.default_rng(7)
    rows = [shapes.rectangle(*rng.uniform(0, 80, 2), *rng.uniform(5, 40, 2)) for _ in range(60)]
    plain = reduce(lambda a, b: a.symmetric_difference(b), [polygon_from(v.points) for v in rows])
    assert region(rows).symmetric_difference(plain).area < 1e-6
