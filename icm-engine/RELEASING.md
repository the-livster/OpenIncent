# Releasing OpenIncent

## Prerequisites

- Python 3.11+ with `uv`
- `cryptography` installed (`uv sync --extra desktop`)
- A signing key configured (see below)
- Push access to `github.com/the-livster/OpenIncent`

## One-time: generate signing keys

```bash
# Generate a 32-byte private key
python -c "import os; print(os.urandom(32).hex())"
# Save this output — it's your private key. NEVER commit it.

# Store it:
echo "your-64-char-hex-key" > ~/.icm-signing-key
# Or: set ICM_SIGNING_KEY=your-64-char-hex-key in your environment

# Derive the public key:
python tools/sign_release.py --public-key
# Copy the output into src/icm_engine/updater.py:
#   _UPDATE_PUBLIC_KEY_HEX = "<public-key-hex>"
#   _PUBLIC_KEY_PLACEHOLDER = False
```

## Cutting a release

### 1. Bump version

Edit `pyproject.toml`:
```toml
[project]
version = "0.2.0"   # was "0.1.0"
```

Commit: `chore: bump version to 0.2.0`

### 2. Build the desktop app

```bash
# Ensure the UI is built
cd ../icm-ui && npm run build && cd ../icm-engine

# Build the desktop .exe
cd desktop
uv run python build.py
```

Output: `desktop/dist/icm-engine/` containing `icm-engine.exe` + `_internal/`

### 3. Package as zip

```bash
cd desktop/dist
# On Windows (PowerShell):
Compress-Archive -Path icm-engine\* -DestinationPath icm-engine-0.2.0-win64.zip
```

### 4. Sign the zip

```bash
python tools/sign_release.py desktop/dist/icm-engine-0.2.0-win64.zip
```

Output:
```
SHA-256:  a1b2c3d4...
Signature: e5f6a7b8...
Saved to:  desktop/dist/icm-engine-0.2.0-win64.zip.sig
```

### 5. Create the manifest

Create `latest.json`:
```json
{
  "version": "0.2.0",
  "download_url": "https://github.com/the-livster/OpenIncent/releases/download/v0.2.0/icm-engine-0.2.0-win64.zip",
  "sha256": "<sha256-from-step-4>",
  "signature": "<signature-from-step-4>",
  "release_notes": "- New feature X\n- Fixed bug Y"
}
```

### 6. Create GitHub Release

1. Go to https://github.com/the-livster/OpenIncent/releases/new
2. Tag: `v0.2.0`
3. Title: `v0.2.0`
4. Attach: `icm-engine-0.2.0-win64.zip`
5. Attach: `latest.json`
6. Publish

### 7. Verify

- Install the current version, launch it
- Wait for the background check (or click "Check for updates" in the Settings tab)
- The update banner should appear with version and release notes
- Click "Update & restart" → app downloads, verifies, installs, relaunches
- New version should be running

## Code signing (Windows)

The .exe is currently **unsigned**. This means:
- Windows SmartScreen will show a warning on first run
- Users must click "More info" → "Run anyway"

To fix this (future):
1. Obtain an EV Code Signing Certificate (~$300-500/year)
2. Sign in `build.py` after PyInstaller:
   ```python
   subprocess.run(["signtool", "sign", "/fd", "SHA256", "/a",
                   "/f", "certificate.pfx", "/p", os.environ["CERT_PASS"],
                   str(DIST_DIR / "icm-engine" / "icm-engine.exe")])
   ```
3. Sign the installer too if using one

## Rollback

If a release is broken:
1. Delete the GitHub Release
2. The next `latest.json` at the release URL will be the previous version's
3. Users on the broken version will be offered a "downgrade" (newer semver)
   — bump the patch version for a hotfix

## Key rotation

If the signing key is compromised:
1. Generate a new key pair
2. Embed the new public key in a patch release
3. Sign all subsequent releases with the new key
4. Revoke trust in the old key by removing it from the codebase
