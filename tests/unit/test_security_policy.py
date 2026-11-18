"""Password policy, e-mail normalisation, CSRF signing and lockout arithmetic."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import security_policy


class TestEmail:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  Aarohi.Sharma@InduLink.Example.TEST ", "aarohi.sharma@indulink.example.test"),
            ("a@b.co", "a@b.co"),
        ],
    )
    def test_normalises(self, raw, expected):
        assert security_policy.normalize_email(raw) == expected

    @pytest.mark.parametrize("raw", ["", "no-at-sign", "a@b", "a b@c.com", "@c.com"])
    def test_rejects_invalid(self, raw):
        assert security_policy.is_valid_email(raw) is False

    def test_accepts_reasonable_address(self):
        assert security_policy.is_valid_email("ada@example.edu") is True


class TestPasswordPolicy:
    def test_rejects_short(self):
        errors = security_policy.password_policy_errors("Ab1!short")
        assert any("at least 10" in message for message in errors)

    def test_accepts_reasonable(self):
        assert security_policy.password_policy_errors("correct-horse-9") == []

    def test_rejects_repeated_character(self):
        errors = security_policy.password_policy_errors("aaaaaaaaaaaa")
        assert any("repeat" in message for message in errors)

    def test_rejects_email_as_password(self):
        errors = security_policy.password_policy_errors(
            "ada.lovelace@example.com", "ada.lovelace@example.com"
        )
        assert any("e-mail" in message for message in errors)

    def test_rejects_whitespace_only(self):
        errors = security_policy.password_policy_errors("            ")
        assert any("spaces" in message for message in errors)

    def test_rejects_overlong(self):
        errors = security_policy.password_policy_errors("x1" * 200)
        assert any("at most" in message for message in errors)


class TestCsrf:
    def test_roundtrip(self):
        nonce = security_policy.new_csrf_nonce()
        signed = security_policy.sign_csrf("k" * 40, nonce)
        assert security_policy.verify_signed_csrf("k" * 40, signed) == nonce

    def test_wrong_secret_rejected(self):
        signed = security_policy.sign_csrf("k" * 40, "nonce")
        assert security_policy.verify_signed_csrf("z" * 40, signed) is None

    def test_tampered_nonce_rejected(self):
        signed = security_policy.sign_csrf("k" * 40, "nonce")
        _, _, signature = signed.rpartition(".")
        assert security_policy.verify_signed_csrf("k" * 40, f"other.{signature}") is None

    @pytest.mark.parametrize("value", ["", "garbage", "no-dot"])
    def test_malformed_rejected(self, value):
        assert security_policy.verify_signed_csrf("k" * 40, value) is None

    def test_nonce_is_unique(self):
        assert security_policy.new_csrf_nonce() != security_policy.new_csrf_nonce()

    def test_constant_time_equals(self):
        assert security_policy.constant_time_equals("abc", "abc") is True
        assert security_policy.constant_time_equals("abc", "abd") is False


class TestLockout:
    NOW = datetime(2026, 10, 28, 12, 0, 0)

    def test_not_locked_below_threshold(self):
        assert (
            security_policy.is_locked_out(
                4,
                self.NOW,
                max_attempts=5,
                lockout=timedelta(minutes=15),
                now=self.NOW,
            )
            is False
        )

    def test_locked_at_threshold(self):
        assert (
            security_policy.is_locked_out(
                5,
                self.NOW,
                max_attempts=5,
                lockout=timedelta(minutes=15),
                now=self.NOW,
            )
            is True
        )

    def test_expires_after_window(self):
        stale = self.NOW - timedelta(minutes=16)
        assert (
            security_policy.is_locked_out(
                5, stale, max_attempts=5, lockout=timedelta(minutes=15), now=self.NOW
            )
            is False
        )

    def test_no_previous_failure_is_not_locked(self):
        assert (
            security_policy.is_locked_out(
                9, None, max_attempts=5, lockout=timedelta(minutes=15), now=self.NOW
            )
            is False
        )


class TestEdgeInputs:
    """Boundary inputs the forms can send that the happy paths never reach."""

    def test_missing_email_normalises_to_empty(self):
        assert security_policy.normalize_email(None) == ""
        assert security_policy.is_valid_email(None) is False

    def test_overlong_email_is_rejected_not_truncated(self):
        overlong = "a" * 300 + "@example.com"
        assert security_policy.normalize_email(overlong) == ""
        assert security_policy.is_valid_email(overlong) is False

    @pytest.mark.parametrize("password", [None, 12345678901])
    def test_non_string_password_is_refused(self, password):
        assert security_policy.password_policy_errors(password) == ["Enter a password."]

    def test_sign_csrf_requires_a_secret(self):
        with pytest.raises(ValueError):
            security_policy.sign_csrf("", "nonce")

    def test_sign_csrf_requires_a_nonce(self):
        with pytest.raises(ValueError):
            security_policy.sign_csrf("k" * 40, "")

    @pytest.mark.parametrize("value", ["nonce.", ".signature", "."])
    def test_signed_value_without_both_halves_is_refused(self, value):
        assert security_policy.verify_signed_csrf("k" * 40, value) is None

    def test_no_secret_cannot_verify(self):
        signed = security_policy.sign_csrf("k" * 40, "nonce")
        assert security_policy.verify_signed_csrf("", signed) is None

    def test_constant_time_equals_handles_missing_values(self):
        assert security_policy.constant_time_equals(None, "abc") is False
        assert security_policy.constant_time_equals("abc", None) is False

    def test_lockout_expiry_is_last_failure_plus_window(self):
        failed_at = datetime(2026, 10, 28, 12, 0, 0)
        assert security_policy.lockout_expiry(
            failed_at, lockout=timedelta(minutes=15)
        ) == failed_at + timedelta(minutes=15)
