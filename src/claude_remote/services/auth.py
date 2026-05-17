"""Password hashing + session secret (auth/#7).

stdlib only — `hashlib.scrypt` (no bcrypt/argon2 dependency). The stored
hash is self-describing (`scrypt$N$r$p$salt_hex$dk_hex`) so parameters can
change later without a data migration. verify_password never raises on a
malformed stored value — it returns False.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# ~16 MB working set (128*N*r). Fine for an interactive single-user login.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DKLEN,
        maxmem=_MAXMEM,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = _scrypt(password, salt, _N, _R, _P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
    except (ValueError, AttributeError):
        return False
    try:
        actual = _scrypt(password, salt, n, r, p)
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


def generate_session_secret() -> str:
    return secrets.token_urlsafe(32)
