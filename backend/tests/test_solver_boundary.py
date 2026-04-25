"""Spec §7 invariant enforcement: services/solver/ imports SQLAlchemy
only in the allow-listed boundary-crossing modules.

Run in CI + locally. If a new file under services/solver/ imports
from app.infrastructure, this test fails with a clear message
identifying the offender + the allow-list rationale.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOLVER_DIR = Path(__file__).resolve().parent.parent / "app" / "services" / "solver"

# Allow-list rationale: each of these modules intentionally crosses
# the boundary to do DB I/O. New files added here REQUIRE a spec
# update + review. Cross-reference spec §7.
_ALLOWED_BOUNDARY_CROSSERS = {
    "constraint_loader.py",  # reads ConstraintConfig → ConstraintSpec
    "input_builder.py",  # reads masterdata → SolverInput
    "trace_writer.py",  # writes solver_run + solver_decision
    # Week 9 SRP cleanup: extracted from cp_sat_optimizer.py.
    # `preemption.py` mutates ScheduleTask + creates ProductionBatch
    # rows for the urgent-batch drum-split / deferral logic.
    "preemption.py",
    # `decision_aggregator.py` reads ConstraintConfig to enumerate the
    # set of user-facing constraints that get a solver_decision row.
    "decision_aggregator.py",
}

# Prefixes that count as "crossing the boundary" — SQLAlchemy ORM
# + session + DB config. Do NOT allow any of these inside pure
# solver modules.
_INFRASTRUCTURE_PREFIXES = (
    "app.infrastructure",
    "app.infrastructure.models",
    "app.infrastructure.database",
)


def _module_imports_infrastructure(py_path: Path) -> list[str]:
    """Return the list of `app.infrastructure.*` names the file imports.

    Walks AST (not regex) so inline comments and string literals don't
    false-positive.
    """
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for prefix in _INFRASTRUCTURE_PREFIXES:
                if node.module == prefix or node.module.startswith(prefix + "."):
                    hits.append(f"from {node.module} import ...")
                    break
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in _INFRASTRUCTURE_PREFIXES:
                    if alias.name == prefix or alias.name.startswith(prefix + "."):
                        hits.append(f"import {alias.name}")
                        break
    return hits


def test_solver_boundary_allow_list_matches_reality() -> None:
    """Every .py file under services/solver/ either:
      (a) is in _ALLOWED_BOUNDARY_CROSSERS, OR
      (b) has zero `app.infrastructure.*` imports.

    Violations mean either a new boundary-crosser is needed (update
    the allow-list + spec §7) OR a solver-pure module accidentally
    picked up an ORM import (fix the import).
    """
    violations: list[str] = []
    allowed_found: set[str] = set()
    for py_path in sorted(_SOLVER_DIR.rglob("*.py")):
        if py_path.name == "__init__.py":
            continue
        hits = _module_imports_infrastructure(py_path)
        is_allowed = py_path.name in _ALLOWED_BOUNDARY_CROSSERS
        if hits and not is_allowed:
            violations.append(
                f"  {py_path.relative_to(_SOLVER_DIR)}: "
                f"{len(hits)} infrastructure import(s): {hits[:3]}"
            )
        if hits and is_allowed:
            allowed_found.add(py_path.name)

    missing = _ALLOWED_BOUNDARY_CROSSERS - allowed_found
    msg_parts = []
    if violations:
        msg_parts.append(
            "Solver modules importing SQLAlchemy OUTSIDE the allow-list "
            "(spec §7 invariant violation):\n" + "\n".join(violations)
        )
    if missing:
        msg_parts.append(
            "Allow-listed modules no longer import infrastructure "
            "(can be removed from allow-list): " + ", ".join(sorted(missing))
        )
    assert not msg_parts, "\n\n".join(msg_parts)


def test_allow_list_files_exist() -> None:
    """Sanity: the allow-list references real files."""
    for name in _ALLOWED_BOUNDARY_CROSSERS:
        path = _SOLVER_DIR / name
        assert path.exists(), f"allow-listed file missing: {path}"


def test_solver_init_does_not_import_infrastructure() -> None:
    """services/solver/__init__.py must not directly import ORM —
    it only re-exports from the boundary-crossing modules.
    """
    init_file = _SOLVER_DIR / "__init__.py"
    hits = _module_imports_infrastructure(init_file)
    assert not hits, f"__init__.py leaks infrastructure imports: {hits}"
