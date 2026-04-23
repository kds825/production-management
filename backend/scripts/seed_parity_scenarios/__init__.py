"""Parity fixture seed scripts (Task 1.2).

Each submodule exposes `seed(db: Session) -> None` that wipes the tables
it touches and inserts a deterministic scenario. Used by Task 1.3
(`capture_parity_fixture.py`) which runs `build_solver_input(db)` on the
seeded DB to produce `backend/tests/fixtures/parity/*.json`.

Determinism contract: no `datetime.now()`, no `uuid.uuid4()`, no unseeded
`random.*` — output is byte-identical across re-runs against the same
truncated starting state.
"""
