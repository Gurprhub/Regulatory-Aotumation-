"""Password hashing, session identifiers and API token generation.

Passwords are hashed with scrypt from the standard library, which keeps the
dependency surface small and avoids the passlib/bcrypt version churn that
regularly breaks builds. Parameters follow the OWASP recommendation for scrypt
(N=2^15, r=8, p=1) and are stored alongside each hash, so they can be raised
later without invalidating existing passwords.

Session identifiers and API tokens are random secrets. Only their SHA-256
digests are stored: a leaked database gives an attacker nothing they can
present back to the application. They are not passwords, so a fast digest is
the right tool — there is no low-entropy secret to brute force.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

# OWASP-recommended scrypt parameters (2024): N = 2^15, r = 8, p = 1.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

#: Bytes of entropy in a session id or API token secret.
_SECRET_BYTES = 32

#: Prefix identifying an API token, and how much of it is stored in the clear
#: so a token can be looked up without scanning every row.
TOKEN_PREFIX = "rat"
TOKEN_LOOKUP_CHARS = 12


def hash_password(password: str) -> str:
    """Return ``scrypt$N$r$p$salt$key``, all binary parts hex-encoded."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_BYTES,
        maxmem=_SCRYPT_N * _SCRYPT_R * 128 * 2,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Check ``password`` against a stored hash, in constant time.

    Returns ``False`` rather than raising for a missing or malformed hash, so a
    user row with no usable password simply cannot log in.
    """
    if not encoded or not password:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, key_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=n * r * 128 * 2,
    )
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str | None) -> bool:
    """True when a stored hash uses weaker parameters than the current ones."""
    if not encoded:
        return True
    try:
        scheme, n_raw, r_raw, p_raw, _salt, _key = encoded.split("$")
    except ValueError:
        return True
    if scheme != "scrypt":
        return True
    try:
        return (int(n_raw), int(r_raw), int(p_raw)) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    except ValueError:
        return True


def new_session_id() -> str:
    """A fresh, unguessable session identifier."""
    return secrets.token_urlsafe(_SECRET_BYTES)


def hash_secret(secret: str) -> str:
    """SHA-256 of a high-entropy secret (session id or API token)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GeneratedToken:
    """A newly minted API token: the plaintext is returned to the caller once."""

    plaintext: str
    lookup: str
    digest: str


def new_api_token() -> GeneratedToken:
    """Mint an API token as ``rat_<secret>``.

    The first :data:`TOKEN_LOOKUP_CHARS` characters of the secret are stored in
    the clear as a lookup key, so verification is an indexed read followed by
    one constant-time digest comparison.
    """
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    plaintext = f"{TOKEN_PREFIX}_{secret}"
    return GeneratedToken(
        plaintext=plaintext,
        lookup=secret[:TOKEN_LOOKUP_CHARS],
        digest=hash_secret(plaintext),
    )


def split_api_token(presented: str) -> tuple[str, str] | None:
    """Return ``(lookup, digest)`` for a presented token, or ``None`` if malformed."""
    if not presented or not presented.startswith(f"{TOKEN_PREFIX}_"):
        return None
    secret = presented[len(TOKEN_PREFIX) + 1 :]
    if len(secret) < TOKEN_LOOKUP_CHARS:
        return None
    return secret[:TOKEN_LOOKUP_CHARS], hash_secret(presented)
