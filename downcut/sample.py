from __future__ import annotations

from .edit import offset
from .geometry import bounds
from .model import MM_PER_IN, Job, Material, Operation, default_tools
from .shapes import circle, rectangle, star
from .textvec import list_fonts, text_vectors

IN = MM_PER_IN


def _font(names: list[str]) -> dict:
    fonts = {row["name"]: row for row in list_fonts()}
    for name in names:
        if name in fonts:
            return fonts[name]
    return next(iter(fonts.values()))


def _center_x(vectors, center: float) -> None:
    x_low, _, x_high, _ = bounds(vectors)
    shift = center - (x_low + x_high) / 2
    for vector in vectors:
        vector.points = [[x + shift, y] for x, y in vector.points]


def sign_job() -> Job:
    width, height, thickness = 20 * IN, 12 * IN, 0.75 * IN
    job = Job(
        material=Material(width, height, thickness),
        units="in",
        spoilboard_allowance=0.02 * IN,
        tools=default_tools(),
    )
    center = width / 2

    edge = rectangle(1 * IN, 1 * IN, 18 * IN, 10 * IN, radius=1 * IN)
    band_outer = rectangle(1.6 * IN, 1.6 * IN, 16.8 * IN, 8.8 * IN, radius=0.7 * IN)
    band_inner = offset([band_outer], -0.16 * IN, sharp=False)

    font = _font(["Georgia Bold", "Times New Roman Bold", "Arial Bold"])
    title = text_vectors("DOWNCUT", font["path"], 1.6 * IN, x=center, y=6.0 * IN, font_index=font["index"], tracking=4.0)
    _center_x(title, center)
    small = _font(["Arial Bold", "Georgia Bold"])
    subtitle = text_vectors("WOOD SIGNS", small["path"], 0.7 * IN, x=center, y=4.3 * IN, font_index=small["index"], tracking=9.0)
    _center_x(subtitle, center)

    stars = [star(3.6 * IN, 4.65 * IN, 0.5 * IN, 0.2 * IN, 5), star(16.4 * IN, 4.65 * IN, 0.5 * IN, 0.2 * IN, 5)]
    holes = [circle(10 * IN, 10.1 * IN, 0.09 * IN), circle(10 * IN, 1.9 * IN, 0.09 * IN)]

    for vector in title:
        vector.group = "title"
    for vector in subtitle:
        vector.group = "subtitle"
    band_outer.group = "band"
    for vector in band_inner:
        vector.group = "band"

    job.vectors = [edge, band_outer, *band_inner, *title, *subtitle, *stars, *holes]
    carve = [band_outer, *band_inner, *title, *subtitle, *stars]
    job.operations = [
        Operation("drill", "dr-125", [v.id for v in holes], {"depth": 0.76 * IN, "peck": 0.25 * IN}, "Mount holes"),
        Operation("vcarve", "vb-60", [v.id for v in carve], {}, "Lettering and border"),
        Operation(
            "profile",
            "em-250",
            [edge.id],
            {
                "side": "outside",
                "depth": 0.76 * IN,
                "tab_count": 6,
                "tab_length": 0.4 * IN,
                "tab_thickness": 0.12 * IN,
                "ramp_length": 1.0 * IN,
            },
            "Cut out",
        ),
    ]
    return job
