"""schedules — 스케줄 라우트 sub-package.

Week 7 Task 7A.1 — 1,702 LOC 단일 파일 routes/schedules.py 를
{list, detail, bulk_update, cascade, revert} 5개 submodule + _shared 헬퍼로 분리.

D7-C 호환: 분리 이전에 `from app.presentation.routes.schedules import X` 로
가져오던 모든 이름은 이 `__init__.py` 의 re-export 로 계속 import 가능하다.
특히 `plan_cascade_preview` 는 `tests/test_cascade_preview_v2.py` /
`tests/test_observability_metrics.py` 가 `unittest.mock.patch(
"app.presentation.routes.schedules.plan_cascade_preview", ...)` 로 패치하므로
같은 경로에서 살아있어야 한다. cascade submodule 은 호출 시점에
`schedules_pkg.plan_cascade_preview` 를 참조해 patch 가 정상 적용되도록 한다.
"""

import importlib as _importlib
import sys as _sys

from fastapi import APIRouter

# 분리 이전 schedules.py 가 직접 import 했던 외부 심볼을 동일 경로로 re-export.
# 외부 코드 / 테스트가 `from app.presentation.routes.schedules import plan_cascade_preview`
# 같은 식으로 의존하고 있을 수 있어 그대로 노출 (ruff 가 순수 re-export 를 지우지
# 못하도록 noqa: F401).
from app.core.feature_flags import is_cascade_v2_enabled  # noqa: F401
from app.domain.entities import (  # noqa: F401
    ScheduleTask,
    TaskPriority,
    TaskStatus,
)
from app.infrastructure.database import get_db  # noqa: F401
from app.infrastructure.memory_store import store  # noqa: F401
from app.infrastructure.models.equipment_master import (  # noqa: F401
    EquipmentMaster as EquipmentMasterModel,
)
from app.infrastructure.models.production_batch import (  # noqa: F401
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_change_set import (  # noqa: F401
    ScheduleChangeSet,
)
from app.infrastructure.models.schedule_task import (  # noqa: F401
    ScheduleTask as ScheduleTaskModel,
)
from app.observability.cascade_logging import log_cascade_request  # noqa: F401
from app.observability.metrics import (  # noqa: F401
    cascade_feature_flag_state,
    cascade_preview_duration_seconds,
    cascade_revert_total,
    cascade_unresolved_total,
)
from app.presentation.routes.schedules._shared import (  # noqa: F401
    VersionDetailResponse,
    VersionSaveRequest,
    VersionSummaryResponse,
    _db_task_to_response,
    _parse_task_id,
    _to_response,
    _versions,
)
from app.presentation.schemas import (  # noqa: F401
    ScheduleTaskCreate,
    ScheduleTaskResponse,
    ScheduleTaskUpdate,
)
from app.presentation.schemas.cascade import (  # noqa: F401
    BulkUpdateErrorCode,
    BulkUpdateRequestV2,
    BulkUpdateSuccess,
    CascadePreviewRequest as CascadePreviewRequestV2,
    CascadePreviewResponse as CascadePreviewResponseV2,
)
from app.services.batch_grouping import (  # noqa: F401
    extract_sq,
    format_spec_display,
)
from app.services.cascade import (  # noqa: F401
    UnresolvedReason,
    plan_cascade_preview,
)
from app.services.cascade.snap import build_snapshot  # noqa: F401
from app.services.schedule_optimizer import PREDECESSOR_PROCESS  # noqa: F401
from app.services.schedule_validators import (  # noqa: F401
    find_due_date_violation,
    find_predecessor_violation,
    find_same_eq_overlap,
)


router = APIRouter(prefix="/schedules", tags=["스케줄"])


# ---------------------------------------------------------------------------
# 서브 라우터 import + 마운트
# ---------------------------------------------------------------------------
# 왜 importlib 를 쓰는가:
#   1) `list.py` 의 모듈명이 builtin `list` 를 그림자처리(shadow)한다.
#      `from . import list as _list_mod` 를 쓰면 패키지 attribute 로 `list`
#      submodule 이 등록되어 (PEP 328 자동 등록), 이후 어떤 코드가 모듈
#      namespace 에서 `list` 를 참조해도 builtin 보다 submodule 이 먼저
#      발견된다.
#   2) `revert.py` / `cascade.py` / `detail.py` / `bulk_update.py` 도 마찬가지로
#      submodule 이 동명의 함수 / Pydantic 모델과 충돌할 수 있다.
#   3) importlib.import_module 은 sys.modules 만 채우지만, Python import 시스템은
#      여전히 패키지 attribute 도 자동 등록한다 → 아래에서 명시 delattr 로 정리.
#      submodule 자체는 sys.modules 에 그대로 남으므로 외부의
#      `from app.presentation.routes.schedules.list import router` 같은 import 는
#      정상 동작.
# ---------------------------------------------------------------------------

_list_mod = _importlib.import_module("app.presentation.routes.schedules.list")
_detail_mod = _importlib.import_module("app.presentation.routes.schedules.detail")
_bulk_update_mod = _importlib.import_module(
    "app.presentation.routes.schedules.bulk_update"
)
_cascade_mod = _importlib.import_module("app.presentation.routes.schedules.cascade")
_revert_mod = _importlib.import_module("app.presentation.routes.schedules.revert")

router.include_router(_list_mod.router)
router.include_router(_detail_mod.router)
router.include_router(_bulk_update_mod.router)
router.include_router(_cascade_mod.router)
router.include_router(_revert_mod.router)

# 패키지 attribute 정리 — submodule 이름이 builtin / 함수명을 그림자처리하지
# 않도록 자동 등록된 attribute 를 제거. submodule 자체는 sys.modules 에 그대로
# 남아 외부 import 는 가능. D7-C re-export 는 이 아래에서 명시적으로 다시 설정.
_pkg = _sys.modules[__name__]
for _name in ("list", "detail", "bulk_update", "cascade", "revert"):
    if hasattr(_pkg, _name):
        delattr(_pkg, _name)


# ---------------------------------------------------------------------------
# D7-C: 핸들러 함수와 submodule-level 헬퍼/스키마를 패키지 namespace 로 re-export.
# 분리 이전 `from app.presentation.routes.schedules import <name>` 으로 접근하던
# 코드(예: tests/test_sheath_spec_list.py 의 `import list_tasks`) 가 그대로
# 동작하도록 같은 이름을 유지.
# ---------------------------------------------------------------------------

# list.py
list_tasks = _list_mod.list_tasks
list_versions = _list_mod.list_versions
get_version = _list_mod.get_version

# detail.py
create_task = _detail_mod.create_task
update_task = _detail_mod.update_task
delete_task = _detail_mod.delete_task
save_version = _detail_mod.save_version

# bulk_update.py
bulk_update_tasks_legacy = _bulk_update_mod.bulk_update_tasks_legacy
bulk_update_v2 = _bulk_update_mod.bulk_update_v2
BulkTaskUpdate = _bulk_update_mod.BulkTaskUpdate
BulkUpdateRequest = _bulk_update_mod.BulkUpdateRequest
_task_serializable = _bulk_update_mod._task_serializable
_coerce_task_id = _bulk_update_mod._coerce_task_id
_feature_disabled_error = _bulk_update_mod._feature_disabled_error

# cascade.py
cascade_preview_legacy = _cascade_mod.cascade_preview_legacy
cascade_preview_v2 = _cascade_mod.cascade_preview_v2
CascadePreviewRequest = _cascade_mod.CascadePreviewRequest
AffectedTask = _cascade_mod.AffectedTask
CascadeConflict = _cascade_mod.CascadeConflict
CascadePreviewResponse = _cascade_mod.CascadePreviewResponse
_SUCCESSOR_PROCESSES = _cascade_mod._SUCCESSOR_PROCESSES
_collect_all_successors = _cascade_mod._collect_all_successors
_reason_to_str = _cascade_mod._reason_to_str
_push_to_schema = _cascade_mod._push_to_schema
_unres_to_schema = _cascade_mod._unres_to_schema

# revert.py
revert = _revert_mod.revert
get_change_set_diff = _revert_mod.get_change_set_diff
_parse_iso_or_none = _revert_mod._parse_iso_or_none
_delta_hours = _revert_mod._delta_hours
_build_task_meta_map = _revert_mod._build_task_meta_map
_merge_meta = _revert_mod._merge_meta
