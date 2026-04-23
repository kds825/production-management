"""Contextvar-based run_id propagation (spec §10a).

Why contextvars: async-safe. Automatically copies on
``asyncio.create_task`` and FastAPI request handlers, so a run_id set
at the middleware entry (or at ``cp_sat_schedule`` entry for non-HTTP
callers) flows through every ``await`` without plumbing by hand.

Why None default: endpoints that are not solves (health, metrics,
equipment CRUD) have no meaningful run_id — leaving it None is the
signal to the LoggerAdapter that it should emit records *without* a
``[run_id=...]`` prefix.

Always pair ``set_run_id(...)`` with ``reset_run_id(token)`` in a
try/finally. A leaked token lets a stale run_id bleed into the next
request that happens to hit the same worker thread.
"""

from __future__ import annotations

import contextvars

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_run_id",
    default=None,
)


def set_run_id(run_id: str | None) -> contextvars.Token:
    """Set run_id for the current context; returns token for reset().

    Callers MUST pass the returned token to ``reset_run_id`` in a
    try/finally block to avoid contextvar leakage across requests.
    """
    return _run_id.set(run_id)


def get_run_id() -> str | None:
    """Return run_id if set in the current context, else None."""
    return _run_id.get()


def reset_run_id(token: contextvars.Token) -> None:
    """Restore the contextvar to the value before ``set_run_id``."""
    _run_id.reset(token)
