"""The gateway's product text carries no emoji.

Measured before this rail: the auto-compact notice persisted into a chat began with a refresh
emoji (``🔄 Auto-compacted at 92%.``), two notification titles led with a hook and an alarm clock,
the messages a linked channel thread received carried a thread, a person and a robot, and the chat
REPL printed a stopwatch, a cross, a refresh and a warning sign in front of its error, compaction
and context lines. An emoji is not a word. A screen reader reads the refresh arrows out as
"counterclockwise arrows button", and the sentence it stands in front of already says what
happened.

The scan walks every string literal the web gateway (``personalclaw/dashboard``), the chat REPL
(``cli_chat.py``) and the error registries compose, and skips what is not product text: docstrings,
log calls and regular-expression patterns. The few emoji that are DATA rather than text are listed
in :data:`DATA_GLYPHS` with the reason, so adding one is a decision somebody writes down.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: What the rail reads: the web gateway, the chat REPL, and the error-sentence registries.
SCANNED = [
    *sorted((SRC / "dashboard").rglob("*.py")),
    SRC / "cli_chat.py",
    SRC / "http_errors.py",
    SRC / "errors.py",
]

#: Pictographs, dingbats, arrows-and-stars, the clock/media block, and the emoji variation
#: selector. Deliberately NOT the plain arrows (``Settings → Chat``) or ``⌘``, which the product
#: copy uses as text.
EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\u23e9-\u23fa\ufe0f]")

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_REGEX_FUNCS = {
    "compile",
    "sub",
    "subn",
    "search",
    "match",
    "fullmatch",
    "findall",
    "finditer",
    "split",
}

#: ``(path relative to personalclaw/, literal)`` → why it is data rather than product text.
DATA_GLYPHS: dict[tuple[str, str], str] = {
    (
        "dashboard/handlers/agents.py",
        "🎨",
    ): "a theme's default glyph: the icon a theme is listed with until its author picks one",
    (
        "dashboard/chat_folders.py",
        "\ufe0f\u200d",
    ): "the variation selector and joiner a folder-icon check accepts, to RECOGNISE an emoji",
}


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _is_skipped(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """A docstring, or an argument of a log call or a regular-expression call."""
    parent = parents.get(node)
    if isinstance(parent, ast.Expr):
        return True  # a docstring or a bare string statement: read by nobody but a developer
    while parent is not None:
        if isinstance(parent, ast.Call):
            func = parent.func
            if isinstance(func, ast.Attribute):
                if func.attr in _LOG_METHODS:
                    return True
                if (
                    func.attr in _REGEX_FUNCS
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "re"
                ):
                    return True
        parent = parents.get(parent)
    return False


def emoji_in(source: str) -> list[tuple[int, str]]:
    """Every ``(line, literal)`` in *source* that carries an emoji and is product text."""
    tree = ast.parse(source)
    parents = _parents(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if EMOJI.search(node.value) and not _is_skipped(node, parents):
                found.append((node.lineno, node.value))
    return found


def test_the_gateways_product_text_carries_no_emoji():
    offenders = []
    for path in SCANNED:
        rel = path.relative_to(SRC).as_posix()
        for line, literal in emoji_in(path.read_text(encoding="utf-8")):
            if (rel, literal) in DATA_GLYPHS:
                continue
            offenders.append(f"{rel}:{line}: {literal[:80]!r}")
    assert (
        not offenders
    ), "product text written with an emoji; say it in words instead:\n  " + "\n  ".join(offenders)


def test_the_scan_reads_what_it_claims_to():
    """Vacuity control: the file set is real and large, and every data glyph still exists."""
    assert all(p.is_file() for p in SCANNED), [p for p in SCANNED if not p.is_file()]
    assert len(SCANNED) > 50
    for rel, literal in DATA_GLYPHS:
        literals = {lit for _, lit in emoji_in((SRC / rel).read_text(encoding="utf-8"))}
        assert literal in literals, f"stale allowance, the literal is gone: {rel}"


def test_the_scan_catches_a_notice_and_skips_what_is_not_product_text():
    """The positive control: the shape of the defect is caught, and the three exemptions hold."""
    source = (
        '"""A docstring may say 🔄."""\n'
        "import logging, re\n"
        "logger = logging.getLogger(__name__)\n"
        '_NOTICE = "🔄 Auto-compacted at {pct:.0f}%."\n'
        'logger.info("📡 subagent status")\n'
        'PATTERN = re.compile("^[\\U0001F000-\\U0001FAFF]+")\n'
        'def notify(name):\n    return f"⏰ {name}"\n'
    )
    found = emoji_in(source)
    assert [line for line, _ in found] == [4, 8], found
