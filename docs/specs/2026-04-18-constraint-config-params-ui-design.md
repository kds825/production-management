# ConstraintConfig 파라미터 UI 편집 — 셋업/교체 카테고리 (4-1 ~ 4-4)

- **작성일**: 2026-04-18
- **작성자**: jaewoo kim + superpowers
- **대상**: 연선 규격교체 시간(3.5h=210분) 등 스케줄러 하드코딩 파라미터를 UI에서 편집 가능하게 전환

## 1. 배경

연선 규격교체 시간 3.5시간(210분)이 코드에 직접 박혀 있고, 변경하려면 파일 수정 후 재배포가 필요하다. 사용자는 프론트에서 숫자를 바꾸고 "자동배열 다시" 누르면 — 진행중 배치는 유지하고 — 앞으로의 계획분에만 새 값이 반영되기를 원한다.

조사 결과 `ConstraintConfig` 테이블이 이미 존재하고, 셋업/교체 카테고리 4개 제약(4-1 규격교체, 4-2 색상교체, 4-3 드럼 권취, 4-4 용접)이 시드되어 있다. **4-4 용접시간은 이미 ConstraintConfig 참조 패턴이 구현되어 있다** (`schedule_optimizer.py:457-466`, `cp_sat_optimizer.py:488-496`). 4-1/4-2는 같은 패턴 복제만 하면 된다.

## 2. 목표와 비목표

### 목표

- 4-1/4-2/4-3/4-4 파라미터를 `ConstraintConfig.params_json`에서 읽도록 통일
- UI에서 파라미터 편집 → 저장 → Stage1/update 재실행 → planned 배치에 신규 값 적용
- 진행중(`in_progress`)·완료(`completed`)·재공완료(`wip_complete`)·기준일자 이전 `scheduled` 배치는 기존 값 보존 (Freeze & Rebuild 재활용)
- **No-op 불변식**: UI에서 값을 변경하지 않으면 기존 스케줄과 완전히 동일한 결과 (기존 하드코딩 기본값을 그대로 시드값으로 사용)

### 비목표

- 신규 테이블 생성 — `ConstraintConfig`가 이미 있다
- Stage1/update 플로우 자체 변경 — 기존 Freeze & Rebuild를 그대로 사용
- SpeedMaster의 row 단위 편집 UI — 이번 범위는 공정 레벨 기본값만 (SpeedMaster override 우선순위는 유지)
- 스케줄러 재실행 자동화 — 저장 후 사용자가 수동으로 Stage1/update 누르는 UX

## 3. 범위 내 제약 (4-1 ~ 4-4)

| 제약 ID | 이름           | params_json key                                                             | 현재 하드코딩 위치                                                                                                                                           | 처리                                                                        |
| ------- | -------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| **4-1** | 규격교체 시간  | `stranding_min`(210), `insulation_min`(60), `sheath_min`(30), `cv_min`(300) | `batch_grouping.py:521,577` `else 210.0` (연선 core/al_core), `schedule_optimizer._get_stranding_setup_min()`가 호출측에서 `setup_time_min` 인자로 받는 흐름 | batch_grouping fallback 2곳 → ConstraintConfig 조회로 교체                  |
| **4-2** | 색상교체 시간  | `sheath_color_min`(120)                                                     | `schedule_optimizer.py:817-819` `120.0` 하드코딩, `cp_sat_optimizer.py` 동일 위치                                                                            | SpeedMaster.setup_color_min 없을 때 fallback을 ConstraintConfig로 교체      |
| **4-3** | 드럼 권취 시간 | (현재 빈 `{}`)                                                              | `_get_drum_winding_min()` 계산식 (SpeedMaster 기반)                                                                                                          | **Phase 1에서 조사 → key 구조 불명확하면 범위에서 제외** (사용자 사전 합의) |
| **4-4** | 용접 시간      | `welding_min`(30)                                                           | 이미 구현됨 (`_DEFAULT_WELDING_MIN=30` 폴백)                                                                                                                 | **변경 없음** — UI에서 편집만 가능해지면 자동 반영                          |

## 4. 아키텍처

### 4.1 백엔드 — 공통 헬퍼 도입

신규 파일 `backend/app/services/constraint_params.py`:

```python
def get_constraint_param(
    db: Session,
    constraint_id: str,
    key: str,
    default: float | None = None,
) -> float:
    """ConstraintConfig.params_json에서 숫자 파라미터 조회.

    Why: ConstraintConfig 조회 패턴이 schedule_optimizer/cp_sat_optimizer/
    batch_grouping 3곳에 중복 구현될 위험 → 단일 진입점으로 통일.

    - row 없음 + default=None → RuntimeError (fail-fast, seed 안내)
    - row 있음 + key 없음 + default=None → KeyError
    - default 주어지면 위 두 경우에 default 반환 (기존 welding 패턴 유지용)
    """
```

### 4.2 기존 호출부 리팩터

1. `schedule_optimizer._get_stranding_setup_min()`의 `spec_min`, `compound_min` 인자:
   - 기본값 docstring(`"기본 240분"`, `"기본 120분"`)이 시드값과 불일치 → docstring 정정
   - 호출측(`schedule_optimizer.py:782-790, 1316-1320`)에서 이미 `setup_time_min = rep.setup_time_min` 사용. 이 값은 batch_grouping 단계에서 확정됨 → 변경 없음

2. `batch_grouping.py:521, 577` 두 곳:

   ```python
   # Before
   else 210.0
   # After
   else get_constraint_param(db, "4-1", "stranding_min")
   ```

   `db` 세션 전달 필요 → 호출 체인 확인 후 주입

3. `schedule_optimizer.py:817-819` (색상교체):

   ```python
   # Before
   color_change_min = float(sm_color[0] or 120.0) if sm_color else 120.0
   # After
   color_change_min = (
       float(sm_color[0])
       if sm_color and sm_color[0] is not None
       else get_constraint_param(db, "4-2", "sheath_color_min")
   )
   ```

   `cp_sat_optimizer.py`의 동일 로직도 함께 변경

4. `welding_min` (4-4): 이미 참조 로직 존재 → `_DEFAULT_WELDING_MIN` 폴백은 "row 자체가 없을 때"만 사용되므로 보존. 또는 헬퍼로 대체 가능(선택)

### 4.3 API

신규 라우터 `backend/app/presentation/routes/constraint_config.py`:

- `GET /constraint-config/` → 전체 리스트 (카테고리 필터 옵션)
- `GET /constraint-config/{constraint_id}` → 단건
- `PATCH /constraint-config/{constraint_id}` body: `{"params_json": {...}}` → 부분 merge 업데이트

Pydantic validation:

- 숫자 키는 `float >= 0` 강제
- 알 수 없는 key 추가 허용 (PoC 유연성)
- 감사 로그: 수정자·수정시각·변경 전/후 값 (기존 유사 패턴 있으면 재사용)

### 4.4 프론트 — `/master/constraints` 페이지

- 기존 `/master/speed` 와 동일한 samildevkit(pwc-design) 스타일 적용
- 카테고리별 아코디언 또는 탭 — 이번 PoC는 **"셋업/교체" 카테고리만** 노출 (4-1 ~ 4-4)
- 각 row: 이름, params_json key-value 편집 필드들(숫자 input)
- "저장" 버튼 → PATCH → 토스트로 성공/실패
- 안내 배너: "값 변경 후 **작업지시서 업데이트** 화면에서 재실행해야 새 계획에 반영됩니다"
- verify-pwc-design 스킬로 디자인 검증

## 5. 데이터 흐름 (재스케줄 시 반영 보장)

```
[1] UI `/master/constraints`에서 stranding_min: 210 → 0 편집
        ↓
[2] PATCH /constraint-config/4-1 → ConstraintConfig row 업데이트 (DB 즉시 반영)
        ↓
[3] 사용자가 Stage1/update 화면에서 base_date 지정 후 재실행
        ↓
[4] Freeze & Rebuild (기존 로직, 무변경):
     - hard_frozen (in_progress/completed/wip_complete) → ProductionBatch 보존, setup_time_min 유지
     - soft_frozen (base_date 이전 scheduled)         → 보존
     - mutable (base_date 이후 scheduled + planned)   → 삭제 후 batch_grouping 재실행
        ↓
[5] batch_grouping이 신규 ConstraintConfig 값으로 setup_time_min 계산하여 새 ProductionBatch 생성
        ↓
[6] Stage2 auto_schedule 실행 → 새 setup_time_min 반영된 간트차트
```

**불변식**: `[1]`에서 값 변경 안하면 `[5]`의 ConstraintConfig 값은 시드값(기존 하드코딩값) 그대로 → 기존 스케줄과 동일.

## 6. Fail-Fast 규칙

- `ConstraintConfig` row 없음 → `RuntimeError("ConstraintConfig '4-1' row not found. Run seed_db.py")`
- `params_json`에 key 없음 → `KeyError` (default 명시적으로 주지 않은 호출부에서)
- API PATCH validation: 음수 거부, 비숫자 거부, 알 수 없는 top-level 필드 거부
- 단, `welding_min`(4-4)은 기존 `_DEFAULT_WELDING_MIN=30` 폴백 유지(하위 호환성)

## 7. 테스트 전략

### 단위

- `get_constraint_param` 헬퍼: row 없음 / key 없음 / 정상 조회 / default 동작
- PATCH API: 음수 거부, merge 로직, row 없음 시 404

### 통합

- `batch_grouping.create_batches()` — ConstraintConfig 시드 상태에서 setup_time_min=210으로 배치 생성되는지 (No-op 불변식)
- ConstraintConfig 4-1 stranding_min=0으로 변경 → 재실행 시 setup_time_min=0
- 색상교체(4-2) 폴백 경로 검증 (SpeedMaster.setup_color_min 없는 장비 조건)

### E2E (Playwright)

1. 기존 Stage1 → Stage2 실행, 연선 배치 `setup_time_min=210` 확인
2. `/master/constraints` 진입 → 4-1 stranding_min을 0으로 편집 → 저장
3. Stage1/update 화면에서 base_date 지정 후 재실행
4. 간트차트에서 연선 교체 구간(3.5h 공백)이 사라졌는지 시각 확인 + 배치 DB 값 검증
5. hard_frozen 상태로 세팅한 배치는 `setup_time_min=210` 유지 (회귀 방지)

### 회귀

- CI에서 `constraint_config` 시드값 미변경 상태로 auto_schedule 실행 시 기존 스냅샷과 동일한지 (`test_overload_split.py` 등 기존 테스트 통과)

## 8. 커밋 분할 (atomic, 검증 통과 후에만 다음으로)

1. **chore**: `get_constraint_param` 헬퍼 + 단위 테스트
2. **feat(scheduler)**: batch_grouping 4-1 하드코딩 제거 + 통합 테스트 (No-op 회귀 포함)
3. **feat(scheduler)**: 4-2 색상교체 하드코딩 제거 (schedule_optimizer + cp_sat_optimizer)
4. **chore(investigation)**: 4-3 드럼 권취 파라미터 조사 — 결과에 따라 5번 수행 또는 스킵
5. **feat(scheduler)**: 4-3 편입 (조사 결과 yes인 경우만)
6. **feat(api)**: `GET/PATCH /constraint-config` 라우터 + 테스트
7. **feat(frontend)**: `/master/constraints` 페이지 (pwc-design + verify-pwc-design 통과)
8. **test(e2e)**: Playwright 시나리오
9. **docs**: CHANGELOG / README 업데이트

## 9. 마이그레이션 / 롤백

- 스키마 변경 없음 → Alembic 마이그레이션 불필요
- 시드값은 **기존 하드코딩값과 동일**하게 유지 (4-1: 210/60/30/300, 4-2: 120, 4-4: 30)
- 롤백: 각 커밋이 atomic이므로 git revert 단위로 가능

## 10. 오픈 이슈

- **4-3 드럼 권취**: params_json이 빈 `{}`고 `_get_drum_winding_min()`이 `SpeedMaster.line_speed_hr` 등 장비별 값을 사용 → 공정 레벨 기본값 개념이 잘 안 맞을 수 있음. Phase 4에서 조사 후 편입 여부 최종 결정
- **스케줄러 경로 이중화**: `schedule_optimizer` + `cp_sat_optimizer` 양쪽에 4-2 패치 필요. 회귀 테스트로 양쪽 보장
- **감사 로그**: PoC 범위에서는 최소 구현(수정시각만) — 추후 확장
