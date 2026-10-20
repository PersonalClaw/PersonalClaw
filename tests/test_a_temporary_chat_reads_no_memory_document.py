"""A Temporary chat reads no memory document, whichever tool it reaches for, and the file tools
leave every one of PersonalClaw's own databases alone, a working folder's included.

Measured before this was written, against the tools as they stood:

* in a Temporary chat, whose notice says memory is neither read nor written, ``read_file`` of
  ``memory/preferences.md`` returned the owner's preferences, ``list_dir``, ``glob`` and ``grep``
  listed and searched the memory folders, and ``cat memory/preferences.md`` ran: the file tools and
  the shell never asked the one check every memory read passes (``memory_reads.reach_of``). The
  same held for an app's work that was not given your memory;
* in every chat, a working folder's own memory database (``_ext/<folder>/memory_index.db``) and its
  learning log (``_ext/<folder>/learning.db``) were files the tools read, listed and wrote, while
  the knowledge library's database beside them was refused: the store screen read only the
  inventory entries inside the workspace and none of the partitions it declares;
* the tools that read a file by its path past the file tools, an artifact's ``content_file`` and
  ``notify_attachment``, and a file-backed artifact, which reads and writes the file it shows,
  held back none of it.

The behaviour now:

* work that may read none of your memory (a Temporary chat's, the work it starts, an app's not
  given your memory, work whose chat's memory setting cannot be read) reads nothing in the memory
  folders: the file tools refuse a path there in the words the memory read path uses, before anyone
  is asked, and leave the folders out of every listing and search; the shell refuses a command that
  names a path there; the sandbox keeps the folders unreadable to the commands such work starts;
* an Incognito chat still reads its memory, and an ordinary chat reads it as before;
* every database the state inventory declares in the workspace, each working folder's included, is
  PersonalClaw's own store to the file tools and the shell, in every chat;
* a tool that reads a file the agent names, and an artifact that shows a file, hold back what the
  file tools hold back for what the file is, and an artifact that shows a memory document changes it
  only in work that may change your memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import personalclaw.config.loader as loader
from personalclaw import mcp_core, memory_reads, memory_writes
from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.file_scope import refusal
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
from personalclaw.tool_providers.base import ToolFailure

KEY = "dashboard:chat-orchard-1700000000"
#: A working folder's memory partition, as ``config.loader.memory_dir_for_cwd`` names one.
SLUG = "home_user_projects_orchard"
TODAY = "2026-10-04"
PREFERENCE = "Prefers green tea in the afternoon."
WITHHELD = "is part of long-term memory"


@pytest.fixture(autouse=True)
def _fresh_read_ledger():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A home inside a folder that stands for the owner's ``~``: its memory as the gateway makes it
    at start, with one preference and a day of history in it, a working folder's memory with its
    own database and learning log beside it, and a note of the owner's outside the memory."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    MemoryStore().init()
    partition = pc / "workspace" / "_ext" / SLUG
    MemoryStore(workspace=partition).init()
    memory = pc / "workspace" / "memory"
    (memory / "preferences.md").write_text(f"# User Preferences\n\n- {PREFERENCE}\n")
    (memory / "history" / f"{TODAY}.md").write_text(f"# {TODAY}\n\nTalked about green tea.\n")
    (partition / "memory" / "preferences.md").write_text("# User Preferences\n\n- Green tea.\n")
    for name in ("memory_index.db", "learning.db"):
        with sqlite3.connect(partition / name) as db:
            db.execute("CREATE TABLE IF NOT EXISTS rows (body TEXT)")
            db.execute("INSERT INTO rows VALUES ('green tea in the afternoon')")
    notes = pc / "workspace" / "notes"
    notes.mkdir()
    (notes / "orchard.md").write_text("- Plant the pear by the green tea bushes.\n")
    return pc


def _workspace(home: Path) -> Path:
    return home / "workspace"


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


def _tools(home: Path) -> NativeBuiltinToolProvider:
    return NativeBuiltinToolProvider(_workspace(home), session_key=KEY, sandbox_mode="off")


async def _drive(
    home: Path, mode: str, calls: list[tuple[str, str, dict]], *, app: str = ""
) -> list[AgentEvent]:
    """One turn of a chat in *mode* (for the app *app*, when one started it), every approval
    answered yes, as its turn engine runs it: as the chat's own work
    (``memory_writes.derived_from``), with worker threads that carry it, as the gateway's do."""
    memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([*[_call(*c) for c in calls], _DONE]),
        tool_providers=[_tools(home)],
        cwd=_workspace(home),
        session_key=KEY,
    )
    seen: list[AgentEvent] = []
    with memory_writes.derived_from(KEY, memory_mode=mode, app=app):
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


def _output(seen: list[AgentEvent], cid: str) -> str:
    result = _result_of(seen, cid)
    return str(result.tool_output)


def _result_of(seen: list[AgentEvent], cid: str) -> AgentEvent:
    return next(e for e in seen if e.kind == EVENT_TOOL_RESULT and e.tool_call_id == cid)


def _refused(result: AgentEvent, control: str) -> None:
    """Refused by *control*, a control the tool enforces, and audited so."""
    assert result.tool_meta.get("ok") is False
    assert (unasked_outcome(result.tool_meta), unasked_reason(result.tool_meta)) == (
        "refused",
        control,
    )


def _refused_unasked(result: AgentEvent, control: str) -> None:
    """Refused by *control* before anyone was asked to approve the call, and audited so."""
    _refused(result, control)
    assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"


# ── a Temporary chat's file tools ───────────────────────────────────────────────────────────

#: Each way a file tool names a memory document or a memory folder.
_MEMORY_READS = {
    "the home's preferences": ("read_file", {"path": "memory/preferences.md"}),
    "a day of the daily history": ("read_file", {"path": f"memory/history/{TODAY}.md"}),
    "a working folder's preferences": (
        "read_file",
        {"path": f"_ext/{SLUG}/memory/preferences.md"},
    ),
    "the memory folder's listing": ("list_dir", {"path": "memory"}),
    "a search of the memory folder": ("grep", {"query": "green tea", "path": "memory"}),
    "a glob of the working folders' memory": ("glob", {"pattern": "**/*.md", "path": "_ext"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("read", list(_MEMORY_READS))
async def test_a_temporary_chats_file_tools_read_no_memory_document(home, read):
    """🔴 Red before: the tool returned the owner's memory to a chat that promises to read none."""
    tool, args = _MEMORY_READS[read]

    seen = await _drive(home, "temporary", [("r1", tool, args)])

    text = _output(seen, "r1")
    assert WITHHELD in text and memory_reads.TEMPORARY in text, text
    assert "green tea" not in text.lower()
    assert _asked(seen) == []
    _refused(_result_of(seen, "r1"), "memory_withheld")
    # The same answer is given before anyone could be asked to approve the call.
    with memory_writes.derived_from(KEY, memory_mode="temporary"):
        before = refusal(tool, args, cwd=_workspace(home))
    assert before is not None and str(before) in text
    assert before.control == "memory_withheld"


@pytest.mark.asyncio
async def test_a_temporary_chats_listings_and_searches_leave_the_memory_folders_out(home):
    """🔴 Red before: the workspace's listing named ``memory/`` and ``_ext/``, and a search of it
    returned the owner's preferences and history beside her note."""
    seen = await _drive(
        home,
        "temporary",
        [
            ("l1", "list_dir", {"path": "."}),
            ("g1", "glob", {"pattern": "**/*.md"}),
            ("s1", "grep", {"query": "green tea"}),
        ],
    )

    listing = _output(seen, "l1").splitlines()
    assert "notes/" in listing
    assert "memory/" not in listing and "_ext/" not in listing
    assert _output(seen, "g1").splitlines() == [os.path.join("notes", "orchard.md")]
    found = _output(seen, "s1")
    assert "orchard.md" in found
    assert "preferences" not in found and "history" not in found and "_ext" not in found


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "cat memory/preferences.md",
        f"grep -rn tea memory/history/{TODAY}.md",
        f"head -n 3 _ext/{SLUG}/memory/preferences.md",
        "ls {home}/workspace/memory",
    ],
)
async def test_a_temporary_chats_command_that_reads_memory_is_refused_unasked(home, command):
    """🔴 Red before: ``cat memory/preferences.md`` ran and printed the owner's preferences. The
    sandbox is off here, so it is the screen that refuses it, before anyone is asked."""
    seen = await _drive(home, "temporary", [("b1", "bash", {"command": command.format(home=home)})])

    assert _asked(seen) == []
    text = _output(seen, "b1")
    assert WITHHELD in text and memory_reads.TEMPORARY in text, text
    assert PREFERENCE not in text
    _refused_unasked(_result_of(seen, "b1"), "memory_withheld")


@pytest.mark.asyncio
async def test_an_apps_work_not_given_your_memory_reads_no_memory_document(home):
    """The same refusal, in the app's words, for an app that does not hold the memory
    permission (here one that is not installed at all)."""
    seen = await _drive(
        home,
        "persistent",
        [
            ("r1", "read_file", {"path": "memory/preferences.md"}),
            ("b1", "bash", {"command": "cat memory/preferences.md"}),
        ],
        app="orchard-planner",
    )

    for cid in ("r1", "b1"):
        text = _output(seen, cid)
        assert WITHHELD in text, text
        assert "This work is for the app orchard-planner, so nothing is read from your memory" in (
            text
        )
        assert PREFERENCE not in text
        _refused(_result_of(seen, cid), "memory_withheld")
    assert _asked(seen) == []


# ── what still reads ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["incognito", "persistent"])
async def test_an_incognito_or_ordinary_chat_reads_its_memory_documents(home, mode):
    """An Incognito chat reads memory as any chat does, and an ordinary chat as it always has."""
    seen = await _drive(
        home,
        mode,
        [
            ("r1", "read_file", {"path": "memory/preferences.md"}),
            ("r2", "read_file", {"path": f"_ext/{SLUG}/memory/preferences.md"}),
            ("l1", "list_dir", {"path": "."}),
            ("s1", "grep", {"query": "green tea", "path": "memory"}),
            ("b1", "bash", {"command": "cat memory/preferences.md"}),
        ],
    )

    assert PREFERENCE in _output(seen, "r1")
    assert "Green tea." in _output(seen, "r2")
    assert {"memory/", "_ext/", "notes/"} <= set(_output(seen, "l1").splitlines())
    assert "preferences.md" in _output(seen, "s1")
    assert PREFERENCE in _output(seen, "b1")


# ── the sandbox around a Temporary chat's commands ──────────────────────────────────────────


def _memory_folders(home: Path) -> list[str]:
    real = os.path.realpath(_workspace(home))
    return [f"{real}/memory", f"{real}/_ext"]


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_sandbox_keeps_the_memory_folders_unreadable_for_a_temporary_chats_commands(home):
    """🔴 Red before: neither the macOS profile nor the Linux launcher kept a Temporary chat's
    commands from reading the memory folders."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    folders = _memory_folders(home)
    with memory_writes.derived_from(KEY, memory_mode="temporary"):
        profile = _build_seatbelt_profile("standard")
        launcher = _build_launcher_script("standard")
    for folder in folders:
        assert f'(deny file-read* (subpath "{folder}"))' in profile
        assert f'(deny file-write* (subpath "{folder}"))' in profile
    compile(launcher, "<launcher>", "exec")
    assert f"MEMORY_HIDDEN = {folders!r}".replace("'", '"') in launcher

    # Work that reads its memory runs with nothing hidden: an Incognito chat's, an ordinary
    # chat's, and the owner's own.
    for scope in (
        memory_writes.derived_from(KEY, memory_mode="incognito"),
        memory_writes.derived_from(KEY, memory_mode="persistent"),
        _nothing(),
    ):
        with scope:
            profile = _build_seatbelt_profile("standard")
            launcher = _build_launcher_script("standard")
        assert not any(f'(deny file-read* (subpath "{f}"))' in profile for f in folders)
        assert "MEMORY_HIDDEN = []" in launcher


def _seatbelt(command: str) -> subprocess.CompletedProcess:
    from personalclaw.sandbox import sandbox_exec_argv

    argv, profile = sandbox_exec_argv(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(profile)


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS profile is measured here; the Linux launcher below",
)
def test_macos_a_temporary_chats_command_cannot_read_the_memory_folders(home):
    """Whatever the command says: the kernel refuses the read, and the write."""
    ws = os.path.realpath(_workspace(home))
    reads = (
        f'cat "{ws}/memory/preferences.md"',
        f'cat "{ws}/_ext/{SLUG}/memory/preferences.md"',
        f'grep -r tea "{ws}/memory"',
    )
    with memory_writes.derived_from(KEY, memory_mode="temporary"):
        runs = [_seatbelt(r) for r in reads]
        wrote = _seatbelt(f'echo x >> "{ws}/memory/preferences.md"')
        elsewhere = _seatbelt(f'cat "{ws}/notes/orchard.md"')

    for run in runs:
        assert run.returncode != 0 and "Operation not permitted" in run.stderr, run
        assert "green tea" not in run.stdout.lower()
    assert wrote.returncode != 0
    assert "pear" in elsewhere.stdout, elsewhere.stderr

    for mode in ("incognito", "persistent"):
        with memory_writes.derived_from(KEY, memory_mode=mode):
            assert PREFERENCE in _seatbelt(reads[0]).stdout


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
def test_linux_a_temporary_chats_command_cannot_read_the_memory_folders(home):
    ws = os.path.realpath(_workspace(home))
    with memory_writes.derived_from(KEY, memory_mode="temporary"):
        read = _launch(f'cat "{ws}/memory/preferences.md"')
        listed = _launch(f'ls -A "{ws}/memory" "{ws}/_ext"')
        wrote = _launch(f'echo x > "{ws}/memory/new.md"')
        elsewhere = _launch(f'cat "{ws}/notes/orchard.md"')

    # Each command ran: the launcher refused none of them, so what each shows is the sandbox's.
    for run in (read, listed, wrote, elsewhere):
        assert "sandbox:" not in run.stderr, run
    assert read.returncode != 0 and PREFERENCE not in read.stdout, read
    assert "preferences.md" not in listed.stdout and SLUG not in listed.stdout, listed
    assert wrote.returncode != 0 and not (_workspace(home) / "memory" / "new.md").exists()
    assert "pear" in elsewhere.stdout, elsewhere.stderr

    with memory_writes.derived_from(KEY, memory_mode="incognito"):
        assert PREFERENCE in _launch(f'cat "{ws}/memory/preferences.md"').stdout


#: The Linux launcher run on any host against a kernel stood in for, one that holds a remount to
#: the rule a user namespace holds it to: a mount's nosuid, nodev and noexec are locked when the
#: mount came from outside the namespace, and a remount that would clear one is refused (EPERM).
#: Here the runtime folder the launcher takes its empty folders from and the home are mounted
#: nosuid,nodev, as a tmpfs and many a home are. Each mount call is written to the log.
_UNDER_A_STRICT_KERNEL = r"""
import builtins, ctypes, errno, json, os, sys, types

cfg = json.loads(sys.argv[1])
MS_BIND, MS_REMOUNT, LOCKED = 4096, 32, 2 | 4 | 8
bound = {}


def _mounted_with(path):
    real = os.path.realpath(path)
    return 2 | 4 if any(real == r or real.startswith(r + os.sep) for r in cfg["nosuid"]) else 0


class _Flags:
    def __init__(self, f_flag):
        self.f_flag = f_flag


def _statvfs(path):
    real = os.path.realpath(path)
    return _Flags(bound.get(real, _mounted_with(real)))


class _Mount:
    def __call__(self, source, target, _fstype, flags, _data):
        real = os.path.realpath(target.decode())
        with builtins.open(cfg["log"], "a") as log:
            print(json.dumps([real, flags]), file=log)
        if flags & MS_REMOUNT:
            if bound.get(real, _mounted_with(real)) & LOCKED & ~flags:
                ctypes.set_errno(errno.EPERM)
                return -1
            return 0
        if flags & MS_BIND and source:
            bound[real] = _mounted_with(source.decode())
        return 0


class StrictKernel:
    def __init__(self, *_args, **_kwargs):
        self.unshare = lambda *_a: 0
        self.mount = _Mount()


def _open(path, *args, **kwargs):
    if str(path).startswith("/proc/"):
        return builtins.open(os.devnull, "w")
    return builtins.open(path, *args, **kwargs)


ctypes.CDLL = StrictKernel
os.statvfs = _statvfs
launcher = types.ModuleType("launcher_under_test")
launcher.open = _open
exec(compile(open(cfg["script"]).read(), cfg["script"], "exec"), launcher.__dict__)
launcher.RUNTIME_DIRS = cfg["runtime_dirs"]
launcher._home_device = lambda: None
sys.argv = [cfg["script"], *cfg["argv"]]
launcher.main()
"""


@pytest.mark.parametrize("nosuid", ["the scratch", "the scratch and the home"])
@pytest.mark.parametrize("mode", ["temporary", "incognito", "persistent"])
def test_the_linux_launcher_fences_a_home_and_a_scratch_mounted_nosuid(
    home, tmp_path, mode, nosuid
):
    """🔴 Red before: the launcher made the home and the memory folders read-only with a remount
    that asked for read-only alone, which a user namespace refuses for a mount whose nosuid or
    nodev it locked, so on such a host every command stopped with "sandbox: could not fence the
    home" (or "could not fence memory", for the empty folder over a Temporary chat's memory,
    which comes from a nosuid,nodev tmpfs on every host). Each read-only remount now keeps the
    flags its mount has, and the command runs fenced."""
    from personalclaw.sandbox import _build_launcher_script

    runtime = tmp_path / "run-user-1000"
    runtime.mkdir()
    script = tmp_path / "launcher.py"
    with memory_writes.derived_from(KEY, memory_mode=mode):
        script.write_text(_build_launcher_script("standard"), encoding="utf-8")
    log = tmp_path / "mounts.jsonl"
    cfg = {
        "script": str(script),
        "runtime_dirs": [str(runtime)],
        "nosuid": [os.path.realpath(runtime)]
        + ([os.path.realpath(tmp_path / "user")] if "home" in nosuid else []),
        "log": str(log),
        "argv": ["/bin/sh", "-c", "echo ran"],
    }
    run = subprocess.run(
        [sys.executable, "-c", _UNDER_A_STRICT_KERNEL, json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert run.returncode == 0 and run.stdout.strip() == "ran", run
    remounts = {
        target: flags
        for target, flags in map(json.loads, log.read_text().splitlines())
        if flags & 32
    }
    # Each remount is read-only and keeps exactly the nosuid and nodev of its mount: the home's,
    # and for a memory folder its own (the home's), or the scratch's for the empty folder over a
    # Temporary chat's memory.
    home_kept = 2 | 4 if "home" in nosuid else 0
    want = {os.path.realpath(home): home_kept}
    if mode != "persistent":
        for folder in _memory_folders(home):
            want[folder] = 2 | 4 if mode == "temporary" else home_kept
    assert {target: flags & (1 | 2 | 4) for target, flags in remounts.items()} == {
        target: 1 | kept for target, kept in want.items()
    }


# ── a working folder's own databases ────────────────────────────────────────────────────────


@pytest.mark.parametrize("database", ["memory_index.db", "learning.db"])
def test_a_working_folders_own_databases_are_not_files_to_the_tools(home, database):
    """🔴 Red before: in an ordinary chat ``read_file`` handed the model the pages of a working
    folder's memory database and learning log, and ``write_file`` replaced them, while the
    knowledge library's database beside them was refused."""
    tools = _tools(home)
    path = f"_ext/{SLUG}/{database}"
    original = (_workspace(home) / path).read_bytes()

    async def run(name: str, args: dict) -> Any:
        return await tools.invoke(name, args)

    for name, args in (
        ("read_file", {"path": path}),
        ("read_file", {"path": str(_workspace(home) / path) + "-wal"}),
        ("write_file", {"path": path, "content": "x"}),
    ):
        result = asyncio.run(run(name, args))
        assert not result.success, name
        assert "PersonalClaw's own memory store" in result.error, result.error
        assert result.metadata.get("refused_by") == "file_scope"
        assert str(refusal(name, args, cwd=_workspace(home))) == result.error

    listing = asyncio.run(run("list_dir", {"path": f"_ext/{SLUG}"})).output.splitlines()
    assert listing == ["memory/"]
    assert asyncio.run(run("glob", {"pattern": "**/*.db"})).output == "(no matches)"
    assert (_workspace(home) / path).read_bytes() == original


@pytest.mark.parametrize(
    "command",
    [
        f"sqlite3 _ext/{SLUG}/learning.db 'select body from rows'",
        f"cd _ext/{SLUG} && strings memory_index.db | head",
    ],
)
def test_a_shell_command_naming_a_working_folders_database_is_refused(home, command):
    """🔴 Red before: the command was put to the owner, and approved it read the database."""
    from personalclaw.hooks import TOOL_DENY, HookManager

    ran = asyncio.run(_tools(home).invoke("bash", {"command": command}))
    assert not ran.success
    assert "PersonalClaw's own memory store" in ran.error, ran.error
    assert ran.metadata.get("refused_by") == "own_store"

    verdict = HookManager().on_tool_call(f"Running: {command}", cwd=_workspace(home))
    assert verdict.action == TOOL_DENY
    assert "PersonalClaw's own memory store" in verdict.reason


# ── a file another tool reads by its path ───────────────────────────────────────────────────


@contextlib.contextmanager
def _call_as(mode: str, *, app: str = ""):
    """A tool call made for the chat *KEY* in *mode* (for the app *app*, when one started it), as
    the gateway's own agent makes one: the chat's session bound for the call inside its work."""
    token = mcp_core.set_current_session_key(KEY)
    try:
        with memory_writes.derived_from(KEY, memory_mode=mode, app=app):
            yield
    finally:
        mcp_core.reset_current_session_key(token)


@pytest.fixture
def store(tmp_path):
    """The artifact library the agent's artifact tools reach, rooted in this test's folder."""
    from unittest.mock import patch

    from personalclaw.artifacts.native import NativeArtifactProvider

    library = NativeArtifactProvider(root=tmp_path / "artifacts")
    with patch("personalclaw.artifacts.registry.get_provider", return_value=library):
        yield library


def test_an_artifacts_content_file_holds_back_what_the_file_tools_do(home, store):
    """🔴 Red before: an artifact's ``content_file`` read any file that is not a credential, so an
    app's work not given your memory copied the owner's preferences into an artifact it made, and
    any chat copied a working folder's memory database, past the file tools' refusals. (A Temporary
    chat's save is refused before any file is read: such a chat changes nothing in the library.)"""
    from personalclaw import mcp_artifacts

    ws = _workspace(home)
    with _call_as("persistent", app="orchard-planner"):
        copied = mcp_artifacts._call_tool(
            "artifact_save",
            {"name": "Preferences", "content_file": str(ws / "memory" / "preferences.md")},
        )
    with _call_as("persistent"):
        database = mcp_artifacts._call_tool(
            "artifact_save",
            {"name": "Rows", "content_file": str(ws / "_ext" / SLUG / "learning.db")},
        )
        note = mcp_artifacts._call_tool(
            "artifact_save",
            {"name": "Orchard note", "content_file": str(ws / "notes" / "orchard.md")},
        )

    assert WITHHELD in copied and "This work is for the app orchard-planner" in copied, copied
    assert PREFERENCE not in copied
    assert "PersonalClaw's own memory store" in database, database
    assert "Saved artifact 'Orchard note'" in note, note
    assert [a.name for a in store.list()] == ["Orchard note"]


def test_a_file_sent_to_the_owner_holds_back_what_the_file_tools_do(home, monkeypatch):
    """🔴 Red before: ``notify_attachment`` copied any file that is not a credential into the
    outbox and sent it on, so a Temporary chat sent the owner's preferences out of her memory, and
    any chat a document stored in the knowledge library, past the file tools' refusals."""
    from personalclaw.config.loader import outbox_dir

    posted: list[str] = []
    monkeypatch.setattr(mcp_core, "_post", lambda path, body=None, **_kw: posted.append(path) or {})
    monkeypatch.setattr(mcp_core, "_current_session_thread_ts", lambda: None)
    ws = _workspace(home)
    stored = ws / "knowledge" / "files" / "orchard.md"
    stored.parent.mkdir(parents=True)
    stored.write_text("- The pear needs a pollinator.\n")

    def send(mode: str, path: Path) -> tuple[Any, Any]:
        args = {"path": str(path)}
        with _call_as(mode):
            return (
                mcp_core._preflight("notify_attachment", args),
                mcp_core._call_tool_inner("notify_attachment", args),
            )

    refused = [send("temporary", ws / "memory" / "preferences.md")]
    refused.append(send("temporary", ws / "_ext" / SLUG / "memory" / "preferences.md"))
    stored_before, stored_out = send("persistent", stored)
    assert posted == [] and not any(outbox_dir().iterdir())

    for before, out in refused:
        assert isinstance(out, ToolFailure), out
        assert WITHHELD in out and memory_reads.TEMPORARY in out, out
        assert isinstance(before, ToolFailure) and before.reason == out.reason
    assert isinstance(stored_out, ToolFailure), stored_out
    assert "PersonalClaw's own knowledge store" in stored_out, stored_out
    assert isinstance(stored_before, ToolFailure)

    # The owner's memory still goes out from a chat that reads it, and a note from any chat.
    _, mine = send("persistent", ws / "memory" / "preferences.md")
    _, note = send("temporary", ws / "notes" / "orchard.md")
    assert mine == "File sent: preferences.md" and note == "File sent: orchard.md"
    assert sorted(p.name for p in outbox_dir().iterdir()) == ["orchard.md", "preferences.md"]


def test_an_artifact_that_shows_a_memory_document_is_held_as_the_document_is(home, store):
    """🔴 Red before: a file-backed artifact reads the file it shows at every read and writes it at
    every save, so a Temporary chat read the owner's preferences through ``artifact_get`` of an
    artifact she had saved from them in Files, and an app's work not given your memory rewrote
    them through ``artifact_update``."""
    from personalclaw import mcp_artifacts

    prefs = _workspace(home) / "memory" / "preferences.md"
    original = prefs.read_text()
    view = store.create(name="My preferences", kind="markdown", source_path=str(prefs)).slug

    with _call_as("temporary"):
        hidden = mcp_artifacts._call_tool("artifact_get", {"slug": view})
    with _call_as("persistent", app="orchard-planner"):
        rewritten = mcp_artifacts._call_tool(
            "artifact_update", {"slug": view, "content": "- Coffee.\n"}
        )
        unread = mcp_artifacts._call_tool("artifact_get", {"slug": view})
    with _call_as("incognito"):
        incognito = mcp_artifacts._call_tool("artifact_get", {"slug": view})
    with _call_as("persistent"):
        ordinary = mcp_artifacts._call_tool("artifact_get", {"slug": view})

    assert WITHHELD in hidden and memory_reads.TEMPORARY in hidden, hidden
    for refused in (rewritten, unread):
        assert isinstance(refused, ToolFailure), refused
        assert "is part of long-term memory" in refused, refused
        assert "This work is for the app orchard-planner" in refused, refused
        assert PREFERENCE not in refused
    assert PREFERENCE not in hidden
    assert prefs.read_text() == original and store.get(view).version == 1
    assert PREFERENCE in incognito and PREFERENCE in ordinary
