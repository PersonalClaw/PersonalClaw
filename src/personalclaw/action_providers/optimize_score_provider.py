"""``optimize-score`` action provider — the optimize-harness search's scoring step.

A thin wrapper over :func:`personalclaw.evals.optimize.score_step`, the step that measures one
candidate of the bundled ``optimize-harness`` template's search with the product's own evaluation
(:mod:`personalclaw.evals.candidate_score`): the candidate template run against the target's own
recorded runs and judged by the eval judge. The search's other steps are ``bash`` steps running
``personalclaw optimize-harness <step>``; this one runs in the gateway instead, because it is the
step that calls models: in the gateway, every call it makes is measured by the engine and booked to
the step and to the run, which is what the run's page shows and what the search's budget is read
back from. A child process's calls would reach neither.
"""

from __future__ import annotations

import json
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult


class OptimizeScoreActionProvider(ActionProvider):
    """Score one optimize-harness candidate against the target's own runs, within the budget.

    ``action_config`` shape (the template's ``config.with``)::

        {
            "target": "code-project",   # required: the template the search is improving
            "sandbox": "/abs/path",     # required: the search's sandbox, its witness and ledger
            "budget_usd": 2.0,          # required: the search's dollar budget
            "ops": [],                  # the candidate's typed ops, as the proposer gave them
            "diff_text": "..."          # the candidate's diff, for the empty-edit check
        }

    Its output is the measurement — ``{score, score_state, reason, passed, judged, rejected,
    cases, spent_usd, budget_stopped}`` — which the search's ``adjudicate`` step reads as the
    candidate's only score. A candidate it does not score (dead, empty, over the budget, or one
    the judge could not score on enough runs) is a successful step whose ``score`` is ``null`` and
    whose ``reason`` says why; only a search that skipped its own preflight fails here.
    """

    @property
    def name(self) -> str:
        return "optimize-score"

    @property
    def display_name(self) -> str:
        return "Score an optimize-harness candidate"

    @property
    def internal(self) -> bool:
        """A step of the optimize-harness search, configured by its template from the search's
        own sandbox and the proposer's answer — not an action to pick for a trigger."""
        return True

    @property
    def hands_config_to_a_model(self) -> bool:
        """The candidate's ops become the prompts its arms are answered from, so a
        ``{{secret:KEY}}`` in them stays a name."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.evals.optimize import LiveMutationError, score_step

        payload = dict(action_config or {})
        provenance = ctx.payload or {}
        payload["run_id"] = str(provenance.get("run_id") or "")
        payload["node_id"] = str(provenance.get("node_id") or "")
        try:
            result = await score_step(payload)
        except (ValueError, LiveMutationError, OSError) as exc:
            # A refusal the step can name — no preflight witness, no sandbox, no run, no budget —
            # and one a retry would meet again, so it is the template's to fix.
            return ActionResult(success=False, error=str(exc), failure_class="user")
        return ActionResult(success=True, stdout=json.dumps(result, sort_keys=True, default=str))


def create_provider(config: dict[str, Any] | None = None) -> "OptimizeScoreActionProvider":
    return OptimizeScoreActionProvider()
