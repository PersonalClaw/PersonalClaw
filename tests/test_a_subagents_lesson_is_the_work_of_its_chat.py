"""A subagent's lesson is the work it does for its chat: saved where the chat may keep it, filed
under the chat, and refused, saying why, where the chat keeps nothing.

``memory_remember`` from any subagent answered "unknown session": the lesson route recognised a
chat's key, a channel's and a transcript's name, and nothing else, so no subagent could save a
lesson, the owner's own agents included, and the tool told the agent to start a new tab. Now the
route judges the write as the work of the session the subagent works for, up the chain to the chat
at the top (``memory_reads.reach_of``, the walk every memory read takes): the lesson is saved when
that chat keeps memory, filed under that chat, and refused in the words every memory write is
refused with when anything on the way keeps nothing.

Driven as the agent drives it: the real gateway over a scratch home, asked by the memory tools'
provider the native agent calls, bound to the subagent's own key.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_memory_is_read_only_where_its_work_may_read_it import (
    APP,
    APP_CHAT,
    DECLARED,
    HERE,
    _call,
    _gateway,
    _install,
)

from personalclaw import memory_writes, session_restrictions
from personalclaw.vector_memory import VectorMemoryStore

#: What the subagent is told to remember.
TAUGHT = "Keep the seed trays on the north bench, out of the wind."
#: Her chat's agent, and the agent it started in turn.
AGENT = "subagent:a1b2c3d4"
ITS_AGENT = "subagent:c0ffee00"
#: A chat nothing records: not open in the gateway, and no transcript.
GONE = "dashboard:chat-30-1790700000"
#: A Temporary chat open in the gateway, with no transcript written yet.
LIVE = "dashboard:chat-31-1790700001"


def _working_for(state: Any, monkeypatch: pytest.MonkeyPatch, parents: dict[str, str]) -> None:
    """The subagents the gateway tracks, each working for the session *parents* names."""
    infos = {
        key.removeprefix("subagent:"): SimpleNamespace(parent_session_key=parent, app="")
        for key, parent in parents.items()
    }
    monkeypatch.setattr(state, "subagents", SimpleNamespace(get=infos.get, count=0))


def _lessons(home: Path) -> dict[str, dict]:
    """Every live lesson by its rule."""
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    try:
        return {str(json.loads(row["value_json"])): dict(row) for row in store.get_lessons()}
    finally:
        store.close()


async def _remember(asked_from: str) -> tuple[bool, str]:
    return await _call(
        "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=asked_from
    )


@pytest.fixture(autouse=True)
def _unmarked():
    """The registry is process-wide: no mark one test makes reaches another."""
    yield
    for key in (HERE, APP_CHAT, GONE, AGENT, ITS_AGENT):
        session_restrictions.clear(key)


# ── saved, as the chat's work ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subagent_of_your_chat_saves_the_lesson_filed_under_the_chat(tmp_path, monkeypatch):
    """🔴 Red on integration: "Lesson was NOT saved: this session is not recognised"."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _working_for(gw.state, monkeypatch, {AGENT: HERE})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert saved_ok, saved
    assert saved == f"Saved lesson (global): {TAUGHT}"
    assert lessons[TAUGHT]["source"] == "user_explicit", "the owner's chat's work, as hers"
    assert lessons[TAUGHT]["source_session"] == HERE, "filed under the chat it works for"


@pytest.mark.asyncio
async def test_an_agent_a_subagent_started_files_its_lesson_under_the_chat_at_the_top(
    tmp_path, monkeypatch
):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _working_for(gw.state, monkeypatch, {ITS_AGENT: AGENT, AGENT: HERE})
        saved_ok, saved = await _remember(ITS_AGENT)
        lessons = _lessons(gw.home)

    assert saved_ok, saved
    assert lessons[TAUGHT]["source_session"] == HERE


@pytest.mark.asyncio
async def test_the_chats_own_lesson_is_still_filed_under_the_chat(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        saved_ok, saved = await _remember(HERE)
        lessons = _lessons(gw.home)

    assert saved_ok, saved
    assert lessons[TAUGHT]["source_session"] == HERE


@pytest.mark.asyncio
async def test_a_subagent_of_an_apps_conversation_saves_as_the_app_when_it_was_given_memory(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: refused as an unknown session, though the app holds the grant."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, {**DECLARED, "memory": True})
        _working_for(gw.state, monkeypatch, {AGENT: APP_CHAT})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert saved_ok, saved
    assert lessons[TAUGHT]["source"] == f"app:{APP}"
    assert lessons[TAUGHT]["source_session"] == APP_CHAT


@pytest.mark.asyncio
async def test_a_subagent_of_an_apps_conversation_without_memory_is_refused_in_its_words(
    tmp_path, monkeypatch
):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        _working_for(gw.state, monkeypatch, {AGENT: APP_CHAT})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert not saved_ok
    assert f"This work is for the app {APP}, which was not given your memory" in saved, saved
    assert TAUGHT not in lessons


# ── refused, saying why, where the chat keeps nothing ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", ["temporary", "incognito"])
async def test_a_subagent_of_a_chat_that_keeps_nothing_is_refused_and_told_why(
    tmp_path, monkeypatch, mark
):
    """🔴 Red on integration: told its session is not recognised, not that the chat keeps
    nothing. Judged by its chat, not only by its own mark: here it carries none."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        getattr(session_restrictions, f"mark_{mark}")(HERE)
        _working_for(gw.state, monkeypatch, {AGENT: HERE})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert not saved_ok
    assert saved == memory_writes.REFUSAL, "the reason every refused memory write gives"
    assert TAUGHT not in lessons


@pytest.mark.asyncio
async def test_a_subagent_of_a_live_temporary_chat_is_refused(tmp_path, monkeypatch):
    """The live chat is read first: a Temporary chat whose transcript is not written yet."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session(LIVE.removeprefix("dashboard:"), memory_mode="temporary")
        _working_for(gw.state, monkeypatch, {AGENT: LIVE})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert not saved_ok
    assert saved == memory_writes.REFUSAL, "the reason every refused memory write gives"
    assert TAUGHT not in lessons


@pytest.mark.asyncio
async def test_a_subagent_of_a_chat_nothing_records_is_refused(tmp_path, monkeypatch):
    """A chat the gateway does not hold and nothing records is one whose mode nothing can say,
    as a Temporary chat that has ended is: its work keeps nothing."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _working_for(gw.state, monkeypatch, {AGENT: GONE})
        saved_ok, saved = await _remember(AGENT)
        lessons = _lessons(gw.home)

    assert not saved_ok
    assert saved == memory_writes.REFUSAL, "the reason every refused memory write gives"
    assert TAUGHT not in lessons


# ── the other memory writes a subagent makes ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subagents_triage_rule_is_filed_under_its_chat(tmp_path, monkeypatch):
    """🔴 Red on integration: the rule was filed under the subagent's own key, which no
    transcript names, so nothing could trace it to the chat it was made for."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _working_for(gw.state, monkeypatch, {AGENT: HERE})
        added_ok, added = await _call(
            "triage_rules",
            {"action": "add", "pattern": "archive:sender:seeds.example.org", "verdict": "deny"},
            asked_from=AGENT,
        )
        store = VectorMemoryStore(db_path=gw.home / "memory.db")
        store.init()
        try:
            rows = store.db.execute(
                "SELECT source_session FROM semantic_memory WHERE key LIKE 'user.approval.%'"
            ).fetchall()
        finally:
            store.close()

    assert added_ok, added
    assert [r["source_session"] for r in rows] == [HERE]


def test_a_subagent_of_an_incognito_chat_changes_workflows_as_its_chat_may(tmp_path, monkeypatch):
    """The workflow routes' guard judges a subagent's call by the chat it works for too, and says
    why in that chat's mode: it may start a run (which keeps the chat's mode), and may not change
    a definition in your library. 🔴 Red on integration: judged by its own key alone, an agent
    that carries no mark of its chat's mode saved the definition."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.workflows.handlers import _guard

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    state = DashboardState(sessions=SimpleNamespace(count=0), start_time=0.0)
    state.get_or_create_session(HERE.removeprefix("dashboard:"), memory_mode="incognito")
    _working_for(state, monkeypatch, {AGENT: HERE})
    app = web.Application()
    app["state"] = state

    def asked(operation: str) -> web.Response | None:
        # The subagent's workflow tool: the internal credential, naming the subagent.
        named = {"X-Internal-Secret": "the-gateways-own", "X-Session-Key": AGENT}
        request = make_mocked_request("POST", "/api/workflows", headers=named, app=app)
        return _guard(request, operation)

    assert asked("workflow_run_start") is None, "its run keeps the chat's mode"
    refused = asked("workflow_def_save")
    assert refused is not None and refused.status == 403
    message = json.loads(refused.body)["error"]["message"]
    assert message == "this session cannot mutate: it keeps nothing, as an Incognito chat does"
