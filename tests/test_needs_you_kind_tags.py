"""Every kind of thing that can wait on you has its tag, the noun its card leads its tag row with.

The tags live in the fixture the server's and the web's card tests share
(``web/src/test/fixtures/needsYouKinds.json``). The web's registry reads the same words, and so do
Learning, Skills and the Proposals view. A new learning kind or card variant, a new skill
proposal kind, or a new kind on the waiting list with no tag would draw an empty tag row. Every
one is checked, the Inbox-only asks included.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw import waiting
from personalclaw.learning import cards
from personalclaw.learning.proposals import Kind
from personalclaw.skills import proposals as skill_proposals

FIXTURE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "test" / "fixtures" / "needsYouKinds.json"
)
TAGS = json.loads(FIXTURE.read_text(encoding="utf-8"))["tags"]


def test_every_card_variant_a_learning_proposal_can_take_has_a_tag():
    variants = set(cards._BUILDERS) | {"lesson", "retirement", "tier", "tier.wider_scope"}
    # A kind with no builder of its own takes its kind as its variant (`cards.variant_of`).
    variants |= {cards.variant_of(_bare(kind.value)) for kind in Kind}
    missing = sorted(v for v in variants if not TAGS["learning"].get(v))
    assert missing == [], missing


def test_both_skill_proposal_kinds_have_a_tag():
    assert set(skill_proposals.LABELS) <= set(TAGS["skills"])
    assert all(TAGS["skills"].values())


def test_every_kind_on_the_waiting_list_has_a_tag():
    kinds = {
        "approval",
        "question",
        "loop_question",
        "gate",
        "park",
        "browse_grant",
        "plan_review",
        "bridge_confirmation",
        *waiting.INBOX_KINDS,
    }
    missing = sorted(k for k in kinds if not TAGS["waiting"].get(k))
    assert missing == [], missing


def test_no_tag_is_a_kinds_long_label():
    for group in ("learning", "skills", "waiting"):
        for tag in TAGS[group].values():
            assert len(tag) <= 20 and "proposed" not in tag.lower(), tag


def _bare(kind: str):
    from personalclaw.learning.proposals import Proposal

    return Proposal(id=f"lp-{kind}", kind=kind, title="t", body="")
