"""An intent's check of your saved items, run in the background: one job per intent, in memory.

A job walks the same candidates (:func:`candidate_ids`) through the same matcher as ``POST
/api/knowledge/intents/{id}/run``, and records each match as it is answered. So a stop keeps what
the job found, and a restart loses only the progress numbers: what a job kept is already in
``intent_outcomes``. After a restart there is no current job, and a new start works.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from personalclaw.cancellation import cancel_and_wait

logger = logging.getLogger(__name__)

STATUS_RUNNING, STATUS_DONE, STATUS_STOPPED, STATUS_FAILED = "running", "done", "stopped", "failed"

#: Items matched at once. The matcher is a model call per item, so a run over a large library
#: would otherwise wait on each call before it makes the next.
MATCH_AT_ONCE = 6


def candidate_ids(store: Any, enabled_for: Sequence[str]) -> list[str]:
    """The items a run of an intent enabled for *enabled_for* checks: every active, unarchived
    knowledge item of those types, or of every type when it names none. Oldest first."""
    sql = "SELECT id FROM items WHERE status = 'active' AND COALESCE(is_archived, 0) = 0"
    kinds = [str(k) for k in enabled_for if str(k)]
    if kinds:
        sql += f" AND item_type IN ({', '.join('?' for _ in kinds)})"
    sql += " ORDER BY created_at, id"
    return [str(r[0]) for r in store.db.execute(sql, kinds)]


@dataclass
class IntentRun:
    """One job: how far it got, and what it kept."""

    intent_id: str
    total: int
    status: str = STATUS_RUNNING
    done: int = 0
    kept: int = 0
    errors: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]

    def answer(self) -> dict[str, Any]:
        return {"status": self.status, "done": self.done, "total": self.total, "kept": self.kept}


@dataclass
class Matched:
    """What :func:`match_and_record` did with one item."""

    matched: bool = False
    new: bool = False
    error: bool = False


async def match_and_record(
    store: Any,
    intent: Any,
    item: dict,
    *,
    pool: Any,
    text_of: Callable[[dict], str],
    prior: set[str],
) -> Matched:
    """Match *item* against *intent* and record a match as the intent's finding. A model failure
    is an error, not a miss (``raise_on_error``), so a run on a cold pool cannot read as "nothing
    matched"."""
    from personalclaw.knowledge.intents import match_intent

    try:
        match = await match_intent(intent, text_of(item), pool=pool, raise_on_error=True)
    except Exception:  # noqa: BLE001 — counted, and the run goes on to the next item
        logger.debug("intent %s: item %s could not be matched", intent.id, item.get("id"))
        return Matched(error=True)
    if match is None:
        return Matched()
    store.record_intent_outcome(
        intent.id,
        intent_name=intent.goal,
        item_id=item["id"],
        item_title=item.get("title") or item.get("ai_title") or "",
        takeaway=match.takeaway,
        fields=match.fields,
    )
    return Matched(matched=True, new=item["id"] not in prior)


class IntentRuns:
    """The jobs, by intent: at most one running per intent. A finished job stays, so its answer
    can still be read, until the next start replaces it."""

    def __init__(self) -> None:
        self._runs: dict[str, IntentRun] = {}

    def current(self, intent_id: str) -> IntentRun | None:
        return self._runs.get(intent_id)

    def running(self, intent_id: str) -> bool:
        run = self._runs.get(intent_id)
        return run is not None and run.status == STATUS_RUNNING

    def start(
        self,
        store: Any,
        intent: Any,
        *,
        pool: Any,
        text_of: Callable[[dict], str],
    ) -> IntentRun:
        """Start checking every candidate for *intent* (the caller has refused a second start and a
        paused intent). Answers the job, already counting."""
        ids = candidate_ids(store, intent.enabled_for)
        run = IntentRun(intent_id=intent.id, total=len(ids))
        self._runs[intent.id] = run
        run.task = asyncio.ensure_future(self._walk(run, store, intent, ids, pool, text_of))
        return run

    async def stop(self, intent_id: str) -> IntentRun | None:
        """Stop *intent_id*'s job, keeping what it found. Answers the job, or None when there is
        none; a job that already ended is answered as it ended."""
        run = self._runs.get(intent_id)
        if run is None or run.status != STATUS_RUNNING or run.task is None:
            return run
        await cancel_and_wait([run.task], what=f"intent {intent_id}'s check of saved items")
        run.status = STATUS_STOPPED
        return run

    def forget(self, intent_id: str) -> None:
        self._runs.pop(intent_id, None)

    async def stop_all(self) -> None:
        """Stop every running job: the gateway is closing."""
        for intent_id in list(self._runs):
            await self.stop(intent_id)

    async def _walk(
        self,
        run: IntentRun,
        store: Any,
        intent: Any,
        ids: Sequence[str],
        pool: Any,
        text_of: Callable[[dict], str],
    ) -> None:
        prior = {o["item_id"] for o in store.outcomes_for_intent(intent.id) if o.get("item_id")}
        gate = asyncio.Semaphore(MATCH_AT_ONCE)

        async def _one(item_id: str) -> None:
            async with gate:
                item = store.get_item(item_id)
                if item:
                    did = await match_and_record(
                        store, intent, item, pool=pool, text_of=text_of, prior=prior
                    )
                    run.kept += int(did.matched)
                    run.errors += int(did.error)
                run.done += 1

        try:
            await asyncio.gather(*(_one(i) for i in ids))
        except asyncio.CancelledError:
            run.status = STATUS_STOPPED
            raise
        except Exception:  # noqa: BLE001 — the job's answer says it failed
            logger.warning("intent %s: the run failed", intent.id, exc_info=True)
            run.status = STATUS_FAILED
            return
        run.status = STATUS_DONE
