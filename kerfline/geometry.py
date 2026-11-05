from __future__ import annotations

import math
from functools import reduce

import numpy as np
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient

from .model import Vector

MIN_AREA = 1e-3


def polygons(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > MIN_AREA else []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(polygons(part))
        return out
    return []


def polygon_from(points) -> Polygon | MultiPolygon:
    pts = [(p[0], p[1]) for p in points]
    if len(pts) < 3:
        return Polygon()
    poly = Polygon(pts)
    if poly.is_valid:
        return poly
    return poly.buffer(0)


def region(vectors: list[Vector]):
    shapes = [polygon_from(v.points) for v in vectors if v.closed]
    shapes = [s for s in shapes if not s.is_empty]
    if not shapes:
        return Polygon()
    return reduce(lambda a, b: a.symmetric_difference(b), shapes)


def oriented(poly: Polygon, ccw: bool) -> Polygon:
    return orient(poly, 1.0 if ccw else -1.0)


def ring_array(ring) -> np.ndarray:
    return np.asarray(ring.coords, dtype=float)[:, :2]


def rotate_ring(points: np.ndarray, x: float, y: float) -> np.ndarray:
    body = points[:-1]
    index = int(np.argmin(np.hypot(body[:, 0] - x, body[:, 1] - y)))
    rotated = np.vstack([body[index:], body[:index]])
    return np.vstack([rotated, rotated[0]])


def path_length(points: np.ndarray) -> float:
    return float(np.hypot(*np.diff(points, axis=0).T).sum())


def insert_stations(points: np.ndarray, stations: list[float]) -> np.ndarray:
    seg = np.hypot(*np.diff(points, axis=0).T)
    cumulative = np.concatenate([[0.0], np.cumsum(seg)])
    total = cumulative[-1]
    wanted = sorted({s for s in stations if 1e-6 < s < total - 1e-6})
    merged = np.unique(np.concatenate([cumulative, wanted])) if wanted else cumulative
    keep = [merged[0]]
    for value in merged[1:]:
        if value - keep[-1] > 1e-7:
            keep.append(value)
    merged = np.array(keep)
    return np.column_stack(
        [
            np.interp(merged, cumulative, points[:, 0]),
            np.interp(merged, cumulative, points[:, 1]),
            merged,
        ]
    )


def inside_line(allowed, start, end, tolerance: float = 0.02) -> bool:
    line = LineString([start, end])
    if line.length < 1e-9:
        return True
    return allowed.buffer(tolerance).covers(line)


def containment_depths(rings: list[np.ndarray]) -> list[int]:
    shells = [Polygon(r) for r in rings]
    probes = [Point(r[0]) for r in rings]
    depths = []
    for i, probe in enumerate(probes):
        count = 0
        for j, shell in enumerate(shells):
            if i != j and shell.area > shells[i].area and shell.buffer(0).contains(probe):
                count += 1
        depths.append(count)
    return depths


def nearest_order(items: list, start_of) -> list:
    remaining = list(items)
    ordered = []
    x, y = 0.0, 0.0
    while remaining:
        best = min(
            range(len(remaining)),
            key=lambda k: (start_of(remaining[k])[0] - x) ** 2 + (start_of(remaining[k])[1] - y) ** 2,
        )
        item = remaining.pop(best)
        ordered.append(item)
        x, y = start_of(item)
    return ordered


def bounds(vectors: list[Vector]) -> tuple[float, float, float, float]:
    points = np.array([p for v in vectors for p in v.points], dtype=float)
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def line_of(vector: Vector) -> LineString:
    points = [(p[0], p[1]) for p in vector.points]
    if vector.closed:
        points.append(points[0])
    return LineString(points)


def join_open(vectors: list[Vector], tolerance: float = 0.01) -> list[Vector]:
    done = [v for v in vectors if v.closed]
    pool = [(list(map(tuple, v.points)), v.layer) for v in vectors if not v.closed]
    while pool:
        chain, layer = pool.pop()
        grew = True
        while grew:
            grew = False
            for index, (other, _) in enumerate(pool):
                if math.dist(chain[-1], other[0]) <= tolerance:
                    chain = chain + other[1:]
                elif math.dist(chain[-1], other[-1]) <= tolerance:
                    chain = chain + other[::-1][1:]
                elif math.dist(chain[0], other[-1]) <= tolerance:
                    chain = other[:-1] + chain
                elif math.dist(chain[0], other[0]) <= tolerance:
                    chain = other[::-1][:-1] + chain
                else:
                    continue
                pool.pop(index)
                grew = True
                break
        if len(chain) > 2 and math.dist(chain[0], chain[-1]) <= tolerance:
            done.append(Vector(points=[list(p) for p in chain[:-1]], closed=True, layer=layer))
        else:
            done.append(Vector(points=[list(p) for p in chain], closed=False, layer=layer))
    return done


def vectors_from_geometry(geom, layer: str = "Layer 1") -> list[Vector]:
    out: list[Vector] = []
    for poly in polygons(geom):
        for ring in [poly.exterior, *poly.interiors]:
            pts = ring_array(ring)
            out.append(Vector(points=pts.tolist(), closed=True, layer=layer))
    return out


__all__ = [
    "polygons",
    "polygon_from",
    "region",
    "oriented",
    "ring_array",
    "rotate_ring",
    "path_length",
    "insert_stations",
    "inside_line",
    "containment_depths",
    "nearest_order",
    "bounds",
    "line_of",
    "join_open",
    "vectors_from_geometry",
    "shapely",
]
