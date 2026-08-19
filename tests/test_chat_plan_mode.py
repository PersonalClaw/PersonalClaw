"""Chat plan mode (CHAT-CRAFT CC-8) — the five properties the atom names.

The whole point of the atom is that there is **no second state machine**: a chat's plan
walkthrough is the *same* ``personalclaw.planning.session`` model the loop planning
surface drives. So these tests mostly pin *routing* — that each chat-side action lands on
the shared transition and inherits its exact rule — plus the two guarantees that are
behavioural rather than structural:

* the no-execute guarantee is the **task-mode gate**, asserted by driving a real
  ``NativeAgentRuntime`` until a mutating tool is denied at the
  ``_guard_and_invoke`` call site — never by inspecting a system prompt. A test that
  asserted "the framing says plan" would pass with the gate deleted;
* activating mid-turn **parks** the run: the transcript is not truncated, and approval
  resumes the SAME conversation with the approved plan rather than restarting it.

``test_native_runtime``'s scripted model/tool harness is imported rather than re-derived,
so the runtime under test here is wired exactly like the runtime the gate ships with.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state
from test_native_runtime import _defn, _drain, _ScriptedModel, _Tool

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.dashboard import chat_plan
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.planning.session import StepStatus

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _app(state) -> web.Application:
    """The chat app plus the six plan-mode routes (registered exactly as server.py does)."""
    app = _make_app(state)
    app.router.add_get("/api/chat/sessions/{session}/plan-session", chat_plan.api_chat_plan_session)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/activate", chat_plan.api_chat_plan_activate
    )
    app.router.add_post("/api/chat/sessions/{session}/plan/edit", chat_plan.api_chat_plan_edit)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/comment", chat_plan.api_chat_plan_comment
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/approve", chat_plan.api_chat_plan_approve
    )
    app.router.add_post("/api/chat/sessions/{session}/plan/cancel", chat_plan.api_chat_plan_cancel)
    return app


def _seed(state, name="c1"):
    """A two-turn chat: the ordinary state a user is in when they reach for Plan."""
    s = state.get_or_create_session(name)
    s.append("user", "u1", "msg msg-u")
    s.append("assistant", "a1", "msg msg-a")
    s.drain()
    return s


def _no_dispatch(monkeypatch) -> list[str]:
    """Capture the prompts chat_plan would run a turn with, without running one."""
    seen: list[str] = []

    async def _fake_run_chat(state, session, msg, **kw):
        seen.append(msg)

    monkeypatch.setattr("personalclaw.dashboard.chat_runner._run_chat", _fake_run_chat)
    return seen


class TestManualOnlyActivation:
    """Clause 3: activation is manual-only — a quick task is untouched."""

    @pytest.mark.asyncio
    async def test_the_composer_affordance_opens_the_shared_walkthrough(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            r = await client.post("/api/chat/sessions/c1/plan/activate")
            assert r.status == 200
            data = await r.json()
        # The persisted shape is a planning.session PlanSession owned by the CHAT —
        # project_id carries the chat key, the step is a plain PlanStep.
        sess, binding = chat_plan.read("c1")
        assert sess is not None
        assert sess.project_id == "c1"
        assert [(s.id, s.kind, s.status) for s in sess.steps] == [
            ("chat-plan-1", chat_plan.PLAN_STEP_KIND, StepStatus.RUNNING.value)
        ]
        # ...and the session is now in the plan posture, which is what gates tools.
        assert chat._task_mode == "plan"
        assert binding["resume_task_mode"] == "agent"
        assert data["parked"] is False

    @pytest.mark.asyncio
    async def test_an_ordinary_turn_never_creates_a_plan_session(self, tmp_path):
        """The ONE send-path touchpoint is the turn-end hook, and it is a no-op with no
        session — so a quick task never grows a review gate."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat.append("assistant", "here is your answer", "msg msg-a")
        chat.drain()
        assert chat_plan.maybe_submit_plan_draft(state, chat) is False
        assert chat_plan.read("c1") == (None, {})
        assert chat._task_mode == "agent"
        async with TestClient(TestServer(_app(state))) as client:
            r = await client.get("/api/chat/sessions/c1/plan-session")
            assert (await r.json())["session"] is None

    def test_nothing_but_the_activate_endpoint_calls_activate(self):
        """Structural half of "manual-only": no send/turn/heuristic path may reach
        ``activate``. A behavioural test can only show that TODAY's send path doesn't;
        this shows no caller exists at all outside the endpoint's own module."""
        callers = sorted(
            p.relative_to(_SRC).as_posix()
            for p in _SRC.rglob("*.py")
            if "chat_plan.activate(" in p.read_text()
        )
        assert callers == [], callers
        own = (_SRC / "dashboard" / "chat_plan.py").read_text()
        # Exactly one call site, inside the activate endpoint.
        assert own.count("= activate(chat, running=") == 1


class TestEditableMarkdownArtifact:
    """Clause 1+2: the artifact is markdown, edited through ``PS.edit_artifact``."""

    @pytest.mark.asyncio
    async def test_the_plan_turns_reply_becomes_the_awaiting_review_artifact(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
        chat.append("assistant", "# Plan\n1. read\n2. write", "msg msg-a")
        chat.drain()
        assert chat_plan.maybe_submit_plan_draft(state, chat) is True
        sess, _ = chat_plan.read("c1")
        step = sess.steps[0]
        assert step.status == StepStatus.AWAITING_REVIEW.value
        assert step.artifact["markdown"] == "# Plan\n1. read\n2. write"
        assert chat_plan.awaiting_review("c1") == "chat-plan-1"

    @pytest.mark.asyncio
    async def test_editing_preserves_non_markdown_artifact_fields(self, tmp_path):
        """``PS.edit_artifact`` merges ONLY ``markdown`` (the structured fields are the
        projection source). A hand-rolled replace would drop the sibling key — so this
        asserts the chat surface really routes through the shared transition."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "draft", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            sess, binding = chat_plan.read("c1")
            sess.steps[0].artifact["structured"] = {"steps": ["a"]}
            chat_plan.write(sess, binding)

            r = await client.post(
                "/api/chat/sessions/c1/plan/edit",
                json={"step_id": "chat-plan-1", "markdown": "# my own plan"},
            )
            assert r.status == 200
        sess, _ = chat_plan.read("c1")
        assert sess.steps[0].artifact["markdown"] == "# my own plan"
        assert sess.steps[0].artifact["structured"] == {"steps": ["a"]}
        # Still awaiting review: editing is not approving.
        assert sess.steps[0].status == StepStatus.AWAITING_REVIEW.value

    @pytest.mark.asyncio
    async def test_editing_a_step_that_is_not_awaiting_review_is_refused(self, tmp_path):
        state = _make_state(tmp_path)
        _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")  # step is RUNNING
            r = await client.post(
                "/api/chat/sessions/c1/plan/edit",
                json={"step_id": "chat-plan-1", "markdown": "x"},
            )
            assert r.status == 409
            assert (await r.json())["error"]["code"] == "step_not_awaiting_review"


class TestApproveAndComment:
    """Clause 2: approve/comment route through ``approve_step``/``comment_step``."""

    @pytest.mark.asyncio
    async def test_approve_inherits_approve_steps_awaiting_review_rule(self, tmp_path):
        """``PS.approve_step`` only transitions an ``awaiting_review`` step. A chat-side
        re-implementation would almost certainly approve a RUNNING one."""
        state = _make_state(tmp_path)
        _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            r = await client.post(
                "/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert r.status == 409
            assert (await r.json())["error"]["code"] == "step_not_awaiting_review"
        sess, _ = chat_plan.read("c1")
        assert sess.steps[0].status == StepStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_comment_sends_the_step_back_for_a_redraft(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path)
        chat = _seed(state)
        seen = _no_dispatch(monkeypatch)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "draft plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            r = await client.post(
                "/api/chat/sessions/c1/plan/comment",
                json={"step_id": "chat-plan-1", "text": "too vague"},
            )
            assert r.status == 200
        sess, _ = chat_plan.read("c1")
        step = sess.steps[0]
        # comment_step's exact effects: the comment is threaded onto the step AND the
        # step goes back to RUNNING (a re-draft), not to approved.
        assert [c["text"] for c in step.comments] == ["too vague"]
        assert step.status == StepStatus.RUNNING.value
        assert len(seen) == 1 and "too vague" in seen[0]

    @pytest.mark.asyncio
    async def test_an_empty_comment_is_refused(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "draft", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            r = await client.post(
                "/api/chat/sessions/c1/plan/comment",
                json={"step_id": "chat-plan-1", "text": "   "},
            )
            assert r.status == 400
            assert (await r.json())["error"]["code"] == "comment_text_required"

    @pytest.mark.asyncio
    async def test_re_planning_appends_a_step_rather_than_forking_state(self, tmp_path):
        """Mid-task re-planning is the shared model's own "steps may grow" shape — a
        second activation extends the SAME session and keeps the first mode memory."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "plan v1", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            await client.post("/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"})
            await client.post("/api/chat/sessions/c1/plan/activate")
        sess, binding = chat_plan.read("c1")
        assert [s.id for s in sess.steps] == ["chat-plan-1", "chat-plan-2"]
        assert [s.status for s in sess.steps] == [
            StepStatus.APPROVED.value,
            StepStatus.RUNNING.value,
        ]
        assert sess.steps[1].title == "Re-plan"
        # The pre-plan mode is remembered once — a re-plan must not record "plan".
        assert binding["resume_task_mode"] == "agent"


class TestTheGateNotThePrompt:
    """Clause 4: the no-execute guarantee is the existing plan task-mode gate.

    Driven through the real ``NativeAgentRuntime`` so the assertion lands on
    ``_guard_and_invoke``'s ``task_mode_denies`` call, not on any prompt text.
    """

    @staticmethod
    def _runtime_bound_to(state, tool):
        """A real runtime whose task mode is written ONLY by the chat's task-mode path."""
        model = _ScriptedModel(
            [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c1",
                        title="write_file",
                        tool_input='{"path":"x","content":"y"}',
                    ),
                    AgentEvent(kind=EVENT_COMPLETE),
                ],
                [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
            ]
        )
        rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
        rt.set_approval_policy("yolo")  # the most permissive approval posture
        state.sessions.set_task_mode = lambda key, mode: rt.set_task_mode(mode)
        return rt

    @pytest.mark.asyncio
    async def test_a_mutating_tool_is_denied_while_awaiting_plan_approval(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        tool = _Tool(name="write_file", requires_approval=False)  # auto-approved
        rt = self._runtime_bound_to(state, tool)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "the plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
        assert chat_plan.awaiting_review("c1") == "chat-plan-1"

        await rt.start()
        seen = await _drain(rt, "write the file")
        result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
        assert "plan mode" in str(result.tool_output).lower()
        assert tool.invoked == []  # denied BEFORE invoke, despite yolo auto-approve

    @pytest.mark.asyncio
    async def test_the_task_mode_control_cannot_relax_the_gate_while_awaiting_review(
        self, tmp_path
    ):
        """If the composer pill could drop `plan` while a step's gate is open, the
        no-execute guarantee would be decorative. The exits are approve and cancel."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "the plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            r = await client.post("/api/chat/task-mode", json={"mode": "agent", "session": "c1"})
            assert r.status == 409
            body = await r.json()
            assert body["error"]["code"] == "plan_awaiting_approval"
            assert body["sessions"] == ["c1"]
        assert chat._task_mode == "plan"

    @pytest.mark.asyncio
    async def test_cancel_lifts_the_gate_and_restores_the_mode(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        tool = _Tool(name="write_file", requires_approval=False)
        rt = self._runtime_bound_to(state, tool)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "the plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            r = await client.post("/api/chat/sessions/c1/plan/cancel")
            assert r.status == 200
        assert chat._task_mode == "agent"
        assert chat_plan.read("c1") == (None, {})
        await rt.start()
        await _drain(rt, "write the file")
        assert tool.invoked == [{"path": "x", "content": "y"}]  # the gate is gone

    @pytest.mark.asyncio
    async def test_approving_lifts_the_gate(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        tool = _Tool(name="write_file", requires_approval=False)
        rt = self._runtime_bound_to(state, tool)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "the plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            r = await client.post(
                "/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert r.status == 200
            body = await r.json()
            assert body["complete"] is True and body["task_mode"] == "agent"
        assert chat._task_mode == "agent"
        await rt.start()
        await _drain(rt, "write the file")
        assert tool.invoked == [{"path": "x", "content": "y"}]


class TestMidTurnParkAndResume:
    """Clause 5: mid-turn activation parks the run without losing the transcript, and
    approval resumes it."""

    @staticmethod
    def _running(state, chat):
        """Make the chat genuinely `running` (session.running reads .task)."""
        state.sessions.stop_turn = _AsyncRecorder()
        chat.task = asyncio.get_running_loop().create_task(asyncio.sleep(30))
        return chat.task

    @pytest.mark.asyncio
    async def test_activating_mid_turn_parks_and_keeps_the_whole_transcript(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat.append("user", "u2", "msg msg-u")
        chat.append("assistant", "partial answer", "msg msg-a")
        chat.drain()
        before = [(m["role"], m["content"]) for m in chat.messages]
        task = self._running(state, chat)
        try:
            async with TestClient(TestServer(_app(state))) as client:
                r = await client.post("/api/chat/sessions/c1/plan/activate")
                assert r.status == 200
                assert (await r.json())["parked"] is True
        finally:
            task.cancel()
        # Parking is a posture change plus a stop request — never a truncation.
        assert [(m["role"], m["content"]) for m in chat.messages] == before
        _sess, binding = chat_plan.read("c1")
        assert binding["parked"] is True
        assert binding["parked_messages"] == len(before)
        assert chat._task_mode == "plan"
        # The in-flight turn was asked to stop cooperatively, not killed.
        assert state.sessions.stop_turn.calls and state.sessions.stop_turn.calls[0][1] is False

    @pytest.mark.asyncio
    async def test_approval_resumes_the_parked_run_and_continues_the_transcript(
        self, tmp_path, monkeypatch
    ):
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat.append("user", "u2", "msg msg-u")
        chat.append("assistant", "partial answer", "msg msg-a")
        chat.drain()
        before = [(m["role"], m["content"]) for m in chat.messages]
        seen = _no_dispatch(monkeypatch)
        task = self._running(state, chat)
        try:
            async with TestClient(TestServer(_app(state))) as client:
                await client.post("/api/chat/sessions/c1/plan/activate")
                chat.append("assistant", "# Approved plan\n- step one", "msg msg-a")
                chat.drain()
                chat_plan.maybe_submit_plan_draft(state, chat)
                r = await client.post(
                    "/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"}
                )
                assert r.status == 200
                body = await r.json()
        finally:
            task.cancel()
        assert body["resumed"] is True and body["task_mode"] == "agent"
        # Resumed, not restarted: the pre-park transcript is still the prefix, and the
        # continuation was appended after it (never in place of it).
        current = [(m["role"], m["content"]) for m in chat.messages]
        assert current[: len(before)] == before
        assert current[-1][0] == "user" and "# Approved plan" in current[-1][1]
        # One turn dispatched, carrying the approved plan as a continuation.
        assert len(seen) == 1
        assert "# Approved plan\n- step one" in seen[0]
        assert "do not re-plan" in seen[0]
        # The park is settled — a later approval must not resume twice.
        _sess, binding = chat_plan.read("c1")
        assert "parked" not in binding

    @pytest.mark.asyncio
    async def test_approving_a_plan_that_was_not_parked_does_not_dispatch(
        self, tmp_path, monkeypatch
    ):
        """Activation outside a turn has nothing to resume — approval just lifts the
        gate. Dispatching anyway would send an unasked-for turn."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        seen = _no_dispatch(monkeypatch)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
            chat.append("assistant", "the plan", "msg msg-a")
            chat.drain()
            chat_plan.maybe_submit_plan_draft(state, chat)
            before = len(chat.messages)
            r = await client.post(
                "/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert (await r.json())["resumed"] is False
        assert seen == []
        assert len(chat.messages) == before


class TestADraftMustComeFromItsOwnTurn:
    """A step may only be drafted by a turn that ran AFTER it opened.

    Found by driving the real UI, not by these tests: on a mid-task re-plan the turn-end
    hook scanned the whole transcript backwards and handed `chat-plan-2` the **previous**
    step's draft, while the re-plan turn's actual reply ("I will answer your question
    directly in one step.") was dropped. The user then reviews — and approves — a plan
    they never asked for, and `_resume_prompt` carries that stale text into the resumed
    run. 26 unit tests passed with that live, because each seeded exactly one candidate
    reply, so scanning backwards could not pick the wrong one.
    """

    @pytest.mark.asyncio
    async def test_a_previous_turns_reply_is_not_reused_as_a_new_steps_draft(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
        chat.append("assistant", "### FIRST PLAN\n1. one", "msg msg-a")
        chat.drain()
        assert chat_plan.maybe_submit_plan_draft(state, chat) is True
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"})
            # A second activation: the mid-task re-plan.
            await client.post("/api/chat/sessions/c1/plan/activate")

        # No new reply yet — the hook must NOT reach back for the first plan.
        assert chat_plan.maybe_submit_plan_draft(state, chat) is False
        sess, _ = chat_plan.read("c1")
        assert sess.steps[1].status == StepStatus.RUNNING.value
        assert sess.steps[1].artifact in ({}, None)

        # The re-plan turn's own reply is what lands.
        chat.append("assistant", "### SECOND PLAN\n1. two", "msg msg-a")
        chat.drain()
        assert chat_plan.maybe_submit_plan_draft(state, chat) is True
        sess, _ = chat_plan.read("c1")
        assert sess.steps[1].artifact["markdown"] == "### SECOND PLAN\n1. two"
        # And the approved first step is untouched.
        assert sess.steps[0].artifact["markdown"] == "### FIRST PLAN\n1. one"

    @pytest.mark.asyncio
    async def test_the_boundary_is_recorded_at_activation(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        async with TestClient(TestServer(_app(state))) as client:
            await client.post("/api/chat/sessions/c1/plan/activate")
        _, binding = chat_plan.read("c1")
        # `_seed` leaves two messages, so the boundary is the transcript length at open.
        assert binding["draft_from"] == len(chat.messages)

    def test_a_corrupt_boundary_does_not_wedge_the_draft(self, tmp_path):
        """A garbage `draft_from` must degrade to "scan everything", never crash the hook."""
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat_plan.activate(chat, running=False)
        sess, binding = chat_plan.read("c1")
        binding["draft_from"] = "not-a-number"
        chat_plan.write(sess, binding)
        chat.append("assistant", "### PLAN\n1. one", "msg msg-a")
        chat.drain()
        assert chat_plan.maybe_submit_plan_draft(state, chat) is True


class TestSidecarRobustness:
    """Storage contract: reads tolerate missing/corrupt and fail OPEN (a corrupt file
    must not wedge the chat behind a gate it can no longer approve out of)."""

    def test_a_corrupt_sidecar_reads_as_no_plan_session(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat_plan.activate(chat, running=False)
        path = chat_plan._path("c1")
        assert path.exists()
        path.write_text("{not json")
        assert chat_plan.read("c1") == (None, {})
        assert chat_plan.awaiting_review("c1") == ""
        assert chat_plan.in_progress("c1") is False

    def test_the_sidecar_lands_under_config_dir(self, tmp_path):
        state = _make_state(tmp_path)
        chat = _seed(state)
        chat_plan.activate(chat, running=False)
        from personalclaw.config.loader import config_dir

        assert chat_plan._path("c1") == config_dir() / "chat_plans" / "c1.json"


class _AsyncRecorder:
    """An awaitable stand-in for SessionManager.stop_turn that records its args."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def __call__(self, key, *, force=False, on_soft=None, on_hard=None, **kw):
        self.calls.append((key, force))
        return None
