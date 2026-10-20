"""Nothing is sent that nobody reads, and no option is offered that nobody passes.

Two pieces of dead weight are gone, and these censuses keep them gone:

* A chat turn used to broadcast a ``heartbeat`` frame every five seconds of a long turn. No
  client read it: the dashboard, the desktop shell and the phone page dispatch on frame types,
  and none of them names this one. The socket's keep-alive is aiohttp's own ping
  (``web.WebSocketResponse(heartbeat=30)`` in ``dashboard/ws.py``), so the frame kept nothing
  alive either. What says a running turn is alive is its phase, said again on the gateway's
  clock (``chat_phase``).
* A turn's free-text ``chat_status`` ("Thinking…") and its ``turn_steerable`` are gone too: what
  a turn is doing, and whether it takes a steer, are fields of the one ``work_phase`` frame.
* ``DefaultDialect.select_allow_option_id`` took a ``prefer_always`` flag that no caller ever
  passed. Its one caller approves a single pending call, so the once option is the one sent.
"""

from __future__ import annotations

import inspect

from no_frame_or_flag_is_left_without_a_reader_shared import _REPO

from personalclaw.acp.dialect import DefaultDialect

_SRC = _REPO / "src" / "personalclaw"


def test_approving_sends_the_once_option_and_takes_no_preference() -> None:
    params = list(inspect.signature(DefaultDialect.select_allow_option_id).parameters)
    assert params == ["self", "offered"]
    offered = [
        {"id": "keep-allowing", "kind": "allow_always"},
        {"id": "this-once", "kind": "allow_once"},
    ]
    assert DefaultDialect().select_allow_option_id(offered) == "this-once"
