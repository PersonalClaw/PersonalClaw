"""A Node CLI PersonalClaw starts keeps its compile cache in the home, not the temp folder.

Node writes the code it compiles to ``<temp>/node-compile-cache`` whenever a program turns the
cache on, which npm does at every start, so every agent CLI and MCP server run through ``npx``
left one outside the home. An ACP agent and an MCP stdio server now start with
``NODE_COMPILE_CACHE`` in ``<home>/installer-cache``. The CLI keeps its faster start; the cache
stays where the home's other installer droppings are, and goes with the home.

The end-to-end case launches a stub Node program through the real agent spawn (a script of this
test's, never an agent CLI): it turns the cache on the way npm does and reports where Node put it.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.transport import AcpProcess
from personalclaw.mcp_discovery import stdio_spawn_env

NODE = shutil.which("node")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    return root


def _cache(home: Path) -> Path:
    return home / "installer-cache" / "node-compile-cache"


async def _env_of_agent(agent: AcpProcess) -> dict:
    with (
        patch("personalclaw.sandbox_providers.none.wrap_argv", return_value=(["/bin/echo"], None)),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
        patch("personalclaw.session._track_pid"),
        patch("personalclaw.session._track_session_pid"),
    ):
        proc = MagicMock()
        proc.pid, proc.returncode = 12345, None
        proc.stderr = MagicMock()
        proc.stderr.readline = AsyncMock(return_value=b"")
        spawn.return_value = proc
        await agent.spawn()
        return spawn.call_args.kwargs["env"]


@pytest.mark.asyncio
async def test_an_agent_cli_starts_with_its_compile_cache_in_the_home(home, tmp_path):
    env = await _env_of_agent(AcpProcess(command=["/bin/echo"], work_dir=tmp_path))

    assert env["NODE_COMPILE_CACHE"] == str(_cache(home))
    assert stat.S_IMODE(_cache(home).stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_an_agents_own_configured_environment_still_wins(home, tmp_path):
    agent = AcpProcess(
        command=["/bin/echo"], work_dir=tmp_path, extra_env={"NODE_COMPILE_CACHE": "/elsewhere"}
    )
    assert (await _env_of_agent(agent))["NODE_COMPILE_CACHE"] == "/elsewhere"


@pytest.mark.parametrize("app_server", [False, True])
def test_an_mcp_server_starts_with_its_compile_cache_in_the_home(home, monkeypatch, app_server):
    monkeypatch.setattr(
        "personalclaw.apps.mcp_bridge.server_app", lambda _name: "an-app" if app_server else None
    )
    assert stdio_spawn_env({}, server="notes")["NODE_COMPILE_CACHE"] == str(_cache(home))
    own = stdio_spawn_env({"NODE_COMPILE_CACHE": "/its-own"}, server="notes")
    assert own["NODE_COMPILE_CACHE"] == "/its-own"


_STUB = """
const m = require('module');
m.enableCompileCache();                     // what npm does at every start
require('./lib.js');
require('fs').writeFileSync(process.argv[2], String(m.getCompileCacheDir()));
m.flushCompileCache();
"""


@pytest.mark.skipif(NODE is None, reason="needs node")
@pytest.mark.asyncio
async def test_a_stub_node_cli_started_as_an_agent_compiles_into_the_home(
    home, tmp_path, monkeypatch
):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(temp))
    work = tmp_path / "work"
    work.mkdir()
    (work / "stub.js").write_text(_STUB, encoding="utf-8")
    (work / "lib.js").write_text("module.exports = () => 42;\n", encoding="utf-8")
    report = work / "cache-dir.txt"

    agent = AcpProcess(command=[NODE, str(work / "stub.js"), str(report)], work_dir=work)
    with patch("personalclaw.session._track_pid"), patch("personalclaw.session._track_session_pid"):
        await agent.spawn()
        await asyncio.wait_for(agent._process.wait(), timeout=30)

    assert report.read_text().startswith(str(_cache(home))), report.read_text()
    assert any(_cache(home).rglob("*")), "Node wrote no compile cache into the home"
    assert not (temp / "node-compile-cache").exists(), "the compile cache went to the temp folder"
