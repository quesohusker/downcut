from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve
from shapely import affinity
from shapely.geometry import Point, box
from shapely.ops import unary_union

from .geometry import line_of, polygon_from, polygons, region
from .model import Vector


@dataclass
class Part:
    vectors: list[Vector]
    shape: object
    area: float = 0.0
    placed: bool = False
    moved: list = field(default_factory=list)


def build_parts(vectors: list[Vector]) -> list[Part]:
    loose: list[list[Vector]] = []
    grouped: dict[str, list[Vector]] = {}
    for vector in vectors:
        if vector.group:
            grouped.setdefault(vector.group, []).append(vector)
        else:
            loose.append([vector])
    sets = list(grouped.values()) + loose

    def footprint(rows: list[Vector]):
        closed = [polygon_from(v.points) for v in rows if v.closed]
        lines = [line_of(v).buffer(0.01) for v in rows if not v.closed]
        return unary_union([s for s in closed + lines if not s.is_empty])

    prints = [footprint(rows) for rows in sets]
    order = sorted(range(len(sets)), key=lambda i: prints[i].area, reverse=True)
    owner = {i: i for i in order}
    for rank, index in enumerate(order):
        probe = prints[index].representative_point() if not prints[index].is_empty else None
        for bigger in order[:rank]:
            larger = prints[bigger].area > prints[index].area * 1.0001
            if probe is not None and larger and owner[bigger] == bigger and prints[bigger].contains(prints[index]):
                owner[index] = bigger
                break

    merged: dict[int, list[Vector]] = {}
    for index in order:
        merged.setdefault(owner[index], []).extend(sets[index])

    parts = []
    for rows in merged.values():
        closed = [v for v in rows if v.closed]
        lines = [line_of(v).buffer(0.01) for v in rows if not v.closed]
        shape = unary_union([region(closed)] + lines) if closed else unary_union(lines)
        parts.append(Part(rows, shape, shape.area))
    parts.sort(key=lambda part: part.shape.convex_hull.area, reverse=True)
    return parts


def _raster(geom, x0: float, y0: float, nx: int, ny: int, res: float) -> np.ndarray:
    image = Image.new("L", (nx, ny), 0)
    draw = ImageDraw.Draw(image)

    def scaled(ring):
        return [((x - x0) / res, (y - y0) / res) for x, y in ring.coords]

    for poly in sorted(polygons(geom), key=lambda p: p.area, reverse=True):
        draw.polygon(scaled(poly.exterior), fill=1, outline=1)
        for hole in poly.interiors:
            draw.polygon(scaled(hole), fill=0, outline=1)
    return np.asarray(image, dtype=np.float32)


def nest(
    vectors: list[Vector],
    width: float,
    height: float,
    gap: float = 6.35,
    margin: float = 0.0,
    rotations=(0, 90, 180, 270),
    keepouts=(),
    resolution: float | None = None,
) -> dict:
    parts = build_parts(vectors)
    res = resolution or float(np.clip(max(width, height) / 900.0, 0.5, 5.0))
    pad = gap / 2 + res * 1.5
    ox, oy = -(pad + 2 * res), -(pad + 2 * res)
    nx = int(math.ceil((width - 2 * ox) / res)) + 1
    ny = int(math.ceil((height - 2 * oy) / res)) + 1
    edge = pad - res
    usable = box(margin - edge, margin - edge, width - margin + edge, height - margin + edge)
    if keepouts:
        usable = usable.difference(unary_union([k.buffer(gap / 2 + res) for k in keepouts]))
    blocked = 1.0 - _raster(usable, ox, oy, nx, ny, res)

    placed_shapes = []
    for part in parts:
        best = None
        for angle in rotations:
            turned = affinity.rotate(part.shape, angle, origin=(0, 0))
            grown = turned.buffer(pad, quad_segs=4)
            bx0, by0, bx1, by1 = grown.bounds
            mw = int(math.ceil((bx1 - bx0) / res)) + 2
            mh = int(math.ceil((by1 - by0) / res)) + 2
            if mw > nx or mh > ny:
                continue
            mask = _raster(grown, bx0, by0, mw, mh, res)
            overlap = fftconvolve(blocked, mask[::-1, ::-1], mode="valid")
            free = overlap < 0.5
            rows = free.any(axis=1)
            if not rows.any():
                continue
            iy = int(rows.argmax())
            ix = int(free[iy].argmax())
            score = (iy + mh, ix)
            if best is None or score < best[0]:
                best = (score, angle, ix, iy, mask, bx0, by0, mw, mh)
        if best is None:
            continue
        _, angle, ix, iy, mask, bx0, by0, mw, mh = best
        region_view = blocked[iy : iy + mh, ix : ix + mw]
        region_view[mask > 0.5] = 1.0
        dx = ox + ix * res - bx0
        dy = oy + iy * res - by0
        part.placed = True
        part.moved = [angle, dx, dy]
        placed_shapes.append(affinity.translate(affinity.rotate(part.shape, angle, origin=(0, 0)), dx, dy))

    moved: list[Vector] = []
    unplaced: list[str] = []
    for part in parts:
        if not part.placed:
            unplaced.extend(v.id for v in part.vectors)
            continue
        angle, dx, dy = part.moved
        radians = math.radians(angle)
        cosine, sine = math.cos(radians), math.sin(radians)

        def change(rows):
            array = np.asarray(rows, dtype=float)
            x = array[:, 0] * cosine - array[:, 1] * sine + dx
            y = array[:, 0] * sine + array[:, 1] * cosine + dy
            return np.column_stack([x, y]).tolist()

        for vector in part.vectors:
            vector.points = change(vector.points)
            if vector.anchors:
                vector.anchors = change(vector.anchors)
            moved.append(vector)

    area = sum(shape.area for shape in placed_shapes)
    return {
        "vectors": moved,
        "unplaced": unplaced,
        "parts": len(parts),
        "placed": len(placed_shapes),
        "used_percent": 100.0 * area / (width * height),
        "shapes": placed_shapes,
    }


__all__ = ["nest", "build_parts", "Point"]
