"""Password hashing and random tokens."""

import hashlib
import secrets

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()
# Verified when a username doesn't exist, so the response time doesn't reveal it
_DUMMY_HASH = _hasher.hash("not-a-real-password")

TOKEN_PREFIX = "ft_"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
