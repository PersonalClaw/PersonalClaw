"""Every pattern the install scanner reads with takes time in line with the length of the line.

A pattern that backtracks reads a long line in time that grows with its square or its cube, so one
untrusted file under the 16 MB read limit holds an install scan for hours (#3852). Each pattern a
scanner module holds at its top level runs on adversarial one-line texts of 1,000 and of 4,000
characters: four times the text may take about four times as long, and the check fails past eight.

The texts are the timing probe's tokens, repeated, and each pattern's own near misses: the start of
its shortest match, cut short, repeated. The modules' sources say which top-level names build a
pattern, so a pattern added to any of them is timed too; a name that yields nothing to time fails.
"""

from __future__ import annotations

import ast
import importlib
import re
import time
from re import _constants as k  # type: ignore[attr-defined]
from re import _parser as parser  # type: ignore[attr-defined]
from typing import Any

import source_index

MODULES = (
    "personalclaw.supply_chain",
    "personalclaw.install_instructions",
    "personalclaw.scan_remote_exec",
)
#: The timing probe's tokens: the words the rules start with, and the characters they skip over.
TOKENS = (
    "curl ", "| ", "python -c exec( ", "base64 -d ", "rm -rf ", "wget ", "eval ", "http://", " ",
    "\\", "a", "$(", "sh -c ", "iwr ", "-", "~/.", "import ", "os.system(", "open(", "/",
)  # fmt: skip
#: Four times the text may take this many times as long, at most.
LIMIT = 8.0
#: A read of 4,000 characters quicker than this is too quick for the ratio to mean anything.
FLOOR = 0.005
_CANDIDATES = "a x1-/ .=:(|\\\"'"


def _patterns(value: Any) -> list[re.Pattern[str]]:
    """The patterns *value* holds: itself, a capped pattern's own, or those in a table of them."""
    if isinstance(value, re.Pattern):
        return [value]
    if isinstance(getattr(value, "regex", None), re.Pattern):
        return [value.regex]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [p for item in value for p in _patterns(item)]
    return []


def _builds_a_pattern(node: ast.AST) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, (ast.Attribute, ast.Name))
        and (call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id)
        in {"compile", "CappedPattern"}
        for call in ast.walk(node)
    )


def _pattern_names(module: str) -> list[str]:
    """The top-level names *module*'s source assigns a value built with ``re.compile``."""
    rel = "src/" + module.replace(".", "/") + ".py"
    (path,) = [p for p in source_index.package_files() if source_index.rel(p) == rel]
    names: list[str] = []
    for node in source_index.tree(path).body:
        if isinstance(node, ast.Assign) and _builds_a_pattern(node.value):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value and _builds_a_pattern(node.value):
            names += [node.target.id] if isinstance(node.target, ast.Name) else []
    return names


def _timed() -> dict[str, tuple[str, re.Pattern[str]]]:
    """Every pattern the modules hold, by its source, with the name it was found under."""
    found: dict[str, tuple[str, re.Pattern[str]]] = {}
    for module in MODULES:
        loaded = importlib.import_module(module)
        for name in sorted(vars(loaded)):
            for pattern in _patterns(getattr(loaded, name)):
                found.setdefault(pattern.pattern, (f"{module}.{name}", pattern))
    return found


def _in(ch: str, items: list[tuple[Any, Any]]) -> bool:
    word = ch.isalnum() or ch == "_"
    category = {
        k.CATEGORY_DIGIT: ch.isdigit(),
        k.CATEGORY_NOT_DIGIT: not ch.isdigit(),
        k.CATEGORY_SPACE: ch.isspace(),
        k.CATEGORY_NOT_SPACE: not ch.isspace(),
        k.CATEGORY_WORD: word,
        k.CATEGORY_NOT_WORD: not word,
    }
    negate, hit = False, False
    for op, av in items:
        negate |= op == k.NEGATE
        hit |= (
            (op == k.LITERAL and ord(ch) == av)
            or (op == k.RANGE and av[0] <= ord(ch) <= av[1])
            or (op == k.CATEGORY and category.get(av, False))
        )
    return hit != negate


def _shortest(sub: Any) -> str:
    """A shortest text the parsed *sub* matches, its assertions aside."""
    out = []
    for op, av in sub:
        if op == k.LITERAL:
            out.append(chr(av))
        elif op in (k.NOT_LITERAL, k.ANY):
            out.append("a" if av != ord("a") else "b")
        elif op in (k.IN, k.CATEGORY):
            items = av if op == k.IN else [(op, av)]
            out.append(next((c for c in _CANDIDATES if _in(c, items)), "a"))
        elif op in (k.MAX_REPEAT, k.MIN_REPEAT, k.POSSESSIVE_REPEAT):
            out.append(_shortest(av[2]) * av[0])
        elif op == k.SUBPATTERN:
            out.append(_shortest(av[-1]))
        elif op == k.BRANCH:
            out.append(_shortest(av[1][0]))
    return "".join(out)


def _near_misses(pattern: re.Pattern[str]) -> set[str]:
    """Each alternative's shortest match cut short, alone and with a space after it."""
    tree = parser.parse(pattern.pattern, pattern.flags)
    top = tree.data[0] if len(tree.data) == 1 else None
    alternatives = top[1][1] if top is not None and top[0] == k.BRANCH else [tree]
    out: set[str] = set()
    for alternative in alternatives:
        whole = _shortest(alternative)
        for cut in {len(whole) // 2, len(whole) - 1, len(whole)} - {0}:
            out |= {whole[:cut], whole[:cut] + " "}
    return out


def _line(token: str, n: int) -> str:
    return (token * (n // len(token) + 1))[:n]


def _seconds(pattern: re.Pattern[str], text: str) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        pattern.search(text)
        best = min(best, time.perf_counter() - start)
    return best


def test_every_pattern_a_scanner_module_holds_is_timed() -> None:
    timed = _timed()
    sourced = [f"{module}.{name}" for module in MODULES for name in _pattern_names(module)]
    assert len(sourced) >= 12, f"found only {sourced}: this check would pass vacuously"
    untimed = [name for name in sourced if not _patterns(_resolve(name))]
    assert untimed == [], f"top-level names that build a pattern but yield none to time: {untimed}"
    assert _resolve("personalclaw.scan_remote_exec.REMOTE_EXEC").pattern in timed


def _resolve(dotted: str) -> Any:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


def test_every_scanner_pattern_reads_a_line_in_time_linear_in_its_length() -> None:
    slow: list[str] = []
    for name, pattern in sorted(_timed().values(), key=lambda found: found[0]):
        for token in sorted({*TOKENS, *_near_misses(pattern)}):
            short = _seconds(pattern, _line(token, 1000))
            if short > 0.2:
                slow.append(f"{name} on {token!r}: {short:.2f} s for 1,000 characters")
                continue
            long = _seconds(pattern, _line(token, 4000))
            if long > FLOOR and long > LIMIT * short:
                slow.append(f"{name} on {token!r}: x{long / short:.0f} for four times the text")
    assert slow == [], "patterns that read a long line in more than linear time:\n" + "\n".join(
        slow
    )


def test_the_check_finds_a_pattern_that_backtracks() -> None:
    backtracks = re.compile(r"\bdd\s+[^\n]*\bof=/dev/sd")
    near = _near_misses(backtracks)
    ratios = [
        _seconds(backtracks, _line(t, 4000)) / max(_seconds(backtracks, _line(t, 1000)), 1e-9)
        for t in near
    ]
    assert max(ratios) > LIMIT, "the near misses no longer reach the square-time read"
