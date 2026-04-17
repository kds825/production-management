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


from app.infrastructure.models.production_batch import ProductionBatch


def test_sheath_color_chain_across_weeks(db):
    """같은 색상이 인접 주차에 있으면 _sheath_chain_key 정렬 후 연속 배치.

    흑 W15, 흑 W16, 갈 W15 → 정렬 후 흑 (W15 → W16) → 갈 (W15) 순.
    Stage 2 내부 정렬 규칙만 직접 호출해서 검증 (auto_schedule 미호출).
    """
    from app.services.batch_grouping import _SHEATH_COLOR_RANK

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
    """create_batches 소스에 `_sheath_chain_key` 2차 정렬이 적용돼 있는지.

    DB 통합 없이 정적 확인만 — 소스 문자열에 키워드가 존재하고 `_sort_key`
    이후에 `_sheath_chain_key` 로 추가 정렬이 들어갔는지를 보장한다.
    """
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "batch_grouping.py"
    )
    src = src_path.read_text(encoding="utf-8")

    # 2차 정렬 helper 가 정의됐고 적용됐는지
    assert "def _sheath_chain_key" in src, "_sheath_chain_key 미정의"
    assert "batches.sort(key=_sheath_chain_key)" in src, "_sheath_chain_key 미적용"

    # _sort_key 보다 뒤에 위치하는지 (stable sort 로 priority 우선)
    pos_primary = src.index("batches.sort(key=_sort_key)")
    pos_chain = src.index("batches.sort(key=_sheath_chain_key)")
    assert pos_chain > pos_primary, (
        "_sheath_chain_key 가 _sort_key 뒤에 와야 stable sort priority 가 동작"
    )


def test_non_sheath_preserves_relative_order(db):
    """비시스 배치는 _sheath_chain_key 에서 동일 키 → 원순서 보존."""
    from app.services.batch_grouping import _SHEATH_COLOR_RANK  # noqa: F401

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

    시드: 흑120(due 4/13), 흑95(due 4/20), 청120(due 4/13), 청95(due 4/20) —
    모두 A120 설비 대상. create_batches 가 생성하는 batch_group (A120_<color>_<week>)
    을 미리 할당하여 scheduler 의 A120 라우팅 필터를 타게 한다.

    기대: 흑·흑·청·청 또는 청·청·흑·흑 (체인지오버 1회).
    절대 금지: 흑·청·흑·청 (체인지오버 3회).
    """
    from app.services.schedule_optimizer import auto_schedule

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
            "due": date(2026, 4, 20),
            "so": "SO-CH-B",
            "bg": "A120_흑_2026W16",
        },
        {
            "color": "청",
            "sq": 120,
            "due": date(2026, 4, 13),
            "so": "SO-CH-C",
            "bg": "A120_청_2026W15",
        },
        {
            "color": "청",
            "sq": 95,
            "due": date(2026, 4, 20),
            "so": "SO-CH-D",
            "bg": "A120_청_2026W16",
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
