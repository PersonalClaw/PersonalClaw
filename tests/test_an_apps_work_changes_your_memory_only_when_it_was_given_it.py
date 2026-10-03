"""An app's work changes your memory only when the app was given it, and then writes as the app.

The memory permission an app declares is one grant, and its install consent says what it grants:
"Read and change your memory". Reading was held to it; changing was not. A conversation an app
started still saved the lessons its agent was told to remember, forgot yours, added triage rules,
taught the after-turn review its corrections and preferences, and was consolidated into your
lessons, facts and episodes, each written as though you had said it: a lesson the agent saved was
stored as ``user_explicit``, the source that outranks every other. And an app's scheduled job
started its agent with no app attached, so the agent read your memory as your own work and the
job's announce turn was assembled with it.

Driven as the work drives it: the real gateway asked over its own API by the memory tools'
provider the native agent calls (bound to the session it is called from), the real turn engine on
a scripted model, the consolidator over a real transcript with its model call answered here, the
invoke-agent action a job's fire runs, and the gateway's own announce of a finished agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_memory_is_read_only_where_its_work_may_read_it import (
    APP,
    APP_CHAT,
    DECLARED,
    HERE,
    LESSON,
    _builder,
    _call,
    _chat,
    _gateway,
    _install,
    _remember,
    _turns,
)

from personalclaw import memory_writes, session_restrictions
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.vector_memory import VectorMemoryStore

#: What the app's manifest needs to hold your memory.
GRANTED = {**DECLARED, "memory": True}
#: What an app's work writes as.
AS_THE_APP = f"app:{APP}"
#: A rule the app's conversation is asked to remember.
TAUGHT = "Water plot 14 before nine, while the shed is still open."
#: The app's scheduled job, by the id it is registered under.
JOB = f"app:{APP}:water-rota"
#: The job's own session, where its announce turn runs.
JOB_SESSION = f"cron:{JOB}"
#: A conversation the app started as an Incognito chat.
APP_INCOGNITO = "dashboard:chat-21-1790600100"


def _not_given_it(text: str) -> None:
    """The refusal of a change, as the work is told it."""
    assert f"This work is for the app {APP}, which was not given your memory" in text, text
    assert "nothing is saved to it or removed from it" in text, text


def _stored(home: Path) -> VectorMemoryStore:
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    return store


def _lessons(home: Path) -> dict[str, dict]:
    """Every live lesson by its rule."""
    store = _stored(home)
    try:
        return {str(json.loads(row["value_json"])): dict(row) for row in store.get_lessons()}
    finally:
        store.close()


def _rows(home: Path, sql: str) -> list[dict]:
    store = _stored(home)
    try:
        return [dict(row) for row in store.db.execute(sql).fetchall()]
    finally:
        store.close()


@pytest.fixture(autouse=True)
def _unmarked():
    """The registry is process-wide: no mark one test makes reaches another."""
    yield
    for key in (HERE, APP_CHAT, APP_INCOGNITO, JOB_SESSION):
        session_restrictions.clear(key)


# ── what the app's conversation saves, forgets and rules through its tools ─────────────────


@pytest.mark.asyncio
async def test_an_apps_conversation_without_the_permission_changes_no_lesson_and_is_told_why(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the lesson was saved as yours, and your lesson was forgotten."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        saved_ok, saved = await _call(
            "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=APP_CHAT
        )
        forgot_ok, forgot = await _call("memory_forget", {"query": "plot 14"}, asked_from=APP_CHAT)
        lessons = _lessons(gw.home)

    assert not saved_ok and not forgot_ok
    _not_given_it(saved)
    _not_given_it(forgot)
    assert TAUGHT not in lessons
    assert LESSON in lessons, "her own lesson is still there"


@pytest.mark.asyncio
async def test_an_apps_conversation_with_the_permission_saves_and_forgets_as_the_app(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the lesson was stored as ``user_explicit``, yours."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, GRANTED)
        saved_ok, saved = await _call(
            "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=APP_CHAT
        )
        lessons = _lessons(gw.home)
        forgot_ok, _forgot = await _call(
            "memory_forget", {"query": "balcony boxes"}, asked_from=APP_CHAT
        )
        after = _lessons(gw.home)
        deleted = _rows(
            gw.home,
            "SELECT source FROM memory_events WHERE event_type = 'delete' ORDER BY id DESC",
        )

    assert saved_ok, saved
    assert lessons[TAUGHT]["source"] == AS_THE_APP
    assert lessons[TAUGHT]["source_session"] == APP_CHAT
    assert forgot_ok
    assert LESSON not in after
    assert deleted and deleted[0]["source"] == AS_THE_APP


@pytest.mark.asyncio
async def test_an_apps_incognito_conversation_changes_nothing_even_with_the_permission(
    tmp_path, monkeypatch
):
    """The app's grant and the chat's mode are two questions, and a change needs both: an
    Incognito chat the app started keeps nothing, whatever the app was given."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, GRANTED)
        _chat(gw.home, APP_INCOGNITO, "Plan the rota.", app=APP, mode="incognito")
        saved_ok, saved = await _call(
            "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=APP_INCOGNITO
        )
        lessons = _lessons(gw.home)

    assert not saved_ok
    assert memory_writes.REFUSAL in saved, saved
    assert TAUGHT not in lessons


@pytest.mark.asyncio
async def test_an_apps_conversation_without_the_permission_adds_and_reads_no_triage_rule(
    tmp_path, monkeypatch
):
    """The triage rules are memory too (``user.approval.*``), added and listed by the agent."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        added_ok, added = await _call(
            "triage_rules",
            {"action": "add", "pattern": "archive:sender:rota.example.org", "verdict": "deny"},
            asked_from=APP_CHAT,
        )
        listed_ok, listed = await _call("triage_rules_list", {}, asked_from=APP_CHAT)
        rules = _rows(gw.home, "SELECT key FROM semantic_memory WHERE key LIKE 'user.approval.%'")

    assert not added_ok
    _not_given_it(added)
    assert rules == []
    assert listed_ok, listed
    assert listed.startswith(f"This work is for the app {APP}, which was not given your memory")


@pytest.mark.asyncio
async def test_your_own_chat_still_saves_its_lessons_as_yours(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        saved_ok, saved = await _call(
            "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=HERE
        )
        lessons = _lessons(gw.home)

    assert saved_ok, saved
    assert lessons[TAUGHT]["source"] == "user_explicit"


@pytest.mark.asyncio
async def test_an_apps_own_request_with_the_permission_writes_as_the_app(tmp_path, monkeypatch):
    """The app's own token, the way the app's page sends it (`X-Session-Key: dashboard:ui`)."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.schedule import api_lessons_create
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def as_the_app(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        request["app"] = APP
        return await handler(request)

    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, {"api": ["/api/lessons"], "memory": True})
        app = web.Application(
            middlewares=[as_the_app, app_permission_middleware, memory_write_middleware()]
        )
        app["state"] = gw.state
        app.router.add_post("/api/lessons", api_lessons_create)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/lessons",
                json={"rule": TAUGHT, "category": "knowledge"},
                headers={"X-Session-Key": "dashboard:ui"},
            )
            body = await resp.json()
        lessons = _lessons(gw.home)

    assert resp.status == 200, body
    assert lessons[TAUGHT]["source"] == AS_THE_APP


# ── the memory store itself ─────────────────────────────────────────────────────────────────


def test_the_memory_store_refuses_an_apps_change_wherever_it_is_made(tmp_path, monkeypatch):
    """The stores keep the promise, not each caller: a write made in an app's work that was not
    given your memory is refused in the app's words, by whatever path it comes."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _remember(tmp_path)
    _install(tmp_path, APP, DECLARED)
    store = _stored(tmp_path)
    try:
        with memory_writes.derived_from(APP_CHAT, app=APP):
            with pytest.raises(memory_writes.MemoryWriteRefused) as refused:
                store.set_semantic("user.rota_day", "Sunday", 1.0, "user_explicit")
        assert refused.value.reason.startswith(f"This work is for the app {APP}")
        assert store.get_semantic("user.rota_day") is None
    finally:
        store.close()


def test_an_apps_write_never_reads_as_yours(tmp_path, monkeypatch):
    """Given your memory, the app's write is the app's, whatever source it names: it cannot take
    the place of a fact you set, and what it sets is not taken for yours."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _install(tmp_path, APP, GRANTED)
    store = _stored(tmp_path)
    store._graph_enabled = False
    try:
        assert store.set_semantic("user.rota_day", "Sunday", 1.0, "user_explicit") is None
        with memory_writes.derived_from(APP_CHAT, app=APP):
            conflict = store.set_semantic("user.rota_day", "Monday", 1.0, "user_explicit")
            assert store.set_semantic("user.shed_code", "garden", 1.0, "user_explicit") is None
        mine = store.get_semantic("user.rota_day")
        theirs = store.get_semantic("user.shed_code")
    finally:
        store.close()

    assert conflict is not None, "the app's write did not replace the fact you set"
    assert json.loads(mine["value_json"]) == "Sunday" and mine["source"] == "user_explicit"
    assert theirs["source"] == AS_THE_APP


# ── after-turn learning ─────────────────────────────────────────────────────────────────────


def _staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from personalclaw.learning import staging

    staging.reset_store()
    store = staging.StagingStore(tmp_path / "staging")
    monkeypatch.setattr(staging, "get_store", lambda *a, **k: store)
    return store


def _denials(store) -> list[str]:
    from personalclaw.learning.staging import FlushOutcome

    with store._cursor() as cur:
        rows = cur.execute(
            "SELECT detail FROM flush_records WHERE outcome = ?",
            (FlushOutcome.FLUSH_SKIPPED.value,),
        ).fetchall()
    return [str(r[0]) for r in rows]


async def _say(state, name: str, text: str, *, app: str = "") -> None:
    """One turn of the real turn engine in the chat *name*, saying *text*."""
    from personalclaw.dashboard.chat_runner import run_chat

    session = state.get_or_create_session(name, created_by_app=app)
    session.append("user", text, "msg msg-u")
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch(
            "personalclaw.after_turn_review.run_skill_ladder_review",
            new=AsyncMock(return_value=None),
        ),
    ):
        await run_chat(state, session, text)


#: A correction (it opens with "Never") that is also a standing veto: a lesson either way.
CORRECTION = "Never water the beans after six."
#: A standing style preference: a preference fact.
PREFERENCE = "I prefer short answers about the allotment."


@pytest.mark.asyncio
async def test_an_apps_conversation_without_the_permission_learns_nothing_after_its_turns(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: its correction became a lesson and its preference a fact of yours."""
    staging = _staging(tmp_path, monkeypatch)
    async with _turns(tmp_path, monkeypatch) as (state, _model, home):
        _install(home, APP, DECLARED)
        await _say(state, "chat-20-1790600000", CORRECTION, app=APP)
        await _say(state, "chat-20-1790600000", PREFERENCE, app=APP)
        lessons = _lessons(home)
        facts = _rows(home, "SELECT key FROM semantic_memory WHERE key LIKE 'pref.facet.%'")

    assert set(lessons) == {LESSON}, lessons
    assert facts == []
    assert any("app_without_memory" in detail for detail in _denials(staging))


@pytest.mark.asyncio
async def test_an_apps_conversation_with_the_permission_learns_as_the_app(tmp_path, monkeypatch):
    _staging(tmp_path, monkeypatch)
    async with _turns(tmp_path, monkeypatch) as (state, _model, home):
        _install(home, APP, GRANTED)
        await _say(state, "chat-20-1790600000", CORRECTION, app=APP)
        await _say(state, "chat-20-1790600000", PREFERENCE, app=APP)
        lessons = _lessons(home)
        facts = _rows(home, "SELECT key, source FROM semantic_memory WHERE key LIKE 'pref.facet.%'")

    learned = {rule: row for rule, row in lessons.items() if rule != LESSON}
    assert learned and all(row["source"] == AS_THE_APP for row in learned.values()), learned
    assert facts and all(row["source"] == AS_THE_APP for row in facts), facts


@pytest.mark.asyncio
async def test_your_own_chat_still_learns_as_yours(tmp_path, monkeypatch):
    _staging(tmp_path, monkeypatch)
    async with _turns(tmp_path, monkeypatch) as (state, _model, home):
        await _say(state, "chat-22-1790600200", CORRECTION)
        lessons = _lessons(home)

    learned = {rule: row for rule, row in lessons.items() if rule != LESSON}
    assert learned and all(row["source"] != AS_THE_APP for row in learned.values()), learned


# ── consolidation ───────────────────────────────────────────────────────────────────────────

#: What the consolidation model answers for the app's conversation.
CONSOLIDATED = {
    "history_entry": "Planned the watering rota for plot 14 with the allotment planner.",
    "lessons": [{"rule": "Water plot 14 on Sunday mornings.", "category": "knowledge"}],
    "episodic": [{"text": "Drew up the plot 14 watering rota for the summer.", "importance": 0.6}],
}


def _consolidator(home: Path) -> tuple[HistoryConsolidator, VectorMemoryStore, AsyncMock]:
    from personalclaw.memory import MemoryStore

    markdown = MemoryStore(workspace=home / "workspace")
    markdown.init()
    store = VectorMemoryStore(db_path=home / "memory.db", confidence_threshold=0.0)
    store.init()
    store._graph_enabled = False
    markdown.vector_store = store
    consolidator = HistoryConsolidator(
        ConversationLog(base_dir=home / "sessions"), markdown, vector_store=store
    )
    asked = AsyncMock(return_value=CONSOLIDATED)
    consolidator._call_llm = asked  # type: ignore[method-assign]
    return consolidator, store, asked


@pytest.mark.asyncio
async def test_an_apps_conversation_without_the_permission_is_not_consolidated(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: its model was asked, and its lessons and episodes became yours."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _install(tmp_path, APP, DECLARED)
    _chat(tmp_path, APP_CHAT, "Plan the watering rota for plot 14.", app=APP)
    consolidator, store, asked = _consolidator(tmp_path)
    try:
        ran = await consolidator.consolidate_now(APP_CHAT)
        ended = await consolidator.consolidate_session(APP_CHAT)
        lessons = store.get_lessons()
        episodes = store.get_episodic_list()
        assert ran is False and ended is False
        asked.assert_not_called()
        assert lessons == [] and episodes == []
        _not_given_it(consolidator.why_nothing_is_kept(APP_CHAT))
    finally:
        store.close()


@pytest.mark.asyncio
async def test_an_apps_conversation_with_the_permission_is_consolidated_as_the_app(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _install(tmp_path, APP, GRANTED)
    _chat(tmp_path, APP_CHAT, "Plan the watering rota for plot 14.", app=APP)
    consolidator, store, asked = _consolidator(tmp_path)
    try:
        ran = await consolidator.consolidate_now(APP_CHAT)
        lessons = store.get_lessons()
        episodes = store.get_episodic_list()
        events = [
            dict(row)
            for row in store.db.execute(
                "SELECT source FROM memory_events WHERE event_type = 'create'"
            ).fetchall()
        ]
    finally:
        store.close()

    assert ran is True
    asked.assert_called_once()
    assert [row["source"] for row in lessons] == [AS_THE_APP]
    assert len(episodes) == 1
    assert events and all(row["source"] == AS_THE_APP for row in events), events


# ── an app's scheduled job ──────────────────────────────────────────────────────────────────


def test_an_apps_job_is_named_by_the_id_its_work_carries():
    from personalclaw.apps.app_crons import app_of, job_id

    assert job_id(APP, "water-rota") == JOB
    assert app_of(JOB) == APP
    for yours in ("clock:daily-digest", "app:", f"app:{APP}", "lifecycle:app:x:y", ""):
        assert app_of(yours) == "", yours


@pytest.mark.asyncio
async def test_the_agent_an_apps_job_starts_recalls_nothing_without_the_permission(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the job's agent recalled her lesson, as her own work.

    The job's fire runs the invoke-agent action the app's cron is registered with; the agent it
    starts carries the job's id, and every check of its work finds the app by it."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.subagent import SubagentManager

    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, DECLARED)
        sessions = MagicMock(count=0)
        sessions.get_or_create = AsyncMock(side_effect=RuntimeError("the run is not this test's"))
        sessions.reset = AsyncMock()
        ctx = MagicMock()
        ctx.hooks.auto_approve_subagent_spawn = True
        manager = SubagentManager(sessions=sessions, ctx_builder=ctx)
        # The host's free memory is not what this test is about.
        monkeypatch.setattr(
            "personalclaw.subagent.check_memory_available", lambda min_gb: (True, 64.0)
        )
        monkeypatch.setattr(
            invoke_agent_provider,
            "get_action_services",
            lambda: SimpleNamespace(subagents=manager),
        )
        monkeypatch.setattr(gw.state, "subagents", manager)
        try:
            fired = await invoke_agent_provider.InvokeAgentActionProvider().execute(
                {"task_template": "Draft this week's watering rota.", "approval_mode": "auto"},
                ActionContext(event="trigger.fired", trigger_id=JOB),
            )
            assert fired.success, fired.error
            agent = str(fired.work_id)
            recalled_ok, recalled = await _call(
                "memory_recall", {"query": "allotment"}, asked_from=agent
            )
            saved_ok, saved = await _call(
                "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=agent
            )
        finally:
            await manager.cancel_all()
        lessons = _lessons(gw.home)

    assert agent.startswith("subagent:")
    assert recalled_ok, recalled
    assert LESSON not in recalled
    assert recalled.startswith(f"This work is for the app {APP}, which was not given your memory")
    assert not saved_ok
    _not_given_it(saved)
    assert TAUGHT not in lessons


@pytest.mark.asyncio
async def test_the_agent_an_apps_job_starts_recalls_with_the_permission(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _install(gw.home, APP, GRANTED)
        info = SimpleNamespace(parent_session_key="", app="", trigger_id=JOB)
        monkeypatch.setattr(gw.state, "subagents", SimpleNamespace(get=lambda _id: info, count=0))
        recalled_ok, recalled = await _call(
            "memory_recall", {"query": "allotment"}, asked_from="subagent:a1b2c3d4"
        )

    assert recalled_ok, recalled
    assert LESSON in recalled


def _announcer(home: Path):
    """The gateway's own announce of a finished agent, over a context builder with her memory."""
    from test_gateway import _make_orchestrator, _mock_dashboard_state, _mock_sessions

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = _mock_dashboard_state()
    # No conversation of an app's is open here (a mock would answer an app for every key).
    orch.dashboard_state.session_creating_app = MagicMock(return_value="")
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        with patch("personalclaw.gateway.SubagentManager") as manager:
            instance = MagicMock()
            instance.running = []
            instance.running_agents_for = MagicMock(return_value=[])
            instance.get = MagicMock(return_value=None)
            manager.return_value = instance
            orch._init_subagents()
    builder, store = _builder(home)
    orch.ctx_builder.build_message = builder.build_message
    return orch, manager.call_args[1]["on_done"], store


def _finished(parent: str) -> MagicMock:
    info = MagicMock(title="", trigger_id="", declined=False)
    info.id = "a1b2c3d4"
    info.parent_session_key = parent
    info.error = None
    info.result = "The rota is drafted."
    info.result_path = ""
    info.task = "Draft this week's watering rota."
    info.agent = ""
    info.silent = False
    info.elapsed = 1.0
    info.started = 0.0
    return info


async def _announce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent: str) -> str:
    """What the announce turn of an agent that worked for *parent* hands its model."""
    home = tmp_path / "data"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _remember(home)
    handed: list[str] = []

    async def _model(client, msg, **_kw):
        handed.append(msg)
        return "Noted."

    orch, on_done, store = _announcer(home)
    try:
        with (
            patch("personalclaw.gateway.stream_and_collect", new=_model),
            patch("personalclaw.context_headroom.resolve_window", new=AsyncMock(return_value=None)),
        ):
            await on_done([_finished(parent)])
    finally:
        store.close()
    assert len(handed) == 1, handed
    return handed[0]


@pytest.mark.asyncio
async def test_an_apps_job_announce_is_assembled_without_your_memory(tmp_path, monkeypatch):
    """🔴 Red on integration: the job's announce turn was handed her lesson."""
    _install(tmp_path / "data", APP, DECLARED)
    handed = await _announce(tmp_path, monkeypatch, JOB_SESSION)

    assert "The rota is drafted." in handed
    assert LESSON not in handed


@pytest.mark.asyncio
async def test_an_apps_job_announce_with_the_permission_reads_your_memory(tmp_path, monkeypatch):
    _install(tmp_path / "data", APP, GRANTED)
    handed = await _announce(tmp_path, monkeypatch, JOB_SESSION)

    assert LESSON in handed


@pytest.mark.asyncio
async def test_your_own_jobs_announce_still_reads_your_memory(tmp_path, monkeypatch):
    _install(tmp_path / "data", APP, DECLARED)
    handed = await _announce(tmp_path, monkeypatch, "cron:clock:daily-digest")

    assert LESSON in handed


# ── a workflow run an app's work started ────────────────────────────────────────────────────


def _ended_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permissions: dict, origin):
    """The run-end learner over a finished run started from *origin*, as the controller calls
    it: what the work it ran wrote as, or ``None`` when it did not run."""
    from personalclaw.workflows import run_finish
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _install(tmp_path, APP, permissions)
    taught: list[str] = []

    def _capture(run, service, *, journal=None):
        taught.append("")  # it ran, whatever it is asked next
        taught[-1] = memory_writes.written_by("run_end")
        return {}

    run = WorkflowRun(id="r-rota", workflow_name="water-rota", status=RunStatus.COMPLETE)
    run.origin = origin
    ctl = SimpleNamespace(
        run=run, services=SimpleNamespace(memory=SimpleNamespace(has_vector=True))
    )
    with patch("personalclaw.learning.run_end.capture", new=_capture):
        run_finish.capture_run_end(ctl)  # type: ignore[arg-type]
    return taught[0] if taught else None  # "" when it ran and could not say as whom


def test_a_run_an_apps_job_started_teaches_nothing_without_the_permission(tmp_path, monkeypatch):
    """🔴 Red on integration: the run's lessons and priors were learned as yours."""
    from personalclaw.workflows.models import OriginKind, RunOrigin

    origin = RunOrigin(kind=OriginKind.HOOK, trigger_id=JOB)
    assert _ended_run(tmp_path, monkeypatch, DECLARED, origin) is None


def test_a_run_an_apps_job_started_teaches_as_the_app_with_the_permission(tmp_path, monkeypatch):
    from personalclaw.workflows.models import OriginKind, RunOrigin

    origin = RunOrigin(kind=OriginKind.HOOK, trigger_id=JOB)
    assert _ended_run(tmp_path, monkeypatch, GRANTED, origin) == AS_THE_APP


def test_your_own_run_still_teaches_as_itself(tmp_path, monkeypatch):
    from personalclaw.workflows.models import OriginKind, RunOrigin

    origin = RunOrigin(kind=OriginKind.HOOK, trigger_id="clock:daily-digest")
    assert _ended_run(tmp_path, monkeypatch, DECLARED, origin) == "run_end"
