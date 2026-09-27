"""The sandbox fences the owner-only NAMES in the home, not whichever files sit there at start.

Measured on `main` with PersonalClaw's own Linux launcher, run as an unprivileged user in a
Linux 6.18 container, and with its own macOS profile:

* **Linux, a missing file.** The launcher bound each owner-only path onto itself read-only, but
  only if it existed. With no ``config.json`` when the shell started, ``echo evil >
  <home>/config.json`` wrote it.
* **Linux, a replaced file.** A bind holds an inode, and the kernel dissolves a bind whose file
  is replaced. The gateway writes ``config.json`` by writing a new file and renaming it over the
  old one, so after the owner's first settings change the running shell wrote ``config.json``.
* **Both, the home moved aside.** The profile and the launcher name paths. ``mv <home> x; mkdir
  -p <home>/hooks; echo evil > <home>/config.json`` gave the gateway a new home under the old path
  on Linux. On macOS ``mv <home> x; echo evil > x/config.json; echo … > x/hooks/pre.sh; mv x
  <home>`` did the same without leaving a trace.

Now the Linux launcher binds the home itself read-only and binds each entry already in it back
writable except the owner-only ones, so an owner-only name is refused whether it exists or not and
however often it is replaced. A mount point cannot be renamed, so the home stays where it is, and
each folder above it that the owner could rename is bound onto itself. The macOS profile denies
writes to the home's own entry and to those folders' entries (a literal names the folder alone).
The price, on Linux only: the shell cannot add, remove or rename an entry at the top of the home.

With the sandbox off none of that applies, so the rest of this file checks that the two layers
that do not depend on it still hold: the screen that refuses a command naming one of these paths,
and the owner's yes that an agent hook waits for.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import personalclaw.config.loader as loader

_OWNER_ONLY = ["agents", "config.json", "grants", "hooks", "mcp.json"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home inside a folder that stands for the owner's `~`, with a workspace in it."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return pc


# ── what the fence is built from ──


def test_the_linux_fence_is_the_home_not_the_files_in_it(home):
    """🔴 Red on main: the launcher listed each owner-only PATH and bound it only if it existed."""
    from personalclaw.sandbox import _build_launcher_script

    real = os.path.realpath(home)
    for level in ("standard", "cc", "strict"):
        launcher = _build_launcher_script(level)
        compile(launcher, "<launcher>", "exec")
        assert f'OWNER_HOME = "{real}"' in launcher
        assert f"OWNER_ONLY_NAMES = {_OWNER_ONLY!r}".replace("'", '"') in launcher
        # The home is remounted read-only after its other entries are bound back writable.
        body = launcher[launcher.index("for target in PINNED:") :]
        assert body.index("os.listdir(OWNER_HOME)") < body.index("_MS_REMOUNT | _MS_RDONLY")
        # Nothing is made in the owner's home on their behalf any more.
        assert "OWNER_ONLY:" not in launcher and "for target, is_dir in" not in launcher


def test_the_folders_the_owner_could_rename_are_pinned(home):
    """🔴 Red on main: nothing stopped the home, or a folder above it, from being moved aside."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile, _pinned_dirs

    real = os.path.realpath(home)
    above = _pinned_dirs(realpath_only=True, include_home=False)
    # The owner owns `user/` and the test's tmp folder above it: both could be renamed.
    assert os.path.dirname(real) in above and real not in above
    assert "/" not in above and all(real.startswith(p + os.sep) for p in above)
    assert above == sorted(above, key=lambda p: p.count(os.sep)), "outermost first"
    assert f"PINNED = {above!r}".replace("'", '"') in _build_launcher_script("standard")

    profile = _build_seatbelt_profile("standard")
    assert f'(deny file-write* (literal "{real}"))' in profile
    for folder in above:
        assert f'(deny file-write* (literal "{folder}"))' in profile


def test_a_folder_the_owner_cannot_rename_is_left_alone(home, monkeypatch):
    """The floor: a folder whose parent the owner neither owns nor may write stays unpinned, so
    the profile does not refuse a `touch` of `/Users` or `/tmp` for nothing."""
    from personalclaw import sandbox

    real = os.path.realpath(home)
    monkeypatch.setattr(sandbox.os, "access", lambda p, mode: False)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: -1)
    assert sandbox._pinned_dirs(realpath_only=True, include_home=False) == []
    assert sandbox._pinned_dirs(realpath_only=True, include_home=True) == [real]


# ── the fence, driven ──


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS profile is measured here; the Linux launcher below",
)
def test_macos_the_home_cannot_be_moved_aside_and_back(home):
    """🔴 Red on main: the shell moved the home aside, wrote config.json and a hook script under
    the new path, and moved it back."""
    from personalclaw.sandbox import sandbox_exec_argv

    (home / "hooks").mkdir()
    (home / "config.json").write_text("{}")
    real = os.path.realpath(home)
    script = (
        f'H="{real}"; mv "$H" "$H-aside" || exit 3; '
        'echo evil > "$H-aside/config.json"; echo x > "$H-aside/hooks/pre.sh"; '
        'mv "$H-aside" "$H"'
    )
    argv, profile = sandbox_exec_argv(["/bin/sh", "-c", script], "standard")
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(profile)

    assert run.returncode == 3 and "Operation not permitted" in run.stderr
    assert (home / "config.json").read_text() == "{}"
    assert list((home / "hooks").iterdir()) == []


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS profile is measured here; the Linux launcher below",
)
def test_macos_the_shell_still_works_inside_the_home(home):
    """The floor: a literal names the folder alone, so what is inside it is untouched."""
    from personalclaw.sandbox import sandbox_exec_argv

    real = os.path.realpath(home)
    script = (
        f'cd "{real}/workspace" && echo x > a.txt && mkdir -p d/e && '
        f"python3 -c \"import os; os.makedirs('{real}/workspace/p', exist_ok=True)\""
    )
    argv, profile = sandbox_exec_argv(["/bin/sh", "-c", script], "standard")
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(profile)

    assert run.returncode == 0, run.stderr
    assert (home / "workspace" / "d" / "e").is_dir() and (home / "workspace" / "p").is_dir()


def _linux_sandbox_works() -> bool:
    if sys.platform != "linux":
        return False
    from personalclaw.sandbox import _probe_unshare

    return _probe_unshare()


def _launch(home: Path, command: str) -> subprocess.CompletedProcess:
    from personalclaw.sandbox import namespace_argv

    argv = namespace_argv(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(argv[1])


linux_only = pytest.mark.skipif(
    not _linux_sandbox_works(),
    reason="needs the Linux namespace sandbox (user + mount namespaces)",
)


@linux_only
def test_linux_a_missing_config_is_refused(home):
    """🔴 Red on main: with no config.json when the shell started, the shell wrote one."""
    run = _launch(home, f'echo evil > "{home}/config.json"')

    assert run.returncode != 0 and not (home / "config.json").exists()


@linux_only
def test_linux_a_replaced_config_stays_refused(home):
    """🔴 Red on main: the gateway's own save (a new file renamed over the old) dissolved the bind,
    and the shell then wrote config.json."""
    (home / "config.json").write_text("{}")

    def owner_saves() -> None:
        time.sleep(1.0)
        fd, tmp = tempfile.mkstemp(dir=home)
        os.write(fd, b'{"owner": 1}')
        os.close(fd)
        os.replace(tmp, home / "config.json")

    saver = threading.Thread(target=owner_saves)
    saver.start()
    run = _launch(home, f'sleep 2.5; echo evil > "{home}/config.json"')
    saver.join()

    assert run.returncode != 0
    assert (home / "config.json").read_text() == '{"owner": 1}'


@linux_only
def test_linux_the_home_cannot_be_moved_aside(home):
    """🔴 Red on main: the shell moved the home aside and made a new one under the same path."""
    (home / "config.json").write_text("{}")
    run = _launch(
        home, f'mv "{home}" "{home}-aside" && mkdir -p "{home}" && echo evil > "{home}/config.json"'
    )

    assert run.returncode != 0 and (home / "config.json").read_text() == "{}"


@linux_only
def test_linux_the_shell_still_works_in_the_homes_folders(home):
    """The floor, and the price. Writes inside the home's folders and to its existing files work;
    a new entry at the top of the home does not, on Linux."""
    (home / "notes.jsonl").write_text("")
    run = _launch(
        home,
        f'echo x > "{home}/workspace/a.txt" && mkdir -p "{home}/workspace/d/e" && '
        f'echo line >> "{home}/notes.jsonl"',
    )
    assert run.returncode == 0, run.stderr
    assert (home / "notes.jsonl").read_text() == "line\n"

    assert _launch(home, f'echo x > "{home}/new.txt"').returncode != 0


# ── with the sandbox off, the screen and the grants still hold ──


def test_with_the_sandbox_off_nothing_fences_the_shell(home):
    """The premise the two tests below rest on: `off` wraps nothing."""
    from personalclaw.sandbox import wrap_argv

    argv = ["bash", "-lc", "true"]
    assert wrap_argv(argv, mode="off") == (argv, None)


@pytest.mark.parametrize(
    "command",
    [
        "echo evil > {home}/config.json",
        "mkdir -p {home}/hooks && printf 'echo pwned' > {home}/hooks/x-pre.sh",
        "cp /dev/null {home}/agents/personalclaw.json",
        "cd {home} && echo '{{}}' > grants/agent_hooks.json",
    ],
)
def test_with_the_sandbox_off_the_shell_screen_still_refuses(home, command):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(home / "workspace", sandbox_mode="off")
    result = asyncio.run(tools.invoke("bash", {"command": command.format(home=home)}))

    assert not result.success and "an agent may not change it" in result.error
    assert not (home / "config.json").exists() and not (home / "hooks").exists()


def test_with_the_sandbox_off_a_tool_call_naming_one_is_refused(home):
    """The screen every approval path consults does not read the sandbox setting at all."""
    from personalclaw.hooks import HookManager

    for title in (f"Running: printf x > {home}/config.json", f"Write {home}/hooks/pre.sh"):
        assert HookManager().on_tool_call(title).action == "deny", title


def test_with_the_sandbox_off_a_hook_that_got_there_still_waits(home):
    """A command can always be spelled so no screen reads the path, and with the sandbox off
    nothing else refuses it. What it wrote still runs only on the owner's yes."""
    from personalclaw.agent import _apply_user_agent_hooks, user_hooks_waiting

    script = home / "hooks" / "audit-pre.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho ran\n")
    script.chmod(0o755)
    merged: dict = {"hooks": {}}
    _apply_user_agent_hooks(merged, {})

    assert [e for entries in merged["hooks"].values() for e in entries] == []
    assert [w["command"] for w in user_hooks_waiting()] == [str(script.resolve())]
