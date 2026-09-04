"""Auto-update: check for new versions, download, verify, install, relaunch.

SECURITY: This module handles code that runs payroll math. Every update is
cryptographically verified before installation. Never install an unverified
artifact.

Architecture (self-rolled manifest, no external updater framework):
  1. Fetch manifest JSON from the configured feed URL
  2. Compare semver against current version
  3. If newer: download the zip artifact
  4. Verify SHA-256 integrity + Ed25519 signature
  5. Extract to a temp staging directory
  6. Hand off to a small helper that atomically swaps the install folder
     and relaunches (the running exe is locked, so a separate process is needed)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger("icm.updater")

# ------------------------------------------------------------------
# Public key (embedded at build time — NEVER commit the private key)
# ------------------------------------------------------------------

# This is the PUBLIC key. The matching PRIVATE key is held offline by
# the maintainer and used only to sign releases via tools/sign_release.py.
# If this key needs to rotate, bump the version and embed the new key.
_UPDATE_PUBLIC_KEY_HEX = (
    "d29ae3f0a207d7e6156296d91f0f50b8"
    "5938004a347734c5a10cfcbd4dae7235"
)
_PUBLIC_KEY_PLACEHOLDER = False


# ------------------------------------------------------------------
# Semver
# ------------------------------------------------------------------


def _parse_semver(v: str) -> tuple[int, int, int]:
    """Parse '1.2.3' → (1, 2, 3). Non-semver strings → (0, 0, 0)."""
    try:
        parts = v.strip().split(".")
        if len(parts) >= 3:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]), 0)
        return (int(parts[0]), 0, 0) if parts[0].isdigit() else (0, 0, 0)
    except (ValueError, IndexError):
        return (0, 0, 0)


def is_newer(candidate: str, current: str) -> bool:
    """Return True if candidate version is strictly newer than current."""
    return _parse_semver(candidate) > _parse_semver(current)


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


@dataclass
class UpdateInfo:
    version: str
    release_notes: str = ""
    download_url: str = ""
    sha256: str = ""
    signature: str = ""  # hex-encoded Ed25519 signature


# ------------------------------------------------------------------
# Manifest fetch
# ------------------------------------------------------------------


def _fetch_manifest(feed_url: str) -> dict[str, Any] | None:
    """Fetch the update manifest JSON from the feed URL. Returns None on failure."""
    import urllib.request as _ur
    try:
        with _ur.urlopen(feed_url, timeout=15) as resp:
            if resp.status != 200:
                logger.warning("Update feed returned HTTP %s", resp.status)
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.debug("Update feed unreachable or invalid", exc_info=True)
        return None

    if not isinstance(data, dict):
        return None
    return data


def check_for_updates(
    current_version: str,
    feed_url: str,
) -> UpdateInfo | None:
    """Check the update feed for a version newer than current_version.

    Returns UpdateInfo if an update is available, None otherwise.
    Never raises — all failures are logged and return None.
    """
    try:
        manifest = _fetch_manifest(feed_url)
    except Exception:
        logger.debug("Update check failed", exc_info=True)
        return None
    if not manifest:
        return None
    if not isinstance(manifest, dict):
        return None

    latest = manifest.get("version", "")
    if not latest or not isinstance(latest, str):
        return None
    if not is_newer(latest, current_version):
        return None

    download_url = manifest.get("download_url", "")
    if not download_url or not isinstance(download_url, str):
        logger.warning("Update manifest missing download_url")
        return None

    # Optional fields
    release_notes = manifest.get("release_notes", "")
    sha256 = manifest.get("sha256", "")
    signature = manifest.get("signature", "")

    return UpdateInfo(
        version=str(latest),
        release_notes=str(release_notes),
        download_url=str(download_url),
        sha256=str(sha256),
        signature=str(signature),
    )


# ------------------------------------------------------------------
# Download
# ------------------------------------------------------------------


def _download(url: str) -> bytes | None:
    """Download from URL. Returns content bytes or None on failure."""
    import urllib.request as _ur
    try:
        with _ur.urlopen(url, timeout=120) as resp:
            if resp.status != 200:
                logger.warning("Download returned HTTP %s", resp.status)
                return None
            # Check content length to avoid OOM
            length = resp.headers.get("Content-Length")
            if length and int(length) > 200 * 1024 * 1024:  # 200 MB cap
                logger.warning("Download too large: %s bytes", length)
                return None
            return cast(bytes, resp.read())
    except Exception:
        logger.warning("Download failed", exc_info=True)
        return None


# ------------------------------------------------------------------
# Verification
# ------------------------------------------------------------------


def _verify_sha256(data: bytes, expected_hex: str) -> bool:
    """Verify SHA-256 digest. Returns True on match, False on mismatch/missing."""
    if not expected_hex:
        return True  # no hash to verify against
    actual = hashlib.sha256(data).hexdigest()
    return actual == expected_hex.lower()


def _verify_ed25519(data: bytes, signature_hex: str, public_key_hex: str) -> bool:
    """Verify an Ed25519 signature. Returns True if valid."""
    if not signature_hex or not public_key_hex:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk_bytes = bytes.fromhex(public_key_hex)
        sig_bytes = bytes.fromhex(signature_hex)
        pk = Ed25519PublicKey.from_public_bytes(pk_bytes)
        pk.verify(sig_bytes, data)
        return True
    except InvalidSignature:
        logger.warning("Ed25519 signature verification FAILED")
        return False
    except Exception:
        logger.warning("Ed25519 verification error", exc_info=True)
        return False


def verify_artifact(data: bytes, sha256: str, signature: str) -> bool:
    """Verify artifact integrity (SHA-256) and authenticity (Ed25519).

    Both checks must pass for the artifact to be trusted.
    """
    if not _verify_sha256(data, sha256):
        logger.warning("SHA-256 verification FAILED")
        return False
    if _PUBLIC_KEY_PLACEHOLDER:
        logger.debug("Ed25519 verification skipped: public key is a placeholder")
        return True
    if not _verify_ed25519(data, signature, _UPDATE_PUBLIC_KEY_HEX):
        logger.warning("Signature verification FAILED")
        return False
    return True


# ------------------------------------------------------------------
# Install: folder swap + relaunch
# ------------------------------------------------------------------


def _get_install_dir() -> Path:
    """Return the directory containing the running executable."""
    if getattr(sys, "frozen", False):
        # PyInstaller: exe is in dist/icm-engine/, install dir is one level up
        exe_dir = Path(sys.executable).parent
        # Check if we're in the dist folder
        if (exe_dir / "_internal").exists():
            return exe_dir
        return exe_dir
    # Development: running from source
    return Path(__file__).parent.parent


def _swap_folders(
    staging_dir: Path,
    install_dir: Path,
    backup_dir: Path,
) -> None:
    """Replace install_dir with staging_dir contents, keeping a backup.

    Strategy for Windows (where the exe is locked):
    1. Copy staging contents into a new folder adjacent to install_dir
    2. Rename old install_dir → backup_dir
    3. Rename new folder → install_dir
    4. Relaunch from new install_dir
    """
    new_dir = install_dir.parent / f"{install_dir.name}_new"

    # Remove stale leftovers
    if new_dir.exists():
        shutil.rmtree(new_dir, ignore_errors=True)

    # Copy staging to new dir
    shutil.copytree(staging_dir, new_dir)

    # Rename old → backup
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    if install_dir.exists():
        os.rename(install_dir, backup_dir)

    # Rename new → install
    os.rename(new_dir, install_dir)


def install_update(
    zip_data: bytes,
    sha256: str,
    signature: str,
) -> tuple[bool, str]:
    """Verify and install an update from zip bytes.

    Returns (success, message).
    On success, the app should call relaunch().
    """
    # 1. Verify
    if not verify_artifact(zip_data, sha256, signature):
        return False, "Update verification failed. The artifact may be tampered with."

    # 2. Extract to staging
    staging = Path(tempfile.mkdtemp(prefix="icm_update_"))
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Security: prevent zip-slip (path traversal)
            for member in zf.infolist():
                target = (staging / member.filename).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    return False, "Rejected archive with path traversal"
            zf.extractall(staging)
    except zipfile.BadZipFile as e:
        return False, f"Corrupt update archive: {e}"

    # 3. Verify staging contains expected structure
    install_dir = _get_install_dir()
    exe_name = "icm-engine.exe" if sys.platform == "win32" else "icm-engine"
    new_exe = list(staging.rglob(exe_name))
    if not new_exe:
        return False, f"Update archive missing {exe_name}"

    # 4. Swap folders
    backup_dir = install_dir.parent / f"{install_dir.name}_backup"
    try:
        _swap_folders(staging, install_dir, backup_dir)
    except Exception as e:
        # Try to restore from backup
        if backup_dir.exists():
            try:
                if install_dir.exists():
                    shutil.rmtree(install_dir, ignore_errors=True)
                os.rename(backup_dir, install_dir)
            except Exception:
                pass
        return False, f"Install failed: {e}"

    # 5. Cleanup staging
    shutil.rmtree(staging, ignore_errors=True)

    return True, f"Update installed to {install_dir}"


def relaunch() -> None:
    """Launch the updated executable and exit the current process."""
    install_dir = _get_install_dir()
    if sys.platform == "win32":
        exe = install_dir / "icm-engine.exe"
    else:
        exe = install_dir / "icm-engine"
    if not exe.exists():
        logger.error("Cannot relaunch: %s not found", exe)
        return
    # Launch detached so the current process can exit. The platform check has
    # to be a statement, not a ternary in the argument: DETACHED_PROCESS only
    # exists on Windows, and mypy narrows sys.platform at statement level only.
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS
    else:
        creationflags = 0
    subprocess.Popen(
        [str(exe)],
        creationflags=creationflags,
        close_fds=True,
    )
    sys.exit(0)


def _self_update(zip_data: bytes, sha256: str, signature: str) -> None:
    """Called from the updater helper process. Runs folder swap + relaunch."""
    ok, msg = install_update(zip_data, sha256, signature)
    if ok:
        relaunch()
    else:
        logger.error("Self-update failed: %s", msg)
        sys.exit(1)


# ------------------------------------------------------------------
# Startup check (non-blocking)
# ------------------------------------------------------------------


def start_update_check(
    current_version: str,
    feed_url: str,
    on_update_available: Any = None,
) -> None:
    """Run an update check in a background thread. Never blocks.

    Args:
        current_version: Current app version string.
        feed_url: URL to the update manifest JSON.
        on_update_available: Optional callback(UpdateInfo) called when an
                             update is found. Called from the background
                             thread — caller is responsible for thread safety.
    """
    def _check() -> None:
        info = check_for_updates(current_version, feed_url)
        if info and on_update_available is not None:
            try:
                on_update_available(info)
            except Exception:
                logger.warning("on_update_available callback failed", exc_info=True)

    t = threading.Thread(target=_check, daemon=True, name="icm-update-check")
    t.start()
