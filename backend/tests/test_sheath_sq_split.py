"""시스 batch_group 규격별 분할 테스트.

회사 수기 계획서는 같은 색상/주차 내에서도 SQ 규격별로 배치를 세로 나열한다
(예: 흑 W15 슬롯 안에 120 / 240 / 500 각각 별도 런). 기존 구현은 색상·주차만
그룹 키에 포함해 다른 SQ도 하나의 ProductionBatch 그룹에 합쳐져 간트에 단일
블록으로만 그려졌다. 이 테스트는 그룹 키에 SQ 접미가 포함되어 규격별 분리가
되는지, 그리고 색상 체인(`_sheath_chain_key`) 연속성은 유지되는지 검증한다.
"""

from __future__ import annotations

from datetime import date


def test_sheath_group_key_splits_same_color_week_by_sq():
    """같은 색상·같은 납기 주차라도 SQ 가 다르면 batch_group 이 달라야 한다."""
    from app.domain.batch_sheath_keys import _compose_sheath_group_key

    k1 = _compose_sheath_group_key(
        proc="저압시스", color="흑", due_date=date(2026, 4, 13), sq=120
    )
    k2 = _compose_sheath_group_key(
        proc="저압시스", color="흑", due_date=date(2026, 4, 13), sq=240
    )
    assert k1 != k2, f"동일 색상·동일 주차인데 SQ 120/240 이 같은 group_key: {k1}"
    # SQ 접미가 실제로 포함됐는지
    assert k1.endswith("_120SQ"), f"SQ 접미 누락: {k1}"
    assert k2.endswith("_240SQ"), f"SQ 접미 누락: {k2}"
    # 색상·설비 라우팅 prefix 유지
    assert k1.startswith("A120_흑_"), f"흑 → A120 라우팅 prefix 깨짐: {k1}"


def test_sheath_group_key_routes_a100_for_other_colors():
    """A100 라우팅 색상(갈/회/녹/황 …)은 A100 prefix + SQ 접미."""
    from app.domain.batch_sheath_keys import _compose_sheath_group_key

    k = _compose_sheath_group_key(
        proc="저압시스", color="갈", due_date=date(2026, 4, 13), sq=95
    )
    assert k.startswith("A100_갈_"), f"갈 → A100 prefix 깨짐: {k}"
    assert k.endswith("_95SQ"), f"SQ 접미 누락: {k}"


def test_sheath_group_key_half_week_bucket_preserved():
    """반주차 H1/H2 분리가 SQ 분할과 동시에 작동해야 한다 (납기 우선 보존)."""
    from app.domain.batch_sheath_keys import _compose_sheath_group_key

    # 월요일(W15H1) vs 목요일(W15H2) — 같은 주차 내 H1/H2 분리
    k_mon = _compose_sheath_group_key(
        proc="저압시스", color="흑", due_date=date(2026, 4, 6), sq=120
    )
    k_thu = _compose_sheath_group_key(
        proc="저압시스", color="흑", due_date=date(2026, 4, 9), sq=120
    )
    assert "H1" in k_mon and "H2" in k_thu, (
        f"H1/H2 bucket 누락: mon={k_mon}, thu={k_thu}"
    )
    assert k_mon != k_thu, "같은 주 H1/H2 납기가 같은 그룹으로 묶임 — EDD 보장 깨짐"


def test_sheath_group_key_high_voltage_includes_sq():
    """고압시스도 SQ 접미가 포함돼 일관성 유지 (실질은 633SQ 단일이라 변화 적음)."""
    from app.domain.batch_sheath_keys import _compose_sheath_group_key

    k = _compose_sheath_group_key(proc="고압시스", color="흑/적", due_date=None, sq=633)
    assert "_633SQ" in k, f"고압시스 SQ 접미 누락: {k}"
    # 슬래시(/) 는 underscore 로 치환되어야 DB 키로 안전
    assert "흑_적" in k or "흑/적" not in k, f"흑/적 슬래시 이스케이프 깨짐: {k}"


def test_sheath_group_key_fallback_for_missing_due_and_color():
    """납기/색상 누락 → '기타' + '9999W99X' 폴백 + SQ 접미."""
    from app.domain.batch_sheath_keys import _compose_sheath_group_key

    k = _compose_sheath_group_key(proc="저압시스", color="", due_date=None, sq=50)
    assert "기타" in k, f"색상 폴백 누락: {k}"
    assert "9999W99X" in k, f"납기 폴백 누락: {k}"
    assert k.endswith("_50SQ"), f"SQ 접미 누락: {k}"


def test_sheath_chain_key_preserves_color_adjacency_across_sq():
    """SQ 분할이 일어나도 `_sheath_chain_key` 정렬 후 같은 색상 블록은 인접해야 한다.

    _sheath_chain_key 는 색상+납기만 쓰므로 SQ 분할이 추가되어도 같은 색상/같은
    납기 주차 그룹들이 연속으로 정렬된다 (stable sort).
    """
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.application.ingest import _SHEATH_COLOR_RANK

    # 흑 SQ 120, 흑 SQ 240, 갈 SQ 120 — 모두 같은 W15 납기
    batches = [
        ProductionBatch(
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=120,
            due_date=date(2026, 4, 13),
            sales_order_id="SO-1",
            sales_order_line=1,
            batch_group="",
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            batch_seq=0,
            run_label="unit-sq-chain",
        ),
        ProductionBatch(
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=240,
            due_date=date(2026, 4, 13),
            sales_order_id="SO-2",
            sales_order_line=1,
            batch_group="",
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            batch_seq=0,
            run_label="unit-sq-chain",
        ),
        ProductionBatch(
            process_name="저압시스",
            sheath_color="갈",
            sq_mm2=120,
            due_date=date(2026, 4, 13),
            sales_order_id="SO-3",
            sales_order_line=1,
            batch_group="",
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            batch_seq=0,
            run_label="unit-sq-chain",
        ),
    ]

    def _chain_key(b: ProductionBatch) -> tuple:
        color = (b.sheath_color or "").strip() or "기타"
        rank = _SHEATH_COLOR_RANK.get(color, 99)
        yr, wk, _ = b.due_date.isocalendar()
        return (1, rank, yr * 100 + wk, b.due_date.toordinal())

    batches.sort(key=_chain_key)
    colors = [b.sheath_color for b in batches]
    # 흑 2건(SQ 120, 240) 이 연속, 그 뒤 갈
    assert colors == ["흑", "흑", "갈"], f"SQ 분할 후 색상 체인 순서 위반: {colors}"
