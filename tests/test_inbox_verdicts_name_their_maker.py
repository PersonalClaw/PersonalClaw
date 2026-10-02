"""A judgment on an Inbox row is rated against the prompt that made it, or not at all.

Every row used to be stamped with a verdict when it was made (a message ``needs_reply`` /
``needs_review``, every other kind ``needs_reply`` / ``high``), the thumbs under it credited the
sorting prompt, and ``POST /api/feedback`` recorded whatever producer the page named. So a verdict
she gave on a placeholder trained the sorting prompt on a label it never produced. These pin the
other half of the fix: no row carries a verdict nobody made, a stored placeholder is cleared, and
the feedback route names the row's own maker or refuses.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import feedback as fb
from personalclaw.inbox import InboxItem, InboxState, InboxStore, emit_attention_item, redact_item

_PROMPT = "native:task-inbox-classify"


def _message(**over) -> InboxItem:
    base = dict(
        id="mail-inbox_ab12cd34ef56ab78_1790000000.0",
        channel="dana@example.com",
        channel_name="dana@example.com",
        thread_ts=None,
        message="Your talk is accepted. Could you send the abstract?",
        sender_id="programme@example.org",
        sender_name="Programme team",
        source="mail-inbox",
        item_kind="email",
        can_reply=True,
    )
    base.update(over)
    return InboxItem(**base)


@pytest.fixture()
def store(tmp_path):
    return InboxStore(tmp_path / "inbox.json")


@pytest.fixture()
def state(store):
    from personalclaw.inbox_service import InboxService

    svc = InboxService(state=InboxState(store._path.with_name("state.json")), store=store)
    frames: list = []
    return SimpleNamespace(
        _inbox_svc=svc,
        frames=frames,
        broadcast_ws=lambda kind, data: frames.append((kind, data)),
        notify=lambda *a, **k: None,
    )


# ── no row carries a verdict nobody made ──


def test_a_row_that_is_not_a_message_carries_no_verdict(store):
    st = SimpleNamespace(notify=lambda *a, **k: None, broadcast_ws=lambda *a, **k: None)
    for source, kind in (("loop", "needs_input"), ("skills", "proposal"), ("apps", "system")):
        item_id = emit_attention_item(st, source=source, kind=kind, title="T", store=store)
        row = store.items[item_id]
        assert (row.classification, row.confidence) == ("", ""), kind
        assert "feedback_producers" not in redact_item(row.to_dict())


def test_a_new_message_carries_no_verdict():
    row = _message()
    assert (row.classification, row.confidence, row.classified_by) == ("", "", "")


def test_a_stored_placeholder_is_cleared_and_her_own_verdict_kept(store):
    """Rows stored before verdicts named their maker: the stamped default goes, hers stays, and
    an agent's post keeps the kind it was posted as, without a confidence nobody had."""
    legacy = [
        {
            **_message(id="m_1").to_dict(),
            "classification": "needs_reply",
            "confidence": "needs_review",
        },
        {**_message(id="m_2").to_dict(), "classification": "noise", "confidence": "user"},
        {
            **_message(id="n_3", item_kind="user_note", source="user").to_dict(),
            "classification": "needs_reply",
            "confidence": "high",
        },
        {
            **_message(id="a_4", source="native", item_kind="message").to_dict(),
            "classification": "fyi",
            "confidence": "high",
        },
    ]
    for row in legacy:
        for key in ("classified_by", "classify_error", "drafted_by"):
            row.pop(key)
    store._path.write_text(json.dumps({"items": legacy}))
    store.load()

    got = {i: (r.classification, r.confidence) for i, r in store.items.items()}
    assert got == {"m_1": ("", ""), "m_2": ("noise", "user"), "n_3": ("", ""), "a_4": ("fyi", "")}
    # Once: a row written back carries the field, so reading it again changes nothing.
    store.update("m_1", classification="fyi", confidence="high", classified_by=_PROMPT)
    again = InboxStore(store._path)
    again.load()
    assert (again.items["m_1"].classification, again.items["m_1"].confidence) == ("fyi", "high")


# ── the feedback route names the row's maker, or refuses ──


def _feedback_app(state) -> web.Application:
    from personalclaw.dashboard.handlers.feedback import register_feedback_routes

    app = web.Application()
    app["state"] = state
    register_feedback_routes(app)
    return app


@pytest.fixture(autouse=True)
def _fresh_feedback_index():
    fb._invalidate()
    yield
    fb._invalidate()


@pytest.mark.asyncio
async def test_a_verdict_on_a_placeholder_is_refused(state):
    state._inbox_svc.inbox.add(_message())
    body = {
        "target_kind": "inbox_classification",
        "target_id": _message().id,
        "verdict": "down",
        "producer_kind": "prompt",
        "producer_id": _PROMPT,
        "snapshot": {"classification": "needs_reply", "confidence": "needs_review"},
    }
    async with TestClient(TestServer(_feedback_app(state))) as c:
        resp = await c.post("/api/feedback", json=body)
        assert resp.status == 409
        assert (await resp.json())["error"]["code"] == "feedback_no_judgment"
        # Her own verdict is not the prompt's either.
        state._inbox_svc.inbox.update(_message().id, classification="fyi", confidence="user")
        assert (await c.post("/api/feedback", json=body)).status == 409
    assert fb.current_verdict("inbox_classification", _message().id) is None


@pytest.mark.asyncio
async def test_a_verdict_is_credited_to_the_prompt_the_row_names(state):
    state._inbox_svc.inbox.add(
        _message(classification="needs_reply", confidence="high", classified_by=_PROMPT)
    )
    body = {
        "target_kind": "inbox_classification",
        "target_id": _message().id,
        "verdict": "down",
        "producer_kind": "prompt",
        "producer_id": "native:some-other-prompt",
    }
    async with TestClient(TestServer(_feedback_app(state))) as c:
        assert (await c.post("/api/feedback", json=body)).status == 200
    rec = fb.current_verdict("inbox_classification", _message().id)
    assert rec is not None and (rec.producer_kind, rec.producer_id) == ("prompt", _PROMPT)


@pytest.mark.asyncio
async def test_a_draft_she_wrote_has_nothing_to_rate(state):
    from personalclaw.dashboard import handlers_inbox

    inbox = state._inbox_svc.inbox
    inbox.add(_message(draft="Thanks, attached.", drafted_by="native:task-inbox-draft"))
    assert redact_item(inbox.items[_message().id].to_dict())["feedback_producers"]["draft"] == {
        "producer_kind": "prompt",
        "producer_id": "native:task-inbox-draft",
    }
    app = _feedback_app(state)
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    body = {"target_kind": "inbox_draft", "target_id": _message().id, "verdict": "up"}
    async with TestClient(TestServer(app)) as c:
        put = await c.put(f"/api/inbox/{_message().id}", json={"draft": "Thanks! Abstract below."})
        assert put.status == 200
        assert "feedback_producers" not in await put.json()
        assert (await c.post("/api/feedback", json=body)).status == 409


@pytest.mark.asyncio
async def test_her_verdict_names_no_prompt_and_clears_a_failure(state):
    from personalclaw.dashboard import handlers_inbox

    inbox = state._inbox_svc.inbox
    inbox.add(_message(classify_error="The background model could not sort it."))
    app = web.Application()
    app["state"] = state
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    async with TestClient(TestServer(app)) as c:
        resp = await c.put(f"/api/inbox/{_message().id}", json={"classification": "fyi"})
        assert resp.status == 200
    row = inbox.items[_message().id]
    assert (row.classification, row.confidence, row.classified_by, row.classify_error) == (
        "fyi",
        "user",
        "",
        "",
    )


# ── a failed message can be sorted again ──


@pytest.mark.asyncio
async def test_sort_again_clears_the_failure_and_wakes_the_sorter(state):
    from personalclaw.dashboard import handlers_inbox

    inbox = state._inbox_svc.inbox
    inbox.add(_message(classify_error="The background model could not sort it."))
    sorter = state._inbox_svc.sorter
    woken: list = []
    sorter.wake = lambda: woken.append(True)
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/inbox/{id}/sort", handlers_inbox.api_inbox_sort)
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(f"/api/inbox/{_message().id}/sort")
        assert resp.status == 200
        assert (await resp.json())["classify_error"] == ""
        assert woken == [True]
        assert [s.id for s in sorter.waiting()] == [_message().id]
        # A row with a verdict, or one that is not a message, is not sorted again.
        inbox.update(_message().id, classification="fyi", confidence="user")
        refused = await c.post(f"/api/inbox/{_message().id}/sort")
        assert refused.status == 409
        assert (await refused.json())["error"]["code"] == "inbox_item_not_sortable"
        assert (await c.post("/api/inbox/nope/sort")).status == 404
        # Nor one she has dismissed: the sorter sorts only what is still open.
        inbox.update(_message().id, classification="", confidence="", classify_error="x")
        inbox.items[_message().id].status = "dismissed"
        assert (await c.post(f"/api/inbox/{_message().id}/sort")).status == 409
