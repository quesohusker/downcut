from __future__ import annotations

import math

import numpy as np

from .toolpaths.base import CUT, RAPID

ARC_CW = 2
ARC_CCW = 3
MAX_SWEEP = 1.9 * math.pi
MAX_STEP = math.radians(12.0)
MAX_SAGITTA = 0.03


def _circle(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return None
    sa, sb, sc = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d
    uy = (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d
    return ux, uy, math.hypot(a[0] - ux, a[1] - uy)


def _fit(points: np.ndarray, tolerance: float, min_radius: float, max_radius: float, helical: bool):
    circle = _circle(points[0], points[len(points) // 2], points[-1])
    if circle is None:
        return None
    cx, cy, radius = circle
    if radius < max(min_radius, 0.05) or radius > max_radius:
        return None
    xy = points[:, :2] - (cx, cy)
    if np.abs(np.hypot(*xy.T) - radius).max() > tolerance:
        return None
    middles = (xy[:-1] + xy[1:]) / 2
    if np.abs(np.hypot(*middles.T) - radius).max() > max(tolerance, MAX_SAGITTA):
        return None
    angles = np.unwrap(np.arctan2(xy[:, 1], xy[:, 0]))
    steps = np.diff(angles)
    total = float(angles[-1] - angles[0])
    if abs(total) > MAX_SWEEP or abs(total) < 1e-6 or np.abs(steps).max() > MAX_STEP:
        return None
    if (steps * math.copysign(1.0, total) <= 0).any():
        return None
    z = points[:, 2]
    if abs(z[-1] - z[0]) > 1e-9 and not helical:
        return None
    expected = z[0] + (z[-1] - z[0]) * (angles - angles[0]) / total
    if np.abs(z - expected).max() > tolerance:
        return None
    return cx, cy, total > 0


def _longest(points, start, tolerance, min_radius, max_radius, helical):
    last = len(points) - 1
    end = start + 3
    if end > last:
        return None
    best = _fit(points[start : end + 1], tolerance, min_radius, max_radius, helical)
    if best is None:
        return None
    step = 4
    while end < last:
        probe = min(end + step, last)
        found = _fit(points[start : probe + 1], tolerance, min_radius, max_radius, helical)
        if found is not None:
            end, best = probe, found
            step *= 2
        elif step == 1:
            break
        else:
            step = max(step // 2, 1)
    return end, best


def fit_arcs(moves, tolerance=0.01, min_radius=0.0, max_radius=5000.0, helical=True) -> list[tuple]:
    out: list[tuple] = []
    index = 0
    count = len(moves)
    while index < count:
        move = moves[index]
        if move[0] != CUT or index == 0:
            out.append(move)
            index += 1
            continue
        feed = move[4]
        stop = index
        while stop + 1 < count and moves[stop + 1][0] == CUT and moves[stop + 1][4] == feed:
            stop += 1
        before = moves[index - 1]
        points = np.array([before[1:4]] + [row[1:4] for row in moves[index : stop + 1]], dtype=float)
        at = 0
        last = len(points) - 1
        while at < last:
            found = _longest(points, at, tolerance, min_radius, max_radius, helical)
            if found is None:
                row = moves[index + at]
                out.append(row)
                at += 1
                continue
            end, (cx, cy, ccw) = found
            x, y, z = points[end]
            out.append((ARC_CCW if ccw else ARC_CW, x, y, z, feed, cx, cy, points[at, 0], points[at, 1]))
            at = end
        index = stop + 1
    return out


__all__ = ["fit_arcs", "ARC_CW", "ARC_CCW", "RAPID", "CUT"]
