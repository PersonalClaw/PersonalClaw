"""``knowledge-relate`` — persist the typed edges a MODEL tier proposed.

`knowledge-persist` writes an item and the edges the FREE deterministic conflict tier could
prove; those are capped at `supersedes`/`contradicts` by construction, because
`_relation_for` derives the verb from the source-precedence ladder and the ladder cannot
observe a structural relation. `derived_from` / `depends_on` / `part_of` are exactly the
relations the model tier proposes (KNOWLEDGE-SYNTHESIS §3.2), and before this provider
existed nothing in production consumed them:
`contradiction.parse_edge_proposals` — the parser built for that answer — had zero importers
outside its own tests, and the `contradiction-review` template interpolated the judge's
verdict into a display string and dropped it. The model was paid for a typed edge on every
run and the store never learned one.

So this is the write-back, and it is a **zero-token** node: the model call already happened
in the `infer` node upstream; this spends nothing and persists what that call produced.

**The model's answer is untrusted input, and it is narrowed at the WRITE end.** A stored
claim can originate from a web page or an inbox message, so a crafted one can try to steer
the judge. Everything reachable from here is bounded before it becomes a row:
`parse_edge_proposals` refuses anything outside the closed five-verb vocabulary, refuses a
self-edge, clamps `confidence` into `[0.1, 0.95]` (never 1.0 — an opinion must not
outrank a proof) and caps the batch at `MAX_EDGES_PER_PASS`; `store.add_item_relation` then
refuses an endpoint that is not a real row, which is what a hallucinated item id is. The
`justification` prose is **reported and never stored** — `item_relations` has no column for
it, and inventing one would put untrusted free text on a page a later prompt reads.

**Nothing proposed is a SUCCESS, not a failure.** An empty `edges` list is the common and
correct answer — most claims contradict nothing. A node that failed on it would make every
healthy conflict-free run look broken, and the fix a user reaches for then is turning the
template off. What the payload does NOT do is let "the judge proposed nothing" hide inside
"the judge's answer was unreadable": those two are separate fields, because an unread answer
means the wire is silently dead again and that is the one failure mode this provider exists
to make visible.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

logger = logging.getLogger(__name__)


class KnowledgeRelateActionProvider(ActionProvider):
    """Persist model-proposed typed item relations. Zero tokens, upsert, error-as-return.

    ``action_config`` shape::

        {
            # The item the edges start FROM — normally the id the persist node returned.
            "source_item": "{{nodes.persist.output.item_id}}",
            # The judge's answer. An object carrying `edges`, exactly as
            # `contradiction.parse_edge_proposals` reads it; a JSON string of the same is
            # accepted because that is what a reference resolves to when the renderer
            # walks it through a string.
            "relations": "{{nodes.judge_conflicts.output}}"
        }
    """

    @property
    def name(self) -> str:
        return "knowledge-relate"

    @property
    def display_name(self) -> str:
        return "Persist Knowledge Relations"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        source_item = str(cfg.get("source_item", "") or "").strip()
        if not source_item:
            # Returned, never raised, like every other knowledge action: the spec is wrong and
            # the run should say which binding to add.
            return ActionResult(
                success=False,
                error=(
                    "knowledge-relate is missing 'source_item' — bind it to the id the "
                    "persist node returned, e.g. {{nodes.persist.output.item_id}}"
                ),
            )
        if "relations" not in cfg:
            return ActionResult(
                success=False,
                error=(
                    "knowledge-relate is missing 'relations' — bind it to the judging node's "
                    "output, e.g. {{nodes.judge_conflicts.output}}"
                ),
            )

        from personalclaw.knowledge import contradiction

        raw = _maybe_json(cfg.get("relations"))
        unread = _unread_reason(raw)
        proposals = contradiction.parse_edge_proposals(raw, source_item=source_item)

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        written: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for edge in proposals:
            try:
                ok = store.add_item_relation(
                    edge.source,
                    edge.target,
                    edge.relation,
                    confidence=edge.confidence,
                    provenance="inferred",
                )
            except Exception:
                # Best-effort per edge, with the refusal REPORTED: an annotation is the cheaper
                # thing to lose than the rest of the batch, but a silently dropped edge is how
                # the two surfaces start disagreeing about what the store knows.
                logger.warning(
                    "could not write inferred %s edge %s -> %s",
                    edge.relation,
                    edge.source,
                    edge.target,
                    exc_info=True,
                )
                refused.append({**_row(edge), "reason": "the store refused the write"})
                continue
            if ok:
                written.append(_row(edge))
            else:
                # The dominant cause, and the one worth naming: the model named a `target` that
                # is not a row. A bare False here would be indistinguishable from a duplicate.
                refused.append(
                    {
                        **_row(edge),
                        "reason": (
                            "the target is not an item in the store, or the edge failed "
                            "validation"
                        ),
                    }
                )

        payload: dict[str, Any] = {
            "source_item": source_item,
            "written": written,
            "refused": refused,
            "counts": {
                "proposed": len(proposals),
                "written": len(written),
                "refused": len(refused),
            },
            "provenance": "inferred",
            "note": (
                "Model-proposed edges, stored with provenance `inferred` and their own "
                "confidence — never 1.0, so an opinion cannot outrank a deterministic proof. "
                "Zero proposed edges is a correct and common answer."
            ),
        }
        if unread:
            # The tell that the wire went dead again, kept OUT of `counts` so it cannot be
            # mistaken for "the judge considered the claims and found nothing".
            payload["unread"] = unread
        return ActionResult(
            success=True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _row(edge: Any) -> dict[str, Any]:
    """One reported edge. `justification` rides the REPORT and never the store.

    `item_relations` has no column for it by design, and the prose is model output derived
    from stored claims — the one place it is safe is a run output a human reads, clamped.
    """
    return {
        "target": edge.target,
        "relation": edge.relation,
        "confidence": round(float(edge.confidence), 4),
        "justification": str(edge.justification or "")[:120],
    }


def _unread_reason(raw: Any) -> str:
    """Why nothing could be read out of the judge's answer, or "" when it was readable.

    `parse_edge_proposals` returns `[]` for a garbled answer AND for an honest "nothing
    conflicts", which are opposite facts: the first means this wire is inert again, the
    second means it worked. Distinguishing them is the whole reason this function exists —
    an inert path that reports the same shape as a working one is exactly how the model-tier
    edge inference stayed unwired through several passes that each looked fine.
    """
    if not isinstance(raw, dict):
        return (
            f"the judging node's answer is a {type(raw).__name__}, not an object carrying "
            "`edges` — nothing could be read out of it"
        )
    if "edges" not in raw:
        return "the judging node's answer carries no `edges` key — nothing could be read out of it"
    if not isinstance(raw.get("edges"), list):
        return "the judging node's `edges` is not a list — nothing could be read out of it"
    return ""


def _maybe_json(raw: Any) -> Any:
    """A reference that rendered through a string arrives as one; parse it back if so.

    Same reason `knowledge-propose` tolerates it: requiring a live object would make the
    provider fail on the one call shape it exists for.
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text or text[0] not in "[{":
        return raw
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        logger.debug("knowledge-relate: relations is a string but not JSON", exc_info=True)
        return raw


def _open_store():
    """Open the ONE global knowledge store, through `knowledge_db_path`.

    Never a locally composed path — `knowledge-persist` records what that costs: a composed
    path wrote to a second database the dashboard could never read, with no error on either
    side.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))
