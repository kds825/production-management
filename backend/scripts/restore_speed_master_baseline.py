"""
SpeedMaster DB 를 backup JSON 상태로 완전 복원.

실행: cd backend && python scripts/restore_speed_master_baseline.py <backup.json> [--dry-run]

Why:
- 2026-04-17 A2 라운드의 공격적 UPDATE 가 PDF 해석 오류(slot 2일→pure 14hr)
  기반이었음이 판명됨. 그 이전 상태(118 rows) 로 되돌린 뒤 보수 재설계(A2')
  진행을 위한 깨끗한 시작점 확보.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.infrastructure.database import SessionLocal  # noqa: E402
from app.infrastructure.models import SpeedMaster  # noqa: E402


def main(backup_path: str, dry_run: bool) -> int:
    payload = json.loads(Path(backup_path).read_text())
    target_rows = payload["rows"]
    s = SessionLocal()
    try:
        cur = s.query(SpeedMaster).count()
        print(f"현재 DB rows = {cur}")
        print(f"복원 대상 rows = {len(target_rows)}")

        if dry_run:
            print("[dry-run] 변경 없음.")
            return 0

        s.query(SpeedMaster).delete()
        s.flush()

        for r in target_rows:
            s.add(
                SpeedMaster(
                    equipment_code=r["equipment_code"],
                    product_type=r["product_type"],
                    cross_section=r["cross_section"],
                    line_speed_mpm=r["line_speed_mpm"],
                    line_speed_hr=r["line_speed_hr"],
                    setup_start_min=r.get("setup_start_min") or 0,
                    setup_spec_min=r.get("setup_spec_min") or 0,
                    setup_color_min=r.get("setup_color_min") or 0,
                    setup_compound_min=r.get("setup_compound_min") or 0,
                )
            )
        s.commit()
        final = s.query(SpeedMaster).count()
        print(f"복원 완료. DB rows = {final}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: restore_speed_master_baseline.py <backup.json> [--dry-run]")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], "--dry-run" in sys.argv))
