"""A secret a settings record owns is never loaded into the gateway's environment.

🔴 THE DEFECT (``origin/main``, measured in a scratch home). ``credentials.py`` says an OWNED key
(``PCSECRET_…``: a provider key, an app's token, and since #3617 every MCP server's ``env`` and
``headers`` value and the webhook token) "is deliberately NOT mirrored into ``os.environ``", because
"exporting it would hand every provider key to every child the gateway spawns".
``AppConfig.load_credentials`` honours that. But ``personalclaw``'s CLI entry point loads the home's
``.env`` with python-dotenv before anything else runs, and python-dotenv loads every line. So
after ``personalclaw gateway`` started, ``os.environ`` held every owned secret, and every MCP server
(spawned with ``env = dict(os.environ)``) received every other server's tokens and every provider's
API key. That is the case that matters once Import works: a third-party server brought in from
Claude Code would start with all of them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_PROBE = textwrap.dedent("""
    import json, os, sys
    sys.argv = ["personalclaw", "--help"]
    from personalclaw import cli
    try:
        cli.main()
    except SystemExit:
        pass
    print("RESULT " + json.dumps({k: os.environ.get(k) for k in sys.stdin.read().split()}))
    """)


@pytest.mark.parametrize("cwd_is_the_home", [False, True], ids=["elsewhere", "in the home"])
def test_the_cli_loads_named_credentials_and_never_an_owned_one(tmp_path, cwd_is_the_home) -> None:
    home = tmp_path / "pclaw-home"
    home.mkdir()
    (home / ".env").write_text(
        "PCSECRET_MCP_GH_ABCD1234__ENV__GITHUB_TOKEN_EF567890=ghp_ownedMcpTokenFixture01234\n"
        "PCSECRET_PROVIDER_WORK_12345678__API_KEY=sk-ownedProviderKeyFixture5678\n"
        "NAMED_FIXTURE_KEY=named-value-the-children-inherit\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PERSONALCLAW_", "PYTEST_"))}
    env.update(
        PERSONALCLAW_HOME=str(home),
        HOME=str(tmp_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    for k in list(env):
        if k.startswith("PCSECRET_") or k == "NAMED_FIXTURE_KEY":
            del env[k]
    names = [
        "PCSECRET_MCP_GH_ABCD1234__ENV__GITHUB_TOKEN_EF567890",
        "PCSECRET_PROVIDER_WORK_12345678__API_KEY",
        "NAMED_FIXTURE_KEY",
    ]
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        input=" ".join(names),
        env=env,
        cwd=home if cwd_is_the_home else elsewhere,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, proc.stdout[-2000:] + proc.stderr[-2000:]
    seen = json.loads(lines[-1][len("RESULT ") :])
    assert (
        seen["NAMED_FIXTURE_KEY"] == "named-value-the-children-inherit"
    ), "named keys stopped loading"
    leaked = [k for k in names if k.startswith("PCSECRET_") and seen[k] is not None]
    assert leaked == [], f"owned secrets reached the process environment: {leaked}"
