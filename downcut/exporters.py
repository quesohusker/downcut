from __future__ import annotations

import io
from xml.sax.saxutils import quoteattr

import ezdxf

from .model import Vector


def to_svg(vectors: list[Vector], width: float, height: float) -> str:
    by_layer: dict[str, list[Vector]] = {}
    for vector in vectors:
        by_layer.setdefault(vector.layer, []).append(vector)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.4f}mm" height="{height:.4f}mm" '
        f'viewBox="0 0 {width:.4f} {height:.4f}">',
    ]
    for layer, rows in by_layer.items():
        lines.append(f'<g id={quoteattr(layer)} fill="none" stroke="#000" stroke-width="0.1">')
        for vector in rows:
            words = [f"{'M' if i == 0 else 'L'}{x:.4f} {height - y:.4f}" for i, (x, y) in enumerate(vector.points)]
            if vector.closed:
                words.append("Z")
            lines.append(f'<path d="{" ".join(words)}"/>')
        lines.append("</g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def to_dxf(vectors: list[Vector]) -> str:
    document = ezdxf.new("R2010", setup=False)
    document.header["$INSUNITS"] = 4
    space = document.modelspace()
    for vector in vectors:
        if vector.layer not in document.layers:
            document.layers.add(vector.layer)
        space.add_lwpolyline(
            [(x, y) for x, y in vector.points],
            format="xy",
            close=vector.closed,
            dxfattribs={"layer": vector.layer},
        )
    stream = io.StringIO()
    document.write(stream)
    return stream.getvalue()
