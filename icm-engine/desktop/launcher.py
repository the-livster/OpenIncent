"""Desktop launcher for icm-engine — starts API server and opens native webview."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from icm_engine.api import app as api_app


def find_free_port(start: int = 8765) -> int:
    for port in range(start, start + 100):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found in range")


def _get_app_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = str(Path.home() / ".local" / "share")
    p = Path(base) / "OpenIncent"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_settings_path() -> Path:
    return _get_app_dir() / "settings.json"


def _load_settings() -> dict[str, object]:
    path = _get_settings_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {}


def _save_settings(settings: dict[str, object]) -> None:
    _get_settings_path().write_text(
        json.dumps(settings, indent=2), encoding="utf-8",
    )


def _add_desktop_routes(app: FastAPI) -> None:
    @app.get("/settings")
    def get_settings() -> dict[str, object]:
        return _load_settings()

    @app.put("/settings")
    async def put_settings(body: dict[str, object]) -> dict[str, object]:
        current = _load_settings()
        current.update(body)
        _save_settings(current)
        return current


def _find_ui_dist() -> Path | None:
    """Find the built UI directory, checking several locations in order."""
    # 1. PyInstaller bundle: sys._MEIPASS/ui_dist
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        bundled = meipass / "ui_dist"
        if bundled.exists():
            return bundled

    # 2. Development: next to this script
    dev_ui = Path(__file__).parent / "ui_dist"
    if dev_ui.exists():
        return dev_ui

    # 3. Environment override
    env_ui = os.environ.get("ICM_UI_DIST", "")
    if env_ui:
        p = Path(env_ui)
        if p.exists():
            return p

    # 4. Sibling repo (monorepo layout)
    sibling = Path(__file__).parent.parent.parent / "icm-ui" / "dist"
    if sibling.exists():
        return sibling

    return None


def mount_static_ui(app: FastAPI, ui_dir: Path) -> None:
    app.mount("/assets", StaticFiles(directory=str(ui_dir / "assets")), name="ui_assets")

    @app.get("/{full_path:path}", response_model=None)
    async def serve_spa(full_path: str = "") -> FileResponse | HTMLResponse:
        index = ui_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse("<h1>UI not found</h1>", status_code=404)


def run_server(port: int) -> None:
    uvicorn.run(api_app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    os.environ["ICM_DESKTOP"] = "1"

    port = find_free_port()
    ui_dir = _find_ui_dist()

    if ui_dir:
        _add_desktop_routes(api_app)
        mount_static_ui(api_app, ui_dir)
    else:
        print("Warning: UI dist not found. Starting API-only.")

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    for _ in range(50):
        try:
            with socket.socket() as s:
                s.connect(("127.0.0.1", port))
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Server failed to start within 5 seconds")

    webview.create_window(
        "OpenIncent",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
