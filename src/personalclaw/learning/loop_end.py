"""The loop-end learner — a terminal loop run mines its own ledger for lessons (PP-5).

The loop-engine sibling of :mod:`personalclaw.learning.run_end`. Before PP-5 the flywheel's §3.2
producers ran only over workflow runs, because a loop's findings/verdicts lived in a file store with
no event vocabulary — `learning.mining` derives from *"the run's own journal"*, and a loop had none.
Now that loop cycles emit `step_started`/`step_completed`/`judge_verdict`/`breaker_trip`/
`watcher_reaped` to the ledger, a loop run is mineable through exactly the reader a workflow run is.

This module supplies the two adapters `learning.mining` needs to treat a loop as a "run":

* :class:`_LoopRunView` — a :class:`~personalclaw.loop.loop.Loop` presented in the run-shape mining
  reads (`id`/`intent`/`workflow_name`/`status`/`project_id`/`origin`).
* :class:`_LoopRunStore` — a `list_runs(workflow_name=, limit=)` over the loop store, so
  `positive_path_candidates` clusters recurring loop paths.

Mirrors run_end's two rules: inert similarity/index unless a live vector store is injected, and
PROPOSE-never-install (every draft clears `learning.proposals.enqueue`, the shared human gate).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

#: How many mined positive-path traces one terminal loop may file — the same bound run_end uses, and
#: for the same reason: the scan is over recent runs, not this one, so an uncapped hook re-files the
#: standing set every completion.
_MAX_TRACE_DRAFTS = 2


@dataclass
class _LoopRunView:
    """A loop presented as a mining "run". Only the attributes `learning.mining` reads."""

    id: str
    intent: str
    workflow_name: str
    status: str
    project_id: str = ""
    created_at: str = ""
    origin: Any = field(default_factory=lambda: SimpleNamespace(session_key=""))

    @classmethod
    def of(cls, loop: Any) -> "_LoopRunView":
        created = ""
        raw = float(getattr(loop, "created_at", 0.0) or 0.0)
        if raw:
            created = datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()
        return cls(
            id=str(getattr(loop, "id", "") or ""),
            # The user's free-text goal is the loop's intent — the "asked" half of intent inversion.
            intent=str(getattr(loop, "task", "") or "").strip(),
            # Scoped by kind, so `positive_path_candidates` clusters loops of one kind together and
            # never merges a loop trace with a workflow of the same name (a separate store anyway).
            workflow_name=f"loop:{getattr(loop, 'kind', '') or 'loop'}",
            status=str(getattr(loop, "status", "") or ""),
            project_id=str(getattr(loop, "project_id", "") or ""),
            created_at=created,
            origin=SimpleNamespace(session_key=str(getattr(loop, "session_key", "") or "")),
        )


class _LoopRunStore:
    """The `list_runs` surface `positive_path_candidates` calls, backed by the loop store."""

    def list_runs(
        self, *, workflow_name: str = "", limit: int = 60
    ) -> tuple[list[_LoopRunView], int]:
        from personalclaw.loop import store as loop_store

        views = [_LoopRunView.of(loop) for loop in loop_store.list_all()]
        if workflow_name:
            views = [v for v in views if v.workflow_name == workflow_name]
        views = views[: max(1, limit)]
        return views, len(views)


def capture(loop: Any, service: Any, *, journal: Any = None, store: Any = None) -> dict[str, int]:
    """Mine a terminal loop's ledger into proposals. Returns a report ``{mined, proposed}``.

    Best-effort throughout — called from the watchdog's `_complete`, which must not fail over a
    mined draft. `positive_path_candidates` + intent inversion run with no service; similarity/index
    run only when a live vector store is injected (mirrors run_end).
    """
    report = {"mined": 0, "proposed": 0}
    from personalclaw.loop import journal as loop_journal
    from personalclaw.loop import store as loop_store

    journal = journal or loop_journal
    store = store or _LoopRunStore()
    view = _LoopRunView.of(loop)

    # Make sure the final cycle is on the ledger before mining reads it back.
    try:
        loop_store.record_cycle_findings(view.id)
    except Exception:
        logger.debug("loop-end: final ingest failed for %s", view.id, exc_info=True)

    try:
        from personalclaw.learning import mining
    except Exception:
        logger.debug("loop-end: mining unavailable", exc_info=True)
        return report

    # Intent inversion — what the loop was asked vs what its cycles actually did. Logged, and (via
    # spec_text) it is what makes the loop's synthesized intent embeddable for the similarity pass.
    try:
        inversion = mining.invert_intent(view, journal=journal)
        if inversion.inverted:
            logger.info(
                "loop %s: intent inversion — drift %.2f, unaddressed %s",
                inversion.run_id,
                inversion.drift,
                ", ".join(inversion.unaddressed[:5]) or "(none)",
            )
    except Exception:
        logger.debug("loop-end: intent inversion failed", exc_info=True)

    # Positive-path traces — recurring successful loop paths, filed as TEMPLATE proposals. This is
    # the no-service path that closes the loop blind spot: a loop kind whose cycles took the same
    # named sequence across runs is a procedure worth naming.
    try:
        traces, _miss = mining.positive_path_candidates(
            workflow_name=view.workflow_name, journal=journal, store=store
        )
        for trace in traces[:_MAX_TRACE_DRAFTS]:
            if mining.file_positive_trace(trace, session_key=view.origin.session_key):
                report["proposed"] += 1
    except Exception:
        logger.debug("loop-end: positive-path mining failed", exc_info=True)

    # Similarity — "you have built this N times", inert without a live vector store.
    if service is not None and getattr(service, "has_vector", False):
        try:
            from personalclaw.learning import run_end
            from personalclaw.learning.detectors import Action, similarity_verdict

            mining.index_run_spec(view, service, journal=journal)
            found = mining.similar_run_matches(view, service, journal=journal)
            if not found.blind:
                verdict = similarity_verdict(matches=found.matches, now=time.time())
                if verdict.action == Action.AUTO_FILE.value and run_end._file_similarity_draft(
                    view, found, verdict
                ):
                    report["proposed"] += 1
        except Exception:
            logger.debug("loop-end: similarity pass failed", exc_info=True)

    report["mined"] = report["proposed"]
    return report
