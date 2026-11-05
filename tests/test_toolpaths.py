import math

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from downcut.geometry import region
from downcut.model import Operation
from downcut.shapes import circle, rectangle
from downcut.textvec import text_vectors
from downcut.toolpaths.base import depth_levels
from support import ARIAL



def add(job, kind, tool_id, vectors, **params):
    job.vectors.extend(vectors)
    job.operations.append(Operation(kind, tool_id, [v.id for v in vectors], params))


def mask_of(geom, heights, res):
    ny, nx = heights.shape
    xs = np.arange(nx) * res
    ys = np.arange(ny) * res
    px, py = np.meshgrid(xs, ys)
    import shapely

    return shapely.contains_xy(geom, px, py)


def signed_area(points):
    pts = np.asarray(points)
    return 0.5 * np.sum(pts[:-1, 0] * pts[1:, 1] - pts[1:, 0] * pts[:-1, 1])


def test_depth_levels_are_even_and_end_at_depth():
    assert depth_levels(10, 3) == pytest.approx([-2.5, -5.0, -7.5, -10.0])
    assert depth_levels(3, 3) == pytest.approx([-3.0])


def test_outside_profile_leaves_part_at_true_size(job, run):
    add(job, "profile", "em-250", [rectangle(50, 50, 100, 60)], side="outside", depth=19.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    assert reports[0].z_min == pytest.approx(-19.0)
    moves = [m for m in paths[0].moves if m[0] == 1]
    xs = [m[1] for m in moves]
    ys = [m[2] for m in moves]
    assert min(xs) == pytest.approx(50 - 3.175, abs=1e-6)
    assert max(xs) == pytest.approx(150 + 3.175, abs=1e-6)
    assert min(ys) == pytest.approx(50 - 3.175, abs=1e-6)
    assert max(ys) == pytest.approx(110 + 3.175, abs=1e-6)
    row = heights[int(80 / res)]
    through = np.where(row <= -19.0 + 1e-6)[0] * res
    inside = through[(through > 40) & (through < 100)]
    outer = through[(through > 100) & (through < 170)]
    assert inside.max() == pytest.approx(50.0, abs=res * 1.5)
    assert outer.min() == pytest.approx(150.0, abs=res * 1.5)


def test_climb_direction_is_clockwise_outside_and_ccw_inside(job):
    from downcut.toolpaths.profile import offset_rings

    shape = region([rectangle(0, 0, 50, 50)])
    outside = offset_rings(shape, "outside", 3.0, True)[0]
    inside = offset_rings(shape, "inside", 3.0, True)[0]
    assert signed_area(outside) < 0
    assert signed_area(inside) > 0


def test_conventional_reverses_direction(job):
    from downcut.toolpaths.profile import offset_rings

    shape = region([rectangle(0, 0, 50, 50)])
    assert signed_area(offset_rings(shape, "outside", 3.0, False)[0]) > 0
    assert signed_area(offset_rings(shape, "inside", 3.0, False)[0]) < 0


def test_inner_ring_is_cut_before_outer_ring(job, run):
    vectors = [rectangle(50, 50, 120, 100), circle(110, 100, 20)]
    add(job, "profile", "em-250", vectors, side="outside", depth=6.0)
    paths, _, reports, _, _ = run(job)
    assert reports[0].passed, reports[0].errors
    first_cut = next(m for m in paths[0].moves if m[0] == 1)
    assert math.hypot(first_cut[1] - 110, first_cut[2] - 100) < 30


def test_tabs_leave_material_at_tab_height(job, run):
    add(
        job,
        "profile",
        "em-250",
        [rectangle(60, 60, 100, 60)],
        side="outside",
        depth=19.0,
        tab_count=4,
        tab_length=10.0,
        tab_thickness=3.0,
    )
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    path_z = [m[3] for m in paths[0].moves if m[0] == 1]
    assert min(path_z) == pytest.approx(-19.0)
    ring = heights[:, int(160 + 3.175 / res * res) and int((160 + 3.175) / res)]
    assert np.isclose(ring, -16.0, atol=0.01).any()
    assert np.isclose(ring, -19.0, atol=0.01).any()


def test_ramp_entry_never_plunges_at_depth_and_clears_floor(job, run):
    add(
        job,
        "profile",
        "em-250",
        [rectangle(60, 60, 100, 60)],
        side="outside",
        depth=12.0,
        ramp_length=30.0,
    )
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    vertical = [
        (a, b)
        for a, b in zip(paths[0].moves, paths[0].moves[1:])
        if b[0] == 1 and a[1] == b[1] and a[2] == b[2] and b[3] < -0.01
    ]
    assert vertical == []
    column = heights[:, int((160 + 3.175) / res)]
    assert column[int(70 / res) : int(110 / res)].max() == pytest.approx(-12.0, abs=0.02)


def test_pocket_clears_floor_and_keeps_island(job, run):
    outer = rectangle(40, 40, 160, 100)
    island = circle(120, 90, 15)
    add(job, "pocket", "em-250", [outer, island], depth=8.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    assert paths[0].warnings == []
    assert reports[0].z_min == pytest.approx(-8.0)
    assert heights[int(90 / res), int(120 / res)] == 0.0
    shape = Polygon([(40, 40), (200, 40), (200, 140), (40, 140)]).difference(Point(120, 90).buffer(15))
    core = shape.buffer(-0.5)
    inside = mask_of(core, heights, res)
    corner_free = mask_of(shape.buffer(-3.175).buffer(3.175), heights, res)
    floor = heights[inside & corner_free]
    assert floor.max() == pytest.approx(-8.0, abs=1e-6)
    outside = ~mask_of(shape.buffer(0.5), heights, res)
    assert heights[outside].min() == 0.0


def test_pocket_tool_too_large_warns(job):
    from downcut.toolpaths import build_all

    add(job, "pocket", "em-500", [rectangle(10, 10, 8, 8)], depth=3.0)
    paths = build_all(job)
    assert paths[0].moves == []
    assert "too large" in paths[0].warnings[0]


def test_peck_drill_reaches_depth_in_steps(job, run):
    add(job, "drill", "dr-125", [circle(40, 40, 3), circle(90, 40, 3)], depth=12.0, peck=4.0)
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    cuts = [round(m[3], 3) for m in paths[0].moves if m[0] == 1]
    assert cuts.count(-4.0) == 2 and cuts.count(-8.0) == 2 and cuts.count(-12.0) == 2
    assert heights[int(40 / res), int(40 / res)] == pytest.approx(-12.0)
    assert heights[int(40 / res), int(90 / res)] == pytest.approx(-12.0)


def test_vcarve_strip_depth_follows_width_and_angle(job, run):
    add(job, "vcarve", "vb-90", [rectangle(60, 60, 120, 10)])
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    assert paths[0].warnings == []
    assert reports[0].z_min == pytest.approx(-5.0, abs=0.03)
    assert heights[int(65 / res), int(120 / res)] == pytest.approx(-5.0, abs=0.05)
    assert heights[int(62.5 / res), int(120 / res)] == pytest.approx(-2.5, abs=0.05)


def test_vcarve_60_degree_goes_deeper(job, run):
    add(job, "vcarve", "vb-60", [rectangle(60, 60, 120, 10)])
    _, _, reports, _, _ = run(job)
    assert reports[0].z_min == pytest.approx(-5.0 / math.tan(math.radians(30)), abs=0.05)


def test_vcarve_outline_matches_shape(job, run):
    shape = rectangle(60, 60, 120, 10)
    add(job, "vcarve", "vb-90", [shape])
    _, _, _, heights, res = run(job)
    target = mask_of(region([shape]), heights, res)
    carved = heights < -0.02
    union = (target | carved).sum()
    assert (target & carved).sum() / union > 0.97


def test_vcarve_circle_is_a_cone(job, run):
    add(job, "vcarve", "vb-90", [circle(100, 100, 5)])
    paths, _, reports, heights, res = run(job)
    assert reports[0].passed, reports[0].errors
    assert reports[0].z_min == pytest.approx(-5.0, abs=0.05)
    assert heights[int(100 / res), int(102.5 / res)] == pytest.approx(-2.5, abs=0.1)


def test_vcarve_text_matches_letter_shape(job, run):
    vectors = text_vectors("RA8", ARIAL, 40.0, x=40, y=60)
    add(job, "vcarve", "vb-60", vectors)
    paths, _, reports, heights, res = run(job, resolution=0.1)
    assert reports[0].passed, reports[0].errors
    target = mask_of(region(vectors), heights, res)
    carved = heights < -0.05
    iou = (target & carved).sum() / (target | carved).sum()
    assert iou > 0.93


def test_vcarve_stays_inside_shape(job, run):
    vectors = text_vectors("S", ARIAL, 50.0, x=40, y=60)
    add(job, "vcarve", "vb-90", vectors)
    _, _, _, heights, res = run(job, resolution=0.1)
    outer = region(vectors).buffer(0.25)
    assert heights[~mask_of(outer, heights, res)].min() > -0.2


def test_vcarve_flat_bottom_with_clearing_tool(job, run):
    add(
        job,
        "vcarve",
        "vb-90",
        [rectangle(50, 50, 120, 60)],
        flat_depth=5.0,
        clear_tool_id="em-250",
    )
    paths, files, reports, heights, res = run(job)
    assert len(files) == 2
    assert all(r.passed for r in reports), [r.errors for r in reports]
    assert heights[int(80 / res), int(110 / res)] == pytest.approx(-5.0, abs=0.01)
    assert heights[int(80 / res), int(52.5 / res)] == pytest.approx(-2.5, abs=0.1)
    floor = heights[int(56 / res) : int(104 / res), int(56 / res) : int(164 / res)]
    assert floor.max() == pytest.approx(-5.0, abs=0.02)


def test_vcarve_without_clearing_tool_warns(job):
    from downcut.toolpaths import build_all

    add(job, "vcarve", "vb-90", [rectangle(50, 50, 120, 60)], flat_depth=5.0)
    paths = build_all(job)
    assert any("clearing tool" in w for w in paths[0].warnings)


def test_vcarve_respects_bit_size(job, run):
    add(job, "vcarve", "vb-90", [rectangle(50, 50, 120, 60)], clear_tool_id="em-250")
    _, _, reports, _, _ = run(job)
    assert all(r.z_min >= -6.36 for r in reports)


def test_vcarve_multi_pass_steps_down_to_final_depth(job, run):
    add(job, "vcarve", "vb-60", [rectangle(60, 60, 120, 10)])
    paths, _, reports, heights, res = run(job)
    final = -5 / math.tan(math.radians(30))
    middle = [round(m[3], 3) for m in paths[0].moves if m[0] == 1 and abs(m[2] - 65) < 0.01]
    assert reports[0].passed, reports[0].errors
    assert sorted(set(middle))[0] == pytest.approx(final, abs=0.05)
    assert len({z for z in middle if z < -1}) >= 3
    assert heights[int(65 / res), int(120 / res)] == pytest.approx(final, abs=0.1)




def test_flat_bottom_text_keeps_letter_shape(job, run):
    vectors = text_vectors("HI", ARIAL, 60.0, x=40, y=60)
    add(job, "vcarve", "vb-90", vectors, flat_depth=3.0, clear_tool_id="em-125")
    paths, files, reports, heights, res = run(job, resolution=0.1)
    assert len(files) == 2
    assert all(r.passed for r in reports), [r.errors for r in reports]
    assert all(r.z_min >= -3.01 for r in reports)
    target = mask_of(region(vectors), heights, res)
    carved = heights < -0.05
    assert (target & carved).sum() / (target | carved).sum() > 0.95
    assert heights[~mask_of(region(vectors).buffer(0.3), heights, res)].min() > -0.2


def test_cut_deeper_than_flutes_warns(job):
    from downcut.toolpaths import build_all

    add(job, "profile", "em-125", [rectangle(50, 50, 100, 60)], side="outside", depth=19.0)
    paths = build_all(job)
    assert any("flute length" in w for w in paths[0].warnings)


def test_cut_within_flutes_does_not_warn(job):
    from downcut.toolpaths import build_all

    add(job, "profile", "em-250", [rectangle(50, 50, 100, 60)], side="outside", depth=19.0)
    assert build_all(job)[0].warnings == []
