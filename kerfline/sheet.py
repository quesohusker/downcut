from __future__ import annotations

import datetime
from html import escape

from .model import MM_PER_IN

STYLE = """
body { font: 13px/1.45 -apple-system, Helvetica, Arial, sans-serif; color: #1c1c1c; margin: 24px; }
h1 { font-size: 20px; margin: 0 0 2px; }
h2 { font-size: 14px; margin: 18px 0 6px; border-bottom: 1px solid #999; padding-bottom: 2px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #ddd; vertical-align: top; }
th { font-size: 12px; color: #444; }
.facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 18px; }
.fail { color: #b3261e; font-weight: 600; }
.warn { color: #8a5a00; }
img { width: 100%; max-width: 760px; border: 1px solid #ccc; margin-top: 8px; }
ul { margin: 4px 0; padding-left: 18px; }
"""


def _length(job, value: float) -> str:
    if job.units == "in":
        return f"{value / MM_PER_IN:.3f} in"
    return f"{value:.2f} mm"


def _rate(job, value: float) -> str:
    if job.units == "in":
        return f"{value / MM_PER_IN:.1f} in/min"
    return f"{value:.0f} mm/min"


def _minutes(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    return f"{minutes // 60} h {minutes % 60} min"


def setup_html(job, rows: list[dict], paths: list, image: str, title: str, post_name: str) -> str:
    material = job.material
    today = datetime.date.today().strftime("%d %b %Y")
    facts = [
        ("Sheet", f"{_length(job, material.width)} x {_length(job, material.height)}"),
        ("Thickness", _length(job, material.thickness)),
        ("Z zero", "Top of material" if job.z_zero == "top" else "Spoilboard"),
        ("XY zero", "Center of sheet" if job.xy_origin == "center" else "Lower left corner"),
        ("Safe Z", _length(job, job.safe_z)),
        ("Spoilboard allowance", _length(job, job.spoilboard_allowance)),
        ("Machine post", post_name),
    ]
    if job.machine.get("name"):
        facts.append(("Machine", job.machine["name"]))
    fact_html = "".join(f"<div><b>{escape(k)}:</b> {escape(v)}</div>" for k, v in facts)

    program_rows = []
    total = 0.0
    for index, row in enumerate(rows, start=1):
        tool = row["tool"]
        report = row["report"]
        total += report.seconds
        status = "Passed" if report.passed else '<span class="fail">Failed</span>'
        program_rows.append(
            "<tr>"
            f"<td>{index}</td><td>{escape(row['file'].name)}</td><td>{escape(tool.name)}</td>"
            f"<td>{tool.rpm}</td><td>{_rate(job, tool.feed)}</td><td>{_rate(job, tool.plunge)}</td>"
            f"<td>{_length(job, report.z_min)}</td><td>{_minutes(report.seconds)}</td><td>{status}</td>"
            "</tr>"
        )

    warnings: list[str] = []
    for path in paths:
        warnings.extend(f"{path.name}: {warning}" for warning in path.warnings)
    for row in rows:
        warnings.extend(f"{row['file'].name}: {warning}" for warning in row["report"].warnings)
        warnings.extend(f"{row['file'].name}: {error}" for error in row["report"].errors)
    warning_html = "".join(f'<li class="warn">{escape(w)}</li>' for w in dict.fromkeys(warnings)) or "<li>None</li>"
    picture = f'<h2>Simulated cut</h2><img alt="Simulated cut of the job" src="{image}">' if image else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(title)} setup sheet</title>
<style>{STYLE}</style></head><body>
<h1>{escape(title)} setup sheet</h1>
<div>{today}. Estimated run time {_minutes(total)}, not counting tool changes.</div>
<h2>Setup</h2><div class="facts">{fact_html}</div>
<h2>Programs, in cut order</h2>
<table><tr><th>#</th><th>File</th><th>Tool</th><th>RPM</th><th>Feed</th><th>Plunge</th><th>Deepest</th><th>Time</th><th>Checker</th></tr>
{''.join(program_rows)}</table>
<h2>Warnings</h2><ul>{warning_html}</ul>
<h2>Before you run</h2>
<ul><li>Air-cut every new program above the material.</li><li>Confirm clamps sit below safe Z and outside the cut.</li>
<li>Set Z zero again after every tool change.</li><li>Keep a hand near the stop.</li></ul>
{picture}
</body></html>
"""
