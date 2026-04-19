"""
SpeedMaster Option A2' (conservative) — 2026-04-17 rollback 후 재적용.

- 빈 설비 9개(ST-AL6BO, ST-30BO, ST-44BO, ST-1150BC, CA-12BO, CA-4BO, CA-LU,
  TP-1, TP-GD, SH-B100) 에만 INSERT.
- 기존 값 있는 설비(ST-54BO1/2/3, ST-T6B0, EX-*, SH-A*) 는 미변경.
- TP-2 는 A2.1 의 tp_data UPDATE 를 재적용 (EX-B100 복제 → 정상값).
- 멱등: (eq, sq) 이미 존재하면 skip.

실행: cd backend && python scripts/fix_speed_master_conservative.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models import SpeedMaster  # noqa: E402


# 빈 설비에 INSERT 할 rows: (equipment, product_type, setup_spec_min, [(sq, mpm), ...])
_INSERT_ROWS: list[tuple[str, str, int, list[tuple[float, float]]]] = [
    # 연선 (AL 7연선)
    ("ST-AL6BO", "7연선 AL", 210, [(25, 13), (35, 12), (50, 10)]),
    # 연선 (AL 19연선)
    ("ST-30BO", "19연선 AL", 210, [(70, 12), (95, 11), (120, 10)]),
    (
        "ST-44BO",
        "19연선 AL",
        210,
        [(70, 12), (95, 11), (120, 10), (150, 10), (185, 9), (240, 8)],
    ),
    # 연선 (AL B/C 소단면)
    ("ST-1150BC", "B/C AL", 210, [(4, 20), (6, 18), (10, 15), (16, 13)]),
    # 연합
    ("CA-12BO", "연합", 120, [(1.5, 20), (2.5, 18), (4, 15), (6, 13)]),
    ("CA-4BO", "연합", 120, [(35, 13), (50, 12), (70, 11), (95, 10)]),
    (
        "CA-LU",
        "연합",
        120,
        [(35, 15), (50, 13), (70, 12), (95, 11), (120, 10), (150, 9)],
    ),
    # T/P (setup 180)
    (
        "TP-1",
        "T/P",
        180,
        [
            (16, 25),
            (25, 24),
            (35, 22),
            (50, 20),
            (70, 18),
            (95, 16),
            (120, 14),
            (150, 13),
            (185, 12),
            (240, 10),
            (300, 9),
            (400, 8),
        ],
    ),
    (
        "TP-GD",
        "T/P",
        180,
        [
            (16, 25),
            (25, 24),
            (35, 22),
            (50, 20),
            (70, 18),
            (95, 16),
            (120, 14),
            (150, 13),
            (185, 12),
            (240, 10),
            (300, 9),
            (400, 8),
        ],
    ),
    # 고압시스 소용량
    ("SH-B100", "고압시스 저용량", 30, [(16, 6), (25, 5.5), (35, 5), (50, 4.5)]),
]

# TP-2 는 A2.1 에서 정상화했던 tp_data UPDATE 재적용 (현재 EX-B100 값 복제 상태)
_TP2_UPDATE: list[tuple[float, float]] = [
    (16, 40),
    (25, 38),
    (35, 35),
    (50, 32),
    (70, 28),
    (95, 25),
    (120, 22),
    (150, 20),
    (185, 18),
    (240, 15),
    (300, 12),
    (400, 10),
]


def main(dry_run: bool = False) -> int:
    s = SessionLocal()
    try:
        # LANDMINE: 키 튜플은 (equipment_code, cross_section) 뿐. SH-A100 같은 설비는
        # 같은 (eq, sq) 에 product_type 다른 다수 행이 존재 → 첫 것만 index 에 남김.
        # 현재 _INSERT_ROWS 에는 multi-product-type 설비가 없어 안전.
        existing_map: dict[tuple[str, float], SpeedMaster] = {}
        for sm in s.query(SpeedMaster).all():
            existing_map[(sm.equipment_code, float(sm.cross_section or 0))] = sm

        inserts: list[tuple[str, float, float]] = []
        updates: list[tuple[str, float, float, float]] = []

        for eq, ptype, setup, pairs in _INSERT_ROWS:
            for sq, mpm in pairs:
                key = (eq, float(sq))
                if key in existing_map:
                    continue
                inserts.append((eq, float(sq), float(mpm)))
                if not dry_run:
                    s.add(
                        SpeedMaster(
                            equipment_code=eq,
                            product_type=ptype,
                            cross_section=float(sq),
                            line_speed_mpm=float(mpm),
                            line_speed_hr=round(float(mpm) * 60, 1),
                            setup_spec_min=setup,
                        )
                    )

        # TP-2 UPDATE (정상화)
        for sq, mpm in _TP2_UPDATE:
            key = ("TP-2", float(sq))
            row = existing_map.get(key)
            if row is None:
                continue
            old_mpm = float(row.line_speed_mpm or 0)
            if abs(old_mpm - mpm) < 1e-6:
                continue
            updates.append(("TP-2", float(sq), old_mpm, float(mpm)))
            if not dry_run:
                row.line_speed_mpm = float(mpm)
                row.line_speed_hr = round(float(mpm) * 60, 1)
                row.product_type = "T/P"
                row.setup_spec_min = 180

        print(f"INSERT planned = {len(inserts)}")
        print(f"UPDATE planned = {len(updates)} (TP-2 정상화)")
        for eq, sq, mpm in inserts[:20]:
            print(f"  INS {eq:<12} SQ={sq:>6} mpm={mpm}")
        if len(inserts) > 20:
            print(f"  ... {len(inserts) - 20} more")
        for eq, sq, old, new in updates[:20]:
            print(f"  UPD {eq:<12} SQ={sq:>6} {old:>5.1f} -> {new}")

        if dry_run:
            print("[dry-run] no commit.")
            return 0

        s.commit()
        total = s.query(SpeedMaster).count()
        print(f"committed. SpeedMaster total rows = {total}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
