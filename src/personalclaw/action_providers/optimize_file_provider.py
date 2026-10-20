"""``optimize-file`` action provider — the optimize-harness search's last step: file its winner.

A thin wrapper over :func:`personalclaw.evals.optimize.file_step`, which files the winner the
search's ledger names as a template-diff proposal a person reviews, and says why the search stopped.
The search's other steps are ``bash`` steps running ``personalclaw optimize-harness <step>``, which
read and write only the search's own sandbox. This one runs in the gateway instead, as the scoring
step does, because filing writes PersonalClaw's own stores in its home: the proposal queue, the
Inbox row that raises the proposal, and the study it pre-registers. A command a run starts runs in
the sandbox, which may not lay out the top of the home (on Linux it can neither add an entry there
nor replace a file there), so a filing made from one was lost in any home that lacked one of those
stores. In the gateway the filing is also the run's own work, held to its rules: a run that keeps
nothing, as the Incognito or Temporary chat that started it, files no proposal.
"""

from __future__ import annotations

import json
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult


class OptimizeFileActionProvider(ActionProvider):
    """File an optimize-harness search's winner as a proposal. Applies NOTHING.

    ``action_config`` shape (the template's ``config.with``)::

        {
            "subject": "code-project",   # the template the search improved
            "sandbox": "/abs/path",      # required: the search's sandbox, its ledger and evidence
            "suite_threshold": 0.3,      # the gate's suite threshold, said in the proposal
            "best_ever": 0.5,            # the floor the search started from
            "halt": "iterations_exhausted",  # required: why the search loop says it stopped
            "halt_detail": "..."         # the clause that says it
        }

    Its output is what the filing came to — ``{ok, filed, halt_reason, summary, proposal_id, ...}``
    — and a search that admitted nothing files nothing and says so, which is a result, not a
    failure. Only a step with no sandbox or no halt to read fails here.
    """

    @property
    def name(self) -> str:
        return "optimize-file"

    @property
    def display_name(self) -> str:
        return "File an optimize-harness search's winner"

    @property
    def internal(self) -> bool:
        """A step of the optimize-harness search, configured by its template from the search's
        own sandbox and loop — not an action to pick for a trigger."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.evals.optimize import file_step

        try:
            result = file_step(dict(action_config or {}))
        except (ValueError, OSError) as exc:
            # A refusal the step can name (no sandbox, no halt, a preflight record that cannot be
            # read), and one a retry would meet again, so it is the template's to fix.
            return ActionResult(success=False, error=str(exc), failure_class="user")
        return ActionResult(success=True, stdout=json.dumps(result, sort_keys=True, default=str))


def create_provider(config: dict[str, Any] | None = None) -> "OptimizeFileActionProvider":
    return OptimizeFileActionProvider()
