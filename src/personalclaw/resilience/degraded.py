"""No-model degraded mode — the platform-wide fallback contract.

Every model-dependent surface declares its LLM-free tier explicitly, so offline
operation is *designed* rather than accidental. A degraded surface never
error-walls: it does less, says so, and (where a queue exists) leaves the rest for
a drain when a provider returns.

A ``DegradedContract`` names the surface, the model use-cases it needs, a
human-readable floor statement, and a read-only ``backlog_probe`` for its
pending-enrichment count. Availability is the cheap no-instantiate probe
``can_resolve_use_case`` over every needed use-case — so this registry is derived,
never persisted (recomputable, so never stored).

Scope note (verified against code 2026-08-24): the three floors this module used to
call "future infrastructure" now EXIST, so they are declared rather than deferred
(PR2-9). LEARN-R19's staging log is real (``learning.staging.StagingStore``:
``flush_records`` + unconsumed-entry backlog), the no-model knowledge-ingest tier is
real (the raw/LLM-free ingest graph, which lands an item ``partial`` with
"insights: model unavailable" — the stamp KNOW-R17 describes), and synthesis
watchers are real (``mode: append_evidence`` persists evidence with no model while
``knowledge.staleness`` counts what the compiled section has not caught up with).

Each of those three surfaces therefore carries BOTH halves of the contract: a
backlog probe that counts its own deficit and a ``drain`` that re-enriches it when a
provider returns. Drains are fired here, on the unavailable→available transition
 — ``evaluate`` runs in a worker thread, so :func:`_fire_drain` schedules onto
a running loop when there is one and otherwise runs the coroutine to completion in
that thread. A drain reports how many items it moved; it never raises into the
caller and never blocks the poll it rides on.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from personalclaw.providers.use_cases import USE_CASE_NAMES

logger = logging.getLogger(__name__)

# A backlog probe is a read-only, exception-safe callable returning the count of
# items awaiting model-enrichment for its surface (0 when the surface has no queue,
# or when the backing store isn't present yet).
BacklogProbe = Callable[[], int]
# A drain takes the live gateway state when there is one (some drains need a running
# worker — the knowledge one re-enqueues through the ingest queue that lives on it) and
# returns how many items it moved, so a recovery is reportable as work done rather than
# as a hook that fired. ``None`` state is legal: a drain that cannot reach what it needs
# returns 0 rather than guessing.
DrainFn = Callable[[Optional[object]], Awaitable[int]]

#: How many items one drain pass moves. Bounded because a drain rides a recovery
#: transition, not a job queue: 500 knowledge items re-enqueued in one burst would spend
#: the model budget the user just got back on a backlog they never asked to clear first.
DRAIN_BATCH = 50


@dataclass(frozen=True)
class DegradedContract:
    """One model-dependent surface's declared no-model tier.

    ``surface`` is a stable slug (an agent/UI branches on it); ``label`` is what the USER
    calls it, and the only name a notification or the degraded chip shows. ``use_cases`` are
    the ``active_models`` use-cases the surface needs to run at full capability. ``floor``
    is the human statement of what still works with no model. ``backlog_probe``
    returns the pending-enrichment count (read-only, fail-safe). ``drain`` re-enriches
    the backlog when a provider returns; it is ``None`` for a surface whose floor is
    "feature off" or "honestly unavailable", because those have nothing to queue and a
    drain hook there would be a control nothing can ever move.
    """

    surface: str
    label: str
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

#: The surfaces the user was TOLD went down, each with whether a model was chosen for it then.
#: A recovery is announced only for one of these, in the words of the notice before it.
#:
#: 🔴 Measured on day 8: a fresh home has no model, so every surface's silent first sight is
#: "down", and binding the first model flipped all eleven model-backed surfaces at once, posting
#: eleven "<slug> recovered — Model available again" notifications about things that had never
#: been up. Setting up is not a recovery. Pairing each recovery with the degradation notice
#: before it is what makes "recovered" mean "it was down, you were told, and it is back".
_announced_down: dict[str, bool] = {}


def use_case_names(contract: DegradedContract) -> list[str]:
    """The user's names for the use cases *contract* needs, in its order."""
    return [USE_CASE_NAMES.get(uc, uc) for uc in contract.use_cases]


def reset_transition_state() -> None:
    """Clear the transition baseline (test isolation)."""
    _last_available.clear()
    _announced_down.clear()


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


def _model_chosen(contract: DegradedContract) -> bool:
    """Whether a model is chosen for every use case the surface needs (``model_chosen``), so an
    unavailable surface can say whether its model is unavailable or simply not chosen yet."""
    from personalclaw.providers.provider_bridge import model_chosen

    try:
        return all(model_chosen(uc) for uc in contract.use_cases)
    except Exception:  # a probe fault reads as chosen — the surface keeps saying "degraded"
        logger.debug("degraded: model-chosen probe raised for %s", contract.surface, exc_info=True)
        return True


def _sentence(text: str) -> str:
    """A diagnosis clause (``provider_bridge``'s ``why``/``fix``) as a sentence of its own."""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _problem(
    contract: DegradedContract, chosen: bool, known: dict[str, Optional[tuple[str, str]]]
) -> str:
    """What an unavailable surface is waiting on, in one sentence: the chip's row and the notice
    say it alike.

    With no model chosen, that no model is chosen — the chip's words for that state. With one
    chosen, why it cannot serve, as resolution would refuse it
    (``provider_bridge.use_case_problem``): "No Chat model", which the notice said for both, was
    false about a model that is chosen.
    ``known`` holds each use case's diagnosis once per evaluation: eleven surfaces need chat.
    """
    needs = " and ".join(use_case_names(contract))
    if not chosen:
        return f"No model chosen for {needs}."
    from personalclaw.providers.provider_bridge import use_case_problem

    for use_case in contract.use_cases:
        if use_case not in known:
            known[use_case] = use_case_problem(use_case)
        found = known[use_case]
        if found is not None:
            why, fix = found
            return " ".join(part for part in (_sentence(why), _sentence(fix)) if part)
    return f"The {needs} model chosen cannot answer now."


def evaluate(*, notify: bool = False, state: object = None) -> list[dict]:
    """Evaluate every contract → a list of ``{surface, label, available, floor, backlog,
    use_cases, model_chosen, problem}`` rows (the ``GET /api/resilience/degraded`` payload).

    ``model_chosen`` is False on an unavailable surface when no model is chosen for a use case
    it needs (``provider_bridge.model_chosen``): it is waiting on a choice, not degraded, and the
    shell chip words it that way. ``problem`` says what an unavailable surface is waiting on
    (:func:`_problem`); ``None`` on one that is available.

    When ``notify`` is set and a ``state`` with a ``.notify`` method is given, the surfaces
    CHANGING availability in this evaluation are announced, ONE notification per kind of change
    however many surfaces it moved: ``warning`` "<surface> degraded" when the chosen model cannot
    serve, ``info`` "Choose a model for <surface>" when none is chosen (each saying the row's
    ``problem``), ``info`` "<surface> recovered" / "<surface> is ready" (with the drained/backlog
    summary) when it is back, and a recovery only after a notice the user was given. Several
    surfaces read "<n> surfaces …" and name each one. The first evaluation of a surface only
    seeds the baseline — it never notifies (no boot storm).
    """
    rows: list[dict] = []
    known: dict[str, Optional[tuple[str, str]]] = {}
    changes: list[_Change] = []
    for contract in _CONTRACTS.values():
        available = _available(contract)
        backlog = _backlog(contract)
        chosen = True if available else _model_chosen(contract)
        problem = None if available else _problem(contract, chosen, known)
        rows.append(
            {
                "surface": contract.surface,
                "label": contract.label,
                "available": available,
                "floor": contract.floor,
                "backlog": backlog,
                "use_cases": list(contract.use_cases),
                "model_chosen": chosen,
                "problem": problem,
            }
        )
        if notify:
            change = _transition(
                contract, available, backlog, state, chosen=chosen, problem=problem
            )
            if change is not None:
                changes.append(change)
    if changes:
        _announce(changes, state)
    return rows


#: The four ways a surface changes, in the order a single evaluation announces them. The two
#: downs are the chip's two states, in its words: a chosen model that cannot serve is a decline;
#: no model chosen is a choice to make, and "degraded" would claim a decline. The two ups follow
#: the notice before them: a decline recovers, and a choice made is "ready" (nothing declined).
_DECLINED, _UNCHOSEN, _RECOVERED, _READY = "declined", "unchosen", "recovered", "ready"
_CHANGE_ORDER = (_DECLINED, _UNCHOSEN, _RECOVERED, _READY)


@dataclass(frozen=True)
class _Change:
    """One surface's change of availability, to be announced with the others of its kind."""

    kind: str
    contract: DegradedContract
    #: What a surface that went down waits on (the row's ``problem``).
    problem: str = ""
    #: A recovery's drained/backlog clause (" · 3 item(s) re-enriched"), or ``""``.
    tail: str = ""


def _transition(
    contract: DegradedContract,
    available: bool,
    backlog: int,
    state: object,
    *,
    chosen: bool,
    problem: Optional[str],
) -> Optional[_Change]:
    """Record one surface's availability and return its change for :func:`_announce`, or None
    when there is nothing to announce: its first sight (the silent baseline), no change, no
    notify sink, or a recovery the user was never told went down."""
    prev = _last_available.get(contract.surface)
    _last_available[contract.surface] = available
    if prev is None or prev == available:
        return None  # first sight (silent baseline) or no change
    drained: Optional[int] = None
    if available and contract.drain is not None:
        # The unavailable→available flip is what fires the drain. Before the
        # notification, and independent of whether a notify sink exists — the
        # re-enrichment is the promise the floor made; the message about it is not.
        drained = _fire_drain(contract, state)
    if not callable(getattr(state, "notify", None)):
        return None
    if not available:  # went down
        return _Change(_DECLINED if chosen else _UNCHOSEN, contract, problem=problem or "")
    if contract.surface not in _announced_down:
        return None  # never announced as down, so nothing to announce as back
    was_chosen = _announced_down.pop(contract.surface)
    # §5.2 criterion #3 wants the recovery to summarize what was RE-ENRICHED, and
    # `backlog` was measured BEFORE the drain ran — reporting it after a drain that
    # just cleared it would announce a queue that no longer exists. So: the drained
    # count when the drain finished here, the standing backlog when it moved nothing
    # or is still running, and nothing at all when there was never a queue.
    if drained:
        tail = f" · {drained} item(s) re-enriched"
    elif backlog:
        tail = f" · {backlog} item(s) awaiting re-enrichment"
    else:
        tail = ""
    return _Change(_RECOVERED if was_chosen else _READY, contract, tail=tail)


def _announce(changes: list[_Change], state: object) -> None:
    """Post one notification per kind of change in *changes*.

    🔴 ONE CHANGE, ONE NOTICE. Most model-backed surfaces need the Chat model, and Background and
    Reasoning resolve through it, so clearing Chat took ten surfaces down in one evaluation and
    posted ten "Choose a model for <surface>" notices, and choosing one again posted ten "is
    ready" ones. A notice now covers every surface one kind of change moved, and names each.
    """
    notify_fn = getattr(state, "notify", None)
    if not callable(notify_fn):
        return
    for kind in _CHANGE_ORDER:
        group = [c for c in changes if c.kind == kind]
        if not group:
            continue
        level, title, body = _notice(kind, group)
        try:
            notify_fn(level, title, body)
        except Exception:
            logger.debug("degraded: notify failed for %s", title, exc_info=True)
            continue
        if kind in (_DECLINED, _UNCHOSEN):
            for change in group:
                _announced_down[change.contract.surface] = kind == _DECLINED


def _notice(kind: str, group: list[_Change]) -> tuple[str, str, str]:
    """``(level, title, body)`` for the surfaces of *group*, which all changed as *kind* says.

    One surface keeps its own words, floor and all. Several are counted in the title and named
    in the body, which says what each waits on once: every surface's floor is in the degraded
    chip, and ten of them in one notice would bury the sentence that says what to do.
    """
    if len(group) == 1:
        change = group[0]
        label, floor = change.contract.label, change.contract.floor
        needs = " and ".join(use_case_names(change.contract))
        if kind == _DECLINED:
            return "warning", f"{label} degraded", f"{change.problem} {floor}"
        if kind == _UNCHOSEN:
            return "info", f"Choose a model for {label}", f"{change.problem} {floor}"
        if kind == _RECOVERED:
            return "info", f"{label} recovered", f"A {needs} model is available again{change.tail}."
        return "info", f"{label} is ready", f"A {needs} model is chosen{change.tail}."
    count = f"{len(group)} surfaces"
    labels = _join([c.contract.label for c in group])
    needs = _join(list(dict.fromkeys(n for c in group for n in use_case_names(c.contract))))
    tails = "".join(f" · {c.contract.label}: {c.tail.removeprefix(' · ')}" for c in group if c.tail)
    if kind == _DECLINED:
        problems = " ".join(dict.fromkeys(c.problem for c in group if c.problem))
        until = f"Until then {labels} do only what works without a model."
        return "warning", f"{count} degraded", f"{problems} {until}".strip()
    if kind == _UNCHOSEN:
        return (
            "info",
            f"Choose a model for {count}",
            f"No model chosen for {needs}. Until one is, {labels} do only what works without "
            "a model.",
        )
    if kind == _RECOVERED:
        return (
            "info",
            f"{count} recovered",
            f"A model is available again for {needs}, so {labels} are back{tails}.",
        )
    return (
        "info",
        f"{count} are ready",
        f"A model is chosen for {needs}, so {labels} can run now{tails}.",
    )


def _join(parts: list[str]) -> str:
    """``"A"``, ``"A and B"``, ``"A, B and C"``."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _fire_drain(contract: DegradedContract, state: object) -> Optional[int]:
    """Run one contract's drain on a recovery, from whichever thread we are on.

    Returns how many items it moved when it ran to completion here, and ``None`` when it
    was scheduled onto a running loop (nobody can honestly report a count for work that
    has not happened yet).

    ``evaluate`` is called through ``asyncio.to_thread`` (the Doctor route), so there is
    usually NO running loop here and ``create_task`` would raise — the drain would look
    wired and never run. So: schedule when a loop is running (never block it), and
    otherwise drive the coroutine to completion in this worker thread, which is already
    off the request path. Either way a fault is swallowed and logged: a broken drain must
    not turn a RECOVERY into an error.
    """
    drain = contract.drain
    if drain is None:
        return None

    async def _run() -> int:
        try:
            moved = int(await drain(state) or 0)
        except Exception:
            logger.debug("degraded: drain failed for %s", contract.surface, exc_info=True)
            return 0
        if moved:
            logger.info("degraded: drained %d item(s) for %s", moved, contract.surface)
        return moved

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            loop.create_task(_run())
            return None
        return asyncio.run(_run())
    except Exception:
        logger.debug("degraded: drain dispatch failed for %s", contract.surface, exc_info=True)
        return None


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


def _memory_staging_backlog() -> int:
    """Unconsumed LEARN-R19 staging entries — the captures no consolidation pass has
    compiled into a proposal yet.

    Reads the store WITHOUT creating it: ``StagingStore.__init__`` only computes paths
    (the schema is written on the first cursor), so a home that never staged anything is
    answered from the absent file. A backlog probe that materialised ``learning.db`` would
    be a write on every poll of a read-only rollup.
    """
    from personalclaw.learning.staging import get_store

    store = get_store()
    if not store.path.exists():
        return 0
    return store.pending_count()


async def _memory_staging_drain(state: Optional[object] = None) -> int:
    """Compile the staged captures that piled up while no model was bound into ONE
    propose-only lesson batch, then mark exactly those entries consumed.

    The pieces LEARN-R19 built for this were all present and unused: ``pending`` reads the
    queue, ``staging_refs`` carries the provenance onto the proposal, and ``mark_consumed``
    is the one mutation staging allows. This is their first caller.

    A proposal that was NOT written (a prior decision forbids re-filing, or an inferred
    batch is still under the evidence floor) consumes nothing: marking the entries consumed
    in exchange for a proposal that does not exist would delete the only record of the
    captures. The backlog therefore stays visible, which is the honest report.
    """
    from personalclaw.learning import proposals
    from personalclaw.learning.staging import get_store

    store = get_store()
    if not store.path.exists():
        return 0
    entries = store.pending(limit=DRAIN_BATCH)
    if not entries:
        return 0
    ids = [int(e.id) for e in entries]
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.LESSON_BATCH.value,
        title=f"{len(entries)} capture(s) staged while no model was bound",
        body="\n".join(f"- {e.content}" for e in entries),
        provenance="inferred",
        source_cadence=str(entries[0].cadence or ""),
        staging_refs=ids,
        # `occurrences=1`/`min_evidence=1`, the convention for a single first-class
        # signal: the batch IS the evidence. Passing `len(entries)` would claim N
        # independent observations of ONE claim, which is what the floor exists to
        # count — and would then hide every backlog under three entries behind it.
        occurrences=1,
        min_evidence=1,
    )
    if prop is None:
        return 0
    store.mark_consumed(ids, f"degraded-drain:{prop.id}")
    return len(ids)


#: The heuristic-tier stamp, as it actually exists (KNOW-R17's ``extraction: heuristic``
#: by another name): the LLM-free ingest graph completes, the insights stage finds no
#: model, and the runner downgrades the item recording exactly that reason.
#: Archived items are excluded for the same reason the batch regen route excludes them —
#: a drain should not spend the model the user just got back on content they put away.
#:
#: ``unsearchable`` is in the status set alongside ``partial`` because the stamp this
#: matches on is the *reason*, not the status. The no-provider first-run home is the case:
#: with neither an insights model nor an embedding model bound, the searchability
#: verdict is the louder of the two facts, so the runner files the item ``unsearchable``
#: and the insights reason rides along in ``processing_error``. Matching ``partial`` alone
#: would have silently emptied this backlog on exactly the homes it exists for — the drain
#: would report zero and binding a model would re-enrich nothing.
_HEURISTIC_ITEMS_SQL = (
    "SELECT id FROM items WHERE processing_status IN ('partial', 'unsearchable') "
    "AND COALESCE(processing_error, '') LIKE '%model unavailable%' "
    "AND status = 'active' AND COALESCE(is_archived, 0) = 0"
)


def _knowledge_heuristic_backlog() -> int:
    """Items the heuristic tier filed: captured, indexed, embedded — un-extracted."""
    from personalclaw.knowledge import get_knowledge_store

    store = get_knowledge_store()
    row = store.db.execute(
        f"SELECT COUNT(*) FROM ({_HEURISTIC_ITEMS_SQL})"
    ).fetchone()  # noqa: S608
    return int((row[0] if row else 0) or 0)


async def _knowledge_heuristic_drain(state: Optional[object] = None) -> int:
    """Re-extract the heuristic-tier items in place, through the ONE ingestion path.

    Re-enqueues onto the live ingest queue rather than calling the graph directly: that
    queue owns the LLM pool and the embedder factory, and it serialises against the store's
    single-threaded connection. Calling ``ingest_item`` from here with no pool would re-run
    the same LLM-free graph and re-file the item ``partial`` — a drain that provably
    changes nothing.

    Without a live queue (a Doctor run outside the gateway) this reports 0 and moves
    nothing. Claiming a re-enrichment that has no worker would be worse than saying no.
    """
    getter = getattr(state, "knowledge_ingest_queue", None)
    if not callable(getter):
        return 0
    try:
        queue = getter()
    except Exception:
        logger.debug("degraded: ingest queue unavailable for knowledge drain", exc_info=True)
        return 0
    if queue is None:
        return 0
    from personalclaw.knowledge import get_knowledge_store

    store = get_knowledge_store()
    rows = store.db.execute(
        f"{_HEURISTIC_ITEMS_SQL} LIMIT ?",  # noqa: S608
        (DRAIN_BATCH,),
    ).fetchall()
    moved = 0
    for row in rows:
        item_id = str(row[0])
        # Status-only transition — not a user edit, so `updated_at` must not move (the
        # same `touch=False` the regenerate route uses; a bumped stamp would re-stale
        # every synthesis that cites this item).
        store.update_item(item_id, processing_status="queued", touch=False)
        queue.enqueue_background(item_id)
        moved += 1
    return moved


#: How many synthesized items one staleness sweep looks at. Bounded because staleness is
#: a per-item join (cited sources + tag-overlap), so an unbounded sweep would make a
#: cheap rollup the most expensive query in the poll.
_SYNTHESIS_SCAN_CAP = 200


def _stale_synthesized_ids(store: Any, limit: int) -> list[str]:
    """The synthesized items the corpus has moved underneath, oldest-compiled first."""
    from personalclaw.knowledge.semantics import SYNTHESIZED_KINDS
    from personalclaw.knowledge.staleness import staleness_for

    kinds = sorted(SYNTHESIZED_KINDS)
    marks = ",".join("?" for _ in kinds)
    rows = store.db.execute(
        f"SELECT id FROM items WHERE item_type IN ({marks}) "  # noqa: S608
        "AND status = 'active' AND COALESCE(is_archived, 0) = 0 "
        "ORDER BY updated_at ASC LIMIT ?",
        (*kinds, int(_SYNTHESIS_SCAN_CAP)),
    ).fetchall()
    out: list[str] = []
    for row in rows:
        item_id = str(row[0])
        try:
            if staleness_for(store, item_id).stale:
                out.append(item_id)
        except Exception:
            logger.debug("degraded: staleness read failed for %s", item_id, exc_info=True)
        if len(out) >= limit:
            break
    return out


def _synthesis_stale_backlog() -> int:
    """Compiled sections the corpus has moved past — the rewrite queue for this surface."""
    from personalclaw.knowledge import get_knowledge_store

    return len(_stale_synthesized_ids(get_knowledge_store(), _SYNTHESIS_SCAN_CAP))


async def _synthesis_evidence_drain(state: Optional[object] = None) -> int:
    """Queue a compiled-section rewrite for each synthesis whose evidence moved on.

    ``mode: append_evidence`` is the floor: with no model the dated evidence entries still
    land (persist-raw-first), and only the compiled summary above them goes stale. So the
    drain's unit of work is one rewrite request per stale synthesis, filed through
    ``knowledge.updates.queue_draft`` — the single enqueue site for knowledge proposals,
    which is what keeps a machine-authored rewrite behind the same human gate a hand-written
    draft clears. It deliberately does NOT rewrite in place: a synthesis the reader may
    already have acted on must not change under them.

    Idempotent by the proposal queue's own content fingerprint: a second recovery over the
    same stale item REINFORCES the pending row instead of filing a duplicate.
    """
    from personalclaw.knowledge import get_knowledge_store
    from personalclaw.knowledge.staleness import staleness_for
    from personalclaw.knowledge.updates import queue_draft

    store = get_knowledge_store()
    queued = 0
    for item_id in _stale_synthesized_ids(store, DRAIN_BATCH):
        item = store.get_item(item_id) or {}
        report = staleness_for(store, item_id)
        title = str(item.get("title") or item_id)
        _verdict, pid, _skip = queue_draft(
            title=f"Recompile “{title}” — its sources moved",
            body=(
                f"{report.new_source_items} new source item(s) and "
                f"{report.changed_sources} changed cited source(s) landed after this "
                "synthesis was compiled. A model is available again: recompile the "
                "compiled section over the current corpus. The dated evidence entries "
                "below it were appended without a model and are already current."
            ),
            target=item_id,
            source_cadence="degraded-recovery",
            # `occurrences` deliberately unsupplied: the evidence floor counts repeated
            # observations of an inference, and staleness is a computed fact about this
            # one item. `queue_draft` takes no `min_evidence`, so supplying a count here
            # would file every rewrite request under a floor of three and drop it.
        )
        if pid:
            queued += 1
    return queued


# ── The initial contract set (only floors that exist in code today) ──────────


def _register_builtin_contracts() -> None:
    # chat — honestly unavailable with no model; the composer shows the needs_model
    # affordance + a Doctor link. No fake fallback, no queue.
    register_contract(
        DegradedContract(
            surface="chat",
            label="Chat",
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
            label="Inbox triage",
            use_cases=("chat",),
            floor="Keyword and name-mention alerts, ingestion, dedup and mute all keep working "
            "without a model; only auto-classify, draft and digest pause.",
            backlog_probe=_inbox_backlog,
        )
    )
    # memory extraction — deterministic preference-facet capture continues; only the
    # LLM skill-ladder review pauses. Every pass records its outcome in the LEARN-R19
    # staging log whether or not it produced anything, so the backlog is the unconsumed
    # captures and the drain is the consolidation pass over them.
    register_contract(
        DegradedContract(
            surface="memory_extraction",
            label="Learning from chats",
            use_cases=("chat",),
            floor="Deterministic preference-facet capture keeps running without a model; only the "
            "LLM after-turn review pauses. Every pass is still recorded in the staging log, and "
            "the captures wait there for a consolidation pass rather than being dropped.",
            backlog_probe=_memory_staging_backlog,
            drain=_memory_staging_drain,
        )
    )
    # knowledge ingest — raw text is captured and stored without a model (the LLM-free
    # ingest graph: passthrough / document-read / structural link / local embed); only LLM
    # entity/insight extraction is skipped, which lands the item 'partial' recording
    # "insights: model unavailable". That stamp IS the heuristic tier's queue marker
    # (KNOW-R17), so it is both the backlog and what the drain re-extracts in place.
    register_contract(
        DegradedContract(
            surface="knowledge_ingest",
            label="Knowledge insights",
            use_cases=("chat",),
            floor="Documents are still captured, indexed and embedded locally without a model "
            "through the LLM-free ingest graph; entity and insight extraction is skipped and the "
            "item is marked partial ('insights: model unavailable') until a model returns, when "
            "it is re-extracted in place.",
            backlog_probe=_knowledge_heuristic_backlog,
            drain=_knowledge_heuristic_drain,
        )
    )
    # synthesis watchers — `mode: append_evidence` is the floor and needs no model: the
    # dated evidence entries land as they arrive (persist-raw-first), and only the compiled
    # section above them falls behind. `knowledge.staleness` counts exactly how far behind,
    # and the drain queues one propose-only recompile per stale synthesis.
    register_contract(
        DegradedContract(
            surface="synthesis_watchers",
            label="Knowledge summaries",
            use_cases=("chat",),
            floor="Watchers keep appending dated evidence entries without a model "
            "(append_evidence persists raw first); only the compiled summary above them stops "
            "being rewritten, and each synthesis says how many new sources it has not caught up "
            "with. A recompile is proposed, never applied in place.",
            backlog_probe=_synthesis_stale_backlog,
            drain=_synthesis_evidence_drain,
        )
    )
    # scheduled research reports — with no model a run cannot write its finding,
    # and that is a DEFERRAL rather than a loss: the failure is recorded without advancing the
    # run stamp or the watermark, so the next window sees exactly the same new material again.
    register_contract(
        DegradedContract(
            surface="research_report",
            label="Research reports",
            use_cases=("background",),
            floor="A scheduled report's run is deferred, not dropped: the failure is recorded "
            "without advancing its last-run stamp or its watermark, so the next window retries "
            "over the same new material. Definitions, schedules and manual runs stay available.",
        )
    )
    # morning source digest — a DIFFERENT shape from research_report, which is why it
    # gets its own surface rather than borrowing that one: nothing is deferred here. The items
    # are already durable in the library before the narrative is attempted, so the honest floor
    # is a digest that still arrives and says the synthesis was missing. Re-running would
    # re-summarise items the user already has, so the cursor advances either way.
    register_contract(
        DegradedContract(
            surface="source_digest",
            label="Morning digest",
            use_cases=("background",),
            floor="The morning digest still arrives without a model: its body says synthesis was "
            "unavailable and points at the collected items, which are already in the library. "
            "Collection, dedup, the rule-grammar filter and the cursor all keep working — only "
            "the narrative prose is missing.",
        )
    )
    # triage digest — its OWN surface rather than borrowing `source_digest` or the
    # reasoning axis, on the same reasoning that earned the digest one: nothing is deferred
    # and nothing is unreasoned-but-answered. The manifest is collected WITHOUT a model, so the
    # gate's `drop` decisions and the item list survive; what a missing model costs is the
    # tiered proposals. Conflating it with `source_digest` would make one contract answer for
    # two different user-facing promises (a knowledge digest and an attention digest).
    register_contract(
        DegradedContract(
            surface="triage_digest",
            label="Triage digest",
            use_cases=("background",),
            floor="The triage digest still arrives without a model: collection, dedup and the "
            "rule-grammar filter keep working and the items are listed with the gate applied, "
            "but no proposals are attached and the body says so. Nothing is deferred and the "
            "window is not re-run, because the items were never at risk.",
        )
    )
    # search ranking — hybrid retrieval degrades vector-arm-off to FTS + graph +
    # recency automatically; the real backlog is items missing an embedding.
    register_contract(
        DegradedContract(
            surface="search_ranking",
            label="Semantic search",
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
            label="Transcription",
            use_cases=("stt",),
            floor="Speech-to-text is simply off without a model — its pipeline nodes are skipped, "
            "not errored. Bind an STT model to turn it on.",
        )
    )
    # browse — its own surface rather than `assistant_reasoning`, because the no-model shape
    # is categorically different: every STEP of the loop is a model decision, so there is no
    # reduced tier to fall back to. The run stops with `ok=False` and an error naming the
    # decision call (`loop.py:479`), and the notes, visited and blocked URLs collected so far
    # are PRESERVED on that result. That distinction is the whole floor: a browse run without
    # a model is not a park (`parked` stays False — a park means "stopped early, notes kept,
    # a human decides"), and it never guesses a navigation to keep moving.
    register_contract(
        DegradedContract(
            surface="browse",
            label="Browse automation",
            use_cases=("reasoning",),
            floor="Browse automation is unavailable without a model — every step of the loop is a "
            "model decision, so there is no reduced tier. A run that loses its model stops and "
            "says so, keeping the notes and the pages it already visited; it never guesses a "
            "navigation to keep going.",
        )
    )
    # the catch-all reasoning axis behind one_shot_completion — the background/reasoning
    # label collapses to the chat axis, so a surface with no dedicated contract that
    # calls one_shot_completion is covered here (the default 'skip + show degraded').
    register_contract(
        DegradedContract(
            surface="assistant_reasoning",
            label="Background tasks",
            use_cases=("chat",),
            floor="Background reasoning tasks (cron NL parsing, chat retag, loop summaries, "
            "web-extract) pause without a model and resume when one returns.",
        )
    )


_register_builtin_contracts()
