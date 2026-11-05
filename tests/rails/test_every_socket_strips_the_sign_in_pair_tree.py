"""Every gateway socket selects ``personalclaw`` and strips the sign-in's pair before its handshake.

The dashboard offers its sign-in's pair as the subprotocol ``pc-pair.<pair>`` beside
``personalclaw`` (``web/src/lib/pair.ts``). A socket route that selects no subprotocol fails a
browser's handshake that offered some, and one that hands ``prepare()`` the request as it came
lets aiohttp log the whole offered list, the pair with it, when nothing overlaps
(``personalclaw.dashboard.socket_protocols``). A route added without either rule turns CI red.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

_SKIP = REPO / "src" / "personalclaw" / "dashboard" / "socket_protocols.py"


def test_every_gateway_socket_selects_personalclaw_and_strips_the_pair() -> None:
    found = 0
    for path in source_index.package_files():
        if path == _SKIP:
            continue
        text = source_index.text(path)
        if "WebSocketResponse(" not in text:
            continue
        rel = path.relative_to(REPO)
        for match in re.finditer(r"WebSocketResponse\((.*)$", text, re.MULTILINE):
            found += 1
            line = text[: match.start()].count("\n") + 1
            assert "protocols=(SOCKET_PROTOCOL,)" in match.group(
                1
            ), f"{rel}:{line} selects no subprotocol"
        prepares = re.findall(r"await ws\.prepare\((.*)$", text, re.MULTILINE)
        assert prepares and all(
            p.startswith("without_pair_protocol(") for p in prepares
        ), f"{rel} prepares a socket on a request that may hold the pair"
    assert found >= 2, "the gateway's two socket routes were not found"
