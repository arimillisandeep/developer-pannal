"""Auth dependencies + role based access control."""
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token
from app.models import User

bearer = HTTPBearer(auto_error=False)

ADMIN = "admin"
ONROLL = "developer_onroll"
OFFROLL = "developer_offroll"
INTERN = "intern"
DEVELOPERS = (ONROLL, OFFROLL)
WORKERS = (ONROLL, OFFROLL, INTERN)
ALL_ROLES = (ADMIN, ONROLL, OFFROLL, INTERN)


def _unauthorized(detail="Not authenticated"):
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    token = creds.credentials if creds else None
    if not token and request is not None:
        token = request.cookies.get("access_token")
    if not token:
        raise _unauthorized()

    try:
        payload = decode_token(token, expected_type="access")
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Access token expired")
    except jwt.PyJWTError:
        raise _unauthorized("Invalid token")

    user = db.get(User, payload.get("sub"))
    if not user:
        raise _unauthorized("User no longer exists")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    return user


def require_roles(*roles: str):
    """Dependency factory enforcing that the caller has one of `roles`."""

    def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user

    return _guard


require_admin = require_roles(ADMIN)
require_developer = require_roles(*DEVELOPERS)
require_worker = require_roles(*WORKERS)
require_staff = require_roles(ADMIN, *WORKERS)


def request_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    return ip, (request.headers.get("user-agent") or "")[:400]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
