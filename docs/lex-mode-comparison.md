# Lex vs Weighted-Sum 모드 비교 절차 (Phase 3 step 5 / Phase 5 §9.2)

**작성**: 2026-04-26 (Phase 3 step 5)
**용도**: `cp_sat_schedule(min_time_mode=True)` (lex_min_time) 와 default
weighted-sum 의 동일 입력 비교 — Phase 5 §9.2 dual-run 게이트의 사전 절차.

---

## 1. 비교 대상

| 모드                   | objective                                                          | 호출                                        |
| ---------------------- | ------------------------------------------------------------------ | ------------------------------------------- |
| weighted-sum (default) | tardiness+idle+chain+slack+EDD pair+EDD mixed past-due 가중합 최소 | `cp_sat_schedule(..., min_time_mode=False)` |
| lex_min_time (opt-in)  | Phase A: max(tardiness) 최소 → Phase B: makespan 최소 (lex)        | `cp_sat_schedule(..., min_time_mode=True)`  |

두 path 는 **같은 BuiltModel** (build_model 산출물) 위에서 분기. lex 는
deepcopy 본을 mutate (max_tard 변수 + Phase B 의 max_tard ≤ T\* hard
constraint) 하므로 fallback 시 원본 무손상.

## 2. run_label 정책 — 별도 두 개 권장 (P6)

`_purge_run_data` (Stage 1 fresh-run) / `_purge_run_tasks`
(auto_schedule 재시도) 의 의미:

| 함수                          | 대상                                                                  | 대상 모드                                               |
| ----------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------- |
| `_purge_run_data`             | audit_log / schedule_task / production_batch / wip_inventory **전체** | Stage 1 fresh ingest 진입 시 (`pipeline_orchestrator`). |
| `_purge_run_tasks(run_label)` | **해당 run_label** 의 schedule_task / audit_log                       | auto_schedule 매 retry 시.                              |

같은 run_label 로 stage2 를 두 번 호출하면 두번째가 첫번째의
schedule_task 를 비운다. 따라서 **dual-mode 비교는 별도 run_label 두 개를
사용**. production_batch (ingest 산출물) 는 보존된다.

### 2.1 권장 워크플로우

```
1. Stage 1 ingest 수행 → run_label_A, production_batch 적재
2. SQL 또는 fixture 헬퍼로 production_batch 를 run_label_B 로 복제
   (sales_order / drum_lot_master / equipment / speed / constraint 데이터는
   shared — production_batch 만 신규 row 로 복제하면 충분)
3. Stage 2 — auto_schedule(run_label_A, ..., min_time_mode=False)
4. Stage 2 — auto_schedule(run_label_B, ..., min_time_mode=True)
5. 비교: schedule_task WHERE run_label IN (run_label_A, run_label_B)
```

production_batch 복제 SQL 스케치:

```sql
INSERT INTO production_batch (
    run_label, batch_seq, batch_group, sales_order_id, sales_order_line,
    process_name, sq_mm2, sheath_color, due_date, customer_priority,
    drum_count, total_length_m, extra_length_m, line_speed_mpm,
    setup_time_min, estimated_duration_min, status, ...
)
SELECT
    'RUN_LABEL_B', batch_seq, batch_group, sales_order_id, sales_order_line,
    process_name, sq_mm2, sheath_color, due_date, customer_priority,
    drum_count, total_length_m, extra_length_m, line_speed_mpm,
    setup_time_min, estimated_duration_min, 'planned', ...
FROM production_batch WHERE run_label = 'RUN_LABEL_A';
```

(실제 컬럼은 `app/infrastructure/models/production_batch.py` 참조 — PK/FK 외 모두 복제)

## 3. 비교 메트릭

`result` dict 의 두 path 출력을 비교:

| 메트릭               | weighted-sum         | lex_min_time                           |
| -------------------- | -------------------- | -------------------------------------- |
| `solver_status`      | OPTIMAL / FEASIBLE   | OPTIMAL / FEASIBLE                     |
| `objective_value`    | 가중합 (단일 scalar) | makespan (Phase B 최적값)              |
| `solver_wall_time_s` | wall-time            | Phase A + Phase B 합산 wall-time       |
| `solver_mode`        | `"weighted_sum"`     | `"lex_min_time"`                       |
| `lex_t_star`         | (없음)               | 모든 납기 충족 시 0 / 위반시 분 단위   |
| `lex_makespan_min`   | (없음)               | Phase B makespan                       |
| `lex_all_due_met`    | (없음)               | T\* == 0 alias                         |
| `total_tasks`        | int                  | int — 같은 batch 면 같은 값            |
| `violations`         | list                 | list                                   |
| `warnings`           | list                 | list (lex INFEASIBLE 폴백 시 1건 추가) |

schedule_task 비교 (per task_id 매핑):

- `equipment_code` — 설비 동일성
- `start_datetime` / `end_datetime` — 시각 차이
- `setup_time_min` — 셋업 시간

## 4. 비교 결과 해석

### 4.1 lex 우월 시나리오 (예측)

- **납기 위반이 있는 over-saturated 입력**: lex Phase A 가 max_tardiness 를
  명시적으로 최소화 → weighted-sum 의 (가중치 비율 의존) 균형보다 납기 우선.
- **on-time 그룹들의 makespan 단축**: Phase B 가 명시적으로 최소.

### 4.2 weighted-sum 우월 시나리오

- **idle / chain / EDD 등 secondary metric 이 중요**: lex 는 납기/makespan
  외 모든 메트릭을 무시. weighted-sum 은 idle / EDD / chain / slack 이
  objective 에 반영.
- **색상 체인 유지 필요**: lex 는 chain_terms 무시 → 같은 색상 클러스터의
  연속 배치가 깨질 수 있음.

### 4.3 동치 시나리오

- 모든 그룹이 납기 여유 + idle/EDD penalty 가 0 (small problem). lex/weighted
  결과가 같거나 makespan 만 차이.

## 5. INFEASIBLE 폴백 동작

`solve_lex_min_time` 의 Phase A 가 INFEASIBLE 이면 모델 자체가 깨진 것
(weighted-sum 도 INFEASIBLE). orchestrator 는 자동으로
`weighted_sum_fallback_from_lex` 모드로 전환 — `result["solver_mode"]` 로
관측. fallback 시 warnings 에 `"lex_min_time INFEASIBLE_A → weighted-sum
폴백"` 1건 추가.

## 6. Phase 5 §9.2 본 비교 결과

**측정**: 2026-04-26
**입력**: `run_label=20260425_225331` (Stage 1 ingest 985 production_batch,
실 ERP 데이터), `time_limit_sec=30`, `tardiness_hard=False`,
`sheath_color_hard=False` (양쪽 동일 — INFEASIBLE 회피용 soft 모드)
**스크립트**: `/tmp/lex_dual_run.py` (cp_sat_schedule 직접 호출,
production_batch.status `planned` reset → 호출 → schedule_task dump 패턴)
**원시 출력**: `/tmp/lex_dual_run.json`

### 6.1 솔버 메트릭

| 메트릭               | weighted-sum (Run 1)       | lex_min_time (Run 2) | 비고                         |
| -------------------- | -------------------------- | -------------------- | ---------------------------- |
| `solver_mode`        | `weighted_sum`             | `lex_min_time`       | lex 폴백 없음                |
| `solver_status`      | FEASIBLE                   | **OPTIMAL**          | lex 가 OPTIMAL 도달          |
| `solver_wall_time_s` | 30.04 (타임아웃 hit)       | **0.19**             | lex 가 158× 빠름             |
| `total_tasks`        | 103                        | 105                  | lex 가 2 groups 더 placement |
| `objective_value`    | 8,113,247,190,597 (가중합) | — (lex 분리)         | 가중합 vs lex 분리           |
| `lex_t_star` (분)    | (없음)                     | 19,140               | 약 13.3일 max tardiness      |
| `lex_makespan_min`   | (없음)                     | 24,074               | 약 16.7일 makespan           |
| `lex_all_due_met`    | (없음)                     | False                | over-saturated 입력 확인     |
| `warnings`           | 2                          | 3                    | lex 가 1건 추가              |

**핵심 관찰**:

1. **lex 가 wall-time 158× 빠름** — Phase A (max_tardiness 최소) +
   Phase B (makespan 최소) 의 두 작은 LP 가 weighted-sum 의 거대 가중합
   탐색보다 훨씬 빠르게 OPTIMAL 도달.
2. **lex 가 2 groups 더 placement** — 입력이 모든 납기 충족 불가능
   (T\*>0) 인 over-saturated 시나리오에서 lex 가 더 많은 그룹을 배치 가능
   하다는 것은 자연스러운 결과 (Phase A 가 max_tardiness 만 최소화하므로
   tardiness 간 차이를 무시할 수 있어 더 많은 placement 허용).
3. **weighted-sum 은 timeout 으로 sub-optimal** — 30 초 한도 내 OPTIMAL
   도달 실패. 가중치 비율이 큰 (8E12) 가중합이 탐색 공간을 폭발시킴.

### 6.2 schedule_task drift

| 비교 항목              | 값  | 비고                                 |
| ---------------------- | --- | ------------------------------------ |
| common batch_groups    | 96  | 양쪽 모두 placement 된 그룹          |
| ws-only groups         | 0   | weighted-sum 만 picked               |
| lex-only groups        | 2   | lex 만 picked (위 1번 결과)          |
| equipment changes      | 5   | 5 그룹 (5/96 = 5.2%) 이 다른 설비    |
| start_datetime changes | 52  | 52 그룹 (52/96 = 54.2%) 시작 시각 차 |

설비 배정은 대부분 동일 (94.8% 동일). 시작 시각은 절반 이상 변동 — lex 가
makespan 최소화 path 를 다르게 선택. 이는 lex Phase B 가 explicit 하게
makespan 을 minimize 하기 때문.

### 6.3 시스 색상 클러스터 보존율

| 모드         | 보존 / 전체 인접 쌍 | 비율   |
| ------------ | ------------------- | ------ |
| weighted-sum | 57 / 57             | 100.0% |
| lex_min_time | 59 / 59             | 100.0% |

**양쪽 모두 100% 보존** — 시스 (저압/고압) 그룹이 같은 prefix
(A100\_/A120\_/FH\_/FL\_) 끼리 같은 설비에 연속 배치되는 비율. lex 가
chain_terms 를 무시한다는 §4.2 우려에도 불구하고 본 데이터에서는 색상
체인이 100% 보존됨. 이는 (a) sheath cluster 가 hard constraint 로 들어
가 있지 않고도 greedy 단계의 sort 정책 (cluster_sort_key) 이 연속 배치를
보장하기 때문 — CP-SAT 의 chain_terms penalty 와 무관하게 캘린더 그리디
가 클러스터 단위 정렬을 enforce.

### 6.4 캘린더 그리디 makespan

| 모드         | greedy timeline makespan | 비고                            |
| ------------ | ------------------------ | ------------------------------- |
| weighted-sum | 38,400 min (~640h)       | CP-SAT order → greedy placement |
| lex_min_time | 38,400 min (~640h)       | 동일 결과                       |
| **delta**    | **+0 min (+0.00%)**      | 동치                            |

CP-SAT 는 group order 를 결정하고, 캘린더 그리디가 실제 시작/종료 시각을
배정. 본 데이터에서는 두 path 의 group order 차이가 캘린더 그리디 단계
에서 동일 makespan 으로 수렴. 이는 (a) 캘린더 calendar (작업가능시간)
제약이 makespan 을 dominant 하게 결정 (b) 두 path 모두 동일 캘린더 위에
서 각자 최선의 packing 을 함을 시사.

> 주의: lex 의 `lex_makespan_min=24,074` 은 **CP-SAT 모델 내부의 work-min
> 단위 makespan** (작업가능 분만 셈, off-hours 미포함). 캘린더 그리디
> 38,400 min 은 **wall-clock minutes** (off-hours 포함). 두 값은 직접
> 비교 불가하나 같은 입력에 대한 두 mode 의 wall-clock makespan 이 동일
> 하다는 것이 핵심.

### 6.5 결론 / 권장 default

| 시나리오                               | 권장 모드                | 근거                                                                     |
| -------------------------------------- | ------------------------ | ------------------------------------------------------------------------ |
| **납기 over-saturated (T\*>0 가능성)** | `lex_min_time`           | 158× 빠름, OPTIMAL 보장, 더 많은 placement, 동일 makespan                |
| **납기 모두 충족 가능 (T\*=0 예상)**   | weighted-sum             | secondary metric (idle / EDD / chain) 함께 최적화                        |
| **dev / ad-hoc 빠른 답이 필요한 경우** | `lex_min_time`           | 158× wall-time 우월                                                      |
| **production batch (현재 default)**    | weighted-sum (변경 없음) | 본 측정만으로 default 변경하기엔 sample 1건 — 추가 시나리오 검증 후 결정 |

### 6.6 Caveats

- **단일 measurement** — `run_label=20260425_225331` (985 batch) 한 set
  결과. 다른 입력 (납기 모두 충족 가능, batch 수 < 100, 시스 비중 높음
  등) 시나리오 별 추가 측정 필요.
- **soft constraint 모드** — `tardiness_hard=False`,
  `sheath_color_hard=False` 로 측정. retry harness 의 default Level 0
  (양쪽 hard) 환경에서는 두 모드 모두 INFEASIBLE 발생 (data 가 hard 납기
  제약 위반). production retry 단계 (Level 3 soft 도달 시) 에서만 lex
  유의미.
- **schedule_optimizer (greedy) idempotency** — production_batch.status
  를 reset 후 호출하므로 idempotent. 단 호출 후 status 가 'scheduled'
  로 mutate 되어 정상 운영 시에는 stage2 가 다시 'planned' 로 전환 필요.
