import base64
import io
import json
import subprocess
import sys
import time
import urllib.request

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def sample_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(b"72 72 144 72 re f")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def main(executable):
    process = subprocess.Popen([executable, "--no-browser", "--port", str(PORT)])
    try:
        for _ in range(120):
            try:
                boot = call("/api/bootstrap")
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("Downcut did not start")
        print("version:", boot["version"], "tools:", len(boot["tools"]), "fonts:", len(boot["fonts"]))
        assert len(boot["tools"]) >= 8, "The tool table is missing"

        with urllib.request.urlopen(BASE + "/", timeout=10) as response:
            page = response.read().decode()
        assert "<title>Downcut</title>" in page, "The page was not served"
        with urllib.request.urlopen(BASE + "/style.css", timeout=10) as response:
            assert response.headers["content-type"].startswith("text/css"), "Wrong type for style.css"
        with urllib.request.urlopen(BASE + "/app.js", timeout=10) as response:
            assert "javascript" in response.headers["content-type"], "Wrong type for app.js"
        print("page: ok")

        job = call("/api/sample")
        computed = call("/api/compute", {"job": job, "post": "grbl", "title": "smoke"})
        assert all(row["passed"] for row in computed["programs"]), computed["programs"]
        print("toolpaths:", len(computed["paths"]), "all passed the checker")

        job["arc_output"] = True
        for post in ("grbl", "mach3", "linuxcnc"):
            files = call("/api/gcode", {"job": job, "post": post, "title": "smoke"})["files"]
            assert files and all(row["report"]["passed"] for row in files), post
        print("posts: ok")

        preview = call("/api/preview", {"job": job, "post": "grbl"})
        assert preview["image"].startswith("data:image/png"), "The preview did not render"
        assert preview["surface"]["nx"] > 10, "The 3D surface is missing"
        print("preview: ok")

        vectors = job["vectors"][:4]
        dxf = call("/api/export-vectors", {"format": "dxf", "vectors": vectors, "width": 500, "height": 300})
        back = call("/api/import", {"name": "a.dxf", "text": dxf["text"]})["vectors"]
        assert back, "DXF did not round trip"
        svg = call("/api/export-vectors", {"format": "svg", "vectors": vectors, "width": 500, "height": 300})
        assert call("/api/import", {"name": "a.svg", "text": svg["text"]})["vectors"], "SVG did not round trip"
        data = base64.b64encode(sample_pdf()).decode()
        assert call("/api/import", {"name": "a.pdf", "data": data})["vectors"], "PDF import failed"
        print("files: ok")

        text = call("/api/text", {"text": "CNC", "font": boot["stroke_fonts"][0], "height": 25})
        assert text["vectors"], "Single-line text failed"
        if boot["fonts"]:
            outline = call("/api/text", {"text": "CNC", "font": boot["fonts"][0], "height": 25})
            print("outline font:", boot["fonts"][0], len(outline["vectors"]))
        print("text: ok")

        nest = call("/api/nest", {"vectors": vectors[:2], "width": 1000, "height": 600, "gap": 6})
        assert nest["placed"] >= 1, "Nesting failed"
        print("nesting: ok")

        post = call("/api/post/check", {"source": boot["starter_post"]})
        assert post["report"]["passed"], post["report"]
        print("machine post: ok")

        sheet = call("/api/sheet", {"job": job, "post": "grbl", "title": "smoke"})
        assert "setup sheet" in sheet["html"], "The setup sheet failed"
        print("setup sheet: ok")

        call("/api/shutdown", {})
        process.wait(timeout=15)
        print("shutdown: ok")
    finally:
        if process.poll() is None:
            process.kill()


if __name__ == "__main__":
    main(sys.argv[1])
