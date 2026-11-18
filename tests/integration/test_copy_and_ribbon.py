"""Copy audit and the synthetic-data ribbon.

Two rules from the brief that are easy to regress:

* the interface never claims prediction, guarantee, compliance, certification or
  a breach-proof system;
* the synthetic-data ribbon appears on every demo page and nowhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import Browser, make_settings

ROOT = Path(__file__).resolve().parent.parent.parent
TEXT_FILES = [
    *ROOT.glob("app/templates/**/*.html"),
    *ROOT.glob("app/routers/*.py"),
    *ROOT.glob("services/*.py"),
    ROOT / "README.md",
    ROOT / "RISKS.md",
]

BANNED = [
    r"\bmachine[\s-]?learning\b",
    r"\bguarantee(s|d)?\b",
    r"\bpredict(s|ed|ing)? (success|performance|outcomes?)\b",
    r"\bforecast(s|ed|ing)?\b",
    r"\bzero loopholes\b",
]

#: Words that are only a problem when unqualified. Each noun after the word is
#: a specific, checkable technical claim rather than a marketing one.
QUALIFIED = {
    "secure": (
        "coding",
        "cookie",
        "cookies",
        "headers",
        "session",
        "transport",
        "by design",
        "storage",
        "context",
    ),
    "compliant": ("with",),
    "certified": (),
}


NEGATIONS = (
    "no ",
    "not ",
    "never ",
    "cannot ",
    "can't ",
    "nothing ",
    "without ",
    "nor ",
    "isn't ",
    "doesn't ",
    "don't ",
    "neither ",
    "non-",
)


def _negated(lowered: str, start: int) -> bool:
    """True when the hit sits in a sentence that denies the claim.

    A disclaimer ("decision support, not a guarantee") is the copy the brief
    asks for; only an unqualified claim is a violation.
    """
    window = lowered[max(0, start - 80) : start]
    window = window.split(".")[-1]
    return any(token in window for token in NEGATIONS)


def _offenders(text: str, *, check_qualified: bool = True) -> list[str]:
    # Markdown emphasis and inline code carry no claim of their own: `Secure` is
    # the cookie attribute's name, not a marketing adjective.
    lowered = re.sub(r"`[^`\n]*`", " ", text.casefold().replace("**", ""))
    found: list[str] = []
    for phrase in BANNED:
        for match in re.finditer(phrase, lowered):
            if not _negated(lowered, match.start()):
                found.append(phrase)
                break
    if not check_qualified:
        # Code identifiers such as ``secure=settings.cookie_secure`` are not copy.
        return found
    for word, allowed in QUALIFIED.items():
        for match in re.finditer(rf"\b{word}\b(?:\s+(\w+))?", lowered):
            follower = (match.group(1) or "").strip()
            if follower in allowed:
                continue
            if _negated(lowered, match.start()):
                continue
            found.append(f"{word} (unqualified)")
            break
    return found


def test_no_banned_claims_in_templates_or_docs():
    violations: list[str] = []
    for path in TEXT_FILES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # The audit explains the rule; it is allowed to quote the phrases.
        if path.name == "test_copy_and_ribbon.py":
            continue
        for phrase in _offenders(text, check_qualified=path.suffix != ".py"):
            violations.append(f"{path.relative_to(ROOT)}: {phrase}")
    assert not violations, "\n".join(violations)


def test_ribbon_is_on_every_demo_page(seeded_app):
    public = ["/", "/how-it-works", "/how-recommendations-work", "/sign-in", "/register"]
    with TestClient(seeded_app) as client:
        page = Browser(client)
        for path in public:
            response = page.get(path)
            assert response.status_code == 200, path
            assert "synthetic-ribbon" in response.text, f"{path} has no ribbon"

        page.sign_in("aarohi.sharma@indulink.example.test")
        for path in ["/student", "/student/evidence", "/notifications"]:
            response = page.get(path)
            assert response.status_code == 200, path
            assert "synthetic-ribbon" in response.text, f"{path} has no ribbon"


def test_ribbon_is_absent_when_demo_mode_is_off(db_path):
    from app.main import create_app

    application = create_app(make_settings(db_path, demo_mode=False))
    try:
        with TestClient(application) as client:
            response = Browser(client).get("/")
        assert response.status_code == 200
        assert "synthetic-ribbon" not in response.text
        assert "Demo sign-in" not in response.text
    finally:
        application.state.db.engine.dispose()
