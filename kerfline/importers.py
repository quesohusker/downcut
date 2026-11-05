from __future__ import annotations

import io
import math

import ezdxf
import numpy as np
from ezdxf.path import make_path
from svgelements import SVG, Arc, Close, CubicBezier, Line, Move, Path, QuadraticBezier, Shape

from .epsimport import import_eps
from .geometry import join_open
from .model import MM_PER_IN, Vector
from .pdfimport import import_pdf

PX_MM = MM_PER_IN / 96.0


def _curve_points(segment) -> list[tuple[float, float]]:
    length = float(segment.length())
    count = int(np.clip(math.ceil(length / 0.5), 12, 160))
    points = [segment.point(t) for t in np.linspace(0, 1, count + 1)[1:]]
    return [(p.x, p.y) for p in points]


def _finish(paths: list[tuple[list[tuple[float, float]], bool]]) -> list[Vector]:
    out = []
    for points, closed in paths:
        cleaned = [points[0]]
        for point in points[1:]:
            if math.dist(point, cleaned[-1]) > 1e-7:
                cleaned.append(point)
        if closed and len(cleaned) > 1 and math.dist(cleaned[0], cleaned[-1]) < 1e-7:
            cleaned.pop()
        minimum = 3 if closed else 2
        if len(cleaned) >= minimum:
            out.append(Vector(points=[[x, y] for x, y in cleaned], closed=closed))
    return out


def _to_lower_left(vectors: list[Vector], flip: bool) -> list[Vector]:
    if not vectors:
        return vectors
    points = np.array([p for v in vectors for p in v.points])
    x_low, y_low = points.min(axis=0)
    y_high = points[:, 1].max()
    for vector in vectors:
        rows = np.asarray(vector.points, dtype=float)
        rows[:, 0] -= x_low
        rows[:, 1] = y_high - rows[:, 1] if flip else rows[:, 1] - y_low
        vector.points = rows.tolist()
    return vectors


def import_svg(text: str) -> list[Vector]:
    drawing = SVG.parse(io.StringIO(text), reify=True, ppi=96.0)
    paths: list[tuple[list[tuple[float, float]], bool]] = []
    for element in drawing.elements():
        if not isinstance(element, Shape):
            continue
        current: list[tuple[float, float]] = []

        def flush(closed: bool) -> None:
            nonlocal current
            if len(current) > 1:
                paths.append((current, closed))
            current = []

        for segment in Path(element):
            if isinstance(segment, Move):
                flush(False)
                current = [(segment.end.x, segment.end.y)]
            elif isinstance(segment, Close):
                flush(True)
            elif isinstance(segment, Line):
                current.append((segment.end.x, segment.end.y))
            elif isinstance(segment, (CubicBezier, QuadraticBezier, Arc)):
                current.extend(_curve_points(segment))
        flush(False)
    scaled = [([(x * PX_MM, y * PX_MM) for x, y in pts], closed) for pts, closed in paths]
    return _to_lower_left(_finish(scaled), flip=True)


def import_dxf(text: str, fallback_units: str = "mm") -> list[Vector]:
    document = ezdxf.read(io.StringIO(text))
    code = document.header.get("$INSUNITS", 0)
    if code == 1:
        scale = MM_PER_IN
    elif code == 4:
        scale = 1.0
    elif code == 5:
        scale = 10.0
    elif code == 6:
        scale = 1000.0
    else:
        scale = MM_PER_IN if fallback_units == "in" else 1.0
    paths: list[tuple[list[tuple[float, float]], bool]] = []
    supported = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE"}
    for entity in document.modelspace():
        if entity.dxftype() not in supported:
            continue
        path = make_path(entity)
        points = [(p.x * scale, p.y * scale) for p in path.flattening(0.01 / scale, segments=8)]
        if len(points) < 2:
            continue
        paths.append((points, math.dist(points[0], points[-1]) < 1e-6))
    return _to_lower_left(join_open(_finish(paths)), flip=False)


def import_outline(paths: list[tuple[list[tuple[float, float]], bool]]) -> list[Vector]:
    return _to_lower_left(join_open(_finish(paths)), flip=False)


def import_binary(name: str, data: bytes, fallback_units: str = "mm") -> list[Vector]:
    lowered = name.lower()
    if lowered.endswith(".svg"):
        return import_svg(data.decode("utf-8", "replace"))
    if lowered.endswith(".dxf"):
        return import_dxf(data.decode("utf-8", "replace"), fallback_units)
    if lowered.endswith(".pdf"):
        return import_outline(import_pdf(data))
    if lowered.endswith(".ai"):
        if b"%PDF" not in data[:1024]:
            raise ValueError("That AI file is an old PostScript version. Save it as PDF compatible or as SVG.")
        return import_outline(import_pdf(data))
    if lowered.endswith((".eps", ".ps")):
        return import_outline(import_eps(data))
    raise ValueError("Only SVG, DXF, PDF, AI, and EPS files are supported.")


def import_file(name: str, text: str, fallback_units: str = "mm") -> list[Vector]:
    return import_binary(name, text.encode("utf-8"), fallback_units)
