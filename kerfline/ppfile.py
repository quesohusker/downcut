from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field

from . import __version__
from .arcfit import ARC_CCW, ARC_CW, fit_arcs
from .model import MM_PER_IN, Job
from .toolpaths.base import CUT, RAPID, Toolpath

STATEMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
VARIABLE = re.compile(r"^VAR\s+([A-Za-z0-9_]+)\s*=\s*\[(.*)\]\s*$", re.IGNORECASE)
BLOCK = re.compile(r"^begin\s+([A-Za-z_]+)", re.IGNORECASE)
QUOTED = re.compile(r'^["“](.*)["”]\s*$')
FORMAT = re.compile(r"^([-+#F]*)(\d*)([.,]?)(\d*)$")
TOKEN = re.compile(r"\[([^\[\]]*)\]")
SPINDLE = re.compile(r"(?<![A-Za-z])M0*[34](?!\d)")
UNIT_INCH = re.compile(r"(?<![A-Za-z])G0*20(?!\d)")
UNIT_MM = re.compile(r"(?<![A-Za-z])G0*21(?!\d)")

NUMERIC = {
    "F", "FC", "FP", "S", "T", "TP", "N", "P", "X", "Y", "Z", "I", "J", "IA", "JA",
    "ArcStartX", "ArcStartY", "ArcMidX", "ArcMidY", "ArcMidXI", "ArcMidYI", "Radius", "Angle",
    "XH", "YH", "ZH", "SAFEZ", "XLENGTH", "YLENGTH", "ZLENGTH", "XMIN", "YMIN", "ZMIN",
    "XMAX", "YMAX", "ZMAX", "TDIA", "DWELL",
}
TEXT = {
    "TOOLNAME", "TOOL_NOTES", "TOOLPATH_NAME", "TP_FILENAME", "TP_DIR", "TP_EXT", "PATHNAME",
    "X_ORIGIN_POS", "Y_ORIGIN_POS", "Z_ORIGIN", "XY_ORIGIN", "TOOLS_USED", "TOOLPATHS_OUTPUT",
    "TOOLPATH_NOTES", "FILE_NOTES", "TIME", "DATE", "PRODUCT", "CUTTER_COMP",
}
REFUSED = {
    "POST_BASE": "This post inherits from another post with POST_BASE. Import the full post it is based on.",
    "ROTARY_WRAP_X": "This is a rotary post. Kerfline has no rotary toolpaths yet.",
    "ROTARY_WRAP_Y": "This is a rotary post. Kerfline has no rotary toolpaths yet.",
    "INVERSE_TIME_MODE": "This is a rotary post. Kerfline has no rotary toolpaths yet.",
}
IGNORED = {
    "PRINT_DIRECT", "DIRECT_OUTPUT", "RAPID_PLUNGE_TO_STARTZ", "RAPID_SPEED", "MAXIMUM_ROTATION",
}


@dataclass
class PpVariable:
    label: str
    always: bool = True
    prefix: str = ""
    flags: str = ""
    width: int = 1
    decimals: int = 3
    separator: str = "."
    multiplier: float = 1.0


@dataclass
class PpPost:
    id: str
    name: str
    extension: str = "tap"
    units: str = "mm"
    line_ending: str = "\n"
    variables: dict[str, PpVariable] = field(default_factory=dict)
    blocks: dict[str, list[str]] = field(default_factory=dict)
    substitute: dict[str, str] = field(default_factory=dict)
    line_start: int = 0
    line_step: int = 10
    line_max: int = 999999
    spindle_range: tuple | None = None
    min_arc: float = 0.0
    max_arc: float = 5000.0
    max_length: float = 0.0
    first_new_segment: bool = False
    manual_spindle: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def supports_arcs(self) -> bool:
        needed = ("CW_ARC_MOVE", "CCW_ARC_MOVE")
        if not all(self.block_for(name) for name in needed):
            return False
        text = " ".join(self.block_for("CW_ARC_MOVE") + self.block_for("CCW_ARC_MOVE"))
        return ("[I]" in text and "[J]" in text) or ("[IA]" in text and "[JA]" in text)

    @property
    def supports_helix(self) -> bool:
        return bool(self.blocks.get("CW_HELICAL_ARC_MOVE") or self.blocks.get("FIRST_CW_HELICAL_ARC_MOVE")) and bool(
            self.blocks.get("CCW_HELICAL_ARC_MOVE") or self.blocks.get("FIRST_CCW_HELICAL_ARC_MOVE")
        )

    def block_for(self, name: str) -> list[str]:
        return self.blocks.get(name) or self.blocks.get(f"FIRST_{name}") or []


def _text_value(raw: str) -> str:
    value = raw.split('" ')[0] if raw.startswith('"') else raw
    return value.strip().strip('"“”').strip()


def _character_codes(value: str) -> str:
    return TOKEN.sub(lambda m: chr(int(m[1])) if m[1].isdigit() else m[0], value)


def _variable(body: str, number: int) -> PpVariable:
    parts = body.split("|")
    if len(parts) < 3:
        raise ValueError(f"Line {number}: a VAR line needs at least label, mode, and prefix.")
    label = parts[0].strip()
    mode = parts[1].strip().upper()
    variable = PpVariable(label, mode != "C", parts[2])
    if len(parts) > 3 and parts[3].strip():
        text = parts[3].strip()
        found = FORMAT.match(text)
        if not found:
            raise ValueError(f"Line {number}: cannot read the value format {text!r}.")
        flags, width, separator, decimals = found.groups()
        if width.startswith("0") and len(width) > 1:
            flags += "0"
            width = width.lstrip("0")
        variable.flags = flags
        variable.width = int(width or 1)
        variable.separator = separator or "."
        variable.decimals = int(decimals or 0)
    if len(parts) > 4 and parts[4].strip():
        try:
            variable.multiplier = float(parts[4])
        except ValueError as error:
            raise ValueError(f"Line {number}: cannot read the multiplier {parts[4]!r}.") from error
    return variable


def parse_pp(text: str, post_id: str = "pp") -> PpPost:
    post = PpPost(id=post_id, name="Imported post")
    block: list[str] | None = None
    for number, raw in enumerate(text.replace("﻿", "").splitlines(), start=1):
        line = raw.strip()
        if not line or line[0] in "+|":
            continue
        found = BLOCK.match(line)
        if found:
            block = post.blocks.setdefault(found[1].upper(), [])
            continue
        found = QUOTED.match(line)
        if found:
            if block is None:
                raise ValueError(f"Line {number}: output text sits outside any block.")
            block.append(found[1])
            continue
        found = VARIABLE.match(line)
        if found:
            variable = _variable(found[2], number)
            post.variables[variable.label] = variable
            block = None
            continue
        found = STATEMENT.match(line)
        if not found:
            raise ValueError(f"Line {number}: cannot read {line[:60]!r}.")
        block = None
        key = found[1].upper()
        value = _text_value(found[2])
        try:
            if key in REFUSED:
                raise ValueError(REFUSED[key])
            if key == "POST_NAME":
                post.name = value
            elif key == "FILE_EXTENSION":
                post.extension = value.lstrip(".")
            elif key == "UNITS":
                post.units = "in" if value.upper().startswith("INCH") else "mm"
            elif key == "LINE_ENDING":
                post.line_ending = _character_codes(value)
            elif key == "LINE_NUMBER_START":
                post.line_start = int(value)
            elif key == "LINE_NUMBER_INCREMENT":
                post.line_step = int(value)
            elif key == "LINE_NUMBER_MAXIMUM":
                post.line_max = int(value)
            elif key == "SPINDLE_SPEED_RANGE":
                post.spindle_range = tuple(float(v) for v in value.split())
            elif key == "SUBSTITUTE":
                post.substitute = dict(zip(value[0::2], value[1::2]))
            elif key == "MIN_ARC_RADIUS":
                post.min_arc = float(value)
            elif key == "MAX_ARC_RADIUS":
                post.max_arc = float(value)
            elif key == "MAXIMUM_LENGTH":
                post.max_length = float(value)
            elif key == "FIRST_TOOLPATH_NEW_SEGMENT":
                post.first_new_segment = value.upper() == "YES"
            elif key == "LASER_SUPPORT":
                post.notes.append("Laser sections were skipped. Kerfline has no laser toolpaths yet.")
            elif key == "TAPE_SPLITTING":
                post.notes.append("Tape splitting was skipped. Kerfline writes one file per tool.")
            elif key not in IGNORED:
                post.notes.append(f"{key} was skipped.")
        except ValueError as error:
            message = str(error)
            raise ValueError(message if message in REFUSED.values() else f"Line {number}: bad value for {key}.") from error
    _review(post)
    return post


def _review(post: PpPost) -> None:
    if not post.block_for("RAPID_MOVE") or not post.block_for("FEED_MOVE"):
        raise ValueError("That post has no RAPID_MOVE or no FEED_MOVE section. It cannot drive a machine.")
    every = " ".join(line for rows in post.blocks.values() for line in rows)
    if not re.search(r"(?<![A-Za-z])G", " ".join(post.block_for("RAPID_MOVE")).upper()):
        post.notes.append("This post does not write G-code. The checker cannot read it, so its files stay blocked.")
    if not SPINDLE.search(every):
        post.manual_spindle = True
        post.notes.append("This post never starts a spindle. Start the router by hand.")
    header = " ".join(post.blocks.get("HEADER", []))
    wants_inch = post.units == "in"
    if UNIT_INCH.search(every) or UNIT_MM.search(every):
        if wants_inch and UNIT_MM.search(header) and not UNIT_INCH.search(header):
            post.notes.append("UNITS says inches but the header sets G21. Check this post before you use it.")
        if not wants_inch and UNIT_INCH.search(header) and not UNIT_MM.search(header):
            post.notes.append("UNITS says MM but the header sets G20. Check this post before you use it.")
    else:
        code = "G20" if wants_inch else "G21"
        post.blocks.setdefault("HEADER", []).insert(0, code)
        post.notes.append(f"The post set no units, so Kerfline added {code} to the header.")
    labels = set(TOKEN.findall(every))
    unknown = sorted(
        label for label in labels if not label.isdigit() and label not in NUMERIC and label not in TEXT
    )
    if unknown:
        post.notes.append("Kerfline leaves these variables blank: " + ", ".join(f"[{v}]" for v in unknown) + ".")
    if not post.supports_arcs:
        post.notes.append("No usable arc sections, so this post writes straight moves only.")


def format_number(variable: PpVariable, value: float) -> str:
    scaled = value * variable.multiplier
    if abs(scaled) < 0.5 * 10 ** (-variable.decimals):
        scaled = 0.0
    left = "<" if "-" in variable.flags else ""
    sign = "+" if "+" in variable.flags else ""
    zero = "0" if "0" in variable.flags and not left else ""
    text = f"{scaled:{left}{sign}{zero}{variable.width}.{variable.decimals}f}"
    if "#" in variable.flags and variable.decimals == 0:
        text += "."
    if variable.separator != ".":
        text = text.replace(".", variable.separator)
    return text


class _Writer:
    def __init__(self, post: PpPost, job: Job, paths: list[Toolpath], title: str, name: str):
        self.post = post
        self.job = job
        self.paths = paths
        self.title = title
        self.name = name
        self.tool = paths[0].tool
        self.scale = 1 / MM_PER_IN if post.units == "in" else 1.0
        self.ox, self.oy, self.oz = 0.0, 0.0, 0.0
        if job.xy_origin == "center":
            self.ox, self.oy = job.material.width / 2, job.material.height / 2
        if job.z_zero == "bottom":
            self.oz = job.material.thickness
        self.last: dict[str, str] = {}
        self.lines: list[str] = []
        self.line_number = post.line_start
        self.values: dict[str, float] = {}
        self.texts: dict[str, str] = {}
        self.current_path = paths[0]
        self.first_rapid = True
        self.series = ""
        self.feed = self.tool.feed
        self.now = datetime.datetime.now()
        self._static_values()

    def _static_values(self) -> None:
        job, s, tool = self.job, self.scale, self.tool
        material = job.material
        rpm = float(tool.rpm)
        if self.post.spindle_range and len(self.post.spindle_range) == 4:
            low, high, slow, fast = self.post.spindle_range
            fraction = (min(max(rpm, slow), fast) - slow) / max(fast - slow, 1e-9)
            rpm = round(low + fraction * (high - low))
        self.values.update(
            {
                "S": rpm,
                "T": float(tool.number or 1),
                "TP": float(tool.number or 1),
                "F": tool.feed * s,
                "FC": tool.feed * s,
                "FP": tool.plunge * s,
                "P": 0.0,
                "X": 0.0,
                "Y": 0.0,
                "Z": (job.safe_z + self.oz) * s,
                "XH": 0.0,
                "YH": 0.0,
                "ZH": (job.safe_z + self.oz) * s,
                "SAFEZ": job.safe_z * s,
                "XLENGTH": material.width * s,
                "YLENGTH": material.height * s,
                "ZLENGTH": material.thickness * s,
                "XMIN": -self.ox * s,
                "YMIN": -self.oy * s,
                "ZMIN": (self.oz - material.thickness) * s,
                "XMAX": (material.width - self.ox) * s,
                "YMAX": (material.height - self.oy) * s,
                "ZMAX": self.oz * s,
                "TDIA": tool.diameter * s,
                "DWELL": 0.0,
            }
        )
        stem, _, extension = self.name.rpartition(".")
        self.texts.update(
            {
                "TOOLNAME": tool.name,
                "TOOL_NOTES": tool.notes,
                "TP_FILENAME": stem or self.name,
                "TP_EXT": extension,
                "TP_DIR": "",
                "PATHNAME": self.name,
                "X_ORIGIN_POS": format_number(PpVariable("x"), self.ox * s),
                "Y_ORIGIN_POS": format_number(PpVariable("y"), self.oy * s),
                "Z_ORIGIN": "Table Surface" if job.z_zero == "bottom" else "Material Surface",
                "XY_ORIGIN": "Centre" if job.xy_origin == "center" else "Bottom Left Corner",
                "TOOLS_USED": tool.name,
                "TOOLPATHS_OUTPUT": ", ".join(path.name for path in self.paths),
                "TOOLPATH_NAME": self.paths[0].name,
                "TOOLPATH_NOTES": "",
                "FILE_NOTES": self.title,
                "TIME": self.now.strftime("%H:%M:%S"),
                "DATE": self.now.strftime("%m/%d/%Y"),
                "PRODUCT": f"Kerfline {__version__}",
                "CUTTER_COMP": "",
            }
        )

    def variable(self, label: str) -> PpVariable:
        found = self.post.variables.get(label)
        if found:
            return found
        decimals = 4 if self.post.units == "in" else 3
        if label in ("S", "T", "TP", "N"):
            decimals = 0
        return PpVariable(label, True, "", "", 1, decimals)

    def token(self, label: str) -> str:
        if label.isdigit():
            return chr(int(label))
        if label in self.texts:
            value = self.texts[label]
            for old, new in self.post.substitute.items():
                value = value.replace(old, new)
            return value
        if label == "N":
            variable = self.variable("N")
            text = variable.prefix + format_number(variable, self.line_number)
            self.line_number += self.post.line_step
            if self.line_number > self.post.line_max:
                self.line_number = self.post.line_start
            return text
        if label not in self.values:
            return ""
        variable = self.variable(label)
        text = format_number(variable, self.values[label])
        if not variable.always and self.last.get(label) == text:
            return ""
        self.last[label] = text
        return variable.prefix + text

    def emit(self, name: str) -> None:
        for template in self.post.blocks.get(name, []):
            line = TOKEN.sub(lambda m: self.token(m[1]), template)
            self.lines.append(line)

    def has(self, name: str) -> bool:
        return bool(self.post.blocks.get(name))

    def choose(self, name: str, first: bool) -> str:
        if first and self.has(f"FIRST_{name}"):
            return f"FIRST_{name}"
        return name if self.has(name) else f"FIRST_{name}"

    def place(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        s = self.scale
        return (x - self.ox) * s, (y - self.oy) * s, (z + self.oz) * s

    def moved(self, x: float, y: float, z: float) -> bool:
        for label, value in (("X", x), ("Y", y), ("Z", z)):
            if format_number(self.variable(label), value) != format_number(self.variable(label), self.values[label]):
                return True
        return False

    def set_position(self, x: float, y: float, z: float) -> None:
        self.values.update({"X": x, "Y": y, "Z": z})

    def straight(self, code: int, x: float, y: float, z: float, feed: float) -> None:
        px, py, pz = self.place(x, y, z)
        if not self.moved(px, py, pz):
            return
        same_xy = (
            format_number(self.variable("X"), px) == format_number(self.variable("X"), self.values["X"])
            and format_number(self.variable("Y"), py) == format_number(self.variable("Y"), self.values["Y"])
        )
        down, up = pz < self.values["Z"], pz > self.values["Z"]
        previous_z = self.values["Z"]
        self.set_position(px, py, pz)
        if code == RAPID:
            if self.first_rapid and self.has("INITIAL_RAPID_MOVE"):
                name = "INITIAL_RAPID_MOVE"
            elif same_xy and up and self.has("RETRACT_MOVE"):
                name = "RETRACT_MOVE"
            else:
                name = "RAPID_MOVE"
            self.first_rapid = False
            self.series = "rapid"
            self.emit(name)
            return
        rate = feed * self.scale
        changed = abs(rate - self.values["F"]) > 1e-9 or self.series == "rapid"
        self.values["F"] = rate
        plunge = same_xy and down and previous_z is not None and (self.has("PLUNGE_MOVE") or self.has("FIRST_PLUNGE_MOVE"))
        kind = "plunge" if plunge else "feed"
        first = self.series != kind or changed
        self.series = kind
        if changed and self.has("FEED_RATE_CHANGE"):
            self.emit("FEED_RATE_CHANGE")
        self.emit(self.choose("PLUNGE_MOVE" if plunge else "FEED_MOVE", first))

    def arc(self, row: tuple) -> None:
        code, x, y, z, feed, cx, cy, sx, sy = row
        s = self.scale
        px, py, pz = self.place(x, y, z)
        ax, ay = sx * s - self.ox * s, sy * s - self.oy * s
        ux, uy = cx * s - self.ox * s, cy * s - self.oy * s
        radius = math.hypot(ax - ux, ay - uy)
        first_angle = math.atan2(ay - uy, ax - ux)
        last_angle = math.atan2(py - uy, px - ux)
        sweep = last_angle - first_angle
        if code == ARC_CW and sweep >= 0:
            sweep -= 2 * math.pi
        elif code == ARC_CCW and sweep <= 0:
            sweep += 2 * math.pi
        middle = first_angle + sweep / 2
        mx, my = ux + radius * math.cos(middle), uy + radius * math.sin(middle)
        helical = abs(pz - self.values["Z"]) > 1e-9
        rate = feed * self.scale
        changed = abs(rate - self.values["F"]) > 1e-9 or self.series == "rapid"
        self.values.update(
            {
                "F": rate,
                "I": ux - ax,
                "J": uy - ay,
                "IA": ux,
                "JA": uy,
                "ArcStartX": ax,
                "ArcStartY": ay,
                "ArcMidX": mx,
                "ArcMidY": my,
                "ArcMidXI": mx - ax,
                "ArcMidYI": my - ay,
                "Radius": radius,
                "Angle": abs(sweep),
            }
        )
        self.set_position(px, py, pz)
        turn = "CW" if code == ARC_CW else "CCW"
        kind = f"{turn}_HELICAL_ARC_MOVE" if helical else f"{turn}_ARC_MOVE"
        first = self.series != kind or changed
        self.series = kind
        if changed and self.has("FEED_RATE_CHANGE"):
            self.emit("FEED_RATE_CHANGE")
        self.emit(self.choose(kind, first))

    def split(self, moves: list[tuple]) -> list[tuple]:
        limit = self.post.max_length / self.scale if self.post.max_length > 0 else 0.0
        if not limit:
            return moves
        out: list[tuple] = []
        previous = None
        for move in moves:
            if previous is not None and move[0] in (RAPID, CUT):
                span = math.dist(previous[1:4], move[1:4])
                pieces = int(math.ceil(span / limit))
                for index in range(1, pieces):
                    t = index / pieces
                    point = [a + (b - a) * t for a, b in zip(previous[1:4], move[1:4])]
                    out.append((move[0], *point, move[4]))
            out.append(move)
            previous = move
        return out

    def render(self, arcs: bool) -> str:
        self.emit("HEADER")
        if self.post.first_new_segment:
            self.emit("TOOLCHANGE")
            self.emit("NEW_SEGMENT")
        self.emit("SPINDLE_ON")
        for index, path in enumerate(self.paths):
            self.current_path = path
            self.texts["TOOLPATH_NAME"] = path.name
            if index:
                self.emit("NEW_SEGMENT")
                self.first_rapid = self.has("INITIAL_RAPID_MOVE") and self.first_rapid
            moves = self.split(path.moves)
            if arcs and self.post.supports_arcs:
                moves = fit_arcs(
                    moves,
                    0.01,
                    self.post.min_arc / self.scale,
                    self.post.max_arc / self.scale,
                    self.post.supports_helix,
                )
            for move in moves:
                if len(move) > 5:
                    self.arc(move)
                else:
                    self.straight(*move)
        self.emit("FOOTER")
        return self.post.line_ending.join(self.lines) + self.post.line_ending


def render_pp(post: PpPost, job: Job, paths: list[Toolpath], title: str, name: str) -> str:
    return _Writer(post, job, paths, title, name).render(job.arc_output)


STARTER = """+ Kerfline starter post. It uses the same format as a Vectric .pp file.
POST_NAME = "My machine (inch)(*.nc)"
FILE_EXTENSION = "nc"
UNITS = "INCHES"
LINE_ENDING = "[10]"

VAR SPINDLE_SPEED = [S|A|S|1.0]
VAR FEED_RATE = [F|C|F|1.1]
VAR X_POSITION = [X|C|X|1.4]
VAR Y_POSITION = [Y|C|Y|1.4]
VAR Z_POSITION = [Z|C|Z|1.4]
VAR X_HOME_POSITION = [XH|A|X|1.4]
VAR Y_HOME_POSITION = [YH|A|Y|1.4]
VAR Z_HOME_POSITION = [ZH|A|Z|1.4]
VAR ARC_CENTRE_I_INC_POSITION = [I|A|I|1.4]
VAR ARC_CENTRE_J_INC_POSITION = [J|A|J|1.4]

begin HEADER
"([TOOLPATHS_OUTPUT])"
"(Tool: [TOOLNAME])"
"G90 G94 G17 G91.1"
"G20"
"G0 [ZH]"
"[S] M3"
"G4 P3"

begin RAPID_MOVE
"G0 [X] [Y] [Z]"

begin FIRST_FEED_MOVE
"G1 [X] [Y] [Z] [F]"

begin FEED_MOVE
"G1 [X] [Y] [Z]"

begin FIRST_CW_ARC_MOVE
"G2 [X] [Y] [I] [J] [F]"

begin CW_ARC_MOVE
"G2 [X] [Y] [I] [J]"

begin FIRST_CCW_ARC_MOVE
"G3 [X] [Y] [I] [J] [F]"

begin CCW_ARC_MOVE
"G3 [X] [Y] [I] [J]"

begin FOOTER
"G0 [ZH]"
"G0 [XH] [YH]"
"M5"
"M30"
"""
