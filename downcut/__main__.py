from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .server import create_app

HOST = "127.0.0.1"


def running_downcut(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/bootstrap", timeout=1) as response:
            return "starter_post" in json.loads(response.read())
    except (OSError, ValueError):
        return False


def free_port(preferred: int) -> int:
    with socket.socket() as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, preferred))
            return preferred
        except OSError:
            probe.bind((HOST, 0))
            return probe.getsockname()[1]


def ensure_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = Path.home() / ".downcut" / "downcut.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    stream = open(log, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def main() -> None:
    parser = argparse.ArgumentParser(prog="downcut")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args, _ = parser.parse_known_args()
    ensure_streams()
    if running_downcut(args.port):
        url = f"http://{HOST}:{args.port}"
        print(f"Downcut is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    port = free_port(args.port)
    url = f"http://{HOST}:{port}"
    print(f"Downcut is running at {url}")
    print("Use File, Quit Downcut to stop it.")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
