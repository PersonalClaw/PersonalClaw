"""Rails for the two commands the getting-started guide hands a newcomer first.

A walk of the published guide on a clean machine — a fresh anonymous clone of
`github.com/PersonalClaw/PersonalClaw`, a brand-new venv, `personalclaw` 0.1.3 from PyPI —
found both of the guide's own commands crashing with raw Python tracebacks in the state a
newcomer is actually in:

  * `personalclaw setup` (guide § "1. Install") died with `EOFError: EOF when reading a line`
    the moment stdin was not a terminal — a pipe, a redirect, a Dockerfile `RUN`, CI.
  * `personalclaw chat -m "hello"` (guide § "4. First chat") dumped ~30 asyncio stack frames
    to bury an already well-composed `WHAT/WHY/FIX` message, because a fresh install has no
    chat model bound yet — which is precisely the state that step runs in.

`personalclaw chat` is now a chat of the gateway the guide starts in § "2. First run". With no
gateway running there is nothing to chat with, and it says so with the command that starts one;
with a gateway and no chat model bound, the turn's own sentence says what to bind
(`test_a_send_with_no_model_says_what_to_do.py`).

These tests drive the REAL entry point in a subprocess with an isolated `PERSONALCLAW_HOME`,
so they reproduce the newcomer's state rather than a reimplementation of it, and can never
touch the developer's real home. They are fully offline: the chat finds no gateway on a port
nothing listens on and reaches nothing, and the URL rail below asserts *shape*, never
reachability.
"""

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `cli.py` has no ``__main__`` guard (it is reached through the ``personalclaw`` console
#: script), so call `main` directly to exercise the real parser.
_RUN_CLI = "from personalclaw.cli import main; main()"


def _run_cli(
    args: list[str], home: Path, stdin_devnull: bool = True, port: int | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERSONALCLAW_HOME": str(home)}
    if port is not None:
        env["PERSONALCLAW_PORT"] = str(port)
    # A stray real-home token/browser-open would make these tests environment-dependent.
    env.pop("PERSONALCLAW_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, "-c", _RUN_CLI, *args],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL if stdin_devnull else None,
        timeout=180,
    )


# ── Guide step 1: `personalclaw setup` ────────────────────────────────────────


def test_setup_survives_a_non_interactive_stdin(tmp_path) -> None:
    """`setup` takes its printed defaults instead of dying on EOFError.

    Seeds `<home>/workspace_dir`, the pointer a real setup saves, so the wizard's workspace
    default is a tmp path: a test must never create a directory outside its own home.
    """
    ws = tmp_path / "ws"
    (tmp_path / "workspace_dir").write_text(str(ws) + "\n", encoding="utf-8")

    proc = _run_cli(["setup"], tmp_path)

    assert "Traceback" not in proc.stderr, proc.stderr
    assert "EOFError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr}"
    # It reached the end of the wizard, not just the banner.
    assert "Agent installed" in proc.stdout, proc.stdout
    # And it said out loud that it was defaulting rather than prompting.
    assert "non-interactive stdin" in proc.stdout, proc.stdout


def test_ask_returns_the_typed_answer_on_a_terminal(monkeypatch, capsys) -> None:
    """The interactive path is unchanged — the guard must not swallow a real answer."""
    from personalclaw import cli_setup

    class _Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli_setup.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda _prompt: "  America/Denver  ")

    assert cli_setup._ask("  Timezone: ") == "America/Denver"
    assert "non-interactive" not in capsys.readouterr().out


def test_ask_survives_eof_on_a_terminal_that_closes(monkeypatch, capsys) -> None:
    """A terminal that reports `isatty` and then hits EOF still must not raise.

    The `isatty` guard alone does not cover this: with the guard in place a pipe never
    reaches `input()`, so the `except EOFError` branch is unreachable from the
    non-interactive test above and was passing under mutation. This pins the other
    door — a real terminal whose stdin closes mid-wizard (ssh drop, closed pty) —
    which is the only way that branch is entered.
    """
    from personalclaw import cli_setup

    class _Tty:
        def isatty(self) -> bool:
            return True

    def _eof(_prompt: str) -> str:
        raise EOFError("stdin closed")

    monkeypatch.setattr(cli_setup.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", _eof)

    assert cli_setup._ask("  Timezone: ") == ""
    # It took the interactive door (no non-interactive notice), then absorbed the EOF.
    assert "non-interactive" not in capsys.readouterr().out


def test_ask_defaults_and_says_so_without_a_terminal(monkeypatch, capsys) -> None:
    from personalclaw import cli_setup

    class _NotATty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(cli_setup.sys, "stdin", _NotATty())

    def _boom(_prompt: str) -> str:  # pragma: no cover - must never be reached
        raise AssertionError("_ask consulted stdin despite it not being a terminal")

    monkeypatch.setattr("builtins.input", _boom)

    assert cli_setup._ask("  Workspace path [/tmp/x]: ") == ""
    assert "non-interactive stdin" in capsys.readouterr().out


def test_no_wizard_prompt_bypasses_the_guard() -> None:
    """Every `setup` prompt goes through `_ask`; only `_ask` itself may call `input`.

    A new bare `input()` would reintroduce the EOFError crash at a new step.
    """
    src = (REPO_ROOT / "src" / "personalclaw" / "cli_setup.py").read_text(encoding="utf-8")
    calls = re.findall(r"\binput\(", src)
    assert len(calls) == 1, f"expected only _ask's own input() call, found {len(calls)}"
    assert "return input(prompt).strip()" in src


# ── Guide step 4: `personalclaw chat -m "hello"` ──────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_chat_with_no_gateway_prints_the_fix_not_a_traceback(tmp_path) -> None:
    """The guide's first-chat command in a fresh home with no gateway running: there is nothing
    to chat with, and it says so, naming the home and the port it asked, and names the guide's own
    command that starts one (§ "2. First run"). The gateway's port is one nothing listens on, so a
    gateway this machine happens to run on the default port cannot answer in the newcomer's
    place."""
    port = _free_port()
    proc = _run_cli(["chat", "-m", "hello"], tmp_path, port=port)

    assert proc.returncode == 1, f"rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "asyncio" not in proc.stderr, proc.stderr
    assert f"No gateway is running for this home ({tmp_path})" in proc.stderr, proc.stderr
    assert (
        f"nothing listens on port {port}, which PERSONALCLAW_PORT names" in proc.stderr
    ), proc.stderr
    assert "Start it with: personalclaw gateway" in proc.stderr, proc.stderr
