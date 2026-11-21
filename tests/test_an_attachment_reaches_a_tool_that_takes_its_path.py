"""A file attached in a chat reaches a tool that takes a file path.

Measured before this was written: an attached file reached the model labelled with its name only
("### Attached file: screen.png"), and an attached image by its name in the turn's note ("The user
attached an image to this message: screen.png"). Nothing said where the file was saved, so a tool
that takes a file path, called on "screen.png", found no such file, and the agent fell back to
judging the attachment by eye.

Now each label names where the file is saved as well as its name
(``attachment_extract.saved_at``), and the model is told the path is for a tool that takes a file
and the name is for what it writes to the user. Every surface the user sees keeps the name the
file was attached with.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_runner import (
    _attached_images_note,
    _attachment_text_blocks,
    run_chat,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

pytestmark = pytest.mark.asyncio

STORED = "c" * 32


def _upload(name: str, data: bytes) -> str:
    """An uploaded attachment, where the upload route puts it (``<uuid-hex>_<name>``)."""
    uploads = config_loader.config_dir() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{STORED}_{name}"
    path.write_bytes(data)
    return str(path)


def _extracted(text: str):
    from personalclaw.knowledge.extract import Extracted

    return Extracted(text, True)


def _reading(text: str):
    extractor = MagicMock()
    extractor.get = AsyncMock(return_value=_extracted(text))
    return patch("personalclaw.dashboard.attachment_extract.get_extractor", return_value=extractor)


# ── what the model is told ───────────────────────────────────────────────────────────────────


async def test_an_attached_files_label_names_where_it_is_saved():
    """🔴 Red before: the label named the file and nothing else."""
    path = _upload("notes.txt", b"Remember the milk.")

    with _reading("Remember the milk."):
        block = await _attachment_text_blocks([path])

    assert f"### Attached file: notes.txt (saved at {os.path.realpath(path)})" in block
    assert "a tool that opens a file at a path itself" in block
    assert "call each file by its name" in block


async def test_an_attached_images_note_names_where_it_is_saved():
    """🔴 Red before: the note named the image and nothing else."""
    path = _upload("screen.png", b"\x89PNG\r\n")

    note = _attached_images_note([path])

    assert f"screen.png (saved at {os.path.realpath(path)})" in note
    assert "a tool that opens a file at a path itself" in note


# ── a tool that takes a path, on the attachment ──────────────────────────────────────────────


class _Opener(ToolProvider):
    """A tool that takes a file path and opens it, as a critique or a converter does."""

    def __init__(self) -> None:
        self.opened: list[bytes] = []

    @property
    def name(self) -> str:
        return "opener"

    @property
    def display_name(self) -> str:
        return "Opener"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="measure_file",
                description="Measure the file at a path.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        target = Path(str(arguments.get("path") or "")).expanduser()
        if not target.is_file():
            return ToolResult(success=False, output=f"No file at {arguments.get('path')}")
        self.opened.append(target.read_bytes())
        return ToolResult(success=True, output=f"{len(self.opened[-1])} bytes")


class _CallsTheTool:
    """A model that hands ``measure_file`` the file the turn names: where it is saved, when the
    label says, else its name, as a model given only the name does."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        if len(self.requests) == 1:
            text = "\n".join(str(m.get("content") or "") for m in messages)
            found = re.search(r"### Attached file: (\S+)(?: \(saved at ([^)]+)\))?", text)
            assert found, "the turn named no attached file"
            path = found.group(2) or found.group(1)
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="measure_file",
                tool_input=json.dumps({"path": path}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="It is 18 bytes.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _state(tmp_path: Path, model: Any, tool: ToolProvider) -> DashboardState:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[tool],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    sessions = MagicMock(count=0)
    sessions._sessions = {}
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=(None, None))
    sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    sessions.record_failure = AsyncMock()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = None
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


async def _send(state: DashboardState, name: str, text: str, files: list[str]):
    session = state.get_or_create_session(name)
    session.append("user", text, "msg msg-u", meta={"files": files})
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        _reading("Q3 totals: 4,210."),
    ):
        await run_chat(state, session, text)
    return session


async def test_a_tool_that_takes_a_path_opens_the_file_the_chat_attached(tmp_path):
    """🔴 Red before: the tool was handed "report.csv", and there is no file there."""
    data = b"Q3 totals: 4,210.\n"
    path = _upload("report.csv", data)
    tool = _Opener()
    state = await _state(tmp_path, _CallsTheTool(), tool)

    await _send(state, "measure", "How big is this file?", [path])

    assert tool.opened == [data]


async def test_the_chat_keeps_the_name_the_file_was_attached_with(tmp_path):
    """What the user sees of her message and the file is as she attached it: the saved path is
    the model's, for its tools, and nowhere on her side of the chat."""
    path = _upload("report.csv", b"Q3 totals: 4,210.\n")
    state = await _state(tmp_path, _CallsTheTool(), _Opener())

    session = await _send(state, "names", "How big is this file?", [path])

    user = next(m for m in session.messages if m.get("role") == "user")
    assert user["content"] == "How big is this file?"
    shown = [m["content"] for m in session.messages if m.get("role") in ("user", "assistant")]
    assert not any(STORED in str(c) for c in shown)


async def test_a_private_chats_attachment_reaches_its_tool_under_that_chats_rules(tmp_path):
    """An Incognito chat's attachment reaches the tool the same way, and the call runs as that
    chat's work: what it does is held to the chat's mode (``memory_writes``)."""
    from personalclaw import memory_writes

    data = b"Q3 totals: 4,210.\n"
    path = _upload("report.csv", data)
    seen_modes: list[str | None] = []

    class _Watching(_Opener):
        async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
            seen_modes.append(memory_writes.session_mode("dashboard:private-1"))
            return await super().invoke(tool_name, arguments)

    tool = _Watching()
    state = await _state(tmp_path, _CallsTheTool(), tool)
    state.get_or_create_session("private-1", memory_mode="incognito")

    await _send(state, "private-1", "How big is this file?", [path])

    assert tool.opened == [data]
    assert seen_modes == ["incognito"]


async def test_the_built_in_file_tools_still_refuse_where_an_attachment_is_saved(tmp_path):
    """Naming the saved path opened no folder: PersonalClaw's own file tools reach the workspace
    and the places the owner allowed, and the home's uploads are none of them. The note says so,
    so the model gives the path only to a tool that opens a file itself."""
    from personalclaw.file_scope import FileScope, OutOfScope

    path = _upload("report.csv", b"Q3 totals: 4,210.\n")

    with pytest.raises(OutOfScope):
        FileScope([tmp_path / "workspace"]).resolve(os.path.realpath(path))
    with _reading("Q3 totals: 4,210."):
        block = await _attachment_text_blocks([path])
    assert "The built-in file tools (read_file, grep, glob) do not reach that folder" in block
