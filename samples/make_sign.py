import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from downcut.gcode import export
from downcut.sample import sign_job
from downcut.simulate import render, simulate
from downcut.toolpaths import build_all
from downcut.verify import check, limits_for

out = ROOT / "samples" / "output"
out.mkdir(exist_ok=True)
start = time.time()
job = sign_job()
paths = build_all(job)
print(f"toolpaths {time.time() - start:.2f}s")
for path in paths:
    print(path.name, len(path.moves), "moves", path.warnings)
programs = []
for item in export(job, paths, "grbl", "sample-sign"):
    tool = job.tool(item.tool_id)
    (out / item.name).write_text(item.text)
    report = check(item.text, limits_for(job, tool))
    print(item.name, "passed" if report.passed else report.errors[:5], f"{report.seconds / 60:.1f} min", f"zmin {report.z_min:.3f} mm", f"lines {report.line_count}")
    programs.append((tool, item.text))
start = time.time()
heights, res = simulate(job, programs, 0.2)
print(f"simulate {time.time() - start:.2f}s res {res}")
(out / "sign-preview.png").write_bytes(render(heights, res, job.material.thickness))
