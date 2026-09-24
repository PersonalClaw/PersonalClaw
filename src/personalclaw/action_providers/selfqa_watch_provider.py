"""``selfqa-commit-watch`` action provider — the vcs trigger's action (SV-11).

Fired by the ``system:selfqa-commit-watch`` ``file``-kind trigger (the AUTOMATION-SUBSTRATE
``vcs`` preset watching the configured repo's ``.git/refs/heads/*``). It resolves the commit
delta through :mod:`personalclaw.selfqa.watch` and starts the ``self-qa`` run by delegating
to the registered ``run-workflow`` provider — the one place that already owns caller dedupe
(``START_DEDUPE``), origin stamping, and supervisor registration. Re-implementing the start
here would be a second writer on that seam.

A quiet fire (no new commits, first sight, unreadable repo) is a SUCCESS carrying its
reason — the two-weight discipline: "skipped for the right reason" and "never ran" must
never look alike.

``action_config`` shape::

    {"repo": "/path/to/repo"}   # written by selfqa.install.reconcile()
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


class SelfQaCommitWatchActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-commit-watch"

    @property
    def display_name(self) -> str:
        return "Self-QA commit watch"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.selfqa import watch

        repo = str((action_config or {}).get("repo", "") or "")
        fire = watch.check(repo)
        if fire.inputs is None:
            # Quiet is a verdict, not an absence: the fire ran and decided nothing is new.
            return ActionResult(success=True, stdout=f"skipped: {fire.quiet_reason}")

        from personalclaw.action_providers.registry import get_action_provider

        runner = get_action_provider("run-workflow")
        if runner is None:
            return ActionResult(
                success=False,
                error="run-workflow provider unavailable",
                stderr="selfqa-commit-watch delegates the start; nothing can start runs",
            )
        result = await runner.execute(
            {
                "workflow": watch.TEMPLATE,
                "inputs": fire.inputs,
                "idempotency_key": fire.idempotency_key,
            },
            ctx,
            timeout=timeout,
        )
        if result.success:
            logger.info(
                "selfqa: commit watch started %s for %d commit(s)",
                watch.TEMPLATE,
                len(fire.inputs.get("commits", [])),
            )
        return result
