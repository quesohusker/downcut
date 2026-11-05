from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..model import Tool

RAPID = 0
CUT = 1


@dataclass
class Toolpath:
    operation_id: str
    name: str
    tool: Tool
    moves: list[tuple] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PathBuilder:
    def __init__(self, tool: Tool, safe_z: float, approach_gap: float = 1.0):
        self.tool = tool
        self.safe_z = safe_z
        self.gap = min(approach_gap, safe_z)
        self.moves: list[tuple] = []
        self.x: float | None = None
        self.y: float | None = None
        self.z: float = safe_z

    def retract(self) -> None:
        if self.z < self.safe_z and self.x is not None:
            self.moves.append((RAPID, self.x, self.y, self.safe_z, 0.0))
            self.z = self.safe_z

    def travel(self, x: float, y: float) -> None:
        self.retract()
        if self.x is not None and math.hypot(x - self.x, y - self.y) < 1e-6:
            return
        self.moves.append((RAPID, x, y, self.z, 0.0))
        self.x, self.y = x, y

    def enter(self, z: float) -> None:
        if self.z > self.gap:
            self.moves.append((RAPID, self.x, self.y, self.gap, 0.0))
            self.z = self.gap
        self.cut(self.x, self.y, z)

    def cut(self, x: float, y: float, z: float) -> None:
        run = math.hypot(x - self.x, y - self.y)
        rise = z - self.z
        if run < 1e-6 and abs(rise) < 1e-6:
            return
        if run < 1e-6:
            feed = self.tool.plunge
        elif rise < 0:
            length = math.hypot(run, rise)
            feed = min(self.tool.feed, self.tool.plunge * length / abs(rise))
        else:
            feed = self.tool.feed
        self.moves.append((CUT, x, y, z, feed))
        self.x, self.y, self.z = x, y, z

    def go_to_cut(self, x: float, y: float, z: float) -> None:
        self.travel(x, y)
        self.enter(z)


def flute_warning(tool: Tool, depth: float) -> list[str]:
    if tool.flute_length > 0 and depth > tool.flute_length + 1e-6:
        return [
            f"Cut depth of {depth:.2f} mm exceeds the {tool.flute_length:.2f} mm flute length of "
            f"{tool.name}. The shank may rub or the bit may break."
        ]
    return []


def depth_levels(depth: float, stepdown: float) -> list[float]:
    count = max(1, math.ceil(depth / stepdown - 1e-9))
    return [-depth * (i + 1) / count for i in range(count)]


def replicate(path: Toolpath, params: dict) -> Toolpath:
    columns = max(int(params.get("array_x") or 1), 1)
    rows = max(int(params.get("array_y") or 1), 1)
    if columns * rows == 1 or not path.moves:
        return path
    step_x = float(params.get("array_step_x") or 0.0)
    step_y = float(params.get("array_step_y") or 0.0)
    moves: list[tuple] = []
    for row in range(rows):
        order = range(columns) if row % 2 == 0 else range(columns - 1, -1, -1)
        for column in order:
            dx, dy = column * step_x, row * step_y
            moves.extend((code, x + dx, y + dy, z, feed) for code, x, y, z, feed in path.moves)
    return Toolpath(path.operation_id, path.name, path.tool, moves, list(path.warnings))
