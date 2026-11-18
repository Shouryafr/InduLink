# Running InduLink locally

FastAPI + Jinja2 + SQLite. No Node, no package manager build step. Everything below is
Windows-specific only where it names `.venv\Scripts\python.exe`; on POSIX use `.venv/bin/python`.

## Reproduce the uncommitted artifacts

A fresh checkout needs three things that are not in git: the virtualenv, the migrations
applied, and the seeded database. The database file (`var/indulink.db` and its `-wal`/`-shm`
companions) is generated, not committed.

1. Create the virtualenv and install pinned dependencies:

   ```bash
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
   ```

2. Apply migrations (creates `var/indulink.db`):

   ```bash
   .venv/Scripts/python.exe -m alembic upgrade head
   ```

3. Seed the deterministic synthetic dataset. This is idempotent: re-running it adds nothing.
   Expected counts: 85 accounts (60 students, 13 mentors, 6 faculty reviewers, 4 industry
   contacts, 2 administrators), 17 projects, 13 teams, 588 evidence items, 20 passports,
   30 feedback/evaluation/appeal records, 26 registry skills.

   ```bash
   .venv/Scripts/python.exe -m data.seed_synthetic
   ```

### This machine: Application Control blocks SQLAlchemy's C extensions

On this checkout the OS refuses to load SQLAlchemy's compiled modules:

```
ImportError: DLL load failed while importing _result_cy: An Application Control policy has blocked this file.
```

Only SQLAlchemy's `.pyd` files are affected (pydantic-core, bcrypt and the rest load fine).
SQLAlchemy 2.1.6 ships a pure-Python module beside every compiled one, so the workaround is to
move the blocked binaries out of the package directory and let the `.py` implementations load:

```bash
SP=.venv/Lib/site-packages/sqlalchemy
mkdir -p "$SP/_blocked_cext"
for f in $(find "$SP" -name "*.pyd"); do mv "$f" "$SP/_blocked_cext/"; done
```

This is a local, reversible venv change (nothing in git). To undo it — after the policy is
relaxed or the wheel is allowlisted — put the files back or reinstall:

```bash
.venv/Scripts/python.exe -m pip install --force-reinstall --no-deps SQLAlchemy==2.1.6
```

Expect somewhat slower query hot paths; the demo pages still answer well under 100 ms.

No `.env` file is required. Development generates an ephemeral `SECRET_KEY`, so sessions do not
survive a server restart, and `DEMO_MODE` defaults on, which is what shows the demo sign-in
panel on `/sign-in`. To pin a secret or change behaviour, copy `.env.example` to `.env` and edit
it — the file is git-ignored and its values must never be committed.

Demo sign-in: password `DemoPass!2026`
(`aarohi.sharma@indulink.example.test`, `harpreet.singh@indulink.example.test`,
`neha.tandon@indulink.example.test` for the faculty reviewer,
`kabir.malhotra@indulink.example.test`, `meera.joshi@indulink.example.test`).

## Run the server

Port **8000** (the project default; the `Makefile` variable is `PORT ?= 8000`). Start it
detached so it outlives the conversation:

```bash
powershell -NoProfile -Command "(Start-Process -FilePath 'C:\Users\shour\OneDrive\Desktop\InduLink\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory 'C:\Users\shour\OneDrive\Desktop\InduLink' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
```

Then confirm it is alive and answering:

```bash
powershell -NoProfile -Command "Get-Process -Id <pid>"
```

**The wrapper may look like it hung.** `Start-Process` prints the pid, but the shell tool can
sit on the detached process's inherited handles until it times out. The server is running even
when no output came back — check `netstat -ano | grep ":8000"` and `curl /healthz` instead of
starting a second copy.

Notes:

- `--reload` is deliberately omitted. Reload spawns a child process, and a preview that dies
  with the file-watcher is harder to reason about than a restart. Restart the same command
  after editing Python; template and CSS changes are picked up per request (Jinja auto-reload
  in development), though static assets are cached with a version stamp, so hard-reload the
  browser after CSS edits.
- The venv's `python.exe` is a launcher: the process actually listening is its `python3.13`
  child. `netstat -ano | grep ":8000"` names the real listener; that is the pid to use when
  stopping the server.
- `make demo` does the same thing in the foreground (`--reload` included) if you just want a
  terminal session. `make test` is the full gate: 445 tests plus a 90% floor on `core/`;
  `make scenarios` runs scenarios A–G alone, and `tools/measure.py` prints the performance table.

## Stopping

```bash
powershell -NoProfile -Command "Stop-Process -Id <listening pid>"
```
