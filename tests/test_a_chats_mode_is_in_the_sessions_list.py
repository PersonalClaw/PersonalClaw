"""The chat list the gateway pushes says each chat's permission mode, YOLO included.

Measured from the code: an open composer showed a chat's permission mode from the chat's own read
and from the answers to its own picks, so a mode set anywhere else (another tab picking for the
same chat, YOLO turned on from another chat, YOLO running out) showed only once the chat was opened
again. The list the gateway pushes on every change (``DashboardState.push_sessions_update``) said
each chat's trust but no longer YOLO, and nothing pushed it when YOLO turned on or off by itself.
Now each row says the mode the chat runs in, as its own read says it (``approval``). YOLO turning
on or off, its time running out included, pushes no list: an app's socket hears every list (cut to
its own rows, which never run on your YOLO), and #3632 made sure it learns nothing of your YOLO.
Your own pages hear the owner-only ``yolo`` refresh, and each open chat reads its mode again on it.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from personalclaw import trust_mode
from personalclaw.dashboard.state import DashboardState


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    trust_mode.disable_yolo()
    s = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    s.frames = []  # type: ignore[attr-defined]
    s._broadcast = lambda payload: s.frames.append(payload)  # type: ignore[method-assign]
    yield s
    trust_mode.disable_yolo()


def _modes(state) -> dict[str, str]:
    """Each chat's mode as the last chat list the gateway pushed says it."""
    lists = [f for f in state.frames if f.get("_type") == "sessions"]
    assert lists, "no chat list went out"
    return {row["key"]: row.get("approval") for row in lists[-1]["_sessions_list"]}


def _chats(state):
    plain = state.get_or_create_session("chat-plain")
    trusted = state.get_or_create_session("chat-trusted")
    trusted._trust = True
    reads = state.get_or_create_session("chat-reads")
    reads._trust_reads = True
    apps = state.get_or_create_session("chat-app", created_by_app="demo-app")
    apps._trust = True
    return plain, trusted, reads, apps


def test_each_row_says_the_mode_its_chat_runs_in(state):
    """🔴 Red before: the rows said no mode at all."""
    _chats(state)

    state.push_sessions_update()

    assert _modes(state) == {
        "chat-plain": "normal",
        "chat-trusted": "trust",
        "chat-reads": "trust_reads",
        # An app's chat runs on the app's permissions, never on yours.
        "chat-app": "normal",
    }


def test_yolo_turned_on_says_yolo_on_every_chat_of_yours(state):
    """🔴 Red before: no row said it."""
    _chats(state)
    state.enable_yolo()

    state.push_sessions_update()

    assert _modes(state) == {
        "chat-plain": "yolo",
        "chat-trusted": "yolo",
        "chat-reads": "yolo",
        "chat-app": "normal",
    }


def test_yolo_running_out_says_each_chats_own_mode_again(state, monkeypatch):
    """🔴 Red before: no row said a chat's mode."""
    _chats(state)
    monkeypatch.setattr(state, "_YOLO_TTL", 0.05)
    state.enable_yolo()
    time.sleep(0.1)

    state.push_sessions_update()

    assert not state.is_yolo_active()
    assert _modes(state)["chat-plain"] == "normal"
    assert _modes(state)["chat-trusted"] == "trust"


def test_yolo_turning_on_or_off_rings_only_your_own_pages(state, monkeypatch):
    """No chat list goes out as YOLO turns on, off or runs out: an app's socket hears every list,
    so one sent then would say when your YOLO flips. The owner-only ``yolo`` refresh goes out."""
    _chats(state)
    monkeypatch.setattr(state, "_YOLO_TTL", 0.05)
    state.frames.clear()

    state.enable_yolo()
    state.disable_yolo()
    state.enable_yolo()
    time.sleep(0.1)
    assert not state.is_yolo_active()

    # On, off, on again, and its time running out: four flips, each one owner-only refresh.
    assert [f["_type"] for f in state.frames] == ["refresh"] * 4
    assert all(f["kinds"] == "yolo" for f in state.frames)


def test_a_chats_own_read_says_the_same_mode(state):
    """The list and the chat's own read say one mode, worked out once (``approval_of``)."""
    plain, trusted, reads, apps = _chats(state)
    state.enable_yolo()
    assert [state.approval_of(s) for s in (plain, trusted, reads, apps)] == [
        "yolo",
        "yolo",
        "yolo",
        "normal",
    ]
    state.disable_yolo()
    assert [state.approval_of(s) for s in (plain, trusted, reads, apps)] == [
        "normal",
        "trust",
        "trust_reads",
        "normal",
    ]
