"""The subprotocol the dashboard's sockets name themselves by, and the one they never pass on.

A browser can't set a header on a WebSocket upgrade, so the dashboard (``web/src/lib/pair.ts``)
offers its sign-in's pair as a subprotocol, ``pc-pair.<pair>``, beside ``personalclaw``. Two
things follow for every socket route:

* it **selects** ``personalclaw`` (``protocols=(SOCKET_PROTOCOL,)``). A browser that offered
  subprotocols fails a handshake that selects none, and aiohttp writes a client's whole offered
  list to the log at WARNING when none overlaps;
* it **strips** every ``pc-pair.*`` entry from the offered list before ``prepare()``
  (:func:`without_pair_protocol`), so nothing downstream of the sign-in check, the handshake and
  its log line included, ever holds the pair, and the 101 can't echo it.

A page that holds no pair offers no subprotocol at all, and its upgrade is what it always was.
"""

from __future__ import annotations

from aiohttp import web
from multidict import CIMultiDict

#: The subprotocol every dashboard socket selects.
SOCKET_PROTOCOL = "personalclaw"
#: The prefix of the offered entry that carries the sign-in's pair.
PAIR_PROTOCOL_PREFIX = "pc-pair."

_HEADER = "Sec-WebSocket-Protocol"


def without_pair_protocol(request: web.Request) -> web.Request:
    """*request*, with every ``pc-pair.*`` entry removed from its offered subprotocols.

    The same request when it offers none, so an upgrade from a page that holds no pair is
    untouched. Hand the result to ``WebSocketResponse.prepare``; read everything else (the
    request's app scope, its session) from the original.
    """
    offered = request.headers.getall(_HEADER, [])
    if not any(PAIR_PROTOCOL_PREFIX in value for value in offered):
        return request
    kept = [
        name
        for value in offered
        for name in (part.strip() for part in value.split(","))
        if name and not name.startswith(PAIR_PROTOCOL_PREFIX)
    ]
    headers = CIMultiDict(request.headers)
    headers.popall(_HEADER, None)
    if kept:
        headers[_HEADER] = ", ".join(kept)
    return request.clone(headers=headers)
