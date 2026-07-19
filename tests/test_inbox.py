"""Tests for the inbox module — config, user resolver, state, and store."""

import json
import time
from unittest.mock import patch

from personalclaw.config.loader import InboxConfig
from personalclaw.inbox import (
    ItemStatus,
    InboxStore,
    InboxItem,
    InboxState,
    UserResolver,
)

# ── InboxConfig ──


def test_inbox_config_defaults():
    cfg = InboxConfig()
    assert cfg.enabled is False
    assert cfg.user_id == ""
    assert cfg.watched_channels == []
    assert cfg.poll_interval_seconds == 60
    assert cfg.style_rules == []


def test_inbox_config_loaded_from_json(tmp_path):
    config_json = tmp_path / "config.json"
    config_json.write_text(json.dumps({
        "inbox": {
            "enabled": True,
            "user_id": "U123",
            "watched_channels": ["C001", "C002"],
            "poll_interval_seconds": 30,
            "style_rules": ["never commit to dates"],
        }
    }))
    with patch("personalclaw.config.loader.config_path", return_value=config_json):
        from personalclaw.config.loader import AppConfig
        cfg = AppConfig.load()
    assert cfg.inbox.enabled is True
    assert cfg.inbox.user_id == "U123"
    assert cfg.inbox.watched_channels == ["C001", "C002"]
    assert cfg.inbox.poll_interval_seconds == 30
    assert cfg.inbox.style_rules == ["never commit to dates"]


def test_inbox_config_min_poll_interval(tmp_path):
    config_json = tmp_path / "config.json"
    config_json.write_text(json.dumps({
        "inbox": {"poll_interval_seconds": 5}
    }))
    with patch("personalclaw.config.loader.config_path", return_value=config_json):
        from personalclaw.config.loader import AppConfig
        cfg = AppConfig.load()
    assert cfg.inbox.poll_interval_seconds >= 30


# ── UserResolver ──


def test_user_resolver_cache():
    resolver = UserResolver()
    resolver.put("U1", "Alice")
    assert resolver.get_cached("U1") == "Alice"
    assert resolver.get_cached("U999") is None


def test_user_resolver_dump_load():
    resolver = UserResolver()
    resolver.put("U1", "Alice")
    data = resolver.dump()
    resolver2 = UserResolver()
    resolver2.load(data)
    assert resolver2.get_cached("U1") == "Alice"


def test_user_resolver_ttl_expired():
    resolver = UserResolver()
    resolver._cache["U1"] = ("Alice", time.time() - 90000)  # expired
    assert resolver.get_cached("U1") is None


# ── InboxState ──


def test_state_save_load(tmp_path):
    state = InboxState(tmp_path / "state.json")
    state.last_read_ts = {"C1": "123.456"}
    state.channel_names = {"C1": "#general"}
    state.dismissed = {"C1_789"}
    state.muted_threads = {"111.222"}
    state.user_resolver.put("U1", "Bob")
    state.save()

    state2 = InboxState(tmp_path / "state.json")
    state2.load()
    assert state2.last_read_ts == {"C1": "123.456"}
    assert state2.channel_names == {"C1": "#general"}
    assert "C1_789" in state2.dismissed
    assert "111.222" in state2.muted_threads
    assert state2.user_resolver.get_cached("U1") == "Bob"


def test_state_load_missing_file(tmp_path):
    state = InboxState(tmp_path / "nope.json")
    state.load()  # should not raise
    assert state.last_read_ts == {}


# ── InboxItem ──


def test_item_roundtrip():
    item = InboxItem(
        id="C1_123", channel="C1", channel_name="#test",
        thread_ts=None, message="hello", sender_id="U1", sender_name="Alice",
        created_at=1000.0,
    )
    d = item.to_dict()
    item2 = InboxItem.from_dict(d)
    assert item2.id == "C1_123"
    assert item2.sender_name == "Alice"
    assert item2.status == ItemStatus.PENDING


# ── InboxStore ──


def test_inbox_add_and_pending(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    item = InboxItem(
        id="C1_1", channel="C1", channel_name="#t",
        thread_ts=None, message="hi", sender_id="U1", sender_name="A",
        created_at=time.time(),
    )
    inbox.add(item)
    assert len(inbox.pending()) == 1
    # add() marks dirty but doesn't save yet
    assert inbox._dirty is True
    assert not (tmp_path / "inbox.json").exists()


def test_inbox_flush_saves(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    inbox.add(InboxItem(
        id="C1_1", channel="C1", channel_name="#t",
        thread_ts=None, message="hi", sender_id="U1", sender_name="A",
        created_at=time.time(),
    ))
    inbox.flush()
    assert (tmp_path / "inbox.json").exists()
    assert inbox._dirty is False


def test_inbox_flush_noop_when_clean(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    inbox.flush()  # nothing to save
    assert not (tmp_path / "inbox.json").exists()


def test_inbox_update(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    item = InboxItem(
        id="C1_1", channel="C1", channel_name="#t",
        thread_ts=None, message="hi", sender_id="U1", sender_name="A",
        created_at=time.time(),
    )
    inbox.add(item)
    inbox.flush()
    updated = inbox.update("C1_1", status=ItemStatus.SENT, draft="reply text")
    assert updated is not None
    assert updated.status == ItemStatus.SENT
    assert updated.draft == "reply text"


def test_inbox_update_missing(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    assert inbox.update("nope", status=ItemStatus.SENT) is None


def test_inbox_cleanup_by_retention(tmp_path):
    """Items older than retention_days are deleted regardless of source/status."""
    inbox = InboxStore(tmp_path / "inbox.json")
    old_item = InboxItem(
        id="C1_old", channel="C1", channel_name="#t",
        thread_ts=None, message="old", sender_id="U1", sender_name="A",
        created_at=time.time() - 91 * 86400,
    )
    new_item = InboxItem(
        id="agent_new", channel="agent", channel_name="agent",
        thread_ts=None, message="new", sender_id="a", sender_name="a",
        created_at=time.time(),
    )
    inbox.add(old_item)
    inbox.add(new_item)
    inbox.flush()
    removed = inbox.cleanup_by_retention(90)
    assert removed == 1
    assert "C1_old" not in inbox.items
    assert "agent_new" in inbox.items
    # persisted
    inbox2 = InboxStore(tmp_path / "inbox.json")
    inbox2.load()
    assert "C1_old" not in inbox2.items


# ── Alert evaluation ──


def test_evaluate_alert_keyword_and_name():
    from personalclaw.inbox import evaluate_alert
    item = InboxItem(
        id="C1_1", channel="C1", channel_name="#t", thread_ts=None,
        message="this is URGENT: prod is down", sender_id="U1", sender_name="A",
    )
    assert evaluate_alert(item, {"alert_keywords": ["urgent"]}) == "keyword: urgent"
    assert evaluate_alert(item, {"alert_keywords": ["nomatch"]}) == ""
    named = InboxItem(
        id="C1_2", channel="C1", channel_name="#t", thread_ts=None,
        message="hey Marlow can you look?", sender_id="U1", sender_name="A",
    )
    # full configured name; message uses one part → still fires (word-boundary
    # match per name part, short particles skipped)
    assert evaluate_alert(
        named, {"alert_on_name_mention": True}, user_name="Jordan Marlow"
    ) == "name mention"
    # substring inside another word must NOT fire
    inside = InboxItem(
        id="C1_4", channel="C1", channel_name="#t", thread_ts=None,
        message="the marlowe novel arrived", sender_id="U1",
        sender_name="A",
    )
    assert evaluate_alert(
        inside, {"alert_on_name_mention": True}, "Marlow"
    ) == ""
    assert evaluate_alert(named, {"alert_on_name_mention": False}, "marlow") == ""
    assert evaluate_alert(named, {"alert_on_name_mention": True}, "") == ""


def test_notify_inbox_alert_redacts_and_notifies():
    from unittest.mock import MagicMock
    from personalclaw.inbox import notify_inbox_alert
    item = InboxItem(
        id="C1_3", channel="C1", channel_name="#general", thread_ts=None,
        message="urgent thing", sender_id="U1", sender_name="Alice",
    )
    state = MagicMock()
    notify_inbox_alert(state, item, "keyword: urgent")
    state.notify.assert_called_once()
    kind, title, body = state.notify.call_args[0]
    assert kind == "inbox_alert"
    assert "Alice" in title and "#general" in title
    assert "keyword: urgent" in body
    notify_inbox_alert(None, item, "x")  # headless → no raise


def test_inbox_save_load(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.json")
    inbox.add(InboxItem(
        id="C1_1", channel="C1", channel_name="#t",
        thread_ts=None, message="hi", sender_id="U1", sender_name="A",
        created_at=1000.0,
    ))
    inbox.flush()
    inbox2 = InboxStore(tmp_path / "inbox.json")
    inbox2.load()
    assert "C1_1" in inbox2.items


