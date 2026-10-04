"""An Incognito or Temporary chat changes nothing in the artifact library, as the library's own
routes hold such a chat, whichever way the change would come.

Measured before this was written: the library's routes refuse every change a request made for work
that keeps nothing asks (``POST /api/artifacts`` answers 403 for it), but the agent's own artifact
tools asked nothing of the sort. In an Incognito chat ``artifact_save`` added an artifact to the
library, which outlives the chat; ``artifact_update`` and ``artifact_delete`` changed and removed an
artifact the owner had made elsewhere; ``document_create`` made a new file there. An artifact the
person mentioned in such a chat was stamped with a ``referenced`` event naming the chat, which the
library's route refuses to record for it, and a workflow run the chat started published its output
into the library.

The behaviour now, the same at both doors the agent's tools run behind (the gateway's own agent,
and the tool server an agent CLI runs):

* work that keeps nothing (an Incognito or Temporary chat's, the work it starts, work whose chat's
  memory setting cannot be read) changes nothing in the library: every tool that would save, change
  or remove an artifact is refused before anyone is asked, in words that say why, and nothing is
  written, an artifact the chat made itself included;
* an artifact the person mentions in such a chat is read for the turn and left as it was, and the
  agent is not invited to change it; a run the chat started publishes nothing, and says why;
* an ordinary chat's tools save and change as before, and its mention is recorded on the artifact.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw import mcp_artifacts, mcp_core, memory_writes
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolFailure

KEY = "dashboard:chat-orchard-1700000000"
ELSEWHERE = "dashboard:chat-pantry-1700000001"
#: The code a refused call carries, as the library's routes and the workflow rule name it.
CODE = "restricted_session"


@pytest.fixture
def library(tmp_path):
    """The artifact store the tools reach, rooted in this test's folder."""
    store = NativeArtifactProvider(root=tmp_path / "artifacts")
    with patch("personalclaw.artifacts.registry.get_provider", return_value=store):
        yield store


class _as_the_chat:
    """A tool call made for the chat *KEY* in *mode*, as the gateway's own agent makes one: the
    chat's session bound for the call (``mcp_core.set_current_session_key``) inside the chat's own
    work (``memory_writes.derived_from``)."""

    def __init__(self, mode: str) -> None:
        self._scope = memory_writes.derived_from(KEY, memory_mode=mode)

    def __enter__(self):
        self._token = mcp_core.set_current_session_key(KEY)
        self._scope.__enter__()
        return self

    def __exit__(self, *exc):
        self._scope.__exit__(*exc)
        mcp_core.reset_current_session_key(self._token)
        return False


def _made_by(library: NativeArtifactProvider, session: str, name: str) -> str:
    """An artifact the agent of the chat *session* saved there, by its slug."""
    return library.create(
        name=name, content="- pears\n", kind="markdown", actor="agent", session_id=session
    ).slug


def _refusal(out: str) -> None:
    assert isinstance(out, ToolFailure), out
    assert out.startswith(f"Error [{CODE}]:"), out
    assert "This session cannot change your artifact library" in out
    assert "it keeps nothing, as a" in out


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_a_private_chats_artifact_save_is_refused_and_saves_nothing(library, mode):
    """🔴 Red before: the artifact was saved, and outlived the chat in the library."""
    args = {"name": "Orchard plan", "content": "- pears\n", "kind": "markdown"}

    with _as_the_chat(mode):
        before = mcp_artifacts._preflight("artifact_save", args)
        out = mcp_artifacts._call_tool("artifact_save", args)

    _refusal(out)
    assert f"as {'an Incognito' if mode == 'incognito' else 'a Temporary'} chat does" in out
    assert "Nothing was saved." in out
    assert isinstance(before, ToolFailure) and before.reason == out.reason
    assert library.list() == []


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_a_private_chat_changes_no_artifact_not_even_one_it_made(library, mode):
    """🔴 Red before: the chat changed and removed artifacts in the library. Refused for every
    one, as the library's routes refuse it: one another chat made, and one this chat's agent made
    itself, which is the library's as soon as it is saved."""
    own = _made_by(library, KEY, "Orchard plan")
    theirs = _made_by(library, ELSEWHERE, "Pantry list")

    with _as_the_chat(mode):
        outs = [
            mcp_artifacts._call_tool("artifact_update", {"slug": slug, "content": "- plums\n"})
            for slug in (own, theirs)
        ]
        outs += [
            mcp_artifacts._call_tool("artifact_update", {"slug": own, "description": "Fruit"}),
            mcp_artifacts._call_tool("artifact_delete", {"slug": own}),
            mcp_artifacts._call_tool("artifact_delete", {"slug": theirs}),
            mcp_artifacts._call_tool(
                "document_create", {"name": "Plan", "slug": own, "markdown": "# Plan\n"}
            ),
        ]

    for out in outs:
        _refusal(out)
        assert "Nothing was changed." in out
    for slug in (own, theirs):
        art = library.get(slug)
        assert art is not None and art.version == 1
        assert art.content == "- pears\n" and art.description == ""


@pytest.mark.parametrize(
    "tool, args",
    [
        ("document_create", {"name": "Orchard plan", "markdown": "# Plan\n\n- pears\n"}),
        ("sheet_create", {"name": "Orchard rows", "csv": "tree,rows\npear,3\n"}),
        ("image_generate", {"prompt": "a pear tree in spring"}),
        ("video_generate", {"prompt": "a pear tree in the wind"}),
    ],
)
def test_a_private_chats_tools_that_make_a_new_artifact_are_refused(library, tool, args):
    """🔴 Red before: the document tools made a new file in the library. Refused before any model
    or writer runs, so nothing is made and nothing is sent."""
    with _as_the_chat("incognito"):
        before = mcp_artifacts._preflight(tool, args)
        out = mcp_artifacts._call_tool(tool, args)

    _refusal(out)
    assert isinstance(before, ToolFailure) and before.reason == out.reason
    assert library.list() == []


def test_an_unreadable_chats_tools_change_nothing(library):
    """Work whose chat's memory setting cannot be read is held as a chat that keeps nothing, and
    says why in its own words."""
    own = _made_by(library, KEY, "Orchard plan")

    with _as_the_chat(memory_writes.UNREADABLE):
        out = mcp_artifacts._call_tool("artifact_update", {"slug": own, "content": "- plums\n"})
        saved = mcp_artifacts._call_tool("artifact_save", {"name": "Plums", "content": "- plums\n"})

    for said in (out, saved):
        assert isinstance(said, ToolFailure) and said.startswith(f"Error [{CODE}]:"), said
        assert "the memory setting of the chat it is for cannot be read" in said
    assert library.get(own).version == 1
    assert [a.slug for a in library.list()] == [own]


def test_an_ordinary_chats_artifact_tools_save_and_change_as_before(library):
    theirs = _made_by(library, ELSEWHERE, "Pantry list")

    with _as_the_chat("persistent"):
        saved = mcp_artifacts._call_tool(
            "artifact_save", {"name": "Orchard plan", "content": "- pears\n", "kind": "markdown"}
        )
        updated = mcp_artifacts._call_tool(
            "artifact_update", {"slug": theirs, "content": "- flour\n"}
        )

    assert "Saved artifact 'Orchard plan'" in saved
    assert "version 2" in updated
    assert {a.slug for a in library.list()} == {"orchard-plan", theirs}


# ── an artifact the person mentions, and a run the chat started ─────────────────────────────


class _Chat:
    """A chat whose latest message mentions the artifacts *slugs*, as the composer sends one."""

    def __init__(self, slugs: list[str]) -> None:
        self.key = KEY
        self.messages = [{"role": "user", "content": "look", "meta": {"artifacts": slugs}}]


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_an_artifact_mentioned_in_a_private_chat_is_read_and_left_as_it_was(library, mode):
    """🔴 Red before: the turn stamped the artifact with a ``referenced`` event naming the chat, a
    trace of it in the library the chat does not outlive, which the library's route refuses to
    record for it, and invited its agent to change the artifact."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    slug = _made_by(library, ELSEWHERE, "Pantry list")
    with _as_the_chat(mode):
        prompt = _inject_artifact_content(None, _Chat([slug]), "What is on it?")

    assert "- pears" in prompt and prompt.endswith("What is on it?")
    assert "This chat keeps nothing, so it changes none of them." in prompt
    assert "artifact_update" not in prompt
    assert [(e.type, e.session_id) for e in library.get(slug).events] == [("created", ELSEWHERE)]


def test_an_artifact_mentioned_in_an_ordinary_chat_records_where_it_was_used(library):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    slug = _made_by(library, ELSEWHERE, "Pantry list")
    with _as_the_chat("persistent"):
        prompt = _inject_artifact_content(None, _Chat([slug]), "What is on it?")

    assert "- pears" in prompt
    assert "call artifact_update on that same slug" in prompt
    assert library.get(slug).events[-1].type == "referenced"
    assert library.get(slug).events[-1].session_id == KEY


def _in_a_run(work: Coroutine[Any, Any, Any], mode: str | None) -> Any:
    """*work* done as a run started in a chat under *mode* does it, under the mode the run
    inherited (``workflows.run_start.run_context``), or as any other run's when *mode* is
    ``None``."""
    if mode is None:
        return asyncio.run(work)
    from personalclaw.workflows import ownership

    context = memory_writes.work_context(ownership.owned_key("wf-orchard", "run"), memory_mode=mode)
    return context.run(asyncio.run, work)


def _publish(mode: str | None) -> Any:
    """A stage of a run, which declares its output a report for the library, finished in a run
    started under *mode* (:func:`_in_a_run`)."""
    from personalclaw.workflows.engine import NodeResult
    from personalclaw.workflows.models import InstanceState, Node
    from personalclaw.workflows.publish_seam import apply_publish

    node = Node.from_dict(
        {"kind": "stage", "id": "s", "config": {"prompt": "x", "publish": "Orchard report"}}
    )
    done = NodeResult(state=InstanceState.DONE, output="# Orchard report\n\n- pears\n")
    return _in_a_run(apply_publish(node, done), mode)


@pytest.mark.parametrize("mode", ["incognito", "temporary", memory_writes.UNREADABLE])
def test_a_run_a_private_chat_started_publishes_nothing_and_says_why(library, mode):
    """🔴 Red before: the run's report was published into the library, which outlives the chat
    that started the run. Now nothing is published, the stage's result says why, and its output is
    the run's as before."""
    result = _publish(mode)

    assert result.published["action"] == "noop", result.published
    assert result.published["reason"].endswith(", so nothing was published to your library")
    assert result.output == "# Orchard report\n\n- pears\n"
    assert library.list() == []


def test_any_other_run_publishes_as_before(library):
    result = _publish(None)

    assert result.published["action"] == "create", result.published
    assert [a.name for a in library.list()] == ["Orchard report"]


#: A report a run's ``render-report`` step renders, as its template declares one.
_BRIEF = {"title": "Orchard brief", "blocks": [{"type": "markdown", "text": "## Pears\n\n- rows"}]}


def _steps(mode: str | None) -> list[Any]:
    """The zero-token steps of a run that write the library, ``artifact-update`` and
    ``render-report``, then a ``render-report`` that only renders, run under *mode*."""
    from personalclaw.action_providers.artifact_update_provider import (
        ArtifactUpdateActionProvider,
    )
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.knowledge_render_provider import (
        KnowledgeRenderReportActionProvider,
    )

    ctx = ActionContext(event="workflow_node")
    board = {"slug": "orchard-board", "content": "<p>Three rows of pears.</p>"}
    return [
        _in_a_run(ArtifactUpdateActionProvider().execute(board, ctx), mode),
        _in_a_run(
            KnowledgeRenderReportActionProvider().execute(
                {"slug": "orchard-brief", "spec": _BRIEF}, ctx
            ),
            mode,
        ),
        _in_a_run(
            KnowledgeRenderReportActionProvider().execute(
                {"spec": _BRIEF, "render_only": True}, ctx
            ),
            mode,
        ),
    ]


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_a_run_a_private_chat_started_writes_nothing_to_the_library_from_its_steps(library, mode):
    """🔴 Red before: the run's ``artifact-update`` and ``render-report`` steps wrote their
    artifacts into the library. Each now fails saying why, and a render that writes nothing still
    renders."""
    updated, rendered, shown = _steps(mode)

    for result, step in ((updated, "artifact-update"), (rendered, "render-report")):
        assert not result.success
        assert result.error.startswith(f"{step}: it keeps nothing, as "), result.error
        assert result.error.endswith(", so nothing was written to your library")
    assert shown.success, shown.error
    assert "Pears" in json.loads(shown.stdout)["html"]
    assert library.list() == []


def test_any_other_runs_steps_write_the_library_as_before(library):
    updated, rendered, shown = _steps(None)

    assert updated.success and rendered.success and shown.success, (updated, rendered)
    assert sorted(a.slug for a in library.list()) == [
        "orchard-board",
        "orchard-brief",
        "orchard-brief-report",
    ]


# ── the gateway's own agent, driven ─────────────────────────────────────────────────────────


class _Model:
    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


def _call(cid: str, tool: str, args: dict[str, Any]) -> list[AgentEvent]:
    return [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=cid, title=tool, tool_input=json.dumps(args)),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


@pytest.mark.asyncio
async def test_a_private_chats_save_is_refused_before_anyone_is_asked(library, tmp_path):
    """The agent's call is answered with why before an approval card could offer it: approving it
    could save nothing."""
    memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
    done = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model(
            [
                _call(
                    "s1",
                    "artifact_save",
                    {"name": "Orchard plan", "content": "- pears\n", "kind": "markdown"},
                ),
                done,
            ]
        ),
        tool_providers=[
            InProcessMcpToolProvider(
                module="personalclaw.mcp_artifacts", provider_name="artifacts", display="Artifacts"
            )
        ],
        cwd=tmp_path,
        session_key=KEY,
    )
    seen: list[AgentEvent] = []
    with memory_writes.derived_from(KEY, memory_mode="temporary"):
        await rt.start()

        async def pump() -> None:
            async for ev in rt.stream("save the plan"):
                seen.append(ev)
                if ev.kind == EVENT_PERMISSION_REQUEST:
                    await rt.approve_tool(ev.request_id)

        await asyncio.wait_for(pump(), timeout=20)

    assert [e.title for e in seen if e.kind == EVENT_PERMISSION_REQUEST] == []
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT and e.tool_call_id == "s1")
    assert "This session cannot change your artifact library" in str(result.tool_output)
    assert library.list() == []


# ── the tool server an agent CLI runs ───────────────────────────────────────────────────────


def test_an_agent_clis_artifact_save_for_a_private_chat_is_refused(library, monkeypatch):
    """The tool server asks the gateway what the chat it serves is, and runs the call as that
    chat's work: the same refusal, though no gateway state is in this process."""
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", KEY)
    monkeypatch.setattr(mcp_core, "_SESSION_MODES", {})
    asked: list[str] = []

    def gateway(path: str, **_kw) -> dict:
        asked.append(path)
        return {"memory_mode": "temporary"}

    monkeypatch.setattr(mcp_core, "_get", gateway)

    out = mcp_core._call_as_its_session(
        "artifact_save", {"name": "Orchard plan", "content": "- pears\n", "kind": "markdown"}
    )

    assert asked == ["/api/chat/sessions/model-reach"]
    _refusal(out)
    assert library.list() == []


def test_the_routes_and_the_tools_ask_one_check(library):
    """The library's routes refuse such work by the same answer the tools ask
    (``memory_reads.keeps_nothing``), so the two doors cannot drift apart."""
    from types import SimpleNamespace

    from personalclaw.dashboard.handlers._shared import _is_restricted_session

    request = SimpleNamespace(headers={"X-Session-Key": KEY})
    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        assert _is_restricted_session(None, request) is True
    with memory_writes.derived_from(KEY, memory_mode="persistent"):
        assert _is_restricted_session(None, request) is False
    with patch("personalclaw.memory_reads.keeps_nothing", return_value="temporary") as asked:
        assert _is_restricted_session(None, request) is True
        assert asked.call_count == 1
        out = mcp_artifacts._call_tool("artifact_save", {"name": "x", "content": "y"})
        assert asked.call_count >= 2
    _refusal(out)
    assert library.list() == []
