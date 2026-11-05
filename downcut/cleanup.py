from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.strtree import STRtree

from .geometry import line_of
from .model import Vector

MIN_OVERLAP_FACTOR = 4.0
COVERED = 0.98


def _size(vector: Vector) -> tuple[float, float]:
    points = np.asarray(vector.points, dtype=float)
    width, height = np.ptp(points, axis=0)
    return float(width), float(height)


def _center(vector: Vector) -> list[float]:
    points = np.asarray(vector.points, dtype=float)
    return [float(v) for v in (points.min(axis=0) + points.max(axis=0)) / 2]


def _tiny_items(vector: Vector, line: LineString, tiny: float, tolerance: float) -> dict | None:
    width, height = _size(vector)
    span = max(width, height)
    if span <= tiny:
        kind = "Tiny line"
        if vector.closed:
            area = abs(Polygon(vector.points).buffer(0).area)
            kind = "Tiny rectangle" if area >= 0.9 * max(width * height, 1e-12) else "Tiny shape"
        return {"vector_id": vector.id, "label": kind, "size": span, "point": _center(vector)}
    if vector.closed and len(vector.points) >= 3:
        area = abs(Polygon(vector.points).buffer(0).area)
        if 2 * area / max(line.length, 1e-9) <= tolerance:
            return {"vector_id": vector.id, "label": "Thin sliver", "size": span, "point": _center(vector)}
    return None


def _covered(line: LineString, other: LineString, tolerance: float) -> float:
    return line.intersection(other.buffer(tolerance, quad_segs=4)).length


def scan(vectors: list[Vector], tiny: float = 0.5, tolerance: float = 0.05) -> dict:
    usable = [v for v in vectors if len(v.points) >= 2]
    lines = [line_of(v) for v in usable]

    tiny_rows = []
    tiny_ids = set()
    for vector, line in zip(usable, lines):
        row = _tiny_items(vector, line, tiny, tolerance)
        if row:
            tiny_rows.append(row)
            tiny_ids.add(vector.id)

    duplicates, overlaps = [], []
    gone: set[str] = set()
    if lines:
        tree = STRtree(lines)
        left, right = tree.query(lines, predicate="dwithin", distance=tolerance)
        order = sorted({(int(a), int(b)) for a, b in zip(left, right) if a < b}, key=lambda pair: pair)
        for a, b in order:
            first, second = usable[a], usable[b]
            if first.id in gone or second.id in gone or first.id in tiny_ids or second.id in tiny_ids:
                continue
            one, two = lines[a], lines[b]
            shorter, longer = (a, b) if one.length <= two.length else (b, a)
            small, big = lines[shorter], lines[longer]
            shared = _covered(small, big, tolerance)
            if shared < max(tiny, MIN_OVERLAP_FACTOR * tolerance):
                continue
            ratio = shared / max(small.length, 1e-9)
            same_size = small.length >= big.length * COVERED
            if ratio >= COVERED and (same_size or not usable[shorter].closed):
                keep, drop = (a, b) if same_size else (longer, shorter)
                label = "Exact copy" if same_size else "Line on top of another"
                duplicates.append(
                    {
                        "vector_id": usable[drop].id,
                        "keep_id": usable[keep].id,
                        "label": label,
                        "size": lines[drop].length,
                        "point": _center(usable[drop]),
                    }
                )
                gone.add(usable[drop].id)
            else:
                fixable = not usable[shorter].closed or not usable[longer].closed
                target = shorter if not usable[shorter].closed else longer
                overlaps.append(
                    {
                        "vector_id": usable[target].id,
                        "other_id": usable[longer if target == shorter else shorter].id,
                        "label": "Shared span" if not fixable else "Overlapping span",
                        "size": shared,
                        "point": _center(usable[target]),
                        "fixable": fixable,
                    }
                )
    return {"tiny": tiny_rows, "duplicates": duplicates, "overlaps": overlaps}


def trim_overlap(target: Vector, other: Vector, tolerance: float = 0.05) -> list[Vector]:
    if target.closed:
        return [target]
    remainder = line_of(target).difference(line_of(other).buffer(tolerance, quad_segs=4))
    parts = list(remainder.geoms) if isinstance(remainder, MultiLineString) else [remainder]
    out = []
    for part in parts:
        if part.geom_type == "LineString" and part.length > tolerance:
            out.append(
                Vector(
                    points=shapely.get_coordinates(part).tolist(),
                    closed=False,
                    layer=target.layer,
                    group=target.group,
                )
            )
    return out
