"""Meetings + Google Meet link generation."""

from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, DEVELOPERS, WORKERS, get_current_user
from app.core import security
from app.models import (
    GoogleAccount,
    Meeting,
    MeetingParticipant,
    TeamMember,
    User,
)
from app.schemas import MeetingIn, MeetingOut
from app.services import audit, google_meet
from app.services.notify import notify


router = APIRouter(prefix="/meetings", tags=["Meetings"])

FRONTEND_MEETINGS_URL = "http://localhost:5173/meetings"


# ---------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------

def _can_manage(user: User) -> bool:
    return user.role in (ADMIN, *DEVELOPERS)


# ---------------------------------------------------------------
# Meeting recipients
# ---------------------------------------------------------------

@router.get("/recipients")
def meeting_recipients(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage(user):
        raise HTTPException(
            status_code=403,
            detail="Only Admin and permanent developers can schedule meetings",
        )

    users = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            User.id != user.id,
        )
        .order_by(User.full_name)
        .all()
    )

    return [
        {
            "id": person.id,
            "full_name": person.full_name,
            "email": person.email,
            "role": person.role,
        }
        for person in users
    ]


# ---------------------------------------------------------------
# Create meeting
# ---------------------------------------------------------------

@router.post("", response_model=MeetingOut, status_code=201)
def create_meeting(
    payload: MeetingIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage(user):
        raise HTTPException(
            status_code=403,
            detail="Only Admin and permanent developers can create meetings",
        )

    if payload.ends_at <= payload.starts_at:
        raise HTTPException(
            status_code=422,
            detail="End time must be after start time",
        )

    allowed_ids = {
        person.id
        for person in db.query(User)
        .filter(
            User.is_active.is_(True),
            User.id != user.id,
        )
        .all()
    }

    invalid = set(payload.participant_ids) - allowed_ids

    if invalid:
        raise HTTPException(
            status_code=403,
            detail="One or more selected participants are invalid",
        )

    meeting = Meeting(
        title=payload.title,
        agenda=payload.agenda,
        created_by=user.id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        provider="google" if payload.create_meet_link else "manual",
    )

    db.add(meeting)
    db.flush()

    error_message = None

    if payload.create_meet_link:
        try:
            emails = [
                person.email
                for person in db.query(User)
                .filter(User.id.in_(payload.participant_ids))
                .all()
                if person.email
            ]

            info = google_meet.create_meet_event(
                db,
                user.id,
                payload.title,
                payload.agenda,
                payload.starts_at,
                payload.ends_at,
                emails,
            )

            meeting.meet_url = info["meet_url"]
            meeting.html_link = info.get("html_link")
            meeting.calendar_event_id = info.get("event_id")
            meeting.status = "scheduled"

        except google_meet.GoogleNotConfigured as exc:
            error_message = str(exc)

        except google_meet.GoogleAPIError as exc:
            error_message = str(exc)

        if error_message:
            meeting.status = "draft"
            meeting.error_message = error_message[:400]
            meeting.meet_url = None

    else:
        meeting.status = "scheduled"

    for participant_id in set(payload.participant_ids):
        participant = db.get(User, participant_id)

        if participant:
            db.add(
                MeetingParticipant(
                    meeting_id=meeting.id,
                    user_id=participant_id,
                )
            )

            notify(
                db,
                participant_id,
                "meeting_invite",
                f"Meeting invitation: {payload.title}",
                (
                    f"{user.full_name} invited you on "
                    f"{payload.starts_at:%d %b %Y, %H:%M} UTC"
                ),
                f"/meetings/{meeting.id}",
            )

    audit.log(
        db,
        user,
        "meeting.created",
        "meeting",
        meeting.id,
        request,
        participants=len(payload.participant_ids),
        meet=bool(meeting.meet_url),
    )

    db.flush()
    return meeting


# ---------------------------------------------------------------
# List meetings
# ---------------------------------------------------------------

@router.get("", response_model=list[MeetingOut])
def list_meetings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    upcoming: bool = Query(False),
):
    query = db.query(Meeting)

    if user.role != ADMIN:
        invited = (
            db.query(MeetingParticipant.meeting_id)
            .filter(MeetingParticipant.user_id == user.id)
            .all()
        )

        invited_ids = [item[0] for item in invited]

        query = query.filter(
            (Meeting.created_by == user.id)
            | (Meeting.id.in_(invited_ids))
        )

    if upcoming:
        query = query.filter(
            Meeting.starts_at >= datetime.now(timezone.utc)
        )

    return query.order_by(Meeting.starts_at.desc()).limit(100).all()


# ---------------------------------------------------------------
# Meeting details
# ---------------------------------------------------------------

@router.get("/{meeting_id}")
def get_meeting(
    meeting_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    participants = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting.id)
        .all()
    )

    is_invited = any(
        participant.user_id == user.id
        for participant in participants
    )

    if (
        user.role != ADMIN
        and meeting.created_by != user.id
        and not is_invited
    ):
        raise HTTPException(
            status_code=403,
            detail="You are not invited to this meeting",
        )

    names = {
        person.id: (
            person.full_name,
            person.email,
            person.role,
        )
        for person in db.query(User).all()
    }

    return {
        **MeetingOut.model_validate(meeting).model_dump(),
        "participants": [
            {
                "user_id": participant.user_id,
                "full_name": names.get(
                    participant.user_id, ("Unknown", "", "")
                )[0],
                "email": names.get(
                    participant.user_id, ("", "", "")
                )[1],
                "role": names.get(
                    participant.user_id, ("", "", "")
                )[2],
                "response": participant.response,
                "joined_at": participant.joined_at,
            }
            for participant in participants
        ],
        "can_manage": (
            _can_manage(user)
            and (
                user.role == ADMIN
                or meeting.created_by == user.id
            )
        ),
        "can_join": bool(meeting.meet_url) and (
            user.role == ADMIN
            or is_invited
            or meeting.created_by == user.id
        ),
    }


# ---------------------------------------------------------------
# Update meeting
# ---------------------------------------------------------------

@router.patch("/{meeting_id}", response_model=MeetingOut)
def update_meeting(
    meeting_id: str,
    payload: MeetingIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if user.role != ADMIN and meeting.created_by != user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only edit your own meetings",
        )

    if payload.ends_at <= payload.starts_at:
        raise HTTPException(
            status_code=422,
            detail="End time must be after start time",
        )

    meeting.title = payload.title
    meeting.agenda = payload.agenda
    meeting.starts_at = payload.starts_at
    meeting.ends_at = payload.ends_at

    for participant in (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting.id)
        .all()
    ):
        notify(
            db,
            participant.user_id,
            "meeting_updated",
            f"Meeting updated: {meeting.title}",
            f"New time: {meeting.starts_at:%d %b %Y, %H:%M} UTC",
            f"/meetings/{meeting.id}",
        )

    audit.log(
        db,
        user,
        "meeting.updated",
        "meeting",
        meeting.id,
        request,
    )

    db.flush()
    return meeting


# ---------------------------------------------------------------
# Cancel meeting
# ---------------------------------------------------------------

@router.post("/{meeting_id}/cancel", response_model=MeetingOut)
def cancel_meeting(
    meeting_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if user.role != ADMIN and meeting.created_by != user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only cancel your own meetings",
        )

    meeting.status = "cancelled"

    for participant in (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting.id)
        .all()
    ):
        notify(
            db,
            participant.user_id,
            "meeting_cancelled",
            f"Meeting cancelled: {meeting.title}",
            "The organiser cancelled this meeting.",
            f"/meetings/{meeting.id}",
        )

    audit.log(
        db,
        user,
        "meeting.cancelled",
        "meeting",
        meeting.id,
        request,
    )

    db.flush()
    return meeting


# ---------------------------------------------------------------
# Join meeting
# ---------------------------------------------------------------

@router.post("/{meeting_id}/join")
def join_meeting(
    meeting_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if meeting.status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Meeting was cancelled",
        )

    participant = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == user.id,
        )
        .one_or_none()
    )

    if (
        user.role != ADMIN
        and meeting.created_by != user.id
        and not participant
    ):
        raise HTTPException(
            status_code=403,
            detail="You are not invited to this meeting",
        )

    if not meeting.meet_url:
        raise HTTPException(
            status_code=409,
            detail=(
                meeting.error_message
                or "No Google Meet link for this meeting yet"
            ),
        )

    if participant:
        participant.joined_at = datetime.now(timezone.utc)
        participant.response = "accepted"

    audit.log(
        db,
        user,
        "meeting.joined",
        "meeting",
        meeting.id,
    )

    db.flush()

    return {
        "meet_url": meeting.meet_url,
        "title": meeting.title,
    }


# ---------------------------------------------------------------
# Accept or decline invitation
# ---------------------------------------------------------------

@router.post("/{meeting_id}/respond")
def respond(
    meeting_id: str,
    response: str = Query(..., pattern="^(accepted|declined)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    participant = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.user_id == user.id,
        )
        .one_or_none()
    )

    if not participant:
        raise HTTPException(
            status_code=404,
            detail="Invitation not found",
        )

    participant.response = response
    db.flush()

    return {"response": response}


# ---------------------------------------------------------------
# Google OAuth status
# ---------------------------------------------------------------

@router.get("/google/status")
def google_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = (
        db.query(GoogleAccount)
        .filter(GoogleAccount.user_id == user.id)
        .one_or_none()
    )

    return {
        "configured": google_meet.is_configured(),
        "linked": bool(account and account.refresh_token),
        "google_email": account.google_email if account else None,
    }


# ---------------------------------------------------------------
# Google OAuth URL
# ---------------------------------------------------------------

@router.get("/google/auth-url")
def google_auth_url(
    user: User = Depends(get_current_user),
):
    if not _can_manage(user):
        raise HTTPException(
            status_code=403,
            detail="You cannot connect Google with this account",
        )

    try:
        state = security.create_token(
            user.id,
            "google_oauth_state",
            timedelta(minutes=10),
        )

        return {
            "url": google_meet.auth_url(state=state)
        }

    except google_meet.GoogleNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )


# ---------------------------------------------------------------
# Google OAuth callback
# ---------------------------------------------------------------

@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(
            url=f"{FRONTEND_MEETINGS_URL}?google=error",
            status_code=303,
        )

    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail="Missing Google authorization code or state",
        )

    try:
        state_payload = security.decode_token(
            state,
            expected_type="google_oauth_state",
        )

        user_id = state_payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=400,
                detail="Invalid OAuth state",
            )

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Connect Google again.",
        )

    user = db.get(User, user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="WORKCODE user is not active",
        )

    if not _can_manage(user):
        raise HTTPException(
            status_code=403,
            detail="This account cannot connect Google",
        )

    try:
        account = google_meet.exchange_code(
            db,
            user.id,
            code,
            None,
        )

        audit.log(
            db,
            user,
            "google.linked",
            "google_account",
            account.id,
            request,
        )

        db.commit()

        return RedirectResponse(
            url=f"{FRONTEND_MEETINGS_URL}?google=linked",
            status_code=303,
        )

    except google_meet.GoogleNotConfigured as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )

    except google_meet.GoogleAPIError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


# ---------------------------------------------------------------
# Unlink Google account
# ---------------------------------------------------------------

@router.delete("/google/unlink")
def google_unlink(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = (
        db.query(GoogleAccount)
        .filter(GoogleAccount.user_id == user.id)
        .one_or_none()
    )

    if account:
        account.refresh_token = None
        account.access_token = None
        db.flush()

    return {
        "detail": "Google account unlinked"
    }