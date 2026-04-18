# SpeedMaster 셋업 시간 인라인 편집 UI

- **작성일**: 2026-04-18
- **작성자**: jaewoo kim + superpowers
- **대상**: `/master/speed` 페이지에서 SpeedMaster 의 4개 셋업 시간 컬럼을 row 단위로 편집 가능하게 한다

## 1. 배경

`docs/specs/2026-04-18-constraint-config-params-ui-design.md` 에서 `ConstraintConfig` 4-1/4-2/4-4 파라미터를 UI 에서 편집 가능하게 했다. 이는 **fallback 경로** 만 제어한다. 장비·SQ 별 실제 값은 `SpeedMaster` 에 저장되어 있고, SpeedMaster row 가 있으면 우선 적용된다.

DB 실측 결과:

- 연선(`ST-*`) 29 rows: `setup_spec_min=210` 전부, `setup_compound_min=0` 전부(선재교체 누락), `setup_start_min` 일부만 채워짐(ST-54BO\*·ST-T6B0 만 30~50, 나머지 0)
- 연합(`CA-*`) 14 rows: 존재함 (changeover-time-analysis.md 의 "연합 누락" 은 과거 정보)
- 참조기준(`여척 및 규격교체 기준.xlsx`): 연선 규격교체 240분 — 시드 210과 불일치

즉 구조적 데이터 누락 문제는 해소됐고, **값 품질을 개선하려면 row 단위 편집 UI 가 필요**하다.

## 2. 목표와 비목표

### 목표

- `/master/speed` 페이지에서 `setup_spec_min`, `setup_color_min`, `setup_compound_min`, `setup_start_min` 4 컬럼 인라인 편집
- `onBlur` autosave (셀 단위 즉시 저장)
- 편집 후 상단 DriftBanner 로 "재실행 미완료" 알림 (Silent drift 방지)
- **No-op 불변식**: 편집하지 않으면 기존 스케줄과 동일한 결과
- 편집 성공/실패 시각 피드백 (녹색 플래시 / 빨간 메시지 + 원복)

### 비목표

- 구조 필드(`equipment_code`, `product_type`, `cross_section`, `line_speed_mpm`, `line_speed_hr`) 편집 — read-only 유지
- row 신규 추가/삭제 — 별도 스펙
- 일괄(bulk) 편집 — 대량 수정은 SQL 마이그레이션으로 처리
- SpeedMaster 변경 이력 UI — row 수가 많아 감사 로그 부담, 필요시 별도
- `setup_compound_min` 기본값 시드 보완 — 도메인 판단 필요

## 3. SpeedMaster vs ConstraintConfig 우선순위 (명문화)

| 상황                                     | 실제 사용값                       |
| ---------------------------------------- | --------------------------------- |
| SpeedMaster row 존재 + 해당 컬럼 값 있음 | **SpeedMaster 값 우선**           |
| SpeedMaster row 없거나 해당 컬럼 NULL/0  | ConstraintConfig 4-1/4-2 fallback |

두 화면의 역할 구분 (UI 안내문으로 노출):

- `/master/constraints` 4-x: **"SpeedMaster 값이 없는 경우의 공정 기본값"**
- `/master/speed` 편집: **"이 장비·SQ 조합의 실제 값 (있으면 우선)"**

## 4. 아키텍처

### 4.1 백엔드

#### SpeedMaster.updated_at 컬럼 (Alembic)

- `Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)`
- Task 5 (`constraint_config.updated_at`) 와 동일 패턴
- `server_default=now()` 로 기존 176 row 자동 backfill

#### 신규 엔드포인트: `PATCH /master/speed_master/{speed_id}/setup-params`

```python
class SpeedSetupUpdate(BaseModel):
    setup_spec_min: float | None = Field(default=None, ge=0)
    setup_color_min: float | None = Field(default=None, ge=0)
    setup_compound_min: float | None = Field(default=None, ge=0)
    setup_start_min: float | None = Field(default=None, ge=0)
    model_config = ConfigDict(extra="forbid")  # 화이트리스트
```

- 4 컬럼만 허용. 구조 필드는 `extra="forbid"` 로 원천 거부
- 음수 거부 (`ge=0`)
- 비숫자 거부 (Pydantic 기본 검증)
- 기존 generic `PUT /master/speed_master/{id}` 는 존재하지만 UI 에선 신규 PATCH 만 사용
- 변경된 값만 `setattr` — None 인 필드는 건드리지 않음

#### drift-status 확장

기존 `/constraints/drift-status` 의 비교 로직을 확장:

```python
latest_dirty_source = max(
    max_or_none(ConstraintConfig.updated_at),
    max_or_none(SpeedMaster.updated_at),
)
dirty = latest_dirty_source is not None and (
    latest_schedule is None or latest_dirty_source > latest_schedule_naive
)
```

### 4.2 프론트

기존 `/master/speed/page.tsx` 확장:

- 테이블 구조 유지 (read-only 컬럼은 그대로)
- 4개 셋업 컬럼을 `<NumberCell value onSave onCancel />` 커스텀 컴포넌트로 교체
- `<NumberCell>`:
  - `<input type="number" min=0>` + onBlur/Enter 시 PATCH 호출
  - 저장 중 → 스피너 아이콘
  - 성공 → 2초간 녹색 배경 플래시
  - 실패 → 빨간 테두리 + 작은 에러 텍스트 + 원래 값 복원
  - 포커스 아웃 시 값 변경 없으면 PATCH 스킵
- 각 셀 아래 회색 작은 글씨로 "(N시간 N분)" 병기 (Task 7 formatMin 재사용)
- 상단에 `<DriftBanner />` 재사용 (Task 8 컴포넌트)

### 4.3 코드 구조

```
backend/
  app/
    presentation/routes/
      master_data.py               (MODIFY)  - PATCH /speed_master/{id}/setup-params 추가
    infrastructure/models/
      speed_master.py              (MODIFY)  - updated_at 컬럼
  alembic/versions/
    <new>_add_speedmaster_updated_at.py (NEW)
  tests/
    api/
      test_speedmaster_setup_patch.py (NEW)

frontend/
  src/app/(main)/master/speed/
    page.tsx                       (MODIFY)  - 편집 컬럼 + DriftBanner
    components/
      NumberCell.tsx               (NEW)     - 인라인 편집 셀
  e2e/
    speedmaster-setup-edit.spec.ts (NEW)

docs/
  specs/2026-04-18-speedmaster-setup-edit-design.md  (현재)
  plans/2026-04-18-speedmaster-setup-edit.md          (writing-plans 단계에서 생성)
```

## 5. 데이터 흐름

```
[1] /master/speed 에서 setup_spec_min 셀 210 → 240 편집
       ↓ onBlur
[2] PATCH /master/speed_master/{id}/setup-params {"setup_spec_min": 240}
       ↓
[3] SpeedMaster row 업데이트 + updated_at=now() (onupdate 자동)
       ↓
[4] 프론트: 셀 녹색 플래시 + 상단 DriftBanner API 재조회 → dirty=true
       ↓
[5] 사용자가 CTA 클릭 → /plan-pipeline (Stage1/update) 이동 후 재실행
       ↓
[6] batch_grouping.create_batches 재실행
     - planned 배치: 삭제 후 새 setup_spec_min=240 으로 재생성
     - hard_frozen (in_progress/completed/wip_complete): 유지
       ↓
[7] Stage2 auto_schedule → 간트 재생성
```

## 6. Fail-Fast

- API: Pydantic 음수/비숫자/알 수 없는 필드 거부 → 422
- API: row 없으면 404
- UI: 저장 실패 시 원래 값 복원 + 인라인 에러 노출 (사용자 데이터 손실 방지)
- UI: 동시 편집 감지는 범위 밖 (PoC — 운영자 1명 전제)

## 7. 테스트 전략

### 단위 (API)

- `test_patch_rejects_structural_fields` — `{"equipment_code": "FOO"}` 거부 (422)
- `test_patch_rejects_negative` — `{"setup_spec_min": -1}` 거부
- `test_patch_partial_update` — 4컬럼 중 일부만 변경 시 다른 컬럼 유지
- `test_patch_updates_updated_at` — onupdate 훅 작동

### 통합

- `test_drift_status_includes_speedmaster` — SpeedMaster.updated_at 최근이면 dirty=true
- `test_drift_status_compares_max` — ConstraintConfig 와 SpeedMaster 중 큰 값으로 비교

### E2E (Playwright, 2 시나리오)

1. **편집 성공 → drift 배지**: 셀 값 변경 → 포커스 해제 → 녹색 플래시 → drift 배너 ON
2. **음수 입력 거부**: 셀에 -10 입력 → 저장 실패 → 빨간 테두리 + 원래 값 복원

### 회귀

- 기존 `e2e/master-pages.spec.ts "선속 마스터 페이지 로드"` 통과 (h1·EX-B100 가시성 유지)
- 기존 `PUT /master/speed_master/{id}` generic CRUD 변경 없이 동작

## 8. 커밋 분할 (7개)

1. **feat(db)**: SpeedMaster.updated_at + Alembic 마이그레이션
2. **feat(api)**: PATCH /master/speed_master/{id}/setup-params + Pydantic 화이트리스트 + 단위 테스트
3. **feat(api)**: drift-status 확장 (SpeedMaster 포함) + 통합 테스트
4. **feat(frontend)**: NumberCell 컴포넌트 + /master/speed 4 컬럼 편집 연결
5. **feat(frontend)**: /master/speed 상단 DriftBanner + /master/constraints 에 우선순위 안내문
6. **test(e2e)**: speedmaster-setup-edit Playwright 2 시나리오
7. **docs**: CHANGELOG + 우선순위 명문화 문서 크로스링크

## 9. No-op 불변식 / 마이그레이션 / 롤백

- Alembic upgrade 시 기존 row 176개는 `server_default=now()` 로 backfill, 기능 영향 없음
- 편집하지 않으면 기존 스케줄 결과 완전 동일
- 롤백: 각 커밋 단위 git revert. Alembic downgrade 지원

## 10. 오픈 이슈

- 셋업 4컬럼을 한 줄에 다 표시하면 좁은 화면에서 input 이 좁음 — Task 4 구현 시 확인 후 가로 스크롤 또는 반응형 조정
- 운영 편의상 "이 장비의 다른 SQ row 에도 같은 값 전파" 미니 버튼이 필요할 수 있음 — 운영 피드백 후 v1.1 확장 후보
- 변경 이력 UI 는 범위 밖. 감사 수요 생기면 `speed_master_history` 테이블 별도 스펙
