"""Desktop launcher for icm-engine — starts API server and opens native webview."""
from __future__ import annotations

import json
import logging
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

from icm_engine import __version__
from icm_engine.api import app as api_app

logger = logging.getLogger("icm.desktop")


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

    # --- Update routes ---
    from icm_engine.updater import (
        UpdateInfo,
        check_for_updates,
        install_update,
    )

    _update_state: dict[str, object] = {
        "status": "idle",         # idle | checking | available | downloading | installing | error
        "info": None,             # UpdateInfo dict when available
        "message": "",
    }

    _UPDATE_FEED_DEFAULT = (
        "https://github.com/the-livster/OpenIncent/releases/latest/download/latest.json"
    )

    def _get_feed_url() -> str:
        settings = _load_settings()
        url = settings.get("update_feed_url", "")
        if url and isinstance(url, str) and url.strip():
            return str(url).strip()
        return _UPDATE_FEED_DEFAULT

    def _on_update_found(info: UpdateInfo) -> None:
        _update_state["status"] = "available"
        _update_state["info"] = {
            "version": info.version,
            "release_notes": info.release_notes,
        }

    @app.get("/update/status")
    def update_status() -> dict[str, object]:
        return dict(_update_state)

    @app.post("/update/check")
    def update_check() -> dict[str, object]:
        if _update_state["status"] in ("downloading", "installing"):
            return dict(_update_state)
        _update_state["status"] = "checking"
        _update_state["message"] = ""
        try:
            info = check_for_updates(__version__, _get_feed_url())
        except Exception as e:
            _update_state["status"] = "error"
            _update_state["message"] = str(e)
            return dict(_update_state)
        if info:
            _on_update_found(info)
        else:
            _update_state["status"] = "idle"
            _update_state["message"] = "You are on the latest version."
        return dict(_update_state)

    @app.post("/update/apply")
    def update_apply() -> dict[str, object]:
        if _update_state["status"] != "available":
            return {"status": "error", "message": "No update available"}
        info_dict = _update_state.get("info")
        if not info_dict or not isinstance(info_dict, dict):
            return {"status": "error", "message": "No update info"}
        _update_state["status"] = "downloading"

        # Re-fetch to get the full manifest with download_url etc.
        current_info = check_for_updates(__version__, _get_feed_url())
        if not current_info:
            _update_state["status"] = "error"
            _update_state["message"] = "Update feed changed — please check again"
            return dict(_update_state)

        # Download the artifact
        from icm_engine.updater import _download
        zip_data = _download(current_info.download_url)
        if zip_data is None:
            _update_state["status"] = "error"
            _update_state["message"] = "Download failed"
            return dict(_update_state)

        # Install
        _update_state["status"] = "installing"
        ok, msg = install_update(zip_data, current_info.sha256, current_info.signature)
        if ok:
            # Relaunch from a thread so the response can be sent
            threading.Thread(target=_delayed_relaunch, daemon=True).start()
            return {"status": "installed", "message": "Relaunching..."}
        else:
            _update_state["status"] = "error"
            _update_state["message"] = msg
            return dict(_update_state)

    @app.post("/update/skip")
    def update_skip() -> dict[str, object]:
        info_dict = _update_state.get("info")
        if info_dict and isinstance(info_dict, dict):
            settings = _load_settings()
            settings["update_skip_version"] = info_dict.get("version", "")
            _save_settings(settings)
        _update_state["status"] = "idle"
        _update_state["info"] = None
        _update_state["message"] = ""
        return dict(_update_state)


def _delayed_relaunch() -> None:
    """Wait for the HTTP response to flush, then relaunch."""
    time.sleep(0.5)
    from icm_engine.updater import relaunch
    relaunch()


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

    # --- Background update check (non-blocking) ---
    _start_background_update_check()

    webview.create_window(
        "OpenIncent",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    webview.start()


def _start_background_update_check() -> None:
    """Run update check in a background thread, respecting throttle + skip."""
    from icm_engine.updater import UpdateInfo, is_newer, start_update_check

    settings = _load_settings()

    # Honour skip-version
    skip_version = str(settings.get("update_skip_version", ""))
    if skip_version:
        if not is_newer(skip_version, __version__):
            # skip_version <= current → clear the skip
            settings.pop("update_skip_version", None)
            _save_settings(settings)
        else:
            logger.debug("Skipping update check: version %s was skipped", skip_version)
            return

    # Throttle: check at most once per 6 hours
    last_checked_str = str(settings.get("update_last_checked", ""))
    if last_checked_str:
        try:
            last_checked = float(last_checked_str)
            if time.time() - last_checked < 6 * 3600:
                logger.debug("Update check throttled (last checked %.0f min ago)",
                             (time.time() - last_checked) / 60)
                return
        except ValueError:
            pass


    feed_url = str(settings.get("update_feed_url", "")) or (
        "https://github.com/the-livster/OpenIncent/releases/latest/download/latest.json"
    )

    def _on_found(info: UpdateInfo) -> None:
        # Check skip again (might have been set between startup and now)
        current_settings = _load_settings()
        skip_v = str(current_settings.get("update_skip_version", ""))
        if skip_v and not is_newer(info.version, skip_v):
            return
        logger.info("Update available: %s → %s", __version__, info.version)

    start_update_check(__version__, feed_url, on_update_available=_on_found)

    # Update last-checked timestamp
    settings["update_last_checked"] = time.time()
    _save_settings(settings)


if __name__ == "__main__":
    main()
