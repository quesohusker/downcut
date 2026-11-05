from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import substring, unary_union

from .geometry import join_open, line_of, polygon_from, region, vectors_from_geometry
from .model import Vector
from .shapes import arc_points

CORNER_TURN = math.radians(5.0)
BONE_TURN = math.radians(20.0)


def combine(vectors: list[Vector], operation: str) -> list[Vector]:
    closed = [v for v in vectors if v.closed]
    if len(closed) < 2:
        return []
    base = region([closed[0]])
    others = [region([v]) for v in closed[1:]]
    for other in others:
        if operation == "weld":
            base = base.union(other)
        elif operation == "subtract":
            base = base.difference(other)
        elif operation == "intersect":
            base = base.intersection(other)
        else:
            raise ValueError(f"Unknown operation: {operation}")
    return vectors_from_geometry(base, closed[0].layer)


def offset(vectors: list[Vector], distance: float, sharp: bool = False) -> list[Vector]:
    join = "mitre" if sharp else "round"
    shape = region(vectors).buffer(distance, quad_segs=16, join_style=join, mitre_limit=8)
    layer = vectors[0].layer if vectors else "Layer 1"
    return vectors_from_geometry(shape, layer)


def join(vectors: list[Vector], tolerance: float) -> list[Vector]:
    return join_open(vectors, max(tolerance, 1e-6))


def close(vectors: list[Vector]) -> list[Vector]:
    out = []
    for vector in vectors:
        if not vector.closed and len(vector.points) > 2:
            vector.closed = True
            vector.anchors = None
        out.append(vector)
    return out


def _ring_body(vector: Vector) -> np.ndarray:
    points = np.asarray(vector.points, dtype=float)
    if vector.closed and len(points) > 1 and np.allclose(points[0], points[-1]):
        return points[:-1]
    return points


def simplify(vectors: list[Vector], tolerance: float) -> list[Vector]:
    for vector in vectors:
        simple = np.asarray(line_of(vector).simplify(tolerance).coords)[:, :2]
        if vector.closed:
            simple = simple[:-1]
        if len(simple) >= (3 if vector.closed else 2):
            vector.points = simple.tolist()
            vector.anchors = None
    return vectors


def _chaikin(points: np.ndarray, closed: bool, passes: int) -> np.ndarray:
    current = points
    for _ in range(passes):
        if closed:
            ahead = np.roll(current, -1, axis=0)
            first, second = 0.75 * current + 0.25 * ahead, 0.25 * current + 0.75 * ahead
            current = np.column_stack([first, second]).reshape(-1, 2)
        else:
            a, b = current[:-1], current[1:]
            first, second = 0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b
            middle = np.column_stack([first, second]).reshape(-1, 2)
            current = np.vstack([current[0], middle, current[-1]])
    return current


def smooth(vectors: list[Vector], tolerance: float = 0.25, passes: int = 3) -> list[Vector]:
    simplify(vectors, tolerance)
    for vector in vectors:
        vector.points = _chaikin(_ring_body(vector), vector.closed, passes).tolist()
    return vectors


def strip_repeats(points: list, tolerance: float = 1e-6) -> list:
    out = [points[0]]
    for point in points[1:]:
        if math.dist(point[:2], out[-1][:2]) > tolerance:
            out.append(point)
    return out


def remove_duplicates(vectors: list[Vector], tolerance: float = 0.01) -> tuple[list[Vector], list[str]]:
    kept: list[tuple[Vector, LineString]] = []
    removed: list[str] = []
    for vector in vectors:
        vector.points = strip_repeats(vector.points)
        if vector.closed and len(vector.points) > 1 and math.dist(vector.points[0], vector.points[-1]) <= 1e-6:
            vector.points.pop()
        if len(vector.points) < (3 if vector.closed else 2):
            removed.append(vector.id)
            continue
        line = line_of(vector)
        bounds = np.array(line.bounds)
        same = any(
            other.closed == vector.closed
            and np.abs(np.array(other_line.bounds) - bounds).max() <= tolerance
            and shapely.hausdorff_distance(line, other_line) <= tolerance
            for other, other_line in kept
        )
        if same:
            removed.append(vector.id)
        else:
            kept.append((vector, line))
    return [v for v, _ in kept], removed


def _fillet_points(points: np.ndarray, closed: bool, radius: float) -> list:
    count = len(points)
    if count < 3:
        return points.tolist()
    lengths = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    out: list = []
    indices = range(count) if closed else range(1, count - 1)
    if not closed:
        out.append(points[0].tolist())
    for index in indices:
        here = points[index]
        before = points[(index - 1) % count]
        after = points[(index + 1) % count]
        u = before - here
        v = after - here
        lu, lv = float(np.hypot(*u)), float(np.hypot(*v))
        if lu < 1e-9 or lv < 1e-9:
            continue
        u, v = u / lu, v / lv
        cosine = float(np.clip(u @ v, -1.0, 1.0))
        inner = math.acos(cosine)
        turn = math.pi - inner
        if turn < CORNER_TURN or inner < 1e-6:
            out.append(here.tolist())
            continue
        room = min(lu, lv) / 2
        setback = min(radius / math.tan(inner / 2), room)
        used = setback * math.tan(inner / 2)
        axis = (u + v) / float(np.hypot(*(u + v)))
        center = here + axis * (used / math.sin(inner / 2))
        start = here + u * setback
        end = here + v * setback
        first = math.atan2(start[1] - center[1], start[0] - center[0])
        last = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = (last - first + math.pi) % (2 * math.pi) - math.pi
        out.extend([x, y] for x, y in arc_points(center[0], center[1], used, first, sweep))
    if not closed:
        out.append(points[-1].tolist())
    return strip_repeats(out)


def fillet(vectors: list[Vector], radius: float) -> list[Vector]:
    for vector in vectors:
        vector.points = _fillet_points(_ring_body(vector), vector.closed, radius)
        vector.anchors = None
    return vectors


def _signed_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _corner_discs(points: np.ndarray, radius: float, style: str, side: str) -> list:
    pts = points if _signed_area(points) > 0 else points[::-1]
    count = len(pts)
    discs = []
    for index in range(count):
        here = pts[index]
        before = pts[(index - 1) % count]
        after = pts[(index + 1) % count]
        u, v = before - here, after - here
        lu, lv = float(np.hypot(*u)), float(np.hypot(*v))
        if lu < 1e-9 or lv < 1e-9:
            continue
        u, v = u / lu, v / lv
        incoming = -u
        turn = math.atan2(incoming[0] * v[1] - incoming[1] * v[0], incoming @ v)
        wanted = turn > BONE_TURN if side == "inside" else turn < -BONE_TURN
        if not wanted:
            continue
        if style == "tbone":
            direction = u if lu <= lv else v
        else:
            middle = u + v
            direction = middle / float(np.hypot(*middle))
            if side == "outside":
                direction = -direction
        if style == "tbone" and side == "outside":
            direction = -direction
        center = here + direction * radius
        discs.append(Point(center).buffer(radius, quad_segs=24))
    return discs


def bone_corners(vectors: list[Vector], tool_diameter: float, style: str = "dog", side: str = "inside") -> list[Vector]:
    radius = tool_diameter / 2
    out: list[Vector] = []
    for vector in vectors:
        if not vector.closed:
            out.append(vector)
            continue
        discs = _corner_discs(_ring_body(vector), radius, style, side)
        shape = polygon_from(vector.points)
        if discs:
            merged = unary_union(discs)
            shape = shape.union(merged) if side == "inside" else shape.difference(merged)
        out.extend(vectors_from_geometry(shape, vector.layer))
    return out


def _cut_stations(line: LineString, cutters: list[Vector]) -> list[float]:
    stations: set[float] = set()
    for cutter in cutters:
        hit = line.intersection(line_of(cutter))
        for part in shapely.get_parts(hit):
            if part.geom_type == "Point":
                stations.add(round(line.project(part), 6))
            elif part.geom_type == "LineString":
                for coord in (part.coords[0], part.coords[-1]):
                    stations.add(round(line.project(Point(coord)), 6))
    return sorted(stations)


def _piece(line: LineString, start: float, end: float, layer: str) -> Vector | None:
    if end - start < 1e-4:
        return None
    part = substring(line, start, end)
    if part.geom_type != "LineString":
        return None
    return Vector(points=np.asarray(part.coords)[:, :2].tolist(), closed=False, layer=layer)


def trim_at(target: Vector, cutters: list[Vector], x: float, y: float) -> list[Vector]:
    line = line_of(target)
    total = line.length
    pick = line.project(Point(x, y))
    stations = _cut_stations(line, cutters)
    if not stations:
        raise ValueError("That vector does not cross another vector.")
    pieces: list[Vector | None]
    if target.closed:
        if len(stations) < 2:
            raise ValueError("A closed vector needs two crossings to trim.")
        before = [s for s in stations if s <= pick]
        after = [s for s in stations if s > pick]
        low = before[-1] if before else stations[-1]
        high = after[0] if after else stations[0]
        if high > low:
            head = _piece(line, high, total, target.layer)
            tail = _piece(line, 0.0, low, target.layer)
            pieces = [head, tail]
            if head and tail:
                joined = head.points + tail.points[1:]
                return [Vector(points=joined, closed=False, layer=target.layer)]
        else:
            pieces = [_piece(line, high, low, target.layer)]
    else:
        low = max([s for s in stations if s <= pick], default=0.0)
        high = min([s for s in stations if s > pick], default=total)
        pieces = [_piece(line, 0.0, low, target.layer), _piece(line, high, total, target.layer)]
    return [p for p in pieces if p]


def extend_to(target: Vector, cutters: list[Vector], x: float, y: float) -> Vector:
    if target.closed:
        raise ValueError("Only open vectors can be extended.")
    points = np.asarray(target.points, dtype=float)
    use_head = math.dist((x, y), points[0]) < math.dist((x, y), points[-1])
    tip = points[0] if use_head else points[-1]
    inner = points[1] if use_head else points[-2]
    direction = tip - inner
    direction = direction / float(np.hypot(*direction))
    corners = shapely.get_coordinates([line_of(c) for c in cutters])
    if len(corners) == 0:
        raise ValueError("Nothing lies in the path of that end.")
    span = float(np.hypot(*(corners - tip).T).max())
    ray = LineString([tip + direction * 1e-5, tip + direction * (span + 1.0)])
    stations = []
    for cutter in cutters:
        hits = shapely.get_coordinates(ray.intersection(line_of(cutter)))
        stations.extend(ray.project(Point(hit)) for hit in hits)
    if not stations:
        raise ValueError("Nothing lies in the path of that end.")
    reach = tip + direction * (min(stations) + 1e-5)
    rows = target.points.copy()
    if use_head:
        rows.insert(0, reach.tolist())
    else:
        rows.append(reach.tolist())
    target.points = rows
    target.anchors = None
    return target


def trim_to_boundary(vectors: list[Vector], boundary: Vector, keep: str = "inside") -> list[Vector]:
    shape = region([boundary])
    if shape.is_empty:
        raise ValueError("The boundary must be a closed vector.")
    out: list[Vector] = []
    for vector in vectors:
        if vector.closed:
            geom = polygon_from(vector.points)
            clipped = geom.intersection(shape) if keep == "inside" else geom.difference(shape)
            out.extend(vectors_from_geometry(clipped, vector.layer))
            continue
        line = line_of(vector)
        clipped = line.intersection(shape) if keep == "inside" else line.difference(shape)
        parts = list(clipped.geoms) if isinstance(clipped, MultiLineString) else [clipped]
        for part in parts:
            if part.geom_type == "LineString" and part.length > 1e-6:
                out.append(Vector(points=np.asarray(part.coords)[:, :2].tolist(), closed=False, layer=vector.layer))
    return out
