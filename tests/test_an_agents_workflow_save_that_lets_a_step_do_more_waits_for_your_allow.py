"""An agent's save of a workflow whose steps would do more is saved on your own Allow, and on
nothing else.

``workflow_author`` saved whatever spec the agent sent. A step that approves its own tool calls
(``approval_mode: "auto"``) or may change things (``capability: "mutating"``) does so each time the
workflow runs, unattended included, and only the editor's save asked the owner about either, so an
agent could save such a step with nobody asked and a later run started on it. Now every save of a
definition runs one posture screen, and the agent's save that loosens a step asks the owner once,
through the approval every other ask uses (the card in the chat that asked, the Inbox, the phone, a
channel), naming the workflow, each such step and what it would then do:

* Nothing is saved until she answers, and her Allow saves what she was shown.
* Her Deny, an ask nobody answers and a stopped turn leave nothing saved.
* Only her own answer counts: neither a Trust nor a YOLO switch answers it.
* A session that acts on its own, and a tool with nowhere to ask, are refused, saying why.
* A newer save of the same workflow replaces the ask; a save her Allow no longer covers is not
  made, and her Inbox says why.
* A save that lets no step do more, or keeps what she already allowed, is made at once.

Driven through the real ``workflow_author`` tool, whose save goes over HTTP to the real workflow
and approval routes, so what is asserted is what the owner is asked and what exists afterwards.
"""

from __future__ import annotations

import asyncio
import copy
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_dashboard_approval import _context_builder, _make_session, _make_state

from personalclaw import mcp_core, mcp_workflows
from personalclaw.dashboard.chat_handlers import api_chat_mode
from personalclaw.dashboard.handlers.sessions import api_approval_resolve, api_approvals
from personalclaw.inbox import OPEN_STATUSES, InboxStore, ItemKind
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows.handlers import register_workflow_routes

CHAT = "chat-lint"
NAME = "lint-fixer"

#: A workflow whose first step fixes the lint on its own: its agent approves its own tool calls.
LOOSE = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {
            "kind": "stage",
            "id": "fix",
            "label": "Fix the lint",
            "config": {"prompt": "Fix every lint error in src/app.", "approval_mode": "auto"},
        },
        {
            "kind": "stage",
            "id": "report",
            "label": "Report",
            "config": {"prompt": "Say what changed."},
        },
    ],
}


def _without_posture() -> dict:
    root = copy.deepcopy(LOOSE)
    root["children"][0]["config"].pop("approval_mode")
    return root


class _MemProvider(defs_mod.WorkflowDefProvider):
    """Where a saved definition lands, so the test can read what was saved."""

    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "agent-save-test-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        items = list(self.saved.values())
        return items[offset : offset + limit], len(items)

    async def get_def(self, name: str):
        return self.saved.get(name)

    async def save_def(self, **fields):
        fields.setdefault("source", "user")
        fields["version"] = int((self.saved.get(fields["name"]) or {}).get("version") or 0) + 1
        self.saved[fields["name"]] = copy.deepcopy(dict(fields))
        return self.saved[fields["name"]]

    async def delete_def(self, name: str) -> bool:
        return self.saved.pop(name, None) is not None


@pytest_asyncio.fixture
async def gateway(tmp_path, monkeypatch):
    """A real dashboard state with its Inbox and one chat, the real workflow and approval routes on
    a live server, and the tool pointed at it as that chat."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    provider = _MemProvider()
    defs_mod.register_provider(provider)

    state, _client = _make_state(tmp_path, context_builder=_context_builder())
    frames: list[tuple[str, dict]] = []
    state.broadcast_ws = lambda kind, data=None: frames.append((kind, data or {}))
    inbox = InboxStore(tmp_path / "inbox_items.json")
    state._inbox_svc = SimpleNamespace(inbox=inbox)
    session = _make_session(CHAT)
    session.title = "Tidy the repo"
    state._sessions[session.key] = session

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
    try:
        yield SimpleNamespace(
            state=state,
            session=session,
            inbox=inbox,
            provider=provider,
            client=client,
            frames=frames,
        )
    finally:
        await client.close()
        defs_mod.unregister_provider("agent-save-test-mem")


async def _author(root: dict, *, name: str = NAME) -> str:
    """The agent's `workflow_author` call, made the way its tool server makes it: synchronously,
    off the gateway's event loop."""
    args = {"name": name, "root": root, "description": "Fixes the lint, then reports."}
    return await asyncio.to_thread(mcp_workflows._call_tool_inner, "workflow_author", args)


async def _until(predicate, what: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never happened within 2s: {what}")


def _asks(state) -> list[dict]:
    return [e for e in state._pending_approvals.values() if e.get("tool") == "workflow_author"]


def _approval_rows(inbox: InboxStore) -> list:
    return [
        i
        for i in inbox.items.values()
        if i.item_kind == ItemKind.AGENT_REQUEST.value and i.status in OPEN_STATUSES
    ]


async def _answer(gateway, ask: dict, action: str) -> None:
    resp = await gateway.client.post(f"/api/approvals/{ask['id']}/{action}")
    assert resp.status == 200, await resp.text()


# ── one ask, naming what the workflow would let a step do ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_save_that_lets_a_step_approve_its_own_calls_asks_you_once(gateway):
    out = await _author(LOOSE)

    asks = _asks(gateway.state)
    assert len(asks) == 1, f"expected ONE ask for the save, got {asks!r}; the tool said: {out}"
    ask = asks[0]
    said = f"{ask['tool_purpose']}\n{ask['tool_input']}"
    # Named by the workflow, the step by its own name, and what it would then do.
    assert f"“{NAME}”" in said, said
    assert "Fix the lint" in said, said
    assert "approves its own tool calls instead of asking you first" in said, said
    assert "Report" not in ask["tool_input"], "a step that does no more was asked about"
    # Asked where the owner is: this chat's card (answered through the queue), and the Inbox,
    # in the words of who asked.
    assert ask["session"] == CHAT
    (frame,) = [d for kind, d in gateway.frames if kind == "approval" and d.get("id") == ask["id"]]
    assert frame["session"] == CHAT and frame["source"] == "agent", frame
    (row,) = _approval_rows(gateway.inbox)
    assert "The agent in “Tidy the repo” is waiting for your decision" in row.message, row.message
    # Nothing exists yet, and the agent is told it waits for her.
    assert gateway.provider.saved == {}, "the save was made before anyone was asked"
    assert "awaiting_approval" in out and "Fix the lint" in out, out


@pytest.mark.asyncio
async def test_write_access_is_asked_about_too(gateway):
    root = _without_posture()
    root["children"][0]["config"]["capability"] = "mutating"

    await _author(root)

    (ask,) = _asks(gateway.state)
    assert "may change files and run commands, not only read" in ask["tool_input"], ask
    assert gateway.provider.saved == {}


@pytest.mark.asyncio
async def test_your_allow_saves_what_you_were_shown(gateway):
    await _author(LOOSE)
    (ask,) = _asks(gateway.state)

    await _answer(gateway, ask, "approve")
    await _until(lambda: NAME in gateway.provider.saved, "the allowed save was never made")

    saved = gateway.provider.saved[NAME]
    assert saved["root"] == LOOSE
    # Her Allow covers what its steps would do; the version is still the agent's save, which an
    # automation of the workflow does not follow until she says to.
    assert saved["_saved_by"] == "agent"
    assert _asks(gateway.state) == []


@pytest.mark.asyncio
async def test_your_deny_leaves_nothing_saved(gateway):
    await _author(LOOSE)
    (ask,) = _asks(gateway.state)

    await _answer(gateway, ask, "reject")
    await asyncio.sleep(0.05)

    assert gateway.provider.saved == {}, "a denied save was made"
    assert _asks(gateway.state) == []


@pytest.mark.asyncio
async def test_a_trust_or_yolo_switch_does_not_answer_it(gateway):
    await _author(LOOSE)
    (ask,) = _asks(gateway.state)

    for mode in ({"mode": "trust", "session": CHAT}, {"mode": "yolo"}):
        resp = await gateway.client.post("/api/chat/mode", json=mode)
        assert resp.status == 200, await resp.text()
    await asyncio.sleep(0.05)

    assert ask["id"] in gateway.state._pending_approvals, "a posture switch answered the save"
    assert gateway.provider.saved == {}
    gateway.state.disable_yolo()


@pytest.mark.asyncio
async def test_an_ask_nobody_answers_leaves_nothing_saved(gateway, monkeypatch):
    monkeypatch.setattr(gateway.state, "approval_window_secs", lambda: 0.05)

    await _author(LOOSE)
    await _until(lambda: not _asks(gateway.state), "the unanswered ask never ended")
    await asyncio.sleep(0.05)

    assert gateway.provider.saved == {}
    assert _approval_rows(gateway.inbox) == [], "its Inbox row is still asking"


@pytest.mark.asyncio
async def test_a_turns_stop_ends_the_save_it_asked_for(gateway):
    from personalclaw import started_work
    from personalclaw.resilience.active_jobs import get_tracker

    get_tracker().register(CHAT, now=time.time() - 1)
    try:
        await _author(LOOSE)
        (ask,) = _asks(gateway.state)
        await started_work.end_turn(gateway.state, f"dashboard:{CHAT}")
        await asyncio.sleep(0.05)
    finally:
        get_tracker().clear(CHAT)

    assert _asks(gateway.state) == [], "a stopped turn's save is still asking"
    (ended,) = [d for kind, d in gateway.frames if kind == "approval_resolved"]
    assert ended["outcome"] == "cancelled" and ended["ended"] == started_work.TURN_STOPPED, ended
    assert gateway.provider.saved == {}


@pytest.mark.asyncio
async def test_a_newer_save_of_the_same_workflow_is_the_one_asked_about(gateway):
    from personalclaw.workflows import definition_ask

    await _author(LOOSE)
    (first,) = _asks(gateway.state)
    newer = copy.deepcopy(LOOSE)
    newer["children"][1]["config"]["prompt"] = "Say what changed, file by file."

    await _author(newer)

    (ask,) = _asks(gateway.state)
    assert ask["id"] != first["id"]
    (ended,) = [d for kind, d in gateway.frames if kind == "approval_resolved"]
    assert ended["id"] == first["id"] and ended["ended"] == definition_ask.SUPERSEDED, ended
    await _answer(gateway, ask, "approve")
    await _until(lambda: NAME in gateway.provider.saved, "the allowed save was never made")
    assert gateway.provider.saved[NAME]["root"] == newer


# ── nobody to ask ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_that_acts_on_its_own_is_refused_saying_why(gateway):
    gateway.session._unattended = True

    out = await _author(LOOSE)

    assert _asks(gateway.state) == [], "a session that acts on its own asked anyway"
    assert gateway.provider.saved == {}
    assert "not saved" in out and "Allow" in out and "Fix the lint" in out, out


@pytest.mark.asyncio
async def test_with_nowhere_to_ask_it_is_refused_saying_why(gateway, monkeypatch):
    """The gateway that asks does not answer: nothing is saved, and the agent is told so."""
    called: list[str] = []

    def unanswered(path: str, body: dict | None = None) -> dict:
        called.append(path)
        return {"error": f"the gateway did not answer POST {path} within 30 s", "timed_out": True}

    monkeypatch.setattr(mcp_core, "_post", unanswered)

    out = await _author(LOOSE)

    assert called == ["/api/workflows/agent-saves"]
    assert gateway.provider.saved == {} and _asks(gateway.state) == []
    assert out.startswith("Error") and "not saved" in out and "only on your owner's" in out, out


# ── what her Allow covers ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_save_her_allow_no_longer_covers_is_not_made_and_her_inbox_says_why(gateway):
    """The stored definition changed while she was asked, so the save would now let a step do
    more that the ask never named: her Allow does not cover it."""
    allowed_before = copy.deepcopy(LOOSE)
    gateway.provider.saved[NAME] = {"name": NAME, "root": allowed_before, "version": 1}
    both = copy.deepcopy(LOOSE)
    both["children"][1]["config"]["approval_mode"] = "auto"
    await _author(both)
    (ask,) = _asks(gateway.state)
    assert "Report" in ask["tool_input"] and "Fix the lint" not in ask["tool_input"], ask
    # Meanwhile the owner made "Fix the lint" ask again.
    gateway.provider.saved[NAME] = {"name": NAME, "root": _without_posture(), "version": 2}

    await _answer(gateway, ask, "approve")
    await _until(
        lambda: any("Not saved" in i.message for i in gateway.inbox.items.values()),
        "her Inbox was never told the allowed save was not made",
    )

    assert gateway.provider.saved[NAME]["version"] == 2, "a save her Allow did not cover was made"


@pytest.mark.asyncio
async def test_a_save_that_lets_no_step_do_more_is_made_at_once(gateway):
    out = await _author(_without_posture())

    assert _asks(gateway.state) == []
    assert gateway.provider.saved[NAME]["root"] == _without_posture(), out


@pytest.mark.asyncio
async def test_resaving_what_you_already_allowed_asks_nobody(gateway):
    gateway.provider.saved[NAME] = {"name": NAME, "root": copy.deepcopy(LOOSE), "version": 1}
    changed = copy.deepcopy(LOOSE)
    changed["children"][1]["config"]["prompt"] = "Say what changed, briefly."

    await _author(changed)

    assert _asks(gateway.state) == []
    assert gateway.provider.saved[NAME]["root"] == changed


@pytest.mark.asyncio
async def test_giving_an_allowed_step_new_work_asks_you_again(gateway):
    """Her yes was to the step she was shown: an agent may not keep a step's Allow and change what
    the step does."""
    gateway.provider.saved[NAME] = {"name": NAME, "root": copy.deepcopy(LOOSE), "version": 1}
    repurposed = copy.deepcopy(LOOSE)
    repurposed["children"][0]["config"]["prompt"] = "Delete the old release branches."

    await _author(repurposed)

    (ask,) = _asks(gateway.state)
    assert "Fix the lint" in ask["tool_input"], ask
    assert gateway.provider.saved[NAME]["version"] == 1, "the repurposed step was saved"


@pytest.mark.asyncio
async def test_a_check_asks_nobody_and_says_which_step_a_save_would_ask_for(gateway):
    out = await asyncio.to_thread(
        mcp_workflows._call_tool_inner, "workflow_check", {"name": NAME, "root": LOOSE}
    )

    assert _asks(gateway.state) == [] and gateway.provider.saved == {}
    assert "needs_owner_allow" in out and "Fix the lint" in out, out


# ── the editor's save sees every step ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_macro_in_the_spec_does_not_hide_a_step_from_the_editors_question(gateway):
    """🔴 Before: one macro node anywhere made the screen read no steps at all, so the editor saved
    a step that approves its own calls with nobody asked."""
    root = copy.deepcopy(LOOSE)
    root["children"].append(
        {"macro": "judge_panel", "id": "review", "config": {"subject": "x", "lenses": ["a", "b"]}}
    )
    body = {"name": NAME, "root": root, "strict": False}

    refused = await gateway.client.post("/api/workflows", json=body)

    assert refused.status == 400, await refused.text()
    error = (await refused.json())["error"]
    assert error["code"] == "confirmation_required", error
    assert error["detail"]["field"] == f"workflows.{NAME}.root.children[0].approval_mode", error
    assert gateway.provider.saved == {}
    allowed = await gateway.client.post("/api/workflows", json={**body, "confirm": True})
    assert allowed.status == 201, await allowed.text()


@pytest.mark.asyncio
async def test_the_editor_asks_again_when_an_allowed_step_is_given_new_work(gateway):
    """The same rule at her own editor: a step that approves its own calls and now does something
    else is asked about once more; re-saving it as it is asks nothing."""
    first = await gateway.client.post(
        "/api/workflows", json={"name": NAME, "root": LOOSE, "confirm": True}
    )
    assert first.status == 201, await first.text()
    revision = (await first.json())["revision"]
    unchanged = await gateway.client.post(
        "/api/workflows", json={"name": NAME, "root": LOOSE}, headers={"If-Match": revision}
    )
    assert unchanged.status == 201, await unchanged.text()
    revision = (await unchanged.json())["revision"]
    repurposed = copy.deepcopy(LOOSE)
    repurposed["children"][0]["config"]["prompt"] = "Delete the old release branches."

    asked = await gateway.client.post(
        "/api/workflows", json={"name": NAME, "root": repurposed}, headers={"If-Match": revision}
    )

    assert asked.status == 400, await asked.text()
    assert (await asked.json())["error"]["code"] == "confirmation_required"
    assert gateway.provider.saved[NAME]["root"] == LOOSE
