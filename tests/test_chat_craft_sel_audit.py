"""CC-6 — the Security Event Log audit for CHAT-CRAFT's nine chat-surface mechanics.

The wrap-up atom's ``done_when`` reads: *"SEL shows one event per security-relevant
action across all seven mechanics (snip rides existing upload SEL)"*. It was written when
the surface had seven; the 2026-07-29 amendment added two more — **Branch** (``CC-7``) and
**chat plan mode** (``CC-8``) — and both carry server actions, so the audit here covers
**nine**. That sentence has three failure modes and this suite is written against all
three, because each one leaves the surface looking audited when it is not:

  1. **ZERO.** A rail that greps for a ``sel()`` call site passes on a call that never
     executes. So every assertion below drives the REAL handler and counts what landed in
     the log — a count of 0 reds.
  2. **MORE THAN ONE.** A duplicate is not cosmetic: two events for one action means two
     writers, and the audit page then over-reports every rewind a user performs. So the
     assertion is ``== 1``, not ``>= 1``.
  3. **A SECOND EVENT FOR SNIP.** The clause is explicit that snip *rides the existing
     upload SEL*. Minting a ``chat.snip`` beside ``upload.file`` would double-count one
     action and split the audit trail for attachments in two. So snip asserts exactly one
     ``upload.file`` **and** that nothing snip-shaped was invented.

**Three of the nine are client-only, and their honest count is zero.** Find, quote-reply
and the streaming reveal never leave the browser: find scans `turns[]` already in memory,
quote-reply writes into the composer, and the reveal only paces text the turn already
delivered. There is no endpoint to call and therefore no security-relevant action to
record — logging one would be noise, not coverage. That is asserted structurally (no
endpoint exists, and the modules make no request) rather than papered over with a count,
because "we checked and it is correctly silent" and "we forgot" look identical in a
report that only lists non-zero numbers.

The SEL these tests read is the per-test temp one — ``tests/conftest.py`` reroutes
``sel._default_dir`` and resets the singleton, so ``sel().recent()`` sees only events this
test produced. A leak into the real ``~/.personalclaw`` would fail conftest's own rail.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard import chat_plan
from personalclaw.dashboard.chat_followups import _maybe_followups
from personalclaw.dashboard.chat_fork import (
    api_chat_session_fork,
    api_chat_session_fork_rewound,
)
from personalclaw.dashboard.chat_handlers import api_chat_session_interrupt
from personalclaw.dashboard.chat_regenerate import api_chat_session_edit_resend
from personalclaw.dashboard.handlers.files import api_upload_file
from personalclaw.sel import sel

# The repo root, for the structural (client-only) assertions.
_ROOT = Path(__file__).resolve().parents[1]
_WEB = _ROOT / "web" / "src"


def _ops(operation: str) -> list[dict]:
    """Every logged event whose ``operation`` is *operation*.

    ``log_api_access(operation=…)`` and ``log_tool_invocation(tool_name=…)`` both land in
    the same ``operation`` field (``sel.py``), so one accessor covers both kinds.
    """
    return [e for e in sel().recent(limit=500) if e.get("operation") == operation]


def _count(operation: str) -> int:
    return len(_ops(operation))


class _FakeTask:
    """A task that looks running to ``session.running`` (a read-only property over
    ``task is not None and not task.done()``) — the same fixture ``test_interrupt.py``
    uses, so "running" means here what it means there."""

    def done(self) -> bool:
        return False


async def _noop_run_chat(state, session, msg, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _mock_run_chat(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.chat_regenerate._run_chat", _noop_run_chat)


def _seed(state, name: str, n_turns: int):
    session = state.get_or_create_session(name)
    for i in range(n_turns):
        session.append("user", f"q{i}", "msg msg-u", ts=f"2026-06-30T05:0{i}:00+00:00")
        session.append("assistant", f"a{i}", "msg msg-a", ts=f"2026-06-30T05:0{i}:30+00:00")
    session.drain()
    return session


# ── 1. Rewind — the one mechanic that rewrites the transcript ────────────────────────


class TestRewindSel:
    """Mechanic 1 (S1a): true rewind. Security-relevant because it TRUNCATES a
    persisted transcript and resets the provider — the most destructive of the nine."""

    @pytest.mark.asyncio
    async def test_one_rewind_event_per_rewind_and_never_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 3)
        app = web.Application()
        app["state"] = state
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )

        assert _count("chat.rewind") == 0, "a stale event exists before the action ran"
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"ts": session.messages[0]["ts"], "content": "edited q0", "rewind": True},
            )
            assert r.status == 200, await r.text()

        events = _ops("chat.rewind")
        # NON-ZERO: the action really did log. EXACTLY ONE: no second writer.
        assert len(events) == 1, f"expected exactly 1 chat.rewind event, got {len(events)}"
        assert events[0]["outcome"] == "allowed"
        assert events[0]["resources"] == "s1"
        # The non-rewind sibling must not also fire — one action, one vocabulary.
        assert _count("chat.edit_resend") == 0

    @pytest.mark.asyncio
    async def test_two_rewinds_log_two_events_not_one_batched(self, tmp_path, monkeypatch):
        """The count tracks ACTIONS. A per-session-once event would under-report."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 4)
        app = web.Application()
        app["state"] = state
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )
        async with TestClient(TestServer(app)) as client:
            for _ in range(2):
                target = next(m for m in session.messages if m.get("role") == "user")
                r = await client.post(
                    "/api/chat/sessions/s1/edit-resend",
                    json={"ts": target["ts"], "content": "edited", "rewind": True},
                )
                assert r.status == 200, await r.text()
        assert _count("chat.rewind") == 2

    @pytest.mark.asyncio
    async def test_restore_as_fork_logs_exactly_one_fork_rewound(self, tmp_path, monkeypatch):
        """Restoring a rewound tail COPIES a transcript into a new slot — its own
        security-relevant action, and its own single event."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        session = _seed(state, "s1", 2)
        # Stamp a rewound tail on the first user message, the shape rewind leaves behind.
        session.messages[0]["rewound"] = [
            {
                "ts": "2026-06-30T05:00:10+00:00",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "discarded answer",
                        "ts": "2026-06-30T05:00:11+00:00",
                    }
                ],
            }
        ]
        app = web.Application()
        app["state"] = state
        app.router.add_post(
            "/api/chat/sessions/{session}/fork-rewound", api_chat_session_fork_rewound
        )
        async with TestClient(TestServer(app)) as client:
            r = await client.post(
                "/api/chat/sessions/s1/fork-rewound",
                json={"index": 0},  # visible index of the edited turn; latest tail by default
            )
            assert r.status == 200, await r.text()
        events = _ops("chat.fork_rewound")
        assert len(events) == 1, f"expected exactly 1 chat.fork_rewound, got {len(events)}"
        assert "from=s1" in events[0]["resources"]


# ── 2. Branch — duplicate a whole conversation into a new session ─────────────────────


class TestBranchSel:
    """Mechanic 2 (amendment (a), CC-7): Branch. Security-relevant because it COPIES a
    persisted transcript into a second session — the event is what tells a user (or an
    auditor) that another copy of a conversation now exists, and where it was cut."""

    @staticmethod
    def _app(state) -> web.Application:
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/sessions/{session}/fork", api_chat_session_fork)
        return app

    @pytest.mark.asyncio
    async def test_one_session_fork_event_per_branch_and_never_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _seed(state, "s1", 3)

        assert _count("chat.session_fork") == 0, "a stale event exists before the action ran"
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/chat/sessions/s1/fork", json={"at_message_index": 2})
            assert r.status == 200, await r.text()
            child = (await r.json())["key"]

        events = _ops("chat.session_fork")
        assert len(events) == 1, f"expected exactly 1 chat.session_fork, got {len(events)}"
        assert events[0]["outcome"] == "allowed"
        # WHERE the cut was made is part of the record: an entry that cannot say how much of
        # the conversation was duplicated cannot answer the only question asked of it.
        assert f"from=s1,to={child}" in events[0]["resources"]
        assert "at_index=2" in events[0]["resources"]
        # Branch must not borrow rewind's vocabulary — "duplicated a conversation" and
        # "restored a discarded ending" are different events on the audit page.
        assert _count("chat.fork_rewound") == 0

    @pytest.mark.asyncio
    async def test_branching_the_same_message_twice_logs_two_events(self, tmp_path, monkeypatch):
        """The amendment's *"the same message may be branched repeatedly"* clause, counted.
        Two branches are two copies; a per-session-once event would under-report the
        second."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _seed(state, "s1", 2)
        async with TestClient(TestServer(self._app(state))) as client:
            children = []
            for _ in range(2):
                r = await client.post("/api/chat/sessions/s1/fork", json={"at_message_index": 1})
                assert r.status == 200, await r.text()
                children.append((await r.json())["key"])
        assert children[0] != children[1], "the second branch reused the first one's slot"
        assert _count("chat.session_fork") == 2

    @pytest.mark.asyncio
    async def test_a_refused_branch_records_the_refusal_not_an_allow(self, tmp_path, monkeypatch):
        """The other direction, and here the honest count is NOT zero: an incognito chat
        cannot be branched, and the endpoint records the refusal *with its reason*. The one
        thing that must never appear is an ``allowed`` event for a copy never made."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seed(state, "s1", 1)
        session.memory_mode = "incognito"
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/chat/sessions/s1/fork", json={})
            assert r.status == 400, await r.text()
        events = _ops("chat.session_fork")
        assert len(events) == 1, f"expected exactly 1 chat.session_fork, got {len(events)}"
        assert [e["outcome"] for e in events] == ["denied"]
        assert "memory_mode=incognito" in events[0]["resources"]


# ── 3. Chat plan mode — the review gate over the shared walkthrough ──────────────────


class TestPlanModeSel:
    """Mechanic 3 (amendment (b), CC-8): plan mode. TWO security-relevant transitions, not
    one — activation puts the chat under the read-only ``plan`` tool gate, and approval
    takes it back out of that posture and runs the plan. Each is exactly one event, and the
    panel's text-only controls (edit, comment) add none."""

    @staticmethod
    def _app(state) -> web.Application:
        app = web.Application()
        app["state"] = state
        app.router.add_post(
            "/api/chat/sessions/{session}/plan/activate", chat_plan.api_chat_plan_activate
        )
        app.router.add_post("/api/chat/sessions/{session}/plan/edit", chat_plan.api_chat_plan_edit)
        app.router.add_post(
            "/api/chat/sessions/{session}/plan/approve", chat_plan.api_chat_plan_approve
        )
        return app

    @pytest.mark.asyncio
    async def test_activation_logs_exactly_one_plan_activate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        chat = _seed(state, "s1", 1)

        assert _count("chat.plan_activate") == 0
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/chat/sessions/s1/plan/activate")
            assert r.status == 200, await r.text()
        # The posture really changed — otherwise the event describes a gate that isn't on.
        assert chat._task_mode == "plan"

        events = _ops("chat.plan_activate")
        assert len(events) == 1, f"expected exactly 1 chat.plan_activate, got {len(events)}"
        assert events[0]["outcome"] == "enabled"
        assert "session=s1" in events[0]["resources"]
        assert "parked=False" in events[0]["resources"]
        # Opening the gate is not approving anything.
        assert _count("chat.plan_approve") == 0

    @pytest.mark.asyncio
    async def test_activating_mid_turn_records_the_park_in_the_same_one_event(
        self, tmp_path, monkeypatch
    ):
        """Parking a running turn is part of the SAME action, not a second one: the entry
        says a run was parked, and there is still exactly one event."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        chat = _seed(state, "s1", 1)
        chat.task = _FakeTask()  # → chat.running is True
        chat._stop_state = "idle"
        state.sessions.stop_turn = AsyncMock(return_value="soft")
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/chat/sessions/s1/plan/activate")
            assert r.status == 200, await r.text()
            assert (await r.json())["parked"] is True
        events = _ops("chat.plan_activate")
        assert len(events) == 1, f"expected exactly 1 chat.plan_activate, got {len(events)}"
        assert "parked=True" in events[0]["resources"]

    @pytest.mark.asyncio
    async def test_approval_logs_one_event_and_the_hand_edit_stays_silent(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        chat = _seed(state, "s1", 1)
        async with TestClient(TestServer(self._app(state))) as client:
            assert (await client.post("/api/chat/sessions/s1/plan/activate")).status == 200
            # The plan-mode turn's reply IS the artifact, handed over by the REAL turn-end
            # hook — so the gate opens on the path production opens it on.
            chat.append("assistant", "## Plan\n1. read\n2. report", "msg msg-a")
            chat.drain()
            assert chat_plan.maybe_submit_plan_draft(state, chat) is True
            r = await client.post(
                "/api/chat/sessions/s1/plan/edit",
                json={"step_id": "chat-plan-1", "markdown": "## Plan\n1. read only"},
            )
            assert r.status == 200, await r.text()
            r = await client.post(
                "/api/chat/sessions/s1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert r.status == 200, await r.text()
            assert (await r.json())["complete"] is True

        approvals = _ops("chat.plan_approve")
        assert len(approvals) == 1, f"expected exactly 1 chat.plan_approve, got {len(approvals)}"
        assert approvals[0]["outcome"] == "allowed"
        assert "step=chat-plan-1" in approvals[0]["resources"]
        assert "complete=True" in approvals[0]["resources"]
        # The whole gate is TWO events: opening it, and approving out of it. The hand-edit
        # in between only rewrote text still awaiting review, so it adds no third — and
        # neither transition fired twice.
        ops = sorted(e.get("operation", "") for e in sel().recent(limit=500))
        assert ops == ["chat.plan_activate", "chat.plan_approve"], ops

    @pytest.mark.asyncio
    async def test_an_approval_the_state_machine_refuses_logs_nothing(self, tmp_path, monkeypatch):
        """The vacuity check: a step that is not awaiting review is refused (409). An event
        here would claim a plan was approved and a read-only posture dropped when the
        walkthrough never opened the gate."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        _seed(state, "s1", 1)
        async with TestClient(TestServer(self._app(state))) as client:
            assert (await client.post("/api/chat/sessions/s1/plan/activate")).status == 200
            r = await client.post(
                "/api/chat/sessions/s1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert r.status == 409, await r.text()
        assert _count("chat.plan_approve") == 0
        assert _count("chat.plan_activate") == 1


# ── 4. Queue interrupt-now ───────────────────────────────────────────────────────────


class TestInterruptSel:
    """Mechanic 4 (S1b): interrupt-now. Security-relevant because it CANCELS a running
    provider turn and promotes someone else's queued message ahead of it."""

    @pytest.mark.asyncio
    async def test_one_interrupt_event_and_the_promoted_id_is_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seed(state, "s1", 1)
        session.task = _FakeTask()  # → session.running is True
        session._stop_state = "idle"
        qid_first = session.queue_append("first queued")
        qid_second = session.queue_append("second queued")
        assert isinstance(qid_first, str) and isinstance(qid_second, str)
        state.sessions.stop_turn = AsyncMock(return_value="soft")
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/sessions/{session}/interrupt", api_chat_session_interrupt)

        assert _count("dashboard_interrupt") == 0
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/s1/interrupt", json={"queue_id": qid_second})
            assert r.status == 200, await r.text()

        events = _ops("dashboard_interrupt")
        assert len(events) == 1, f"expected exactly 1 dashboard_interrupt, got {len(events)}"
        assert events[0]["outcome"] == "soft"
        assert events[0]["metadata"]["session"] == "s1"
        # /stop is the OTHER verb over the same session — it must not also fire, or the
        # audit page cannot tell "cancelled and kept the queue" from "cancelled and
        # cleared it".
        assert _count("dashboard_stop") == 0

    @pytest.mark.asyncio
    async def test_a_refused_interrupt_logs_nothing(self, tmp_path, monkeypatch):
        """The vacuity check in the other direction: an interrupt that is REFUSED
        (empty queue → 400) must not leave an event claiming a turn was cancelled."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _seed(state, "s1", 1)
        session.task = _FakeTask()  # → session.running is True
        session._stop_state = "idle"
        state.sessions.stop_turn = AsyncMock(return_value="soft")
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/sessions/{session}/interrupt", api_chat_session_interrupt)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/chat/sessions/s1/interrupt", json={})
            assert r.status == 400
        assert _count("dashboard_interrupt") == 0


# ── 7. Follow-up chips ───────────────────────────────────────────────────────────────


def _mock_bg_stream(state, text):
    client = MagicMock()
    client.reject_tool = AsyncMock()
    client._history = MagicMock()

    async def _stream(prompt):
        yield __import__("personalclaw.llm.base", fromlist=["LLMEvent"]).LLMEvent(
            kind="text_chunk", text=text
        )
        yield __import__("personalclaw.llm.base", fromlist=["LLMEvent"]).LLMEvent(kind="complete")

    client.stream = _stream
    state.sessions.get_or_create = AsyncMock(return_value=(client, False, False))
    state.sessions.release = MagicMock()


class TestFollowupChipsSel:
    """Mechanic 7 (S3a): follow-up chips. Security-relevant because it spends a MODEL
    CALL on the user's budget without them asking for it — the audit page is how they
    find out it happened."""

    @pytest.mark.asyncio
    async def test_one_event_per_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "how do I read a file?", "msg msg-u", broadcast=False)
        session.append("assistant", "use open().", "msg msg-a", broadcast=False)
        session.drain()
        _mock_bg_stream(state, '["Show an example", "How do I test it?"]')

        assert _count("chat_followups") == 0
        await _maybe_followups(state, session)
        events = _ops("chat_followups")
        assert len(events) == 1, f"expected exactly 1 chat_followups, got {len(events)}"
        assert events[0]["metadata"]["count"] == 2
        assert events[0]["metadata"]["session"] == "s1"

    @pytest.mark.asyncio
    async def test_a_generation_that_produced_nothing_logs_nothing(self, tmp_path, monkeypatch):
        """No chips means no model output was shown to the user, so there is nothing to
        report. An event here would claim a suggestion the user never saw."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("s1")
        session.append("user", "hi", "msg msg-u", broadcast=False)
        session.append("assistant", "hello", "msg msg-a", broadcast=False)
        session.drain()
        _mock_bg_stream(state, "not json at all")
        await _maybe_followups(state, session)
        assert _count("chat_followups") == 0


# ── 9. Screen-snip — rides the EXISTING upload SEL ───────────────────────────────────


class TestSnipRidesUploadSel:
    """Mechanic 9 (S4a): screen-snip. The cropped PNG goes through ``api.uploadFiles``,
    so the security-relevant action is the FILE WRITE, already logged as
    ``upload.file``. The clause is that snip rides that event — so the assertion is one
    ``upload.file`` and no snip-specific second event."""

    @staticmethod
    def _app(tmp_path, monkeypatch) -> web.Application:
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers.files._upload_dir", lambda: tmp_path / "uploads"
        )
        monkeypatch.setattr(
            "personalclaw.dashboard.attachment_extract.get_extractor",
            lambda: type("E", (), {"start": lambda self, *a, **k: None})(),
        )
        app = web.Application()
        app.router.add_post("/api/upload/file", api_upload_file)
        return app

    @pytest.mark.asyncio
    async def test_a_snip_upload_logs_exactly_one_upload_file_event(self, tmp_path, monkeypatch):
        app = self._app(tmp_path, monkeypatch)
        # The bytes SnipOverlay produces: one PNG named like the crop it came from.
        png = b"\x89PNG\r\n\x1a\n" + b"snip-pixels" * 8
        assert _count("upload.file") == 0
        async with TestClient(TestServer(app)) as client:
            form = FormData()
            form.add_field("file", png, filename="screen-snip-2026.png", content_type="image/png")
            r = await client.post("/api/upload/file", data=form)
            assert r.status == 200, await r.text()
            body = await r.json()
            assert body["paths"] and body["paths"][0].endswith("screen-snip-2026.png")

        events = _ops("upload.file")
        assert len(events) == 1, f"expected exactly 1 upload.file event, got {len(events)}"
        assert events[0]["outcome"] == "success"
        assert events[0]["resources"] == "files:1"

    @pytest.mark.asyncio
    async def test_snip_mints_no_second_event_of_its_own(self, tmp_path, monkeypatch):
        """The explicit "rides the existing upload SEL" clause, measured: after a snip
        upload the ONLY thing in the log is the upload event. A ``chat.snip`` /
        ``screen_capture`` / ``snip`` operation appearing here means one action is being
        double-counted."""
        app = self._app(tmp_path, monkeypatch)
        async with TestClient(TestServer(app)) as client:
            form = FormData()
            form.add_field(
                "file", b"\x89PNG\r\n\x1a\npx", filename="snip.png", content_type="image/png"
            )
            assert (await client.post("/api/upload/file", data=form)).status == 200

        ops = [e.get("operation", "") for e in sel().recent(limit=500)]
        assert ops == ["upload.file"], f"snip logged more than the upload event: {ops}"
        for invented in ("chat.snip", "snip", "screen_capture", "screenshot", "display_capture"):
            assert invented not in ops


# ── 5, 6, 8. The client-only three — correctly silent, asserted structurally ─────────


class TestClientOnlyMechanicsAreCorrectlySilent:
    """Find (5), quote-reply (6) and the streaming reveal (8) have no server action, so
    zero SEL events is the RIGHT answer, not a gap.

    Asserted structurally because a count of zero proves nothing on its own: it reads the
    same whether the mechanic is client-only or whether its event was dropped. What can
    be proved is that there is nothing to log — no endpoint, and no request from the
    module that implements it."""

    def test_find_scans_memory_and_never_calls_the_server(self):
        for name in ("findMatches.ts", "FindBar.tsx"):
            src = (_WEB / "pages" / "chat" / name).read_text(encoding="utf-8")
            assert "api." not in src, f"{name} gained a server call — it now needs SEL cover"
            assert "fetch(" not in src, f"{name} gained a fetch — it now needs SEL cover"

    def test_no_find_endpoint_exists_to_audit(self):
        server = (_ROOT / "src" / "personalclaw" / "dashboard" / "server.py").read_text(
            encoding="utf-8"
        )
        # SESSION-MANAGEMENT owns CROSS-session search; in-conversation find is a
        # client-side scan of already-hydrated turns and adds no route.
        assert "find-in-conversation" not in server
        assert "/api/chat/sessions/{session}/find" not in server

    def test_the_streaming_reveal_only_paces_text_already_delivered(self):
        src = (_WEB / "pages" / "chat" / "useStreamCoalescer.ts").read_text(encoding="utf-8")
        assert "fetch(" not in src
        assert "api." not in src
        # It is a reveal-cadence transform over chunks the turn already sent; the turn
        # itself is what the SEL records.
        assert "CoalescerCore" in src

    def test_quote_reply_writes_into_the_composer_not_over_the_wire(self):
        page = (_WEB / "pages" / "ChatPage.tsx").read_text(encoding="utf-8")
        assert "function quoteToComposer" in page or "const quoteToComposer" in page
        # The quote path's only effect is local composer state — the SEND that follows is
        # an ordinary turn, already covered by the turn's own logging.
        idx = page.index("quoteToComposer")
        body = page[idx : idx + 900]
        assert "api." not in body, "quote-reply started calling the server"


# ── The audit as a whole: every nine accounted for, none double-writing ─────────────


class TestTheAuditIsComplete:
    #: Every operation the nine mechanics emit, mapped to the module that emits it. This is
    #: the audit's contract in one place: one distinct name per action, and the name the
    #: user-facing guide prints is the name the code actually writes.
    _EMITTERS = {
        "chat.rewind": "dashboard/chat_regenerate.py",
        "chat.fork_rewound": "dashboard/chat_fork.py",
        "chat.session_fork": "dashboard/chat_fork.py",
        "chat.plan_activate": "dashboard/chat_plan.py",
        "chat.plan_approve": "dashboard/chat_plan.py",
        "dashboard_interrupt": "dashboard/chat_handlers.py",
        "chat_followups": "dashboard/chat_followups.py",
        "upload.file": "dashboard/handlers/files.py",
    }

    def test_every_operation_name_is_distinct_and_written_where_it_is_claimed(self):
        """Eight actions, eight names, each present in the module that owns it.

        Distinctness is what lets the audit page tell a rewind from a branch, or an
        interrupt from a stop. The second half is the part that keeps this table from
        becoming fiction: a name that exists only here would describe a log nobody writes,
        which is exactly how a renamed operation slips past a suite of green tests.
        """
        assert len(set(self._EMITTERS)) == len(self._EMITTERS) == 8
        for op, rel in self._EMITTERS.items():
            src = (_ROOT / "src" / "personalclaw" / rel).read_text(encoding="utf-8")
            assert f'"{op}"' in src, f"{op} is not emitted anywhere in {rel}"

    def test_the_guide_documents_all_nine_mechanics_and_everything_they_record(self):
        """CC-6's docs clause as a rail rather than a promise.

        The guide is the only place a user learns what the chat surface can do and what it
        records. Two mechanics shipped *after* it was written (Branch, plan mode) and it
        went on saying "seven" with three operations missing from its table — a wrong count
        in the one document whose job is the count. So the count, the section numbering and
        every recorded operation are asserted here, beside the audit they describe.
        """
        guide = (_ROOT / "docs" / "guides" / "chat-surface.md").read_text(encoding="utf-8")
        numbered = re.findall(r"(?m)^## (\d+)\. ", guide)
        assert numbered == [str(i) for i in range(1, 10)], numbered
        assert "nine things the chat surface can do" in guide
        assert "of these nine change something a security log" in guide
        for op in self._EMITTERS:
            assert f"`{op}`" in guide, f"the guide's recording table never mentions {op}"

    def test_the_two_newest_mechanics_are_named_in_the_guide_as_the_ui_names_them(self):
        """The guide's "Where:" line has to match the control a user is hunting for. Both
        new mechanics are reached by a label, so the label is checked against the frontend
        that renders it — a renamed affordance leaves the guide pointing at nothing."""
        guide = (_ROOT / "docs" / "guides" / "chat-surface.md").read_text(encoding="utf-8")
        actions = (_WEB / "pages" / "chat" / "MessageActions.tsx").read_text(encoding="utf-8")
        page = (_WEB / "pages" / "ChatPage.tsx").read_text(encoding="utf-8")
        assert 'label="Branch from here"' in actions and "**Branch from here**" in guide
        assert 'label="Plan this first"' in page and "**Plan this first**" in guide

    @pytest.mark.asyncio
    async def test_the_whole_log_after_one_of_each_is_exactly_seven_events(
        self, tmp_path, monkeypatch
    ):
        """The end-to-end count. One rewind + one restore-as-fork + one branch + one plan
        activation + one plan approval + one interrupt + one snip upload = SEVEN events, no
        more. This is the assertion a duplicate writer anywhere in the nine fails, and the
        assertion a dropped emitter fails."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.handlers.files._upload_dir", lambda: tmp_path / "uploads"
        )
        monkeypatch.setattr(
            "personalclaw.dashboard.attachment_extract.get_extractor",
            lambda: type("E", (), {"start": lambda self, *a, **k: None})(),
        )
        state = _make_state(tmp_path)
        state.sessions.reset = AsyncMock()
        state.sessions.stop_turn = AsyncMock(return_value="soft")
        session = _seed(state, "s1", 3)
        session.messages[0]["rewound"] = [
            {
                "ts": "2026-06-30T05:00:10+00:00",
                "messages": [
                    {"role": "assistant", "content": "old", "ts": "2026-06-30T05:00:11+00:00"}
                ],
            }
        ]
        app = web.Application()
        app["state"] = state
        app.router.add_post(
            "/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend
        )
        app.router.add_post(
            "/api/chat/sessions/{session}/fork-rewound", api_chat_session_fork_rewound
        )
        app.router.add_post("/api/chat/sessions/{session}/fork", api_chat_session_fork)
        app.router.add_post(
            "/api/chat/sessions/{session}/plan/activate", chat_plan.api_chat_plan_activate
        )
        app.router.add_post(
            "/api/chat/sessions/{session}/plan/approve", chat_plan.api_chat_plan_approve
        )
        app.router.add_post("/api/chat/sessions/{session}/interrupt", api_chat_session_interrupt)
        app.router.add_post("/api/upload/file", api_upload_file)

        async with TestClient(TestServer(app)) as client:
            # restore-as-fork first, while the rewound tail is still on message 0
            r = await client.post(
                "/api/chat/sessions/s1/fork-rewound",
                json={"index": 0},  # visible index of the edited turn; latest tail by default
            )
            assert r.status == 200, await r.text()
            r = await client.post(
                "/api/chat/sessions/s1/edit-resend",
                json={"ts": session.messages[0]["ts"], "content": "edited", "rewind": True},
            )
            assert r.status == 200, await r.text()
            r = await client.post("/api/chat/sessions/s1/fork", json={"at_message_index": 0})
            assert r.status == 200, await r.text()
            # Plan mode BEFORE the turn is made to look running, so this activation is the
            # ordinary (unparked) one and the interrupt below still has a turn to cancel.
            assert (await client.post("/api/chat/sessions/s1/plan/activate")).status == 200
            session.append("assistant", "## Plan\n1. read", "msg msg-a")
            session.drain()
            assert chat_plan.maybe_submit_plan_draft(state, session) is True
            r = await client.post(
                "/api/chat/sessions/s1/plan/approve", json={"step_id": "chat-plan-1"}
            )
            assert r.status == 200, await r.text()
            session.task = _FakeTask()  # → session.running is True
            session._stop_state = "idle"
            session.queue_append("queued")
            r = await client.post("/api/chat/sessions/s1/interrupt", json={})
            assert r.status == 200, await r.text()
            form = FormData()
            form.add_field(
                "file", b"\x89PNG\r\n\x1a\npx", filename="snip.png", content_type="image/png"
            )
            assert (await client.post("/api/upload/file", data=form)).status == 200

        ops = sorted(e.get("operation", "") for e in sel().recent(limit=500))
        assert ops == [
            "chat.fork_rewound",
            "chat.plan_activate",
            "chat.plan_approve",
            "chat.rewind",
            "chat.session_fork",
            "dashboard_interrupt",
            "upload.file",
        ], f"the nine mechanics produced {ops}"
