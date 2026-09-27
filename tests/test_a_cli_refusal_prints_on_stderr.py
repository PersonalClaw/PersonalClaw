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

import ast
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

import personalclaw

PACKAGE = Path(personalclaw.__file__).parent

#: Functions whose stdout before a failing status is their real output, and why.
REPORTS_WITH_A_STATUS = {
    ("apps/quality.py", "main"): "the verification report is the output; 1 says what failed",
    ("cli_commands.py", "_security"): "`security verify` reports the log; 1 says it is tampered",
    ("cli_doctor.py", "_doctor"): "the doctor's report ends on its verdict and what to fix",
    ("inbound/auth.py", "_show_token"): "`inbound token show` answers either way; 1 = none works",
    ("evals/child.py", "main"): "the result line is the protocol its parent reads on stdout",
    ("evals/optimize.py", "main"): "JSON on stdout is the bash action's protocol, errors too",
}


@dataclass(frozen=True)
class _Site:
    module: str
    function: str
    line: int
    text: str


def _nonzero_exit(stmt: ast.stmt) -> bool:
    def code(node: ast.AST | None) -> int | None:
        return node.value if isinstance(node, ast.Constant) and type(node.value) is int else None

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Attribute) and func.attr == "exit":
            if isinstance(func.value, ast.Name) and func.value.id == "sys" and stmt.value.args:
                return code(stmt.value.args[0]) not in (None, 0)
    if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
        if getattr(stmt.exc.func, "id", "") == "SystemExit" and stmt.exc.args:
            return code(stmt.exc.args[0]) not in (None, 0)
    if isinstance(stmt, ast.Return):
        return code(stmt.value) not in (None, 0)
    return False


def _stdout_print(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == "print"
        and not any(k.arg == "file" for k in stmt.value.keywords)
    )


def _own_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    """``fn`` and its nodes, without those of a function nested in it (walked on its own)."""
    stack: list[ast.AST] = [fn]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)


def _refusals_on_stdout(source: str, module: str) -> set[_Site]:
    """Each stdout print in a block that runs straight into a non-zero exit."""
    sites: set[_Site] = set()
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _own_nodes(fn):
            blocks = [getattr(node, f, None) for f in ("body", "orelse", "finalbody")]
            blocks += [h.body for h in getattr(node, "handlers", []) or []]
            for block in blocks:
                if not (isinstance(block, list) and block and isinstance(block[0], ast.stmt)):
                    continue
                for i, stmt in enumerate(block):
                    if not _nonzero_exit(stmt):
                        continue
                    j = i - 1
                    while j >= 0 and _stdout_print(block[j]):
                        sites.add(_Site(module, fn.name, block[j].lineno, ast.unparse(block[j])))
                        j -= 1
    return sites


def _package_sites() -> set[_Site]:
    sites: set[_Site] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        module = path.relative_to(PACKAGE).as_posix()
        sites |= _refusals_on_stdout(path.read_text(encoding="utf-8"), module)
    return sites


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


def test_no_command_prints_a_refusal_on_stdout():
    unexpected = sorted(
        (s for s in _package_sites() if (s.module, s.function) not in REPORTS_WITH_A_STATUS),
        key=lambda s: (s.module, s.line),
    )
    assert not unexpected, "a refusal printed on stdout (give it file=sys.stderr):\n" + "\n".join(
        f"  {s.module}:{s.line} in {s.function}: {s.text}" for s in unexpected
    )


def test_every_named_report_is_still_there():
    """A named function that no longer reports before a failing status excuses nothing, so a
    stale entry is removed rather than left to excuse the next refusal written there."""
    reporting = {(s.module, s.function) for s in _package_sites()}
    assert set(REPORTS_WITH_A_STATUS) <= reporting, set(REPORTS_WITH_A_STATUS) - reporting


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
    assert "Gateway not running — start it with: personalclaw gateway" in proc.stderr


def test_logout_with_no_gateway_puts_nothing_on_stdout(tmp_path, monkeypatch, capsys):
    from personalclaw import cli_server

    monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
    with pytest.raises(SystemExit) as exited:
        cli_server._logout(1)

    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == "" and "Gateway not running" in err


def test_a_refused_restore_names_the_components_on_stderr(tmp_path, monkeypatch, capsys):
    """The list after "Unknown component" belongs to the refusal, so it is on stderr with it;
    `--list-components` asked for it, so there it is the output, on stdout."""
    from personalclaw import snapshot

    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"")
    monkeypatch.setattr(snapshot, "_is_gateway_running", lambda: False)

    assert snapshot.restore_main([str(archive), "--components", "bogus"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "Unknown component: bogus" in err and "Available components:" in err

    assert snapshot.restore_main(["--list-components"]) == 0
    out, err = capsys.readouterr()
    assert "Available components:" in out and err == ""
