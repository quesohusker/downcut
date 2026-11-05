from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString

from ..geometry import (
    containment_depths,
    insert_stations,
    nearest_order,
    oriented,
    path_length,
    polygons,
    region,
    ring_array,
)
from ..model import Job, Operation
from .base import PathBuilder, Toolpath, depth_levels, flute_warning


def offset_rings(geom, side: str, offset: float, climb: bool, sharp: bool = False) -> list[np.ndarray]:
    if side == "outside":
        join = "mitre" if sharp else "round"
        shape = geom.buffer(offset, quad_segs=16, join_style=join, mitre_limit=20)
        ccw = not climb
    elif side == "inside":
        shape = geom.buffer(-offset, quad_segs=16, join_style="mitre", mitre_limit=20)
        ccw = climb
    else:
        shape = geom
        ccw = climb
    rings: list[np.ndarray] = []
    for poly in polygons(shape):
        directed = oriented(poly, ccw)
        rings.append(ring_array(directed.exterior))
        rings.extend(ring_array(hole) for hole in directed.interiors)
    return rings


def _tab_intervals(total: float, count: int, width: float) -> list[tuple[float, float]]:
    if count <= 0 or width >= total / count:
        return []
    half = width / 2
    return [((i + 0.5) * total / count - half, (i + 0.5) * total / count + half) for i in range(count)]


def _in_tab(station: float, tabs: list[tuple[float, float]]) -> bool:
    return any(start <= station <= end for start, end in tabs)


def _height(station: float, top: float, bottom: float, ramp: float) -> float:
    if ramp > 0 and station < ramp:
        return top + (bottom - top) * station / ramp
    return bottom


def _lap(builder, points, top, bottom, ramp, tabs, tab_z, stop=None):
    for a, b in zip(points[:-1], points[1:]):
        if stop is not None and a[2] >= stop - 1e-9:
            break
        z_a = _height(a[2], top, bottom, ramp)
        z_b = _height(b[2], top, bottom, ramp)
        if tabs and _in_tab((a[2] + b[2]) / 2, tabs):
            z_a, z_b = max(z_a, tab_z), max(z_b, tab_z)
        builder.cut(a[0], a[1], z_a)
        builder.cut(b[0], b[1], z_b)


def _at_longest_span(ring: np.ndarray) -> np.ndarray:
    body = ring[:-1]
    ahead = np.roll(body, -1, axis=0)
    spans = np.hypot(*(ahead - body).T)
    index = int(np.argmax(spans))
    middle = (body[index] + ahead[index]) / 2
    rotated = np.vstack([middle, np.roll(body, -(index + 1), axis=0), middle])
    return rotated


def _lead_arc(start, direction, normal, radius, first, last) -> np.ndarray:
    center = start + normal * radius
    angles = np.radians(np.linspace(first, last, 13))
    return center + radius * (np.outer(np.sin(angles), direction) - np.outer(np.cos(angles), normal))


def lead_paths(ring: np.ndarray, kind: str, length: float, fits):
    start = ring[0]
    direction = ring[1] - ring[0]
    direction = direction / float(np.hypot(*direction))
    left = np.array([-direction[1], direction[0]])
    for normal in (left, -left):
        if kind == "arc":
            lead_in = _lead_arc(start, direction, normal, length, -90.0, 0.0)
            lead_out = _lead_arc(start, direction, normal, length, 0.0, 90.0)
        else:
            reach = length * math.sqrt(0.5)
            lead_in = np.array([start + (normal - direction) * reach, start])
            lead_out = np.array([start, start + (normal + direction) * reach])
        if fits(LineString(np.vstack([lead_in, lead_out[1:]]))):
            return lead_in, lead_out
    return None


def cut_ring(builder, ring, levels, ramp_length, tab_count, tab_length, tab_thickness, lead=None):
    total = path_length(ring)
    ramp = min(ramp_length, total / 2) if ramp_length > 0 else 0.0
    bottom = levels[-1]
    tab_z = bottom + tab_thickness
    width = tab_length + builder.tool.diameter
    tabs = _tab_intervals(total, tab_count, width) if tab_thickness > 0 else []
    stations = [ramp] if ramp else []
    for start, end in tabs:
        stations.extend([start, end])
    dense = insert_stations(ring, stations)
    if lead is not None:
        lead_in, lead_out = lead
        for level in levels:
            builder.travel(lead_in[0, 0], lead_in[0, 1])
            builder.enter(level)
            for x, y in lead_in[1:]:
                builder.cut(x, y, level)
            _lap(builder, dense, level, level, 0.0, tabs, tab_z)
            for x, y in lead_out[1:]:
                builder.cut(x, y, level)
            builder.retract()
        return
    builder.travel(dense[0, 0], dense[0, 1])
    top = 0.0
    for index, level in enumerate(levels):
        if index == 0 and ramp:
            builder.enter(0.0)
        elif not ramp:
            start_z = max(level, tab_z) if tabs and _in_tab(0.0, tabs) else level
            if builder.z > 0:
                builder.enter(start_z)
            else:
                builder.cut(dense[0, 0], dense[0, 1], start_z)
        _lap(builder, dense, top, level, ramp, tabs, tab_z)
        top = level
    if ramp:
        _lap(builder, dense, bottom, bottom, 0.0, tabs, tab_z, stop=ramp)


def cut_open(builder, points, levels):
    for level in levels:
        builder.travel(points[0, 0], points[0, 1])
        builder.enter(level)
        for x, y in points[1:]:
            builder.cut(x, y, level)
        builder.retract()


def build_profile(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    side = params.get("side", "outside")
    depth = float(params["depth"])
    allowance = float(params.get("allowance", 0.0))
    climb = bool(params.get("climb", True))
    ramp = float(params.get("ramp_length", 0.0))
    tab_count = int(params.get("tab_count", 0))
    tab_length = float(params.get("tab_length", 0.0))
    tab_thickness = float(params.get("tab_thickness", 0.0))
    lead_kind = params.get("lead", "none")
    lead_length = float(params.get("lead_length") or 0.0)

    vectors = job.vectors_for(op.vector_ids)
    path = Toolpath(op.id, op.name or "Profile", tool)
    path.warnings.extend(flute_warning(tool, depth))
    if depth > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Cut depth exceeds material thickness.")
    if tab_thickness >= depth and tab_count > 0:
        path.warnings.append("Tab thickness is not less than cut depth. Tabs were ignored.")
        tab_thickness = 0.0

    levels = depth_levels(depth, tool.stepdown)
    builder = PathBuilder(tool, job.safe_z)
    offset = 0.0 if side == "on" else tool.radius + allowance
    closed = [v for v in vectors if v.closed]
    open_vectors = [v for v in vectors if not v.closed]

    shape = region(closed) if closed else None
    rings = offset_rings(shape, side, offset, climb) if closed else []
    leading = lead_kind in ("arc", "line") and lead_length > 0
    if leading and side == "on":
        path.warnings.append("Leads need a side. They were skipped for a cut on the line.")
        leading = False
    if leading and ramp > 0:
        path.warnings.append("Leads replace the ramp, so the ramp was ignored.")
        ramp = 0.0
    if leading and rings:
        if side == "outside":
            part = shape.buffer(offset - 0.02, quad_segs=16)

            def fits(line):
                return line.disjoint(part)
        else:
            room = shape.buffer(-(offset - 0.02), join_style="mitre", mitre_limit=20)

            def fits(line):
                return room.covers(line)
    if closed and not rings:
        path.warnings.append("Tool is too large for the selected shape. No path was created.")
    if open_vectors and side != "on":
        path.warnings.append("Open vectors can only be cut on the line. They were skipped.")

    skipped = False
    if rings:
        depths = containment_depths(rings)
        for level_index in sorted(set(depths), reverse=True):
            group = [r for r, d in zip(rings, depths) if d == level_index]
            for ring in nearest_order(group, lambda r: r[0]):
                lead = None
                if leading:
                    ring = _at_longest_span(ring)
                    lead = lead_paths(ring, lead_kind, lead_length, fits)
                    if lead is None and not skipped:
                        skipped = True
                        path.warnings.append("A lead did not fit at one or more shapes. Those shapes plunge on the line.")
                cut_ring(builder, ring, levels, ramp, tab_count, tab_length, tab_thickness, lead)
                builder.retract()

    if side == "on":
        for vector in open_vectors:
            cut_open(builder, np.asarray(vector.points, dtype=float), levels)

    builder.retract()
    path.moves = builder.moves
    return [path]
