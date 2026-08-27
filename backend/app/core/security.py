from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── 密码 ────────────────────────────────────────────

def hash_password(password: str) -> str:
    # bcrypt 限制密码最大 72 字节，超过则截断
    if len(password.encode("utf-8")) > 72:
        password = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT ─────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.auth_token_expire_minutes)
    payload = {
        "sub": user_id,
        "email": email,
        "typ": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=settings.auth_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.auth_secret, algorithms=[settings.auth_algorithm])
    except JWTError:
        return None
    # 拒绝把 SSE ticket 当 access 用
    typ = payload.get("typ")
    if typ is not None and typ != "access":
        return None
    return payload


def create_sse_ticket(user_id: str, task_id: str, expires_seconds: int | None = None) -> str:
    """短命 SSE 票：只用于 EventSource ?ticket=，勿把 access JWT 塞进 URL。"""
    ttl = expires_seconds if expires_seconds is not None else settings.sse_ticket_expire_seconds
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tid": task_id,
        "typ": "sse",
        "exp": now + timedelta(seconds=max(30, ttl)),
        "iat": now,
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=settings.auth_algorithm)


def decode_sse_ticket(ticket: str) -> dict | None:
    try:
        payload = jwt.decode(ticket, settings.auth_secret, algorithms=[settings.auth_algorithm])
    except JWTError:
        return None
    if payload.get("typ") != "sse" or not payload.get("sub") or not payload.get("tid"):
        return None
    return payload
