"""The file tools reach the folders the owner shared: her knowledge sources, read only, and her
allowed working directories, read and change.

Asked "What's on my plate today?", the agent needed five shell approvals and 44 seconds to read
her notes. They sit in folders she added as knowledge sources, but the file tools reached only
the session's own folder ("path … escapes the workspace root"), so every read became a shell
command, and every shell command waited for her. Her Settings › Agent defaults › Allowed working
directories named the same folders and governed only where a subagent may start, so a change
there became a raw shell command too.

Now there is one scope (``file_scope``): reads in the workspace, the allowed working directories
and the knowledge sources' folders (only what a source itself takes in); changes in the
workspace and the allowed working directories; everything else refused, by a check a caller can
make before any approval is asked for. The model is told where each turn.
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

_NOTE = "School pickup at 15:30"


def _allow(pc_home: Path, folders: list[str]) -> None:
    (pc_home / "config.json").write_text(
        json.dumps({"agent": {"subagent_cwd_allowed_roots": folders}}), encoding="utf-8"
    )


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A PersonalClaw home with its workspace, and the owner's own home beside it: a notes folder
    she added as a knowledge source, a code folder she allowed, and a folder she shared with
    nobody."""
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "pc-home"
    workspace = pc_home / "workspace"
    workspace.mkdir(parents=True)
    user = root / "user"
    daily = user / "Notes" / "Daily"
    (daily / ".drafts").mkdir(parents=True)
    (daily / "2026-09-30.md").write_text(f"# Wed 30 Sep\n- {_NOTE}\n", encoding="utf-8")
    (daily / "scan.txt").write_text("a file this folder does not share\n", encoding="utf-8")
    (daily / ".drafts" / "half.md").write_text("an unfinished draft\n", encoding="utf-8")
    private = user / "Private"
    private.mkdir()
    (private / "diary.md").write_text("not for the agent\n", encoding="utf-8")
    code = user / "src" / "app"
    code.mkdir(parents=True)
    (code / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    _allow(pc_home, ["~/src"])

    from personalclaw.knowledge import get_knowledge_store

    store = get_knowledge_store()
    source = store.create_source(
        name="Notes: Daily",
        provider="watched-dir",
        kind="dir",
        spec={"path": "~/Notes/Daily", "include": ["*.md"]},
        item_type="note",
    )
    return SimpleNamespace(
        pc=pc_home,
        ws=workspace,
        user=user,
        daily=daily,
        private=private,
        code=code,
        store=store,
        source=source,
    )


def _tools(cwd: Path) -> NativeBuiltinToolProvider:
    """The platform provider as the gateway builds it for a session."""
    return NativeBuiltinToolProvider(cwd=cwd, categories=PLATFORM_CATEGORIES)


def _call(home, name: str, **arguments):
    return asyncio.run(_tools(home.ws).invoke(name, arguments))


# ── a knowledge source: read, with no approval ───────────────────────────────────────────────


def test_a_note_in_a_knowledge_source_is_read(home):
    """🔴 Before: "path '~/Notes/Daily/2026-09-30.md' escapes the workspace root"."""
    for path in ("~/Notes/Daily/2026-09-30.md", str(home.daily / "2026-09-30.md")):
        result = _call(home, "read_file", path=path)
        assert result.success, (path, result.error)
        assert _NOTE in result.output
        assert refusal("read_file", {"path": path}, cwd=home.ws) == ""


def test_the_folder_lists_and_searches_as_the_source_shares_it(home):
    listing = _call(home, "list_dir", path="~/Notes/Daily")
    assert listing.success, listing.error
    assert "2026-09-30.md" in listing.output
    assert "scan.txt" not in listing.output and ".drafts" not in listing.output

    found = _call(home, "grep", query="15:30", path="~/Notes/Daily")
    assert found.success, found.error
    named = str(home.daily / "2026-09-30.md")
    assert f"{named}:2:" in found.output, found.output
    assert _call(home, "read_file", path=named).success, "a hit names a file read_file opens"

    matched = _call(home, "glob", pattern="**/*", path="~/Notes/Daily")
    assert matched.success and matched.output.strip() == named, matched.output


def test_a_read_in_a_source_runs_without_an_approval(home):
    """Driven through the native loop with nothing pre-approved: the read runs, no permission
    request is raised, and the turn's note tells the model where the notes are."""
    from test_native_runtime import _defn, _drain, _ScriptedModel

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )

    call = json.dumps({"path": "~/Notes/Daily/2026-09-30.md"})
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_file", tool_input=call
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="pickup"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )

    async def _turn():
        runtime = NativeAgentRuntime(
            definition=_defn(), model_provider=model, tool_providers=[_tools(home.ws)], cwd=home.ws
        )
        await runtime.start()
        return await asyncio.wait_for(_drain(runtime, "What's on my plate today?"), timeout=10)

    events = asyncio.run(_turn())
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in events]
    result = next(e for e in events if e.kind == EVENT_TOOL_RESULT)
    assert _NOTE in str(result.tool_output)
    notes = [m["content"] for m in model.seen_messages[0] if m.get("role") == "system"]
    assert any("[file places]" in n and "~/Notes/Daily" in n for n in notes), notes


# ── what stays refused ───────────────────────────────────────────────────────────────────────


def test_a_read_outside_every_place_is_refused(home):
    for name, arguments in (
        ("read_file", {"path": "~/Private/diary.md"}),
        ("list_dir", {"path": "~/Private"}),
        ("grep", {"query": "agent", "path": "~/Private"}),
        ("glob", {"pattern": "*", "path": str(home.private)}),
    ):
        result = _call(home, name, **arguments)
        assert not result.success, name
        assert "not for the agent" not in (result.output or "")
        assert "outside every folder the file tools reach" in result.error
        assert refusal(name, arguments, cwd=home.ws) == result.error


def test_a_climb_or_a_link_out_of_a_source_is_refused(home):
    (home.daily / "linked.md").symlink_to(home.private / "diary.md")
    (home.daily / "elsewhere").symlink_to(home.private, target_is_directory=True)
    for path in (
        "~/Notes/Daily/../../Private/diary.md",
        "~/Notes/Daily/linked.md",
        "~/Notes/Daily/elsewhere/diary.md",
    ):
        result = _call(home, "read_file", path=path)
        assert not result.success, path
        assert "outside every folder the file tools reach" in result.error, result.error
        assert refusal("read_file", {"path": path}, cwd=home.ws), path

    listing = _call(home, "list_dir", path="~/Notes/Daily")
    assert "linked.md" not in listing.output and "elsewhere" not in listing.output
    searched = _call(home, "grep", query="agent", path="~/Notes/Daily")
    assert "not for the agent" not in searched.output
    assert _call(home, "glob", pattern="../*", path="~/Notes/Daily").success is False


def test_a_source_shares_only_the_files_it_takes_in(home):
    for path in ("~/Notes/Daily/scan.txt", "~/Notes/Daily/.drafts/half.md"):
        result = _call(home, "read_file", path=path)
        assert not result.success, path
        assert "which shares only its *.md files" in result.error


def test_a_source_is_never_changed(home):
    note = home.daily / "2026-09-30.md"
    before = note.read_text(encoding="utf-8")
    _call(home, "read_file", path=str(note))
    for name, arguments in (
        ("write_file", {"path": str(note), "content": "gone"}),
        ("edit_file", {"path": str(note), "old_str": "15:30", "new_str": "16:00"}),
        ("write_file", {"path": "~/Notes/Daily/new.md", "content": "new"}),
    ):
        result = _call(home, name, **arguments)
        assert not result.success, name
        assert "which the file tools read and never change" in result.error
        assert refusal(name, arguments, cwd=home.ws) == result.error
    assert note.read_text(encoding="utf-8") == before
    assert not (home.daily / "new.md").exists()


def test_a_source_she_pauses_or_points_elsewhere_stops_being_readable(home):
    path = "~/Notes/Daily/2026-09-30.md"
    assert _call(home, "read_file", path=path).success
    home.store.update_source(home.source, enabled=False)
    assert "outside every folder" in _call(home, "read_file", path=path).error
    home.store.update_source(home.source, enabled=True, spec={"path": "~/src", "include": ["*.md"]})
    assert "outside every folder" in _call(home, "read_file", path=path).error
    home.store.update_source(home.source, spec={"path": "~/Notes/Daily", "include": ["*.md"]})
    assert _call(home, "read_file", path=path).success


# ── an allowed working directory: read and change ─────────────────────────────────────────────


def test_an_allowed_working_directory_is_read_and_changed(home):
    """🔴 Before: the setting governed only where a subagent may start, so the file tools refused
    the folder and the agent wrote its change from the shell."""
    main = home.code / "main.py"
    assert "def main" in _call(home, "read_file", path="~/src/app/main.py").output
    edited = _call(home, "edit_file", path=str(main), old_str="return 1", new_str="return 2")
    assert edited.success, edited.error
    assert main.read_text(encoding="utf-8").endswith("return 2\n")
    written = _call(home, "write_file", path="~/src/app/util.py", content="X = 1\n")
    assert written.success, written.error
    assert (home.code / "util.py").read_text(encoding="utf-8") == "X = 1\n"
    assert refusal("write_file", {"path": "~/src/app/util.py"}, cwd=home.ws) == ""


def test_a_rewind_restores_a_change_in_an_allowed_folder(home):
    """🔴 Without the folder recorded, the rewind refused it: "outside the session's workspace
    roots"."""
    from personalclaw import turn_checkpoints

    key = "dashboard:rewind-allowed"
    main = home.code / "main.py"
    original = main.read_text(encoding="utf-8")
    assert turn_checkpoints.begin_turn(key, cwd=home.ws) == 1
    assert turn_checkpoints.begin_turn(key, cwd=home.ws) == 2
    tools = NativeBuiltinToolProvider(cwd=home.ws, session_key=key, categories=PLATFORM_CATEGORIES)
    assert asyncio.run(tools.invoke("read_file", {"path": str(main)})).success
    changed = asyncio.run(
        tools.invoke("edit_file", {"path": str(main), "old_str": "return 1", "new_str": "return 9"})
    )
    assert changed.success, changed.error
    result = turn_checkpoints.apply_rewind(key, 1)
    assert result.ok, result.errors
    assert main.read_text(encoding="utf-8") == original


def test_the_change_tools_still_ask_first(home):
    defs = {d.name: d for d in asyncio.run(_tools(home.ws).list_tools())}
    assert defs["write_file"].requires_approval and defs["edit_file"].requires_approval
    for name in ("read_file", "list_dir", "glob", "grep"):
        assert not defs[name].requires_approval, name


def test_a_change_outside_the_workspace_and_allowed_folders_is_refused(home):
    for path in ("~/Private/diary.md", "~/src/../Private/diary.md"):
        arguments = {"path": path, "content": "x"}
        result = _call(home, "write_file", **arguments)
        assert not result.success, path
        assert "outside every folder the file tools may change" in result.error
        assert refusal("write_file", arguments, cwd=home.ws) == result.error
    assert (home.private / "diary.md").read_text(encoding="utf-8") == "not for the agent\n"


def test_a_link_out_of_an_allowed_folder_is_refused(home):
    (home.code / "diary.md").symlink_to(home.private / "diary.md")
    result = _call(home, "write_file", path="~/src/app/diary.md", content="overwritten")
    assert not result.success
    assert (home.private / "diary.md").read_text(encoding="utf-8") == "not for the agent\n"


def test_removing_an_allowed_folder_revokes_it(home):
    assert _call(home, "read_file", path="~/src/app/main.py").success
    _allow(home.pc, [])
    assert "outside every folder" in _call(home, "read_file", path="~/src/app/main.py").error
    assert not _call(home, "write_file", path="~/src/app/x.py", content="x").success


def test_a_system_folder_named_as_allowed_is_no_place(home):
    _allow(home.pc, ["/"])
    assert [p.kind for p in FileScope([home.ws]).places] == ["workspace", "source"]


# ── what the model is told ───────────────────────────────────────────────────────────────────


def test_the_turn_note_names_each_place_and_what_it_allows(home):
    note = places_note(FileScope([home.ws]), knowledge=True)
    assert "~/src: an allowed working directory" in note
    assert "~/Notes/Daily: the knowledge source 'Notes: Daily', read only: its *.md files" in note
    assert note.index("call knowledge_search first") < note.index("read_file, list_dir")
    assert "knowledge_get returns a note's full text and its path" in note
    _allow(home.pc, [])
    home.store.update_source(home.source, enabled=False)
    assert places_note(FileScope([home.ws])) == "", "nothing beyond the workspace, no note"


def test_the_tool_descriptions_say_where_they_reach(home):
    defs = {d.name: d.description for d in asyncio.run(_tools(home.ws).list_tools())}
    for name in ("read_file", "list_dir", "glob", "grep"):
        assert "knowledge-source folder" in defs[name] or "knowledge source" in defs[name], name
        assert "allowed working director" in defs[name], name
    for name in ("write_file", "edit_file"):
        assert "allowed working directories" in defs[name], name


def test_knowledge_get_names_the_notes_file(home):
    item = home.store.create_typed_item(
        item_type="note",
        title="2026-09-30.md",
        content=f"# Wed 30 Sep\n- {_NOTE}\n",
        source_id=home.source,
        guid="2026-09-30.md",
    )
    tools = NativeBuiltinToolProvider(cwd=home.ws)
    shown = asyncio.run(tools.invoke("knowledge_get", {"id": item}))
    assert shown.success, shown.error
    assert "path: ~/Notes/Daily/2026-09-30.md" in shown.output
    assert _NOTE in shown.output


# ── PersonalClaw's own stores: not files to the agent ────────────────────────────────────────


def test_the_librarys_own_database_is_not_a_file_to_the_tools(home):
    """🔴 Before: `grep xmlUrl` in the workspace printed rows of `knowledge/knowledge.db`, the
    library's own database, past the masking the knowledge tools apply."""
    database = home.ws / "knowledge" / "knowledge.db"
    assert database.is_file(), "the library keeps its database in the workspace"
    home.store.create_typed_item(item_type="note", title="OPML", content="empty xmlUrl crash")
    found = _call(home, "grep", query="xmlUrl")
    assert found.success and "knowledge.db" not in found.output, found.output
    assert "knowledge.db" not in _call(home, "list_dir", path="knowledge").output
    assert "knowledge.db" not in _call(home, "glob", pattern="**/*.db").output
    for name, arguments in (
        ("read_file", {"path": "knowledge/knowledge.db"}),
        ("read_file", {"path": str(database) + "-wal"}),
        ("write_file", {"path": "knowledge/knowledge.db", "content": "x"}),
    ):
        result = _call(home, name, **arguments)
        assert not result.success, name
        assert "PersonalClaw's own knowledge store" in result.error
        assert "knowledge_search" in result.recovery_hints[0]
        assert refusal(name, arguments, cwd=home.ws) == result.error
    assert database.stat().st_size > 1


def test_a_shell_command_naming_the_librarys_database_is_refused_before_approval(home):
    from personalclaw.hooks import TOOL_DENY, HookManager

    screen = HookManager()
    database = home.ws / "knowledge" / "knowledge.db"
    for title in (
        "Running: cd knowledge 2>/dev/null; grep -a -n xmlUrl knowledge.db | head -60",
        f"Running: sqlite3 {database} 'select title from items'",
        f"Reading {database}",
    ):
        verdict = screen.on_tool_call(title, cwd=home.ws)
        assert verdict.action == TOOL_DENY, title
        assert "knowledge_search" in verdict.reason
    ordinary = screen.on_tool_call("Running: grep -n pickup notes.md", cwd=home.ws)
    assert ordinary.action != TOOL_DENY
    # And the shell tool itself, for a caller no screen stood in front of.
    ran = _call(home, "bash", command="cd knowledge; grep -a -n xmlUrl knowledge.db")
    assert not ran.success and "knowledge_search" in ran.error


def test_a_search_under_another_folder_is_ordered_against_a_change_there(home):
    """A search given a `path` reads that folder, so a change inside it in the same turn is not
    run beside it: their order stays the order the model asked for."""
    from personalclaw.agents.native.dispatch_plan import conflicts, reservations_for

    cwd = str(home.ws)
    change = reservations_for("write_file", {"path": "~/src/app/util.py"}, cwd=cwd)
    for name, arguments in (
        ("grep", {"query": "X", "path": "~/src"}),
        ("glob", {"pattern": "**/*.py", "path": "~/src/app"}),
    ):
        assert conflicts(reservations_for(name, arguments, cwd=cwd), change), name
    elsewhere = reservations_for("grep", {"query": "X", "path": "~/Notes/Daily"}, cwd=cwd)
    assert not conflicts(elsewhere, change)


# ── a workflow step: reads its run's folders beside its own ──────────────────────────────────


def _batch_run(home, *, project_id: str = "") -> str:
    """A run started as a batch is: its folder named in its inputs, as `subagent_run` names it."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(
        id=store.new_run_id(),
        workflow_name="subagent-batch-1",
        inputs={"cwd": str(home.repo)},
        project_id=project_id,
    )
    store.create(run)
    return run.id


@pytest.fixture()
def step(home):
    """A repo in the workspace, outside every allowed folder, and the empty folder a step of a
    batch started in it works in."""
    repo = home.ws / "feedsmith"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "digest.py").write_text("def digest():\n    return 'ok'\n", encoding="utf-8")
    scratch = home.pc / "scratch" / "leaf"
    scratch.mkdir(parents=True)
    home.repo, home.scratch = repo, scratch
    return home


def test_a_read_only_step_reads_the_repo_its_batch_started_in(step):
    """🔴 Before: the batch's folder reached no step, so its review leaves, working in an empty
    folder, had every read of the repo refused."""
    from personalclaw.workflows.ownership import OWNED_PREFIX
    from personalclaw.workflows.provisioning import step_reads

    reads = step_reads(f"{OWNED_PREFIX}{_batch_run(step)}")
    assert reads == [str(step.repo)]
    tools = NativeBuiltinToolProvider(
        cwd=step.scratch, read_roots=[Path(r) for r in reads], categories=PLATFORM_CATEGORIES
    )
    shown = asyncio.run(tools.invoke("read_file", {"path": str(step.repo / "src" / "digest.py")}))
    assert shown.success and "def digest" in shown.output, shown.error
    found = asyncio.run(tools.invoke("grep", {"query": "digest", "path": str(step.repo)}))
    assert f"{step.repo / 'src' / 'digest.py'}:1:" in found.output
    changed = asyncio.run(
        tools.invoke("write_file", {"path": str(step.repo / "src" / "digest.py"), "content": "x"})
    )
    assert not changed.success and "a folder this work reads and never changes" in changed.error
    assert "return 'ok'" in (step.repo / "src" / "digest.py").read_text(encoding="utf-8")


def test_a_step_reads_its_projects_bound_tree(step):
    from personalclaw.tasks.hierarchy import HierarchyStore
    from personalclaw.workflows.ownership import OWNED_PREFIX
    from personalclaw.workflows.provisioning import step_reads

    site = step.user / "Projects" / "site"
    site.mkdir(parents=True)
    project = HierarchyStore().create_project("Site", workspace_dir=str(site))
    reads = step_reads(f"{OWNED_PREFIX}{_batch_run(step, project_id=project.id)}")
    assert reads == [str(site), str(step.repo)]


def test_a_batch_folder_no_spawn_may_work_in_reaches_nothing(step):
    from personalclaw.workflows import store
    from personalclaw.workflows.ownership import OWNED_PREFIX
    from personalclaw.workflows.provisioning import step_reads

    run_id = _batch_run(step)
    run = store.get(run_id)
    run.inputs = {"cwd": str(step.private)}
    store.save(run)
    assert step_reads(f"{OWNED_PREFIX}{run_id}") == []
    assert step_reads("chat-1") == [], "a spawn that is no workflow step reads nothing more"


@pytest.mark.asyncio
async def test_a_steps_spawn_hands_its_reads_to_its_file_tools(step):
    from unittest.mock import AsyncMock, MagicMock, patch

    from test_subagent import _mock_ctx_builder_auto_spawn, _mock_sessions

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.subagent import SubagentInfo, SubagentManager
    from personalclaw.workflows.ownership import OWNED_PREFIX

    async def _nothing(*_a, **_k):
        return
        yield

    native = MagicMock(spec=NativeAgentRuntime)
    native.stream = MagicMock(side_effect=lambda *a, **kw: _nothing())
    native.context_usage_pct = lambda: 0.0
    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(return_value=(native, True, False))
    manager = SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder_auto_spawn())
    info = SubagentInfo(
        id="t2",
        task="Review the digest change.",
        approval_mode="auto",
        capability_class="research",
        parent_run=f"{OWNED_PREFIX}{_batch_run(step)}",
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:t2")
    assert sessions.get_or_create.call_args.kwargs["read_tool_roots"] == [str(step.repo)]


# ── a note a chat names opens where she reads the chat ────────────────────────────────────────


def _files_app():
    from unittest.mock import MagicMock

    from aiohttp import web

    from personalclaw.dashboard.handlers import api_file_read

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    return app


def _note_item(home, guid: str = "2026-09-30.md") -> str:
    item = home.store.create_typed_item(
        item_type="note",
        title=guid.rsplit("/", 1)[-1],
        content=f"# Wed 30 Sep\n- {_NOTE}\n",
        source_id=home.source,
        guid=guid,
    )
    assert item
    return item


@pytest.mark.asyncio
async def test_a_note_the_agent_cites_opens_from_the_chat(home):
    """🔴 Before: the chip named the note's file, `file-read` resolved it against the workspace,
    and the panel said "Couldn't open this file. not found"."""
    from aiohttp.test_utils import TestClient, TestServer

    _note_item(home)
    async with TestClient(TestServer(_files_app())) as client:
        for params in (
            {"path": "2026-09-30.md", "resolve": "1"},
            {"path": "~/Notes/Daily/2026-09-30.md", "resolve": "1"},
            {"path": str(home.daily / "2026-09-30.md")},
        ):
            resp = await client.get("/api/file-read", params=params)
            assert resp.status == 200, (params, await resp.text())
            assert _NOTE in await resp.text()


@pytest.mark.asyncio
async def test_what_the_agent_may_not_read_stays_closed_to_the_chat_too(home):
    from aiohttp.test_utils import TestClient, TestServer

    (home.daily / "linked.md").symlink_to(home.private / "diary.md")
    async with TestClient(TestServer(_files_app())) as client:
        for path in (
            "~/Private/diary.md",
            "~/Notes/Daily/scan.txt",
            "~/Notes/Daily/linked.md",
            "~/Notes/Daily/../../Private/diary.md",
        ):
            resp = await client.get("/api/file-read", params={"path": path, "resolve": "1"})
            assert resp.status in (400, 403, 404), (path, resp.status)
            assert "not for the agent" not in await resp.text()


@pytest.mark.asyncio
async def test_a_file_name_two_notes_share_names_neither(home):
    from aiohttp.test_utils import TestClient, TestServer

    (home.daily / "Talks").mkdir()
    (home.daily / "Talks" / "2026-09-30.md").write_text("another note\n", encoding="utf-8")
    _note_item(home)
    _note_item(home, guid="Talks/2026-09-30.md")
    async with TestClient(TestServer(_files_app())) as client:
        bare = await client.get("/api/file-read", params={"path": "2026-09-30.md", "resolve": "1"})
        assert bare.status != 200
        deeper = await client.get(
            "/api/file-read", params={"path": "Talks/2026-09-30.md", "resolve": "1"}
        )
        assert deeper.status == 200 and "another note" in await deeper.text()


def test_knowledge_search_names_the_notes_file_beside_its_id(home):
    item = _note_item(home)
    tools = NativeBuiltinToolProvider(cwd=home.ws)
    from unittest.mock import patch

    hits = [{"id": item}]
    with patch("personalclaw.knowledge.retrieval.HybridRetriever.search_with_diagnostics") as find:
        find.return_value.results = hits
        find.return_value.degradations = []
        found = asyncio.run(tools.invoke("knowledge_search", {"query": "pickup"}))
    assert found.success, found.error
    assert f"(id={item}) [~/Notes/Daily/2026-09-30.md]" in found.output
