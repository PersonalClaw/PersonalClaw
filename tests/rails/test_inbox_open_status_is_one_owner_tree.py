"""The source-tree checks split from
``tests/test_inbox_open_status_is_one_owner.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from inbox_open_status_is_one_owner_shared import REPO

#: A two-element collection literal naming exactly PENDING and SEEN, in either order, as strings or
#: as enum members. Deliberately anchored on the CLOSING bracket so a longer tuple that merely
#: starts with the pair — `skills/proposals.py`'s `("pending", "seen", "dismissed", "handled")`, a
#: different fact ("answered or not") — is not swept up in this.
_MEMBER = r"""(?:ItemStatus\.)?["']?%s["']?(?:\.value)?"""


_PAIR = re.compile(
    r"[\{\(\[]\s*(?:%s\s*,\s*%s|%s\s*,\s*%s)\s*,?\s*[\}\)\]]"
    % (
        _MEMBER % "(?:pending|PENDING)",
        _MEMBER % "(?:seen|SEEN)",
        _MEMBER % "(?:seen|SEEN)",
        _MEMBER % "(?:pending|PENDING)",
    )
)


#: The frontend equivalent: an array/tuple literal of the two status strings. The one legitimate
#: frontend definition is the exhaustive `STATUS_OPEN` record in `lib/attentionLanes.ts`, whose
#: `pending: true` / `seen: true` entries are on separate lines and are not this shape.
_Q = r"""['"]%s['"]"""


_PAIR_TS = re.compile(
    r"[\[\(]\s*(?:%s\s*,\s*%s|%s\s*,\s*%s)\s*,?\s*[\]\)]"
    % (_Q % "pending", _Q % "seen", _Q % "seen", _Q % "pending")
)


def _code_lines(path: Path):
    """(lineno, text) for lines that are not wholly a comment.

    Comment-only lines are skipped because the fix's own prose names the statuses it replaced, and a
    census that counted explanations would make documenting the defect impossible.
    """
    for n, line in enumerate(source_index.text(path).splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*") or stripped.startswith("//"):
            continue
        yield n, line


def test_only_one_module_spells_the_open_status_pair():
    """The census. A second spelling anywhere under `src/personalclaw/` is a third definition.

    This is the rail, not the fix: the fix consumed the owner at six call sites, and nothing stops
    a seventh from writing `{"pending", "seen"}` inline next month. Two definitions of open is how
    33 and 37 ended up on the same screen.
    """
    found: list[str] = []
    for path in source_index.package_files():
        for lineno, line in _code_lines(path):
            if _PAIR.search(line):
                found.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert len(found) == 1, (
        "exactly one module may spell the open-status pair — everything else imports "
        f"inbox.OPEN_STATUSES. Found {len(found)}:\n" + "\n".join(found)
    )
    assert found[0].startswith("src/personalclaw/inbox.py:")
    assert "OPEN_STATUSES" in found[0], (
        "the one spelling must be the OPEN_STATUSES definition itself, not some other line in "
        f"inbox.py: {found[0]}"
    )


def test_the_frontend_does_not_respell_the_open_pair():
    """The frontend half of the census: `OPEN_STATUSES`/`isOpen` are derived, never re-listed.

    `pages/inbox/inboxMeta` re-exports them off `lib/attentionLanes`. A page that writes
    `['pending', 'seen']` again is a second frontend definition, which is how the inbox's chips and
    Mission Control's lanes could disagree.
    """
    found: list[str] = []
    for path in [p for p in source_index.web_files() if p.suffix in {".ts", ".tsx"}]:
        if ".test." in path.name:
            continue
        for lineno, line in _code_lines(path):
            if _PAIR_TS.search(line):
                found.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not found, "the frontend must derive the open set from STATUS_OPEN:\n" + "\n".join(found)
