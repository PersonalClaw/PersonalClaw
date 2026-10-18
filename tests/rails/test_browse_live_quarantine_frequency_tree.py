"""The source-tree checks split from
``tests/test_browse_live_quarantine_frequency.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from rails_repo import REPO

_REPO = REPO


_WORKFLOW = _REPO / ".github" / "workflows" / "browse-live-quarantine-frequency.yml"


def test_workflow_states_and_invokes_the_three_of_five_main_threshold() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["CI"]' in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert 'BROWSE_LIVE_WINDOW: "5"' in text
    assert 'BROWSE_LIVE_THRESHOLD: "3"' in text
    assert "3 of the last 5 DISTINCT head SHAs targeting main" in text
    assert "python3 tools/browse_live_quarantine_frequency.py" in text
    assert "--base main" in text
