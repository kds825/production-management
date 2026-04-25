"""batch_grouping 패키지 — 공정별 배치 생성/분할/묶음 정렬 모듈 묶음.

D7-C 호환 표면:
- 외부 (presentation/services/tests) 는 모두 `app.services.batch_grouping.<name>`
  으로 import 한다. 본 `__init__` 가 하위 모듈 + 도메인 모듈에서 모든 D7-C
  심볼을 끌어와 re-export 함으로써 import 경로 호환을 유지한다.
- ruff/F401 unused-import 경고는 의도된 re-export 이므로 `# noqa: F401` 처리.

분할 결과 (Week 3 Task 3A.3):
- helpers.py   — extract_sq, format_spec_display 외 라우팅/선속/재질 보조
- grouper.py   — create_batches, deduplicate_group_headers (Stage 1 본체)
- splitting.py — detect_split_candidates, execute_auto_splits (자동 분할)

Phase 1 step 1 (refactoring v2):
- constants.py + sheath.py 의 도메인 룰 (`_WIP_COVERED_PROCESSES`,
  `_SHEATH_COLOR_RANK`, `_TFR8_PATTERN`, `_A120_COLORS`,
  `_compose_sheath_group_key`) 은 `app.domain.batch_sheath_keys` 로 이동.
  본 `__init__` 가 호환을 위해 재export 한다 (Phase 5 까지).
"""

from app.domain.batch_sheath_keys import (  # noqa: F401
    _A120_COLORS,
    _SHEATH_COLOR_RANK,
    _TFR8_PATTERN,
    _WIP_COVERED_PROCESSES,
    _compose_sheath_group_key,
)
from app.services.batch_grouping.grouper import (  # noqa: F401
    create_batches,
    deduplicate_group_headers,
)
from app.services.batch_grouping.helpers import (  # noqa: F401
    _find_item,
    _find_speed,
    _get_processes,
    _infer_material,
    _infer_routing,
    _process_order,
    extract_sq,
    format_spec_display,
)
from app.services.batch_grouping.splitting import (  # noqa: F401
    _apply_auto_split,
    detect_split_candidates,
    execute_auto_splits,
)
