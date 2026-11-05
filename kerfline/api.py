from __future__ import annotations

import base64
import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict

from . import (
    __version__,
    edit,
    exporters,
    importers,
    inlay,
    layout,
    nesting,
    sample,
    shapes,
    templates,
    textvec,
    tiling,
    validate,
    vtdb,
)
from .geometry import polygon_from
from .gcode import POSTS, Post, export, origin_shift, post_for
from .model import Job, Material, Operation, Vector, default_tools, job_from_dict, job_to_dict
from .ppfile import STARTER, parse_pp, render_pp
from .sheet import setup_html
from .simulate import render, simulate
from .toolpaths import build_all, build_operation
from .toolpaths.base import RAPID
from .verify import check, limits_for

CACHE_SIZE = 64
_cache: OrderedDict[str, list] = OrderedDict()


def _vector_rows(vectors: list[Vector]) -> list[dict]:
    return [asdict(v) for v in vectors]


def _vectors(rows: list[dict]) -> list[Vector]:
    return [Vector(**row) for row in rows]


def _used(op: Operation, vector_ids: set, tool_ids: set) -> None:
    vector_ids.update(op.vector_ids)
    tool_ids.update({op.tool_id, op.params.get("clear_tool_id")})
    for row in op.params.get("operations", []):
        _used(Operation(**row), vector_ids, tool_ids)


def _operation_key(job, op) -> str:
    wanted: set = set()
    tool_ids: set = set()
    _used(op, wanted, tool_ids)
    payload = {
        "op": [op.kind, op.tool_id, op.params, op.name],
        "vectors": [[v.points, v.closed] for v in job.vectors if v.id in wanted],
        "tools": [asdict(t) for t in job.tools if t.id in tool_ids],
        "safe_z": job.safe_z,
        "thickness": job.material.thickness,
        "allowance": job.spoilboard_allowance,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _build(job) -> list:
    paths = []
    for op in job.operations:
        if not op.enabled:
            continue
        key = _operation_key(job, op)
        if key in _cache:
            _cache.move_to_end(key)
            built = _cache[key]
            for path in built:
                path.operation_id = op.id
        else:
            built = build_operation(job, op)
            _cache[key] = built
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
        paths.extend(built)
    return paths


def _display(path) -> dict:
    runs = []
    current = None
    last = None
    for code, x, y, _, _ in path.moves:
        kind = "rapid" if code == RAPID else "cut"
        if current is None or current["kind"] != kind:
            current = {"kind": kind, "points": [last] if last else []}
            runs.append(current)
        last = [round(x, 3), round(y, 3)]
        current["points"].append(last)
    return {
        "operation_id": path.operation_id,
        "name": path.name,
        "tool": path.tool.name,
        "warnings": path.warnings,
        "runs": [r for r in runs if len(r["points"]) > 1],
    }


def _programs(job, paths, post_id: str, title: str) -> list[dict]:
    post = post_for(job, post_id)
    strict = isinstance(post, Post)
    spindle = strict or not post.manual_spindle
    rows = []
    for tile in tiling.tiles(job, paths):
        name = f"{title}-{tile.label}" if tile.label else title
        for item in export(tile.job, tile.paths, post_id, name):
            tool = tile.job.tool(item.tool_id)
            report = check(item.text, limits_for(tile.job, tool, strict, spindle))
            ox, oy, oz = origin_shift(tile.job)
            rows.append(
                {
                    "file": item,
                    "tool": tool,
                    "report": report,
                    "shift": (ox + tile.x, oy + tile.y, oz),
                    "tile": tile.label,
                }
            )
    return rows


def _notices(job) -> list[str]:
    notes = []
    bed_w = float(job.machine.get("width") or 0.0)
    bed_h = float(job.machine.get("height") or 0.0)
    if bed_w > 0 and bed_h > 0 and not (job.tiling or {}).get("enabled"):
        if job.material.width > bed_w + 1e-6 or job.material.height > bed_h + 1e-6:
            notes.append(f"The sheet is larger than {job.machine.get('name', 'the machine')}. Turn on tiling.")
    return notes


def bootstrap() -> dict:
    return {
        "version": __version__,
        "tools": [asdict(t) for t in default_tools()],
        "posts": [{"id": p.id, "name": p.name} for p in POSTS.values()],
        "fonts": [row["name"] for row in textvec.list_fonts()],
        "stroke_fonts": list(textvec.STROKE_FONTS),
        "starter_post": STARTER,
    }


def sample_job() -> dict:
    return job_to_dict(sample.sign_job())


def import_vectors(body: dict) -> dict:
    if body.get("data"):
        data = base64.b64decode(body["data"])
    else:
        data = body["text"].encode("utf-8")
    vectors = importers.import_binary(body["name"], data, body.get("units", "mm"))
    if not vectors:
        raise ValueError("No usable vectors were found in that file.")
    return {"vectors": _vector_rows(vectors)}


def make_shape(body: dict) -> dict:
    kind = body["kind"]
    p = body["params"]
    if kind == "rectangle":
        vector = shapes.rectangle(p["x"], p["y"], p["width"], p["height"], p.get("radius", 0.0))
    elif kind == "ellipse":
        vector = shapes.ellipse(p["cx"], p["cy"], p["rx"], p["ry"])
    elif kind == "polygon":
        vector = shapes.polygon(p["cx"], p["cy"], p["radius"], int(p.get("sides", 6)))
    elif kind == "star":
        vector = shapes.star(p["cx"], p["cy"], p["radius"], p["radius"] * 0.4, int(p.get("points", 5)))
    elif kind == "polyline":
        vector = shapes.polyline(p["points"], bool(p.get("closed", False)))
    elif kind == "curve":
        vector = shapes.curve(p["points"], bool(p.get("closed", False)))
    elif kind == "arc":
        vector = shapes.three_point_arc(*p["points"])
    else:
        raise ValueError(f"Unknown shape: {kind}")
    return {"vectors": _vector_rows([vector])}


def make_text(body: dict) -> dict:
    name = body["font"]
    height = float(body["height"])
    align = body.get("align", "left")
    tracking = float(body.get("tracking", 0.0))
    kerns = body.get("kerns") or None
    face = textvec.STROKE_FONTS.get(name)
    font = None
    if face is None:
        fonts = {row["name"]: row for row in textvec.list_fonts()}
        font = fonts.get(name)
        if font is None:
            raise ValueError(f"Font not found: {name}")
    warning = ""
    if body.get("path"):
        path = Vector(**body["path"])
        vectors, width = textvec.text_on_path(
            body["text"],
            path,
            height,
            align,
            float(body.get("raise_by", 0.0)),
            bool(body.get("flip", False)),
            tracking,
            kerns,
            font["path"] if font else None,
            font["index"] if font else 0,
            face,
        )
        if width > layout.path_length(path):
            warning = "The text is longer than the curve. Letters bunch at the end."
    elif face:
        vectors = textvec.stroke_vectors(
            body["text"],
            face,
            height,
            float(body.get("x", 0.0)),
            float(body.get("y", 0.0)),
            align,
            tracking,
            kerns,
        )
    else:
        vectors = textvec.text_vectors(
            body["text"],
            font["path"],
            height,
            float(body.get("x", 0.0)),
            float(body.get("y", 0.0)),
            align,
            font["index"],
            tracking,
            kerns,
        )
    if not vectors:
        raise ValueError("That text produced no shapes in this font.")
    return {"vectors": _vector_rows(vectors), "warning": warning}


def edit_vectors(body: dict) -> dict:
    vectors = _vectors(body["vectors"])
    operation = body["operation"]
    removed: list[str] = []
    if operation in ("weld", "subtract", "intersect"):
        result = edit.combine(vectors, operation)
    elif operation == "offset":
        result = edit.offset(vectors, float(body["distance"]), bool(body.get("sharp", False)))
    elif operation == "join":
        result = edit.join(vectors, float(body.get("tolerance", 0.01)))
    elif operation == "close":
        result = edit.close(vectors)
    elif operation == "smooth":
        result = edit.smooth(vectors, float(body.get("tolerance", 0.25)), int(body.get("passes", 3)))
    elif operation == "simplify":
        result = edit.simplify(vectors, float(body.get("tolerance", 0.05)))
    elif operation == "dedupe":
        result, removed = edit.remove_duplicates(vectors, float(body.get("tolerance", 0.01)))
    elif operation == "fillet":
        result = edit.fillet(vectors, float(body["radius"]))
    elif operation in ("dogbone", "tbone"):
        style = "dog" if operation == "dogbone" else "tbone"
        result = edit.bone_corners(vectors, float(body["diameter"]), style, body.get("side", "inside"))
    elif operation == "trim_boundary":
        boundary = Vector(**body["boundary"])
        result = edit.trim_to_boundary(vectors, boundary, body.get("keep", "inside"))
    else:
        raise ValueError(f"Unknown edit: {operation}")
    if not result:
        raise ValueError("That edit produced no shape.")
    return {"vectors": _vector_rows(result), "removed": removed}


def trim_vector(body: dict) -> dict:
    pieces = edit.trim_at(Vector(**body["target"]), _vectors(body["cutters"]), float(body["x"]), float(body["y"]))
    return {"vectors": _vector_rows(pieces)}


def extend_vector(body: dict) -> dict:
    target = edit.extend_to(Vector(**body["target"]), _vectors(body["cutters"]), float(body["x"]), float(body["y"]))
    return {"vectors": _vector_rows([target])}


def layout_vectors(body: dict) -> dict:
    vectors = _vectors(body["vectors"])
    kind = body["kind"]
    if kind == "grid":
        copies = layout.grid_array(
            vectors,
            int(body["columns"]),
            int(body["rows"]),
            float(body["step_x"]),
            float(body["step_y"]),
        )
    elif kind == "circle":
        copies = layout.circular_array(
            vectors,
            int(body["count"]),
            float(body["cx"]),
            float(body["cy"]),
            float(body.get("sweep", 360.0)),
            bool(body.get("rotate", True)),
        )
    elif kind == "path":
        copies = layout.along_path(vectors, Vector(**body["path"]), int(body["count"]), bool(body.get("rotate", True)))
    else:
        raise ValueError(f"Unknown layout: {kind}")
    return {"vectors": _vector_rows(copies)}


def nest_vectors(body: dict) -> dict:
    vectors = _vectors(body["vectors"])
    keepouts = [polygon_from(v.points) for v in _vectors(body.get("keepouts", []))]
    step = int(body.get("rotation_step", 90))
    rotations = tuple(range(0, 360, step)) if step > 0 else (0,)
    result = nesting.nest(
        vectors,
        float(body["width"]),
        float(body["height"]),
        float(body.get("gap", 6.35)),
        float(body.get("margin", 0.0)),
        rotations,
        keepouts,
    )
    return {
        "vectors": _vector_rows(result["vectors"]),
        "unplaced": result["unplaced"],
        "parts": result["parts"],
        "placed": result["placed"],
        "used_percent": result["used_percent"],
    }


def validate_vectors(body: dict) -> dict:
    return {"issues": validate.validate(_vectors(body["vectors"]))}


def export_vectors(body: dict) -> dict:
    vectors = _vectors(body["vectors"])
    title = body.get("title", "vectors")
    if body["format"] == "svg":
        text = exporters.to_svg(vectors, float(body["width"]), float(body["height"]))
        return {"name": f"{title}.svg", "text": text}
    if body["format"] == "dxf":
        return {"name": f"{title}.dxf", "text": exporters.to_dxf(vectors)}
    raise ValueError("Export supports SVG and DXF.")


def make_inlay(body: dict) -> dict:
    job = job_from_dict(body["job"])
    maker = inlay.vcarve_inlay if body["kind"] == "vcarve" else inlay.auto_inlay
    result = maker(job, body)
    return {
        "vectors": _vector_rows(result["vectors"]),
        "operations": [asdict(op) for op in result["operations"]],
        "layers": result["layers"],
        "notes": result["notes"],
    }


def make_template(body: dict) -> dict:
    return templates.make_template(job_from_dict(body["job"]), body.get("operation_ids"))


def apply_template(body: dict) -> dict:
    job = job_from_dict(body["job"])
    made = templates.apply_template(job, body["template"], body.get("vector_ids"))
    return {"operations": [asdict(op) for op in made]}


def _sample_run(post_row: dict) -> tuple:
    job = Job(material=Material(100.0, 100.0, 12.0), units="mm", tools=default_tools(), arc_output=True)
    vector = shapes.circle(50.0, 50.0, 25.0)
    job.vectors.append(vector)
    job.operations.append(
        Operation("profile", "em-250", [vector.id], {"side": "outside", "depth": 3.0}, "Sample profile")
    )
    job.custom_posts = [post_row]
    return job, build_all(job)


def check_post(body: dict) -> dict:
    source = body["source"]
    post = parse_pp(source, "check")
    job, paths = _sample_run({"id": "check", "name": post.name, "source": source})
    text = render_pp(post, job, paths, "Sample", f"sample.{post.extension}")
    tool = job.tools[0]
    report = check(text, limits_for(job, tool, False, not post.manual_spindle))
    return {
        "name": post.name,
        "extension": post.extension,
        "units": post.units,
        "notes": post.notes,
        "arcs": post.supports_arcs,
        "sample": text,
        "report": report.to_dict(),
    }


def import_machine(body: dict) -> dict:
    data = base64.b64decode(body["data"])
    name = body["name"]
    lowered = name.lower()
    if lowered.endswith(".pp"):
        source = data.decode("utf-8", "replace")
        summary = check_post({"source": source})
        return {"kind": "post", "source": source, **summary}
    if lowered.endswith(".vtdb"):
        return {"kind": "tools", **vtdb.read_vtdb(data)}
    raise ValueError("Pick a Vectric post processor (.pp) or tool database (.vtdb).")


def compute(body: dict) -> dict:
    job = job_from_dict(body["job"])
    paths = _build(job)
    rows = _programs(job, paths, body.get("post", "grbl"), body.get("title", "job"))
    return {
        "paths": [_display(path) for path in paths],
        "programs": [{"tool": row["tool"].name, "tile": row["tile"], **row["report"].to_dict()} for row in rows],
        "notices": _notices(job),
    }


def _surface(job, rows) -> tuple:
    programs = [(row["tool"], row["file"].text, row["shift"]) for row in rows]
    heights, resolution = simulate(job, programs)
    return heights, resolution, render(heights, resolution, job.material.thickness)


def _height_map(heights, resolution: float) -> dict:
    stride = max(1, max(heights.shape) // 460)
    small = heights[::stride, ::stride].astype("<f4")
    return {
        "heights": base64.b64encode(small.tobytes()).decode(),
        "nx": int(small.shape[1]),
        "ny": int(small.shape[0]),
        "step": resolution * stride,
    }


def preview(body: dict) -> dict:
    job = job_from_dict(body["job"])
    rows = _programs(job, _build(job), body.get("post", "grbl"), "preview")
    heights, resolution, png = _surface(job, rows)
    return {
        "image": "data:image/png;base64," + base64.b64encode(png).decode(),
        "surface": _height_map(heights, resolution),
        "programs": [{"tool": row["tool"].name, "tile": row["tile"], **row["report"].to_dict()} for row in rows],
    }


def gcode(body: dict) -> dict:
    job = job_from_dict(body["job"])
    rows = _programs(job, _build(job), body.get("post", "grbl"), body.get("title", "job"))
    return {
        "files": [
            {
                "name": row["file"].name,
                "tool": row["tool"].name,
                "text": row["file"].text,
                "report": row["report"].to_dict(),
            }
            for row in rows
        ]
    }


def setup_sheet(body: dict) -> dict:
    job = job_from_dict(body["job"])
    title = body.get("title", "job")
    post_id = body.get("post", "grbl")
    paths = _build(job)
    rows = _programs(job, paths, post_id, title)
    _, _, png = _surface(job, rows)
    image = "data:image/png;base64," + base64.b64encode(png).decode()
    html = setup_html(job, rows, paths, image, title, post_for(job, post_id).name)
    return {"name": f"{title}-setup-sheet.html", "html": html}
