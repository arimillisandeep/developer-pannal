
"""Authentication: login, refresh rotation, logout, forgot/reset password."""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    new_reset_token,
    password_strength_error,
    verify_password,
    decode_token,
)
from app.models import PasswordResetToken, User, UserSession
from app.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    RefreshIn,
    ResetPasswordIn,
    TokenPair,
    UserOut,
)
from app.services.email import send_email
from app.services import audit
from app.ws.manager import manager

router = APIRouter(prefix="/auth", tags=["Authentication"])

REDIRECTS = {
    "admin": "/admin",
    "developer_onroll": "/onroll",
    "developer_offroll": "/offroll",
    "intern": "/intern",
}


def _issue_tokens(db: Session, user: User, request: Request) -> dict:
    now = datetime.now(timezone.utc)

    jti = hash_token(f"{user.id}{now.isoformat()}")

    session = UserSession(
        user_id=user.id,
        refresh_jti=jti,
        ip=request.client.host if request.client else "",
        user_agent=(request.headers.get("user-agent") or "")[:400],
        expires_at=now + timedelta(
            days=settings.REFRESH_TOKEN_TTL_DAYS
        ),
    )

    db.add(session)
    user.last_login_at = now
    db.flush()

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id, jti)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_TTL_MIN * 60,
        "user": UserOut.model_validate(user),
    }


# ---------------------------------------------------------
# LOGIN
# ---------------------------------------------------------

@router.post("/login", response_model=TokenPair)
def login(
    payload: LoginIn,
    request: Request,
    db: Session = Depends(get_db),
):
    identifier = payload.identifier.strip().lower()

    user = (
        db.query(User)
        .filter(
            or_(
                User.email == identifier,
                User.username == identifier,
            )
        )
        .one_or_none()
    )

    if user is None:
        audit.log(
            db, None, "auth.login_failed",
            "user", identifier, request
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not verify_password(payload.password, user.password_hash):
        audit.log(
            db, None, "auth.login_failed",
            "user", identifier, request
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact an administrator.",
        )

    data = _issue_tokens(db, user, request)

    audit.log(
        db,
        user,
        "auth.login",
        "user",
        user.id,
        request,
        role=user.role,
    )

    data["redirect_to"] = REDIRECTS.get(user.role, "/")

    return data


# ---------------------------------------------------------
# REFRESH TOKEN
# ---------------------------------------------------------

@router.post("/refresh", response_model=TokenPair)
def refresh(
    payload: RefreshIn,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        claims = decode_token(
            payload.refresh_token,
            expected_type="refresh",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Refresh token expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid refresh token",
        )

    session = (
        db.query(UserSession)
        .filter(UserSession.refresh_jti == claims.get("jti"))
        .one_or_none()
    )

    if not session or session.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail="Session revoked",
        )

    expires = session.expires_at

    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=401,
            detail="Session expired",
        )

    user = db.get(User, session.user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="User unavailable",
        )

    session.revoked_at = datetime.now(timezone.utc)

    data = _issue_tokens(db, user, request)

    audit.log(
        db, user, "auth.refresh",
        "user", user.id, request
    )

    data["redirect_to"] = REDIRECTS.get(user.role, "/")

    return data


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

@router.post("/logout")
def logout(
    payload: RefreshIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        claims = decode_token(
            payload.refresh_token,
            expected_type="refresh",
        )

        session = (
            db.query(UserSession)
            .filter(
                UserSession.refresh_jti == claims["jti"]
            )
            .one_or_none()
        )

        if session:
            session.revoked_at = datetime.now(timezone.utc)

    except jwt.PyJWTError:
        pass

    audit.log(
        db, user, "auth.logout",
        "user", user.id, request
    )

    return {"detail": "Logged out"}


# ---------------------------------------------------------
# CURRENT USER
# ---------------------------------------------------------

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---------------------------------------------------------
# FORGOT PASSWORD
# ---------------------------------------------------------

@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordIn,
    request: Request,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()

    user = (
        db.query(User)
        .filter(User.email == email)
        .one_or_none()
    )
    if user and user.is_active:
        raw_token, hashed_token = new_reset_token()

        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashed_token,
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=30),
            )
        )

        db.flush()

        audit.log(
            db,
            None,
            "auth.password_reset_requested",
            "user",
            user.id,
            request,
        )

        email_body = f"""
Hello {user.full_name},

We received a request to reset your WORKCODE password.

Your password reset token is:

{raw_token}

Enter this token on the Reset Password page.

This token expires in 30 minutes.

If you did not request this, ignore this email.

WORKCODE Team
"""

        try:
            send_email(
                to_email=user.email,
                subject="WORKCODE - Password Reset",
                body=email_body,
            )
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to send password reset email: %s",
                error
           )

    return {
        "detail": "If that email exists, a reset email has been sent."
    }


# ---------------------------------------------------------
# RESET PASSWORD
# ---------------------------------------------------------

@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordIn,
    request: Request,
    db: Session = Depends(get_db),
):
    error = password_strength_error(payload.new_password)

    if error:
        raise HTTPException(
            status_code=422,
            detail=error,
        )

    record = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash
            == hash_token(payload.token)
        )
        .one_or_none()
    )

    if not record or record.used_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Invalid or already used reset token",
        )

    expires = record.expires_at

    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="Reset token expired",
        )

    user = db.get(User, record.user_id)

    if not user:
        raise HTTPException(
            status_code=400,
            detail="User no longer exists",
        )

    user.password_hash = hash_password(payload.new_password)
    record.used_at = datetime.now(timezone.utc)

    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
    ).update(
        {"revoked_at": datetime.now(timezone.utc)}
    )

    audit.log(
        db,
        user,
        "auth.password_reset",
        "user",
        user.id,
        request,
    )

    return {"detail": "Password updated. Please sign in."}


# ---------------------------------------------------------
# CHANGE PASSWORD
# ---------------------------------------------------------

@router.post("/change-password")
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(
        payload.current_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect",
        )

    error = password_strength_error(payload.new_password)

    if error:
        raise HTTPException(
            status_code=422,
            detail=error,
        )

    user.password_hash = hash_password(payload.new_password)

    audit.log(
        db,
        user,
        "auth.password_changed",
        "user",
        user.id,
        request,
    )

    return {"detail": "Password changed"}


# ---------------------------------------------------------
# USER SESSIONS
# ---------------------------------------------------------

@router.get("/sessions")
def my_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sessions = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        .order_by(UserSession.created_at.desc())
        .limit(25)
        .all()
    )

    return [
        {
            "id": session.id,
            "ip": session.ip,
            "user_agent": session.user_agent,
            "created_at": session.created_at,
            "last_seen_at": session.last_seen_at,
            "expires_at": session.expires_at,
        }
        for session in sessions
    ]


# ---------------------------------------------------------
# ONLINE USERS
# ---------------------------------------------------------

@router.get("/online")
def online_users(user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admins only",
        )

    online = manager.online_users()

    return {
        "online": online,
        "count": len(online),
    }
