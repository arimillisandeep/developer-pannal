"""Notification history and read state (live delivery happens over WebSocket)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import Notification, User
from app.schemas import NotificationOut

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                       unread_only: bool = Query(False), limit: int = Query(50, ge=1, le=200)):
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    return query.order_by(Notification.created_at.desc()).limit(limit).all()


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.query(func.count(Notification.id)).filter(Notification.user_id == user.id,
                                                     Notification.is_read.is_(False)).scalar() or 0
    return {"unread": n}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    n.read_at = datetime.now(timezone.utc)
    db.flush()
    return {"detail": "read"}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(Notification).filter(Notification.user_id == user.id,
                                  Notification.is_read.is_(False)).update(
        {"is_read": True, "read_at": datetime.now(timezone.utc)})
    return {"detail": "all read"}


@router.delete("/{notification_id}")
def delete_notification(notification_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(n)
    return {"detail": "deleted"}
