"""Each ACP agent CLI keeps its own state where the sandbox lets it write, so its first start works.

The sandbox refuses the agent a write to ``<home>/agents``, ``hooks``, ``grants`` and
``config.json`` (#3725), and it now fences those names whether they exist or not. So the question
for every ACP app is where its CLI keeps its own state, and whether that is still writable. Read
from each app's code and its CLI's documented defaults:

* claude-code: ``CLAUDE_CONFIG_DIR``, which the app points at ``<home>/cc-config`` and creates
  before it spawns the CLI, and a cache in the user's cache folder;
* codex: ``CODEX_HOME``, by default ``~/.codex``;
* gemini: ``~/.gemini``;
* kiro: ``~/.kiro`` and the user's data folder. Its app turns the host sandbox off, because the
  CLI sandboxes itself and the host's profile cannot nest around it.

None of those is an owner-only name, and none is inside one. A stub stands in for each CLI here,
because a real agent CLI does more than keep state in HOME: in a scratch home, one handshake
started a machine-wide MCP server that fetched and cached a credential. The stub writes where its
CLI writes, through the real ACP spawn and the real sandbox, and reports every write that failed.
The control is the same stub refused ``<home>/agents``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

import personalclaw.config.loader as loader
from personalclaw.sandbox import detect_backend

_CACHE = "Library/Caches" if sys.platform == "darwin" else ".cache"
_DATA = "Library/Application Support" if sys.platform == "darwin" else ".local/share"

#: What each CLI writes at its first start, relative to ``$HOME`` unless it names a variable, and
#: the sandbox mode its app spawns it with.
_CLIS: dict[str, dict] = {
    "claude-code": {
        "sandbox_mode": "auto",
        "writes": [
            "$CLAUDE_CONFIG_DIR/.claude.json",
            "$CLAUDE_CONFIG_DIR/sessions/1.json",
            f"{_CACHE}/claude-cli-nodejs/workspace/mcp-logs-core/log.jsonl",
        ],
    },
    "codex": {
        "sandbox_mode": "auto",
        "writes": [".codex/config.toml", ".codex/state_5.sqlite", ".codex/tmp/arg0/lock"],
    },
    "gemini": {"sandbox_mode": "auto", "writes": [".gemini/settings.json", ".gemini/tmp/x"]},
    "kiro": {
        "sandbox_mode": "off",
        "writes": [".kiro/settings/cli.json", f"{_DATA}/kiro-cli/data.sqlite3"],
    },
}

_STUB = textwrap.dedent("""
    import json, os, sys
    home, report, pc_home = os.environ["HOME"], sys.argv[1], sys.argv[2]
    failed = []
    for rel in json.loads(sys.argv[3]):
        if rel.startswith("$"):
            var, _, rest = rel[1:].partition("/")
            path = os.path.join(os.environ[var], rest)
        else:
            path = os.path.join(home, rel)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Write the way these CLIs do: a new file renamed over the old one.
            with open(path + ".tmp", "w") as f:
                f.write("state")
            os.replace(path + ".tmp", path)
        except OSError as exc:
            failed.append([rel, exc.strerror])
    try:
        os.makedirs(os.path.join(pc_home, "agents"), exist_ok=True)
        with open(os.path.join(pc_home, "agents", "personalclaw.json"), "w") as f:
            f.write("{}")
        refused = False
    except OSError:
        refused = True
    with open(report, "w") as f:
        json.dump({"failed": failed, "agents_refused": refused}, f)
    """)


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return pc


async def _first_start(home: Path, cli: str, tmp_path: Path) -> dict:
    from personalclaw.acp.transport import AcpProcess

    stub = tmp_path / f"stub-{cli}.py"
    stub.write_text(_STUB)
    report = home / "workspace" / f"{cli}-report.json"
    extra_env: dict[str, str] = {}
    if cli == "claude-code":
        # What the app does before the spawn (`claude-code-agent/provider.py`, `_build_env`).
        (home / "cc-config").mkdir()
        extra_env["CLAUDE_CONFIG_DIR"] = str(home / "cc-config")
    proc = AcpProcess(
        command=[
            sys.executable,
            str(stub),
            str(report),
            str(os.path.realpath(home)),
            json.dumps(_CLIS[cli]["writes"]),
        ],
        work_dir=home / "workspace",
        sandbox_mode=_CLIS[cli]["sandbox_mode"],
        extra_env=extra_env,
    )
    await proc.spawn()
    assert proc.process is not None
    await asyncio.wait_for(proc.process.wait(), timeout=60)
    proc.teardown()
    assert report.exists(), proc.stderr_tail()
    return json.loads(report.read_text())


@pytest.mark.skipif(
    detect_backend("auto") == "none", reason="needs an OS sandbox to measure what it allows"
)
@pytest.mark.parametrize("cli", sorted(_CLIS))
def test_an_acp_cli_first_start_keeps_its_state(home, tmp_path, cli):
    result = asyncio.run(_first_start(home, cli, tmp_path))

    assert result["failed"] == [], f"{cli}'s own state was refused: {result['failed']}"
    if _CLIS[cli]["sandbox_mode"] != "off":
        assert result["agents_refused"], "the control: the sandbox refuses <home>/agents"
