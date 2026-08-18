"""Platform event registry (APE-2) — the runtime that honours APE-1's declaration.

APE-1 shipped ``permissions.eventSubscriptions`` and deliberately added no accessor and no
delivery. This suite drives the runtime half through the path an app actually receives an
event by: ``app_events.emit`` fans out, the app drains its broker inbox over the real
``GET /api/apps/message`` route.

What it pins, in order of how much it would cost to get wrong:

* **Deny by default at DISPATCH.** An INSTALLED, ENABLED, running fixture app that declared
  nothing receives nothing — proven from the drain route, not from the manifest parse. A
  filter applied at declaration time and not at dispatch is the "declared, never enforced"
  shape, and it would deliver core facts to every installed app.
* **Exact match, no leakage.** A near-miss (``task.completed.extra``) and a would-be
  wildcard (``task.*``) subscriber both receive nothing when ``task.completed`` fires.
* **The two axes stay apart.** ``eventSubscriptions`` is not ``permissions.events``:
  neither grant implies the other, asserted from both sides (APE-1's
  ``test_event_subscriptions_do_not_widen_the_ws_event_allowlist`` is the manifest-side
  half; these are the dispatch-side half).
* **The three emit sites are real.** Each event is driven from the production function that
  makes the fact true — ``get_or_create_session``, ``ingest_item``, ``update_task`` — not
  by calling ``emit`` and trusting a call site exists.
* **SEL stays clean.** Ordinary fan-out and ordinary non-delivery write nothing; only an
  unregistered emit is audited. The audit assertion has a positive control in this file, so
  the "zero rows" assertion is not vacuous.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_events, app_manager, manager
from personalclaw.apps.app_events import (
    KNOWLEDGE_INGESTED,
    PLATFORM_EVENTS,
    PLATFORM_SENDER,
    SESSION_CREATED,
    TASK_COMPLETED,
)
from personalclaw.apps.manifest import Permissions
from personalclaw.apps.permissions import PermissionChecker
from personalclaw.dashboard.handlers.apps import register_app_routes


def _checker(**perms) -> PermissionChecker:
    return PermissionChecker(app_name="listener", permissions=Permissions(**perms))


# ── unit: the gate (deny by default, EXACT match, separate axis) ──


def test_can_receive_platform_event_deny_by_default():
    """An app that declares nothing is subscribed to nothing."""
    c = _checker()
    for name in PLATFORM_EVENTS:
        assert not c.can_receive_platform_event(name)


def test_can_receive_platform_event_is_exact_match_only():
    """No prefix, no trailing ``*`` — unlike ``api``/``events`` and like ``desktop``.

    The near-miss names are asserted explicitly: a prefix match would hand a subscriber
    every event that merely STARTS with the name it asked for, which for a dotted
    vocabulary is a whole namespace."""
    c = _checker(eventSubscriptions=[TASK_COMPLETED])
    assert c.can_receive_platform_event(TASK_COMPLETED)
    assert not c.can_receive_platform_event("task.completed.extra")
    assert not c.can_receive_platform_event("task.completedX")
    assert not c.can_receive_platform_event("task.")
    assert not c.can_receive_platform_event("")
    # …and a declared pattern is a literal name, never a pattern.
    assert not _checker(eventSubscriptions=["task.*"]).can_receive_platform_event(TASK_COMPLETED)
    assert not _checker(eventSubscriptions=["*"]).can_receive_platform_event(TASK_COMPLETED)


def test_the_two_event_vocabularies_stay_separate():
    """The dispatch-side half of APE-1's contract. ``events`` is the gateway's WS
    event-type allowlist (``can_use_event``); ``eventSubscriptions`` is the platform
    registry. Holding one must grant nothing about the other — otherwise the registry
    inherits a second, wider path to the same data."""
    subscribed = _checker(eventSubscriptions=[SESSION_CREATED])
    assert subscribed.can_receive_platform_event(SESSION_CREATED)
    assert not subscribed.can_use_event(SESSION_CREATED)

    ws_only = _checker(events=[SESSION_CREATED])
    assert ws_only.can_use_event(SESSION_CREATED)
    assert not ws_only.can_receive_platform_event(SESSION_CREATED)


def test_appmessaging_and_platform_events_are_different_grants():
    """Sharing the broker inbox as a TRANSPORT must not share the GRANTS. An app that may
    message another app is not thereby subscribed, and a subscriber may still message
    nobody."""
    talker = _checker(appMessaging=["other"])
    assert talker.can_use_app_messaging("other")
    assert not talker.can_receive_platform_event(TASK_COMPLETED)

    listener = _checker(eventSubscriptions=[TASK_COMPLETED])
    assert listener.can_receive_platform_event(TASK_COMPLETED)
    assert not listener.can_use_app_messaging("other")


# ── unit: the registry ──


def test_registry_holds_exactly_the_three_declared_events():
    assert set(PLATFORM_EVENTS) == {SESSION_CREATED, KNOWLEDGE_INGESTED, TASK_COMPLETED}
    for name, spec in PLATFORM_EVENTS.items():
        assert spec.name == name
        assert spec.summary.strip()
        assert spec.payload_keys


def test_payloads_carry_identifiers_not_prose():
    """The registry's key sets are a SECURITY contract, not tidiness: a payload carrying a
    task title or an item's text would hand a subscriber content its ``permissions.api``
    scope may not cover, silently widening ``can_use_api``. Pinned so a later field
    addition has to be a deliberate edit here."""
    assert PLATFORM_EVENTS[SESSION_CREATED].payload_keys == ("session",)
    assert PLATFORM_EVENTS[KNOWLEDGE_INGESTED].payload_keys == ("item_id", "status")
    assert PLATFORM_EVENTS[TASK_COMPLETED].payload_keys == ("task_id", "status")
    # No key anywhere that names free text.
    for spec in PLATFORM_EVENTS.values():
        assert not {"title", "content", "text", "body", "summary"} & set(spec.payload_keys)


def test_payload_is_projected_onto_the_declared_keys():
    spec = PLATFORM_EVENTS[TASK_COMPLETED]
    body = app_events._coerce_payload(
        spec, {"task_id": "t1", "status": "done", "title": "secret task title"}
    )
    assert body == {"task_id": "t1", "status": "done"}  # undeclared key dropped


def test_long_string_values_are_capped():
    spec = PLATFORM_EVENTS[SESSION_CREATED]
    body = app_events._coerce_payload(spec, {"session": "s" * 5000})
    assert len(body["session"]) == app_events.MAX_VALUE_CHARS


def test_platform_sender_can_never_be_an_app_name():
    """Platform events are told apart from app-to-app messages by their sender. That only
    holds if no installed app can be NAMED ``@platform``."""
    with pytest.raises(ValueError):
        manager._validate_app_name(PLATFORM_SENDER)


# ── dispatch: the path an app actually receives an event by ──


@asynccontextmanager
async def _client(tmp_path, monkeypatch):
    """A client for the app-inbox routes. ``X-Test-App`` stands in for the verified
    app-scoped token exactly as ``test_app_messaging`` does."""
    with _isolated(tmp_path, monkeypatch):

        @web.middleware
        async def stamp_app(request, handler):
            ident = request.headers.get("X-Test-App", "")
            if ident:
                request["app"] = ident
            return await handler(request)

        app = web.Application(middlewares=[stamp_app])
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


@contextmanager
def _isolated(tmp_path, monkeypatch):
    """Bind the apps dir, the broker inbox and the SEL to ``tmp_path``.

    Both patches are needed: ``apps_dir`` reads ``manager.config_dir`` while the inbox path
    reads ``config.loader.config_dir``, and a test that patched only one would write half
    its state into the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))  # SEL + queue bind here
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield


def _install(tmp_path: Path, name: str, *, subscriptions: list[str] | None = None, **perms):
    """Install a fixture app for real (``app_manager.install``) so the subscriber it
    resolves is an INSTALLED, ENABLED app, not a hand-built manifest."""
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    mani: dict = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    block: dict = dict(perms)
    if subscriptions is not None:
        block["eventSubscriptions"] = subscriptions
    if block:
        mani["permissions"] = block
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    res = app_manager.install(d)
    assert res.ok, res.error


def _hdr(app_name: str) -> dict[str, str]:
    return {"X-Test-App": app_name}


async def _inbox(client, app_name: str) -> list[dict]:
    r = await client.get("/api/apps/message", headers=_hdr(app_name))
    assert r.status == 200, await r.text()
    return (await r.json())["messages"]


@pytest.mark.asyncio
async def test_subscriber_receives_and_every_non_subscriber_never_does(tmp_path, monkeypatch):
    """THE clause. Four apps installed and enabled at once, one emit, four drains.

    ``quiet`` is the deny-by-default proof and is deliberately a RUNNING installed app
    rather than an absent one — an absent app proves nothing about a filter. ``nearmiss``
    and ``wildcard`` are the leakage proofs: both declared something that a prefix or
    glob match WOULD have accepted."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        _install(tmp_path, "quiet")  # no permissions block at all
        _install(tmp_path, "nearmiss", subscriptions=["task.completed.extra"])
        _install(tmp_path, "wildcard", subscriptions=["task.*"])

        delivered = app_events.emit(TASK_COMPLETED, {"task_id": "t-1", "status": "done"})
        assert delivered == ["listener"]

        msgs = await _inbox(client, "listener")
        assert len(msgs) == 1
        assert msgs[0]["type"] == TASK_COMPLETED
        assert msgs[0]["from"] == PLATFORM_SENDER
        assert json.loads(msgs[0]["payload"]) == {"task_id": "t-1", "status": "done"}

        for other in ("quiet", "nearmiss", "wildcard"):
            assert await _inbox(client, other) == [], f"{other} received an undeclared event"


@pytest.mark.asyncio
async def test_a_subscriber_receives_only_the_events_it_declared(tmp_path, monkeypatch):
    """Per-event, not per-app: subscribing to one registered event must not open the other
    two."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        assert app_events.emit(SESSION_CREATED, {"session": "s1"}) == []
        assert app_events.emit(KNOWLEDGE_INGESTED, {"item_id": "i1", "status": "done"}) == []
        assert await _inbox(client, "listener") == []
        assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == ["listener"]
        assert len(await _inbox(client, "listener")) == 1


@pytest.mark.asyncio
async def test_a_disabled_app_stops_receiving(tmp_path, monkeypatch):
    """A declaration goes dormant with its app: disabling must stop delivery without
    touching the manifest."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == ["listener"]
        await _inbox(client, "listener")  # drain

        assert app_manager.disable("listener") is True
        assert app_events.emit(TASK_COMPLETED, {"task_id": "t2", "status": "done"}) == []
        assert await _inbox(client, "listener") == []


@pytest.mark.asyncio
async def test_an_app_messaging_grant_delivers_no_platform_event(tmp_path, monkeypatch):
    """The transport is shared; the grants are not. An app that declared ``appMessaging``
    (and no subscription) receives no platform event."""
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "talker", appMessaging=["listener"])
        assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == []
        assert await _inbox(client, "talker") == []


# ── audit posture ──


def test_an_unregistered_emit_is_refused_and_audited(tmp_path, monkeypatch):
    """The positive control for the SEL assertions below: the writer works, so a later
    "no rows" assertion means silence, not a broken emitter. An unregistered name is a
    code defect at an emit site that would otherwise be delivered to nobody, forever,
    with no trace."""
    from personalclaw.sel import sel

    with _isolated(tmp_path, monkeypatch):
        assert app_events.emit("task.exploded", {"x": 1}) == []
        rows = [
            e
            for e in sel().recent(50)
            if e.get("event_type") == "app_platform_event" and e.get("outcome") == "rejected"
        ]
        assert rows, "an unregistered emit wrote no audit row"
        assert "event=task.exploded" in rows[0].get("resources", "")


@pytest.mark.asyncio
async def test_ordinary_fan_out_and_ordinary_non_delivery_write_no_sel(tmp_path, monkeypatch):
    """SEL clean. An app never REQUESTS a platform event — dispatch is host-initiated — so
    a non-delivery is not an access attempt, and one row per (installed app × emitted
    event) would drown the real rows in the HMAC chain. Both the delivered and the
    filtered app are present here, and neither produces a row."""
    from personalclaw.sel import sel

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        _install(tmp_path, "quiet")
        for i in range(3):
            app_events.emit(TASK_COMPLETED, {"task_id": f"t{i}", "status": "done"})
        assert len(await _inbox(client, "listener")) == 3
        assert await _inbox(client, "quiet") == []

        rows = [e for e in sel().recent(200) if e.get("event_type") == "app_platform_event"]
        assert rows == [], f"ordinary delivery polluted the SEL: {rows}"


# ── the three emit sites, driven from production code ──


@pytest.mark.asyncio
async def test_session_created_fires_at_its_emit_site(tmp_path, monkeypatch):
    """Driven through ``DashboardState.get_or_create_session`` — the function that inserts
    the session row — not by calling ``emit`` and hoping a call site exists."""
    from tests.chat_test_helpers import _make_state

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[SESSION_CREATED])
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")

        msgs = await _inbox(client, "listener")
        assert [m["type"] for m in msgs] == [SESSION_CREATED]
        assert json.loads(msgs[0]["payload"]) == {"session": "s1"}

        # Idempotent lookup of an EXISTING session is not a creation.
        state.get_or_create_session("s1")
        assert await _inbox(client, "listener") == []


@pytest.mark.asyncio
async def test_a_rehydrated_session_is_not_announced_as_created(tmp_path, monkeypatch):
    """``get_or_create_session`` is ALSO the rehydration path — bulk startup restore and
    every resume / post-to-an-old-session route reach its create branch for a session that
    already exists on disk. Announcing those would re-fire ``session.created`` for every
    restored session on every gateway restart, and a subscribed app would double-count
    sessions it already saw.

    Driven the way it actually happens: persist a session's history, drop it from the
    in-memory map (a restart), then materialize it again."""
    from personalclaw.dashboard.chat_utils import _history_key_for
    from tests.chat_test_helpers import _make_state

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[SESSION_CREATED])
        state = _make_state(tmp_path)
        state.get_or_create_session("s1")
        assert [m["type"] for m in await _inbox(client, "listener")] == [SESSION_CREATED]

        # The restart: the session is gone from memory but its history is on disk (the
        # real ConversationLog write path — appending a message mints the metadata line
        # `resolve_history_key` looks for).
        state.conversation_log.append(_history_key_for("s1"), "user", "hello")
        state._sessions.pop("s1")
        assert state._has_persisted_history("s1"), "the fixture did not persist any history"
        state.get_or_create_session("s1")
        assert await _inbox(client, "listener") == [], "a rehydrated session was re-announced"


@pytest.mark.asyncio
async def test_knowledge_ingested_fires_at_its_emit_site(tmp_path, monkeypatch):
    """Driven through the real ingestion pipeline (``ingest_item``) on a note item, at the
    same terminal point the SSE ``ingest_complete`` fires from."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[KNOWLEDGE_INGESTED])
        ensure_nodes_registered()
        store = KnowledgeStore(str(tmp_path / "k.db"))
        item_id = store.create_typed_item(item_type="note", title="N", content="the body text")
        status = await ingest_item(store, item_id)
        assert status == "done"

        msgs = await _inbox(client, "listener")
        assert [m["type"] for m in msgs] == [KNOWLEDGE_INGESTED]
        assert json.loads(msgs[0]["payload"]) == {"item_id": item_id, "status": "done"}
        # The item's text is NOT in the event — the app fetches content through its own
        # granted API scope, so the subscription grants timing, not content.
        assert "body text" not in msgs[0]["payload"]


@pytest.mark.asyncio
async def test_a_failed_ingest_is_not_announced_as_ingested(tmp_path, monkeypatch):
    """The event has to be true to its name: a run whose pipeline failed ingested nothing,
    so it announces nothing — matching the earlier failure exits in ``ingest_item``, which
    return before the emit site entirely.

    Driven with a real failing node on the NORMAL terminal path — the one that DOES reach
    the emit site. An early-return failure would pass this test under either design, which
    would make it vacuous."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline import runner as pipeline_runner
    from personalclaw.knowledge.pipeline.graph import NodeSpec, PipelineGraph
    from personalclaw.knowledge.pipeline.registry import register_node
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.pipeline.types import NodeOutput
    from personalclaw.knowledge.store import KnowledgeStore

    class _FailingNode:
        node_type = "ape2-boom"
        backend = "stub"
        uses_use_case = None

        async def run(self, inputs, ctx):
            return NodeOutput(
                node_type=self.node_type, backend=self.backend, success=False, error="boom"
            )

    def _only_boom(item_type, *, enrichment=""):
        g = PipelineGraph(item_type=item_type)
        g.add(NodeSpec("ape2-boom", backend="stub"))
        g.validate()
        return g

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[KNOWLEDGE_INGESTED])
        ensure_nodes_registered()
        register_node(_FailingNode())
        monkeypatch.setattr(pipeline_runner, "graph_for", _only_boom)
        store = KnowledgeStore(str(tmp_path / "k.db"))
        item_id = store.create_typed_item(item_type="note", title="N", content="x")
        status = await ingest_item(store, item_id)
        assert status == "failed", f"the fixture did not reach a failed TERMINAL run: {status}"
        assert await _inbox(client, "listener") == []


@pytest.mark.asyncio
async def test_task_completed_fires_at_its_emit_site_on_the_edge(tmp_path, monkeypatch):
    """Driven through ``NativeTaskProvider.update_task`` on the same edge-triggered
    boundary the ``TaskComplete`` user hook fires on — and NOT on a non-completion edit,
    which is what makes it an edge rather than a level."""
    import personalclaw.tasks.native as nat

    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        with patch.object(nat, "config_dir", lambda: tmp_path):
            provider = nat.NativeTaskProvider()
            task = await provider.create_task(title="A")
            # A plain edit is not a completion.
            await provider.update_task(task.id, title="A2")
            assert await _inbox(client, "listener") == []

            await provider.update_task(task.id, status="done")
            msgs = await _inbox(client, "listener")
            assert [m["type"] for m in msgs] == [TASK_COMPLETED]
            assert json.loads(msgs[0]["payload"]) == {"task_id": task.id, "status": "done"}
            assert "A2" not in msgs[0]["payload"]  # no title in the payload

            # Re-saving an already-done task must not fire again (edge, not level).
            await provider.update_task(task.id, status="done")
            assert await _inbox(client, "listener") == []


def test_emit_never_raises_into_its_call_site(tmp_path, monkeypatch):
    """Every emit site is an OBSERVER boundary: a broken manifest or an unwritable inbox
    must not fail the session creation, ingest or task edit that produced the fact."""
    with _isolated(tmp_path, monkeypatch):
        with patch.object(app_events, "subscribers", side_effect=RuntimeError("boom")):
            assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == []
        with patch.object(app_events, "_deliver", side_effect=OSError("read-only fs")):
            _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
            assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == []


def test_a_failed_delivery_to_a_subscriber_is_audited(tmp_path, monkeypatch):
    """The other bounded audit case: an app that EARNED the event and did not get it. Rare
    and real, unlike ordinary non-delivery."""
    from personalclaw.sel import sel

    with _isolated(tmp_path, monkeypatch):
        _install(tmp_path, "listener", subscriptions=[TASK_COMPLETED])
        with patch.object(app_events, "_deliver", side_effect=OSError("read-only fs")):
            assert app_events.emit(TASK_COMPLETED, {"task_id": "t1", "status": "done"}) == []
        rows = [
            e
            for e in sel().recent(50)
            if e.get("event_type") == "app_platform_event" and e.get("outcome") == "error"
        ]
        assert rows, "a subscriber silently missed an event it declared"
        assert rows[0].get("caller_identity") == "app:listener"


def test_no_subscribers_is_a_quiet_no_op(tmp_path, monkeypatch):
    """The overwhelmingly common production case: nothing installed declares the event."""
    with _isolated(tmp_path, monkeypatch):
        assert app_events.emit(SESSION_CREATED, {"session": "s"}) == []
        assert app_events.subscribers(SESSION_CREATED) == []


def test_asyncio_is_not_needed_by_the_registry():
    """``emit`` is sync on purpose: two of the three emit sites are sync functions, and an
    awaitable would have forced a task-spawn (and an ordering hazard) into them."""
    assert not asyncio.iscoroutinefunction(app_events.emit)
