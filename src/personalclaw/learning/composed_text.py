"""What learning took from text nobody typed, and how it is taken back.

Until learning read only a person's own words (:mod:`personalclaw.own_words`), every per-turn
capture read the message the model was sent. That message holds a saved prompt's text in place of
its ``@name``, an attached file's text, a referenced note, an automation's or a subagent's message,
a theme's persona and natural voice's instructions. So a prompt that says "List what I said I would
do" became the lesson "User correction to honor: Execute the following instructions: …", and a
prompt that defines "Yesterday" put that definition in the glossary.

Two kinds of what was learned that way are told apart, and handled differently:

* **A correction lesson whose quoted words are not hers** is recognized by its own text. The
  after-turn review quoted the whole message, so a lesson it learned from composed text opens with
  what the platform put ahead of her words (:data:`COMPOSED_OPENINGS`), or carries a block it
  added after them (:data:`APPENDED_BLOCKS`). Such a lesson is retracted
  (:func:`retract_composed_corrections`): tombstoned through the memory log, so Memory → History
  can undo it; its evidence voided, so it cannot come back at its old strength; and a lesson it
  had displaced restored.
* **A learned veto, preference or glossary line** was read out of the middle of the message, so
  nothing in it says where it came from. One that matches the text of a saved prompt, or of a
  theme's or natural voice's instructions, may have come from there, or she may have said the same
  thing herself; what is stored cannot tell which. So it is not removed: it is offered for review
  (:func:`offer_review`) as one proposal that lists each item and what it matches, whose Accept
  removes them (:func:`install_accepted_review`) and whose Reject keeps them for good.

:func:`settle` does both over every memory store in the home. It runs at each gateway start and
changes nothing once there is nothing left to take back.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from personalclaw.after_turn_review import CORRECTION_PREFIX

logger = logging.getLogger(__name__)

#: The openings the platform put ahead of a person's words in the message the after-turn review
#: quoted: a saved prompt run in place of its ``@name`` (the bundled wrapper; :func:`openings` adds
#: the one in use when it was edited), an attached file's text, a referenced knowledge item or
#: artifact, the entity a chat was opened to investigate, an app's background context, an
#: automation's notification, a subagent's report, and plan mode's revision and resume prompts.
#: Each is a fixed string of the platform's that a message a person types does not open with.
COMPOSED_OPENINGS: tuple[str, ...] = (
    "Execute the following instructions:",
    "The user attached the following file(s).",
    "The user referenced the following item(s) from their knowledge library.",
    "The user referenced the following artifact(s).",
    "The user opened this chat to investigate the following entity",
    '[Background context from "',
    "[Cron notification from ",
    "[Subagent completion event]",
    "[Subagent completion batch",
    "Revise the plan with this feedback:",
    "The plan below was reviewed and approved.",
)

#: The blocks the platform added after a person's words: a theme's persona on a new session's
#: first turn, and natural voice's instructions on every turn it is on.
APPENDED_BLOCKS: tuple[str, ...] = (
    "[LUMON PERSONA]",
    "[CLAW ARCADE PERSONA]",
    "[RETRO TERMINAL PERSONA]",
    "[NATURAL VOICE]",
)

#: The ``source`` the memory log records for what this module removes or restores.
SOURCE = "learning_cleanup"

#: The tag of the review proposal, which is how its installer finds it (:func:`is_review_proposal`).
REVIEW_TAG = "learned_from_text_not_typed"
_REVIEW_TARGET = "learning.text_not_typed"

_REVIEW_TITLE = "Check what may have been learned from text you didn't type"
_REVIEW_INTRO = (
    "Learning used to read the whole message the agent was sent, so the text of a saved prompt "
    "you ran, or of a theme's or natural voice's instructions, could be learned as if you had "
    "said it. Each of these matches such a text, so it may have come from there, or you may have "
    "said the same thing yourself:"
)
_REVIEW_OUTRO = "Accept removes them. Reject keeps them, and they are not offered again."

_SPACE_RE = re.compile(r"\s+")
_GLOSSARY = "glossary"
_GLOSSARY_SEP = " — "


@dataclass(frozen=True)
class ReviewItem:
    """One learned item a composed text may account for.

    ``kind`` is ``lesson`` (a veto), ``preference`` or ``glossary``; ``ref`` is what removing it
    needs (the lesson's or preference's key, the glossary line itself); ``source`` names the text
    it matches, in the words the proposal uses.
    """

    kind: str
    ref: str
    text: str
    source: str

    @property
    def evidence_ref(self) -> str:
        return f"{self.kind}:{self.ref}"


def openings() -> tuple[str, ...]:
    """:data:`COMPOSED_OPENINGS`, with the opening of the saved-prompt wrapper in use when it was
    edited (Settings → Prompts): a lesson quoted from a run under the edited wrapper opens with it.
    """
    try:
        from personalclaw.prompt_providers.runtime import render_snippet_block

        mark = "\x00content\x00"
        lead = render_snippet_block("prompt-expansion", {"content": mark, "user_text": ""})
        lead = lead.split(mark, 1)[0].strip() if mark in lead else ""
    except Exception:  # noqa: BLE001 - the bundled openings still apply
        logger.debug("prompt wrapper opening unavailable", exc_info=True)
        lead = ""
    if lead and lead not in COMPOSED_OPENINGS:
        return (*COMPOSED_OPENINGS, lead)
    return COMPOSED_OPENINGS


def is_composed_correction(rule: str, leads: tuple[str, ...]) -> bool:
    """Whether *rule* is a correction lesson that quoted text the platform composed: its quoted
    words open with one of *leads*, or carry one of :data:`APPENDED_BLOCKS`."""
    if not rule.startswith(CORRECTION_PREFIX):
        return False
    quoted = rule[len(CORRECTION_PREFIX) :]
    return quoted.startswith(leads) or any(block in quoted for block in APPENDED_BLOCKS)


def _rule_of(row: dict) -> str:
    try:
        return str(json.loads(row.get("value_json") or '""'))
    except (TypeError, ValueError):
        return ""


def retract_composed_corrections(vs: Any, leads: tuple[str, ...]) -> list[str]:
    """Retract every correction lesson in the store *vs* that quoted composed text, after giving
    back what each had displaced. One whose retraction was undone stays: she took it back. Returns
    the retracted keys."""

    def composed(text: str) -> bool:
        return is_composed_correction(text, leads)

    keys = [
        str(row["key"])
        for row in vs.get_lessons()
        if row.get("source") == "after_turn_review"
        and composed(_rule_of(row))
        and not vs.deletion_undone(str(row["key"]), SOURCE)
    ]
    for key in keys:
        vs.restore_displaced_by(key, keep=lambda text: not composed(text))
    return [key for key in keys if vs.retract_lesson(key, SOURCE)]


def _normal(text: str) -> str:
    return _SPACE_RE.sub(" ", text or "").strip().casefold()


def _shipped_prompt_names() -> set[str]:
    """The prompts the platform ships for its own work (each bound to a use-case): the core
    catalog's and the ones native apps own. A chat does not run those as a saved prompt."""
    from personalclaw.apps import prompt_registry
    from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS

    return {p.name for p in BUNDLED_PROMPTS} | set(prompt_registry.default_prompt_names().values())


def composed_sources() -> list[tuple[str, str]]:
    """``(what it is, its text)`` for each text the turn builder put into a turn that a learned
    item could have been read out of: each saved prompt of hers, each theme's persona and natural
    voice's instructions, as they read now."""
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_default_provider,
    )
    from personalclaw.prompt_providers.runtime import render_snippet_block

    _ensure_default_providers_registered()
    provider = get_default_provider()
    if provider is None:
        return []
    shipped = _shipped_prompt_names()
    out: list[tuple[str, str]] = [
        (f"the saved prompt @{tpl.name}", tpl.content)
        for tpl in provider.list_prompts()
        if tpl.kind == "user" and tpl.name not in shipped and (tpl.content or "").strip()
    ]
    for snippet in provider.list_snippets():
        name = snippet.name
        if name.startswith("persona-"):
            label = f"the {name.removeprefix('persona-').replace('-', ' ').title()} theme's persona"
        elif name == "natural-voice":
            label = "natural voice's instructions"
        else:
            continue
        text = render_snippet_block(name)
        if text.strip():
            out.append((label, text))
    return out


def review_items(vs: Any, sources: list[tuple[str, str]]) -> list[ReviewItem]:
    """The vetoes, preferences and glossary lines in the store *vs* whose text one of *sources*
    holds, each with the first source that does."""
    from personalclaw import memory_slots
    from personalclaw.preference_facets import load_facets

    texts = [(label, _normal(text)) for label, text in sources]

    def found_in(*parts: str) -> str:
        """The first source holding every one of *parts* as whole words, or ``""``."""
        wanted = [re.compile(rf"(?<!\w){re.escape(_normal(p))}(?!\w)") for p in parts if p.strip()]
        if not wanted:
            return ""
        return next((label for label, text in texts if all(w.search(text) for w in wanted)), "")

    items: list[ReviewItem] = []
    for row in vs.get_lessons():
        if row.get("source") != "facet_veto":
            continue
        rule = _rule_of(row)
        if label := found_in(rule):
            items.append(ReviewItem("lesson", str(row["key"]), rule, label))
    for key, facet in load_facets(vs):
        if label := found_in(facet.text):
            items.append(ReviewItem("preference", key, facet.text, label))
    for line in memory_slots.live_lines(memory_slots.load(vs, _GLOSSARY)):
        term, _, definition = line.text.partition(_GLOSSARY_SEP)
        if definition and (label := found_in(term, definition)):
            items.append(ReviewItem("glossary", line.text, line.text, label))
    return items


_KIND_WORDS = {"lesson": "Lesson", "preference": "Preference", "glossary": "Glossary"}


def offer_review(items: list[ReviewItem]) -> Any:
    """File the one review proposal for *items*, or ``None`` when there is nothing to review or one
    is already waiting: a pending review is still the place to decide, and a second would ask the
    same question twice."""
    from personalclaw.learning import proposals

    if not items:
        return None
    if any(
        REVIEW_TAG in (p.tags or [])
        for p in proposals.list_pending(proposals.Kind.RETIREMENT.value)
    ):
        return None
    lines = [
        f"- {_KIND_WORDS[item.kind]}: {item.text} (the same as in {item.source})" for item in items
    ]
    body = "\n".join([_REVIEW_INTRO, "", *lines, "", _REVIEW_OUTRO])
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.RETIREMENT.value,
        title=_REVIEW_TITLE,
        body=body,
        target=_REVIEW_TARGET,
        provenance="inferred",
        source_cadence="cleanup",
        # What the Learning page shows on the row she decides: each item, and the text it matches.
        source_excerpt="\n".join([*lines, _REVIEW_OUTRO]),
        evidence_refs=[item.evidence_ref for item in items],
        evidence_strength="correlated",
        tags=[REVIEW_TAG],
        occurrences=len(items),
        min_evidence=1,
    )
    return prop


def settle(main: Any) -> dict[str, int]:
    """Take back, across every memory store in the home, what learning read out of text nobody
    typed: retract the correction lessons recognized as quoted from it, and offer the rest that
    may have come from it for review. *main* is the gateway's own store (``None`` when it has
    none). Returns how many were retracted and how many were offered."""
    from personalclaw.context import every_memory_vector_store

    leads = openings()
    sources = composed_sources()
    retracted = 0
    items: list[ReviewItem] = []
    with every_memory_vector_store(main) as stores:
        for vs in stores:
            retracted += len(retract_composed_corrections(vs, leads))
            items.extend(review_items(vs, sources))
    offered = offer_review(items)
    return {"retracted": retracted, "offered": len(items) if offered is not None else 0}


# ── Accepting the review ──────────────────────────────────────────────────────


def is_review_proposal(data: dict) -> bool:
    """Whether the proposal record *data* is the review :func:`offer_review` files."""
    return str(data.get("kind") or "") == "retirement" and REVIEW_TAG in (data.get("tags") or [])


def install_accepted_review(data: dict, main: Any) -> None:
    """Remove, from every memory store in the home, each item the accepted review lists. An item
    already gone is skipped: the review asked about it, and it is not there to keep."""
    from personalclaw import memory_slots
    from personalclaw.context import every_memory_vector_store

    refs = [str(ref).partition(":") for ref in data.get("evidence_refs") or []]
    with every_memory_vector_store(main) as stores:
        for vs in stores:
            for kind, _, ref in refs:
                if kind == "lesson":
                    vs.retract_lesson(ref, SOURCE)
                elif kind == "preference":
                    vs.delete_semantic(ref, SOURCE)
                elif kind == "glossary":
                    memory_slots.tombstone(vs, _GLOSSARY, ref, actor="human", source=SOURCE)
