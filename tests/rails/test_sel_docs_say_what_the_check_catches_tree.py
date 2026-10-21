"""The security documents say what the audit log's integrity check catches, and what it does not.

They called the Security Event Log "HMAC-chained" and "tamper-evident", which reads as "a
change to the log shows". `verify_integrity` in ``sel.py`` recomputes each entry's own HMAC and
allows breaks in the ``prev_hash`` chain on purpose, since several processes append to one log,
so an entry edited in place is caught and a deleted one is not. The behaviour half is
``tests/test_sel.py::TestVerifyIntegrityExtras``. This half reads the documents, so it is a rail.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from rails_repo import REPO

_ARCHITECTURE = REPO / "docs" / "architecture" / "security.md"
_THREAT_MODEL = REPO / "docs" / "security" / "threat-model.md"
_LIMITATIONS = REPO / "docs" / "security" / "limitations.md"
_DOCS = (REPO / "README.md", REPO / "SECURITY.md", _ARCHITECTURE, _THREAT_MODEL, _LIMITATIONS)

# A claim about the whole log: "HMAC-chained" or "hash-chained", or "tamper-evident" said of the
# log, the audit or the SEL, before it or after it, rather than of its entries.
_WHOLE_LOG_CLAIM = re.compile(
    r"\b(?:HMAC|hash)-chained\b"
    r"|tamper-evident[\s(]+(?:HMAC\)\s+)?(?:security\s+event\s+|event\s+|audit\s+)?"
    r"(?:log|audit|SEL)\b(?!\s+entr)"
    r"|\b(?:log|audit|SEL)\s+(?:is|stays|remains)\s+tamper-evident\b",
    re.I,
)


def _text(path: Path) -> str:
    """A file's text: through the suite's source index where it holds the file, else read here."""
    return source_index.text(path) if source_index.holds(path) else path.read_text("utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_no_security_document_claims_the_whole_log_shows_a_change():
    for path in _DOCS:
        hits = _WHOLE_LOG_CLAIM.findall(_flat(_text(path)))
        assert not hits, (
            f"{path.relative_to(REPO)} says {hits!r}: the integrity check catches an entry edited "
            "in place, not one deleted, so say that of the entries"
        )


def test_the_architecture_says_what_verify_integrity_catches_and_misses():
    text = _text(_ARCHITECTURE)
    start = text.index("## Audit — the Security Event Log")
    section = _flat(text[start : text.index("\n## ", start + 1)])
    for claim in ("verify_integrity", "prev_hash", "edited in place", "deleted", "sel_archive/"):
        assert claim in section, f"the SEL section must name {claim!r}"


def test_the_threat_model_says_a_deleted_entry_goes_unreported():
    text = _text(_THREAT_MODEL)
    rows = [
        _flat(block)
        for block in re.split(r"\n(?=- \*\*|\| )", text)
        if "sel.py::SecurityEventLog" in block
    ]
    assert len(rows) == 2, "the threat model cites the SEL in its §5 list and its ASI10 row"
    for row in rows:
        assert "deleted" in row, f"this passage must say a deleted entry is not caught: {row[:80]}"
