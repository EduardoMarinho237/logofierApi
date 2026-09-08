from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt

from app.config import settings

ACCESS_TOKEN_TYPE = "access"


def ensure_utc(dt: datetime) -> datetime:
    """Normalize DB datetimes (SQLite stores naive UTC) to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str, token_version: int) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    return jwt.encode(
        {
            "sub": user_id,
            "ver": token_version,
            "jti": uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": expire,
            "type": ACCESS_TOKEN_TYPE,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict | None:
    """Return the token payload, or None if invalid/expired/not an access token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return payload


def generate_refresh_token() -> tuple[str, str, str]:
    """Return (raw_token, token_hash, token_id)."""
    token_id = uuid4().hex
    raw = token_urlsafe(48)
    token_hash = sha256(raw.encode()).hexdigest()
    return raw, token_hash, token_id


def hash_refresh_token(raw: str) -> str:
    return sha256(raw.encode()).hexdigest()
