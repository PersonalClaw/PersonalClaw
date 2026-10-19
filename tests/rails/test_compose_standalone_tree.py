"""The platforms guide states a missing compose ``env_file``'s real failure, not a silent one.

The tree half of ``tests/test_compose_standalone.py``, which says what the defect was and tests
the shipped compose files. ``docs/guides/platforms.md`` had the direction backwards: it told a
reader the keys "silently do not load", when a missing required ``env_file`` is a hard Compose
failure. It reads the guide, so it is a rail: it runs once per CI run, in the rails job.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

_GUIDE = REPO / "docs" / "guides" / "platforms.md"


def test_the_guide_states_the_real_failure_not_a_silent_one():
    """`docs/guides/platforms.md` had the direction backwards; it must not regress."""
    raw = source_index.text(_GUIDE)
    # Strip the blockquote markers and collapse the wrapping, so the assertion pins the
    # SENTENCE rather than the column at which a paragraph happened to be re-flowed.
    guide = " ".join(" ".join(line.lstrip().removeprefix(">").split()) for line in raw.splitlines())
    assert (
        "keys silently do not load" not in guide
    ), "a missing required env_file is a HARD Compose failure, not a silent skip"
    assert "`required` defaults to **true**" in guide
    assert "failed to start at all" in guide
