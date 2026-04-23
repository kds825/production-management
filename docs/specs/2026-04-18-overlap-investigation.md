# SH-A100 19분 Overlap — 원인 조사 메모 (Phase D 이월)

라운드: 2026-04-18
관련 plan: `docs/superpowers/plans/2026-04-17-phase-c-scheduler-fixes.md` Task 3

## 관측 사실

현재 baseline run `20260417_215558` 의 SH-A100 설비에서:

| task  | batch_group               | start               | end                 |
| ----- | ------------------------- | ------------------- | ------------------- |
| 28966 | `A100_회_2026W18H1_120SQ` | 2026-04-08 03:00:00 | 2026-04-08 05:00:00 |
| 28942 | `A100_갈_2026W16H2_400SQ` | 2026-04-08 04:41:00 | 2026-04-08 08:00:00 |

**overlap = 19분** (04:41 ~ 05:00).

### 스케줄링 순서 (created_at)

- 28942 created_at = 12:56:38.932 (먼저)
- 28966 created_at = 12:56:48.379 (나중, 약 10초 후)

즉 28942 가 먼저 04:41~08:00 에 앉은 후, 28966 이 (04:41, 08:00) slot 을 알고 있는 상태에서 03:00~05:00 에 배치됨.

## `_find_available_slot` 분석

```python
def _find_available_slot(earliest, duration_min, occupied_slots, db, equipment_code):
    candidate = earliest
    sorted_slots = sorted(occupied_slots, key=lambda s: s[0])
    for slot_start, slot_end in sorted_slots:
        candidate_end = calculate_end_datetime(candidate, duration_min, db, equipment_code)
        if candidate_end <= slot_start:
            return candidate
        if candidate < slot_end:
            candidate = slot_end
    return candidate
```

28966 스케줄링 시 예상 동작:

- earliest = 03:00 (가정), duration = 120 min
- slots 에 (04:41, 08:00) 포함
- candidate_end = 05:00, `05:00 <= 04:41` NO, `03:00 < 08:00` YES → candidate = 08:00
- 예상 return: 08:00

실제 결과: **03:00** (예상과 불일치)

## 가설

### H1: timeline 공유 누락 (가능성 낮음)

`timeline.setdefault(eq_code, []).append(...)` 는 line 941 에서 작동. global mutation 이라 다음 group 에서 `timeline.get(eq_code, [])` 로 보일 것. 코드 리뷰 기준 정상.

### H2: `reverse_start` pipeline 역산 경로 (가능성 높음)

`schedule_optimizer.py:899-916` 의 파이프라인 역산 블록:

```python
if pred_end_latest is not None:
    reverse_start = calculate_start_datetime(pred_end_latest, best_total_duration, ...)
    if reverse_start > best_start:
        delayed_start = _find_available_slot(reverse_start, ...)
```

28966 이 predecessor (저압절연 first_drum) 역산으로 03:00 으로 당겨질 때 `slots` snapshot 에 28942 slot 누락 가능성.

**check point**: `slots` 변수가 루프 진입 시점 (line 734) 한 번 읽고 끝인지, 재조회하는지.

### H3: multi-equip 분배 경로 중복 timeline 추가 누락 (가능성 중)

line 1345 `timeline.setdefault(eq_code, []).append(...)` 는 multi-equip 분배 경로. SH-A100 single-eq 배치라면 무관.

### H4: `best_start` < `_find_available_slot` 결과로 덮어쓰기 (가능성 중)

eligible 내 여러 eq 의 min 선택 시 slots 가 다른 eq 의 상태 로 판정되는 경우.

## Phase D 수정 방향

1. 재현 test 작성 완료 (`tests/test_overlap_tight_window.py`).
2. H2 검증 — line 906 `delayed_start` 호출 시 `slots` 를 `timeline.get(eq_code, [])` 로 fresh 재조회하는 patch.
3. 여전히 overlap 있으면 H3, H4 순차 검증.
4. 수정 후 xfail marker 제거.

## 2026-04-18 확정: H6 (Ceiling mismatch) — fix 적용

### 결정적 증거

28966 group 의 `batch_seq>=0 total_duration` = **37.4min**, setup 30 → eq_total_duration **= 67.4min**. 하지만 wall 점유 = 120min (03:00-05:00).

계산 흐름:

1. `_find_available_slot(03:00, 67.4, slots=[(04:41, 08:00)])`:
   - candidate_end = 03:00 + 67.4 = 04:07.4. **올림 없음**.
   - 04:07 <= 04:41 TRUE → return 03:00 ✓ (bug: 올림 무시)
2. `calculate_end_datetime(03:00, 67.4)` = 04:07.
3. `schedule_optimizer.py:922-925` 올림: 04:07 → **05:00**.
4. `timeline.append((03:00, 05:00))` — 올림된 end.
5. 28966 wall 점유 실제 120min. 28942 (먼저 04:41-08:00 앉음) 과 19분 overlap 생성.

### Fix

`_find_available_slot` 내부에서 `candidate_end` 를 같은 올림 규칙으로 처리. 1-line diff:

```python
if (candidate_end.minute > 0 or candidate_end.second > 0 or candidate_end.microsecond > 0):
    candidate_end = candidate_end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
```

### 회귀 테스트

`tests/test_find_slot_ceiling.py` 4 cases:

- 67min + tight window → push 검증
- 정각 end 는 fit (기존 동작)
- 59min end → 04:00 올림 → slot 정확히 맞아 fit
- empty occupied

### 잔여 작업

- 새 stage2 run 실행 → `test_overlap_tight_window.py::test_no_overlap_in_baseline_run` xfail marker 제거 가능성 확인.
- baseline run `20260417_215558` 은 과거 DB 데이터 → 남아있는 overlap 은 코드 fix 가 소급 적용 안 됨. 다음 Stage2 재실행 때 overlap 없어야.

## 현재 조치

- `schedule_optimizer.py` `_find_available_slot` — 올림 규칙 적용.
- `tests/test_find_slot_ceiling.py` — unit test 4 PASS.
- `tests/test_overlap_tight_window.py` — baseline run 기준 xfail 유지 (새 run 실행 전까지).
- 본 문서 — root cause H6 확정 기록.

## Phase D 검증 완료 (2026-04-18)

### 새 stage2 run 결과

`20260418_jit_run` (baseline 984 batches 복사 + greedy 재실행 + JIT=1):

- **설비 내 overlap 0건** — ceiling fix 효과 확인.
- constraint_checker 위반 9건 — setup_time 등 (overlap 제외).
- JIT post-processing 62 shifts 적용 — 역전/overlap 0건.

### 후속 조치

- `tests/test_overlap_tight_window.py::test_no_overlap_in_verified_run` 추가 —
  새 run 에서 overlap 0 검증. 현재 PASS.
- 기존 xfail 은 historical baseline 추적용으로 유지 (strict=False, XPASS 시에도 무해).
  DB 데이터 자체를 re-run 으로 덮어쓰면 다른 대화/세션의 참조가 깨질 수 있어 유지.
