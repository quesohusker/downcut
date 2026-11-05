from __future__ import annotations

import math

import numpy as np
import shapely
from scipy.ndimage import median_filter
from scipy.spatial import Voronoi

from ..geometry import path_length, polygons, region
from ..model import Job, Operation
from .base import PathBuilder, Toolpath, depth_levels, flute_warning
from .profile import cut_open, cut_ring, offset_rings
from .vcarve import _cut_chain, _sample_boundary, simplify_3d


def build_chamfer(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    path = Toolpath(op.id, op.name or "Chamfer", tool)
    if tool.kind != "vbit":
        path.warnings.append("Chamfers need a V-bit. No path was created.")
        return [path]
    width = float(params["width"])
    clearance = float(params.get("clearance") or 0.0)
    side = params.get("side", "outside")
    climb = bool(params.get("climb", True))
    reach = width + clearance
    depth = max(reach - tool.tip_radius, 0.0) / tool.tan_half
    if reach > tool.radius + 1e-9:
        path.warnings.append(
            f"Chamfer needs {reach:.2f} mm of cutting radius and {tool.name} has {tool.radius:.2f} mm."
        )
    path.warnings.extend(flute_warning(tool, depth))
    if depth > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Chamfer depth exceeds material thickness.")

    vectors = job.vectors_for(op.vector_ids)
    closed = [v for v in vectors if v.closed]
    open_vectors = [v for v in vectors if not v.closed]
    levels = depth_levels(depth, tool.stepdown)
    builder = PathBuilder(tool, job.safe_z)
    if closed:
        rings = offset_rings(region(closed), side, clearance, climb, sharp=True)
        if not rings:
            path.warnings.append("Clearance leaves no path inside the shape.")
        for ring in rings:
            cut_ring(builder, ring, levels, 0.0, 0, 0.0, 0.0)
            builder.retract()
    if open_vectors and side != "on":
        path.warnings.append("Open vectors can only be chamfered on the line. They were skipped.")
    if side == "on":
        for vector in open_vectors:
            cut_open(builder, np.asarray(vector.points, dtype=float), levels)
    builder.retract()
    path.moves = builder.moves
    return [path]


def flute_scale(stations: np.ndarray, total: float, kind: str, ramp: float, smooth: bool) -> np.ndarray:
    scale = np.ones_like(stations)
    if ramp > 0 and kind in ("start", "both"):
        scale = np.minimum(scale, stations / ramp)
    if ramp > 0 and kind in ("end", "both"):
        scale = np.minimum(scale, (total - stations) / ramp)
    scale = np.clip(scale, 0.0, 1.0)
    return np.sin(scale * math.pi / 2) ** 2 if smooth else scale


def build_fluting(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    depth = float(params["depth"])
    kind = params.get("ramp", "both")
    ramp = float(params.get("ramp_length") or 0.0)
    smooth = bool(params.get("smooth", False))

    path = Toolpath(op.id, op.name or "Fluting", tool)
    path.warnings.extend(flute_warning(tool, depth))
    if depth > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Cut depth exceeds material thickness.")

    builder = PathBuilder(tool, job.safe_z)
    levels = depth_levels(depth, tool.stepdown)
    for vector in job.vectors_for(op.vector_ids):
        points = np.asarray(vector.points, dtype=float)
        if vector.closed:
            points = np.vstack([points, points[0]])
        total = path_length(points)
        usable = min(ramp, total / 2) if kind == "both" else min(ramp, total)
        if ramp > usable + 1e-9 and not any("shortened" in w for w in path.warnings):
            path.warnings.append("Ramp was shortened because the vector is too short.")
        spacing = 0.5
        line = shapely.segmentize(shapely.linestrings(points), spacing)
        dense = shapely.get_coordinates(line)
        stations = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(dense, axis=0).T))])
        scale = flute_scale(stations, stations[-1], kind, usable, smooth)
        for level in levels:
            heights = level * scale
            builder.go_to_cut(dense[0, 0], dense[0, 1], heights[0])
            for (x, y), z in zip(dense[1:], heights[1:]):
                builder.cut(x, y, z)
            builder.retract()
    builder.retract()
    path.moves = builder.moves
    return [path]


def boundary_radii(poly, spacing: float):
    samples, ring_ids, _, _ = _sample_boundary(poly, spacing)
    voronoi = Voronoi(samples)
    vertices = voronoi.vertices
    shapely.prepare(poly)
    inside = shapely.contains_xy(poly, vertices[:, 0], vertices[:, 1])
    radii = np.zeros(len(samples))
    for index, region_index in enumerate(voronoi.point_region):
        ids = [v for v in voronoi.regions[region_index] if v >= 0 and inside[v]]
        if ids:
            radii[index] = float(np.hypot(*(vertices[ids] - samples[index]).T).max())
    return samples, ring_ids, radii


def build_prism(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    path = Toolpath(op.id, op.name or "Raised prism", tool)
    if tool.kind != "vbit":
        path.warnings.append("Prisms need a V-bit. No path was created.")
        return [path]
    limit = tool.max_vee_depth
    wanted = float(params.get("max_depth") or 0.0)
    if wanted > 0:
        limit = min(limit, wanted)

    parts = polygons(region(job.vectors_for(op.vector_ids)))
    if not parts:
        path.warnings.append("Prisms need closed vectors. No path was created.")
        return [path]

    chains: list[np.ndarray] = []
    deepest = 0.0
    clipped = False
    for poly in parts:
        spacing = float(np.clip(poly.length / 4000.0, 0.06, 0.4))
        samples, ring_ids, radii = boundary_radii(poly, spacing)
        for ring in np.unique(ring_ids):
            mask = ring_ids == ring
            points = samples[mask]
            smooth_radii = median_filter(radii[mask], size=5, mode="wrap")
            depth = np.maximum(smooth_radii - tool.tip_radius, 0.0) / tool.tan_half
            if depth.max() > limit + 1e-9:
                clipped = True
            depth = np.minimum(depth, limit)
            deepest = max(deepest, float(depth.max()))
            shallow = int(np.argmin(depth))
            order = np.roll(np.arange(len(points)), -shallow)
            loop = np.column_stack([points[order], -depth[order]])
            chains.append(simplify_3d(np.vstack([loop, loop[0]]), 0.02))

    if deepest <= 0:
        path.warnings.append("Prism depth is zero. The shape is too thin.")
        return [path]
    if clipped:
        path.warnings.append("Shape is wider than the bit can reach. The top of the prism stays flat.")
    path.warnings.extend(flute_warning(tool, deepest))
    if deepest > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Prism depth exceeds material thickness.")

    builder = PathBuilder(tool, job.safe_z)
    for level in depth_levels(deepest, tool.stepdown):
        for chain in chains:
            _cut_chain(builder, chain, level)
            builder.retract()
    builder.retract()
    path.moves = builder.moves
    return [path]
