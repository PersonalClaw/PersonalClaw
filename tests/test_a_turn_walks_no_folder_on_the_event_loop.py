"""A chat turn walks no folder on the gateway's event loop.

Each chat turn records the chat's folder as it stands when the turn begins
(``turn_checkpoints.begin_turn``): the path, size and modification time of each file, up to 20,000
of them, so a rewind's preview can name the files that changed with no backup. That is a walk of
the folder, and it ran inside ``run_chat`` on the event loop, so every turn, one that changed no
file included, stopped the whole gateway (every other chat, stream and request) for as long as the
walk took: about 65 ms for a 7,000-file repository, measured, and more at the cap.

Measured here rather than inferred from where the call sits: the walk asks the event loop to run
something and waits for it. The loop can run it only while the walk is somewhere else, so a walk on
the loop could never be answered.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw import turn_checkpoints as tc
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent


def _folder(tmp_path: Path, files: int = 400) -> Path:
    folder = tmp_path / "project"
    for i in range(files):
        sub = folder / f"pkg{i % 20}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"mod{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    return folder


def _watch_walks(monkeypatch, folder: Path, loop: asyncio.AbstractEventLoop) -> list[dict]:
    """Every listing of *folder* itself, with the thread it ran on and whether the event loop
    answered while it ran. ``os.scandir`` is what every walk of a folder starts with."""
    seen: list[dict] = []
    real_scandir = os.scandir
    loop_thread = threading.get_ident()
    top = str(folder.resolve())

    def scandir(path=".", *args, **kwargs):
        if os.fspath(path) == top:
            on_loop = threading.get_ident() == loop_thread
            answered = False
            if not on_loop:
                # The loop answers this only if it is free while the walk runs.
                done = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result=True), loop)
                answered = done.result(timeout=10)
            seen.append({"on_loop": on_loop, "loop_answered": answered})
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    return seen


def _a_turn_that_changes_no_file(tmp_path: Path, folder: Path):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent")
    session.workspace_dir = str(folder)
    _set_stream(
        client,
        [
            LLMEvent(kind=EVENT_TEXT_CHUNK, text="Nothing to change here."),
            LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
        ],
    )
    return state, session


@pytest.mark.asyncio
async def test_a_turn_with_no_file_changes_walks_no_folder_on_the_event_loop(tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    state, session = _a_turn_that_changes_no_file(tmp_path, folder)
    walks = _watch_walks(monkeypatch, folder, asyncio.get_running_loop())

    await _drive(state, session)

    # Positive control: the turn did record its folder, so "no walk on the loop" is not "no walk".
    assert walks, "the turn never recorded its folder"
    assert [w["on_loop"] for w in walks] == [False] * len(walks), walks
    assert all(w["loop_answered"] for w in walks), walks
    assert tc.current_turn(session.key) == 1


def _store_bytes(session_key: str) -> int:
    """Every byte the session's checkpoint store holds on disk."""
    root = tc.session_dir(session_key)
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


@pytest.mark.asyncio
async def test_a_turn_that_changes_nothing_adds_no_second_record_of_the_folder(tmp_path):
    """Cheap in the store too. A turn that changed nothing finds the folder as the turn before it
    recorded it, so it keeps no second copy of that record: it costs its own small manifest. A
    record of every file in every turn's manifest cost about 70 bytes a file a turn (a 7,000-file
    repository: about 480 KB a turn, rewritten whole by each backup the turn then took)."""
    folder = _folder(tmp_path, files=1000)
    state, session = _a_turn_that_changes_no_file(tmp_path, folder)

    await _drive(state, session)
    first = _store_bytes(session.key)
    await _drive(state, session)
    second = _store_bytes(session.key)

    assert tc.current_turn(session.key) == 2
    # Positive control: the first turn's store does hold a record of the 1,000 files.
    assert first > 4096, first
    assert second - first < 1024, (first, second)
    # Vacuity floor: a turn after a file DID change records the folder anew.
    (folder / "pkg0" / "mod0.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
    await _drive(state, session)
    assert _store_bytes(session.key) - second > 2048
