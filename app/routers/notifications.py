"""In-app notification inbox, available to every signed-in account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db, require_user
from app.rendering import render
from data.models import User
from services import notifications

router = APIRouter(tags=["notifications"])
AnyUser = Depends(require_user)


@router.get("/notifications")
def inbox(request: Request, user: User = AnyUser, db: Session = Depends(get_db)):
    rows = notifications.for_user(db, user.id)
    return render(
        request,
        "notifications.html",
        active="/notifications",
        rows=rows,
        unread=notifications.unread_count(db, user.id),
    )


@router.post("/notifications/read-all")
def read_all(request: Request, user: User = AnyUser, db: Session = Depends(get_db)):
    notifications.mark_all_read(db, user_id=user.id)
    return RedirectResponse("/notifications?saved=read", status_code=303)


@router.post("/notifications/{notification_id}/read")
def read_one(
    notification_id: int,
    request: Request,
    user: User = AnyUser,
    db: Session = Depends(get_db),
):
    notifications.mark_read(db, user_id=user.id, notification_id=notification_id)
    return RedirectResponse("/notifications", status_code=303)
