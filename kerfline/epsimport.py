from __future__ import annotations

import math
import re
import struct

from .model import MM_PER_IN
from .pdfimport import Identity, _multiply, apply, flatten_cubic

PT_MM = MM_PER_IN / 72.0
TOKEN = re.compile(rb"\(([^()\\]|\\.)*\)|<[0-9A-Fa-f\s]*>|[\[\]{}]|/?[^\s\[\]{}()<>/%]+")
MAX_STEPS = 2_000_000
MAX_DEPTH = 32


def _body(data: bytes) -> bytes:
    if data[:4] == b"\xc5\xd0\xd3\xc6":
        start, length = struct.unpack("<II", data[4:12])
        return data[start : start + length]
    return data


def _tokens(body: bytes) -> list[bytes]:
    clean = re.sub(rb"%[^\r\n]*", b"", body)
    return [m.group(0) for m in TOKEN.finditer(clean)]


def _procedure(tokens: list[bytes], index: int) -> tuple[list[bytes], int]:
    depth = 0
    start = index + 1
    while index < len(tokens):
        if tokens[index] == b"{":
            depth += 1
        elif tokens[index] == b"}":
            depth -= 1
            if depth == 0:
                return tokens[start:index], index + 1
        index += 1
    return tokens[start:], index


def _number(token: bytes) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


class _Machine:
    def __init__(self):
        self.stack: list = []
        self.names: dict[bytes, object] = {}
        self.ctm = Identity
        self.saved: list = []
        self.current: list = []
        self.pen = None
        self.subpaths: list = []
        self.paths: list = []
        self.steps = 0

    def numbers(self, count: int) -> list[float]:
        values = self.stack[-count:]
        if len(values) < count or not all(isinstance(v, float) for v in values):
            raise ValueError("Unsupported")
        del self.stack[-count:]
        return values

    def flush(self, closed: bool) -> None:
        if len(self.current) > 1:
            self.subpaths.append((self.current, closed))
        self.current = []

    def paint(self, close_all: bool, keep: bool = True) -> None:
        self.flush(False)
        if keep:
            for points, closed in self.subpaths:
                self.paths.append(([(x * PT_MM, y * PT_MM) for x, y in points], closed or close_all))
        self.subpaths = []

    def move(self, x: float, y: float) -> None:
        self.flush(False)
        self.pen = (x, y)
        self.current = [apply(self.ctm, x, y)]

    def line(self, x: float, y: float) -> None:
        self.pen = (x, y)
        self.current.append(apply(self.ctm, x, y))

    def curve(self, x1, y1, x2, y2, x3, y3) -> None:
        start = self.current[-1]
        self.pen = (x3, y3)
        self.current.extend(
            flatten_cubic(start, apply(self.ctm, x1, y1), apply(self.ctm, x2, y2), apply(self.ctm, x3, y3))
        )

    def arc(self, cx, cy, r, first, last, clockwise: bool) -> None:
        start, end = math.radians(first), math.radians(last)
        if clockwise:
            while end > start:
                end -= 2 * math.pi
        else:
            while end < start:
                end += 2 * math.pi
        count = max(8, int(abs(end - start) / math.radians(5)))
        for index in range(count + 1):
            angle = start + (end - start) * index / count
            x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
            if index == 0 and not self.current:
                self.move(x, y)
            else:
                self.line(x, y)

    def execute(self, tokens: list[bytes], depth: int = 0) -> None:
        index = 0
        while index < len(tokens):
            self.steps += 1
            if self.steps > MAX_STEPS or depth > MAX_DEPTH:
                raise ValueError("Too large")
            token = tokens[index]
            if token == b"{":
                body, index = _procedure(tokens, index)
                self.stack.append(body)
                continue
            index += 1
            value = _number(token)
            if value is not None:
                self.stack.append(value)
            elif token.startswith(b"/"):
                self.stack.append(token)
            elif token[:1] in (b"(", b"<", b"[", b"]", b"}"):
                continue
            else:
                self.word(token, depth)

    def word(self, token: bytes, depth: int) -> None:
        if token in self.names:
            value = self.names[token]
            if isinstance(value, list):
                self.execute(value, depth + 1)
            elif isinstance(value, bytes):
                self.word(value, depth)
            else:
                self.stack.append(value)
            return
        try:
            self.builtin(token)
        except ValueError as error:
            if str(error) == "Too large":
                raise
            self.stack.clear()

    def builtin(self, token: bytes) -> None:
        s = self.stack
        if token == b"def":
            if len(s) >= 2:
                value = s.pop()
                key = s.pop()
                if isinstance(key, bytes) and key.startswith(b"/"):
                    self.names[key[1:]] = value
        elif token == b"load":
            if s and isinstance(s[-1], bytes):
                s[-1] = s[-1].lstrip(b"/")
        elif token == b"bind":
            return
        elif token == b"pop":
            if s:
                s.pop()
        elif token == b"dup":
            if s:
                s.append(s[-1])
        elif token == b"exch":
            if len(s) >= 2:
                s[-1], s[-2] = s[-2], s[-1]
        elif token == b"add":
            a, b = self.numbers(2)
            s.append(a + b)
        elif token == b"sub":
            a, b = self.numbers(2)
            s.append(a - b)
        elif token == b"mul":
            a, b = self.numbers(2)
            s.append(a * b)
        elif token == b"div":
            a, b = self.numbers(2)
            s.append(a / b if b else 0.0)
        elif token == b"neg":
            s.append(-self.numbers(1)[0])
        elif token == b"newpath":
            self.paint(False, False)
        elif token == b"moveto":
            self.move(*self.numbers(2))
        elif token == b"rmoveto":
            dx, dy = self.numbers(2)
            self.move(self.pen[0] + dx, self.pen[1] + dy)
        elif token == b"lineto":
            self.line(*self.numbers(2))
        elif token == b"rlineto":
            dx, dy = self.numbers(2)
            self.line(self.pen[0] + dx, self.pen[1] + dy)
        elif token == b"curveto":
            self.curve(*self.numbers(6))
        elif token == b"rcurveto":
            v = self.numbers(6)
            px, py = self.pen
            self.curve(px + v[0], py + v[1], px + v[2], py + v[3], px + v[4], py + v[5])
        elif token == b"arc":
            self.arc(*self.numbers(5), False)
        elif token == b"arcn":
            self.arc(*self.numbers(5), True)
        elif token == b"closepath":
            self.flush(True)
        elif token == b"stroke":
            self.paint(False)
        elif token in (b"fill", b"eofill"):
            self.paint(True)
        elif token in (b"clip", b"eoclip"):
            return
        elif token == b"gsave":
            self.saved.append(self.ctm)
        elif token == b"grestore":
            if self.saved:
                self.ctm = self.saved.pop()
        elif token == b"translate":
            tx, ty = self.numbers(2)
            self.ctm = _multiply((1.0, 0.0, 0.0, 1.0, tx, ty), self.ctm)
        elif token == b"scale":
            sx, sy = self.numbers(2)
            self.ctm = _multiply((sx, 0.0, 0.0, sy, 0.0, 0.0), self.ctm)
        elif token == b"rotate":
            angle = math.radians(self.numbers(1)[0])
            cosine, sine = math.cos(angle), math.sin(angle)
            self.ctm = _multiply((cosine, sine, -sine, cosine, 0.0, 0.0), self.ctm)


def import_eps(data: bytes) -> list[tuple[list[tuple[float, float]], bool]]:
    machine = _Machine()
    try:
        machine.execute(_tokens(_body(data)))
    except ValueError as error:
        raise ValueError("That EPS file is too complex to read. Save it as PDF or SVG.") from error
    if not machine.paths:
        raise ValueError("No vector paths were found in that EPS file. Save it as PDF or SVG.")
    return machine.paths
