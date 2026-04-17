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

## 현재 조치

- regression test `tests/test_overlap_tight_window.py` — xfail marker (strict=False).
- 본 문서 기록으로 Phase D 재개 시작점 제공.
- Phase C commits 는 Task 1 (C-FindSpeed) + Task 2 (C-TP) 만 반영, Task 3 는 이 investigation 문서 + regression test 로 대체.
