"""Run now tells the trigger's route how the run went, the way a scheduled fire does.

The owner set a digest to report to her direct messages on a chat channel and pressed Run now. The
run wrote its digest and its history said so, and the channel heard nothing: only the scheduled
fire consulted the route. Every hand-run and outside fire (Run now, the restart review's Run now,
an answered park, a webhook, a view refresh) goes through one dispatch, and that dispatch now
reports through the same reporter the scheduled fire uses. An action that only started its work
still says nothing until that work ends, as for a fire.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.action_providers as AP
from personalclaw import channel_delivery, channel_transports
from personalclaw.action_providers import ActionResult
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as handlers
from personalclaw.triggers import delivery as D
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

CHAT = "numchat"
TID = "clock:feed-digest"
OUTPUT = "23 feeds: 217 new entries, 0 unchanged, 0 failed"


class _Chat(ChannelTransportProvider):
    name = property(lambda self: CHAT)
    display_name = property(lambda self: "NumChat")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def validate_target(self, target: str) -> str:
        return ""


class _Handle:
    """The channel's outbound half: what reached it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw: Any) -> str:
        self.sent.append((channel, text))
        return "m-1"


class _State:
    """The `DashboardState` members the run and its report touch."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def push_refresh(self, *keys: str) -> None:
        return None

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})


class _Digest:
    """The trigger's action: a command that writes the digest and says what it did."""

    outcome: Any = ActionResult(success=True, stdout=OUTPUT)

    async def execute(self, config, ctx, timeout=30):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(handlers, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(handlers, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _Digest())
    key = owner_id_credential(CHAT)
    monkeypatch.delenv(key, raising=False)
    handle = _Handle()
    channel_transports.register_transport(_Chat())
    channel_delivery.register(handle, provider=CHAT)
    save_credential(key, "4242")
    yield tmp_path, handle
    os.environ.pop(key, None)
    channel_transports.unregister_transport(CHAT)
    channel_delivery.register(None, provider=CHAT)


def _store_trigger(home_dir, *, failure_delivery: str = "") -> None:
    TriggerStore(base_dir=home_dir).upsert(
        Trigger(
            id=TID,
            name="feed digest",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "45 6 * * *"},
            capabilities={"providers": ["digest-cmd"]},
            workflow={"inline": {"provider": "digest-cmd", "config": {"command": "digest"}}},
            delivery=f"channel:{CHAT}",
            failure_delivery=failure_delivery,
        )
    )


def _run_now(state: _State) -> web.Request:
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(
        "POST", f"/api/triggers/schedule:{TID}/run", match_info={"id": f"schedule:{TID}"}, app=app
    )

    async def _json() -> dict:
        return {}

    req.json = _json  # type: ignore[assignment]
    return req


async def _settled(state: _State, *, want: int = 1) -> list[dict[str, Any]]:
    """The notes, once the channel send each waits for has finished."""
    for _ in range(200):
        if len(state.notes) >= want:
            return state.notes
        await asyncio.sleep(0.01)
    return state.notes


@pytest.mark.asyncio
async def test_run_now_reports_to_the_channel_its_trigger_names(home, monkeypatch):
    home_dir, handle = home
    _store_trigger(home_dir)
    monkeypatch.setattr(_Digest, "outcome", ActionResult(success=True, stdout=OUTPUT))
    state = _State()

    resp = await trigger_runs.api_trigger_run(_run_now(state))
    notes = await _settled(state)

    assert resp.status == 200
    assert handle.sent == [("dm-4242", f"feed digest finished\n{OUTPUT}")]
    assert len(notes) == 1 and notes[0]["meta"][D.SENT_TO_CHANNEL_KEY] == CHAT


@pytest.mark.asyncio
async def test_a_failed_run_now_reports_its_failure(home, monkeypatch):
    home_dir, handle = home
    _store_trigger(home_dir)
    monkeypatch.setattr(_Digest, "outcome", RuntimeError("the feed host did not answer"))
    state = _State()

    await trigger_runs.api_trigger_run(_run_now(state))
    await _settled(state)

    assert handle.sent == [
        ("dm-4242", "feed digest failed\nRuntimeError: the feed host did not answer")
    ]


@pytest.mark.asyncio
async def test_a_run_now_that_only_started_its_work_says_nothing_yet(home, monkeypatch):
    """The control: the work reports when it ends, as it does for a scheduled fire."""
    home_dir, handle = home
    _store_trigger(home_dir)
    monkeypatch.setattr(
        _Digest, "outcome", ActionResult(success=True, stdout="started", outcome="launched")
    )
    state = _State()

    await trigger_runs.api_trigger_run(_run_now(state))
    await asyncio.sleep(0.1)

    assert handle.sent == [] and state.notes == []


def test_every_hand_run_and_outside_fire_hands_the_dispatch_its_state():
    """A caller that passes no state reports nowhere, which is the defect again one caller later."""
    callers = 0
    for module in (trigger_runs, handlers):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "_dispatch_store_action":
                continue
            callers += 1
            assert "state" in {k.arg for k in node.keywords}, ast.unparse(node)
    assert callers >= 5, f"found only {callers} callers; the scan is not seeing them"
