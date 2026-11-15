"""A proposal's review masks what it serves, for every kind it draws.

``learning.review`` builds each kind's content with its own builder (``review._CONTENT``), and a
kind with none falls back to the body as markdown. Each builder reads its own fields: a template's
steps, a pasted card's prompt, a failed run's error, a lesson conflict's rules, a change's ops, the
captures. So each is handed a credential in every field it reads, and only the mask comes back.
Planting an unmasked read in any one builder reds its own row.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.learning import review

pytestmark = pytest.mark.security

SECRET = "ghp_" + "A1b2C3d4E5" * 4
MASK = "[REDACTED: credential]"
CARD = (
    "A card you pasted:\n\n```json\n"
    + json.dumps(
        {
            "name": "deploy",
            "description": f"Deploy with {SECRET}",
            "steps": [{"id": "push", "prompt": f"Push with {SECRET}"}],
            "content": f"Use {SECRET} to sign in",
            "variables": [{"name": "repo"}],
            "system_prompt": f"You hold {SECRET}",
            "voice": f"Terse, signs with {SECRET}",
        }
    )
    + "\n```"
)


def _prop(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "lp-1",
        "title": f"Title with {SECRET}",
        "body": f"Body with {SECRET}",
        "source_excerpt": f"you typed {SECRET}",
        "change_manifest": {},
        "source_cadence": "",
        "staging_refs": [],
        "run_id": "run-1",
        "evidence_refs": [],
        "session_key": "dashboard:c1",
    }
    base.update(over)
    prop = SimpleNamespace(**base)
    prop.to_dict = lambda: dict(base)
    return prop


#: Each variant, with a credential in every field its builder reads.
CASES: dict[str, SimpleNamespace] = {
    "template.named": _prop(
        body=f"- Fetch with {SECRET} for {{{{week}}}}\n- Group it\n\nWhy: {SECRET}"
    ),
    "template.recurring_path": _prop(body=f"- Fetch with {SECRET}\n- Send it"),
    "template.plan_shape": _prop(body=f"A plan built with {SECRET}\n\nThe rest"),
    "template.card": _prop(body=CARD),
    "prompt.card": _prop(body=CARD),
    "agent.card": _prop(body=CARD),
    "lesson.failed_run": _prop(
        body=f"Stop on {SECRET}\n\nThe capsule with {SECRET}", source_excerpt=f"Error: {SECRET}"
    ),
    "lesson.conflict": _prop(
        change_manifest={
            "targeted_fix": [
                {
                    "yours": [f"Mine: {SECRET}"],
                    "rule": f"Learned: {SECRET}",
                    "negative": f"Not {SECRET}",
                    "memory": "",
                }
            ]
        }
    ),
    "lesson.captures": _prop(body=f"- A capture with {SECRET}", staging_refs=["c-1"]),
    "skill.promoted": _prop(body=f"1. Rebuild with {SECRET}\n2. Restart"),
    "template_diff": _prop(
        change_manifest={
            "targeted_fix": [
                {"op": "update_node", "node_id": "build", "fields": {"prompt": f"Use {SECRET}"}}
            ],
            "predicted_fixes": [f"Fixes {SECRET}"],
        }
    ),
    "retirement.text_not_typed": _prop(
        body=f"- Preference: always use {SECRET} (the same as in a page with {SECRET})"
    ),
    "retirement": _prop(),  # no builder of its own: the body as markdown
}


def test_every_builder_has_a_row():
    """A builder added to the review without a row here would not be pinned."""
    assert set(review._CONTENT) <= set(CASES), sorted(set(review._CONTENT) - set(CASES))
    assert any(v not in review._CONTENT for v in CASES), "the markdown default has its row"


@pytest.mark.parametrize("variant", sorted(CASES))
def test_a_kinds_content_comes_back_masked(variant, monkeypatch):
    from personalclaw.learning import staging

    # The captures' own store holds the same words; it has nothing else to read here.
    class Store:
        def sources_for(self, uids):
            return [
                {"uid": u, "day": "2026-11-15", "cadence": "nightly", "kind": "preference"}
                for u in uids
            ]

        def content_for(self, uid):
            return f"The whole capture, with {SECRET}"

    monkeypatch.setattr(staging, "get_store", lambda: Store())
    out = json.dumps(review._content(CASES[variant], variant, capture="c-1"))
    assert SECRET not in out, out
    assert MASK in out, out
