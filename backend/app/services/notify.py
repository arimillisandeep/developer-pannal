"""In-app notifications: persisted in PostgreSQL + pushed live over WebSocket."""
from sqlalchemy.orm import Session

from app.models import Notification, User
from app.ws.manager import manager


def notify(db: Session, user_id: str, ntype: str, title: str, body: str = "", link: str = "") -> Notification:
    n = Notification(user_id=user_id, type=ntype, title=title, body=body, link=link)
    db.add(n)
    db.flush()
    manager.publish(user_id, {
        "channel": "notification",
        "data": {
            "id": n.id, "type": ntype, "title": title, "body": body,
            "link": link, "is_read": False,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        },
    })
    return n


def notify_admins(db: Session, ntype: str, title: str, body: str = "", link: str = "") -> None:
    for admin in db.query(User).filter(User.role == "admin", User.is_active.is_(True)).all():
        notify(db, admin.id, ntype, title, body, link)


def notify_many(db: Session, user_ids, ntype: str, title: str, body: str = "", link: str = "") -> None:
    for uid in set(user_ids):
        notify(db, uid, ntype, title, body, link)
