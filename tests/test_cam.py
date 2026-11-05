import base64
import math
import re

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from downcut import api, inlay, shapes, templates, tiling, vtdb
from downcut.arcfit import ARC_CCW, ARC_CW, fit_arcs
from downcut.gcode import export
from downcut.geometry import polygon_from
from downcut.model import Material, Operation, Tool, Vector, job_from_dict, job_to_dict, new_layer
from downcut.ppfile import STARTER, format_number, parse_pp, PpVariable, render_pp
from downcut.simulate import simulate
from downcut.toolpaths import build_all
from downcut.toolpaths.base import CUT, RAPID
from downcut.verify import check, limits_for, parse


def add(job, kind, tool_id, vectors, **params):
    job.vectors.extend(vectors)
    op = Operation(kind, tool_id, [v.id for v in vectors], params)
    job.operations.append(op)
    return op


def cuts(path):
    previous = None
    out = []
    for move in path.moves:
        if previous is not None and move[0] == CUT:
            out.append((previous[1:4], move[1:4]))
        previous = move
    return out


def zone_slope_ok(path, angle, tol=0.3):
    for (x0, y0, z0), (x1, y1, z1) in cuts(path):
        run = math.hypot(x1 - x0, y1 - y0)
        if z1 < z0 - 1e-6 and run > 0 and z1 < 0:
            assert math.degrees(math.atan2(z0 - z1, run)) <= angle + tol


@pytest.mark.parametrize("entry", ["ramp", "helix"])
def test_pocket_entries_never_plunge_straight_below_the_top(job, run, entry):
    add(job, "pocket", "em-125", [shapes.rectangle(50, 50, 60, 40)], depth=6.0, entry=entry, entry_angle=3.0)
    paths, _, reports, heights, res = run(job)
    assert all(r.passed for r in reports), reports[0].errors
    reached = 0.0
    for (x0, y0, z0), (x1, y1, z1) in cuts(paths[0]):
        straight_down = math.hypot(x1 - x0, y1 - y0) < 1e-6 and z1 < z0
        if straight_down:
            assert z1 >= reached - 1e-6
        reached = min(reached, z1)
    zone_slope_ok(paths[0], 3.0)
    assert heights[int(70 / res), int(80 / res)] == pytest.approx(-6.0, abs=0.01)


def test_helix_falls_back_to_ramp_in_a_slot_too_small(job, run):
    add(job, "pocket", "em-125", [shapes.rectangle(50, 50, 60, 3.3)], depth=3.0, entry="helix")
    paths, _, reports, *_ = run(job)
    assert reports[0].passed
    assert any("helix" in w for w in paths[0].warnings)


def test_finish_pass_reaches_the_final_wall_after_roughing(job, run):
    add(job, "pocket", "em-125", [shapes.rectangle(50, 50, 60, 40)], depth=3.0, finish_allowance=0.5)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed
    assert heights[int(70 / res), int(52 / res)] <= -3.0 + 0.01
    assert heights[int(70 / res), int(49 / res)] == 0.0
    xs = [m[1] for m in paths[0].moves if m[0] == CUT]
    assert min(xs) == pytest.approx(50 + 1.5875, abs=0.01)
    rough = [m for m in paths[0].moves[: len(paths[0].moves) // 2] if m[0] == CUT]
    assert min(m[1] for m in rough) >= 50 + 1.5875 + 0.5 - 0.01


@pytest.mark.parametrize("lead", ["arc", "line"])
def test_outside_lead_starts_clear_of_the_part_and_keeps_size(job, run, lead):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "profile", "em-250", [plate], side="outside", depth=6.0, lead=lead, lead_length=6.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed
    shape = polygon_from(plate.points)
    tool_r = 3.175
    for (x0, y0, _), (x1, y1, z1) in cuts(paths[0]):
        if z1 < 0:
            assert LineString([(x0, y0), (x1, y1)]).distance(shape) >= tool_r - 0.03
    first_cut = next(m for m in paths[0].moves if m[0] == CUT and m[3] < 0)
    assert Point(first_cut[1], first_cut[2]).distance(shape) > tool_r + 1.0
    assert heights[int(100 / res), int(110 / res)] == 0.0


def test_inside_lead_stays_inside(job, run):
    hole = shapes.rectangle(60, 60, 100, 80)
    add(job, "profile", "em-250", [hole], side="inside", depth=6.0, lead="arc", lead_length=5.0)
    paths, _, reports, *_ = run(job)
    assert reports[0].passed
    room = polygon_from(hole.points).buffer(-3.15, join_style="mitre")
    for (x0, y0, _), (x1, y1, z1) in cuts(paths[0]):
        if z1 < 0:
            assert room.covers(LineString([(x0, y0), (x1, y1)]))


def test_lead_that_cannot_fit_is_reported(job, run):
    hole = shapes.rectangle(60, 60, 8, 8)
    add(job, "profile", "em-250", [hole], side="inside", depth=3.0, lead="arc", lead_length=20.0)
    paths, _, reports, *_ = run(job)
    assert reports[0].passed
    assert any("lead did not fit" in w for w in paths[0].warnings)


def test_merge_joins_toolpaths_in_one_operation(job, run):
    a = shapes.rectangle(20, 20, 40, 40)
    b = shapes.rectangle(100, 20, 40, 40)
    job.vectors.extend([a, b])
    children = [
        {"kind": "profile", "tool_id": "em-250", "vector_ids": [a.id], "params": {"side": "outside", "depth": 3.0}},
        {"kind": "pocket", "tool_id": "em-250", "vector_ids": [b.id], "params": {"depth": 3.0}},
    ]
    job.operations.append(Operation("merge", "em-250", [a.id, b.id], {"operations": children}, "Both"))
    paths, files, reports, heights, res = run(job)
    assert len(paths) == 1 and len(files) == 1 and reports[0].passed
    assert heights[int(40 / res), int(120 / res)] == pytest.approx(-3.0, abs=0.01)
    assert heights[int(40 / res), int(15 / res)] == pytest.approx(-3.0, abs=0.01)


def test_merge_refuses_mixed_tools(job):
    a = shapes.rectangle(20, 20, 40, 40)
    job.vectors.append(a)
    child = {"kind": "profile", "tool_id": "em-125", "vector_ids": [a.id], "params": {"depth": 3.0}}
    job.operations.append(Operation("merge", "em-250", [a.id], {"operations": [child]}))
    with pytest.raises(ValueError, match="same tool"):
        build_all(job)


def test_toolpath_array_copies_the_cut_without_new_vectors(job, run):
    add(job, "pocket", "em-125", [shapes.rectangle(20, 20, 20, 20)], depth=2.0,
        array_x=3, array_y=2, array_step_x=50, array_step_y=40)
    _, _, reports, heights, res = run(job)
    assert reports[0].passed
    assert len(job.vectors) == 1
    for x, y in [(30, 30), (80, 30), (130, 30), (30, 70), (80, 70), (130, 70)]:
        assert heights[int(y / res), int(x / res)] == pytest.approx(-2.0, abs=0.01)
    assert heights[int(55 / res), int(55 / res)] == 0.0


def test_disabled_operation_is_skipped(job, run):
    op = add(job, "pocket", "em-125", [shapes.rectangle(20, 20, 20, 20)], depth=2.0)
    op.enabled = False
    add(job, "pocket", "em-250", [shapes.rectangle(80, 20, 20, 20)], depth=2.0)
    paths, *_ = run(job)
    assert len(paths) == 1 and paths[0].tool.id == "em-250"


def test_chamfer_cuts_the_exact_width_and_angle(job, run):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "chamfer", "vb-90", [plate], width=3.0, side="inside", clearance=0.0)
    paths, _, reports, heights, res = run(job, resolution=0.1)
    assert reports[0].passed
    assert reports[0].z_min == pytest.approx(-3.0, abs=1e-3)
    row = heights[int(100 / res)]
    assert row[int(60 / res)] == pytest.approx(-3.0, abs=0.15)
    assert row[int(61.5 / res)] == pytest.approx(-1.5, abs=0.15)
    assert row[int(63.2 / res)] == 0.0


def test_chamfer_needs_a_vbit_and_warns_if_too_wide(job):
    plate = shapes.rectangle(60, 60, 100, 80)
    add(job, "chamfer", "em-250", [plate], width=1.0)
    assert "V-bit" in build_all(job)[0].warnings[0]
    job.operations.clear()
    add(job, "chamfer", "vb-90", [plate], width=10.0)
    assert any("cutting radius" in w for w in build_all(job)[0].warnings)


def test_fluting_ramps_in_and_out_and_reaches_depth(job, run):
    line = Vector([[50, 100], [200, 100]], False)
    add(job, "fluting", "bn-250", [line], depth=3.0, ramp="both", ramp_length=40.0)
    _, _, reports, heights, res = run(job)
    assert reports[0].passed
    row = heights[int(100 / res)]
    assert row[int(120 / res)] == pytest.approx(-3.0, abs=0.05)
    assert row[int(70 / res)] == pytest.approx(-1.5, abs=0.15)
    assert row[int(52 / res)] > -0.5


def test_smooth_fluting_uses_an_eased_ramp(job):
    from downcut.toolpaths.decor import flute_scale

    stations = np.array([0.0, 10.0, 20.0, 80.0, 100.0])
    linear = flute_scale(stations, 100.0, "both", 20.0, False)
    eased = flute_scale(stations, 100.0, "both", 20.0, True)
    assert linear.tolist() == pytest.approx([0, 0.5, 1, 1, 0])
    assert eased[1] == pytest.approx(0.5)
    assert flute_scale(np.array([5.0]), 100.0, "both", 20.0, True)[0] < 0.25


def test_prism_ridge_stays_at_the_surface(job, run):
    strip = shapes.rectangle(50, 50, 120, 10)
    add(job, "prism", "vb-90", [strip])
    paths, _, reports, heights, res = run(job, resolution=0.1)
    assert reports[0].passed
    assert reports[0].z_min == pytest.approx(-5.0, abs=0.1)
    row = heights[:, int(110 / res)]
    assert row[int(55 / res)] == pytest.approx(0.0, abs=0.15)
    assert row[int(50 / res)] == pytest.approx(-5.0, abs=0.2)
    assert row[int(52.5 / res)] == pytest.approx(-2.5, abs=0.2)


def test_vbit_flat_tip_reduces_depth(job, run):
    job.tools.append(Tool("tip", "Tip", "vbit", 12.7, 1000, 300, 18000, 20.0, angle=90.0, tip_diameter=2.0))
    strip = shapes.rectangle(50, 50, 120, 10)
    add(job, "vcarve", "tip", [strip])
    _, _, reports, heights, res = run(job, resolution=0.1)
    assert reports[0].z_min == pytest.approx(-4.0, abs=0.05)
    assert heights[int(55 / res), int(110 / res)] == pytest.approx(-4.0, abs=0.1)


def test_auto_inlay_plug_fits_the_pocket(job, run):
    star = shapes.star(70, 100, 40, 18, 5)
    job.vectors.append(star)
    result = inlay.auto_inlay(job, {"tool_id": "em-125", "vector_ids": [star.id], "depth": 3.0, "clearance": 0.1, "plug_extra": 0.5})
    job.layers.extend(result["layers"])
    job.vectors.extend(result["vectors"])
    job.operations.extend(result["operations"])
    pocket = next(v for v in result["vectors"] if v.layer == inlay.POCKET_LAYER)
    plug = next(v for v in result["vectors"] if v.layer == inlay.PLUG_LAYER)
    a, b = polygon_from(pocket.points), polygon_from(plug.points)
    from shapely import affinity

    moved = affinity.translate(affinity.scale(b, -1, 1), a.centroid.x - affinity.scale(b, -1, 1).centroid.x, a.centroid.y - b.centroid.y)
    assert a.contains(moved)
    assert a.area - moved.area == pytest.approx(a.length * 0.1, rel=0.1)
    assert all(op.enabled for op in result["operations"])
    _, _, reports, *_ = run(job)
    assert all(r.passed for r in reports)


def test_vcarve_inlay_plug_never_hits_the_pocket(job):
    job.material = Material(300.0, 200.0, 19.0)
    shape = shapes.star(70, 100, 30, 13, 5)
    job.vectors.append(shape)
    result = inlay.vcarve_inlay(
        job,
        {"tool_id": "vb-60", "vector_ids": [shape.id], "depth": 6.0, "gap": 1.5, "clear_tool_id": "em-125", "margin": 12.0},
    )
    job.layers.extend(result["layers"])
    job.vectors.extend(result["vectors"])
    job.operations.extend(result["operations"])
    plug_op = result["operations"][1]
    assert plug_op.params["start_depth"] == pytest.approx(4.5)
    assert plug_op.enabled

    res = 0.2

    def surface(ops):
        job.operations = ops
        paths = build_all(job)
        programs = [(job.tool(f.tool_id), f.text) for f in export(job, paths, "grbl")]
        for tool, text in programs:
            report = check(text, limits_for(job, tool))
            assert report.passed, report.errors
        return simulate(job, programs, res)[0]

    female = surface([result["operations"][0]])
    male = surface([result["operations"][1]])
    x0 = min(p[0] for p in shape.points)
    x1 = max(p[0] for p in shape.points)
    plug_vectors = [v for v in result["vectors"]]
    mirrored = plug_vectors[0]
    mx0 = min(p[0] for p in mirrored.points)
    mx1 = max(p[0] for p in mirrored.points)
    start = 4.5
    worst = 1e9
    y0 = min(p[1] for p in shape.points)
    y1 = max(p[1] for p in shape.points)
    for j in range(int((y0 - 1) / res), int((y1 + 1) / res)):
        y = j * res
        for i in range(int((x0 - 1) / res), int((x1 + 1) / res)):
            x = i * res
            mirror_x = mx1 - (x - x0)
            k = int(round(mirror_x / res))
            if k < 0 or k >= male.shape[1]:
                continue
            assembled = -(start + male[j, k])
            worst = min(worst, assembled - female[j, i])
    assert worst >= -0.25


def test_templates_round_trip_and_follow_thickness(job):
    plate = shapes.rectangle(20, 20, 40, 40)
    add(job, "profile", "em-250", [plate], side="outside", depth=19.5)
    template = templates.make_template(job)
    job.material = Material(300.0, 200.0, 12.0)
    made = templates.apply_template(job, template, [plate.id])
    assert made[0].params["depth"] == pytest.approx(12.5)
    assert made[0].vector_ids == [plate.id]
    job.tools = [t for t in job.tools if t.id != "em-250"]
    with pytest.raises(ValueError, match="not in this job"):
        templates.apply_template(job, template, [plate.id])


def test_template_matches_vectors_by_layer(job):
    plate = shapes.rectangle(20, 20, 40, 40)
    plate.layer = "Cut"
    add(job, "profile", "em-250", [plate], side="outside", depth=3.0)
    template = templates.make_template(job)
    other = shapes.rectangle(100, 20, 40, 40)
    other.layer = "Cut"
    job.vectors.append(other)
    made = templates.apply_template(job, template)
    assert set(made[0].vector_ids) == {plate.id, other.id}


def test_arc_fit_recovers_a_circle_within_tolerance():
    circle = shapes.circle(0, 0, 40)
    points = circle.points + [circle.points[0]]
    moves = [(RAPID, points[0][0], points[0][1], -1.0, 0.0)]
    moves += [(CUT, x, y, -1.0, 1000.0) for x, y in points[1:]]
    fitted = fit_arcs(moves)
    arcs = [row for row in fitted if len(row) > 5]
    assert 2 <= len(arcs) <= 3
    assert all(row[0] == ARC_CCW for row in arcs)
    for row in arcs:
        assert math.hypot(row[5], row[6]) == pytest.approx(0, abs=0.02)


def test_arc_fit_leaves_corners_alone():
    moves = [(RAPID, 0, 0, -1, 0)] + [(CUT, x, y, -1, 1000) for x, y in [(10, 0), (10, 10), (0, 10), (0, 0)]]
    assert fit_arcs(moves) == moves


def test_arc_fit_sees_clockwise_and_helical_moves():
    angles = np.linspace(0, -math.pi, 60)
    moves = [(RAPID, 10, 0, 0.0, 0.0)]
    moves += [(CUT, 10 * math.cos(a), 10 * math.sin(a), -a / math.pi * -2, 900) for a in angles[1:]]
    fitted = fit_arcs(moves)
    arcs = [row for row in fitted if len(row) > 5]
    assert len(arcs) == 1 and arcs[0][0] == ARC_CW and arcs[0][3] == pytest.approx(-2, abs=1e-6)


@pytest.mark.parametrize("post", ["grbl", "mach3", "linuxcnc"])
@pytest.mark.parametrize("units", ["in", "mm"])
def test_arc_output_matches_the_straight_output(job, post, units):
    job.units = units
    add(job, "profile", "em-250", [shapes.circle(100, 100, 40)], side="outside", depth=6.0)
    add(job, "pocket", "em-125", [shapes.ellipse(220, 100, 30, 20)], depth=3.0, entry="helix")
    paths = build_all(job)
    plain = export(job, paths, post)
    job.arc_output = True
    arced = export(job, paths, post)
    for before, after in zip(plain, arced):
        tool = job.tool(after.tool_id)
        report = check(after.text, limits_for(job, tool))
        assert report.passed, report.errors
        assert " I" in after.text and ("G2 " in after.text or "G3 " in after.text)
        assert len(after.text) < len(before.text) * 0.6
        a = np.array([s.end for s in parse(before.text)[0] if None not in s.end and not s.rapid])
        b = np.array([s.end for s in parse(after.text)[0] if None not in s.end and not s.rapid])
        assert LineString(a).hausdorff_distance(LineString(b)) < 0.04


def test_checker_catches_a_bad_arc_and_an_arc_past_the_floor(job):
    limits = limits_for(job, job.tools[0])
    head = "G90 G21 G17\nG0 Z5.08\nM3 S1000\nG0 X50 Y50\nG1 Z-2 F500\n"
    bad = head + "G2 X60 Y50 I3 J0\nG0 Z5.08\nM5\nM30\n"
    assert any("off its circle" in e for e in check(bad, limits).errors)
    wide = head + "G2 X-50 Y50 I-50 J0 Z-2\nG0 Z5.08\nM5\nM30\n"
    assert any("outside the material" in e for e in check(wide, limits).errors)
    assert check(head + "G90.1\nG2 X60 Y50 I55 J50\nG0 Z5.08\nM5\nM30\n", limits).passed


def test_checker_measures_arc_length(job):
    limits = limits_for(job, job.tools[0])
    text = "G90 G21 G17\nG0 Z5.08\nM3 S1000\nG0 X50 Y50\nG1 Z-2 F500\nG3 X30 Y50 I-10 J0\nG0 Z5.08\nM5\nM30\n"
    report = check(text, limits)
    assert report.cut_length_mm == pytest.approx(math.pi * 10 + 7.08, abs=0.02)


def test_checker_handles_plus_signs_and_refuses_comma_decimals(job):
    limits = limits_for(job, job.tools[0])
    text = "G90 G21\nG0 Z+5.08\nM3 S1000\nG0 X+500.0 Y+10.0\nM5\nM30\n"
    assert any("outside the material" in e for e in check(text, limits).errors)
    comma = "G90 G21\nG0 Z5,0\nM5\nM30\n"
    assert any("comma" in e for e in check(comma, limits).errors)


def test_checker_notes_machine_moves_without_failing(job):
    limits = limits_for(job, job.tools[0])
    text = "G90 G21\nG0 Z5.08\nM3 S1000\nG0 X10 Y10\nG1 Z-1 F300\nG1 X20\nG0 Z5.08\nM5\nG53 G0 Z0\nM30\n"
    report = check(text, limits)
    assert report.passed and any("machine coordinates" in w for w in report.warnings)


def test_keepout_blocks_a_cut_that_enters_it(job, run):
    clamp = shapes.rectangle(85, 85, 30, 30)
    clamp.layer = "Clamps"
    job.layers.append(new_layer("Clamps", keepout=True))
    job.vectors.append(clamp)
    add(job, "pocket", "em-250", [shapes.rectangle(50, 50, 40, 40)], depth=3.0)
    _, _, reports, *_ = run(job)
    assert reports[0].passed is False
    assert any("keep-out" in e for e in reports[0].errors)


def test_keepout_clear_of_the_tool_passes(job, run):
    clamp = shapes.rectangle(150, 150, 30, 30)
    clamp.layer = "Clamps"
    job.layers.append(new_layer("Clamps", keepout=True))
    job.vectors.append(clamp)
    add(job, "pocket", "em-250", [shapes.rectangle(50, 50, 40, 40)], depth=3.0)
    _, _, reports, *_ = run(job)
    assert reports[0].passed


def test_tiling_cuts_the_whole_job_across_tiles(job):
    job.material = Material(600.0, 200.0, 12.0)
    add(job, "pocket", "em-250", [shapes.rectangle(50, 50, 500, 100)], depth=3.0)
    add(job, "profile", "em-250", [shapes.rectangle(20, 20, 560, 160)], side="outside", depth=12.5)
    data = job_to_dict(job)
    whole = api.preview({"job": data, "post": "grbl"})
    data["tiling"] = {"enabled": True, "width": 250.0, "height": 300.0, "overlap": 25.0}
    split = api.gcode({"job": data, "post": "grbl", "title": "t"})
    names = [row["name"] for row in split["files"]]
    assert any("tile-r1c1" in n for n in names) and any("tile-r1c3" in n for n in names)
    assert all(row["report"]["passed"] for row in split["files"]), split["files"][0]["report"]
    for row in split["files"]:
        assert row["report"]["x_range"][1] <= 250.0 + 3.2
    tiled = api.preview({"job": data, "post": "grbl"})
    a = np.frombuffer(base64.b64decode(whole["surface"]["heights"]), "<f4")
    b = np.frombuffer(base64.b64decode(tiled["surface"]["heights"]), "<f4")
    assert a.shape == b.shape
    assert np.abs(a - b).max() < 0.02


GRBL_STYLE = '''
+ Test post written for these tests
POST_NAME = "Test mm (*.gc)"
FILE_EXTENSION = "gc"
UNITS = "MM"
LINE_ENDING = "[13][10]"
SUBSTITUTE = "({)}"
LINE_NUMBER_START = 100
LINE_NUMBER_INCREMENT = 5
VAR LINE_NUMBER = [N|A|N|1.0]
VAR SPINDLE_SPEED = [S|A|S|1.0]
VAR FEED_RATE = [F|C|F|1.1]
VAR PLUNGE_RATE = [FP|C|F|1.1]
VAR X_POSITION = [X|C|X|1.3]
VAR Y_POSITION = [Y|C|Y|1.3]
VAR Z_POSITION = [Z|C|Z|1.3]
VAR X_HOME_POSITION = [XH|A|X|1.3]
VAR Y_HOME_POSITION = [YH|A|Y|1.3]
VAR Z_HOME_POSITION = [ZH|A|Z|1.3]
VAR ARC_CENTRE_I_INC_POSITION = [I|A|I|1.3]
VAR ARC_CENTRE_J_INC_POSITION = [J|A|J|1.3]
begin HEADER
"[N] ( [TOOLNAME] )"
"[N] G90 G21 G17 G91.1"
"[N] G0 [ZH]"
"[N] [S] M3"
begin RAPID_MOVE
"[N] G0[X][Y][Z]"
begin FIRST_FEED_MOVE
"[N] G1[X][Y][Z][F]"
begin FEED_MOVE
"[N] G1[X][Y][Z]"
begin FIRST_CW_ARC_MOVE
"[N] G2[X][Y][I][J][F]"
begin CW_ARC_MOVE
"[N] G2[X][Y][I][J]"
begin FIRST_CCW_ARC_MOVE
"[N] G3[X][Y][I][J][F]"
begin CCW_ARC_MOVE
"[N] G3[X][Y][I][J]"
begin FOOTER
"[N] G0 [ZH]"
"[N] G0 [XH] [YH]"
"[N] M5"
"[N] M30"
'''


def post_job(job, source=GRBL_STYLE):
    job.custom_posts = [{"id": "mine", "name": "Mine", "source": source}]
    add(job, "profile", "em-250", [shapes.circle(100, 100, 40)], side="outside", depth=6.0)
    return job


def test_pp_format_reads_flags_widths_and_multipliers():
    assert format_number(PpVariable("X", decimals=3), 1.5) == "1.500"
    assert format_number(PpVariable("X", flags="+", decimals=3), 1.5) == "+1.500"
    assert format_number(PpVariable("X", flags="0", width=8, decimals=3), 1.5) == "0001.500"
    assert format_number(PpVariable("X", width=8, decimals=3), 1.5) == "   1.500"
    assert format_number(PpVariable("X", flags="-", width=8, decimals=3), 1.5) == "1.500   "
    assert format_number(PpVariable("X", decimals=2, separator=","), 1.5) == "1,50"
    assert format_number(PpVariable("X", flags="#", decimals=0), 3.0) == "3."
    assert format_number(PpVariable("F", decimals=1, multiplier=0.01666), 600.0) == "10.0"
    assert format_number(PpVariable("X", decimals=3), -0.0001) == "0.000"


def test_pp_parse_reads_the_statements():
    post = parse_pp(GRBL_STYLE, "x")
    assert (post.name, post.extension, post.units) == ("Test mm (*.gc)", "gc", "mm")
    assert post.line_ending == "\r\n"
    assert post.substitute == {"(": "{", ")": "}"}
    assert post.supports_arcs and not post.supports_helix
    assert post.variables["X"].always is False and post.variables["X"].prefix == "X"


def test_pp_output_follows_the_post_and_passes_the_checker(job):
    post_job(job)
    job.tools[0].name = "End mill (1/4)"
    files = export(job, build_all(job), "mine", "demo")
    text = files[0].text
    assert files[0].name.endswith(".gc")
    assert "\r\n" in text
    lines = text.split("\r\n")
    assert lines[0] == "N100 ( End mill {1/4} )"
    assert lines[1].startswith("N105 G90")
    assert len(re.findall(r" M3(?!\d)", text)) == 1
    assert lines[-2].endswith("M30")
    report = check(text, limits_for(job, job.tools[0], False, True))
    assert report.passed, report.errors


def test_pp_only_prints_changed_words(job):
    post_job(job)
    text = export(job, build_all(job), "mine", "demo")[0].text
    moves = [line for line in text.split("\r\n") if " G1" in line]
    assert moves
    assert not any("X" in line and line.count("X") > 1 for line in moves)
    plunges = [line for line in moves if "Z" in line and "X" not in line]
    assert plunges and all("F" in line for line in plunges[:1])
    feeds = [line.split("F")[1] for line in moves if "F" in line]
    assert any(a != b for a, b in zip(feeds, feeds[1:])) or len(feeds) >= 1


def test_pp_arcs_match_straight_output(job):
    post_job(job)
    paths = build_all(job)
    straight = export(job, paths, "mine")[0].text
    job.arc_output = True
    arced = export(job, paths, "mine")[0].text
    assert " G3" in arced or " G2" in arced
    report = check(arced, limits_for(job, job.tools[0], False, True))
    assert report.passed, report.errors
    a = np.array([s.end for s in parse(straight)[0] if None not in s.end and not s.rapid])
    b = np.array([s.end for s in parse(arced)[0] if None not in s.end and not s.rapid])
    assert LineString(a).hausdorff_distance(LineString(b)) < 0.04


def test_pp_inch_post_scales_a_metric_job(job):
    post_job(job, STARTER)
    text = export(job, build_all(job), "mine")[0].text
    assert "G20" in text
    report = check(text, limits_for(job, job.tools[0], False, True))
    assert report.passed, report.errors
    assert report.z_min == pytest.approx(-6.0, abs=0.01)
    assert report.x_range[1] == pytest.approx(100 + 40 + 3.175, abs=0.05)


def test_starter_post_has_no_stray_spaces(job):
    post_job(job, STARTER)
    text = export(job, build_all(job), "mine")[0].text
    moves = [line for line in text.splitlines() if line.startswith(("G0", "G1", "G2", "G3"))]
    assert moves and not any("  " in line for line in moves)


def test_pp_multiplier_writes_inches_per_second(job):
    source = GRBL_STYLE.replace("VAR FEED_RATE = [F|C|F|1.1]", "VAR FEED_RATE = [F|C|F|1.2|0.01666]")
    post_job(job, source)
    text = export(job, build_all(job), "mine")[0].text
    assert "F33.86" in text or "F33.85" in text


def test_pp_adds_units_when_a_post_has_none():
    source = GRBL_STYLE.replace("G90 G21 G17 G91.1", "G90 G17 G91.1")
    post = parse_pp(source)
    assert post.blocks["HEADER"][0] == "G21"
    assert any("added G21" in note for note in post.notes)


def test_pp_manual_spindle_post_passes_with_a_note(job):
    source = GRBL_STYLE.replace('"[N] [S] M3"\n', "").replace('"[N] M5"\n', "")
    post_job(job, source)
    data = api.compute({"job": job_to_dict(job), "post": "mine"})
    row = data["programs"][0]
    assert row["passed"], row["errors"]
    assert any("spindle" in w for w in row["warnings"])


def test_pp_refuses_rotary_and_base_posts():
    with pytest.raises(ValueError, match="rotary"):
        parse_pp('POST_NAME="R"\nROTARY_WRAP_Y=A\n')
    with pytest.raises(ValueError, match="POST_BASE"):
        parse_pp('POST_NAME="R"\nPOST_BASE="x.pp"\n')
    with pytest.raises(ValueError, match="RAPID_MOVE"):
        parse_pp('POST_NAME="R"\n')


def test_pp_bad_line_names_the_line():
    with pytest.raises(ValueError, match="Line 2"):
        parse_pp('POST_NAME="R"\nthis is not a post line\n')


def test_pp_hpgl_post_stays_blocked(job):
    source = '''
POST_NAME = "Plotter"
FILE_EXTENSION = "plt"
UNITS = "MM"
VAR X_POSITION = [X|A||1.0|40]
VAR Y_POSITION = [Y|A||1.0|40]
begin HEADER
"IN;PA;"
begin RAPID_MOVE
"PU[X],[Y];"
begin FEED_MOVE
"PD[X],[Y];"
begin FOOTER
"PU;"
'''
    post_job(job, source)
    data = api.compute({"job": job_to_dict(job), "post": "mine"})
    assert data["programs"][0]["passed"] is False


def test_post_check_returns_a_sample_and_a_report():
    data = api.check_post({"source": GRBL_STYLE})
    assert data["report"]["passed"], data["report"]["errors"]
    assert data["arcs"] is True
    assert "G3" in data["sample"] or "G2" in data["sample"]


def make_vtdb(tmp_path, rate_units=4):
    import sqlite3

    path = tmp_path / "t.vtdb"
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        create table machine (id text, name text, make text, model text, controller_type text,
          dimensions_units integer, max_width real, max_height real);
        create table material (id text, name text);
        create table tool_geometry (id text, name_format text, notes text, tool_type integer, units integer,
          diameter real, included_angle real, flat_diameter real, num_flutes integer, flute_length real, tip_radius real);
        create table tool_cutting_data (id text, rate_units integer, feed_rate real, plunge_rate real,
          spindle_speed integer, spindle_dir integer, stepdown real, stepover real, length_units integer, tool_number integer);
        create table tool_entity (id text, material_id text, machine_id text, tool_geometry_id text, tool_cutting_data_id text);
        create table tool_tree_entry (id text, parent_group_id text, sibling_order integer, tool_geometry_id text, name text);
        insert into machine values ('m1','Router','Acme','R1','GRBL',1,32.125,32.125);
        insert into material values ('w','Hardwood');
        insert into tool_geometry values ('g1','1/4 Flat','',1,1,0.25,null,null,2,1.0,null);
        insert into tool_geometry values ('g2','60 Vee','',4,1,0.25,60.0,0.01,2,null,null);
        insert into tool_geometry values ('g3','6 mm Ball','',0,0,6.0,null,null,2,null,null);
        insert into tool_geometry values ('g4','Form','',7,1,0.5,null,null,2,null,null);
        insert into tool_cutting_data values ('c1',{rate_units},160.0,80.0,16000,0,0.28,0.125,1,null);
        insert into tool_cutting_data values ('c2',{rate_units},100.0,50.0,18000,0,0.04,0.004,1,null);
        insert into tool_cutting_data values ('c3',{rate_units},60.0,30.0,18000,0,0.1,0.02,1,null);
        insert into tool_cutting_data values ('c4',{rate_units},60.0,30.0,18000,0,0.1,0.02,1,null);
        insert into tool_cutting_data values ('n1',4,null,null,null,0,null,null,0,7);
        insert into tool_entity values ('e1','w','m1','g1','c1');
        insert into tool_entity values ('e2','w','m1','g2','c2');
        insert into tool_entity values ('e3','w','m1','g3','c3');
        insert into tool_entity values ('e4','w','m1','g4','c4');
        insert into tool_entity values ('e5',null,'m1','g1','n1');
        insert into tool_tree_entry values ('t1',null,0,null,'Shop');
        insert into tool_tree_entry values ('t2','t1',0,null,'Flats');
        insert into tool_tree_entry values ('t3','t2',0,'g1',null);
        """
    )
    db.commit()
    db.close()
    return path.read_bytes()


def test_vtdb_reads_tools_machine_and_units(tmp_path):
    data = vtdb.read_vtdb(make_vtdb(tmp_path))
    tools = {row["name"]: row for row in data["tools"]}
    flat = tools["1/4 Flat"]
    assert flat["kind"] == "endmill" and flat["diameter"] == pytest.approx(6.35)
    assert flat["feed"] == pytest.approx(160 * 25.4)
    assert flat["stepdown"] == pytest.approx(0.28 * 25.4)
    assert flat["number"] == 7
    assert flat["machine"] == "Router" and flat["material"] == "Hardwood"
    assert flat["notes"] == "Shop > Flats"
    vee = tools["60 Vee"]
    assert vee["kind"] == "vbit" and vee["angle"] == 60 and vee["tip_diameter"] == pytest.approx(0.254)
    assert tools["6 mm Ball"]["kind"] == "ballnose" and tools["6 mm Ball"]["diameter"] == pytest.approx(6.0)
    assert "Form" not in tools
    assert data["machines"][0]["width"] == pytest.approx(32.125 * 25.4)
    assert data["materials"] == ["Hardwood"]
    assert any("no matching tool type" in note for note in data["notes"])


def test_vtdb_tools_load_into_a_job_and_cut(tmp_path, job, run):
    rows = vtdb.read_vtdb(make_vtdb(tmp_path))["tools"]
    job.tools = [Tool(**row) for row in rows]
    flat = next(t for t in job.tools if t.kind == "endmill")
    add(job, "pocket", flat.id, [shapes.rectangle(50, 50, 40, 40)], depth=6.0)
    _, _, reports, *_ = run(job)
    assert reports[0].passed


def test_vtdb_refuses_other_files():
    with pytest.raises(ValueError, match="tool database"):
        vtdb.read_vtdb(b"not a database at all, just some bytes" * 40)


def test_machine_import_routes_each_file_type(tmp_path):
    post = api.import_machine({"name": "my.pp", "data": base64.b64encode(GRBL_STYLE.encode()).decode()})
    assert post["kind"] == "post" and post["source"] == GRBL_STYLE
    tools = api.import_machine({"name": "my.vtdb", "data": base64.b64encode(make_vtdb(tmp_path)).decode()})
    assert tools["kind"] == "tools" and len(tools["tools"]) == 3
    with pytest.raises(ValueError, match=".pp"):
        api.import_machine({"name": "my.txt", "data": ""})


def test_notice_when_the_sheet_exceeds_the_machine(job):
    job.machine = {"name": "Router", "width": 200.0, "height": 200.0}
    add(job, "pocket", "em-250", [shapes.rectangle(50, 50, 40, 40)], depth=3.0)
    assert "tiling" in api.compute({"job": job_to_dict(job)})["notices"][0]


def test_setup_sheet_names_files_tools_and_warnings(job):
    add(job, "pocket", "em-250", [shapes.rectangle(50, 50, 40, 40)], depth=3.0)
    add(job, "vcarve", "vb-60", [shapes.rectangle(100, 50, 40, 40)], flat_depth=2.0)
    data = api.setup_sheet({"job": job_to_dict(job), "title": "panel", "post": "grbl"})
    html = data["html"]
    assert data["name"] == "panel-setup-sheet.html"
    assert "panel-01-" in html and "1/4 in end mill" in html and "Add a clearing tool" in html
    assert "http://" not in html and "https://" not in html


def test_old_project_files_still_load():
    old = {
        "material": {"width": 100, "height": 100, "thickness": 10},
        "vectors": [{"points": [[0, 0], [1, 0], [1, 1]], "closed": True, "layer": "Layer 1", "id": "a", "group": ""}],
        "tools": [{"id": "t", "name": "T", "kind": "endmill", "diameter": 6, "feed": 1000, "plunge": 300, "rpm": 18000, "stepdown": 3}],
        "operations": [{"kind": "profile", "tool_id": "t", "vector_ids": ["a"], "params": {"depth": 1}}],
    }
    job = job_from_dict(old)
    assert job.layers[0]["name"] == "Layer 1" and job.operations[0].enabled is True
