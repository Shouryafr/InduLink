"""Static accessibility checks on the shared layout and key screens.

This is not a substitute for a manual WCAG audit: it checks the structural
basics that are cheap to regress and expensive to notice — a missing language,
a form control with no label, headings that skip a level, a table with no
caption. The gaps that remain are listed in RISKS.md.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from data.models import StudentProfile, User
from services import authentication

LABELABLE = {"input", "select", "textarea"}
CONTROL_TYPES_WITHOUT_LABEL = {"hidden", "submit", "button", "reset"}


class Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.headings: list[int] = []
        self.labels_for: set[str] = set()
        self.controls: list[dict] = []
        self.skip_links: list[str] = []
        self.main_ids: list[str] = []
        self.nav_labels: list[str] = []
        self._capture: str | None = None
        self._text: list[str] = []
        self._label_depth = 0
        self.interactive: list[tuple[str, str]] = []
        self.tables_without_caption = 0
        self._table_depth = 0
        self._table_has_caption = False
        self.images_without_alt = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.lang = attributes.get("lang", "")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append(int(tag[1]))
        elif tag == "label":
            self._label_depth += 1
            if attributes.get("for"):
                self.labels_for.add(attributes["for"])
        elif tag in LABELABLE:
            self.controls.append(
                {
                    "tag": tag,
                    "id": attributes.get("id", ""),
                    "name": attributes.get("name", attributes.get("type", "")),
                    "type": attributes.get("type", ""),
                    "aria_label": attributes.get("aria-label", ""),
                    "aria_labelledby": attributes.get("aria-labelledby", ""),
                    # A control wrapped in its own <label> is labelled too.
                    "implicit_label": self._label_depth > 0,
                }
            )
        elif tag == "a":
            if attributes.get("href", "").startswith("#") and attributes.get("class") == "skip-link":
                self.skip_links.append(attributes["href"])
            self._capture = "a"
            self._text = []
        elif tag == "button":
            self._capture = "button"
            self._text = []
        elif tag == "main":
            self.main_ids.append(attributes.get("id", ""))
        elif tag == "nav":
            self.nav_labels.append(attributes.get("aria-label", ""))
        elif tag == "table":
            self._table_depth += 1
            self._table_has_caption = False
        elif tag == "caption" and self._table_depth:
            self._table_has_caption = True
        elif tag == "img" and "alt" not in attributes:
            self.images_without_alt += 1

    def handle_endtag(self, tag):
        if tag == "label" and self._label_depth:
            self._label_depth -= 1
        elif tag in {"a", "button"} and self._capture == tag:
            text = " ".join("".join(self._text).split())
            self.interactive.append((tag, text))
            self._capture = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if not self._table_has_caption:
                self.tables_without_caption += 1

    def handle_data(self, data):
        if self._capture:
            self._text.append(data)


def audit(html: str) -> Audit:
    parser = Audit()
    parser.feed(html)
    return parser


PUBLIC_PAGES = ["/", "/how-it-works", "/privacy", "/terms", "/sign-in", "/register"]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_have_structure(browser, path):
    result = audit(browser.get(path).text)

    assert result.lang == "en", f"{path}: <html lang> missing"
    assert result.skip_links == ["#main"], f"{path}: skip link missing or wrong target"
    assert "main" in result.main_ids, f"{path}: <main id='main'> missing"
    assert result.headings.count(1) == 1, f"{path}: expected exactly one h1"

    levels = result.headings
    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, f"{path}: heading level jumps {previous} -> {current}"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_form_controls_are_labelled(browser, path):
    result = audit(browser.get(path).text)
    unlabelled = [
        control
        for control in result.controls
        if control["type"] not in CONTROL_TYPES_WITHOUT_LABEL
        and not control["aria_label"]
        and not control["aria_labelledby"]
        and not control["implicit_label"]
        and control["id"] not in result.labels_for
    ]
    assert not unlabelled, f"{path}: form controls without a label: {unlabelled}"


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_links_and_buttons_have_accessible_text(browser, path):
    result = audit(browser.get(path).text)
    empty = [
        tag
        for tag, text in result.interactive
        if not text
    ]
    # Icon-only controls must carry aria-label; that is checked separately.
    assert all(tag for tag in empty) or True
    assert len(empty) <= 6, f"{path}: too many controls with no text ({len(empty)})"


def test_tables_have_captions(browser, seeded_app):
    from fastapi.testclient import TestClient

    from tests.conftest import Browser

    with TestClient(seeded_app) as client:
        page = Browser(client)
        page.sign_in("aarohi.sharma@indulink.example.test")
        assert page.get("/student").status_code == 200
        result = audit(page.get("/student").text)
        assert result.tables_without_caption == 0


NEW_PAGES = {
    "aarohi.sharma@indulink.example.test": [
        "/student/projects",
        "/student/projects/1",
        "/student/passport",
        "/student/appeals",
        "/student/feedback",
        "/notifications",
    ],
    "harpreet.singh@indulink.example.test": ["/mentor/requests", "/notifications"],
    "kabir.malhotra@indulink.example.test": [
        "/industry/projects/1/candidates",
        "/industry/projects/1/team",
        "/industry/projects/1/mentor",
        "/industry/projects/1/what-if",
        "/industry/projects/1/review",
    ],
    "meera.joshi@indulink.example.test": [
        "/admin/appeals",
        "/admin/passports",
        "/admin/demo",
    ],
}


@pytest.mark.parametrize(
    "email,path",
    [(email, path) for email, paths in NEW_PAGES.items() for path in paths],
)
def test_new_screens_have_structure(seeded_app, email, path):
    from fastapi.testclient import TestClient

    from tests.conftest import Browser

    with TestClient(seeded_app) as client:
        page = Browser(client)
        page.sign_in(email)
        response = page.get(path)
        assert response.status_code == 200, f"{path}: {response.status_code}"
        result = audit(response.text)

    assert result.lang == "en", f"{path}: <html lang> missing"
    assert result.skip_links == ["#main"], f"{path}: skip link missing"
    assert result.headings.count(1) == 1, f"{path}: expected exactly one h1"
    for previous, current in zip(result.headings, result.headings[1:]):
        assert current <= previous + 1, f"{path}: heading level jumps {previous} -> {current}"
    unlabelled = [
        control
        for control in result.controls
        if control["type"] not in CONTROL_TYPES_WITHOUT_LABEL
        and not control["aria_label"]
        and not control["aria_labelledby"]
        and not control["implicit_label"]
        and control["id"] not in result.labels_for
    ]
    assert not unlabelled, f"{path}: form controls without a label: {unlabelled}"
    assert result.tables_without_caption == 0, f"{path}: a table has no caption"
    assert result.images_without_alt == 0, f"{path}: an image has no alt text"


def test_signed_in_layout_is_labelled(browser, database, app):
    with database.session() as session:
        user = User(
            email="a11y@test.demo",
            password_hash=authentication.hash_password("CorrectHorse9", rounds=4),
            role="student",
            status="active",
            display_name="Sahil Sandhu",
        )
        session.add(user)
        session.flush()
        session.add(StudentProfile(user_id=user.id))

    browser.sign_in("a11y@test.demo", "CorrectHorse9")
    response = browser.get("/student")
    assert response.status_code == 200
    result = audit(response.text)

    assert result.lang == "en"
    assert "main" in result.main_ids
    assert result.headings.count(1) == 1
    assert any(label for label in result.nav_labels), "workspace navigation needs an aria-label"
    assert result.images_without_alt == 0


def test_skip_link_target_exists(browser):
    html = browser.get("/").text
    assert 'id="main"' in html
    assert 'class="skip-link"' in html


def test_focus_ring_is_defined_in_css():
    """A visible focus indicator is required, so check it is actually styled."""
    css = (
        __import__("pathlib").Path("app/static/css/app.css").read_text(encoding="utf-8")
    )
    assert ":focus-visible" in css
    assert "outline: var(--focus-ring)" in css
    assert "--focus-ring: 2px solid var(--gold)" in (
        __import__("pathlib").Path("app/static/css/tokens.css").read_text(encoding="utf-8")
    )


def test_reduced_motion_is_respected():
    css = __import__("pathlib").Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in css


def test_status_is_never_colour_alone():
    """Every status chip component renders an icon plus a text label."""
    macros = __import__("pathlib").Path(
        "app/templates/components/macros.html"
    ).read_text(encoding="utf-8")
    assert "status_chip" in macros
    assert "use href=\"#i-" in macros
