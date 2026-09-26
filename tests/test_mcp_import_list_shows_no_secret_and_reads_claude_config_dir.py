"""The import list says which server a row is and holds no credential; Claude Code is read
where it is.

🔴 THE DEFECTS (measured on ``origin/main``).

1. ``GET /api/mcp/importable`` sent each Claude Code server's full command, arguments and URL to the
   Tools page, which keeps the response in session storage. #3623 stopped it sending ``env`` and
   ``headers`` values; a token in an argument (``--api-key …``, ``--header "Authorization: Bearer
   …"``) or in a URL (``?token=…``, ``https://user:pw@…``, the secret path segment a hosted
   endpoint embeds) still went. ``GET /api/mcp`` did the same for every configured server: its
   command, arguments, URL and header references, none of which the page shows.
2. The MCP importer read ``Path.home() / ".claude.json"``, frozen at import, while the onboarding
   importer honours ``$CLAUDE_CONFIG_DIR``. With it set, the list came from a file Claude Code does
   not read, Import looked for the spec there too, and the Claude Code toggle wrote there.

Each gateway step runs in its own interpreter with ``HOME``, ``PERSONALCLAW_HOME`` and
``CLAUDE_CONFIG_DIR`` set before ``personalclaw`` is imported, as a gateway starts: the path that
was frozen at import is then the fixture's, and the real ``~/.claude.json`` is never read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from personalclaw.apps.secret_fields import SECRET_MASK as MASK

S_KEY = "sk-fixtureArgKey0123456789abcdefABCDEF"
S_TOKEN = "fixtureTokenFlagValue42"
S_ENV = "ghp_fixtureInlineEnvToken0123456789ab"
S_BEARER = "fixture.bearer.0123456789abcdefghij"
S_PASS = "fixtureUrlPassword77"
S_QUERY = "fixtureQueryToken99"
S_PATH = "YTk3ZjE4NmItOWE0Ny00ZjBlLTg0MWEtYzQ4MzZlZjM2ZDI3OjU1NDM"
S_APIKEY = "fixtureHostedApiKey55"
S_USER = "fixtureSsePassword31"
SECRETS = (S_KEY, S_TOKEN, S_ENV, S_BEARER, S_PASS, S_QUERY, S_PATH, S_APIKEY, S_USER)

ARGS = [
    "-y",
    "@fixture/github-mcp",
    "--api-key",
    S_KEY,
    f"--token={S_TOKEN}",
    f"GITHUB_TOKEN={S_ENV}",
    "--header",
    f"Authorization: Bearer {S_BEARER}",
    f"https://user:{S_PASS}@api.fixture.invalid/v1?token={S_QUERY}&region=eu",
    "--port",
    "8080",
    "/Users/someone/projects",
]
SERVERS = {
    "cc-args": {"type": "stdio", "command": "/opt/fixture/node-v20/bin/npx", "args": ARGS},
    "cc-hosted": {
        "type": "http",
        "url": f"https://mcp.fixture.invalid/api/mcp/s/{S_PATH}/mcp?api_key={S_APIKEY}&profile=w",
    },
    "cc-userinfo": {"type": "sse", "url": f"https://alice:{S_USER}@sse.fixture.invalid/sse"},
}

# One gateway process. argv[1] is JSON: `own` is a server to add through the Tools page first, and
# `import` the names to import, each with Claude Code's own entry left in place (`ccGlobal: true`).
_DRIVER = textwrap.dedent("""
    import asyncio, json, sys

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    PLAN = json.loads(sys.argv[1])


    async def main():
        from personalclaw.dashboard.handlers import mcp as h
        from personalclaw.onboarding_import.sources import claude_code

        app = web.Application()
        app["state"] = type("State", (), {"_background_tasks": set()})()
        app.router.add_get("/api/mcp", h.api_mcp_servers)
        app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
        app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
        app.router.add_put("/api/mcp/servers/{name}", h.api_mcp_server_detail)
        out = {"onboarding_root": str(claude_code.resolve_root())}
        async with TestClient(TestServer(app)) as c:
            if PLAN.get("own"):
                name, body = PLAN["own"]
                out["own"] = (await c.put(f"/api/mcp/servers/{name}", json=body)).status
            out["importable"] = await (await c.get("/api/mcp/importable")).text()
            changes = [{"name": n, "personalclaw": True, "ccGlobal": True} for n in PLAN["import"]]
            if changes:
                resp = await c.post("/api/mcp/apply", json={"changes": changes})
                out["apply"] = await resp.json()
            out["listed"] = await (await c.get("/api/mcp")).text()
            await asyncio.gather(*app["state"]._background_tasks, return_exceptions=True)
        print("RESULT " + json.dumps(out))


    asyncio.run(main())
    """)


@pytest.fixture
def world(tmp_path):
    pclaw, user, cc_dir = tmp_path / "pclaw", tmp_path / "user", tmp_path / "cc-config"
    for d in (pclaw, user, cc_dir):
        d.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    env.update(
        PERSONALCLAW_HOME=str(pclaw),
        HOME=str(user),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    env.pop("CLAUDE_CONFIG_DIR", None)
    return {"pclaw": pclaw, "user": user, "cc_dir": cc_dir, "driver": driver, "env": env}


def _write_claude(path: Path, servers: dict) -> None:
    path.write_text(
        json.dumps({"numStartups": 9, "projects": {"/w": {}}, "mcpServers": servers}),
        encoding="utf-8",
    )


def _boot(world, plan: dict, *, claude_config_dir: Path | None = None) -> dict:
    env = dict(world["env"])
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    proc = subprocess.run(
        [sys.executable, str(world["driver"]), json.dumps(plan)],
        env=env,
        cwd=world["pclaw"].parent,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, (
        f"the gateway process failed (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n"
        f"{proc.stderr[-4000:]}"
    )
    return json.loads(lines[-1][len("RESULT ") :])


def _mcp_json(world) -> dict:
    return json.loads((world["pclaw"] / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_the_import_list_names_each_server_and_carries_no_credential(world) -> None:
    _write_claude(world["user"] / ".claude.json", SERVERS)
    out = _boot(world, {"import": ["cc-args"]})

    for secret in SECRETS:
        assert secret not in out["importable"], f"the import list sent {secret!r} to the browser"
    rows = {r["name"]: r for r in json.loads(out["importable"])["servers"]}
    assert rows["cc-args"] == {
        "name": "cc-args",
        "backend": "Claude Code",
        "transport": "stdio",
        # The program, not where it lives, and the arguments that say which server this is.
        "command": "npx",
        "args": [
            "-y",
            "@fixture/github-mcp",
            "--api-key",
            MASK,
            f"--token={MASK}",
            f"GITHUB_TOKEN={MASK}",
            "--header",
            f"Authorization: {MASK}",
            f"https://{MASK}@api.fixture.invalid/v1?token={MASK}&region={MASK}",
            "--port",
            "8080",
            "/Users/someone/projects",
        ],
        "url": "",
        "env": [],
        "headers": [],
    }
    assert rows["cc-hosted"]["transport"] == "http"
    assert rows["cc-hosted"]["command"] == "" and rows["cc-hosted"]["args"] == []
    assert rows["cc-hosted"]["url"] == (
        f"https://mcp.fixture.invalid/api/mcp/s/{MASK}/mcp?api_key={MASK}&profile={MASK}"
    )
    assert rows["cc-userinfo"]["transport"] == "sse"
    assert rows["cc-userinfo"]["url"] == f"https://{MASK}@sse.fixture.invalid/sse"

    # The import itself reads the whole definition, server-side.
    assert not any(c.get("error") for c in out["apply"]["results"]), out["apply"]
    assert _mcp_json(world)["cc-args"]["args"] == ARGS
    # And the list of configured servers carries no definition at all.
    for secret in SECRETS:
        assert secret not in out["listed"], f"GET /api/mcp sent {secret!r} to the browser"
    listed = {s["name"]: s for s in json.loads(out["listed"])}
    for key in ("command", "args", "url", "headers", "cwd"):
        assert key not in listed["cc-args"], f"GET /api/mcp still sends {key}: {listed['cc-args']}"
    assert listed["cc-args"]["transport"] == "stdio"


def test_claude_code_is_read_and_written_where_claude_config_dir_puts_it(world) -> None:
    home_file = world["user"] / ".claude.json"
    dir_file = world["cc_dir"] / ".claude.json"
    _write_claude(home_file, {"home-only": {"command": "echo", "args": ["home"]}})
    _write_claude(dir_file, {"dir-only": {"command": "echo", "args": ["dir"]}})
    home_before = home_file.read_bytes()

    own = ["pc-own", {"command": "echo", "args": ["own"]}]
    out = _boot(
        world, {"own": own, "import": ["dir-only", "pc-own"]}, claude_config_dir=world["cc_dir"]
    )
    assert out["onboarding_root"] == str(world["cc_dir"]), "precondition: onboarding honours it"
    offered = [r["name"] for r in json.loads(out["importable"])["servers"]]
    assert offered == ["dir-only"], f"the list did not come from $CLAUDE_CONFIG_DIR: {offered}"
    assert out["own"] == 200
    results = {r["name"]: r for r in out["apply"]["results"]}
    assert "error" not in results["dir-only"], results["dir-only"]
    assert _mcp_json(world)["dir-only"]["args"] == ["dir"], "Import read another Claude Code"
    # Putting a server into Claude Code's scope writes the file Claude Code reads.
    cc_servers = json.loads(dir_file.read_text(encoding="utf-8"))["mcpServers"]
    assert "pc-own" in cc_servers, "the Claude Code toggle did not write $CLAUDE_CONFIG_DIR"
    assert cc_servers["pc-own"]["args"] == ["own"]
    assert Path(cc_servers["pc-own"]["command"]).name == "echo"
    assert home_file.read_bytes() == home_before, "the Claude Code toggle wrote ~/.claude.json"

    # Unset, Claude Code's file is the one in the home directory, beside ~/.claude.
    out = _boot(world, {"import": []})
    offered = [r["name"] for r in json.loads(out["importable"])["servers"]]
    assert offered == ["home-only"], offered


def test_the_mcp_importer_asks_the_onboarding_resolver(monkeypatch, tmp_path) -> None:
    """One resolver: the MCP importer's Claude Code file IS what the onboarding importer's module
    answers, so a change to how Claude Code is found reaches both."""
    from personalclaw import mcp_discovery
    from personalclaw.onboarding_import.sources import claude_code

    elsewhere = tmp_path / "elsewhere.json"
    monkeypatch.setattr(claude_code, "global_config_path", lambda: elsewhere)
    assert mcp_discovery._import_sources() == ((elsewhere, "Claude Code"),)
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    assert mcp_mod._cc_global_json() == elsewhere


def test_the_global_config_path_follows_claude_codes_own_rule(monkeypatch, tmp_path) -> None:
    from personalclaw.onboarding_import.sources import claude_code

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert claude_code.global_config_path() == user / ".claude.json"
    cc = tmp_path / "cc"
    cc.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cc))
    assert claude_code.global_config_path() == cc / ".claude.json"
    assert claude_code.resolve_root() == cc
    # A legacy `.config.json` in the config root wins, as it does for Claude Code.
    (cc / ".config.json").write_text("{}", encoding="utf-8")
    assert claude_code.global_config_path() == cc / ".config.json"


@pytest.mark.parametrize(
    "args, shown",
    [
        (["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], None),
        (["--api-key", "abc"], ["--api-key", MASK]),
        (["--API_KEY=abc"], [f"--API_KEY={MASK}"]),
        (["--client-secret", "s3cr3t", "--port", "7"], ["--client-secret", MASK, "--port", "7"]),
        (["-H", "X-Api-Key: abc"], ["-H", f"X-Api-Key: {MASK}"]),
        (["--header=Authorization: Bearer abc"], [f"--header=Authorization: {MASK}"]),
        (["-e", "GITHUB_PERSONAL_ACCESS_TOKEN"], None),
        (["run", "ghp_0123456789abcdefABCDEF0123"], ["run", MASK]),
        (["serve", "8f14e45fceea167a5a36dedd4bea2543"], ["serve", MASK]),
        (["mcp-server-sqlite-2.0.1", "--db", "./data.db"], None),
        (["-c", "API_KEY=abc node server.js"], ["-c", f"API_KEY={MASK}"]),
        (["-c", "node server.js --token abc"], ["-c", f"node server.js --token {MASK}"]),
        (["--url=https://h.invalid/p?key=v"], [f"--url=https://h.invalid/p?key={MASK}"]),
        (["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMDEyMzQ1Njc4OQ"], [MASK]),
    ],
)
def test_masked_args(args, shown) -> None:
    from personalclaw.mcp_discovery import masked_args

    assert masked_args(args) == (args if shown is None else shown)


@pytest.mark.parametrize(
    "url, shown",
    [
        ("https://mcp.example.com/mcp", None),
        ("http://127.0.0.1:8080/sse", None),
        ("https://mcp.example.com/mcp?token=abc", f"https://mcp.example.com/mcp?token={MASK}"),
        ("https://tok@github.invalid/x", f"https://{MASK}@github.invalid/x"),
        ("https://u:p@[::1]:9000/mcp", f"https://{MASK}@[::1]:9000/mcp"),
        (f"https://z.invalid/api/mcp/s/{S_PATH}/sse", f"https://z.invalid/api/mcp/s/{MASK}/sse"),
        ("https://h.invalid/mcp#frag", f"https://h.invalid/mcp#{MASK}"),
        ("https://h.invalid:notaport/x", MASK),
        ("not a url", None),
    ],
)
def test_masked_url(url, shown) -> None:
    from personalclaw.mcp_discovery import masked_url

    assert masked_url(url) == (url if shown is None else shown)
