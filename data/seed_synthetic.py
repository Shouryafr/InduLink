"""Deterministic synthetic dataset for the completed prototype.

Every person is fictional and every name comes from the North Indian pools in
``data/demo_personas.py``. The generator is deterministic (fixed seed) and
idempotent: running it twice adds nothing and changes no existing row. It never
deletes anything, and every row it writes is flagged ``is_synthetic``.

Run with::

    python -m data.seed_synthetic

The seed refuses to run unless demo mode is on and the environment is a local,
CI, demo or staging one, so synthetic people can never appear in production.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import Settings
from core import evidence_policy, requirements as req_rules
from data.demo_personas import (
    ADMIN_NAMES,
    COMPANIES,
    DEMO_PASSWORD,
    EMAIL_DOMAIN,
    FACULTY_NAMES,
    FIRST_NAMES_MEN,
    FIRST_NAMES_WOMEN,
    INDUSTRY_NAMES,
    MENTOR_NAMES,
    SIKH_MEN,
    SIKH_WOMEN,
    STUDENT_SEED_NAMES,
    SURNAMES,
    email_for,
)
from data.models import (
    Appeal,
    Contribution,
    Evaluation,
    Evidence,
    Feedback,
    Mentor,
    MentorAssignment,
    MentorSkill,
    MentorUnavailability,
    Notification,
    PassportEntry,
    Project,
    ProjectVersion,
    RecommendationSnapshot,
    Requirement,
    Skill,
    SkillAlias,
    StudentProfile,
    StudentSkill,
    Team,
    TeamMember,
    User,
    utcnow,
)
from services import authentication, skill_registry

RANDOM_SEED = 20261030
SEED_VERSION = "2026-10-30.1"

ALLOWED_SEED_ENVIRONMENTS = frozenset(
    {"local", "ci", "demo", "staging", "development", "test"}
)

MINIMUMS = {
    "students": 60,
    "mentors": 12,
    "faculty": 6,
    "industry": 4,
    "admins": 2,
    "projects": 15,
    "teams": 10,
    "evidence": 120,
    "passports": 20,
    "engagement": 30,
}

SKILLS: list[tuple[str, str, list[str]]] = [
    ("Python", "software", ["py", "python3"]),
    ("JavaScript", "software", ["js", "java script"]),
    ("TypeScript", "software", ["ts"]),
    ("React", "software", ["reactjs", "react.js"]),
    ("FastAPI", "software", ["fast api"]),
    ("SQL", "data", ["ansi sql"]),
    ("PostgreSQL", "data", ["postgres", "psql"]),
    ("Data modelling", "data", ["data modeling", "schema design"]),
    ("Data visualisation", "data", ["dataviz", "data viz"]),
    ("Statistics", "data", ["stats"]),
    ("Networking", "security", ["tcp ip", "network protocols"]),
    ("Threat modelling", "security", ["threat modeling"]),
    ("Secure coding", "security", ["appsec"]),
    ("Penetration testing", "security", ["pentest"]),
    ("Linux administration", "security", ["linux admin", "sysadmin"]),
    ("C", "embedded", ["ansi c"]),
    ("Embedded systems", "embedded", ["firmware"]),
    ("RTOS", "embedded", ["real time operating systems"]),
    ("Signal processing", "embedded", ["dsp"]),
    ("Technical writing", "delivery", ["docs", "documentation"]),
    ("Project coordination", "delivery", ["delivery management"]),
    ("Accessibility", "design", ["a11y"]),
    ("UI design", "design", ["interface design"]),
    ("Energy systems", "domain", ["power systems"]),
]

#: Canonical skills named by the master blueprint's display scenario (section 16).
#:
#: They are registered in the catalogue but kept out of the random distribution
#: above: adding them to ``SKILLS`` would shift every deterministic student draw,
#: and only the curated display candidates are meant to hold them. "Database
#: Management" is deliberately distinct from "SQL" (administration and schema
#: ownership versus the query language), and "Cybersecurity" from "Threat
#: modelling" and "Secure coding" (the umbrella practice versus two of its parts).
DISPLAY_SKILLS: list[tuple[str, str, list[str]]] = [
    ("Database Management", "data", ["dbms", "database administration", "database management system"]),
    ("Cybersecurity", "security", ["cyber security", "information security", "infosec"]),
]

#: The named candidates behind the master blueprint's section 18 display cases.
#: Their profiles are fixed by ``EDGE_SKILLS``, so the automatic per-project
#: coverage pass must leave them alone: gifting one of them reviewed evidence
#: would erase the case it exists to show.
DISPLAY_CASE_STUDENTS: frozenset[str] = frozenset(
    {
        "Neha Khanna",  # private profile
        "Sakshi Chopra",  # declared skills, deliberately no evidence
        "Anjali Mehra",  # full successful match
        "Dev Rathore",  # high soft score, one mandatory below level
    }
)

DEPARTMENTS = (
    "Computer Science",
    "Electronics and Communication",
    "Mechanical Engineering",
    "Management Studies",
)

PROJECT_SPECS: list[dict] = [
    {
        "key": "smartgrid",
        "title": "Smart-grid load forecasting dashboard",
        "domain": "Energy",
        "owner": 0,
        "difficulty": 4,
        "team": (3, 4),
        "state": "open",
        "visibility": "public",
        "start": -5,
        "days": 45,
        "timezone": "Asia/Kolkata",
        "description": (
            "Build an operator-facing dashboard that shows short-horizon substation load against "
            "weather and demand drivers, with an explanation for every band shown."
        ),
        "requirements": [
            ("Python", 3, 3, True),
            ("SQL", 3, 2, True),
            ("Data visualisation", 3, 2, False),
            ("Statistics", 2, 1, False),
        ],
    },
    {
        "key": "clinic",
        "title": "Clinic appointment triage companion",
        "domain": "Web",
        "owner": 1,
        "difficulty": 3,
        "team": (2, 3),
        "state": "open",
        "visibility": "university",
        "start": -2,
        "days": 30,
        "timezone": "Asia/Kolkata",
        "description": (
            "A small internal tool that routes appointment requests to the right service, with "
            "accessible forms and an audit trail of every change."
        ),
        "requirements": [
            ("FastAPI", 3, 3, True),
            ("PostgreSQL", 2, 2, False),
            ("Accessibility", 3, 2, True),
            ("Technical writing", 2, 1, False),
        ],
    },
    {
        "key": "soc",
        "title": "Security operations triage workbook",
        "domain": "Security",
        "owner": 0,
        "difficulty": 5,
        "team": (2, 3),
        "state": "open",
        "visibility": "private",
        "start": 0,
        "days": 60,
        "timezone": "Asia/Kolkata",
        "description": (
            "Turn a noisy alert feed into a triage workbook with documented decision rules. "
            "All material is confidential to the partner organisation."
        ),
        "requirements": [
            ("Threat modelling", 4, 3, True),
            ("Networking", 3, 2, True),
            ("Secure coding", 3, 2, False),
            ("Linux administration", 3, 1, False),
        ],
    },
    {
        "key": "firmware",
        "title": "Sensor node firmware reliability study",
        "domain": "Embedded",
        "owner": 1,
        "difficulty": 4,
        "team": (2, 2),
        "state": "approved",
        "visibility": "university",
        "start": 7,
        "days": 75,
        "timezone": "Asia/Kolkata",
        "description": (
            "Characterise recovery behaviour of a battery sensor node after brown-out and propose "
            "a watchdog configuration that survives a week of stress."
        ),
        "requirements": [
            ("C", 4, 4, True),
            ("Embedded systems", 3, 3, True),
            ("RTOS", 2, 2, False),
            ("Signal processing", 2, 1, False),
        ],
    },
    {
        "key": "catalogue",
        "title": "Open data catalogue refresh",
        "domain": "Data",
        "owner": 0,
        "difficulty": 2,
        "team": (2, 4),
        "state": "active",
        "visibility": "public",
        "start": -30,
        "days": 90,
        "timezone": "Asia/Kolkata",
        "description": (
            "Document and republish a municipal open data catalogue so every dataset carries a "
            "description, a licence and a refresh cadence."
        ),
        "requirements": [
            ("Python", 3, 3, True),
            ("Data modelling", 3, 3, True),
            ("Technical writing", 3, 2, False),
        ],
    },
    {
        "key": "accessibility",
        "title": "Accessibility audit of a student portal",
        "domain": "Web",
        "owner": 1,
        "difficulty": 3,
        "team": (2, 3),
        "state": "completed",
        "visibility": "public",
        "start": -120,
        "days": 60,
        "timezone": "Asia/Kolkata",
        "description": (
            "Audit a student portal against WCAG 2.2 AA, report findings with evidence, and hand "
            "over a prioritised remediation plan."
        ),
        "requirements": [
            ("Accessibility", 4, 3, True),
            ("JavaScript", 3, 2, False),
            ("Technical writing", 3, 2, True),
        ],
    },
    {
        "key": "telemetry",
        "title": "Telemetry ingestion prototype",
        "domain": "Data",
        "owner": 2,
        "difficulty": 3,
        "team": (2, 3),
        "state": "pending_approval",
        "visibility": "university",
        "start": 2,
        "days": 50,
        "timezone": "Asia/Kolkata",
        "description": (
            "Prototype an ingestion path for device telemetry with schema validation and a "
            "replay path for rejected records."
        ),
        "requirements": [
            ("Python", 3, 3, True),
            ("Linux administration", 2, 2, False),
            ("Data modelling", 2, 2, False),
        ],
    },
    {
        "key": "playbook",
        "title": "Incident response playbook draft",
        "domain": "Security",
        "owner": 3,
        "difficulty": 2,
        "team": (2, 2),
        "state": "draft",
        "visibility": "private",
        "start": 10,
        "days": 40,
        "timezone": "Asia/Kolkata",
        "description": (
            "Draft a first-line incident response playbook for a small engineering team, with "
            "decision points and escalation triggers."
        ),
        "requirements": [
            ("Technical writing", 3, 3, True),
            ("Threat modelling", 2, 2, False),
        ],
    },
    {
        "key": "fraud",
        "title": "Fraud screening rules refresh",
        "domain": "Data",
        "owner": 0,
        "difficulty": 4,
        "team": (2, 3),
        "state": "open",
        "visibility": "public",
        "start": -8,
        "days": 55,
        "timezone": "Asia/Kolkata",
        "changed": True,
        "description": (
            "Refresh a rules-based screening workbook: document each rule, test it against "
            "synthetic cases and report false-positive drivers honestly."
        ),
        "requirements": [
            ("Python", 3, 3, True),
            ("SQL", 3, 3, True),
            ("Statistics", 3, 2, False),
        ],
        "v2_requirements": [
            ("Python", 3, 3, True),
            ("SQL", 4, 3, True),
            ("Statistics", 3, 2, False),
        ],
        "v2_reason": "Partner raised the SQL level after the first screening run was too weak.",
    },
    {
        "key": "navigation",
        "title": "Campus navigation for new students",
        "domain": "Web",
        "owner": 1,
        "difficulty": 2,
        "team": (2, 3),
        "state": "open",
        "visibility": "university",
        "start": -1,
        "days": 35,
        "timezone": "Europe/London",
        "description": (
            "A small, accessible wayfinding tool for a first-week campus route, usable on a "
            "phone and with a keyboard."
        ),
        "requirements": [
            ("React", 3, 3, True),
            ("TypeScript", 3, 3, True),
            ("UI design", 3, 2, False),
            ("Accessibility", 2, 1, False),
        ],
    },
    {
        "key": "calibration",
        "title": "Sensor calibration data pipeline",
        "domain": "Embedded",
        "owner": 2,
        "difficulty": 4,
        "team": (2, 2),
        "state": "open",
        "visibility": "public",
        "start": 0,
        "days": 70,
        "timezone": "Asia/Kolkata",
        "description": (
            "Collect calibration readings from a bench rig, clean them, and publish a repeatable "
            "report with the uncertainty stated."
        ),
        "requirements": [
            ("Embedded systems", 3, 3, True),
            ("C", 3, 2, True),
            ("Signal processing", 3, 2, False),
        ],
    },
    {
        "key": "data_quality",
        "title": "Student records data-quality sweep",
        "domain": "Data",
        "owner": 2,
        "difficulty": 3,
        "team": (3, 4),
        "state": "active",
        "visibility": "public",
        "start": -25,
        "days": 80,
        "timezone": "Asia/Kolkata",
        "description": (
            "Profile a synthetic student-records extract, list every quality issue with a rule, "
            "and hand over a reproducible cleaning script."
        ),
        "requirements": [
            ("SQL", 3, 3, True),
            ("Data modelling", 3, 3, True),
            ("Python", 2, 2, False),
            ("Technical writing", 2, 2, False),
        ],
    },
    {
        "key": "api_hardening",
        "title": "API hardening review",
        "domain": "Security",
        "owner": 1,
        "difficulty": 4,
        "team": (2, 3),
        "state": "active",
        "visibility": "university",
        "start": -20,
        "days": 60,
        "timezone": "Asia/Kolkata",
        "description": (
            "Review a small public API against a documented checklist and fix the highest-risk "
            "findings with tests for each fix."
        ),
        "requirements": [
            ("Secure coding", 4, 3, True),
            ("FastAPI", 3, 3, True),
            ("Threat modelling", 3, 2, False),
        ],
    },
    {
        "key": "devops",
        "title": "Deployment baseline for a teaching lab",
        "domain": "Software",
        "owner": 3,
        "difficulty": 3,
        "team": (2, 3),
        "state": "open",
        "visibility": "public",
        "start": 3,
        "days": 40,
        "timezone": "Asia/Kolkata",
        "description": (
            "Write and test a repeatable deployment procedure for a teaching lab machine, "
            "including a rollback step that is actually exercised."
        ),
        "requirements": [
            ("Linux administration", 3, 3, True),
            ("Python", 2, 2, False),
            ("Technical writing", 2, 2, False),
        ],
    },
    {
        "key": "incident",
        "title": "Incident review and learning notes",
        "domain": "Security",
        "owner": 0,
        "difficulty": 3,
        "team": (2, 3),
        "state": "completed",
        "visibility": "public",
        "start": -100,
        "days": 55,
        "timezone": "Asia/Kolkata",
        "description": (
            "Run a blameless review of a synthetic outage, record the timeline, and write the "
            "learning notes a new team member could follow."
        ),
        "requirements": [
            ("Threat modelling", 3, 3, True),
            ("Technical writing", 3, 3, True),
            ("Networking", 2, 2, False),
        ],
    },
    {
        "key": "cancelled",
        "title": "Archived pilot: campus print queue",
        "domain": "Software",
        "owner": 3,
        "difficulty": 2,
        "team": (2, 2),
        "state": "cancelled",
        "visibility": "private",
        "start": -60,
        "days": 20,
        "timezone": "Asia/Kolkata",
        "description": "Withdrawn by the partner before work started. Kept visible for the audit trail.",
        "requirements": [
            ("Python", 2, 2, True),
            ("UI design", 2, 1, False),
        ],
    },
    # --- The master blueprint's display scenario (section 16) -----------------
    # The one connected story the run of show uses. Requirements, levels and
    # weights are copied from the guide exactly: 30/25/20/25, all mandatory.
    {
        "key": "finmon",
        "title": "Secure Financial Transaction Monitoring System",
        "domain": "Security",
        # Owned by the documented demo partner (owner index 0), so one account
        # drives the whole run of show without switching logins mid-demo.
        "owner": 0,
        "difficulty": 4,
        "team": (3, 4),
        "state": "open",
        "visibility": "public",
        "start": -3,
        "days": 45,
        "timezone": "Asia/Kolkata",
        "description": (
            "Build a monitoring prototype that flags suspicious transaction patterns in a "
            "synthetic ledger, explains every rule that fired and records the decision trail. "
            "No real customer data, no real payments, and no automatic blocking."
        ),
        "eligibility_note": (
            "Open to second-, third- and final-year students in the four participating "
            "departments. All four requirements are mandatory, so a strong score never "
            "compensates for a missing one."
        ),
        "evaluation_criteria": (
            "Working detection prototype (35%), test and evidence quality (25%), written "
            "report and rule explanations (25%), short demo (15%)."
        ),
        "deliverables": (
            "A working prototype over the synthetic ledger, a rule catalogue with the "
            "evidence behind each rule, a written report, and a short demo."
        ),
        "requirements": [
            ("Python", 4, 30, True),
            ("Database Management", 3, 25, True),
            ("Networking", 3, 20, True),
            ("Cybersecurity", 4, 25, True),
        ],
    },
]

MENTOR_SPECS: list[dict] = [
    {
        "domains": "Energy systems, data platforms",
        "experience": "Twelve years building grid analytics; supervised nine industry projects.",
        "years": 8,
        "count": 14,
        "max_projects": 3,
        "hours_cap": 12,
        "hours_per": 4,
        "skills": [("Python", 5), ("SQL", 4), ("Statistics", 4), ("Data visualisation", 4)],
    },
    {
        "domains": "Web platforms, accessibility",
        "experience": "Front-end platform lead; accessibility reviewer for two public portals.",
        "years": 6,
        "count": 10,
        "max_projects": 3,
        "hours_cap": 10,
        "hours_per": 4,
        "skills": [("JavaScript", 5), ("React", 4), ("Accessibility", 5), ("Technical writing", 3)],
    },
    {
        "domains": "Security operations",
        "experience": "SOC lead; threat-modelling trainer for three cohorts.",
        "years": 11,
        "count": 18,
        "max_projects": 2,
        "hours_cap": 8,
        "hours_per": 4,
        "skills": [("Threat modelling", 5), ("Networking", 4), ("Secure coding", 4)],
    },
    {
        "domains": "Embedded systems",
        "experience": "Firmware engineer; RTOS and low-power design.",
        "years": 9,
        "count": 11,
        "max_projects": 3,
        "hours_cap": 10,
        "hours_per": 3,
        "skills": [("C", 5), ("Embedded systems", 5), ("RTOS", 4), ("Signal processing", 3)],
    },
    {
        "domains": "Data engineering",
        "experience": "Data platform architect; ingestion and schema governance.",
        "years": 4,
        "count": 6,
        "max_projects": 2,
        "hours_cap": 40,
        "hours_per": 14,
        "skills": [("Python", 4), ("Data modelling", 5), ("PostgreSQL", 4), ("Linux administration", 3)],
    },
    {
        "domains": "Statistics, research methods",
        "experience": "Applied statistics; review methods for student projects.",
        "years": 15,
        "count": 22,
        "max_projects": 4,
        "hours_cap": 10,
        "hours_per": 2,
        "skills": [("Statistics", 5), ("Data visualisation", 3), ("Technical writing", 4)],
    },
    {
        "domains": "Software delivery",
        "experience": "Engineering manager; delivery reviews and test strategy.",
        "years": 7,
        "count": 12,
        "max_projects": 3,
        "hours_cap": 9,
        "hours_per": 3,
        "skills": [("Python", 4), ("Project coordination", 5), ("Technical writing", 4)],
    },
    {
        "domains": "Interface design",
        "experience": "Product designer; accessible interface reviews.",
        "years": 5,
        "count": 8,
        "max_projects": 3,
        "hours_cap": 9,
        "hours_per": 3,
        "skills": [("UI design", 5), ("Accessibility", 4), ("React", 3)],
    },
    {
        "domains": "Networks and infrastructure",
        "experience": "Network engineer; lab infrastructure and Linux administration.",
        "years": 10,
        "count": 16,
        "max_projects": 3,
        "hours_cap": 12,
        "hours_per": 4,
        "skills": [("Networking", 5), ("Linux administration", 5), ("Secure coding", 3)],
    },
    {
        "domains": "Embedded signal work",
        "experience": "Instrumentation engineer; calibration and signal chain reviews.",
        "years": 6,
        "count": 7,
        "max_projects": 2,
        "hours_cap": 8,
        "hours_per": 4,
        "skills": [("Signal processing", 5), ("Embedded systems", 4), ("C", 4)],
    },
    {
        "domains": "Data visualisation",
        "experience": "Analytics lead; dashboards and honest chart review.",
        "years": 5,
        "count": 9,
        "max_projects": 3,
        "hours_cap": 10,
        "hours_per": 3,
        "skills": [("Data visualisation", 5), ("Statistics", 3), ("JavaScript", 3)],
    },
    {
        "domains": "Software quality",
        "experience": "QA lead; test design and evidence review.",
        "years": 8,
        "count": 13,
        "max_projects": 1,
        "hours_cap": 4,
        "hours_per": 2,
        "skills": [("Python", 4), ("Technical writing", 5), ("Secure coding", 3)],
    },
    {
        # Covers every requirement of the display challenge, so the mentor step of
        # the run of show has a genuine fit rather than a list of gaps.
        "domains": "Financial security, data platforms",
        "experience": (
            "Payments security engineer; built monitoring rules for a synthetic ledger and "
            "reviewed student security projects for three cohorts."
        ),
        "years": 12,
        "count": 15,
        "max_projects": 3,
        "hours_cap": 12,
        "hours_per": 4,
        "skills": [
            ("Python", 5),
            ("Database Management", 5),
            ("Networking", 4),
            ("Cybersecurity", 5),
        ],
    },
]

FACULTY_SPECS: list[dict] = [
    {"domains": "Faculty reviewer, computing", "skills": [("Python", 5), ("Data modelling", 4)]},
    {"domains": "Faculty reviewer, electronics", "skills": [("Embedded systems", 5), ("C", 4)]},
    {"domains": "Faculty reviewer, management", "skills": [("Project coordination", 5), ("Technical writing", 4)]},
    {"domains": "Faculty reviewer, design", "skills": [("Accessibility", 5), ("UI design", 4)]},
    {"domains": "Faculty reviewer, security", "skills": [("Threat modelling", 4), ("Networking", 4)]},
    {"domains": "Faculty reviewer, statistics", "skills": [("Statistics", 5), ("Data visualisation", 3)]},
]

#: Fixed profiles for the seeded edge cases. Each entry is
#: skill name -> (declared level, [(category, status, demonstrated level, expiry offset days)]).
EDGE_SKILLS: dict[str, dict[str, tuple[int | None, list[tuple[str, str, int, int | None]]]]] = {
    "Aarohi Sharma": {
        "Python": (4, [("project_based", "verified", 4, None), ("practical_task", "reviewed", 4, None)]),
        "SQL": (4, [("practical_task", "reviewed", 4, None)]),
        "Data visualisation": (3, [("project_based", "reviewed", 3, None)]),
        "Statistics": (3, [("self_declared", "submitted", 3, None)]),
        "FastAPI": (3, [("project_based", "reviewed", 3, None)]),
        "Technical writing": (3, [("project_based", "reviewed", 3, None)]),
    },
    "Riya Dhillon": {
        "Python": (4, [("project_based", "reviewed", 4, None)]),
        "SQL": (4, [("practical_task", "reviewed", 4, None)]),
        "Data visualisation": (3, [("project_based", "reviewed", 3, None)]),
        "Statistics": (2, []),
    },
    "Karan Gill": {
        "Python": (4, [("project_based", "reviewed", 4, None)]),
        "SQL": (4, [("practical_task", "reviewed", 4, None)]),
        "Data visualisation": (3, [("project_based", "reviewed", 3, None)]),
        "Statistics": (2, []),
    },
    "Naina Kapoor": {
        "Python": (3, [("certificate", "verified", 4, -30)]),
        "SQL": (3, [("practical_task", "reviewed", 3, None)]),
    },
    # Master blueprint section 18, "incomplete evidence": every level is declared at
    # or above the display challenge's threshold and nothing has been reviewed, so
    # the candidate is provisional and the screen says "evidence needed", never zero.
    "Sakshi Chopra": {
        "Python": (4, []),
        "Database Management": (3, []),
        "Networking": (3, []),
        "Cybersecurity": (4, []),
        "JavaScript": (3, []),
        "Technical writing": (3, []),
    },
    # Section 18, "successful match": reviewed evidence at or above every mandatory
    # level, so this candidate scores the full 100 and is eligible outright.
    "Anjali Mehra": {
        "Python": (5, [("project_based", "verified", 5, None)]),
        "Database Management": (4, [("practical_task", "reviewed", 4, None)]),
        "Networking": (4, [("project_based", "reviewed", 4, None)]),
        "Cybersecurity": (5, [("project_based", "verified", 5, None)]),
    },
    # Section 18, "mandatory failure": three strong confirmed levels and a reviewed
    # Networking level of 1 against a required 3. The soft score stays high (~87)
    # and the candidate is still not eligible, which is the point of the case.
    "Dev Rathore": {
        "Python": (5, [("project_based", "verified", 5, None)]),
        "Database Management": (4, [("practical_task", "reviewed", 4, None)]),
        "Networking": (1, [("practical_task", "reviewed", 1, None)]),
        "Cybersecurity": (5, [("project_based", "reviewed", 5, None)]),
    },
    "Simran Kaur": {
        "Threat modelling": (3, [("practical_task", "rejected", 3, None)]),
        "Networking": (3, [("project_based", "reviewed", 3, None)]),
    },
}


# --- helpers --------------------------------------------------------------


class SeedRefused(RuntimeError):
    """Raised when the seed is asked to run somewhere it must not."""


def ensure_seed_allowed(settings: Settings) -> None:
    """Refuse to write synthetic people outside a demo environment."""
    if not settings.demo_mode:
        raise SeedRefused(
            "The synthetic dataset only runs with DEMO_MODE=true. Real deployments must not "
            "contain synthetic people."
        )
    if settings.app_env.lower() not in ALLOWED_SEED_ENVIRONMENTS:
        raise SeedRefused(
            f"APP_ENV={settings.app_env!r} is not a demo environment. Allowed: "
            + ", ".join(sorted(ALLOWED_SEED_ENVIRONMENTS))
            + "."
        )


def _user(
    session: Session,
    *,
    email: str,
    name: str,
    role: str,
    password_hash: str,
) -> User:
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing
    user = User(
        email=email,
        password_hash=password_hash,
        role=role,
        status="active",
        display_name=name,
        is_synthetic=True,
    )
    session.add(user)
    session.flush()
    return user


def _student_profile(session: Session, user: User) -> StudentProfile:
    profile = session.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))
    if profile is not None:
        return profile
    profile = StudentProfile(user_id=user.id, visibility="private", is_synthetic=True)
    session.add(profile)
    session.flush()
    return profile


def _ensure_skills(session: Session) -> dict[str, Skill]:
    existing = {skill.canonical_name: skill for skill in session.scalars(select(Skill)).all()}
    for name, category, aliases in (*SKILLS, *DISPLAY_SKILLS):
        if name in existing:
            continue
        skill = Skill(canonical_name=name, category=category, is_synthetic=True)
        session.add(skill)
        session.flush()
        existing[name] = skill
        for alias in aliases:
            session.add(SkillAlias(skill_id=skill.id, alias=alias))
    skill_registry.ensure_levels(session)
    session.flush()
    return existing


def _plain_name(name: str) -> str:
    """A person name without its title, so reserved names compare equally."""
    parts = [
        part
        for part in name.replace(".", " ").split()
        if part.lower() not in {"dr", "prof", "professor"}
    ]
    return " ".join(parts)


def _build_roster(rng: random.Random) -> list[str]:
    """60 unique North Indian student names, demo personas first.

    Names already used by a mentor, faculty reviewer, partner or administrator
    are reserved, because no two seeded people may share a full name. Titles are
    stripped first, so "Dr Harpreet Singh" also reserves "Harpreet Singh".
    """
    used = {
        _plain_name(name)
        for name in (
            *STUDENT_SEED_NAMES,
            *MENTOR_NAMES,
            *FACULTY_NAMES,
            *INDUSTRY_NAMES,
            *ADMIN_NAMES,
        )
    }
    women = list(FIRST_NAMES_WOMEN)
    men = list(FIRST_NAMES_MEN)
    order = []
    for index in range(max(len(women), len(men))):
        if index < len(women):
            order.append((women[index], "woman"))
        if index < len(men):
            order.append((men[index], "man"))
    surnames = [surname for surname in SURNAMES if surname != "Singh"]
    offset = rng.randrange(len(surnames))

    names = list(STUDENT_SEED_NAMES)
    cursor = 0
    while len(names) < 60:
        first, gender = order[cursor % len(order)]
        if first in SIKH_WOMEN or first in SIKH_MEN:
            surname = "Kaur" if gender == "woman" else "Singh"
        else:
            surname = surnames[(offset + cursor) % len(surnames)]
        full = f"{first} {surname}"
        cursor += 1
        if full in used:
            continue
        used.add(full)
        names.append(full)
    return names


def _seed_student_body(
    session: Session,
    rng: random.Random,
    *,
    profile: StudentProfile,
    index: int,
    name: str,
    skills: dict[str, Skill],
    today: date,
) -> None:
    if session.scalar(select(StudentSkill.id).where(StudentSkill.student_id == profile.id)):
        return
    if session.scalar(select(Evidence.id).where(Evidence.student_id == profile.id)):
        return

    department = DEPARTMENTS[index % len(DEPARTMENTS)]
    year = 1 + (index % 4)
    profile.headline = f"{year}{'st' if year == 1 else 'nd' if year == 2 else 'rd' if year == 3 else 'th'}-year {department} student building evidence-backed project experience."
    profile.interests = "Data platforms, applied security, accessible interface quality."
    profile.coursework = f"{department} core modules, research methods, team project."
    profile.experience = "Two coursework projects and one summer placement."
    profile.preferred_roles = rng.choice(
        ["backend", "data analysis", "QA", "frontend", "coordination"]
    )
    # A realistic spread: most students opt into being visible to partners,
    # some stay inside the institution, a few keep a private profile.
    profile.visibility = rng.choice(
        ["public", "public", "public", "university", "university", "private"]
    )
    profile.availability_hours = rng.choice([4, 6, 8, 10, 12, 16])
    profile.availability_from = today - timedelta(days=rng.randint(0, 10))
    profile.availability_to = today + timedelta(days=rng.choice([60, 90, 120, 150]))
    profile.availability_timezone = "Asia/Kolkata"
    stale = index % 3 == 1
    profile.availability_updated_at = utcnow() - timedelta(
        days=52 if stale else rng.randint(1, 12)
    )

    # Deliberate edge cases, keyed by name so the demo script can name them.
    if name == "Diya Arora":
        profile.availability_hours = None
        profile.availability_from = None
        profile.availability_to = None
        profile.availability_updated_at = None
        profile.headline = "Second-year student; availability not recorded yet."
    elif name == "Tanvi Bisht":
        profile.availability_from = today - timedelta(days=200)
        profile.availability_to = today - timedelta(days=120)
        profile.availability_updated_at = utcnow()
        profile.headline = "Third-year student; current window ended before the live challenges."
    elif name == "Ishita Chauhan":
        profile.availability_timezone = "Europe/London"
        profile.availability_from = today + timedelta(days=20)
        profile.availability_to = today + timedelta(days=120)
        profile.availability_updated_at = utcnow()
        profile.headline = "Exchange student; availability recorded in London time."
    elif name == "Neha Khanna":
        profile.visibility = "private"
        profile.headline = "Final-year student with a private profile."
    elif name in {
        "Aarohi Sharma",
        "Riya Dhillon",
        "Karan Gill",
        # Section 18 display cases: the partner account has to see them, and a
        # random visibility draw would silently remove the case from the demo.
        "Anjali Mehra",
        "Dev Rathore",
    }:
        # The demo personas and the documented tie pair must be visible to the
        # partner account, or the screens would look empty for the wrong reason.
        profile.visibility = "public"

    fixed = EDGE_SKILLS.get(name)
    if fixed is not None:
        for skill_name, (declared, claims) in fixed.items():
            skill = skills[skill_name]
            if declared is not None:
                session.add(
                    StudentSkill(
                        student_id=profile.id, skill_id=skill.id, declared_level=declared
                    )
                )
            for category, status, level, expiry_offset in claims:
                session.add(
                    Evidence(
                        student_id=profile.id,
                        skill_id=skill.id,
                        category=category,
                        source=f"Synthetic portfolio item for {skill_name}",
                        evidence_date=today - timedelta(days=120),
                        expiry_date=(today + timedelta(days=expiry_offset)) if expiry_offset is not None else None,
                        demonstrated_level=level,
                        status=status,
                        review_notes=(
                            "Checked the submitted work against the level definition."
                            if status in {"reviewed", "verified", "rejected"}
                            else ""
                        ),
                        reviewed_at=(
                            utcnow() - timedelta(days=30)
                            if status in {"reviewed", "verified", "rejected"}
                            else None
                        ),
                        is_synthetic=True,
                    )
                )
        return

    skill_names = [skill_name for skill_name, _category, _aliases in SKILLS]
    chosen = rng.sample(skill_names, k=rng.randint(3, 6))
    categories = list(evidence_policy.EVIDENCE_CATEGORIES)
    for skill_name in chosen:
        skill = skills[skill_name]
        declared = rng.randint(1, 5)
        session.add(
            StudentSkill(student_id=profile.id, skill_id=skill.id, declared_level=declared)
        )
        for _ in range(rng.randint(1, 3)):
            category = rng.choice(categories)
            status = rng.choice(
                ["submitted", "under_review", "reviewed", "reviewed", "verified", "rejected"]
            )
            claim = min(5, max(1, declared + rng.choice([-1, 0, 0, 1])))
            expiry = None
            if category == "certificate":
                expiry = today + timedelta(days=rng.choice([-45, 30, 180, 400]))
            session.add(
                Evidence(
                    student_id=profile.id,
                    skill_id=skill.id,
                    category=category,
                    source=f"Synthetic portfolio item {rng.randint(100, 999)}",
                    evidence_date=today - timedelta(days=rng.randint(20, 700)),
                    expiry_date=expiry,
                    demonstrated_level=claim,
                    status=status,
                    review_notes=(
                        "Checked the submitted work against the level definition."
                        if status in {"reviewed", "verified", "rejected"}
                        else ""
                    ),
                    reviewed_at=(
                        utcnow() - timedelta(days=rng.randint(1, 40))
                        if status in {"reviewed", "verified", "rejected"}
                        else None
                    ),
                    is_synthetic=True,
                )
            )


def _seed_students(
    session: Session,
    rng: random.Random,
    skills: dict[str, Skill],
    password_hash: str,
    today: date,
) -> list[StudentProfile]:
    profiles: list[StudentProfile] = []
    for index, name in enumerate(_build_roster(rng)):
        email = (
            "aarohi.sharma@indulink.example.test"
            if index == 0
            else email_for(name, prefix="student.")
        )
        user = _user(session, email=email, name=name, role="student", password_hash=password_hash)
        profile = _student_profile(session, user)
        _seed_student_body(
            session,
            rng,
            profile=profile,
            index=index,
            name=name,
            skills=skills,
            today=today,
        )
        profiles.append(profile)
    session.flush()
    return profiles


def _ensure_project_coverage(
    session: Session,
    projects: list[Project],
    skills: dict[str, Skill],
    profiles: list[StudentProfile],
    today: date,
) -> None:
    """Make sure every live challenge has eligible, evidenced candidates.

    Random skill assignment alone leaves most challenges with nobody who holds
    both mandatory skills, which would make the matching screens look empty for
    reasons that are an artefact of the seed. Here each live challenge gets a
    small, deterministic cohort whose mandatory skills carry reviewed evidence
    at the required level. It only adds rows, so re-running changes nothing.
    """
    for index, (spec, project) in enumerate(zip(PROJECT_SPECS, projects)):
        if project.state not in {"open", "approved", "active"}:
            continue
        required = spec.get("v2_requirements") or spec["requirements"]
        mandatory = [(row[0], row[1]) for row in required if row[3]]
        if not mandatory:
            continue
        cohort_size = min(max(project.team_size_max + 3, 5), 9)
        start = (index * 7 + 3) % len(profiles)
        cohort = [profiles[(start + offset) % len(profiles)] for offset in range(cohort_size)]
        for position, profile in enumerate(cohort):
            if profile.user is not None and profile.user.display_name in DISPLAY_CASE_STUDENTS:
                continue
            if profile.visibility == "private":
                profile.visibility = "public"
            for skill_name, required_level in mandatory:
                skill = skills[skill_name]
                level = min(5, required_level + (position % 3))
                status = "verified" if position % 4 == 0 else "reviewed"
                declared = session.scalar(
                    select(StudentSkill).where(
                        StudentSkill.student_id == profile.id,
                        StudentSkill.skill_id == skill.id,
                    )
                )
                if declared is None:
                    session.add(
                        StudentSkill(
                            student_id=profile.id,
                            skill_id=skill.id,
                            declared_level=level,
                        )
                    )
                confirming = session.scalars(
                    select(Evidence).where(
                        Evidence.student_id == profile.id,
                        Evidence.skill_id == skill.id,
                        Evidence.status.in_({"reviewed", "verified"}),
                    )
                ).all()
                if any(
                    row.demonstrated_level is not None
                    and row.demonstrated_level >= required_level
                    for row in confirming
                ):
                    continue
                session.add(
                    Evidence(
                        student_id=profile.id,
                        skill_id=skill.id,
                        category="project_based",
                        source=f"Synthetic project evidence for {skill_name}",
                        evidence_date=today - timedelta(days=45 + position * 7),
                        demonstrated_level=level,
                        status=status,
                        review_notes=(
                            "Checked the submitted work against the level definition."
                        ),
                        reviewed_at=utcnow() - timedelta(days=20),
                        is_synthetic=True,
                    )
                )
    session.flush()


def _seed_projects(
    session: Session,
    industry_users: list[User],
    skills: dict[str, Skill],
    today: date,
) -> list[Project]:
    projects: list[Project] = []
    for spec in PROJECT_SPECS:
        owner = industry_users[spec["owner"]]
        existing = session.scalar(select(Project).where(Project.title == spec["title"]))
        if existing is not None:
            projects.append(existing)
            continue

        start = today + timedelta(days=spec["start"])
        deadline = start + timedelta(days=int(spec["days"] * 1.5))
        project = Project(
            owner_id=owner.id,
            title=spec["title"],
            domain=spec["domain"],
            description=spec["description"],
            difficulty=spec["difficulty"],
            team_size_min=spec["team"][0],
            team_size_max=spec["team"][1],
            start_date=start,
            deadline=deadline,
            state=spec["state"],
            visibility=spec["visibility"],
            timezone=spec["timezone"],
            eligibility_note=spec.get(
                "eligibility_note",
                "Open to students in the four participating departments.",
            ),
            evaluation_criteria=spec.get(
                "evaluation_criteria",
                "Deliverable quality, evidence of testing, clarity of write-up.",
            ),
            deliverables_summary=spec.get(
                "deliverables",
                "A working prototype, a written report, and a short demo.",
            ),
            is_synthetic=True,
        )
        session.add(project)
        session.flush()

        _write_version(
            session,
            project=project,
            rows=spec["requirements"],
            skills=skills,
            version_no=1,
            reason="Initial challenge submission",
            owner=owner,
            today=today,
        )
        if spec.get("v2_requirements"):
            _write_version(
                session,
                project=project,
                rows=spec["v2_requirements"],
                skills=skills,
                version_no=2,
                reason=spec["v2_reason"],
                owner=owner,
                today=today,
                previous=True,
            )
        projects.append(project)
    session.flush()
    return projects


def _write_version(
    session: Session,
    *,
    project: Project,
    rows: list[tuple],
    skills: dict[str, Skill],
    version_no: int,
    reason: str,
    owner: User,
    today: date,
    previous: bool = False,
) -> ProjectVersion:
    if previous:
        current = session.scalars(
            select(ProjectVersion).where(
                ProjectVersion.project_id == project.id,
                ProjectVersion.is_current.is_(True),
            )
        ).all()
        for row in current:
            row.is_current = False
        session.flush()

    weights = req_rules.normalize_weights([row[2] for row in rows])
    version = ProjectVersion(
        project_id=project.id,
        version_no=version_no,
        created_by=owner.id,
        reason=reason[:300],
        is_current=True,
        snapshot={
            "title": project.title,
            "domain": project.domain,
            "difficulty": project.difficulty,
            "visibility": project.visibility,
            "timezone": project.timezone,
            "requirements": [
                {
                    "skill_id": skills[row[0]].id,
                    "required_level": row[1],
                    "weight": weights[index],
                    "is_mandatory": row[3],
                }
                for index, row in enumerate(rows)
            ],
        },
    )
    session.add(version)
    session.flush()
    for index, row in enumerate(rows):
        session.add(
            Requirement(
                version_id=version.id,
                skill_id=skills[row[0]].id,
                required_level=row[1],
                weight=weights[index],
                is_mandatory=row[3],
            )
        )
    return version


def _mentor_user(session: Session, name: str, password_hash: str) -> User:
    email = (
        "harpreet.singh@indulink.example.test"
        if name == MENTOR_NAMES[0]
        else email_for(name, prefix="mentor.")
    )
    return _user(session, email=email, name=name, role="mentor", password_hash=password_hash)


def _seed_mentors(
    session: Session,
    skills: dict[str, Skill],
    password_hash: str,
    today: date,
) -> list[Mentor]:
    mentors: list[Mentor] = []
    for index, (name, spec) in enumerate(zip(MENTOR_NAMES, MENTOR_SPECS)):
        user = _mentor_user(session, name, password_hash)
        mentor = session.scalar(select(Mentor).where(Mentor.user_id == user.id))
        if mentor is None:
            mentor = Mentor(
                user_id=user.id,
                domains=spec["domains"],
                experience=spec["experience"],
                mentoring_experience_years=spec["years"],
                mentoring_projects_count=spec["count"],
                max_active_projects=spec["max_projects"],
                weekly_hours_cap=spec["hours_cap"],
                hours_per_project=spec["hours_per"],
                preferred_levels="2-4",
                is_synthetic=True,
            )
            session.add(mentor)
            session.flush()
            for skill_name, level in spec["skills"]:
                session.add(
                    MentorSkill(
                        mentor_id=mentor.id,
                        skill_id=skills[skill_name].id,
                        level=level,
                        evidence_basis="Synthetic record: teaching, publication or delivery.",
                    )
                )
        mentors.append(mentor)

    # Faculty reviewers: mentor-role accounts with review expertise and no project capacity used.
    for name, spec in zip(FACULTY_NAMES, FACULTY_SPECS):
        email = (
            "neha.tandon@indulink.example.test"
            if name == FACULTY_NAMES[0]
            else email_for(name, prefix="faculty.")
        )
        user = _user(session, email=email, name=name, role="mentor", password_hash=password_hash)
        mentor = session.scalar(select(Mentor).where(Mentor.user_id == user.id))
        if mentor is None:
            mentor = Mentor(
                user_id=user.id,
                domains=spec["domains"],
                experience="Faculty reviewer for evidence and challenge approval.",
                mentoring_experience_years=10,
                mentoring_projects_count=0,
                max_active_projects=1,
                weekly_hours_cap=2,
                hours_per_project=0,
                preferred_levels="1-5",
                is_synthetic=True,
            )
            session.add(mentor)
            session.flush()
            for skill_name, level in spec["skills"]:
                session.add(
                    MentorSkill(
                        mentor_id=mentor.id,
                        skill_id=skills[skill_name].id,
                        level=level,
                        evidence_basis="Synthetic faculty review record.",
                    )
                )

    # One mentor with no availability across every seeded project window.
    unavailable_mentor = mentors[11]
    existing = session.scalar(
        select(MentorUnavailability).where(
            MentorUnavailability.mentor_id == unavailable_mentor.id
        )
    )
    if existing is None:
        session.add(
            MentorUnavailability(
                mentor_id=unavailable_mentor.id,
                from_date=today - timedelta(days=120),
                to_date=today + timedelta(days=240),
                reason="Sabbatical for the whole demo period; capacity shown as unavailable.",
            )
        )
    # A second mentor unavailable for a shorter, visible window.
    if not session.scalar(
        select(MentorUnavailability).where(MentorUnavailability.mentor_id == mentors[3].id)
    ):
        session.add(
            MentorUnavailability(
                mentor_id=mentors[3].id,
                from_date=today + timedelta(days=14),
                to_date=today + timedelta(days=28),
                reason="Conference and field work",
            )
        )
    session.flush()
    return mentors


def _seed_assignments(
    session: Session, projects: list[Project], mentors: list[Mentor], today: date
) -> None:
    by_key = {spec["key"]: project for spec, project in zip(PROJECT_SPECS, projects)}

    def assign(project: Project, mentor: Mentor, state: str, reason: str) -> None:
        existing = session.scalar(
            select(MentorAssignment).where(
                MentorAssignment.mentor_id == mentor.id,
                MentorAssignment.project_id == project.id,
            )
        )
        if existing is not None:
            return
        session.add(
            MentorAssignment(
                project_id=project.id,
                mentor_id=mentor.id,
                state=state,
                confirmed_at=utcnow() if state == "confirmed" else None,
                reason=reason,
                fit_snapshot={"synthetic": True, "seed": SEED_VERSION},
            )
        )
        session.flush()

    # Confirmed mentors on the projects already under way.
    assign(by_key["catalogue"], mentors[4], "confirmed", "Synthetic demo assignment")
    assign(by_key["api_hardening"], mentors[2], "confirmed", "Synthetic demo assignment")
    assign(by_key["accessibility"], mentors[1], "confirmed", "Synthetic demo assignment")
    assign(by_key["incident"], mentors[0], "confirmed", "Synthetic demo assignment")
    assign(by_key["data_quality"], mentors[5], "confirmed", "Synthetic demo assignment")

    # Contested slot: two students recommended the same mentor, one slot open.
    assign(by_key["fraud"], mentors[0], "proposed", "Recommended for the SQL and statistics requirements")
    assign(by_key["fraud"], mentors[1], "proposed", "Second choice: stronger accessibility background")

    # Pending requests visible in the mentor inbox.
    assign(by_key["navigation"], mentors[7], "proposed", "Interface design and accessibility fit")
    assign(by_key["calibration"], mentors[9], "proposed", "Signal processing fit")
    assign(by_key["smartgrid"], mentors[10], "proposed", "Data visualisation fit")


def _seed_teams(
    session: Session,
    projects: list[Project],
    profiles: list[StudentProfile],
    rng: random.Random,
) -> list[Team]:
    by_key = {spec["key"]: project for spec, project in zip(PROJECT_SPECS, projects)}
    plans = [
        ("catalogue", "Catalogue delivery A", "confirmed", 3, [10, 11, 12]),
        ("catalogue", "Catalogue delivery B", "proposed", 1, [13]),
        ("data_quality", "Data quality squad", "confirmed", 3, [14, 15, 16]),
        ("data_quality", "Data quality alternate", "proposed", 2, [17, 18]),
        ("api_hardening", "API hardening pair", "confirmed", 2, [19, 20]),
        ("api_hardening", "API hardening reserve", "proposed", 2, [21, 22]),
        ("accessibility", "Audit team one", "confirmed", 3, [23, 24, 25]),
        ("accessibility", "Audit team two", "dissolved", 2, [26, 27]),
        ("incident", "Review team", "confirmed", 3, [28, 29, 30]),
        ("incident", "Review reserve", "proposed", 2, [31, 32]),
        ("smartgrid", "Load dashboard trio", "confirmed", 3, [33, 34, 35]),
        ("fraud", "Screening pair", "confirmed", 2, [36, 37]),
        ("soc", "Triage pair", "confirmed", 2, [38, 39]),
    ]
    teams: list[Team] = []
    for key, label, state, count, indexes in plans:
        project = by_key.get(key)
        if project is None:
            continue
        existing = session.scalar(
            select(Team).where(Team.project_id == project.id, Team.label == label)
        )
        if existing is not None:
            teams.append(existing)
            continue
        team = Team(
            project_id=project.id,
            state=state,
            label=label,
            rationale="Seeded team used to demonstrate the team screen.",
            score_snapshot={"seed": SEED_VERSION},
        )
        session.add(team)
        session.flush()
        roles = ["backend", "data analysis", "documentation", "coordination"]
        selected = [profiles[index % len(profiles)] for index in indexes][:count]
        for position, profile in enumerate(selected):
            session.add(
                TeamMember(
                    team_id=team.id,
                    student_id=profile.id,
                    role=roles[position % len(roles)],
                    assigned_hours=rng.choice([4, 6, 8]),
                )
            )
        teams.append(team)
    session.flush()
    # Deliberately invalid team: one member against a minimum of two. It must
    # be refused at confirmation, not quietly accepted.
    return teams


def _seed_outcomes(
    session: Session,
    projects: list[Project],
    profiles: list[StudentProfile],
    industry_users: list[User],
    mentors: list[Mentor],
    teams: list[Team],
    today: date,
) -> None:
    by_key = {spec["key"]: project for spec, project in zip(PROJECT_SPECS, projects)}
    mentor_users = [mentor.user for mentor in mentors[:6]]

    # Contributions for confirmed teams.
    contributions: list[Contribution] = []
    if not session.scalar(select(func.count(Contribution.id))):
        for team in teams:
            if team.state != "confirmed":
                continue
            for member in team.members[:3]:
                row = Contribution(
                    project_id=team.project_id,
                    member_id=member.student_id,
                    summary="Built and tested part of the deliverable; notes recorded in the project log.",
                    artefact_refs=["synthetic/report.pdf"],
                    status="confirmed" if team.project_id % 2 == 0 else "awaiting_review",
                    reviewer_id=mentor_users[0].id,
                    reviewer_comment="Scope matches the contribution summary.",
                )
                session.add(row)
                session.flush()
                contributions.append(row)

    # Passports: 20 entries across confirmed teams, some published.
    if not session.scalar(select(func.count(PassportEntry.id))):
        created = 0
        states = ["approved", "unpublished", "proposed", "approved"]
        for team in teams:
            if team.state != "confirmed":
                continue
            project = session.get(Project, team.project_id)
            for member in team.members:
                if created >= 20:
                    break
                profile = session.get(StudentProfile, member.student_id)
                if profile is None:
                    continue
                state = states[created % len(states)]
                session.add(
                    PassportEntry(
                        student_id=profile.id,
                        project_id=project.id,
                        role=member.role or "contributor",
                        technologies=["Python", "SQL"] if created % 2 == 0 else ["React", "TypeScript"],
                        competencies=[
                            "Delivered a scoped task against a written brief",
                            "Recorded evidence for a mentor review",
                        ],
                        verified_scope="The mentor confirmed the work described in the project log.",
                        reviewer_id=mentor_users[0].id,
                        approver_id=mentor_users[0].id if state == "approved" else None,
                        publication_state=state,
                        shared_fields=["role", "technologies", "competencies", "verified_scope"],
                    )
                )
                created += 1
            if created >= 20:
                break
        session.flush()

    # Evaluations, feedback and appeals: 30 records in total.
    if not session.scalar(select(func.count(Evaluation.id))):
        for index in range(8):
            project = projects[(index * 3) % len(projects)]
            confidential = index % 3 != 0
            session.add(
                Evaluation(
                    project_id=project.id,
                    rubric={"delivery": 40, "quality": 40, "communication": 20},
                    scores={"delivery": 3 + index % 2, "quality": 3, "communication": 4},
                    reviewer_id=industry_users[index % len(industry_users)].id,
                    outcome="met" if index % 4 else "partially_met",
                    summary=(
                        "Work met the brief with minor gaps; notes are confidential to the reviewer."
                        if confidential
                        else "Shared summary: the deliverable met the agreed acceptance criteria."
                    ),
                    confidential=confidential,
                    is_synthetic=True,
                )
            )

    if not session.scalar(select(func.count(Feedback.id))):
        ratings = [("clarity", 4), ("reliability", 3), ("collaboration", 5)]
        for index in range(12):
            project = projects[(index * 2) % len(projects)]
            subject = profiles[(index * 5 + 7) % len(profiles)]
            session.add(
                Feedback(
                    author_id=mentor_users[index % len(mentor_users)].id,
                    project_id=project.id,
                    subject_user_id=subject.user_id,
                    subject_role="student",
                    structured_ratings={key: 1 + (value + index) % 5 for key, value in ratings},
                    comments=(
                        "Structured feedback recorded from the synthetic review."
                        if index % 2
                        else "Confidential note for the administrator and the author only."
                    ),
                    confidential=bool(index % 2),
                    is_synthetic=True,
                )
            )

    if not session.scalar(select(func.count(Appeal.id))):
        statuses = ["open", "under_review", "resolved", "withdrawn"]
        for index in range(10):
            profile = profiles[(index * 7 + 3) % len(profiles)]
            contribution = contributions[index % len(contributions)] if contributions else None
            status = statuses[index % len(statuses)]
            session.add(
                Appeal(
                    contribution_id=contribution.id if contribution else None,
                    student_id=profile.id,
                    statement=(
                        "The contribution summary understates the work I did on the data model. "
                        "I have attached the review notes as context."
                    ),
                    status=status,
                    resolution=(
                        "Reviewed with the mentor; the summary was corrected."
                        if status == "resolved"
                        else ""
                    ),
                    resolved_by=mentor_users[0].id if status == "resolved" else None,
                    resolved_at=utcnow() if status == "resolved" else None,
                )
            )
    session.flush()


def _seed_notifications(session: Session, mentors: list[Mentor]) -> None:
    if session.scalar(select(func.count(Notification.id))):
        return
    student = session.scalar(
        select(User).where(User.email == "aarohi.sharma@indulink.example.test")
    )
    industry = session.scalar(
        select(User).where(User.email == "kabir.malhotra@indulink.example.test")
    )
    admin = session.scalar(select(User).where(User.email == "meera.joshi@indulink.example.test"))
    mentor_user = mentors[0].user
    notifications = [
        (student, "match", "Two challenges match your evidence", "Smart-grid and fraud screening both list your confirmed Python and SQL levels.", "/student/projects"),
        (student, "evidence", "One evidence item is awaiting review", "Your statistics evidence is still provisional until a reviewer decides.", "/student/evidence"),
        (industry, "candidates", "Candidate ranking is ready", "The smart-grid challenge has a ranked shortlist with a Why panel for each candidate.", "/industry/projects/1/candidates"),
        (mentor_user, "request", "A mentor request needs your decision", "A project has been matched to you and is waiting for confirmation.", "/mentor/requests"),
        (admin, "system", "Synthetic demo data loaded", "Every person here is fictional and every record is labelled synthetic.", "/admin"),
    ]
    for user, kind, title, body, href in notifications:
        if user is None:
            continue
        session.add(
            Notification(
                user_id=user.id,
                kind=kind,
                title=title,
                body=body,
                href=href,
                is_synthetic=True,
            )
        )
    session.flush()


def _seed_snapshots(session: Session) -> None:
    """Record what was recommended for the demo projects, including before an edit."""
    try:
        from services import matching
    except Exception:  # pragma: no cover - services are always importable in the app
        return

    # A project whose requirements changed keeps the ranking made under v1, so
    # the screen can show what the edit did to the shortlist.
    changed = session.scalars(
        select(ProjectVersion)
        .where(ProjectVersion.version_no > 1, ProjectVersion.is_current.is_(True))
    ).all()
    for current in changed:
        previous = session.scalar(
            select(ProjectVersion).where(
                ProjectVersion.project_id == current.project_id,
                ProjectVersion.version_no == current.version_no - 1,
            )
        )
        project = session.get(Project, current.project_id)
        if previous is None or project is None:
            continue
        exists = session.scalar(
            select(RecommendationSnapshot.id).where(
                RecommendationSnapshot.project_id == project.id,
                RecommendationSnapshot.version_id == previous.id,
            )
        )
        if exists:
            continue
        matching.record_snapshot(
            session, project, previous, generated_at=previous.created_at
        )
    matching.refresh_snapshots(session, limit=6)


def seed(session: Session, *, settings: Settings | None = None) -> dict:
    """Populate the database. Safe to run repeatedly."""
    settings = settings or Settings.load()
    ensure_seed_allowed(settings)

    rng = random.Random(RANDOM_SEED)
    today = date.today()
    password_hash = authentication.hash_password(DEMO_PASSWORD, rounds=settings.bcrypt_rounds)

    skills = _ensure_skills(session)

    admins = [
        _user(
            session,
            email=(
                "meera.joshi@indulink.example.test"
                if index == 0
                else email_for(name, prefix="admin.")
            ),
            name=name,
            role="admin",
            password_hash=password_hash,
        )
        for index, name in enumerate(ADMIN_NAMES)
    ]

    industry_users: list[User] = []
    for index, name in enumerate(INDUSTRY_NAMES):
        company = COMPANIES[index % len(COMPANIES)]
        email = (
            "kabir.malhotra@indulink.example.test"
            if index == 0
            else email_for(name, prefix="partner.")
        )
        user = _user(
            session,
            email=email,
            name=f"{name} · {company}",
            role="industry",
            password_hash=password_hash,
        )
        industry_users.append(user)

    profiles = _seed_students(session, rng, skills, password_hash, today)
    projects = _seed_projects(session, industry_users, skills, today)
    _ensure_project_coverage(session, projects, skills, profiles, today)
    mentors = _seed_mentors(session, skills, password_hash, today)
    _seed_assignments(session, projects, mentors, today)
    teams = _seed_teams(session, projects, profiles, rng)
    _seed_outcomes(
        session,
        projects,
        profiles,
        industry_users,
        mentors,
        teams,
        today,
    )
    _seed_notifications(session, mentors)
    session.flush()
    _seed_snapshots(session)
    session.flush()

    engagement = (
        (session.scalar(select(func.count(Evaluation.id))) or 0)
        + (session.scalar(select(func.count(Feedback.id))) or 0)
        + (session.scalar(select(func.count(Appeal.id))) or 0)
    )
    return {
        "seed_version": SEED_VERSION,
        "users": session.scalar(select(func.count(User.id))) or 0,
        "students": session.scalar(select(func.count(StudentProfile.id))) or 0,
        "mentors": session.scalar(
            select(func.count(Mentor.id)).where(Mentor.domains.not_like("Faculty reviewer%"))
        )
        or 0,
        "faculty": session.scalar(
            select(func.count(Mentor.id)).where(Mentor.domains.like("Faculty reviewer%"))
        )
        or 0,
        "projects": session.scalar(select(func.count(Project.id))) or 0,
        "teams": session.scalar(select(func.count(Team.id))) or 0,
        "evidence": session.scalar(select(func.count(Evidence.id))) or 0,
        "passports": session.scalar(select(func.count(PassportEntry.id))) or 0,
        "engagement": engagement,
        "admin": admins[0].email,
    }


def main() -> None:  # pragma: no cover - convenience entry point
    from data.database import Database

    settings = Settings.load()
    ensure_seed_allowed(settings)
    database = Database(settings)
    with database.session() as session:
        counts = seed(session, settings=settings)
    print("Synthetic demo data ready:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"\nDemo sign-in (all roles): {DEMO_PASSWORD}")


if __name__ == "__main__":  # pragma: no cover
    main()
