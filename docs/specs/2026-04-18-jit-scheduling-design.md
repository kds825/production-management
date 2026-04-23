# JIT (Just-In-Time) 후방 shift — 납기 여유 활용 스케줄링

라운드: 2026-04-18
관련: Phase D Task 2. PDF T6B0 4/8~4/12 gap 재현 목표.

## 문제 정의

### 관측

현재 scheduler 는 **greedy forward packing + EDD** 로 작동:

- 각 group 을 due 오름차순 정렬 → 앞에서부터 가용 설비 slot 에 ASAP 배치
- 결과: 납기 여유 있는 task 도 일찍 시작 → gap 없이 연속 배치

PDF T6B0 (baseline run 20260417_215558 참조):

- 35SQ 6틀 58,200m: 4/3 19:00 → 4/8 04:00 (우리) ↔ 4/8 근처 (PDF)
- 25SQ 3틀 40,500m: **4/8 04:00 → 4/9 14:00 (우리)** ↔ **4/13~ (PDF, 3~5일 gap)**

25SQ 납기 = 4/20, duration ~34h. 4/13 시작해도 4/15 완료 가능 → 5일 여유. PDF 는
이 여유를 의도적 gap 으로 할당 (JIT + 후공정 slot 조율).

### 우리 코드 부재 요소

- **납기 여유 인지**: pure-work end 가 납기 보다 훨씬 빠르면 그 여유를 활용할 수
  있는 로직 없음
- **후방 shift**: 설비 내 gap 이 있어도 task 를 뒤로 밀지 않음

## 설계: Post-processing JIT shift

스케줄러 main loop (greedy) 는 그대로 두고, 완료 후 **설비별 우→좌 pass** 로
각 task 를 납기와 후행 task 가 허용하는 한도 내에서 뒤로 밀기.

### Input

- `tasks_created: list[ScheduleTask]` — scheduler 가 생성한 전체 task
- `db: Session` — DB + calendar_engine 접근
- `min_slack_days: int = 3` — 납기 여유가 이 값 이상일 때만 shift 고려

### Algorithm

```
for each equipment eq in timeline:
    tasks = sorted(eq.tasks, by start_datetime)
    for i in reversed(range(len(tasks))):
        t = tasks[i]
        pb = lookup_batch(t.batch_id)
        if pb.due_date absent: continue
        slack_days = (pb.due_date - t.end_datetime.date()).days
        if slack_days < min_slack_days: continue

        # Upper bound: 다음 설비 내 task 의 start, 또는 납기 말일
        next_start = tasks[i+1].start_datetime if i+1 < len(tasks) else
                     datetime.combine(pb.due_date, time(23, 59))

        # Successor 제약 (pipeline): (order_id, line) 공유 후공정 task 의 start
        # 시각이 shift 된 end 보다 후에 와야. 가장 이른 successor.start 가 cap.
        succ_cap = earliest successor start for (pb.sales_order_id, pb.sales_order_line)

        upper = min(next_start, succ_cap, due_end)

        # Shift end 를 upper 까지 늘릴 때 new start 계산 (calendar-aware 역산)
        wall_min = (t.end_datetime - t.start_datetime).total_seconds() / 60
        new_start = calculate_start_datetime(upper, wall_min, db, eq)

        if new_start <= t.start_datetime: continue  # no gain

        t.start_datetime = new_start
        t.end_datetime = upper
```

### 불변식 (Invariants)

1. **순서 보존**: `tasks[i].end <= tasks[i+1].start` 유지 (후방 shift 는 뒤 task
   에 닿을 수 있지만 초과 금지)
2. **납기 준수**: `t.end_datetime <= due_end` (납기일 23:59)
3. **pipeline 의존성**: successor task 의 start >= shifted end
4. **wall duration 보존**: shift 는 wallclock 길이 (break 포함) 유지 — `calculate_start_datetime`
   사용으로 calendar-aware 유지

### Scope 제한 (이 라운드)

**포함**:

- 단일 설비 내 후방 shift
- Sales-order-level successor 제약

**미포함 (Phase E 후보)**:

- 후공정 설비 pipeline JIT (연선 shift → 절연 shift → 시스 shift chain)
- SQ cluster / color chain 관계 재정렬
- WIP matching 변화

이유: single-pass backward shift 가 상호 독립적이라 안전. 다른 설비·공정 연쇄는
상태 공간 폭발.

## 수학적 안전성

**Claim**: post-pass 가 기존 invariant 유지.

증명 대략:

- shift 는 start 증가 + end 증가 (duration 불변) 만 허용 → precedence 순서 유지
- next_start cap 으로 설비 내 겹침 불가
- succ_cap 으로 pipeline 어긋남 불가
- due cap 으로 납기 위반 생성 안 함

## 구현 위치

`backend/app/services/schedule_optimizer.py` 에 `apply_jit_delay()` 함수 추가.
`auto_schedule()` 반환 직전 optional 호출.

```python
def auto_schedule(...):
    # ... 기존 logic ...
    if jit_enabled:
        apply_jit_delay(tasks_created, db, min_slack_days=3)
    db.flush()
    return tasks_created
```

`jit_enabled` 는 env var `SCHEDULER_JIT=1` 등으로 toggle 가능 (opt-in).

## 리스크

- **Stage 1 breakdown 호환**: Stage 1 의 `estimated_duration_min` 은 JIT shift 후 의미가
  "계획 순작업 시간" → DB 에 그대로. UI breakdown 에 영향 없음.
- **Predecessor cascade**: JIT shift 된 task 의 end 가 successor start 를 넘으려 하면
  shift skip. succ 자체는 shift 안 하므로 chain 단절 안 됨.
- **Break 구간 경계**: `calculate_start_datetime` 이 break 을 skip 하며 역산하므로
  wall duration 유지. 다만 break 을 지나치는 shift 는 edge case 검토 필요 (test 에 포함).

## 테스트 계획

- **unit**: toy fixture — 2 task (35SQ 6틀, 25SQ 3틀) on TP-like equipment. 25SQ 납기 +7일.
  JIT 적용 후 25SQ.start 가 정확히 (next_start or due) 로 shifted.
- **integration**: baseline run 20260417_215558 replay with JIT → T6B0 25SQ 가 4/13+
  로 shift 되는지 확인. PDF 와 블록 배치 대사.
- **invariant**: overlap 0건 (이미 Phase D Task 1 fix 로 해소된 상태 유지).

## 이 라운드 범위

- ✅ 설계 문서 (본 파일)
- ✅ prototype 함수 (standalone, 미통합)
- ✅ unit test (prototype 검증)

**미진행** (다음 라운드):

- auto_schedule 통합 + env var toggle
- 새 stage2 run 실행 + T6B0 gap 재현 검증
- 후공정 pipeline chain JIT
