"""LoggerAdapter that prepends ``[run_id=...]`` to every record.

Why an adapter (not a Filter / not a custom Formatter): formatters
are attached per-handler and so a third-party handler (uvicorn,
pytest's caplog) misses the prefix. The adapter attaches to the
*logger call site* — every emit from code that used
``get_run_logger(__name__)`` gets the prefix regardless of which
handler formats it.

Opt-in: existing ``logging.getLogger(__name__)`` usages keep their
old behavior unchanged. Call sites that want run_id tagging switch
to ``get_run_logger(__name__)``.
"""

from __future__ import annotations

import logging

from .run_context import get_run_id


class RunIdLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that prefixes records with ``[run_id=<uuid>]``.

    When no run_id is in the current contextvar, the adapter emits
    the record unchanged — no empty prefix, so grep-friendly output
    for non-solve codepaths stays clean.
    """

    def process(self, msg: object, kwargs: dict) -> tuple[object, dict]:
        rid = get_run_id()
        prefix = f"[run_id={rid}] " if rid else ""
        return f"{prefix}{msg}", kwargs


def get_run_logger(name: str) -> RunIdLoggerAdapter:
    """Convenience factory — use instead of ``logging.getLogger`` for
    call sites that should emit run_id-prefixed messages.

    Returns an adapter wrapping the module's standard Logger so all
    existing handlers, level configs, and propagation rules continue
    to apply.
    """
    return RunIdLoggerAdapter(logging.getLogger(name), {})
