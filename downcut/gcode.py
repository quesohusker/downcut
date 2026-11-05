from __future__ import annotations

import re
from dataclasses import dataclass

from .arcfit import fit_arcs
from .model import MM_PER_IN, XY_ORIGINS, Job
from .ppfile import PpPost, parse_pp, render_pp
from .toolpaths.base import RAPID, Toolpath


@dataclass(frozen=True)
class Post:
    id: str
    name: str
    extension: str
    preamble: str
    end_code: str
    wrapper: str = ""


POSTS = {
    "grbl": Post("grbl", "GRBL", "nc", "G90 G94 G17", "M30"),
    "mach3": Post("mach3", "Mach3 / Mach4", "tap", "G90 G94 G17 G40", "M30"),
    "linuxcnc": Post("linuxcnc", "LinuxCNC", "ngc", "G90 G94 G17 G40 G49", "M2", "%"),
}


@dataclass
class GcodeFile:
    name: str
    tool_id: str
    tool_name: str
    text: str


def _number(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _note(text: str) -> str:
    return "(" + re.sub(r"[()]", "", text) + ")"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "tool"


def origin_shift(job: Job) -> tuple[float, float, float]:
    fx, fy = XY_ORIGINS.get(job.xy_origin, (0.0, 0.0))
    ox, oy = job.material.width * fx, job.material.height * fy
    oz = job.material.thickness if job.z_zero == "bottom" else 0.0
    return ox, oy, oz


def post_for(job: Job, post_id: str) -> Post | PpPost:
    if post_id in POSTS:
        return POSTS[post_id]
    for row in job.custom_posts:
        if row["id"] == post_id:
            return parse_pp(row["source"], post_id)
    raise ValueError(f"Unknown machine post: {post_id}")


def program(job: Job, paths: list[Toolpath], post_id: str, title: str = "Downcut job", name: str = "") -> str:
    post = post_for(job, post_id)
    if isinstance(post, PpPost):
        return render_pp(post, job, paths, title, name or "job")
    tool = paths[0].tool
    inches = job.units == "in"
    scale = 1 / MM_PER_IN if inches else 1.0
    decimals = 5 if inches else 3
    feed_decimals = 1 if inches else 0
    ox, oy, oz = origin_shift(job)

    def num(value: float) -> str:
        return _number(value * scale, decimals)

    safe = num(job.safe_z + oz)
    lines: list[str] = []
    if post.wrapper:
        lines.append(post.wrapper)
    lines.append(_note(title))
    lines.append(_note(f"Tool: {tool.name}"))
    for path in paths:
        lines.append(_note(f"Operation: {path.name}"))
    lines.append(post.preamble)
    if job.arc_output:
        lines.append("G91.1")
    lines.append("G20" if inches else "G21")
    lines.append(f"G0 Z{safe}")
    lines.append(f"M3 S{int(tool.rpm)}")
    lines.append("G4 P3")

    last = {"X": None, "Y": None, "Z": safe, "F": None}
    for path in paths:
        moves = fit_arcs(path.moves) if job.arc_output else path.moves
        for row in moves:
            if len(row) > 5:
                code, x, y, z, feed, cx, cy, sx, sy = row
                words = []
                for axis, value in (("X", x - ox), ("Y", y - oy)):
                    text = num(value)
                    words.append(f"{axis}{text}")
                    last[axis] = text
                text = num(z + oz)
                if text != last["Z"]:
                    words.append(f"Z{text}")
                    last["Z"] = text
                words.append(f"I{_number((cx - sx) * scale, decimals)}")
                words.append(f"J{_number((cy - sy) * scale, decimals)}")
                rate = _number(feed * scale, feed_decimals)
                if rate != last["F"]:
                    words.append(f"F{rate}")
                    last["F"] = rate
                lines.append(f"G{code} " + " ".join(words))
                continue
            code, x, y, z, feed = row
            words = []
            for axis, value in (("X", x - ox), ("Y", y - oy), ("Z", z + oz)):
                text = num(value)
                if text != last[axis]:
                    words.append(f"{axis}{text}")
                    last[axis] = text
            if not words:
                continue
            if code == RAPID:
                lines.append("G0 " + " ".join(words))
            else:
                rate = _number(feed * scale, feed_decimals)
                if rate != last["F"]:
                    words.append(f"F{rate}")
                    last["F"] = rate
                lines.append("G1 " + " ".join(words))

    if last["Z"] != safe:
        lines.append(f"G0 Z{safe}")
    lines.append("G0 X0 Y0")
    lines.append("M5")
    lines.append(post.end_code)
    if post.wrapper:
        lines.append(post.wrapper)
    return "\n".join(lines) + "\n"


def group_by_tool(paths: list[Toolpath]) -> list[list[Toolpath]]:
    groups: list[list[Toolpath]] = []
    for path in paths:
        if not path.moves:
            continue
        if groups and groups[-1][0].tool.id == path.tool.id:
            groups[-1].append(path)
        else:
            groups.append([path])
    return groups


def export(job: Job, paths: list[Toolpath], post_id: str, title: str = "downcut-job") -> list[GcodeFile]:
    post = post_for(job, post_id)
    files = []
    groups = group_by_tool(paths)
    for index, group in enumerate(groups, start=1):
        tool = group[0].tool
        name = f"{_slug(title)}-{index:02d}-{_slug(tool.name)}.{post.extension}"
        files.append(GcodeFile(name, tool.id, tool.name, program(job, group, post_id, title, name)))
    return files
