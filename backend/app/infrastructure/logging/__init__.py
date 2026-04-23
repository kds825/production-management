"""Run-ID correlation primitives (spec §10a).

Exports:
- contextvar helpers (set/get/reset) for async-safe run_id propagation.
- LoggerAdapter factory that prepends ``[run_id=...]`` to records.
- FastAPI middleware that stamps ``X-Run-Id`` on every response.
"""

from .adapters import RunIdLoggerAdapter, get_run_logger
from .middleware import RunIdMiddleware
from .run_context import get_run_id, reset_run_id, set_run_id

__all__ = [
    "set_run_id",
    "get_run_id",
    "reset_run_id",
    "RunIdLoggerAdapter",
    "get_run_logger",
    "RunIdMiddleware",
]
