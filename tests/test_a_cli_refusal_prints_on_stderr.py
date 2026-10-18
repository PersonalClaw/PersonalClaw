"""A refusal goes to stderr, so stdout carries only a command's real output.

`$(personalclaw token)` with no gateway captured "❌ Gateway not running — start it with:
personalclaw gateway" as if it were the sign-in link: the refusal printed on stdout and only the
exit status said otherwise. `logout`, `stop`, `update`, `spawn`, the eval family (`judge-bench`,
`eval-harvest`, `study`, `ablation`, `eval-gate`, `retrieval-eval`), `auth`, `inbound token`,
`project`, `app new`, `snapshot`, `restore`, `backup validate`, `capture import` and `push test`
printed theirs the same way. Every one is on stderr now.

The rail walks the package for the shape of a refusal: a stdout print directly before a non-zero
exit. Where stdout IS the answer and the status says pass or fail (a verdict: `doctor`,
`security verify`, `inbound token show`, the app quality check), or stdout is a protocol another
program parses (the eval child, the optimize action), the function is named below, with why.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest
from a_cli_refusal_prints_on_stderr_shared import _refusals_on_stdout


def test_the_detector_finds_a_refusal_and_nothing_else():
    """Calibrated both ways, or the rail below passes over a walk that detects nothing."""
    source = textwrap.dedent("""
        import sys

        def refuses():
            print("Error: gateway not running")
            sys.exit(1)

        def refuses_with_a_hint():
            print("❌ Not a usable version")
            print("   Give a release version")
            return 1

        def refuses_on_stderr():
            print("Error: gateway not running", file=sys.stderr)
            sys.exit(1)

        def succeeds():
            print("done")
            sys.exit(0)

        def reports_then_works():
            print("progress")
            do_the_work()
            return 1
        """)
    found = {(s.function, s.text) for s in _refusals_on_stdout(source, "fixture.py")}
    assert found == {
        ("refuses", "print('Error: gateway not running')"),
        ("refuses_with_a_hint", "print('❌ Not a usable version')"),
        ("refuses_with_a_hint", "print('   Give a release version')"),
    }


def test_token_with_no_gateway_puts_nothing_on_stdout(tmp_path):
    """`$(personalclaw token)` is a URL or nothing: the refusal is on stderr."""
    home = tmp_path / "user"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    env.update(
        PERSONALCLAW_HOME=str(tmp_path / "pc"),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        HOME=str(home),
        PYTHONIOENCODING="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "personalclaw", "token"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc
    assert proc.stdout == ""
    assert "No gateway is running for this home" in proc.stderr, proc.stderr
    assert "Start it with: personalclaw gateway" in proc.stderr, proc.stderr


def test_logout_with_no_gateway_puts_nothing_on_stdout(tmp_path, monkeypatch, capsys, unset_env):
    from personalclaw import cli_server

    unset_env("PERSONALCLAW_PORT")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exited:
        cli_server._logout(None)

    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == "" and "No gateway is running for this home" in err


def test_a_refused_restore_names_the_components_on_stderr(tmp_path, monkeypatch, capsys):
    """The list after "Unknown component" belongs to the refusal, so it is on stderr with it;
    `--list-components` asked for it, so there it is the output, on stdout."""
    from personalclaw import snapshot

    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"")
    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)

    assert snapshot.restore_main([str(archive), "--components", "bogus"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "Unknown component: bogus" in err and "Available components:" in err

    assert snapshot.restore_main(["--list-components"]) == 0
    out, err = capsys.readouterr()
    assert "Available components:" in out and err == ""
