"""The empty-state suggestion list has to work in the empty state.

`_FALLBACK_SUGGESTIONS` is shown in exactly one situation: `generate_suggestions` found no
substantive context — no memory, no sessions, no automations — or the LLM was unavailable. It is
also what `SuggestionsCache` seeds from before the first generation. So it is the brand-new-install
list, on `#/chat`, which is the most-visited surface in the product.

Two of its six entries asked about state that state does not have:

    "Summarize my recent conversations"   there are none; that is WHY the fallback fired
    "Review my latest PR"                 assumes a repository context nobody has configured

A suggestion chip whose best possible answer is "you don't have any" spends a first impression to
say nothing.

🪤 THIS RAIL PINS THE CRITERION, NOT THE STRINGS. An exact-list assertion would red on every
re-wording and teach the next person to update the expected list without thinking — the same
failure mode that let two stale hint counts survive in `web/src/ui/forms.tsx`. What is asserted
here is the property that made the old entries wrong: **no fallback suggestion may refer to prior
user state**. Copy can change freely; only a dead end coming back reds the gate.
"""

import re

from personalclaw import suggestions

#: Words that make a suggestion depend on history the fallback state does not have. Each one is
#: taken from an entry that actually shipped, so this is a measured list rather than a guessed one.
_BACKREFERENCE = re.compile(
    r"\b(?:my\s+recent|recent|latest|my\s+last|last\s+week|earlier|previous|my\s+conversations)\b",
    re.IGNORECASE,
)

#: The two strings this rail exists to keep out, kept verbatim so the mutation is obvious.
_THE_DEAD_ENDS = ("Summarize my recent conversations", "Review my latest PR")


def test_the_regex_actually_matches_the_entries_it_was_written_for():
    """Vacuity floor. A back-reference detector that matches nothing would let the whole list
    through while looking like a passing gate — and that is the most common way a rail in this
    repo dies."""
    for dead in _THE_DEAD_ENDS:
        assert _BACKREFERENCE.search(dead), (
            f"the detector no longer matches {dead!r}, which is one of the two entries it was "
            f"written from. Every assertion below is now vacuous."
        )


def test_no_fallback_suggestion_refers_to_state_the_empty_state_lacks():
    offenders = [s for s in suggestions._FALLBACK_SUGGESTIONS if _BACKREFERENCE.search(s)]
    assert not offenders, (
        "these fallback suggestions ask about prior user state, and the fallback list is shown "
        'ONLY when there is none — so their best possible answer is "you don\'t have any":\n  '
        + "\n  ".join(offenders)
        + "\n\nRe-word them to something a brand-new instance can actually do."
    )


def test_the_list_is_a_real_population():
    """A second vacuity floor, on the other side: an empty list would pass the check above."""
    assert len(suggestions._FALLBACK_SUGGESTIONS) >= 4, (
        f"only {len(suggestions._FALLBACK_SUGGESTIONS)} fallback suggestions — the check above "
        f"passes trivially on a short list, and the chip row looks broken."
    )


def test_every_suggestion_survives_the_parser_that_would_drop_it():
    """`_parse_suggestions` discards anything over 80 characters and takes only the first six.
    A fallback entry that the generated path would reject is a fallback the two code paths
    disagree about."""
    for s in suggestions._FALLBACK_SUGGESTIONS:
        assert s.strip(), "an empty suggestion would render as a blank chip"
        assert len(s) <= 80, f"{s!r} is {len(s)} chars; the parser drops anything over 80"
    assert len(suggestions._FALLBACK_SUGGESTIONS) <= 6, (
        "more than six fallback suggestions — `_parse_suggestions` caps the generated list at "
        "six, so the fallback would show a longer row than the real thing ever does."
    )
