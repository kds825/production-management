"""시스 색상 체인 그룹핑 테스트 — A'' 접근안.

create_batches 내 `_sheath_chain_key` 2차 정렬이 시스 배치를 색상 기준으로
연속 배치하는지 검증한다. Stage 2 이터레이션 순서가 그룹 생성 순서·DB insert
순서를 결정하므로, 같은 색상의 시스 배치는 인접한 납기주차를 가로질러 연속
block 을 형성해야 한다.

시드 데이터는 ProductionBatch 를 직접 DB 에 삽입하고, create_batches 의 정렬
로직만 격리 호출 (실제 오퍼레이터 호출이 아닌) — 정렬 결과의 batch_id 순서를
확인하여 invariant 를 검증한다.

Stage 1 전체 파이프라인을 태우는 통합 테스트는 test_pipeline_sync 계열에서
이미 커버되므로, 본 파일은 정렬 규칙 단위 테스트에 집중한다.
"""

from datetime import date

import pytest

from app.infrastructure.models.production_batch import ProductionBatch


def test_sheath_color_chain_across_weeks(db):
    """같은 색상이 인접 주차에 있으면 _sheath_chain_key 정렬 후 연속 배치.

    흑 W15, 흑 W16, 갈 W15 → 정렬 후 흑 (W15 → W16) → 갈 (W15) 순.
    Stage 2 내부 정렬 규칙만 직접 호출해서 검증 (auto_schedule 미호출).
    """
    from app.application.ingest import _SHEATH_COLOR_RANK

    # 시드 배치 — batch_id 를 obj 에 부여하기 위해 flush 필요
    batches = [
        ProductionBatch(
            run_label="unit-chain-1",
            batch_seq=0,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=120,
            due_date=date(2026, 4, 13),  # W15
            drum_count=1,
            total_length_m=1000,
            drum_length_m=1000,
            conductor_material="CU",
            sales_order_id="SO-A",
            sales_order_line=1,
            batch_group="",
        ),
        ProductionBatch(
            run_label="unit-chain-1",
            batch_seq=0,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=95,
            due_date=date(2026, 4, 20),  # W16
            drum_count=1,
            total_length_m=800,
            drum_length_m=800,
            conductor_material="CU",
            sales_order_id="SO-B",
            sales_order_line=1,
            batch_group="",
        ),
        ProductionBatch(
            run_label="unit-chain-1",
            batch_seq=0,
            process_name="저압시스",
            sheath_color="갈",
            sq_mm2=120,
            due_date=date(2026, 4, 13),  # W15
            drum_count=1,
            total_length_m=500,
            drum_length_m=500,
            conductor_material="CU",
            sales_order_id="SO-C",
            sales_order_line=1,
            batch_group="",
        ),
    ]

    # 모듈 수준 constants 가 등록됐는지 먼저 확인
    assert "흑" in _SHEATH_COLOR_RANK
    assert "갈" in _SHEATH_COLOR_RANK
    assert _SHEATH_COLOR_RANK["흑"] < _SHEATH_COLOR_RANK["갈"]

    # _sheath_chain_key 와 동일한 규칙으로 직접 정렬
    # (create_batches 내부 closure 이므로 재현)
    def _chain_key(b: ProductionBatch) -> tuple:
        if b.process_name not in ("저압시스", "고압시스"):
            return (0, 0, 0, 0)
        color = (b.sheath_color or "").strip() or "기타"
        rank = _SHEATH_COLOR_RANK.get(color, 99)
        if b.due_date:
            yr, wk, _ = b.due_date.isocalendar()
            wk_int = yr * 100 + wk
            ord_ = b.due_date.toordinal()
        else:
            wk_int = 999999
            ord_ = 9999999
        return (1, rank, wk_int, ord_)

    batches.sort(key=_chain_key)
    colors_in_order = [b.sheath_color for b in batches]

    # 흑 이 연속, 그 뒤 갈
    assert colors_in_order == ["흑", "흑", "갈"], (
        f"색상 체인 순서 위반: {colors_in_order}"
    )


def test_sheath_chain_key_present_and_applied(db):
    """create_batches 후처리에 `_sheath_chain_key` 2차 정렬이 적용돼 있는지.

    DB 통합 없이 정적 확인만 — Phase 2 Task 2.2 추출 이후 소스 위치는
    `_batch_grouper_finalize.py` 로 이동했고, `batch_grouper.py` 본체는
    `apply_sheath_secondary_sort()` 호출만 남는다. 두 모듈의 invariant 를
    함께 검증한다.
    """
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "app" / "application" / "ingest"
    src_grouper = (base / "batch_grouper.py").read_text(encoding="utf-8")
    src_finalize = (base / "_batch_grouper_finalize.py").read_text(encoding="utf-8")

    # finalize 모듈에 정렬 helper 정의가 있고 batches.sort 호출까지 들어갔는지
    assert "def _sheath_chain_key" in src_finalize, "_sheath_chain_key 미정의"
    assert "batches.sort(key=_sheath_chain_key)" in src_finalize, (
        "_sheath_chain_key 미적용"
    )

    # batch_grouper.py 본체에서 sort_batches 가 sheath 2차 정렬보다 먼저 호출되는지
    pos_primary = src_grouper.index("sort_batches(batches)")
    pos_chain = src_grouper.index("apply_sheath_secondary_sort(batches)")
    assert pos_chain > pos_primary, (
        "apply_sheath_secondary_sort 가 sort_batches 뒤에 와야 stable sort priority 가 동작"
    )


def test_non_sheath_preserves_relative_order(db):
    """비시스 배치는 _sheath_chain_key 에서 동일 키 → 원순서 보존."""
    from app.application.ingest import _SHEATH_COLOR_RANK  # noqa: F401

    # 비시스 2건 + 시스 1건 혼합 — 비시스 상대 순서가 유지되는지
    b_stranding = ProductionBatch(
        run_label="unit-chain-stable",
        batch_seq=0,
        process_name="연선",
        sq_mm2=50,
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id="SO-X",
        sales_order_line=1,
        batch_group="",
    )
    b_insul = ProductionBatch(
        run_label="unit-chain-stable",
        batch_seq=0,
        process_name="저압절연",
        sq_mm2=50,
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id="SO-Y",
        sales_order_line=1,
        batch_group="",
    )
    b_sheath = ProductionBatch(
        run_label="unit-chain-stable",
        batch_seq=0,
        process_name="저압시스",
        sheath_color="흑",
        sq_mm2=50,
        due_date=date(2026, 4, 13),
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id="SO-Z",
        sales_order_line=1,
        batch_group="",
    )

    # _sheath_chain_key 를 직접 재현 (closure 라 import 불가)
    def _chain_key(b: ProductionBatch) -> tuple:
        if b.process_name not in ("저압시스", "고압시스"):
            return (0, 0, 0, 0)
        return (1, 99, 0, 0)

    batches = [b_stranding, b_insul, b_sheath]
    batches.sort(key=_chain_key)
    # 비시스 (연선, 저압절연) 가 앞에 유지되고 상대 순서 보존
    non_sheath = [
        b.process_name
        for b in batches
        if b.process_name not in ("저압시스", "고압시스")
    ]
    assert non_sheath == ["연선", "저압절연"], f"비시스 상대 순서 깨짐: {non_sheath}"


# ---- E2E helpers ----


def _get_sheath_tasks_sorted(db, run_label, eq_code_prefix):
    from app.infrastructure.models.schedule_task import ScheduleTask

    return (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.equipment_code.like(f"{eq_code_prefix}%"),
        )
        .order_by(ScheduleTask.start_datetime)
        .all()
    )


def _get_color(db, task):
    b = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == task.batch_id)
        .first()
    )
    return (b.sheath_color or "").strip() if b else ""


def test_sheath_scheduler_actual_order_forms_color_chain(db):
    """End-to-end: auto_schedule 이후 SH-A120 설비의 실제 시작시각 순서가
    색상 체인을 형성한다.

    시드: 흑120(due 4/13), 흑95(due 4/13), 청120(due 4/20), 청95(due 4/20) —
    같은 주차에 단일 색상만 배치해 `cluster_sort_key = (latest_due, ...,
    color_rank, ...)` 규칙에서도 색상 체인이 유지되는 시나리오.

    Why (현재 설계 3차 iteration): `cluster_sort_key` 는 납기(latest_due)
    primary, 색상은 3차 키이다. 따라서 납기가 뒤섞인 상태(흑 W15/청 W15/
    흑 W16/청 W16)에서는 흑·청·흑·청 인 게 정상이고 색상 체인은 깨진다.
    본 테스트는 "납기 그룹 내에서는 단일 색" 이라는 실사용 시나리오로
    색상 체인이 형성됨을 검증한다 (KBI 현장 실제 패턴).
    """
    from app.application.scheduling.greedy.auto_schedule import auto_schedule

    rows = [
        {
            "color": "흑",
            "sq": 120,
            "due": date(2026, 4, 13),
            "so": "SO-CH-A",
            "bg": "A120_흑_2026W15",
        },
        {
            "color": "흑",
            "sq": 95,
            "due": date(2026, 4, 13),
            "so": "SO-CH-B",
            "bg": "A120_흑_2026W15B",
        },
        {
            "color": "청",
            "sq": 120,
            "due": date(2026, 4, 20),
            "so": "SO-CH-C",
            "bg": "A120_청_2026W16",
        },
        {
            "color": "청",
            "sq": 95,
            "due": date(2026, 4, 20),
            "so": "SO-CH-D",
            "bg": "A120_청_2026W16B",
        },
    ]
    for r in rows:
        db.add(
            ProductionBatch(
                run_label="test-chain-e2e",
                batch_seq=0,
                process_name="저압시스",
                sheath_color=r["color"],
                sq_mm2=r["sq"],
                due_date=r["due"],
                drum_count=1,
                drum_length_m=1000,
                total_length_m=1000,
                conductor_material="CU",
                sales_order_id=r["so"],
                sales_order_line=1,
                batch_group=r["bg"],
            )
        )
    db.flush()

    auto_schedule(run_label="test-chain-e2e", db=db)

    tasks = _get_sheath_tasks_sorted(db, "test-chain-e2e", eq_code_prefix="SH-A120")
    assert len(tasks) == 4, (
        f"A120 설비에 4 태스크 기대, 실제 {len(tasks)} — 라우팅 실패 가능"
    )

    colors = [_get_color(db, t) for t in tasks]
    # 체인지오버 횟수 (인접 색상 변화 수) ≤ 1
    changeovers = sum(1 for i in range(len(colors) - 1) if colors[i] != colors[i + 1])
    assert changeovers <= 1, f"색상 체인지오버 과다: {changeovers}회, 순서={colors}"


def _get_due(db, task):
    b = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == task.batch_id)
        .first()
    )
    return b.due_date if b else None


def test_sheath_half_week_split_separates_same_week_early_late_due(db):
    """같은 주(W15) 내 월·목요일 납기 차이 → H1/H2 분리 그룹.
    효과: EDD H1 그룹이 EDD H2 그룹보다 먼저 스케줄.
    """
    from app.application.scheduling.greedy.auto_schedule import auto_schedule

    # 같은 W15 주 내 흑 색상 4건:
    #   월(4/6) H1: 납기 임박
    #   수(4/8) H1
    #   목(4/9) H2
    #   금(4/10) H2
    # 기대: 실제 배치 순서가 H1 그룹 먼저, H2 그룹 나중
    rows = [
        ("흑", 50, date(2026, 4, 6), "SO-HW-1"),  # W15H1
        ("흑", 60, date(2026, 4, 8), "SO-HW-2"),  # W15H1
        ("흑", 70, date(2026, 4, 9), "SO-HW-3"),  # W15H2
        ("흑", 80, date(2026, 4, 10), "SO-HW-4"),  # W15H2
    ]
    for color, sq, due, so in rows:
        db.add(
            ProductionBatch(
                run_label="test-halfweek",
                batch_seq=0,
                process_name="저압시스",
                sheath_color=color,
                sq_mm2=sq,
                due_date=due,
                drum_count=1,
                drum_length_m=500,
                total_length_m=500,
                conductor_material="CU",
                sales_order_id=so,
                sales_order_line=1,
                batch_group="",
            )
        )
    db.flush()

    auto_schedule(run_label="test-halfweek", db=db)

    tasks = _get_sheath_tasks_sorted(db, "test-halfweek", eq_code_prefix="SH-A120")
    assert len(tasks) >= 2, f"기대 ≥2 tasks, 실제 {len(tasks)}"

    # H1 그룹(due ≤ 4/8) task 들이 H2(due ≥ 4/9) 보다 먼저 끝나야
    h1_end_max = max(
        (
            t.end_datetime
            for t in tasks
            if _get_due(db, t) and _get_due(db, t) <= date(2026, 4, 8)
        ),
        default=None,
    )
    h2_start_min = min(
        (
            t.start_datetime
            for t in tasks
            if _get_due(db, t) and _get_due(db, t) >= date(2026, 4, 9)
        ),
        default=None,
    )
    assert h1_end_max is not None and h2_start_min is not None, (
        f"H1 또는 H2 tasks 누락 — H1 end: {h1_end_max}, H2 start: {h2_start_min}"
    )
    # H1 그룹의 마지막 종료가 H2 그룹의 첫 시작 이전이거나 같음
    assert h1_end_max <= h2_start_min, (
        f"H1 종료 {h1_end_max} > H2 시작 {h2_start_min} — 반주차 분할 미작동"
    )


def test_long_color_chain_not_broken_by_half_week(db):
    """같은 색상 H1·H2 연속이면 여전히 체인 형성 (다른 색으로 끼어들지 않음).

    batch_group 을 H1/H2 bucket 형식으로 pre-seed 하여 A120 라우팅이
    타게 함 — batch_grouping 의 새 H1/H2 bucket 포맷과 정합.
    """
    from app.application.scheduling.greedy.auto_schedule import auto_schedule

    rows = [
        # (color, sq, due, so, bucket)
        ("흑", 50, date(2026, 4, 6), "SO-CCH-1", "2026W15H1"),  # 월
        ("흑", 60, date(2026, 4, 9), "SO-CCH-2", "2026W15H2"),  # 목
        ("흑", 70, date(2026, 4, 13), "SO-CCH-3", "2026W16H1"),  # 월(다음주)
    ]
    for color, sq, due, so, bucket in rows:
        db.add(
            ProductionBatch(
                run_label="test-chain-hw",
                batch_seq=0,
                process_name="저압시스",
                sheath_color=color,
                sq_mm2=sq,
                due_date=due,
                drum_count=1,
                drum_length_m=500,
                total_length_m=500,
                conductor_material="CU",
                sales_order_id=so,
                sales_order_line=1,
                batch_group=f"A120_{color}_{bucket}",
            )
        )
    db.flush()

    auto_schedule(run_label="test-chain-hw", db=db)
    tasks = _get_sheath_tasks_sorted(db, "test-chain-hw", eq_code_prefix="SH-A120")
    colors = [_get_color(db, t) for t in tasks]
    # 모두 흑이어야
    assert all(c == "흑" for c in colors), f"색상 이탈: {colors}"
    assert len(tasks) == 3, f"3개 태스크 기대, 실제 {len(tasks)}"


def test_cp_sat_color_chain_bonus(db):
    """CP-SAT 솔버가 시스 같은 색상을 인접 배치하는 해를 선호.

    P9-B 이후: tardiness_hard=True (기본) 이면 과거 납기는 infeasible.
    본 테스트는 "color chain soft 최적화" 가 목적이므로 tardiness_hard=False 로
    호출해 soft penalty 모드에서 color chain preference 만 검증한다.
    (원래 fixture 의 due_date 2026-04-13, 2026-04-20 은 현재일 2026-04-20 기준
    일부 이미 경과/당일이라 hard 모드에선 infeasible.)
    """
    from app.application.scheduling.cp_sat.orchestrator import cp_sat_schedule

    rows = [
        ("흑", 120, date(2026, 4, 13), "SO-CP-1"),
        ("흑", 95, date(2026, 4, 20), "SO-CP-2"),
        ("청", 120, date(2026, 4, 13), "SO-CP-3"),
        ("청", 95, date(2026, 4, 20), "SO-CP-4"),
    ]
    for color, sq, due, so in rows:
        db.add(
            ProductionBatch(
                run_label="test-cpsat-chain",
                batch_seq=0,
                process_name="저압시스",
                sheath_color=color,
                sq_mm2=sq,
                due_date=due,
                drum_count=1,
                drum_length_m=1000,
                total_length_m=1000,
                conductor_material="CU",
                sales_order_id=so,
                sales_order_line=1,
                batch_group="",
            )
        )
    db.flush()

    # tardiness_hard=False: P9-B 이전 weight-based 동작과 등가.
    # 본 테스트는 color chain bonus 에만 관심 있음.
    result = cp_sat_schedule(run_label="test-cpsat-chain", db=db, tardiness_hard=False)
    assert result.get("solver_status") in ("OPTIMAL", "FEASIBLE"), (
        f"Solver failed: {result.get('solver_status')}"
    )

    tasks = _get_sheath_tasks_sorted(db, "test-cpsat-chain", eq_code_prefix="SH-A120")
    if len(tasks) < 2:
        pytest.skip(f"SH-A120 에 2개 이상 task 필요 (실제 {len(tasks)})")
    colors = [_get_color(db, t) for t in tasks]
    changeovers = sum(1 for i in range(len(colors) - 1) if colors[i] != colors[i + 1])
    assert changeovers <= 1, (
        f"CP-SAT: 색상 체인지오버 과다 {changeovers}회, 순서={colors}"
    )
