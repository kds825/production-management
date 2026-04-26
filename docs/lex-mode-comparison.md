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

## 6. Phase 5 §9.2 본 비교 (TBD)

실 ERP 파일 기반 dual-run 결과 + per-task drift 표 + per-cluster 색상
체인 보존율 + makespan 차이를 본 문서에 추가 예정 (Phase 5 §9.2 종료
시점).
