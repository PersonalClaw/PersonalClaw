"""``run-workflow`` action provider — start a v2 workflow run from a trigger.

Deleted with the old workflow feature (WORKFLOWS-V2 Phase 1) and re-registered here
against the v2 engine. It is re-added to ``ALLOWED_HOOK_PROVIDERS`` **in the same
commit**: a provider registered in one set but not the other is exactly the mismatch
that lets a trigger validate, save, and then fail at fire time with nothing actionable.

``action_config`` shape::

    {
        "workflow": "triage-inbox",     # required: a saved def name
        "inputs": {"since": "1h"},      # optional: run inputs
        "mode": "background",           # background (default) | blocking
        "project_id": "...",            # optional project binding
        "idempotency_key": "..."        # optional caller dedupe key
    }

**`outcome: "launched"`, not success.** A background run has only STARTED when this
returns; its real outcome lands in the run's own ledger. Reporting it as plain success
would make an unverified run look verified — the honesty contract `ActionResult.outcome`
exists for.

**`on_overlap` is honoured here**, not left to the caller. A per-minute trigger against
a ten-minute workflow must not stack runs, and the def's declared policy (`skip` by
default) is the single place that decision belongs. The decision itself lives in
`workflows.overlap.decide` — exhaustive over the policy enum, with a raising tail —
because `queue` previously matched no branch here and fell through to "start now", which
is the opposite of what its name promises (WV-14).
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


class RunWorkflowActionProvider(ActionProvider):
    """Start a workflow run. Never drives it — execution is engine-owned."""

    @property
    def name(self) -> str:
        return "run-workflow"

    @property
    def display_name(self) -> str:
        return "Run workflow"

    @property
    def supports_dry_run(self) -> bool:
        """A workflow run is spawn-based, so an observe-mode preview is meaningful."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        import json
        import time

        context = ctx
        started = time.monotonic()
        name = str((action_config or {}).get("workflow", "") or "").strip()
        if not name:
            return ActionResult(
                success=False,
                error="run-workflow requires a `workflow` name",
                stderr="no workflow named in the action config",
            )

        try:
            from personalclaw.workflows import defs as defs_mod
            from personalclaw.workflows import overlap as overlap_mod
            from personalclaw.workflows import store
            from personalclaw.workflows.effects import START_DEDUPE
            from personalclaw.workflows.models import (
                OriginKind,
                OverlapPolicy,
                RunOrigin,
                RunStatus,
                WorkflowRun,
            )
        except Exception as exc:  # the engine should always import; be explicit if not
            return ActionResult(
                success=False,
                error=f"workflow engine unavailable: {exc}",
                stderr="could not import the v2 workflow engine",
            )

        # Caller dedupe (WF2-R1): a retried tool/trigger dispatch with the same key gets
        # the EXISTING run back rather than minting a second one doing the same work.
        caller_key = str((action_config or {}).get("idempotency_key", "") or "")
        if caller_key:
            existing = START_DEDUPE.lookup(caller_key)
            if existing:
                return ActionResult(
                    success=True,
                    outcome="launched",
                    stdout=json.dumps({"run_id": existing, "deduped": True}),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        definition = await _load_def(defs_mod, name)
        if definition is None:
            return ActionResult(
                success=False,
                error=f"unknown workflow {name!r}",
                stderr="no workflow definition by that name is registered",
            )

        spec = _spec_of(definition)
        if not isinstance(spec, dict) or not spec.get("root"):
            return ActionResult(
                success=False,
                error=f"workflow {name!r} has no usable spec",
                stderr="the definition carries no root node",
            )

        # on_overlap — the def's declared policy, applied before a second run exists. The
        # branch lives in `overlap.decide`, exhaustive over the enum with a raising tail.
        overlap = _overlap_of(definition, OverlapPolicy)
        active = [r for r in store.active_runs() if r.workflow_name == name]
        queued = overlap_mod.queued_runs(name)
        action = overlap_mod.decide(overlap, active=len(active), queued=len(queued))
        Act = overlap_mod.OverlapAction

        if action == Act.SKIP:
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {"skipped": True, "reason": "already running", "run_id": active[0].id}
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if (action_config or {}).get("dry_run"):
            # Honest preview: nothing is created — checked BEFORE the queue path, because a
            # preview that persisted a queued run would be a write. It names the DECIDED
            # action, so a dry run against a busy def says "would queue", not "would start".
            # The engine's own preflight is what would validate inputs, and claiming more
            # than that here would be a lie.
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {
                        "dry_run": True,
                        "would": action.value,
                        "would_start": name,
                        "inputs": dict((action_config or {}).get("inputs") or {}),
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if action == Act.DROP:
            # The cap refused this start. Loud in BOTH places: a truncation that reported
            # "queued" would be the same lie as the concurrent start this atom replaced.
            logger.warning(
                "run-workflow: dropped a queued start for %s — the queue is already %d deep "
                "(max %d); the pending run is %s",
                name,
                len(queued),
                overlap_mod.MAX_QUEUE_DEPTH,
                queued[0].id,
            )
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {
                        "dropped": True,
                        "reason": "queue_full",
                        "queue_depth": len(queued),
                        "max_queue_depth": overlap_mod.MAX_QUEUE_DEPTH,
                        "queued_run_id": queued[0].id,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if action not in (Act.QUEUE, Act.START, Act.CANCEL_THEN_START):
            # Refuse before anything is created. The dangerous default at this call site is
            # "fall through and launch" — which is exactly what `queue` used to do.
            raise AssertionError(f"run-workflow has no branch for OverlapAction.{action.name}")

        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=name,
                status=RunStatus.DRAFT,
                inputs=dict((action_config or {}).get("inputs") or {}),
                mode=str((action_config or {}).get("mode", "background") or "background"),
                project_id=str((action_config or {}).get("project_id", "") or ""),
                origin=RunOrigin(
                    kind=OriginKind.HOOK,
                    trigger_id=str(getattr(context, "context", "") or ""),
                ),
                # The queued marker goes in the SAME insert as the row — marking after
                # `create` would leave a window in which the row is an ordinary DRAFT.
                extra=overlap_mod.queued_extra() if action == Act.QUEUE else {},
            )
        )
        store.write_spec(run.id, spec)
        if caller_key:
            START_DEDUPE.remember(caller_key, run.id)

        if action == Act.QUEUE:
            # Persisted, not launched, and NAMED. The drain (`overlap.drain`, called from
            # the single terminal writer and from the watchdog poll) starts it when the
            # prior ends. `outcome="queued"` and not "skip": a durable run record exists,
            # so reporting it as a no-op skip would under-report real state; and not
            # "launched": nothing is running.
            return ActionResult(
                success=True,
                outcome="queued",
                stdout=json.dumps(
                    {
                        "run_id": run.id,
                        "workflow": name,
                        "queued": True,
                        "started": False,
                        "behind": [r.id for r in active],
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if action == Act.CANCEL_THEN_START:
            for prior in active:
                store.request_cancel(prior.id)

        launched = await _launch(run, spec)
        if not launched:
            return ActionResult(
                success=False,
                error="no workflow supervisor available to start the run",
                stderr=f"run {run.id} was created but not started",
                stdout=json.dumps({"run_id": run.id, "started": False}),
            )

        return ActionResult(
            success=True,
            # "launched", never plain success: the run has STARTED, and its real outcome
            # lands in its own ledger. Anything stronger would report unverified work as
            # verified.
            outcome="launched",
            stdout=json.dumps({"run_id": run.id, "workflow": name, "started": True}),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


async def _load_def(defs_mod: Any, name: str) -> Any | None:
    """Find a def across every registered provider. Never raises on a miss — a bad name
    is a user error with an actionable message, not a traceback."""
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            logger.debug("workflow def provider %s failed on %s", provider_name, name)
            continue
        if found is not None:
            return found
    return None


def _spec_of(definition: Any) -> dict[str, Any]:
    if isinstance(definition, dict):
        return definition
    to_dict = getattr(definition, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return result if isinstance(result, dict) else {}
    return {}


def _overlap_of(definition: Any, policy_cls: Any) -> Any:
    raw = getattr(definition, "on_overlap", None)
    if raw is not None:
        return raw
    if isinstance(definition, dict):
        try:
            return policy_cls(str(definition.get("on_overlap", "skip") or "skip"))
        except ValueError:
            return policy_cls.SKIP
    return policy_cls.SKIP


async def _launch(run: Any, spec: dict[str, Any]) -> bool:
    """Hand the run to the watchdog, which owns controller registration.

    Going through the supervisor rather than constructing a controller here is what keeps
    ONE writer per run: a provider-owned controller would be invisible to adoption and
    cancel, and a restart would start a second one alongside it.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        logger.debug("action services unavailable for run-workflow", exc_info=True)
        return False
    supervisor = getattr(services, "workflows", None) if services else None
    if supervisor is None:
        return False
    try:
        await supervisor.launch(run, spec)
    except Exception:
        logger.exception("run-workflow: supervisor refused to launch run %s", run.id)
        return False
    return True
