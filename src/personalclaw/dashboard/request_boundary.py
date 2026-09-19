"""The middleware that turns an unguarded request-shape fault into the wire envelope, once.

Fourteen-plus ``/api/*`` handlers read the request body or a query/path parameter
without first checking its shape: ``body.get(...)`` on a body that parsed to a list,
``int(request.query.get("limit"))`` on a non-numeric string, ``.strip()`` on a
non-string field. A slightly-off request therefore raises an unhandled
``AttributeError``/``ValueError``/``TypeError`` and aiohttp answers its bare
``500 text/plain`` "Server got itself in trouble" — which a JSON client cannot parse,
so it cannot tell "I sent bad input" from "the server broke". That breaks the one
wire error-envelope contract (``AGENTS.md`` §"Shared conventions" → **Error envelope
(HTTP)**) the same way an unnormalized 405 did (#2846, fixed in ``spa_fallback``).

Answering it per handler would mean a ``try``/``except`` around every body read and
every ``int()`` — the class was filed against ``/api/tasks`` (#2855), task comments
(#554) and thirteen more POST routes (#2861), and the codebase had already been
closing it one endpoint at a time (``handlers_inbox`` digest ``hours``, ``chat_folders``
``order``, the tasks-comments body guard), which is exactly how a systemic gap stays
open: the next route forgets. So it is answered once here — any route may let a
request-shape fault propagate, and every route reports it identically as
``400 {"error": {"code": "bad_request", ...}}``.

**Why 400, and which faults.** The three builtin types below are the "unguarded
request-shape access" family: a bad numeric parameter (``ValueError`` from ``int()``/
``float()``), a body that parsed to a non-object (``AttributeError`` from ``.get()`` on
a list, ``TypeError`` from a coercion), or ``.strip()`` on a non-string scalar. The
codebase already answers this family ``400`` wherever a handler happened to guard it
(see ``tests/test_gateway_4xx_not_500.py``); this makes 400 the default for the routes
that did not. The raw exception is logged, never returned: the client gets the stable
envelope and its generic sentence, and the traceback stays in ``gateway.log`` — the
current behavior leaks the internal fault to the client, this one does not.

**Two precisions, one gate.** The fault family described above is what NOBODY guarded,
and its generic sentence names no field because it cannot — it is reading an
``AttributeError``. The other branch serves
:class:`~personalclaw.request_validation.RequestValidationError`,
which a handler raised ON PURPOSE via the shared write-path validator and which DOES
name the field. Both live here for the same reason: answered per handler, the next route
forgets. Ordering is load-bearing — the deliberate refusal is caught first, because
routing it through the fault branch would discard the field name that is the entire
value of having validated.

**What it deliberately does NOT catch.** ``web.HTTPException`` is re-raised untouched:
a handler that answers 404/400/redirect on purpose, and the router's 404/405 that
``spa_fallback`` normalizes, are already-formed responses, not faults to reinterpret.
Every other exception type (``RuntimeError``, ``OSError``, ``KeyError``, a custom
service error, …) also propagates unchanged, so a genuine server bug keeps surfacing as
a ``500`` rather than being mislabelled a client error — the same reasoning
``invalid_id_gate`` states for staying innermost. The envelope is scoped to ``/api/*``
(as ``spa_fallback``'s normalization is): a fault on an HTML/static route propagates
unchanged, because a JSON envelope is the wrong answer for a browser and this class is
``/api``-only.

**Placement.** Installed just OUTSIDE ``invalid_id_middleware`` in ``server.py``'s
explicit ordering, so ``invalid_id`` (which maps ``UnsafeRecordId`` — not a
``ValueError`` subclass — to its own ``invalid_id`` code) still runs closest to the
handler and this gate never shadows it. It reuses the already-registered ``bad_request``
wire code, so no new code is minted and the append-only registry rail is untouched.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.request_validation import RequestValidationError

logger = logging.getLogger(__name__)


def request_boundary_middleware() -> Any:
    """Build the request-shape-fault middleware.

    A factory (rather than a bare middleware) so the marker attribute below can be
    attached to the installed instance, letting a test assert the gate is actually in
    ``app.middlewares`` — the same reason ``invalid_id_middleware`` and
    ``api_version_middleware`` are factories.
    """

    @web.middleware  # type: ignore[misc]
    async def _mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException:
            # An already-formed HTTP response (a handler's deliberate 4xx/redirect, or
            # the router's 404/405 that spa_fallback owns). Never reinterpret it.
            raise
        except RequestValidationError as exc:
            # The DELIBERATE half of this middleware's job, and it must be caught before
            # the fault family below. A handler that called
            # `request_validation.json_object_body`/`require_string` has already decided
            # this request is malformed and knows WHICH field; the refusal carries its own
            # code and message, so there is nothing to reinterpret — only to serve. This is
            # what makes adoption a one-line change at the 265 body-read sites `dd5c279db`
            # carried, instead of a try/except at each (see request_validation's docstring).
            #
            # NOT path-scoped, unlike the branch below: an explicit refusal is a formed
            # answer for any route that chose to validate, not a fault being rescued on the
            # /api surface. And answered here rather than by the handler for the reason the
            # docstring gives — a per-handler answer is how a systemic gap stays open.
            return exc.response
        except (ValueError, TypeError, AttributeError) as exc:
            # Scoped to /api/* for the same reason spa_fallback scopes its 404/405
            # normalization: the wire envelope is what a JSON client reads, and turning a
            # fault on an HTML/static route into a JSON blob would be the wrong answer for
            # a browser. Off /api the fault propagates unchanged (this class is /api-only).
            if not request.path.startswith("/api/"):
                raise
            # The unguarded request-shape fault family (#2861/#2855/#554). WARNING, not
            # DEBUG: if this ever masks a genuine bug of one of these types, the log line
            # naming the route and the exception is how it is found. The message is NOT
            # returned to the client — only the stable envelope and its generic sentence.
            logger.warning(
                "request-shape fault on %s %s: %s: %s",
                request.method,
                request.path,
                type(exc).__name__,
                exc,
            )
            return json_error("bad_request", status=400)

    _mw._is_request_boundary_gate = True  # type: ignore[attr-defined]
    return _mw
