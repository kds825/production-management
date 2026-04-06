"""
to-be 엑셀 vs 시스템 결과 비교 스크립트
- 연선 계획(1차) 시트와 시스템 Stage1 배치를 batch-level로 비교
- 실행: python scripts/compare_tobe.py <run_label>
"""

import sys
import xlrd
import requests
from collections import defaultdict

API_BASE = "http://localhost:8000"
TOBE_FILE = (
    "/Users/jaewookim/Desktop/Project/KBI_PoC/Documents/생산 계획 수주 정리(공정별).xls"
)


def parse_tobe_stranding(wb):
    """연선 계획(1차) 시트에서 배치 그룹별 수주 목록 추출"""
    sh = wb.sheet_by_name("연선 계획(1차)")
    groups = []
    current_group = None
    header_row = 4  # Row 4 is column headers

    for r in range(5, sh.nrows):
        c0 = str(sh.cell(r, 0).value).strip()
        c4 = str(sh.cell(r, 4).value).strip()
        c12 = sh.cell(r, 12).value  # 수량(M)

        # Batch group header: no order_id in c0, has SQ info in c4
        if not c0 and c4 and ("SQ" in c4 or "kcmil" in c4.lower()):
            if current_group:
                groups.append(current_group)
            current_group = {
                "header": c4,
                "sq": _extract_sq(c4),
                "orders": [],
                "subtotal_m": 0,
            }
            continue

        # Data row: has order number
        if c0 and (c0.startswith("S1") or c0.startswith("Y1") or c0.startswith("P1")):
            if current_group is None:
                continue
            order = {
                "order_id": c0,
                "product_group": str(sh.cell(r, 4).value).strip(),
                "spec": str(sh.cell(r, 5).value).strip(),
                "color": str(sh.cell(r, 6).value).strip(),
                "qty_m": float(c12) if c12 else 0,
                "drum_length": float(sh.cell(r, 9).value) if sh.cell(r, 9).value else 0,
                "drum_count": float(sh.cell(r, 11).value)
                if sh.cell(r, 11).value
                else 0,
            }
            current_group["orders"].append(order)
            continue

        # Subtotal row (blank c0, number in c12, no SQ keyword)
        if not c0 and c12 and not c4:
            if current_group:
                current_group["subtotal_m"] = float(c12)

    if current_group:
        groups.append(current_group)

    return groups


def parse_tobe_insulation(wb, sheet_name="B100EXT(1차)"):
    """절연 시트에서 배치 그룹별 수주 목록 추출"""
    sh = wb.sheet_by_name(sheet_name)
    groups = []
    current_group = None
    header_row = None

    # Find header row
    for r in range(min(sh.nrows, 10)):
        if str(sh.cell(r, 0).value).strip() == "수주번호":
            header_row = r
            break

    if header_row is None:
        return groups

    for r in range(header_row + 1, sh.nrows):
        c0 = str(sh.cell(r, 0).value).strip()
        c4 = str(sh.cell(r, 4).value).strip()
        c5 = str(sh.cell(r, 5).value).strip()
        c12 = sh.cell(r, 12).value

        is_data = c0 and (
            c0.startswith("S1") or c0.startswith("Y1") or c0.startswith("P1")
        )
        is_blank = not c0 and not c4 and not c5

        if is_data:
            sq = _extract_sq_from_spec(c5)
            group_key = f"{sq}_{c4}" if c4 else sq

            if current_group is None or current_group["key"] != group_key:
                if current_group:
                    groups.append(current_group)
                current_group = {
                    "key": group_key,
                    "sq": sq,
                    "orders": [],
                    "subtotal_m": 0,
                }

            current_group["orders"].append(
                {
                    "order_id": c0,
                    "product_group": c4,
                    "spec": c5,
                    "color": str(sh.cell(r, 6).value).strip(),
                    "qty_m": float(c12) if c12 else 0,
                }
            )

        elif is_blank and current_group:
            # Check if next row starts a new group
            if r + 1 < sh.nrows:
                next_c0 = str(sh.cell(r + 1, 0).value).strip()
                if next_c0 and (next_c0.startswith("S1") or next_c0.startswith("Y1")):
                    groups.append(current_group)
                    current_group = None

        elif not c0 and c12 and current_group:
            current_group["subtotal_m"] = float(c12)

    if current_group:
        groups.append(current_group)

    return groups


def parse_tobe_sheath(wb, sheet_name):
    """시스 시트에서 배치 그룹별 수주 목록 추출"""
    sh = wb.sheet_by_name(sheet_name)
    groups = []
    current_group = None
    header_row = None

    for r in range(min(sh.nrows, 10)):
        if str(sh.cell(r, 0).value).strip() == "수주번호":
            header_row = r
            break

    if header_row is None:
        return groups

    for r in range(header_row + 1, sh.nrows):
        c0 = str(sh.cell(r, 0).value).strip()
        c5 = str(sh.cell(r, 5).value).strip()
        c6 = str(sh.cell(r, 6).value).strip()  # color
        c12 = sh.cell(r, 12).value

        is_data = c0 and (
            c0.startswith("S1") or c0.startswith("Y1") or c0.startswith("P1")
        )
        is_blank = not c0 and not c5

        if is_data:
            sq = _extract_sq_from_spec(c5)
            color = c6 or "기타"
            group_key = f"{sq}_{color}"

            if current_group is None or current_group["key"] != group_key:
                if current_group:
                    groups.append(current_group)
                current_group = {
                    "key": group_key,
                    "sq": sq,
                    "color": color,
                    "orders": [],
                    "subtotal_m": 0,
                }

            current_group["orders"].append(
                {
                    "order_id": c0,
                    "spec": c5,
                    "color": color,
                    "qty_m": float(c12) if c12 else 0,
                }
            )

        elif is_blank and current_group and current_group["orders"]:
            groups.append(current_group)
            current_group = None

        elif not c0 and c12 and current_group:
            current_group["subtotal_m"] = float(c12)

    if current_group and current_group.get("orders"):
        groups.append(current_group)

    return groups


def get_system_batches(run_label):
    """시스템 API에서 배치 결과 조회"""
    resp = requests.get(f"{API_BASE}/api/pipeline/stage1/{run_label}/batches")
    resp.raise_for_status()
    return resp.json()


def compare_stranding(tobe_groups, system_batches):
    """연선 배치 비교"""
    # Filter system batches for stranding (ST- and CORE- groups)
    sys_stranding = [b for b in system_batches if b.get("process_name") == "연선"]

    # Group system batches by SQ (merge 압축+압축연선, exclude CORE)
    sys_groups = defaultdict(list)
    for b in sys_stranding:
        bg = b.get("batch_group", "?")
        if bg.startswith("CORE"):
            continue  # CORE batches are internal, not in to-be
        sq = b.get("sq_mm2", 0)
        voltage = b.get("voltage", "")
        key = f"ST-{int(sq)}-{voltage}"
        sys_groups[key].append(b)

    print("\n" + "=" * 80)
    print("연선 COMPARISON: to-be vs system")
    print("=" * 80)

    print(f"\nTo-be groups: {len(tobe_groups)}")
    print(f"System batch_groups: {len(sys_groups)}")

    # Compare group by group
    mismatches = []
    for i, tg in enumerate(tobe_groups):
        sq = tg["sq"]
        header = tg["header"]
        tobe_order_ids = sorted(set(o["order_id"] for o in tg["orders"]))
        tobe_qty = sum(o["qty_m"] for o in tg["orders"])

        # Find matching system group by SQ (now keyed as ST-{SQ}-{voltage})
        matched_sys = None
        for bg, batches in sys_groups.items():
            sys_sq = _extract_sq_from_batch_group(bg)
            if sys_sq == sq:
                matched_sys = (bg, batches)
                break

        if not matched_sys:
            # Special case: 1250kcmil = 633SQ
            if sq == 1250:
                for bg, batches in sys_groups.items():
                    if "633" in bg:
                        matched_sys = (bg, batches)
                        break
            else:
                for bg, batches in sys_groups.items():
                    if f"-{int(sq)}-" in bg:
                        matched_sys = (bg, batches)
                        break

        print(f"\n--- Group {i + 1}: {header[:50]} ---")
        print(
            f"  To-be: {len(tg['orders'])} orders, {tobe_qty:.0f}m, IDs: {tobe_order_ids[:5]}..."
        )

        if matched_sys:
            bg, batches = matched_sys
            sys_order_ids = sorted(set(b.get("sales_order_id", "") for b in batches))
            sys_qty = sum(float(b.get("total_length_m", 0) or 0) for b in batches)
            print(
                f"  System [{bg}]: {len(batches)} batches, {sys_qty:.0f}m, IDs: {sys_order_ids[:5]}..."
            )

            # Compare order sets
            tobe_set = set(tobe_order_ids)
            sys_set = set(sys_order_ids)
            missing = tobe_set - sys_set
            extra = sys_set - tobe_set

            if missing:
                print(f"  MISMATCH: Missing in system: {missing}")
                mismatches.append(
                    {"group": header, "type": "missing_orders", "orders": list(missing)}
                )
            if extra:
                print(f"  MISMATCH: Extra in system: {extra}")
                mismatches.append(
                    {"group": header, "type": "extra_orders", "orders": list(extra)}
                )

            qty_diff = abs(tobe_qty - sys_qty)
            if qty_diff > tobe_qty * 0.01:  # >1% difference
                print(
                    f"  MISMATCH: Qty diff {qty_diff:.0f}m ({tobe_qty:.0f} vs {sys_qty:.0f})"
                )
                mismatches.append(
                    {
                        "group": header,
                        "type": "qty_diff",
                        "tobe": tobe_qty,
                        "system": sys_qty,
                    }
                )

            if not missing and not extra and qty_diff <= tobe_qty * 0.01:
                print("  MATCH ✓")
        else:
            print(f"  MISMATCH: No matching system group found for SQ={sq}")
            mismatches.append({"group": header, "type": "no_match", "sq": sq})

    return mismatches


def _extract_sq(header):
    """Extract SQ number from header like '120SQ --- 3틀 생산'"""
    import re

    m = re.search(r"(\d+)\s*SQ", header, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*kcmil", header, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return 0


def _extract_sq_from_spec(spec):
    """Extract SQ from spec like '4C x 120SQ' or '1C x 1250KCMIL(633SQ)'"""
    import re

    m = re.search(r"(\d+)\s*SQ", spec, re.IGNORECASE)
    if m:
        return m.group(1) + "SQ"
    m = re.search(r"(\d+)\s*KCMIL", spec, re.IGNORECASE)
    if m:
        return m.group(1) + "KCMIL"
    return spec


def _extract_sq_from_batch_group(bg):
    """Extract SQ from batch_group like 'ST-120-저압' or 'CORE-300-저압'"""
    parts = bg.split("-")
    for p in parts:
        try:
            return float(p)
        except ValueError:
            continue
    return 0


def main():
    run_label = sys.argv[1] if len(sys.argv) > 1 else None
    if not run_label:
        # Get latest run
        resp = requests.get(f"{API_BASE}/api/pipeline/runs")
        runs = resp.json()
        if not runs:
            print("ERROR: No runs found")
            return
        run_label = runs[0]["run_label"]
        print(f"Using latest run: {run_label}")

    # Load to-be
    wb = xlrd.open_workbook(TOBE_FILE)

    # Get system batches
    sys_batches = get_system_batches(run_label)
    print(f"System total batches: {len(sys_batches)}")

    # 1. Compare stranding
    tobe_stranding = parse_tobe_stranding(wb)
    stranding_mismatches = compare_stranding(tobe_stranding, sys_batches)

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total_groups = len(tobe_stranding)
    matched = total_groups - len(
        [m for m in stranding_mismatches if m["type"] == "no_match"]
    )
    print(f"연선: {matched}/{total_groups} groups matched")
    print(f"Total mismatches: {len(stranding_mismatches)}")

    if stranding_mismatches:
        print("\nMismatch details:")
        for m in stranding_mismatches:
            print(f"  - {m['group'][:40]}: {m['type']}")


if __name__ == "__main__":
    main()
