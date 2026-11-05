from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .geometry import nearest_order
from .model import Job, Material, Vector
from .toolpaths.base import RAPID, PathBuilder, Toolpath


@dataclass
class Tile:
    label: str
    x: float
    y: float
    job: Job
    paths: list[Toolpath]


def _span(p0: float, p1: float, low: float, high: float, t0: float, t1: float):
    delta = p1 - p0
    if abs(delta) < 1e-12:
        return (t0, t1) if low - 1e-9 <= p0 <= high + 1e-9 else None
    a, b = (low - p0) / delta, (high - p0) / delta
    if a > b:
        a, b = b, a
    t0, t1 = max(t0, a), min(t1, b)
    return (t0, t1) if t0 <= t1 else None


def clip_path(path: Toolpath, x0: float, y0: float, x1: float, y1: float, safe_z: float) -> Toolpath:
    builder = PathBuilder(path.tool, safe_z)
    previous = None
    for code, x, y, z, _ in path.moves:
        if previous is None or code == RAPID:
            previous = (x, y, z)
            continue
        ax, ay, az = previous
        previous = (x, y, z)
        span = _span(ax, x, x0, x1, 0.0, 1.0)
        if span:
            span = _span(ay, y, y0, y1, span[0], span[1])
        if not span:
            continue
        t0, t1 = span
        sx, sy, sz = ax + (x - ax) * t0 - x0, ay + (y - ay) * t0 - y0, az + (z - az) * t0
        ex, ey, ez = ax + (x - ax) * t1 - x0, ay + (y - ay) * t1 - y0, az + (z - az) * t1
        joined = (
            builder.x is not None
            and builder.z < safe_z
            and math.hypot(builder.x - sx, builder.y - sy) < 1e-6
            and abs(builder.z - sz) < 1e-6
        )
        if not joined:
            builder.go_to_cut(sx, sy, sz)
        builder.cut(ex, ey, ez)
    builder.retract()
    return Toolpath(path.operation_id, path.name, path.tool, builder.moves, list(path.warnings))


def _shifted(vectors: list[Vector], dx: float, dy: float) -> list[Vector]:
    return [
        Vector(points=[[x - dx, y - dy] for x, y in v.points], closed=v.closed, layer=v.layer, id=v.id)
        for v in vectors
    ]


def tiles(job: Job, paths: list[Toolpath]) -> list[Tile]:
    setup = job.tiling or {}
    width = float(setup.get("width") or 0.0)
    height = float(setup.get("height") or 0.0)
    overlap = float(setup.get("overlap") or 0.0)
    if not setup.get("enabled") or width <= 0 or height <= 0:
        return [Tile("", 0.0, 0.0, job, paths)]
    if overlap >= min(width, height):
        raise ValueError("Tile overlap must be smaller than the tile.")
    sheet = job.material
    step_x, step_y = width - overlap, height - overlap
    columns = max(math.ceil((sheet.width - overlap) / step_x - 1e-9), 1)
    rows = max(math.ceil((sheet.height - overlap) / step_y - 1e-9), 1)
    out: list[Tile] = []
    for row in range(rows):
        for column in range(columns):
            x0, y0 = column * step_x, row * step_y
            x1, y1 = min(x0 + width, sheet.width), min(y0 + height, sheet.height)
            clipped = [clip_path(path, x0, y0, x1, y1, job.safe_z) for path in paths]
            clipped = [path for path in clipped if path.moves]
            if not clipped:
                continue
            view = replace(
                job,
                material=Material(x1 - x0, y1 - y0, sheet.thickness),
                vectors=_shifted(job.keepout_vectors(), x0, y0),
                operations=[],
                tiling={},
            )
            out.append(Tile(f"tile-r{row + 1}c{column + 1}", x0, y0, view, clipped))
    return out


__all__ = ["tiles", "clip_path", "Tile", "nearest_order"]
