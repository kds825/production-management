"""Spec §7 invariant enforcement: application/scheduling/cp_sat/ imports
SQLAlchemy only in the allow-listed boundary-crossing modules.

Run in CI + locally. If a new file under application/scheduling/cp_sat/
imports from app.infrastructure, this test fails with a clear message
identifying the offender + the allow-list rationale.

Why path moved: Phase 1 step 4a (architecture-target.md §4) — solver/*
는 application/scheduling/cp_sat/ 으로 이동. invariant 자체는 동일.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOLVER_DIR = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "application"
    / "scheduling"
    / "cp_sat"
)

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
    # Phase 1 step 4b (architecture-target.md §4 P2): `orchestrator.py`
    # 는 직전 `cp_sat_optimizer.py` 의 단순 rename. cp_sat_schedule()
    # entry-point 는 본질적으로 use-case orchestrator 라 DB I/O 가
    # 정당. 본 파일 ≤200 LOC 분해는 Phase 3 의 책임.
    "orchestrator.py",
    # Phase 2 Task 2.6 (B-3.1): `_load_inputs.py` 는 cp_sat_schedule §1-3
    # (DB 배치/마스터데이터 로드) 를 추출한 use-case helper. 본질적으로
    # ProductionBatch / EquipmentMaster / SpeedMaster / DrumLotMaster 를
    # DB 에서 읽기 때문에 boundary crosser. parity 보장이 핵심 invariant.
    "_load_inputs.py",
}

# Prefixes that count as "crossing the boundary" — SQLAlchemy ORM
# + session + DB config. Do NOT allow any of these inside pure
# solver modules.
_INFRASTRUCTURE_PREFIXES = (
    "app.infrastructure",
    "app.infrastructure.models",
    "app.infrastructure.database",
)


def _is_type_checking_guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return True if ``node`` is nested inside an ``if TYPE_CHECKING:`` block.

    runtime 에 실행되지 않는 import 는 boundary 위반이 아니다 (DB I/O 없음).
    """
    cursor = parents.get(node)
    while cursor is not None:
        if isinstance(cursor, ast.If):
            test = cursor.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            if (
                isinstance(test, ast.Attribute)
                and isinstance(test.value, ast.Name)
                and test.attr == "TYPE_CHECKING"
            ):
                return True
        cursor = parents.get(cursor)
    return False


def _module_imports_infrastructure(py_path: Path) -> list[str]:
    """Return the list of `app.infrastructure.*` names the file imports.

    Walks AST (not regex) so inline comments and string literals don't
    false-positive. ``if TYPE_CHECKING:`` block 의 import 는 runtime 에서
    실행되지 않으므로 (PEP 484) boundary 검사에서 제외 — 함수 시그니처
    type-hint 전용 import 는 DB I/O 가 아니다.
    """
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    hits: list[str] = []
    for node in ast.walk(tree):
        if _is_type_checking_guarded(node, parents):
            continue
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
    """Every .py file under application/scheduling/cp_sat/ either:
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
    """application/scheduling/cp_sat/__init__.py must not directly import
    ORM — it only re-exports from the boundary-crossing modules.
    """
    init_file = _SOLVER_DIR / "__init__.py"
    hits = _module_imports_infrastructure(init_file)
    assert not hits, f"__init__.py leaks infrastructure imports: {hits}"
