"""The synthetic demo people, in one place.

Every name here comes from the pools in the Prototype Completion Brief section 3.
Sikh first names use Kaur (women) and Singh (men); all other names use the
ordinary surname pool. Emails use the reserved domain ``indulink.example.test``,
so no message can ever reach a real address. Nothing in this module is used by
scoring or eligibility: names are display text only.
"""

from __future__ import annotations

from dataclasses import dataclass

DEMO_PASSWORD = "DemoPass!2026"
EMAIL_DOMAIN = "indulink.example.test"

FIRST_NAMES_WOMEN: tuple[str, ...] = (
    "Aarohi",
    "Anjali",
    "Diya",
    "Harleen",
    "Ishita",
    "Jasleen",
    "Kavya",
    "Manpreet",
    "Meera",
    "Muskan",
    "Naina",
    "Neha",
    "Pooja",
    "Priya",
    "Ritika",
    "Riya",
    "Sakshi",
    "Simran",
    "Tanvi",
    "Gurleen",
)

FIRST_NAMES_MEN: tuple[str, ...] = (
    "Aarav",
    "Amandeep",
    "Ankit",
    "Arjun",
    "Dev",
    "Gurpreet",
    "Harpreet",
    "Harsh",
    "Jaspreet",
    "Kabir",
    "Karan",
    "Lakshya",
    "Manav",
    "Mohit",
    "Nikhil",
    "Rohan",
    "Sahil",
    "Tushar",
    "Vikram",
    "Yash",
)

SURNAMES: tuple[str, ...] = (
    "Arora",
    "Bhatia",
    "Bisht",
    "Chauhan",
    "Chopra",
    "Dhillon",
    "Gill",
    "Gupta",
    "Joshi",
    "Kapoor",
    "Khanna",
    "Malhotra",
    "Mehra",
    "Mishra",
    "Pandey",
    "Rathore",
    "Rawat",
    "Saxena",
    "Sandhu",
    "Sethi",
    "Shekhawat",
    "Sharma",
    "Singh",
    "Tandon",
    "Thakur",
    "Tiwari",
    "Verma",
    "Walia",
    "Yadav",
)

#: First names that follow Sikh naming convention.
SIKH_WOMEN: frozenset[str] = frozenset(
    {"Harleen", "Jasleen", "Manpreet", "Simran", "Gurleen"}
)
SIKH_MEN: frozenset[str] = frozenset(
    {"Amandeep", "Gurpreet", "Harpreet", "Jaspreet"}
)

TITLES: tuple[str, ...] = ("Dr", "Prof")


@dataclass(frozen=True)
class Persona:
    name: str
    email: str
    role: str
    label: str
    note: str = ""


def email_for(name: str, *, prefix: str = "") -> str:
    """Deterministic address on the reserved demo domain.

    Titles are dropped so an address never starts with "dr."; the prefix keeps
    the role visible in the demo login list.
    """
    parts = [
        part
        for part in name.replace(".", " ").split()
        if part.lower() not in {"dr", "prof"}
    ]
    clean = ".".join(part.lower().replace("'", "") for part in parts)
    while ".." in clean:
        clean = clean.replace("..", ".")
    return f"{prefix}{clean.strip('.')}@{EMAIL_DOMAIN}"


STUDENT_PERSONA = Persona(
    name="Aarohi Sharma",
    email="aarohi.sharma@indulink.example.test",
    role="student",
    label="Final-year computer science student",
    note="Contested-slot and matching demo account.",
)
MENTOR_PERSONA = Persona(
    name="Dr Harpreet Singh",
    email="harpreet.singh@indulink.example.test",
    role="mentor",
    label="Mentor, energy and data platforms",
    note="Confirms one of two students competing for the same slot.",
)
INDUSTRY_PERSONA = Persona(
    name="Kabir Malhotra",
    email="kabir.malhotra@indulink.example.test",
    role="industry",
    label="Industry partner, Northwind Grid",
    note="Owns the smart-grid challenge and its edited requirements.",
)
ADMIN_PERSONA = Persona(
    name="Meera Joshi",
    email="meera.joshi@indulink.example.test",
    role="admin",
    label="University administrator",
    note="Can reset the demo dataset and read the audit log.",
)
FACULTY_PERSONA = Persona(
    name="Prof. Neha Tandon",
    email="neha.tandon@indulink.example.test",
    role="mentor",
    label="Faculty reviewer",
    note="Reviews evidence; not available for project mentoring.",
)

DEMO_LOGINS: tuple[Persona, ...] = (
    STUDENT_PERSONA,
    MENTOR_PERSONA,
    INDUSTRY_PERSONA,
    ADMIN_PERSONA,
)

#: Names used by the deterministic generator for the non-demo cast. They are
#: listed here so the name audit, the demo script and the seed agree.
MENTOR_NAMES: tuple[str, ...] = (
    "Dr Harpreet Singh",
    "Dr Manpreet Kaur",
    "Prof Jaspreet Singh",
    "Dr Aarav Malhotra",
    "Prof Ankit Mehra",
    "Dr Arjun Mishra",
    "Prof Dev Pandey",
    "Dr Gurpreet Singh",
    "Prof Harsh Rathore",
    "Dr Jasleen Kaur",
    "Prof Lakshya Rawat",
    "Dr Mohit Saxena",
    # Security and data-platform mentor covering the display challenge's four requirements.
    "Dr Ritika Sethi",
)

FACULTY_NAMES: tuple[str, ...] = (
    "Prof. Neha Tandon",
    "Dr Anjali Sethi",
    "Prof. Rohan Shekhawat",
    "Dr Pooja Thakur",
    "Prof. Tushar Tiwari",
    "Dr Priya Verma",
)

INDUSTRY_NAMES: tuple[str, ...] = (
    "Kabir Malhotra",
    "Naina Sandhu",
    "Nikhil Walia",
    "Kavya Yadav",
)

ADMIN_NAMES: tuple[str, ...] = (
    "Meera Joshi",
    "Vikram Chauhan",
)

#: Fictional organisations. None of them is a real company.
COMPANIES: tuple[str, ...] = (
    "Northwind Grid",
    "Harbour Analytics",
    "Terrace Works",
    "Lantern Metrics",
)

#: Students whose profiles are fixed so the demo edge cases always exist.
STUDENT_SEED_NAMES: tuple[str, ...] = (
    "Aarohi Sharma",
    "Diya Arora",
    "Tanvi Bisht",
    "Ishita Chauhan",
    "Sakshi Chopra",
    "Riya Dhillon",
    "Karan Gill",
    "Naina Kapoor",
    "Neha Khanna",
    "Simran Kaur",
    # The two named candidates for the master blueprint's display scenario (section 16).
    "Anjali Mehra",
    "Dev Rathore",
)

ALL_POOL_FIRST_NAMES: frozenset[str] = frozenset(FIRST_NAMES_WOMEN) | frozenset(
    FIRST_NAMES_MEN
)
ALL_POOL_SURNAMES: frozenset[str] = frozenset(SURNAMES) | {"Kaur"}
