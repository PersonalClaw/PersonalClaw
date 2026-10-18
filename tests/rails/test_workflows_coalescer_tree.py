"""The source-tree checks split from
``tests/test_workflows_coalescer.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
from rails_repo import REPO

from personalclaw.workflows.coalescer import COALESCING_EVENTS

pytestmark = pytest.mark.anyio


def test_the_allowlist_is_a_subset_of_the_published_events() -> None:
    """A coalescing entry for an event nothing publishes is dead configuration that reads as
    a live guarantee — and the reverse (a typo'd name) would silently never batch."""
    import re

    from tests.controller_sources import controller_modules

    root = REPO
    published: set[str] = set()
    for path in (*controller_modules(), root / "src/personalclaw/workflows/service.py"):
        text = path.read_text(encoding="utf-8")
        published |= set(re.findall(r'_publish\(\s*"(workflow_[a-z_]+)"', text))
    assert COALESCING_EVENTS <= published, sorted(COALESCING_EVENTS - published)
