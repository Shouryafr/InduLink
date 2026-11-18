"""Viewing another person's profile, subject to the student's visibility choice."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_user
from app.rendering import render
from data.models import User
from services import skill_registry, student_profiles

router = APIRouter(tags=["people"])


@router.get("/students/{profile_id}")
def student_profile(
    profile_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = student_profiles.get_by_id(db, profile_id)
    # Server-side visibility check; a denial is a 403 and is audited.
    student_profiles.require_view(user, profile)

    views = student_profiles.skill_views(db, profile)
    # Only skills with confirmed evidence are shown to a viewer who is not the
    # owner or an administrator: a declared level is the student's own claim.
    if user.id != profile.user_id and user.role != "admin":
        views = [view for view in views if view.assessment.confirmed_level is not None]

    return render(
        request,
        "student/public_profile.html",
        active="/students",
        profile=profile,
        views=views,
        availability=student_profiles.availability_stamp(profile),
        registry=skill_registry.load(db),
    )
