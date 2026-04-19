# SpeedMaster 셋업 시간 인라인 편집 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/master/speed` 페이지에서 SpeedMaster 의 4 컬럼(`setup_spec_min`, `setup_color_min`, `setup_compound_min`, `setup_start_min`)을 onBlur autosave 로 인라인 편집, drift-status 에 SpeedMaster.updated_at 포함.

**Architecture:**

- 백엔드: `SpeedMaster.updated_at` 컬럼 + Alembic 1개. 화이트리스트 Pydantic 모델로 4컬럼만 허용하는 신규 `PATCH /master/speed_master/{id}/setup-params` (기존 generic CRUD 는 그대로 둠). `/constraints/drift-status` 확장.
- 프론트: 기존 `/master/speed/page.tsx` 확장. `NumberCell` 컴포넌트 신규 (onBlur PATCH + 성공/실패 피드백 + 시간 병기). 상단 `DriftBanner` 재사용 (Task 8 컴포넌트).
- 설계: `docs/specs/2026-04-18-speedmaster-setup-edit-design.md`

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic, Next.js 16 (webpack), React 19, Playwright, pytest, Postgres(Supabase)

**주의사항:**

- `frontend/AGENTS.md`: Next.js 16 의 API/관례가 training data와 다를 수 있음 — 새 패턴 도입 전 `node_modules/next/dist/docs/` 확인
- pwc-design(samildevkit) 규칙 준수 — 수동 UI 검증
- feedback_commit_verification: 각 태스크 완료 후 **검증 통과한 뒤에만** 커밋, 다음 태스크 착수
- 이전 스프린트의 `DriftBanner` (`frontend/src/app/(main)/master/constraints/components/DriftBanner.tsx`) 를 재사용

---

## File Structure

```
backend/
  app/
    presentation/routes/
      master_data.py               (MODIFY)   - PATCH /speed_master/{id}/setup-params 추가
      constraints.py               (MODIFY)   - drift-status 핸들러 확장 (SpeedMaster.updated_at 포함)
    infrastructure/models/
      speed_master.py              (MODIFY)   - updated_at 컬럼
  alembic/versions/
    <new>_add_speedmaster_updated_at.py (NEW)
  tests/
    api/
      test_speedmaster_setup_patch.py (NEW)
    test_drift_status_speedmaster.py  (NEW)

frontend/
  src/app/(main)/master/speed/
    page.tsx                       (MODIFY)   - 편집 컬럼 + DriftBanner + 2컬럼 추가(compound/start)
    components/
      NumberCell.tsx               (NEW)      - 인라인 편집 셀 (onBlur PATCH)
  src/app/(main)/master/constraints/
    page.tsx                       (MODIFY)   - 파라미터 탭 상단에 우선순위 안내문 한 줄
  e2e/
    speedmaster-setup-edit.spec.ts (NEW)
  e2e/
    master-pages.spec.ts           (MODIFY 가능) - 기존 "선속 마스터 페이지 로드" 테스트가 깨지면 셀렉터 조정

docs/
  specs/2026-04-18-speedmaster-setup-edit-design.md   (기존, 참조)
  plans/2026-04-18-speedmaster-setup-edit.md          (현재 문서)
  CHANGELOG.md                                        (MODIFY, 한 문단 추가)
```

---

## Task 1: SpeedMaster.updated_at 컬럼 + Alembic 마이그레이션

**Files:**

- Modify: `backend/app/infrastructure/models/speed_master.py`
- Create: `backend/alembic/versions/<new>_add_speedmaster_updated_at.py`

- [ ] **Step 1.1: SpeedMaster 모델에 updated_at 추가**

Edit `backend/app/infrastructure/models/speed_master.py` 전체를 다음으로 교체:

```python
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String

from app.infrastructure.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SpeedMaster(Base):
    __tablename__ = "speed_master"

    speed_id = Column(Integer, primary_key=True, autoincrement=True)
    equipment_code = Column(
        String(20), ForeignKey("equipment_master.equipment_code"), nullable=False
    )
    product_type = Column(String(50))
    cross_section = Column(Numeric)
    line_speed_mpm = Column(Numeric)
    line_speed_hr = Column(Numeric)
    setup_start_min = Column(Numeric, default=0)
    setup_spec_min = Column(Numeric, default=0)
    setup_color_min = Column(Numeric, default=0)
    setup_compound_min = Column(Numeric, default=0)
    updated_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
```

- [ ] **Step 1.2: Alembic heads 확인**

Run: `cd backend && alembic heads`
Expected: 현재 head 해시 출력 (예: `708591555e9b` 또는 더 최근). 기록해둔다 (다음 단계 down_revision 확인용).

- [ ] **Step 1.3: Alembic 리비전 생성**

Run: `cd backend && alembic revision -m "add speedmaster updated_at"`
Expected: `Generating /Users/jaewookim/Desktop/Project/KBI_PoC/backend/alembic/versions/<hash>_add_speedmaster_updated_at.py ...` 출력. 생성된 파일 경로 기록.

- [ ] **Step 1.4: 마이그레이션 내용 작성**

생성된 파일을 편집. 기존 자동 생성된 `revision`, `down_revision`, `branch_labels`, `depends_on` 값은 그대로 두고 `upgrade()` 와 `downgrade()` 를 다음으로 교체:

```python
def upgrade() -> None:
    op.add_column(
        "speed_master",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("speed_master", "updated_at")
```

상단에 필요한 import 확인:

```python
from alembic import op
import sqlalchemy as sa
```

- [ ] **Step 1.5: 마이그레이션 적용**

Run: `cd backend && alembic upgrade head`
Expected: `INFO [alembic.runtime.migration] Running upgrade ... -> <new_rev>` 출력, 에러 없음.

- [ ] **Step 1.6: downgrade/upgrade sanity**

Run: `cd backend && alembic downgrade -1 && alembic upgrade head`
Expected: 두 단계 모두 성공.

- [ ] **Step 1.7: 기존 row backfill 확인**

Run:

```bash
cd backend && python -c "
from app.infrastructure.database import SessionLocal
from app.infrastructure.models.speed_master import SpeedMaster
s = SessionLocal()
rows = s.query(SpeedMaster).all()
null_count = sum(1 for r in rows if r.updated_at is None)
print(f'total={len(rows)}, null_updated_at={null_count}')
print(f'sample updated_at = {rows[0].updated_at}')
s.close()
"
```

Expected: `total=176, null_updated_at=0` (server_default=now() 로 backfill 됐으므로). sample 은 마이그레이션 시각.

- [ ] **Step 1.8: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add backend/app/infrastructure/models/speed_master.py backend/alembic/versions/*_add_speedmaster_updated_at.py
git status  # 정확히 2 파일 확인
git commit -m "$(cat <<'EOF'
feat(db): SpeedMaster.updated_at 컬럼 (drift-status 확장용)

- timezone-aware DateTime, default/onupdate = now()
- server_default=now() 로 기존 176 row 자동 backfill
- Task 5 (constraint_config.updated_at) 와 동일 패턴
EOF
)"
```

---

## Task 2: PATCH /master/speed_master/{id}/setup-params (화이트리스트)

**Files:**

- Modify: `backend/app/presentation/routes/master_data.py`
- Create: `backend/tests/api/test_speedmaster_setup_patch.py`

- [ ] **Step 2.1: 실패 테스트 작성 (5 cases)**

Create `backend/tests/api/test_speedmaster_setup_patch.py`:

```python
"""PATCH /master/speed_master/{id}/setup-params — 화이트리스트 4컬럼 편집."""

from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.speed_master import SpeedMaster


client = TestClient(app)


def _pick_one_id(db) -> int:
    row = db.query(SpeedMaster).first()
    assert row is not None, "speed_master seed required"
    return int(row.speed_id)


def test_patch_rejects_structural_field(db) -> None:
    """equipment_code 같은 구조 필드는 거부한다 (extra='forbid')."""
    sid = _pick_one_id(db)
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"equipment_code": "FOO"},
    )
    assert resp.status_code == 422


def test_patch_rejects_negative(db) -> None:
    sid = _pick_one_id(db)
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": -1},
    )
    assert resp.status_code == 422


def test_patch_updates_single_column(db) -> None:
    """4컬럼 중 하나만 바꾸면 나머지는 유지된다."""
    sid = _pick_one_id(db)
    before = (
        db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    )
    before_color = float(before.setup_color_min or 0)

    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": 999},
    )
    assert resp.status_code == 200
    db.commit()

    after = (
        db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    )
    assert float(after.setup_spec_min) == 999
    assert float(after.setup_color_min or 0) == before_color

    # cleanup
    client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": float(before.setup_spec_min or 0)},
    )


def test_patch_updates_updated_at(db) -> None:
    """PATCH 시 updated_at 이 갱신된다 (onupdate hook)."""
    sid = _pick_one_id(db)
    row = db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    original_updated_at = row.updated_at

    # no-op 이 아닌 실제 변경
    new_val = float(row.setup_start_min or 0) + 1
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_start_min": new_val},
    )
    assert resp.status_code == 200
    db.commit()

    db.refresh(row)
    assert row.updated_at > original_updated_at

    # cleanup
    client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_start_min": new_val - 1},
    )


def test_patch_returns_404_when_missing() -> None:
    resp = client.patch(
        "/api/master/speed_master/999999/setup-params",
        json={"setup_spec_min": 100},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2.2: 테스트 실패 확인**

Run: `cd backend && pytest tests/api/test_speedmaster_setup_patch.py -v`
Expected: 5 FAIL (`404` on all — 엔드포인트 없음).

- [ ] **Step 2.3: 엔드포인트 추가**

Edit `backend/app/presentation/routes/master_data.py`. 상단 import 에 Pydantic v2 도구 추가 (이미 있으면 생략):

```python
from pydantic import BaseModel, ConfigDict, Field
```

파일 맨 아래에 추가 (기존 generic PUT 바로 아래):

```python
class SpeedSetupUpdate(BaseModel):
    """SpeedMaster 셋업 시간 4컬럼 화이트리스트 모델.

    Why: generic PUT /master/speed_master/{id} 는 모든 컬럼을 허용한다.
    UI 에서 구조 필드(equipment_code, product_type, cross_section,
    line_speed_*) 오편집을 원천 차단하려고 별도 엔드포인트로 분리.
    """

    setup_spec_min: float | None = Field(default=None, ge=0)
    setup_color_min: float | None = Field(default=None, ge=0)
    setup_compound_min: float | None = Field(default=None, ge=0)
    setup_start_min: float | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


@router.patch(
    "/speed_master/{speed_id}/setup-params",
    summary="SpeedMaster 셋업 시간 4컬럼 편집 (화이트리스트)",
)
def patch_speed_setup_params(
    speed_id: int,
    body: SpeedSetupUpdate,
    db: Session = Depends(get_db),
):
    row = (
        db.query(SpeedMaster).filter(SpeedMaster.speed_id == speed_id).first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"speed_master id={speed_id} 없음")

    payload = body.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(row, key, value)

    db.commit()
    db.refresh(row)
    return _row_to_dict(row)
```

- [ ] **Step 2.4: 테스트 통과 확인**

Run: `cd backend && pytest tests/api/test_speedmaster_setup_patch.py -v`
Expected: 5 passed.

- [ ] **Step 2.5: 전체 회귀**

Run: `cd backend && pytest tests/ -x --tb=short --ignore=tests/test_routes.py`
Expected: all pass (test_routes.py 는 사전부터 실패하는 무관 테스트).

- [ ] **Step 2.6: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add backend/app/presentation/routes/master_data.py backend/tests/api/test_speedmaster_setup_patch.py
git status
git commit -m "$(cat <<'EOF'
feat(api): PATCH /master/speed_master/{id}/setup-params 화이트리스트

- Pydantic SpeedSetupUpdate: setup_spec/color/compound/start_min 4컬럼만 허용
- model_config extra='forbid' — 구조 필드(equipment_code 등) 원천 거부
- ge=0 — 음수 입력 거부
- exclude_unset — None 필드는 건드리지 않음 (부분 업데이트 보존)
- 기존 generic PUT /master/speed_master/{id} 는 그대로 유지 (별도 용도)
EOF
)"
```

---

## Task 3: drift-status 확장 (SpeedMaster 포함)

**Files:**

- Modify: `backend/app/presentation/routes/constraints.py`
- Create: `backend/tests/test_drift_status_speedmaster.py`

- [ ] **Step 3.1: 현재 drift-status 위치 확인**

Run: `cd backend && grep -n "drift-status\|_to_naive_utc\|latest_constraint" app/presentation/routes/constraints.py | head -15`
Expected: `drift-status` 엔드포인트 줄번호 (대략 119~140 근처), `_to_naive_utc` 헬퍼 줄번호. 기록해둔다.

- [ ] **Step 3.2: 실패 테스트 작성**

Create `backend/tests/test_drift_status_speedmaster.py`:

```python
"""drift-status 확장 — SpeedMaster.updated_at 이 최근이면 dirty=true."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster


client = TestClient(app)


def test_drift_status_dirty_when_speedmaster_updated_after_schedule(db) -> None:
    """SpeedMaster.updated_at 이 마지막 auto_schedule 실행보다 최근이면 dirty=true."""
    # SpeedMaster 한 row 의 updated_at 을 '지금' 으로 강제 갱신
    row = db.query(SpeedMaster).first()
    assert row is not None
    row.updated_at = datetime.now(timezone.utc)
    # ScheduleTask 최신 created_at 은 과거여야 성립.
    # 로컬 commit 을 유도해서 route 핸들러가 조회하도록 함.
    db.commit()

    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    # latest_constraint_updated_at 이 아니라 SpeedMaster 기준으로도 dirty 판단 가능해야 함
    assert "dirty" in body
    assert isinstance(body["dirty"], bool)


def test_drift_status_includes_speedmaster_field(db) -> None:
    """응답 스키마에 latest_speed_master_updated_at 이 포함되어야 UI 가 활용 가능."""
    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "latest_speed_master_updated_at" in body
```

- [ ] **Step 3.3: 실패 확인**

Run: `cd backend && pytest tests/test_drift_status_speedmaster.py -v`
Expected: `test_drift_status_includes_speedmaster_field` 는 FAIL (필드 없음). `test_drift_status_dirty_when_speedmaster_updated_after_schedule` 는 아직 확장되지 않아 `dirty=False` 또는 `True` 중 어느 쪽이 나와도 어설션 자체는 bool 이면 통과 — 하지만 필드 자체가 추가되면 의미있는 검증이 됨.

- [ ] **Step 3.4: drift-status 핸들러 확장**

Edit `backend/app/presentation/routes/constraints.py`. Step 3.1 에서 확인한 `get_drift_status` 함수를 통째로 다음으로 교체 (줄번호는 환경마다 다를 수 있음 — 해당 함수 본체 찾아서 교체):

```python
@router.get("/drift-status", summary="ConstraintConfig/SpeedMaster 편집 후 재실행 필요 여부")
def get_drift_status(db: Session = Depends(get_db)):
    """Silent drift 방지 — UI 상단 배너 트리거.

    Why: ConstraintConfig 또는 SpeedMaster 최신 updated_at 이 ScheduleTask
    최신 created_at 보다 나중이면 'dirty'. ScheduleTask.created_at 을
    '마지막 auto_schedule 실행 시각' 프록시로 사용.
    """
    latest_constraint = db.query(func.max(ConstraintConfig.updated_at)).scalar()
    latest_speed = db.query(func.max(SpeedMaster.updated_at)).scalar()
    latest_schedule = db.query(func.max(ScheduleTask.created_at)).scalar()

    candidates = [x for x in (latest_constraint, latest_speed) if x is not None]
    latest_edit = max(candidates) if candidates else None

    if latest_edit is None:
        dirty = False
    elif latest_schedule is None:
        dirty = True
    else:
        dirty = _to_naive_utc(latest_edit) > latest_schedule

    return {
        "dirty": dirty,
        "latest_constraint_updated_at": (
            latest_constraint.isoformat() if latest_constraint else None
        ),
        "latest_speed_master_updated_at": (
            latest_speed.isoformat() if latest_speed else None
        ),
        "latest_schedule_run_at": (
            latest_schedule.isoformat() if latest_schedule else None
        ),
    }
```

상단 import 에 SpeedMaster 추가 (이미 있는지 확인 후):

```python
from app.infrastructure.models.speed_master import SpeedMaster
```

- [ ] **Step 3.5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_drift_status_speedmaster.py -v`
Expected: 2 passed.

- [ ] **Step 3.6: 기존 constraints API 테스트 회귀**

Run: `cd backend && pytest tests/api/test_constraints_params.py -v`
Expected: 4 passed (dirty 필드·라우트 순서 등 여전히 OK).

- [ ] **Step 3.7: 전체 회귀**

Run: `cd backend && pytest tests/ -x --tb=short --ignore=tests/test_routes.py`
Expected: all pass.

- [ ] **Step 3.8: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add backend/app/presentation/routes/constraints.py backend/tests/test_drift_status_speedmaster.py
git status
git commit -m "$(cat <<'EOF'
feat(api): drift-status 확장 — SpeedMaster.updated_at 포함

- ConstraintConfig 또는 SpeedMaster 중 최신 edit 시각을 last_schedule_run 과 비교
- 응답에 latest_speed_master_updated_at 필드 추가 (UI 디버깅용)
- SpeedMaster 편집 후에도 drift 배너가 정확히 ON 되도록 보장
EOF
)"
```

---

## Task 4: NumberCell 컴포넌트 + /master/speed 편집 연결

**Files:**

- Create: `frontend/src/app/(main)/master/speed/components/NumberCell.tsx`
- Modify: `frontend/src/app/(main)/master/speed/page.tsx`

- [ ] **Step 4.1: Next.js 16 컨벤션 확인 (필요시)**

Run: `ls /Users/jaewookim/Desktop/Project/KBI_PoC/frontend/node_modules/next/dist/docs/ 2>/dev/null | head -5`
React 19 Client Component 패턴 ("use client" + useState) 이 기존 ConstraintsPage 와 동일하게 동작하므로 별도 조사 불필요. 의심 나는 케이스만 `node_modules/next/dist/docs/` 를 참조.

- [ ] **Step 4.2: NumberCell 컴포넌트 작성**

Create directory if missing:

```bash
mkdir -p /Users/jaewookim/Desktop/Project/KBI_PoC/frontend/src/app/\(main\)/master/speed/components
```

Create `frontend/src/app/(main)/master/speed/components/NumberCell.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

type Status = "idle" | "saving" | "success" | "error";

interface NumberCellProps {
  value: number;
  onSave: (next: number) => Promise<void>;
  min?: number;
  step?: number;
}

function formatMin(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return "0분";
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h === 0) return `${mm}분`;
  if (mm === 0) return `${h}시간`;
  return `${h}시간 ${mm}분`;
}

/**
 * 인라인 숫자 편집 셀.
 *
 * Why: onBlur autosave + 성공/실패 피드백 + 시간 병기 를 한 곳에 캡슐화.
 * 같은 로직이 셋업 4컬럼에 반복되므로 별도 컴포넌트로 추출.
 */
export function NumberCell({
  value,
  onSave,
  min = 0,
  step = 1,
}: NumberCellProps) {
  const [draft, setDraft] = useState<string>(String(value));
  const [status, setStatus] = useState<Status>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 외부에서 value 가 바뀌면 draft 동기화 (예: 저장 성공 후 row 재조회)
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  useEffect(() => {
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

  const commit = async () => {
    const parsed = Number(draft);
    // 변경 없음 또는 동일 값 → 스킵
    if (!Number.isFinite(parsed) || parsed < min) {
      setErrorMsg(`${min} 이상의 숫자를 입력하세요`);
      setStatus("error");
      setDraft(String(value));
      return;
    }
    if (parsed === value) {
      setStatus("idle");
      setErrorMsg(null);
      return;
    }

    setStatus("saving");
    setErrorMsg(null);
    try {
      await onSave(parsed);
      setStatus("success");
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setStatus("idle"), 1500);
    } catch (e) {
      setStatus("error");
      setErrorMsg(e instanceof Error ? e.message : "저장 실패");
      setDraft(String(value));
    }
  };

  const parsed = Number(draft);
  const previewMin = Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;

  const bg =
    status === "success"
      ? "bg-green-50"
      : status === "error"
        ? "bg-red-50"
        : "";
  const border = status === "error" ? "border-red-500" : "border-gray-300";

  return (
    <div
      className={`flex flex-col items-end gap-0.5 rounded px-1 py-0.5 ${bg}`}
    >
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={min}
          step={step}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") {
              setDraft(String(value));
              setErrorMsg(null);
              setStatus("idle");
              (e.target as HTMLInputElement).blur();
            }
          }}
          disabled={status === "saving"}
          className={`w-20 rounded border px-2 py-0.5 text-right text-sm ${border}`}
        />
        {status === "saving" && (
          <span className="text-xs text-gray-400">저장중…</span>
        )}
        {status === "success" && (
          <span className="text-xs text-green-600">✓</span>
        )}
      </div>
      <span className="text-[10px] text-gray-400">
        ({formatMin(previewMin)})
      </span>
      {errorMsg && <span className="text-[10px] text-red-600">{errorMsg}</span>}
    </div>
  );
}
```

- [ ] **Step 4.3: page.tsx 를 편집 가능한 테이블로 확장**

Edit `frontend/src/app/(main)/master/speed/page.tsx` 전체를 다음으로 교체:

```tsx
"use client";

import { useEffect, useState } from "react";
import { NumberCell } from "./components/NumberCell";

interface SpeedRecord {
  speed_id: number;
  equipment_code: string;
  product_type: string;
  cross_section: number;
  line_speed_mpm: number;
  setup_spec_min: number;
  setup_color_min: number;
  setup_compound_min: number;
  setup_start_min: number;
  updated_at: string | null;
}

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type SetupField =
  | "setup_spec_min"
  | "setup_color_min"
  | "setup_compound_min"
  | "setup_start_min";

export default function SpeedPage() {
  const [speeds, setSpeeds] = useState<SpeedRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetch(`${API}/master/speed_master`)
      .then((r) => r.json())
      .then((data) => {
        setSpeeds(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const saveCell = async (
    speed_id: number,
    field: SetupField,
    value: number,
  ) => {
    const resp = await fetch(
      `${API}/master/speed_master/${speed_id}/setup-params`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      },
    );
    if (!resp.ok) {
      let detail = `저장 실패 (${resp.status})`;
      try {
        const body = await resp.json();
        if (body?.detail) detail = String(body.detail);
      } catch {}
      throw new Error(detail);
    }
    const updated = (await resp.json()) as SpeedRecord;
    setSpeeds((prev) =>
      prev.map((s) => (s.speed_id === speed_id ? { ...s, ...updated } : s)),
    );
  };

  const filtered = filter
    ? speeds.filter(
        (s) =>
          s.equipment_code.includes(filter) ||
          s.product_type?.includes(filter) ||
          String(s.cross_section).includes(filter),
      )
    : speeds;

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">선속 마스터</h1>
        <input
          type="text"
          placeholder="검색 (설비코드, 제품유형, SQ)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="rounded-md border px-3 py-1.5 text-sm"
        />
      </div>

      <p className="mb-3 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
        이 장비·SQ 조합의 <b>실제 값</b> 을 편집합니다. 값이 있으면 이 값이 우선
        적용되고, 없으면 <code>/master/constraints</code> 의 공정
        기본값(4-1/4-2) 이 fallback 으로 사용됩니다.
      </p>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">설비코드</th>
              <th className="px-3 py-2 text-left">제품유형</th>
              <th className="px-3 py-2 text-right">SQ</th>
              <th className="px-3 py-2 text-right">선속(m/min)</th>
              <th className="px-3 py-2 text-right">규격교체</th>
              <th className="px-3 py-2 text-right">색상교체</th>
              <th className="px-3 py-2 text-right">컴파운드교체</th>
              <th className="px-3 py-2 text-right">시작셋업</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.map((s) => (
              <tr key={s.speed_id} className="hover:bg-gray-50/60">
                <td className="px-3 py-2 font-mono text-xs">
                  {s.equipment_code}
                </td>
                <td className="px-3 py-2">{s.product_type}</td>
                <td className="px-3 py-2 text-right">{s.cross_section}</td>
                <td
                  className={`px-3 py-2 text-right font-medium ${
                    s.line_speed_mpm ? "" : "bg-yellow-50 text-yellow-600"
                  }`}
                >
                  {s.line_speed_mpm || "미기재"}
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_spec_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_spec_min", v)}
                  />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_color_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_color_min", v)}
                  />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_compound_min ?? 0)}
                    onSave={(v) =>
                      saveCell(s.speed_id, "setup_compound_min", v)
                    }
                  />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_start_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_start_min", v)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-400">총 {filtered.length}건</p>
    </div>
  );
}
```

- [ ] **Step 4.4: 타입체크**

Run: `cd /Users/jaewookim/Desktop/Project/KBI_PoC/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: 출력 없음 (타입 에러 없음). 에러가 뜨면 `SpeedRecord` 필드 누락 여부 먼저 확인.

- [ ] **Step 4.5: 수동 확인 (선택)**

Run backend: `cd backend && uvicorn app.main:app --reload`
Run frontend: `cd frontend && npm run dev`
브라우저 `http://localhost:3000/master/speed`:

- 4 셋업 컬럼이 input 으로 표시됨
- 값 편집 → 포커스 해제 → 녹색 ✓ → 1.5초 후 사라짐
- 음수 입력 → 빨간 테두리 + 에러 메시지 + 원래 값 복원
- "(N시간 N분)" 보조 텍스트 정확한지

- [ ] **Step 4.6: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add frontend/src/app/\(main\)/master/speed/
git status
git commit -m "$(cat <<'EOF'
feat(frontend): /master/speed 4 컬럼 인라인 편집 (NumberCell)

- NumberCell: onBlur autosave + 성공 플래시 + 실패 시 원복 + 시간 병기
- setup_spec/color/compound/start_min 4 컬럼 편집 가능
- 상단 안내문: SpeedMaster 값 우선, 없으면 ConstraintConfig fallback
- 기존 표에 compound/start 컬럼 추가 표시 (이전에는 숨겨져 있었음)
EOF
)"
```

---

## Task 5: DriftBanner 재사용 + /master/constraints 우선순위 안내

**Files:**

- Modify: `frontend/src/app/(main)/master/speed/page.tsx`
- Modify: `frontend/src/app/(main)/master/constraints/page.tsx`

- [ ] **Step 5.1: /master/speed 에 DriftBanner 추가**

Edit `frontend/src/app/(main)/master/speed/page.tsx`:

파일 상단 import 에 추가:

```tsx
import { DriftBanner } from "../constraints/components/DriftBanner";
```

state 추가 (`const [loading, ...]` 바로 아래):

```tsx
const [driftRefresh, setDriftRefresh] = useState(0);
```

`saveCell` 함수 맨 끝에 (setSpeeds 직후) 추가:

```tsx
setDriftRefresh(Date.now());
```

렌더에서 안내문 `<p>` 바로 **위** 에 `<DriftBanner refreshKey={driftRefresh} />` 삽입:

```tsx
<DriftBanner refreshKey={driftRefresh} />

<p className="mb-3 rounded border border-blue-200 bg-blue-50 ...">
  ...
</p>
```

- [ ] **Step 5.2: /master/constraints 우선순위 안내문 한 줄 추가**

Edit `frontend/src/app/(main)/master/constraints/page.tsx`. `{tab === "params" && (` 분기 내부의 `<ParamEditor .../>` 바로 **위** 에 다음 안내문 추가:

```tsx
<p className="mb-3 rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
  공정별 기본값입니다. 특정 장비·SQ 조합에 실제 값이 필요하면{" "}
  <a href="/master/speed" className="text-blue-600 hover:underline">
    선속 마스터
  </a>{" "}
  에서 편집하세요 (row 값이 있으면 여기 기본값보다 우선 적용됩니다).
</p>
```

- [ ] **Step 5.3: 타입체크**

Run: `cd /Users/jaewookim/Desktop/Project/KBI_PoC/frontend && npx tsc --noEmit 2>&1 | head -10`
Expected: 출력 없음.

- [ ] **Step 5.4: 수동 확인 (선택)**

- `/master/speed` 상단에 drift 배너 자리 확인 — 편집하지 않았으면 숨겨져 있어야 함.
- 값 하나 편집 → 배너 나타나야 함 (API 에 dirty=true 가 떠야 함).
- `/master/constraints` 파라미터 탭 상단에 회색 안내 박스 + `선속 마스터` 링크 클릭 시 이동.

- [ ] **Step 5.5: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add frontend/src/app/\(main\)/master/speed/ frontend/src/app/\(main\)/master/constraints/
git status
git commit -m "$(cat <<'EOF'
feat(frontend): /master/speed DriftBanner + /master/constraints 안내문

- /master/speed 상단에 DriftBanner 재사용 (Task 8 컴포넌트)
- 저장 성공 시 driftRefresh 트리거 → 배너 즉시 갱신
- /master/constraints 파라미터 탭에 'row 값 있으면 우선' 안내문 추가 — 두 화면의 역할 명확화
EOF
)"
```

---

## Task 6: E2E Playwright 2 시나리오

**Files:**

- Create: `frontend/e2e/speedmaster-setup-edit.spec.ts`

- [ ] **Step 6.1: 시나리오 파일 작성**

Create `frontend/e2e/speedmaster-setup-edit.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * Why: 실제 DB 값을 변경하는 E2E — afterEach 에서 시드값 복원은 어렵다
 * (편집된 row 의 원래 값을 모름). 대신 테스트 1에서 편집 전 값을 캡처해
 * 테스트 끝에 복원한다.
 */

test.describe("SpeedMaster 셋업 시간 인라인 편집", () => {
  test("시나리오 1 — 셀 편집 성공 → drift 배너 ON", async ({
    page,
    request,
  }) => {
    await page.goto("/master/speed");
    await expect(page.locator("h1")).toContainText("선속 마스터");

    // 첫 번째 데이터 row 의 '규격교체' 컬럼 input
    const firstRow = page.locator("tbody tr").first();
    const specInput = firstRow.locator('input[type="number"]').nth(0);
    const originalValue = await specInput.inputValue();

    // 값 편집
    const newValue = (Number(originalValue) + 1).toString();
    await specInput.fill(newValue);
    await specInput.blur();

    // 성공 피드백 (✓ 아이콘 또는 녹색 배경)
    await expect(firstRow.locator("text=✓")).toBeVisible({ timeout: 5000 });

    // drift 배너 ON
    await expect(
      page.getByText("저장된 변경이 아직 스케줄에 반영되지 않았습니다"),
    ).toBeVisible({ timeout: 10000 });

    // cleanup — 원래 값 복원 (다음 테스트 격리)
    await specInput.fill(originalValue);
    await specInput.blur();
    await page.waitForTimeout(1000);
  });

  test("시나리오 2 — 음수 입력 거부 + 원래 값 복원", async ({ page }) => {
    await page.goto("/master/speed");
    const firstRow = page.locator("tbody tr").first();
    const specInput = firstRow.locator('input[type="number"]').nth(0);
    const originalValue = await specInput.inputValue();

    // 음수 입력
    await specInput.fill("-10");
    await specInput.blur();

    // 에러 메시지 노출
    await expect(firstRow.getByText("0 이상의 숫자를 입력하세요")).toBeVisible({
      timeout: 5000,
    });

    // input 값이 원래 값으로 복원되었는지
    await expect(specInput).toHaveValue(originalValue);
  });
});
```

- [ ] **Step 6.2: Playwright 가 파일을 인식하는지 확인**

Run: `cd /Users/jaewookim/Desktop/Project/KBI_PoC/frontend && npx playwright test --list e2e/speedmaster-setup-edit.spec.ts 2>&1 | head -10`
Expected: 2 tests 리스팅.

- [ ] **Step 6.3: 실제 실행 (수동 선택)**

사전에 백엔드 + 프론트엔드 가 로컬에 떠있어야 함. 둘 다 실행 중이면:

```
cd /Users/jaewookim/Desktop/Project/KBI_PoC/frontend && npx playwright test e2e/speedmaster-setup-edit.spec.ts --headed
```

Expected: 2 passed. 실패 시 스크린샷이 `frontend/test-results/` 에 저장됨.

서버가 없으면 이 단계는 스킵 가능 (리스팅만으로 syntax 유효성 확인).

- [ ] **Step 6.4: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add frontend/e2e/speedmaster-setup-edit.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): SpeedMaster 인라인 편집 시나리오 2종

- 시나리오 1: 셀 편집 성공 → ✓ 피드백 + drift 배너 ON + 원복 cleanup
- 시나리오 2: 음수 입력 거부 + 원래 값 복원 검증
EOF
)"
```

---

## Task 7: 문서 업데이트

**Files:**

- Modify: `CHANGELOG.md`

- [ ] **Step 7.1: CHANGELOG 한 섹션 추가**

Edit `CHANGELOG.md`. 기존 `## 2026-04-18 — ConstraintConfig 파라미터 UI 편집 ...` 항목 **바로 위** 에 새 섹션 삽입 (최신이 위):

```markdown
## 2026-04-18 — SpeedMaster 셋업 시간 인라인 편집

- `/master/speed` 페이지에서 `setup_spec_min` / `setup_color_min` / `setup_compound_min` / `setup_start_min` 4 컬럼 인라인 편집 (onBlur autosave)
- Pydantic 화이트리스트: 구조 필드(`equipment_code` 등) 편집 차단, 음수 거부
- `SpeedMaster.updated_at` 컬럼 추가 → `/constraints/drift-status` 에 포함 → SpeedMaster 편집 후에도 재실행 배너 ON
- `/master/constraints` 파라미터 탭에 "row 값 있으면 우선" 안내문 → 두 화면 역할 명확화
- 우선순위 규칙 명문화: `SpeedMaster row 값 > ConstraintConfig 4-x 공정 기본값`
- 범위 밖: row 신규 추가/삭제, 일괄 편집, 변경 이력 UI, 구조 필드 편집
- 스펙: `docs/specs/2026-04-18-speedmaster-setup-edit-design.md`
- 플랜: `docs/plans/2026-04-18-speedmaster-setup-edit.md`
```

- [ ] **Step 7.2: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add CHANGELOG.md
git commit -m "docs: CHANGELOG SpeedMaster 셋업 시간 인라인 편집 기능 기록"
```

---

## Self-Review

### Spec coverage

스펙 §요구사항 → Task 매핑:

- §2 목표 "4 컬럼 인라인 편집, onBlur autosave" → Task 4
- §2 목표 "편집 후 DriftBanner" → Task 5
- §2 목표 "편집 성공/실패 피드백" → Task 4 (NumberCell `status` 상태 관리)
- §2 No-op 불변식 → Alembic `server_default=now()` + No-op 은 "편집 안 하면 동일" 이 자명 (값 자체는 그대로)
- §2 비목표 (row 신규/삭제, 일괄, 이력) → 태스크 없음, CHANGELOG 에 명시 (Task 7)
- §3 우선순위 명문화 → Task 4 안내문 + Task 5 /master/constraints 안내문
- §4.1 백엔드: updated_at + PATCH + drift-status 확장 → Task 1/2/3
- §4.2 프론트 NumberCell + page.tsx → Task 4
- §4.2 DriftBanner 재사용 → Task 5
- §4.3 파일 구조 → 모든 Task 파일 경로 일치
- §5 데이터 흐름 → Task 2/3 (PATCH → updated_at → drift-status) + Task 5 (DriftBanner)
- §6 Fail-Fast → Task 2 Pydantic ge=0/extra=forbid, 404
- §7 테스트: 단위/통합/E2E → Task 2/3/6. "test_patch_partial_update" 등 4-5개 단위 테스트 Task 2 에 포함. drift 통합 테스트 Task 3
- §8 커밋 분할 7개 → 정확히 7 Task
- §10 오픈 이슈 → 플랜 실행 중 참고

### Placeholder scan

- "TBD"/"TODO" 없음
- 모든 코드 블록은 실제 구현 가능한 구체 코드
- Alembic 자동 생성 해시만 `<new>` 로 표기 — Step 1.3 에서 실행 시점에 확정되므로 placeholder 가 아님
- 테스트가 "set the right status code" 같은 모호한 지시 없음 — 모두 구체 assert

### Type consistency

- `SpeedSetupUpdate` (Pydantic) 필드 이름: `setup_spec_min` / `setup_color_min` / `setup_compound_min` / `setup_start_min` — Task 2/4 양쪽 일치
- `SpeedRecord` TypeScript 인터페이스 (Task 4) 와 실제 모델 컬럼 이름 일치
- `NumberCell` props: `value: number, onSave: (next: number) => Promise<void>` — Task 4 정의, Task 5 수정 시 그대로 사용
- `DriftBanner` props: `refreshKey?: number` — 기존 Task 8 구현과 일치, Task 5 에서 재사용
- API 경로 `PATCH /api/master/speed_master/{id}/setup-params` — Task 2/4/6 일치 (프론트 `API` 상수는 기본값 `http://localhost:8000/api` 로 prefix)

모든 체크 통과.

---
