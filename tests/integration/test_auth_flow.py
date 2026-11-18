"""Sign-up, sign-in, sessions, lockout, CSRF and response headers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from data.models import AuditEvent, User, utcnow
from services import authentication


def add_user(
    database,
    email: str,
    *,
    role: str = "student",
    password: str = "CorrectHorse9",
    status: str = "active",
) -> int:
    with database.session() as session:
        user = User(
            email=email,
            password_hash=authentication.hash_password(password, rounds=4),
            role=role,
            status=status,
            display_name="Aarav Gupta",
        )
        session.add(user)
        session.flush()
        return user.id


class TestCsrf:
    def test_sign_in_page_issues_a_signed_cookie_and_form_token(self, browser):
        response = browser.get("/sign-in")
        assert response.status_code == 200
        raw = browser.client.cookies.get("indulink_csrf")
        assert raw and "." in raw
        assert browser.csrf in response.text

    def test_post_without_token_is_rejected(self, browser, database):
        add_user(database, "noc@test.demo")
        browser.get("/sign-in")
        response = browser.post(
            "/sign-in", {"email": "noc@test.demo", "password": "CorrectHorse9"}, with_token=False
        )
        assert response.status_code == 403
        assert "expired or came from another site" in response.text

    def test_post_with_wrong_token_is_rejected(self, browser, database):
        add_user(database, "wrong@test.demo")
        browser.get("/sign-in")
        response = browser.post(
            "/sign-in",
            {"email": "wrong@test.demo", "password": "CorrectHorse9", "csrf_token": "nope"},
        )
        assert response.status_code == 403

    def test_csrf_rejection_is_audited(self, browser, database):
        browser.get("/sign-in")
        browser.post("/sign-in", {"email": "a@b.co", "password": "x"}, with_token=False)
        with database.engine.connect() as conn:
            actions = [row[0] for row in conn.execute(text("SELECT action FROM audit_events"))]
        assert "security.csrf_rejected" in actions


class TestRegistration:
    def test_register_creates_an_active_account_and_signs_in(self, browser, database):
        browser.get("/register")
        response = browser.post(
            "/register",
            {
                "display_name": "New Student",
                "email": "new.student@test.demo",
                "password": "a-long-password-1",
                "confirm_password": "a-long-password-1",
                "role": "student",
                "accepted_terms": "yes",
            },
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/student"
        assert browser.client.cookies.get("indulink_session")

        with database.session() as session:
            user = session.scalar(select(User).where(User.email == "new.student@test.demo"))
            assert user is not None
            assert user.status == "active"
            assert user.password_hash.startswith("$2b$")
            assert "a-long-password-1" not in user.password_hash

        assert browser.get("/student").status_code == 200

    def test_register_refuses_weak_password(self, browser, database):
        browser.get("/register")
        response = browser.post(
            "/register",
            {
                "display_name": "Weak",
                "email": "weak@test.demo",
                "password": "short",
                "confirm_password": "short",
                "role": "student",
                "accepted_terms": "yes",
            },
        )
        assert response.status_code == 422
        assert "at least 10" in response.text

    def test_register_refuses_mismatched_passwords(self, browser):
        browser.get("/register")
        response = browser.post(
            "/register",
            {
                "display_name": "Mismatch",
                "email": "mismatch@test.demo",
                "password": "a-long-password-1",
                "confirm_password": "a-long-password-2",
                "role": "student",
                "accepted_terms": "yes",
            },
        )
        assert response.status_code == 422

    def test_register_refuses_terms_not_accepted(self, browser):
        browser.get("/register")
        response = browser.post(
            "/register",
            {
                "display_name": "No terms",
                "email": "noterms@test.demo",
                "password": "a-long-password-1",
                "confirm_password": "a-long-password-1",
                "role": "student",
            },
        )
        assert response.status_code == 422

    def test_register_refuses_admin_role(self, database, app):
        from services import authentication as auth
        from services.errors import ValidationError

        with database.session() as session:
            with pytest.raises(ValidationError):
                auth.register_user(
                    session,
                    email="sneaky@test.demo",
                    password="a-long-password-1",
                    role="admin",
                    display_name="Manav Bhatia",
                    settings=app.state.settings,
                )
            session.rollback()


class TestSignIn:
    def test_successful_sign_in_redirects_to_the_workspace(self, browser, database):
        add_user(database, "ok@test.demo")
        response = browser.sign_in("ok@test.demo", "CorrectHorse9")
        assert response.status_code == 303
        assert response.headers["location"] == "/student"
        assert browser.get("/student").status_code == 200

    def test_unknown_email_and_wrong_password_are_indistinguishable(self, browser, database):
        add_user(database, "known@test.demo")
        unknown = browser.sign_in("ghost@test.demo", "CorrectHorse9")
        browser.get("/sign-in")
        wrong = browser.post(
            "/sign-in", {"email": "known@test.demo", "password": "WrongPassword1"}
        )
        assert unknown.status_code == wrong.status_code == 400
        assert "E-mail or password not recognised" in unknown.text
        assert "E-mail or password not recognised" in wrong.text
        assert "ghost@test.demo" not in unknown.text

    def test_lockout_after_repeated_failures(self, browser, database, app):
        add_user(database, "lock@test.demo")
        browser.get("/sign-in")
        for _ in range(app.state.settings.signin_max_attempts):
            browser.post("/sign-in", {"email": "lock@test.demo", "password": "WrongPassword1"})
        blocked = browser.post("/sign-in", {"email": "lock@test.demo", "password": "CorrectHorse9"})
        assert blocked.status_code == 400
        assert "Too many attempts" in blocked.text

    def test_suspended_account_cannot_sign_in(self, browser, database):
        add_user(database, "suspended@test.demo", status="suspended")
        response = browser.sign_in("suspended@test.demo", "CorrectHorse9")
        assert response.status_code == 400
        assert "not active" in response.text

    def test_suspension_takes_effect_on_the_next_request(self, browser, database):
        user_id = add_user(database, "later@test.demo")
        browser.sign_in("later@test.demo", "CorrectHorse9")
        assert browser.get("/student").status_code == 200
        with database.session() as session:
            session.get(User, user_id).status = "suspended"
        assert browser.get("/student").status_code == 303

    def test_next_parameter_only_accepts_internal_paths(self, browser, database):
        add_user(database, "next@test.demo")
        response = browser.sign_in("next@test.demo", "CorrectHorse9", next_url="https://evil.example")
        assert response.headers["location"] == "/student"

    def test_next_parameter_is_honoured_when_internal(self, browser, database):
        add_user(database, "next2@test.demo")
        response = browser.sign_in("next2@test.demo", "CorrectHorse9", next_url="/student/evidence")
        assert response.headers["location"] == "/student/evidence"


class TestSessions:
    def test_cookie_flags(self, browser, database):
        add_user(database, "cookie@test.demo")
        response = browser.sign_in("cookie@test.demo", "CorrectHorse9")
        cookie_header = response.headers.get("set-cookie", "")
        assert "HttpOnly" in cookie_header
        assert "SameSite=lax" in cookie_header or "SameSite=Lax" in cookie_header

    def test_sign_out_revokes_the_session(self, browser, database):
        add_user(database, "out@test.demo")
        browser.sign_in("out@test.demo", "CorrectHorse9")
        assert browser.get("/student").status_code == 200
        response = browser.post("/sign-out")
        assert response.status_code == 303
        assert browser.get("/student").status_code == 303

    def test_expired_session_is_refused(self, browser, database, app):
        add_user(database, "expiry@test.demo")
        browser.sign_in("expiry@test.demo", "CorrectHorse9")
        with database.session() as session:
            session.execute(
                text("UPDATE sessions SET expires_at = :past"),
                {"past": (utcnow() - timedelta(days=1)).isoformat(sep=" ")},
            )
        assert browser.get("/student").status_code == 303

    def test_revoked_session_is_refused(self, browser, database):
        add_user(database, "revoked@test.demo")
        browser.sign_in("revoked@test.demo", "CorrectHorse9")
        with database.session() as session:
            session.execute(
                text("UPDATE sessions SET revoked_at = :now"),
                {"now": utcnow().isoformat(sep=" ")},
            )
        assert browser.get("/student").status_code == 303

    def test_password_change_revokes_other_sessions(self, database, app):
        user_id = add_user(database, "change@test.demo")
        settings = app.state.settings
        with database.session() as session:
            user = session.get(User, user_id)
            token = authentication.create_session(session, user=user, settings=settings)
            assert authentication.resolve_session(session, token=token, settings=settings)
            authentication.change_password(
                session,
                user=user,
                current_password="CorrectHorse9",
                new_password="a-brand-new-password-2",
                settings=settings,
            )
            assert authentication.resolve_session(session, token=token, settings=settings) is None

    def test_only_a_hash_of_the_token_is_stored(self, database, app):
        user_id = add_user(database, "hash@test.demo")
        with database.session() as session:
            user = session.get(User, user_id)
            token = authentication.create_session(session, user=user, settings=app.state.settings)
        with database.engine.connect() as conn:
            stored = conn.execute(text("SELECT id FROM sessions")).scalar()
        assert stored != token
        assert len(stored) == 64


class TestHeaders:
    def test_security_headers_present(self, browser):
        response = browser.get("/")
        headers = {key.lower(): value for key, value in response.headers.items()}
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]

    def test_csp_allows_no_inline_scripts(self, browser):
        csp = browser.get("/").headers["content-security-policy"]
        script_directive = [
            part for part in csp.split(";") if part.strip().startswith("script-src")
        ][0]
        assert "unsafe-inline" not in script_directive
        assert "self" in script_directive

    def test_pages_are_not_cached(self, browser):
        assert browser.get("/sign-in").headers["cache-control"] == "no-store"
