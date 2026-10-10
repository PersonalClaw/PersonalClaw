# @module-tag docs
#
# Architecture docs name Python modules the reader is expected to find in the tree. Two
# drifts of the same kind were fixed together: the widgets doc's file table named
# `personalclaw/dashboard/surface_layers.py`, which does not exist (the module is
# `src/personalclaw/surface_layers.py`), and the security doc said the built-in denylist
# held "112 shell patterns" while the packaged `baseline_denylist.json` held 116. This
# test compares those docs against the tree so neither drift can come back unnoticed.
#
# The ratchet: every backticked `personalclaw/…` path in `docs/architecture/*.md` must
# resolve against `src/`, and any "N patterns" count in the security doc must equal the
# packaged baseline's length. A drift either way fails loudly instead of waiting for a
# reader to compare the panel with the doc.

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "architecture"
SRC_DIR = REPO_ROOT / "src"
BASELINE = REPO_ROOT / "src" / "personalclaw" / "baseline_denylist.json"

# Backticked module paths, e.g. `personalclaw/surface_layers.py`. "around line 332" style
# references and prose mentions without backticks are intentionally not matched — the
# table rows and inline literals are the claims a reader follows.
PATH_RE = re.compile(r"`(personalclaw/[\w/]+\.py)`")
COUNT_RE = re.compile(r"(\d+)\s+shell patterns")


def _docs():
    return sorted(DOCS_DIR.glob("*.md"))


def test_every_backticked_module_path_in_architecture_docs_resolves():
    unresolved = []
    for doc in _docs():
        for match in PATH_RE.finditer(doc.read_text(encoding="utf-8")):
            rel = match.group(1)
            if not (SRC_DIR / rel).is_file():
                unresolved.append(f"{doc.name}: `{rel}`")
    assert not unresolved, "doc names a module the tree does not have:\n" + "\n".join(unresolved)


def test_security_doc_denylist_count_matches_the_packaged_baseline():
    security = (DOCS_DIR / "security.md").read_text(encoding="utf-8")
    counts = COUNT_RE.findall(security)
    assert (
        counts
    ), "security.md no longer states a denylist pattern count — drop or rework this check"
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = len(baseline["patterns"])
    for stated in counts:
        assert int(stated) == expected, (
            f"security.md says {stated} shell patterns; the packaged "
            f"baseline_denylist.json holds {expected}"
        )
