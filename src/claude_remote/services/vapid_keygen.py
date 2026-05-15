"""VAPID keypair generation (NIST P-256 / prime256v1).

Uses py-vapid >= 1.9 which wraps cryptography. If the py_vapid API drifts,
the fallback is direct cryptography.hazmat.primitives.asymmetric.ec keygen.

Spec: REQ-3.5, REQ-3.6, SC-3.3
"""

from __future__ import annotations

import base64
from typing import cast

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid  # type: ignore[import-untyped]


def generate_keypair() -> tuple[str, str]:
    """Generate a fresh NIST P-256 VAPID keypair.

    Returns:
        (public_b64url_no_padding, private_pem)
        - public_b64url_no_padding: URL-safe base64 (no padding), decodes to
          65 bytes in uncompressed X9.62 format (0x04 || X || Y).
        - private_pem: PEM-encoded EC private key string (ascii).

    The public key format matches what browsers expect for applicationServerKey
    after passing through urlBase64ToUint8Array.
    """
    v = Vapid()
    v.generate_keys()
    public_raw = cast(
        bytes,
        v.public_key.public_bytes(  # type: ignore[union-attr]
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        ),
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")
    private_pem = cast(str, v.private_pem().decode("ascii"))  # type: ignore[attr-defined]
    return public_b64, private_pem
