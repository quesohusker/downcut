from __future__ import annotations

import math

import numpy as np

from .model import Vector


def _closed(points) -> Vector:
    return Vector(points=[[float(x), float(y)] for x, y in points], closed=True)


def rectangle(x: float, y: float, width: float, height: float, radius: float = 0.0) -> Vector:
    if radius <= 0:
        return _closed([(x, y), (x + width, y), (x + width, y + height), (x, y + height)])
    r = min(radius, width / 2, height / 2)
    points = []
    corners = [
        (x + width - r, y + r, -90),
        (x + width - r, y + height - r, 0),
        (x + r, y + height - r, 90),
        (x + r, y + r, 180),
    ]
    for cx, cy, start in corners:
        for angle in np.linspace(start, start + 90, 13):
            a = math.radians(angle)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return _closed(points)


def ellipse(cx: float, cy: float, rx: float, ry: float) -> Vector:
    count = max(90, int(max(rx, ry) * 2))
    angles = np.linspace(0, 2 * math.pi, count, endpoint=False)
    return _closed(zip(cx + rx * np.cos(angles), cy + ry * np.sin(angles)))


def circle(cx: float, cy: float, radius: float) -> Vector:
    return ellipse(cx, cy, radius, radius)


def polygon(cx: float, cy: float, radius: float, sides: int) -> Vector:
    angles = math.pi / 2 + 2 * math.pi * np.arange(sides) / sides
    return _closed(zip(cx + radius * np.cos(angles), cy + radius * np.sin(angles)))


def star(cx: float, cy: float, outer: float, inner: float, points: int) -> Vector:
    angles = math.pi / 2 + math.pi * np.arange(points * 2) / points
    radii = np.where(np.arange(points * 2) % 2 == 0, outer, inner)
    return _closed(zip(cx + radii * np.cos(angles), cy + radii * np.sin(angles)))


def polyline(points, closed: bool = False) -> Vector:
    return Vector(points=[[float(x), float(y)] for x, y in points], closed=closed)


def arc_points(cx: float, cy: float, radius: float, start: float, sweep: float, tolerance: float = 0.01):
    limit = min(1.0, tolerance / max(radius, 1e-9))
    step = max(2 * math.acos(1 - limit), math.radians(1.0))
    count = max(2, int(math.ceil(abs(sweep) / step)))
    angles = start + sweep * np.arange(count + 1) / count
    return list(zip(cx + radius * np.cos(angles), cy + radius * np.sin(angles)))


def three_point_arc(a, b, c) -> Vector:
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return polyline([a, c])
    sa, sb, sc = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (sa * (by - cy) + sb * (cy - ay) + sc * (ay - by)) / d
    uy = (sa * (cx - bx) + sb * (ax - cx) + sc * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    start = math.atan2(ay - uy, ax - ux)
    middle = (math.atan2(by - uy, bx - ux) - start) % (2 * math.pi)
    end = (math.atan2(cy - uy, cx - ux) - start) % (2 * math.pi)
    sweep = end if middle < end else end - 2 * math.pi
    return polyline(arc_points(ux, uy, radius, start, sweep))


def curve_points(anchors, closed: bool = False) -> list:
    pts = [np.asarray(p, dtype=float) for p in anchors]
    if len(pts) < 3:
        return [tuple(p) for p in pts]
    count = len(pts)
    spans = count if closed else count - 1
    out = []
    for index in range(spans):
        if closed:
            p0, p1, p2, p3 = (pts[(index + k) % count] for k in (-1, 0, 1, 2))
        else:
            p1, p2 = pts[index], pts[index + 1]
            p0 = pts[index - 1] if index > 0 else p1 * 2 - p2
            p3 = pts[index + 2] if index + 2 < count else p2 * 2 - p1
        steps = int(np.clip(math.ceil(float(np.linalg.norm(p2 - p1))), 8, 48))
        for t in np.arange(steps) / steps:
            value = 0.5 * (
                2 * p1
                + (p2 - p0) * t
                + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                + (3 * p1 - p0 - 3 * p2 + p3) * t * t * t
            )
            out.append((float(value[0]), float(value[1])))
    if not closed:
        out.append((float(pts[-1][0]), float(pts[-1][1])))
    return out


def curve(anchors, closed: bool = False) -> Vector:
    vector = polyline(curve_points(anchors, closed), closed)
    vector.anchors = [[float(x), float(y)] for x, y in anchors]
    return vector
