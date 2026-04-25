"""batch_grouping 패키지 — 도메인 보조 함수 (규격 파싱·라우팅·선속·재질).

설계 의도:
- create_batches/splitting 양쪽에서 공통으로 호출하는 stateless 보조 함수만 모음.
- 모든 함수는 DB session 비의존 — pure dict/객체 변환만 수행.
- 본 모듈을 import 해도 SQLAlchemy 모델 외 무거운 의존이 끌려오지 않도록 유지.
"""

import re

from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.process_routing import ProcessRouting
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.speed_master import SpeedMaster


def extract_sq(spec_raw: str | None) -> float | None:
    """
    규격 텍스트에서 SQ(mm²) 값을 추출한다.

    지원 패턴:
      - "4C x 35SQ"        → 35.0
      - "300SQ"            → 300.0
      - "1250KCMIL(633SQ)" → 633.0  (괄호 내 SQ 우선)
      - "500KCMIL"         → 253.0  (KCMIL → mm² 변환표)
    """
    if not spec_raw:
        return None

    # SQ 단위가 명시된 경우 — 괄호 안이든 밖이든 가장 먼저 매칭
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # KCMIL 표기 → 근사 SQ 변환 (IEC 62890 기준 반올림값)
    kcmil_to_sq: dict[str, float] = {
        "4/0": 107.0,
        "500": 253.0,
        "750": 380.0,
        "1000": 507.0,
        "1250": 633.0,
        "1500": 759.0,
        "2000": 1013.0,
    }
    upper = spec_raw.upper()
    for kcmil, sq_val in kcmil_to_sq.items():
        # KCMIL 숫자가 단독으로 등장하는지 확인 (예: "500KCMIL" but not "1500KCMIL" when checking "500")
        pattern = rf"(?<!\d){re.escape(kcmil)}(?:\s*KCMIL)?"
        if re.search(pattern, upper):
            return sq_val

    return None


def format_spec_display(
    spec_raw: str | None,
    core_count: int,
    sq_mm2: float,
) -> str:
    """
    규격 표시 문자열을 반환한다.

    고압 제품(AWG / KCMIL 단위)은 원본 단위를 그대로 사용하고,
    일반 저압 제품은 "{core_count}C x {sq_mm2}SQ" 형태를 반환한다.

    지원 패턴 (spec_raw 기준):
      - "4C x 4/0AWG(107SQ)"   → "4C x 4/0AWG"
      - "1C x 500KCMIL(253SQ)" → "1C x 500KCMIL"
      - "4C x 35SQ"            → "4C x 35SQ"  (기본)
    """
    if spec_raw:
        # AWG 표기 — "4/0AWG" 또는 "2AWG" 등
        m_awg = re.search(r"(\d+/\d+|\d+)\s*AWG", spec_raw, re.IGNORECASE)
        if m_awg:
            return f"{core_count}C x {m_awg.group(1)}AWG"

        # KCMIL 표기 — "500KCMIL", "1250 KCMIL" 등
        m_kcmil = re.search(r"(\d+(?:\.\d+)?)\s*KCMIL", spec_raw, re.IGNORECASE)
        if m_kcmil:
            return f"{core_count}C x {m_kcmil.group(1)}KCMIL"

        # SQ 표기 — spec_raw의 SQ 값을 우선 사용 (sq_mm2 파라미터 무시)
        # CORE 배치처럼 sq_mm2가 내부 작업용으로 변경된 경우에도 원본 규격 표시
        m_sq = re.search(r"(\d+(?:\.\d+)?)\s*SQ", spec_raw, re.IGNORECASE)
        if m_sq:
            return f"{core_count}C x {int(float(m_sq.group(1)))}SQ"

    return f"{core_count}C x {int(sq_mm2)}SQ"


def _find_item(order: SalesOrder, items: dict[str, ItemMaster]) -> ItemMaster | None:
    """
    수주에 매칭되는 ItemMaster를 찾는다.

    1순위: order.item_code 직접 매칭 (ERP 수주에 품목 코드가 있는 경우)
    2순위: product_group + voltage 조합으로 퍼지 매칭 (품목 코드 없는 경우)

    퍼지 매칭은 첫 번째 일치 항목을 반환하므로 동일 그룹/전압에 여러 품목이 있으면
    오매칭 가능성이 있다. 근본 해결은 ERP 품목 코드 입력률 개선이다.
    """
    if order.item_code and order.item_code in items:
        return items[order.item_code]

    # 퍼지 매칭: product_group + voltage 동시 일치
    if order.product_group and order.voltage:
        for item in items.values():
            if (
                item.product_group == order.product_group
                and item.voltage == order.voltage
            ):
                return item

    return None


def _infer_routing(order: SalesOrder) -> str:
    """
    ItemMaster 매칭 실패 시 제품군·전압·심선수 조합으로 라우팅 코드를 추론한다.

    RT-001/002: 저압 CV (단심/다심)
    RT-003/004: HFCO (단심/다심)
    RT-005/006 : TFR-8 (단심/다심)
    RT-006/007 : TFR-8 고내화 (단심/다심)
    RT-HP1:     6/10kV 고압
    RT-HP2:     22.9kV/35kV URD
    RT-TFR:     TFR-GV 내화케이블
    """
    pg = (order.product_group or "").upper()
    voltage = order.voltage or ""
    core_count = int(order.core_count or 1)

    if "URD" in pg or "35" in voltage:
        return "RT-HP3"
    if "22.9" in voltage:
        return "RT-HP2"
    if "6/10" in voltage:
        return "RT-HP1"
    if "TFR-GV" in pg:
        return "RT-TFR"
    if "TFR-CV" in pg:
        return "RT-002" if core_count > 1 else "RT-001"
    if "TFR-8(" in pg:
        return "RT-008" if core_count > 1 else "RT-007"
    if "TFR-8" in pg:
        return "RT-006" if core_count > 1 else "RT-005"
    if "연동선" in pg or "나동선" in pg:
        return "RT-BARE"  # 연동선/나동선: 연선만 (절연/시스 불필요)
    # 기본값: 저압 CV
    return "RT-002" if core_count > 1 else "RT-001"


def _get_processes(routing: ProcessRouting) -> list[str]:
    """
    라우팅 레코드에서 None이 아닌 공정명 목록을 순서대로 반환한다.
    process_1 ~ process_6 컬럼을 순회하며 빈 문자열도 제외한다.
    """
    procs: list[str] = []
    for attr in (
        "process_1",
        "process_2",
        "process_3",
        "process_4",
        "process_5",
        "process_6",
    ):
        val = getattr(routing, attr, None)
        if val and val.strip():
            procs.append(val.strip())
    return procs


def _find_speed(
    lookup: dict[tuple, SpeedMaster],
    process_name: str,
    order: SalesOrder,
    sq: float,
) -> SpeedMaster | None:
    """
    (equipment_code, product_type, cross_section) 조합으로 선속 레코드를 조회한다.

    공정명 → 설비 코드 목록 매핑은 현장 설비 구성 기준이다.
    product_type 키는 SpeedMaster.product_type 컬럼값과 정확히 일치해야 한다.
    """
    # 공정명 → 설비 코드 후보 목록
    equipment_map: dict[str, list[str]] = {
        "저압절연": ["EX-B100"],
        "저압시스": ["SH-A100", "SH-A120"],
        "고압절연": ["EX-CV1", "EX-CV2"],
        "고압시스": ["SH-A150", "SH-B100"],
        "연선": ["ST-T6B0", "ST-AL6BO", "ST-54BO1", "ST-54BO2", "ST-54BO3", "ST-30BO"],
        "신선": ["WD-A100"],
        # CA-12BO (소단면 1.5~6), CA-4BO (35~95), CA-LU (Laying Up 35~150) 순회.
        # 이전 버그: "AS-A100" 은 equipment_master 에 없는 설비 — 37/37 batch 가
        # line_speed_mpm=None 으로 저장되던 회귀.
        "연합": ["CA-12BO", "CA-4BO", "CA-LU"],
        "T/P": ["TP-2"],
    }

    pg = order.product_group or ""
    core_count = int(order.core_count or 1)

    # product_type 결정 — SpeedMaster.product_type 컬럼 값과 일치시킴
    # 연선/신선 공정은 제품군 무관하게 설비 기준 선속 적용
    if process_name in ("연선", "신선", "T/P"):
        product_type: str | None = process_name
    elif "HFCO" in pg.upper():
        product_type = "HFCO"
    elif "TFR-GV" in pg.upper():
        product_type = "TFR-GV"
    elif process_name == "저압절연":
        product_type = "저압절연"
    elif process_name in ("저압시스", "고압시스"):
        # 10-5: core_count == 4 일 때 SpeedMaster 키를 "4C"로 직접 조회한다.
        # 4C 초과 심선수도 현장 기준 4C 테이블을 그대로 적용한다.
        if core_count >= 4:
            product_type = "4C"
        else:
            product_type = f"TFR-CV {core_count}C"
    else:
        product_type = None

    equip_codes = equipment_map.get(process_name, [])
    for eq in equip_codes:
        key = (eq, product_type, sq)
        if key in lookup:
            return lookup[key]

    return None


def _infer_material(order: SalesOrder) -> str:
    """
    도체 재질을 추론한다.
    - URD 제품군은 알루미늄 도체 사용
    - al_weight_kg > 0이면 알루미늄
    - 그 외 기본값 구리(CU)
    """
    pg = (order.product_group or "").upper()
    if "URD" in pg:
        return "AL"
    al_kg = order.al_weight_kg
    if al_kg is not None and float(al_kg) > 0:
        return "AL"
    return "CU"


def _process_order(process_name: str | None) -> int:
    """
    공정 정렬 우선순위 반환 (낮을수록 먼저).
    신선 → 연선 → 절연 → 연합/T·P → 시스 → 기타 → 외주
    """
    order_map: dict[str, int] = {
        "신선": 0,
        "연선": 1,
        "저압절연": 2,
        "고압절연": 2,
        "연합": 3,
        "T/P": 3,
        "저압시스": 4,
        "고압시스": 4,
        "HFCO시스": 4,
        "외주": 99,
    }
    return order_map.get(process_name or "", 50)
