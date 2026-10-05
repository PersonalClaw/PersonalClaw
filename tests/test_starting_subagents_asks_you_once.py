"""Starting subagents asks you once: one task's start, or one ask for a whole batch.

`subagent_run` asked about one thing twice. The call asked, since it is no read, and then what it
started asked again: one task's start asked as its own approval, a batch with a task that may
change things asked once more for that, and a batch that only reads started its run at once and
then asked to start each of its tasks (two asks for a batch of two, seen live), while its card in
the chat said "Running 0/2" and the agent guessed why.

Now the call declares that what it starts asks for itself (`WORK_ASKS_META_KEY`), so the call
asks nobody, on PersonalClaw's own runtime and over an agent CLI alike, and a batch asks once:

* a batch that only reads asks as one subagent's start asks, through the gateway's own start
  relay, listed under its chat; the grants that start that chat's subagents start it without
  asking (its Trust, YOLO), and a Trust switch answers its ask;
* a batch with a task that may change things asks for her own Allow alone, as before;
* what allowed it is on its run's record, and each task starts on it, asking nobody again;
* a batch waiting for its answer survives a restart: the gateway that comes back asks it again,
  or says nobody answered in time once its window has passed; the Inbox names it as the batch it
  is, and its card reads how it stands;
* two batches started in the same millisecond have two names.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from test_a_batch_that_may_change_things_asks_you_once import (  # noqa: F401 - a fixture, by name
    CHAT,
    FIND,
    FIX,
    _approval_rows,
    _asks,
    _run_tool,
    _start_its_task,
    _until,
    gateway,
)
from test_a_declared_read_asks_nobody import _OneCall, _Recording
from test_an_agent_cli_read_asks_nobody import _asked_by_claude_code, _background
from test_an_agent_cli_read_asks_nobody import _turn as _acp_turn

from personalclaw import approval_grants, mcp_subagents
from personalclaw.acp.mcp_servers import core_tool_declaration
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, EVENT_TOOL_RESULT
from personalclaw.tool_providers.base import RiskLevel
from personalclaw.workflows import batch_start, store
from personalclaw.workflows.models import valid_name

#: A second task that only reads, beside `FIND`.
READ_TESTS = {
    "task": "List the tests that cover the retry helper in tests/",
    "title": "Find the retry tests",
    "objective": "know which tests would catch a change to the retry ceiling",
    "output_format": "a numbered list of test names, one sentence each",
    "boundary": "read only: change no file",
}


# ── the call asks nobody: what it starts asks ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_run_declares_that_what_it_starts_asks_for_itself():
    """🔴 Before: `subagent_run` was built `requires_approval=True`, like every other change, so
    its call asked before the start it makes asked again."""
    tools = {
        d.name: d
        for d in await InProcessMcpToolProvider(
            module="personalclaw.mcp_subagents", provider_name="under-test"
        ).list_tools()
    }
    run = tools["subagent_run"]
    assert run.requires_approval is False
    # Still no read: Ask mode, Trust reads and a `read` grant treat it as the change it is.
    assert run.risk_level is RiskLevel.CAUTION
    # The control: a change of the same module that declares nothing of the kind still asks.
    assert tools["best_of_n"].requires_approval is True


async def _native_turn(tool: str, args: str, *, task_mode: str = "agent"):
    """One native turn calling *tool* with no approval policy set: every ask is declined, so the
    call ran only if nobody had to be asked."""
    provider = _Recording("personalclaw.mcp_subagents")
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, args),
        tool_providers=[provider],
    )
    rt.set_task_mode(task_mode)
    await rt.start()
    asked: list[str] = []
    results: list[str] = []

    async def drain() -> None:
        async for ev in rt.stream("go"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asked.append(ev.title)
                await rt.reject_tool(ev.request_id)
            elif ev.kind == EVENT_TOOL_RESULT:
                results.append(str(ev.tool_output))

    await asyncio.wait_for(drain(), timeout=10)
    return provider.invoked, asked, results


@pytest.mark.asyncio
async def test_a_chat_on_personalclaws_own_runtime_is_not_asked_about_the_call():
    """🔴 Before: the call raised its own card, and the start asked again behind it."""
    invoked, asked, _ = await _native_turn("subagent_run", '{"task": "Summarise the notes"}')
    assert asked == []
    assert invoked == ["subagent_run"]


@pytest.mark.asyncio
async def test_a_change_that_declares_nothing_of_the_kind_still_asks():
    invoked, asked, _ = await _native_turn("best_of_n", '{"prompt": "Name the release"}')
    assert asked == ["best_of_n"]
    assert invoked == []


@pytest.mark.asyncio
async def test_ask_mode_still_refuses_it():
    """It is no read: the refusals that come before any approval still come first."""
    invoked, asked, results = await _native_turn(
        "subagent_run", '{"task": "Summarise the notes"}', task_mode="ask"
    )
    assert invoked == [] and asked == []
    assert results, "the refusal never reached the model"


def _cli_call(tool: str, args: dict):
    return _asked_by_claude_code(tool, args)


def test_an_agent_cli_call_carries_the_declaration_and_kiros_title_cannot_wear_it():
    assert _cli_call("subagent_run", {"task": "Summarise the notes"}).work_asks is True
    assert _cli_call("best_of_n", {"prompt": "Name the release"}).work_asks is False
    # A shell call can produce kiro-cli's title, so only a destructive declaration is taken there.
    kiro = "Running: @personalclaw-core/subagent_run"
    assert core_tool_declaration(kiro, "execute", {"task": "x"}).work_asks is False


@pytest.mark.asyncio
async def test_a_chat_over_an_agent_cli_is_not_asked_about_the_call(tmp_path):
    """🔴 Before: claude-code's permission request for `subagent_run` reached the owner."""
    client, asked, decided = await _acp_turn(
        tmp_path, _cli_call("subagent_run", {"task": "Summarise the notes"}), answer="denied"
    )
    assert asked is False
    client.approve_tool.assert_awaited_once_with("req-1")
    assert decided == [approval_grants.WORK_ASKS]


@pytest.mark.asyncio
async def test_a_change_that_declares_nothing_of_the_kind_still_asks_over_an_agent_cli(tmp_path):
    client, asked, decided = await _acp_turn(
        tmp_path, _cli_call("best_of_n", {"prompt": "Name the release"}), answer="denied"
    )
    assert asked is True and decided == []
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_mode_still_refuses_it_over_an_agent_cli(tmp_path):
    from test_acp_permission_authority import (
        _context_builder,
        _drive,
        _make_state,
        _session,
        _set_stream,
    )

    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="ask", trust=False)
    request = _cli_call("subagent_run", {"task": "Summarise the notes"})
    _set_stream(client, [request, LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")])
    await _drive(state, session)
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_background_agent_on_an_agent_cli_is_not_relayed_the_call():
    client, relayed = await _background(_cli_call("subagent_run", {"task": "Summarise the notes"}))
    relayed.assert_not_awaited()
    client.approve_tool.assert_awaited_once_with("req-1")


# ── a batch asks once ───────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def relay(gateway):  # noqa: F811 - the fixture imported above, by name
    """The gateway's own subagent manager and start relay over the real dashboard state, as
    `GatewayOrchestrator._init_subagents` wires them: a batch that only reads asks through it.
    The chat's approval policy is the test's to set (its Trust pushes ``auto``)."""
    from test_gateway import _make_orchestrator
    from test_subagent import _mock_ctx_builder, _mock_sessions

    policy = {"value": ""}
    sessions = _mock_sessions()
    sessions.get_approval_policy = lambda key: (
        policy["value"] if key == f"dashboard:{CHAT}" else ""
    )
    orch = _make_orchestrator()
    orch.sessions = sessions
    orch.ctx_builder = _mock_ctx_builder()
    orch.dashboard_state = gateway.state
    with patch("personalclaw.subagent.SubagentManager.start_reaper"):
        orch._init_subagents()
    gateway.state.subagents = orch.subagent_mgr
    return SimpleNamespace(**vars(gateway), policy=policy, manager=orch.subagent_mgr)


def _batch_name(out: str) -> str:
    import json

    head = json.loads(out.splitlines()[0])
    name = str(head.get("batch") or "")
    assert valid_name(name), out
    return name


@pytest.mark.asyncio
async def test_a_batch_that_only_reads_asks_you_once_in_its_chat(relay):
    """🔴 Before: it started at once and asked once for each task's start, under the run's
    steps, while nothing in the chat said why it waited."""
    out = await _run_tool(FIND, READ_TESTS)

    (ask,) = _asks(relay.state)
    name = _batch_name(out)
    assert ask["id"] == batch_start.ask_id(name)
    # Asked where the owner is: this chat's card and the Inbox, as a start asks.
    assert ask["session"] == CHAT and ask["source"] == "subagent"
    assert "each of which only reads" in ask["tool_purpose"], ask
    assert "Find the retry callers" in ask["tool_input"]
    assert "Find the retry tests" in ask["tool_input"]
    # Nothing exists yet, and the agent is told it waits, and for what.
    assert relay.provider.saved == {} and relay.supervisor.launched == []
    assert "awaiting_approval" in out and "only read" in out, out
    # The Inbox names the batch, not a subagent (none of its tasks is one yet), and so does every
    # card's "From".
    (row,) = _approval_rows(relay.inbox)
    assert "A batch of 2 subagent tasks from “Retry ceiling” is waiting" in row.message, row.message
    assert ask["source_label"] == "batch of chat “Retry ceiling”", ask
    # What it can touch agrees with what it says: its tasks only read, so no card says it writes
    # files, as the name "subagent" alone said beside a purpose saying none of them does.
    assert ask["blast_radius"]["writes"] is False, ask


@pytest.mark.asyncio
async def test_your_allow_starts_it_and_each_task_starts_on_that_allow(relay, monkeypatch):
    out = await _run_tool(FIND, READ_TESTS)
    (ask,) = _asks(relay.state)

    resp = await relay.client.post(f"/api/approvals/{ask['id']}/approve")
    assert resp.status == 200, await resp.text()
    await _until(lambda: relay.supervisor.launched, "the batch never started after the Allow")

    (launched,) = relay.supervisor.launched
    run = store.get(launched[0].id)
    assert run is not None and run.workflow_name == _batch_name(out)
    consent = run.extra[batch_start.CONSENT_KEY]
    assert consent["by"] == approval_grants.YOU and consent["approval"] == ask["id"]
    # Each task starts on that Allow: no second ask.
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: "interactive")
    asked = AsyncMock(return_value=True)
    await _start_its_task(asked, run.id)
    asked.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_trusted_chat_starts_a_batch_that_only_reads_at_once(relay):
    """As a subagent the chat starts would: on the chat's Trust, with nobody asked."""
    relay.policy["value"] = "auto"

    out = await _run_tool(FIND, READ_TESTS)

    assert _asks(relay.state) == []
    (launched,) = relay.supervisor.launched
    run = store.get(launched[0].id)
    assert run is not None
    assert run.extra[batch_start.CONSENT_KEY]["by"] == approval_grants.PARENT_TRUST
    assert '"run_id"' in out and run.id in out, out


@pytest.mark.asyncio
async def test_a_trust_switch_answers_its_ask(relay):
    await _run_tool(FIND, READ_TESTS)
    assert len(_asks(relay.state)) == 1

    resp = await relay.client.post("/api/chat/mode", json={"mode": "trust", "session": CHAT})
    assert resp.status == 200, await resp.text()
    await _until(lambda: relay.supervisor.launched, "the Trust switch left it asking")
    assert _asks(relay.state) == []


@pytest.mark.asyncio
async def test_a_batch_that_may_change_things_still_asks_for_your_own_allow(relay):
    """A trusted chat starts a batch that only reads; one that may change things still asks, and
    only her answer starts it."""
    relay.policy["value"] = "auto"

    await _run_tool(FIX, FIND)

    (ask,) = _asks(relay.state)
    assert "may change things" in ask["tool_purpose"]
    assert relay.supervisor.launched == []
    assert relay.state.answered_alone(ask["id"])
    # It is the batch of its chat too, and its card still says it may write files.
    assert ask["source_label"] == "batch of chat “Retry ceiling”", ask
    assert ask["blast_radius"]["writes"] is True, ask


@pytest.mark.asyncio
async def test_with_nowhere_to_ask_a_batch_that_only_reads_is_refused(gateway):  # noqa: F811
    """No subagent manager, so no start relay: nothing can ask, and nothing starts."""
    out = await _run_tool(FIND, READ_TESTS)
    assert _asks(gateway.state) == []
    assert gateway.provider.saved == {} and gateway.supervisor.launched == []
    assert "nowhere to ask" in out, out


# ── its card reads how it stands ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_its_card_reads_how_it_stands(relay):
    out = await _run_tool(FIND, READ_TESTS)
    name = _batch_name(out)

    resp = await relay.client.get(f"/api/workflows/batches/{name}")
    assert resp.status == 200, await resp.text()
    assert (await resp.json())["status"] == batch_start.ASKING

    (ask,) = _asks(relay.state)
    await relay.client.post(f"/api/approvals/{ask['id']}/approve")
    await _until(lambda: relay.supervisor.launched, "the batch never started")
    (launched,) = relay.supervisor.launched
    body = await (await relay.client.get(f"/api/workflows/batches/{name}")).json()
    assert body["status"] == batch_start.STARTED and body["run_id"] == launched[0].id


@pytest.mark.asyncio
async def test_its_card_says_why_it_never_started(relay):
    out = await _run_tool(FIND, READ_TESTS)
    name = _batch_name(out)
    (ask,) = _asks(relay.state)

    await relay.client.post(f"/api/approvals/{ask['id']}/reject")
    await _until(lambda: relay.supervisor.announced, "the chat was never told")

    body = await (await relay.client.get(f"/api/workflows/batches/{name}")).json()
    assert body["status"] == batch_start.NOT_STARTED
    assert "declined" in body["error"] and "never started" in body["error"], body
    # What it would have started is gone with it: nothing can ask for it again.
    assert "start" not in (batch_start.read_record(name) or {})


@pytest.mark.asyncio
async def test_a_name_no_batch_has_is_not_found(relay):
    for name in ("subagent-batch-404", "../config"):
        resp = await relay.client.get(f"/api/workflows/batches/{name}")
        assert resp.status == 404, name


# ── a restart does not lose it ─────────────────────────────────────────────────────────────────


async def _restart(state) -> None:
    """What a gateway's stop does to a batch waiting for its answer: its wait is cancelled with
    the process, and no approval survives in the registry."""
    waiting = [t for t in list(state._background_tasks) if not t.done()]
    for task in waiting:
        task.cancel()
    await asyncio.gather(*waiting, return_exceptions=True)
    assert _asks(state) == [], "an ask survived its process"


@pytest.mark.asyncio
async def test_a_waiting_batch_is_asked_again_after_a_restart_and_starts_on_that_answer(relay):
    """🔴 Before: its ask lived only in memory; a restart lost it, and the chat never heard."""
    out = await _run_tool(FIND, READ_TESTS)
    name = _batch_name(out)
    await _restart(relay.state)
    assert batch_start.state_of(name)["status"] == batch_start.ASKING

    assert batch_start.resume(relay.state, relay.supervisor) == 1

    await _until(lambda: _asks(relay.state), "the batch was never asked again")
    (ask,) = _asks(relay.state)
    assert ask["id"] == batch_start.ask_id(name) and ask["session"] == CHAT
    await relay.client.post(f"/api/approvals/{ask['id']}/approve")
    await _until(lambda: relay.supervisor.launched, "the batch never started")
    assert len(relay.supervisor.launched) == 1
    # Asked once per gateway: a second resume finds nothing waiting.
    assert batch_start.resume(relay.state, relay.supervisor) == 0


@pytest.mark.asyncio
async def test_a_batch_whose_window_passed_while_the_gateway_was_down_says_nobody_answered(
    relay, monkeypatch
):
    out = await _run_tool(FIX, FIND)
    name = _batch_name(out)
    await _restart(relay.state)
    record = batch_start.read_record(name) or {}
    batch_start._save_record(name, {**record, "asked_at": time.time() - 3 * 3600})
    monkeypatch.setattr(approval_grants, "approval_window_secs", lambda: 30 * 60.0)

    assert batch_start.resume(relay.state, relay.supervisor) == 0

    assert _asks(relay.state) == []
    (told,) = relay.supervisor.announced
    (ending,) = told
    assert "not approved in time" in ending.error and "never started" in ending.error
    assert ending.declined is False
    assert batch_start.state_of(name)["status"] == batch_start.NOT_STARTED


@pytest.mark.asyncio
async def test_a_batch_allowed_as_the_gateway_stopped_is_not_started_twice(relay):
    out = await _run_tool(FIND, READ_TESTS)
    name = _batch_name(out)
    await _restart(relay.state)
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    record = batch_start.read_record(name) or {}
    batch_start._save_record(name, {**record, "status": batch_start.STARTING})
    store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, session_key=f"dashboard:{CHAT}"),
        )
    )

    assert batch_start.resume(relay.state, relay.supervisor) == 0
    assert _asks(relay.state) == [] and relay.supervisor.launched == []
    assert batch_start.read_record(name) is None


@pytest.mark.asyncio
async def test_a_stopped_turns_batch_is_not_asked_again_after_a_restart(relay):
    """What a Stop ended stays ended: the record says why, and the next gateway asks nothing."""
    from personalclaw import started_work
    from personalclaw.resilience.active_jobs import get_tracker

    get_tracker().register(CHAT, now=time.time() - 1)
    try:
        out = await _run_tool(FIND, READ_TESTS)
        await _until(lambda: _asks(relay.state), "the batch never asked")
        await started_work.end_turn(relay.state, f"dashboard:{CHAT}")
        await asyncio.sleep(0.05)
    finally:
        get_tracker().clear(CHAT)
    name = _batch_name(out)
    state = batch_start.state_of(name)
    assert state["status"] == batch_start.NOT_STARTED, state
    assert started_work.TURN_STOPPED in state["error"], state
    assert relay.supervisor.announced == []

    assert batch_start.resume(relay.state, relay.supervisor) == 0
    assert _asks(relay.state) == []


def test_an_unreadable_record_asks_nothing(tmp_path, monkeypatch):
    """A record only ever asks, and one that cannot be read is no batch at all."""
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    folder = store.workflows_dir() / "batches"
    folder.mkdir(parents=True)
    (folder / "subagent-batch-1.json").write_text("{not json", encoding="utf-8")
    state = SimpleNamespace(_background_tasks=set(), subagents=MagicMock())
    assert batch_start.resume(state, SimpleNamespace(announce=MagicMock())) == 0
    assert batch_start.read_record("subagent-batch-1") is None


# ── two batches, two names ──────────────────────────────────────────────────────────────────────


def test_two_batches_started_in_the_same_millisecond_have_two_names(monkeypatch):
    """🔴 Before: the name was the millisecond alone, so the second batch's definition replaced the
    first's, and the first run's steps read the second's tasks."""
    monkeypatch.setattr(mcp_subagents.time, "time", lambda: 1790949705.313)
    first, second = mcp_subagents._batch_def_name(), mcp_subagents._batch_def_name()
    assert first != second
    assert valid_name(first) and valid_name(second)
    assert first.startswith("subagent-batch-1790949705313-")


# ── a step that waits says why, on the agent's reads ────────────────────────────────────────────


def _batch_run(*, origin_kind=None, session_key: str = f"dashboard:{CHAT}"):
    """A batch's run as `batch_start` creates it, its two steps running."""
    from personalclaw.workflows import batch_compile
    from personalclaw.workflows.models import (
        InstanceState,
        Node,
        NodeInstance,
        OriginKind,
        RunOrigin,
        RunStatus,
        WorkflowRun,
        walk,
    )

    leaves = [batch_compile.leaf_from_item(FIND), batch_compile.leaf_from_item(READ_TESTS)]
    spec = batch_compile.compile_batch(leaves, run_name="subagent-batch-9").spec
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-9",
            status=RunStatus.RUNNING,
            origin=RunOrigin(kind=origin_kind or OriginKind.SUBAGENT_TOOL, session_key=session_key),
        )
    )
    store.write_spec(run.id, spec)
    steps = [(p, n) for p, n in walk(Node.from_dict(spec["root"])) if n.kind.value == "stage"]
    store.write_state(
        run.id, {p: NodeInstance(path=p, state=InstanceState.RUNNING) for p, _ in steps}
    )
    return run, [n.id for _, n in steps]


@pytest.fixture
def runs_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


def _asks_held(*asks: dict):
    """The approval registry as the gateway's process holds it (`ActionServices.state`)."""
    from personalclaw.action_providers.services import ActionServices

    return ActionServices(
        state=SimpleNamespace(
            asks_under=lambda prefix: [a for a in asks if a["session"].startswith(prefix)]
        )
    )


def test_a_status_read_says_which_step_waits_and_for_what(runs_home, monkeypatch):
    """🔴 Before: both steps read "running" while each waited on its owner, and the agent that
    started the batch guessed it was "probably a shell approval"."""
    from personalclaw.action_providers import services
    from personalclaw.workflows import service

    run, (first, second) = _batch_run()
    monkeypatch.setattr(
        services,
        "_services",
        _asks_held(
            {"id": "subagent:ab12:1", "session": f"workflow:{run.id}:{first}", "tool": "bash"},
            {"id": "spawn:cd34", "session": f"workflow:{run.id}:{second}", "tool": "subagent_run"},
            {"id": "subagent:ef56:1", "session": "workflow:another:x", "tool": "write_file"},
        ),
    )

    nodes = {n["node_id"]: n for n in service.status(run.id)["nodes"]}
    assert nodes[first]["waiting_for"] == "its owner's answer on bash"
    assert nodes[second]["waiting_for"] == "its owner's Allow to start"
    # And the read of the run by the agent of the chat that started it, the tool, its runtime
    # naming that chat for the call, says the same.
    from personalclaw import mcp_core, mcp_workflows

    token = mcp_core.set_current_session_key(f"dashboard:{CHAT}")
    try:
        said = mcp_workflows._call_tool_inner("workflow_status", {"run_id": run.id})
    finally:
        mcp_core.reset_current_session_key(token)
    assert "its owner's answer on bash" in said, said


def test_a_step_nothing_waits_on_says_nothing_and_neither_does_a_process_without_the_registry(
    runs_home, monkeypatch
):
    from personalclaw.action_providers import services
    from personalclaw.workflows import service

    run, _ids = _batch_run()
    monkeypatch.setattr(services, "_services", _asks_held())
    assert all("waiting_for" not in n for n in service.status(run.id)["nodes"])
    monkeypatch.setattr(services, "_services", None)
    assert all("waiting_for" not in n for n in service.status(run.id)["nodes"])


@pytest.mark.asyncio
async def test_the_list_of_agents_says_one_waits_for_its_owner_and_on_what(relay, monkeypatch):
    from personalclaw.subagent import SubagentInfo

    waiting = SubagentInfo(id="ab12cd34", task="List the retry callers", parent_session_key="x")
    relay.manager._agents[waiting.id] = waiting
    relay.state._pending_approvals["subagent:ab12cd34:1"] = {
        "id": "subagent:ab12cd34:1",
        "session": "x",
        "tool": "bash",
    }
    import json

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.messaging import api_spawn_list

    app = web.Application()
    app["state"] = relay.state
    # Asked as the chat's agent asks it: its tool names the chat with the internal credential.
    asked = make_mocked_request(
        "GET", "/", headers={"X-Internal-Secret": "internal", "X-Session-Key": "x"}, app=app
    )
    listed = json.loads((await api_spawn_list(asked)).text)
    (row,) = listed["agents"]
    assert row["waiting_for"] == "bash"

    monkeypatch.setattr(mcp_subagents, "_get", lambda _path: listed)
    said = mcp_subagents._call_tool_inner("subagent_list", {})
    assert "waiting for your owner's answer on bash" in said, said


# ── a batch's subagents are shown in its chat ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_tasks_events_reach_the_chat_that_started_it(relay):
    """🔴 Before: they carried their run's own key, which no chat page shows, so the chat's
    Activity panel had no Subagents tab for the batch it started."""
    from personalclaw.subagent import SubagentInfo

    run, (first, _second) = _batch_run()
    task = SubagentInfo(
        id="t1", task="the step's prompt", parent_session_key=f"workflow:{run.id}:{first}"
    )
    await relay.manager._on_event("subagent_spawn", task, {"task": task.task, "agent": ""})
    await relay.manager._on_event("subagent_done", task, {"elapsed": 1.0})

    spawned = [d for kind, d in relay.frames if kind == "subagent_spawn"]
    assert spawned == [
        {
            "id": "t1",
            "session": CHAT,
            "run": run.id,
            "task": "the step's prompt",
            "agent": "",
            "title": "Find the retry callers",
        }
    ]
    (done,) = [d for kind, d in relay.frames if kind == "subagent_done"]
    assert done["session"] == CHAT and done["run"] == run.id


@pytest.mark.asyncio
async def test_a_step_of_a_run_no_chat_started_stays_its_runs(relay):
    """The control: a run started some other way is no chat's, so its steps' events keep the
    run's key."""
    from personalclaw.subagent import SubagentInfo
    from personalclaw.workflows.models import OriginKind

    run, (first, _second) = _batch_run(origin_kind=OriginKind.API, session_key="")
    task = SubagentInfo(id="t2", task="p", parent_session_key=f"workflow:{run.id}:{first}")
    await relay.manager._on_event("subagent_spawn", task, {"task": "p", "agent": ""})
    (spawned,) = [d for kind, d in relay.frames if kind == "subagent_spawn"]
    assert spawned["session"] == f"workflow:{run.id}:{first}" and "run" not in spawned
