"""The scheduled-research-report runner (WF2KNO-12).

One fire of this provider is one report run: resolve the report's source scope, write ONE
finding about what arrived, and stamp the report so the next run starts where this one
stopped. The report definition itself (schedule, scope, policy, watermark) lives in
`personalclaw.knowledge.research_reports`; this module only RUNS one.

Three decisions in here are load-bearing enough to state up front, because each one has an
obvious-looking alternative that is silently wrong:

**The watermark is taken at SCOPE-RESOLUTION time, never at completion time.** The run reads
`resolution_ts = time.time()` immediately before it asks the store what is new, and stamps
exactly that value on success. Stamping "now" at the END instead would cover the whole window
the model spent writing: any item captured while the model was mid-sentence would fall before
the new watermark without ever having been in a scope, and would therefore be skipped
*forever*. Nothing would error, no item would be missing from the store, and the report would
simply never mention it — which is why this is a comment and a test rather than a convention.

**A report never reads its own output.** Items of `research_reports.FINDING_KIND` are excluded
from every scope. A report writes a finding, the finding is a knowledge item, and a knowledge
item newer than the watermark is in the next scope — so without the exclusion the second run
summarizes the first run's summary and the report degenerates into infinite regress with a
model bill attached.

**An empty scope is a terminal SUCCESS, not a no-op failure and not a skipped run.** Nothing
new arrived is the normal, common outcome of a frequent schedule. It spends zero tokens, writes
no item, and still advances the watermark — because the window really was examined.

`action_config` shape::

    {
        "report_id": "weekly-perf",   # required; the report to run
        "dry_run": false              # optional; resolve the scope and return a preview,
                                      # spending no tokens, writing nothing, stamping nothing
    }
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Sequence

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

# Through the persist provider's own opener, never a locally composed path: its docstring
# records a measured incident where re-deriving `<home>/knowledge/knowledge.db` here read a
# DIFFERENT database than the dashboard's `<home>/workspace/knowledge/knowledge.db`, with no
# error on either side. One opener, one database.
from personalclaw.action_providers.knowledge_persist_provider import _open_store

logger = logging.getLogger(__name__)

#: How many source items a single run will show the model. A scope is unbounded in principle
#: (a quiet report resumed after a month can match thousands), and an unbounded prompt is the
#: failure mode that turns one late run into a context-limit error. The NEWEST items survive
#: the cut, because a report is about what changed most recently.
MAX_SCOPE_ITEMS = 40

#: The model's own request for another pass. A sentinel rather than a JSON envelope because
#: the reply is prose the finding is written FROM, and a wrapper the model forgets to close
#: would lose the whole finding.
CONTINUE_TOKEN = "CONTINUE"


def _hold_claim(report_id: str) -> bool:
    """Take the single-flight lease for this report's run. False when someone else holds it.

    The manual-run route refuses with a 409 while this claim is held, which is the whole of
    the atom's "a manual run is idempotent against an in-flight scheduled fire" clause — and
    that refusal is inert unless a run WRITES the claim. It was: the route read a key nothing
    ever set. `Claim.max_duration_secs` is the self-expiry, so a process killed mid-run cannot
    wedge the report forever.
    """
    from personalclaw.knowledge.research_reports import report_claim_id
    from personalclaw.triggers import claims as _claims
    from personalclaw.triggers.scheduling import Claim

    claim_id = report_claim_id(report_id)
    if _claims.is_running(claim_id):
        return False
    _claims.write_claim(
        Claim(trigger_id=claim_id, holder="knowledge-report", claimed_at=time.time())
    )
    return True


def _release_claim(report_id: str) -> None:
    """Release the lease. Best-effort: a failed release self-expires with the claim."""
    from personalclaw.knowledge.research_reports import report_claim_id
    from personalclaw.triggers import claims as _claims

    try:
        _claims.release_claim(report_claim_id(report_id))
    except Exception:  # pragma: no cover - a release failure self-heals via max_duration
        logger.debug("knowledge-report: claim release failed", exc_info=True)


class KnowledgeReportActionProvider(ActionProvider):
    """Run one scheduled research report: resolve its scope, write one finding.

    ``action_config`` shape::

        {
            "report_id": "weekly-perf",   # required
            "dry_run": false              # optional; preview the resolved scope only
        }
    """

    @property
    def name(self) -> str:
        return "knowledge-report"

    @property
    def display_name(self) -> str:
        return "Run Research Report"

    @property
    def supports_dry_run(self) -> bool:
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        """Run one report under the single-flight lease.

        The lease is the atom's "a manual run is idempotent against an in-flight scheduled
        fire" clause. The refusal lives in the manual-run route, which reads
        ``research-report:<id>`` — and a route that reads a key nothing ever writes is a 409
        that can never fire, so the WRITE belongs here, around every exit path.
        """
        report_id = str((action_config or {}).get("report_id", "") or "").strip()
        if not report_id:
            return ActionResult(
                success=False,
                error="knowledge-report is missing 'report_id' — name the report to run",
            )
        if not _hold_claim(report_id):
            # Not a failure: the other run is doing this work. Success with a named skip is
            # what makes a duplicate manual fire harmless.
            return ActionResult(
                success=True,
                stdout=json.dumps({"report_id": report_id, "skipped": "already_running"}),
            )
        try:
            return await self._execute_locked(action_config, ctx, timeout)
        finally:
            _release_claim(report_id)

    async def _execute_locked(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}
        report_id = str(cfg.get("report_id", "") or "").strip()
        if not report_id:
            return ActionResult(
                success=False,
                error="knowledge-report is missing 'report_id' — name the report to run",
            )

        rr = _reports_module()
        defn = rr.get_report(report_id)
        if defn is None:
            # No `record_run` here on purpose: there is no report row to stamp, so recording a
            # failure would have to invent one. The caller's own error is the record.
            return ActionResult(
                success=False,
                error=f"knowledge-report: no report definition {report_id!r}",
            )
        if not getattr(defn, "enabled", True):
            return ActionResult(
                success=True,
                stdout=json.dumps({"report_id": report_id, "skipped": "disabled"}),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # THE PRE-FLIGHT — `is_due`'s only production caller (WF2KNO-12's remainder).
        #
        # The clock trigger decides when this runner is INVOKED; `is_due` decides whether this
        # invocation is the report's window, and it is the only place the four hardening rules
        # live: an unparseable expression fails CLOSED, a never-run report anchors on
        # `created_ts` (and refuses to anchor at all without one, rather than on the epoch),
        # fifty skipped windows fire ONCE, and a failed run advances neither the stamp nor the
        # watermark. None of that is derivable from a cron expression, so the trigger is
        # allowed to be more eager than the report and this absorbs the difference.
        #
        # A MANUAL run skips it: the user clicking "Run now" is the authority for that fire,
        # and refusing it as "not due" would make the button lie. The flag comes from the
        # config the manual route builds, so the scheduled path cannot set it by accident.
        manual = bool(cfg.get("manual"))
        if not manual:
            due, why = rr.is_due(defn, now=time.time())
            if not due:
                # A named skip, not a failure: nothing went wrong, and `record_run` is
                # deliberately NOT called — stamping a run that did not happen would advance
                # the watermark past material this fire never read.
                return ActionResult(
                    success=True,
                    stdout=json.dumps(
                        {"report_id": report_id, "skipped": "not_due", "reason": why}
                    ),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        # THE watermark. Read before the scope is resolved (see the module docstring) and
        # threaded unchanged to `record_run` — never re-read at the end.
        resolution_ts = time.time()

        try:
            store = _open_store()
            finding_kind = str(rr.FINDING_KIND)
            source_items = _resolve_scope(
                store,
                getattr(defn, "source", None),
                cutoff_ts=_source_cutoff(defn, now=resolution_ts),
                exclude_kind=finding_kind,
            )
            context_items = _resolve_scope(
                store,
                getattr(defn, "context", None),
                # The context scope is BACKGROUND, so it is not watermark-filtered: it exists to
                # be cited against, and a watermark would empty it on the second run, which is
                # exactly when the background matters most. Only an explicit window narrows it.
                cutoff_ts=_context_cutoff(defn, now=resolution_ts),
                exclude_kind=finding_kind,
            )
        except Exception as exc:  # noqa: BLE001 — a store failure is a failed run, not a crash
            logger.debug("knowledge-report %s: scope resolution failed", report_id, exc_info=True)
            rr.record_run(report_id, ok=False, error=f"scope resolution failed: {exc}")
            return ActionResult(
                success=False,
                error=f"knowledge-report: could not resolve the scope for {report_id}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if cfg.get("dry_run"):
            # Stamps nothing: a preview that advanced the watermark would make the NEXT real run
            # skip the very items it just previewed.
            return ActionResult(
                success=True,
                stdout=json.dumps(
                    {
                        "report_id": report_id,
                        "dry_run": True,
                        "source_items": [str(i.get("id") or "") for i in source_items],
                        "context_items": [str(i.get("id") or "") for i in context_items],
                        "resolution_ts": resolution_ts,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if not source_items:
            # Terminal success. No model call, no item, and the watermark still advances — the
            # window WAS examined, and re-examining it next run would re-pay for the same
            # nothing. Asserted by counting model calls, not by the absence of an item.
            rr.record_run(report_id, ok=True, watermark_ts=resolution_ts)
            return ActionResult(
                success=True,
                stdout=json.dumps(
                    {
                        "report_id": report_id,
                        "source_items": 0,
                        "note": (
                            "nothing new arrived in this report's source scope — no finding "
                            "written, watermark advanced"
                        ),
                        "watermark_ts": resolution_ts,
                        "model_calls": 0,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        try:
            refs = _numbered_refs(rr, defn, source_items, context_items)
            text, calls = await _write_finding(defn, refs)
            if not text.strip():
                rr.record_run(report_id, ok=False, error="the model returned an empty finding")
                return ActionResult(
                    success=False,
                    error=f"knowledge-report: {report_id} produced an empty finding",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            persist_cfg = _persist_config(rr, defn, text=text, refs=refs)
            result = await _persist(persist_cfg, ctx, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — error-as-return; the run is recorded either way
            logger.debug("knowledge-report %s: run failed", report_id, exc_info=True)
            rr.record_run(report_id, ok=False, error=str(exc)[:200])
            return ActionResult(
                success=False,
                error=f"knowledge-report: {report_id} failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if not result.success:
            # A REFUSED persist is a failed run, and the run stamp must not advance: the items
            # this run read are still unreported, so the next run has to see them again.
            rr.record_run(report_id, ok=False, error=result.error[:200])
            return ActionResult(
                success=False,
                error=f"knowledge-report: {report_id} could not persist its finding: "
                f"{result.error}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        rr.record_run(report_id, ok=True, watermark_ts=resolution_ts)
        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "report_id": report_id,
                    "source_items": len(source_items),
                    "context_items": len(context_items),
                    "registered_sources": len(refs),
                    "citation_policy": str(getattr(defn, "citation_policy", "")),
                    "model_calls": calls,
                    "watermark_ts": resolution_ts,
                    "persist": _persist_body(result),
                }
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


# ── the report module seam ──


def _reports_module() -> Any:
    """The report registry, imported at CALL time.

    Deferred rather than module-level so this provider can be imported (and registered) in a
    tree where the registry module is still landing, and so a test can substitute a fake
    registry by patching ONE name.

    The `import a.b.c as rr` form is deliberate and not a style slip: while the module is still
    landing, `from personalclaw.knowledge import research_reports` reds mypy with
    `attr-defined`, whereas a plain module import is covered by the project's
    `ignore_missing_imports`. It needs no `type: ignore` to delete later.
    """
    import personalclaw.knowledge.research_reports as rr

    return rr


# ── scope resolution ──


def _source_cutoff(defn: Any, *, now: float) -> float:
    """The source scope's lower bound: an explicit window if one is set, else the watermark.

    `window_secs > 0` deliberately OVERRIDES the watermark rather than intersecting with it —
    "the last 24 hours" is a statement about what the report is about, not a resumption cursor,
    and intersecting the two would make a rolling-window report silently skip a window it was
    down for.
    """
    scope = getattr(defn, "source", None)
    window = int(getattr(scope, "window_secs", 0) or 0)
    if window > 0:
        return now - window
    return float(getattr(defn, "watermark_ts", 0.0) or 0.0)


def _context_cutoff(defn: Any, *, now: float) -> float:
    scope = getattr(defn, "context", None)
    window = int(getattr(scope, "window_secs", 0) or 0)
    return now - window if window > 0 else 0.0


def _resolve_scope(
    store: Any,
    scope: Any,
    *,
    cutoff_ts: float,
    exclude_kind: str,
) -> list[dict[str, Any]]:
    """Items in *scope*: tagged inside the tag SUBTREE, changed after *cutoff_ts*.

    Returned oldest-first, because the order here becomes the citation numbering and a numbering
    that reshuffles between runs makes two findings disagree about what `[2]` was.
    """
    if scope is None:
        return []
    tags = [str(t) for t in (getattr(scope, "tags", ()) or ())]
    if not tags:
        return []
    tag_ids = _tag_closure(store, tags)
    if not tag_ids:
        return []

    item_ids: set[str] = set()
    for tag_id in tag_ids:
        # The store's own membership accessor. Underscore-prefixed but it IS the store's
        # items-for-a-tag read; hand-writing the `item_tags` join here would be a second
        # reader of that table, which is the duplication this plan removes.
        item_ids.update(str(i) for i in store._items_with_tag(tag_id))

    rows: list[dict[str, Any]] = []
    for item_id in item_ids:
        item = store.get_item(item_id)
        if not item:
            continue
        if str(item.get("kind") or "") == exclude_kind:
            # A report never feeds on its own output — see the module docstring's regress note.
            continue
        if str(item.get("status") or "active") != "active" or item.get("is_archived"):
            continue
        if _epoch(item) <= cutoff_ts:
            continue
        rows.append(item)

    rows.sort(key=lambda r: (_epoch(r), str(r.get("id") or "")))
    # Newest survive the cut, then oldest-first order is restored for numbering.
    return rows[-MAX_SCOPE_ITEMS:]


def _tag_closure(store: Any, roots: Sequence[str]) -> set[int]:
    """Every tag id at or under one of *roots*, matched by name (case-insensitively).

    Tag parentage lives in `tags.parent_id`, so a scope naming a parent has to walk down to its
    children: a report scoped to `perf` that ignored `perf/latency` would silently miss the
    items a user filed most specifically. The visited set also makes a cyclic parent chain
    terminate instead of hanging the run.
    """
    try:
        rows = list(store.list_tags())
    except Exception:  # noqa: BLE001 — an unreadable taxonomy is an empty scope, not a crash
        logger.debug("knowledge-report: tag taxonomy unreadable", exc_info=True)
        return set()

    wanted = {r.strip().lower() for r in roots if r and r.strip()}
    children: dict[int, list[int]] = {}
    frontier: list[int] = []
    for row in rows:
        try:
            tag_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        parent = row.get("parent_id")
        if parent is not None:
            try:
                children.setdefault(int(parent), []).append(tag_id)
            except (TypeError, ValueError):
                pass
        if str(row.get("name") or "").strip().lower() in wanted:
            frontier.append(tag_id)

    seen: set[int] = set()
    stack = list(frontier)
    while stack:
        tag_id = stack.pop()
        if tag_id in seen:
            continue
        seen.add(tag_id)
        stack.extend(children.get(tag_id, ()))
    return seen


def _epoch(item: dict[str, Any]) -> float:
    """An item's change time as an epoch float.

    `updated_at` is stored as an ISO string, and the watermark is a float, so one of them has to
    convert. An unparseable stamp reads as 0.0 — which puts the item BEFORE every cutoff and
    keeps it out of scope, rather than letting a malformed row into every report forever.
    """
    for key in ("updated_at", "created_at"):
        raw = item.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            return datetime.fromisoformat(raw.strip()).timestamp()
        except ValueError:
            continue
    return 0.0


# ── citations ──


def _numbered_refs(
    rr: Any,
    defn: Any,
    source_items: Sequence[dict[str, Any]],
    context_items: Sequence[dict[str, Any]],
) -> tuple[Any, ...]:
    """The numbered sources this run may cite, decided by the report's citation policy.

    ONE numbering across both scopes, source items first: `register_sources` numbers by position,
    so numbering the two scopes separately would mint two `[1]`s that resolve to different items.
    Under `CITE_SOURCE_ONLY` the context items are simply never registered, so a marker pointing
    at one resolves to nothing and is stripped on write — which is the policy actually biting,
    not merely being recorded.
    """
    from personalclaw.knowledge import citations

    items = list(source_items)
    if str(getattr(defn, "citation_policy", "")) == str(rr.ALLOW_CITING_CONTEXT):
        items.extend(context_items)
    return citations.register_sources(items)


# ── the bounded writing loop ──


async def _write_finding(defn: Any, refs: Sequence[Any]) -> tuple[str, int]:
    """Ask the model for the finding, at most `iteration_cap` times.

    The cap is enforced by the loop bound, and the count of calls made is RETURNED so a caller
    (and a test) can see the enforcement rather than trust it: a cap that is read and not
    enforced is indistinguishable from this at the call site, which is why the test counts calls
    instead of asserting the field's value.
    """
    cap = max(1, int(getattr(defn, "iteration_cap", 1) or 1))
    calls = 0
    text = ""
    notes: list[str] = []
    for turn in range(1, cap + 1):
        reply = await _one_shot(_build_prompt(defn, refs, turn=turn, cap=cap, notes=notes))
        calls += 1
        text = str(reply or "")
        if not _wants_another_pass(text):
            break
        notes.append(_strip_continue(text))
        text = _strip_continue(text)
    return _strip_continue(text), calls


def _wants_another_pass(text: str) -> bool:
    lines = [ln.strip() for ln in str(text or "").strip().splitlines() if ln.strip()]
    return bool(lines) and lines[-1] == CONTINUE_TOKEN


def _strip_continue(text: str) -> str:
    lines = str(text or "").rstrip().splitlines()
    while lines and lines[-1].strip() in ("", CONTINUE_TOKEN):
        lines.pop()
    return "\n".join(lines).strip()


def _build_prompt(
    defn: Any,
    refs: Sequence[Any],
    *,
    turn: int,
    cap: int,
    notes: Sequence[str],
) -> str:
    markers = ", ".join(f"[{int(getattr(r, 'marker', 0))}]" for r in refs) or "(none)"
    lines = [
        "You are writing ONE research finding for a scheduled report.",
        f"Report: {getattr(defn, 'name', '') or getattr(defn, 'id', '')}",
        "",
        str(getattr(defn, "prompt", "") or ""),
        "",
        "SOURCES — cite a claim by appending the source's bracketed number:",
    ]
    for ref in refs:
        lines.append(f"[{int(getattr(ref, 'marker', 0))}] {getattr(ref, 'excerpt', '')}")
    lines += [
        "",
        f"The only valid citation markers are {markers}. Do NOT invent a citation marker, and "
        "do not cite a number that is not listed above: an invented marker is dropped when the "
        "finding is stored, so the sentence it was supposed to support silently loses its "
        "provenance.",
        f"This is pass {turn} of at most {cap}.",
        "Reply with the finished finding. Only if you genuinely need another pass, end your "
        f"reply with {CONTINUE_TOKEN} alone on the last line.",
    ]
    if notes:
        lines += ["", "Your notes from earlier passes:", *notes]
    return "\n".join(lines)


async def _one_shot(prompt: str) -> str:
    """One model call. A module-level seam so a test can count calls without a network."""
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case="reasoning")


# ── the write ──


def _persist_config(
    rr: Any,
    defn: Any,
    *,
    text: str,
    refs: Sequence[Any],
) -> dict[str, Any]:
    summary = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return {
        "title": str(getattr(defn, "name", "") or getattr(defn, "id", "") or "Research finding"),
        "content": text,
        "kind": str(rr.FINDING_KIND),
        "summary": summary[:280],
        "tags": [str(t) for t in (getattr(getattr(defn, "source", None), "tags", ()) or ())],
        # The shape `knowledge-persist` already documents: it parses the `[n]` markers out of
        # the prose and derives the stored citations from the ones that resolved.
        "citation_sources": [
            {
                "marker": int(getattr(r, "marker", 0)),
                "item_id": str(getattr(r, "item_id", "") or ""),
                "chunk_index": int(getattr(r, "chunk_index", -1)),
                "excerpt": str(getattr(r, "excerpt", "") or ""),
            }
            for r in refs
        ],
        "mode": "upsert",
        "source_ref": f"research-report:{getattr(defn, 'id', '')}",
    }


async def _persist(
    action_config: dict[str, Any],
    ctx: ActionContext,
    *,
    timeout: int = 30,
) -> ActionResult:
    """Write the finding through the SAME provider every other knowledge write uses.

    Not a direct store write: a second write path would skip the persist check, the citation
    resolution, the conflict edges and the enrichment enqueue — all of which a finding needs
    exactly as much as a hand-written fact does.
    """
    from personalclaw.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    return await KnowledgePersistActionProvider().execute(action_config, ctx, timeout=timeout)


def _persist_body(result: ActionResult) -> dict[str, Any]:
    try:
        body = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}
