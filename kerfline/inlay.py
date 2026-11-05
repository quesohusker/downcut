from __future__ import annotations

from shapely.geometry import box

from .geometry import bounds, polygons, region, vectors_from_geometry
from .model import Job, Operation, Vector, new_id, new_layer

PLUG_LAYER = "Inlay plug"
POCKET_LAYER = "Inlay pocket"


def _ensure_layer(job: Job, name: str) -> dict | None:
    if any(layer["name"] == name for layer in job.layers):
        return None
    layer = new_layer(name)
    job.layers.append(layer)
    return layer


def _mirrored(vectors: list[Vector], layer: str) -> list[Vector]:
    x0, _, x1, _ = bounds(vectors)
    middle = x0 + x1
    return [
        Vector(points=[[middle - x, y] for x, y in v.points], closed=v.closed, layer=layer)
        for v in vectors
    ]


def _free_shift(job: Job, plug: list[Vector], used: list[Vector], gap: float) -> tuple[float, float] | None:
    x0, y0, x1, y1 = bounds(plug)
    width, height = x1 - x0, y1 - y0
    taken = [box(*bounds([v])) for v in job.vectors + used if v.points]
    sheet = box(0, 0, job.material.width, job.material.height)
    a0, b0, a1, b1 = bounds(used)
    choices = [
        (a1 + gap - x0, 0.0),
        (a0 - gap - width - x0, 0.0),
        (0.0, b1 + gap - y0),
        (0.0, b0 - gap - height - y0),
    ]
    for dx, dy in choices:
        spot = box(x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        if sheet.covers(spot) and not any(spot.buffer(gap / 2).intersects(t) for t in taken):
            return dx, dy
    return None


def _shift(vectors: list[Vector], dx: float, dy: float) -> None:
    for vector in vectors:
        vector.points = [[x + dx, y + dy] for x, y in vector.points]


def _place(job: Job, plug: list[Vector], used: list[Vector], gap: float, notes: list[str]) -> bool:
    shift = _free_shift(job, plug, used, gap)
    if shift is None:
        notes.append(
            "The plug did not fit beside the pocket, so it sits on top of it and its toolpath is off. "
            "Turn the pocket off and the plug on when you cut the plug stock."
        )
        return False
    _shift(plug, *shift)
    return True


def auto_inlay(job: Job, body: dict) -> dict:
    tool = job.tool(body["tool_id"])
    depth = float(body["depth"])
    clearance = float(body.get("clearance") or 0.0)
    extra = float(body.get("plug_extra") or 0.0)
    chosen = job.vectors_for(body["vector_ids"])
    shape = region([v for v in chosen if v.closed])
    if not polygons(shape):
        raise ValueError("Auto-inlay needs closed vectors.")
    if tool.kind != "endmill":
        raise ValueError("Auto-inlay needs an end mill.")
    radius = tool.radius
    fitted = shape.buffer(-radius, quad_segs=16).buffer(2 * radius, quad_segs=16).buffer(-radius, quad_segs=16)
    if not polygons(fitted):
        raise ValueError("The tool is too large for that shape.")

    notes: list[str] = []
    layers = [row for row in (_ensure_layer(job, POCKET_LAYER), _ensure_layer(job, PLUG_LAYER)) if row]
    pocket_vectors = vectors_from_geometry(fitted, POCKET_LAYER)
    plug_shape = fitted.buffer(-clearance, quad_segs=16) if clearance > 0 else fitted
    plug_vectors = _mirrored(vectors_from_geometry(plug_shape, PLUG_LAYER), PLUG_LAYER)
    beside = _place(job, plug_vectors, pocket_vectors, tool.diameter * 2, notes)

    pocket = Operation(
        "pocket",
        tool.id,
        [v.id for v in pocket_vectors],
        {"depth": depth, "climb": True},
        "Inlay pocket",
    )
    plug = Operation(
        "profile",
        tool.id,
        [v.id for v in plug_vectors],
        {"side": "outside", "depth": depth + extra, "climb": True},
        "Inlay plug",
        enabled=beside,
    )
    return {"vectors": pocket_vectors + plug_vectors, "operations": [pocket, plug], "layers": layers, "notes": notes}


def vcarve_inlay(job: Job, body: dict) -> dict:
    tool = job.tool(body["tool_id"])
    if tool.kind != "vbit":
        raise ValueError("V-carve inlays need a V-bit.")
    depth = float(body["depth"])
    gap = float(body.get("gap") or 0.0)
    margin = float(body.get("margin") or 0.0) or tool.diameter
    clear_id = body.get("clear_tool_id") or ""
    if gap >= depth:
        raise ValueError("The glue gap must be less than the pocket depth.")
    if depth > tool.max_vee_depth:
        raise ValueError(
            f"That depth needs more bit than {tool.name} has. It reaches {tool.max_vee_depth:.2f} mm."
        )
    chosen = [v for v in job.vectors_for(body["vector_ids"]) if v.closed]
    if not chosen:
        raise ValueError("V-carve inlays need closed vectors.")

    notes: list[str] = []
    layers = [row for row in (_ensure_layer(job, PLUG_LAYER),) if row]
    mirrored = _mirrored(chosen, PLUG_LAYER)
    x0, y0, x1, y1 = bounds(mirrored)
    edge = Vector(
        points=[
            [x0 - margin, y0 - margin],
            [x1 + margin, y0 - margin],
            [x1 + margin, y1 + margin],
            [x0 - margin, y1 + margin],
        ],
        closed=True,
        layer=PLUG_LAYER,
    )
    plug_vectors = [*mirrored, edge]
    beside = _place(job, plug_vectors, chosen, tool.diameter, notes)

    params = {"flat_depth": depth}
    if clear_id:
        params["clear_tool_id"] = clear_id
    pocket = Operation("vcarve", tool.id, [v.id for v in chosen], dict(params), "V inlay pocket")
    plug = Operation(
        "vcarve",
        tool.id,
        [v.id for v in plug_vectors],
        {**params, "start_depth": depth - gap},
        "V inlay plug",
        enabled=beside,
    )
    return {"vectors": plug_vectors, "operations": [pocket, plug], "layers": layers, "notes": notes}


__all__ = ["auto_inlay", "vcarve_inlay", "new_id"]
