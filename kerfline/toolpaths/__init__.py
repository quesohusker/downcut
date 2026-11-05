from __future__ import annotations

from ..model import Job, Operation
from .base import Toolpath, replicate
from .decor import build_chamfer, build_fluting, build_prism
from .drill import build_drill
from .pocket import build_pocket
from .profile import build_profile
from .vcarve import build_vcarve


def build_merge(job: Job, op: Operation) -> list[Toolpath]:
    tool = job.tool(op.tool_id)
    merged = Toolpath(op.id, op.name or "Merged toolpath", tool)
    extras: list[Toolpath] = []
    for row in op.params.get("operations", []):
        child = Operation(**row)
        if child.tool_id != op.tool_id:
            raise ValueError("Merged toolpaths must all use the same tool.")
        for path in build_operation(job, child):
            if path.tool.id == op.tool_id:
                merged.moves.extend(path.moves)
                merged.warnings.extend(path.warnings)
            else:
                path.operation_id = op.id
                extras.append(path)
    return [merged, *extras]


BUILDERS = {
    "profile": build_profile,
    "pocket": build_pocket,
    "drill": build_drill,
    "vcarve": build_vcarve,
    "chamfer": build_chamfer,
    "fluting": build_fluting,
    "prism": build_prism,
    "merge": build_merge,
}


def build_operation(job: Job, op: Operation) -> list[Toolpath]:
    return [replicate(path, op.params) for path in BUILDERS[op.kind](job, op)]


def build_all(job: Job) -> list[Toolpath]:
    paths: list[Toolpath] = []
    for op in job.operations:
        if op.enabled:
            paths.extend(build_operation(job, op))
    return paths
