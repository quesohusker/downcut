from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import uharfbuzz as hb
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTCollection, TTFont
from HersheyFonts import HersheyFonts
from shapely.geometry import LineString
from shapely.ops import unary_union

from .geometry import polygon_from, polygons, vectors_from_geometry
from .model import Vector

STROKE_FONTS = {
    "Single Line Sans": "futural",
    "Single Line Roman": "rowmans",
    "Single Line Script": "scripts",
    "Single Line Cursive": "cursive",
}

FONT_FOLDERS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
]


class _FlattenPen(BasePen):
    def __init__(self, glyph_set, steps: int = 16):
        super().__init__(glyph_set)
        self.contours: list[list[tuple[float, float]]] = []
        self.current: list[tuple[float, float]] = []
        self.steps = steps

    def _moveTo(self, pt):
        self.current = [pt]

    def _lineTo(self, pt):
        self.current.append(pt)

    def _curveToOne(self, pt1, pt2, pt3):
        p0 = np.array(self.current[-1], dtype=float)
        p1, p2, p3 = (np.array(p, dtype=float) for p in (pt1, pt2, pt3))
        for t in np.linspace(0, 1, self.steps + 1)[1:]:
            u = 1 - t
            point = u**3 * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t**3 * p3
            self.current.append((float(point[0]), float(point[1])))

    def _qCurveToOne(self, pt1, pt2):
        p0 = np.array(self.current[-1], dtype=float)
        p1, p2 = (np.array(p, dtype=float) for p in (pt1, pt2))
        for t in np.linspace(0, 1, self.steps + 1)[1:]:
            u = 1 - t
            point = u * u * p0 + 2 * u * t * p1 + t * t * p2
            self.current.append((float(point[0]), float(point[1])))

    def _closePath(self):
        if len(self.current) > 2:
            self.contours.append(self.current)
        self.current = []

    def _endPath(self):
        self.current = []


@lru_cache(maxsize=1)
def list_fonts() -> tuple[dict, ...]:
    found: dict[str, dict] = {}
    for folder in FONT_FOLDERS:
        try:
            paths = sorted(folder.rglob("*"))
        except OSError:
            continue
        for path in paths:
            suffix = path.suffix.lower()
            if suffix not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                if suffix == ".ttc":
                    collection = TTCollection(str(path), lazy=True)
                    for index, font in enumerate(collection.fonts):
                        name = font["name"].getBestFullName()
                        if name and not name.startswith("."):
                            found.setdefault(name, {"name": name, "path": str(path), "index": index})
                else:
                    font = TTFont(str(path), lazy=True)
                    name = font["name"].getBestFullName()
                    if name and not name.startswith("."):
                        found.setdefault(name, {"name": name, "path": str(path), "index": 0})
            except Exception:
                continue
    return tuple(sorted(found.values(), key=lambda row: row["name"].lower()))


def _cap_height(font: TTFont, glyph_set) -> float:
    table = font["OS/2"] if "OS/2" in font else None
    value = getattr(table, "sCapHeight", 0) if table else 0
    if value:
        return float(value)
    cmap = font.getBestCmap()
    if ord("H") in cmap:
        pen = _FlattenPen(glyph_set)
        glyph_set[cmap[ord("H")]].draw(pen)
        if pen.contours:
            ys = [p[1] for contour in pen.contours for p in contour]
            return float(max(ys) - min(ys))
    return float(font["head"].unitsPerEm) * 0.7


def _glyph_shape(contours):
    shapes = [polygon_from(contour) for contour in contours]
    signed = []
    for contour, shape in zip(contours, shapes):
        pts = np.asarray(contour, dtype=float)
        area = 0.5 * float(np.sum(pts[:-1, 0] * pts[1:, 1] - pts[1:, 0] * pts[:-1, 1]))
        area += 0.5 * float(pts[-1, 0] * pts[0, 1] - pts[0, 0] * pts[-1, 1])
        signed.append((area, shape))
    if not signed:
        return None
    biggest = max(signed, key=lambda row: abs(row[0]))[0]
    outers = [s for a, s in signed if a * biggest > 0 and not s.is_empty]
    holes = [s for a, s in signed if a * biggest < 0 and not s.is_empty]
    shape = unary_union(outers)
    if holes:
        shape = shape.difference(unary_union(holes))
    return shape


def _shape_text(path: str, index: int, text: str):
    blob = hb.Blob.from_file_path(path)
    face = hb.Face(blob, index)
    font = hb.Font(face)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, {"kern": True, "liga": True})
    return [
        (info.codepoint, info.cluster, pos.x_advance, pos.x_offset, pos.y_offset)
        for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions)
    ]


@dataclass
class Glyph:
    contours: list[np.ndarray]
    x: float
    advance: float


def _kern(kerns: dict | None, index: int) -> float:
    return float(kerns.get(str(index), 0.0)) if kerns else 0.0


def _outline_lines(text, font_path, font_index, height, tracking, kerns):
    font = TTFont(font_path, fontNumber=font_index, lazy=True)
    glyph_set = font.getGlyphSet()
    order = font.getGlyphOrder()
    scale = height / _cap_height(font, glyph_set)
    lines = []
    offset = 0
    for line in text.split("\n"):
        cursor = 0.0
        row = []
        for glyph_id, cluster, advance, off_x, off_y in _shape_text(font_path, font_index, line):
            pen = _FlattenPen(glyph_set)
            glyph_set[order[glyph_id]].draw(pen)
            width = advance * scale
            contours = [
                np.column_stack(
                    [
                        np.asarray(c, dtype=float)[:, 0] * scale + off_x * scale,
                        np.asarray(c, dtype=float)[:, 1] * scale + off_y * scale,
                    ]
                )
                for c in pen.contours
            ]
            row.append(Glyph(contours, cursor, width))
            cursor += width + tracking + _kern(kerns, offset + cluster)
        lines.append((row, max(cursor - tracking, 0.0)))
        offset += len(line) + 1
    return lines


@lru_cache(maxsize=8)
def _hershey(face: str):
    font = HersheyFonts()
    font.load_default_font(face)
    return font


def _stroke_lines(text, face, height, tracking, kerns):
    font = _hershey(face)
    probe = next(iter(font.glyphs_for_text("H")))
    cap = abs(probe.base_line - probe.cap_line) or 21.0
    flip = -1.0 if probe.cap_line < probe.base_line else 1.0
    scale = height / cap
    lines = []
    offset = 0
    for line in text.split("\n"):
        cursor = 0.0
        row = []
        for index, glyph in enumerate(font.glyphs_for_text(line)):
            width = glyph.char_width * scale
            contours = []
            for stroke in glyph.strokes:
                points = np.asarray(list(stroke), dtype=float)
                if len(points) < 2:
                    continue
                contours.append(
                    np.column_stack(
                        [
                            (points[:, 0] - glyph.left_offset) * scale,
                            flip * (points[:, 1] - glyph.base_line) * scale,
                        ]
                    )
                )
            row.append(Glyph(contours, cursor, width))
            cursor += width + tracking + _kern(kerns, offset + index)
        lines.append((row, max(cursor - tracking, 0.0)))
        offset += len(line) + 1
    return lines


def _placed(lines, x, y, align, height):
    gap = height * 1.45
    out = []
    for line_index, (row, width) in enumerate(lines):
        if align == "center":
            start = x - width / 2
        elif align == "right":
            start = x - width
        else:
            start = x
        base = y - line_index * gap
        for glyph in row:
            shift = np.array([start + glyph.x, base])
            out.append([contour + shift for contour in glyph.contours])
    return out


def _text_line(path: Vector, flip: bool) -> LineString:
    points = np.asarray(path.points, dtype=float)
    if not path.closed:
        return LineString(points)
    x, y = points[:, 0], points[:, 1]
    if float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) > 0:
        points = points[::-1]
    anchor = int(np.argmin(points[:, 1]) if flip else np.argmax(points[:, 1]))
    points = np.roll(points, -anchor, axis=0)
    return LineString(np.vstack([points, points[0]]))


def _bent(lines, path: Vector, align, raise_by, flip):
    row, width = lines[0]
    line = _text_line(path, flip)
    total = line.length
    if path.closed:
        start = {"center": -width / 2, "right": -width}.get(align, 0.0)
    elif align == "center":
        start = (total - width) / 2
    elif align == "right":
        start = total - width
    else:
        start = 0.0
    out = []
    for glyph in row:
        middle = glyph.x + glyph.advance / 2
        station = start + (width - middle if flip else middle)
        if path.closed:
            station %= total
        station = float(np.clip(station, 0.0, total))
        here = line.interpolate(station)
        ahead = line.interpolate(min(station + 0.1, total))
        behind = line.interpolate(max(station - 0.1, 0.0))
        angle = math.atan2(ahead.y - behind.y, ahead.x - behind.x)
        if flip:
            angle += math.pi
        cosine, sine = math.cos(angle), math.sin(angle)
        placed = []
        for contour in glyph.contours:
            cx = contour[:, 0] - glyph.advance / 2
            cy = contour[:, 1] + raise_by
            placed.append(
                np.column_stack([here.x + cx * cosine - cy * sine, here.y + cx * sine + cy * cosine])
            )
        out.append(placed)
    return out, width


def _outlines_to_vectors(glyphs) -> list[Vector]:
    out: list[Vector] = []
    for contours in glyphs:
        if not contours:
            continue
        shape = _glyph_shape([[tuple(p) for p in contour] for contour in contours])
        if shape is not None and polygons(shape):
            out.extend(vectors_from_geometry(shape))
    return out


def _strokes_to_vectors(glyphs) -> list[Vector]:
    out: list[Vector] = []
    for contours in glyphs:
        for contour in contours:
            closed = len(contour) > 3 and np.allclose(contour[0], contour[-1], atol=1e-6)
            rows = contour[:-1] if closed else contour
            out.append(Vector(points=rows.tolist(), closed=closed))
    return out


def text_vectors(
    text: str,
    font_path: str,
    height: float,
    x: float = 0.0,
    y: float = 0.0,
    align: str = "left",
    font_index: int = 0,
    tracking: float = 0.0,
    kerns: dict | None = None,
) -> list[Vector]:
    lines = _outline_lines(text, font_path, font_index, height, tracking, kerns)
    return _outlines_to_vectors(_placed(lines, x, y, align, height))


def stroke_vectors(
    text: str,
    face: str,
    height: float,
    x: float = 0.0,
    y: float = 0.0,
    align: str = "left",
    tracking: float = 0.0,
    kerns: dict | None = None,
) -> list[Vector]:
    lines = _stroke_lines(text, face, height, tracking, kerns)
    return _strokes_to_vectors(_placed(lines, x, y, align, height))


def text_on_path(
    text: str,
    path: Vector,
    height: float,
    align: str = "center",
    raise_by: float = 0.0,
    flip: bool = False,
    tracking: float = 0.0,
    kerns: dict | None = None,
    font_path: str | None = None,
    font_index: int = 0,
    face: str | None = None,
) -> tuple[list[Vector], float]:
    flat = text.replace("\n", " ")
    if face:
        lines = _stroke_lines(flat, face, height, tracking, kerns)
        glyphs, width = _bent(lines, path, align, raise_by, flip)
        return _strokes_to_vectors(glyphs), width
    lines = _outline_lines(flat, font_path, font_index, height, tracking, kerns)
    glyphs, width = _bent(lines, path, align, raise_by, flip)
    return _outlines_to_vectors(glyphs), width
