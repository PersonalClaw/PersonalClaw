"""An app update says a restart is needed while an agent session still runs one of its servers.

An agent CLI — an ACP session (Claude Code, Codex, Kiro…) — starts the MCP servers its own
configuration names when the session starts, as its own child processes, and keeps each one for
the session's life. When an app is unloaded (an update, a disable, an uninstall), #3645 closes the
servers the gateway's own MCP client spawned; the agent's are not the gateway's to close. Measured
on origin/main 33d20e10f with a probe whose MCP server runs from the app's directory,
started by an agent CLI spawned through the real ACP transport: the update v1 → v2 answered
``restart_required: false`` and the session's server went on running v1's file.

The contract now: the unload looks for any process of the gateway's tree still running the app's
files once everything it stops has stopped, and each is a restart reason (#3645's one signal). One
under an agent session says the session keeps it until it restarts. The control proves the
processes the unload does stop — the backend, the worker, the gateway's own MCP server — are not
reported.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterator

import pytest
from mcp_owner_allowed import allow_configured

from personalclaw.apps import manager
from personalclaw.providers import registry as registry_module

APP = "agent-probe"

_MCP_SERVER_PY = '''
"""The probe's MCP server, answering with its version."""

from mcp.server.fastmcp import FastMCP

VERSION = {version!r}
server = FastMCP("agent-probe")


@server.tool()
def probe_version() -> str:
    """Which version of the probe's MCP server answers."""
    return VERSION


if __name__ == "__main__":
    server.run()
'''

_BACKEND_PY = """\
import http.server
import os

http.server.ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ["PORT"])), http.server.BaseHTTPRequestHandler
).serve_forever()
"""

_WORKER_PY = """\
import time

while True:
    time.sleep(0.05)
"""

# An agent CLI, as far as this test needs one: it starts the server its own configuration names
# (here: the probe's, from the probe's directory) and keeps it for as long as its session lives.
_AGENT_CLI_PY = """\
import os
import subprocess
import sys
import time

subprocess.Popen(
    [sys.executable, "mcp_server.py"], cwd=os.environ["PROBE_APP_DIR"], stdin=subprocess.PIPE
)
time.sleep(600)
"""


def _source(root: Path, version: str, *, processes: bool = False) -> Path:
    d = root / f"src-{version}-{processes}" / APP
    d.mkdir(parents=True)
    (d / "mcp_server.py").write_text(textwrap.dedent(_MCP_SERVER_PY).format(version=version))
    manifest: dict = {
        "name": APP,
        "version": f"{version.lstrip('v')}.0.0",
        "displayName": "Agent Probe",
        "description": "An app whose MCP server an agent session may also run.",
        "mcpServers": {"version": {"command": sys.executable, "args": ["mcp_server.py"]}},
    }
    if processes:
        (d / "backend").mkdir()
        (d / "backend" / "server.py").write_text(_BACKEND_PY)
        (d / "worker.py").write_text(_WORKER_PY)
        manifest["backend"] = {"entryPoint": "backend/server.py", "type": "python", "port": "auto"}
        manifest["permissions"] = {"backgroundTasks": True, "storage": True}
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """An isolated home (the ACP transport's pid files included) and app children allowed."""
    import personalclaw.config.loader as loader
    from personalclaw import mcp_client

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_WORKERS", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)
    monkeypatch.setattr(mcp_client, "_registry", None)
    registry_module.reset_provider_registry()
    yield tmp_path
    from personalclaw.apps import app_manager, app_runtime
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor

    app_runtime.unload(APP, app_manager._manifest_of(APP), forget=True)
    get_backend_supervisor().unhold(APP)
    get_worker_supervisor().unhold(APP)
    live = mcp_client._registry
    if live is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(live.shutdown_all())
        finally:
            loop.close()
    registry_module.reset_provider_registry()


def _install(home: Path, **probe: bool) -> None:
    from personalclaw.apps import app_manager

    result = app_manager.install(_source(home, "v1", **probe), confirm=True)
    assert result.ok, result.error


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


async def _agent_session(home: Path):
    """An agent CLI spawned the way the gateway spawns one (the ACP transport, which records it as
    this gateway's agent process), once it has started the probe's server."""
    from personalclaw.acp.transport import AcpProcess

    cli = home / "agent_cli.py"
    cli.write_text(_AGENT_CLI_PY)
    agent = AcpProcess(
        command=[sys.executable, str(cli)],
        work_dir=home / "workspace",
        sandbox_mode="off",
        extra_env={"PROBE_APP_DIR": str(manager.app_dir(APP))},
    )
    await agent.spawn()
    assert agent.pid is not None
    for _ in range(100):
        servers = _children(agent.pid)
        if servers:
            return agent, servers
        await asyncio.sleep(0.05)
    await agent.kill(force=True)
    raise AssertionError("the agent CLI never started the probe's server")


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["update", "disable"])
async def test_an_agent_sessions_own_server_of_the_app_is_a_restart_reason(home, step):
    from personalclaw.apps import app_manager, app_runtime

    _install(home)
    agent, servers = await _agent_session(home)
    try:
        if step == "update":
            result = await asyncio.to_thread(
                app_manager.update, _source(home, "v2"), APP, confirm=True
            )
            assert result.ok, result.error
            reason = result.restart_reason
            assert (
                result.restart_required is True
            ), "the update said nothing, and the agent session runs the old server"
        else:
            assert await asyncio.to_thread(app_manager.disable, APP)
            reason = app_runtime.restart_reason(APP)
        assert "agent session" in reason and "until that session restarts" in reason, reason
        assert f"pid {servers[0]}" in reason, reason
        assert all(_alive(p) for p in servers), "the session's server was killed under it"
    finally:
        await agent.kill(force=True)


@pytest.mark.asyncio
async def test_what_the_unload_stops_itself_is_not_a_restart_reason(home):
    """The control: the backend, the worker and the MCP server the gateway's own client started
    all run from the app's directory too, and the unload stops each of them — so an update
    with no agent session holding anything needs no restart."""
    from personalclaw.apps import app_manager
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.mcp_client import get_mcp_client_registry

    _install(home, processes=True)
    allow_configured(f"{APP}:version")  # the owner's Allow on the Tools page
    registry = get_mcp_client_registry()
    assert registry is not None
    conn = registry.get(f"{APP}:version")
    assert conn is not None
    ok, output = await conn.call_tool("probe_version", {})
    assert ok and output == "v1", output
    backend = get_backend_supervisor().get(APP)
    worker = get_worker_supervisor().get(APP, "worker")
    # Vacuity floor: three processes of the app's are running from its directory before the update.
    assert backend is not None and worker is not None and worker.is_alive()

    result = await asyncio.to_thread(
        app_manager.update, _source(home, "v2", processes=True), APP, confirm=True
    )

    assert result.ok, result.error
    assert result.restart_reason == "", result.restart_reason
