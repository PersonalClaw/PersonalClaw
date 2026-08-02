"""The project Session Brief — push-based retrieval into workflow RUNS (KNOW-R12).

A workflow that has to ASK for context gets it only when the template author thought to add a
retrieve node. The Session Brief inverts that: every run in a project starts with a bounded
digest of what the store already knows about that project, including its decision log, so a
resumed or forked run does not re-litigate settled choices.

**Scope guard, and it is load-bearing.** This composes into RUN context ONLY. PersonalClaw has a
deliberate invariant that knowledge is never ambiently injected into CHAT — it enters a chat
session through the composer's @-picker or the agent's `knowledge_search` tool, both of which are
the user asking. Nothing here may be reachable from a chat-context path; a brief that leaked into
chat would silently convert an explicit user action into an ambient one, and the user would have
no way to tell where the context came from.

**Bounded, and honest about it.** A brief that quietly dropped half the decision log would let a
run contradict a decision it was never shown. So the budget is enforced by DROPPING WHOLE ITEMS,
newest and highest-precedence first, and the brief says how many it left out.

**Fenced.** Knowledge items partly derive from web and inbox content, so the brief is untrusted
data. It is composed as fenced blocks with the same doctrine as `fenced_sources`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Default ceiling for a whole brief. `KnowledgeConfig.session_brief_max_tokens` overrides it.
#: Small on purpose: this is injected into EVERY run in a project, so its cost is paid over and
#: over and a generous default would be a permanent tax nobody attributes to the right feature.
DEFAULT_MAX_TOKENS = 800

#: The usual ~4 chars/token approximation. Used rather than a tokenizer because this runs at run
#: start on the hot path, and being wrong by 10% on a budget is fine while being slow is not.
CHARS_PER_TOKEN = 4

#: Kinds that earn a place before anything else, in order. A decision is the highest-value thing
#: in the store for a resumed run: the journal says what happened, the decision says WHY, and
#: only the second one stops the run from re-deciding it.
PRIORITY_KINDS = ("decision", "overview", "insight")

#: Per-item cap, so one long report cannot consume the whole budget and crowd out five decisions.
MAX_ITEM_CHARS = 600


@dataclass
class BriefItem:
    """One entry considered for the brief."""

    item_id: str = ""
    kind: str = "fact"
    title: str = ""
    body: str = ""
    origin: str = "external"
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> BriefItem:
        import json

        data = dict(row) if not isinstance(row, dict) else row
        try:
            meta = json.loads(data.get("file_metadata") or "{}")
        except (TypeError, ValueError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        source = meta.get("source")
        origin = meta.get("origin") or (
            source.get("origin", "") if isinstance(source, dict) else ""
        )
        return cls(
            item_id=str(data.get("id", "") or ""),
            kind=str(data.get("kind", "") or "fact"),
            title=str(data.get("title", "") or ""),
            body=str(data.get("summary") or data.get("content") or ""),
            origin=str(origin or "external"),
            updated_at=str(data.get("updated_at", "") or ""),
        )

    @property
    def text(self) -> str:
        capped = self.body.strip()[:MAX_ITEM_CHARS]
        return f"{self.title}\n{capped}".strip() if self.title else capped


@dataclass
class SessionBrief:
    """The composed brief, plus what it had to leave out."""

    items: list[BriefItem] = field(default_factory=list)
    dropped: int = 0
    project: str = ""
    budget_tokens: int = DEFAULT_MAX_TOKENS

    @property
    def empty(self) -> bool:
        return not self.items

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "item_count": len(self.items),
            "dropped": self.dropped,
            "budget_tokens": self.budget_tokens,
            "item_ids": [i.item_id for i in self.items],
        }

    def render(self) -> str:
        """The fenced, numbered block a run's context receives.

        Returns "" when empty — NOT a header with nothing under it. A "what is already known"
        heading followed by blank space reads to a model as "this project has no prior knowledge",
        which is a claim, and a false one when the real answer is "the brief was not built".
        """
        if not self.items:
            return ""
        from personalclaw.security import fence_untrusted

        header = [
            f"Already known about this project ({len(self.items)} items). "
            "Build on these rather than re-deriving them; cite as [n] when you use one.",
        ]
        if self.dropped:
            # Stated, not silent. A run that believes it saw everything will contradict what it
            # was not shown and have no reason to doubt itself.
            header.append(
                f"({self.dropped} more items did not fit the context budget — "
                "retrieve explicitly if you need them.)"
            )
        blocks = [
            fence_untrusted(f"[{index}] {item.text}", source=f"knowledge:{item.kind}")
            for index, item in enumerate(self.items, start=1)
        ]
        return "\n".join(header + blocks)


def compose(
    items: list[BriefItem],
    *,
    project: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SessionBrief:
    """Pick what fits, in priority order.

    Decisions first (a resumed run re-litigating a settled choice is the failure this exists to
    prevent), then overviews and insights, then everything else newest-first. Within a tier,
    user-origin items outrank system-compiled ones.

    Items are dropped WHOLE. A truncated decision is worse than an absent one: half a rationale
    reads as a complete one, and a run would act on the half it saw.
    """
    budget_chars = max(0, int(max_tokens)) * CHARS_PER_TOKEN
    ranked = sorted(items, key=_rank)
    chosen: list[BriefItem] = []
    used = 0
    for item in ranked:
        cost = len(item.text) + 40  # +fence overhead, so the budget is not silently overshot
        if used + cost > budget_chars:
            continue  # skip, do not stop: a long item must not block every shorter one after it
        chosen.append(item)
        used += cost
    return SessionBrief(
        items=chosen,
        dropped=len(items) - len(chosen),
        project=project,
        budget_tokens=int(max_tokens),
    )


def _rank(item: BriefItem) -> tuple[int, int, str]:
    """Sort key: kind priority, then origin, then recency (newest first)."""
    kind_rank = (
        PRIORITY_KINDS.index(item.kind) if item.kind in PRIORITY_KINDS else len(PRIORITY_KINDS)
    )
    origin_rank = 0 if item.origin.strip().lower() in ("user", "human") else 1
    # Inverted string sort for recency: ISO stamps compare lexically, and negating is not
    # available for strings, so the key is the complement.
    return (kind_rank, origin_rank, _invert(item.updated_at))


def _invert(stamp: str) -> str:
    """Make a lexical ascending sort descending, so newer items come first."""
    return "".join(chr(0x10FFFF - ord(ch)) if ord(ch) < 0x10FFFF else ch for ch in (stamp or ""))


# ── project scoping ──


def project_tag(project_id: str) -> str:
    """The tag a project's knowledge is filed under.

    Knowledge has ONE global library with no partitions — a project is a tag, not a database. So
    scoping is a tag filter, and the normalization has to match what the persist path writes or
    the brief silently returns nothing for every project.
    """
    return re.sub(r"[^a-z0-9]+", "-", (project_id or "").lower()).strip("-")


def load_items(store: Any, *, project_id: str, limit: int = 50) -> list[BriefItem]:
    """Candidate items for a project's brief.

    An unscoped call returns NOTHING rather than the whole store: a brief with no project is not
    "everything the user knows", and injecting the entire library into every run would be both
    expensive and wrong. The over-fetch (`limit` above what fits) is deliberate — the budget
    decides what makes it, and it needs candidates to choose among.
    """
    tag = project_tag(project_id)
    if not tag:
        return []
    try:
        rows = list(
            store.db.execute(
                "SELECT i.id, i.kind, i.title, i.summary, i.content, i.updated_at, "
                "i.file_metadata FROM items i "
                "JOIN item_tags it ON it.item_id = i.id "
                "JOIN tags t ON t.id = it.tag_id "
                "WHERE t.name = ? AND i.is_archived = 0 "
                "ORDER BY i.updated_at DESC LIMIT ?",
                (tag, max(1, limit)),
            )
        )
    except Exception:
        # A brief is an enhancement, never a precondition. A store that cannot answer must not
        # stop the run — it just starts without the digest.
        logger.debug("session brief query failed for project %r", project_id, exc_info=True)
        return []
    return [BriefItem.from_row(row) for row in rows]


def build(store: Any, *, project_id: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> SessionBrief:
    """The whole path: load, rank, fit. Never raises."""
    try:
        return compose(
            load_items(store, project_id=project_id),
            project=project_id,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.debug("session brief composition failed", exc_info=True)
        return SessionBrief(project=project_id, budget_tokens=int(max_tokens))
