"""The source-tree checks split from
``tests/test_resilience_degraded_lint.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest
import source_index
from rails_repo import REPO
from resilience_degraded_lint_shared import _CALL_SITE_SURFACES

_SRC = REPO / "src" / "personalclaw"


_CALL_RE = re.compile(r"\bone_shot_completion\s*\(")


def _code_only(path: Path) -> str:
    """*path*'s source with comments and string literals dropped.

    🪤 A RAW-FILE REGEX COUNTS PROSE. The bundled `ollama-models` provider
    documents that its native `format` option is what `one_shot_completion(output_type=…)`
    passes — four times, all in comments and docstrings, never once as a call — and the raw
    `re.search` read every one of them as a call site. The census then demanded a degraded
    contract for a file that has no model-dependent surface at all, and satisfying it would
    have written a FALSE fact into `_CALL_SITE_SURFACES`: the whole value of that map is that
    each entry is a real call whose no-model floor someone judged.

    Tokenizing is the fix rather than rewording the prose, because the next file to mention
    the function in a comment would hit the same wall. Docstrings are STRING tokens and are
    dropped with the rest — a name inside a string is not a call. An unparseable file falls
    back to its raw text: a syntax error must not make a real call site invisible.
    """
    text = source_index.text(path)
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline))
    except (SyntaxError, tokenize.TokenError):
        return text
    return "\n".join(t.string for t in tokens if t.type not in (tokenize.COMMENT, tokenize.STRING))


def _files_calling_one_shot() -> set[str]:
    """Repo-relative (posix) paths of every source file that CALLS
    one_shot_completion — excluding llm_helpers.py, which DEFINES it."""
    hits: set[str] = set()
    for path in source_index.package_files():
        if path.name == "llm_helpers.py":
            continue
        if _CALL_RE.search(_code_only(path)):
            hits.add(path.relative_to(_SRC).as_posix())
    return hits


@pytest.mark.timeout(40)
def test_every_one_shot_call_site_is_mapped():
    """Every file calling one_shot_completion must appear in the map — so a new
    model-dependent surface can't ship without declaring its degraded floor."""
    called = _files_calling_one_shot()
    mapped = set(_CALL_SITE_SURFACES)
    unmapped = {p for p in called if not any(p.endswith(m) for m in mapped)}
    assert not unmapped, (
        "New one_shot_completion call-site(s) with no degraded-contract mapping: "
        f"{sorted(unmapped)}. Add each file to _CALL_SITE_SURFACES in this test AND "
        "ensure it maps to a registered DegradedContract surface."
    )


def test_map_has_no_stale_entries():
    """A file in the map that no longer calls one_shot_completion is stale — remove it
    (keeps the ratchet honest in both directions)."""
    called = _files_calling_one_shot()
    stale = {m for m in _CALL_SITE_SURFACES if not any(p.endswith(m) for p in called)}
    assert (
        not stale
    ), f"Stale _CALL_SITE_SURFACES entries (file no longer calls one_shot): {sorted(stale)}"
