"""Characterization smoke test — namespace 무결성 보증 (refactoring 라운드 v2).

본 테스트의 진정한 가치
-----------------------

`docs/architecture-target.md` §4 Phase 1 진행 중에는 `services/X.py` 의 함수가
`domain/`, `application/`, `infrastructure/` 의 새 모듈로 이동한다. 일부
모듈 (특히 `services/schedule_optimizer.py` D7-C shell) 은 Phase 5 까지
**re-export 셸 형태로 보존**되어 21 monkeypatch test site 의 contract 를
지킨다.

본 테스트는 shell 이 reexport 하는 모든 심볼이 새 위치의 함수 객체와
**동일한 Python object (`is` 비교)** 임을 보증한다. 매 phase step 후 갱신.

phase 진행 중 새 path 의 import 가 아직 안 되는 상황도 graceful — 일시적
ImportError 는 failures 로 누적되되 그 step 의 commit message 가 새 path
import 를 동시 commit 한 경우에는 그린이어야 한다.

Phase 5 §9.4 에서 shell + `services/__init__.py` 가 삭제되면 본 테스트는
`PAIRS` 가 비워지고 trivially pass 가 된다. 이후에는 raw smoke import +
일반 pytest 가 보호한다.
"""

from __future__ import annotations

import importlib
from typing import List, Tuple

# (legacy module path, attribute name, new module path)
# 매 phase step 후 추가. 빈 list 일 때는 trivially pass.
PAIRS: List[Tuple[str, str, str]] = [
    # Phase 1 step 1 (domain/ leaf) 후 추가:
    # batch_grouping/__init__ 가 도메인 키들을 재export 하는지 (호환 표면)
    (
        "app.services.batch_grouping",
        "_SHEATH_COLOR_RANK",
        "app.domain.batch_sheath_keys",
    ),
    (
        "app.services.batch_grouping",
        "_WIP_COVERED_PROCESSES",
        "app.domain.batch_sheath_keys",
    ),
    ("app.services.batch_grouping", "_A120_COLORS", "app.domain.batch_sheath_keys"),
    ("app.services.batch_grouping", "_TFR8_PATTERN", "app.domain.batch_sheath_keys"),
    (
        "app.services.batch_grouping",
        "_compose_sheath_group_key",
        "app.domain.batch_sheath_keys",
    ),
    # Phase 1 step 2 (infrastructure/ leaf) 후 추가:
    # ("app.services.calendar_engine", "calculate_end_datetime", "app.infrastructure.calendar_engine"),
    # Phase 1 step 3 후 추가:
    # ("app.services.audit_logger", "log_decision", "app.application._shared.audit_logger"),
    # ("app.services.constraint_params", "ConstraintParams", "app.application._shared.constraint_params"),
    # ...
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
    """주요 layer 패키지가 모두 import 가능 — cycle / missing module 검출."""
    importlib.import_module("app.main")
    importlib.import_module("app.domain")
    importlib.import_module("app.infrastructure")
    importlib.import_module("app.presentation")
    importlib.import_module(
        "app.services"
    )  # Phase 5 종료 시 삭제됨 — 그 시점에 본 라인 제거
