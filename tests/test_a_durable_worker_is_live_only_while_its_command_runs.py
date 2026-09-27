"""A durable worker is live while its command runs, and not a moment longer — against real tmux.

`new_session` returned True whenever tmux answered 0, and tmux answers 0 to a `new-session` whose
command exits at once. `has_session` read `tmux has-session`, which is 0 while the server holds the
session, and a user whose own tmux configuration keeps panes after their command exits
(`remain-on-exit on`) keeps a finished worker's session there indefinitely: the provisioning loop
then waited out its whole timeout on a step that had ended, and the boot sweep counted the dead
worker as live work. Two more things the same call let through: a workspace that is not a
directory (tmux starts the session in `$HOME` instead), and an argv element ending in `;`, which
tmux reads as the end of the command, so what followed it ran as a tmux command.

Now the panes are asked (`#{pane_dead}`), the worker's pane is told not to remain whatever the
user's configuration says, a missing workspace is refused, and every argv element reaches the
command as written.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from personalclaw import tmux_substrate
from personalclaw.workflows import provisioning, worktrees

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("tmux") is None, reason="needs the tmux binary"),
]


@pytest.fixture
def home(monkeypatch):
    """A short home (its socket path must fit), whose tmux server reads a user configuration
    that keeps every pane after its command exits."""
    root = Path(tempfile.mkdtemp(prefix="pcw-", dir="/tmp")).resolve()
    user = root / "user"
    user.mkdir()
    (user / ".tmux.conf").write_text("set -g remain-on-exit on\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    try:
        yield root
    finally:
        tmux_substrate.kill_server(root)
        shutil.rmtree(root, ignore_errors=True)


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(tmux_substrate._argv(*args), capture_output=True, text=True, timeout=10)


def _sessions() -> list[str]:
    return _tmux("list-sessions", "-F", "#{session_name}").stdout.split()


async def _until(predicate, *, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def test_a_command_that_has_already_exited_is_not_a_live_session(home):
    ws = home / "ws"
    ws.mkdir()
    name = tmux_substrate.durable_session_name("proj", "run", "exits")

    assert await tmux_substrate.new_session(name, workspace=str(ws), command=["false"]) is False
    assert not await tmux_substrate.has_session(name)
    assert await _until(lambda: name not in _sessions()), "the dead worker stayed on the server"


async def test_a_session_kept_after_its_command_exited_reads_dead_everywhere(home):
    """A session some earlier gateway opened without turning `remain-on-exit` off."""
    ws = home / "ws"
    ws.mkdir()
    name = tmux_substrate.durable_session_name("proj", "run", "kept")
    assert _tmux("new-session", "-d", "-s", name, "-c", str(ws), "true").returncode == 0
    assert await _until(
        lambda: _tmux("list-panes", "-s", "-t", f"={name}", "-F", "#{pane_dead}").stdout.strip()
        == "1"
    ), "the user configuration did not keep the pane (the premise of this test)"

    assert name in _sessions()
    assert not await tmux_substrate.has_session(name)
    assert not tmux_substrate.has_session_sync(name)
    assert all(session != name for session, _ in tmux_substrate.pane_paths_sync())


async def test_a_running_worker_is_live_and_marked_a_worker(home):
    ws = home / "ws"
    ws.mkdir()
    name = tmux_substrate.durable_session_name("proj", "run", "runs")

    assert await tmux_substrate.new_session(name, workspace=str(ws), command=["sleep", "60"])
    assert await tmux_substrate.has_session(name) and tmux_substrate.has_session_sync(name)
    assert (name, str(ws)) in tmux_substrate.pane_paths_sync()
    assert (name, tmux_substrate.WORKER_KIND) in await tmux_substrate.list_sessions()


async def test_an_argument_ending_in_a_semicolon_reaches_the_command_as_written(home):
    ws = home / "ws"
    ws.mkdir()
    name = tmux_substrate.durable_session_name("proj", "run", "semicolons")
    script = 'printf "%s|" "$@" > out; sleep 60'
    argv = ["/bin/sh", "-c", script, "sh", "a;", ";", "set-option", "@pclaw_kind", "owned"]

    assert await tmux_substrate.new_session(name, workspace=str(ws), command=argv)
    assert await _until(lambda: (ws / "out").exists())
    assert (ws / "out").read_text() == "a;|;|set-option|@pclaw_kind|owned|"
    assert (
        name,
        tmux_substrate.WORKER_KIND,
    ) in await tmux_substrate.list_sessions(), "an argument of the command ran as a tmux command"


async def test_a_workspace_that_is_not_a_directory_is_refused(home):
    name = tmux_substrate.durable_session_name("proj", "run", "nowhere")
    probe = home / "user" / "ran-here"
    argv = ["/bin/sh", "-c", f"pwd > {probe}; sleep 60"]

    assert not await tmux_substrate.new_session(name, workspace=str(home / "gone"), command=argv)
    await asyncio.sleep(0.3)
    assert not probe.exists(), "the step ran in $HOME instead of its workspace"
    assert name not in _sessions()


async def test_a_durable_step_that_finished_before_it_was_asked_about_runs_once(home, monkeypatch):
    """`new_session` reads False for a step that finished before it asked. The step's own status
    file is the result then: falling back to a bare run did the step a second time."""
    monkeypatch.setattr(provisioning, "_durable_enabled", lambda: True)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent / "src"))
    ws = home / "ws"
    ws.mkdir()
    (ws / "step.py").write_text("open('ran', 'a').write('x')\n", encoding="utf-8")
    command = f"{sys.executable} step.py"
    rc = (ws / worktrees.setup_marker(command)).with_suffix(".rc")
    spawn = tmux_substrate.new_session

    async def _asked_after_it_finished(*args, **kwargs):
        # The real spawn, answered the way it answers a step that was quicker than its check.
        await spawn(*args, **kwargs)
        assert await _until(rc.exists), "the durable step never wrote its status"
        return False

    monkeypatch.setattr(tmux_substrate, "new_session", _asked_after_it_finished)
    name = tmux_substrate.durable_session_name("proj", "run", "once")

    ok, detail = await provisioning.run_step(command, ws, env={}, durable_session=name)

    assert ok, detail
    assert (ws / "ran").read_text() == "x", "the step ran twice"
