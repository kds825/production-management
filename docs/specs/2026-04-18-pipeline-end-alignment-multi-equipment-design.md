# 파이프라인 End-Alignment 누락 수정 — 멀티설비 경로 + CP-SAT 단일 경로

- 작성일: 2026-04-18
- 브랜치: `dev_jaewoo`
- 관련: 고압(HV) 케이블 스케줄 후공정 조기 종료 현상

## 1. 문제

고압 케이블 생산 스케줄에서 후공정이 선행공정보다 **더 일찍 끝나는** 물리적으로 불가능한 스케줄이 생성됨.

### 재현 (2026-04-18 자동배열 결과)

| 설비    | 공정     | 시작 | 종료     |
| ------- | -------- | ---- | -------- |
| CV 1호  | 고압절연 | 4/9  | **4/17** |
| CV 2호  | 고압절연 | 4/9  | **4/17** |
| A150EXT | 고압시스 | 4/9  | **4/16** |
| B150EXT | 고압시스 | 4/9  | **4/16** |

공정 순서는 `연선 → 고압절연 → 고압시스`이므로 시스 종료가 절연 종료보다 앞서는 것은 불가능.

### 불변식

- `T_succ_end ≥ T_pred_end` (후공정 끝 ≥ 선행공정 끝)
- `duration` 고정 → 블록 폭 고정, **시작을 지연**하여 정렬 (블록 확장 금지)

## 2. 근본 원인

"파이프라인 유휴 최소 역산 공식"이 두 위치에서 누락/오구현:

### 2.1 `_schedule_multi_equipment` — 완전 누락

`backend/app/services/schedule_optimizer.py:1080-1379`의 멀티설비 분배 함수는 greedy·CP-SAT 양쪽에서 공유되는데, `process_end_by_sq`를 **읽는 코드 자체가 없음**. 첫드럼 overlap(`process_first_output_by_sq`)과 고압시스 20h 정적 버퍼만 존재.

**발동 조건**: `is_high_insul` 또는 `is_high_sheath` 이고 드럼 수 ≥ 2, 적합 설비 ≥ 2. 고압절연(CV#1+CV#2)과 고압시스(A150+B100)가 항상 해당.

**대조**: 저압시스(A100/A120)는 색상별 설비 강제 라우팅(`schedule_optimizer.py:572-580`)으로 `len(eligible)=1` → 단일설비 경로로 빠져 end-alignment 혜택 받음. 이것이 "저압은 괜찮은데 고압만 이상"의 원인.

### 2.2 CP-SAT 단일 경로 — 방식 오류 (블록 폭 확장)

`backend/app/services/cp_sat_optimizer.py:1027-1056`은 `best_start`를 유지한 채 `end_dt`를 `pred_last + per_drum_duration`으로 확장. 결과적으로 `end_dt - best_start > duration`이 되어 블록 폭이 늘어남. 사용자 요구(블록 폭 고정)와 정반대.

### 2.3 올바른 참조 구현

`schedule_optimizer.py:869-910` (greedy 단일 경로)이 유일한 정답:

```python
# T_succ_start = max(T_pred_first_drum, T_pred_end - D_succ)
reverse_start = calculate_start_datetime(pred_end_latest, best_total_duration, db, eq_code)
if reverse_start > best_start:
    best_start = _find_available_slot(reverse_start, best_total_duration, slots, db, eq_code)
    end_dt = calculate_end_datetime(best_start, best_total_duration, db, eq_code)
if end_dt < pred_end_latest:
    end_dt = pred_end_latest
```

## 3. 설계

### 3.1 수정 범위

| #   | 파일                    | 위치                                              | 변경 내용                                                                          |
| --- | ----------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------- |
| A   | `schedule_optimizer.py` | `_schedule_multi_equipment` (라인 1272-1350 내부) | 각 서브태스크의 `slot_start` 계산 직후, `end_dt` 계산 전에 end-alignment 역산 추가 |
| B   | `cp_sat_optimizer.py`   | 라인 1020-1056                                    | `best_start` 확정 전에 역산해 start를 지연. 기존 "end_dt 확장" 블록 제거           |

### 3.2 공통 역산 로직 추출

두 곳에 중복 삽입하지 않고 helper로 추출:

```python
# backend/app/services/_pipeline_alignment.py  (신규)
def align_start_to_predecessor_end(
    *,
    current_start: datetime,
    duration_min: float,
    process_name: str,
    pred_proc: str | None,
    group_sqs: set[int],
    process_end_by_sq: dict[tuple[str, int], datetime],
    slots: list,
    db: Session,
    equipment_code: str,
) -> tuple[datetime, datetime]:
    """반환: (aligned_start, aligned_end). 불변식: aligned_end >= pred_end_latest."""
```

- `pred_end_latest` 집계: `pred_proc`와 시스인 경우 `"연합"` 추가, 그룹 내 모든 SQ를 순회
- `reverse_start = calculate_start_datetime(pred_end_latest, duration_min, db, equipment_code)`
- `reverse_start > current_start` 일 때만 `_find_available_slot(reverse_start, duration_min, slots, db, equipment_code)`로 지연
- 캘린더 보정 오차 대비 `aligned_end < pred_end_latest` 면 `aligned_end = pred_end_latest`

### 3.3 멀티설비 경로 적용 (변경 A)

서브태스크 루프 `schedule_optimizer.py:1272-1350` 내부, `slot_start = _find_available_slot(...)` 직후에 helper 호출. 각 서브태스크는 자기 `eq_total_duration`(드럼 수에 비례)을 가지므로 **서브태스크별로 독립 역산**.

`timeline`·`split_end_dts`·`split_first_outputs`·`process_end_by_sq` 갱신은 정렬된 `(slot_start, end_dt)` 기준으로 수행.

### 3.4 CP-SAT 단일 경로 적용 (변경 B)

`best_start = _find_available_slot(earliest, total_dur, slots, db, chosen_eq_code)` 직후에 helper 호출. 기존 라인 1027-1056의 "end_dt 확장" 블록은 **삭제**(helper가 대체).

### 3.5 불변식 수치 비교

예: 고압절연 종료 = 4/17 08:00, 고압시스 `best_total_duration` = 7일 (= 168h).

- 현재(버그): `best_start = 4/9 08:00`, `end_dt = 4/16 08:00` → 시스가 절연보다 하루 먼저 끝남
- 수정 후: `reverse_start = 4/17 08:00 - 168h = 4/10 08:00`, 첫드럼 overlap(4/9 +α)보다 늦으므로 `best_start = 4/10 08:00`, `end_dt = 4/17 08:00` → 종료 정렬, 블록 폭 168h 유지

## 4. 테스트

### 4.1 단위 테스트 (신규)

`backend/tests/test_pipeline_alignment.py`:

1. `test_sheath_end_aligned_when_insulation_slower` — 절연이 시스보다 느릴 때 시스 시작이 지연되어 `sheath_end >= insulation_end` 보장
2. `test_block_width_preserved` — `end_dt - start` == `duration_min`(캘린더 휴식 제외 유효 시간) 유지, 확장되지 않음
3. `test_no_change_when_already_aligned` — 시스가 원래 더 늦게 끝나는 경우 start가 바뀌지 않음
4. `test_mixed_sq_group_uses_latest_pred_end` — 고압시스 색상 혼합 SQ 그룹에서 모든 SQ의 선행 종료 중 최대값 사용

### 4.2 통합/E2E

- `backend/tests/test_schedule_optimizer.py` 에 고압 파이프라인 고정 시드 케이스: 4/18 재현 시나리오와 동일한 배치로 자동배열 후 "고압시스 최종 종료 ≥ 고압절연 최종 종료" 검증
- Playwright: `/scheduler` 접근 → 자동배열 실행 → DOM에서 고압시스 마지막 블록 `data-end`가 고압절연 마지막 블록 `data-end` 이상인지 확인

### 4.3 회귀 방어

- 저압시스 end-alignment가 기존대로 단일 경로에서 동작하는지 스냅샷 테스트
- 연선(ST-) 멀티설비 분배에서 블록 폭이 늘어나지 않는지 확인

## 5. 비범위 (Out of Scope)

- 프론트엔드 `moveTask` 수동 이동 시 파이프라인 재검증 (`schedule_optimizer.py` 상단 주석의 3번 항목) — 별개 이슈
- `constraint_checker._check_precedence` 가 종료시각 정렬 위반을 감지하도록 확장 — 본 수정 이후 감지 필요성이 낮아지므로 후속 과제

## 6. 리스크

- 멀티설비 서브태스크의 start가 각기 달라질 수 있어 "균등 분배" 직관이 깨질 수 있음 → duration 비율은 유지되므로 각 설비 로드는 여전히 proportional. start 시각만 다르다는 점 문서화.
- 캘린더 휴식 구간과 `calculate_start_datetime` 역산의 상호작용은 단일 경로에서 검증된 동일 함수를 재사용하므로 신규 리스크 없음.
