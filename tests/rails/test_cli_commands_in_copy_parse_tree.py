"""The source-tree checks split from
``tests/test_cli_commands_in_copy_parse.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re

import source_index
from cli_commands_in_copy_parse_shared import _argv, _parse_error
from table_rows import row

#: How many commands the copy holds today. The table test asserts it exactly, so a row the scan
#: stops finding fails rather than quietly leaving the table. A new command in copy raises it.
_ROWS = 115


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


def _spa_commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in source_index.web_files():
        if (
            ".test." in path.name
            or "/test/" in path.as_posix()
            or path.suffix not in (".ts", ".tsx")
        ):
            continue
        text = source_index.text(path)
        rel = source_index.rel(path)
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
    for path in source_index.package_files():
        rel = source_index.rel(path)
        if rel in _DESCRIBES_COMMANDS_THAT_RAN:
            continue
        tree = source_index.tree(path)
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


def _commands() -> list[tuple[str, str]]:
    """Every command in copy, read when a test asks, never as the module is collected."""
    return _spa_commands() + _message_commands()


def test_the_scan_finds_the_copy_it_is_meant_to_read():
    """Vacuity guard: each source shape is read, so a green is a measurement."""
    found = _commands()
    where = {site.split(":")[0] for site, _ in found}
    commands = {command for _, command in found}
    assert len(found) >= 60, len(found)
    assert "web/src/pages/settings/DurabilityPanel.tsx" in where  # <code> elements
    assert "web/src/pages/settings/CompanionPanel.tsx" in where  # backticks in a hint string
    assert "web/src/pages/settings/UpdatesPanel.tsx" in where  # \` in a template literal
    assert "web/src/pages/apps/installConsent.tsx" in where  # cmd('personalclaw setup')
    # The replace refusal: the dashboard's 409 and the terminal's, one sentence.
    assert "src/personalclaw/snapshot.py" in where
    assert "personalclaw doctor" in commands


def test_every_command_in_copy_parses(subtests):
    """One row per command in copy, reported as a subtest named by its site when it fails. The
    rows are read here, not at collection, so collecting this module parses nothing; the count is
    today's exactly."""
    found = _commands()
    assert len(found) == _ROWS, (
        f"the copy holds {len(found)} commands, not {_ROWS}: a row went missing, or a new command "
        "was added (then raise _ROWS)"
    )
    for site, command in found:
        with row(subtests, site=site, command=command):
            error = _parse_error(_argv(command))
            assert not error, f"{site} tells users to run `{command}`, and the CLI says: {error}"
