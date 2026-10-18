"""The source-tree checks split from
``tests/test_workflows_introspection.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import source_index


def test_GATE_REJECTED_is_still_emitted_NOWHERE():
    """The measurement this module is built around. If a future change starts emitting it,
    this test
    fails and the projection should be revisited to count it — a silent second source of truth for
    rejections would double-count them."""
    import re as _re

    # A WRITE, not a mention: `journal.py` declares the constant and `introspection.py`
    # documents why
    # it is unused, and matching either would make this test fail on its own explanation.
    # The pattern
    # is the shape a real emitter has — the constant passed to a journal write.
    emit = _re.compile(r"write\s*\(\s*[\w_.]*GATE_REJECTED")
    emitters = [
        str(f.relative_to(source_index.REPO))
        for f in source_index.package_files()
        if emit.search(source_index.text(f))
    ]
    assert emitters == [], f"GATE_REJECTED is now emitted by {emitters} — revisit gate_stats"
