from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

import shapely
from shapely.geometry import Polygon

MM_PER_IN = 25.4
EPS = 1e-3
TOKEN = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")
COMMA_NUMBER = re.compile(r"[A-Z]\s*[-+]?\d+,\d")
DEFAULT_RAPID_MM_MIN = 5080.0
CHORD_TOLERANCE = 0.01
ARC_RADIUS_TOLERANCE = 0.005
ORIGIN_FRACTIONS = {
    "lower_left": (0.0, 0.0),
    "lower_right": (1.0, 0.0),
    "upper_left": (0.0, 1.0),
    "upper_right": (1.0, 1.0),
    "center": (0.5, 0.5),
}


@dataclass
class Limits:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_top: float
    z_floor: float
    safe_z: float
    strict: bool = True
    spindle_required: bool = True
    tool_radius: float = 0.0
    keepouts: list = field(default_factory=list)


@dataclass
class Segment:
    rapid: bool
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    feed: float


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cut_length_mm: float = 0.0
    rapid_length_mm: float = 0.0
    seconds: float = 0.0
    x_range: tuple[float, float] = (0.0, 0.0)
    y_range: tuple[float, float] = (0.0, 0.0)
    z_min: float = 0.0
    line_count: int = 0

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def limits_for(job, tool, strict: bool = True, spindle_required: bool = True) -> Limits:
    fx, fy = ORIGIN_FRACTIONS.get(job.xy_origin, (0.0, 0.0))
    x_low, y_low = -job.material.width * fx, -job.material.height * fy
    z_top = job.material.thickness if job.z_zero == "bottom" else 0.0
    margin = tool.diameter / 2 + 0.01
    keepouts = [
        Polygon([(x + x_low, y + y_low) for x, y in vector.points]).buffer(0)
        for vector in job.keepout_vectors()
    ]
    return Limits(
        x_low - margin,
        x_low + job.material.width + margin,
        y_low - margin,
        y_low + job.material.height + margin,
        z_top,
        z_top - job.material.thickness - job.spoilboard_allowance,
        z_top + job.safe_z,
        strict,
        spindle_required,
        tool.diameter / 2,
        keepouts,
    )


def _arc_points(start, end, cx, cy, clockwise: bool) -> list[tuple[float, float, float]]:
    x0, y0, z0 = start
    x1, y1, z1 = end
    radius = math.hypot(x0 - cx, y0 - cy)
    first = math.atan2(y0 - cy, x0 - cx)
    sweep = math.atan2(y1 - cy, x1 - cx) - first
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    elif not clockwise and sweep <= 0:
        sweep += 2 * math.pi
    limit = min(1.0, CHORD_TOLERANCE / max(radius, 1e-9))
    step = max(2 * math.acos(1 - limit), 1e-3)
    count = max(1, math.ceil(abs(sweep) / step))
    points = []
    for index in range(1, count + 1):
        angle = first + sweep * index / count
        fraction = index / count
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), z0 + (z1 - z0) * fraction))
    points[-1] = (x1, y1, z1)
    return points


def parse(text: str, strict: bool = True, spindle_required: bool = True):
    segments: list[Segment] = []
    faults: list[str] = []
    notices: list[str] = []
    state = {"motion": None, "x": None, "y": None, "z": None, "feed": 0.0, "scale": None}
    spindle_on = False
    ended = False
    dwell_seconds = 0.0
    spindle_at_end = False
    absolute_ij = False
    unknown_position = False
    moved = False

    def soft(message: str) -> None:
        if message not in notices:
            notices.append(message)

    for number, raw in enumerate(text.splitlines(), start=1):
        line = re.sub(r"\(.*?\)", "", raw).split(";")[0].strip().upper()
        if not line or line == "%":
            continue
        if COMMA_NUMBER.search(line):
            faults.append(f"Line {number}: comma decimals cannot be checked.")
            continue
        words = [(k, float(v)) for k, v in TOKEN.findall(line)]
        codes = {(k, v) for k, v in words if k in "GM"}
        values = {k: v for k, v in words if k not in "GM"}

        machine_move = ("G", 53.0) in codes or ("G", 28.0) in codes or ("G", 30.0) in codes
        if ("G", 91.0) in codes and not machine_move:
            faults.append(f"Line {number}: relative mode G91 is not supported.")
        if ("G", 18.0) in codes or ("G", 19.0) in codes:
            faults.append(f"Line {number}: only the XY arc plane G17 is supported.")
        if ("G", 92.0) in codes:
            if moved or any(values.get(axis, 0.0) != 0.0 for axis in "XYZ"):
                faults.append(f"Line {number}: G92 shifts the coordinates and cannot be checked.")
            else:
                soft("Program zeroes the machine with G92 where the tool sits.")
            continue
        if ("G", 90.1) in codes:
            absolute_ij = True
        if ("G", 91.1) in codes:
            absolute_ij = False
        if ("G", 20.0) in codes:
            state["scale"] = MM_PER_IN
        if ("G", 21.0) in codes:
            state["scale"] = 1.0
        if ("M", 3.0) in codes or ("M", 4.0) in codes:
            spindle_on = True
        if ("M", 5.0) in codes:
            spindle_on = False
        if ("M", 6.0) in codes:
            soft("Program holds a tool change. The checker skips the moves the controller makes for it.")
        if ("M", 2.0) in codes or ("M", 30.0) in codes:
            ended = True
            spindle_at_end = spindle_on
        if ("G", 4.0) in codes:
            dwell_seconds += values.get("P", 0.0)
            continue
        if machine_move:
            soft("Program moves in machine coordinates. The checker cannot follow those moves.")
            state["x"], state["y"], state["z"] = None, None, None
            unknown_position = True
            continue
        if ("G", 0.0) in codes:
            state["motion"] = 0
        if ("G", 1.0) in codes:
            state["motion"] = 1
        if ("G", 2.0) in codes:
            state["motion"] = 2
        if ("G", 3.0) in codes:
            state["motion"] = 3

        if not any(axis in values for axis in "XYZ"):
            if "F" in values:
                state["feed"] = values["F"] * (state["scale"] or 1.0)
            continue

        scale = state["scale"]
        if scale is None:
            faults.append(f"Line {number}: motion before units are set.")
            scale = 1.0
        if "F" in values:
            state["feed"] = values["F"] * scale
        if state["motion"] is None:
            faults.append(f"Line {number}: motion with no active G0 or G1.")
            continue

        previous = (state["x"], state["y"], state["z"])
        for axis, key in (("X", "x"), ("Y", "y"), ("Z", "z")):
            if axis in values:
                state[key] = values[axis] * scale
        moved = True
        rapid = state["motion"] == 0

        if not rapid and state["feed"] <= 0:
            faults.append(f"Line {number}: feed move with no feed rate.")
        if not rapid and not spindle_on:
            if spindle_required:
                faults.append(f"Line {number}: feed move with the spindle off.")
            else:
                soft("Program never starts a spindle. Start the router by hand.")
        if None in previous:
            if ("X" in values or "Y" in values) and state["z"] is None:
                message = f"Line {number}: XY move before Z is known."
                if strict and not unknown_position:
                    faults.append(message)
                else:
                    soft("Program moves in XY before it sets Z. Raise the bit before you start.")
        end = (state["x"], state["y"], state["z"])
        if state["motion"] in (2, 3):
            if None in previous or None in end:
                faults.append(f"Line {number}: arc from an unknown position.")
                continue
            if "I" not in values and "J" not in values:
                faults.append(f"Line {number}: arc with no I or J center.")
                continue
            if absolute_ij:
                cx = values.get("I", previous[0] / scale) * scale
                cy = values.get("J", previous[1] / scale) * scale
            else:
                cx = previous[0] + values.get("I", 0.0) * scale
                cy = previous[1] + values.get("J", 0.0) * scale
            first = math.hypot(previous[0] - cx, previous[1] - cy)
            second = math.hypot(end[0] - cx, end[1] - cy)
            if abs(first - second) > ARC_RADIUS_TOLERANCE:
                faults.append(f"Line {number}: arc end is {abs(first - second):.3f} mm off its circle.")
                continue
            start = previous
            for point in _arc_points(previous, end, cx, cy, state["motion"] == 2):
                segments.append(Segment(False, start, point, state["feed"]))
                start = point
            continue
        segments.append(Segment(rapid, previous, end, state["feed"]))

    if not ended:
        if strict:
            faults.append("Program has no end code.")
        else:
            soft("Program has no end code.")
    if spindle_at_end:
        faults.append("Spindle is still on at the end of the program.")
    return segments, faults, dwell_seconds, notices


def _check_keepouts(segments, limits: Limits, note) -> None:
    cuts = [
        s
        for s in segments
        if None not in s.start and None not in s.end and not s.rapid and min(s.start[2], s.end[2]) < limits.z_top + EPS
    ]
    if not cuts:
        return
    lines = shapely.linestrings([[s.start[:2], s.end[:2]] for s in cuts])
    for zone in limits.keepouts:
        hits = shapely.dwithin(lines, zone, limits.tool_radius)
        if hits.any():
            spot = cuts[int(hits.argmax())].end
            note(f"Cut enters a keep-out zone near X={spot[0]:.3f} Y={spot[1]:.3f} mm.")


def check(text: str, limits: Limits, rapid_mm_min: float = DEFAULT_RAPID_MM_MIN) -> Report:
    segments, faults, dwell, notices = parse(text, limits.strict, limits.spindle_required)
    report = Report(errors=list(faults), warnings=list(notices), line_count=len(text.splitlines()))
    x_seen, y_seen, z_seen = [], [], []
    last_z = None

    def note(message: str) -> None:
        if len(report.errors) < 40:
            report.errors.append(message)

    for index, seg in enumerate(segments):
        x0, y0, z0 = seg.start
        x1, y1, z1 = seg.end
        last_z = z1
        if x1 is not None:
            x_seen.append(x1)
        if y1 is not None:
            y_seen.append(y1)
        if z1 is not None:
            z_seen.append(z1)
        if None in (x0, y0, z0):
            moved_xy = x1 is not None and y1 is not None and (x0 != x1 or y0 != y1)
            if seg.rapid and moved_xy and z1 is not None and z1 < limits.safe_z - EPS:
                note(f"Rapid XY move below safe Z at Z={z1:.3f} mm, move {index}.")
            if x1 is not None and (x1 < limits.x_min - EPS or x1 > limits.x_max + EPS):
                note(f"X={x1:.3f} mm is outside the material, move {index}.")
            if y1 is not None and (y1 < limits.y_min - EPS or y1 > limits.y_max + EPS):
                note(f"Y={y1:.3f} mm is outside the material, move {index}.")
            continue
        run = math.hypot(x1 - x0, y1 - y0)
        drop = z0 - z1
        length = math.hypot(run, drop)
        if seg.rapid:
            report.rapid_length_mm += length
            report.seconds += length / rapid_mm_min * 60
            if run > EPS and min(z0, z1) < limits.safe_z - EPS:
                note(f"Rapid XY move below safe Z at Z={min(z0, z1):.3f} mm, move {index}.")
            if drop > 0 and z1 < limits.z_top - EPS:
                note(f"Rapid descent into material to Z={z1:.3f} mm, move {index}.")
        else:
            report.cut_length_mm += length
            if seg.feed > 0:
                report.seconds += length / seg.feed * 60
        if x1 < limits.x_min - EPS or x1 > limits.x_max + EPS:
            note(f"X={x1:.3f} mm is outside the material, move {index}.")
        if y1 < limits.y_min - EPS or y1 > limits.y_max + EPS:
            note(f"Y={y1:.3f} mm is outside the material, move {index}.")
        if z1 < limits.z_floor - EPS:
            note(f"Z={z1:.3f} mm cuts below the allowed floor of {limits.z_floor:.3f} mm, move {index}.")

    if last_z is not None and last_z < limits.safe_z - EPS:
        note(f"Program ends with Z={last_z:.3f} mm, below safe Z.")
    if not segments:
        if limits.strict:
            report.warnings.append("Program has no motion.")
        else:
            report.errors.append("The checker found no moves it can read in this program.")
    if limits.keepouts:
        _check_keepouts(segments, limits, note)
    report.seconds += dwell
    if x_seen and y_seen and z_seen:
        report.x_range = (min(x_seen), max(x_seen))
        report.y_range = (min(y_seen), max(y_seen))
        report.z_min = min(z_seen)
    return report
