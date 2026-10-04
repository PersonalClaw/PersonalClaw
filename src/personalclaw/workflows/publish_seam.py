"""The `publish:` seam at dispatch — where `publish.py`'s pure decision meets the artifact store.

`engine.dispatch` hands every settled node's result to `apply_publish`, beside the artifact gate,
so a new node kind inherits publishing rather than silently dropping a declared output.
`publish.py` decides (create, new version or no-op, and the lineage); this module carries the
decision out: the registry write, the media copies the body references (read only from under the
run's own cwd), the run's `publishes.jsonl` the outbox lists, and the consumption question the
dormancy sweep grades.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.workflows.models import Failure, FailureClass, InstanceState, Node

if TYPE_CHECKING:
    from personalclaw.workflows.engine import NodeResult

logger = logging.getLogger(__name__)


def _publish_media_resolver(cwd: str | None) -> Any:
    """A `rewrite_media_refs` resolver reading files under the run's own cwd — and only there.

    Containment is the whole security posture of the copy: `..` traversal and symlinks out of the
    tree are refused, so a published body cannot pull `~/.ssh/id_rsa` into an artifact the dashboard
    serves by writing `![](../../../.ssh/id_rsa)`. Returns None (never raises) for anything it will
    not read, which `rewrite_media_refs` reports as unresolved rather than silently dropping.
    """
    import hashlib
    from pathlib import Path

    from personalclaw.security import is_sensitive_path

    #: Companion copies ride inside the artifact's version dir, which the dashboard serves and the
    #: 50-snapshot window holds. A large binary copied per version would blow both, so the cap is
    #: deliberately far below the artifact body cap.
    max_bytes = 8 * 1024 * 1024

    def _resolve(reference: str) -> tuple[bytes, str] | None:
        if not cwd:
            return None
        try:
            root = Path(cwd).resolve()
            target = (root / reference).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return None
            if is_sensitive_path(str(target)):
                return None
            if target.stat().st_size > max_bytes:
                return None
            data = target.read_bytes()
        except (OSError, ValueError):
            return None
        return data, hashlib.sha256(data).hexdigest()

    return _resolve


async def apply_publish(
    node: Node, result: NodeResult, *, run_id: str = "", cwd: str | None = None
) -> NodeResult:
    """Publish a node's output as an Artifact when it declares `publish:` (WORK-CONTAINERS §2,
    S47).

    At the dispatch seam beside the artifact gate, so a new node kind inherits publishing
    rather than
    silently dropping a declared output.

    A MALFORMED declaration FAILS the node. The alternative — treating it as "no publish" —
    would let
    a node whose author declared a deliverable report success while producing nothing, which is the
    completion-lie class the artifact gate exists to catch. A declaration is a promise about output.

    A REGISTRY failure does not fail the node. The work happened; losing the copy is worth reporting
    on the result, not worth discarding a completed stage over. The distinction is deliberate: a bad
    declaration is the author's bug (fail loudly), a registry outage is the environment's (degrade
    honestly).

    The text a publish writes is the node's output, often what an earlier step read on the web,
    and Knowledge's search keeps an artifact's text and recalls it into prompts. So it is read by
    the content scan before anything is written (``knowledge.artifact_ingest.text_refusal``); what
    the scan refuses, or could not check, is not published, and the result says why in the scan's
    words, as it says why a registry write failed.

    A run started for work that keeps nothing (an Incognito or Temporary chat's) publishes nothing:
    the result says why, and the node's output stays the run's, as every change to the library such
    work asks for is refused (``mcp_artifacts``).
    """
    from personalclaw.workflows.engine import NodeResult
    from personalclaw.workflows.publish import (
        PublishAction,
        flatten_lineage,
        parse_publish,
        rewrite_media_refs,
        upsert_plan,
    )

    cfg = node.config or {}
    if "publish" not in cfg:
        return result
    spec, error = parse_publish(cfg)
    if error:
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"invalid publish declaration: {error}",
                remediation=(
                    "fix the node's `publish:` block; it declares an output nothing produced"
                ),
            ),
        )
    if spec is None or result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result
    # A run an Incognito or Temporary chat started runs under the mode its record inherited
    # (`run_start.run_context`), and work that keeps nothing changes nothing in the library, which
    # outlives it, as the agent's artifact tools and the library's routes hold it.
    from personalclaw import memory_reads

    if why := memory_reads.why_work_keeps_nothing():
        return _with_publish(
            result,
            {"action": "noop", "reason": f"{why}, so nothing was published to your library"},
        )

    content = result.output if isinstance(result.output, str) else ""
    if not content and isinstance(result.output, dict):
        content = str(result.output.get("text") or result.output.get("output") or "")
    if not content.strip():
        # Nothing to publish is NOT an error: a node whose output is structured data the caller
        # binds elsewhere has still done its job. Recording it keeps the absence visible.
        return _with_publish(result, {"action": "noop", "reason": "node output was not text"})

    try:
        from personalclaw.artifacts.registry import get_provider as _artifact_provider

        provider = _artifact_provider()
        if provider is None or provider.readonly:
            # Guarded FIRST rather than mid-flow: the earlier shape reached the writer branches with
            # `provider` still possibly None, which typechecking caught. A publish path that could
            # dereference a missing provider would turn "no artifact store configured" into a
            # traceback on a completed stage.
            return _with_publish(
                result, {"action": "noop", "reason": "no writable artifact provider"}
            )
        # Media self-containment BEFORE the material-change comparison:
        # the rewritten body is what gets stored, so gating on the pre-rewrite text would compare a
        # body the artifact never holds. A first publish would then look unchanged on its second run
        # purely because the reference names differ.
        content, media_copies, media_unresolved = rewrite_media_refs(
            content, _publish_media_resolver(cwd)
        )
        existing = provider.find_similar(spec.artifact)
        previous = None
        if existing is not None:
            detail = provider.get(existing.slug)
            previous = getattr(detail, "content", None) if detail else None
        plan = upsert_plan(
            spec, content, existing_content=previous, run_id=run_id, node_id=node.id or ""
        )
        # The lineage and change note ride on the artifact's own EVENT metadata. Without
        # this the plan computed a full run/node lineage and the artifact landed carrying none of it
        # — provenance computed and discarded, so "which run produced this" had no answer on disk.
        event_meta = {
            "run_id": run_id,
            "node_id": node.id or "",
            "change_note": plan.change_note,
            # Flattened to scalar keys: `clean_event_metadata` bounds event metadata to scalars, so
            # the nested dict was being stringified into an unparseable Python repr.
            **flatten_lineage(plan.lineage),
        }
        if plan.action in (PublishAction.CREATE, PublishAction.VERSION):
            refused = await _text_refusal(spec, content, existing, plan.action)
            if refused:
                return _with_publish(result, {"action": "error", "reason": refused})
        if plan.action is PublishAction.CREATE:
            created = provider.create(
                name=spec.artifact,
                content=content,
                kind=spec.kind,
                source="subagent",
                description=spec.description,
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {**plan.to_dict(), "slug": getattr(created, "slug", "")}
        elif plan.action is PublishAction.VERSION and existing is not None:
            updated = provider.update(
                existing.slug,
                content=content,
                snapshot=True,
                event_type="iterated",
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {
                **plan.to_dict(),
                "slug": getattr(updated, "slug", existing.slug if existing else ""),
            }
        else:
            payload = {**plan.to_dict(), "slug": existing.slug if existing else ""}
        # Copies land AFTER the body, because the destination is keyed by the slug the write just
        # settled. A copy failure does NOT fail the node for the same reason a registry failure does
        # not: the work happened. It is REPORTED instead — a body whose image reference points at a
        # copy that was never made must say so, or the artifact looks self-contained and isn't.
        payload["media"] = _land_media_copies(
            provider, str(payload.get("slug") or ""), media_copies, media_unresolved
        )
        _journal_publish(run_id, node.id or "", payload)
        _open_publish_outcome(run_id, node.id or "", payload)
        return _with_publish(result, payload)
    except Exception as exc:
        logger.debug("publish failed for node %s", node.id, exc_info=True)
        return _with_publish(result, {"action": "error", "reason": f"{type(exc).__name__}: {exc}"})


#: How long a published artifact gets to find a reader before the bet is graded. A week,
#: because a deliverable nobody opened in a week is the signal the dormancy sweep exists to
#: surface, and anything shorter would grade a Friday artifact on Monday morning.
PUBLISH_CONSUMPTION_HORIZON_SECS = 7 * 24 * 3600.0


def _open_publish_outcome(run_id: str, node_id: str, payload: dict[str, Any]) -> None:
    """Open the artifact's outcome question: we published a deliverable — did anyone consume it?

    The `publish:` producer of the general outcome facility (PP-9). Publishing records what the run
    DID; this records the bet about what it was FOR, so an artifact stream nobody reads becomes a
    measurable fact instead of a busy outbox. `PP-10` supplies the ground truth this asks for: a
    :data:`~personalclaw.ledger.outcomes.SOURCE_CONSUMPTION` question is graded off the artifact's
    own lifecycle timeline and the dashboard pin list — writers that already exist — so the answer
    is a real `measured` 1.0/0.0 on any box, with no vector store and no new counter. `PP-10`'s
    dormancy sweep (`learning/consumer_liveness.py`) then reads the RESOLUTIONS and proposes pausing
    or retiring a work unit whose last N cycles all went untouched.

    Best-effort, like the publish journal beside it: the artifact already landed, and no ledger
    write is worth failing a completed stage over.
    """
    slug = str(payload.get("slug") or "")
    if not run_id or not slug:
        return
    from personalclaw.ledger import outcomes
    from personalclaw.workflows.journal import Journal

    try:
        Journal(run_id).open_outcome(
            producer=outcomes.PRODUCER_PUBLISH,
            subject=f"published artifact `{slug}`",
            metric=outcomes.consumption_metric(slug),
            metric_source=outcomes.SOURCE_CONSUMPTION,
            horizon_secs=PUBLISH_CONSUMPTION_HORIZON_SECS,
            # One consumption is the whole bet: a deliverable is for somebody.
            baseline=1.0,
            node_id=node_id,
            slug=slug,
            artifact=str(payload.get("artifact") or ""),
            action=str(payload.get("action") or ""),
        )
    except Exception:
        logger.debug("publish outcome open failed for run %s", run_id, exc_info=True)


def _journal_publish(run_id: str, node_id: str, payload: dict[str, Any]) -> None:
    """Record one publish outcome in the run's own log — what the §2.5 outbox lists.

    A run-scoped journal rather than a query over the artifact registry: the registry knows an
    artifact exists, not which run published it, and reconstructing that from event metadata means
    scanning every artifact's events to answer "what did THIS run produce". A NOOP is journalled
    too,
    because an outbox that hides a converged republish makes the artifact look abandoned by its
    producer — the same reason `upsert_plan` attaches provenance to a no-op.
    """
    if not run_id or not payload.get("slug"):
        return
    from datetime import datetime, timezone

    from personalclaw.workflows import store as _store

    try:
        _store.append_jsonl(
            run_id,
            "publishes.jsonl",
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "node_id": node_id,
                "slug": payload.get("slug", ""),
                "artifact": payload.get("artifact", ""),
                "kind": payload.get("kind", ""),
                "action": payload.get("action", ""),
                "change_note": payload.get("change_note", ""),
                "media": payload.get("media", {}),
            },
        )
    except Exception:
        # A journal write must never fail a completed stage — the artifact already landed.
        logger.debug("publish journal write failed for run %s", run_id, exc_info=True)


def _land_media_copies(
    provider: Any, slug: str, copies: list[Any], unresolved: list[tuple[str, str]]
) -> dict[str, Any]:
    """Copy each referenced local file into the artifact's version dir. Reports what landed.

    `unresolved` rides in the SAME record as the successes so one read answers "is this artifact
    self-contained?". Split across two fields on two surfaces, the failures are the ones nobody
    looks at.
    """
    stored: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = [{"reference": r, "reason": why} for r, why in unresolved]
    for copy in copies:
        ok = False
        if slug:
            try:
                ok = provider.store_version_file(slug, copy.filename, copy.data)
            except Exception:
                logger.debug("media copy failed for %s/%s", slug, copy.filename, exc_info=True)
                ok = False
        if ok:
            stored.append(
                {
                    "reference": copy.reference,
                    "filename": copy.filename,
                    "sha256": copy.sha256,
                    "size": copy.size,
                }
            )
        else:
            failed.append(
                {
                    "reference": copy.reference,
                    "reason": "the artifact store did not accept the copy",
                }
            )
    return {"stored": stored, "unresolved": failed, "self_contained": not failed}


async def _text_refusal(spec: Any, content: str, existing: Any, action: Any) -> str:
    """Why the content scan will not let *content* be published as the artifact *spec* names, in
    the scan's words, or ``""`` when it may (``knowledge.artifact_ingest.text_refusal``). A new
    artifact (``create``) is read with its name and description, as it is written; a new version
    of *existing* writes its body only."""
    from personalclaw.knowledge.artifact_ingest import text_refusal
    from personalclaw.workflows.publish import PublishAction

    if action is PublishAction.CREATE:
        refused = await text_refusal(
            spec.kind, name=spec.artifact, description=spec.description, content=content
        )
        return refused.nothing_made if refused is not None else ""
    if existing is None:
        return ""  # a version of nothing writes nothing
    refused = await text_refusal(existing.kind, content=content)
    return refused.not_changed if refused is not None else ""


def _with_publish(result: NodeResult, payload: dict[str, Any]) -> NodeResult:
    """Attach the publish outcome to the node's output without disturbing it.

    A string output stays reachable at its original binding path — wrapping it in a dict would
    break
    every `{{nodes.x.output}}` downstream, so publishing a node's output would change what its
    consumers read.
    """
    result.published = payload
    if isinstance(result.output, dict):
        # Mirrored into the output too, so a downstream `{{nodes.x.output.published.slug}}` binding
        # can reach it — the typed field is for the ledger, the mirror is for the graph.
        result.output = {**result.output, "published": payload}
    return result
