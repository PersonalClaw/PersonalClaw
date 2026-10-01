"""``cache_hit_pct``'s DISJOINTNESS evidence must keep resolving to live code.

``stats.cache_hit_pct`` does not merely assert that ``input_tokens`` excludes the cached
tokens; the whole denominator rests on that premise, and the docstring discharges it by
CITING independent places in the tree (eight anchors, tabled below). That evidence block is
load-bearing prose: a reader who cannot follow it has no way to check the arithmetic, and a
reader who follows a citation into unrelated code is actively misled.

Prose citations rot silently. Two of this docstring's citations already did, and neither of
the change's two hand-run audit passes caught it, because nothing executes a docstring:

* ``dashboard/chat_runner.py:3712-3714`` was cited as composing the rendered "cached"
  figure as ``cache_read + cache_creation``. PCS-7 DELETED that pre-summed composition —
  splitting it into ``N read / N written`` is the change's own change — so the citation
  pointed into ACP JSON-string commentary. The cited code did not move; it ceased to exist.
* ``dashboard/chat_runner.py:492-525`` was cited for the ``context_pct`` honesty rule and
  pointed at agent-name resolution and a redaction helper.

So this file executes the evidence block. It used to pin each citation as a LINE RANGE that
had to contain one token, and that form failed both ways. It went red on every unrelated
merge that inserted a line above a cited range (#3742 moved ``chat_runner.py`` by three), and
it stayed GREEN while three ranges drifted off their claims, because the one token still
matched inside the stale range.

Now each citation names a SYMBOL, ``path::Class.method``, the form
``tests/test_sso_design_note_citations.py`` holds the SSO note to. The table below holds, for
each symbol, the exact statements the claim rests on, and the rail requires every one of them
as a whole line inside that symbol's own source, found by parsing the file. A line shift
cannot break a citation. A rename or deletion reds it, and so does any edit that changes what
a cited statement says: ``input_tokens = it`` becoming ``input_tokens = it + cached`` is
precisely the meaning change the disjointness claim forbids.

The table is the rail's own statement of what must hold, written from the CLAIM each citation
makes, never derived from the file it checks — a floor read out of the same code it is meant
to pin would pass on any code at all.

Vacuity floors, because "every citation resolves" is the shape an empty citation set also
returns:

* ``test_the_docstring_really_carries_every_tabled_citation`` proves the docstring cites
  each one, so a deleted citation reds instead of silently shrinking the checked set.
* ``test_the_citation_set_is_exactly_the_tabled_one`` scans the docstring for the citation
  PATTERN and requires it to match the table, so a NEW untabled citation reds rather than
  riding along unchecked.
* ``test_the_docstring_cites_nothing_by_line_number`` keeps the form that rotted out.
* ``TestTheCheckerDiscriminates`` positive-controls ``_what_the_cited_code_lacks``: a real
  symbol that does not say a statement, a missing symbol, a statement that sits in the
  WRONG function, a changed statement, and a shifted but unchanged one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import personalclaw
from personalclaw.stats import cache_hit_pct

SRC = Path(personalclaw.__file__).parent

# (module path relative to the package, symbol, the statements its claim rests on — each an
# exact source line, stripped).
_CITATIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # The process-lifetime tallies stay the only store of the two cache counts.
    (
        "stats.py",
        "Stats._init_counters",
        ('"cache_creation_tokens": 0,', '"cache_read_tokens": 0,'),
    ),
    # `input_tokens` is taken verbatim from the SDK's `usage.input_tokens`, in both paths.
    (
        "llm/anthropic.py",
        "AnthropicProvider._stream_chat",
        ('it = getattr(usage, "input_tokens", None)', "input_tokens = it"),
    ),
    (
        "llm/anthropic.py",
        "AnthropicProvider._complete_chat",
        ('it = getattr(usage, "input_tokens", None)', "input_tokens = it"),
    ),
    # The cache counts come from the SDK's two SEPARATE fields.
    (
        "llm/anthropic.py",
        "_read_cache_usage",
        ('return _int("cache_creation_input_tokens"), _int("cache_read_input_tokens")',),
    ),
    # The bill adds the three buckets, each at its own rate: the one pricing function's.
    (
        "routing/rates.py",
        "ModelRate.cost",
        (
            "(input_tokens or 0) * self.in_per_mtok",
            "+ (cache_read_tokens or 0) * read_rate",
            "+ (cache_creation_tokens or 0) * write_rate",
        ),
    ),
    # The ledger folds the three into three separate aggregate keys.
    (
        "usage_ledger.py",
        "_fold",
        (
            'agg["input_tokens"] += int(row.get("input_tokens", 0) or 0)',
            'agg["cache_read_tokens"] += int(row.get("cache_read_tokens", 0) or 0)',
            'agg["cache_creation_tokens"] += int(row.get("cache_creation_tokens", 0) or 0)',
        ),
    ),
    # Its own counterpart, which adds the same three.
    (
        "routing/rates.py",
        "cache_savings_usd",
        ("(input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)",),
    ),
    # The honesty rule: no measurement prints nothing, never `context 0%`.
    ("dashboard/chat_runner.py", "_turn_complete_line", ("if context_pct is not None:",)),
)

# A ``path::symbol`` inside double backticks.
_CITE_RE = re.compile(r"``([\w./]+\.py::[A-Za-z_][\w.]*)``")
# The retired forms: ``path:NN-MM`` and a bare ``:NN-MM`` continuing the file before it.
_LINE_CITE_RE = re.compile(r"``[\w./]*:\d+(?:-\d+)?``")


def _find(node: ast.AST, parts: list[str]) -> ast.AST | None:
    """The function or class *parts* names under *node* (``["Class", "method"]`` for a member)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.Try, ast.With)):
            found = _find(child, parts)
            if found is not None:
                return found
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if child.name == parts[0]:
                return child if len(parts) == 1 else _find(child, parts[1:])
    return None


def _what_the_cited_code_lacks(
    rel_path: str, symbol: str, statements: tuple[str, ...], *, root: Path = SRC
) -> list[str]:
    """Why one citation no longer holds, or ``[]`` when *symbol* exists and says every statement.

    A statement counts only as a WHOLE stripped line inside the symbol's own source span, so a
    near-miss edit (``input_tokens = it + cached``) and a statement that lives in some OTHER
    function both fail, and a shift of the whole symbol does not.
    """
    source = (root / rel_path).read_text(encoding="utf-8")
    node = _find(ast.parse(source), symbol.split("."))
    if node is None:
        return [f"{rel_path}::{symbol} no longer exists"]
    start, end = getattr(node, "lineno"), getattr(node, "end_lineno")
    said = {line.strip() for line in source.splitlines()[start - 1 : end]}
    return [f"{rel_path}::{symbol} no longer says {s!r}" for s in statements if s not in said]


def _spelling(rel_path: str, symbol: str) -> str:
    return f"{rel_path}::{symbol}"


def _doc() -> str:
    doc = cache_hit_pct.__doc__
    assert doc, "cache_hit_pct lost its docstring — the evidence block IS the deliverable"
    return doc


class TestTheEvidenceBlockStillResolves:
    def test_every_cited_symbol_still_says_what_its_claim_rests_on(self) -> None:
        """The rail proper: follow each citation into the code and check the claim."""
        broken = [
            problem
            for rel, symbol, statements in _CITATIONS
            for problem in _what_the_cited_code_lacks(rel, symbol, statements)
        ]
        assert not broken, "cache_hit_pct cites code that was renamed or changed:\n" + "\n".join(
            broken
        )

    def test_the_docstring_really_carries_every_tabled_citation(self) -> None:
        """Vacuity floor: a citation deleted from the prose must red, not shrink the set."""
        doc = _doc()
        missing = [
            _spelling(rel, symbol)
            for rel, symbol, _ in _CITATIONS
            if f"``{_spelling(rel, symbol)}``" not in doc
        ]
        assert not missing, f"tabled citations absent from the docstring: {missing}"

    def test_the_citation_set_is_exactly_the_tabled_one(self) -> None:
        """Vacuity floor: a NEW citation must be tabled, not ride along unchecked."""
        found = set(_CITE_RE.findall(_doc()))
        assert found, "the citation pattern matched nothing — the regex, not the docstring, broke"
        assert found == {_spelling(rel, symbol) for rel, symbol, _ in _CITATIONS}
        assert len(_CITATIONS) == 8

    def test_the_docstring_cites_nothing_by_line_number(self) -> None:
        stale = _LINE_CITE_RE.findall(_doc())
        assert not stale, f"cite the function instead of a line range: {stale}"


class TestTheCheckerDiscriminates:
    def test_a_real_symbol_that_does_not_say_it_is_reported(self) -> None:
        lacks = _what_the_cited_code_lacks(
            "llm/anthropic.py", "_read_cache_usage", ("if context_pct is not None:",)
        )
        assert lacks == [
            "llm/anthropic.py::_read_cache_usage no longer says 'if context_pct is not None:'"
        ]

    def test_a_renamed_symbol_is_reported(self) -> None:
        assert _what_the_cited_code_lacks("pricing.py", "estimate_cost_v2", ()) == [
            "pricing.py::estimate_cost_v2 no longer exists"
        ]

    def test_scope_meaning_and_drift(self, tmp_path: Path) -> None:
        """The three properties the table relies on, on a module this test controls."""
        body = (
            "class P:\n"
            "    def stream(self, it):\n"
            "        input_tokens = it\n"
            "        return input_tokens\n"
            "\n"
            "    def other(self, it):\n"
            "        cached = it\n"
            "        return cached\n"
        )
        (tmp_path / "m.py").write_text(body, encoding="utf-8")
        assert (
            _what_the_cited_code_lacks("m.py", "P.stream", ("input_tokens = it",), root=tmp_path)
            == []
        )
        # SCOPE: the statement exists in the file, but not in the function that was cited.
        assert _what_the_cited_code_lacks("m.py", "P.other", ("input_tokens = it",), root=tmp_path)
        # MEANING: a near-miss edit of the cited statement reds, even though the old text is a
        # prefix of the new line.
        (tmp_path / "m.py").write_text(
            body.replace("input_tokens = it\n", "input_tokens = it + cached\n"), encoding="utf-8"
        )
        assert _what_the_cited_code_lacks("m.py", "P.stream", ("input_tokens = it",), root=tmp_path)
        # DRIFT: the same code 40 lines lower still resolves.
        (tmp_path / "m.py").write_text("\n" * 40 + body, encoding="utf-8")
        assert (
            _what_the_cited_code_lacks("m.py", "P.stream", ("input_tokens = it",), root=tmp_path)
            == []
        )
