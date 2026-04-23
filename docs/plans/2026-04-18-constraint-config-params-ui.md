# ConstraintConfig 파라미터 UI 편집 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 연선 규격교체 시간 등 스케줄러 파라미터를 UI에서 편집 가능하게 전환하고, 편집 후 재실행 반영 여부를 사용자가 모달에서 선택하게 한다.

**Architecture:**

- 백엔드: 기존 `ConstraintConfig` 테이블 재사용, 프리페치 캐시(`ConstraintParams`)로 스케줄러 핫루프 보호, 기존 `/constraints` 라우터 확장(신규 라우터 X), Alembic 1개 추가(`updated_at` + `constraint_config_history`)
- 프론트: 기존 `/master/constraints/page.tsx`(토글 전용) 확장 — 파라미터 편집 + 변경 이력 탭 + drift 배지 + 저장 모달
- 설계 기반: `docs/specs/2026-04-18-constraint-config-params-ui-design.md` v2

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic, Next.js 16 (webpack), React 19, Playwright, pytest, Postgres(Supabase)

**주의사항:**

- `frontend/AGENTS.md` 경고: "This is NOT the Next.js you know" — 새 컴포넌트 패턴 추가 전 `node_modules/next/dist/docs/` 확인 필요
- pwc-design(samildevkit) 규칙 준수 — verify-pwc-design 스킬 통과 필수
- feedback_commit_verification: 각 태스크 완료 후 **검증 통과한 뒤에만** 커밋, 다음 태스크 착수

---

## File Structure

```
backend/
  app/
    services/
      constraint_params.py          (NEW)      - ConstraintParams 프리페치 캐시 + get 헬퍼
      batch_grouping.py             (MODIFY)   - :521, :577 fallback 교체, create_batches 진입 시 load()
      schedule_optimizer.py         (MODIFY)   - :817-819 색상 교체, :457-467 welding 리팩터
      cp_sat_optimizer.py           (MODIFY)   - 대칭 위치 동일
    presentation/routes/
      constraints.py                (MODIFY)   - drift-status, preview-impact, history 엔드포인트 추가
    infrastructure/models/
      constraint_config.py          (MODIFY)   - updated_at 컬럼
      constraint_config_history.py  (NEW)      - 변경이력 모델
      scheduler_run_log.py          (NEW)      - last_run_at 기록용 (이미 있으면 재사용)
  alembic/versions/
    e1f2a3b4c5d6_add_constraint_config_history_and_timestamps.py  (NEW)
  tests/
    services/
      test_constraint_params.py     (NEW)
    test_batch_grouping_4_1.py      (NEW)      - No-op 회귀 + stranding_min=0 동작
    test_schedulers_color_4_2.py    (NEW)      - schedule vs cp_sat 동등성
    api/
      test_constraints_params.py    (NEW)      - drift-status/preview-impact/history API

frontend/
  src/app/(main)/master/constraints/
    page.tsx                        (MODIFY)   - 파라미터 편집 + 탭 + drift 배지 + 모달
    components/
      ParamEditor.tsx               (NEW)      - 4-1/4-2/4-4 카드 폼
      SaveModal.tsx                 (NEW)      - "지금 반영" vs "다음부터" 2지선다 + 영향 프리뷰
      HistoryTab.tsx                (NEW)      - 변경이력 timeline
      DriftBanner.tsx               (NEW)      - 상단 배지
  e2e/
    constraint-config-params.spec.ts (NEW)

docs/
  specs/2026-04-18-constraint-config-params-ui-design.md   (존재)
  plans/2026-04-18-constraint-config-params-ui.md          (현재 문서)
```

---

## Task 1: ConstraintParams 헬퍼 + 프리페치 캐시

**Files:**

- Create: `backend/app/services/constraint_params.py`
- Create: `backend/tests/services/test_constraint_params.py`

- [ ] **Step 1.1: 테스트 작성 (실패 상태)**

Create `backend/tests/services/__init__.py` (empty file).

Create `backend/tests/services/test_constraint_params.py`:

```python
"""ConstraintParams 프리페치 캐시 헬퍼 테스트."""

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.services.constraint_params import ConstraintParams


def test_load_builds_dict_of_params(db: Session) -> None:
    """DB 모든 ConstraintConfig row를 constraint_id 키 dict로 프리페치한다."""
    params = ConstraintParams.load(db)
    # 시드된 4-1 / 4-2 / 4-4 가 존재해야 함
    assert "4-1" in params.by_id
    assert params.by_id["4-1"].get("stranding_min") == 210
    assert params.by_id["4-2"].get("sheath_color_min") == 120
    assert params.by_id["4-4"].get("welding_min") == 30


def test_get_returns_value(db: Session) -> None:
    params = ConstraintParams.load(db)
    assert params.get("4-1", "stranding_min") == 210.0
    assert isinstance(params.get("4-1", "stranding_min"), float)


def test_get_uses_default_when_key_missing(db: Session) -> None:
    params = ConstraintParams.load(db)
    assert params.get("4-1", "nonexistent_key", default=99.0) == 99.0


def test_get_raises_when_row_missing_and_no_default(db: Session) -> None:
    params = ConstraintParams(by_id={})
    with pytest.raises(RuntimeError, match="ConstraintConfig '4-1' row not found"):
        params.get("4-1", "stranding_min")


def test_get_raises_when_key_missing_and_no_default(db: Session) -> None:
    params = ConstraintParams(by_id={"4-1": {}})
    with pytest.raises(RuntimeError, match="key 'stranding_min' missing"):
        params.get("4-1", "stranding_min")


def test_frozen_dataclass_prevents_mutation() -> None:
    params = ConstraintParams(by_id={"4-1": {"stranding_min": 210}})
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        params.by_id = {}  # type: ignore
```

- [ ] **Step 1.2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/services/test_constraint_params.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.constraint_params'`)

- [ ] **Step 1.3: 헬퍼 구현**

Create `backend/app/services/constraint_params.py`:

```python
"""ConstraintConfig 파라미터 프리페치 캐시.

Why: schedule_optimizer / batch_grouping 이 루프 내부에서 ConstraintConfig 를
조회하면 N+1 쿼리가 발생한다. 한 요청(create_batches 또는 auto_schedule)
진입 시 1회 프리페치하여 dict 스냅샷으로 전달한다.

전역 lru_cache 사용 금지 — PATCH 후 stale 위험.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig


@dataclass(frozen=True)
class ConstraintParams:
    """create_batches / auto_schedule 1회 실행 동안 재사용되는 스냅샷."""

    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, db: Session) -> "ConstraintParams":
        rows = db.query(ConstraintConfig).all()
        return cls(
            by_id={r.constraint_id: dict(r.params_json or {}) for r in rows},
        )

    def get(
        self,
        constraint_id: str,
        key: str,
        default: float | None = None,
    ) -> float:
        """params_json 에서 숫자 파라미터 조회. Fail-fast 정책."""
        row = self.by_id.get(constraint_id)
        if row is None:
            if default is not None:
                return float(default)
            raise RuntimeError(
                f"ConstraintConfig '{constraint_id}' row not found. "
                "Run seed_db.py to initialize constraint parameters."
            )
        if key not in row:
            if default is not None:
                return float(default)
            raise RuntimeError(
                f"ConstraintConfig '{constraint_id}' params_json key "
                f"'{key}' missing."
            )
        return float(row[key])
```

- [ ] **Step 1.4: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/services/test_constraint_params.py -v`
Expected: 6 passed

- [ ] **Step 1.5: 커밋**

```bash
git add backend/app/services/constraint_params.py backend/tests/services/__init__.py backend/tests/services/test_constraint_params.py
git commit -m "feat(scheduler): ConstraintParams 프리페치 캐시 헬퍼

- ConstraintConfig N+1 쿼리 방지 (create_batches/auto_schedule 진입 1회 load)
- Fail-fast: row 또는 key 누락 + default 없음 → RuntimeError
- frozen dataclass — 전역 lru_cache 금지 원칙 (PATCH stale 방지)"
```

---

## Task 2: batch_grouping 4-1 fallback 제거 (+ No-op 회귀)

**Files:**

- Modify: `backend/app/services/batch_grouping.py` (진입부, `:521`, `:577`)
- Create: `backend/tests/test_batch_grouping_4_1.py`

- [ ] **Step 2.1: 회귀 테스트 작성 (No-op 불변식)**

Create `backend/tests/test_batch_grouping_4_1.py`:

```python
"""batch_grouping 4-1 (규격교체) fallback — ConstraintConfig 연동 회귀."""

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.services.constraint_params import ConstraintParams


def _get_4_1(db: Session) -> ConstraintConfig:
    return (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "4-1")
        .first()
    )


def test_4_1_seed_has_stranding_min_210(db: Session) -> None:
    """No-op 불변식 전제: 시드값 210 유지."""
    row = _get_4_1(db)
    assert row is not None
    assert row.params_json.get("stranding_min") == 210


def test_constraint_params_reads_4_1(db: Session) -> None:
    """batch_grouping 에서 get_constraint_param 로 읽을 때 시드값과 일치."""
    params = ConstraintParams.load(db)
    assert params.get("4-1", "stranding_min") == 210.0
```

- [ ] **Step 2.2: 테스트 실행 — 통과 확인 (seed 검증)**

Run: `cd backend && pytest tests/test_batch_grouping_4_1.py -v`
Expected: 2 passed (시드값 210 확인)

이 테스트는 회귀 guard — fallback 교체 후에도 통과해야 No-op 불변식 유지.

- [ ] **Step 2.3: batch_grouping.py — ConstraintParams 진입부 로드**

Read `backend/app/services/batch_grouping.py` lines 100-130 to find the `create_batches` function signature.

Then edit the function to load `ConstraintParams` once near the top:

```python
# near top of create_batches body
from app.services.constraint_params import ConstraintParams
constraint_params = ConstraintParams.load(db)
```

Exact placement: 직후에 `db: Session` 매개변수 사용이 시작되는 지점. 모든 하위 로직에서 `constraint_params` 변수가 가시적이어야 함.

- [ ] **Step 2.4: `:521` fallback 교체**

Edit `backend/app/services/batch_grouping.py` line 521 area:

```python
# Before:
core_setup = (
    float(core_speed_o.setup_spec_min)
    if core_speed_o and core_speed_o.setup_spec_min
    else 210.0
)

# After:
core_setup = (
    float(core_speed_o.setup_spec_min)
    if core_speed_o and core_speed_o.setup_spec_min
    else constraint_params.get("4-1", "stranding_min")
)
```

- [ ] **Step 2.5: `:577` fallback 교체 (동일 패턴, al_core)**

Edit `backend/app/services/batch_grouping.py` line 577 area:

```python
# Before:
al_core_setup = (
    float(al_core_speed_o.setup_spec_min)
    if al_core_speed_o and al_core_speed_o.setup_spec_min
    else 210.0
)

# After:
al_core_setup = (
    float(al_core_speed_o.setup_spec_min)
    if al_core_speed_o and al_core_speed_o.setup_spec_min
    else constraint_params.get("4-1", "stranding_min")
)
```

- [ ] **Step 2.6: 정적 검사 — `else 210.0` 자취 확인**

Run: `cd backend && grep -n "else 210.0" app/services/batch_grouping.py`
Expected: (아무 출력도 없어야 함)

- [ ] **Step 2.7: 기존 테스트 전체 실행 — 회귀 없음 확인**

Run: `cd backend && pytest tests/ -x --tb=short`
Expected: 모든 기존 테스트 통과 (시드값 210 유지이므로 결과 동일)

`test_overload_split.py` (setup_time_min=210 가정)가 반드시 통과해야 함.

- [ ] **Step 2.8: 커밋**

```bash
git add backend/app/services/batch_grouping.py backend/tests/test_batch_grouping_4_1.py
git commit -m "feat(scheduler): 4-1 fallback → ConstraintParams 조회 (No-op 유지)

- batch_grouping.py :521, :577 연선 core/al_core 서브배치 fallback
- 하드코딩 210.0 제거, ConstraintConfig 4-1 stranding_min 조회
- 시드값 210 유지 → 기존 스케줄 결과 완전 동일 (회귀 guard 테스트 추가)
- 범위 밖: :463-467 연선 메인 배치 else 0.0 (별도 스펙)"
```

---

## Task 3: 4-2 색상교체 fallback 교체 + 시맨틱 교정

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py` (`:817-819` 인근)
- Modify: `backend/app/services/cp_sat_optimizer.py` (대칭 위치)
- Create: `backend/tests/test_schedulers_color_4_2.py`

- [ ] **Step 3.1: 테스트 작성 (실패 상태) — 시맨틱 교정 + 동등성**

Create `backend/tests/test_schedulers_color_4_2.py`:

```python
"""4-2 색상교체 fallback — sm_color NULL vs 0 시맨틱 교정 + 이중 스케줄러 동등성."""

from unittest.mock import MagicMock

import pytest

from app.services.constraint_params import ConstraintParams


def _make_params_with(sheath_color_min: float) -> ConstraintParams:
    return ConstraintParams(
        by_id={"4-2": {"sheath_color_min": sheath_color_min}},
    )


def test_color_change_falls_back_to_constraint_params_when_sm_color_none() -> None:
    """SpeedMaster.setup_color_min IS NULL → ConstraintConfig 4-2 읽음."""
    # 이 테스트는 schedule_optimizer 의 헬퍼(또는 inline 로직)가
    # sm_color=None 일 때 params.get("4-2", "sheath_color_min") 를 호출함을 검증.
    from app.services.schedule_optimizer import _resolve_color_change_min

    params = _make_params_with(120.0)
    result = _resolve_color_change_min(sm_color_min=None, params=params)
    assert result == 120.0


def test_color_change_uses_sm_color_zero_as_valid_value() -> None:
    """시맨틱 교정: sm_color_min == 0 을 '값 없음' 이 아닌 '0분 허용' 으로 처리."""
    from app.services.schedule_optimizer import _resolve_color_change_min

    params = _make_params_with(120.0)  # fallback 이 호출되면 120 이 나올 것
    result = _resolve_color_change_min(sm_color_min=0.0, params=params)
    assert result == 0.0  # 0 이 그대로 반환되어야 함 (이전 버전은 120.0 반환했음)


def test_color_change_uses_sm_color_nonzero() -> None:
    from app.services.schedule_optimizer import _resolve_color_change_min

    params = _make_params_with(120.0)
    result = _resolve_color_change_min(sm_color_min=90.0, params=params)
    assert result == 90.0


def test_cp_sat_color_resolution_matches_schedule_optimizer() -> None:
    """이중 스케줄러 동등성 — 동일 헬퍼를 사용해야 함."""
    from app.services.schedule_optimizer import _resolve_color_change_min as a
    from app.services.cp_sat_optimizer import _resolve_color_change_min as b

    params = _make_params_with(120.0)
    for sm in (None, 0.0, 60.0, 120.0):
        assert a(sm_color_min=sm, params=params) == b(sm_color_min=sm, params=params)
```

- [ ] **Step 3.2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/test_schedulers_color_4_2.py -v`
Expected: FAIL (`_resolve_color_change_min` 함수 부재)

- [ ] **Step 3.3: `schedule_optimizer.py` 에 헬퍼 함수 추가**

`backend/app/services/schedule_optimizer.py` 상단(`_DEFAULT_WELDING_MIN = 30` 근처):

```python
def _resolve_color_change_min(
    sm_color_min: float | None,
    params: "ConstraintParams",  # forward reference - import at top
) -> float:
    """색상교체 시간 결정.

    - SpeedMaster.setup_color_min 이 None 이면 ConstraintConfig 4-2 fallback.
    - 0.0 은 '값 없음' 이 아닌 '0분 허용' 으로 처리 (시맨틱 교정).
    """
    if sm_color_min is None:
        return params.get("4-2", "sheath_color_min")
    return float(sm_color_min)
```

파일 상단 import 추가:

```python
from app.services.constraint_params import ConstraintParams
```

- [ ] **Step 3.4: `:817-819` 호출부 교체**

Read `backend/app/services/schedule_optimizer.py` lines 800-830 to confirm the context (시스 공정 색상 체크 블록).

Edit:

```python
# Before:
sm_color = (
    db.query(SpeedMaster.setup_color_min)
    .filter(SpeedMaster.equipment_code == eq.equipment_code)
    .first()
)
color_change_min = (
    float(sm_color[0] or 120.0) if sm_color else 120.0
)

# After:
sm_color = (
    db.query(SpeedMaster.setup_color_min)
    .filter(SpeedMaster.equipment_code == eq.equipment_code)
    .first()
)
sm_color_val = sm_color[0] if sm_color else None
color_change_min = _resolve_color_change_min(
    sm_color_min=sm_color_val,
    params=constraint_params,  # auto_schedule 진입부에서 load
)
```

이 호출 지점은 `auto_schedule` 함수 내부이므로, 함수 진입부에 `constraint_params = ConstraintParams.load(db)` 를 추가해야 함. 이미 `welding_cfg = ... 4-4` 조회 블록 근처에 자연스럽게 추가 가능.

- [ ] **Step 3.5: `cp_sat_optimizer.py` 동일 처리**

Read `backend/app/services/cp_sat_optimizer.py` — `schedule_optimizer.py:817-819` 의 대칭 위치(색상교체 120.0 하드코딩) 찾아 동일 패턴 적용. 그리고 동일한 이름의 `_resolve_color_change_min` 을 import 재사용:

```python
from app.services.schedule_optimizer import _resolve_color_change_min
```

(또는 `constraint_params.py` 로 끌어내도 됨 — 어차피 순수 함수)

**더 깔끔한 선택**: `_resolve_color_change_min` 을 `constraint_params.py` 에 두고 양쪽에서 import. Task 3.3 의 구현 위치를 `constraint_params.py` 로 옮기는 것을 권장.

- [ ] **Step 3.6: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/test_schedulers_color_4_2.py -v`
Expected: 4 passed

- [ ] **Step 3.7: 전체 회귀**

Run: `cd backend && pytest tests/ -x --tb=short`
Expected: 모든 테스트 통과

- [ ] **Step 3.8: 커밋**

```bash
git add backend/app/services/schedule_optimizer.py backend/app/services/cp_sat_optimizer.py backend/app/services/constraint_params.py backend/tests/test_schedulers_color_4_2.py
git commit -m "feat(scheduler): 4-2 색상교체 fallback 통합 + 시맨틱 교정

- schedule_optimizer :817-819 + cp_sat_optimizer 대칭 위치 통일
- _resolve_color_change_min 헬퍼로 양 스케줄러 동등성 보장
- 시맨틱 교정: sm_color_min==0 을 '값 없음' 이 아닌 '0분 허용' 으로 처리
- SpeedMaster.setup_color_min IS NULL 시 ConstraintConfig 4-2 sheath_color_min 조회
- 이전: float(sm_color[0] or 120.0) — 0 을 잘못 치환했던 버그 수정"
```

---

## Task 4: 4-4 welding 기존 코드 ConstraintParams 로 통합 (중복 제거)

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py` (`:455-467`)
- Modify: `backend/app/services/cp_sat_optimizer.py` (`:488-497`)

- [ ] **Step 4.1: 동등성 회귀 테스트 추가**

Append to `backend/tests/test_schedulers_color_4_2.py`:

```python
def test_welding_min_resolution_dedup() -> None:
    """4-4 welding — 양 스케줄러가 ConstraintParams 로 통일되는지."""
    from app.services.constraint_params import ConstraintParams

    params = ConstraintParams(by_id={"4-4": {"welding_min": 30}})
    assert params.get("4-4", "welding_min", default=30) == 30.0
    # row 있지만 key 누락 → default 반환
    params2 = ConstraintParams(by_id={"4-4": {}})
    assert params2.get("4-4", "welding_min", default=30) == 30.0
    # row 아예 없음 → default 반환 (하위 호환)
    params3 = ConstraintParams(by_id={})
    assert params3.get("4-4", "welding_min", default=30) == 30.0
```

- [ ] **Step 4.2: `schedule_optimizer.py :455-467` 리팩터**

Read 현재 코드:

```python
# 용접 시간 (4-4): constraint_config에서 welding_min 읽기
welding_cfg = (
    db.query(ConstraintConfig)
    .filter(ConstraintConfig.constraint_id == "4-4")
    .first()
)
welding_min = _DEFAULT_WELDING_MIN
if welding_cfg and welding_cfg.params_json:
    welding_min = float(
        welding_cfg.params_json.get("welding_min", _DEFAULT_WELDING_MIN)
    )
```

Replace with:

```python
# 용접 시간 (4-4): ConstraintParams 에서 읽기 (하위 호환 default 유지)
welding_min = constraint_params.get(
    "4-4", "welding_min", default=_DEFAULT_WELDING_MIN,
)
```

상단에 ConstraintConfig import 가 본 함수에서만 사용됐다면 제거.

- [ ] **Step 4.3: `cp_sat_optimizer.py :488-497` 리팩터 (동일 패턴)**

Read 해당 블록. 대체:

```python
welding_min = constraint_params.get(
    "4-4", "welding_min", default=_DEFAULT_WELDING_MIN,
)
```

진입부에 `constraint_params = ConstraintParams.load(db)` 가 이미 Task 3 에서 추가되었는지 확인. 안 됐으면 추가.

- [ ] **Step 4.4: 테스트 + 회귀**

Run: `cd backend && pytest tests/test_schedulers_color_4_2.py -v`
Expected: 5 passed

Run: `cd backend && pytest tests/ -x --tb=short`
Expected: all pass

- [ ] **Step 4.5: 커밋**

```bash
git add backend/app/services/schedule_optimizer.py backend/app/services/cp_sat_optimizer.py backend/tests/test_schedulers_color_4_2.py
git commit -m "refactor(scheduler): 4-4 welding 개별 ConstraintConfig 조회 → ConstraintParams 통합

- schedule_optimizer / cp_sat_optimizer 중복 조회 로직 제거
- _DEFAULT_WELDING_MIN=30 하위 호환 default 유지
- 이중 경로 drift 리스크 해소"
```

---

## Task 5: Alembic 마이그레이션 — updated_at + constraint_config_history

**Files:**

- Modify: `backend/app/infrastructure/models/constraint_config.py`
- Create: `backend/app/infrastructure/models/constraint_config_history.py`
- Create: `backend/alembic/versions/e1f2a3b4c5d6_add_constraint_config_history_and_timestamps.py`

- [ ] **Step 5.1: 모델 수정 — `constraint_config.updated_at` 추가**

Edit `backend/app/infrastructure/models/constraint_config.py`:

```python
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConstraintConfig(Base):
    __tablename__ = "constraint_config"

    constraint_id = Column(String(10), primary_key=True)
    constraint_name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=50)
    impact_level = Column(String(10))
    params_json = Column(JSONB, default={})
    applicable_processes = Column(JSONB, default=[])
    implementation_type = Column(String(20))
    notes = Column(Text)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
```

- [ ] **Step 5.2: 이력 모델 생성**

Create `backend/app/infrastructure/models/constraint_config_history.py`:

```python
"""ConstraintConfig params_json 변경 이력.

Why: 감사 관점 — 누가/언제/무엇을 바꿨는지 UI 에 노출 (design review L 결정).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConstraintConfigHistory(Base):
    __tablename__ = "constraint_config_history"

    history_id = Column(Integer, primary_key=True, autoincrement=True)
    constraint_id = Column(
        String(10),
        ForeignKey("constraint_config.constraint_id"),
        nullable=False,
        index=True,
    )
    changed_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    changed_by = Column(String(100), nullable=True)  # PoC: null 허용
    old_params_json = Column(JSONB, nullable=True)
    new_params_json = Column(JSONB, nullable=False)
```

- [ ] **Step 5.3: Alembic 리비전 생성**

Run: `cd backend && alembic revision -m "add constraint_config_history and timestamps"`
Expected: 새 파일 경로 출력 (예: `backend/alembic/versions/<hash>_add_constraint_config_history_and_timestamps.py`)

Note the generated file path (head hash will differ from the plan's `e1f2a3b4c5d6` — use actual).

- [ ] **Step 5.4: 마이그레이션 작성**

Edit the newly generated file:

```python
"""add constraint_config_history and timestamps

Revision ID: <generated>
Revises: c3d4e5f6a7b8  # 또는 현재 최신 head
Create Date: 2026-04-18 ...
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.add_column(
        "constraint_config",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "constraint_config_history",
        sa.Column("history_id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("constraint_id", sa.String(length=10), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.Column("old_params_json", postgresql.JSONB(), nullable=True),
        sa.Column("new_params_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["constraint_id"], ["constraint_config.constraint_id"]
        ),
    )
    op.create_index(
        "ix_constraint_config_history_constraint_id",
        "constraint_config_history",
        ["constraint_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_constraint_config_history_constraint_id")
    op.drop_table("constraint_config_history")
    op.drop_column("constraint_config", "updated_at")
```

- [ ] **Step 5.5: 마이그레이션 실행**

Run: `cd backend && alembic upgrade head`
Expected: `INFO [alembic.runtime.migration] Running upgrade ... -> <new rev>`

- [ ] **Step 5.6: 테스트 DB 로 역 마이그레이션 후 재적용 sanity**

Run: `cd backend && alembic downgrade -1 && alembic upgrade head`
Expected: 두 단계 모두 성공

- [ ] **Step 5.7: 커밋**

```bash
git add backend/app/infrastructure/models/constraint_config.py backend/app/infrastructure/models/constraint_config_history.py backend/alembic/versions/*_add_constraint_config_history_and_timestamps.py
git commit -m "feat(db): constraint_config_history 테이블 + updated_at 컬럼

- params_json 변경 이력 저장 (감사 관점)
- updated_at 타임스탬프 → drift-status API 에서 last_run_at 과 비교"
```

---

## Task 6: 변경 이력 기록 + drift-status + preview-impact API

**Files:**

- Modify: `backend/app/presentation/routes/constraints.py`
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/test_constraints_params.py`

- [ ] **Step 6.1: 테스트 먼저 (실패)**

Create `backend/tests/api/__init__.py` (empty).

Create `backend/tests/api/test_constraints_params.py`:

```python
"""ConstraintConfig 확장 API — history / drift-status / preview-impact 테스트."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)


client = TestClient(app)


def test_patch_records_history(db) -> None:
    """PATCH 시 old/new params_json 을 constraint_config_history 에 기록."""
    before = db.query(ConstraintConfigHistory).count()

    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": 200}},
    )
    assert resp.status_code == 200

    db.commit()  # test session 격리 회피 — route 가 commit 했으므로 refresh 만
    after = db.query(ConstraintConfigHistory).count()
    assert after == before + 1

    latest = (
        db.query(ConstraintConfigHistory)
        .order_by(ConstraintConfigHistory.history_id.desc())
        .first()
    )
    assert latest.constraint_id == "4-1"
    assert latest.new_params_json.get("stranding_min") == 200
    assert latest.old_params_json.get("stranding_min") == 210

    # cleanup — 시드값 복원
    client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": 210, "insulation_min": 60, "sheath_min": 30, "cv_min": 300}},
    )


def test_get_history(db) -> None:
    resp = client.get("/api/constraints/4-1/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "history" in body
    assert isinstance(body["history"], list)


def test_drift_status_returns_dirty_flag() -> None:
    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "dirty" in body
    assert isinstance(body["dirty"], bool)


def test_preview_impact_counts_planned_batches() -> None:
    """4-1 stranding_min 변경 시 영향받는 planned 배치 개수 + Δ 총 리드타임."""
    resp = client.post(
        "/api/constraints/4-1/preview-impact",
        json={"new_params_json": {"stranding_min": 0}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "affected_batch_count" in body
    assert "total_delta_min" in body
    assert body["affected_batch_count"] >= 0  # PoC DB 상태에 따라 가변
```

- [ ] **Step 6.2: 테스트 실행 — 실패 확인**

Run: `cd backend && pytest tests/api/test_constraints_params.py -v`
Expected: 4 FAIL (엔드포인트 없음)

- [ ] **Step 6.3: `constraints.py` — PATCH 이력 기록 추가**

Edit `backend/app/presentation/routes/constraints.py` — `update_constraint` 수정:

```python
@router.patch("/{constraint_id}", summary="제약조건 수정 (on/off, 파라미터)")
def update_constraint(constraint_id: str, body: dict, db: Session = Depends(get_db)):
    row = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == constraint_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"제약조건 '{constraint_id}' 없음")

    # 변경 이력 기록 — params_json 이 실제로 바뀐 경우만
    if "params_json" in body:
        old_params = dict(row.params_json or {})
        new_params = dict(body["params_json"])
        if old_params != new_params:
            history = ConstraintConfigHistory(
                constraint_id=constraint_id,
                old_params_json=old_params,
                new_params_json=new_params,
            )
            db.add(history)
        row.params_json = new_params

    if "is_enabled" in body:
        row.is_enabled = body["is_enabled"]
    if "priority" in body:
        row.priority = body["priority"]
    db.commit()
    return {"constraint_id": constraint_id, "updated": True}
```

상단 import 추가:

```python
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
```

- [ ] **Step 6.4: history 엔드포인트 추가**

Append to `constraints.py`:

```python
@router.get("/{constraint_id}/history", summary="제약조건 변경 이력")
def get_constraint_history(constraint_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(ConstraintConfigHistory)
        .filter(ConstraintConfigHistory.constraint_id == constraint_id)
        .order_by(ConstraintConfigHistory.changed_at.desc())
        .limit(50)
        .all()
    )
    return {
        "constraint_id": constraint_id,
        "history": [
            {
                "history_id": r.history_id,
                "changed_at": r.changed_at.isoformat(),
                "changed_by": r.changed_by,
                "old_params_json": r.old_params_json,
                "new_params_json": r.new_params_json,
            }
            for r in rows
        ],
    }
```

- [ ] **Step 6.5: drift-status 엔드포인트**

Append:

```python
from sqlalchemy import func
from app.infrastructure.models.schedule_task import ScheduleTask


@router.get("/drift-status", summary="ConstraintConfig 편집 후 재실행 필요 여부")
def get_drift_status(db: Session = Depends(get_db)):
    """ConstraintConfig 최신 updated_at > ScheduleTask 최신 created_at 이면 dirty=true.

    Why: Silent drift 방지 — UI 상단 배너 트리거용. ScheduleTask.created_at 을
    "마지막 auto_schedule 실행 시각" 프록시로 사용 (auto_schedule 이 항상
    ScheduleTask 를 재생성하므로).
    """
    latest_constraint = db.query(func.max(ConstraintConfig.updated_at)).scalar()
    latest_schedule = db.query(func.max(ScheduleTask.created_at)).scalar()

    if latest_constraint is None:
        dirty = False
    elif latest_schedule is None:
        dirty = True
    else:
        dirty = latest_constraint > latest_schedule

    return {
        "dirty": dirty,
        "latest_constraint_updated_at": (
            latest_constraint.isoformat() if latest_constraint else None
        ),
        "latest_schedule_run_at": (
            latest_schedule.isoformat() if latest_schedule else None
        ),
    }
```

**주의**: `ScheduleTask.created_at` 필드가 실재하는지 확인. 없으면 `run_label` 에서 시각 파싱하거나 `ProductionBatch.created_at` 으로 fallback. 1회 grep 필요:

Run: `cd backend && grep -n "created_at" app/infrastructure/models/schedule_task.py`

없으면 `ProductionBatch.created_at` 사용하도록 위 코드 수정.

- [ ] **Step 6.6: preview-impact 엔드포인트**

Append:

```python
from app.infrastructure.models.production_batch import ProductionBatch


@router.post(
    "/{constraint_id}/preview-impact",
    summary="파라미터 변경 시 영향받는 planned 배치 개수/Δ",
)
def preview_impact(
    constraint_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """PoC: 4-1 stranding_min 변경만 정확히 계산. 다른 제약은 count 0 반환.

    반환: affected_batch_count, total_delta_min
    """
    new_params = (body or {}).get("new_params_json", {})

    if constraint_id == "4-1" and "stranding_min" in new_params:
        current_row = (
            db.query(ConstraintConfig)
            .filter(ConstraintConfig.constraint_id == "4-1")
            .first()
        )
        current = float(
            (current_row.params_json or {}).get("stranding_min", 210)
        ) if current_row else 210.0
        new_val = float(new_params["stranding_min"])
        delta = new_val - current

        # planned 연선 배치 중 현재 setup_time_min 이 current 와 같은 건만 카운트
        # (= fallback 으로 들어간 배치. SpeedMaster override 받은 배치는 제외)
        count_q = db.query(func.count(ProductionBatch.batch_id)).filter(
            ProductionBatch.status == "planned",
            ProductionBatch.setup_time_min == current,
        )
        count = count_q.scalar() or 0
        total_delta = delta * count

        return {
            "affected_batch_count": int(count),
            "total_delta_min": float(total_delta),
            "current_value": current,
            "new_value": new_val,
        }

    return {
        "affected_batch_count": 0,
        "total_delta_min": 0.0,
        "note": f"preview-impact 는 PoC 범위에서 4-1 stranding_min 만 지원",
    }
```

- [ ] **Step 6.7: 테스트 실행 — 통과 확인**

Run: `cd backend && pytest tests/api/test_constraints_params.py -v`
Expected: 4 passed

- [ ] **Step 6.8: 전체 회귀**

Run: `cd backend && pytest tests/ -x --tb=short`
Expected: all pass

- [ ] **Step 6.9: 커밋**

```bash
git add backend/app/presentation/routes/constraints.py backend/tests/api/
git commit -m "feat(api): constraints history/drift-status/preview-impact 엔드포인트

- PATCH /constraints/{id} 시 params_json 변경 이력 자동 기록
- GET /constraints/{id}/history — 이력 타임라인 (UI 탭용)
- GET /constraints/drift-status — 저장 후 재실행 미완료 감지 (배너 트리거)
- POST /constraints/{id}/preview-impact — 4-1 stranding_min 변경 영향 배치 수/Δ"
```

---

## Task 7: 프론트 — params 편집 카드 폼 (4-1/4-2/4-4)

**Files:**

- Modify: `frontend/src/app/(main)/master/constraints/page.tsx`
- Create: `frontend/src/app/(main)/master/constraints/components/ParamEditor.tsx`

- [ ] **Step 7.1: Next.js 16 컨벤션 사전 확인**

Read `frontend/node_modules/next/dist/docs/` 에서 Client Component / use("server") 관련 가이드 훑기. 특히 React 19 + Next 16 조합에서 `useState` 갱신 패턴 변경 여부.

Run: `ls frontend/node_modules/next/dist/docs/ 2>/dev/null | head -10`

필요한 경우 Skill("pwc-design") 로 samildevkit 디자인 토큰·컴포넌트 규칙 확인.

- [ ] **Step 7.2: ParamEditor 컴포넌트 생성**

Create `frontend/src/app/(main)/master/constraints/components/ParamEditor.tsx`:

```tsx
"use client";

import { useState } from "react";

interface ParamSpec {
  key: string;
  label: string;
  unit: string; // "분"
  helperText: string;
  seedDefault: number;
}

interface ConstraintSpec {
  constraint_id: string;
  constraint_name: string;
  params: ParamSpec[];
}

export const EDITABLE_CONSTRAINTS: ConstraintSpec[] = [
  {
    constraint_id: "4-1",
    constraint_name: "규격교체 시간",
    params: [
      {
        key: "stranding_min",
        label: "연선 규격교체",
        unit: "분",
        helperText: "연선 공정에서 SQ 다른 제품으로 바뀔 때 설비 재조정 시간",
        seedDefault: 210,
      },
      {
        key: "insulation_min",
        label: "저압절연 규격교체",
        unit: "분",
        helperText: "저압절연 공정 SQ 전환 시간",
        seedDefault: 60,
      },
      {
        key: "sheath_min",
        label: "시스 규격교체",
        unit: "분",
        helperText: "시스 공정 SQ 전환 시간",
        seedDefault: 30,
      },
      {
        key: "cv_min",
        label: "고압절연(CV) 규격교체",
        unit: "분",
        helperText: "고압절연 CV 공정 SQ 전환 시간",
        seedDefault: 300,
      },
    ],
  },
  {
    constraint_id: "4-2",
    constraint_name: "색상교체 시간",
    params: [
      {
        key: "sheath_color_min",
        label: "시스 색상교체",
        unit: "분",
        helperText:
          "저압시스 공정 색상 전환 시간 (SpeedMaster 값 없을 때 fallback)",
        seedDefault: 120,
      },
    ],
  },
  {
    constraint_id: "4-4",
    constraint_name: "용접 시간",
    params: [
      {
        key: "welding_min",
        label: "연선 용접",
        unit: "분",
        helperText: "연선 공정 스플라이스 로트 용접 시간",
        seedDefault: 30,
      },
    ],
  },
];

function formatMin(m: number): string {
  if (m <= 0) return "0분";
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h === 0) return `${mm}분`;
  if (mm === 0) return `${h}시간`;
  return `${h}시간 ${mm}분`;
}

interface ParamRowProps {
  spec: ParamSpec;
  value: number;
  onChange: (v: number) => void;
  invalid: boolean;
}

function ParamRow({ spec, value, onChange, invalid }: ParamRowProps) {
  return (
    <div className="flex items-start gap-4 py-3">
      <div className="flex-1">
        <label className="block text-sm font-medium">{spec.label}</label>
        <p className="mt-0.5 text-xs text-gray-500">{spec.helperText}</p>
      </div>
      <div className="w-52">
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            step={1}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className={`w-24 rounded border px-2 py-1 text-right text-sm ${invalid ? "border-red-500" : "border-gray-300"}`}
          />
          <span className="text-xs text-gray-600">{spec.unit}</span>
          <span className="text-xs text-gray-400">({formatMin(value)})</span>
        </div>
        {invalid && (
          <p className="mt-1 text-xs text-red-600">
            0 이상의 숫자를 입력하세요
          </p>
        )}
      </div>
    </div>
  );
}

interface ParamEditorProps {
  constraints: { constraint_id: string; params_json: Record<string, number> }[];
  onParamsChange: (id: string, params: Record<string, number>) => void;
  onRestoreDefault: (id: string) => void;
}

export function ParamEditor({
  constraints,
  onParamsChange,
  onRestoreDefault,
}: ParamEditorProps) {
  return (
    <div className="space-y-4">
      {EDITABLE_CONSTRAINTS.map((spec) => {
        const dbRow = constraints.find(
          (c) => c.constraint_id === spec.constraint_id,
        );
        const current = dbRow?.params_json || {};
        return (
          <div
            key={spec.constraint_id}
            className="rounded-lg border border-gray-200 bg-white"
          >
            <div className="flex items-center justify-between border-b bg-gray-50 px-4 py-2">
              <h3 className="text-sm font-semibold">
                {spec.constraint_id} · {spec.constraint_name}
              </h3>
              <button
                onClick={() => onRestoreDefault(spec.constraint_id)}
                className="text-xs text-blue-600 hover:underline"
              >
                기본값으로 되돌리기
              </button>
            </div>
            <div className="divide-y px-4">
              {spec.params.map((p) => {
                const value = Number(current[p.key] ?? p.seedDefault);
                const invalid = isNaN(value) || value < 0;
                return (
                  <ParamRow
                    key={p.key}
                    spec={p}
                    value={value}
                    invalid={invalid}
                    onChange={(v) =>
                      onParamsChange(spec.constraint_id, {
                        ...current,
                        [p.key]: v,
                      })
                    }
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 7.3: `page.tsx` 에 탭 구조 + ParamEditor 통합**

Edit `frontend/src/app/(main)/master/constraints/page.tsx`. 기존 토글 테이블은 **"제약 on/off" 탭**으로 보존, 신규 **"파라미터" 탭**에 ParamEditor 배치. `"변경 이력"` 탭은 Task 8 에서.

기존 파일 첫 줄들 유지, `export default function ConstraintsPage()` 내부를 개편:

```tsx
// 상단에 추가
import { EDITABLE_CONSTRAINTS, ParamEditor } from "./components/ParamEditor";

type Tab = "params" | "toggle" | "history";
```

`useState` 추가:

```tsx
const [tab, setTab] = useState<Tab>("params");
const [editedParams, setEditedParams] = useState<
  Record<string, Record<string, number>>
>({});
```

렌더 상단에 탭 네비:

```tsx
<div className="mb-4 flex gap-2 border-b">
  {(["params", "toggle", "history"] as Tab[]).map((t) => (
    <button
      key={t}
      onClick={() => setTab(t)}
      className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-blue-600 font-semibold" : "text-gray-500"}`}
    >
      {t === "params"
        ? "파라미터"
        : t === "toggle"
          ? "제약 on/off"
          : "변경 이력"}
    </button>
  ))}
</div>
```

`tab === "params"` 분기에서 `<ParamEditor ... />` 렌더. Props:

- `constraints`: 기존 state 에서 4-1/4-2/4-4 만 필터
- `onParamsChange`: `editedParams[id] = patch` 로 set
- `onRestoreDefault`: `editedParams[id] = Object.fromEntries(EDITABLE_CONSTRAINTS.find(c => c.constraint_id === id)!.params.map(p => [p.key, p.seedDefault]))`

저장 버튼 (Task 8 의 모달 트리거):

```tsx
{
  tab === "params" && Object.keys(editedParams).length > 0 && (
    <div className="mt-4 flex justify-end">
      <button
        onClick={() => setModalOpen(true)}
        className="rounded bg-blue-600 px-4 py-2 text-sm text-white"
      >
        저장
      </button>
    </div>
  );
}
```

- [ ] **Step 7.4: 프론트 타입체크 + 빌드**

Run: `cd frontend && npm run build`
Expected: build success. 에러 시 Next.js 16 API 변경 영향일 가능성 — `node_modules/next/dist/docs/` 재확인.

- [ ] **Step 7.5: dev 서버 + 수동 확인**

Run: `cd frontend && npm run dev`, browse to `http://localhost:3000/master/constraints`.

확인:

- "파라미터" 탭이 기본 선택
- 4-1/4-2/4-4 카드 노출
- 값 편집 시 "(N시간 M분)" 병기 업데이트
- "기본값으로 되돌리기" 클릭 시 시드값 복원

- [ ] **Step 7.6: verify-pwc-design**

Skill("verify-pwc-design") 호출 — 이탤릭 오용, Orange 배경, 텍스트 컬러, CSS 변수 미사용 등 체크. 문제 수정.

- [ ] **Step 7.7: 커밋**

```bash
git add frontend/src/app/\(main\)/master/constraints/
git commit -m "feat(frontend): /master/constraints 파라미터 편집 탭 추가 (4-1/4-2/4-4)

- ParamEditor 컴포넌트 — 카드 + 시간 병기 + 기본값 복원
- 기존 토글 테이블은 '제약 on/off' 탭으로 보존
- 변경 이력 탭 자리 확보 (Task 8에서 구현)"
```

---

## Task 8: 프론트 — drift 배지 + 저장 모달 + 변경 이력 탭

**Files:**

- Create: `frontend/src/app/(main)/master/constraints/components/DriftBanner.tsx`
- Create: `frontend/src/app/(main)/master/constraints/components/SaveModal.tsx`
- Create: `frontend/src/app/(main)/master/constraints/components/HistoryTab.tsx`
- Modify: `frontend/src/app/(main)/master/constraints/page.tsx`

- [ ] **Step 8.1: DriftBanner 컴포넌트**

Create `frontend/src/app/(main)/master/constraints/components/DriftBanner.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export function DriftBanner() {
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    fetch(`${API}/constraints/drift-status`)
      .then((r) => r.json())
      .then((data) => setDirty(Boolean(data?.dirty)))
      .catch(() => setDirty(false));
  }, []);

  if (!dirty) return null;

  return (
    <div className="mb-4 flex items-center gap-3 rounded border border-yellow-400 bg-yellow-50 px-4 py-2 text-sm">
      <span>⚠️</span>
      <span className="flex-1">
        저장된 변경이 아직 스케줄에 반영되지 않았습니다.
      </span>
      <a href="/plan-pipeline" className="text-blue-600 hover:underline">
        작업지시서 업데이트로 이동 →
      </a>
    </div>
  );
}
```

(실제 Stage1/update 경로가 `/plan-pipeline` 아닐 수 있음 — 기존 프론트 라우트 구조 확인 후 수정)

- [ ] **Step 8.2: SaveModal 컴포넌트**

Create `frontend/src/app/(main)/master/constraints/components/SaveModal.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface PreviewImpact {
  affected_batch_count: number;
  total_delta_min: number;
  current_value?: number;
  new_value?: number;
  note?: string;
}

interface SaveModalProps {
  open: boolean;
  edits: Record<string, Record<string, number>>;
  onClose: () => void;
  onSaved: () => void;
}

export function SaveModal({ open, edits, onClose, onSaved }: SaveModalProps) {
  const [preview, setPreview] = useState<Record<string, PreviewImpact>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const fetchAll = async () => {
      const results: Record<string, PreviewImpact> = {};
      for (const [id, params] of Object.entries(edits)) {
        const resp = await fetch(`${API}/constraints/${id}/preview-impact`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_params_json: params }),
        });
        results[id] = await resp.json();
      }
      setPreview(results);
    };
    fetchAll();
  }, [open, edits]);

  if (!open) return null;

  const doSave = async (triggerStage1: boolean) => {
    setSaving(true);
    try {
      for (const [id, params] of Object.entries(edits)) {
        await fetch(`${API}/constraints/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ params_json: params }),
        });
      }
      if (triggerStage1) {
        const today = new Date().toISOString().slice(0, 10);
        const form = new FormData();
        form.append("upload_mode", "incremental");
        form.append("base_date", today);
        // Note: erp_file 재업로드 없이 재실행 하는 별도 엔드포인트 필요.
        // PoC 단계: 사용자에게 "Stage1/update 페이지로 이동해 재실행" 안내
        // 또는 백엔드에 POST /constraints/apply-now 추가 (선택).
        // 여기서는 단순히 라우팅만 이동.
        window.location.href = "/plan-pipeline";
        return;
      }
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-lg bg-white p-6">
        <h3 className="mb-3 text-lg font-bold">변경 사항 확인</h3>

        <div className="mb-4 space-y-2 text-sm">
          {Object.entries(preview).map(([id, p]) => (
            <div key={id} className="rounded border px-3 py-2">
              <div className="font-mono text-xs text-gray-500">{id}</div>
              {p.current_value !== undefined && (
                <div>
                  {p.current_value} → {p.new_value} 분
                </div>
              )}
              <div className="text-xs text-gray-600">
                영향 계획 배치: {p.affected_batch_count}건{" · "}
                예상 Δ: {p.total_delta_min > 0 ? "+" : ""}
                {p.total_delta_min}분
              </div>
              {p.note && <div className="text-xs text-gray-400">{p.note}</div>}
            </div>
          ))}
        </div>

        <p className="mb-4 text-xs text-gray-500">
          진행중(in_progress) 배치와 기준일자 이전 확정 배치는 변경되지
          않습니다.
        </p>

        <div className="flex flex-col gap-2">
          <button
            disabled={saving}
            onClick={() => doSave(true)}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            지금 기존 계획에도 반영 (Stage1/update 이동)
          </button>
          <button
            disabled={saving}
            onClick={() => doSave(false)}
            className="rounded border border-blue-600 px-4 py-2 text-sm text-blue-600 disabled:opacity-50"
          >
            다음 자동배열부터 적용
          </button>
          <button
            disabled={saving}
            onClick={onClose}
            className="rounded px-4 py-2 text-sm text-gray-500"
          >
            취소
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 8.3: HistoryTab 컴포넌트**

Create `frontend/src/app/(main)/master/constraints/components/HistoryTab.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface HistoryRow {
  history_id: number;
  changed_at: string;
  changed_by: string | null;
  old_params_json: Record<string, number> | null;
  new_params_json: Record<string, number>;
}

const IDS = ["4-1", "4-2", "4-4"];

export function HistoryTab() {
  const [rows, setRows] = useState<
    Array<HistoryRow & { constraint_id: string }>
  >([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAll = async () => {
      const all: Array<HistoryRow & { constraint_id: string }> = [];
      for (const id of IDS) {
        const resp = await fetch(`${API}/constraints/${id}/history`);
        const data = await resp.json();
        for (const r of data.history as HistoryRow[]) {
          all.push({ ...r, constraint_id: id });
        }
      }
      all.sort((a, b) => b.changed_at.localeCompare(a.changed_at));
      setRows(all);
      setLoading(false);
    };
    fetchAll();
  }, []);

  if (loading)
    return <div className="p-4 text-sm text-gray-400">로딩 중...</div>;
  if (rows.length === 0) {
    return (
      <div className="p-4 text-sm text-gray-500">변경 이력이 없습니다.</div>
    );
  }

  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div
          key={`${r.constraint_id}-${r.history_id}`}
          className="rounded border px-3 py-2 text-sm"
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-xs text-gray-500">
              {r.constraint_id}
            </span>
            <span className="text-xs text-gray-400">
              {new Date(r.changed_at).toLocaleString("ko-KR")}
              {r.changed_by && ` · ${r.changed_by}`}
            </span>
          </div>
          <pre className="mt-1 overflow-x-auto rounded bg-gray-50 p-2 text-xs">
            {`- ${JSON.stringify(r.old_params_json, null, 0)}
+ ${JSON.stringify(r.new_params_json, null, 0)}`}
          </pre>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 8.4: `page.tsx` 에 DriftBanner + SaveModal + HistoryTab 연결**

Edit `frontend/src/app/(main)/master/constraints/page.tsx`:

상단 import:

```tsx
import { DriftBanner } from "./components/DriftBanner";
import { SaveModal } from "./components/SaveModal";
import { HistoryTab } from "./components/HistoryTab";
```

`useState` 추가: `const [modalOpen, setModalOpen] = useState(false);`

탭 영역 상단에 `<DriftBanner />`.

`tab === "history"` 분기: `<HistoryTab />`

저장 버튼 바로 아래에:

```tsx
<SaveModal
  open={modalOpen}
  edits={editedParams}
  onClose={() => setModalOpen(false)}
  onSaved={() => {
    setEditedParams({});
    window.location.reload();
  }}
/>
```

- [ ] **Step 8.5: 빌드 + dev 검증**

Run: `cd frontend && npm run build`
Expected: success.

Run: `cd frontend && npm run dev`, 브라우저에서:

- 파라미터 편집 → 저장 → 모달 노출, 영향 건수·Δ 표시
- "다음 자동배열부터 적용" → DB PATCH + 모달 닫힘 + 배너 ON 예상
- "변경 이력" 탭에 방금 변경 row 추가됨 확인
- DriftBanner 노출 확인

- [ ] **Step 8.6: verify-pwc-design 재실행**

Skill("verify-pwc-design") — 새 컴포넌트 3개 + 모달 디자인 검증.

- [ ] **Step 8.7: 커밋**

```bash
git add frontend/src/app/\(main\)/master/constraints/
git commit -m "feat(frontend): drift 배지 + 저장 모달 + 변경 이력 탭

- DriftBanner: /constraints/drift-status dirty=true 시 상단 띠 + CTA 링크
- SaveModal: preview-impact 호출해 영향 배치 N건 · Δ 표시, 즉시 반영/다음부터 2지선다
- HistoryTab: 4-1/4-2/4-4 변경 이력 통합 타임라인 (diff 포맷)"
```

---

## Task 9: E2E Playwright 시나리오

**Files:**

- Create: `frontend/e2e/constraint-config-params.spec.ts`

- [ ] **Step 9.1: 기존 e2e 패턴 훑어보기**

Read `frontend/e2e/master-pages.spec.ts` 한 번 — 기존 /master/\* 페이지 검증 패턴 파악.

- [ ] **Step 9.2: 시나리오 작성**

Create `frontend/e2e/constraint-config-params.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

const BASE = process.env.BASE_URL || "http://localhost:3000";

test.describe("ConstraintConfig 파라미터 편집", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/master/constraints`);
    await expect(page.getByText("제약조건 관리")).toBeVisible();
  });

  test("시나리오 1 — 다음 자동배열부터 적용", async ({ page }) => {
    await page.getByRole("button", { name: "파라미터" }).click();

    // 4-1 stranding_min 입력 필드 (첫 카드 첫 행)
    const strandingInput = page.locator('input[type="number"]').first();
    await strandingInput.fill("0");

    // 분→시간 병기 표시 확인
    await expect(page.getByText("(0분)")).toBeVisible();

    // 저장 버튼 → 모달
    await page.getByRole("button", { name: "저장" }).click();
    await expect(page.getByText("변경 사항 확인")).toBeVisible();

    // 영향 배치 카운트 표시
    await expect(page.getByText(/영향 계획 배치: \d+건/)).toBeVisible();

    await page.getByRole("button", { name: "다음 자동배열부터 적용" }).click();

    // drift 배너 ON
    await expect(
      page.getByText("저장된 변경이 아직 스케줄에 반영되지 않았습니다"),
    ).toBeVisible({ timeout: 5000 });

    // 이력 탭에 row 생김
    await page.getByRole("button", { name: "변경 이력" }).click();
    await expect(page.getByText(/"stranding_min":\s?0/)).toBeVisible();
  });

  test("시나리오 2 — 기본값 복원", async ({ page }) => {
    await page.getByRole("button", { name: "파라미터" }).click();

    await page
      .getByText("4-1 · 규격교체 시간")
      .locator("..")
      .getByRole("button", { name: "기본값으로 되돌리기" })
      .click();

    const strandingInput = page.locator('input[type="number"]').first();
    await expect(strandingInput).toHaveValue("210");
    await expect(page.getByText("(3시간 30분)")).toBeVisible();
  });

  test("시나리오 3 — 시간 단위 병기 (N 결정)", async ({ page }) => {
    await page.getByRole("button", { name: "파라미터" }).click();
    await expect(page.getByText("(3시간 30분)")).toBeVisible(); // 4-1 210분
    await expect(page.getByText("(2시간)")).toBeVisible(); // 4-2 120분
    await expect(page.getByText("(30분)")).toBeVisible(); // 4-4 30분
  });
});
```

- [ ] **Step 9.3: 백엔드 + 프론트 띄우고 실행**

Run backend:

```bash
cd backend && uvicorn app.main:app --reload
```

Run frontend:

```bash
cd frontend && npm run dev
```

Run e2e:

```bash
cd frontend && npx playwright test e2e/constraint-config-params.spec.ts --headed
```

Expected: 3 passed

실패 시 screenshot `frontend/test-results/` 에 저장됨 — 확인 후 셀렉터 조정.

- [ ] **Step 9.4: 테스트 완료 후 DB 정리**

시나리오 1이 실제 DB 값을 바꾸므로, 테스트 후 원복:

```bash
curl -X PATCH http://localhost:8000/api/constraints/4-1 \
  -H "Content-Type: application/json" \
  -d '{"params_json": {"stranding_min": 210, "insulation_min": 60, "sheath_min": 30, "cv_min": 300}}'
```

또는 테스트 `afterEach` 훅에 같은 API 호출 추가.

- [ ] **Step 9.5: 커밋**

```bash
git add frontend/e2e/constraint-config-params.spec.ts
git commit -m "test(e2e): constraint-config 파라미터 편집 시나리오 3종

- 다음 자동배열부터 적용 — drift 배너 + 이력 탭 검증
- 기본값 복원 — 210 + '3시간 30분' 병기 확인
- 시간 단위 병기 UX (N 결정)"
```

---

## Task 10: 문서 업데이트

**Files:**

- Modify: `README.md` 또는 `CHANGELOG.md`
- Modify: `docs/changeover-time-analysis.md` (참고 연결)

- [ ] **Step 10.1: CHANGELOG 한 문단 추가**

Edit `README.md` 또는 별도 CHANGELOG 에:

```markdown
### 2026-04-18 — ConstraintConfig 파라미터 UI 편집 (4-1/4-2/4-4)

- `/master/constraints` 페이지 **"파라미터" 탭** 추가 — 규격교체/색상교체/용접 시간을 UI 에서 편집 가능
- 저장 시 모달 — 영향 배치 N건 프리뷰 + "지금 기존 계획에도 반영" vs "다음 자동배열부터 적용" 선택
- 변경 이력 탭 + Silent drift 배너 (저장 후 재실행 미완료 시)
- 연선 메인 배치 `else 0.0` 과 드럼 권취(4-3) 는 범위 밖 — 별도 스펙 예정
- 스펙: `docs/specs/2026-04-18-constraint-config-params-ui-design.md`
- 플랜: `docs/plans/2026-04-18-constraint-config-params-ui.md`
```

- [ ] **Step 10.2: changeover-time-analysis.md 에 out-of-scope 노트 추가**

Edit `docs/changeover-time-analysis.md` 맨 아래:

```markdown
---

## 2026-04-18 업데이트 — UI 편집 지원

본 문서가 지적한 시드 불일치(연선 210 vs 참조 240) 는 **UI 편집으로 정정 가능**하다. `/master/constraints` 파라미터 탭에서 `4-1 stranding_min` 을 240 으로 변경하면 다음 자동배열부터 반영된다. 다만 SpeedMaster 연선 row 추가는 별도 과제로 남아있어, fallback 경로에만 영향을 준다.
```

- [ ] **Step 10.3: 커밋**

```bash
git add README.md docs/changeover-time-analysis.md
git commit -m "docs: ConstraintConfig 파라미터 UI 편집 기능 안내 + changeover 분석 업데이트"
```

---

## Self-Review

### Spec coverage

Spec v2 요구사항 → Task 매핑:

- §1.1 정직 고지(210 vs 240, 우선순위) → Task 10.2 changeover-time 업데이트 + UI 안내 (Task 7 helperText)
- §2 목표: 4-1/4-2/4-4 파라미터 편집 → Task 7, 8
- §2 No-op 불변식 → Task 2 회귀 guard + Task 3 시맨틱 교정 정직 고지
- §2 Silent drift 방지 → Task 6.5 drift-status API + Task 8.1 DriftBanner
- §3 범위 (4-3 제외) → Task 6.6 preview-impact note, Task 10.1 CHANGELOG
- §4.1 프리페치 캐시 → Task 1 (전역 lru_cache 금지 원칙 명시)
- §4.2 호출부 리팩터 → Task 2, 3, 4
- §4.3 API → Task 6
- §4.4 UI → Task 7, 8
- §5 데이터 흐름 (저장 모달) → Task 8.2
- §6 Fail-fast → Task 1, 6 (RuntimeError/404 메시지)
- §7 테스트 → Task 2, 3, 4, 6, 9 (단위/통합/동등성/E2E)
- §8 커밋 분할 → 10개 task = 10개 atomic commit
- §10 마이그레이션 → Task 5

### Placeholder scan

- "TBD"/"TODO" 없음
- "적절한 에러 처리" 같은 모호 표현 없음 — 구체적 RuntimeError / 404 명시
- 커밋 메시지, 코드, 명령어 전부 구체
- 단, Task 3.5 에서 `cp_sat_optimizer.py` 의 **정확한 줄번호**가 명시되지 않음 — agent 가 grep 으로 찾아야 함. 실행 가능(grep 단계 포함). 명시 줄번호 대신 "schedule_optimizer:817-819 대칭 위치" 로 설명.
- Task 6.5 `ScheduleTask.created_at` 존재 확인 단계 명시 — 없으면 fallback 지시 포함.
- Task 7.1 Next.js 16 사전 확인 단계 있음 — AGENTS.md 경고 대응.

### Type consistency

- `ConstraintParams` 이름 Task 1, 2, 3, 4 에서 일관 사용
- `_resolve_color_change_min(sm_color_min, params)` 시그니처 Task 3.1, 3.3, 3.5 일치
- API prefix 모두 `/api/constraints/*` (기존 라우터 확장)
- 프론트 탭 키 `"params" | "toggle" | "history"` Task 7.3, 8.4 일치

모든 체크 통과. 구현 플랜 완성.

---
