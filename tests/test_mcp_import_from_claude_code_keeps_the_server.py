"""Import from Claude Code keeps the server, across a restart, and no value reaches the browser.

🔴 THE DEFECT (measured on ``origin/main``). The Tools page's Import posted ``POST /api/mcp/apply``
with ``{personalclaw: true, globalMcp: false, ccGlobal: true}``. ``personalclaw: true`` copied the
Claude Code spec into ``mcp.json``; ``globalMcp: false`` then removed it again from
``_GLOBAL_MCP_JSON``, which UT3 had pointed at the very same file
(``_GLOBAL_MCP_JSON = _canonical_mcp_json()``). The request answered 200 with
``{"personalclaw": "added", "globalMcp": "removed"}``, ``mcp.json`` was left empty, the values #3617
stored on the way in were deleted with the server, and the row stayed under "Discovered in other
tools". ``test_mcp_apply.py``'s import test passed throughout: it patched ``_GLOBAL_MCP_JSON`` to a
SECOND file, a wiring production does not have.

So nothing here is patched. Each step runs in its own interpreter with ``PERSONALCLAW_HOME`` and
``HOME`` set before ``personalclaw`` is imported, as a gateway starts, so every import-time path
resolves the way it does in production. The second process IS the restart: a fresh interpreter that
runs the gateway's boot steps and then lists the server, spawns it and asks it for its environment.

And ``GET /api/mcp/importable`` returned each Claude Code server's ``env`` (and a remote server's
``headers``) VALUES, tokens included, to a page that never shows them. The import reads Claude
Code's file server-side, so the listing needs only each variable's NAME and whether it has a value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from personalclaw.mcp_client import mcp_sdk_available

NAME = "cc-echo"
REMOTE = "cc-remote"
TOKEN = "ghp_fixtureClaudeCodeImportToken0123456789"
HEADER_TOKEN = "Bearer fixture-claude-code-remote-header-9f8e7d6c"
LEVEL = "fixture-verbose-level"
# A multi-line secret (a PEM key) — the shape `.env` could not hold, which #3617 left inline.
PEM = (
    "-----BEGIN PRIVATE KEY-----\nMIIfixtureLineOne0123\nMIIfixtureLineTwo4567\n"
    "-----END PRIVATE KEY-----"
)

pytestmark = pytest.mark.skipif(not mcp_sdk_available(), reason="requires the 'mcp' SDK extra")

# A stdio MCP server whose one tool reports a variable of the environment it was started with.
_ECHO_SERVER = textwrap.dedent("""
    import os
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("echo")

    @mcp.tool(description="report one variable of this server's environment")
    def read_env(name: str) -> str:
        return os.environ.get(name, "<unset>")

    if __name__ == "__main__":
        mcp.run()
    """)

# One gateway "boot" per invocation. `import` lists, imports the way the Tools page does, and lists
# again; `restart` runs the boot steps a gateway runs, then lists, and spawns the server through
# the native client (the path an agent's tool call takes) to read what it was started with.
_DRIVER = textwrap.dedent("""
    import asyncio, json, sys

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    STEP, NAME = sys.argv[1], sys.argv[2]


    async def main():
        from personalclaw.dashboard.handlers import mcp as h

        if STEP == "restart":
            from personalclaw.agent import rebuild_agent_config
            from personalclaw.config.secret_refs import migrate_plaintext_secrets

            rebuild_agent_config()
            migrate_plaintext_secrets()
            h._migrate_legacy_mcp_json()

        app = web.Application()
        app["state"] = type("State", (), {"_background_tasks": set()})()
        app.router.add_get("/api/mcp", h.api_mcp_servers)
        app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
        app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
        out = {}
        async with TestClient(TestServer(app)) as c:
            out["importable_body"] = await (await c.get("/api/mcp/importable")).text()
            if STEP == "import":
                change = {"name": NAME, "personalclaw": True, "ccGlobal": True}
                resp = await c.post("/api/mcp/apply", json={"changes": [change]})
                out["apply_status"] = resp.status
                out["apply"] = await resp.json()
            out["listed"] = [s["name"] for s in await (await c.get("/api/mcp")).json()]
            after = await (await c.get("/api/mcp/importable")).json()
            out["importable_after"] = [s["name"] for s in after["servers"]]
            await asyncio.gather(*app["state"]._background_tasks, return_exceptions=True)

        if STEP == "restart":
            from personalclaw.mcp_client import McpClientRegistry, _personalclaw_mcp_specs

            reg = McpClientRegistry()
            try:
                reg.load_from_specs(_personalclaw_mcp_specs())
                conn = reg.get(NAME)
                out["spawned_env"] = {}
                for var in ("FIXTURE_TOKEN", "FIXTURE_PEM", "LOG_LEVEL"):
                    ok, value = await conn.call_tool("read_env", {"name": var})
                    out["spawned_env"][var] = value if ok else f"<call failed: {value}>"
            finally:
                await reg.shutdown_all()
        print("RESULT " + json.dumps(out))


    asyncio.run(main())
    """)


@pytest.fixture
def world(tmp_path):
    """A PersonalClaw home, a fake user home holding a FIXTURE Claude Code config, and the env a
    gateway process gets. The real ``~/.claude.json`` is never read: ``HOME`` is the fixture."""
    home = tmp_path / "pclaw-home"
    user = tmp_path / "user-home"
    home.mkdir()
    user.mkdir()
    server = tmp_path / "echo_server.py"
    server.write_text(_ECHO_SERVER, encoding="utf-8")
    claude = user / ".claude.json"
    claude.write_text(
        json.dumps(
            {
                "numStartups": 7,
                "projects": {"/work": {"allowedTools": []}},
                "mcpServers": {
                    NAME: {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(server)],
                        "env": {"FIXTURE_TOKEN": TOKEN, "FIXTURE_PEM": PEM, "LOG_LEVEL": LEVEL},
                    },
                    REMOTE: {
                        "type": "http",
                        "url": "https://mcp.example.invalid/mcp",
                        "headers": {"Authorization": HEADER_TOKEN},
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    env.update(
        PERSONALCLAW_HOME=str(home),
        HOME=str(user),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    env.pop("CLAUDE_CONFIG_DIR", None)
    return {"home": home, "claude": claude, "driver": driver, "env": env, "cwd": tmp_path}


def _boot(world, step: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(world["driver"]), step, NAME],
        env=world["env"],
        cwd=world["cwd"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, (
        f"the {step} process failed (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n"
        f"{proc.stderr[-4000:]}"
    )
    return json.loads(lines[-1][len("RESULT ") :])


def _servers(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})


def test_an_imported_server_is_kept_and_still_runs_after_a_restart(world) -> None:
    claude_before = world["claude"].read_bytes()

    first = _boot(world, "import")
    assert first["apply_status"] == 200, first
    assert NAME in first["listed"], f"the import did not keep the server: {first['apply']}"
    assert NAME not in first["importable_after"], "an imported server is still offered for import"

    mcp_json = world["home"] / "mcp.json"
    spec = _servers(mcp_json).get(NAME)
    assert spec is not None, f"mcp.json has no {NAME!r} after the import: {first['apply']}"
    for var in ("FIXTURE_TOKEN", "FIXTURE_PEM"):
        assert spec["env"][var].startswith("{{secret:"), f"{var} is not a reference: {spec}"
    raw = mcp_json.read_text(encoding="utf-8")
    assert TOKEN not in raw and "MIIfixtureLineOne0123" not in raw, "a secret stayed in mcp.json"
    agent_copy = _servers(world["home"] / "agents" / "personalclaw.json").get(NAME)
    assert agent_copy is not None, "the rebuild did not copy the imported server into the agent"
    assert world["claude"].read_bytes() == claude_before, "importing modified Claude Code's file"

    second = _boot(world, "restart")
    assert NAME in second["listed"], "the imported server is gone after a restart"
    assert NAME not in second["importable_after"]
    assert second["spawned_env"] == {
        "FIXTURE_TOKEN": TOKEN,
        "FIXTURE_PEM": PEM,
        "LOG_LEVEL": LEVEL,
    }, "after the restart the server did not start with the values Claude Code gave it"
    assert world["claude"].read_bytes() == claude_before


def test_the_import_listing_sends_names_and_never_a_value(world) -> None:
    first = _boot(world, "import")
    body = first["importable_body"]
    for secret in (TOKEN, HEADER_TOKEN, "MIIfixtureLineOne0123", LEVEL):
        assert (
            secret not in body
        ), f"GET /api/mcp/importable sent a value to the browser: {secret!r}"
    servers = {s["name"]: s for s in json.loads(body)["servers"]}
    assert servers[NAME]["env"] == [
        {"name": "FIXTURE_TOKEN", "hasValue": True},
        {"name": "FIXTURE_PEM", "hasValue": True},
        {"name": "LOG_LEVEL", "hasValue": True},
    ]
    assert servers[REMOTE]["headers"] == [{"name": "Authorization", "hasValue": True}]
    # What a user needs to recognise the server is still there.
    assert servers[NAME]["command"] == sys.executable
    assert servers[REMOTE]["url"] == "https://mcp.example.invalid/mcp"
