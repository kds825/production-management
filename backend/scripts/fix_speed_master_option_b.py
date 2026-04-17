"""
SpeedMaster Option B-refined 적용 — 2026-04-17 라운드.

Why:
- 현 DB 는 seed_db.py 와 divergence 상태. ST-54BO1/2/3 에 수동 입력된 낮은 값
  (fallback 10mpm 근처)이 연선 duration 2.5~3배 과다의 실질 원인.
- TP-1 / TP-GD / CA-* / SH-B100 / ST-AL6BO 등 9개 설비는 행 아예 없어 fallback 의존.
- TP-2 는 EX-B100 값 복제 상태(잘못됨)이지만 스케줄러가 배정 안 하므로 미변경.

동작:
- (equipment_code, cross_section) 조합이 DB 에 있으면 UPDATE, 없으면 INSERT.
- TP-2 는 DESIRED_ROWS 에 없어 자동으로 skip.
- 멱등: 여러 번 실행해도 동일 결과.

실행:
    source backend/venv/bin/activate
    cd backend
    python scripts/fix_speed_master_option_b.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models import SpeedMaster  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# 타겟 SpeedMaster 행 정의. tuple: (equipment_code, cross_section, mpm,
# setup_spec_min, product_type)
# 값 근거: seed_db.py 의 기존 패턴 + REVISED.md 의 PDF 레퍼런스(120SQ → 25mpm).
# 생산팀 실측 대조 필요.
# ──────────────────────────────────────────────────────────────────────
DESIRED_ROWS: list[tuple[str, float, float, int, str]] = []


def _add_range(
    equipment: str, product_type: str, setup: int, pairs: list[tuple[float, float]]
) -> None:
    for sq, mpm in pairs:
        DESIRED_ROWS.append((equipment, float(sq), float(mpm), setup, product_type))


# 연선 (setup_spec_min=210, SQ 교체 setup)
# ST-54BO1: CU 61연선 70~800SQ — 기존 8행 UPDATE + 3행 INSERT
_add_range(
    "ST-54BO1",
    "61연선 CU",
    210,
    [
        (70, 30),
        (95, 28),
        (120, 25),
        (150, 22),
        (185, 20),
        (240, 18),
        (300, 15),
        (400, 12),
        (500, 10),
        (630, 8),
        (800, 6),
    ],
)
# ST-54BO2: AL 61연선 — 기존 2행 UPDATE + 9행 INSERT
_add_range(
    "ST-54BO2",
    "61연선 AL",
    210,
    [
        (70, 32),
        (95, 30),
        (120, 27),
        (150, 24),
        (185, 22),
        (240, 20),
        (300, 17),
        (380, 15),
        (400, 14),
        (500, 12),
        (507, 12),
        (630, 10),
        (633, 10),
        (800, 8),
    ],
)
# ST-54BO3: AL 61연선 — 기존 3행 UPDATE + 8행 INSERT (+107SQ, 633SQ 기존)
_add_range(
    "ST-54BO3",
    "61연선 AL",
    210,
    [
        (70, 32),
        (95, 30),
        (107, 28),
        (120, 27),
        (150, 24),
        (185, 22),
        (240, 20),
        (300, 17),
        (380, 15),
        (400, 14),
        (500, 12),
        (630, 10),
        (633, 10),
        (800, 8),
    ],
)
# ST-30BO: AL 19연선 70~120SQ
_add_range(
    "ST-30BO",
    "19연선 AL",
    210,
    [
        (70, 25),
        (95, 22),
        (120, 20),
    ],
)
# ST-44BO: AL 19연선 70~240SQ
_add_range(
    "ST-44BO",
    "19연선 AL",
    210,
    [
        (70, 25),
        (95, 22),
        (120, 20),
        (150, 18),
        (185, 16),
        (240, 14),
    ],
)
# ST-1150BC: AL B/C 4~16SQ
_add_range(
    "ST-1150BC",
    "B/C AL",
    210,
    [
        (4, 40),
        (6, 38),
        (10, 35),
        (16, 30),
    ],
)
# ST-T6B0: CU 7연선 — 25/35 OK, 50 만 13.3 교정
_add_range(
    "ST-T6B0",
    "7연선 CU",
    210,
    [
        (25, 25),
        (35, 25),
        (50, 20),
    ],
)
# ST-AL6BO: AL 7연선 25~50SQ
_add_range(
    "ST-AL6BO",
    "7연선 AL",
    210,
    [
        (25, 22),
        (35, 20),
        (50, 17),
    ],
)

# 연합 (setup_spec_min=120)
_add_range(
    "CA-12BO",
    "연합",
    120,
    [
        (1.5, 35),
        (2.5, 32),
        (4, 30),
        (6, 28),
    ],
)
_add_range(
    "CA-4BO",
    "연합",
    120,
    [
        (35, 28),
        (50, 25),
        (70, 22),
        (95, 20),
    ],
)
_add_range(
    "CA-LU",
    "연합",
    120,
    [
        (35, 30),
        (50, 28),
        (70, 25),
        (95, 22),
        (120, 20),
        (150, 18),
    ],
)

# T/P (setup_spec_min=180)
_TP_DATA = [
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
_add_range("TP-1", "T/P", 180, _TP_DATA)
_add_range("TP-GD", "T/P", 180, _TP_DATA)

# 고압시스 SH-B100 (setup_spec_min=30)
_add_range(
    "SH-B100",
    "고압시스 저용량",
    30,
    [
        (16, 8),
        (25, 7.5),
        (35, 7),
        (50, 6.5),
    ],
)


def main(dry_run: bool = False) -> int:
    s = SessionLocal()
    try:
        existing_map: dict[tuple[str, float], SpeedMaster] = {}
        for sm in s.query(SpeedMaster).all():
            sq = float(sm.cross_section or 0)
            existing_map[(sm.equipment_code, sq)] = sm

        inserts: list[tuple[str, float, float]] = []
        updates: list[
            tuple[str, float, float, float]
        ] = []  # (eq, sq, old_mpm, new_mpm)

        for eq, sq, mpm, setup, ptype in DESIRED_ROWS:
            key = (eq, sq)
            hr = round(mpm * 60, 1)
            if key in existing_map:
                row = existing_map[key]
                old_mpm = float(row.line_speed_mpm or 0)
                if abs(old_mpm - mpm) < 1e-6:
                    continue  # no-op
                updates.append((eq, sq, old_mpm, mpm))
                if not dry_run:
                    row.line_speed_mpm = mpm
                    row.line_speed_hr = hr
                    row.setup_spec_min = setup
                    row.product_type = ptype
            else:
                inserts.append((eq, sq, mpm))
                if not dry_run:
                    s.add(
                        SpeedMaster(
                            equipment_code=eq,
                            product_type=ptype,
                            cross_section=sq,
                            line_speed_mpm=mpm,
                            line_speed_hr=hr,
                            setup_spec_min=setup,
                        )
                    )

        print(f"DESIRED_ROWS total = {len(DESIRED_ROWS)}")
        print(f"INSERT planned     = {len(inserts)}")
        print(f"UPDATE planned     = {len(updates)}")
        if updates:
            print("UPDATE preview:")
            for eq, sq, old, new in updates[:20]:
                print(f"  {eq:<12} SQ={sq:>6} mpm {old:>6.2f} -> {new:>5}")
            if len(updates) > 20:
                print(f"  ... {len(updates) - 20} more")
        if inserts:
            print("INSERT preview:")
            for eq, sq, mpm in inserts[:20]:
                print(f"  {eq:<12} SQ={sq:>6} mpm {mpm}")
            if len(inserts) > 20:
                print(f"  ... {len(inserts) - 20} more")

        if dry_run:
            print("[dry-run] no DB commit.")
            return 0

        s.commit()
        total = s.query(SpeedMaster).count()
        print(f"committed. SpeedMaster total rows now = {total}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    raise SystemExit(main(dry_run=dry))
