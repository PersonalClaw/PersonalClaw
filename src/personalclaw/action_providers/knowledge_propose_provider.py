"""``knowledge-propose`` — file a knowledge draft into the human review queue.

The write half of the maintenance tier is `knowledge-persist`; this is the PROPOSE half.
A gap-healing draft or a `schema.md` convention edit (KNOWLEDGE-SYNTHESIS §3.3/§3.4) goes
through `knowledge.updates.queue_draft` under `Kind.KNOWLEDGE_DRAFT` and waits for a human,
because the alternative was measured and is worse: a drafted entry nobody reviewed becomes
a citable source for the next draft, and two generations later the store cites itself as
evidence.

This provider parses one call shape into drafts and reports the outcome; it does NOT talk to
the queue itself. `knowledge.updates` is the single enqueue site for knowledge (WF2KNO-11),
so the kind, the body clamp and the meaning of a SKIP are decided once for both a drafted
entry and an update to an existing one.

Before this provider existed, NOTHING reachable from a workflow could reach the proposal
queue at all — `enqueue`'s only callers were Python-side, so a template that wanted to
propose had exactly one option available to it: write. That is why gap-healing shipped
persisting a TTL'd probe tagged `proposal` into the knowledge store. It looked like a
proposal in the store and reached no review gate, and the probe expired 30 days later
whether or not anyone had ever seen it.

**A SKIP is a SUCCESS.** `enqueue` returns `Verdict.SKIP` when a prior decision forbids
re-filing (already accepted, still cooling down after a rejection) or when an inferred
draft is below the evidence floor. Its docstring is explicit that the caller must treat
that as success — *not nagging is the feature*. A provider that failed the node on SKIP
would make a healthy cadence look broken every single time it correctly said nothing, and
the fix a user would reach for is turning the template off.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.knowledge import updates

logger = logging.getLogger(__name__)

#: Ceiling on drafts filed per call. The queue's own `DEFAULT_QUOTA_PER_RUN` bounds a
#: learning pass; this bounds one action node, for the same reason: a pass that files
#: twenty proposals is not being thorough, it is being unreadable.
MAX_DRAFTS_PER_CALL = 10


class KnowledgeProposeActionProvider(ActionProvider):
    """Route knowledge drafts to the LEARNING-FLYWHEEL proposal queue.

    ``action_config`` shape — one draft::

        {
            "title": "Retrieval cascade",
            "body": "…",
            "target": "retrieval-cascade",      # optional: what the draft is ABOUT
            "evidence": "…quoted excerpts…",    # optional: fenced as source_excerpt
            "occurrences": 5,                   # optional: how much evidence backs it
            "source_cadence": "gap-healing",    # optional: which pass produced it
            "tags": ["gap-healing"]
        }

    or a batch, which is the shape a `stage` node's schema produces::

        {
            "drafts": [
                {"entity": "…", "title": "…", "body": "…", "sufficient_evidence": true}
            ],
            "evidence": {"…entity…": ["[id] excerpt", …]},
            "source_cadence": "gap-healing"
        }

    `drafts` is also accepted as a JSON string: a template reference renders through the
    same string path a prompt does, so requiring a live list would have made the batch form
    fail on the one call shape it exists for.
    """

    @property
    def name(self) -> str:
        return "knowledge-propose"

    @property
    def display_name(self) -> str:
        return "Propose Knowledge Draft"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        drafts = _drafts(cfg)
        if not drafts:
            return ActionResult(
                success=False,
                error=(
                    "knowledge-propose has nothing to file — supply `drafts`, or a `title` "
                    "and `body` for a single draft"
                ),
            )

        run_id = str((ctx.payload or {}).get("run_id", "") or "")
        cadence = str(cfg.get("source_cadence", "") or "knowledge-synthesis")
        provenance = str(cfg.get("provenance", "") or "inferred")
        tags = [str(t) for t in (cfg.get("tags") or []) if str(t).strip()]
        evidence = _maybe_json(cfg.get("evidence"))

        filed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for draft in drafts[:MAX_DRAFTS_PER_CALL]:
            title = str(draft.get("title", "") or draft.get("entity", "") or "").strip()
            body = str(draft.get("body", "") or draft.get("content", "") or "").strip()
            if not title or not body:
                skipped.append({"title": title, "reason": "draft has no title or no body"})
                continue
            # An ungrounded draft is the one thing this path must not file. The template's
            # own gate asks the model to admit it; honoring the admission is what makes the
            # admission worth asking for.
            if "sufficient_evidence" in draft and not _truthy(draft.get("sufficient_evidence")):
                skipped.append({"title": title, "reason": "draft reported insufficient evidence"})
                continue

            verdict, pid, skip_reason = updates.queue_draft(
                title=title,
                body=body,
                target=str(draft.get("target", "") or cfg.get("target", "") or ""),
                provenance=provenance,
                source_cadence=cadence,
                run_id=run_id,
                source_excerpt=_excerpt(draft, evidence),
                occurrences=_int(draft.get("mentions", cfg.get("occurrences")), 0),
                tags=tags,
            )
            row = {"title": title, "verdict": verdict, "id": pid}
            # A SKIP is a successful outcome with an explanation, so it is reported beside
            # the filed rows rather than as an error. The phrase comes from the updater
            # because the updater owns the enqueue — one queue, one explanation of a SKIP.
            if skip_reason:
                row["reason"] = skip_reason
                skipped.append(row)
            else:
                filed.append(row)

        payload = {
            "filed": filed,
            "skipped": skipped,
            "counts": {
                "filed": len(filed),
                "skipped": len(skipped),
                "considered": len(drafts),
            },
            "kind": updates.DRAFT_KIND,
            "note": (
                "Filed as PROPOSALS awaiting review, not written to the knowledge store. "
                "A skipped draft is a success: a prior decision already covers it, or it is "
                "below the evidence floor."
            ),
        }
        return ActionResult(
            success=True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _drafts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The drafts to file, from either call shape.

    Tolerates the JSON-string form because that is what a `{{nodes.x.output.drafts}}`
    reference resolves to when the renderer walks it through a string.
    """
    raw = cfg.get("drafts")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("knowledge-propose: drafts is a string but not JSON", exc_info=True)
            raw = None
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)]
    if cfg.get("title") or cfg.get("body"):
        return [dict(cfg)]
    return []


def _maybe_json(raw: Any) -> Any:
    """A `{{… | json}}` reference arrives as a STRING; parse it back if it is one.

    The same reason `_drafts` tolerates it: the excerpt map is keyed by entity, and a raw
    JSON string would make every per-entity lookup miss and file every draft unsourced.
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text or text[0] not in "[{":
        return raw
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return raw


def _excerpt(draft: dict[str, Any], evidence: Any) -> str:
    """The quoted source material behind one draft, as plain text.

    `enqueue` fences whatever it is given, so this only has to find it. Per-entity first
    (the batch form keys excerpts by entity), then the draft's own field, then a shared
    blob — a draft filed with no excerpt is a proposal a reviewer cannot check.
    """
    own = draft.get("evidence") or draft.get("source_excerpt")
    if own:
        return _flatten(own)
    if isinstance(evidence, dict):
        key = str(draft.get("entity", "") or draft.get("title", "") or "")
        if key in evidence:
            return _flatten(evidence[key])
        return ""
    return _flatten(evidence) if evidence else ""


def _flatten(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return "\n".join(str(x) for x in raw)
    if raw is None:
        return ""
    return json.dumps(raw, ensure_ascii=False)


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return fallback
    return int(raw)
