"""The grounding preamble (UP-R14) — resolve the entities a goal names, before anything runs.

Two failures this prevents, both measured in entity-heavy planning:

**A goal that names a person, company, place or product should resolve that identity ONCE, up
front, and carry it into every stage.** Left unresolved, each stage re-guesses who "Ana" is, and a
research or booking run pattern-matches the narrative to whatever the model half-remembers — a
confident wrong identity is worse than a missing one. So the planner emits a deterministic
identity-resolution node as the FIRST node: a zero-token lookup against the memory entity graph,
its result injected into run state with a guard ("use exactly this resolved identity; do not
substitute unless a tool result explicitly disproves it"). Lookup failure degrades to
entity-name-only context with a `degraded` flag — never a mid-graph network call.

**Retrieval should be formed from a fresh reading of the goal, not from raw intent text.** So the
preamble also extracts the goal's topics — the nouns worth looking up — and hands them to the
grill's facts-vs-decisions lookup so "check what I already know" queries something the planner
understood rather than the user's literal phrasing.

Pure by construction: entity resolution is INJECTED (`resolver(text) -> list[dict]`), topic
extraction is a deterministic keyword pass. No I/O, no model, no clock here — the caller owns the
resolver and the grill dispatch, which is what makes the node shape and the topic split testable at
all.
"""

from __future__ import annotations

import re
from typing import Any, Callable

#: A resolver takes the goal text and returns resolved entities as
#: `[{id, name, entity_type, aliases}]` — the shape `MemoryService.resolve_entities` yields.
#: Injected so the preamble stays pure and offline-safe; a None resolver means "no graph wired".
EntityResolver = Callable[[str], list[dict]]

#: The guard the resolved-identity node carries into the run. Frozen wording, because a guard a
#: later stage can re-interpret is not a guard: the whole point is that the resolved identity is not
#: re-litigated by a model that half-remembers a different one.
IDENTITY_GUARD = (
    "Use exactly this resolved identity. Do not substitute a different entity unless a tool result "
    "explicitly disproves it."
)

#: The prohibition added for entity-heavy domains — a stated UP-R14 requirement. A research or
#: financial run must not fit an unresolved name to a plausible narrative.
NO_PATTERN_MATCH_PROHIBITION = (
    "Do not pattern-match narrative to an unresolved name — if an entity was not resolved, treat "
    "it as unknown rather than assuming who or what it is."
)

#: Stopwords for topic extraction — small on purpose, mirroring the matcher's stoplist. An
#: aggressive list drops the one noun that made a topic worth looking up.
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "my",
        "me",
        "i",
        "it",
        "this",
        "that",
        "is",
        "are",
        "be",
        "do",
        "does",
        "from",
        "by",
        "at",
        "as",
        "about",
        "into",
        "over",
        "then",
        "so",
        "we",
        "our",
        "us",
        "you",
        "your",
        "up",
        "out",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "please",
        "want",
        "need",
        "make",
        "set",
        "get",
        "run",
    }
)


def extract_topics(goal: str, *, limit: int = 6) -> list[str]:
    """The goal's topic nouns — the retrieval queries feeding the grill's lookup.

    Deterministic: distinct content tokens in first-seen order, capped. A model-extracted topic list
    reads better and cannot be tested, and the value here is a stable set of lookup terms — the same
    goal must query the same things next week, which a sampled model cannot promise.
    """
    seen: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", (goal or "").lower()):
        if token in _STOP or token in seen:
            continue
        seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def resolve_entities(goal: str, resolver: EntityResolver | None) -> tuple[list[dict], bool]:
    """Resolve the entities a goal names. Returns `(entities, degraded)`.

    `degraded` is True when a resolver was supposed to run and could not produce a resolved identity
    — no graph wired, or the lookup found nothing for a goal that clearly names something. It is the
    flag the emitted node carries so a downstream stage knows it is working from a name, not a
    resolved identity, and a reviewer can see WHY the preamble is thin.
    """
    if resolver is None:
        return [], True
    try:
        entities = resolver(goal) or []
    except Exception:
        return [], True
    return entities, not entities


def build_preamble_node(goal: str, resolver: EntityResolver | None) -> dict[str, Any] | None:
    """The deterministic entity-resolution FIRST node, or None when the goal grounds nothing.

    A `transform` node, not an action: resolution already happened here (zero-token, against the
    graph), and the node's job is to INJECT that resolved identity into run state under a stable id
    every stage can bind to. Emitting an `action` that re-runs a lookup at run time would be the
    mid-graph network call the plan forbids. When nothing resolved, the node still emits with a
    `degraded: true` payload and the name-only context, so the guard and the prohibition still reach
    the stages — a preamble that vanishes on a miss teaches the stages nothing about the gap.

    Returns None only when the goal has no extractable topic AND no resolver ran — there is nothing
    to ground, and an empty preamble node would be scaffolding for its own sake.
    """
    entities, degraded = resolve_entities(goal, resolver)
    topics = extract_topics(goal)
    if not entities and not topics:
        return None

    payload: dict[str, Any] = {
        "resolved_entities": entities,
        "topics": topics,
        "degraded": degraded,
        "guard": IDENTITY_GUARD,
    }
    if degraded or _entity_heavy(entities):
        # Entity-heavy domains (research/financial) and every degraded resolution both carry the
        # do-not-pattern-match prohibition — the first because the domain invites it, the second
        # because a name working without a resolved identity is exactly when it happens.
        payload["prohibition"] = NO_PATTERN_MATCH_PROHIBITION

    return {
        "kind": "transform",
        "id": "ground",
        # A whole-value literal binding: `resolve()` passes a non-string through untouched, so the
        # payload lands in run state verbatim for downstream stages to bind to as
        # `{{nodes.ground.output.resolved_entities}}`.
        "config": {"expr": payload},
    }


def _entity_heavy(entities: list[dict]) -> bool:
    """Whether the resolved set is a domain the prohibition targets (people/orgs/financial)."""
    heavy = {"person", "organization", "org", "company", "product", "place", "asset", "ticker"}
    return any(str(e.get("entity_type", "")).lower() in heavy for e in entities)


def prepend_preamble(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Put the preamble node FIRST in the proposed tree.

    A sequence root gains it at index 0; any other root kind is wrapped in a sequence so resolution
    genuinely runs before the work. Returns a new dict — the caller's tree is not mutated, so a
    failure to prepend cannot corrupt the plan it was enhancing.
    """
    if not isinstance(root, dict) or not node:
        return root
    if root.get("kind") == "sequence" and isinstance(root.get("children"), list):
        out = dict(root)
        out["children"] = [node, *root["children"]]
        return out
    return {"kind": "sequence", "id": "root", "children": [node, root]}
