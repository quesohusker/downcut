import numpy as np
import pytest

from kerfline.gcode import POSTS, export
from kerfline.importers import import_dxf, import_svg, join_open
from kerfline.model import MM_PER_IN, Material, Operation, Vector
from kerfline.shapes import circle, rectangle
from kerfline.toolpaths import build_all
from kerfline.verify import Limits, check, limits_for

LIMITS = Limits(0, 100, 0, 100, 0, -10, 5)
GOOD = """G90 G94 G17
G21
G0 Z5
M3 S18000
G0 X10 Y10
G1 Z-2 F300
G1 X20 F600
G0 Z5
M5
M30
"""


def one_profile(job):
    shape = rectangle(50, 50, 100, 60)
    job.vectors.append(shape)
    job.operations.append(Operation("profile", "em-250", [shape.id], {"side": "outside", "depth": 6.0}))
    return build_all(job)


def test_checker_accepts_good_program():
    report = check(GOOD, LIMITS)
    assert report.passed, report.errors
    assert report.cut_length_mm == pytest.approx(17.0)
    assert report.seconds > 0


@pytest.mark.parametrize(
    "bad,expected",
    [
        (GOOD.replace("G0 X10 Y10", "G1 Z-1 F300\nG0 X10 Y10"), "Rapid XY move below safe Z"),
        (GOOD.replace("G1 Z-2 F300", "G0 Z-2\nG1 Z-2 F300"), "Rapid descent into material"),
        (GOOD.replace("G1 Z-2 F300", "G1 Z-11 F300"), "below the allowed floor"),
        (GOOD.replace("G1 X20 F600", "G1 X120 F600"), "outside the material"),
        (GOOD.replace("M3 S18000\n", ""), "spindle off"),
        (GOOD.replace("M5\n", ""), "Spindle is still on"),
        (GOOD.replace("M30\n", ""), "no end code"),
        (GOOD.replace("G21\n", ""), "before units"),
        (GOOD.replace("G1 Z-2 F300", "G1 Z-2").replace(" F600", ""), "no feed rate"),
        (GOOD.replace("G0 Z5\nM5", "M5"), "below safe Z"),
        (GOOD.replace("G90 G94 G17", "G91"), "relative mode"),
    ],
)
def test_checker_rejects_bad_programs(bad, expected):
    report = check(bad, LIMITS)
    assert not report.passed
    assert any(expected in e for e in report.errors), report.errors


def test_checker_reads_inches():
    text = GOOD.replace("G21", "G20").replace("Z5", "Z0.2").replace("Z-2", "Z-0.1")
    text = text.replace("X10 Y10", "X1 Y1").replace("X20", "X2")
    report = check(text, LIMITS)
    assert report.passed, report.errors
    assert report.z_min == pytest.approx(-2.54)
    assert report.x_range[1] == pytest.approx(50.8)


@pytest.mark.parametrize("post", sorted(POSTS))
def test_all_posts_pass_the_checker(job, post):
    paths = one_profile(job)
    item = export(job, paths, post)[0]
    assert item.name.endswith(POSTS[post].extension)
    report = check(item.text, limits_for(job, job.tool("em-250")))
    assert report.passed, report.errors


def test_inch_output_is_scaled(job):
    job.units = "in"
    paths = one_profile(job)
    text = export(job, paths, "grbl")[0].text
    assert "G20" in text
    report = check(text, limits_for(job, job.tool("em-250")))
    assert report.passed, report.errors
    assert report.z_min == pytest.approx(-6.0, abs=1e-3)
    assert report.x_range[1] == pytest.approx(153.175, abs=1e-3)


def test_bottom_zero_shifts_z(job):
    job.z_zero = "bottom"
    paths = one_profile(job)
    text = export(job, paths, "grbl")[0].text
    report = check(text, limits_for(job, job.tool("em-250")))
    assert report.passed, report.errors
    assert report.z_min == pytest.approx(13.0, abs=1e-3)


def test_center_origin_shifts_xy(job):
    job.xy_origin = "center"
    paths = one_profile(job)
    report = check(export(job, paths, "grbl")[0].text, limits_for(job, job.tool("em-250")))
    assert report.passed, report.errors
    assert report.x_range[0] == pytest.approx(50 - 3.175 - 150, abs=1e-3)


def test_each_tool_gets_its_own_file(job):
    a, b = rectangle(20, 20, 50, 50), circle(150, 100, 2)
    job.vectors.extend([a, b])
    job.operations.append(Operation("pocket", "em-250", [a.id], {"depth": 3.0}))
    job.operations.append(Operation("drill", "dr-125", [b.id], {"depth": 3.0}))
    files = export(job, build_all(job), "grbl")
    assert len(files) == 2
    assert "M3 S18000" in files[0].text and "M3 S12000" in files[1].text


def test_cut_order_survives_a_tool_change(job):
    pocket, letter, edge = rectangle(20, 20, 50, 50), rectangle(100, 40, 60, 8), rectangle(10, 10, 200, 120)
    job.vectors.extend([pocket, letter, edge])
    job.operations.append(Operation("pocket", "em-250", [pocket.id], {"depth": 3.0}))
    job.operations.append(Operation("vcarve", "vb-90", [letter.id], {}))
    job.operations.append(Operation("profile", "em-250", [edge.id], {"side": "outside", "depth": 19.0}))
    files = export(job, build_all(job), "grbl")
    assert [f.tool_id for f in files] == ["em-250", "vb-90", "em-250"]
    assert [f.name[:5] for f in files] == ["kerfl", "kerfl", "kerfl"]
    assert len({f.name for f in files}) == 3


def test_same_tool_in_a_row_shares_one_file(job):
    a, b = rectangle(20, 20, 50, 50), rectangle(100, 20, 50, 50)
    job.vectors.extend([a, b])
    job.operations.append(Operation("pocket", "em-250", [a.id], {"depth": 3.0}))
    job.operations.append(Operation("pocket", "em-250", [b.id], {"depth": 3.0}))
    assert len(export(job, build_all(job), "grbl")) == 1


def test_svg_import_applies_transform_flips_y_and_scales():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
    <g transform="translate(10,20)"><rect x="0" y="0" width="50" height="30"/></g>
    <rect x="100" y="70" width="10" height="10"/>
    </svg>"""
    vectors = import_svg(svg)
    assert len(vectors) == 2
    points = np.array(vectors[0].points)
    assert np.ptp(points[:, 0]) == pytest.approx(50.0, abs=0.01)
    assert np.ptp(points[:, 1]) == pytest.approx(30.0, abs=0.01)
    assert points[:, 1].max() == pytest.approx(60.0, abs=0.01)
    assert vectors[0].closed


def test_svg_curves_flatten_to_true_circle():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">
    <circle cx="50" cy="50" r="40"/></svg>"""
    points = np.array(import_svg(svg)[0].points)
    radius = np.hypot(points[:, 0] - 40, points[:, 1] - 40)
    assert radius.min() == pytest.approx(40.0, abs=0.02)
    assert radius.max() == pytest.approx(40.0, abs=0.02)


def test_dxf_import_reads_circle_and_joins_lines():
    import io

    import ezdxf

    document = ezdxf.new(units=4)
    space = document.modelspace()
    space.add_circle((50, 50), 10)
    for start, end in [((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))]:
        space.add_line(start, end)
    stream = io.StringIO()
    document.write(stream)
    vectors = import_dxf(stream.getvalue())
    assert len(vectors) == 2
    assert all(v.closed for v in vectors)


def test_dxf_inch_files_convert_to_mm():
    import io

    import ezdxf

    document = ezdxf.new(units=1)
    document.modelspace().add_circle((5, 5), 1)
    stream = io.StringIO()
    document.write(stream)
    points = np.array(import_dxf(stream.getvalue())[0].points)
    assert np.ptp(points[:, 0]) == pytest.approx(2 * MM_PER_IN, abs=0.05)


def test_join_open_leaves_separate_lines_apart():
    a = Vector(points=[[0, 0], [10, 0]], closed=False)
    b = Vector(points=[[50, 50], [60, 50]], closed=False)
    assert len(join_open([a, b])) == 2


def test_material_dataclass_round_trip():
    assert Material(1, 2, 3).thickness == 3
