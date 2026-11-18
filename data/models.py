"""The shared data model (brief section 5).

One source of truth per entity. Constraints live here as well as in the
services, so invalid data cannot be persisted even if a service is bypassed.

Timestamp convention: naive UTC. ``core.freshness`` treats naive values as
UTC. This keeps the schema portable to PostgreSQL, where the same columns can
be migrated to ``timestamptz`` without changing application code.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core import evidence_policy, requirements, skills

ROLES: tuple[str, ...] = ("student", "mentor", "industry", "admin")
ACCOUNT_STATUSES: tuple[str, ...] = ("pending", "active", "suspended")
PROFILE_VISIBILITIES: tuple[str, ...] = ("private", "university", "public")
TEAM_STATES: tuple[str, ...] = ("proposed", "confirmed", "dissolved")
MENTOR_ASSIGNMENT_STATES: tuple[str, ...] = (
    "proposed",
    "confirmed",
    "declined",
    "completed",
    "cancelled",
)
TASK_STATUSES: tuple[str, ...] = ("backlog", "in_progress", "review", "done", "blocked")
MILESTONE_STATES: tuple[str, ...] = ("planned", "in_progress", "met", "missed")
PUBLICATION_STATES: tuple[str, ...] = ("unpublished", "proposed", "approved", "rejected")
FILE_VISIBILITIES: tuple[str, ...] = ("team", "mentor", "company", "admin")


def utcnow() -> datetime:
    """Naive UTC timestamp for storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sql_in(values) -> str:
    """Render a tuple of strings as a SQL IN list.

    ``repr`` on a Python list produces square brackets, which SQLite reads as a
    subquery inside a CHECK constraint. Quoting explicitly keeps the generated
    DDL valid on SQLite and PostgreSQL alike.
    """
    quoted = ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)
    return f"({quoted})"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SyntheticMixin:
    """Every seeded row is flagged so the UI can label synthetic demo data."""

    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# --- Identity and access --------------------------------------------------


class User(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {sql_in(ROLES)}", name="ck_users_role"),
        CheckConstraint(f"status IN {sql_in(ACCOUNT_STATUSES)}", name="ck_users_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    failed_signins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_signin_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_signed_in_at: Mapped[datetime | None] = mapped_column(DateTime)

    student_profile: Mapped["StudentProfile | None"] = relationship(
        back_populates="user", uselist=False
    )
    mentor_profile: Mapped["Mentor | None"] = relationship(back_populates="user", uselist=False)

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class Session(Base):
    """Server-side session. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_reason: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    user: Mapped[User] = relationship()


class AuditEvent(Base):
    """Append-only audit trail. Database triggers refuse UPDATE and DELETE."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False, default="anonymous")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    after_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# --- Skill registry -------------------------------------------------------


class Skill(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(f"category IN {sql_in(skills.CATEGORIES)}", name="ck_skills_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="software")

    aliases: Mapped[list["SkillAlias"]] = relationship(back_populates="skill")


class SkillAlias(Base, TimestampMixin):
    __tablename__ = "skill_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    skill: Mapped[Skill] = relationship(back_populates="aliases")


class ProficiencyLevel(Base, TimestampMixin):
    """Written level definition, shown wherever a level appears."""

    __tablename__ = "proficiency_levels"
    __table_args__ = (
        CheckConstraint("level >= 1 AND level <= 5", name="ck_proficiency_levels_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)


# --- Student side ---------------------------------------------------------


class StudentProfile(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "student_profiles"
    __table_args__ = (
        CheckConstraint(
            f"visibility IN {sql_in(PROFILE_VISIBILITIES)}", name="ck_student_profiles_visibility"
        ),
        CheckConstraint(
            "availability_hours IS NULL OR availability_hours >= 0",
            name="ck_student_profiles_hours",
        ),
        CheckConstraint(
            "availability_to IS NULL OR availability_from IS NULL "
            "OR availability_to >= availability_from",
            name="ck_student_profiles_availability_window",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    headline: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    interests: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coursework: Mapped[str] = mapped_column(Text, nullable=False, default="")
    experience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    availability_hours: Mapped[int | None] = mapped_column(Integer)
    availability_from: Mapped[date | None] = mapped_column(Date)
    availability_to: Mapped[date | None] = mapped_column(Date)
    #: IANA name, e.g. Asia/Kolkata. Shown wherever an availability window is shown.
    availability_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kolkata"
    )
    availability_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    preferred_roles: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")

    user: Mapped[User] = relationship(back_populates="student_profile")
    skills: Mapped[list["StudentSkill"]] = relationship(back_populates="student")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="student")


class StudentSkill(Base, TimestampMixin):
    """Declared level only. Assessed level lives in ``evidence``."""

    __tablename__ = "student_skills"
    __table_args__ = (
        UniqueConstraint("student_id", "skill_id", name="uq_student_skills_pair"),
        CheckConstraint(
            "declared_level >= 1 AND declared_level <= 5",
            name="ck_student_skills_declared_level",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    declared_level: Mapped[int] = mapped_column(Integer, nullable=False)

    student: Mapped[StudentProfile] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


class Evidence(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            f"category IN {sql_in(evidence_policy.EVIDENCE_CATEGORIES)}",
            name="ck_evidence_category",
        ),
        CheckConstraint(
            f"status IN {sql_in(evidence_policy.EVIDENCE_STATUSES)}", name="ck_evidence_status"
        ),
        CheckConstraint(
            "demonstrated_level IS NULL OR (demonstrated_level >= 1 AND demonstrated_level <= 5)",
            name="ck_evidence_demonstrated_level",
        ),
        CheckConstraint("length(source) > 0", name="ck_evidence_source_present"),
        Index("ix_evidence_student_skill", "student_id", "skill_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    demonstrated_level: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    student: Mapped[StudentProfile] = relationship(back_populates="evidence")
    skill: Mapped[Skill] = relationship()
    reviewer: Mapped[User | None] = relationship()


# --- Mentor side ----------------------------------------------------------


class Mentor(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "mentors"
    __table_args__ = (
        CheckConstraint("max_active_projects > 0", name="ck_mentors_max_active_projects"),
        CheckConstraint("weekly_hours_cap > 0", name="ck_mentors_weekly_hours_cap"),
        CheckConstraint(
            "hours_per_project IS NULL OR hours_per_project >= 0",
            name="ck_mentors_hours_per_project",
        ),
        CheckConstraint(
            "mentoring_experience_years IS NULL OR mentoring_experience_years >= 0",
            name="ck_mentors_experience_years",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    domains: Mapped[str] = mapped_column(Text, nullable=False, default="")
    experience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mentoring_experience_years: Mapped[int | None] = mapped_column(Integer)
    mentoring_projects_count: Mapped[int | None] = mapped_column(Integer)
    max_active_projects: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    weekly_hours_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    hours_per_project: Mapped[int | None] = mapped_column(Integer, default=2)
    preferred_levels: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    user: Mapped[User] = relationship(back_populates="mentor_profile")
    skills: Mapped[list["MentorSkill"]] = relationship(back_populates="mentor")
    unavailability: Mapped[list["MentorUnavailability"]] = relationship(back_populates="mentor")


class MentorSkill(Base, TimestampMixin):
    __tablename__ = "mentor_skills"
    __table_args__ = (
        UniqueConstraint("mentor_id", "skill_id", name="uq_mentor_skills_pair"),
        CheckConstraint("level >= 1 AND level <= 5", name="ck_mentor_skills_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mentor_id: Mapped[int] = mapped_column(
        ForeignKey("mentors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_basis: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    mentor: Mapped[Mentor] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


class MentorUnavailability(Base, TimestampMixin):
    __tablename__ = "mentor_unavailability"
    __table_args__ = (
        CheckConstraint("to_date >= from_date", name="ck_mentor_unavailability_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mentor_id: Mapped[int] = mapped_column(
        ForeignKey("mentors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    mentor: Mapped[Mentor] = relationship(back_populates="unavailability")


# --- Projects and requirements --------------------------------------------


class Project(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            f"state IN {sql_in([s.value for s in requirements.ProjectState])}",
            name="ck_projects_state",
        ),
        CheckConstraint(
            f"visibility IN {sql_in(requirements.VISIBILITIES)}", name="ck_projects_visibility"
        ),
        CheckConstraint("team_size_min >= 1", name="ck_projects_team_min"),
        CheckConstraint("team_size_max >= team_size_min", name="ck_projects_team_range"),
        CheckConstraint("difficulty >= 1 AND difficulty <= 5", name="ck_projects_difficulty"),
        CheckConstraint("deadline >= start_date", name="ck_projects_dates"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    team_size_min: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    team_size_max: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default=requirements.ProjectState.DRAFT.value
    )
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    eligibility_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The partner's working timezone, compared with a student's when availability is checked.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    evaluation_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deliverables_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    owner: Mapped[User] = relationship()
    versions: Mapped[list["ProjectVersion"]] = relationship(
        back_populates="project", order_by="ProjectVersion.version_no"
    )

    @property
    def is_open_for_matching(self) -> bool:
        return requirements.ProjectState(self.state) in requirements.MATCHABLE_STATES


class ProjectVersion(Base, TimestampMixin):
    """Immutable requirement snapshot. Editing a project writes a new version."""

    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_no", name="uq_project_versions_number"),
        # One current version per project, without a circular foreign key.
        Index(
            "uq_project_versions_current",
            "project_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current IS TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    project: Mapped[Project] = relationship(back_populates="versions")
    requirements: Mapped[list["Requirement"]] = relationship(back_populates="version")


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("version_id", "skill_id", name="uq_requirements_version_skill"),
        CheckConstraint(
            "required_level >= 1 AND required_level <= 5", name="ck_requirements_level"
        ),
        CheckConstraint("weight > 0 AND weight <= 1", name="ck_requirements_weight"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    required_level: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    version: Mapped[ProjectVersion] = relationship(back_populates="requirements")
    skill: Mapped[Skill] = relationship()


class WorkPackage(Base, TimestampMixin):
    __tablename__ = "work_packages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected')", name="ck_work_packages_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    dependencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    suggested_role: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")


# --- Teams and mentor assignments -----------------------------------------


class Team(Base, TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(TEAM_STATES)}", name="ck_teams_state"),
        # A project can have at most one confirmed team, even under a race.
        Index(
            "uq_teams_confirmed_project",
            "project_id",
            unique=True,
            sqlite_where=text("state = 'confirmed'"),
            postgresql_where=text("state = 'confirmed'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Human-readable account of how the suggestion was built (Why panel).
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Frozen scoring output, so the screen can be re-read after the inputs move.
    score_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    members: Mapped[list["TeamMember"]] = relationship(back_populates="team")
    project: Mapped[Project] = relationship()


class TeamMember(Base, TimestampMixin):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "student_id", name="uq_team_members_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    assigned_hours: Mapped[int | None] = mapped_column(Integer)

    team: Mapped[Team] = relationship(back_populates="members")


class MentorAssignment(Base, TimestampMixin):
    __tablename__ = "mentor_assignments"
    __table_args__ = (
        CheckConstraint(
            f"state IN {sql_in(MENTOR_ASSIGNMENT_STATES)}", name="ck_mentor_assignments_state"
        ),
        # A project can have at most one confirmed mentor.
        Index(
            "uq_mentor_assignments_confirmed_project",
            "project_id",
            unique=True,
            sqlite_where=text("state = 'confirmed'"),
            postgresql_where=text("state = 'confirmed'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mentor_id: Mapped[int] = mapped_column(
        ForeignKey("mentors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    fit_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[Project] = relationship()
    mentor: Mapped[Mentor] = relationship()


class RecommendationSnapshot(Base, TimestampMixin):
    """Immutable history of what was recommended, from which inputs."""

    __tablename__ = "recommendation_snapshots"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('candidates', 'teams', 'mentor', 'readiness', 'radar', 'rematch')",
            name="ck_recommendation_snapshots_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="RESTRICT")
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# --- Execution ------------------------------------------------------------


class Milestone(Base, TimestampMixin):
    __tablename__ = "milestones"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(MILESTONE_STATES)}", name="ck_milestones_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="planned")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(TASK_STATUSES)}", name="ck_tasks_status"),
        CheckConstraint(
            "blocked_by_task_id IS NULL OR blocked_by_task_id != id",
            name="ck_tasks_not_self_blocked",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="backlog")
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocked_by_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT")
    )
    blocked_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class Deliverable(Base, TimestampMixin):
    __tablename__ = "deliverables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, default="project")
    target_id: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Evaluation(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rubric: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Confidential evaluations are visible to the author and administrators only.
    confidential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --- Outcomes -------------------------------------------------------------


class Contribution(Base, TimestampMixin):
    """Individual contribution, recorded separately from the team outcome."""

    __tablename__ = "contributions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'awaiting_review', 'confirmed', 'disputed')",
            name="ck_contributions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artefact_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewer_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Appeal(Base, TimestampMixin):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'under_review', 'resolved', 'withdrawn')", name="ck_appeals_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contribution_id: Mapped[int | None] = mapped_column(
        ForeignKey("contributions.id", ondelete="RESTRICT"), index=True
    )
    passport_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("passport_entries.id", ondelete="RESTRICT")
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    student: Mapped[StudentProfile] = relationship()


class PassportEntry(Base, TimestampMixin):
    __tablename__ = "passport_entries"
    __table_args__ = (
        CheckConstraint(
            f"publication_state IN {sql_in(PUBLICATION_STATES)}", name="ck_passport_entries_publication"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    technologies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    competencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    verified_scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    publication_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unpublished"
    )
    shared_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    student: Mapped[StudentProfile] = relationship()
    project: Mapped[Project] = relationship()


class Feedback(Base, TimestampMixin, SyntheticMixin):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    #: The person the feedback is about, when it is about a person rather than a team.
    subject_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    subject_role: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    structured_ratings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    comments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Confidential feedback is visible to its author and administrators only.
    confidential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    author: Mapped[User] = relationship(foreign_keys=[author_id])
    subject: Mapped["User | None"] = relationship(foreign_keys=[subject_user_id])
    project: Mapped["Project | None"] = relationship()


class StoredFile(Base, TimestampMixin):
    """Metadata for an upload. Bytes live outside the web root; the app never runs them."""

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint(
            f"visibility IN {sql_in(FILE_VISIBILITIES)}", name="ck_files_visibility"
        ),
        CheckConstraint("size > 0 AND size <= 10485760", name="ck_files_size"),
        CheckConstraint("length(stored_name) > 0", name="ck_files_stored_name"),
        Index("ix_files_project_visibility", "project_id", "visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    stored_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stated_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    sniffed_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="team")


class Scenario(Base, TimestampMixin):
    """What-If sandbox on an immutable snapshot. Never joined to live tables."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    base_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendation_snapshots.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Notification(Base, TimestampMixin, SyntheticMixin):
    """In-app notification. Rules-generated; no e-mail is sent from the demo."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    href: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class SubmissionToken(Base):
    """Idempotency key for a form post, so a double click creates one record."""

    __tablename__ = "submission_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    result_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    result_id: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SkillProposal(Base, TimestampMixin):
    """A student or mentor asking an administrator to add a missing skill."""

    __tablename__ = "skill_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'accepted', 'rejected')", name="ck_skill_proposals_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposed_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="software")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
