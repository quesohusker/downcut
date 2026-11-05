from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

from ..geometry import inside_line, oriented, path_length, polygons, region, ring_array, rotate_ring
from ..model import Job, Operation, Tool
from .base import PathBuilder, Toolpath, depth_levels, flute_warning


@dataclass
class Entry:
    kind: str
    angle: float
    tool: Tool
    ccw: bool
    notes: set[str] = field(default_factory=set)


def _plunge(builder, ring, z) -> None:
    builder.go_to_cut(ring[0, 0], ring[0, 1], z)


def _ramp(builder, entry: Entry, ring, top: float, z: float) -> None:
    total = path_length(ring)
    if total < 1e-6:
        _plunge(builder, ring, z)
        return
    need = (top - z) / math.tan(math.radians(entry.angle))
    builder.travel(ring[0, 0], ring[0, 1])
    builder.enter(top)
    along = 0.0
    while True:
        for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
            along += math.hypot(x1 - x0, y1 - y0)
            builder.cut(x1, y1, top + (z - top) * min(along / need, 1.0) if need > 0 else z)
        if along >= need:
            return


def _helix_site(allowed, point):
    probe = Point(point)
    for part in polygons(allowed.buffer(0.02)):
        if part.distance(probe) < 0.03:
            line = shapely.maximum_inscribed_circle(part, tolerance=0.02)
            x, y = line.coords[0]
            return x, y, line.length
    return None


def _helix(builder, entry: Entry, ring, top: float, z: float, allowed) -> bool:
    site = _helix_site(allowed, ring[0])
    if site is None:
        return False
    cx, cy, room = site
    radius = min(entry.tool.radius * 0.8, room - 0.05)
    if radius < entry.tool.radius * 0.2:
        return False
    start = (cx + radius, cy)
    target = (ring[0, 0], ring[0, 1])
    if not inside_line(allowed, start, target):
        return False
    pitch = 2 * math.pi * radius * math.tan(math.radians(entry.angle))
    turns = max(math.ceil((top - z) / pitch), 1)
    steps = 36 * turns
    sign = 1.0 if entry.ccw else -1.0
    builder.travel(*start)
    builder.enter(top)
    for index in range(1, steps + 1):
        angle = sign * 2 * math.pi * index / 36
        builder.cut(cx + radius * math.cos(angle), cy + radius * math.sin(angle), top + (z - top) * index / steps)
    for index in range(1, 37):
        angle = sign * 2 * math.pi * index / 36
        builder.cut(cx + radius * math.cos(angle), cy + radius * math.sin(angle), z)
    builder.cut(target[0], target[1], z)
    return True


def enter_at(builder, ring, top: float, z: float, allowed, entry: Entry | None) -> None:
    if entry is None or entry.kind == "plunge":
        _plunge(builder, ring, z)
    elif entry.kind == "helix" and _helix(builder, entry, ring, top, z, allowed):
        return
    else:
        if entry.kind == "helix":
            entry.notes.add("A helix did not fit at one or more entries. Those entries ramp along the path.")
        _ramp(builder, entry, ring, top, z)


def clearing_rings(allowed, stepover: float, climb: bool) -> list:
    ordered = []

    def visit(poly):
        inner = poly.buffer(-stepover, join_style="mitre", mitre_limit=8)
        for child in polygons(inner):
            visit(child)
        directed = oriented(poly, climb)
        for ring in [directed.exterior, *directed.interiors]:
            ordered.append(ring_array(ring))

    for poly in polygons(allowed):
        visit(poly)
    return ordered


def cut_rings(builder, rings, allowed, z, top: float = 0.0, entry: Entry | None = None) -> None:
    for ring in rings:
        if builder.x is None or builder.z >= 0:
            start = rotate_ring(ring, builder.x or 0.0, builder.y or 0.0)
            enter_at(builder, start, top, z, allowed, entry)
        else:
            start = rotate_ring(ring, builder.x, builder.y)
            target = (start[0, 0], start[0, 1])
            if inside_line(allowed, (builder.x, builder.y), target):
                builder.cut(target[0], target[1], z)
            else:
                enter_at(builder, start, top, z, allowed, entry)
        for x, y in start[1:]:
            builder.cut(x, y, z)


def finish_walls(builder, allowed, rough, z, climb: bool) -> None:
    for poly in polygons(allowed):
        directed = oriented(poly, climb)
        for ring in [directed.exterior, *directed.interiors]:
            builder.retract()
            start = rotate_ring(ring_array(ring), builder.x or 0.0, builder.y or 0.0)
            target = (start[0, 0], start[0, 1])
            clear = nearest_points(rough, Point(target))[0] if not rough.is_empty else None
            away = clear is not None and inside_line(allowed, (clear.x, clear.y), target)
            if away:
                builder.go_to_cut(clear.x, clear.y, z)
                builder.cut(target[0], target[1], z)
            else:
                builder.go_to_cut(target[0], target[1], z)
            for x, y in start[1:]:
                builder.cut(x, y, z)
            if away:
                builder.cut(clear.x, clear.y, z)


def uncut_area(allowed, rings, radius: float) -> float:
    if not rings:
        return 0.0
    swept = unary_union([LineString(r).buffer(radius, quad_segs=8) for r in rings])
    return allowed.buffer(radius, quad_segs=8).difference(swept.buffer(0.01)).area


def build_pocket(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    depth = float(params["depth"])
    allowance = float(params.get("allowance", 0.0))
    climb = bool(params.get("climb", True))
    stepover = float(params.get("stepover") or tool.stepover or tool.diameter * 0.4)
    finish = float(params.get("finish_allowance") or 0.0)
    kind = params.get("entry", "plunge")
    angle = float(params.get("entry_angle") or 3.0)

    path = Toolpath(op.id, op.name or "Pocket", tool)
    path.warnings.extend(flute_warning(tool, depth))
    if depth > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Cut depth exceeds material thickness.")
    if stepover > tool.diameter * 0.7:
        path.warnings.append("Stepover exceeds 70 percent of tool diameter. Corners may stay uncut.")

    shape = region(job.vectors_for(op.vector_ids))
    walls = shape.buffer(-(tool.radius + allowance), join_style="mitre", mitre_limit=20)
    if not polygons(walls):
        path.warnings.append("Tool is too large for the selected shape. No path was created.")
        return [path]
    rough = walls
    if finish > 0:
        rough = walls.buffer(-finish, join_style="mitre", mitre_limit=20)
        if not polygons(rough):
            path.warnings.append("Finish allowance leaves no room to rough the pocket.")
            rough = walls
            finish = 0.0

    rings = clearing_rings(rough, stepover, climb)
    missed = uncut_area(rough, rings, tool.radius)
    if missed > tool.diameter**2 * 0.05:
        path.warnings.append(f"About {missed:.1f} sq mm of the pocket stays uncut. Reduce the stepover.")

    entry = Entry(kind, max(angle, 0.5), tool, climb) if kind in ("ramp", "helix") else None
    builder = PathBuilder(tool, job.safe_z)
    top = 0.0
    levels = depth_levels(depth, tool.stepdown)
    for level in levels:
        builder.retract()
        cut_rings(builder, rings, rough, level, top, entry)
        top = level
    if finish > 0:
        finish_walls(builder, walls, rough, levels[-1], climb)
    builder.retract()
    path.moves = builder.moves
    if entry is not None:
        path.warnings.extend(sorted(entry.notes))
    return [path]
