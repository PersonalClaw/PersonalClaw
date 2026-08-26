"""Stage 5 — rank + render (PROACTIVE-ASSISTANT §1.5).

Ranking is the substrate's materiality order and nothing else (`MATERIALITY_ORDER`, AUTO-R2):
runs that touched the world lead, errors follow, words follow those, noise sinks. Re-deriving
a second weighting here — "importance", "urgency" — would be a fifth dialect for the same
question the Run Ledger already answers, so this module consumes that vocabulary rather than
inventing beside it.

Rendering is deliberately deterministic and deliberately NOT a model call. A digest whose body
was written by a model is a second place injected content can reach a human, and the §1.3 call
has already been paid; the body here is assembled from typed fields the pipeline holds. That
also makes the digest assertable: a test can require that a dropped item's title is absent,
which is the property the gate's refusal path only *means* something through.

The digest is `info`-ranked on purpose. `notification_allowed` defers `info` inside quiet
hours, which for a MORNING digest is the correct behaviour rather than a limitation — success
criterion 1 asks for exactly that deferral.
"""

from __future__ import annotations

from dataclasses import dataclass

from personalclaw.proactive.manifest import (
    MATERIALITY_ERROR,
    SOURCE_RUN,
    CollectedItem,
    Manifest,
    materiality_rank,
)
from personalclaw.proactive.proposals import Proposal

#: Notification kind for a digest. `info` so quiet hours defer it (§1.5). An error inside the
#: window does NOT promote the digest: promoting would mean a run failure at 02:00 wakes the
#: user through the surface whose whole promise is that it waits until morning.
DIGEST_NOTIFY_KIND = "info"

#: Digest title. Stable, because it is also the notification's dedupe-visible text.
DIGEST_TITLE = "Morning triage"


def _item_sort_key(item: CollectedItem) -> tuple[int, str, str]:
    # Descending timestamp inside a materiality band: within "things that touched the world",
    # the most recent is the one the user has least context on.
    return (materiality_rank(item.materiality), _invert(item.ts), item.ordinal)


def _invert(ts: str) -> str:
    """Sort a string timestamp descending inside an ascending tuple sort."""
    return "".join(chr(0x10FFFD - ord(c)) if ord(c) < 0x10FFFD else c for c in ts)


def rank_items(items: tuple[CollectedItem, ...] | list[CollectedItem]) -> tuple[CollectedItem, ...]:
    """Materiality-first ordering. Ordinals are untouched — ranking reorders, never renumbers."""
    return tuple(sorted(items, key=_item_sort_key))


def rank_proposals(
    proposals: tuple[Proposal, ...] | list[Proposal],
    manifest: Manifest,
) -> tuple[Proposal, ...]:
    """Order proposals by the materiality of the item each one is about.

    A proposal whose item has left the manifest cannot happen (the ordinal contract refuses it
    upstream), but the lookup still tolerates it rather than raising: a ranking function is the
    wrong place for the id contract to be re-litigated, and a crash here would lose a digest
    that had already been paid for.
    """

    def key(p: Proposal) -> tuple[int, str, str]:
        item = manifest.by_ordinal(p.item_id)
        if item is None:
            return (len(("action", "error", "response", "none")), "", p.item_id)
        return _item_sort_key(item)

    return tuple(sorted(proposals, key=key))


@dataclass(frozen=True)
class Digest:
    """The rendered digest: what `notify` receives, plus the counts a ledger row carries."""

    title: str
    body: str
    kind: str = DIGEST_NOTIFY_KIND
    collected: int = 0
    proposed: int = 0
    surfaced: int = 0
    dropped: int = 0


def _line(item: CollectedItem) -> str:
    who = f" — {item.sender}" if item.sender else ""
    link = f" ({item.permalink})" if item.permalink else ""
    return f"  {item.ordinal}. {item.title}{who}{link}"


def render_digest(
    manifest: Manifest,
    *,
    kept: tuple[CollectedItem, ...],
    proposals: tuple[Proposal, ...],
    dropped_count: int,
    degraded: bool = False,
    auto_lines: tuple[str, ...] = (),
) -> Digest:
    """Assemble the digest body from typed fields — no model call, no free-text passthrough.

    Sections, in the order §1.5 asks for: what your machine did (the run lane, permalinks
    inline), what needs you (ranked proposals with their enforced tier), then everything else
    as a ranked list. A window the gate emptied renders the "nothing needs you" line rather
    than an empty body, because a blank digest reads as a broken digest.

    `auto_lines` is §1.6 bound 4's half of the first section: what the machine did WITHOUT
    being asked, each line naming the rule that authorised it. It joins the run lane under one
    heading rather than getting its own, because "what your machine did" is one question and
    two headings would make the user read twice to answer it — and it is rendered FIRST inside
    that section, since an action already taken outranks a run that merely finished.

    **`proposals` must be the PENDING set, not the batch.** The caller auto-executes before
    rendering (`pipeline.run_triage`), so anything that ran is in `auto_lines`; passing the
    whole batch here would list an item under "needs you" that the machine had already handled
    seconds earlier, which is the one thing a digest cannot get wrong.
    """
    ranked = rank_items(kept)
    ranked_proposals = rank_proposals(proposals, manifest)
    proposal_ids = {p.item_id for p in ranked_proposals}

    sections: list[str] = []

    machine = [i for i in ranked if i.source == SOURCE_RUN]
    if machine or auto_lines:
        lines = ["What your machine did:"]
        lines.extend(auto_lines)
        for item in machine:
            flag = " [needs you]" if item.materiality == MATERIALITY_ERROR else ""
            lines.append(f"{_line(item)}{flag}")
        sections.append("\n".join(lines))

    if ranked_proposals:
        lines = ["Needs you:"]
        for p in ranked_proposals:
            about = manifest.by_ordinal(p.item_id)
            subject = about.title if about is not None else f"item {p.item_id}"
            lines.append(f"  {p.item_id}. [{p.tier}] {p.action_type} — {subject}")
            if p.reasoning:
                lines.append(f"       {p.reasoning}")
        sections.append("\n".join(lines))
    elif degraded:
        sections.append("Needs you:\n  (no proposals this run — the proposal stage was refused)")

    rest = [i for i in ranked if i.source != SOURCE_RUN and i.ordinal not in proposal_ids]
    if rest:
        lines = ["Also waiting:"]
        lines.extend(_line(item) for item in rest)
        sections.append("\n".join(lines))

    if dropped_count:
        sections.append(f"Filtered by your rules: {dropped_count}")

    if not sections:
        sections.append("Nothing needs you — the window was quiet.")

    return Digest(
        title=DIGEST_TITLE,
        body="\n\n".join(sections),
        kind=DIGEST_NOTIFY_KIND,
        collected=len(manifest),
        proposed=len(ranked_proposals),
        surfaced=len(kept),
        dropped=dropped_count,
    )


__all__ = [
    "DIGEST_NOTIFY_KIND",
    "DIGEST_TITLE",
    "Digest",
    "rank_items",
    "rank_proposals",
    "render_digest",
]
