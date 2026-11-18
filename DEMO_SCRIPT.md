# Demo script

A guided tour of every feature, using only the seeded synthetic dataset. No step
needs anything you cannot see on screen.

**Before you start**

```bash
make demo        # migrate, seed, serve http://127.0.0.1:8000
```

If the database is already seeded, `make run` is enough. Every account below uses
the password **`DemoPass!2026`**; the sign-in page lists them while
`DEMO_MODE=true`. The ribbon at the top of every page says the data is synthetic;
that is deliberate and stays on.

For a six-minute run of show on the blueprint's canonical challenge — *Secure
Financial Transaction Monitoring System* — use [DISPLAY_GUIDE.md](DISPLAY_GUIDE.md)
section 5. This document is the longer tour that covers every feature.

---

## 1. Student — Aarohi Sharma

Sign in as **`aarohi.sharma@indulink.example.test`**.

1. **Overview** (`/student`). Four counters, then *Next best actions*. Each action
   says why it is suggested. Note that "No data" is its own card, not a zero.
2. **Skills table.** Six separate columns: declared, assessed, provisional,
   evidence status, item count, detail. Assessed values come only from reviewed
   or verified evidence.
3. **My profile and skills** (`/student/profile`). Change visibility to
   *Public*, set availability to 10 hours a week, save. The freshness stamp under
   availability changes to "updated today".
4. **My evidence** (`/student/evidence`). Pick a skill, category
   *Practical task*, add a source line, level 4, submit. It appears as
   *Submitted* — not as a confirmed level.
5. **Challenges** (`/student/projects`). Open a challenge. The match panel shows
   each requirement, your level, and which requirement you do not yet meet.
6. **Skill passport** (`/student/passport`). Aarohi has no confirmed team place,
   so the create form explains that a passport needs one. That is the intended
   refusal, not a bug — use Yash Arora in section 6 to walk the create→publish flow.
7. **Notifications.** The bell shows unread items; open the inbox and mark all
   read.

Then sign out.

## 2. Mentor — Dr Harpreet Singh

Sign in as **`harpreet.singh@indulink.example.test`**.

1. **Overview** (`/mentor`). Capacity, current projects and the review queue
   summary.
2. **Mentor requests** (`/mentor/requests`). One request is *proposed*. Accept it:
   the screen confirms, the partner and team are notified, and the same request
   cannot be answered twice.
3. **Expertise registry** (`/mentor/registry`). Change your weekly hours cap and
   save; add an unavailability window and watch the capacity warning.
4. **Evidence review** (`/mentor/evidence`). Open a submitted item, choose
   *Reviewed*, set the level you could confirm, and write what you checked. The
   note is required. The item leaves the queue and the student's assessed level
   moves.

Sign out. To see a **faculty reviewer**, sign in as
**`neha.tandon@indulink.example.test`** — the same review queue, with review
expertise but no project capacity.

## 3. Industry partner — Kabir Malhotra

Sign in as **`kabir.malhotra@indulink.example.test`**.

1. **Overview** (`/industry`). Your challenges by state, with counts of
   candidates and teams.
2. **My challenges** (`/industry/projects`). Open *Smart-grid load forecasting
   dashboard* (project 1).
3. **Project detail.** Requirements with percentages that sum to 100%, the current
   version, and the allowed next states. Change state and give a reason — a
   refused transition explains itself instead of crashing.
4. **Candidates** (`/industry/projects/1/candidates`). The ranked shortlist.
   For each candidate open the **Why** panel: per-requirement contribution,
   eligibility, and the reason for the placement. Candidates who fail a mandatory
   requirement sit below every eligible candidate and say which one failed.
5. **Team** (`/industry/projects/1/team`). The suggestion names each person and
   why they were added, plus requirements it does not cover. Confirm the ticked
   members. A second confirmation is refused as a conflict, not silently
   duplicated.
6. **Mentor** (`/industry/projects/1/mentor`). The fit list shows expertise,
   capacity and availability. Request a mentor.
7. **What-If** (`/industry/projects/1/what-if`). Raise a required level by one and
   run it. You get entrants, leavers and moved candidates, and the page states
   that no stored data changed — verified by checksum, not by promise.
8. **Edited requirements.** Open *Fraud screening rules refresh* (project 9). It
   has two versions: open the older version and compare the shortlist stamp.
9. **Review** (`/industry/projects/1/review`). Record an evaluation and directed
   feedback; mark confidentiality and note that it is visible only to the people
   named and administrators.

Sign out.

## 4. Administrator — Meera Joshi

Sign in as **`meera.joshi@indulink.example.test`**.

1. **Overview** (`/admin`). Counts, pending approvals, recent activity.
2. **Users** (`/admin/users`). Filter by role; suspend and reactivate an account.
3. **Skill registry** (`/admin/skills`). Add a skill, add an alias, resolve a
   student's proposal.
4. **Evidence oversight** (`/admin/evidence`). The full queue across mentors; every
   decision is audited.
5. **Project approvals** (`/admin/projects`). Approve the challenge that is
   *pending approval* (project 7) and watch it become available to students.
6. **Appeals** (`/admin/appeals`). Open an appeal, set *Resolved* and write the
   resolution.
7. **Passports** (`/admin/passports`). Published and unpublished entries, with the
   scope a mentor confirmed.
8. **Audit log** (`/admin/audit`). Search by action or actor. Note that entries
   cannot be edited or deleted — the database refuses it.
9. **Demo controls** (`/admin/demo`). *Reset demo dataset* rebuilds the seeded
   database in about 1.5 seconds, keeps you signed in, and records the reset in
   the audit log.

## 5. Deliberate edge cases

Sign in as the administrator and use `/admin/users` to find each person, or sign
in as the student directly.

| Person | What to look for |
| --- | --- |
| **Diya Arora** | Availability not recorded. Matching shows "no data"; it does not guess. |
| **Tanvi Bisht** | Availability window ended before the live challenges; she is not matched to them. |
| **Ishita Chauhan** | Availability recorded in `Europe/London`; overlap is computed timezone-aware. |
| **Neha Khanna** | Private profile: invisible to partners, visible to the administrator. |
| **Sakshi Chopra** | Declared skills and **no** evidence — every level is provisional, nothing is confirmed. |
| **Simran Kaur** | One rejected upload: it stays on the record and contributes nothing. |
| **Naina Kapoor** | Expired certificate: labelled expired, no confirmed level. |
| **Riya Dhillon / Karan Gill** | Equal scores: the tie is broken deterministically and the screen shows why. |

## 6. Create and publish a passport — Yash Arora

Sign in as **`student.yash.arora@indulink.example.test`** (a confirmed place on
project 3 with no passport yet).

1. **Skill passport** (`/student/passport`). Choose project 3, add a role,
   technologies and two competencies, describe the verified scope.
2. Create it. It is *Unpublished* and private to you.
3. Publish it. Without a verified scope the publish action is refused with the
   reason; with one it becomes readable to other signed-in people.
4. **Export** downloads a small JSON file (`indulink-passport-<id>.json`) with the
   attachment header.

## 7. Reset between walkthroughs

`/admin/demo` → *Reset demo dataset*. Everything above returns to its seeded
state in about 1.5 seconds; the reset itself appears in the audit log.

## What this tour does not claim

Effectiveness, prediction or employability. The interface is decision support on
synthetic records; every number here comes from the seed, not from real people.
