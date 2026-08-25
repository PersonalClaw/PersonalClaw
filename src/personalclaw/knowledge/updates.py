"""ONE updater for a knowledge item: propose, then accept.

WF2KNO-11 clause B. An update to an existing knowledge item runs as a PROPOSAL the owner
inspects and accepts or dismisses, so generated prose never silently overwrites human
writing. The plain "just update it" path is the SAME path with the acceptance folded in —
``auto_accept=True`` files the proposal and immediately accepts it — rather than a second
function that writes to the row directly.

That second function is exactly the drift this atom removes. A direct-write update sitting
beside a propose-an-update is two code paths over one row: the queue's decision memory, its
fingerprint dedup, its inbox surfacing and its audit trail all attach to one of them, and
every caller then silently picks which of those guarantees it gets. So there is no direct
writer in here. The ONLY way proposed content reaches the store is
:func:`personalclaw.learning.proposals.accept`'s installer, which is why ``auto_accept`` is
a parameter of the propose call and not a branch around the queue.

The write itself goes through :meth:`~personalclaw.knowledge.store.KnowledgeStore.
update_item` — the store's own writer, FTS sync included. This module holds no SQL and no
second upsert, and it does not re-implement validation either: ``semantics.check_persist``
is the single validation entry point and is called before anything is queued.

:func:`regenerate_synthesis` sits ON TOP of that one updater rather than beside it: it
recomputes a synthesized item from the sources the item itself cites and hands the result to
:func:`propose_update`. The recompute lives here rather than in the dashboard route so the
route cannot grow a second one, and enqueueing through ``propose_update`` is what makes a
regeneration inherit the stored fields, the validation, the identical-to-stored no-op and the
content-hash idempotence instead of re-deciding all four at a second call site.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from personalclaw.knowledge import consolidation, semantics
from personalclaw.learning import proposals

logger = logging.getLogger(__name__)

#: The queue kind every knowledge draft and knowledge update is filed under.
DRAFT_KIND = proposals.Kind.KNOWLEDGE_DRAFT.value

#: Longest body a queued proposal may carry. A proposal is something a human reads in an
#: inbox card, not a document. The clamp lives here rather than in a caller because it is
#: part of the enqueue: two callers clamping differently is two queues.
MAX_BODY_CHARS = 8000

#: What a SKIP means, said in the one place that can say it. ``enqueue`` logs the specific
#: reason at debug and deliberately does not return it — the queue's decision memory is not
#: the caller's business.
SKIP_REASON = "a prior decision covers it, or it is below the evidence floor"

#: Tag prefix carrying the content hash of a proposed update. It is what lets a re-proposal
#: of the SAME edit find its own pending row instead of reinforcing it into a second review.
#: Keyed on the item plus this hash — never on the body text, which the queue's own
#: similarity cascade already reads for a different purpose.
HASH_TAG_PREFIX = "content-hash:"

#: How much of the digest the tag carries. Enough to be collision-free per item, short
#: enough to read in an inbox card.
HASH_TAG_CHARS = 16


@dataclass(frozen=True)
class UpdateOutcome:
    """What one :func:`propose_update` call did. A dashboard route returns this verbatim."""

    item_id: str
    #: The queued proposal, or "" when nothing was queued (a no-op, a validation failure,
    #: or a SKIP the queue's decision memory forbade).
    proposal_id: str = ""
    #: True ONLY when the write actually landed in the store.
    applied: bool = False
    #: True when it is waiting for the owner to accept or dismiss it.
    pending: bool = False
    #: True on EXACTLY one path: the idempotence check found this same edit already pending
    #: for this item and returned that row's id rather than queueing a second review. It is
    #: what lets a caller distinguish "your edit is now waiting" from "your edit was already
    #: waiting" — two different sentences to show a user, and `pending` is True for both.
    #: False everywhere else, including the byte-identical no-op and a validation refusal.
    already_pending: bool = False
    #: One short human phrase: why it is pending, why it was refused, or why it was a no-op.
    #: Empty when the update applied — there is nothing to explain.
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def hash_tag(digest: str) -> str:
    """The idempotence tag for a content digest."""
    return f"{HASH_TAG_PREFIX}{digest[:HASH_TAG_CHARS]}"


def queue_draft(
    *,
    title: str,
    body: str,
    target: str = "",
    provenance: str = "inferred",
    source_cadence: str = "",
    run_id: str = "",
    source_excerpt: str = "",
    occurrences: int = 0,
    tags: list[str] | None = None,
) -> tuple[str, str, str]:
    """File ONE knowledge proposal. Returns ``(verdict, proposal_id, skip_reason)``.

    The single enqueue site for knowledge. Both the ``knowledge-propose`` action provider
    and :func:`propose_update` come through here, so the kind, the body clamp and the
    meaning of a SKIP are decided once.

    ``skip_reason`` is non-empty exactly when the queue said SKIP — which is a SUCCESS, not
    a failure: a prior decision forbids re-filing, or an inferred proposal is below the
    evidence floor. Not nagging is the feature.
    """
    verdict, prop = proposals.enqueue(
        kind=DRAFT_KIND,
        title=title,
        body=body[:MAX_BODY_CHARS],
        target=target,
        provenance=provenance,
        source_cadence=source_cadence,
        run_id=run_id,
        source_excerpt=source_excerpt,
        occurrences=occurrences,
        tags=list(tags or []),
    )
    pid = str(getattr(prop, "id", "") or "") if prop is not None else ""
    if verdict is proposals.Verdict.SKIP:
        return verdict.value, pid, SKIP_REASON
    return verdict.value, pid, ""


def pending_update(item_id: str, digest: str) -> proposals.Proposal | None:
    """The pending proposal already carrying this exact edit for this item, if any.

    Keyed on the item (the proposal's ``target``) plus the content digest, so proposing the
    same edit twice is one review rather than two. The queue's own fingerprint cascade would
    REINFORCE the row instead — bumping a reinforcement count as if a second independent
    observation had happened, which for a re-submitted edit is simply untrue.
    """
    tag = hash_tag(digest)
    for prop in proposals.list_pending(DRAFT_KIND):
        if prop.target == item_id and tag in prop.tags:
            return prop
    return None


async def propose_update(
    store: Any,
    item_id: str,
    *,
    content: str | None = None,
    summary: str | None = None,
    claims: list | None = None,
    citations: list | None = None,
    auto_accept: bool,
) -> dict:
    """Propose an update to an existing knowledge item. Returns an :class:`UpdateOutcome`.

    ``auto_accept=False`` leaves the item untouched and the proposal pending: the owner's
    stored writing survives until they say otherwise. ``auto_accept=True`` runs the SAME
    enqueue and then immediately accepts it, so even the unattended path leaves a queue row,
    a recorded decision and an audit entry behind.

    Every field left at ``None`` is inherited from the stored row, so a caller editing only
    the summary cannot blank the content by omission.

    ``auto_accept`` presents itself to the gate as the owner, because the only caller
    entitled to pass it is an owner-initiated edit (a dashboard PUT, a CLI edit). An agent
    or engine caller passes ``auto_accept=False`` and waits — the gate in
    ``proposals.accept`` refuses a non-human actor outright under every trust mode.

    Async because both an action provider and a dashboard route await it, and because the
    accept installer is the seam where a future embedding refresh would land.
    """
    row = store.get_item(item_id)
    if not row:
        return UpdateOutcome(item_id=item_id, reason=f"no knowledge item {item_id!r}").to_dict()

    if content is None and summary is None and claims is None and citations is None:
        return UpdateOutcome(
            item_id=item_id,
            reason="nothing proposed — supply content, summary, claims or citations",
        ).to_dict()

    meta = dict(row.get("file_metadata") or {})
    title = str(row.get("title") or "")
    kind = str(row.get("kind") or "") or "fact"
    stored_content = str(row.get("content") or "")
    stored_summary = str(row.get("summary") or "")
    stored_claims = [c for c in (meta.get("claims") or []) if isinstance(c, dict)]
    stored_citations = [str(c) for c in (meta.get("citations") or [])]

    next_content = stored_content if content is None else str(content)
    next_summary = stored_summary if summary is None else str(summary)
    next_claims = stored_claims if claims is None else [c for c in claims if isinstance(c, dict)]
    next_citations = stored_citations if citations is None else [str(c) for c in citations]

    # Validation is NOT re-implemented here: `check_persist` is the single entry point, and
    # its failure is handed back as the outcome's reason rather than raised. A caller that
    # proposed something unwritable needs the sentence, not a traceback.
    check = semantics.check_persist(
        kind=kind,
        title=title,
        content=next_content,
        summary=next_summary,
        claims=next_claims,
        citations=next_citations,
    )
    if not check.ok:
        return UpdateOutcome(item_id=item_id, reason=check.error).to_dict()

    # The stored digest is recomputed from the row through the SAME function, never read
    # from the `content_hash` column: a row written by a path that never set that column
    # would otherwise read as an edit of itself on every single call.
    stored_digest = semantics.content_hash(
        title=title, content=stored_content, summary=stored_summary, claims=stored_claims
    )
    if check.content_hash == stored_digest:
        return UpdateOutcome(
            item_id=item_id,
            reason="identical to the stored item — nothing to propose",
        ).to_dict()

    already = pending_update(item_id, check.content_hash)
    if already is not None:
        return UpdateOutcome(
            item_id=item_id,
            proposal_id=already.id,
            pending=True,
            already_pending=True,
            reason="this exact edit is already waiting for review",
        ).to_dict()

    verdict, pid, skip_reason = queue_draft(
        title=title,
        body=next_content,
        target=item_id,
        provenance="human" if auto_accept else "inferred",
        source_cadence="knowledge-update",
        # The evidence for an UPDATE is the writing it would replace. A reviewer deciding
        # whether generated prose may overwrite human prose needs the human prose in front of
        # them; the queue fences it, which is right — it is store content, not instructions.
        source_excerpt=stored_content,
        tags=[hash_tag(check.content_hash), "knowledge-update"],
    )
    if skip_reason or not pid:
        return UpdateOutcome(
            item_id=item_id, reason=skip_reason or "the queue filed nothing"
        ).to_dict()

    if not auto_accept:
        return UpdateOutcome(
            item_id=item_id,
            proposal_id=pid,
            pending=True,
            reason="waiting for the owner to accept or dismiss it",
        ).to_dict()

    landed: list[bool] = []

    def _install(_prop: proposals.Proposal) -> None:
        """Apply the row. The only write in this module, reachable only through accept."""
        if next_claims or claims is not None:
            meta["claims"] = next_claims
        if next_citations or citations is not None:
            meta["citations"] = next_citations
        # ONLY the fields `update_item` actually honors. Measured against `_ITEM_COLUMNS`:
        # `kind`, `logical_key` and `content_hash` are columns the store's public updater does
        # not accept — passing them would be silently dropped, which is the worst shape of all
        # (a write that reports success and stores nothing). Identity is not editable through
        # this API anyway: `kind` and the title that derives `logical_key` are inherited from
        # the row. The consequence to know is that the `content_hash` COLUMN goes stale after
        # an accepted update, so this module recomputes the stored digest from the row's own
        # fields (below) rather than trusting that column.
        store.update_item(
            item_id,
            title=title,
            content=next_content,
            summary=next_summary,
            file_metadata=meta,
        )
        landed.append(True)

    try:
        proposals.accept(pid, installer=_install, actor="user")
    except proposals.AcceptError as exc:
        # The row is still pending: `accept` records its decision only after the install
        # succeeds, so the owner can still act on it.
        logger.warning("knowledge update %s could not be accepted: %s", pid, exc)
        return UpdateOutcome(
            item_id=item_id, proposal_id=pid, pending=True, reason=f"accept refused: {exc}"
        ).to_dict()

    if not landed:
        # Accept returned without the installer running — the write did not happen, so this
        # must not claim it did.
        return UpdateOutcome(
            item_id=item_id,
            proposal_id=pid,
            pending=True,
            reason="the accept step did not apply the write",
        ).to_dict()
    return UpdateOutcome(item_id=item_id, proposal_id=pid, applied=True).to_dict()


# ── regenerating a synthesis (the staleness banner's one action) ──


class SynthesisUnavailable(RuntimeError):
    """No model produced a synthesis, so there was nothing to propose.

    ENVIRONMENTAL, not a content refusal: the corpus was fine and the recompute simply could
    not run. Raised rather than folded into an :class:`UpdateOutcome` reason because the caller
    has to tell those two apart WITHOUT matching on a prose sentence — the dashboard route
    answers 503 here and 200-with-a-reason for a refusal, and a route branching on ``reason``
    text would change meaning the day the sentence is reworded.
    """


#: The use case a regeneration resolves its model through — the same one the report provider
#: uses for report prose (``action_providers/knowledge_report_provider.py:587``). A synthesis
#: is reasoning-grade work; running it on the background tier would quietly produce a worse
#: document than the one it proposes to replace.
SYNTHESIS_USE_CASE = "reasoning"


async def _synthesis_completion(prompt: str) -> str:
    """The ONE model call a regeneration makes, split out so a test can drive the recompute.

    ``one_shot_completion`` returns a FALSY value rather than raising when no model is bound,
    so the caller tests the returned text and never waits for an exception that will not come.
    The ``or ""`` here normalizes the shape only — it is NOT the degradation check, which is
    :func:`regenerate_synthesis`' business and visible to the reader.
    """
    from personalclaw.llm_helpers import one_shot_completion

    return str(await one_shot_completion(prompt, use_case=SYNTHESIS_USE_CASE) or "")


def synthesis_sources(store: Any, item_id: str) -> list[consolidation.Item]:
    """The items this synthesis cites, as they read NOW. The inputs a recompute re-reads.

    Read through the store's public ``item_citations`` rather than a query of this module's
    own: the citation rows ARE the document's declared provenance, so "what would this say if
    it were written again" has one answer instead of a second definition of relatedness that
    can drift from the one :mod:`personalclaw.knowledge.staleness` measures.

    **Known scope, stated because the banner counts more than this.** Staleness rule (b) —
    active, non-synthesized items sharing a tag that arrived after the synthesis — is not in
    this set. That population is only reachable through the private
    ``staleness._new_tagged_items`` (``knowledge/staleness.py:146``), which returns a COUNT and
    not ids. So a regeneration recompiles the sources the document declares; it does not
    silently re-scope the document onto material it never cited. What the reviewer gets is
    therefore arguable rather than oracular, which is the same bargain ``scope`` strikes on the
    staleness payload.
    """
    seen: set[str] = set()
    sources: list[consolidation.Item] = []
    for citation in store.item_citations(item_id):
        source_id = str(citation.get("source_item_id") or "")
        if not source_id or source_id == item_id or source_id in seen:
            continue
        seen.add(source_id)
        row = store.get_item(source_id)
        if row:
            sources.append(consolidation.Item.from_row(row))
    return sources


async def regenerate_synthesis(store: Any, item_id: str, *, completion: Any = None) -> dict:
    """Recompute ONE synthesized item from its sources and file the result for review.

    The action behind the staleness banner, and it has to actually regenerate: the shape this
    replaced called :func:`propose_update` with no content at all, which returned "nothing
    proposed" while the route reported success — an inert control on the only remedy the banner
    offers.

    It enqueues THROUGH :func:`propose_update` rather than through :func:`queue_draft`
    directly. ``queue_draft`` is the raw enqueue underneath; reaching past ``propose_update``
    to it would be a second updater for one row (the drift this module's header refuses) and
    would give up the four things that layer owns: the stored fields a recompile inherits so it
    cannot blank the summary by omission, ``semantics.check_persist``, the identical-to-stored
    no-op, and the content-hash idempotence that makes a second click the SAME review
    (``already_pending``) instead of a second one.

    ``completion`` overrides the model call for tests. Returns an :class:`UpdateOutcome` dict,
    whose ``pending``/``proposal_id`` are the only honest evidence that anything was filed.
    Raises :class:`SynthesisUnavailable` when the recompute produced no text.
    """
    row = store.get_item(item_id)
    if not row:
        return UpdateOutcome(item_id=item_id, reason=f"no knowledge item {item_id!r}").to_dict()

    sources = synthesis_sources(store, item_id)
    if not sources:
        # A recompute with no inputs cannot produce anything, and a model asked to consolidate
        # nothing would invent the document instead — the one outcome a synthesis must never
        # have. Refused with the reason a reader can act on (add a citation), not with a 200
        # that claims a proposal exists.
        return UpdateOutcome(
            item_id=item_id,
            reason="nothing to regenerate from — this synthesis cites no sources to re-read",
        ).to_dict()

    # The SAME prompt the consolidation pass synthesizes with, doctrine and fencing included.
    # A second prompt here would mean two definitions of what a synthesis may say.
    prompt = consolidation.synthesis_prompt(consolidation.Cluster(items=sources))
    caller = completion or _synthesis_completion
    text = str(await caller(prompt) or "").strip()
    if not text:
        raise SynthesisUnavailable(
            "regeneration needs a model and none produced a synthesis — bind one for the "
            f"{SYNTHESIS_USE_CASE} use case in Settings → Models"
        )

    return await propose_update(store, item_id, content=text, auto_accept=False)
