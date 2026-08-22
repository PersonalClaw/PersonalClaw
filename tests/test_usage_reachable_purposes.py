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
#: The app-name half is NOT resolvable to a fixed purpose by hand — see :func:`_app_names`. It
#: used to be hand-resolved here as "an app name → APP_PURPOSE", which was wrong and hid a real
#: writer for a whole purpose; the app names are now censused out of the source instead.
_RESOLVED_EXPRESSIONS = {
    "_src": ("channel", "cron"),
    "source": ("chat",),  # plus every `app=` literal — see `_app_names`
}

#: A non-empty ``app=`` string literal. ``dashboard/state.py``'s ``session._app = app`` is the
#: ONE assignment of the field the chat seam forwards, so every ``app=`` literal in the tree is a
#: value ``source`` can take. Comment lines are stripped first: three comments in `gateway.py` /
#: `chat_runner.py` describe `app="loop"` in prose, and a text scan that counted those would look
#: like it had measured something when the real call sites had moved.
_APP_ARG = re.compile(r"""app\s*=\s*["']([a-z][a-z0-9_-]*)["']""")


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


def _code_lines(text: str) -> str:
    """``text`` with whole-line comments dropped, so a scanner cannot read prose as a call site."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _app_names() -> set[str]:
    """Every ``app=`` literal in the tree — i.e. every value ``session._app`` can hold.

    ``chat_runner`` passes ``session._app or "chat"`` as the turn ``source``, and
    ``dashboard/state.py:session._app = app`` is the only assignment of that field, so an
    ``app=`` literal IS a turn-ledger source. This is censused rather than hand-resolved because
    hand-resolving it as "an app name, therefore APP_PURPOSE" is precisely the mistake that let
    the loop engine write ``loop`` turn rows for releases while ``UNWRITTEN_PURPOSES`` claimed
    nothing could: an app name only reaches APP_PURPOSE when it is NOT already a key of
    ``PURPOSE_BY_SOURCE`` (``purpose_for_source`` checks that map FIRST).
    """
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        found.update(_APP_ARG.findall(_code_lines(path.read_text(encoding="utf-8"))))
    return found


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
                    if expression == "source":
                        # The chat seam forwards `session._app`, so every app name is a source.
                        found.update(_app_names())
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


def test_the_app_name_census_is_not_vacuous() -> None:
    """The floor for the app half: an empty app census re-hides the writer it exists to find."""
    apps = _app_names()
    assert apps, "no `app=` literal found — the regex or the worker-session call sites moved"
    # `loops` is plan_walkthrough's planner session: a real app name that is NOT a source key, so
    # it proves the census sees BOTH kinds and the collision test below is discriminating.
    assert "loops" in apps, apps
    # Comments must not be readable as call sites. `gateway.py` mentions `app="loop"` in prose and
    # creates no session with it, so it is the live proof that the stripping discriminates: the raw
    # text matches and the code-only text must not.
    gw = (_SRC / "gateway.py").read_text(encoding="utf-8")
    assert _APP_ARG.findall(gw), "gateway.py no longer mentions an app name; re-derive this guard"
    assert not _APP_ARG.findall(
        _code_lines(gw)
    ), "comment stripping is broken — gateway.py's prose is counting as a writer"


def test_loop_has_a_turn_ledger_writer_via_the_worker_session_app() -> None:
    """The loop engine IS a turn-ledger writer, through its worker session's ``app``.

    Two prior MRT-3 sessions recorded the opposite ("the turn ledger has no loop rows at all")
    because the census only resolved LITERAL ``source=`` arguments and the loop's spelling is a
    runtime value. The chain, each hop asserted below: ``loop/manager.py`` names the worker
    session ``app="loop"`` -> ``state.py`` stores it as ``session._app`` -> ``chat_runner``
    passes ``session._app or "chat"`` as the turn ``source``. ``purpose_for_source`` then hits
    ``PURPOSE_BY_SOURCE["loop"]`` on the first lookup, so it is the ``loop`` purpose and not
    ``app``.
    """
    manager = (_SRC / "loop" / "manager.py").read_text(encoding="utf-8")
    assert manager.count('app="loop"') >= 2, "the main worker + task worker app names moved"
    state = (_SRC / "dashboard" / "state.py").read_text(encoding="utf-8")
    assert "session._app = app" in state, "the _app assignment moved; re-derive this chain"
    runner = (_SRC / "dashboard" / "chat_runner.py").read_text(encoding="utf-8")
    assert 'source=getattr(session, "_app", "") or "chat"' in runner, "the chat seam moved"

    assert "loop" in _writer_sources()
    assert PURPOSE_BY_SOURCE["loop"] == "loop"
    assert "loop" not in UNWRITTEN_PURPOSES, "loop has a writer — it cannot be excluded"
    assert "loop" in reachable_purposes()


def test_reachable_purposes_omits_the_unwritten_and_keeps_the_rest() -> None:
    got = set(reachable_purposes())
    assert not (got & UNWRITTEN_PURPOSES), f"{got & UNWRITTEN_PURPOSES} cannot be filled"
    # The ones with live writers are all present, so the exclusion is narrow, not a blanket.
    assert {"interactive", "background", APP_PURPOSE} <= got, got
    assert got, "reachable_purposes() must never be empty — a chart with no rows at all"


def test_the_app_names_that_collide_with_the_source_vocabulary_are_exactly_these() -> None:
    """A ratchet, because a colliding app name silently RE-BUCKETS spend.

    An ``app=`` literal that is also a ``PURPOSE_BY_SOURCE`` key does not land in ``app`` — it
    lands in that key's purpose, and ``app_sources`` never censuses its name. That is correct for
    ``loop`` (the loop engine really is loop spend) and would be wrong for, say, an installed app
    named ``cron``. Pinning the set makes each new collision a decision rather than a silent
    reassignment; the fix for a red is to rename the app or to accept the bucket here, never to
    delete this test.
    """
    colliding = {a for a in _app_names() if a in PURPOSE_BY_SOURCE}
    assert colliding == {"loop"}, (
        f"app name(s) {sorted(colliding)} collide with the turn `source` vocabulary, so their "
        f"spend is bucketed as {sorted(PURPOSE_BY_SOURCE[a] for a in colliding)} rather than "
        f"censused under `app`. Confirm that is intended."
    )
    # The complement is non-empty, so the collision filter is discriminating rather than total.
    assert _app_names() - colliding, "every app name collides — the filter is measuring nothing"


def test_an_app_source_still_reaches_the_app_purpose() -> None:
    """``chat_runner`` passes ``session._app or "chat"``, so an unrecognized source is an app
    name by design. APP_PURPOSE must therefore stay reachable even though no writer passes the
    literal string "app"."""
    assert APP_PURPOSE not in PURPOSE_BY_SOURCE.values()
    assert APP_PURPOSE in reachable_purposes()
