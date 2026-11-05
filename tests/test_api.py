import base64
import re

import pytest
from fastapi.testclient import TestClient

from kerfline.server import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(scope="module")
def sample(client):
    return client.get("/api/sample").json()


def test_page_and_assets_are_served(client):
    assert "Kerfline" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").headers["content-type"].startswith("text/css")


def test_page_loads_nothing_from_the_network(client):
    for name in ("/", "/app.js", "/platform.js", "/icons.js", "/shell.js", "/pen.js", "/panels.js", "/orbit.js", "/style.css"):
        text = client.get(name).text
        assert "http://" not in text
        assert "https://" not in text


def test_only_local_hosts_are_served():
    guarded = TestClient(create_app(), base_url="http://evil.example")
    assert guarded.get("/api/bootstrap").status_code == 400
    assert TestClient(create_app(), base_url="http://127.0.0.1:8765").get("/api/bootstrap").status_code == 200


def test_every_script_target_is_on_the_page(client):
    page = client.get("/").text
    found = set()
    for name in ("/app.js", "/pen.js", "/panels.js", "/shell.js"):
        found.update(re.findall(r"\$\('#([a-z0-9-]+)'\)", client.get(name).text))
    assert len(found) > 60
    missing = sorted(name for name in found if f'id="{name}"' not in page)
    assert missing == []


def test_every_menu_item_has_a_target(client):
    page = client.get("/").text
    shell = client.get("/shell.js").text
    for name in re.findall(r'data-click="([a-z0-9-]+)"', page):
        assert f'id="{name}"' in page, name
    for name in re.findall(r'data-run="([a-z0-9-]+)"', page):
        assert f"'{name}':" in shell or f"  {name}:" in shell, name
    for name in re.findall(r'button[^>]* data-form="([a-z]+)"', page):
        assert f'<div data-form="{name}"' in page, name


def test_every_icon_has_a_button(client):
    icons = client.get("/icons.js").text
    table = icons[icons.index("const ICONS") : icons.index("function addIcon")]
    names = re.findall(r"^  '?([a-z_-]+)'?:", table, re.MULTILINE)
    page = client.get("/").text
    script = client.get("/app.js").text
    assert len(names) > 40
    for name in names:
        assert f'id="{name}"' in page or f"  {name}: {{" in script, name


def test_bootstrap_lists_tools_posts_and_fonts(client):
    data = client.get("/api/bootstrap").json()
    assert {p["id"] for p in data["posts"]} == {"grbl", "mach3", "linuxcnc"}
    assert any(t["kind"] == "vbit" for t in data["tools"])
    assert data["fonts"]


def test_compute_reports_paths_and_checker_results(client, sample):
    data = client.post("/api/compute", json={"job": sample}).json()
    assert len(data["paths"]) == 3
    assert all(p["runs"] for p in data["paths"])
    assert all(row["passed"] for row in data["programs"]), data["programs"]


def test_compute_cache_keeps_operation_ids(client, sample):
    first = client.post("/api/compute", json={"job": sample}).json()
    second = client.post("/api/compute", json={"job": sample}).json()
    assert [p["operation_id"] for p in first["paths"]] == [p["operation_id"] for p in second["paths"]]


def test_preview_returns_a_png(client, sample):
    data = client.post("/api/preview", json={"job": sample}).json()
    header, payload = data["image"].split(",", 1)
    assert header == "data:image/png;base64"
    assert base64.b64decode(payload)[:8] == b"\x89PNG\r\n\x1a\n"


def test_gcode_returns_one_file_per_tool(client, sample):
    files = client.post("/api/gcode", json={"job": sample, "post": "grbl", "title": "sign"}).json()["files"]
    assert len(files) == 3
    assert all(f["report"]["passed"] for f in files)
    assert all(f["name"].startswith("sign-") and f["name"].endswith(".nc") for f in files)


def test_failed_program_is_reported_as_failed(client, sample):
    job = dict(sample)
    job["operations"] = [dict(sample["operations"][2], params={"side": "outside", "depth": 40.0})]
    data = client.post("/api/compute", json={"job": job}).json()
    assert data["programs"][0]["passed"] is False
    assert any("floor" in e for e in data["programs"][0]["errors"])


def test_import_accepts_binary_data(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100"><rect width="50" height="20"/></svg>'
    body = {"name": "a.svg", "data": base64.b64encode(svg).decode()}
    vectors = client.post("/api/import", json=body).json()["vectors"]
    assert len(vectors) == 1 and vectors[0]["closed"]


def test_every_edit_route_answers(client):
    rect = client.post(
        "/api/shape", json={"kind": "rectangle", "params": {"x": 0, "y": 0, "width": 50, "height": 50}}
    ).json()["vectors"]
    for body in (
        {"operation": "fillet", "radius": 3.0},
        {"operation": "dogbone", "diameter": 6.0},
        {"operation": "tbone", "diameter": 6.0, "side": "outside"},
        {"operation": "smooth", "tolerance": 0.1},
        {"operation": "simplify", "tolerance": 0.1},
        {"operation": "dedupe"},
        {"operation": "join", "tolerance": 0.1},
        {"operation": "close"},
    ):
        response = client.post("/api/edit", json={**body, "vectors": rect})
        assert response.status_code == 200, body
    grid = client.post(
        "/api/layout", json={"kind": "grid", "vectors": rect, "columns": 2, "rows": 1, "step_x": 60, "step_y": 0}
    ).json()["vectors"]
    assert len(grid) == 1
    nest = client.post("/api/nest", json={"vectors": rect + grid, "width": 200, "height": 100, "gap": 3}).json()
    assert nest["placed"] == 2
    assert client.post("/api/validate", json={"vectors": rect}).json()["issues"] == []
    svg = client.post(
        "/api/export-vectors", json={"format": "svg", "vectors": rect, "width": 100, "height": 100}
    ).json()
    assert svg["name"].endswith(".svg")


def test_shape_and_edit_round_trip(client):
    a = client.post(
        "/api/shape", json={"kind": "rectangle", "params": {"x": 0, "y": 0, "width": 50, "height": 50}}
    ).json()["vectors"]
    b = client.post(
        "/api/shape", json={"kind": "ellipse", "params": {"cx": 50, "cy": 25, "rx": 10, "ry": 10}}
    ).json()["vectors"]
    welded = client.post("/api/edit", json={"operation": "weld", "vectors": a + b}).json()["vectors"]
    assert len(welded) == 1
    grown = client.post("/api/edit", json={"operation": "offset", "vectors": a, "distance": 2.0}).json()
    assert grown["vectors"]


def test_text_makes_counters_as_separate_rings(client):
    data = client.get("/api/bootstrap").json()
    font = "Arial Bold" if "Arial Bold" in data["fonts"] else data["fonts"][0]
    vectors = client.post("/api/text", json={"text": "O", "font": font, "height": 25.0}).json()["vectors"]
    assert len(vectors) == 2


def test_bad_input_returns_a_clear_error(client):
    response = client.post("/api/import", json={"name": "a.png", "text": "x"})
    assert response.status_code == 400
    assert "SVG, DXF, PDF" in response.json()["error"]
    response = client.post("/api/text", json={"text": "A", "font": "No Such Font", "height": 25})
    assert response.status_code == 400


def test_mime_tables_do_not_touch_system_files(client):
    import mimetypes

    assert mimetypes.knownfiles == []


def test_font_scan_survives_blocked_folders(monkeypatch):
    from pathlib import Path

    from kerfline import textvec

    class Blocked:
        def rglob(self, _):
            raise PermissionError("blocked by the sandbox")

    textvec.list_fonts.cache_clear()
    monkeypatch.setattr(textvec, "FONT_FOLDERS", [Blocked(), Path("/no/such/folder")])
    assert textvec.list_fonts() == ()
    textvec.list_fonts.cache_clear()
