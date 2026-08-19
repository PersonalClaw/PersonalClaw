"""No-model degraded mode — the platform-wide fallback contract (PLATFORM-RESILIENCE §5).

Every model-dependent surface declares its LLM-free tier explicitly, so offline
operation is *designed* rather than accidental. A degraded surface never
error-walls: it does less, says so, and (where a queue exists) leaves the rest for
a drain when a provider returns.

A ``DegradedContract`` names the surface, the model use-cases it needs, a
human-readable floor statement, and a read-only ``backlog_probe`` for its
pending-enrichment count. Availability is the cheap no-instantiate probe
``can_resolve_use_case`` over every needed use-case — so this registry is derived,
never persisted (§7: recomputable, so never stored).

Scope note (honest, verified against code 2026-07-25): three floors the plan names
are **future infrastructure** and are NOT built here — LEARN-R19's memory-staging
log, KNOW-R17's heuristic knowledge extractor, and the synthesis watchers all live
in unbuilt Workflows-v2 plans. This session registers the floors that exist *today*
(with real backlog probes where a store already tracks the deficit, and an honest
``0`` where the queue is future infra) and the ``drain`` hook is ``None`` for every
contract — drains become §4 remediation-engine jobs when that lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# A backlog probe is a read-only, exception-safe callable returning the count of
# items awaiting model-enrichment for its surface (0 when the surface has no queue,
# or when the backing store isn't present yet).
BacklogProbe = Callable[[], int]
DrainFn = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class DegradedContract:
    """One model-dependent surface's declared no-model tier.

    ``surface`` is a stable slug (an agent/UI branches on it). ``use_cases`` are the
    ``active_models`` use-cases the surface needs to run at full capability. ``floor``
    is the human statement of what still works with no model. ``backlog_probe``
    returns the pending-enrichment count (read-only, fail-safe). ``drain`` re-enriches
    the backlog when a provider returns — ``None`` until the §4 engine owns drains.
    """

    surface: str
    use_cases: tuple[str, ...]
    floor: str
    backlog_probe: BacklogProbe = lambda: 0
    drain: Optional[DrainFn] = None


# ── The module registry ──────────────────────────────────────────────────────

_CONTRACTS: dict[str, DegradedContract] = {}


def register_contract(contract: DegradedContract) -> None:
    """Register (or replace, by surface) a degraded contract."""
    _CONTRACTS[contract.surface] = contract


def all_contracts() -> list[DegradedContract]:
    """Every registered contract (registration order)."""
    return list(_CONTRACTS.values())


def get_contract(surface: str) -> Optional[DegradedContract]:
    return _CONTRACTS.get(surface)


# ── Availability evaluation + transition notification ────────────────────────

# Last-seen availability per surface, so we notify only on a CHANGE (down / recovery)
# rather than every poll. Seeded on the first evaluation (silent baseline — no boot
# storm). Process-global by design (one gateway); reset helper for tests.
_last_available: dict[str, bool] = {}


def reset_transition_state() -> None:
    """Clear the transition baseline (test isolation)."""
    _last_available.clear()


def _available(contract: DegradedContract) -> bool:
    """Is every use-case this surface needs resolvable right now? (cheap, no instantiate)"""
    from personalclaw.providers.provider_bridge import can_resolve_use_case

    try:
        return all(can_resolve_use_case(uc) for uc in contract.use_cases)
    except Exception:  # a probe fault must never make a surface look falsely down
        logger.debug("degraded: availability probe raised for %s", contract.surface, exc_info=True)
        return True


def _backlog(contract: DegradedContract) -> int:
    """The surface's pending-enrichment count — read-only and exception-safe."""
    try:
        return max(0, int(contract.backlog_probe()))
    except Exception:
        logger.debug("degraded: backlog probe raised for %s", contract.surface, exc_info=True)
        return 0


def evaluate(*, notify: bool = False, state: object = None) -> list[dict]:
    """Evaluate every contract → a list of ``{surface, available, floor, backlog,
    use_cases}`` rows (the ``GET /api/resilience/degraded`` payload).

    When ``notify`` is set and a ``state`` with a ``.notify`` method is given, a
    surface CHANGING availability emits one notification: ``warning`` on going down,
    ``info`` (with the drained/backlog summary) on recovery. The first evaluation of
    a surface only seeds the baseline — it never notifies (no boot storm).
    """
    rows: list[dict] = []
    for contract in _CONTRACTS.values():
        available = _available(contract)
        backlog = _backlog(contract)
        rows.append(
            {
                "surface": contract.surface,
                "available": available,
                "floor": contract.floor,
                "backlog": backlog,
                "use_cases": list(contract.use_cases),
            }
        )
        if notify:
            _maybe_notify(contract, available, backlog, state)
    return rows


def _maybe_notify(contract: DegradedContract, available: bool, backlog: int, state: object) -> None:
    prev = _last_available.get(contract.surface)
    _last_available[contract.surface] = available
    if prev is None or prev == available:
        return  # first sight (silent baseline) or no change
    notify_fn = getattr(state, "notify", None)
    if not callable(notify_fn):
        return
    try:
        if not available:  # went down
            notify_fn(
                "warning",
                f"{contract.surface} degraded",
                f"No model for {', '.join(contract.use_cases)} — {contract.floor}",
            )
        else:  # recovered
            tail = f" · {backlog} item(s) awaiting re-enrichment" if backlog else ""
            notify_fn(
                "info",
                f"{contract.surface} recovered",
                f"Model available again for {', '.join(contract.use_cases)}{tail}.",
            )
    except Exception:
        logger.debug("degraded: notify failed for %s", contract.surface, exc_info=True)


def degraded_surfaces() -> list[str]:
    """The surfaces currently unavailable (no notify, no state) — a cheap rollup for
    the Doctor and the shell chip's count."""
    return [r["surface"] for r in evaluate() if not r["available"]]


# ── Backlog probes (read-only, fail-safe) ────────────────────────────────────
#
# Each returns 0 on any error or when the backing store isn't present. Only surfaces
# whose deficit is ALREADY tracked by an existing store get a real count; the rest
# report 0 honestly (their pending-enrichment queue is future infra — see the module
# docstring).


def _inbox_backlog() -> int:
    """Pending inbox items with no LLM classification yet — the enrichment the
    background model would add. Uses the existing ``classification`` field (no new
    marker); ingestion/dedup/keyword-alerts already ran (the deterministic floor)."""
    from personalclaw.inbox import InboxStore

    return sum(1 for item in InboxStore().pending() if not getattr(item, "classification", ""))


def _search_backlog() -> int:
    """Active knowledge items missing an embedding — the vector arm's backfill queue.
    The retrieval ladder already degrades to FTS+graph without these (the floor); this
    is what a re-index drain would process when an embedder returns."""
    from personalclaw.knowledge import get_knowledge_store

    return int(get_knowledge_store().count_items_missing_embedding())


# ── The initial contract set (only floors that exist in code today) ──────────


def _register_builtin_contracts() -> None:
    # chat — honestly unavailable with no model; the composer shows the needs_model
    # affordance + a Doctor link. No fake fallback, no queue.
    register_contract(
        DegradedContract(
            surface="chat",
            use_cases=("chat",),
            floor="Chat is unavailable without a model — the composer shows how to bind one. "
            "PersonalClaw never fakes a reply.",
        )
    )
    # inbox — keyword/name-mention alerts + ingestion/dedup/mute are all zero-LLM
    # (evaluate_alert). Only classify/draft/digest need a model; un-classified pending
    # items are the backlog a drain would enrich.
    register_contract(
        DegradedContract(
            surface="inbox_enrichment",
            use_cases=("chat",),
            floor="Keyword and name-mention alerts, ingestion, dedup and mute all keep working "
            "without a model; only auto-classify, draft and digest pause.",
            backlog_probe=_inbox_backlog,
        )
    )
    # memory extraction — deterministic preference-facet capture continues; only the
    # LLM skill-ladder review pauses. (LEARN-R19's staging queue is future infra, so
    # there is no pending-enrichment count to report yet — honestly 0.)
    register_contract(
        DegradedContract(
            surface="memory_extraction",
            use_cases=("chat",),
            floor="Deterministic preference-facet capture keeps running without a model; only the "
            "LLM after-turn review pauses.",
        )
    )
    # knowledge ingest — raw text is captured and stored without a model (passthrough /
    # document-read nodes); only LLM entity/insight extraction is skipped, leaving the
    # item 'partial'. (KNOW-R17's heuristic extractor is future infra.)
    register_contract(
        DegradedContract(
            surface="knowledge_ingest",
            use_cases=("chat",),
            floor="Documents are still captured and stored without a model; entity and insight "
            "extraction is skipped and the item is marked partial until a model returns.",
        )
    )
    # scheduled research reports (WF2KNO-12) — with no model a run cannot write its finding,
    # and that is a DEFERRAL rather than a loss: the failure is recorded without advancing the
    # run stamp or the watermark, so the next window sees exactly the same new material again.
    register_contract(
        DegradedContract(
            surface="research_report",
            use_cases=("background",),
            floor="A scheduled report's run is deferred, not dropped: the failure is recorded "
            "without advancing its last-run stamp or its watermark, so the next window retries "
            "over the same new material. Definitions, schedules and manual runs stay available.",
        )
    )
    # search ranking — hybrid retrieval degrades vector-arm-off to FTS + graph +
    # recency automatically; the real backlog is items missing an embedding.
    register_contract(
        DegradedContract(
            surface="search_ranking",
            use_cases=("embedding",),
            floor="Search degrades from hybrid to keyword (FTS) + graph + recency ranking with no "
            "embedding model; results stay useful, just not semantically ranked.",
            backlog_probe=_search_backlog,
        )
    )
    # transcription/speech — an unbound stt/tts/diarization use-case turns that feature
    # visibly off (the executor gate skips its nodes). A declared 'feature off' floor;
    # no queue.
    register_contract(
        DegradedContract(
            surface="transcription",
            use_cases=("stt",),
            floor="Speech-to-text is simply off without a model — its pipeline nodes are skipped, "
            "not errored. Bind an STT model to turn it on.",
        )
    )
    # the catch-all reasoning axis behind one_shot_completion — the background/reasoning
    # label collapses to the chat axis, so a surface with no dedicated contract that
    # calls one_shot_completion is covered here (the default 'skip + show degraded').
    register_contract(
        DegradedContract(
            surface="assistant_reasoning",
            use_cases=("chat",),
            floor="Background reasoning tasks (cron NL parsing, chat retag, loop summaries, "
            "web-extract) pause without a model and resume when one returns.",
        )
    )


_register_builtin_contracts()
