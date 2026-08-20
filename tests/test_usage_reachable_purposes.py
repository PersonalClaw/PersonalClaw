"""``reachable_purposes()`` must match the writers that actually exist (MRT-3).

A spend chart that renders a row nothing can ever fill is not showing "no spend" — it is
showing a bucket with no writer, and the two read identically. ``PURPOSE_BY_SOURCE`` declares
five purposes; only some have a live turn-ledger writer, so :data:`UNWRITTEN_PURPOSES` names
the rest and this file keeps that list honest in BOTH directions:

* a purpose excluded as unwritten that in fact gains a writer  → the exclusion is now a LIE
  hiding real spend, which is the worse failure and the one this file exists to catch;
* a purpose advertised as reachable with no writer             → a permanent zero row.

The census reads the real ``record_from_event`` call sites out of the source, because the thing
under test is *which sources the code passes* — importing the modules would prove only that
they import.
"""

from __future__ import annotations

import re
from pathlib import Path

from personalclaw.routing.usage import (
    APP_PURPOSE,
    PURPOSE_BY_SOURCE,
    UNWRITTEN_PURPOSES,
    reachable_purposes,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The seam every turn row goes through. Both spellings appear (a direct call and the thin
#: chat wrapper), so the census keys on the seam name rather than on any one module.
_SEAM = re.compile(r"record_from_event\s*\(")
#: `source=` as passed at a call site: a literal, or an expression we resolve by hand below.
_SOURCE_ARG = re.compile(r"source\s*=\s*(?:\"([a-z_]+)\"|'([a-z_]+)'|([A-Za-z_][\w.]*))")

#: Expressions (not literals) that reach the seam, resolved by reading the code once:
#:   gateway.py       `source=_src`                                → "channel" | "cron"
#:   chat_runner.py   `source=source` ← `session._app or "chat"`    → "chat" | an app name
#: An app name is not a literal purpose — it maps to APP_PURPOSE by design, so it is recorded
#: here as that purpose rather than as a source spelling.
_RESOLVED_EXPRESSIONS = {
    "_src": ("channel", "cron"),
    "source": ("chat",),  # plus an app name → APP_PURPOSE, asserted separately
}


def _call_arguments(text: str, open_paren: int) -> str:
    """The argument text of the call whose ``(`` is at ``open_paren``, by paren balance.

    Scoped to the CALL, not to the file. Scanning a whole file that merely contains the seam
    swept up every unrelated ``source=`` in it — ``gateway.py`` alone yielded ``dashboard`` and
    ``gateway``, and ``watchdog.py`` passes ``source="loop"`` to an INBOX helper, which would
    have made the loop assertion below claim a turn-ledger writer that does not exist.
    """
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren : i + 1]
    return ""


def _writer_sources() -> set[str]:
    """Every ``source`` value the live turn-ledger call sites can pass."""
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for match in _SEAM.finditer(text):
            args = _call_arguments(text, match.end() - 1)
            for literal_dq, literal_sq, expression in _SOURCE_ARG.findall(args):
                name = literal_dq or literal_sq
                if name:
                    found.add(name)
                elif expression in _RESOLVED_EXPRESSIONS:
                    found.update(_RESOLVED_EXPRESSIONS[expression])
    return found


def test_the_writer_census_is_not_vacuous() -> None:
    """The floor: if the census finds nothing, every assertion below passes for free."""
    sources = _writer_sources()
    assert len(sources) >= 4, f"census found only {sources} — the seam or regex has drifted"
    # Sources we know are live; if these ever vanish the census is measuring the wrong thing.
    assert {"background", "subagent", "cli"} <= sources, sources


def test_the_census_is_scoped_to_the_call_and_not_the_file() -> None:
    """An unrelated ``source=`` in a seam-bearing file must NOT count as a writer.

    ``watchdog.py`` passes ``source="loop"`` to ``emit_attention_item`` — an INBOX source, not a
    turn row. A file-scoped census (the first cut of this test) also picked up ``dashboard`` and
    ``gateway`` from unrelated calls in ``gateway.py``. Both would be false writers, and the
    ``loop`` one would have silently inverted the finding this file records.
    """
    sources = _writer_sources()
    assert (
        "dashboard" not in sources and "gateway" not in sources
    ), f"census leaked non-seam sources {sources} — it is matching per FILE, not per CALL"
    # The innocent line really is there, so the exclusion above is doing work, not vacuous.
    watchdog = (_SRC / "loop" / "watchdog.py").read_text(encoding="utf-8")
    assert 'source="loop"' in watchdog, "the innocent inbox line moved; re-derive this guard"
    assert not _SEAM.search(watchdog), "watchdog gained a turn-ledger call; re-scope this guard"


def test_every_unwritten_purpose_really_has_no_writer() -> None:
    """The load-bearing direction: an exclusion that hides REAL spend.

    If a writer appears for a purpose still listed in ``UNWRITTEN_PURPOSES``, the surface is
    silently dropping a row that now has data. Fix by removing it from the set — not by
    relaxing this test.
    """
    sources = _writer_sources()
    written_purposes = {PURPOSE_BY_SOURCE[s] for s in sources if s in PURPOSE_BY_SOURCE}
    wrongly_excluded = written_purposes & UNWRITTEN_PURPOSES
    assert not wrongly_excluded, (
        f"{sorted(wrongly_excluded)} now HAS a turn-ledger writer but is still listed as "
        f"unwritten, so reachable_purposes() hides a row that has real spend. "
        f"Remove it from UNWRITTEN_PURPOSES. Writer sources found: {sorted(sources)}"
    )


def test_loop_has_no_turn_ledger_writer_today() -> None:
    """The measurement behind excluding ``loop`` — stated so its basis is visible.

    The loop engine records nothing to the turn ledger: its inferences resolve under the
    ``loops`` guard axis into ``model_calls.jsonl``, which the fold censuses into
    ``uncounted`` rather than summing. If this test fails, loop spend became summable and the
    cockpit's "~$X this run" clause is unblocked — see MODEL-ROUTING-TELEMETRY's MRT-3 log.
    """
    assert "loop" not in _writer_sources()
    assert "loop" in UNWRITTEN_PURPOSES


def test_reachable_purposes_omits_the_unwritten_and_keeps_the_rest() -> None:
    got = set(reachable_purposes())
    assert not (got & UNWRITTEN_PURPOSES), f"{got & UNWRITTEN_PURPOSES} cannot be filled"
    # The ones with live writers are all present, so the exclusion is narrow, not a blanket.
    assert {"interactive", "background", APP_PURPOSE} <= got, got
    assert got, "reachable_purposes() must never be empty — a chart with no rows at all"


def test_an_app_source_still_reaches_the_app_purpose() -> None:
    """``chat_runner`` passes ``session._app or "chat"``, so an unrecognized source is an app
    name by design. APP_PURPOSE must therefore stay reachable even though no writer passes the
    literal string "app"."""
    assert APP_PURPOSE not in PURPOSE_BY_SOURCE.values()
    assert APP_PURPOSE in reachable_purposes()
