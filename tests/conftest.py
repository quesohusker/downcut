import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kerfline.gcode import export
from kerfline.model import Job, Material, default_tools
from kerfline.simulate import simulate
from kerfline.toolpaths import build_all
from kerfline.verify import check, limits_for


@pytest.fixture
def job():
    return Job(material=Material(300.0, 200.0, 19.0), units="mm", tools=default_tools())


@pytest.fixture
def run():
    def execute(job, resolution=0.25, post="grbl"):
        paths = build_all(job)
        files = export(job, paths, post)
        reports = []
        programs = []
        for item in files:
            tool = job.tool(item.tool_id)
            reports.append(check(item.text, limits_for(job, tool)))
            programs.append((tool, item.text))
        heights, res = simulate(job, programs, resolution)
        return paths, files, reports, heights, res

    return execute
