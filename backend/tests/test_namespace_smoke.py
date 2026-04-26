"""Characterization smoke test — namespace 무결성 보증 (refactoring 라운드 v2).

본 테스트의 진정한 가치
-----------------------

`docs/architecture-target.md` §4 Phase 1 진행 중에는 `services/X.py` 의 함수가
`domain/`, `application/`, `infrastructure/` 의 새 모듈로 이동한다.

본 테스트는 shell 이 reexport 하는 모든 심볼이 새 위치의 함수 객체와
**동일한 Python object (`is` 비교)** 임을 보증했다. Phase 5 §9.4 에서
shell + `services/__init__.py` 가 삭제되어 PAIRS 는 D7-C 와 무관한 cross-
package 검증만 남는다 (cp_sat orchestrator re-export 등). 이후에는 raw
smoke import + 일반 pytest 가 보호한다.
"""

from __future__ import annotations

import importlib
from typing import List, Tuple

# (legacy module path, attribute name, new module path)
# Phase 5 §9.4 후: services/schedule_optimizer 셸 삭제로 그 path 는 모두 제거.
# 남은 것은 cross-package re-export invariant (cp_sat orchestrator 의 calendar_ops /
# db_ops 노출) + ingest aggregate __init__ 의 도메인 키 재노출.
PAIRS: List[Tuple[str, str, str]] = [
    # ingest aggregate __init__.py 의 batch_sheath_keys 재export 호환 표면.
    (
        "app.application.ingest",
        "_SHEATH_COLOR_RANK",
        "app.domain.batch_sheath_keys",
    ),
    (
        "app.application.ingest",
        "_WIP_COVERED_PROCESSES",
        "app.domain.batch_sheath_keys",
    ),
    ("app.application.ingest", "_A120_COLORS", "app.domain.batch_sheath_keys"),
    ("app.application.ingest", "_TFR8_PATTERN", "app.domain.batch_sheath_keys"),
    (
        "app.application.ingest",
        "_compose_sheath_group_key",
        "app.domain.batch_sheath_keys",
    ),
    # cp_sat orchestrator 가 application/_shared/ 의 헬퍼들을 재export 하는지
    # (D7-C invariant — orchestrator-level 모니터링 동안 유지).
    (
        "app.application.scheduling.cp_sat.orchestrator",
        "resolve_base_date",
        "app.application._shared.calendar_ops",
    ),
    (
        "app.application.scheduling.cp_sat.orchestrator",
        "_delete_task_safely",
        "app.application._shared.db_ops",
    ),
]


def test_legacy_paths_resolve_to_new_objects() -> None:
    """모든 PAIRS 의 legacy attr 와 new attr 가 동일 객체 (`is`) 인지 확인."""
    if not PAIRS:
        # 본 라운드 시작 시점 (Phase 0.5a) 또는 Phase 5 종료 후: trivially pass.
        return

    failures: List[str] = []
    for legacy_mod, attr, new_mod in PAIRS:
        try:
            lm = importlib.import_module(legacy_mod)
            nm = importlib.import_module(new_mod)
        except ImportError as exc:
            failures.append(f"{new_mod}: not importable yet ({exc})")
            continue

        if not hasattr(lm, attr):
            failures.append(f"{legacy_mod}.{attr}: missing on legacy module")
            continue
        if not hasattr(nm, attr):
            failures.append(f"{new_mod}.{attr}: missing on new module")
            continue

        legacy_obj = getattr(lm, attr)
        new_obj = getattr(nm, attr)
        if legacy_obj is not new_obj:
            failures.append(
                f"{legacy_mod}.{attr} is NOT {new_mod}.{attr} "
                f"(legacy id={id(legacy_obj)}, new id={id(new_obj)})"
            )

    assert not failures, "namespace migration drift detected:\n" + "\n".join(failures)


def test_smoke_imports_succeed() -> None:
    """주요 layer 패키지가 모두 import 가능 — cycle / missing module 검출.

    Phase 5 §9.4 에서 ``app.services`` 패키지 삭제 → 본 smoke 에서도 제거.
    """
    importlib.import_module("app.main")
    importlib.import_module("app.domain")
    importlib.import_module("app.application")
    importlib.import_module("app.infrastructure")
    importlib.import_module("app.presentation")
