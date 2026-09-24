"""Register the standing maintenance jobs on KL-14's host.

Three jobs documented a cadence and had none. Before this module the memory lint ran only
when someone opened the Health panel, knowledge consolidation only when an action dispatched
it, and the chunk backfill only at gateway boot. Each is idempotent and set-shaped, which is
exactly what a watermark-triggered host is for.

**Registration is a separate module from the host on purpose.** `maintenance.py` must be
importable by the write path — `store.py` calls `mark_dirty` on every index-affecting write
— and if the host imported the jobs, every knowledge write would drag in the memory service,
the consolidation planner and an action-provider module. So the host knows nothing about its
passes and this module is imported once, at gateway startup, where paying for those imports
is already the cost of being a gateway.

**KL-19 added a fifth pass: `derived_refresh`.** The structural editing verbs invalidate the
chunk layer and the whole-item vector of every item they touch (a split whose halves keep the
parent's vectors is silently wrong), and something has to rebuild them on a cadence. Before it
the chunk backfill ran only at gateway boot and nothing at all drained a NULLed item vector, so
"refreshed through the maintenance host" would have meant "refreshed at the next restart".

**All three of clause 7's named jobs are now registered.** An earlier revision of this module
recorded the third one — "the graph linker backfill" — as having no callable, which was true
when measured: the only backfills were `chunk_backfill.backfill_item_chunks`,
`store.backfill_entity_description` (a one-entity setter) and `ArtifactIngest.backfill`, and the
nearest linker was a PRIVATE helper inside an action provider. Rather than register a private
helper across a module boundary, the public pass was built: `knowledge/link_backfill.py`.

**Three of the five passes here are genuinely RESUMABLE** — the linker backfill, the
similarity-edge pass and KL-19's derived refresh — and only those three are registered
`batched=True`. The distinction is not cosmetic — see `MaintenancePass.batched`. The lint and
the consolidation sweep both return a REPORT (findings, clusters), so a host reading that as
remaining work would re-run them once per allowed sub-batch forever. The three backfills
return WORK DONE and 0 when the backlog is drained, which is the contract the sub-batch loop
is written against.

**KL-13's similarity-edge pass is hosted here for the reason the clause names: "never inline
on the write path."** Similarity is the most expensive graph work in the store — it compares a
new item against the library — and it is exactly the shape that tempts an inline call at
`create_typed_item`. Inline, a bulk import of N items does N passes, each superseded by the
next, while holding the write lock. Registered here it inherits the watermark's coalescing (N
writes, one pass) and the sub-batch loop's bounded claims. Nothing in `store.py` calls it; the
write path's only obligation is the `mark_dirty` it already performs.

**What is still deliberately not done: consolidation does not APPLY.** It runs through the real
gated executor in dry-run. `_apply` writes a model-authored summary and then archives every
input item, and putting that on an unattended cadence is an autonomy decision this atom does
not own — workspace guidance orders Autonomy Guardrails before any unattended-execution work.
See `run_consolidation_pass` for the full statement; what changed here is that the min-hours
and min-cluster gates now bind on a cadence instead of only when a human opens a panel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.memory_providers.base import MemoryProvider

logger = logging.getLogger(__name__)

#: Names, so the wiring test asserts against one spelling rather than two copies of a string.
PASS_MEMORY_LINT = "memory_lint"
PASS_CONSOLIDATION = "knowledge_consolidation"
PASS_LINK_BACKFILL = "entity_link_backfill"
PASS_SIMILARITY_EDGES = "similarity_edges"
PASS_DERIVED_REFRESH = "derived_refresh"
PASS_VAULT_PROJECTION = "knowledge_vault_projection"


def _memory_lint_pass(*, batch_size: int = 0) -> int:
    """Run the memory-health sweep. Returns the number of findings (a REPORT, not a backlog).

    Registered `batched=False`: this is a whole-store sweep, and its return value counts
    ISSUES FOUND. A store with three standing findings returns 3 every time, so a host that
    read that as remaining work would re-lint it once per allowed sub-batch, forever.
    """
    from personalclaw import memory_service
    from personalclaw.memory import MemoryStore

    # A real provider, not `None`. `service_for(None)` happened to work at runtime and mypy
    # caught it: the signature wants a `MemoryProvider`, and relying on undocumented
    # tolerance of None is how a later `hasattr(provider, ...)` branch starts skipping
    # silently. `MemoryStore()` is the markdown provider `service_for` documents as "the
    # common per-session case", and it resolves the same home this pass runs under.
    #
    # The `cast` is the same one `mcp_workflows.py:1566` and `dashboard/state.py:968` already
    # use for this call: `service_for`'s annotation says `MemoryProvider` while its own
    # docstring documents `MemoryStore` support, so the annotation is narrower than the
    # contract. Following the existing convention rather than widening another module's
    # signature inside this atom.
    service = memory_service.service_for(cast("MemoryProvider", MemoryStore()))
    if service is None:
        return 0
    report = service.lint()
    if not isinstance(report, dict):
        return 0
    # Count the findings rather than trusting a top-level total the report may not carry:
    # a shape change upstream should read as "no findings", never as a crash on a tick.
    total = 0
    for value in report.values():
        if isinstance(value, list):
            total += len(value)
    return total


def _consolidation_pass(*, batch_size: int = 0) -> int:
    """Report how many clusters the gated consolidation pass identified. Still DRY RUN.

    Delegates to `knowledge_maintain_provider.run_consolidation_pass`, which drives the real
    `KnowledgeConsolidateActionProvider.execute`. That indirection is the substantive part: the
    gate (`check_gates`) lives inside `execute`, so the user's `consolidate_min_hours` and
    `consolidate_min_cluster` are honoured on this cadence. The first version of this function
    called `plan_consolidation` directly and therefore bypassed the gate entirely — two
    round-tripped config knobs that nothing read.

    A DECLINED gate returns 0, which is a normal outcome of a frequent cadence rather than an
    error, and is indistinguishable here from "no clusters" on purpose: both mean "nothing to
    report this tick". `batched=False` for the same reason as the lint — the count is a report.
    """
    from personalclaw.action_providers import knowledge_maintain_provider as kmp

    return kmp.run_consolidation_pass(batch_size=batch_size)


def _link_backfill_pass(*, batch_size: int = 0) -> int:
    """Sweep one bounded batch of never-linked items through the deterministic alias linker.

    RESUMABLE (`batched=True`), like the similarity-edge pass and unlike the two sweeps: it
    returns items PROCESSED and 0 when the backlog is drained, so the host's sub-batch loop
    drains it across a tick instead of re-running a whole-store sweep. `link_backfill` keys that
    backlog on a `mention_sweeps` row rather than on "has no mentions", because an item may
    legitimately name no known entity — keyed the other way the head of the backlog would
    absorb every sub-batch forever.
    """
    from personalclaw.knowledge import link_backfill

    return link_backfill.link_backfill_pass(
        batch_size=batch_size or link_backfill.BATCH_SIZE,
    )


def _similarity_edge_pass(*, batch_size: int = 0) -> int:
    """Claim one bounded batch of the similarity-edge backlog. Returns items processed.

    RESUMABLE (`batched=True`), for the same reason as the linker backfill and not the lint:
    `similarity_pass` returns items PROCESSED and 0 when the backlog is drained, so the value
    is remaining work and the host's sub-batch loop is what finishes the library. Marked
    `batched=False` this would still import, still register and still look wired while draining
    exactly ONE batch per tick — a library larger than one batch would never converge and
    nothing in `MaintenanceResult` would say so.

    This function is the whole reason the pass is not inline: the edge build is the store's most
    expensive graph work, and hanging it off `create_typed_item` would do it once per imported
    item while holding the write lock. Here it runs once per watermark, off the write path.

    `batch_size=0` means "the host has no opinion", so `similarity_pass`'s own default binds
    rather than this module duplicating a constant that belongs to the pass.
    """
    from personalclaw.knowledge import similarity_edges

    if batch_size > 0:
        return similarity_edges.similarity_pass(batch_size=batch_size)
    return similarity_edges.similarity_pass()


def _derived_refresh_pass(*, batch_size: int = 0) -> int:
    """Rebuild the chunk layer and the whole-item vectors that have been INVALIDATED.

    KL-19's structural editing verbs (split, extract, merge, retitle …) rewrite item bodies,
    and every derived artifact computed from the old text is wrong the instant they do. The
    verbs therefore INVALIDATE rather than recompute — drop the chunks, NULL the item vector,
    release the similarity claims, clear the sweep markers — and this pass is what makes that
    honest instead of merely tidy. Without it the invalidation would be the whole story and a
    split's halves would sit vector-less until the next gateway boot: the chunk backfill ran
    only at startup and nothing at all drained a NULLed item vector on a cadence.

    RESUMABLE (`batched=True`), and the return value is PROGRESS, not backlog size. That
    distinction is load-bearing here in a way it is not for the other backfills: the chunk
    backfill's `unchanged` bucket holds items it cannot ever chunk (content blank to
    `str.strip()` but not to SQLite), which stay in the backlog forever. Returning
    `chunked + reembedded` means those items contribute 0, the host stops claiming, and a
    library with one unchunkable item still converges. Returning a backlog COUNT here would
    busy-loop `max_batches` times per tick on an item that can never leave it.

    Embeddings need a live embedder, so with none bound this returns 0 and the backlog waits —
    the same "the library stays keyword-searchable and resumes once a model is bound" contract
    `chunk_backfill` states. A refresh that silently wrote no vectors while reporting progress
    would be worse than one that waits.
    """
    from personalclaw.knowledge import chunk_backfill, get_knowledge_embedder, get_knowledge_store

    embedder = get_knowledge_embedder()
    if embedder is None:
        return 0
    store = get_knowledge_store()
    bounded = batch_size if batch_size > 0 else chunk_backfill.BATCH_SIZE
    done = 0
    result = chunk_backfill.backfill_item_chunks(store, embedder, max_items=bounded)
    done += int(result.get("chunked") or 0)
    # `only_missing` — never a whole-library re-embed on a maintenance tick. That is
    # `reembed_all`'s other caller (the model-switch re-index), and running it on a cadence
    # would re-embed every item the user owns every time any write moved the watermark.
    embedded = store.reembed_all(embedder, only_missing=True, limit=bounded)
    done += int(embedded.get("reembedded") or 0)
    return done


def _vault_projection_pass(*, batch_size: int = 0) -> int:
    """Reconcile the markdown projection of the library against the store (KL-20).

    Hosted HERE rather than on a cadence of its own, which is what the atom asks for: the
    projection is idempotent, set-shaped, watermark-triggered work — the exact shape KL-14
    exists for — and a second loop writing the owner's files would be the second projector
    this atom was written to avoid, arriving as a scheduler instead of as a module.

    RESUMABLE (`batched=True`) and the return value is PROGRESS, not backlog size, for the
    reason `_derived_refresh_pass` states: every refusal (a two-sided conflict, a page the
    owner deleted, an item too large to project) records a durable ledger state so it
    contributes 0 on the next sub-batch. Returning a backlog COUNT here would busy-loop
    `max_batches` times per tick on a page only the owner can resolve.

    Mode `off` — the shipped default — returns 0 having touched nothing, so an unconfigured
    install pays one config read per tick and writes no files at all.
    """
    from personalclaw.knowledge import vault

    return vault.projection_pass(batch_size=batch_size)


def register_all() -> list[str]:
    """Register every standing pass. Idempotent; returns the names registered.

    Each registration is independently guarded: a module that fails to import must cost its
    own pass and not the others, because the failure mode this replaces was "nothing had a
    cadence at all".

    `batched` is per pass and NOT a shared default — the linker backfill and the similarity-edge
    pass each drain a real backlog, while the lint and the consolidation sweep return a report.
    Getting that wrong in either direction is silent: a sweep marked resumable busy-loops on its
    own finding count, and a backlog marked single-sweep only ever drains one batch per tick.
    """
    from personalclaw.knowledge import maintenance

    registered: list[str] = []
    for name, fn, batched in (
        (PASS_MEMORY_LINT, _memory_lint_pass, False),
        (PASS_CONSOLIDATION, _consolidation_pass, False),
        (PASS_LINK_BACKFILL, _link_backfill_pass, True),
        (PASS_SIMILARITY_EDGES, _similarity_edge_pass, True),
        # Registered BEFORE the similarity pass would want its output, but the host runs
        # passes in sorted-name order, and `derived_refresh` < `similarity_edges` happens to
        # put the rebuild first. That ordering is convenient, not relied upon: each pass is
        # keyed on its own sweep markers, so a refresh landing after the edge pass in one tick
        # simply re-arms it and the next tick converges.
        (PASS_DERIVED_REFRESH, _derived_refresh_pass, True),
        # KL-20. Sorted-name order puts it after `derived_refresh` and before
        # `memory_lint`/`similarity_edges`, which is convenient (an absorbed vault edit has
        # already invalidated its derived layer by then) and, like every other ordering here,
        # not relied upon: the projection is keyed on its own ledger, so landing in any order
        # converges on the next tick.
        (PASS_VAULT_PROJECTION, _vault_projection_pass, True),
    ):
        try:
            maintenance.register_pass(name, fn, batched=batched)
            registered.append(name)
        except Exception:  # noqa: BLE001
            logger.warning("maintenance pass %r not registered", name, exc_info=True)
    return registered
