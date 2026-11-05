from __future__ import annotations

import mimetypes
import os
import threading
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api

WEB = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    mimetypes.knownfiles = []
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("text/html", ".html")
    app = FastAPI(title="Downcut", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.exception_handler(ValueError)
    async def bad_input(_: Request, error: ValueError):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_field(_: Request, error: KeyError):
        return JSONResponse({"error": f"Missing value: {error}"}, status_code=400)

    @app.get("/api/bootstrap")
    def bootstrap():
        return api.bootstrap()

    @app.get("/api/sample")
    def sample():
        return api.sample_job()

    @app.post("/api/shutdown")
    def shutdown():
        threading.Timer(0.2, os._exit, [0]).start()
        return {"ok": True}

    routes = {
        "/api/import": api.import_vectors,
        "/api/shape": api.make_shape,
        "/api/text": api.make_text,
        "/api/edit": api.edit_vectors,
        "/api/trim": api.trim_vector,
        "/api/extend": api.extend_vector,
        "/api/layout": api.layout_vectors,
        "/api/nest": api.nest_vectors,
        "/api/validate": api.validate_vectors,
        "/api/scan": api.scan_vectors,
        "/api/trim-overlap": api.trim_overlap,
        "/api/export-vectors": api.export_vectors,
        "/api/inlay": api.make_inlay,
        "/api/template/make": api.make_template,
        "/api/template/apply": api.apply_template,
        "/api/machine": api.import_machine,
        "/api/post/check": api.check_post,
        "/api/sheet": api.setup_sheet,
        "/api/compute": api.compute,
        "/api/preview": api.preview,
        "/api/gcode": api.gcode,
    }

    def bind(path, handler):
        @app.post(path)
        def endpoint(body: dict = Body(...)):
            return handler(body)

    for path, handler in routes.items():
        bind(path, handler)

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
