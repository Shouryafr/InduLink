# Residual risks and open items

Honest register for the current build. Anything listed here is genuinely open; it
is not "in progress". Fixed defects are in [DEFECTS.md](DEFECTS.md).

## What is not proven

| Area | Status | Consequence |
| --- | --- | --- |
| `services/` coverage | 69% against an 85% target | Several service behaviours are exercised only through screens and route guards. The partial features are listed in [FEATURES.md](FEATURES.md). |
| PostgreSQL | Not run | The suite runs on SQLite. Both partial indexes carry `postgresql_where`, and no SQLite-only SQL is used in services, but a PostgreSQL CI run is unverified. |
| Automated accessibility (axe) | Not run | Structural checks plus a keyboard pass only. Not a WCAG 2.2 AA audit. |
| Playwright journeys | Not run | The journeys are covered by `tests/scenarios`, which drive real routes through the ASGI test client rather than a browser. |
| Responsive verification | By inspection | Breakpoints and stacking rules are in CSS and checked at 360/768/1024/1440 in a browser, but no screenshot-diff suite guards them. |
| Demo reset | Measured, not unit-tested | 1.52 s measured; the route works, but no assertion fails if it regresses. |

## Product and data risks in what is built

- **Synthetic data only.** 60 students, 17 projects, 13 mentors. Behaviour can be
  demonstrated; effectiveness cannot. Seeded figures must never be presented as
  findings.
- **No real e-mail delivery.** Password help is a screen; there is no reset token
  flow, so an account cannot actually recover a password. Self-service reset needs
  a mail provider and a single-use token table.
- **Sign-in throttling is per account, not per IP.** Credential stuffing spread
  across many addresses is slowed only by bcrypt cost.
- **Search is a `LIKE` filter.** No ranking, no full-text index, no pagination
  performance work.
- **Single-writer SQLite.** WAL plus a busy timeout covers the demo and tests.
  Concurrent writers across processes will contend; a deployment needing real
  concurrency should move to PostgreSQL.
- **Copy is unapproved.** Privacy and terms describe intended behaviour; they are
  drafts for institutional review, not legal text.
- **No e-mail verification.** Registration accepts any well-formed address, so an
  address can be claimed by whoever types it first.
- **Rate limiting beyond sign-in is absent.** Registration and evidence submission
  have no throttle.
- **Audit log growth.** Append-only and never pruned, so it grows without bound.
  Retention and export policy are not implemented.
- **`make` is not present on every Windows machine.** The README lists the
  equivalent commands; CI should use the Makefile.
- **No file uploads.** The prototype deliberately has no uploaded-code execution
  and no attachment pipeline; evidence is described, not attached. The upload rules
  in the original brief therefore remain unimplemented rather than half-built.

## Security items deliberately deferred

- Multi-factor authentication.
- CSP reporting endpoint and violation monitoring.
- Encryption at rest beyond the host filesystem.
- Secrets rotation procedure.

## Claims deliberately absent

The interface never claims prediction, guarantee, compliance, certification or a
breach-proof system. A test fails the suite if such copy appears without a
qualifier (`tests/integration/test_copy_and_ribbon.py`). Terms and privacy text
stay as drafts "to be confirmed by your institution".
