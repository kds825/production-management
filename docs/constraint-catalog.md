# Constraint Catalog

**Audience:** anyone adding/editing solver behavior, plus auditors who want
a single page that lists every active constraint by id and Korean name.
**Source of truth:** the `constraint_config` table in the live database.
This file is a **dump** generated against the current dev DB; re-run the
generator below before publishing in case the table has drifted.

---

## What is `constraint_config`?

`constraint_config` is the live, mutable source of truth for **which
constraints the solver respects** and **what their parameters/weights
are**. The model lives at
`backend/app/infrastructure/models/constraint_config.py`. Every row has:

- `constraint_id` (PK, e.g. `4-1`, `W-EDDP`)
- `constraint_name` — Korean name shown in the admin UI and Decision Card.
- `category` — coarse grouping for the admin tab (`색상관리`, `셋업/교체`, …).
- `is_enabled` — boolean toggle. Disabled rows are skipped at solve time.
- `priority` — admin-tab sort order (smaller = earlier).
- `impact_level` — display-only severity hint.
- `params_json` — JSONB blob; the solver reads keys it cares about
  (e.g. `cv_min`, `weight`).
- `applicable_processes` — JSONB array of stage codes the rule applies to.
- `implementation_type` — one of `table_param` / `code_logic` / `hybrid`.
  See `docs/private-symbol-inventory.md` for the assignment table.
- `notes` — free-form audit text, surfaced in the admin UI history view.
- `updated_at` — last-write timestamp.

Mutation paths: `routes/constraints.py` (PATCH for params/toggle, POST for
baseline promote/reset). Every change is mirrored to
`constraint_config_history` so the admin tab can show a diff.

---

## Generator recipe (re-run before merge)

```bash
cd backend && source venv/bin/activate
python - <<'PY' > /tmp/constraint_catalog_table.md
from app.infrastructure.database import SessionLocal
from app.infrastructure.models.constraint_config import ConstraintConfig
import json
db = SessionLocal()
rows = db.query(ConstraintConfig).order_by(ConstraintConfig.constraint_id).all()
print("| ID | 한글명 | 카테고리 | implementation_type | priority | enabled | params_json |")
print("| --- | --- | --- | --- | --- | --- | --- |")
for r in rows:
    pj = json.dumps(r.params_json or {}, ensure_ascii=False) or "{}"
    if len(pj) > 60: pj = pj[:57] + "..."
    en = "✓" if r.is_enabled else "·"
    print(f"| `{r.constraint_id}` | {r.constraint_name or '-'} | {r.category or '-'} | "
          f"`{r.implementation_type or '-'}` | {r.priority} | {en} | `{pj}` |")
db.close()
PY
```

Then paste the output into the table below, replacing the previous block.

---

## Catalog (snapshot — generated on 2026-04-25 against dev DB)

`✓` = `is_enabled=true`; `·` = disabled. Snapshot taken after Week 9
seed-data sweep (the prior `_test_marker` rows from in-process integration
tests have been restored from `BASELINE_phase0-initial_20260423T101449Z`
history).

| ID        | 한글명                             | 카테고리      | implementation_type | priority | enabled | params_json                                                    |
| --------- | ---------------------------------- | ------------- | ------------------- | -------- | ------- | -------------------------------------------------------------- |
| `1-1`     | 거래처 우선순위                    | 납기/우선순위 | `table_param`       | 1        | ✓       | `{}`                                                           |
| `1-2`     | 납기 기준(도착/출하)               | 납기/우선순위 | `table_param`       | 2        | ✓       | `{"transport_days": 1}`                                        |
| `1-3`     | 긴급 변경 대응                     | 납기/우선순위 | `hybrid`            | 3        | ✓       | `{}`                                                           |
| `2-1`     | 재공 활용(연선/절연 재고우선)      | SM수량/재고   | `hybrid`            | 4        | ✓       | `{"loss_limit_pct": 8, "min_remainder_m": 50, "shortage_t...`  |
| `2-2`     | 외주 조건(≤10SQ, 고내화16)         | SM수량/재고   | `table_param`       | 5        | ✓       | `{}`                                                           |
| `2-3`     | 틀단위 기준 생산                   | SM수량/재고   | `table_param`       | 6        | ✓       | `{}`                                                           |
| `2-4`     | 61연선 분리                        | SM수량/재고   | `code_logic`        | 7        | ✓       | `{}`                                                           |
| `3-1`     | 색상별 여척 추가                   | 색상관리      | `table_param`       | 10       | ✓       | `{"extra_length_m": 7, "sample_extra_m": 10}`                  |
| `3-2`     | 색상 묶음 배치                     | 색상관리      | `code_logic`        | 11       | ✓       | `{}`                                                           |
| `3-3`     | 설비별 색상그룹 제한               | 색상관리      | `table_param`       | 12       | ✓       | `{}`                                                           |
| `3-4`     | 잔량 흑색 소진                     | 색상관리      | `table_param`       | 13       | ·       | `{"remnant_threshold_m": 200}`                                 |
| `4-1`     | 규격교체 시간                      | 셋업/교체     | `table_param`       | 20       | ✓       | `{"cv_min": 300, "sheath_min": 30, "stranding_min": 30, "i...` |
| `4-2`     | 색상교체 시간                      | 셋업/교체     | `table_param`       | 21       | ✓       | `{"sheath_color_min": 120}`                                    |
| `4-3`     | 드럼 권취 시간                     | 셋업/교체     | `table_param`       | 22       | ✓       | `{}`                                                           |
| `4-4`     | 용접 시간                          | 셋업/교체     | `table_param`       | 23       | ✓       | `{"welding_min": 30}`                                          |
| `4-5`     | 테이핑 속도 제한                   | 셋업/교체     | `table_param`       | 24       | ✓       | `{}`                                                           |
| `5-1`     | SQ 기준 설비 배정                  | 품명/규격     | `table_param`       | 30       | ✓       | `{}`                                                           |
| `5-2`     | 연선방식 구분(압축/원형/수밀)      | 품명/규격     | `table_param`       | 31       | ✓       | `{}`                                                           |
| `5-3`     | 다심 우선배치                      | 품명/규격     | `code_logic`        | 32       | ✓       | `{}`                                                           |
| `5-4`     | 나선/연동선 별도                   | 품명/규격     | `table_param`       | 33       | ✓       | `{}`                                                           |
| `5-5`     | TFR-GV 절연 생략                   | 품명/규격     | `table_param`       | 34       | ✓       | `{}`                                                           |
| `6-1`     | 안전교육(매월 마지막2주 월요일)    | 캘린더        | `table_param`       | 40       | ✓       | `{"deduction_hours": 2}`                                       |
| `6-2`     | 금요일 야간 단축                   | 캘린더        | `table_param`       | 41       | ✓       | `{"friday_hours": 14}`                                         |
| `6-3`     | 부재자 계획 반영                   | 캘린더        | `hybrid`            | 42       | ✓       | `{}`                                                           |
| `6-4`     | 공휴일/휴무                        | 캘린더        | `table_param`       | 43       | ✓       | `{}`                                                           |
| `7-1`     | 불량 재작업 버퍼                   | 불량/설비     | `hybrid`            | 50       | ✓       | `{"defect_buffer_pct": 0.0}`                                   |
| `7-2`     | 설비고장 대체                      | 불량/설비     | `code_logic`        | 51       | ·       | `{}`                                                           |
| `8-1`     | 테이프/컴파운드 재고               | 자재수급      | `hybrid`            | 60       | ·       | `{}`                                                           |
| `8-2`     | 조달 리드타임                      | 자재수급      | `table_param`       | 61       | ·       | `{"lead_time_days": 7}`                                        |
| `8-3`     | CU/AL 원자재                       | 자재수급      | `hybrid`            | 62       | ·       | `{}`                                                           |
| `9-1`     | 다심 연합 트리거                   | 후속공정      | `code_logic`        | 70       | ✓       | `{}`                                                           |
| `9-2`     | GC 라우팅 제외(B100 X)             | 후속공정      | `table_param`       | 71       | ✓       | `{}`                                                           |
| `9-3`     | (5-5와 통합) TFR-GV 바이패스       | 후속공정      | `table_param`       | 72       | ✓       | `{}`                                                           |
| `10-1`    | (5-1과 통합) SQ 기준 설비 배정     | 기타          | `table_param`       | 80       | ✓       | `{}`                                                           |
| `10-2`    | CU/AL 재질 설비 분리               | 기타          | `table_param`       | 81       | ✓       | `{}`                                                           |
| `10-3`    | 시스 재질 라우팅                   | 기타          | `table_param`       | 82       | ✓       | `{}`                                                           |
| `10-4`    | 전압별 드럼 분류                   | 기타          | `table_param`       | 83       | ✓       | `{}`                                                           |
| `10-5`    | 4심 계산법                         | 기타          | `table_param`       | 84       | ✓       | `{}`                                                           |
| `W-CHAIN` | 색상 인접 비용                     | 색상관리      | `code_logic`        | 50       | ✓       | `{"weight": 120}`                                              |
| `W-DHARD` | 납기 강제 (soft fallback)          | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 100000}`                                           |
| `W-EDDM`  | EDD pair 위반 (past-due ↔ on-time) | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 1000000000}`                                       |
| `W-EDDP`  | EDD pair 위반 (normal)             | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 10000}`                                            |
| `W-IDLE`  | 유휴 분당 패널티                   | 효율          | `code_logic`        | 50       | ✓       | `{"weight": 1}`                                                |
| `W-PSEV`  | 과거 납기 심각도 K                 | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 5}`                                                |
| `W-SLACK` | 납기 임박 가중 base                | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 100000}`                                           |
| `W-TCRIT` | 납기 지연 (critical)               | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 10000000}`                                         |
| `W-TNORM` | 납기 지연 (normal)                 | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 100000}`                                           |
| `W-TRANS` | 설비 전환 셋업                     | 셋업/교체     | `code_logic`        | 50       | ✓       | `{"weight": 180}`                                              |
| `W-TURG`  | 납기 지연 (urgent)                 | 납기/우선순위 | `code_logic`        | 50       | ✓       | `{"weight": 1000000}`                                          |

---

## `W-*` rows (Week 5 weight migration)

The `W-*` ids are **objective-function weights**, not solver constraints
in the classical sense. Week 5 Task 5A migrated them out of hardcoded
constants in `services/solver/objective.py` into `constraint_config` rows
so admins can tune them via the same UI as everything else. Full audit
trail and per-weight rationale lives in **`docs/hardcoded-weights.md`**.

`params_json.weight` is the only field the solver reads from these rows;
other fields exist purely so the admin tab UI can render them in the
existing list/detail templates.

---

## How to add a new constraint

1. **Pick an id.** Numeric scheme (`<group>-<n>`) for domain rules,
   `W-*` for objective weights.
2. **Add a `constraint_config` row.** Either via Alembic migration
   (preferred for shipped behavior) or `backend/scripts/seed_db.py` for
   local seed data. Set `is_enabled=true` only after the solver wires the
   id; otherwise toggle defaults on a half-implemented feature can break
   parity fixtures.
3. **Wire it.**
   - `implementation_type=table_param`: read `params_json` from
     `services/solver/spec.py` and pass into the OR-Tools model. No code
     branching needed beyond the param.
   - `implementation_type=code_logic`: add a branch in
     `services/solver/objective.py` (for weights) or
     `services/solver/model_builder.py` (for hard/soft constraints).
   - `implementation_type=hybrid`: both — params drive coefficients,
     code logic drives the structural shape.
4. **Add a parity fixture.** Pick the closest existing scenario in
   `backend/tests/fixtures/parity/`, copy + edit the seed script in
   `scripts/seed_parity_scenarios/`, regenerate the frozen output via
   `scripts/parity_freeze_current_behavior.py`, and commit with a
   `parity-update:` subject so the parity-fixture-guard CI lets it
   through.
5. **Document it here.** Re-run the generator above and replace the
   table; bump the snapshot date in the heading.
