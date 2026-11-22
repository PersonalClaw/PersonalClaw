"""A create the gateway makes for an agent CLI is made once, and a lost answer is never said as a
call that was not made.

The tool server an agent CLI runs hands the calls that write PersonalClaw's stores (automations,
webhooks, tiles, proposals) to the gateway through ``POST /api/tools/invoke`` and waits for its
answer (``mcp_core.made_by_the_gateway``). When that answer was lost after the call was made, the
same create could be made twice:

* The route made the call, then failed writing its security-log row, and answered a plain-text 500.
  The tool said "Could not make this call: PersonalClaw's gateway makes it, and it did not answer",
  which was false, and an agent that believed it called again.
* An agent that called again after any lost answer made a second, identical automation.

Now the route answers a call it made whatever its log took, the tool says any answer lost after
the call reached the gateway as "it may have made it, so look before you call it again", and the
gateway answers the same create from the same chat within minutes with the first one's answer,
making nothing again (``dashboard.handlers.made_once``).

The first rows drive the real gateway (``start_dashboard``) and the real tool server an agent CLI
starts, over stdio, in a home its sandbox fences.
"""

from __future__ import annotations

import asyncio
import http.client
import io
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest
from test_an_agent_clis_tools_save_in_a_home_its_sandbox_fences import (  # noqa: F401
    KEY,
    _data,
    _ToolServer,
    gateway,
)

from personalclaw import home_gateway, mcp_core
from personalclaw.dashboard.handlers import made_once
from personalclaw.tool_providers.base import ToolFailure
from personalclaw.triggers.store import TriggerStore

BINS = {"name": "Bins reminder", "when": "in 3 hours", "say": "Bins out tonight."}


@pytest.fixture(autouse=True)
def _nothing_remembered():
    made_once.reset_for_tests()
    yield
    made_once.reset_for_tests()


def _named(name: str) -> list[str]:
    return [r.trigger.id for r in TriggerStore().load() if r.trigger.name == name]


# ── Through the real gateway and tool server ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_create_called_again_from_the_same_chat_is_made_once(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Red before: the second call made a second, identical automation, which then fired
    twice."""
    async with _ToolServer(gateway, tmp_path) as server:
        ok, first = await server.call("automation_create", BINS)
        assert ok, first
        ok, again = await server.call("automation_create", BINS)

    assert ok, again
    assert _data(first)["trigger"]["id"] in again
    assert "made this same call a few minutes ago" in again
    assert _named("Bins reminder") == [_data(first)["trigger"]["id"]]


@pytest.mark.asyncio
async def test_a_create_asking_for_something_else_is_made(
    gateway, tmp_path  # noqa: F811 - the imported fixture
):
    async with _ToolServer(gateway, tmp_path) as server:
        ok, first = await server.call("automation_create", BINS)
        ok_too, other = await server.call(
            "automation_create", {**BINS, "name": "Recycling reminder"}
        )

    assert ok and ok_too, (first, other)
    assert "made this same call" not in other
    assert len(_named("Bins reminder")) == len(_named("Recycling reminder")) == 1


@pytest.mark.asyncio
async def test_a_call_made_whose_row_the_log_could_not_take_is_answered_as_made(
    gateway, tmp_path, monkeypatch  # noqa: F811 - the imported fixture
):
    """🔴 Red before: the route answered a plain-text 500, and the tool said the call it had
    made "Could not make this call"."""
    from personalclaw.dashboard.handlers import tools as tools_handlers

    class _Full:
        def log_tool_invocation(self, **_row: Any) -> None:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(tools_handlers, "_sel", lambda: _Full())
    async with _ToolServer(gateway, tmp_path) as server:
        ok, made = await server.call("automation_create", BINS)

    assert ok, made
    assert _named("Bins reminder") == [_data(made)["trigger"]["id"]]


# ── What the tool server says of an answer it did not get ────────────────────────────────────────


def _gateway_answers(monkeypatch, outcome: BaseException | bytes) -> None:
    """The gateway as ``_post`` reaches it: raising *outcome*, or answering 200 with it."""
    monkeypatch.setattr(mcp_core, "_api_base", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(mcp_core, "_internal_headers", lambda extra=None: dict(extra or {}))

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def opened(_request, *, timeout: float):
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(home_gateway, "open_loopback", opened)


def _plain_500() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:9/api/tools/invoke",
        500,
        "Internal Server Error",
        {},  # type: ignore[arg-type]
        io.BytesIO(b"500 Internal Server Error\n\nServer got itself in trouble"),
    )


@pytest.mark.parametrize(
    ("outcome", "said"),
    [
        (_plain_500(), "its answer was lost (HTTP 500: 500 Internal Server Error"),
        (
            http.client.RemoteDisconnected("Remote end closed connection without response"),
            "its answer was lost (Remote end closed connection without response)",
        ),
        (b"<html>not the route's answer</html>", "its answer was lost"),
    ],
    ids=["a plain-text 500", "a connection dropped before the answer", "an answer not read"],
)
def test_an_answer_lost_after_the_call_reached_the_gateway_says_it_may_have_been_made(
    monkeypatch, outcome, said
):
    """🔴 Red before: each said "Could not make this call", and an agent called it again."""
    _gateway_answers(monkeypatch, outcome)

    made = mcp_core.made_by_the_gateway("hook_register", {"hook_id": "build-done"})

    assert isinstance(made.answer, ToolFailure) and made.audited is False
    assert said in made.answer, made.answer
    assert made.answer.endswith("it may have made it, so look before you call it again.")


def test_a_call_that_never_reached_the_gateway_is_still_said_not_made(monkeypatch):
    _gateway_answers(
        monkeypatch, urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    )

    made = mcp_core.made_by_the_gateway("hook_register", {"hook_id": "build-done"})

    assert made.answer.startswith("Error: Could not make this call"), made.answer


# ── What the gateway remembers ───────────────────────────────────────────────────────────────────


def test_only_a_chats_creates_are_remembered():
    assert made_once.key_of(KEY, "automation_create", BINS) is not None
    assert made_once.key_of(KEY, "hook_register", {"hook_id": "x"}) is not None
    assert made_once.key_of(KEY, "automation_list", {}) is None
    assert made_once.key_of("internal", "automation_create", BINS) is None
    assert made_once.key_of(KEY, "automation_create", BINS) != made_once.key_of(
        "dashboard:another-chat", "automation_create", BINS
    )


@pytest.mark.asyncio
async def test_a_repeat_while_the_first_is_still_made_waits_for_its_answer():
    """The wait for the first ran out and the agent called again while the gateway still made
    it: the second is answered with the first's answer, not made beside it."""
    key = made_once.key_of(KEY, "automation_create", BINS)
    assert key is not None and await made_once.claim(key) is None

    second = asyncio.create_task(made_once.claim(key))
    await asyncio.sleep(0.01)
    assert not second.done()
    made_once.made(key, {"ok": True, "output": '{"trigger": {"id": "t-1"}}', "error": ""})

    assert (await second)["output"] == '{"trigger": {"id": "t-1"}}'


@pytest.mark.asyncio
async def test_a_create_that_failed_is_forgotten_so_it_can_be_tried_again():
    key = made_once.key_of(KEY, "automation_create", BINS)
    assert key is not None and await made_once.claim(key) is None
    waiting = asyncio.create_task(made_once.claim(key))
    await asyncio.sleep(0.01)

    made_once.made(key, None)

    assert await waiting is None, "the repeat of a failed create is made"


@pytest.mark.asyncio
async def test_a_create_made_longer_ago_than_the_window_is_made_again(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(made_once, "time", SimpleNamespace(monotonic=lambda: now[0]))
    key = made_once.key_of(KEY, "automation_create", BINS)
    assert key is not None and await made_once.claim(key) is None
    made_once.made(key, {"ok": True, "output": "made", "error": ""})

    now[0] += made_once.REPEAT_WINDOW_SECS - 1
    assert await made_once.claim(key) == {"ok": True, "output": "made", "error": ""}
    now[0] += 2
    assert await made_once.claim(key) is None
