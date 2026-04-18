# ConstraintConfig 파라미터 UI 편집 — 셋업/교체 카테고리 (4-1, 4-2, 4-4)

- **작성일**: 2026-04-18 (v2 — 병렬 리뷰 반영)
- **작성자**: jaewoo kim + superpowers (CEO/Engineer/Design/2nd opinion 4-way review)
- **대상**: 연선 규격교체 시간 등 스케줄러 파라미터를 UI에서 편집 가능하게 전환
- **변경이력**:
  - v1 → v2: 4-3 공식 제외, silent drift 감지 배지 추가, SpeedMaster 우선순위 명시, 210 vs 240 정직 고지, 프리페치 캐시, 이중 스케줄러 동등성 테스트, 감사 로그 UI 탭, 저장 후 즉시 재실행 모달 통합

## 1. 배경

연선 규격교체 시간 3.5시간(210분)이 스케줄러 로직 **fallback 경로**에 박혀 있고, 변경하려면 파일 수정 후 재배포가 필요하다. 사용자는 프론트에서 숫자를 바꾸고 — 진행중 배치는 유지하고 — 앞으로의 계획분에만 새 값이 반영되기를 원한다.

조사 결과 `ConstraintConfig` 테이블이 이미 존재하고, 셋업/교체 카테고리 4개 제약이 시드되어 있다. **4-4 용접시간은 이미 ConstraintConfig 참조 패턴이 구현되어 있다** (`schedule_optimizer.py:457-466`, `cp_sat_optimizer.py:488-496`). 4-1/4-2는 같은 패턴 복제만 하면 된다.

### 1.1 중요한 사전 고지 (정직성)

- **"하드코딩"의 정확한 의미**: `batch_grouping.py:521, 577`의 `else 210.0`은 **SpeedMaster row가 없을 때만** 실행되는 폴백이다. 근본 원인은 `docs/changeover-time-analysis.md`가 지적한 대로 **SpeedMaster에 연선 레코드 누락**이다. 본 스펙은 폴백 소스를 ConstraintConfig로 옮길 뿐, SpeedMaster 데이터 보완은 별도 과제로 남긴다.
- **210 vs 240 불일치**: 참조기준(`여척 및 규격교체 기준.xlsx`)은 연선 규격교체 = 240분이다. 현재 시드는 210분이다. 본 스펙은 **시드값 210을 그대로 유지**(No-op 불변식 확보)하며, 값 정정은 UI 편집으로 사용자가 결정하게 한다.
- **SpeedMaster vs ConstraintConfig 우선순위**: SpeedMaster row가 있으면 override 우선, 없을 때만 ConstraintConfig fallback. 이 순서는 변하지 않는다. 사용자가 UI에서 ConstraintConfig를 바꿔도 SpeedMaster 값이 있는 장비·SQ는 영향받지 않는다 — 이 규칙은 UI 설명문에도 명시한다.

## 2. 목표와 비목표

### 목표

- 4-1/4-2/4-4 파라미터를 `ConstraintConfig.params_json`에서 읽도록 통일 (4-4는 이미 구현됨)
- UI에서 파라미터 편집 → 저장 → (선택) Stage1/update 재실행 → planned 배치에 신규 값 적용
- 진행중/완료/재공완료/기준일자 이전 scheduled 배치는 기존 값 보존 (Freeze & Rebuild 재활용)
- **No-op 불변식** (시맨틱 교정 포함): 값 미변경 시 기존 스케줄과 동일한 결과. 단 `sm_color[0] == 0`을 "값 없음"에서 "0분 허용"으로 시맨틱 교정함(§4.2에서 상세)
- **Silent drift 방지**: 편집 후 재실행 전 상태를 UI에서 가시화

### 비목표

- 신규 테이블 생성 — `ConstraintConfig` 재사용
- SpeedMaster 데이터 보완 (연선 row 추가) — 별도 스펙
- Stage1/update 플로우 자체 변경 — 기존 Freeze & Rebuild 그대로
- **4-3 드럼 권취 편입 — 공식 제외**. 사유: `_get_drum_winding_min()`이 `SpeedMaster.line_speed_hr` 같은 장비별 계산식을 사용. "공정 레벨 기본값" 개념과 의미가 맞지 않음. 필요 시 별도 스펙으로 분리
- `batch_grouping.py:463-467`의 **연선 메인 배치 `else 0.0`** — 이번 범위 아님. core/al_core 서브배치(`:521, :577`)만 대상

## 3. 범위 내 제약

| 제약 ID | 이름          | params_json key                                                             | 현재 하드코딩 위치                                                                  | 처리                                                                                |
| ------- | ------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **4-1** | 규격교체 시간 | `stranding_min`(210), `insulation_min`(60), `sheath_min`(30), `cv_min`(300) | `batch_grouping.py:521,577` `else 210.0` (61연선 core/al_core 서브배치만)           | fallback을 헬퍼 조회로 교체                                                         |
| **4-2** | 색상교체 시간 | `sheath_color_min`(120)                                                     | `schedule_optimizer.py:817-819`, `cp_sat_optimizer.py`의 대칭 위치 `120.0` 하드코딩 | SpeedMaster.setup_color_min **값 없음** 시 헬퍼 조회 (0분도 허용하도록 시맨틱 교정) |
| **4-4** | 용접 시간     | `welding_min`(30)                                                           | 이미 구현 (`_DEFAULT_WELDING_MIN=30` 폴백)                                          | 코드 변경 없음. UI 연결만                                                           |

## 4. 아키텍처

### 4.1 백엔드 — 공통 헬퍼 + 프리페치 캐시

신규 파일 `backend/app/services/constraint_params.py`:

```python
@dataclass(frozen=True)
class ConstraintParams:
    """create_batches / auto_schedule 1회 실행 동안 재사용되는 프리페치 스냅샷."""
    by_id: dict[str, dict[str, Any]]  # "4-1" -> params_json

    @classmethod
    def load(cls, db: Session) -> "ConstraintParams":
        rows = db.query(ConstraintConfig).all()
        return cls(by_id={r.constraint_id: (r.params_json or {}) for r in rows})

    def get(self, constraint_id: str, key: str, default: float | None = None) -> float:
        """row/key 없음 + default=None → RuntimeError (fail-fast, seed 안내)."""
```

**캐싱 원칙**:

- 전역 `lru_cache` / 모듈 싱글턴 **금지** (PATCH 후 stale 위험)
- **request-scoped**: `create_batches` 진입 시 1회 `load()` → closure로 전달
- Python 함수 간 직접 전달 (dict/closure), FastAPI dependency injection 불필요

### 4.2 기존 호출부 리팩터

**(a) `batch_grouping.py:114` — `create_batches(run_label, db, …)`**

- 진입 시 `params = ConstraintParams.load(db)` 호출
- `:521, :577` 두 곳의 `else 210.0` → `else params.get("4-1", "stranding_min")`
- `db` 세션은 시그니처에 이미 있으므로 침투성 0

**(b) `schedule_optimizer.py:817-819` — 색상교체**

- 현재: `float(sm_color[0] or 120.0) if sm_color else 120.0`
- 변경: `float(sm_color[0]) if sm_color and sm_color[0] is not None else params.get("4-2", "sheath_color_min")`
- **시맨틱 교정**: `sm_color[0] == 0`(SpeedMaster에 "0분"으로 명시)을 "값 없음"으로 치환하던 버그가 교정됨. No-op 불변식은 "값 없음/None인 row에 한해 동일"로 보정됨
- `cp_sat_optimizer.py`의 동일 위치도 함께 변경

**(c) `schedule_optimizer.py:455-470`, `cp_sat_optimizer.py:488-497` — 용접(4-4)**

- 기존 ConstraintConfig 개별 조회 코드를 `params.get("4-4", "welding_min", _DEFAULT_WELDING_MIN)` 로 통일
- 이중 코드 제거 (Engineer 리뷰 지적 — 드리프트 위험 제거)

### 4.3 API

신규 라우터 `backend/app/presentation/routes/constraint_config.py`:

- `GET /constraint-config/` — 카테고리 필터 옵션 (PoC는 "셋업/교체"만)
- `GET /constraint-config/{constraint_id}` — 단건
- `PATCH /constraint-config/{constraint_id}` body: `{"params_json": {...}}` → 부분 merge
- `GET /constraint-config/{constraint_id}/history` — 변경 이력 조회 (L 결정: UI 탭 노출)
- `GET /constraint-config/drift-status` — 마지막 `auto_schedule` 실행 시각 vs ConstraintConfig 최신 updated_at 비교 결과 (A 결정: silent drift 배지용)
- `POST /constraint-config/{constraint_id}/preview-impact` — 값 변경 시 영향받는 planned 배치 개수·총 리드타임 Δ 계산 (J-a: 변경 전 프리뷰)

Pydantic validation: 숫자 키는 `float >= 0`, 음수·비숫자 거부.

**변경 이력 저장**: `ConstraintConfig` 테이블에 `updated_at` 컬럼 + 별도 `constraint_config_history(constraint_id, changed_at, old_params_json, new_params_json, changed_by)` 테이블. 마이그레이션 1개 추가.

### 4.4 프론트 — `/master/constraints` (K 결정)

**네비**: `/master/constraints` + 라벨 **"제약 파라미터"**. 기존 `/master/speed`와 동일한 depth.

**레이아웃** (samildevkit + pwc-design + verify-pwc-design 검증):

- 상단 **Drift 배지** (A): 서버 `drift-status` 응답이 `dirty=true`면 "⚠️ 저장된 변경이 아직 스케줄에 반영되지 않았습니다" 띠 + "작업지시서 업데이트로 이동" CTA
- 카테고리 **"셋업/교체"** 카드형 섹션 — 4-1 / 4-2 / 4-4 라벨 + helper text로 적용 조건 설명
- 각 필드: `210 분 (3시간 30분)` 병기 (N 결정), 분 단위 input. 인라인 validation (빨간 helper text, 저장 버튼 disabled)
- 각 행 우측에 **"기본값 복원"** 버튼 (시드값 복원)
- 상단 탭: **"파라미터" | "변경 이력"** (L 결정) — 이력 탭은 `history` API 데이터를 timeline으로 표시

**저장 플로우** (M 결정 — 2지선다 모달):

1. 저장 버튼 클릭 시 확인 모달 오픈
2. 모달 상단: **"이 변경이 적용되면 영향받는 planned 배치: N건 / 예상 리드타임 Δ: -Xh"** (J-a — `/preview-impact` 결과)
3. 선택지:
   - **"지금 기존 계획에도 반영"** → PATCH + Stage1/update 즉시 호출 (base_date는 오늘 자정 같은 합리적 기본값)
   - **"다음 자동배열부터 적용"** → PATCH만 수행, drift 배지는 켜짐
4. 취소 버튼 — 저장 무효화

**"기본값으로 되돌리기"**: 시드값(§9)을 가져와 폼에 로드 (저장까진 사용자가 직접 누름).

**빈 상태**: ConstraintConfig row 없음 → "시드 데이터가 없습니다. 관리자에게 문의하세요." + 비활성 폼.

## 5. 데이터 흐름 (재스케줄 반영 경로)

```
[1] UI `/master/constraints`에서 편집 (예: stranding_min 210 → 0)
        ↓
[2] 저장 버튼 → 모달 → preview-impact 호출 → "영향 배치 N건" 표시
        ↓ 사용자 선택
   ┌──────────────────┬──────────────────────────┐
   │ "지금 기존계획 반영"   │ "다음 자동배열부터 적용"    │
   │                  │                          │
   │ PATCH 4-1        │ PATCH 4-1               │
   │   ↓              │   ↓                      │
   │ Stage1/update 즉시 │ 끝. 다음 Stage1/update 시 │
   │ 호출 (default      │ 자동 반영.                │
   │ base_date=오늘 00시) │ 드리프트 배지 ON          │
   │   ↓              │                          │
   │ Freeze & Rebuild │                          │
   │   ↓              │                          │
   │ Stage2 auto_sched │                         │
   │   ↓              │                          │
   │ 간트 갱신         │                          │
   └──────────────────┴──────────────────────────┘
```

**Drift 감지**: `auto_schedule` 실행 시 `run_label` 단위 `last_run_at`을 DB에 기록. `/drift-status`는 `max(ConstraintConfig.updated_at) > last_run_at`이면 `dirty=true`.

## 6. Fail-Fast 규칙

- `ConstraintConfig` row 없음 → `RuntimeError("ConstraintConfig '4-1' row not found. Run seed_db.py")`
- `params_json`에 key 없음, `default` 없음 → `RuntimeError` (명시적)
- API PATCH validation: 음수·비숫자·알 수 없는 top-level 필드 거부 (2nd opinion 지적: "알 수 없는 key 허용"은 버그의 다른 이름 — Cut 반영)
- 단, `welding_min`(4-4)은 기존 `_DEFAULT_WELDING_MIN=30` 폴백 유지 (하위 호환)

## 7. 테스트 전략

### 단위

- `ConstraintParams.load/get` 헬퍼: row 없음 / key 없음 / 정상 조회 / default 동작
- PATCH API: 음수 거부, merge 로직, row 없음 시 404
- `preview-impact` API: planned 배치 필터링, setup_time_min Δ 집계 정확성

### 통합

- `batch_grouping.create_batches()`: 시드 상태에서 setup_time_min=210 (No-op 불변식)
- 4-1 stranding_min=0 변경 → 재실행 시 setup_time_min=0
- 색상교체(4-2) 폴백 경로 — SpeedMaster.setup_color_min=None인 장비에서 ConstraintConfig로 읽히는지
- **(G 수정)** `sm_color[0] == 0` 경계값: 변경 전후 값 차이를 "시맨틱 교정" 주석과 함께 회귀 테스트로 박제
- **(G 수정)** `schedule_optimizer` vs `cp_sat_optimizer` 파라메트릭 동등성: 동일 입력에서 동일 setup 결과

### E2E (Playwright)

1. 기존 Stage1→Stage2, 연선 배치 `setup_time_min=210` 확인
2. `/master/constraints` 진입 → 4-1 stranding_min을 0으로 편집
3. 저장 모달에서 "영향 배치 N건" 표시 확인 → **"다음 자동배열부터 적용"** 선택
4. drift 배지 ON 확인 → CTA 클릭 → Stage1/update 페이지 이동
5. 재실행 후 간트차트에서 연선 교체 구간 사라짐 + 배치 DB `setup_time_min=0` 검증
6. hard_frozen 배치는 `setup_time_min=210` 유지 (회귀)
7. 시나리오 2: 저장 모달에서 **"지금 기존 계획에도 반영"** 선택 → Stage1/update 자동 호출 후 간트 즉시 반영 확인
8. 시나리오 3: "기본값으로 되돌리기" → 값 복원 → 저장 → drift 해소

### 회귀 스냅샷

- CI: ConstraintConfig 시드 미변경 + `sm_color` 조건 동일 상태에서 auto_schedule 결과 스냅샷 비교
- 쿼리 카운터: 프리페치 캐시 도입 후 `create_batches` 요청당 ConstraintConfig 쿼리가 1회인지 (N+1 회귀 방지)

## 8. 커밋 분할 (atomic, 검증 후 다음으로)

1. **chore**: `ConstraintParams` 헬퍼 + 단위 테스트 + seed_db 검증 스크립트 (커밋 2 전에 seed 선행 보증)
2. **feat(scheduler)**: `batch_grouping` 4-1 fallback 교체 + No-op 통합 테스트 (회귀 스냅샷 포함)
3. **feat(scheduler)**: 4-2 색상교체 fallback 교체 (`schedule_optimizer` + `cp_sat_optimizer`) + 시맨틱 교정 고지 주석 + 동등성 테스트
4. **refactor(scheduler)**: 4-4 welding 개별 조회 → `ConstraintParams.get` 통일 (기능 변화 없음)
5. **feat(api)**: `GET/PATCH /constraint-config/*` 라우터 + history/drift-status/preview-impact API + Pydantic validation
6. **feat(db)**: `constraint_config_history` 테이블 Alembic 마이그레이션 + `ConstraintConfig.updated_at` 컬럼
7. **feat(frontend)**: `/master/constraints` 페이지 — 카드 폼 + 단위 병기 + 기본값 복원 (pwc-design + verify-pwc-design 통과)
8. **feat(frontend)**: 변경 이력 탭 + drift 배지 + 저장 모달 (영향 배치 프리뷰 포함)
9. **test(e2e)**: Playwright 시나리오 3종 (다음 실행부터 / 즉시 반영 / 되돌리기)
10. **docs**: CHANGELOG + README 업데이트 (4-3은 out-of-scope 명시)

## 9. 시드값 = 기존 하드코딩값 보존

- 4-1: `stranding_min=210, insulation_min=60, sheath_min=30, cv_min=300`
- 4-2: `sheath_color_min=120`
- 4-4: `welding_min=30`

값 미변경 시 기존 결과 동일 (§2 시맨틱 교정 예외 제외).

## 10. 마이그레이션 / 롤백

- `ConstraintConfig.updated_at` 추가 + `constraint_config_history` 테이블 생성 Alembic 마이그레이션 1개
- 스키마 기존 row 보존, 초기 `updated_at=now()`
- 롤백: 각 atomic commit 단위 git revert. 마이그레이션은 downgrade 지원

## 11. 오픈 이슈 (v2에서 해소 또는 이관)

- v1에서 ✅ 해소:
  - 4-3 드럼 권취 → **out-of-scope 확정** (§2)
  - Silent drift → drift 배지 + 저장 모달 2지선다로 해소 (§4.4, §5)
- v2에서 남는 이슈:
  - SpeedMaster 연선 row 누락 보완 (근본 원인) — 별도 스펙 필요
  - 저장 모달의 "지금 반영" 선택 시 base_date 기본값을 "오늘 00시"로 두는데, 실제 현장 운영에서는 "내일 00시" 등이 적절할 수 있음 — v1 릴리즈 후 현장 피드백으로 조정
