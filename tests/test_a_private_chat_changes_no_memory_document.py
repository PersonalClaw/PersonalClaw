"""An Incognito or Temporary chat changes no memory document, whichever tool it reaches for.

Measured on a scratch gateway before this was written: in an Incognito chat the agent read
``memory/preferences.md`` with ``read_file`` and wrote it back with ``write_file``; the owner
approved the card, and the Memory page then showed the chat's words as one of the owner's
preferences. ``edit_file`` changed projects.md the same way, ``write_file`` made a day of the daily
history, and a shell redirect appended to preferences.md from inside the sandbox. The security log
recorded each call as approved, and nothing refused any of them. The memory documents are files in
the folder every chat starts in (``<home>/workspace``), and the file tools and the shell wrote them
directly, past ``MemoryStore._persist``, the one write of a memory file that refuses such a chat.

The behaviour now:

* the file tools refuse to change anything in the memory folders (the home's ``memory`` folder and
  every working folder's memory, in ``_ext``) in work that may change none of your memory: an
  Incognito or Temporary chat's, an app's not given your memory. The refusal carries the
  memory-write refusal's sentence and code, comes before anyone is asked to approve the call, and
  holds again in the tool when the call runs unasked;
* the shell refuses a command that names a path there and does more than read it, before anyone is
  asked, and the sandbox keeps the memory folders read-only to every command started for such
  work, whatever the command says;
* an ordinary chat's write of a memory document goes through the document's store
  (``MemoryStore._persist``): under the documents' lock and indexed, so keyword search finds it at
  once. So does the owner's save in the Files editor;
* an Incognito chat still reads its memory, and still writes everywhere else.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.config.loader as loader
from personalclaw import memory_writes
from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    TOOL_META_NOT_RUN,
    AgentEvent,
    unasked_outcome,
    unasked_reason,
)
from personalclaw.memory import MemoryStore

KEY = "dashboard:chat-garden-1700000000"
#: A working folder's memory partition, as ``config.loader.memory_dir_for_cwd`` names one.
SLUG = "home_user_projects_garden"
TODAY = "2026-10-04"

REFUSAL = memory_writes.REFUSAL
KEPT = "is part of long-term memory"


@pytest.fixture(autouse=True)
def _fresh_read_ledger():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A home inside a folder that stands for the owner's ``~``, its memory made as the gateway
    makes it at start, and a working folder's memory beside it."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    MemoryStore().init()
    MemoryStore(workspace=pc / "workspace" / "_ext" / SLUG).init()
    return pc


def _workspace(home: Path) -> Path:
    return home / "workspace"


def _snapshot(home: Path) -> dict[str, str]:
    """Every file in the memory folders, by its path, with what it holds."""
    out = {}
    for folder in (_workspace(home) / "memory", _workspace(home) / "_ext"):
        for path in sorted(folder.rglob("*") if folder.is_dir() else ()):
            if path.is_file() and path.suffix == ".md":
                out[str(path.relative_to(home))] = path.read_text(encoding="utf-8")
    return out


# ── the native turn, driven ─────────────────────────────────────────────────────────────────


class _Model:
    """Replays scripted turns, one per inference."""

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


_DONE = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]


def _tools(home: Path, *, sandbox_mode: str = "off") -> NativeBuiltinToolProvider:
    return NativeBuiltinToolProvider(_workspace(home), session_key=KEY, sandbox_mode=sandbox_mode)


async def _drive(home: Path, mode: str, turns: list[list[AgentEvent]], **kw) -> list[AgentEvent]:
    """One turn of a chat in *mode*, every approval answered yes, as its turn engine runs it: as
    the chat's own work (``memory_writes.derived_from``), with worker threads that carry it, as
    the gateway's do (``carry_scope_into_worker_threads``)."""
    memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([*turns, _DONE]),
        tool_providers=[_tools(home, **kw)],
        cwd=_workspace(home),
        session_key=KEY,
    )
    seen: list[AgentEvent] = []
    with memory_writes.derived_from(KEY, memory_mode=mode):
        await rt.start()

        async def pump() -> None:
            async for ev in rt.stream("go"):
                seen.append(ev)
                if ev.kind == EVENT_PERMISSION_REQUEST:
                    await rt.approve_tool(ev.request_id)

        await asyncio.wait_for(pump(), timeout=20)
    return seen


def _asked(seen: list[AgentEvent]) -> list[str]:
    return [e.title for e in seen if e.kind == EVENT_PERMISSION_REQUEST]


def _result_of(seen: list[AgentEvent], cid: str) -> AgentEvent:
    return next(e for e in seen if e.kind == EVENT_TOOL_RESULT and e.tool_call_id == cid)


def _refused_unasked(result: AgentEvent, control: str) -> None:
    """Refused by *control* before anyone was asked, and audited so."""
    assert result.tool_meta.get("ok") is False
    assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"
    assert (unasked_outcome(result.tool_meta), unasked_reason(result.tool_meta)) == (
        "refused",
        control,
    )


#: Each way the file tools reach a memory document: a read first where the file exists (the
#: pre-edit read gate's rule, which a model meets too), then the change.
_FILE_CHANGES = {
    "preferences": [
        ("r1", "read_file", {"path": "memory/preferences.md"}),
        (
            "w1",
            "write_file",
            {
                "path": "memory/preferences.md",
                "content": "# User Preferences\n\n- Prefers green tea in the afternoon.\n",
            },
        ),
    ],
    "projects": [
        ("r1", "read_file", {"path": "memory/projects.md"}),
        (
            "w1",
            "edit_file",
            {
                "path": "memory/projects.md",
                "old_str": "<!-- Current work context -->",
                "new_str": "<!-- Current work context -->\n- Planning a spring garden.",
            },
        ),
    ],
    "a new day of the daily history": [
        (
            "w1",
            "write_file",
            {
                "path": f"memory/history/{TODAY}.md",
                "content": f"# {TODAY}\n\n#### 09:00 UTC\nTalked about the garden.\n",
            },
        ),
    ],
    "a working folder's preferences": [
        ("r1", "read_file", {"path": f"_ext/{SLUG}/memory/preferences.md"}),
        (
            "w1",
            "write_file",
            {
                "path": f"_ext/{SLUG}/memory/preferences.md",
                "content": "# User Preferences\n\n- Waters the beds on Fridays.\n",
            },
        ),
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["incognito", "temporary"])
@pytest.mark.parametrize("change", list(_FILE_CHANGES))
async def test_a_private_chats_file_tools_change_no_memory_document(home, mode, change):
    """🔴 Red before: the owner was asked, approved, and the document took the chat's words."""
    before = _snapshot(home)

    seen = await _drive(
        home, mode, [_call(cid, tool, args) for cid, tool, args in _FILE_CHANGES[change]]
    )

    assert _asked(seen) == []
    result = _result_of(seen, "w1")
    text = str(result.tool_output)
    assert KEPT in text and REFUSAL in text, text
    assert "Nothing was written." in text
    _refused_unasked(result, "restricted_session_block")
    assert _snapshot(home) == before


@pytest.mark.parametrize(
    "tool, args",
    [
        ("write_file", {"path": "memory/preferences.md", "content": "- Likes oolong.\n"}),
        (
            "edit_file",
            {"path": "memory/projects.md", "old_str": "# Active Projects", "new_str": "# Plans"},
        ),
    ],
)
def test_the_tool_refuses_the_change_itself_when_nobody_was_asked(home, tool, args):
    """A session whose approvals are waived runs the call with no pre-flight: the tool's own
    check is the same one, so the change is refused there too, in the same words."""
    before = _snapshot(home)
    tools = _tools(home)

    async def run():
        await tools.invoke("read_file", {"path": args["path"]})
        return await tools.invoke(tool, args)

    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        result = asyncio.run(run())

    assert not result.success
    assert KEPT in result.error and REFUSAL in result.error
    assert result.metadata.get("refused_by") == "restricted_session_block"
    assert _snapshot(home) == before


def test_an_apps_work_not_given_your_memory_changes_no_memory_document(home):
    """The same refusal, in the app's words, for an app that does not hold the memory
    permission (here one that is not installed at all)."""
    before = _snapshot(home)
    tools = _tools(home)

    async def run():
        await tools.invoke("read_file", {"path": "memory/preferences.md"})
        return await tools.invoke(
            "write_file", {"path": "memory/preferences.md", "content": "- Likes oolong.\n"}
        )

    with memory_writes.derived_from(KEY, memory_mode="persistent", app="garden-planner"):
        result = asyncio.run(run())

    assert not result.success
    assert KEPT in result.error
    assert "This work is for the app garden-planner, so it may not change your memory" in (
        result.error
    )
    assert result.metadata.get("refused_by") == "app_memory_not_granted"
    assert _snapshot(home) == before


# ── what still works ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_ordinary_chats_write_of_a_memory_document_goes_through_its_store(home):
    """🔴 Red before: the write landed past the store, so the keyword index never learned it and a
    search found nothing until a rebuild. Now it is the store's own write: indexed at once."""
    content = "# User Preferences\n\n- Prefers oolong in the afternoon.\n"
    seen = await _drive(
        home,
        "persistent",
        [
            _call("r1", "read_file", {"path": "memory/preferences.md"}),
            _call("w1", "write_file", {"path": "memory/preferences.md", "content": content}),
            _call("r2", "read_file", {"path": "memory/projects.md"}),
            _call(
                "e1",
                "edit_file",
                {
                    "path": "memory/projects.md",
                    "old_str": "<!-- Current work context -->",
                    "new_str": "- Repotting the basil seedlings.",
                },
            ),
        ],
    )

    assert _asked(seen) == ["write_file", "edit_file"]
    assert _result_of(seen, "w1").tool_meta.get("ok") is not False
    store = MemoryStore()
    assert (_workspace(home) / "memory" / "preferences.md").read_text(encoding="utf-8") == content
    assert [hit["path"] for hit in store.search("oolong")] == [str(store._preferences_file)]
    assert [hit["path"] for hit in store.search("basil")] == [str(store._projects_file)]
    assert store.fts_desync_count() == 0


def test_an_ordinary_chats_write_of_a_memory_document_waits_for_the_documents_lock(home):
    """A consolidation holds the documents' lock across its read and its write: the agent's write
    of a document waits for it, and lands once it is let go."""
    from personalclaw.memory import hold_documents

    target = _workspace(home) / "memory" / "preferences.md"
    held, release = threading.Event(), threading.Event()

    def consolidation() -> None:
        with hold_documents():
            held.set()
            release.wait(5)

    holder = threading.Thread(target=consolidation)
    holder.start()
    assert held.wait(5)
    tools = _tools(home)

    async def run():
        await tools.invoke("read_file", {"path": "memory/preferences.md"})
        write = asyncio.ensure_future(
            tools.invoke("write_file", {"path": "memory/preferences.md", "content": "- Tea.\n"})
        )
        await asyncio.sleep(0.5)
        waited = not write.done() and "- Tea." not in target.read_text(encoding="utf-8")
        release.set()
        return waited, await write

    with memory_writes.derived_from(KEY, memory_mode="persistent"):
        waited, result = asyncio.run(run())
    holder.join(5)

    assert waited, "the write landed while the documents' lock was held"
    assert result.success, result.error
    assert target.read_text(encoding="utf-8") == "- Tea.\n"


@pytest.mark.asyncio
async def test_an_incognito_chat_still_reads_its_memory_and_writes_elsewhere(home):
    seen = await _drive(
        home,
        "incognito",
        [
            _call("r1", "read_file", {"path": "memory/preferences.md"}),
            _call("w1", "write_file", {"path": "notes/garden.md", "content": "- Beds dug.\n"}),
        ],
    )

    assert "# User Preferences" in str(_result_of(seen, "r1").tool_output)
    assert _asked(seen) == ["write_file"]
    assert _result_of(seen, "w1").tool_meta.get("ok") is not False
    assert (_workspace(home) / "notes" / "garden.md").read_text(encoding="utf-8") == "- Beds dug.\n"


# ── the shell ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["incognito", "temporary"])
@pytest.mark.parametrize(
    "command",
    [
        "printf '%s\\n' '- Likes oolong.' >> memory/preferences.md",
        "cd memory && sed -i.bak 's/Active/Current/' projects.md",
        f"cp /dev/null memory/history/{TODAY}.md",
        f"mkdir -p _ext/{SLUG}/memory && echo x > _ext/{SLUG}/memory/projects.md",
        "rm {home}/workspace/memory/preferences.md",
    ],
)
async def test_a_private_chats_command_that_changes_memory_is_refused_unasked(home, mode, command):
    """🔴 Red before: the command was put to the owner and, approved, changed the document. With
    the sandbox off here, so it is the screen that refuses it, before anyone is asked."""
    before = _snapshot(home)

    seen = await _drive(home, mode, [_call("b1", "bash", {"command": command.format(home=home)})])

    assert _asked(seen) == []
    result = _result_of(seen, "b1")
    text = str(result.tool_output)
    assert KEPT in text and REFUSAL in text, text
    _refused_unasked(result, "restricted_session_block")
    assert _snapshot(home) == before


@pytest.mark.asyncio
async def test_a_private_chats_command_that_only_reads_memory_runs(home):
    seen = await _drive(
        home, "incognito", [_call("b1", "bash", {"command": "cat memory/preferences.md"})]
    )

    assert "# User Preferences" in str(_result_of(seen, "b1").tool_output)


@pytest.mark.asyncio
async def test_an_ordinary_chats_command_is_not_held_by_the_memory_screen(home):
    seen = await _drive(
        home,
        "persistent",
        [
            _call(
                "b1", "bash", {"command": "printf '%s\\n' '- Likes oolong.' >> memory/projects.md"}
            )
        ],
    )

    assert _asked(seen) == ["bash"]
    assert "- Likes oolong." in (_workspace(home) / "memory" / "projects.md").read_text()


# ── the sandbox around a private chat's commands ────────────────────────────────────────────


def _memory_folders(home: Path) -> list[str]:
    real = os.path.realpath(_workspace(home))
    return [f"{real}/memory", f"{real}/_ext"]


def test_the_sandbox_keeps_the_memory_folders_read_only_for_a_private_chats_commands(home):
    """🔴 Red before: neither the macOS profile nor the Linux launcher named the memory folders."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    folders = _memory_folders(home)
    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        profile = _build_seatbelt_profile("standard")
        launcher = _build_launcher_script("standard")
    for folder in folders:
        assert f'(deny file-write* (subpath "{folder}"))' in profile
    # The folder that holds them keeps its own entry, as the home does, so they stay at the
    # paths the rules name; what is inside it is untouched (a literal names the folder alone).
    holder = os.path.realpath(_workspace(home))
    assert f'(deny file-write* (literal "{holder}"))' in profile
    compile(launcher, "<launcher>", "exec")
    assert f"MEMORY_FOLDERS = {folders!r}".replace("'", '"') in launcher

    # Work that may change memory runs with no such fence: an ordinary chat's, and the owner's.
    for scope in (memory_writes.derived_from(KEY, memory_mode="persistent"), _nothing()):
        with scope:
            profile = _build_seatbelt_profile("standard")
            launcher = _build_launcher_script("standard")
        assert not any(f'(subpath "{folder}")' in profile for folder in folders)
        assert f'(literal "{holder}")' not in profile
        assert "MEMORY_FOLDERS = []" in launcher


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _seatbelt(command: str) -> subprocess.CompletedProcess:
    from personalclaw.sandbox import sandbox_exec_argv

    argv, profile = sandbox_exec_argv(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(profile)


macos_only = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS profile is measured here; the Linux launcher below",
)


@macos_only
def test_macos_a_private_chats_command_cannot_write_the_memory_folders(home):
    """Whatever the command says: the kernel refuses the write, a new file and a new folder."""
    ws = os.path.realpath(_workspace(home))
    before = _snapshot(home)
    writes = (
        f'printf x >> "{ws}/memory/preferences.md"',
        f'echo x > "{ws}/memory/history/{TODAY}.md"',
        f'mkdir -p "{ws}/_ext/another_folder/memory"',
    )
    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        runs = [_seatbelt(w) for w in writes]
        elsewhere = _seatbelt(f'echo x > "{ws}/notes.txt" && mkdir -p "{ws}/plans/spring"')

    for run in runs:
        assert run.returncode != 0 and "Operation not permitted" in run.stderr, run
    assert _snapshot(home) == before
    assert not (_workspace(home) / "_ext" / "another_folder").exists()
    assert elsewhere.returncode == 0, elsewhere.stderr
    assert (_workspace(home) / "plans" / "spring").is_dir()

    # An ordinary chat's command writes there as before.
    with memory_writes.derived_from(KEY, memory_mode="persistent"):
        assert _seatbelt(writes[0]).returncode == 0


def _linux_sandbox_works() -> bool:
    if sys.platform != "linux":
        return False
    from personalclaw.sandbox import _probe_unshare

    return _probe_unshare()


def _launch(command: str) -> subprocess.CompletedProcess:
    from personalclaw.sandbox import namespace_argv

    argv = namespace_argv(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(argv[1])


@pytest.mark.skipif(
    not _linux_sandbox_works(), reason="needs the Linux namespace sandbox (user + mount namespaces)"
)
def test_linux_a_private_chats_command_cannot_write_the_memory_folders(home):
    ws = os.path.realpath(_workspace(home))
    shutil.rmtree(_workspace(home) / "_ext")  # a folder that is not there yet is fenced too
    before = _snapshot(home)
    writes = (
        f'printf x >> "{ws}/memory/preferences.md"',
        f'echo x > "{ws}/memory/history/{TODAY}.md"',
        f'mkdir -p "{ws}/_ext/another_folder/memory"',
    )
    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        runs = [_launch(w) for w in writes]
        elsewhere = _launch(f'echo x > "{ws}/notes.txt"')

    for run in runs:
        assert run.returncode != 0, run
    assert _snapshot(home) == before
    assert not (_workspace(home) / "_ext" / "another_folder").exists()
    assert elsewhere.returncode == 0, elsewhere.stderr

    with memory_writes.derived_from(KEY, memory_mode="persistent"):
        assert _launch(writes[0]).returncode == 0


@pytest.mark.skipif(
    not _linux_sandbox_works(), reason="needs the Linux namespace sandbox (user + mount namespaces)"
)
@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_linux_a_private_chats_command_runs_in_a_home_with_no_workspace_yet(home, mode):
    """The memory folders a private chat's command is fenced from are made when the home has none
    yet, and the command runs with them fenced.

    🔴 Red before: the launcher made a missing memory folder only after the top of the home was
    read-only, where no entry can be added, so with no workspace in the home it made none and
    stopped every command of a private chat's work ("could not fence memory")."""
    shutil.rmtree(_workspace(home))
    ws = os.path.realpath(_workspace(home))
    with memory_writes.derived_from(KEY, memory_mode=mode):
        ran = _launch("true")
        wrote = _launch(f'echo x > "{ws}/memory/preferences.md"')

    assert ran.returncode == 0, ran.stderr
    assert wrote.returncode != 0, wrote
    assert (_workspace(home) / "memory").is_dir() and (_workspace(home) / "_ext").is_dir()
    assert not (_workspace(home) / "memory" / "preferences.md").exists()


# ── the owner's own save ────────────────────────────────────────────────────────────────────


def _file_app() -> web.Application:
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    @web.middleware
    async def owner(request, handler):
        with scoped_to_app(""):
            return await handler(request)

    app = web.Application(middlewares=[owner])
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    return app


@pytest.mark.asyncio
async def test_the_files_editor_saves_a_memory_document_through_its_store(home):
    """🔴 Red before: the save replaced the file past the store, and the keyword index did not
    have the owner's words until a rebuild."""
    path = _workspace(home) / "memory" / "preferences.md"
    roots = [("Workspace", str(_workspace(home)))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app())) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": (await read.text()) + "- Likes oolong.\n"},
                headers={"If-Match": read.headers["ETag"]},
            )
            assert saved.status == 200, await saved.text()

    store = MemoryStore()
    assert path.read_text(encoding="utf-8").endswith("- Likes oolong.\n")
    assert [hit["path"] for hit in store.search("oolong")] == [str(store._preferences_file)]
