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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import namespace_sandbox
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


def _launch(home: Path, command: str) -> subprocess.CompletedProcess:
    from personalclaw.sandbox import namespace_argv

    argv = namespace_argv(["/bin/sh", "-c", command], "standard")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(argv[1])


linux_only = namespace_sandbox.needs_it()


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


# ── the security log's key, log and archive, in the home in use and the default one ──

#: What each home's key holds, so a read of it is seen in what a command printed.
_KEY = b"audit-key-0123456789abcdef-signs"
_ROW = '{"event_id": "e1", "entry_hash": "h1"}\n'
_ARCHIVED = '{"event_id": "e0", "entry_hash": "h0"}\n'
_LEVELS = ("standard", "cc", "strict")


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """A dev home in use beside the owner's default one, each with a security log: its key, the
    log, and one rotated file in its archive."""
    user = tmp_path / "user"
    active, default = user / "dev-home", user / ".personalclaw"
    for home in (active, default):
        (home / "workspace").mkdir(parents=True)
        (home / "sel_archive").mkdir()
        (home / "sel_spool").mkdir()
        (home / "sel_hmac.key").write_bytes(_KEY)
        (home / "security_events.jsonl").write_text(_ROW)
        (home / "sel_archive" / "security_events.20261101T000000.000000Z.jsonl").write_text(
            _ARCHIVED
        )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(active))
    monkeypatch.setattr(loader, "config_dir", lambda: active)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return active, default


def _sandboxed(level: str, command: str) -> subprocess.CompletedProcess:
    """*command* through the real wrap at *level*: the macOS profile, or the Linux launcher."""
    from personalclaw.sandbox import namespace_argv, sandbox_exec_argv

    if sys.platform == "darwin":
        argv, cleanup = sandbox_exec_argv(["/bin/sh", "-c", command], level)
    else:
        argv = namespace_argv(["/bin/sh", "-c", command], level)
        cleanup = argv[1]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(cleanup)


def _a_real_sandbox():
    if sys.platform == "darwin":
        return pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="needs sandbox-exec")
    return namespace_sandbox.needs_it()


real_sandbox = _a_real_sandbox()


@real_sandbox
@pytest.mark.parametrize("level", _LEVELS)
def test_a_sandboxed_child_cannot_read_the_audit_key(homes, level, tmp_path):
    """🔴 Red on main: at every level the child printed the key that signs the security log, in
    the home in use and in the default one, and could copy it out by a hard link, so a row it
    wrote passed the log's check. Now macOS refuses the read and the link, and Linux shows an
    empty file in the key's place."""
    for home in homes:
        key = home / "sel_hmac.key"
        read = _sandboxed(level, f'cat "{key}"; echo; ln "{key}" "{tmp_path}/{home.name}.key"')

        assert _KEY.decode() not in read.stdout, (home, level, read)
        copied = tmp_path / f"{home.name}.key"
        assert not copied.exists() or copied.read_bytes() != _KEY, (home, level)
        assert key.read_bytes() == _KEY


@real_sandbox
@pytest.mark.parametrize("level", _LEVELS)
def test_a_sandboxed_child_cannot_change_the_audit_log_or_its_archive(homes, level, tmp_path):
    """🔴 Red on main: the child appended a row to the security log, truncated it, replaced it,
    removed it, and did the same to the archive and to a file the log adopts into it, in both
    homes. Each is now refused, and the floor holds: the spool and the home's other files are still
    written."""
    for home in homes:
        log, archive = home / "security_events.jsonl", home / "sel_archive"
        archived = archive / "security_events.20261101T000000.000000Z.jsonl"
        (tmp_path / "forged.jsonl").write_text('{"event_id": "forged"}\n')
        for command in (
            f'echo forged >> "{log}"',
            f': > "{log}"',
            f'mv "{tmp_path}/forged.jsonl" "{log}"',
            f'rm -f "{log}"',
            f'echo forged >> "{archived}"',
            f'rm -f "{archived}"',
            f'mv "{archive}" "{home}/aside"',
            f'echo forged > "{archive}/security_events.planted.jsonl"',
            f'echo forged > "{home}/security_events.planted.bak.jsonl"',
            f'ln "{log}" "{home}/workspace/log"',
        ):
            run = _sandboxed(level, command)
            assert run.returncode != 0, (home, level, command)
        assert log.read_text() == _ROW, (home, level)
        assert archived.read_text() == _ARCHIVED, (home, level)
        assert sorted(p.name for p in archive.iterdir()) == [archived.name]
        assert not (home / "security_events.planted.bak.jsonl").exists()

    active = homes[0]
    floor = _sandboxed(
        level,
        f'echo row > "{active}/sel_spool/a.json" && echo x > "{active}/workspace/a.txt" && '
        f'cat "{active}/security_events.jsonl"',
    )
    assert floor.returncode == 0, floor.stderr
    assert floor.stdout == _ROW and (active / "sel_spool" / "a.json").read_text() == "row\n"


@pytest.mark.parametrize("level", _LEVELS)
def test_each_fence_holds_every_entry_the_security_log_declares(homes, level):
    """🔴 Red on main: neither the macOS profile nor the Linux launcher named the security log's
    key, log or archive. Both are built from one declaration (``owner_only.AUDIT_FENCED``), for
    each home it is fenced in, both spellings of each on macOS."""
    from personalclaw.owner_only import AUDIT_FENCED
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    profile = _build_seatbelt_profile(level)
    launcher = _build_launcher_script(level)
    compile(launcher, "<launcher>", "exec")
    reals = [os.path.realpath(home) for home in homes]
    assert (
        f"AUDIT_HOMES, AUDIT_KEY, AUDIT_NAMES, AUDIT_PATTERNS = [{json.dumps(reals)}, "
        '"sel_hmac.key", ["security_events.jsonl", "sel_archive"], '
        '["security_events.*.bak.jsonl"]]'
    ) in launcher
    for home in {*map(str, homes), *reals}:
        for entry in AUDIT_FENCED:
            path = os.path.join(home, entry.name)
            if entry.pattern:
                folder = (home + os.sep).replace(".", "\\.")
                rule = (
                    f'(deny file-write* (regex #"^{folder}security_events\\.[^/]*\\.bak\\.jsonl$"))'
                )
                assert rule in profile, rule
                continue
            kind = "subpath" if entry.is_dir else "literal"
            assert f'(deny file-write* ({kind} "{path}"))' in profile, path
            assert (f'(deny file-read* ({kind} "{path}"))' in profile) is entry.hidden, path
    # The default home, which is not the one in use, is pinned where it is, as the active one is.
    for spelling in {str(homes[1]), reals[1]}:
        assert f'(deny file-write* (literal "{spelling}"))' in profile
    # And each home's spool is held where it is, while the files in it are not.
    for home in {*map(str, homes), *reals}:
        spool = os.path.join(home, "sel_spool")
        assert f'(deny file-write* (literal "{spool}"))' in profile, spool
        assert f'(deny file-write* (subpath "{spool}"))' not in profile, spool


@real_sandbox
@pytest.mark.parametrize("level", _LEVELS)
def test_a_sandboxed_child_cannot_move_the_spool_and_still_writes_in_it(homes, level):
    """🔴 Red before this on macOS. The spool's own entry is held where it is, in each home, and
    made before a launch when it is missing, so the child still leaves its rows in it."""
    active = homes[0]
    (active / "sel_spool").rmdir()
    for home in homes:
        spool = home / "sel_spool"
        for command in (
            f'rm -rf "{spool}"',
            f'mv "{spool}" "{home}/aside"',
            f'rm -rf "{spool}"; ln -s "{home}/workspace" "{spool}"',
        ):
            _sandboxed(level, command)
            assert spool.is_dir() and not spool.is_symlink(), (home, level, command)
            assert not (home / "aside").exists(), (home, level, command)

    floor = _sandboxed(level, f'echo row > "{active}/sel_spool/b.json"')
    assert floor.returncode == 0, floor.stderr
    assert (active / "sel_spool" / "b.json").read_text() == "row\n"


@real_sandbox
@pytest.mark.parametrize("level", _LEVELS)
def test_what_a_child_does_to_the_spool_leaves_the_intake_inside_it(homes, level):
    """🔴 Red before this on macOS, at every level. Whatever the child does to the spool's entry
    through the real wrap, the gateway's next intake reads and removes nothing outside the
    spool."""
    from personalclaw import sel_intake

    active = homes[0]
    kept = active / "grants"
    kept.mkdir()
    (kept / "kept.json").write_text('{"not": "a row"}')
    spool = active / "sel_spool"
    _sandboxed(level, f'rm -rf "{spool}"; ln -s "{kept}" "{spool}"')

    sel_intake.take_in_spool()
    assert sorted(p.name for p in kept.iterdir()) == ["kept.json"], level
    assert (kept / "kept.json").read_text() == '{"not": "a row"}', level


def test_the_spool_is_made_before_a_launch_and_an_entry_there_is_left(homes, tmp_path):
    """🔴 Red before this: nothing made the spool before a launch, and a fence that holds its
    entry would leave a home that had none with nowhere for its rows. It is made now, the
    home's own; an entry already under its name is left as it is, for the gateway to judge."""
    from personalclaw.sandbox import _make_the_audit_spool

    active = homes[0]
    (active / "sel_spool").rmdir()
    _make_the_audit_spool()
    assert (active / "sel_spool").is_dir() and not (active / "sel_spool").is_symlink()
    assert (active / "sel_spool").stat().st_mode & 0o077 == 0

    (active / "sel_spool").rmdir()
    (active / "sel_spool").symlink_to(tmp_path, target_is_directory=True)
    _make_the_audit_spool()
    assert (active / "sel_spool").is_symlink()


#: The Linux launcher run on any host against a kernel stood in for: each mount call is written to
#: the log as ``[source, target, flags]`` and answered as done, except one onto a target the
#: config names to fail, which is refused (EPERM).
_UNDER_A_RECORDING_KERNEL = r"""
import builtins, ctypes, errno, json, os, sys, types

cfg = json.loads(sys.argv[1])


class _Mount:
    def __call__(self, source, target, _fstype, flags, _data):
        real = os.path.realpath(target.decode())
        with builtins.open(cfg["log"], "a") as log:
            print(json.dumps([source and source.decode(), real, flags]), file=log)
        if real in cfg["fail"]:
            ctypes.set_errno(errno.EPERM)
            return -1
        return 0


class RecordingKernel:
    def __init__(self, *_args, **_kwargs):
        self.unshare = lambda *_a: 0
        self.mount = _Mount()


def _open(path, *args, **kwargs):
    if str(path).startswith("/proc/"):
        return builtins.open(os.devnull, "w")
    return builtins.open(path, *args, **kwargs)


ctypes.CDLL = RecordingKernel
launcher = types.ModuleType("launcher_under_test")
launcher.open = _open
exec(compile(open(cfg["script"]).read(), cfg["script"], "exec"), launcher.__dict__)
launcher.RUNTIME_DIRS = cfg["runtime_dirs"]
launcher._home_device = lambda: None
sys.argv = [cfg["script"], *cfg["argv"]]
launcher.main()
"""

_MS_BIND, _MS_REMOUNT, _MS_RDONLY = 4096, 32, 1


def _launch_under_a_recording_kernel(tmp_path: Path, level: str, fail: list[str]):
    from personalclaw.sandbox import _build_launcher_script

    runtime = tmp_path / "run-user"
    runtime.mkdir(exist_ok=True)
    script = tmp_path / "launcher.py"
    script.write_text(_build_launcher_script(level), encoding="utf-8")
    log = tmp_path / "mounts.jsonl"
    log.unlink(missing_ok=True)
    cfg = {
        "script": str(script),
        "runtime_dirs": [str(runtime)],
        "log": str(log),
        "fail": fail,
        "argv": ["/bin/sh", "-c", "echo ran"],
    }
    run = subprocess.run(
        [sys.executable, "-c", _UNDER_A_RECORDING_KERNEL, json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    mounts = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return run, mounts, os.path.realpath(runtime)


@pytest.mark.parametrize("level", _LEVELS)
def test_the_linux_launcher_hides_each_key_and_binds_no_log_back(homes, level, tmp_path):
    """🔴 Red on main: the launcher bound the security log and its archive back writable with
    every other entry of the home in use, left the default home's open, and hid no key. Now each
    home's key has an empty file of the launch's bound over it, at every level; neither home's log
    or archive is bound back; and the default home is bound onto itself and made read-only."""
    run, mounts, runtime = _launch_under_a_recording_kernel(tmp_path, level, fail=[])
    assert run.returncode == 0 and run.stdout.strip() == "ran", run

    for home in map(os.path.realpath, homes):
        key = os.path.join(home, "sel_hmac.key")
        hides = [m for m in mounts if m[1] == key]
        assert len(hides) == 1 and hides[0][2] == _MS_BIND, (key, hides)
        assert hides[0][0].startswith(runtime + os.sep), hides
        bound_back = {m[1] for m in mounts if m[0] == m[1]}
        for name in ("security_events.jsonl", "sel_archive", "sel_hmac.key"):
            assert os.path.join(home, name) not in bound_back, (home, name)
        assert os.path.join(home, "workspace") in bound_back, home
        assert home in bound_back, f"{home} is not bound onto itself"
        remounts = [m for m in mounts if m[1] == home and m[2] & _MS_REMOUNT]
        assert remounts and remounts[-1][2] & _MS_RDONLY, (home, remounts)
    # The key is hidden before either home is bound onto itself, so each home's bind carries it.
    order = [m[1] for m in mounts]
    for home in map(os.path.realpath, homes):
        assert order.index(os.path.join(home, "sel_hmac.key")) < order.index(home)


def test_the_linux_launcher_stops_where_a_key_cannot_be_hidden(homes, tmp_path):
    """A key the launcher cannot hide stops the command: it never runs with the key readable."""
    key = os.path.join(os.path.realpath(homes[1]), "sel_hmac.key")
    run, _mounts, _runtime = _launch_under_a_recording_kernel(tmp_path, "standard", fail=[key])

    assert run.returncode != 0 and "ran" not in run.stdout, run
    assert "could not hide the security log's key" in run.stderr, run.stderr


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
