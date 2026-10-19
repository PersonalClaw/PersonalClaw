"""The chat-surface guide documents every mechanic and everything it records, as the UI names it.

The tree half of ``tests/test_chat_craft_sel_audit.py``, which drives each mechanic's real handler
and counts what lands in the Security Event Log. These read ``docs/guides/chat-surface.md`` and
the frontend that renders the controls it names, so they are a rail: they run once per CI run, in
the rails job.
"""

from __future__ import annotations

import re

import source_index
from chat_craft_sel_shared import EMITTERS
from rails_repo import REPO

_GUIDE = REPO / "docs" / "guides" / "chat-surface.md"
_WEB = REPO / "web" / "src"


def test_the_guide_documents_all_nine_mechanics_and_everything_they_record():
    """The guide's coverage as a rail rather than a promise.

    The guide is the only place a user learns what the chat surface can do and what it records.
    Two mechanics shipped *after* it was written (Branch, plan mode) and it went on saying
    "seven" with three operations missing from its table — a wrong count in the one document
    whose job is the count. So the count, the section numbering and every recorded operation are
    asserted here, beside the audit they describe.
    """
    guide = source_index.text(_GUIDE)
    numbered = re.findall(r"(?m)^## (\d+)\. ", guide)
    assert numbered == [str(i) for i in range(1, 10)], numbered
    assert "nine things the chat surface can do" in guide
    assert "of these nine change something a security log" in guide
    for op in EMITTERS:
        assert f"`{op}`" in guide, f"the guide's recording table never mentions {op}"


def test_the_two_newest_mechanics_are_named_in_the_guide_as_the_ui_names_them():
    """The guide's "Where:" line has to match the control a user is hunting for. Both new
    mechanics are reached by a label, so the label is checked against the frontend that renders
    it — a renamed affordance leaves the guide pointing at nothing."""
    guide = source_index.text(_GUIDE)
    actions = source_index.text(_WEB / "pages" / "chat" / "MessageActions.tsx")
    page = source_index.text(_WEB / "pages" / "chat" / "composer" / "ChatComposer.tsx")
    assert 'label="Branch from here"' in actions and "**Branch from here**" in guide
    assert 'label="Plan this first"' in page and "**Plan this first**" in guide
