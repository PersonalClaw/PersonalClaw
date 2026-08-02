"""The per-store `schema.md` conventions contract (KNOW-R16).

A knowledge base accumulates conventions whether or not anyone writes them down: what
counts as a `fact` versus an `insight`, how titles are phrased, when two articles should
be one. Left implicit, every writer invents their own — and a store written by a dozen
synthesis runs across six months reads like a dozen different stores.

So the conventions live in a document at `workspace/knowledge/schema.md`, loaded into the
context of every persist and synthesis operation. **Structure is the contract**: the
heuristic floor and the LLM tier write into the same shape, which is what lets the
intelligence tier be swapped without rewriting the store.

**The document is owner-editable and never overwritten.** A scaffold is written once if
absent; after that it is read-only from the system's side. Schema-edit *proposals* route
through the learning queue like any other durable change — because a store's conventions
are exactly the kind of thing an eager synthesis pass would happily rewrite to suit
whatever it was doing that night.
"""

from __future__ import annotations

import logging
from pathlib import Path

from personalclaw.knowledge.semantics import KINDS, RELATION_TYPES

logger = logging.getLogger(__name__)

SCHEMA_FILENAME = "schema.md"

#: How much of the conventions document is loaded into a write/synthesis prompt. Bounded
#: because this is prepended to EVERY knowledge operation: an owner who writes an essay
#: here would otherwise pay for it on every persist for the rest of the store's life.
CONTEXT_BUDGET_CHARS = 4_000


def schema_path(workspace: Path | str) -> Path:
    return Path(workspace) / "knowledge" / SCHEMA_FILENAME


def default_scaffold() -> str:
    """The starting conventions document.

    Written as something an owner would actually edit rather than a template full of
    placeholders: the kinds and relation verbs are generated from the code, so the scaffold
    cannot drift from the vocabulary the store enforces.
    """
    kinds = "\n".join(f"- `{kind}`" for kind in KINDS)
    verbs = ", ".join(f"`{verb}`" for verb in RELATION_TYPES)
    return f"""# Knowledge conventions

This document is the contract for how knowledge is written in this store. It is loaded
into the context of every persist and synthesis operation, so what you write here shapes
what gets stored.

It is yours. Nothing overwrites it — the system proposes changes through the review queue
rather than editing it directly.

## Kinds in use

{kinds}

Use the narrowest kind that fits. A `fact` that is really an argument should be an
`insight`; an `insight` nobody sourced should not be stored at all.

## Titles

One idea per title, phrased as the thing itself rather than as a question. Titles are half
an item's identity — two writes with the same normalized title are treated as the same
item — so a title that describes a topic rather than a claim will collect unrelated edits.

## Linking

Relations use these verbs: {verbs}.

Prefer `supersedes` over deleting: a superseded item stays queryable, which is how "what
did we believe in June" remains answerable. Use `contradicts` when two items genuinely
disagree — it is a flag for review, not a failure.

## Claims and evidence

A claim is phenomenon-level: "cold starts are slow", not "cold starts took 4.2s on
Tuesday". Numbers and specifics belong in the quote, so claims from different sources
about the same phenomenon can be compared at all.

Every claim carries the source's own words. A paraphrase loses the thing that made the
source worth citing.

## Emphasis

Say what you could not establish. A report that quietly omits its gaps reads as more
complete than it is, and the reader has no way to know which parts to trust.
"""


def ensure_scaffold(workspace: Path | str) -> tuple[Path, bool]:
    """Write the scaffold if absent. Returns (path, created).

    Never overwrites. An owner's conventions are the one thing in the store the system has
    no business editing — and a "helpful" refresh that reformatted them would silently
    discard the reasoning they encode.
    """
    path = schema_path(workspace)
    if path.exists():
        return path, False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_scaffold(), encoding="utf-8")
    except OSError:
        logger.debug("could not write the knowledge schema scaffold", exc_info=True)
        return path, False
    return path, True


def load_conventions(workspace: Path | str, *, budget: int = CONTEXT_BUDGET_CHARS) -> str:
    """The conventions text for a prompt, bounded.

    Truncated at a LINE boundary rather than mid-sentence: half a convention is worse than
    none, because the reader acts on the half they can see. An absent document returns ""
    rather than the scaffold — a store with no conventions should behave as though it has
    none, not as though it silently adopted the defaults.
    """
    path = schema_path(workspace)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) <= budget:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = len(line) + 1
        if used + cost > budget:
            break
        used += cost
        kept.append(line)
    kept.append("\n…[conventions truncated — see workspace/knowledge/schema.md]")
    return "\n".join(kept)
