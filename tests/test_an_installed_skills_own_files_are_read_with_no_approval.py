"""An installed skill's own files are read with no approval: the file tools reach each skill's
folder in the skills library, read only.

Asked for a postmortem, the agent loaded the skill that writes one, whose instructions say "Use
template.md for the structure", and ``read_file`` refused the template: the skills library was
outside every folder the file tools reach, so the read became a shell command that waited for the
owner's approval.

Now the library is a place the file tools read, and inside it only what each installed skill ships
in its own folder: links and ``..`` resolved first, nothing hidden (the library's own records, an
install's lock file). A skill's instructions come from ``skill_invoke`` alone, which applies its
accepted refinements and counts the use, so its ``SKILL.md`` is not a file to these tools. Nothing
in the library is changed through them: a write is refused before anyone is asked, and a shell
command that writes there asks first, as it always did.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.agents.native.builtin_tools import PLATFORM_CATEGORIES, NativeBuiltinToolProvider
from personalclaw.file_scope import FileScope, places_note, refusal

_NAME = "imported/claude_code/incident-writeup"
_FOLDER = f"~/.personalclaw/skills/{_NAME}"
_TEMPLATE = "## Customer impact\n\n## Timeline (UTC)\n"
_SKILL = (
    "---\nname: incident-writeup\ndescription: Turn incident notes into a blameless postmortem.\n"
    "---\n\n# Incident write-up\n\nUse `template.md` for the structure.\n"
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """The owner's home, PersonalClaw's home inside it: a skill she imported from another tool,
    with the template its instructions name and the record of its install; the library's own
    record beside it; a folder of hers she shared with nobody; and a skill folder elsewhere on
    her disk."""
    root = Path(os.path.realpath(tmp_path))
    user = root / "user"
    pc_home = user / ".personalclaw"
    workspace = pc_home / "workspace"
    workspace.mkdir(parents=True)
    library = pc_home / "skills"
    skill = library / "imported" / "claude_code" / "incident-writeup"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (skill / "template.md").write_text(_TEMPLATE, encoding="utf-8")
    (skill / ".pclaw-lock.json").write_text('{"id": "incident-writeup"}', encoding="utf-8")
    (library / ".usage.json").write_text("{}", encoding="utf-8")
    (pc_home / "config.json").write_text("{}", encoding="utf-8")
    private = user / "Private"
    private.mkdir()
    (private / "diary.md").write_text("not for the agent\n", encoding="utf-8")
    elsewhere = user / "Downloads" / "release-notes"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text(_SKILL.replace("incident-writeup", "release-notes"))
    (elsewhere / "draft.md").write_text("not for the agent either\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    return SimpleNamespace(
        user=user,
        pc=pc_home,
        ws=workspace,
        library=library,
        skill=skill,
        private=private,
        elsewhere=elsewhere,
    )


def _tools(cwd: Path) -> NativeBuiltinToolProvider:
    """The platform provider as the gateway builds it for a session."""
    return NativeBuiltinToolProvider(cwd=cwd, categories=PLATFORM_CATEGORIES)


def _call(home, name: str, *, cwd: Path | None = None, **arguments):
    return asyncio.run(_tools(cwd or home.ws).invoke(name, arguments))


def _said(result) -> str:
    """Everything a refused call tells the model: its sentence and its hints."""
    return " ".join([result.error or "", *(result.recovery_hints or [])])


# ── a skill's own files: read, with no approval ──────────────────────────────────────────────


def test_the_template_a_skill_names_is_read(home):
    """🔴 Before: "path '~/.personalclaw/skills/…/template.md' is outside every folder the file
    tools reach", and the read became a shell command."""
    for path in (f"{_FOLDER}/template.md", str(home.skill / "template.md")):
        result = _call(home, "read_file", path=path)
        assert result.success, (path, result.error)
        assert "## Customer impact" in result.output
        assert refusal("read_file", {"path": path}, cwd=home.ws) is None


def test_a_skills_folder_lists_and_searches_what_it_ships(home):
    listing = _call(home, "list_dir", path=_FOLDER)
    assert listing.success, listing.error
    assert listing.output.split() == ["template.md"], "not its instructions, not the install record"

    found = _call(home, "grep", query="Customer impact", path=_FOLDER)
    assert found.success, found.error
    assert f"{_FOLDER}/template.md:1:" in found.output, found.output
    assert _call(home, "read_file", path=f"{_FOLDER}/template.md").success

    matched = _call(home, "glob", pattern="**/*", path=_FOLDER)
    assert matched.success and matched.output.strip() == f"{_FOLDER}/template.md", matched.output


def test_the_skill_and_its_template_load_with_no_approval(home):
    """The owner's turn, driven through the native loop with nothing pre-approved: the skill
    loads and names its folder, the template its instructions name is read from there, and
    nobody is asked."""
    from test_native_runtime import _defn, _drain, _ScriptedModel

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    def _call_event(call_id: str, tool: str, arguments: dict) -> list[AgentEvent]:
        return [
            AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=call_id,
                title=tool,
                tool_input=json.dumps(arguments),
            ),
            AgentEvent(kind=EVENT_COMPLETE),
        ]

    model = _ScriptedModel(
        [
            _call_event("c1", "skill_invoke", {"name": _NAME}),
            _call_event("c2", "read_file", {"path": f"{_FOLDER}/template.md"}),
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="draft"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )

    async def _turn():
        runtime = NativeAgentRuntime(
            definition=_defn(),
            model_provider=model,
            tool_providers=[InProcessMcpToolProvider(), _tools(home.ws)],
            cwd=home.ws,
        )
        await runtime.start()
        return await asyncio.wait_for(
            _drain(runtime, "Write a blameless postmortem from my notes."), timeout=30
        )

    events = asyncio.run(_turn())
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in events]
    results = {e.tool_call_id: str(e.tool_output) for e in events if e.kind == EVENT_TOOL_RESULT}
    assert "Use `template.md` for the structure." in results["c1"]
    assert f"[This skill's folder: {_FOLDER}." in results["c1"], results["c1"]
    assert "## Customer impact" in results["c2"], results["c2"]


# ── what stays out of reach ──────────────────────────────────────────────────────────────────


def test_a_climb_or_a_link_out_of_a_skills_folder_is_refused(home):
    (home.skill / "notes.md").symlink_to(home.private / "diary.md")
    (home.skill / "settings.json").symlink_to(home.pc / "config.json")
    (home.library / "release-notes").symlink_to(home.elsewhere, target_is_directory=True)
    # The floor: the folder is readable and each target is on disk, so a refusal below is the
    # check working, not a path that reaches nothing.
    assert _call(home, "read_file", path=f"{_FOLDER}/template.md").success
    assert (home.skill / "notes.md").read_text(encoding="utf-8") == "not for the agent\n"
    assert (home.library / "release-notes" / "draft.md").is_file()
    for path in (
        f"{_FOLDER}/../../../../config.json",
        f"{_FOLDER}/../../../../../Private/diary.md",
        f"{_FOLDER}/notes.md",
        f"{_FOLDER}/settings.json",
        "~/.personalclaw/skills/release-notes/draft.md",
    ):
        result = _call(home, "read_file", path=path)
        assert not result.success, path
        assert "not for the agent" not in (result.output or ""), path
        assert refusal("read_file", {"path": path}, cwd=home.ws) is not None, path

    listing = _call(home, "list_dir", path=_FOLDER)
    assert "notes.md" not in listing.output and "settings.json" not in listing.output
    searched = _call(home, "grep", query="agent", path=_FOLDER)
    assert "not for the agent" not in searched.output, searched.output
    assert not _call(home, "list_dir", path="~/.personalclaw/skills/release-notes").success


def test_a_skills_instructions_are_skill_invokes_to_load(home):
    """The one place a skill's body is read applies its accepted refinements and counts the use,
    so the file tools point there instead of reading SKILL.md themselves."""
    for path in (f"{_FOLDER}/SKILL.md", f"{_FOLDER}/skill.md"):
        result = _call(home, "read_file", path=path)
        assert not result.success, path
        assert "Incident write-up" not in (result.output or "")
        assert f"skill_invoke(name='{_NAME}')" in _said(result), _said(result)
        assert str(refusal("read_file", {"path": path}, cwd=home.ws)) == result.error


def test_the_librarys_own_records_are_no_skills_files(home):
    for name, arguments in (
        ("read_file", {"path": "~/.personalclaw/skills/.usage.json"}),
        ("read_file", {"path": f"{_FOLDER}/.pclaw-lock.json"}),
        ("list_dir", {"path": "~/.personalclaw/skills"}),
        ("list_dir", {"path": "~/.personalclaw/skills/imported"}),
    ):
        result = _call(home, name, **arguments)
        assert not result.success, arguments
        assert "where the file tools reach only the files each installed skill ships" in (
            result.error
        ), result.error
        assert str(refusal(name, arguments, cwd=home.ws)) == result.error


def test_a_session_in_a_folder_holding_the_home_reaches_no_more_of_it(home):
    """A worker in ``~`` works in a folder that contains the home, and never reaches into the
    home through it: inside the home only the skills' own files are added, nothing beside them."""
    for path, readable in (
        (f"{_FOLDER}/template.md", True),
        (f"{_FOLDER}/SKILL.md", False),
        (f"{_FOLDER}/.pclaw-lock.json", False),
        ("~/.personalclaw/skills/.usage.json", False),
        ("~/.personalclaw/config.json", False),
    ):
        result = _call(home, "read_file", cwd=home.user, path=path)
        assert result.success is readable, (path, result.error)
    listing = _call(home, "list_dir", cwd=home.user, path=_FOLDER)
    assert listing.output.split() == ["template.md"], listing.output


# ── a write still goes through the owner ─────────────────────────────────────────────────────


def test_a_skills_files_are_never_changed_by_the_file_tools(home):
    template = home.skill / "template.md"
    for name, arguments in (
        ("write_file", {"path": f"{_FOLDER}/template.md", "content": "gone"}),
        ("edit_file", {"path": f"{_FOLDER}/template.md", "old_str": "Customer", "new_str": "X"}),
        ("write_file", {"path": f"{_FOLDER}/new.md", "content": "new"}),
    ):
        result = _call(home, name, **arguments)
        assert not result.success, name
        assert "which the file tools read and never change" in result.error, result.error
        assert str(refusal(name, arguments, cwd=home.ws)) == result.error
    assert template.read_text(encoding="utf-8") == _TEMPLATE
    assert not (home.skill / "new.md").exists()


def test_a_write_to_a_skill_still_asks_first(home):
    """Through the native loop: write_file is refused before anyone is asked, and the shell
    command that writes the same file waits for the owner, who says no; nothing changes."""
    from test_native_runtime import _defn, _ScriptedModel

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    template = home.skill / "template.md"

    def _call_event(call_id: str, tool: str, arguments: dict) -> list[AgentEvent]:
        return [
            AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=call_id,
                title=tool,
                tool_input=json.dumps(arguments),
            ),
            AgentEvent(kind=EVENT_COMPLETE),
        ]

    model = _ScriptedModel(
        [
            _call_event("c1", "write_file", {"path": f"{_FOLDER}/template.md", "content": "x"}),
            _call_event("c2", "bash", {"command": f"printf x > '{template}'"}),
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    asked: list[str] = []

    async def _turn():
        runtime = NativeAgentRuntime(
            definition=_defn(), model_provider=model, tool_providers=[_tools(home.ws)], cwd=home.ws
        )
        await runtime.start()
        seen = []
        async for event in runtime.stream("Tighten the incident template."):
            seen.append(event)
            if event.kind == EVENT_PERMISSION_REQUEST:
                asked.append(event.title)
                await runtime.reject_tool(event.request_id)
        return seen

    events = asyncio.run(asyncio.wait_for(_turn(), timeout=30))
    assert asked == ["bash"], "the shell asks; the file tools refuse before asking"
    results = {e.tool_call_id: str(e.tool_output) for e in events if e.kind == EVENT_TOOL_RESULT}
    assert "which the file tools read and never change" in results["c1"], results["c1"]
    assert template.read_text(encoding="utf-8") == _TEMPLATE


# ── what the model is told ───────────────────────────────────────────────────────────────────


def test_skill_invoke_names_the_folder_a_skills_files_are_in(home):
    from personalclaw.mcp_core import _call_tool_inner

    out = _call_tool_inner("skill_invoke", {"name": _NAME})
    assert f"[This skill's folder: {_FOLDER}. The files it names are there.]" in out, out
    assert out.endswith("[End of skill]")

    single = home.library / "plain-steps"
    single.mkdir()
    (single / "SKILL.md").write_text(
        "---\nname: plain-steps\ndescription: d\n---\n\nStep 1.\n", encoding="utf-8"
    )
    (single / ".pclaw-lock.json").write_text("{}", encoding="utf-8")
    plain = _call_tool_inner("skill_invoke", {"name": "plain-steps"})
    assert "This skill's folder" not in plain, "a skill that is its instructions alone names none"


def test_the_tools_say_they_reach_a_skills_folder_and_the_turn_note_leaves_it_out(home):
    defs = {d.name: d.description for d in asyncio.run(_tools(home.ws).list_tools())}
    for name in ("read_file", "list_dir", "glob", "grep"):
        assert "installed skill" in defs[name], name
    assert "skill folders" in defs["bash"], "the shell points its reads to the file tools"
    refused = _call(home, "read_file", path="~/Private/diary.md")
    assert "each installed skill's own folder" in refused.error, refused.error
    # The library is in every home: naming it in every turn's note would cost every turn.
    assert places_note(FileScope([home.ws])) == ""


@pytest.mark.asyncio
async def test_a_skills_file_the_agent_names_opens_where_she_reads_the_chat(home):
    from unittest.mock import MagicMock

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import api_file_read

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    async with TestClient(TestServer(app)) as client:
        opened = await client.get("/api/file-read", params={"path": f"{_FOLDER}/template.md"})
        assert opened.status == 200, await opened.text()
        assert "## Customer impact" in await opened.text()
        record = await client.get("/api/file-read", params={"path": f"{_FOLDER}/.pclaw-lock.json"})
        assert record.status in (400, 403, 404), record.status
