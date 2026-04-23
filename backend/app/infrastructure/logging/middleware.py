"""FastAPI middleware: stamp ``X-Run-Id`` on every response (spec §10a).

Generates a fresh UUID4 when the inbound request has no ``X-Run-Id``
header (the common case for KBI internal traffic today). Accepts a
client-supplied value otherwise — needed for end-to-end request
tracing when a future frontend wants to pre-generate run_ids and
correlate them with its own telemetry.

Sets the run_id into the contextvar for the duration of the
request so any log emitted by the route handler — or by downstream
services like ``cp_sat_schedule`` — carries the same ``[run_id=...]``
prefix automatically.

Middleware ordering note: this middleware MUST be added to the app
BEFORE ``CORSMiddleware``. In Starlette, the first-added middleware
is the outermost wrapper, so it sees the request first (sets
contextvar early → downstream logging works) and writes the
response last (stamps the header AFTER CORS has done its work, so
the header is not dropped).
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .run_context import reset_run_id, set_run_id

_HEADER = "X-Run-Id"


class RunIdMiddleware(BaseHTTPMiddleware):
    """Echo or generate a run_id per request; propagate via contextvar."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(_HEADER)
        run_id = incoming or str(uuid.uuid4())
        token = set_run_id(run_id)
        try:
            response: Response = await call_next(request)
        finally:
            # Reset the contextvar even if the route raised — leaking
            # a run_id to the next request on the same worker thread
            # would cross-contaminate logs.
            reset_run_id(token)
        # Always echo back so clients (and the browser DevTools
        # Network tab) can surface the run_id for support tickets.
        response.headers[_HEADER] = run_id
        return response
