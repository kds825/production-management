SHELL := /bin/bash
-include .env.worktree
export

.PHONY: bootstrap doctor verify parity parity-quick parity-fixture \
        test test-backend test-frontend lint typecheck db-fresh

bootstrap:
	@command -v python3.11 >/dev/null || { echo "python3.11 required"; exit 1; }
	@test -d backend/venv || (cd backend && python3.11 -m venv venv)
	@source backend/venv/bin/activate && pip install -q -r backend/requirements.txt
	@test -f .env.worktree || cp .env.worktree.example .env.worktree 2>/dev/null || true
	@test -f backend/.env || { echo "✗ backend/.env missing — set DATABASE_URL before bootstrap"; exit 1; }
	@grep -q '^DATABASE_URL=' backend/.env || { echo "✗ backend/.env has no DATABASE_URL"; exit 1; }
	@source backend/venv/bin/activate && cd backend && alembic current
	@echo "Bootstrap complete. Try: make doctor"

doctor:
	@echo "═══ $$(basename $$(pwd)) — doctor ═══"
	@test -f .env.worktree && echo "  ✓ .env.worktree present" || echo "  ⚠ no .env.worktree (run 'make bootstrap' to seed)"
	@test -f backend/.env && grep -q '^DATABASE_URL=' backend/.env \
	  && echo "  ✓ DATABASE_URL configured" \
	  || { echo "  ✗ backend/.env DATABASE_URL missing"; exit 1; }
	@source backend/venv/bin/activate && cd backend \
	  && HEAD=$$(alembic heads | awk '{print $$1}') \
	  && CURR=$$(alembic current 2>/dev/null | awk '{print $$1}') \
	  && if [ "$$CURR" = "$$HEAD" ]; then \
	       echo "  ✓ alembic at head ($$HEAD)"; \
	     else \
	       echo "  ⚠ alembic drift: code head=$$HEAD current=$$CURR"; \
	     fi
	@source backend/venv/bin/activate && python -c "import pytest" 2>/dev/null \
	  && echo "  ✓ pytest discoverable" \
	  || echo "  ✗ pytest missing"
	@cd frontend && npm ls vitest >/dev/null 2>&1 \
	  && echo "  ✓ vitest discoverable" \
	  || echo "  ⚠ vitest missing (run npm install)"
	@BRANCH=$$(git branch --show-current); \
	 WT=$$(basename $$(pwd)); \
	 case "$$WT" in \
	   KBI_PoC_track_a) [[ "$$BRANCH" == "refactoring/track-a-solver" ]] \
	     && echo "  ✓ branch matches worktree" \
	     || echo "  ✗ expected refactoring/track-a-solver, got $$BRANCH" ;; \
	   KBI_PoC_track_b) [[ "$$BRANCH" == "refactoring/track-b-admin" ]] \
	     && echo "  ✓ branch matches worktree" \
	     || echo "  ✗ expected refactoring/track-b-admin, got $$BRANCH" ;; \
	   *) echo "  ✓ main worktree ($$BRANCH)" ;; \
	 esac

verify: lint typecheck test parity

parity:
	cd backend && source venv/bin/activate && pytest tests/test_parity_harness.py -m parity -v

parity-quick:
	cd backend && source venv/bin/activate && pytest tests/test_parity_harness.py -m parity --parity-quick -v

parity-fixture:
	@test -n "$(FIXTURE)" || { echo "Usage: make parity-fixture FIXTURE=02"; exit 1; }
	cd backend && source venv/bin/activate && pytest tests/test_parity_harness.py::test_parity -m parity -k "$(FIXTURE)" -v

test: test-backend test-frontend

test-backend:
	cd backend && source venv/bin/activate && pytest -q

test-frontend:
	cd frontend && npm run test

lint:
	cd backend && source venv/bin/activate && ruff check . || true
	cd frontend && npm run lint

typecheck:
	cd backend && source venv/bin/activate && mypy app/ || true
	cd frontend && npm run typecheck

db-fresh:
	@echo "⚠ This uses the throwaway docker-compose DB (kbi/kbi_poc_2026/kbi_scheduler), NOT Supabase."
	docker compose up -d db
	sleep 3
	DATABASE_URL="postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler" \
	  bash -c 'cd backend && source venv/bin/activate && alembic upgrade head && python seed_db.py'
