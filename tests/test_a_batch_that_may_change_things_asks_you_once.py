"""A batch whose tasks may change things starts on one Allow of yours, and on nothing else.

`subagent_run` compiles two or more tasks into one run. A task declared `mutating` carries the write
grant (`capability: "mutating"`) that only the owner's consent may put on a step, so the save asked
for a `confirm` the tool never sent, and every batch that changed anything was refused before it
started. The tool must not answer that question itself. The batch asks it of the owner instead,
once, through the approval every other ask uses (the card in the chat that started it, the Inbox,
the phone, a channel), naming what each task may change: the files and commands its tools reach,
and the paths it says it will write.

* Nothing is saved or started until the owner answers, and an Allow starts it: the definition is
  saved with the write grant she allowed, and the run is the chat's batch.
* A Deny ends it declined: nothing is saved or started, and the chat that started it hears so.
* Only her own answer counts: no standing grant answers it, and neither does a Trust or YOLO switch
  flipped while it waits.
* An ask nobody answers ends it unstarted, and says so: not as her Deny.
* Her Allow starts its tasks: each starts on it, not on a second ask, and only a task of the run
  she allowed does.
* A run that acts alone (a loop started Unattended) has nobody to ask, and a gateway with nowhere to
  ask has nobody either, so neither starts one: each is refused, saying why.
* A loop stopped, or a turn stopped, while the batch it asked to start waits ends that ask with
  it, saying so, and tells nobody: that work has nobody left to report to.

Driven through the real `subagent_run` tool, whose calls go over HTTP to the real workflow and
approval routes, so what is asserted is what the owner is asked and what exists afterwards.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_a_stopped_loop_ends_what_it_started import _NudgeService
from test_dashboard_approval import _context_builder, _make_session, _make_state
from test_one_allow_covers_a_triggers_agent import _manager, _spawn_rows

from personalclaw import approval_grants, mcp_core, mcp_subagents
from personalclaw.automation_posture import POSTURE_SPECS
from personalclaw.dashboard.chat_handlers import api_chat_mode
from personalclaw.dashboard.handlers.sessions import api_approval_resolve, api_approvals
from personalclaw.inbox import OPEN_STATUSES, InboxStore, ItemKind
from personalclaw.loop import manager as loop_manager
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.workflows import batch_start
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import store
from personalclaw.workflows.handlers import register_workflow_routes
from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

CHAT = "chat-a"

#: Two tasks: one that changes a file in the repository, one that only reads it.
FIX = {
    "task": "Raise the retry ceiling in src/app/retry.py from three to five",
    "title": "Raise the retry ceiling",
    "objective": "make the retry ceiling five everywhere it is decided",
    "output_format": "the diff you made, then one sentence on why",
    "boundary": "change only the retry module and its test",
    "capability": "mutating",
    "writes": ["src/app/retry.py"],
}
FIND = {
    "task": "List every caller of the retry helper in src/app",
    "title": "Find the retry callers",
    "objective": "know every place that depends on the retry ceiling",
    "output_format": "a numbered list of file:line, one sentence each",
    "boundary": "read only: change no file",
}


class _MemProvider(defs_mod.WorkflowDefProvider):
    """Where a saved definition lands, so the test can read what was saved."""

    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "batch-test-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        items = list(self.saved.values())
        return items[offset : offset + limit], len(items)

    async def get_def(self, name: str):
        return self.saved.get(name)

    async def save_def(self, **fields):
        fields.setdefault("version", 1)
        fields.setdefault("source", "user")
        self.saved[fields["name"]] = dict(fields)
        return self.saved[fields["name"]]

    async def delete_def(self, name: str) -> bool:
        return self.saved.pop(name, None) is not None


class _Supervisor:
    """The workflow supervisor: what it was asked to launch, and what it told a conversation."""

    def __init__(self) -> None:
        self.launched: list[tuple[object, dict]] = []
        self.announced: list[list] = []

    def controller(self, _run_id: str):
        return None

    async def launch(self, run, spec, *, depth: int = 0):
        self.launched.append((run, spec))
        return SimpleNamespace(run=run)

    def announce(self, infos) -> None:
        self.announced.append(list(infos))


@pytest_asyncio.fixture
async def gateway(tmp_path, monkeypatch):
    """A real dashboard state with its Inbox and one chat, the real workflow and approval routes on
    a live server, and the tool pointed at it as that chat."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    # A model is bound for every use case, as on an install that has one: the run-start check
    # that a step's model resolves is not what these tests are about.
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda _use_case: True
    )
    provider = _MemProvider()
    defs_mod.register_provider(provider)

    state, _client = _make_state(tmp_path, context_builder=_context_builder())
    frames: list[tuple[str, dict]] = []
    state.broadcast_ws = lambda kind, data=None: frames.append((kind, data or {}))
    inbox = InboxStore(tmp_path / "inbox_items.json")
    state._inbox_svc = SimpleNamespace(inbox=inbox)
    session = _make_session(CHAT)
    session.title = "Retry ceiling"
    state._sessions[session.key] = session
    supervisor = _Supervisor()
    state.workflows = supervisor

    app = _api_app(state)
    register_workflow_routes(app)
    app.router.add_get("/api/approvals", api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)
    app.router.add_post("/api/chat/mode", api_chat_mode)
    client = TestClient(TestServer(app))
    await client.start_server()
    base = str(client.make_url("")).rstrip("/")
    monkeypatch.setattr(mcp_core, "_api_base", lambda: base)
    monkeypatch.setattr(mcp_core, "_internal_secret", lambda: "test-secret")
    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: f"dashboard:{CHAT}")
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: f"dashboard:{CHAT}")
    try:
        yield SimpleNamespace(
            state=state,
            session=session,
            inbox=inbox,
            provider=provider,
            supervisor=supervisor,
            client=client,
            frames=frames,
        )
    finally:
        await client.close()
        defs_mod.unregister_provider("batch-test-mem")


async def _run_tool(*tasks: dict, cwd: str = "") -> str:
    """The agent's `subagent_run` call, made the way its tool server makes it: synchronously, over
    HTTP, off the gateway's event loop."""
    args: dict = {"tasks": list(tasks)}
    if cwd:
        args["cwd"] = cwd
    return await asyncio.to_thread(mcp_subagents._call_tool_inner, "subagent_run", args)


async def _until(predicate, what: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never happened within 2s: {what}")


def _asks(state) -> list[dict]:
    return [e for e in state._pending_approvals.values() if e.get("tool") == "subagent_run"]


def _approval_rows(inbox: InboxStore) -> list:
    return [
        i
        for i in inbox.items.values()
        if i.item_kind == ItemKind.AGENT_REQUEST.value and i.status in OPEN_STATUSES
    ]


# ── one ask, naming what may change ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_that_may_change_things_asks_you_once_and_names_what_may_change(gateway):
    out = await _run_tool(FIX, FIND)

    asks = _asks(gateway.state)
    assert len(asks) == 1, f"expected ONE ask for the batch, got {asks!r}; the tool said: {out}"
    ask = asks[0]
    said = f"{ask['tool_purpose']}\n{ask['tool_input']}"
    # Named by the task's own title, with the files and commands it may change and the path it
    # says it writes; the task that only reads is said to.
    assert "Raise the retry ceiling" in said, said
    assert "change files and run commands" in said, said
    assert "src/app/retry.py" in said, said
    assert "Find the retry callers" in said and "only reads" in said, said
    # Asked where the owner is: this chat's card, and the Inbox. The card's frame says a
    # background agent asked, so the chat answers it through the approvals queue.
    assert ask["session"] == CHAT
    (frame,) = [d for kind, d in gateway.frames if kind == "approval" and d.get("id") == ask["id"]]
    assert frame["session"] == CHAT and frame["source"] == "subagent", frame
    assert len(_approval_rows(gateway.inbox)) == 1
    # Nothing exists yet, and the agent is told it waits for her.
    assert gateway.provider.saved == {}
    assert gateway.supervisor.launched == []
    assert "awaiting_approval" in out, out


@pytest.mark.asyncio
async def test_the_ask_says_its_tasks_read_the_folder_it_was_started_in_only_where_they_do(
    gateway, tmp_path, monkeypatch
):
    """A task reads the folder its batch was started in only where a subagent may work (the
    workspace, the allowed folders), so the ask says it reads one there and says it does not, and
    where that is set, for one anywhere else."""
    workspace = tmp_path / "workspace"
    inside = workspace / "notes"
    inside.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))

    await _run_tool(FIX, FIND, cwd=str(outside))
    await _run_tool(FIX, FIND, cwd=str(inside))

    said = {ask["tool_input"] for ask in _asks(gateway.state)}
    (away,) = [text for text in said if str(outside) in text]
    assert f"They do not read {outside}, the folder it was started in" in away, away
    assert "Allowed working directories" in away and "they read" not in away, away
    (home,) = [text for text in said if os.path.realpath(inside) in text]
    assert f"they read {os.path.realpath(inside)}, the folder it was started in" in home, home
    assert "do not read" not in home, home


@pytest.mark.asyncio
async def test_it_starts_only_after_you_allow_it(gateway):
    await _run_tool(FIX, FIND)
    ask = _asks(gateway.state)[0]

    resp = await gateway.client.post(f"/api/approvals/{ask['id']}/approve")
    assert resp.status == 200, await resp.text()
    await _until(lambda: gateway.supervisor.launched, "the batch never started after the Allow")

    assert len(gateway.supervisor.launched) == 1
    run, spec = gateway.supervisor.launched[0]
    # The definition she allowed: the task that changes things carries the write grant.
    saved = gateway.provider.saved[run.workflow_name]
    grants = {c["id"]: c["config"].get("capability") for c in saved["root"]["children"]}
    assert sorted(grants.values()) == ["mutating", "research"], grants
    # It is the chat's batch: a subagent batch, started from this chat.
    stored = store.get(run.id)
    assert stored is not None
    assert stored.origin.kind == OriginKind.SUBAGENT_TOOL
    assert stored.origin.session_key == f"dashboard:{CHAT}"


@pytest.mark.asyncio
async def test_deny_ends_it_declined(gateway):
    await _run_tool(FIX, FIND)
    ask = _asks(gateway.state)[0]

    resp = await gateway.client.post(f"/api/approvals/{ask['id']}/reject")
    assert resp.status == 200, await resp.text()
    await _until(lambda: gateway.supervisor.announced, "the chat was never told it was declined")

    assert gateway.provider.saved == {}, "a declined batch was saved"
    assert gateway.supervisor.launched == [], "a declined batch started"
    (told,) = gateway.supervisor.announced
    assert len(told) == 1
    ending = told[0]
    assert ending.declined is True
    assert ending.parent_session_key == f"dashboard:{CHAT}"
    assert "declined" in ending.error and "never started" in ending.error, ending.error
    assert "Raise the retry ceiling" in ending.task and "Find the retry callers" in ending.task


@pytest.mark.asyncio
async def test_a_trust_or_yolo_switch_does_not_answer_it(gateway):
    await _run_tool(FIX, FIND)
    ask = _asks(gateway.state)[0]

    for mode in ({"mode": "trust", "session": CHAT}, {"mode": "yolo"}):
        resp = await gateway.client.post("/api/chat/mode", json=mode)
        assert resp.status == 200, await resp.text()
    await asyncio.sleep(0.05)

    assert ask["id"] in gateway.state._pending_approvals, "a posture switch answered the batch"
    assert gateway.supervisor.launched == []
    gateway.state.disable_yolo()


@pytest.mark.asyncio
async def test_an_ask_nobody_answers_ends_it_unstarted_not_declined(gateway, monkeypatch):
    monkeypatch.setattr(gateway.state, "approval_window_secs", lambda: 0.05)
    await _run_tool(FIX, FIND)
    await _until(lambda: gateway.supervisor.announced, "the chat was never told nobody answered")

    assert gateway.provider.saved == {} and gateway.supervisor.launched == []
    (told,) = gateway.supervisor.announced
    (ending,) = told
    assert ending.declined is False, "an ask nobody answered reads as her Deny"
    assert "not approved in time" in ending.error and "never started" in ending.error, ending.error
    assert _approval_rows(gateway.inbox) == [], "its Inbox row is still asking"


def test_the_ask_says_what_every_posture_key_lets_a_task_do():
    """A key a step may carry only with the owner's consent is one the ask must put in words, or
    a batch could carry it past an ask that says nothing about it."""
    assert set(batch_start.WHAT_IT_MAY_DO) == set(POSTURE_SPECS)


# ── its tasks start on that Allow ───────────────────────────────────────────────────────────────


def _batch_run(*, allowed: bool, kind: OriginKind = OriginKind.SUBAGENT_TOOL) -> str:
    """A run as `batch_start` creates it: started from the chat, with her Allow on its record
    when she gave one."""
    consent = {"approval": "batch:subagent-batch-1", "allowed_at": 1.0, "by": "you"}
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-1",
            origin=RunOrigin(kind=kind, session_key=f"dashboard:{CHAT}"),
            extra={batch_start.CONSENT_KEY: consent} if allowed else {},
        )
    )
    return run.id


async def _start_its_task(asked: AsyncMock, run_id: str):
    manager = _manager(asked)
    with patch("personalclaw.subagent.SubagentManager._run", new=AsyncMock()):
        info = manager.spawn(
            FIX["task"],
            parent_session_key=f"workflow:{run_id}",
            parent_run=f"workflow:{run_id}",
        )
        assert info is not None and not info.done, info
        await asyncio.wait_for(manager._tasks[info.id], timeout=10)
    return manager, info


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    # Settings → Agent defaults → Approval mode "interactive": nothing else approves its calls.
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: "interactive")


@pytest.mark.asyncio
async def test_its_tasks_start_on_that_allow(runs):
    asked = AsyncMock(return_value=True)
    manager, info = await _start_its_task(asked, _batch_run(allowed=True))

    asked.assert_not_awaited()
    started = [r for r in _spawn_rows(info.id) if r.get("outcome") == "auto_approved_spawn"]
    assert started and started[-1]["metadata"]["decided_by"] == approval_grants.BATCH_ALLOWED
    # The start only: what the task then does asks as any agent's calls do.
    assert manager._standing_grant(info) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["not_allowed", "not_a_batch", "unknown"])
async def test_a_step_of_any_other_run_still_asks(runs, which):
    """The control: a batch run whose record carries no Allow, a run started some other way that
    carries one, and a run that does not exist each leave the start to ask."""
    run_id = {
        "not_allowed": lambda: _batch_run(allowed=False),
        "not_a_batch": lambda: _batch_run(allowed=True, kind=OriginKind.API),
        "unknown": lambda: "wr-nobody-made-this",
    }[which]()
    asked = AsyncMock(return_value=True)
    await _start_its_task(asked, run_id)
    asked.assert_awaited_once()


# ── nobody to ask ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_that_acts_alone_is_refused_saying_why(gateway):
    gateway.session._unattended = True

    out = await _run_tool(FIX, FIND)

    assert _asks(gateway.state) == [], "a run that acts alone asked anyway"
    assert gateway.provider.saved == {} and gateway.supervisor.launched == []
    assert "Allow" in out and "Raise the retry ceiling" in out, out


@pytest.mark.asyncio
async def test_a_loops_stop_ends_the_batch_its_worker_asked_to_start(
    gateway, tmp_path, monkeypatch
):
    """🔴 Before: the ask stayed on Home and in the Inbox after the Stop, and once nobody had
    answered it, the batch's ending was handed to the stopped loop's worker as a new turn."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    loop = loop_store.create(Loop(id="", name="Release notes", kind="goal", task="tidy the notes"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    worker = _make_session(f"loop-{loop.id}")
    gateway.state._sessions[worker.key] = worker
    for module in (mcp_core, mcp_subagents):
        monkeypatch.setattr(module, "_resolve_session_key", lambda: f"dashboard:{worker.key}")
    await _run_tool(FIX, FIND)
    (ask,) = _asks(gateway.state)
    assert ask["session"] == worker.key

    await loop_manager.stop(gateway.state, _NudgeService(), loop.id)
    await asyncio.sleep(0.05)

    assert _asks(gateway.state) == [], "the stopped loop's batch is still asking"
    (ended,) = [d for kind, d in gateway.frames if kind == "approval_resolved"]
    assert ended["outcome"] == "cancelled", ended
    assert ended["ended"] == "its loop “Release notes” was stopped", ended
    assert _approval_rows(gateway.inbox) == []
    assert gateway.provider.saved == {} and gateway.supervisor.launched == []
    assert gateway.supervisor.announced == [], "the stopped loop's worker was told"


@pytest.mark.asyncio
async def test_a_turns_stop_ends_the_batch_it_asked_to_start(gateway):
    """What a turn started ends with its Stop (`started_work.end_turn`), and a batch still
    waiting for her Allow is something it started: its ask ends saying why, and it never starts."""
    import time

    from personalclaw import started_work
    from personalclaw.resilience.active_jobs import get_tracker

    get_tracker().register(CHAT, now=time.time() - 1)
    try:
        await _run_tool(FIX, FIND)
        (ask,) = _asks(gateway.state)
        await started_work.end_turn(gateway.state, f"dashboard:{CHAT}")
        await asyncio.sleep(0.05)
    finally:
        get_tracker().clear(CHAT)

    assert _asks(gateway.state) == [], "a stopped turn's batch is still asking"
    (ended,) = [d for kind, d in gateway.frames if kind == "approval_resolved"]
    assert ended["outcome"] == "cancelled", ended
    assert ended["ended"] == started_work.TURN_STOPPED, ended
    assert gateway.provider.saved == {} and gateway.supervisor.launched == []
    assert gateway.supervisor.announced == [], "a stopped turn was handed the batch's ending"


@pytest.mark.asyncio
async def test_an_earlier_turns_batch_outlives_a_later_turns_stop(gateway):
    """The control: a batch an earlier turn asked to start is that turn's, and keeps asking."""
    import time

    from personalclaw import started_work
    from personalclaw.resilience.active_jobs import get_tracker

    await _run_tool(FIX, FIND)
    get_tracker().register(CHAT, now=time.time() + 1)
    try:
        await started_work.end_turn(gateway.state, f"dashboard:{CHAT}")
    finally:
        get_tracker().clear(CHAT)
    assert len(_asks(gateway.state)) == 1, "a later turn's Stop ended an earlier turn's batch"


@pytest.mark.asyncio
async def test_with_nowhere_to_ask_it_is_refused(gateway):
    refused = await batch_start.start(
        None,
        gateway.supervisor,
        name="subagent-batch-1",
        root=_compiled_root(),
        workspace={"mode": "scratch"},
        inputs={},
        writes={},
        session_key=f"dashboard:{CHAT}",
    )

    assert refused.get("ok") is False, refused
    assert "nowhere to ask" in refused["message"], refused
    assert gateway.provider.saved == {} and gateway.supervisor.launched == []


# ── a batch that only reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_that_only_reads_starts_at_once_as_the_chats_batch(gateway):
    out = await _run_tool({**FIX, "capability": "research", "writes": []}, FIND)

    assert _asks(gateway.state) == [], "a batch that only reads asked to start"
    assert len(gateway.supervisor.launched) == 1, out
    run, _spec = gateway.supervisor.launched[0]
    stored = store.get(run.id)
    assert stored is not None
    assert stored.origin.kind == OriginKind.SUBAGENT_TOOL, stored.origin
    assert stored.origin.session_key == f"dashboard:{CHAT}"
    assert '"run_id"' in out and run.id in out, out


def _compiled_root() -> dict:
    from personalclaw.workflows import batch_compile

    leaves = [batch_compile.leaf_from_item(FIX), batch_compile.leaf_from_item(FIND)]
    result = batch_compile.compile_batch(leaves, run_name="subagent-batch-1")
    assert result.compiled and result.ok, result.findings
    return result.spec["root"]
