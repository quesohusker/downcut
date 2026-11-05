import math

import numpy as np
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from shapely.geometry import Point, Polygon

from kerfline import edit, exporters, importers, layout, nesting, shapes, textvec, validate
from kerfline.geometry import line_of, polygon_from, region
from kerfline.model import Vector
from support import ARIAL



def square(x=0.0, y=0.0, size=50.0):
    return shapes.rectangle(x, y, size, size)


def test_three_point_arc_runs_through_the_middle_point():
    arc = shapes.three_point_arc((0, 0), (10, 10), (20, 0))
    assert arc.points[0] == pytest.approx([0, 0], abs=1e-6)
    assert arc.points[-1] == pytest.approx([20, 0], abs=1e-6)
    assert all(math.hypot(x - 10, y) == pytest.approx(10, abs=1e-6) for x, y in arc.points)
    assert max(y for _, y in arc.points) == pytest.approx(10, abs=0.02)


def test_curve_passes_through_every_anchor_and_keeps_them():
    anchors = [[0, 0], [20, 15], [40, 0], [60, 15]]
    curve = shapes.curve(anchors)
    line = line_of(curve)
    assert all(line.distance(Point(a)) < 1e-6 for a in anchors)
    assert curve.anchors == anchors


def test_join_makes_a_closed_vector_and_keeps_the_layer():
    parts = [
        Vector([[0, 0], [10, 0]], False, layer="A"),
        Vector([[10, 0], [10, 10]], False, layer="A"),
        Vector([[10, 10], [0, 0.004]], False, layer="A"),
    ]
    joined = edit.join(parts, 0.01)
    assert len(joined) == 1 and joined[0].closed and joined[0].layer == "A"


def test_simplify_and_smooth_keep_the_vector_id():
    circle = shapes.circle(0, 0, 50)
    before = len(circle.points)
    keep = circle.id
    edit.simplify([circle], 0.5)
    assert len(circle.points) < before and circle.id == keep
    rough = Vector([[0, 0], [10, 10], [20, 0], [30, 10]], False)
    edit.smooth([rough], 0.01, 2)
    assert rough.points[0] == [0, 0] and rough.points[-1] == [30, 10]
    assert len(rough.points) > 4


def test_remove_duplicates_finds_a_reversed_copy_and_repeat_points():
    a = square()
    b = Vector(list(reversed(a.points)), True)
    c = Vector([[0, 0], [0, 0], [5, 5]], False)
    kept, removed = edit.remove_duplicates([a, b, c])
    assert [v.id for v in kept] == [a.id, c.id]
    assert removed == [b.id]
    assert c.points == [[0, 0], [5, 5]]


def test_fillet_rounds_corners_at_the_exact_radius():
    plate = square()
    edit.fillet([plate], 5.0)
    shape = polygon_from(plate.points)
    expected = 50 * 50 - (4 - math.pi) * 25
    assert shape.area == pytest.approx(expected, rel=1e-3)


def test_fillet_limits_radius_to_half_of_a_short_edge():
    plate = shapes.rectangle(0, 0, 100, 10)
    edit.fillet([plate], 50.0)
    assert polygon_from(plate.points).is_valid


def test_dogbone_lets_the_cutter_reach_every_corner():
    tool = 6.0
    pocket = square(10, 10, 30)
    result = edit.bone_corners([pocket], tool)
    shape = region(result).buffer(0.01)
    reach = 3.0 / math.sqrt(2)
    for x, y, dx, dy in [(10, 10, 1, 1), (40, 10, -1, 1), (40, 40, -1, -1), (10, 40, 1, -1)]:
        disc = Point(x + dx * reach, y + dy * reach).buffer(3.0)
        assert shape.covers(disc)
        assert disc.distance(Point(x, y)) < 0.01


def test_tbone_notches_only_the_short_wall():
    slot = shapes.rectangle(0, 0, 60, 20)
    shape = region(edit.bone_corners([slot], 6.0, "tbone"))
    x0, y0, x1, y1 = shape.bounds
    assert (y0, y1) == pytest.approx((0, 20), abs=1e-6)
    assert x0 == pytest.approx(-3, abs=0.01) and x1 == pytest.approx(63, abs=0.01)


def test_outside_dogbone_cuts_into_the_part_at_reflex_corners():
    points = [[0, 0], [40, 0], [40, 20], [20, 20], [20, 40], [0, 40]]
    part = Vector(points, True)
    shape = region(edit.bone_corners([part], 6.0, "dog", "outside"))
    assert shape.area < polygon_from(points).area
    assert shape.area > polygon_from(points).area - 30


def test_trim_removes_only_the_clicked_span():
    target = Vector([[0, 0], [100, 0]], False)
    a = Vector([[30, -10], [30, 10]], False)
    b = Vector([[70, -10], [70, 10]], False)
    pieces = edit.trim_at(target, [a, b], 50, 1)
    assert len(pieces) == 2
    assert line_of(pieces[0]).length + line_of(pieces[1]).length == pytest.approx(60)


def test_trim_on_a_closed_vector_leaves_one_open_piece():
    ring = square(0, 0, 40)
    a = Vector([[-5, 20], [45, 20]], False)
    pieces = edit.trim_at(ring, [a], 40, 10)
    assert len(pieces) == 1 and not pieces[0].closed
    assert line_of(pieces[0]).length == pytest.approx(160 - 40 - 40 + 40, abs=40.1)


def test_extend_reaches_the_next_vector():
    target = Vector([[0, 0], [10, 0]], False)
    wall = Vector([[50, -10], [50, 10]], False)
    edit.extend_to(target, [wall], 12, 0)
    assert target.points[-1] == pytest.approx([50, 0], abs=1e-3)


def test_trim_to_boundary_clips_lines_and_shapes():
    bound = square(0, 0, 50)
    line = Vector([[-10, 25], [60, 25]], False)
    big = shapes.rectangle(25, 25, 50, 50)
    result = edit.trim_to_boundary([line, big], bound)
    assert line_of(result[0]).length == pytest.approx(50)
    assert polygon_from(result[1].points).area == pytest.approx(25 * 25)


def test_validator_names_each_problem():
    bow = Vector([[0, 0], [10, 10], [10, 0], [0, 10]], True)
    open_one = Vector([[20, 0], [30, 0]], False)
    a = square(40, 0, 10)
    b = Vector(list(a.points), True)
    c = shapes.rectangle(45, 5, 10, 10)
    d = Vector([[70, 0], [70, 0], [80, 0]], False)
    kinds = {row["kind"] for row in validate.validate([bow, open_one, a, b, c, d])}
    assert {"self", "open", "duplicate", "cross", "zero"} <= kinds


def test_validator_passes_clean_art():
    assert validate.validate([square(), square(60)]) == []


def test_grid_array_counts_and_spacing():
    copies = layout.grid_array([square(size=10)], 3, 2, 20, 30)
    assert len(copies) == 5
    x = sorted({min(p[0] for p in v.points) for v in copies})
    assert x == pytest.approx([0, 20, 40])


def test_circular_array_keeps_radius_and_rotates():
    seed = shapes.rectangle(95, -5, 10, 10)
    copies = layout.circular_array([seed], 4, 0, 0)
    assert len(copies) == 3
    for vector in copies:
        center = polygon_from(vector.points).centroid
        assert math.hypot(center.x, center.y) == pytest.approx(100, abs=1e-6)


def test_copies_along_a_path_follow_it():
    path = Vector([[0, 0], [100, 0]], False)
    copies = layout.along_path([square(-5, -5, 10)], path, 5)
    assert len(copies) == 5
    x = sorted(polygon_from(v.points).centroid.x for v in copies)
    assert x == pytest.approx([0, 25, 50, 75, 100], abs=1e-6)


def test_nesting_keeps_every_part_apart_and_on_the_sheet():
    parts = [shapes.rectangle(0, 0, 80, 50) for _ in range(6)]
    parts += [shapes.star(0, 0, 30, 12, 5) for _ in range(4)]
    result = nesting.nest(parts, 300, 200, gap=6.35)
    assert result["unplaced"] == []
    shapes_out = result["shapes"]
    sheet = Polygon([(0, 0), (300, 0), (300, 200), (0, 200)])
    for index, shape in enumerate(shapes_out):
        assert sheet.buffer(0.01).covers(shape)
        for other in shapes_out[index + 1 :]:
            assert shape.distance(other) >= 6.35 - 1e-6


def test_nesting_uses_a_hole_for_a_small_part():
    frame = [shapes.rectangle(0, 0, 100, 100), shapes.rectangle(20, 20, 60, 60)]
    frame[0].group = frame[1].group = "frame"
    small = shapes.rectangle(200, 0, 30, 30)
    result = nesting.nest(frame + [small], 110, 110, gap=3)
    assert result["unplaced"] == []
    box = polygon_from(small.points)
    hole = polygon_from(frame[1].points)
    assert hole.contains(box)


def test_nesting_avoids_keepouts_and_reports_leftovers():
    clamp = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    parts = [shapes.rectangle(0, 0, 40, 40)]
    result = nesting.nest(parts, 150, 100, gap=2, keepouts=[clamp])
    assert polygon_from(parts[0].points).distance(clamp) >= 2 - 1e-6
    too_big = shapes.rectangle(0, 0, 500, 500)
    result = nesting.nest([too_big], 150, 100, gap=2)
    assert result["unplaced"] == [too_big.id]


def test_svg_export_round_trips_at_full_scale():
    plate = shapes.rectangle(10, 20, 80, 40)
    ring = shapes.circle(50, 40, 10)
    text = exporters.to_svg([plate, ring], 200, 100)
    back = importers.import_svg(text)
    assert len(back) == 2
    shape = max((polygon_from(v.points) for v in back), key=lambda s: s.area)
    assert shape.bounds[2] - shape.bounds[0] == pytest.approx(80, abs=0.01)


def test_dxf_export_round_trips_at_full_scale():
    plate = shapes.rectangle(10, 20, 80, 40)
    line = Vector([[0, 0], [30, 0]], False)
    back = importers.import_dxf(exporters.to_dxf([plate, line]))
    shape = max((v for v in back if v.closed), key=lambda v: polygon_from(v.points).area)
    assert polygon_from(shape.points).area == pytest.approx(3200, abs=0.1)
    assert any(not v.closed for v in back)


def pdf_bytes(content: bytes) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    import io

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_import_reads_paths_at_scale():
    content = b"72 72 144 72 re f\nq 2 0 0 2 0 0 cm 150 150 m 186 150 l 186 186 l h S Q\n0 0 m 1 1 l n\n"
    vectors = importers.import_binary("a.pdf", pdf_bytes(content))
    assert len(vectors) == 2
    areas = sorted(polygon_from(v.points).area for v in vectors)
    assert areas[1] == pytest.approx((2 * 25.4) * 25.4, rel=1e-3)
    assert areas[0] == pytest.approx(0.5 * (72 * 25.4 / 72) ** 2, rel=1e-3)


def test_pdf_import_follows_curves():
    k = 0.5523 * 72
    content = (
        f"72 0 m {72} {k} {k} 72 0 72 c {-k} 72 -72 {k} -72 0 c "
        f"-72 {-k} {-k} -72 0 -72 c {k} -72 72 {-k} 72 0 c h f"
    ).encode()
    vectors = importers.import_binary("c.pdf", pdf_bytes(content))
    assert polygon_from(vectors[0].points).area == pytest.approx(math.pi * 25.4**2, rel=2e-3)


def test_pdf_with_only_text_says_so():
    with pytest.raises(ValueError, match="outlines"):
        importers.import_binary("t.pdf", pdf_bytes(b"BT /F1 12 Tf (Hi) Tj ET"))


def test_ai_file_is_read_as_pdf_and_old_ones_are_refused():
    vectors = importers.import_binary("a.ai", pdf_bytes(b"0 0 72 72 re f"))
    assert len(vectors) == 1
    with pytest.raises(ValueError, match="PostScript"):
        importers.import_binary("old.ai", b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator 8")


def test_eps_import_runs_macros_and_transforms():
    eps = b"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 200 200
/m /moveto load def
/l {lineto} bind def
/box { 3 dict begin 0 0 m 72 0 l 72 72 l 0 72 l closepath } def
gsave 2 2 scale box fill grestore
100 100 translate 0 0 36 0 360 arc closepath stroke
showpage
"""
    vectors = importers.import_binary("a.eps", eps)
    areas = sorted(polygon_from(v.points).area for v in vectors)
    assert areas[1] == pytest.approx((2 * 25.4) ** 2, rel=1e-3)
    assert areas[0] == pytest.approx(math.pi * 12.7**2, rel=5e-3)


def test_eps_with_nothing_readable_says_so():
    with pytest.raises(ValueError, match="PDF or SVG"):
        importers.import_binary("a.eps", b"%!PS\n/foo { bar } def\nshowpage\n")


def test_stroke_text_is_open_lines_at_cap_height():
    vectors = textvec.stroke_vectors("H", "futural", 25.0, 10, 10)
    assert vectors and not any(v.closed for v in vectors)
    ys = [p[1] for v in vectors for p in v.points]
    assert min(ys) == pytest.approx(10, abs=0.01)
    assert max(ys) == pytest.approx(35, abs=0.01)


def test_stroke_text_keeps_loops_closed_for_o():
    vectors = textvec.stroke_vectors("O", "futural", 25.0)
    assert len(vectors) == 1 and vectors[0].closed


def test_manual_kerning_moves_following_letters_only():
    base = textvec.text_vectors("AB", ARIAL, 25.0)
    kerned = textvec.text_vectors("AB", ARIAL, 25.0, kerns={"0": 10.0})
    assert base[0].points == kerned[0].points
    assert max(p[0] for p in kerned[-1].points) == pytest.approx(max(p[0] for p in base[-1].points) + 10, abs=1e-6)


def test_stroke_kerning_shifts_the_next_letter():
    base = textvec.stroke_vectors("HH", "futural", 25.0)
    kerned = textvec.stroke_vectors("HH", "futural", 25.0, kerns={"0": 5.0})
    assert max(p[0] for v in kerned for p in v.points) == pytest.approx(
        max(p[0] for v in base for p in v.points) + 5, abs=1e-6
    )


def test_text_follows_a_circle():
    path = shapes.circle(100, 100, 80)
    vectors, width = textvec.text_on_path("ROUND TEXT", path, 12.0, font_path=ARIAL)
    assert width < line_of(path).length
    for vector in vectors:
        center = polygon_from(vector.points).centroid
        assert 79 < math.hypot(center.x - 100, center.y - 100) < 93
    top = max(polygon_from(v.points).centroid.y for v in vectors)
    assert top > 180


def test_flipped_text_on_a_circle_sits_at_the_bottom_inside():
    path = shapes.circle(100, 100, 80)
    vectors, _ = textvec.text_on_path("ROUND", path, 12.0, font_path=ARIAL, flip=True)
    for vector in vectors:
        center = polygon_from(vector.points).centroid
        assert center.y < 100
        assert 67 < math.hypot(center.x - 100, center.y - 100) < 81


def test_flipped_text_sits_on_the_other_side_of_a_line():
    path = Vector([[0, 0], [200, 0]], False)
    above, _ = textvec.text_on_path("HI", path, 12.0, font_path=ARIAL)
    below, _ = textvec.text_on_path("HI", path, 12.0, font_path=ARIAL, flip=True)
    assert min(p[1] for v in above for p in v.points) > -1
    assert max(p[1] for v in below for p in v.points) < 1
