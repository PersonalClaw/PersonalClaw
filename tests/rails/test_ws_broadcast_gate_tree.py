"""The source-tree checks split from
``tests/test_ws_broadcast_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_every_note_type_in_the_tree_is_mapped():
    """The rail that makes an unmapped type a failing BUILD rather than a mystery frame.

    Scans `src/` for `"_type": "<value>"` literals and requires each to be translatable. Without
    it, the drop above is only discovered by someone noticing a missing toast in production.
    """
    import re

    from personalclaw.dashboard.ws_state import BROADCAST_NOTE_TYPES

    src = REPO / "src"
    found: dict[str, str] = {}
    for path in source_index.package_files():
        for m in re.finditer(r'"_type":\s*"([a-z_]+)"', source_index.text(path)):
            found.setdefault(m.group(1), str(path.relative_to(src)))
    assert found, "the scan found no `_type` literals at all — it would pass vacuously"
    unmapped = {k: v for k, v in found.items() if k not in BROADCAST_NOTE_TYPES}
    # `metadata` and `archive` are session-JSONL record types, not dashboard notes; they never
    # reach `_broadcast`. Named here so the exemption is a decision on the record.
    unmapped = {k: v for k, v in unmapped.items() if k not in ("metadata", "archive")}
    assert not unmapped, f"note types no producer can translate: {unmapped}"
