"""A refused request, said in the gateway log.

A refusal's full record is its security log row (``personalclaw security events``), but that is
not where an operator looks first after a user reports a 403, and the gateway log has no access
log at its default level (WARNING, with aiohttp's held there too). So each refusal also leaves one
WARNING line in the gateway log: the method, the path (never the query string), the reason and the
caller's address, and where its full record is.

A scan cannot flood the file. The first refusal per caller and reason in a window is said; the
rest are counted, and one line says how many once the window has closed. Past
:data:`_MAX_OPEN` callers at once, refusals from further callers are counted together.

Three places say them: the origin check outside token auth
(:func:`personalclaw.dashboard.origin.origin_refusal`), token auth
(:func:`personalclaw.dashboard.token_auth._log_auth`), for the refusals it answers before a request
goes further, and :func:`refusal_line_middleware`, just inside it, for a 403 any inner middleware
or handler answers (the file routes' path refusals among them).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

#: How long one caller's refusals for one reason are said as one line.
REFUSAL_WINDOW_SECS = 60.0
#: Callers with a window open at once; refusals from further callers are counted together.
_MAX_OPEN = 64
_OTHERS = ("other callers", "")
_SHOWN_CHARS = 200
_WHERE = "Its full record: personalclaw security events."


def _shown(text: str) -> str:
    """*text* as one log line can carry it: printable, and cut to a length."""
    return "".join(c if c.isprintable() else "?" for c in text)[:_SHOWN_CHARS]


class RefusalLines:
    """The gateway log's lines for refused requests, one per caller and reason in a window."""

    def __init__(self, window_secs: float = REFUSAL_WINDOW_SECS, max_open: int = _MAX_OPEN) -> None:
        self._window = window_secs
        self._max_open = max_open
        self._open: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def say(self, request: web.BaseRequest, reason: str) -> None:
        """Say one refusal of *request*, or count it into its caller's open window."""
        address = request.remote or "unknown"
        reason = _shown(reason or "refused")
        now = time.monotonic()
        with self._lock:
            closed = [(k, e) for k, e in self._open.items() if now - e["since"] >= self._window]
            for key, _entry in closed:
                del self._open[key]
            key = (address, reason)
            if key not in self._open and len(self._open) >= self._max_open:
                key = _OTHERS
            entry = self._open.get(key)
            if entry is None:
                self._open[key] = {"since": now, "more": 0}
            else:
                entry["more"] += 1
        for closed_key, closed_entry in closed:
            self._summarize(closed_key, closed_entry)
        if entry is not None:
            return
        # The path as it was sent, percent-encoded: never the query string, and never a decoded
        # character that could start a line of its own.
        path = _shown(request.rel_url.raw_path)
        if key == _OTHERS:
            logger.warning(
                "Refused %s %s from %s: %s. Refusals from further callers are counted until "
                "the window closes. %s",
                request.method,
                path,
                address,
                reason,
                _WHERE,
            )
        else:
            logger.warning(
                "Refused %s %s from %s: %s. %s", request.method, path, address, reason, _WHERE
            )

    def flush(self) -> None:
        """Say every open window's count now (gateway stop, and tests)."""
        with self._lock:
            pending = list(self._open.items())
            self._open.clear()
        for key, entry in pending:
            self._summarize(key, entry)

    def _summarize(self, key: tuple[str, str], entry: dict[str, Any]) -> None:
        more = int(entry["more"])
        if more <= 0:
            return  # the window's one refusal is already its own line
        address, reason = key
        if key == _OTHERS:
            logger.warning(
                "Refused %d more request(s) from other callers within %ds. %s",
                more,
                int(self._window),
                _WHERE,
            )
        else:
            logger.warning(
                "Refused %d more request(s) from %s within %ds: %s. %s",
                more,
                address,
                int(self._window),
                reason,
                _WHERE,
            )


REFUSALS = RefusalLines()


def say_refused(request: web.BaseRequest, reason: str) -> None:
    """Say a refusal of *request* in the gateway log. Never raises: a log line must not turn a
    refusal into a 500."""
    try:
        REFUSALS.say(request, reason)
    except Exception:
        logger.debug("could not say a refusal in the gateway log", exc_info=True)


def flush_refusal_lines() -> None:
    """Say every open window's count now; the gateway calls this on stop."""
    REFUSALS.flush()


def _reason_of(response: web.StreamResponse) -> str:
    """A 403's reason as its body gives it: the error code (and its ``reason``), else the text."""
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or response.content_type != "application/json":
        return "forbidden"
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return "forbidden"
    if isinstance(error, dict):
        code, why = str(error.get("code") or "forbidden"), error.get("reason")
        return f"{code} ({why})" if isinstance(why, str) and why else code
    return error if isinstance(error, str) and error else "forbidden"


@web.middleware
async def refusal_line_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """Say a 403 that a middleware inside this one or a handler answered. Token auth sits outside
    and says its own: a request it refuses never reaches here, so none is said twice."""
    try:
        response = await handler(request)
    except web.HTTPForbidden as exc:
        say_refused(request, exc.reason or "forbidden")
        raise
    if response.status == 403:
        say_refused(request, _reason_of(response))
    return response
