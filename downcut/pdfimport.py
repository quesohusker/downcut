from __future__ import annotations

import io
import math

import numpy as np
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream

from .model import MM_PER_IN

PT_MM = MM_PER_IN / 72.0
MAX_DEPTH = 8
CLOSING = {b"s", b"b", b"b*"}
FILLING = {b"f", b"F", b"f*", b"B", b"B*"}
STROKING = {b"S"}
Identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply(a, b):
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def apply(matrix, x, y):
    return (matrix[0] * x + matrix[2] * y + matrix[4], matrix[1] * x + matrix[3] * y + matrix[5])


def flatten_cubic(p0, p1, p2, p3) -> list[tuple[float, float]]:
    span = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    count = int(np.clip(math.ceil(span * PT_MM / 0.5), 8, 96))
    out = []
    for t in np.linspace(0, 1, count + 1)[1:]:
        u = 1 - t
        out.append(
            (
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            )
        )
    return out


class _Painter:
    def __init__(self, reader):
        self.reader = reader
        self.paths: list[tuple[list[tuple[float, float]], bool]] = []
        self.text_seen = False

    def run(self, stream, resources, matrix, depth: int = 0) -> None:
        operations = ContentStream(stream, self.reader).operations
        stack: list = []
        ctm = matrix
        subpaths: list[list] = []
        current: list = []
        pending_clip = False

        def flush_current(closed: bool) -> None:
            nonlocal current
            if len(current) > 1:
                subpaths.append([current, closed])
            current = []

        def paint(close_all: bool, keep: bool) -> None:
            flush_current(False)
            if keep:
                for points, closed in subpaths:
                    self.paths.append(([(x * PT_MM, y * PT_MM) for x, y in points], closed or close_all))
            subpaths.clear()

        for operands, operator in operations:
            if operator == b"q":
                stack.append(ctm)
            elif operator == b"Q":
                ctm = stack.pop() if stack else matrix
            elif operator == b"cm":
                ctm = _multiply(tuple(float(v) for v in operands), ctm)
            elif operator == b"m":
                flush_current(False)
                current = [apply(ctm, float(operands[0]), float(operands[1]))]
            elif operator == b"l":
                current.append(apply(ctm, float(operands[0]), float(operands[1])))
            elif operator in (b"c", b"v", b"y"):
                values = [float(v) for v in operands]
                start = current[-1]
                if operator == b"c":
                    c1 = apply(ctm, values[0], values[1])
                    c2 = apply(ctm, values[2], values[3])
                    end = apply(ctm, values[4], values[5])
                elif operator == b"v":
                    c1 = start
                    c2 = apply(ctm, values[0], values[1])
                    end = apply(ctm, values[2], values[3])
                else:
                    c1 = apply(ctm, values[0], values[1])
                    end = apply(ctm, values[2], values[3])
                    c2 = end
                current.extend(flatten_cubic(start, c1, c2, end))
            elif operator == b"h":
                flush_current(True)
            elif operator == b"re":
                x, y, w, h = (float(v) for v in operands)
                flush_current(False)
                corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                subpaths.append([[apply(ctm, a, b) for a, b in corners], True])
            elif operator in (b"W", b"W*"):
                pending_clip = True
            elif operator == b"n":
                paint(False, False)
                pending_clip = False
            elif operator in CLOSING:
                paint(True, not pending_clip)
                pending_clip = False
            elif operator in FILLING:
                paint(True, not pending_clip)
                pending_clip = False
            elif operator in STROKING:
                paint(False, not pending_clip)
                pending_clip = False
            elif operator in (b"Tj", b"TJ", b"'", b'"'):
                self.text_seen = True
            elif operator == b"Do" and depth < MAX_DEPTH:
                self._form(operands[0], resources, ctm, depth)

    def _form(self, name, resources, ctm, depth: int) -> None:
        if resources is None or "/XObject" not in resources:
            return
        items = resources["/XObject"].get_object()
        if name not in items:
            return
        form = items[name].get_object()
        if form.get("/Subtype") != "/Form":
            return
        local = form.get("/Matrix")
        matrix = _multiply(tuple(float(v) for v in local), ctm) if local else ctm
        inner = form.get("/Resources")
        self.run(form, inner.get_object() if inner else resources, matrix, depth + 1)


def import_pdf(data: bytes) -> list[tuple[list[tuple[float, float]], bool]]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("That PDF is password protected.")
        page = reader.pages[0]
        contents = page.get_contents()
    except PdfReadError as error:
        raise ValueError("That file could not be read as a PDF.") from error
    except (IndexError, KeyError) as error:
        raise ValueError("That PDF has no pages.") from error
    if contents is None:
        raise ValueError("That PDF page is empty.")
    resources = page.get("/Resources")
    painter = _Painter(reader)
    painter.run(contents, resources.get_object() if resources else None, Identity)
    if not painter.paths:
        if painter.text_seen:
            raise ValueError("That PDF holds live text. Convert the text to outlines and save it again.")
        raise ValueError("No vector paths were found in that PDF.")
    return painter.paths
