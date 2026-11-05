from __future__ import annotations

import numpy as np

from ..geometry import nearest_order, polygon_from
from ..model import Job, Operation
from .base import RAPID, PathBuilder, Toolpath, flute_warning


def hole_centers(vectors) -> list[tuple[float, float]]:
    centers = []
    for vector in vectors:
        if vector.closed:
            centroid = polygon_from(vector.points).centroid
            centers.append((centroid.x, centroid.y))
        else:
            first = np.asarray(vector.points[0], dtype=float)
            centers.append((float(first[0]), float(first[1])))
    return centers


def build_drill(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    depth = float(op.params["depth"])
    peck = float(op.params.get("peck", 0.0))

    path = Toolpath(op.id, op.name or "Drill", tool)
    path.warnings.extend(flute_warning(tool, depth))
    if depth > job.material.thickness + job.spoilboard_allowance:
        path.warnings.append("Drill depth exceeds material thickness.")

    builder = PathBuilder(tool, job.safe_z)
    for x, y in nearest_order(hole_centers(job.vectors_for(op.vector_ids)), lambda c: c):
        builder.travel(x, y)
        builder.enter(-min(peck, depth) if peck > 0 else -depth)
        reached = -builder.z
        while reached < depth - 1e-9:
            builder.moves.append((RAPID, x, y, builder.gap, 0.0))
            builder.z = builder.gap
            reached = min(reached + peck, depth)
            builder.cut(x, y, -reached)
        builder.retract()
    path.moves = builder.moves
    return [path]
