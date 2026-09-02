"""PCS-7 — ``cache_hit_pct``'s DISJOINTNESS evidence must keep resolving to live code.

``stats.cache_hit_pct`` does not merely assert that ``input_tokens`` excludes the cached
tokens; the whole denominator rests on that premise, and the docstring discharges it by
CITING independent places in the tree at ``path:line-range`` (eight anchors, tabled below).
That evidence block is load-bearing prose: a reader who cannot follow it has no way to check
the arithmetic, and a reader who follows a citation into unrelated code is actively misled.

Prose citations rot silently. Two of this docstring's citations already did, and neither of
the atom's two hand-run audit passes caught it, because nothing executes a docstring:

* ``dashboard/chat_runner.py:3712-3714`` was cited as composing the rendered "cached"
  figure as ``cache_read + cache_creation``. PCS-7 DELETED that pre-summed composition —
  splitting it into ``N read / N written`` is the atom's own change — so the citation
  pointed into ACP JSON-string commentary. The cited code did not move; it ceased to exist.
* ``dashboard/chat_runner.py:492-525`` was cited for the ``context_pct`` honesty rule and
  pointed at agent-name resolution and a redaction helper.

So this file executes the evidence block. For every citation, the range is read FROM DISK
and must still contain the token the docstring claims is there — which catches a line
shift and a rename alike, the two ways such a citation dies.

The table below is the rail's own statement of what must hold, written from the CLAIM each
citation makes, never derived from the file it checks — a floor read out of the same range
it is meant to pin would pass on any range at all.

Vacuity floors, because "every citation resolves" is the shape an empty citation set also
returns:

* ``test_the_docstring_really_carries_every_tabled_citation`` proves the docstring cites
  each one, so a deleted citation reds instead of silently shrinking the checked set.
* ``test_the_citation_set_is_exactly_the_tabled_one`` scans the docstring for the citation
  PATTERN and requires the count to match the table, so a NEW untabled citation reds
  rather than riding along unchecked.
* ``test_the_checker_discriminates`` positive-controls ``_range_contains`` two ways: a
  real repo range that genuinely lacks a token must be False, and a synthetic file whose
  token sits just outside the range must be False while the containing range is True.
"""

from __future__ import annotations

import re
from pathlib import Path

import personalclaw
from personalclaw.stats import cache_hit_pct

SRC = Path(personalclaw.__file__).parent

# (module path relative to the package, start, end, token the docstring CLAIMS is there,
#  how the citation is spelled in the prose).
#
# ``:715-717`` is cited as a bare "twin" continuation of the preceding anthropic.py
# citation, so its prose spelling carries no path. That is deliberate in the docstring and
# the table mirrors it rather than normalising it away.
_CITATIONS = (
    ("stats.py", 43, 44, "cache_read_tokens", "stats.py:43-44"),
    ("llm/anthropic.py", 529, 531, "input_tokens = it", "llm/anthropic.py:529-531"),
    ("llm/anthropic.py", 715, 717, "input_tokens = it", ":715-717"),
    ("llm/anthropic.py", 84, 98, "cache_read_input_tokens", "llm/anthropic.py:84-98"),
    ("pricing.py", 106, 113, "cache_read_rate", "pricing.py:106-113"),
    ("usage_ledger.py", 197, 200, "cache_creation_tokens", "usage_ledger.py:197-200"),
    ("pricing.py", 166, 168, "cache_creation_tokens", "pricing.py:166-168"),
    (
        "dashboard/chat_runner.py",
        635,
        636,
        "context_pct is not None",
        "dashboard/chat_runner.py:635-636",
    ),
)

# Any ``path:NN-MM`` or bare ``:NN-MM`` inside double backticks.
_CITE_RE = re.compile(r"``([\w./]*:\d+-\d+)``")


def _range_contains(rel_path: str, start: int, end: int, token: str, *, root: Path = SRC) -> bool:
    """Is *token* present in *rel_path* lines *start*..*end* inclusive (1-based)?"""
    lines = (root / rel_path).read_text(encoding="utf-8").splitlines()
    return token in "\n".join(lines[start - 1 : end])


def _doc() -> str:
    doc = cache_hit_pct.__doc__
    assert doc, "cache_hit_pct lost its docstring — the evidence block IS the deliverable"
    return doc


class TestTheEvidenceBlockStillResolves:
    def test_every_cited_range_still_contains_what_it_claims(self) -> None:
        """The rail proper: follow each citation into the file and check the claim."""
        broken = [
            f"{spelling} -> {rel}:{start}-{end} no longer contains {token!r}"
            for rel, start, end, token, spelling in _CITATIONS
            if not _range_contains(rel, start, end, token)
        ]
        assert not broken, "cache_hit_pct cites code that moved or was renamed:\n" + "\n".join(
            broken
        )

    def test_the_docstring_really_carries_every_tabled_citation(self) -> None:
        """Vacuity floor: a citation deleted from the prose must red, not shrink the set."""
        doc = _doc()
        missing = [spelling for *_, spelling in _CITATIONS if f"``{spelling}``" not in doc]
        assert not missing, f"tabled citations absent from the docstring: {missing}"

    def test_the_citation_set_is_exactly_the_tabled_one(self) -> None:
        """Vacuity floor: a NEW citation must be tabled, not ride along unchecked."""
        found = set(_CITE_RE.findall(_doc()))
        assert found, "the citation pattern matched nothing — the regex, not the docstring, broke"
        assert found == {spelling for *_, spelling in _CITATIONS}


class TestTheCheckerDiscriminates:
    def test_the_checker_discriminates(self, tmp_path: Path) -> None:
        """Positive control: ``_range_contains`` must answer False on a wrong range."""
        # (a) A real repo range that genuinely lacks the token.
        assert not _range_contains("llm/anthropic.py", 84, 98, "context_pct is not None")
        # (b) A synthetic file where the token sits one line OUTSIDE the range.
        (tmp_path / "m.py").write_text("a\nb\nTOKEN\n", encoding="utf-8")
        assert not _range_contains("m.py", 1, 2, "TOKEN", root=tmp_path)
        assert _range_contains("m.py", 1, 3, "TOKEN", root=tmp_path)
