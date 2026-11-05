from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from .gcode import origin_shift
from .verify import parse


def grid_for(job, resolution: float):
    nx = int(math.ceil(job.material.width / resolution)) + 1
    ny = int(math.ceil(job.material.height / resolution)) + 1
    return np.zeros((ny, nx)), nx, ny


def _window(lo, hi, origin, res, count):
    first = max(0, int(math.floor((lo - origin) / res)))
    last = min(count - 1, int(math.ceil((hi - origin) / res)))
    return first, last


def _stamp(heights, res, seg, tool):
    (x0, y0, z0), (x1, y1, z1) = seg
    ny, nx = heights.shape
    radius = tool.radius
    i0, i1 = _window(min(x0, x1) - radius, max(x0, x1) + radius, 0.0, res, nx)
    j0, j1 = _window(min(y0, y1) - radius, max(y0, y1) + radius, 0.0, res, ny)
    if i0 > i1 or j0 > j1:
        return
    xs = i0 * res + np.arange(i1 - i0 + 1) * res
    ys = j0 * res + np.arange(j1 - j0 + 1) * res
    px, py = np.meshgrid(xs, ys)
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    conical = tool.kind == "vbit"
    tip = tool.tip_radius
    if tool.kind == "ballnose":
        steps = max(int(math.ceil(length / res)), 1)
        for index in range(steps + 1):
            t = index / steps
            cx, cy, cz = x0 + dx * t, y0 + dy * t, z0 + (z1 - z0) * t
            distance = np.hypot(px - cx, py - cy)
            mask = distance <= radius
            surface = cz + radius - np.sqrt(np.clip(radius * radius - distance * distance, 0.0, None))
            view = heights[j0 : j1 + 1, i0 : i1 + 1]
            view[mask] = np.minimum(view[mask], surface[mask])
        return

    if length < 1e-9:
        distance = np.hypot(px - x0, py - y0)
        low = min(z0, z1)
        surface = low + np.maximum(distance - tip, 0.0) / tool.tan_half if conical else np.full_like(px, low)
        mask = distance <= radius
    else:
        ux, uy = dx / length, dy / length
        along = (px - x0) * ux + (py - y0) * uy
        across = np.abs(-(px - x0) * uy + (py - y0) * ux)
        slope = (z1 - z0) / length
        if conical:
            k = tool.tan_half
            c = slope * k
            if abs(c) < 1:
                best = along - across * c / math.sqrt(1 - c * c)
                best = np.clip(best, 0.0, length)
            elif c >= 1:
                best = np.zeros_like(along)
            else:
                best = np.full_like(along, length)
            distance = np.hypot(along - best, across)
            surface = z0 + slope * best + np.maximum(distance - tip, 0.0) / k
            mask = distance <= radius
        else:
            within = across <= radius
            reach = np.sqrt(np.clip(radius * radius - across * across, 0.0, None))
            start = along - reach
            end = along + reach
            mask = within & (start <= length) & (end >= 0)
            best = np.maximum(0.0, start) if slope > 0 else np.minimum(length, end)
            surface = z0 + slope * best

    view = heights[j0 : j1 + 1, i0 : i1 + 1]
    view[mask] = np.minimum(view[mask], surface[mask])


def simulate(job, programs, resolution: float | None = None) -> tuple[np.ndarray, float]:
    res = resolution or max(0.25, max(job.material.width, job.material.height) / 1400)
    heights, _, _ = grid_for(job, res)
    base = origin_shift(job)
    for entry in programs:
        tool, text = entry[0], entry[1]
        ox, oy, oz = entry[2] if len(entry) > 2 else base
        segments = parse(text)[0]
        for seg in segments:
            if seg.rapid or None in seg.start or None in seg.end:
                continue
            x0, y0, z0 = seg.start
            x1, y1, z1 = seg.end
            z0, z1 = z0 - oz, z1 - oz
            if z0 >= 0 and z1 >= 0:
                continue
            _stamp(heights, res, ((x0 + ox, y0 + oy, z0), (x1 + ox, y1 + oy, z1)), tool)
    return np.maximum(heights, -job.material.thickness), res


def render(heights: np.ndarray, res: float, thickness: float) -> bytes:
    grad_y, grad_x = np.gradient(heights, res)
    norm = np.sqrt(grad_x**2 + grad_y**2 + 1)
    light = np.array([-0.45, 0.55, 0.70])
    light = light / np.linalg.norm(light)
    shade = (-grad_x * light[0] - grad_y * light[1] + light[2]) / norm
    shade = np.clip(shade, 0.0, 1.0)
    depth = np.clip(-heights / max(thickness, 1e-6), 0.0, 1.0)
    top = np.array([214.0, 176.0, 128.0])
    deep = np.array([126.0, 88.0, 54.0])
    colour = top[None, None, :] * (1 - depth[..., None]) + deep[None, None, :] * depth[..., None]
    lit = colour * (0.30 + 0.92 * shade[..., None])
    lit[heights <= -thickness + 1e-6] = np.array([34.0, 36.0, 40.0])
    image = Image.fromarray(np.clip(lit[::-1], 0, 255).astype(np.uint8), "RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=False)
    return buffer.getvalue()
