"""A child PersonalClaw starts resolves its programs on the PATH the gateway started with.

🔴 Before: a stdio tool server of the owner's own started with a copy of the gateway's environment
as it was at that moment. The first transcription had put ``/opt/homebrew/bin`` in front of that
``PATH`` (it held an ffmpeg), so a server whose command was ``uvx`` resolved one the owner never had
on their ``PATH``. The agent's own shell kept the owner's ``PATH``, which is how it showed: the
fault was in the environment every child is copied from.

Now a child's ``PATH`` is the one the gateway started with (``env.startup_path``), plus what its
spawner adds on purpose, whatever has changed ``os.environ`` since. And no code PersonalClaw runs
changes it: not to find a program, and not to mirror a stored secret that happens to be named
after a variable that decides which programs run.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def started(tmp_path, monkeypatch):
    """The ``PATH`` this process started with: one folder of the test's own."""
    folder = tmp_path / "started" / "bin"
    folder.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(folder))
    return str(folder)


@pytest.mark.asyncio
async def test_a_tool_server_started_after_a_transcription_gets_the_startup_path(started, tmp_path):
    from personalclaw.env import augmented_path
    from personalclaw.mcp_discovery import stdio_spawn_env
    from personalclaw.stt.provider import TranscriptResult
    from personalclaw.transcribe import transcribe_audio_detailed

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF" + b"\0" * 40)
    prov = MagicMock()
    prov.transcribe_detailed = AsyncMock(return_value=TranscriptResult(text="hello"))
    with (
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": True},
        ),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.stt.registry.active_stt", return_value=(prov, "stt-v1")),
    ):
        await transcribe_audio_detailed(str(clip))

    assert stdio_spawn_env({}, server="notes")["PATH"] == augmented_path(started)


def test_an_in_process_change_to_path_never_reaches_a_child(started, tmp_path, monkeypatch):
    """Whatever changes ``os.environ["PATH"]`` after the start (a library, say), the children are
    built from the ``PATH`` the process started with."""
    from personalclaw import env
    from personalclaw.mcp_discovery import stdio_spawn_env
    from personalclaw.sandbox import build_child_env

    env.record_startup_environment()
    elsewhere = str(tmp_path / "elsewhere" / "bin")
    monkeypatch.setenv("PATH", elsewhere + os.pathsep + started)

    assert env.startup_path() == started
    assert env.gateway_env()["PATH"] == started
    assert build_child_env(site="hook")["PATH"] == started
    assert stdio_spawn_env({}, server="notes")["PATH"] == env.augmented_path(started)
    # What a server sets for itself still goes in front, as it always has.
    own = stdio_spawn_env({"PATH": "/opt/notes/bin"}, server="notes")["PATH"]
    assert own == "/opt/notes/bin" + os.pathsep + env.augmented_path(started)


def test_the_start_is_recorded_once(started, tmp_path, monkeypatch):
    from personalclaw import env

    env.record_startup_environment()
    monkeypatch.setenv("PATH", str(tmp_path / "later"))
    env.record_startup_environment()

    assert env.startup_path() == started


def test_a_process_that_started_with_no_path_gives_its_children_none(started, monkeypatch):
    from personalclaw import env

    monkeypatch.delenv("PATH")
    env.record_startup_environment()
    monkeypatch.setenv("PATH", "/added/later")

    assert env.startup_path() is None
    assert "PATH" not in env.gateway_env()


def test_a_secret_named_path_is_stored_and_never_becomes_the_process_path(started, unset_env):
    from personalclaw.config.credentials import get_credential, save_credentials

    unset_env("EXAMPLE_SERVICE_TOKEN")
    save_credentials({"PATH": "/srv/secret-bin", "EXAMPLE_SERVICE_TOKEN": "tok-example"})

    assert os.environ["PATH"] == started
    assert get_credential("PATH") == "/srv/secret-bin"
    # The control: an ordinary named credential is still mirrored for the children that read it.
    assert os.environ["EXAMPLE_SERVICE_TOKEN"] == "tok-example"


def test_a_stored_secret_that_decides_which_code_runs_is_not_loaded_into_the_process(unset_env):
    from personalclaw.config.credentials import save_credentials
    from personalclaw.config.loader import AppConfig

    unset_env("NODE_OPTIONS", "EXAMPLE_SERVICE_TOKEN")
    save_credentials({"NODE_OPTIONS": "--require /srv/x.js", "EXAMPLE_SERVICE_TOKEN": "tok"})
    unset_env("NODE_OPTIONS", "EXAMPLE_SERVICE_TOKEN")

    creds = AppConfig().load_credentials()

    assert creds["NODE_OPTIONS"] == "--require /srv/x.js"
    assert "NODE_OPTIONS" not in os.environ
    assert os.environ["EXAMPLE_SERVICE_TOKEN"] == "tok"


@pytest.fixture
def scratch_tmpdir(tmp_path, monkeypatch):
    """The gateway's TMPDIR, a folder of the test's own, and a PATH a shell runs on."""
    folder = tmp_path / "gateway-tmp"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("TMPDIR", f"{folder}/")
    return f"{folder}/"


async def _agent_shell_says(tmp_path, command: str) -> str:
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    result = await NativeBuiltinToolProvider(tmp_path).invoke("bash", {"command": command})
    assert result.success, result.error
    return str(result.output).strip()


_PYTHON_SCRATCH = "python3 -c 'import tempfile; print(tempfile.gettempdir())'"


@pytest.mark.asyncio
async def test_the_agents_shell_keeps_its_scratch_files_in_the_gateways_tmpdir(
    scratch_tmpdir, tmp_path
):
    """The agent's command (``bash -lc``, in the sandbox, under the resource ceiling) is handed the
    TMPDIR the gateway runs with, and what reads it keeps its scratch files there."""
    assert await _agent_shell_says(tmp_path, 'printf "%s" "$TMPDIR"') == scratch_tmpdir
    assert await _agent_shell_says(tmp_path, _PYTHON_SCRATCH) == scratch_tmpdir.rstrip("/")


@pytest.mark.asyncio
async def test_an_in_process_change_to_tmpdir_never_reaches_the_agents_shell(
    scratch_tmpdir, tmp_path, monkeypatch
):
    """TMPDIR is held to the start the way PATH is: whatever changes ``os.environ["TMPDIR"]``
    later, a child gets the one the gateway started with."""
    from personalclaw import env

    env.record_startup_environment()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "changed-later"))

    assert await _agent_shell_says(tmp_path, 'printf "%s" "$TMPDIR"') == scratch_tmpdir
    assert env.gateway_env()["TMPDIR"] == scratch_tmpdir


_AFTER_A_RESTART = """
import json, os
from personalclaw import env, restart_request
from personalclaw.sandbox import build_child_env

env.record_startup_environment()
os.environ["PATH"] = "/opt/example/bin" + os.pathsep + os.environ["PATH"]
again = restart_request.request_restart(auth_mode="none")
print(json.dumps({
    "launched_with": env.startup_path(),
    "child": build_child_env(site="hook")["PATH"],
    "restart_again": again.env["PATH"],
    "runtime_only": "EXAMPLE_RUNTIME_ONLY" in again.env,
}))
"""


def test_a_restart_starts_from_the_environment_the_gateway_was_launched_with(
    started, tmp_path, monkeypatch, unset_env
):
    """🔴 Red before: the in-app Restart re-execs the gateway in place with ``os.environ`` as it
    then stood, so a PATH widened while it ran became the new process's launch environment, and
    every child it built inherited the widening; each restart kept what the last one had.

    Now a restart starts the new image with the environment the gateway was launched with (plus
    the sign-in mode it pins), so the new process records that same launch, and its children and
    its own next restart are built from it: nothing a running gateway changed carries over."""
    import json
    import subprocess
    import sys

    from personalclaw import env, restart_request, shutdown_event

    unset_env("EXAMPLE_RUNTIME_ONLY")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pchome"))
    env.record_startup_environment()
    launched = dict(os.environ)
    monkeypatch.setenv("PATH", "/opt/example/bin" + os.pathsep + started)
    os.environ["EXAMPLE_RUNTIME_ONLY"] = "set while it ran"
    monkeypatch.setattr(restart_request, "_pending", None)
    try:
        request = restart_request.request_restart(auth_mode="none")
    finally:
        shutdown_event.clear()

    assert request.env == {**launched, "PERSONALCLAW_AUTH_MODE": "none"}

    # The new image, started the way `restart_request.start` starts it: that environment, the
    # same interpreter. It changes its PATH as the old one did, and restarts again.
    done = subprocess.run(
        [sys.executable, "-c", _AFTER_A_RESTART],
        env=request.env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {
        "launched_with": started,
        "child": started,
        "restart_again": started,
        "runtime_only": False,
    }
