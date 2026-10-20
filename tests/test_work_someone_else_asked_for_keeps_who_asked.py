"""Work someone other than the owner asked for keeps who asked, for as long as it lasts.

A turn a colleague in a shared channel thread asked for changes none of the owner's memory on its
own: what its memory tools ask to change waits for her word. But the turn could start work that
outlives it, and that work counted as hers once the turn ended. A workflow run it started, a loop
it made and a callback it registered recorded nobody, so when their steps, cycles and turns later
asked to remember something, the colleague's request was saved as a lesson of hers, with the
source that outranks everything memory holds. A subagent such a turn started was held while the
turn ran, and its report, handed back to the chat after the turn had ended, ran as hers. And an
automation it made was the owner's own standing work, with the colleague's words in it.

Now such work records who asked on its own record when it is made (``lasting_work.ASKED_BY``),
and every piece of it reads that record, after the turn has ended and after a restart: a run's
steps and its own work, a loop's cycles, a callback's turn, the turn that hands a subagent's report
back. What they would change of her memory is held for her word, or refused where nobody can be
asked, exactly as the turn itself would be. An automation is not made or changed on someone else's
say-so at all, as for an Incognito chat, and a loop is not steered with the words of someone who
did not ask for it. What she asks for herself is unchanged.

Driven as the work runs: the real gateway over a scratch home, the real turn engine on a native
runtime whose scripted model calls the memory tools, those tools' calls back over the gateway's
own API, the gateway's own subagent completion delivery, and the real ``mcp-core`` process an
agent CLI runs.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (  # noqa: F401
    ABOUT_HER,
    COLLEAGUE,
    HERS,
    OWNER,
    PROVIDER,
    _a_shared_channel,
    _asks,
    _audit,
    _colleague,
    _gateway,
    _lessons,
    _settled,
    _unmarked,
)

from personalclaw import lasting_work, memory_writes, session_restrictions
from personalclaw.turn_source import arrived_on, asked_by

#: The shared thread's chat, as the door names it, and the owner's own source in it.
CHAT_NAME = "chat-50-1790750000"
CHAT = f"dashboard:{CHAT_NAME}"


def _hers() -> dict[str, str]:
    return arrived_on("1790700000.000100", OWNER, PROVIDER)


class _Turn:
    """A turn of the shared thread's chat whose message *source* sent, named as the turn engine
    names it (``turn_source.asked_by``): what it starts is its work."""

    def __init__(self, source: dict[str, str]) -> None:
        self._scope = memory_writes.derived_from(CHAT)
        self._source = source

    def __enter__(self) -> None:
        self._scope.__enter__()
        memory_writes.asked_for(asked_by(self._source))

    def __exit__(self, *exc: Any) -> None:
        self._scope.__exit__(*exc)


def _the_turn_has_ended() -> None:
    """Nothing in the process says who asked for a turn any more, as after the turn or a
    restart."""
    assert session_restrictions.asked_by(CHAT) is None
    assert session_restrictions.asked_by(CHAT_NAME) is None


async def _remember(session_key: str, rule: str) -> tuple[bool, str]:
    """What the memory tool answers a call that work running under *session_key* makes."""
    from personalclaw import mcp_core
    from personalclaw.tool_providers.registry import create_memory_provider

    token = mcp_core.set_current_session_key(session_key)
    try:
        result = await create_memory_provider().invoke(
            "memory_remember", {"rule": rule, "category": "preference"}
        )
    finally:
        mcp_core.reset_current_session_key(token)
    return bool(result.success), str(result.output if result.success else result.error)


def _held(told: str) -> None:
    assert told.startswith("Not saved yet: Jonas (U0JONASCOL) on teamchat asked for this"), told
    assert "the owner has been asked whether to keep it" in told, told


# ── a workflow run ────────────────────────────────────────────────────────────────────────────────


def _a_run(origin: str = CHAT) -> Any:
    from personalclaw.workflows import store
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    return store.create(
        WorkflowRun(
            id="",
            workflow_name="dinner-plans",
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=origin),
        )
    )


@pytest.mark.asyncio
async def test_a_run_a_colleagues_turn_started_changes_none_of_her_memory_after_it_ends(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the run's step remembered the colleague's statement as her lesson
    once the turn that started the run had ended."""
    from personalclaw.workflows.ownership import owned_key

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session(CHAT_NAME)
        with _Turn(_colleague()):
            run = _a_run()
        _the_turn_has_ended()
        ok, told = await _remember(owned_key(run.id, "book"), ABOUT_HER)
        await _settled(gw.state)
        lessons = _lessons(gw.home)
        (ask,) = _asks(gw.state, "memory_remember")

    assert ok, told
    _held(told)
    assert ABOUT_HER not in lessons
    # The owner is asked in the chat the run was started from.
    assert ask["session"] == CHAT_NAME
    assert "Jonas (U0JONASCOL) on teamchat asked the agent to remember this" in ask["tool_purpose"]


def test_a_runs_own_work_is_held_to_who_asked_wherever_it_resumes(tmp_path):
    """🔴 Red on integration: the run's own work, as a restart resumes it, wrote her lesson."""
    from personalclaw.vector_memory import VectorMemoryStore
    from personalclaw.workflows import run_start, store

    with _Turn(_colleague()):
        run = _a_run()
    _the_turn_has_ended()
    records = VectorMemoryStore(db_path=tmp_path / "memory.db")
    records.init()
    try:

        def _write() -> None:
            records.write_lesson(ABOUT_HER, source="user_explicit")

        with pytest.raises(memory_writes.MemoryWriteRefused) as refused:
            run_start.run_context(store.get(run.id)).run(_write)
        assert refused.value.code == memory_writes.ASKED_BY_SOMEONE_ELSE
        assert "Jonas (U0JONASCOL) on teamchat asked for this" in refused.value.reason
        assert list(records.get_lessons()) == []
    finally:
        records.close()


def test_a_run_started_from_a_runs_work_keeps_who_asked_for_its_parent():
    """A step's sub-run and a fork carry who asked for the run they come from."""
    from personalclaw.workflows import ownership, store

    with _Turn(_colleague()):
        parent = _a_run()
    handed = ownership.inherited_extra(store.get(parent.id))
    assert lasting_work.recorded(handed.get(lasting_work.ASKED_BY)) == _colleague()
    owners = _a_run()
    assert lasting_work.ASKED_BY not in ownership.inherited_extra(store.get(owners.id))


@pytest.mark.asyncio
async def test_the_owners_own_run_still_remembers_at_once(tmp_path, monkeypatch):
    """The control: a run her own turn started is hers, before and after the turn ends."""
    from personalclaw.workflows.ownership import owned_key

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session(CHAT_NAME)
        with _Turn(_hers()):
            run = _a_run()
        ok, told = await _remember(owned_key(run.id, "book"), HERS)
        lessons = _lessons(gw.home)
        asked = _asks(gw.state, "memory_remember")

    assert ok and told == f"Saved lesson (global): {HERS}", told
    assert lessons[HERS]["source"] == "user_explicit"
    assert asked == []


# ── a loop ────────────────────────────────────────────────────────────────────────────────────────


def _a_loop(*, attended: bool = True) -> Any:
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    return loop_store.create(
        Loop(
            id="",
            name="Dinner watch",
            kind="general",
            task="Watch the team calendar for dinners and note what everyone eats.",
            attended=attended,
        )
    )


async def _a_cycle(gw: Any, loop: Any, *, unattended: bool = False) -> str:
    """One cycle of *loop*'s worker, as the loop's scheduler starts it: its nudge in the worker's
    own chat, and a turn in which the worker's model asks to remember something."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.loop import manager as loop_manager

    worker = gw.state.get_or_create_session(name=loop_manager.session_key(loop.id), app="loop")
    worker._unattended = unattended
    nudge = "[cycle 2] Carry on with the loop's task."
    worker.append("nudge", nudge, "msg msg-nudge")
    await run_chat(gw.state, worker, nudge)
    await _settled(gw.state)
    return gw.agent.told()


@pytest.mark.asyncio
async def test_a_loop_a_colleagues_turn_made_changes_none_of_her_memory_in_its_cycles(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the loop's cycle, its turn started by the loop's own nudge, saved
    the colleague's statement as her lesson."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        with _Turn(_colleague()):
            loop = _a_loop()
        _the_turn_has_ended()
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        told = await _a_cycle(gw, loop)
        lessons = _lessons(gw.home)
        (ask,) = _asks(gw.state, "memory_remember")

    _held(told)
    assert ABOUT_HER not in lessons
    assert ask["session"] == f"loop-{loop.id}", "her card is the loop's"


@pytest.mark.asyncio
async def test_an_unattended_loops_cycle_refuses_the_change_with_nobody_to_ask(
    tmp_path, monkeypatch
):
    """Nobody watches an Unattended loop, so nobody can be asked: nothing is written, saying so."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        with _Turn(_colleague()):
            loop = _a_loop(attended=False)
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        told = await _a_cycle(gw, loop, unattended=True)
        lessons = _lessons(gw.home)
        asked = _asks(gw.state, "memory_remember")

    assert told.startswith(
        "Error: Nothing was written to the owner's memory: Jonas (U0JONASCOL) on teamchat asked"
    ), told
    assert "it acts on its own, with nobody there to answer" in told, told
    assert ABOUT_HER not in lessons and asked == []


@pytest.mark.asyncio
async def test_the_owners_own_loop_still_remembers_at_once(tmp_path, monkeypatch):
    """The control: a loop her own turn made is hers in every cycle."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        with _Turn(_hers()):
            loop = _a_loop()
        gw.agent.calls.append(("memory_remember", {"rule": HERS, "category": "preference"}))
        told = await _a_cycle(gw, loop)
        lessons = _lessons(gw.home)

    assert told == f"Saved lesson (global): {HERS}", told
    assert lessons[HERS]["source"] == "user_explicit"


def test_a_loop_is_steered_only_with_the_words_of_whoever_asked_for_it():
    """A colleague's words do not go into the owner's loop; the colleague's own loop takes them."""
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import manager as loop_manager

    hers = _a_loop()
    with _Turn(_colleague()):
        theirs = _a_loop()

    class _Scheduler:
        def get_by_session(self, _key: str) -> None:
            return None

    def _steer(loop_id: str, text: str) -> Any:
        return asyncio.run(loop_manager.nudge(None, _Scheduler(), loop_id, text))

    with _Turn(_colleague()):
        with pytest.raises(lasting_work.Refused) as refused:
            _steer(hers.id, ABOUT_HER)
        _steer(theirs.id, "Also note the dessert orders.")

    assert refused.value.code == memory_writes.ASKED_BY_SOMEONE_ELSE
    assert str(refused.value).startswith(
        "Nothing was sent to it: Jonas (U0JONASCOL) on teamchat asked for this"
    ), str(refused.value)
    assert loop_files.read_guidance(hers.id) == ""
    assert "dessert" in loop_files.read_guidance(theirs.id)


def test_the_loops_end_teaches_nothing_from_a_colleagues_loop(monkeypatch):
    """🔴 Red on integration: the loop-end learner took the colleague's loop's outcome as hers."""
    from personalclaw.learning import loop_end
    from personalclaw.loop.watchdog import LoopWatchdog

    captured: list[str] = []
    monkeypatch.setattr(loop_end, "capture", lambda loop, _svc: captured.append(loop.id))
    watchdog = LoopWatchdog.__new__(LoopWatchdog)
    monkeypatch.setattr(watchdog, "_memory_service", lambda: None, raising=False)
    with _Turn(_colleague()):
        theirs = _a_loop()
    hers = _a_loop()

    watchdog._capture_loop_end(theirs.id)
    watchdog._capture_loop_end(hers.id)

    assert captured == [hers.id]


# ── the turn that hands a subagent's report back ──────────────────────────────────────────────────


def _completion(state: Any):
    """The gateway's own subagent completion delivery, into *state*."""
    from test_gateway import _make_orchestrator, _mock_sessions

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = state
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
        orch._init_subagents()
    return manager.call_args[1]["on_done"]


def _an_agent(agent_id: str) -> Any:
    from personalclaw.subagent import SubagentInfo

    return SubagentInfo(
        id=agent_id,
        task="Find a dinner place near the office for Thursday.",
        done=True,
        result="Booked a table at the corner bistro for seven.",
        parent_session_key=CHAT,
    )


@pytest.mark.asyncio
async def test_a_colleagues_subagents_report_handed_back_keeps_who_asked(tmp_path, monkeypatch):
    """🔴 Red on integration: the turn that handed the colleague's subagent's report back to the
    chat, after the colleague's turn had ended, ran as the owner's and saved her lesson."""
    from personalclaw.subagent import agent_work_id

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session(CHAT_NAME)
        with _Turn(_colleague()):
            memory_writes.hand_on(agent_work_id("5ab1e7c1"), CHAT)
        _the_turn_has_ended()
        on_done = _completion(gw.state)
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        await on_done([_an_agent("5ab1e7c1")])
        await _settled(gw.state)
        told = gw.agent.told()
        lessons = _lessons(gw.home)

    _held(told)
    assert ABOUT_HER not in lessons


@pytest.mark.asyncio
async def test_a_report_that_waited_behind_a_turn_keeps_who_asked(tmp_path, monkeypatch):
    """🔴 Red on integration: a report queued while the chat was busy ran, once the turn ahead of
    it ended, as the owner's."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.turn_source import DASHBOARD_SOURCE

    report = "[Subagent completion event]\nAgent `5ab1e7c2` completed\nTask: dinner\n\nBooked."
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = gw.state.get_or_create_session(CHAT_NAME)
        chat.queue_append(report, asked_for_by=_colleague())
        gw.agent.calls.append([])
        gw.agent.calls.append(("memory_remember", {"rule": ABOUT_HER, "category": "preference"}))
        chat.append("user", "What is on for Thursday?", "msg msg-u", source=DASHBOARD_SOURCE)
        await run_chat(gw.state, chat, "What is on for Thursday?")
        await _settled(gw.state)
        told = gw.agent.told()
        lessons = _lessons(gw.home)

    _held(told)
    assert ABOUT_HER not in lessons


@pytest.mark.asyncio
async def test_the_owners_own_subagents_report_still_remembers_at_once(tmp_path, monkeypatch):
    """The control: a subagent her own turn started reports back as hers."""
    from personalclaw.subagent import agent_work_id

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.state.get_or_create_session(CHAT_NAME)
        with _Turn(_hers()):
            memory_writes.hand_on(agent_work_id("5ab1e7c3"), CHAT)
        on_done = _completion(gw.state)
        gw.agent.calls.append(("memory_remember", {"rule": HERS, "category": "preference"}))
        await on_done([_an_agent("5ab1e7c3")])
        await _settled(gw.state)
        told = gw.agent.told()

    assert told == f"Saved lesson (global): {HERS}", told


# ── an automation ─────────────────────────────────────────────────────────────────────────────────


def test_an_automation_is_not_made_or_changed_on_someone_elses_word(tmp_path):
    """🔴 Red on integration: the colleague's turn saved an automation, which runs later as the
    owner's own standing work, and changed one of hers."""
    from personalclaw.triggers import tools as trigger_tools
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    saved = tmp_path / "triggers.json"
    mine = trigger_tools.create(
        store, name="balcony plants", when="in 3 hours", message="Water them.", created_by="user"
    )
    assert mine.ok, mine.text
    kept = saved.read_text(encoding="utf-8")
    with _Turn(_colleague()):
        with pytest.raises(lasting_work.Refused) as made:
            trigger_tools.create(store, name="seafood", when="every day at 9am", message=ABOUT_HER)
        with pytest.raises(lasting_work.Refused) as changed:
            trigger_tools.update(
                store, trigger_id=str(mine.data["trigger"]["id"]), patch={"name": ABOUT_HER}
            )
    untouched = saved.read_text(encoding="utf-8")
    with _Turn(_hers()):
        hers = trigger_tools.create(store, name="train", when="in 2 hours", message=HERS)

    for refused, opening in ((made, "Nothing was set up"), (changed, "Nothing was changed")):
        assert refused.value.code == memory_writes.ASKED_BY_SOMEONE_ELSE
        assert str(refused.value).startswith(
            f"{opening}: Jonas (U0JONASCOL) on teamchat asked for this"
        ), str(refused.value)
        assert "Triggers page" in str(refused.value)
    assert untouched == kept, "nothing was saved or changed on someone else's say-so"
    assert hers.ok, hers.text


# ── a callback ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_callback_a_colleagues_turn_registered_keeps_who_asked(tmp_path, monkeypatch):
    """🔴 Red on integration: the callback recorded nobody, so its turn's requests changed her
    memory as hers, and its Allow did not say whose it was."""
    from personalclaw import webhook_callbacks

    async with _gateway(tmp_path, monkeypatch) as gw:
        with _Turn(_colleague()):
            webhook_callbacks.register("dinner:rsvp", "Tell the thread who replied.")
        with _Turn(_hers()):
            webhook_callbacks.register("train:delay", "Tell me if the train is late.")
        _the_turn_has_ended()
        theirs = webhook_callbacks.get("dinner:rsvp")
        ok, told = await _remember(theirs.session_key, ABOUT_HER)
        await _settled(gw.state)
        hers = webhook_callbacks.get("train:delay")
        her_ok, her_told = await _remember(hers.session_key, HERS)
        lessons = _lessons(gw.home)

    assert ok, told
    _held(told)
    assert ABOUT_HER not in lessons
    assert her_ok and her_told == f"Saved lesson (global): {HERS}", her_told
    assert "registered in a turn Jonas (U0JONASCOL) on teamchat asked for" in (
        webhook_callbacks.consent(theirs)
    )
    assert "asked for" not in webhook_callbacks.consent(hers)


@pytest.mark.asyncio
async def test_a_callbacks_turn_runs_as_asked_for_by_whoever_registered_it(monkeypatch):
    """🔴 Red on integration: the turn an outside system's call starts ran as nobody's work, so
    everything in it was the owner's. It runs as the callback's own work now, held to its record."""
    from unittest.mock import AsyncMock

    from personalclaw import webhook_callbacks
    from personalclaw.dashboard.handlers import hooks

    seen: dict[str, dict[str, str]] = {}

    async def _its_turn(_state: Any, key: str, _message: str, _agent: Any) -> str:
        seen[key] = memory_writes.asker()
        return ""

    monkeypatch.setattr(hooks, "_run_hook_inner", _its_turn)
    with _Turn(_colleague()):
        webhook_callbacks.register("dinner:rsvp", "Tell the thread who replied.")
    with _Turn(_hers()):
        webhook_callbacks.register("train:delay", "Tell me if the train is late.")
    sessions = MagicMock(reset=AsyncMock(), record_failure=AsyncMock())
    state = MagicMock(sessions=sessions)
    for callback_id in ("dinner:rsvp", "train:delay"):
        callback = webhook_callbacks.get(callback_id)
        await hooks._hook_semaphore.acquire()
        await hooks._run_hook_agent(
            state,
            callback.session_key,
            "Ana replied yes.",
            "RSVP",
            None,
            False,
            60,
            webhook_callbacks.restored_context(callback).text,
        )

    assert seen == {"hook:dinner:rsvp": _colleague(), "hook:train:delay": {}}


# ── an agent CLI's tool server ────────────────────────────────────────────────────────────────────


def test_the_tool_server_an_agent_cli_runs_asks_the_gateway_who_asked(tmp_path, monkeypatch):
    """🔴 Red on integration: the tool server, which holds no turn, took every turn for the
    owner's, so a callback an agent CLI's colleague turn registered recorded nobody and its
    automation was made. It asks the gateway, which runs the turn."""
    from personalclaw import mcp_automation, mcp_core, webhook_callbacks

    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", CHAT)
    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
    asked: list[str] = []

    def _gateway_answers(path: str, *_a: Any, **_kw: Any) -> dict[str, Any]:
        asked.append(path)
        return {"memory_mode": "persistent", "asked_by": _colleague()}

    monkeypatch.setattr(mcp_core, "_get", _gateway_answers)

    refused = mcp_automation._preflight(
        "automation_create", {"name": "seafood", "when": "in 2 hours", "message": ABOUT_HER}
    )
    webhook_callbacks.register("dinner:rsvp", "Tell the thread who replied.")
    recorded = webhook_callbacks.get("dinner:rsvp")

    assert refused is not None, "the tool server made the automation"
    assert str(refused).startswith(
        f"Error [{memory_writes.ASKED_BY_SOMEONE_ELSE}]: Nothing was set up: Jonas (U0JONASCOL) "
        "on teamchat asked for this"
    ), str(refused)
    assert lasting_work.recorded(recorded.asked_by) == _colleague()
    assert set(asked) == {"/api/chat/sessions/model-reach"}


def test_a_gateway_that_does_not_answer_is_not_taken_for_the_owner(monkeypatch):
    """Fail closed: a tool server that cannot hear who asked records someone no record names."""
    from personalclaw import mcp_core

    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
    monkeypatch.setattr(mcp_core, "_get", lambda *_a, **_kw: {"error": "unreachable"})

    someone = memory_writes.asker()

    assert someone, "nothing says the owner asked"
    assert lasting_work.recorded(someone) == someone


# ── what a record can say ─────────────────────────────────────────────────────────────────────────


def test_a_record_that_cannot_be_read_is_not_taken_for_the_owners():
    """The owner's own work records nobody. A record that is there and cannot be read names
    someone no record names, so its work is held as someone else's."""
    assert lasting_work.recorded(None) == {}
    assert lasting_work.recorded({}) == {}
    assert lasting_work.recorded(_colleague()) == _colleague()
    for garbled in ("Jonas", ["x"], {"source_user": 7}, {"note": "x"}):
        assert lasting_work.recorded(garbled), garbled
