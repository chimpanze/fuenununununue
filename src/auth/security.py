from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import JWT_SECRET, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, RATE_LIMIT_PER_MINUTE
from src.core.database import get_async_session, get_optional_async_session, is_db_enabled
from src.models.database import User as ORMUser

# Password hashing context
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# In-memory token blacklist
_TOKEN_BLACKLIST: set[str] = set()

# Simple in-memory rate limiter: user_id -> (window_start_epoch_sec, count)
_RATE_LIMIT_STATE: Dict[int, tuple[int, int]] = {}

# In-memory user store for DB-disabled environments
class _UserLite:
    def __init__(self, id: int, username: str, email: Optional[str], password_hash: Optional[str]) -> None:
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_active = True
        self.created_at = datetime.now(timezone.utc)
        self.last_login: Optional[datetime] = None

_MEM_USERS: Dict[int, _UserLite] = {}
_MEM_USERS_BY_NAME: Dict[str, _UserLite] = {}
_MEM_NEXT_ID: int = 1


def reset_in_memory_auth_state() -> None:
    """Reset in-memory auth-related state (users, blacklist, rate limits).

    Used for tests and dev runs where the database is disabled, to ensure
    isolation between app startups/TestClient contexts.
    """
    global _MEM_NEXT_ID
    _MEM_USERS.clear()
    _MEM_USERS_BY_NAME.clear()
    _MEM_NEXT_ID = 1
    _TOKEN_BLACKLIST.clear()
    _RATE_LIMIT_STATE.clear()


def mem_create_user(username: str, email: Optional[str], password_hash: Optional[str]) -> _UserLite:
    global _MEM_NEXT_ID
    user = _UserLite(_MEM_NEXT_ID, username, email, password_hash)
    _MEM_USERS[user.id] = user
    _MEM_USERS_BY_NAME[user.username] = user
    _MEM_NEXT_ID += 1
    return user


def mem_get_user_by_username(username: str) -> Optional[_UserLite]:
    return _MEM_USERS_BY_NAME.get(username)


def mem_get_user_by_id(user_id: int) -> Optional[_UserLite]:
    return _MEM_USERS.get(user_id)


def hash_password(password: str) -> str:
    # Prefer passlib hashing; fall back to a simple tagged scheme when unavailable
    try:
        return _pwd_context.hash(password)
    except Exception:
        return f"plain:{password}"


def verify_password(password: str, password_hash: str) -> bool:
    # First try passlib verification; if that path is unavailable, support the fallback scheme
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        if isinstance(password_hash, str) and password_hash.startswith("plain:"):
            return password_hash == f"plain:{password}"
        return False


def create_access_token(subject: str, additional_claims: Optional[Dict[str, Any]] = None, expires_minutes: Optional[int] = None) -> str:
    expire_delta = expires_minutes if expires_minutes is not None else ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_delta)
    to_encode: Dict[str, Any] = {"sub": subject, "exp": expire, "jti": str(uuid.uuid4())}
    if additional_claims:
        to_encode.update(additional_claims)
    token = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def decode_token(token: str) -> Dict[str, Any]:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    return payload


async def get_current_user(token: str = Depends(oauth2_scheme), session: Optional[AsyncSession] = Depends(get_optional_async_session)) -> ORMUser | _UserLite:
    if not token or token in _TOKEN_BLACKLIST:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        user_id = int(sub)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    if not is_db_enabled():
        user = mem_get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
        return user

    result = await session.execute(select(ORMUser).where(ORMUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return user


async def ensure_user_matches_path(user_id: int, user=Depends(get_current_user)):
    if user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: user_id mismatch")
    return user


def blacklist_token(token: str) -> None:
    _TOKEN_BLACKLIST.add(token)


def rate_limit_check(user_id: int) -> None:
    now = int(time.time())
    window_start = now - (now % 60)
    state = _RATE_LIMIT_STATE.get(user_id)
    if state is None or state[0] != window_start:
        _RATE_LIMIT_STATE[user_id] = (window_start, 1)
        return
    count = state[1] + 1
    if count > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _RATE_LIMIT_STATE[user_id] = (window_start, count)


async def rate_limiter_dependency(user=Depends(get_current_user)) -> None:
    rate_limit_check(user.id)
