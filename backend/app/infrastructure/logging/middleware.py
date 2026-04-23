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
AFTER ``CORSMiddleware``. In Starlette, ``add_middleware`` PREPENDS
to the internal stack, so the LAST-added middleware is the outermost
wrapper. Wrapping outside CORS ensures preflight (OPTIONS) responses
also carry ``X-Run-Id`` — spec §10a's "every response" invariant.
"""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .run_context import reset_run_id, set_run_id

_HEADER = "X-Run-Id"

# Defense-in-depth sanitization on client-supplied run_id. Starlette/ASGI
# stacks typically reject CRLF in headers, but we strip explicitly so a
# malformed value surfaces as a server-generated UUID rather than a
# half-trusted echo. Printable ASCII only; reject anything else.
_SAFE_RUNID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


class RunIdMiddleware(BaseHTTPMiddleware):
    """Echo or generate a run_id per request; propagate via contextvar."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(_HEADER)
        if incoming and _SAFE_RUNID.match(incoming):
            run_id = incoming
        else:
            run_id = str(uuid.uuid4())
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
