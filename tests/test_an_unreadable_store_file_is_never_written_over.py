"""A store file that cannot be read is never written over, whichever store it is.

One rule, kept by ``record_files`` and taken by every store of records and settings under the
home: a file that is there and holds no document of its own (not JSON, or JSON that is not the
store's) is never read as empty for a write. Each read a write is built on refuses with
``record_files.Unreadable``, the file stays exactly as it was, and a copy of it as it was is kept
beside it. A writer that runs on its own (a tool call's hook status, an inbound message's count,
an arriving inbox item, a boot's review card, a routing proposal) writes nothing and does not fail
its caller.

Each store here used to read such a file as empty and write its next change over it, which lost
everything else it held. A readable file behaves as it did, and so does a file holding nothing:
there is nothing in it to lose.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from personalclaw import record_files

#: A store cut off in the middle of a record, as a crash or a full disk leaves one.
BROKEN = '{"cut off": [{"id": "keep", "name": "kept"'
#: JSON that is no store's document.
NOT_ITS_DOCUMENT = '"a string, not a document any store keeps"'


@pytest.fixture
def home() -> Path:
    """The test's own home, as every store resolves it (the suite gives each test its own)."""
    from personalclaw.config.loader import config_dir

    return config_dir()


def _state() -> Any:
    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=time.time())


def _item(item_id: str, message: str = "a message") -> Any:
    from personalclaw.inbox import InboxItem

    return InboxItem(
        id=item_id,
        channel="c1",
        channel_name="general",
        thread_ts=None,
        message=message,
        sender_id="u1",
        sender_name="Someone",
    )


def _item_row(item_id: str) -> dict:
    return {
        "id": item_id,
        "channel": "c1",
        "channel_name": "general",
        "thread_ts": None,
        "message": "kept",
        "sender_id": "u1",
        "sender_name": "Someone",
    }


# ── the writes, each through its store's own API ────────────────────────────────────────────


def _trigger_upsert(home: Path) -> None:
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(Trigger(id="new", name="New", kind="manual", enabled=False))


def _hook_create(home: Path) -> None:
    from personalclaw.hooks import ScriptHookStore

    ScriptHookStore(config_dir=home).create(
        {
            "id": "new",
            "name": "New",
            "event": "Stop",
            "provider": "bash",
            "provider_config": {"command": "/nonexistent/pc-fixture-hook"},
            "enabled": False,
        }
    )


def _inbox_arrival(home: Path) -> None:
    from personalclaw.inbox import InboxStore

    store = InboxStore(home / "inbox.json")
    store.load()
    store.add(_item("arrived_2"))
    store.flush()


def _folder_save(home: Path) -> None:
    state = _state()
    state.load_folders()
    state._folders.append({"id": "new", "name": "New"})
    state.save_folders()


def _tag_save(home: Path) -> None:
    state = _state()
    state.load_tags()
    state._tags.append({"id": "new", "name": "new"})
    state.save_tags()


def _board_save(home: Path) -> None:
    state = _state()
    state.load_tags()
    state._tag_boards.append({"id": "new", "name": "New", "tag_ids": [], "order": 1})
    state.save_tag_boards()


def _comment_add(home: Path) -> None:
    from personalclaw import doc_comments

    doc_comments.add(doc_id="doc-1", comment="a new note")


def _view_create(home: Path) -> None:
    from personalclaw.dashboard import views_store

    views_store.create_view("New")


def _report_save(home: Path) -> None:
    from personalclaw.knowledge import research_reports

    research_reports.save_report(
        research_reports.from_dict({"id": "rpt-new", "name": "New", "prompt": "summarize"})
    )


def _grant_give(home: Path) -> None:
    from personalclaw.owner_grants import GrantBook

    GrantBook("callbacks").give("new", "what was allowed")


def _trust_seal(home: Path) -> None:
    from personalclaw import mcp_read_only_trust

    mcp_read_only_trust.seal("new-server", [{"name": "lookup", "description": "reads"}], {})


def _descriptions_noted(home: Path) -> None:
    from personalclaw import mcp_read_only_trust as trust

    listed = trust.definition({"name": "lookup", "description": "reads"})
    trust._note_descriptions("new-server", [listed])


def _entity_saved(home: Path) -> None:
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    _save_entity_settings("fixture", {**(_load_entity_settings("fixture") or {}), "new": 1})


def _pin(home: Path) -> None:
    from personalclaw.workflows import pinned

    pinned.pin("new-artifact")


def _rules_save(home: Path) -> None:
    from personalclaw import notification_rules

    doc = notification_rules.load_rules()
    doc.setdefault("rules", {})["agent/message"] = {"mode": "digest"}
    notification_rules.save_rules(doc)


def _template_save(home: Path) -> None:
    from personalclaw.dashboard import session_templates

    _tid, err = session_templates.save_template({"name": "New"})
    assert not err, err


def _sender_allowed(home: Path) -> None:
    from personalclaw import channel_trust

    channel_trust.allow_sender("fixture-chat", "u-new", "New")


def _unknown_sender(home: Path) -> None:
    from personalclaw import channel_trust

    channel_trust.note_unknown_sender(None, "fixture-chat", "u-stranger", "Stranger")


def _routing_dismissed(home: Path) -> None:
    from personalclaw.agents import routing

    routing.record_dismiss("new-agent", now=2.0)


def _organize_declined(home: Path) -> None:
    from personalclaw import session_organize

    session_organize.record_decline(
        session_organize.OrganizeProposal(session_key="chat-1", folder_id="f1"), now=2.0
    )


def _onboarding_step(home: Path) -> None:
    from personalclaw import onboarding

    onboarding.merge_onboarding_state({"step": "essentials"})


def _callback_registered(home: Path) -> None:
    from personalclaw import webhook_callbacks

    webhook_callbacks.register("new", "what the callback starts from")


def _review_recorded(home: Path) -> None:
    from personalclaw.triggers import review

    review.record(
        [review.ReviewCard(trigger_id="new", kind=review.MISSED, count=1, latest=2.0, oldest=2.0)],
        base_dir=home,
    )


def _proposal_filed(home: Path) -> None:
    from personalclaw.routing import proposals

    proposals.propose(
        use_case="reasoning",
        query_class="summarize",
        current=["cloud:big", "local:small"],
        proposed=["local:small", "cloud:big"],
        evidence={},
        home=home,
    )


def _rebench_queued(home: Path) -> None:
    from personalclaw.evals import model_watchdog

    # The write a rebind makes (`model_watchdog.check`): the queue as it stands, and what it adds.
    model_watchdog.save_queue(model_watchdog.load_queue(strict=True) + [{"id": "new"}])


def _app_message(home: Path) -> None:
    from personalclaw.apps import messaging

    messaging._append_to_queue(
        "target-app",
        messaging.AppMessage(
            id="new",
            sender="sender-app",
            target="target-app",
            type="note",
            payload="hello",
            ts="2026-10-04T00:00:00+00:00",
        ),
    )


def _decision_recorded(home: Path) -> None:
    from personalclaw.learning import proposals

    proposals.record_decision(
        proposals.Proposal(id="p-new", kind="skill", title="New", body="b", fingerprint="new"),
        "rejected",
    )


# ── the stores ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Store:
    name: str
    #: Its file, under the home.
    path: Callable[[Path], Path]
    #: A readable file holding one record or setting, "keep".
    seed: Any
    #: A write through the store's own API, as one made after the file broke.
    write: Callable[[Path], None]
    #: After a write over the readable seed: is "keep" still there?
    kept: Callable[[Any], bool]
    #: True: the write refuses (``record_files.Unreadable``). False: it runs on its own, so it
    #: writes nothing and returns, never failing its caller.
    refuses: bool = True


def _entity(name: str) -> Callable[[Path], Path]:
    return lambda h: h / "entity_settings" / f"{name}.json"


def _ids(key: str, field: str = "id") -> Callable[[Any], bool]:
    return lambda doc: any(r.get(field) == "keep" for r in (doc[key] if key else doc))


def _has(*keys: str) -> Callable[[Any], bool]:
    def check(doc: Any) -> bool:
        for key in keys:
            doc = doc[key]
        return "keep" in doc

    return check


def _decisions(home: Path) -> Path:
    from personalclaw.learning import proposals

    return proposals._decisions_path()


STORES = [
    Store(
        "automations",
        lambda h: h / "triggers.json",
        {"version": 1, "triggers": [{"id": "keep", "name": "Kept", "kind": "manual"}]},
        _trigger_upsert,
        _ids("triggers"),
    ),
    Store(
        "lifecycle triggers",
        lambda h: h / "hooks.json",
        {"hooks": [{"id": "keep", "name": "Kept", "event": "Stop", "enabled": False}]},
        _hook_create,
        _ids("hooks"),
    ),
    Store(
        "inbox items",
        lambda h: h / "inbox.json",
        {"items": [_item_row("keep")]},
        _inbox_arrival,
        _ids("items"),
        refuses=False,
    ),
    Store("folders", lambda h: h / "folders.json", [{"id": "keep"}], _folder_save, _ids("")),
    Store("tags", lambda h: h / "tags.json", [{"id": "keep"}], _tag_save, _ids("")),
    Store("tag boards", lambda h: h / "tag_boards.json", [{"id": "keep"}], _board_save, _ids("")),
    Store(
        "document comments",
        lambda h: h / "doc_comments.json",
        {"comments": [{"id": "keep", "doc_id": "doc-1", "comment": "kept", "ts": 1.0}]},
        _comment_add,
        _ids("comments"),
    ),
    Store(
        "dashboard views",
        lambda h: h / "dashboard_views.json",
        {"views": [{"id": "keep", "name": "Kept", "tiles": []}], "overlay": {}},
        _view_create,
        _ids("views"),
    ),
    Store(
        "research reports",
        lambda h: h / "research_reports.json",
        [{"id": "keep", "name": "Kept", "prompt": "p"}],
        _report_save,
        _ids(""),
    ),
    Store(
        "a grant book",
        lambda h: h / "grants" / "callbacks.json",
        {"version": 1, "grants": {"keep": {"seal": "x", "at": "2026-01-01T00:00:00+00:00"}}},
        _grant_give,
        _has("grants"),
    ),
    Store(
        "MCP label trust",
        lambda h: h / "grants" / "mcp_read_only.json",
        {"servers": {"keep": {"at": "2026-01-01T00:00:00+00:00", "tools": {}}}},
        _trust_seal,
        _has("servers"),
    ),
    Store(
        "MCP descriptions seen",
        lambda h: h / "grants" / "mcp_tool_descriptions.json",
        {"servers": {"keep": {"lookup": "h"}}},
        _descriptions_noted,
        _has("servers"),
        refuses=False,
    ),
    Store(
        "entity settings (the one writer)",
        _entity("fixture"),
        {"keep": True},
        _entity_saved,
        _has(),
    ),
    Store(
        "pinned artifacts",
        _entity("pinned_artifacts"),
        {"pins": [{"slug": "keep", "pinned_at": "2026-01-01T00:00:00+00:00", "run_id": ""}]},
        _pin,
        _ids("pins", "slug"),
    ),
    Store(
        "notification rules",
        _entity("notification_rules"),
        {"rules": {"keep": {"mode": "immediate"}}},
        _rules_save,
        _has("rules"),
    ),
    Store(
        "chat templates",
        _entity("session_templates"),
        {"keep": {"name": "Kept", "first_prompt": "", "created_at": 1.0}},
        _template_save,
        _has(),
    ),
    Store(
        "sender trust (the owner's Allow)",
        _entity("channel_trust"),
        {"fixture-chat": {"allowed_senders": {"keep": {"via": "owner"}}}},
        _sender_allowed,
        _has("fixture-chat", "allowed_senders"),
    ),
    Store(
        "sender trust (an inbound message)",
        _entity("channel_trust"),
        {"fixture-chat": {"allowed_senders": {"keep": {"via": "owner"}}}},
        _unknown_sender,
        _has("fixture-chat", "allowed_senders"),
        refuses=False,
    ),
    Store(
        "agent routing notices",
        _entity("agent_routing"),
        {"muted": [], "dismissals": {"keep": {"count": 1, "last_dismissed_at": 1.0}}},
        _routing_dismissed,
        _has("dismissals"),
    ),
    Store(
        "organize suggestions declined",
        _entity("session_organize"),
        {"declined": {"keep": 1.0}},
        _organize_declined,
        _has("declined"),
    ),
    Store(
        "onboarding progress",
        _entity("onboarding"),
        {"step": "name", "first_success": {"knowledge": True}},
        _onboarding_step,
        lambda doc: doc["first_success"]["knowledge"] is True and doc["step"] == "essentials",
    ),
    Store(
        "callbacks",
        lambda h: h / "webhook_callbacks.json",
        {"version": 1, "callbacks": [{"id": "keep", "context_summary": "k", "registered_at": 1.0}]},
        _callback_registered,
        _ids("callbacks"),
    ),
    Store(
        "review cards",
        lambda h: h / "trigger-review.json",
        {"cards": [{"trigger_id": "keep", "kind": "missed", "count": 1, "latest": 1.0}]},
        _review_recorded,
        _ids("cards", "trigger_id"),
        refuses=False,
    ),
    Store(
        "routing proposals",
        lambda h: h / "routing_proposals.json",
        {"version": 1, "proposals": [], "rejections": {"keep": "2026-01-01T00:00:00+00:00"}},
        _proposal_filed,
        _has("rejections"),
        refuses=False,
    ),
    Store(
        "re-benchmark queue",
        lambda h: h / "evals" / "rebench_queue.json",
        {"entries": [{"id": "keep"}]},
        _rebench_queued,
        _ids("entries"),
    ),
    Store(
        "an app's messages",
        lambda h: h / "app_messages" / "target-app.json",
        [{"id": "keep", "from": "sender-app", "type": "note", "payload": "p", "ts": "t"}],
        _app_message,
        _ids(""),
    ),
    Store(
        "learning decisions",
        _decisions,
        {
            "keep": {
                "fingerprint": "keep",
                "verdict": "rejected",
                "kind": "skill",
                "title": "t",
                "decided_at": "t",
            }
        },
        _decision_recorded,
        _has(),
    ),
]


def _at(home: Path, store: Store, text: str) -> Path:
    path = store.path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("text", [BROKEN, NOT_ITS_DOCUMENT], ids=["not JSON", "not its document"])
@pytest.mark.parametrize("store", STORES, ids=[s.name for s in STORES])
def test_a_write_never_replaces_a_file_it_could_not_read(home, store, text):
    path = _at(home, store, text)
    before = path.read_bytes()
    if store.refuses:
        with pytest.raises(record_files.Unreadable) as refused:
            store.write(home)
        assert refused.value.path == path
        assert path.name in str(refused.value)
    else:
        store.write(home)
    assert path.read_bytes() == before, f"the unreadable {store.name} file was written over"
    copies = record_files.kept_copies(path)
    assert [c.read_bytes() for c in copies] == [before], "no copy of it as it was was kept"
    assert copies[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("store", STORES, ids=[s.name for s in STORES])
def test_a_readable_file_behaves_as_it_did(home, store):
    path = _at(home, store, json.dumps(store.seed))
    store.write(home)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert store.kept(doc), f"the {store.name} write lost what the file held"
    assert doc != store.seed, f"the {store.name} write did not land"
    assert record_files.kept_copies(path) == []


@pytest.mark.parametrize("store", STORES, ids=[s.name for s in STORES])
def test_a_file_holding_nothing_is_written_as_absent(home, store):
    """Zero bytes hold nothing a write could destroy: refusing would leave the store unwritable
    for good, over a file there is nothing in."""
    path = _at(home, store, "")
    store.write(home)
    assert json.loads(path.read_text(encoding="utf-8")) is not None
    assert record_files.kept_copies(path) == []


# ── what a refused write leaves behind, and the writes that are not one call over one file ──


def test_a_lifecycle_trigger_whose_save_was_refused_is_neither_listed_nor_run(home):
    """The store holds its triggers in memory. A save refused there used to leave the new
    trigger held anyway, so it was listed and ran on every tool call while the owner had been
    told nothing was saved."""
    from personalclaw.hooks import ScriptHookStore

    (home / "hooks.json").write_text(BROKEN, encoding="utf-8")
    store = ScriptHookStore(config_dir=home)
    with pytest.raises(record_files.Unreadable):
        store.create(
            {
                "id": "refused",
                "name": "Refused",
                "event": "PreToolUse",
                "provider": "bash",
                "provider_config": {"command": "/nonexistent/pc-fixture-hook"},
            }
        )
    assert store.list_all() == []
    assert asyncio.run(store.fire("PreToolUse", tool_name="read_file")) == []


def test_a_change_to_a_lifecycle_trigger_refused_leaves_it_as_it_was(home):
    """The file breaks while the gateway holds its triggers: they still run as they were last
    read, and a change to one is refused whole, the command it ran included."""
    from personalclaw.hooks import ScriptHookStore

    path = home / "hooks.json"
    held = {
        "id": "h1",
        "name": "Held",
        "event": "PreToolUse",
        "provider": "bash",
        "provider_config": {"command": "/nonexistent/pc-fixture-hook"},
        "enabled": False,
    }
    path.write_text(json.dumps({"hooks": [held]}), encoding="utf-8")
    store = ScriptHookStore(config_dir=home)
    path.write_text(BROKEN, encoding="utf-8")
    with pytest.raises(record_files.Unreadable):
        store.update("h1", {"provider_config": {"command": "/nonexistent/pc-fixture-other"}})
    with pytest.raises(record_files.Unreadable):
        store.toggle("h1")
    with pytest.raises(record_files.Unreadable):
        store.delete("h1")
    [hook] = store.list_all()
    assert hook.provider_config == {"command": "/nonexistent/pc-fixture-hook"}
    assert hook.enabled is False
    assert path.read_text(encoding="utf-8") == BROKEN


def test_a_tool_calls_hooks_run_their_course_over_an_unreadable_file(home):
    """Every tool call fires the hooks, and the fire wrote ``hooks.json`` after, matched or not:
    the first tool call after the file broke wrote ``{"hooks": []}`` over every lifecycle trigger.
    Now a fire writes only when a hook ran, and the status it could not record never fails it."""
    from personalclaw import hooks
    from personalclaw.hooks import ScriptHookStore

    path = home / "hooks.json"
    held = {
        "id": "h1",
        "name": "Held",
        "event": "PreToolUse",
        "provider": "bash",
        "provider_config": {"command": "/nonexistent/pc-fixture-hook"},
        "enabled": True,
    }
    path.write_text(json.dumps({"hooks": [held]}), encoding="utf-8")
    store = ScriptHookStore(config_dir=home)
    path.write_text(BROKEN, encoding="utf-8")
    ran = asyncio.run(store.fire("PreToolUse", tool_name="read_file"))
    assert len(ran) == 1, "the hook held from before the file broke still runs"
    assert path.read_text(encoding="utf-8") == BROKEN
    assert hooks.unreadable_file(home) is not None
    # Nothing held, nothing run: still nothing written.
    fresh = ScriptHookStore(config_dir=home)
    assert asyncio.run(fresh.fire("PreToolUse", tool_name="read_file")) == []
    assert path.read_text(encoding="utf-8") == BROKEN


def test_a_folder_refused_is_not_held_either(home):
    """The folders, tags and boards are held in memory too: a refused change is taken back, so
    the list does not show it and the next write after a repair does not land it."""
    state = _state()
    (home / "folders.json").write_text(json.dumps([{"id": "keep"}]), encoding="utf-8")
    state.load_folders()
    (home / "folders.json").write_text(BROKEN, encoding="utf-8")
    state._folders.append({"id": "refused"})
    with pytest.raises(record_files.Unreadable):
        state.save_folders()
    assert [f["id"] for f in state._folders] == ["keep"]
    (home / "folders.json").write_text(json.dumps([{"id": "keep"}]), encoding="utf-8")
    state._folders.append({"id": "after"})
    state.save_folders()
    saved = json.loads((home / "folders.json").read_text(encoding="utf-8"))
    assert [f["id"] for f in saved] == ["keep", "after"]


def test_a_folder_made_over_an_unreadable_file_is_refused_with_the_reason(home):
    """Through the route the sidebar calls: 409 ``store_unreadable``, saying which file, why,
    and where its copy is kept, and nothing made."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.chat_folders import api_chat_folder_create
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    (home / "folders.json").write_text(BROKEN, encoding="utf-8")
    state = _state()
    state.load_folders()

    async def go() -> tuple[int, dict]:
        app = web.Application(middlewares=[request_boundary_middleware()])
        app["state"] = state
        app.router.add_post("/api/chat/folders", api_chat_folder_create)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/folders", json={"name": "Work"})
            return resp.status, await resp.json()

    status, body = asyncio.run(go())
    assert status == 409
    assert body["error"]["code"] == "store_unreadable"
    message = body["error"]["message"]
    assert "folders.json could not be read" in message
    [copy] = record_files.kept_copies(home / "folders.json")
    assert copy.name in message
    assert state._folders == []
    assert (home / "folders.json").read_text(encoding="utf-8") == BROKEN


def test_an_inbox_item_that_arrived_meanwhile_lands_once_the_file_reads_again(home):
    """An arriving item is held while inbox.json cannot be read, and written with what the
    repaired file holds at the next save: nothing is lost on either side."""
    from personalclaw.inbox import InboxStore

    path = home / "inbox.json"
    path.write_text(BROKEN, encoding="utf-8")
    store = InboxStore(path)
    store.load()
    store.add(_item("arrived_2", "arrived"))
    store.flush()
    assert path.read_text(encoding="utf-8") == BROKEN
    path.write_text(json.dumps({"items": [_item_row("keep_1")]}), encoding="utf-8")
    store.flush()
    ids = {item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["items"]}
    assert ids == {"keep_1", "arrived_2"}


def test_a_run_never_drops_queued_edits_it_could_not_read(home):
    """A run's queued edits are written whole from what its controller holds, and an empty queue
    removes the file. Over a record that cannot be read, either would lose the edits in it."""
    from personalclaw.workflows import store as wf_store

    path = wf_store.run_dir("run-1") / wf_store.PENDING_MUTATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BROKEN, encoding="utf-8")
    for entries in ([{"ops": [], "actor": "user"}], []):
        with pytest.raises(record_files.Unreadable):
            wf_store.write_pending_mutations("run-1", entries)
        assert path.read_text(encoding="utf-8") == BROKEN
    assert wf_store.read_pending_mutations("run-1") == []
    # Readable, as before: a queue written, then emptied and removed.
    path.write_text(json.dumps([{"ops": [], "actor": "user"}]), encoding="utf-8")
    wf_store.write_pending_mutations("run-1", [{"ops": [], "actor": "owner"}])
    assert wf_store.read_pending_mutations("run-1") == [{"ops": [], "actor": "owner"}]
    wf_store.write_pending_mutations("run-1", [])
    assert not path.exists()


def test_app_updates_are_not_announced_again_and_again(home, monkeypatch):
    """The marks of the updates already announced cannot be read and are never written over, so
    each read of the Apps page would announce every update again. None is announced until they
    can be read; the Apps page still lists them."""
    from personalclaw.apps import catalog

    path = home / "entity_settings" / "app_updates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BROKEN, encoding="utf-8")
    update = {"name": "fixture-app", "latestVersion": "2.0.0"}
    announced: list[dict] = []
    monkeypatch.setattr(catalog, "updates_available", lambda: [update])
    monkeypatch.setattr(catalog, "_emit_app_update", lambda _state, u: announced.append(u))
    for _ in range(2):
        assert catalog.surface_app_updates(object()) == [update]
    assert announced == []
    assert path.read_text(encoding="utf-8") == BROKEN
    # A file holding nothing holds no mark: announced once, and the mark written.
    path.write_text("", encoding="utf-8")
    catalog.surface_app_updates(object())
    catalog.surface_app_updates(object())
    assert announced == [update]
    assert json.loads(path.read_text(encoding="utf-8")) == {"notified": {"fixture-app": "2.0.0"}}


def test_an_accept_the_decision_store_refuses_installs_nothing(home, monkeypatch):
    """An accept's decision is written after the install, so a decision store that cannot be
    read refuses it first: refused afterwards, the change would be in and the proposal still
    waiting."""
    from personalclaw.learning import proposals

    proposals._decisions_path().parent.mkdir(parents=True, exist_ok=True)
    proposals._decisions_path().write_text(BROKEN, encoding="utf-8")
    prop = proposals.Proposal(id="p1", kind="skill", title="t", body="b", fingerprint="f1")
    monkeypatch.setattr(proposals, "_load", lambda pid: prop if pid == "p1" else None)
    installed: list[str] = []
    with pytest.raises(record_files.Unreadable):
        proposals.accept("p1", installer=lambda p: installed.append(p.id))
    assert installed == [], "the change was installed before the store refused its decision"
    assert proposals._decisions_path().read_text(encoding="utf-8") == BROKEN


def test_the_inbound_gate_denies_and_tells_nobody_while_trust_cannot_be_read(home):
    """Nothing is trusted when the trust store cannot be read (fail closed), so every sender is
    unknown. Recording each one would write over the file, and with no dedup that can be kept,
    each message would raise a notice: the message is denied and nothing else happens."""
    from personalclaw import channel_trust

    path = home / "entity_settings" / "channel_trust.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BROKEN, encoding="utf-8")
    state = MagicMock()
    for _ in range(2):
        verdict = channel_trust.guard_inbound(state, "fixture-chat", "u1", text="hello")
        assert not verdict.allowed
    state.notify.assert_not_called()
    assert path.read_text(encoding="utf-8") == BROKEN


def test_a_routing_proposal_waits_while_the_queue_cannot_be_read(home):
    """A learned stage files proposals on a schedule: over a queue it cannot read it files none,
    rather than replace the pending proposals and every rejection's cooldown with its one."""
    from personalclaw.routing import proposals

    path = home / "routing_proposals.json"
    path.write_text(BROKEN, encoding="utf-8")
    filed = proposals.propose(
        use_case="reasoning",
        query_class="summarize",
        current=["cloud:big"],
        proposed=["local:small"],
        evidence={},
        home=home,
    )
    assert filed is None
    assert path.read_text(encoding="utf-8") == BROKEN


def test_the_found_file_is_said_once_and_named_until_it_changes(home, caplog):
    """The first read to find a file it cannot read says so in the log, once however often it is
    read, and names it for the Doctor until the file changes."""
    from personalclaw.dashboard import views_store

    path = home / "dashboard_views.json"
    path.write_text(BROKEN, encoding="utf-8")
    with caplog.at_level("WARNING", logger="personalclaw.record_files"):
        for _ in range(3):
            assert views_store._read_disk() == {"views": [], "overlay": {}}
            with pytest.raises(record_files.Unreadable):
                views_store.create_view("New")
    said = [r for r in caplog.records if "dashboard_views.json could not be read" in r.message]
    assert len(said) == 1

    def named() -> list[Path]:
        # This process's findings, in this test's home (a worker runs other tests' homes too).
        return [f.path for f in record_files.unreadable_files() if home in f.path.parents]

    assert named() == [path]
    path.write_text(json.dumps({"views": [], "overlay": {}}), encoding="utf-8")
    assert named() == []


def test_a_refusal_raised_again_and_again_carries_one_traceback(home):
    """The refusal for a file found unreadable is remembered, for the log and the Doctor. Raised
    from that one instance by every read and write, it kept every traceback it had been raised
    through, so each warning logged with it said all of them: every raise is a fresh refusal."""
    import traceback

    from personalclaw.dashboard import views_store
    from personalclaw.hooks import ScriptHookStore

    (home / "dashboard_views.json").write_text(BROKEN, encoding="utf-8")
    (home / "hooks.json").write_text(BROKEN, encoding="utf-8")
    store = ScriptHookStore(config_dir=home)
    writes: list[Callable[[], object]] = [
        lambda: views_store.create_view("New"),
        lambda: store.create({"id": "new", "name": "New", "event": "Stop"}),
    ]
    for write in writes:
        lengths = set()
        for _ in range(4):
            with pytest.raises(record_files.Unreadable) as refused:
                write()
            lengths.add(len(traceback.format_exception(refused.value)))
        assert len(lengths) == 1, lengths
