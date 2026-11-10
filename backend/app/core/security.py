"""Password hashing (PBKDF2-HMAC-SHA256, stdlib only) and JWT helpers."""
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

_ITERATIONS = 600_000


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def password_strength_error(password: str) -> str | None:
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters."
    if password.isalpha() or password.isdigit():
        return "Password must contain letters and numbers."
    return None


# ----------------------------------------------------------------------- jwt
def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(sub: str, token_type: str, ttl: timedelta, extra: dict | None = None) -> str:
    payload = {
        "sub": sub,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(_now().timestamp()),
        "exp": int((_now() + ttl).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    return create_token(user_id, "access", timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN), {"role": role})


def create_refresh_token(user_id: str, jti: str) -> str:
    return create_token(user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS), {"jti": jti})


def decode_token(token: str, expected_type: str | None = None) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_reset_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
