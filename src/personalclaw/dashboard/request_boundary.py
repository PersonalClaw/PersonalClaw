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
envelope and its generic sentence.

**Her request's, or PersonalClaw failing.** The same types are also what PersonalClaw's own
code raises when it breaks, and read by type alone a broken product blamed her request:
deactivating an app once torch was loaded raised a ``TypeError`` four calls beneath the route,
in the scan of the loaded modules, and the Apps page said the request was malformed. So the
type is not enough, and where it was raised decides:

* a ``TypeError`` or ``AttributeError`` is her request's only when it was raised while the route
  read the request: in the route's own code, the request reader
  (:mod:`personalclaw.request_validation`), or a library they called on it (aiohttp reading the
  body, ``json`` decoding it), with nothing of PersonalClaw's own code or an app's running
  beneath the route. Raised beneath it, it is PersonalClaw failing: ``500 internal_error``,
  which says so, logged at ERROR with its traceback.
* a ``ValueError`` is a value that was refused, wherever it was raised: that is how the
  product's own code refuses one (a time zone that is not one, a name a store will not take,
  the dozens of refusals that subclass it), so it stays her request's ``400``.

**A store file that could not be read** is answered here too, once for every route:
``record_files.Unreadable`` (a ``ValueError``, so it is caught before the fault family) means a
file the request needed is there and cannot be read, and nothing was written to it. It answers
``store_unreadable``, 409 for a write and 500 for a read, with the refusal's own sentence, which
names the file, why, where its copy is kept and what to do. Answered per handler, the next route
would let it fall through to ``bad_request``.

**Two precisions, one gate.** The fault family described above is what NOBODY guarded,
and its generic sentence names no field because it cannot — it is reading an
``AttributeError``. The other branch serves
:class:`~personalclaw.request_validation.RequestValidationError`,
which a handler raised ON PURPOSE via the shared write-path validator and which DOES
name the field. Both live here for the same reason: answered per handler, the next route
forgets. Ordering is load-bearing — the deliberate refusal is caught first, because
routing it through the fault branch would discard the field name that is the entire
value of having validated.

**A link in the home.** :class:`~personalclaw.durability.home_paths.LinkInTheWay` is the home's
own refusal of a symbolic link, or a file with another name, where a request was to write, read or
take a lock: a store's lock, which every write of the store takes, is the common case. It is a
formed answer too, served as ``409 link_in_the_way`` with the sentence that names the link, so a
route need not catch it for the owner to read what is in the way.

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
handler and this gate never shadows it. Its codes are ``bad_request`` and ``internal_error``,
both in the append-only registry.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import traceback
import types
from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw import app_code, record_files, request_validation
from personalclaw.durability.home_paths import LinkInTheWay
from personalclaw.http_errors import json_error
from personalclaw.request_validation import RequestValidationError

logger = logging.getLogger(__name__)

#: Where PersonalClaw's own code is (the package folder, with a trailing separator), as its
#: frames name their files.
_PACKAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "")
#: The one reader of request bodies and fields: its frames read her request, as the route's do.
_REQUEST_READER = request_validation.__file__


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
        except LinkInTheWay as exc:
            # The home's refusal of a link where the request was to write, read or lock
            # (`durability.home_paths`): a store's lock, most often, which every write of the
            # store takes. A formed answer like the one above, so not path-scoped either.
            logger.warning("%s %s stopped at a link: %s", request.method, request.path, exc)
            return json_error(
                "link_in_the_way",
                message=f"Stopped at {exc}. Remove the link and try again.",
                status=409,
            )
        except record_files.Unreadable as exc:
            # A store file the request needed could not be read, so nothing was written to it
            # (`record_files`: never written over unread). Before the fault family below, which
            # would call it a malformed request because it is a ValueError. 409 for a write it
            # refused, 500 for a read it cannot answer; the message names the file, why, where
            # its copy is kept and what to do, and never quotes the file.
            if not request.path.startswith("/api/"):
                raise
            status = 500 if request.method in ("GET", "HEAD") else 409
            return json_error("store_unreadable", message=str(exc), status=status)
        except (ValueError, TypeError, AttributeError) as exc:
            # Scoped to /api/* for the same reason spa_fallback scopes its 404/405
            # normalization: the wire envelope is what a JSON client reads, and turning a
            # fault on an HTML/static route into a JSON blob would be the wrong answer for
            # a browser. Off /api the fault propagates unchanged (this class is /api-only).
            if not request.path.startswith("/api/"):
                raise
            if not isinstance(exc, ValueError) and not _raised_reading_the_request(exc, request):
                # PersonalClaw's own code, or an app's, broke beneath the route: a 500 that says
                # the product failed, and the traceback for whoever reads the log.
                logger.error(
                    "PersonalClaw failed answering %s %s: %s: %s",
                    request.method,
                    request.path,
                    type(exc).__name__,
                    exc,
                    exc_info=exc,
                )
                return json_error("internal_error", status=500)
            # The unguarded request-shape fault family (#2861/#2855/#554). WARNING, not
            # DEBUG: the log line naming the route and the exception is how a fault read as
            # hers is found. The message is NOT returned to the client — only the stable
            # envelope and its generic sentence.
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


def _raised_reading_the_request(exc: BaseException, request: web.Request) -> bool:
    """Whether *exc* was raised while the route read the request.

    From the route's own frame inward, every frame must be the route's own code, the request
    reader's, or a library's (aiohttp, ``json``, the standard library): a frame of PersonalClaw's
    own code or of an app's means the product was at work, and the fault is its own. A route
    whose code cannot be found is not one a fault can be pinned on.
    """
    try:
        route = _route_file(request)
        files = [frame.f_code.co_filename for frame, _line in traceback.walk_tb(exc.__traceback__)]
        if route is None or route not in files:
            return False
        return not any(_products(path, route) for path in files[files.index(route) :])
    except Exception:  # noqa: BLE001 — a fault that cannot be traced is not pinned on her request
        logger.debug(
            "could not trace a fault on %s %s", request.method, request.path, exc_info=True
        )
        return False


def _route_file(request: web.Request) -> str | None:
    """The file the matched route's handler is written in, through any decorator around it."""
    handler: Any = request.match_info.handler
    while isinstance(handler, functools.partial):
        handler = handler.func
    handler = inspect.unwrap(getattr(handler, "__func__", handler))
    code = getattr(handler, "__code__", None)
    return code.co_filename if isinstance(code, types.CodeType) else None


def _products(path: str, route: str) -> bool:
    """Whether a frame in *path* is PersonalClaw's own code or an app's, not reading the request."""
    if path in (route, _REQUEST_READER):
        return False
    return path.startswith(_PACKAGE) or app_code.loaded_app(path) is not None
