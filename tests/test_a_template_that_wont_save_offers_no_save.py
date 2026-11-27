"""A proposed template the installer would refuse offers no Save, and says why before it is pressed.

Measured before this was written: a proposed template whose step holds something shaped like a
credential offered Save template on its card and in its review, and pressing it was refused (the
installer's sentence, ``test_a_refused_learning_accept_says_why_in_plain_words.py``). The review
read the template's steps when it was drawn, and said nothing.

Now the review builds the template the Accept would save, through the same validator, and a
template it refuses is drawn with no Accept: its effect is ``none``, and its sentence says why,
never the value. A clean template still offers Save template, and saves.
"""

from __future__ import annotations

from test_a_refused_learning_accept_says_why_in_plain_words import (  # noqa: F401 (fixture)
    MARKER,
    _template_with_a_credential,
    home,
)

from personalclaw.learning import cards, proposals, review

WONT_SAVE = (
    "One of its steps holds something shaped like a credential, so it won't be saved. Reject it, "
    "or make the template yourself with the credential kept as a secret."
)


def _clean_template() -> proposals.Proposal:
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE.value,
        title="Template: morning-forecast",
        body="\n".join(
            f"- {step}"
            for step in (
                "Read today's forecast for Lisbon",
                "Note any rain after noon",
                "Post a one-line summary",
            )
        ),
        provenance="inferred",
        session_key="dashboard:c2",
        run_id="morning-forecast",
        tags=["ad_hoc_to_template", "auto_file", "name:morning-forecast"],
    )
    return prop


def test_its_review_offers_no_save_and_says_why(home) -> None:  # noqa: F811 - the imported fixture
    """🔴 Red before: it offered Save template."""
    said = review.review_for(_template_with_a_credential())["accept"]

    assert said == {"effect": "none", "verb": "", "sentence": WONT_SAVE, "confirm": False}
    for leak in (MARKER, "WF_INLINE_SECRET", "the card"):
        assert leak not in said["sentence"]


def test_its_card_draws_no_save_either(home) -> None:  # noqa: F811 - the imported fixture
    """🔴 Red before: the card drew Save template too."""
    card = cards.card_for(_template_with_a_credential())

    assert card["accept"]["effect"] == "none"
    assert card["accept"]["verb"] == ""


def test_a_clean_template_saves(home) -> None:  # noqa: F811 - the imported fixture
    prop = _clean_template()

    said = review.review_for(prop)["accept"]
    assert said["effect"] == "installs"
    assert said["verb"] == "Save template"

    proposals.accept(prop.id, actor="user")
    assert (home / "workflows" / "defs" / "morning-forecast" / "workflow.json").is_file()
