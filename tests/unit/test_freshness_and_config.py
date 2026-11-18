"""Freshness stamps and configuration guard rails."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from config import ConfigurationError, Settings
from core import freshness


class TestAge:
    def test_naive_datetime_treated_as_utc(self):
        now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        assert freshness.age_days(datetime(2026, 10, 26, 12), now) == 2

    def test_date_input(self):
        assert freshness.age_days(date(2026, 10, 1), datetime(2026, 10, 28, tzinfo=timezone.utc)) == 27

    def test_none(self):
        assert freshness.age_days(None) is None

    def test_future_clamps(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        assert freshness.age_days(datetime(2026, 11, 1, tzinfo=timezone.utc), now) == 0


class TestDescribe:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (0, "updated today"),
            (1, "updated yesterday"),
            (3, "updated 3 days ago"),
            (10, "updated last week"),
            (21, "updated 3 weeks ago"),
            (95, "updated 3 months ago"),
            (800, "updated 2 years ago"),
        ],
    )
    def test_phrases(self, days, expected):
        assert freshness.describe_age(days) == expected

    def test_never_updated(self):
        assert freshness.describe_age(None) == "never updated"


class TestStamp:
    def test_fresh_is_not_stale(self):
        stamp = freshness.stamp(datetime(2026, 10, 20), now=datetime(2026, 10, 28))
        assert stamp["stale"] is False
        assert stamp["tone"] == "muted"
        assert "updated" in stamp["label"]

    def test_old_is_stale_and_says_so(self):
        stamp = freshness.stamp(datetime(2026, 8, 1), now=datetime(2026, 10, 28))
        assert stamp["stale"] is True
        assert stamp["tone"] == "warn"
        assert "revalidate" in stamp["label"]

    def test_missing_value_names_the_subject(self):
        stamp = freshness.stamp(None, subject="Requirements")
        assert "Requirements never updated" in stamp["label"]

    def test_threshold_is_configurable(self):
        stamp = freshness.stamp(
            datetime(2026, 10, 20), now=datetime(2026, 10, 28), threshold_days=2
        )
        assert stamp["stale"] is True


class TestSettings:
    def test_default_bcrypt_cost_is_twelve(self):
        settings = Settings.load({"SECRET_KEY": "x" * 40, "APP_ENV": "test"})
        assert settings.bcrypt_rounds == 12

    def test_production_requires_secret(self):
        with pytest.raises(ConfigurationError):
            Settings.load({"APP_ENV": "production", "DEMO_MODE": "false"})

    def test_production_requires_long_secret(self):
        with pytest.raises(ConfigurationError):
            Settings.load(
                {"APP_ENV": "production", "SECRET_KEY": "short", "DEMO_MODE": "false"}
            )

    def test_production_refuses_weak_bcrypt_cost(self):
        with pytest.raises(ConfigurationError):
            Settings.load(
                {
                    "APP_ENV": "production",
                    "SECRET_KEY": "x" * 40,
                    "BCRYPT_ROUNDS": "4",
                    "DEMO_MODE": "false",
                }
            )

    def test_production_refuses_demo_mode(self):
        # The Production Launch Brief forbids demo logins and synthetic data in
        # production, so the app refuses to start rather than relying on a flag.
        with pytest.raises(ConfigurationError):
            Settings.load(
                {"APP_ENV": "production", "SECRET_KEY": "x" * 40, "DEMO_MODE": "true"}
            )

    def test_development_gets_an_ephemeral_secret(self):
        settings = Settings.load({"APP_ENV": "development"})
        assert len(settings.secret_key) >= 32

    def test_production_sets_secure_cookies(self):
        settings = Settings.load(
            {"APP_ENV": "production", "SECRET_KEY": "x" * 40, "DEMO_MODE": "false"}
        )
        assert settings.cookie_secure is True

    def test_database_url_default_is_sqlite(self):
        settings = Settings.load({"SECRET_KEY": "x" * 40, "APP_ENV": "test"})
        assert settings.database_url.startswith("sqlite+pysqlite")
