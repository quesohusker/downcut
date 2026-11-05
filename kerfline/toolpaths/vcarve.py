from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import shapely
from scipy.spatial import Voronoi, cKDTree
from shapely.geometry import LineString, Polygon

from ..geometry import oriented, polygons, region, ring_array, rotate_ring
from ..model import Job, Operation
from .base import PathBuilder, Toolpath, depth_levels
from .pocket import clearing_rings, cut_rings

MIN_FEET_ANGLE = np.radians(14.0)
SHARP_CORNER = np.radians(150.0)


@dataclass
class Skeleton:
    chains: list[np.ndarray]


def _sample_boundary(poly: Polygon, spacing: float):
    points, ring_ids, stations, totals = [], [], [], []
    for ring_id, ring in enumerate([poly.exterior, *poly.interiors]):
        dense = shapely.segmentize(LineString(ring.coords), spacing)
        coords = np.asarray(dense.coords, dtype=float)[:, :2]
        lengths = np.hypot(*np.diff(coords, axis=0).T)
        along = np.concatenate([[0.0], np.cumsum(lengths)])
        points.append(coords[:-1])
        ring_ids.append(np.full(len(coords) - 1, ring_id))
        stations.append(along[:-1])
        totals.append(np.full(len(coords) - 1, along[-1]))
    return (
        np.vstack(points),
        np.concatenate(ring_ids),
        np.concatenate(stations),
        np.concatenate(totals),
    )


def _sharp_corners(poly: Polygon) -> np.ndarray:
    corners = []
    directed = oriented(poly, True)
    for ring in [directed.exterior, *directed.interiors]:
        pts = ring_array(ring)[:-1]
        before = pts - np.roll(pts, 1, axis=0)
        after = np.roll(pts, -1, axis=0) - pts
        cross = before[:, 0] * after[:, 1] - before[:, 1] * after[:, 0]
        dot = (before * after).sum(axis=1)
        turn = np.arctan2(cross, dot)
        interior = np.pi - turn
        corners.append(pts[(turn > 0) & (interior < SHARP_CORNER)])
    return np.vstack(corners) if corners else np.empty((0, 2))


def _chains_from_edges(edges: set[tuple[int, int]]) -> list[list[int]]:
    adjacent: dict[int, list[int]] = defaultdict(list)
    for a, b in edges:
        adjacent[a].append(b)
        adjacent[b].append(a)
    used: set[tuple[int, int]] = set()
    chains: list[list[int]] = []

    def key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    def walk(start: int, next_node: int) -> list[int]:
        chain = [start, next_node]
        used.add(key(start, next_node))
        previous, current = start, next_node
        while len(adjacent[current]) == 2:
            first, second = adjacent[current]
            following = second if first == previous else first
            if key(current, following) in used:
                break
            used.add(key(current, following))
            chain.append(following)
            previous, current = current, following
        return chain

    for node, neighbours in adjacent.items():
        if len(neighbours) != 2:
            for neighbour in neighbours:
                if key(node, neighbour) not in used:
                    chains.append(walk(node, neighbour))
    for node, neighbours in adjacent.items():
        for neighbour in neighbours:
            if key(node, neighbour) not in used:
                chains.append(walk(node, neighbour))
    return chains


def _inscribed_point(poly: Polygon) -> np.ndarray:
    line = shapely.maximum_inscribed_circle(poly, tolerance=0.005)
    x, y = line.coords[0]
    return np.array([[x, y, line.length]])


def medial_skeleton(poly: Polygon, spacing: float) -> Skeleton:
    samples, ring_ids, stations, totals = _sample_boundary(poly, spacing)
    voronoi = Voronoi(samples)
    pairs = voronoi.ridge_points
    ridges = np.array(voronoi.ridge_vertices)
    finite = (ridges >= 0).all(axis=1)
    pairs, ridges = pairs[finite], ridges[finite]

    a, b = pairs[:, 0], pairs[:, 1]
    same_ring = ring_ids[a] == ring_ids[b]
    gap = np.abs(stations[a] - stations[b])
    geodesic = np.minimum(gap, totals[a] - gap)
    distant = ~same_ring | (geodesic > 2.5 * spacing)

    vertices = voronoi.vertices
    middle = (vertices[ridges[:, 0]] + vertices[ridges[:, 1]]) / 2
    to_a = samples[a] - middle
    to_b = samples[b] - middle
    cosine = (to_a * to_b).sum(axis=1) / (
        np.hypot(*to_a.T) * np.hypot(*to_b.T) + 1e-12
    )
    open_feet = np.arccos(np.clip(cosine, -1.0, 1.0)) >= MIN_FEET_ANGLE

    shapely.prepare(poly)
    inside = shapely.contains_xy(poly, vertices[:, 0], vertices[:, 1])
    keep = distant & open_feet & inside[ridges[:, 0]] & inside[ridges[:, 1]]
    kept = ridges[keep]
    if len(kept) == 0:
        return Skeleton(chains=[_inscribed_point(poly)])

    used = np.unique(kept)
    snapped = np.round(vertices[used] / 1e-4).astype(np.int64)
    _, labels = np.unique(snapped, axis=0, return_inverse=True)
    labels = labels.ravel()
    node_of = {int(v): int(n) for v, n in zip(used, labels)}
    positions = np.zeros((labels.max() + 1, 2))
    positions[labels] = vertices[used]

    edges: set[tuple[int, int]] = set()
    for v0, v1 in kept:
        n0, n1 = node_of[int(v0)], node_of[int(v1)]
        if n0 != n1:
            edges.add((min(n0, n1), max(n0, n1)))

    tree = cKDTree(samples)
    radius, _ = tree.query(positions)
    chains = []
    for chain in _chains_from_edges(edges):
        chains.append(np.column_stack([positions[chain], radius[chain]]))
    if not chains:
        return Skeleton(chains=[_inscribed_point(poly)])
    return Skeleton(chains=_extend_to_corners(chains, poly))


def _extend_to_corners(chains: list[np.ndarray], poly: Polygon) -> list[np.ndarray]:
    corners = _sharp_corners(poly)
    if len(corners) == 0:
        return chains
    tree = cKDTree(corners)
    ends: dict[tuple[float, float], int] = defaultdict(int)
    for chain in chains:
        for point in (chain[0], chain[-1]):
            ends[(round(point[0], 4), round(point[1], 4))] += 1
    out = []
    for chain in chains:
        current = chain
        for at_head in (True, False):
            point = current[0] if at_head else current[-1]
            if ends[(round(point[0], 4), round(point[1], 4))] != 1:
                continue
            distance, index = tree.query(point[:2])
            if distance < 6.0 * point[2] + 1e-6:
                tip = np.array([[*corners[index], 0.0]])
                current = np.vstack([tip, current]) if at_head else np.vstack([current, tip])
        out.append(current)
    return out


def simplify_3d(points: np.ndarray, tolerance: float) -> np.ndarray:
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        origin = points[first]
        axis = points[last] - origin
        span = float(np.linalg.norm(axis))
        relative = points[first + 1 : last] - origin
        if span < 1e-12:
            distance = np.linalg.norm(relative, axis=1)
        else:
            distance = np.linalg.norm(np.cross(relative, axis / span), axis=1)
        index = int(np.argmax(distance))
        if distance[index] > tolerance:
            middle = first + 1 + index
            keep[middle] = True
            stack.append((first, middle))
            stack.append((middle, last))
    return points[keep]


def _spans_below(z: np.ndarray, level: float) -> list[tuple[int, int]]:
    deep = z < level - 1e-6
    spans = []
    index = 0
    count = len(z)
    while index < count:
        if deep[index]:
            start = index
            while index + 1 < count and deep[index + 1]:
                index += 1
            spans.append((max(0, start - 1), min(count - 1, index + 1)))
        index += 1
    return spans


def _order_chains(chains: list[np.ndarray]) -> list[np.ndarray]:
    if not chains:
        return []
    heads = np.array([c[0, :2] for c in chains])
    tails = np.array([c[-1, :2] for c in chains])
    alive = np.ones(len(chains), dtype=bool)
    ordered = []
    x, y = 0.0, 0.0
    for _ in chains:
        to_head = np.hypot(heads[:, 0] - x, heads[:, 1] - y)
        to_tail = np.hypot(tails[:, 0] - x, tails[:, 1] - y)
        to_head[~alive] = np.inf
        to_tail[~alive] = np.inf
        head_best, tail_best = int(np.argmin(to_head)), int(np.argmin(to_tail))
        if to_head[head_best] <= to_tail[tail_best]:
            chain = chains[head_best]
            alive[head_best] = False
        else:
            chain = chains[tail_best][::-1]
            alive[tail_best] = False
        ordered.append(chain)
        x, y = chain[-1, 0], chain[-1, 1]
    return ordered


def _cut_chain(builder, chain: np.ndarray, floor: float) -> None:
    z = np.maximum(chain[:, 2], floor)
    x0, y0 = chain[0, 0], chain[0, 1]
    connected = (
        builder.x is not None
        and builder.z < 0
        and np.hypot(builder.x - x0, builder.y - y0) < 1e-3
    )
    if connected:
        builder.cut(x0, y0, z[0])
    else:
        builder.go_to_cut(x0, y0, z[0])
    if len(chain) == 1:
        builder.cut(x0, y0, z[0])
    for (x, y), height in zip(chain[1:, :2], z[1:]):
        builder.cut(x, y, height)


def _cut_passes(builder, chains, rings, levels) -> None:
    previous = None
    for level in levels:
        pieces = []
        for chain in chains:
            if previous is None:
                pieces.append(chain)
            else:
                for start, end in _spans_below(chain[:, 2], previous):
                    pieces.append(chain[start : end + 1])
        for chain in _order_chains(pieces):
            _cut_chain(builder, chain, level)
        previous = level
    for ring, ring_z in rings:
        for level in levels:
            target = max(level, ring_z)
            start = rotate_ring(ring, builder.x or 0.0, builder.y or 0.0)
            builder.go_to_cut(start[0, 0], start[0, 1], target)
            for x, y in start[1:]:
                builder.cut(x, y, target)
            if target <= ring_z:
                break


def _split_at_flat(chain: np.ndarray, flat_radius: float) -> list[np.ndarray]:
    if len(chain) == 1:
        return [chain] if chain[0, 2] < flat_radius else []
    pieces = []
    run: list[np.ndarray] = []
    below = False
    for row in chain:
        clamped = np.array([row[0], row[1], flat_radius])
        if row[2] < flat_radius - 1e-6:
            run.append(row)
            below = True
            continue
        if below:
            run.append(clamped)
            pieces.append(np.array(run))
        run = [clamped]
        below = False
    if below and len(run) > 1:
        pieces.append(np.array(run))
    return pieces


def _corner_runs(chain: np.ndarray, low: float, high: float) -> list[np.ndarray]:
    mask = (chain[:, 2] >= low - 1e-6) & (chain[:, 2] <= high)
    runs = []
    index = 0
    while index < len(chain):
        if mask[index]:
            start = index
            while index + 1 < len(chain) and mask[index + 1]:
                index += 1
            if index > start:
                runs.append(chain[start : index + 1])
        index += 1
    return runs


def build_vcarve(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    params = op.params
    path = Toolpath(op.id, op.name or "V-carve", tool)
    if tool.kind != "vbit":
        path.warnings.append("V-carving needs a V-bit. No path was created.")
        return [path]

    tan_half = tool.tan_half
    tip = tool.tip_radius
    start = float(params.get("start_depth") or 0.0)
    tool_limit = tool.max_vee_depth
    flat_depth = float(params.get("flat_depth") or 0.0)
    if flat_depth <= 0 and params.get("max_depth"):
        flat_depth = float(params["max_depth"])

    shape = region(job.vectors_for(op.vector_ids))
    parts = polygons(shape)
    if not parts:
        path.warnings.append("V-carving needs closed vectors. No path was created.")
        return [path]

    deepest = 0.0
    all_chains: list[np.ndarray] = []
    flats = []
    limit = min(flat_depth, tool_limit) if flat_depth > 0 else tool_limit
    if start >= limit:
        path.warnings.append("Start depth is at or below the depth limit. No path was created.")
        return [path]
    flat_radius = (limit - start) * tan_half + tip
    clear_id = params.get("clear_tool_id")
    corner_reach = job.tool(clear_id).radius if clear_id else 0.0

    for poly in parts:
        spacing = float(np.clip(poly.length / 5000.0, 0.06, 0.4))
        skeleton = medial_skeleton(poly, spacing)
        widest = max((c[:, 2].max() for c in skeleton.chains), default=0.0)
        deepest = max(deepest, max(widest - tip, 0.0) / tan_half + start)
        if widest > flat_radius + 1e-6:
            flats.extend(polygons(poly.buffer(-flat_radius, quad_segs=16)))
        for chain in skeleton.chains:
            for piece in _split_at_flat(chain, flat_radius):
                carved = np.column_stack(
                    [piece[:, :2], -(np.maximum(piece[:, 2] - tip, 0.0) / tan_half + start)]
                )
                all_chains.append(simplify_3d(carved, 0.01))
            if corner_reach > 0:
                for run in _corner_runs(chain, flat_radius, flat_radius + corner_reach):
                    level = np.full(len(run), -limit)
                    all_chains.append(simplify_3d(np.column_stack([run[:, :2], level]), 0.01))

    if not all_chains and not flats:
        path.warnings.append("No V-carve path was found for this shape.")
        return [path]

    final = -min(deepest, limit)
    if final >= 0:
        path.warnings.append("V-carve depth is zero. The shape is too thin.")
        return [path]

    rings = []
    for flat in flats:
        directed = oriented(flat, True)
        for ring in [directed.exterior, *directed.interiors]:
            rings.append((ring_array(ring), final))

    levels = depth_levels(-final, tool.stepdown)
    builder = PathBuilder(tool, job.safe_z)
    _cut_passes(builder, all_chains, rings, levels)
    builder.retract()
    path.moves = builder.moves
    if -final > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("V-carve depth exceeds material thickness.")

    results = [path]
    if flats:
        if clear_id:
            results.append(_clearing_path(job, op, job.tool(clear_id), flats, final))
        else:
            path.warnings.append(
                f"Shape needs depth of {deepest:.2f} mm but the limit is {limit:.2f} mm. "
                "Add a clearing tool or the center stays uncut."
            )
    return results


def _clearing_path(job, op, tool, flats, final) -> Toolpath:
    path = Toolpath(op.id, f"{op.name or 'V-carve'} clearing", tool)
    builder = PathBuilder(tool, job.safe_z)
    stepover = tool.stepover or tool.diameter * 0.4
    groups = []
    for flat in flats:
        allowed = flat.buffer(-tool.radius, join_style="mitre", mitre_limit=20)
        if polygons(allowed):
            groups.append((allowed, clearing_rings(allowed, stepover, True)))
    if not groups:
        path.warnings.append("Clearing tool is too large for the flat area.")
    for level in depth_levels(-final, tool.stepdown):
        for allowed, rings in groups:
            builder.retract()
            cut_rings(builder, rings, allowed, level)
    builder.retract()
    path.moves = builder.moves
    return path


__all__ = ["build_vcarve", "medial_skeleton", "region"]
