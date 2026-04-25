"""batch_grouping 패키지 — 공정별 배치 생성/분할/묶음 정렬 모듈 묶음.

D7-C 호환 표면:
- 외부 (presentation/services/tests) 는 모두 `app.services.batch_grouping.<name>`
  으로 import 한다. 본 `__init__` 가 하위 모듈에서 모든 D7-C 심볼을 끌어와
  re-export 함으로써, 1971-line 단일 파일을 5개 모듈로 분할해도 import 경로가
  깨지지 않는다.
- ruff/F401 unused-import 경고는 의도된 re-export 이므로 `# noqa: F401` 처리.

분할 결과 (Week 3 Task 3A.3):
- constants.py — 룩업 상수, 정규식, 색상 랭크
- helpers.py   — extract_sq, format_spec_display 외 라우팅/선속/재질 보조
- sheath.py    — _compose_sheath_group_key (시스 그룹 키 합성)
- grouper.py   — create_batches, deduplicate_group_headers (Stage 1 본체)
- splitting.py — detect_split_candidates, execute_auto_splits (자동 분할)
"""

from app.services.batch_grouping.constants import (  # noqa: F401
    _A120_COLORS,
    _SHEATH_COLOR_RANK,
    _TFR8_PATTERN,
    _WIP_COVERED_PROCESSES,
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
from app.services.batch_grouping.sheath import _compose_sheath_group_key  # noqa: F401
from app.services.batch_grouping.splitting import (  # noqa: F401
    _apply_auto_split,
    detect_split_candidates,
    execute_auto_splits,
)
