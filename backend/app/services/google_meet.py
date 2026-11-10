"""Google Calendar / Meet integration.

Without GOOGLE_CLIENT_ID configured the endpoint returns a 503 with a clear
message — WORKCODE never shows a fake Meet link.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import GoogleAccount

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class GoogleNotConfigured(Exception):
    pass


class GoogleAPIError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def auth_url(state: str) -> str:
    if not is_configured():
        raise GoogleNotConfigured("Google OAuth is not configured (GOOGLE_CLIENT_ID/SECRET missing).")
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": settings.GOOGLE_SCOPES + " https://www.googleapis.com/auth/userinfo.email",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(db: Session, user_id: str, code: str, redirect_uri: str | None = None) -> GoogleAccount:
    if not is_configured():
        raise GoogleNotConfigured("Google OAuth is not configured.")
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri or settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = httpx.post(TOKEN_URL, data=data, timeout=20)
    if resp.status_code != 200:
        raise GoogleAPIError(f"Token exchange failed: {resp.text[:300]}")
    payload = resp.json()

    email = ""
    try:
        ui = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo",
                       headers={"Authorization": f"Bearer {payload['access_token']}"}, timeout=15)
        if ui.status_code == 200:
            email = ui.json().get("email", "")
    except httpx.HTTPError:
        pass

    acc = db.query(GoogleAccount).filter(GoogleAccount.user_id == user_id).one_or_none()
    if not acc:
        acc = GoogleAccount(user_id=user_id)
        db.add(acc)
    acc.google_email = email
    acc.access_token = payload.get("access_token")
    if payload.get("refresh_token"):
        acc.refresh_token = payload["refresh_token"]
    acc.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    acc.scopes = settings.GOOGLE_SCOPES
    db.flush()
    return acc


def _fresh_token(db: Session, acc: GoogleAccount) -> str:
    exp = acc.token_expires_at
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp > datetime.now(timezone.utc) + timedelta(seconds=60) and acc.access_token:
        return acc.access_token
    if not acc.refresh_token:
        raise GoogleAPIError("Google session expired — reconnect your Google account.")
    resp = httpx.post(TOKEN_URL, data={
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": acc.refresh_token,
        "grant_type": "refresh_token",
    }, timeout=20)
    if resp.status_code != 200:
        raise GoogleAPIError(f"Token refresh failed: {resp.text[:300]}")
    payload = resp.json()
    acc.access_token = payload["access_token"]
    acc.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    db.flush()
    return acc.access_token


def create_meet_event(db: Session, owner_id: str, title: str, agenda: str, starts_at: datetime,
                      ends_at: datetime, attendees: list[str]) -> dict:
    """Create a Calendar event with a real Google Meet conference."""
    if not is_configured():
        raise GoogleNotConfigured("Google Calendar is not configured on this server.")

    acc = db.query(GoogleAccount).filter(GoogleAccount.user_id == owner_id).one_or_none()
    if not acc:
        raise GoogleAPIError("Your Google account is not linked yet. Connect Google first.")

    token = _fresh_token(db, acc)
    body = {
        "summary": title,
        "description": agenda,
        "start": {"dateTime": starts_at.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": ends_at.isoformat(), "timeZone": "UTC"},
        "attendees": [{"email": e} for e in attendees if e],
        "conferenceData": {
            "createRequest": {
                "requestId": f"workcode-{int(starts_at.timestamp())}-{owner_id[:8]}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    resp = httpx.post(
        f"{CALENDAR_API}/calendars/primary/events",
        params={"conferenceDataVersion": 1, "sendUpdates": "all"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise GoogleAPIError(f"Calendar API error: {resp.text[:300]}")
    data = resp.json()
    meet_url = (data.get("hangoutLink") or
                next((ep.get("uri") for ep in data.get("conferenceData", {}).get("entryPoints", [])
                      if ep.get("entryPointType") == "video"), None))
    if not meet_url:
        raise GoogleAPIError("Calendar event created but Google returned no Meet link.")
    return {"meet_url": meet_url, "html_link": data.get("htmlLink"), "event_id": data.get("id")}
