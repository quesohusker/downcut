from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Point

from .geometry import bounds, line_of
from .model import Vector, new_id


def _copy(vector: Vector, change, group: str) -> Vector:
    points = change(np.asarray(vector.points, dtype=float)).tolist()
    anchors = change(np.asarray(vector.anchors, dtype=float)).tolist() if vector.anchors else None
    return Vector(points=points, closed=vector.closed, layer=vector.layer, group=group, anchors=anchors)


def _rotation(cx: float, cy: float, angle: float, dx: float = 0.0, dy: float = 0.0):
    cosine, sine = math.cos(angle), math.sin(angle)

    def change(points: np.ndarray) -> np.ndarray:
        x = points[:, 0] - cx
        y = points[:, 1] - cy
        return np.column_stack([cx + x * cosine - y * sine + dx, cy + x * sine + y * cosine + dy])

    return change


def grid_array(vectors: list[Vector], columns: int, rows: int, step_x: float, step_y: float) -> list[Vector]:
    copies: list[Vector] = []
    for row in range(rows):
        for column in range(columns):
            if row == 0 and column == 0:
                continue
            group = new_id()
            shift = np.array([column * step_x, row * step_y])
            copies.extend(_copy(v, lambda p: p + shift, group) for v in vectors)
    return copies


def circular_array(
    vectors: list[Vector], count: int, cx: float, cy: float, sweep: float = 360.0, rotate: bool = True
) -> list[Vector]:
    copies: list[Vector] = []
    x0, y0, x1, y1 = bounds(vectors)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    closed_circle = abs(sweep) >= 360.0 - 1e-9
    step = math.radians(sweep) / (count if closed_circle else max(count - 1, 1))
    for index in range(1, count):
        group = new_id()
        angle = step * index
        if rotate:
            change = _rotation(cx, cy, angle)
        else:
            rx = cx + (mx - cx) * math.cos(angle) - (my - cy) * math.sin(angle)
            ry = cy + (mx - cx) * math.sin(angle) + (my - cy) * math.cos(angle)
            shift = np.array([rx - mx, ry - my])
            change = lambda p, s=shift: p + s
        copies.extend(_copy(v, change, group) for v in vectors)
    return copies


def along_path(vectors: list[Vector], path: Vector, count: int, rotate: bool = True) -> list[Vector]:
    line = line_of(path)
    x0, y0, x1, y1 = bounds(vectors)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    total = line.length
    spacing = total / count if path.closed else total / max(count - 1, 1)
    copies: list[Vector] = []
    for index in range(count):
        station = index * spacing
        here = line.interpolate(station)
        ahead = line.interpolate(min(station + 0.05, total))
        behind = line.interpolate(max(station - 0.05, 0.0))
        angle = math.atan2(ahead.y - behind.y, ahead.x - behind.x) if rotate else 0.0
        group = new_id()
        change = _rotation(mx, my, angle, here.x - mx, here.y - my)
        copies.extend(_copy(v, change, group) for v in vectors)
    return copies


def path_length(path: Vector) -> float:
    return line_of(path).length


__all__ = ["grid_array", "circular_array", "along_path", "path_length", "Point"]
