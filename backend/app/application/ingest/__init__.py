"""ingest use-case 패키지 — ERP/WIP 입수 + Stage1/2 entry + batch grouping.

직전 services/batch_grouping/ + services/pipeline/ 두 패키지의 D7-C 호환 표면을
통합한 aggregate. 외부 (presentation/routes, application/scheduling) 가 다중
심볼을 한 import 로 가져갈 수 있도록 본 모듈에서 모든 public 심볼을 re-export
한다 (ruff F401 noqa 명시).

서브모듈 (Phase 1 step 5 신 위치):
    pipeline_orchestrator     — Stage1/Stage2 top-level coordinator + AI cache hooks
    stage1                    — Stage 1 (ERP→batch) orchestration
    stage2                    — Stage 2 (solver/greedy) orchestration
    run_labeler               — run_label generation + base_date / date-range parsing
    batch_grouper             — create_batches, deduplicate_group_headers (Stage 1 본체)
    batch_splitter            — detect_split_candidates, execute_auto_splits (자동 분할)
    batch_helpers             — extract_sq, format_spec_display 외 라우팅/선속/재질 보조
    wip_matching, wip_promotion — WIP 매칭/승격
"""

from app.application.ingest.batch_grouper import (  # noqa: F401
    create_batches,
    deduplicate_group_headers,
)
from app.application.ingest.batch_helpers import (  # noqa: F401
    _find_item,
    _find_speed,
    _get_processes,
    _infer_material,
    _infer_routing,
    _process_order,
    extract_sq,
    format_spec_display,
)
from app.application.ingest.batch_splitter import (  # noqa: F401
    _apply_auto_split,
    detect_split_candidates,
    execute_auto_splits,
)
from app.application.ingest.pipeline_orchestrator import (  # noqa: F401
    execute_stage1_ingest,
    execute_stage2,
)
from app.application.ingest.run_labeler import (  # noqa: F401
    new_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
from app.application.ingest.stage1 import run_solver_stage  # noqa: F401
from app.application.ingest.stage2 import run_greedy_stage  # noqa: F401

# 도메인 키 호환 (Phase 1 step 1 에서 분리된 sheath 키들). 직전 batch_grouping
# 셸이 같은 호환을 제공하던 표면을 본 ingest aggregate 가 흡수.
from app.domain.batch_sheath_keys import (  # noqa: F401
    _A120_COLORS,
    _SHEATH_COLOR_RANK,
    _TFR8_PATTERN,
    _WIP_COVERED_PROCESSES,
    _compose_sheath_group_key,
)
