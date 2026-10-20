"""The source-tree checks split from
``tests/test_chat_plan_mode.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


class TestManualOnlyActivation:
    """Clause 3: activation is manual-only — a quick task is untouched."""

    def test_nothing_but_the_activate_endpoint_calls_activate(self):
        """Structural half of "manual-only": no send/turn/heuristic path may reach
        ``activate``. A behavioural test can only show that TODAY's send path doesn't;
        this shows no caller exists at all outside the endpoint's own module."""
        callers = sorted(
            p.relative_to(_SRC).as_posix()
            for p in source_index.package_files()
            if "chat_plan.activate(" in source_index.text(p)
        )
        assert callers == [], callers
        own = source_index.text(_SRC / "dashboard" / "chat_plan.py")
        # Exactly one call site, inside the activate endpoint.
        assert own.count("= activate(chat, running=") == 1
