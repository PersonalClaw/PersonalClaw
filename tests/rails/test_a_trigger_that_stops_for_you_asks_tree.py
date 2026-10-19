"""No surface or doc tells anyone to look for a window nothing opens.

The tree half of ``tests/test_a_trigger_that_stops_for_you_asks.py``, which drives a trigger that
stops at a sign-in page through the real handlers. The site-level row and the mirror's banner once
told the person to sign in "in the handoff window", a window nothing opens. These scan the shipped
source, the web client, the docs and the tests for that phrase, so they are a rail: they run once
per CI run, in the rails job.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from rails_repo import REPO

_PACKAGE = REPO / "src" / "personalclaw"
_WEB = REPO / "web" / "src"
_DOCS = REPO / "docs"
_TESTS = REPO / "tests"
_SUFFIXES = {
    _PACKAGE: (".py", ".md", ".json", ".html", ".ts", ".tsx"),
    _WEB: (".ts", ".tsx", ".css", ".md"),
    _DOCS: (".md",),
}
_SKIP_DIRS = {"node_modules", "__pycache__", "dist", ".venv"}
#: The two files that quote the phrase on purpose: this rail, and the suite module whose
#: docstring records where it shipped.
_QUOTERS = {
    Path(__file__).resolve(),
    (_TESTS / "test_a_trigger_that_stops_for_you_asks.py").resolve(),
}
#: A string literal split across a join: `"… handoff "\n    "window"` (Python), `'handoff ' +\n
#: 'window'` (TypeScript), with an optional Python prefix on the second half.
_JOIN = re.compile(r"""["'`]\s*\+?\s*(?:[rRbBfFuU]{1,2})?["'`]""")
#: A comment continued on the next line: `# … handoff\n    # window`.
_COMMENT_LEADER = re.compile(r"\n\s*(?:#|//|\*)\s?")
_PHRASE = "handoff window"


def _prose(text: str) -> str:
    """The text as a reader meets it: string joins and comment leaders removed, whitespace (JSX
    text wraps across lines) collapsed."""
    return re.sub(r"\s+", " ", _COMMENT_LEADER.sub(" ", _JOIN.sub("", text))).lower()


def _kept(path: Path) -> bool:
    return not _SKIP_DIRS.intersection(path.relative_to(REPO).parts) and (
        path.resolve() not in _QUOTERS
    )


def _scanned_texts() -> dict[str, str]:
    """Each scanned file's text, by repository-relative path. The package, the web client and the
    docs come from the source index; ``tests/``, which it does not hold, is walked beside it."""
    indexed = (
        *source_index.package_any_files(),
        *source_index.web_files(),
        *source_index.docs_files(),
    )
    texts = {
        path.relative_to(REPO).as_posix(): source_index.text(path)
        for root, suffixes in _SUFFIXES.items()
        for path in source_index.under(indexed, root, suffixes)
        if _kept(path)
    }
    for path in sorted(_TESTS.rglob("*.py")):
        if path.is_file() and _kept(path):
            texts[path.relative_to(REPO).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    return texts


def test_the_phrase_scan_sees_the_phrase_however_the_source_breaks_it():
    """Positive control: each way the phrase shipped on `main`, as the source broke it."""
    python_join = 'x = (\n    f"Open the site in the handoff "\n    "window and sign in."\n)'
    jsx_wrap = "<p>\n            site in the handoff\n            window and sign in;\n</p>"
    ts_join = "const s = 'open the handoff ' +\n  'window'"
    comment = "# caller cannot accidentally launch the handoff\n    # window against another"
    for sample in (python_join, jsx_wrap, ts_join, comment):
        assert _PHRASE in _prose(sample), sample


def test_no_surface_or_doc_names_a_handoff_window():
    """`browse/mirror.py` (the expired-session row), `BrowseMirror.tsx` (its banner) and
    `browse/handoff.py` (the headful-launch helper nothing called) all named it."""
    texts = _scanned_texts()
    # Non-vacuity: the three files that carried it are among those read, and the scan is broad.
    for carried in (
        "src/personalclaw/browse/mirror.py",
        "src/personalclaw/browse/handoff.py",
        "web/src/pages/dashboard/widgets/BrowseMirror.tsx",
    ):
        assert carried in texts, carried
    assert len(texts) > 2000, len(texts)

    hits = sorted(name for name, text in texts.items() if _PHRASE in _prose(text))
    assert hits == []
