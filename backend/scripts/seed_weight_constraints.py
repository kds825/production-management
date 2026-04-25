"""Idempotent seed of weight-bearing constraint_config rows.

Per Week 5 migration (Task 5A.2): each Python constant in
docs/hardcoded-weights.md becomes a constraint_config row with
implementation_type='code_logic' and params_json={"weight": <current_value>}.

Why a separate script (vs. inlining into seed_db.py only):
  - seed_db.py is gated by a "skip if customer_master not empty" check, so
    re-running it on an already-seeded DB is a no-op. Weight rows must be
    addable to live DBs that were seeded before Week 5.
  - This script is row-level idempotent — each constraint_id is checked
    individually, so partial failures + re-runs converge.

Re-running this script is safe — existing rows are skipped.

Usage:
    cd backend
    source venv/bin/activate
    python scripts/seed_weight_constraints.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models.constraint_config import ConstraintConfig  # noqa: E402

# (constraint_id, korean_name, category, weight, priority, notes)
# Values MUST match the current Python constants exactly — parity invariant.
# Source: backend/app/services/cp_sat_optimizer.py + backend/app/domain/constants.py.
#
# constraint_id 는 String(10) 컬럼이므로 ≤10 chars. `W-` prefix = Week 5 weight
# migration. 기존 `1-1 .. 10-5` 컨벤션과 충돌하지 않는 별도 네임스페이스.
WEIGHT_SEEDS: list[tuple[str, str, str, int, int, str]] = [
    # ── domain/constants.py baselines ──
    (
        "W-DHARD",
        "납기 강제 (soft fallback)",
        "납기/우선순위",
        100_000,
        50,
        "tardiness_hard=False 모드의 fallback 가중치. _DUE_HARD_WEIGHT 와 1:1.",
    ),
    (
        "W-CHAIN",
        "색상 인접 비용",
        "색상관리",
        120,
        50,
        "_CHAIN_WEIGHT — 색상 chain proximity 비용. compose_objective 미소비, "
        "ModelWeights 번들 통과용 (Week 5 migration parity invariant).",
    ),
    (
        "W-TRANS",
        "설비 전환 셋업",
        "셋업/교체",
        180,
        50,
        "_TRANSITION_WEIGHT — Round 2 HIGH #6 연선 SQ 전이 penalty.",
    ),
    # ── cp_sat_optimizer.py scalars ──
    (
        "W-IDLE",
        "유휴 분당 패널티",
        "효율",
        1,
        50,
        "_IDLE_WEIGHT — pipeline idle minute 당 1.",
    ),
    (
        "W-SLACK",
        "납기 임박 가중 base",
        "납기/우선순위",
        100_000,
        50,
        "_SLACK_WEIGHT_BASE — on-time 그룹 slack-weighted completion. "
        "weight = max(1, base // slack_min).",
    ),
    (
        "W-PSEV",
        "과거 납기 심각도 K",
        "납기/우선순위",
        5,
        50,
        "_PAST_SEVERITY_K — past-due severity divisor. "
        "1 + past_days/K 배율로 _TARDINESS_WEIGHT 곱해짐.",
    ),
    (
        "W-EDDP",
        "EDD pair 위반 (normal)",
        "납기/우선순위",
        10_000,
        50,
        "_EDD_PAIR_WEIGHT — 같은 공정+공유 설비 후보 쌍에서 납기 빠른 쪽이 "
        "뒤에 시작하면 페널티.",
    ),
    (
        "W-EDDM",
        "EDD pair 위반 (past-due ↔ on-time)",
        "납기/우선순위",
        1_000_000_000,
        50,
        "_EDD_MIXED_PASTDUE_WEIGHT — past-due 그룹이 on-time 그룹 뒤에 놓이면 "
        "heavy penalty. Hybrid C — 공장관리자 mental model 강제.",
    ),
    # ── _TARDINESS_WEIGHT dict expansion ──
    # _TARDINESS_WEIGHT = {
    #     "critical": _DUE_HARD_WEIGHT * 100,
    #     "urgent":   _DUE_HARD_WEIGHT * 10,
    #     "normal":   _DUE_HARD_WEIGHT,
    # }
    (
        "W-TCRIT",
        "납기 지연 (critical)",
        "납기/우선순위",
        100_000 * 100,  # = 10_000_000
        50,
        "_TARDINESS_WEIGHT['critical'] = _DUE_HARD_WEIGHT × 100.",
    ),
    (
        "W-TURG",
        "납기 지연 (urgent)",
        "납기/우선순위",
        100_000 * 10,  # = 1_000_000
        50,
        "_TARDINESS_WEIGHT['urgent'] = _DUE_HARD_WEIGHT × 10.",
    ),
    (
        "W-TNORM",
        "납기 지연 (normal)",
        "납기/우선순위",
        100_000,
        50,
        "_TARDINESS_WEIGHT['normal'] = _DUE_HARD_WEIGHT.",
    ),
]


def main() -> int:
    db = SessionLocal()
    try:
        added = 0
        skipped = 0
        for cid, name, category, weight, priority, notes in WEIGHT_SEEDS:
            existing = db.query(ConstraintConfig).filter_by(constraint_id=cid).first()
            if existing is not None:
                print(f"  skip: {cid} (exists)")
                skipped += 1
                continue
            db.add(
                ConstraintConfig(
                    constraint_id=cid,
                    constraint_name=name,
                    category=category,
                    is_enabled=True,
                    priority=priority,
                    impact_level="★★",
                    params_json={"weight": weight},
                    applicable_processes=["전체"],
                    implementation_type="code_logic",
                    notes=notes,
                )
            )
            added += 1
            print(f"  add:  {cid} weight={weight} priority={priority}")
        db.commit()
        print(f"\nadded: {added}  skipped: {skipped}  total seeds: {len(WEIGHT_SEEDS)}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"FAILED: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
