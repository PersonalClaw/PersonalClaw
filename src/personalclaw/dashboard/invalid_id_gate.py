"""The middleware that turns an :class:`UnsafeRecordId` into a ``400``, once.

:mod:`personalclaw.record_ids` refuses an untrusted record id that is not a single path
segment, and it refuses it *inside the store* — deliberately, because the MCP tools, the
workflow action providers and the CLI reach those stores without passing through a
handler, and a check in the handler layer would leave them unguarded.

That leaves one question per HTTP route: what does the refusal look like on the wire?
Answering it per handler would mean a ``try``/``except UnsafeRecordId`` in every route
that takes an id — dozens today, and one forgotten later, which is how the class opened
in the first place. So it is answered once here: any route may let the refusal
propagate, and every route reports it identically as
``400 {"error": {"code": "invalid_id", …}}``.

**Why a 400 and not a 404.** The refusal is a statement about the *request*, not about
the store's contents. `#459` records nearly filing "this store validates its ids" as a
non-finding because a traversal attempt answered ``404`` — indistinguishable from a
missing record — so the same probe run against a *fixed* store would have been just as
unreadable. A ``400`` naming the offending parameter is the difference between a control
you can audit and one you can only hope is there.

**Placement.** Installed as the INNERMOST middleware in
``server.py``'s explicit ordering, so it wraps the handler and nothing else: an
``UnsafeRecordId`` raised by an outer middleware would be a bug in that middleware, not
a client error, and should keep surfacing as a ``500``.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.record_ids import UnsafeRecordId

logger = logging.getLogger(__name__)


def invalid_id_middleware() -> Any:
    """Build the invalid-record-id middleware.

    A factory (rather than a bare middleware) so the marker attribute below can be
    attached to the instance the app installs, letting a test assert the gate is
    actually in ``app.middlewares`` instead of merely importable — the same reason
    :func:`~personalclaw.dashboard.api_version_gate.api_version_middleware` is one.
    """

    @web.middleware  # type: ignore[misc]
    async def _mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except UnsafeRecordId as exc:
            # DEBUG, not WARNING: a malformed id is a client error, and a 404-scanner
            # pointed at these routes would otherwise fill the log. The SEL records the
            # refused request itself.
            logger.debug("refused unsafe record id on %s: %s", request.path, exc)
            return json_error("invalid_id", message=str(exc), status=400)

    _mw._is_invalid_id_gate = True  # type: ignore[attr-defined]
    return _mw
