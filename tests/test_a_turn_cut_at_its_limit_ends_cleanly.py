"""A nudged turn cut at its time limit ends cleanly, and knows, as it ends, that its limit ended it.

Measured: a loop planner's turn ran past its 600 s bound, and the cut crashed in its own clean-up —
``AttributeError: '_ChatSession' object has no attribute '_running'`` ("Task exception was never
retrieved"). The session is slotted and its ``running`` is read off its turn's task, so there is no
flag to write: the cut turn has already ended, and only saying so was left to do. The turn says so
itself (`turn_deadline`), once, so the chat shows one notice and ends in an error, not as a stop;
`tests/test_a_queued_turn_past_its_limit_says_so.py` drives that through the real chat runner.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from personalclaw.dashboard.state import _ChatSession


class _Nudge:
    def __init__(self, lid: str, session_name: str) -> None:
        self.id, self.session_name, self.active = lid, session_name, True


class _FakeNudgeService:
    """Stands in for `AutoNudgeService` so the gateway hands it the REAL `_fire` callback."""

    last: "_FakeNudgeService | None" = None

    def __init__(self, *, base_dir: Any, on_fire: Any) -> None:
        self.on_fire = on_fire
        self.rows: dict[str, _Nudge] = {}
        _FakeNudgeService.last = self

    async def start(self) -> None:
        return None

    def subscribe(self, _observer: Any) -> None:
        return None

    def get_by_session(self, name: str) -> _Nudge | None:
        return self.rows.get(name)

    async def remove(self, loop_id: str) -> None:
        return None

    def notify_turn_complete(self, *_: Any, **__: Any) -> None:
        return None


def test_a_planner_turn_past_its_limit_is_stopped_without_a_crash() -> None:
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    key = "loop-plan-abcd1234"
    session = _ChatSession(key, agent="planner")
    session._app = "loops"  # the planner's app: a plain chat turn bound, not a loop cycle
    dstate = MagicMock()
    dstate._sessions = {key: session}
    dstate._background_tasks = set()
    dstate.waiting_on_owner.return_value = False
    orch.dashboard_state = dstate
    orch.loop_watchdog = None

    ended_by: list[float] = []

    async def _wedged_run_chat(_state: Any, _sess: Any, _msg: str) -> None:
        from personalclaw.dashboard import turn_deadline

        try:
            await asyncio.sleep(60)
        finally:
            # As the real turn does as it ends: read whether its limit is what stopped it.
            ended_by.append(turn_deadline.limit_passed())

    nudge = MagicMock(
        id="N1", session_name=key, message="draft the step", stop_sentinel_path="", cycle_count=0
    )

    async def _go() -> None:
        with (
            patch("personalclaw.gateway.autonudge_enabled", return_value=True),
            patch("personalclaw.gateway.AutoNudgeService", _FakeNudgeService),
            patch("personalclaw.gateway.CHAT_TURN_TIMEOUT", 0.2),
            patch("personalclaw.dashboard.chat.run_chat", _wedged_run_chat),
        ):
            await orch._init_autonudge()
            svc = _FakeNudgeService.last
            svc.rows[key] = _Nudge("N1", key)
            assert await svc.on_fire(nudge) is True
            # At `integration` this raises the AttributeError the cut's clean-up hit.
            await asyncio.wait_for(session.task, timeout=10)

    asyncio.run(_go())

    assert session.running is False
    assert ended_by == [0.2], "the turn could not tell that its limit stopped it"
