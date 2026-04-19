# 스케줄러 파이프라인·시스 그룹핑 재설계 Design

- 작성일: 2026-04-17
- 브랜치: `dev_jaewoo`
- 관련 커밋: `c00d9ba`, `193c25d`, `200da4a`, `12b7270`, `9220a58`

## 배경

사용자 피드백 10건을 조사한 결과 다음으로 분류된다.

- **로직 버그/설계 이슈 (P0)**
  - 연선↔절연, 연합↔시스 파이프라인에서 후공정이 선행공정보다 먼저 끝나는 경우 발생
  - 시스 배치 그룹핑이 규격(SQ) 1차 키 → 색상 체인지오버 과다, 납기 위반 발생
  - 절연·시스 공정에서 블록이 겹쳐 보이거나 실제 시간 겹침 의심
- **UI 개선 (P1)**
  - 시스 블록 라벨에 색상만 노출, 묶인 수주의 규격 표시 없음
- **검증만 필요 (P2, 이미 구현됨)**
  - 고내화 제품군 절연 분할 (`200da4a`), 고내화 '고' 배지 (`193c25d`)
  - 블록 클릭 시 우측 상세 패널 (`page.tsx:1180` flex-row)
  - 주말 접힘 8px (`SchedulerView.tsx:24`)

## 목표

1. 파이프라인 동기화 공식화: 연선 → 절연 → 시스가 **유휴 시간 최소** + **후공정 끝 ≥ 선행공정 끝** 불변식 보장
2. 시스 배치 그룹핑을 **색상 1차 키 + 주차 유지 + 납기 hard** 로 재설계
3. 겹침 발생 시 동일 목적함수로 **자동 재최적화**, 2회 실패 시 저장 거부 + 경고 배너
4. 시스 블록 라벨에 묶인 규격 목록 표시 (D 방식, 최대 3개 + 축약)
5. 기존 구현 4건 실제 동작 검증, 이상 시 보정

## 비목표 (Out of Scope)

- 드럼별 절연 블록 분할 (공식으로 충분, YAGNI)
- CP-SAT 목적함수 전면 재설계 (증분 추가만)
- 색상 체인지오버 시간 파라미터 튜닝 (기본값 유지)
- 간트 zoom·pan 로직 변경

---

## 1. 파이프라인 동기화 (연선 → 절연 → 시스)

### 1.1 현재 문제

- `schedule_optimizer.py:548-569` 에서 절연 시작 = 연선 첫 드럼 출력 시각 (`process_first_output_by_sq`). 시작 하한은 맞음.
- `schedule_optimizer.py:630-656` 에서 end 하한 = `선행 끝 + 후공정 1틀 duration`. 과다한 하한이며 사용자 요구(선행 끝 ≥ 후공정 끝)와 의미가 다름.
- 역산 로직이 없어, 절연 선속이 연선의 2배면 절연이 일찍 시작해 일찍 끝남 → 유휴 발생 후 연선 마지막 드럼 대기 중 "절연 먼저 끝남" 체감.

### 1.2 설계

핵심 공식:

```
T_i_start = max(T_s_first_drum, T_s_end - D_i)
T_i_end   = T_i_start + D_i
```

- `T_s_first_drum`: 연선 첫 드럼 완료 시각 (시작 하한)
- `T_s_end`: 연선 전체(마지막 드럼) 완료 시각
- `D_i`: 절연 duration (선속 기반 + 캘린더 보정 고정값)
- 불변식: `T_i_end >= T_s_end`, `T_i_start >= T_s_first_drum`

시스도 동일:
```
T_sh_start = max(T_i_first_drum, T_i_end - D_sh)
T_sh_end   = T_sh_start + D_sh
```

### 1.3 케이스 분석

| 상황 | 결과 |
|-----|------|
| 절연 duration > 연선 전체 duration | 시작 = `T_s_first_drum` (기존과 동일), 절연이 느려 연선 이후 끝남 |
| 절연 duration < 연선 전체 duration (2배 빠름) | 시작 = `T_s_end - D_i`, 절연 끝과 연선 끝이 정렬 |
| 연선 단드럼 (1틀) | `T_s_first_drum ≈ T_s_end` → 시작 = 연선 끝 |

### 1.4 구현 포인트

**Greedy 경로** (`schedule_optimizer.py`):
- `earliest` 계산부에서 신규 공식 적용
- `선행 끝 + 1틀` 하한(`:630-656`) 삭제
- `process_end_by_sq`, `process_first_output_by_sq` 둘 다 참조 (데이터는 이미 있음)

**CP-SAT 경로** (`cp_sat_optimizer.py`):
- 기존 precedence `pred_end <= succ_start` → 아래로 교체:
  - `pred_first_drum_end <= succ_start`
  - `pred_end <= succ_end` (신규)
- 목적함수에 `minimize sum(succ_end - pred_end)` 항 추가 (유휴 최소화)

**캘린더 엔진** (`calendar_engine.py`):
- 역방향 duration 헬퍼 추가: `calculate_start_datetime(end_dt, duration) -> start_dt` (휴식·주말 보정)
- 역산 결과가 비가동 구간이면 앞으로 당겨 유효 가동 시각으로 스냅

### 1.5 블록 width 불변

Duration 은 선속 기반 고정값이며 본 설계는 **시작 시점만 이동**시킨다. 블록이 과도하게 확장되지 않는다.

---

## 2. 시스 배치 그룹핑 재설계 (A'' 접근안)

### 2.1 현재 문제

- `batch_grouping.py:800-815`에서 `group_key = f"A120_{색상}_{주차}"` 형태이지만, 이전 단계에서 이미 규격(SQ) 기준으로 설비 할당이 결정됨 → 규격 다르면 다른 블록, 색상 체인지오버 잦음
- 같은 주차 내에서도 납기 3일 차이 수주가 뒤로 밀려 납기 위반 사례 발생

### 2.2 설계

그룹핑 키 (Stage 1):

```python
group_key = f"{설비}_{색상}_{납기주차}"
```

주차 유지 이유: 작업지시서·리포팅이 주차 단위, 긴급수주 삽입점 확보.

그룹 간 정렬 우선순위 (Stage 2):

1. **색상 체인 최대화** — 인접 주차에 같은 색상 그룹이 있으면 연속 배치 (체인지오버 0회)
2. **절연 완료 시각 오름차순** — 선행 공정 종료 전 시작 불가 (hard)
3. **납기(EDD) 오름차순** — 동등 시 빠른 것 먼저

분할 규칙 (hard constraint 위반 시):

- `예상완료시각 > 납기` → 해당 수주 **앞에서 색상 블록 절단** → 앞부분 별도 블록으로 먼저 처리
- 절연 미완료 수주가 그룹 내에 포함되면 → skip → 다음 색상으로 전환 → 절연 완료 후 복귀

### 2.3 예시

```
W15 [흑] 120·95 SQ   ─┐
W16 [흑] 70 SQ        ├─  색상 체인 (체인지오버 0회)
W16 [흑] 50 SQ       ─┘
W16 [갈] 120 SQ       ←  체인지오버 1회
W14 [갈] 긴급 4C x 4SQ (W14 납기)  ←  분할되어 앞으로 이동
```

### 2.4 구현 포인트

- `batch_grouping.py`: 그룹키 유지, Stage 2 정렬·분할 로직 신설
- `cp_sat_optimizer.py`: 색상 체인 보너스 항 목적함수에 추가 (가중치: 납기 < 색상 체인 < 겹침 금지)
- 납기는 hard constraint로 처리 (위반 해 탐색 금지)
- 긴급수주 선점(`_try_preempt_for_urgent`) 로직과 호환성 유지

---

## 3. 블록 겹침 철저 방지 + 자동 재최적화

### 3.1 현재 상태

- CP-SAT `add_no_overlap` 설비별 hard constraint ✅
- Greedy `_find_available_slot` + timeline tracking ✅
- `constraint_checker.check_overlap` 사후 검증 ✅
- 프론트 렌더 `top:4` 고정 → Y축 시각 겹침 ❌

### 3.2 설계

**처리 플로우:**

```
1. CP-SAT / greedy 로 스케줄 생성
2. constraint_checker.validate_all() 수행
3. 겹침 없음? → DB 저장
4. 겹침 발견 → 재시도 (최대 2회)
     - 동일 목적함수 (납기 hard → 색상 체인 → 유휴 최소 → 겹침 금지)
     - 충돌 블록의 equipment·time_window release 후 CP-SAT 재실행
5. 2회 실패 시:
     - DB 저장 거부 (기존 스케줄 유지)
     - audit_log: event="overlap_persist_alert", severity="high"
     - 프론트 상단 경고 배너: "스케줄에 겹침이 있습니다 — 관리자 확인 필요"
```

### 3.3 구현 포인트

**백엔드 (`schedule_optimizer.py` 래퍼):**
```python
def auto_schedule(...):
    for attempt in range(3):
        tasks = _run_optimization(...)
        violations = constraint_checker.validate_all(tasks)
        if not violations.has_overlap():
            return tasks  # 성공
        audit_log.record(event="overlap_detected_retry", attempt=attempt)
    audit_log.record(event="overlap_persist_alert", severity="high")
    raise SchedulerOverlapError(tasks, violations)
```

- 긴급수주 선점, WIP 재배정, 배치분할 경로 공통 wrapper 사용
- `SchedulerOverlapError` 는 기존 스케줄 유지하며 프론트에 전달

**프론트 (경고 배너):**
- `scheduler/page.tsx` 상단에 `ConstraintAlert` 컴포넌트 확장 또는 신규 배너
- API 응답에서 `overlap_alert: true` 수신 시 표시
- 사용자가 관리자 확인 후 수동 재스케줄 버튼으로 복구

**Y축 시각화 스태킹 (B1):**
- `SchedulerView.tsx` lane 개수 동적 계산
- 시간 겹침 블록 감지 → 다음 lane 에 배치 (top 값 분리)
- 설비 row 높이 = 가장 많은 동시 블록 수 × lane height
- 단 백엔드가 정상이면 lane 1개 유지

### 3.4 테스트

- `tests/test_overlap_invariant.py` 신규: CP-SAT / greedy / 긴급수주 선점 / WIP 재배정 / 배치분할 5개 시나리오별 스케줄 생성 후 `validate_all().has_overlap() is False` 단언
- Playwright: 간트 렌더 후 모든 블록 bbox 계산, Y축 lane 분리 시각 확인

---

## 4. 시스 블록 라벨 (D 방식)

### 4.1 설계

데이터 준비 (백엔드):

- `ProductionBatch` 또는 `ScheduleTask` 직렬화 시 `spec_list: list[str]` 필드 추가 (묶인 수주의 규격 중복 제거 목록)

렌더 규칙 (`GanttTaskBlock.tsx`):

```typescript
const sqList = dedupe(task.spec_list);  // ["50SQ", "60SQ", "100SQ", ...]
const label = sqList.length <= 3
    ? sqList.join("·") + " SQ"
    : `${sqList.slice(0,3).join("·")} SQ +${sqList.length-3}종`;
```

표시 형식:

- 1줄: `[색상]` chip (기존 유지)
- 2줄: 규격 목록 (10px, `white-space:nowrap`, ellipsis fallback)
- 툴팁 / 우측 패널: 전체 규격 + 수주번호 나열

### 4.2 구현 포인트

- 백엔드: `batch_grouping.py` 또는 라우트 스키마에서 `spec_list` 집계
- 프론트: `GanttTaskBlock.tsx` 시스 라벨 렌더부 수정 (연선/절연 블록은 기존 유지)

---

## 5. 검증 (이미 구현됨 — 동작 확인)

| 항목 | 검증 방법 | 통과 기준 |
|-----|---------|---------|
| 고내화 저압절연 그룹 분리 (`200da4a`) | TFR-8 제품 시드 데이터로 스케줄 생성 | 저압절연 블록이 일반·고내화 2개로 분리 |
| 고내화 '고' 배지 (`193c25d`) | playwright 스크린샷 | 주황색 '고' 배지 렌더 |
| 우측 상세 패널 | UI 실행 확인 | 블록 클릭 시 우측 420px 패널 표시 |
| 주말 접힘 8px | UI 실행 확인 + `SchedulerView.tsx:24` 상수 | 주말 열 폭 8px |

검증 결과 이상 시에만 수정, 정상이면 종료.

---

## 6. 리스크 및 주의사항

1. **CP-SAT 목적함수 가중치 재조정** — 색상 체인 보너스 추가 시 기존 납기 위반 최소화 가중치와 충돌할 수 있음. 납기 hard constraint 유지로 완화.
2. **캘린더 엔진 역방향 duration** — 휴식·주말 경계에서 edge case 발생 가능. 테스트 케이스 5개 이상 작성.
3. **겹침 재최적화 성능** — CP-SAT 2회 재실행 시 응답 시간 증가. 기존 단일 실행 대비 최대 3배 cap.
4. **기존 audit_log 스키마** — `overlap_detected_retry`, `overlap_persist_alert` 이벤트 타입 추가 필요.
5. **lane 스태킹 렌더 성능** — 블록 100개 이상에서 lane 계산 O(n²) 되지 않도록 sweep-line 알고리즘 사용.

## 7. 구현 우선순위 (의존성 순)

1. **P0-A** 캘린더 엔진 역방향 duration 헬퍼 (`calendar_engine.py`) — 파이프라인 동기화 선행 조건
2. **P0-B** 파이프라인 동기화 공식 적용 (`schedule_optimizer.py`, `cp_sat_optimizer.py`)
3. **P0-C** 시스 색상 체인 그룹핑 (`batch_grouping.py`, CP-SAT 목적함수)
4. **P0-D** 겹침 자동 재최적화 + 저장 거부 + 경고 배너
5. **P1-E** 시스 블록 라벨 D 방식 (`spec_list` 추가 + 렌더)
6. **P2-F** 기존 구현 4건 동작 검증 (실패 시 보정)
7. **P2-G** Y축 lane 스태킹 (백엔드 겹침 방지 후 시각 안전망)

각 단계마다 테스트 통과 확인 후 atomic commit.

## 8. 수용 기준 (Acceptance Criteria)

- [ ] 파이프라인: 임의 시드 데이터 100건 스케줄 후 `T_i_end >= T_s_end`, `T_sh_end >= T_i_end` 불변식 100% 충족
- [ ] 시스 그룹핑: 동일 색상 인접 주차 체인 형성률 (체인지오버 횟수 ÷ 가능 최대) ≥ 80%, 납기 위반 0건
- [ ] 겹침: 5개 시나리오 전수 테스트에서 겹침 0건, 2회 재시도 실패 시 경고 배너 표출
- [ ] 라벨: 시스 블록에서 규격 ≤ 3개는 전부, 4개 이상은 축약 표시
- [ ] 검증: 고내화 분할·배지·우측 패널·주말 접힘 현행 동작 확인 완료
