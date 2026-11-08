"""How replies explain things: the owner's default, a chat's own choice, and what a turn carries.

The choices and the text each sends are one table, ``explanations.json``, read here at import and
by the web at build time. A turn resolves its choice on every turn, most specific first
(:data:`EXPLAIN_PRECEDENCE`): this chat's own (its session's ``explain``), then the owner's default
(``agent.explain``), read fresh from config so a change reaches the next turn. A value that names
no choice states nothing and the next scope decides. What a turn carries is its section
(:func:`section`), which ``reply_style.appended`` puts after the reply style's, with the platform's
safety rules once, last. Changes how things are explained, never a fact, a caveat or a refusal.

The first-run card once kept its answer as a pinned ``style`` preference holding the table's text;
:func:`adopt_card_answer` moves such an answer to ``agent.explain`` at gateway start.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

_TABLE = Path(__file__).with_name("explanations.json")


def _a_row(row: object) -> bool:
    return isinstance(row, dict) and all(
        isinstance(row.get(k), str) for k in ("id", "label", "hint", "sent")
    )


def _shipped(rows: object) -> tuple[dict[str, str], ...]:
    if (
        isinstance(rows, list)
        and rows
        and all(_a_row(r) for r in rows)
        and rows[0]["id"] == DEFAULT
        and not rows[0]["sent"]
    ):
        return tuple(rows)
    raise RuntimeError(
        f"{_TABLE} is not a table of explanations, 'default' first and sending nothing"
    )


#: The choice that sends nothing: the agent explains things its usual way.
DEFAULT = "default"

ROWS: tuple[dict[str, str], ...] = _shipped(json.loads(_TABLE.read_text(encoding="utf-8")))
EXPLAIN_IDS: tuple[str, ...] = tuple(r["id"] for r in ROWS)
_BY_ID = {r["id"]: r for r in ROWS}

#: Most specific first. No agent scope.
EXPLAIN_PRECEDENCE: tuple[str, ...] = ("conversation", "platform")

#: The section's fence, opening and closing.
OPEN = "[EXPLANATIONS: {label}]"
CLOSE = "[END EXPLANATIONS]"


class Resolved(NamedTuple):
    """A resolved choice and the scope (a member of :data:`EXPLAIN_PRECEDENCE`) that decided it."""

    explain: str
    source: str


def normalize_choice(value: object) -> str:
    """*value* as a choice's id, or ``""`` when it names none."""
    if not isinstance(value, str):
        return ""
    word = value.strip().lower()
    return word if word in _BY_ID else ""


def resolve(conversation: object = "", platform: object = "") -> Resolved:
    """One turn's choice, by walking :data:`EXPLAIN_PRECEDENCE`."""
    stated = {
        "conversation": normalize_choice(conversation),
        "platform": normalize_choice(platform),
    }
    for source in EXPLAIN_PRECEDENCE:
        if stated[source]:
            return Resolved(stated[source], source)
    return Resolved(DEFAULT, "platform")


def platform_default() -> str:
    """The owner's default (``agent.explain``), from a fresh config read; ``default`` on any read
    failure, never a guess."""
    try:
        from personalclaw.config.loader import AppConfig

        return normalize_choice(getattr(AppConfig.load().agent, "explain", "")) or DEFAULT
    except Exception:
        logger.debug("explanations config read failed", exc_info=True)
        return DEFAULT


def resolve_for(session: Any) -> Resolved:
    """The choice *session*'s next turn resolves to: its own, else the owner's default."""
    return resolve(getattr(session, "explain", ""), platform_default())


def label(explain: str) -> str:
    """The choice's label (``No preference`` for one that names none)."""
    return _BY_ID.get(normalize_choice(explain) or DEFAULT, _BY_ID[DEFAULT])["label"]


def text(explain: str) -> str:
    """What the choice sends, or ``""``."""
    return _BY_ID.get(normalize_choice(explain), _BY_ID[DEFAULT])["sent"]


def section(explain: str) -> str:
    """The choice's fenced section, or ``""`` for one that sends nothing."""
    sent = text(explain)
    if not sent:
        return ""
    return f"{OPEN.format(label=label(explain))}\n{sent}\n{CLOSE}"


def adopt_card_answer(store: Any) -> str:
    """Move the answer the old first-run card kept as a preference to the owner's default.

    The card wrote each answer as a pinned, explicit ``style`` facet holding the table's text, in
    *store* (the global memory's record store). Each such facet that is pinned and not forgotten is
    forgotten, so it is not sent beside the default, and the newest one's choice becomes
    ``agent.explain`` when the config states none (no value, ``default`` or one that names no
    choice); a choice the config already states is kept. Every other facet is left, a card answer
    the owner unpinned or forgot included. Keyed on the data, so a second run finds nothing and
    writes nothing. Returns the choice it set, or ``""``.
    """
    from personalclaw.config.transactions import mutate_config
    from personalclaw.preference_facets import Facet, facet_key, forget_facet

    held: list[tuple[str, str, str]] = []
    for row in ROWS:
        if not row["sent"]:
            continue
        key = facet_key("style", row["sent"])
        stored = store.get_semantic(key)
        if not stored:
            continue
        try:
            facet = Facet.from_payload(json.loads(stored["value_json"]))
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        if facet.pinned and not facet.forgotten:
            held.append((facet.updated_at, key, row["id"]))
    if not held:
        return ""
    choice = max(held)[2]

    def change(document: dict[str, Any]) -> str:
        agent = document.setdefault("agent", {})
        if not isinstance(agent, dict):
            return ""
        if normalize_choice(agent.get("explain")) not in ("", DEFAULT):
            return ""  # the config states a choice of its own, which is kept
        agent["explain"] = choice
        return choice

    adopted = mutate_config(change)
    for _at, key, _choice in held:
        forget_facet(store, key)
    if adopted:
        logger.info("Explanations: the card's answer %r is the default now", adopted)
    return adopted
