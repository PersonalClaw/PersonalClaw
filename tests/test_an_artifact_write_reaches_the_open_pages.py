"""An open artifact page re-reads the artifact once the store has written it, whoever wrote it.

The Iterate panel is a chat embedded beside the artifact, in a document of its own, so the artifact
page learned about a new version from that chat's ``tool_call`` frame. That frame is sent when the
model ASKS for the call, before its approval and before the write: the page re-read the version it
already showed and never read again (the panel said "→ version 2" while the pane kept "v1").

The store's own change signal now reaches every open page as the gateway's ``artifacts`` refresh
hint, AFTER the write: the agent's tools (which write from an executor thread), a workflow step,
another tab, a revert, a delete. The gateway subscribes the relay at start and lets it go when it
stops.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.artifacts import changes
from personalclaw.artifacts.native import NativeArtifactProvider

_PNG = b"\x89PNG\r\n\x1a\n" + b"a picture" * 4


@pytest.fixture
def world(tmp_path):
    state = _make_state(tmp_path)
    frames: list[tuple[str, object]] = []
    state._ws_clients = [MagicMock(closed=False)]  # a page is open
    state.broadcast_ws = lambda kind, data: frames.append((kind, data))
    changes.subscribe(state.announce_artifact_change)
    try:
        yield NativeArtifactProvider(root=tmp_path / "artifacts"), frames
    finally:
        changes.unsubscribe(state.announce_artifact_change)


_WRITTEN = ("refresh", {"kinds": ["artifacts"]})


def test_a_version_the_agent_writes_from_its_thread_reaches_the_page(world) -> None:
    store, frames = world
    art = store.create_binary(name="Opening slide", data=_PNG, mime="image/png", actor="agent")
    assert frames == [_WRITTEN], "the creation itself is a write the library shows"
    frames.clear()

    edited = b"\x89PNG\r\n\x1a\n" + b"the bubbles are blue" * 4
    worker = threading.Thread(
        target=lambda: store.update_binary(art.slug, data=edited, mime="image/png", actor="agent")
    )
    worker.start()
    worker.join()

    assert frames == [_WRITTEN]
    got = store.get(art.slug)
    assert got is not None and got.version == 2, "the hint follows the write, never precedes it"


def test_every_writer_of_a_text_artifact_is_heard_and_a_no_op_is_not(world) -> None:
    store, frames = world
    note = store.create(name="Notes", content="# Notes", kind="markdown")
    store.update(note.slug, content="# Notes, v2", snapshot=True, actor="user")
    store.revert(note.slug, 1, actor="user")
    assert frames == [_WRITTEN] * 3

    frames.clear()
    store.update(note.slug, content="# Notes", snapshot=True, actor="user")  # the body it has
    assert frames == [], "a write that changed nothing says nothing"

    assert store.delete(note.slug)
    assert frames == [_WRITTEN], "a removed artifact moves the library too"


@pytest.mark.asyncio
async def test_the_gateway_relays_the_store_while_it_runs_and_not_after(tmp_path) -> None:
    from personalclaw.dashboard.server import start_dashboard

    runner, state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        assert state.announce_artifact_change in changes._listeners
    finally:
        await runner.cleanup()
    assert state.announce_artifact_change not in changes._listeners
