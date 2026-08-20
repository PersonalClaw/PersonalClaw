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

**All three of clause 7's named jobs are now registered.** An earlier revision of this module
recorded the third one — "the graph linker backfill" — as having no callable, which was true
when measured: the only backfills were `chunk_backfill.backfill_item_chunks`,
`store.backfill_entity_description` (a one-entity setter) and `ArtifactIngest.backfill`, and the
nearest linker was a PRIVATE helper inside an action provider. Rather than register a private
helper across a module boundary, the public pass was built: `knowledge/link_backfill.py`.

**The one pass here that is genuinely RESUMABLE is the linker backfill**, and it is the only
one registered `batched=True`. The distinction is not cosmetic — see `MaintenancePass.batched`.
The lint and the consolidation sweep both return a REPORT (findings, clusters), so a host
reading that as remaining work would re-run them once per allowed sub-batch forever. The
linker backfill returns ITEMS PROCESSED and 0 when the backlog is drained, which is the
contract the sub-batch loop is written against.

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

    The only RESUMABLE pass registered here (`batched=True`): it returns items PROCESSED and 0
    when the backlog is drained, so the host's sub-batch loop drains it across a tick instead of
    re-running a whole-store sweep. `link_backfill` keys that backlog on a `mention_sweeps` row
    rather than on "has no mentions", because an item may legitimately name no known entity —
    keyed the other way the head of the backlog would absorb every sub-batch forever.
    """
    from personalclaw.knowledge import link_backfill

    return link_backfill.link_backfill_pass(
        batch_size=batch_size or link_backfill.BATCH_SIZE,
    )


def register_all() -> list[str]:
    """Register every standing pass. Idempotent; returns the names registered.

    Each registration is independently guarded: a module that fails to import must cost its
    own pass and not the others, because the failure mode this replaces was "nothing had a
    cadence at all".

    `batched` is per pass and NOT a shared default — the linker backfill drains a real backlog
    while the other two return a report. Getting that wrong in either direction is silent: a
    sweep marked resumable busy-loops on its own finding count, and a backlog marked
    single-sweep only ever drains one batch per tick.
    """
    from personalclaw.knowledge import maintenance

    registered: list[str] = []
    for name, fn, batched in (
        (PASS_MEMORY_LINT, _memory_lint_pass, False),
        (PASS_CONSOLIDATION, _consolidation_pass, False),
        (PASS_LINK_BACKFILL, _link_backfill_pass, True),
    ):
        try:
            maintenance.register_pass(name, fn, batched=batched)
            registered.append(name)
        except Exception:  # noqa: BLE001
            logger.warning("maintenance pass %r not registered", name, exc_info=True)
    return registered
