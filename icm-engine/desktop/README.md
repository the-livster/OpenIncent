# Desktop launcher

Bundles icm-engine + web UI into a single Windows `.exe` with an embedded browser window. No terminal, no Python, no Docker — double-click to run.

## How it works

- **PyInstaller** bundles Python + all deps into a single executable
- **pywebview** wraps a native Chromium/Edge window that loads the React UI
- The FastAPI server starts in a background thread on a random free port
- The UI is pre-built React app served as static files by FastAPI

## Build instructions

### 1. Build the web UI

```bash
cd ../icm-ui
npm install
npm run build
```

If the UI repo is at a different path, set `ICM_UI_DIST`:

```bash
set ICM_UI_DIST=C:\path\to\icm-ui\dist
```

### 2. Activate the icm-engine environment

```bash
cd ../icm-engine
uv sync
```

### 3. Build the exe

```bash
uv run python desktop/build.py
```

### 4. Output

```
desktop/dist/icm-engine/icm-engine.exe
```

### 5. Create installer (optional)

1. Install [Inno Setup](https://jrsoftware.org/isinfo.php) (free)
2. Open `desktop/installer.iss` in Inno Setup Compiler
3. Click Compile
4. Output: `desktop/dist/icm-engine-setup.exe`

## Bundle size

~150–250 MB. This includes the full Python interpreter, all dependencies, and the UI assets. Acceptable for v1.

## Cold start time

~5–15 seconds. Python interpreter + uvicorn startup. Acceptable for v1.

## Settings storage

- Windows: `%APPDATA%/OpenIncent/settings.json`
- Mac: `~/Library/Application Support/OpenIncent/settings.json`
- Linux: `~/.local/share/OpenIncent/settings.json`

Settings include: `anthropic_api_key`, `default_currency`, `recent_files`.

The SQLite database is stored alongside settings at `openincent.db` in the same directory.

## Manual test checklist

- [ ] Double-click exe → window opens within 15 seconds
- [ ] Load example plan + transactions + payees → calculate → see results
- [ ] Plan-from-text works when API key is configured
- [ ] Close window → process exits cleanly
- [ ] Run twice in a row — no port conflicts

## Known issues

### Windows SmartScreen

Unsigned executables trigger a SmartScreen warning on first run. Users click "More info → Run anyway." Solution: code-signing certificate ($200–400/year). Not in v1.

### Antivirus false positives

PyInstaller bundles are sometimes flagged by antivirus. This is a false positive. Whitelist or submit to the AV vendor.

### Missing UI dist

If the UI isn't built, the launcher starts API-only and shows an error page. Build the UI first.

### Mac/Linux

The same `launcher.py` works on Mac and Linux via pywebview. The PyInstaller build and Inno Setup installer are Windows-only. Use `py2app` or `pyinstaller` equivalents for Mac/Linux.

## Out of scope for v1

- Auto-updates
- Code signing
- Mac (.dmg) and Linux (.AppImage) builds
- Telemetry / crash reporting
- File associations (double-click .yaml to open in icm-engine)
