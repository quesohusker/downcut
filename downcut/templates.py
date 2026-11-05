from __future__ import annotations

from dataclasses import asdict

from .model import Job, Operation, new_id

FORMAT = "downcut-template"
VERSION = 1


def _row(job: Job, op: Operation) -> dict:
    tool = job.tool(op.tool_id)
    params = {k: v for k, v in op.params.items()}
    through = None
    depth = params.get("depth")
    if depth is not None and float(depth) >= job.material.thickness - 1e-6:
        through = float(depth) - job.material.thickness
    layers = sorted({v.layer for v in job.vectors_for(op.vector_ids)})
    return {
        "kind": op.kind,
        "name": op.name,
        "tool_id": op.tool_id,
        "tool_name": tool.name,
        "params": params,
        "layers": layers,
        "through_extra": through,
    }


def make_template(job: Job, operation_ids: list[str] | None = None) -> dict:
    wanted = set(operation_ids) if operation_ids else None
    rows: list[dict] = []
    for op in job.operations:
        if wanted is not None and op.id not in wanted:
            continue
        if op.kind == "merge":
            rows.extend(_row(job, Operation(**child)) for child in op.params.get("operations", []))
        else:
            rows.append(_row(job, op))
    if not rows:
        raise ValueError("There are no toolpaths to save.")
    return {"format": FORMAT, "version": VERSION, "operations": rows}


def apply_template(job: Job, template: dict, vector_ids: list[str] | None = None) -> list[Operation]:
    if template.get("format") != FORMAT:
        raise ValueError("That is not a Downcut toolpath template.")
    by_id = {tool.id: tool for tool in job.tools}
    by_name = {tool.name: tool for tool in job.tools}
    missing = []
    made: list[Operation] = []
    for row in template["operations"]:
        tool = by_id.get(row["tool_id"]) or by_name.get(row["tool_name"])
        if tool is None:
            missing.append(row["tool_name"])
            continue
        if vector_ids:
            ids = list(vector_ids)
        else:
            layers = set(row.get("layers") or [])
            ids = [v.id for v in job.vectors if v.layer in layers]
        params = dict(row["params"])
        if row.get("through_extra") is not None:
            params["depth"] = job.material.thickness + float(row["through_extra"])
        made.append(Operation(row["kind"], tool.id, ids, params, row.get("name", ""), new_id()))
    if missing:
        raise ValueError("These tools are not in this job: " + ", ".join(sorted(set(missing))))
    return made


__all__ = ["make_template", "apply_template", "asdict"]
