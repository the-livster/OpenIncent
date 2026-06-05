"""Build the desktop bundle with PyInstaller."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
REPO_ROOT = ROOT.parent
UI_DIST = REPO_ROOT.parent / "icm-ui" / "dist"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"


def is_windows() -> bool:
    return sys.platform == "win32"


def main() -> None:
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            shutil.rmtree(d)

    # Try to find UI dist
    ui_target = ROOT / "ui_dist"
    if not UI_DIST.exists():
        # Check environment override
        env_ui = Path(__file__).parent.parent / "icm-ui" / "dist"
        if env_ui.exists():
            ui_source = env_ui
        else:
            print(f"UI dist not found at {UI_DIST} or {env_ui}.")
            print("Build the UI first: cd icm-ui && npm install && npm run build")
            print("Skipping UI bundling — launcher will run API-only.")
            ui_source = None
    else:
        ui_source = UI_DIST

    if ui_source:
        if ui_target.exists():
            shutil.rmtree(ui_target)
        shutil.copytree(ui_source, ui_target)
        print(f"Copied UI dist from {ui_source} to {ui_target}")

    sep = ";" if is_windows() else ":"
    data_args = []
    if ui_target.exists():
        data_args = ["--add-data", f"{ui_target}{sep}ui_dist"]

    pyi_args = [
        "pyinstaller",
        "--name", "icm-engine",
        "--windowed",
        "--hidden-import", "icm_engine",
        "--hidden-import", "cryptography",
        "--collect-all", "icm_engine",
        *data_args,
        str(ROOT / "launcher.py"),
    ]

    icon = ROOT / "icon.ico"
    if icon.exists():
        pyi_args.insert(3, "--icon")
        pyi_args.insert(4, str(icon))

    subprocess.run(pyi_args, check=True, cwd=ROOT)

    print(f"Build complete. Output: {DIST_DIR / 'icm-engine'}")
    print(f"Executable: {DIST_DIR / 'icm-engine' / 'icm-engine.exe'}")


if __name__ == "__main__":
    main()
