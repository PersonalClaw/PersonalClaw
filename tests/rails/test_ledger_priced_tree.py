"""The source-tree checks split from
``tests/test_ledger_priced.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import pathlib

import source_index
from ledger_priced_shared import DISCLOSURE, MONEY_PRODUCERS, _consumer_reason, _reads_money
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


#: Absolute lower bounds, independent of the parse that derives the expected set. A rail that found
#: zero sites would pass every assertion below while measuring nothing, so the census must clear a
#: floor it cannot compute for itself. Set BELOW the true counts on purpose — this is a floor, not a
#: pin, so ordinary growth of the tree never reds it.
#:
#: KNOWN LIMITATION: these two integers are the one thing the rail cannot defend. A contributor who
#: lowers them can make the census vacuous. What the pairing DOES defend is the accident: the byte
#: census below re-counts the same producers with `str.count` and no AST at all, so a broken or
#: over-narrowed parse is caught by an instrument that does not share the parser.
#:
#: Measured at the time of writing: 15 consumers, 5 of which read a dollar, 25 producer-call
#: occurrences by byte count. The floors sit below those so ordinary refactoring never reds them.
MIN_MONEY_CONSUMERS = 10


MIN_MONEY_READ_SITES = 4


def _python_sources() -> list[pathlib.Path]:
    """Every package module, from the suite's one source index."""
    return list(source_index.package_files())


def _money_consumers() -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """Every function in `src/` that handles a ledger money figure.

    Returns ``(consumers, violations)``. A *consumer* is a function that either calls one of
    :data:`MONEY_PRODUCERS`, IS one of them, or is a method of a :data:`MONEY_CLASSES` dataclass. A
    *violation* is a consumer that reads a :data:`MONEY_KEYS` value without referencing
    :data:`DISCLOSURE` anywhere in its own body.
    """
    consumers: list[tuple[str, int, str]] = []
    violations: list[tuple[str, int, str]] = []
    for path in _python_sources():
        rel = str(path.relative_to(_SRC.parent.parent))
        tree = source_index.tree(path)
        for fn, cls in _functions(tree):
            reason = _consumer_reason(fn, cls)
            if reason is None:
                continue
            consumers.append((rel, fn.lineno, f"{cls + '.' if cls else ''}{fn.name}"))
            body = ast.unparse(fn)
            if _reads_money(fn) and DISCLOSURE not in body:
                violations.append((rel, fn.lineno, f"{cls + '.' if cls else ''}{fn.name}"))
    return consumers, violations


def _functions(tree: ast.AST):
    """Yield ``(function_node, enclosing_class_name)`` for every function in the module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, node.name
        elif isinstance(node, ast.Module):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, ""


def test_no_consumer_reads_a_ledger_dollar_without_consulting_priced():
    """The rail. A money figure that hides its own incompleteness is the defect.

    Reported with file:line so a red names the consumer rather than the count.
    """
    consumers, violations = _money_consumers()
    assert not violations, "these read a ledger dollar without consulting `priced`: " + ", ".join(
        f"{path}:{line} {name}" for path, line, name in violations
    )
    # Vacuity floor, direction one: the census must actually have found consumers to check.
    assert len(consumers) >= MIN_MONEY_CONSUMERS, (
        f"the census found only {len(consumers)} money consumers "
        f"(floor {MIN_MONEY_CONSUMERS}) — the parse is broken or over-narrowed, so a green "
        f"result above measured nothing: {consumers}"
    )
    reading = [c for c in consumers if _reads_money_at(c)]
    assert len(reading) >= MIN_MONEY_READ_SITES, (
        f"only {len(reading)} consumers actually READ a dollar (floor {MIN_MONEY_READ_SITES}) — "
        f"a census of consumers that read nothing cannot catch a consumer that reads wrongly"
    )


def _reads_money_at(consumer: tuple[str, int, str]) -> bool:
    """Whether the consumer at ``(path, lineno, name)`` reads a money key. Re-parsed by LINE, so
    this second pass cannot silently agree with the first by sharing its node objects."""
    path, lineno, _name = consumer
    root = _SRC.parent.parent / path
    tree = ast.parse(root.read_text(encoding="utf-8"), filename=str(root))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == lineno:
            return _reads_money(node)
    return False


def test_the_census_floor_holds_under_a_byte_count_that_shares_no_parser():
    """Vacuity floor, direction two: the same producers, counted without the AST.

    The floors above are integers this file owns, and an AST walker that quietly stopped matching
    would make them trivially satisfiable in the wrong direction — a green rail over an empty set.
    `str.count` over the same files is a second instrument with no parser in common, so a broken
    parse reds here even when the walker reports a plausible-looking zero. It counts a correlated
    quantity (call SYNTAX, not resolved consumers) against the same absolute floor, which is the
    point: two instruments that can only agree by the producers really being there.
    """
    calls = 0
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for producer in MONEY_PRODUCERS:
            calls += text.count(f"{producer}(")
    assert calls >= MIN_MONEY_CONSUMERS, (
        f"a byte census of {sorted(MONEY_PRODUCERS)} call syntax found {calls} occurrences, "
        f"below the floor {MIN_MONEY_CONSUMERS} — either the tree shrank or the floors are wrong"
    )
    # And the disclosure really is in the primitive's own source, so `DISCLOSURE` is not a typo that
    # would make every `DISCLOSURE not in body` check above pass vacuously.
    reader = (_SRC / "ledger" / "reader.py").read_text(encoding="utf-8")
    assert f'"{DISCLOSURE}"' in reader, "the primitive does not emit the key this rail polices"
