"""SpeedMaster 재파생 helper — dry-run 우선, 실제 UPDATE 는 --apply 명시.

Why:
- 2026-04-18 분석에서 발견: DB 는 /20 기준, Excel 수식은 /24 기준. 두 derivation
  경로가 공존하여 유지보수 혼란.
- 이 스크립트는 (equipment_code, cross_section) 조합별 "예상 speed" 를 configurable
  한 effective_hours_per_day 로 재파생하고, DB 현재 값과 비교만 한다.
- `--apply` 플래그가 명시된 경우에만 UPDATE 실행. 기본은 dry-run.

근거 데이터:
- 일생산량 (m/day): `공정설비 및 규격 정리` 시트 (KBI 내부 문서)
- 틀당 연선길이 (m): 동 시트 — duration 검증용 (speed 계산에는 미사용)

사용:
    python scripts/recalc_speedmaster.py --base 20          # dry-run, /20
    python scripts/recalc_speedmaster.py --base 22          # dry-run, /22
    python scripts/recalc_speedmaster.py --base 20 --apply  # 실제 UPDATE

참고: 이 스크립트는 ST-* (연선) 만 대상. TP/EX/SH/CA 는 별도 로직 필요.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_HERE.parent / ".env")

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models import SpeedMaster  # noqa: E402


# KBI "공정설비 및 규격 정리" 시트 — CU 기준 규격 및 선속 일 생산량
# 키: SQ (int), 값: daily_production_m
DAILY_PRODUCTION_ST: dict[int, int] = {
    16: 30000,
    25: 30000,
    35: 30000,
    50: 16000,
    70: 14000,
    95: 14000,
    120: 14000,
    150: 13000,
    185: 13000,
    240: 12000,
    300: 12000,
    400: 12000,
}

TARGET_EQUIPMENTS = ("ST-54BO1", "ST-54BO2", "ST-54BO3", "ST-T6B0")


def derive_speed(daily_production_m: float, base_hours: float) -> float:
    """일생산량(m/day) → line_speed(m/min). base_hours = 하루 유효가동시간 가정."""
    return daily_production_m / (base_hours * 60.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base",
        type=float,
        default=20.0,
        help="일 유효가동시간 (hr). 기본 20 (현재 DB), 22 (연선 Mon-Thu 엄격), 24 (Excel 공식).",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB UPDATE 수행 (기본 dry-run).",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (
            db.query(SpeedMaster)
            .filter(SpeedMaster.equipment_code.in_(TARGET_EQUIPMENTS))
            .order_by(SpeedMaster.equipment_code, SpeedMaster.cross_section)
            .all()
        )

        print(
            f"base_hours = {args.base}  |  mode = {'APPLY' if args.apply else 'DRY-RUN'}"
        )
        print(
            f"{'eq':10} {'SQ':>5} {'daily':>7} {'current':>8} {'proposed':>9} {'delta':>7}"
        )

        changes: list[tuple[SpeedMaster, float]] = []
        for r in rows:
            sqi = int(float(r.cross_section or 0))
            daily = DAILY_PRODUCTION_ST.get(sqi)
            if daily is None:
                continue
            proposed = round(derive_speed(daily, args.base), 2)
            current = float(r.line_speed_mpm or 0)
            delta = round(proposed - current, 2)
            changes.append((r, proposed))
            print(
                f"{r.equipment_code:10} {sqi:5d} {daily:7d} "
                f"{current:8.2f} {proposed:9.2f} {delta:+7.2f}"
            )

        if args.apply:
            for r, proposed in changes:
                r.line_speed_mpm = proposed
                r.line_speed_hr = round(proposed * 60.0, 1)
            db.commit()
            print(f"\nAPPLIED {len(changes)} rows.")
        else:
            print(
                f"\nDRY-RUN — {len(changes)} rows would be updated. Use --apply to commit."
            )
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
