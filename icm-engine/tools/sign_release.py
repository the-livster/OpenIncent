"""Sign a release artifact with Ed25519.

Usage:
    python tools/sign_release.py <path-to-zip>

Creates <path-to-zip>.sig containing the hex-encoded Ed25519 signature.
Also prints the SHA-256 hash for the manifest.

Requires: cryptography (pip install cryptography)
The private key is read from the ICM_SIGNING_KEY environment variable
(a 64-char hex string) or from ~/.icm-signing-key.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def _load_private_key() -> bytes:
    """Load the Ed25519 private key (32 bytes) from env or file."""
    env_key = os.environ.get("ICM_SIGNING_KEY", "").strip()
    if env_key:
        return bytes.fromhex(env_key)

    key_file = Path.home() / ".icm-signing-key"
    if key_file.exists():
        return bytes.fromhex(key_file.read_text().strip())

    print("ERROR: No signing key found.", file=sys.stderr)
    print("Set ICM_SIGNING_KEY env var or create ~/.icm-signing-key", file=sys.stderr)
    print("The key must be a 64-character hex string (32 bytes).", file=sys.stderr)
    print("Generate one with: python -c \"import os; print(os.urandom(32).hex())\"", file=sys.stderr)
    sys.exit(1)


def sign_file(path: Path) -> tuple[str, str]:
    """Sign a file. Returns (sha256_hex, signature_hex)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_bytes = _load_private_key()
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

    data = path.read_bytes()

    sha256 = hashlib.sha256(data).hexdigest()
    signature = private_key.sign(data).hex()

    return sha256, signature


def derive_public_key() -> str:
    """Derive and print the public key from the private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_bytes = _load_private_key()
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    return private_key.public_key().public_bytes_raw().hex()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/sign_release.py <path-to-zip>", file=sys.stderr)
        print("       python tools/sign_release.py --public-key  (show public key)", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--public-key":
        print(derive_public_key())
        return

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)

    sha256, signature = sign_file(path)

    # Write signature file
    sig_path = path.with_suffix(path.suffix + ".sig")
    sig_path.write_text(signature)

    print(f"SHA-256:  {sha256}")
    print(f"Signature: {signature}")
    print(f"Saved to:  {sig_path}")


if __name__ == "__main__":
    main()
