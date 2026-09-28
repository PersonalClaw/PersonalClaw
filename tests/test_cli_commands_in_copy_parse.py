"""Every ``personalclaw …`` command PersonalClaw tells a user to run parses against the real CLI.

🔴 The Backups panel told users to run ``personalclaw restore --replace`` twice, and a replace
restore refused over HTTP said the same. The flag is ``--mode replace`` (the ``restore`` parser in
``cli.py``), so the command a user copied failed with ``unrecognized arguments: --replace``.
Nothing held copy to the parser, so the two drifted and nobody could tell.

What this reads:

* **The SPA** (``web/src``, tests excluded): the text of a ``<code>`` element, a backticked
  command inside a quoted string (a hint, a label, a toast), the same inside a template literal
  (written ``\\`…\\```), and a quoted string that is a command on its own
  (``cmd('personalclaw setup')``). A bare backtick span in TypeScript is a template literal or a
  comment, and is not copy.
* **PersonalClaw's own messages**: every string constant under ``src/personalclaw`` that is not a
  docstring, with an f-string's holes read as placeholders. These reach users as CLI output, API
  errors and Doctor rows.

A placeholder (``<archive>``, ``NAME=…``, a ``{…}`` hole) becomes a sample value, so this checks
the words and flags a user types, not the values they fill in.
"""

from __future__ import annotations

import ast
import contextlib
import html
import io
import re
import shlex
from pathlib import Path

import pytest

from personalclaw.cli import build_parser

_ROOT = Path(__file__).resolve().parents[1]

#: Files whose backticked commands describe a command someone RAN, not one a user is told to run.
_DESCRIBES_COMMANDS_THAT_RAN = {
    "src/personalclaw/guardrails/self_destruct.py": (
        "labels the PersonalClaw command an agent's shell command would run, for the host-effect "
        "ledger, including commands it could not resolve"
    ),
}

_CODE_ELEMENT = re.compile(r"<code\b[^>]*>(.*?)</code>", re.S)
_QUOTED = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"")
_BACKTICKED = re.compile(r"`(personalclaw [^`]+)`")
_ESCAPED_BACKTICKED = re.compile(r"\\`(personalclaw [^`\\]+)\\`")
_WHOLE_COMMAND = re.compile(r"^['\"](personalclaw [^'\"]+)['\"]$")
_COMMENT_LINE = re.compile(r"^\s*(//|\*|/\*|\{/\*)")
_HOLE = re.compile(r"\$?\{[^{}]*\}")


def _spa_commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted((_ROOT / "web" / "src").rglob("*.ts*")):
        if (
            ".test." in path.name
            or "/test/" in path.as_posix()
            or path.suffix not in (".ts", ".tsx")
        ):
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_ROOT).as_posix()
        for match in _CODE_ELEMENT.finditer(text):
            body = " ".join(match.group(1).split())
            if body.startswith("personalclaw "):
                line = text.count("\n", 0, match.start()) + 1
                found.append((f"{rel}:{line}", body))
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _ESCAPED_BACKTICKED.finditer(line):
                found.append((f"{rel}:{lineno}", match.group(1)))
            if _COMMENT_LINE.match(line):
                continue
            for literal in _QUOTED.findall(line):
                whole = _WHOLE_COMMAND.match(literal)
                if whole:
                    found.append((f"{rel}:{lineno}", whole.group(1)))
                    continue
                for match in _BACKTICKED.finditer(literal):
                    found.append((f"{rel}:{lineno}", match.group(1)))
    return found


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                    ids.add(id(stmt.value))
    return ids


def _message_commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted((_ROOT / "src" / "personalclaw").rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _DESCRIBES_COMMANDS_THAT_RAN:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                skip.update(id(part) for part in node.values)
                text = "".join(
                    part.value if isinstance(part, ast.Constant) else "{hole}"
                    for part in node.values
                )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in skip:
                    continue
                text = node.value
            else:
                continue
            for match in _BACKTICKED.finditer(text):
                found.append((f"{rel}:{node.lineno}", match.group(1)))
    return found


_SAMPLE = "sample{}"


def _argv(command: str) -> list[str]:
    """The words after ``personalclaw``, each placeholder replaced by its own sample value."""
    samples = iter(range(1, 1000))
    text = _HOLE.sub(lambda _m: _SAMPLE.format(next(samples)), html.unescape(command))
    words = shlex.split(text)[1:]
    return [
        (
            _SAMPLE.format(next(samples))
            if word.startswith("<") or word.endswith(">")
            else word.replace("…", _SAMPLE.format(next(samples)))
        )
        for word in words
    ]


_PARSER = build_parser()
_INVALID_SAMPLE = re.compile(r"invalid choice: '(sample\d+)' \(choose from '([^']+)'")


def _parse_error(argv: list[str]) -> str:
    """What the real parser says about *argv*, or ``""`` when it takes it.

    A sample standing in for a value the copy fills in at run time (``--store {store}``) can
    land where the parser takes only certain values. Then the first value it names is used and
    the parse runs again, so the rest of the command is still checked.
    """
    for _ in range(len(argv) + 1):
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                _PARSER.parse_args(argv)
        except SystemExit as exc:
            if exc.code in (0, None):
                return ""
            said = stderr.getvalue().strip().splitlines()[-1]
            choice = _INVALID_SAMPLE.search(said)
            if not choice:
                return said
            argv = [choice.group(2) if word == choice.group(1) else word for word in argv]
            continue
        return ""
    return "a placeholder never fitted any value the parser takes"


_COMMANDS = _spa_commands() + _message_commands()


def test_the_scan_finds_the_copy_it_is_meant_to_read():
    """Vacuity guard: each source shape is read, so a green is a measurement."""
    where = {site.split(":")[0] for site, _ in _COMMANDS}
    commands = {command for _, command in _COMMANDS}
    assert len(_COMMANDS) >= 60, len(_COMMANDS)
    assert "web/src/pages/settings/DurabilityPanel.tsx" in where  # <code> elements
    assert "web/src/pages/settings/CompanionPanel.tsx" in where  # backticks in a hint string
    assert "web/src/pages/settings/UpdatesPanel.tsx" in where  # \` in a template literal
    assert "web/src/pages/apps/installConsent.tsx" in where  # cmd('personalclaw setup')
    # The replace refusal: the dashboard's 409 and the terminal's, one sentence.
    assert "src/personalclaw/snapshot.py" in where
    assert "personalclaw doctor" in commands


def test_the_parser_refuses_what_this_rail_exists_to_catch():
    """Control: the check sees the defect it was written for."""
    assert "unrecognized arguments: --replace" in _parse_error(
        _argv("personalclaw restore --replace")
    )
    assert _parse_error(_argv("personalclaw restore <archive> --mode replace")) == ""


@pytest.mark.parametrize(("site", "command"), _COMMANDS, ids=[site for site, _ in _COMMANDS])
def test_a_command_in_copy_parses(site, command):
    error = _parse_error(_argv(command))
    assert not error, f"{site} tells users to run `{command}`, and the CLI says: {error}"
