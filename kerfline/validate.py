from __future__ import annotations

import math
import re

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree
from shapely.validation import explain_validity

from .geometry import line_of
from .model import Vector

SHORT_LENGTH = 0.1
SMALL_AREA = 0.01


def _issue(kind: str, message: str, ids: list[str], point=None) -> dict:
    return {"kind": kind, "message": message, "vector_ids": ids, "point": point}


def _zero_spans(vector: Vector) -> int:
    points = np.asarray(vector.points, dtype=float)
    steps = np.hypot(*np.diff(points, axis=0).T)
    count = int((steps < 1e-6).sum())
    if vector.closed and math.dist(vector.points[0], vector.points[-1]) < 1e-6:
        count += 1
    return count


def validate(vectors: list[Vector]) -> list[dict]:
    issues: list[dict] = []
    lines: list[LineString] = []
    usable: list[Vector] = []
    for vector in vectors:
        if len(vector.points) < 2:
            issues.append(_issue("empty", "Vector has fewer than two points.", [vector.id]))
            continue
        zeros = _zero_spans(vector)
        if zeros:
            issues.append(_issue("zero", f"Vector has {zeros} zero-length span(s).", [vector.id]))
        line = line_of(vector)
        lines.append(line)
        usable.append(vector)
        if line.length < SHORT_LENGTH:
            issues.append(_issue("short", f"Vector is only {line.length:.3f} mm long.", [vector.id]))
        if vector.closed:
            shape = Polygon(vector.points) if len(vector.points) >= 3 else Polygon()
            if not shape.is_valid:
                found = re.search(r"\[(\S+) (\S+)\]", explain_validity(shape))
                point = [float(found[1]), float(found[2])] if found else None
                issues.append(_issue("self", "Closed vector crosses itself.", [vector.id], point))
            elif shape.is_empty or shape.area < SMALL_AREA:
                issues.append(_issue("flat", "Closed vector has almost no area.", [vector.id]))
        else:
            gap = math.dist(vector.points[0], vector.points[-1])
            issues.append(_issue("open", f"Open vector. Its ends are {gap:.3f} mm apart.", [vector.id], vector.points[0]))
            if not line.is_simple:
                issues.append(_issue("self", "Open vector crosses itself.", [vector.id]))

    if lines:
        tree = STRtree(lines)
        left, right = tree.query(lines, predicate="intersects")
        for a, b in zip(left.tolist(), right.tolist()):
            if a >= b:
                continue
            first, second = lines[a], lines[b]
            ids = [usable[a].id, usable[b].id]
            same = (
                usable[a].closed == usable[b].closed
                and shapely.hausdorff_distance(first, second) <= 0.01
            )
            if same:
                issues.append(_issue("duplicate", "Two vectors lie on top of each other.", ids))
            else:
                issues.append(_issue("cross", "Two vectors cross or touch.", ids))
    return issues
