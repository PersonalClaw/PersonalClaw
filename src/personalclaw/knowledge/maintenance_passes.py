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

**What is NOT registered here, and why (KL-14 clause 7 is therefore PARTIAL).** The clause
names "the graph linker backfill" as the third job. There is no such callable: measured with
`grep -rn "def .*backfill" src/personalclaw/knowledge/` the only backfills are
`chunk_backfill.backfill_item_chunks` (KL-12, registered by the boot-hook replacement),
`store.backfill_entity_description` (a one-entity setter, not a pass) and
`artifact_ingest.ArtifactIngest.backfill`. The nearest thing to a linker pass is
`action_providers/knowledge_maintain_provider._reindex` / `_wikilink_mentions`, both PRIVATE
helpers inside an action provider and neither a resumable whole-library sweep. Registering a
private helper across a module boundary, or inventing a public linker backfill, would be a
different atom's design decision made silently here — so the clause is recorded unmet with
this evidence instead.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.memory_providers.base import MemoryProvider

logger = logging.getLogger(__name__)

#: Names, so the wiring test asserts against one spelling rather than two copies of a string.
PASS_MEMORY_LINT = "memory_lint"
PASS_CONSOLIDATION = "knowledge_consolidation"


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
    """Plan consolidation and report how many clusters are worth a pass. PLAN ONLY.

    🔴 Deliberately does not EXECUTE. `KnowledgeConsolidateActionProvider.execute` spends
    model calls and merges items, and moving that onto an unattended cadence is a decision
    about autonomy — a background pass that rewrites the user's library without being asked
    is a different thing from one that keeps an index fresh. `plan_consolidation`'s own
    docstring is "Everything a pass would do, without doing any of it", which is precisely
    the half that belongs on a maintenance cadence today: it surfaces the work so the Health
    panel and the digest can offer it.

    Returns the number of clusters planned. `batched=False` for the same reason as the lint.
    """
    from personalclaw.action_providers import knowledge_maintain_provider as kmp
    from personalclaw.knowledge import consolidation

    try:
        store = kmp._open_store()
    except Exception:  # noqa: BLE001 — an unavailable store is not a tick failure
        logger.debug("consolidation pass: knowledge store unavailable", exc_info=True)
        return 0
    items = [i for i in kmp._load_items(store) if not i.consolidated and not i.is_archived]
    if not items:
        return 0
    plan = consolidation.plan_consolidation(items)
    clusters = getattr(plan, "clusters", None)
    return len(clusters) if isinstance(clusters, list) else 0


def register_all() -> list[str]:
    """Register every standing pass. Idempotent; returns the names registered.

    Each registration is independently guarded: a module that fails to import must cost its
    own pass and not the others, because the failure mode this replaces was "nothing had a
    cadence at all".
    """
    from personalclaw.knowledge import maintenance

    registered: list[str] = []
    for name, fn in (
        (PASS_MEMORY_LINT, _memory_lint_pass),
        (PASS_CONSOLIDATION, _consolidation_pass),
    ):
        try:
            maintenance.register_pass(name, fn, batched=False)
            registered.append(name)
        except Exception:  # noqa: BLE001
            logger.warning("maintenance pass %r not registered", name, exc_info=True)
    return registered


def _probe_unregistered_linker_backfill() -> Any:
    """Intentionally absent. See the module docstring: KL-14's third named job has no callable.

    Kept as a named stub ONLY so a reader grepping for "linker" in this module finds the
    reason rather than concluding it was forgotten. It is never registered and never called.
    """
    raise NotImplementedError(
        "KL-14 names a 'graph linker backfill' that does not exist as a callable; see this "
        "module's docstring for the measurement and why inventing one belongs to another atom"
    )
