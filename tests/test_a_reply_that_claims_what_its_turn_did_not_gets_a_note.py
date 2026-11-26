"""A reply that claims work its own turn's record does not show gets a short note saying so.

🔴 Before: a turn could end "The helper completed its survey" having run no helper, or say a file
is staged in a repository where it is not, and the reply went out as it was; the only check of
what a turn claimed was the check-work skill, run when someone asked. Now the turn's last answer is
held to its own record before it settles, at no model cost (`turn_claims`), and each claim the
record does not bear out gets one line after the answer. A reply whose claims match gets none.

The chat cases drive the real turn engine (``run_chat``) with the real ``NativeAgentRuntime``;
only the model is scripted.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.config import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_claims import GIT
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.turn_claims import TurnRecord, note_for

HELPER_SAID = "The helper completed its survey."
HELPER_NOTE = (
    "Note: this answer says “The helper completed its survey”; "
    "the turn's record shows no helper ran."
)


class _Tools(ToolProvider):
    """One tool that writes a note, and asks nobody."""

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="write_note",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="done")


def _chat(tmp_path: Path, reply: str, *, folder: Path | None = None) -> tuple[DashboardState, Any]:
    """A chat whose model calls ``write_note`` once and then answers *reply*."""

    def _model() -> _ScriptedModel:
        call = AgentEvent(
            kind=EVENT_TOOL_CALL, tool_call_id="call-w", title="write_note", tool_input="{}"
        )
        return _ScriptedModel(
            [
                [call, AgentEvent(kind=EVENT_COMPLETE)],
                [AgentEvent(kind=EVENT_TEXT_CHUNK, text=reply), AgentEvent(kind=EVENT_COMPLETE)],
            ]
        )

    def factory(_key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(), model_provider=_model(), tool_providers=[_Tools()], cwd=tmp_path
        )

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        conversation_log=log,
    )
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = state.get_or_create_session()
    session.workspace_dir = str(folder) if folder is not None else ""
    return state, session


async def _answer(state: DashboardState, session: Any) -> str:
    session.append("user", "go", "msg msg-u")
    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    [last] = [m for m in session.messages if m.get("role") == "assistant"][-1:]
    return str(last.get("content") or "")


def _streamed(state: DashboardState) -> str:
    return "".join(
        call.args[1]["content"]
        for call in state.broadcast_ws.call_args_list  # type: ignore[attr-defined]
        if call.args and call.args[0] == "chat_chunk"
    )


def _git(folder: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=folder,
        env=env,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, *, stage: bool) -> Path:
    """A repository whose `notes.md` was committed and then changed, staged or not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "notes.md").write_text("# Notes\n")
    _git(repo, "add", "notes.md")
    _git(repo, "commit", "-q", "-m", "notes")
    (repo / "notes.md").write_text("# Notes\nWeek one\n")
    if stage:
        _git(repo, "add", "notes.md")
    return repo


@pytest.mark.asyncio
async def test_a_helper_the_turn_never_ran_is_noted(tmp_path):
    state, session = _chat(tmp_path, HELPER_SAID)
    said = await _answer(state, session)
    assert said == f"{HELPER_SAID}\n\n{HELPER_NOTE}", said
    # Streamed as the answer's own words, so the chat shows it as the turn ends.
    assert _streamed(state).endswith(f"\n\n{HELPER_NOTE}")


@pytest.mark.asyncio
async def test_a_file_said_to_be_staged_that_is_not_is_noted_naming_both(tmp_path):
    repo = _repo(tmp_path, stage=False)
    state, session = _chat(tmp_path, "I updated the plan, and `notes.md` is staged.", folder=repo)
    said = await _answer(state, session)
    assert said.endswith(
        "\n\nNote: this answer says `notes.md` is staged; "
        "the repository shows it has changes that are not staged."
    ), said


@pytest.mark.asyncio
async def test_a_reply_whose_claims_match_its_record_gets_no_note(tmp_path):
    """The control: a note on every turn cannot pass."""
    repo = _repo(tmp_path, stage=True)
    reply = "I updated the plan, and `notes.md` is staged."
    state, session = _chat(tmp_path, reply, folder=repo)
    assert await _answer(state, session) == reply
    assert "Note:" not in _streamed(state)


@pytest.mark.parametrize(
    ("reply", "record"),
    [
        ("I will ask a helper to finish the survey.", TurnRecord()),
        (HELPER_SAID, TurnRecord(tools=("subagent_run",))),
        (HELPER_SAID, TurnRecord(tools=("Task: survey the docs",))),
        ("`notes.md` is staged.", TurnRecord(folder="")),
        ("I wrote the plan.", TurnRecord()),
    ],
    ids=["a plan", "a helper that ran", "an agent CLI's helper", "no folder", "no claim it reads"],
)
def test_what_the_record_bears_out_or_cannot_settle_says_nothing(reply: str, record: TurnRecord):
    assert note_for(reply, record) == ""


def test_a_folder_that_is_no_repository_says_nothing(tmp_path):
    assert note_for("`notes.md` is staged.", TurnRecord(folder=str(tmp_path)), git=GIT) == ""


def test_a_file_never_added_is_not_staged(tmp_path):
    repo = _repo(tmp_path, stage=True)
    (repo / "plan.md").write_text("new\n")
    assert note_for("I staged `plan.md`.", TurnRecord(folder=str(repo)), git=GIT) == (
        "Note: this answer says `plan.md` is staged; the repository shows it is not staged."
    )


def test_without_a_git_a_staged_claim_is_not_read(tmp_path):
    """The repository is read only through the git its caller hands in (`chat_claims.GIT`)."""
    repo = _repo(tmp_path, stage=True)
    (repo / "plan.md").write_text("new\n")
    assert note_for("I staged `plan.md`.", TurnRecord(folder=str(repo))) == ""


# ── `personalclaw run` offers the check too ──────────────────────────────────────────────────


def _run_turn(reply: str, calls: int, *, session: str = "") -> str:
    """The offer line `personalclaw run` prints for a turn of *calls* calls answered *reply*."""
    from types import SimpleNamespace

    from personalclaw.cli_run import _Collector, check_work_offer

    collector = _Collector("chat-1-1", "plain")
    for n in range(calls):
        collector.feed({"type": "tool_call", "data": {"session": "chat-1-1", "tool": f"t{n}"}})
    collector.feed({"type": "chat_chunk", "data": {"session": "chat-1-1", "content": reply}})
    return check_work_offer(collector, SimpleNamespace(session=session))


def test_a_run_whose_turn_earns_the_check_offers_it():
    """🔴 Before: the offer was a chip in the web chat alone, so a run never saw it."""
    assert _run_turn("Done: I updated `notes.md`.", 3, session="week plan") == (
        "to check what this turn says it did: "
        "personalclaw run --session 'week plan' -p \"check your work\""
    )
    assert "--session NAME" in _run_turn("Done: I updated `notes.md`.", 3)


@pytest.mark.parametrize(
    ("reply", "calls"), [("Done.", 2), ("Here is what I found.", 3)], ids=["few calls", "no claim"]
)
def test_a_run_whose_turn_does_not_earn_it_offers_nothing(reply: str, calls: int):
    assert _run_turn(reply, calls, session="week plan") == ""
