# InduLink — development tasks.
#
# Everything the brief names as a command lives here: `make test` is the
# CI-style gate and `make demo` seeds synthetic data and starts the site.

VENV ?= .venv
ifeq ($(OS),Windows_NT)
PY := $(VENV)/Scripts/python.exe
else
PY := $(VENV)/bin/python
endif

PORT ?= 8000
HOST ?= 127.0.0.1

.PHONY: help install migrate seed demo run test scenarios coverage clean

help:
	@echo "install   create $(VENV) and install pinned requirements"
	@echo "migrate   apply database migrations (alembic upgrade head)"
	@echo "seed      load the deterministic synthetic dataset"
	@echo "demo      migrate, seed, then serve a seeded site on http://$(HOST):$(PORT)"
	@echo "run       serve the app without re-seeding"
	@echo "test      run the full suite with coverage and the core/ 90% gate"
	@echo "scenarios run scenarios A to G against the seeded dataset"
	@echo "clean     remove caches and the local database"

install:
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

migrate:
	$(PY) -m alembic upgrade head

seed:
	$(PY) -m data.seed_synthetic

demo: migrate seed
	$(PY) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --reload

run:
	$(PY) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

# The exit gate: every test, coverage report, and core/ above 90% lines.
test:
	$(PY) -m pytest tests -q --cov=core --cov=app --cov=services \
		--cov-report=term-missing --cov-report=
	$(PY) -m coverage report --include="core/*" --fail-under=90

# Scenarios A to G, each end to end through the real routes.
scenarios:
	$(PY) -m pytest tests/scenarios -q

coverage: test

clean:
	rm -rf .pytest_cache .hypothesis .coverage htmlcov
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -f var/indulink.db var/indulink.db-wal var/indulink.db-shm
