"""``selfqa-triage`` action provider — step zero of the Self-QA loop, at zero tokens.

Classifies each new commit's user-visible impact and writes one ledger record per commit, then
hands the impactful ones forward as structured output the template branches on.

**Why an action node rather than the `infer` node the plan sketched.** Two properties the plan's
own §3.2 asks for are only obtainable from code. The skip must be *recorded with its rationale*,
and only a node with the run id can write the run ledger — an `infer` node returns text, and the
engine's own `step_skipped` row carries no reason (`journal.step_skipped` takes no rationale), so
a model-authored triage leaves the "why did nothing run?" question unanswered in the one place
the plan says it must be answered. And the classification must be *assertable*: a test-only
commit skipped for the right reason and a companion that never fired both produce zero findings,
so a prompt whose only checkable property is "some string came back" cannot tell them apart.

Triage is also the wrong place to spend a model call. Judging "could a user notice this?" from
changed paths is a decision a `frozenset` makes correctly and for free, on every commit, forever.
The judgment the loop genuinely needs — *what scenario would catch this* — stays in the
template's `scenario-gen` stage, where it belongs.

``action_config`` shape::

    {
        "repo": "/path/to/repo",     # required
        "commits": ["sha", …],        # required
        "max_scenarios": 3            # optional; defaults to agent.self_qa.max_scenarios_per_fire
    }

Output (one JSON object, so the template can bind ``{{nodes.triage.output.*}}``)::

    {
        "verdicts":   [{sha, impact, rationale, subject, paths}],
        "impactful":  [{…}],          # capped at max_scenarios
        "skipped":    [{…}],
        "has_impactful": true|false,  # what the branch reads
        "recorded": 4                 # ledger rows written
    }
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


def _configured_cap() -> int:
    """`agent.self_qa.max_scenarios_per_fire`, or the dataclass default if config is unreadable.

    Read here rather than templated in, because the ceiling is the user's standing answer to "how
    much unattended work may one push start" — a template input would let a caller talk past it.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().agent.self_qa.max_scenarios_per_fire)
    except Exception:  # noqa: BLE001 - a config read must not fail the node
        return 3


class SelfQaTriageActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-triage"

    @property
    def display_name(self) -> str:
        return "Triage Commits for Self-QA"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        import json

        from personalclaw.selfqa.ledger import record_triage
        from personalclaw.selfqa.triage import triage_commits

        repo = str(action_config.get("repo", "") or "").strip()
        raw_commits = action_config.get("commits")
        if isinstance(raw_commits, str):
            # A binding may arrive as the watcher's JSON payload rather than a list.
            try:
                parsed = json.loads(raw_commits)
            except ValueError:
                parsed = [s.strip() for s in raw_commits.split(",") if s.strip()]
            raw_commits = parsed.get("commits") if isinstance(parsed, dict) else parsed
        commits = [str(s).strip() for s in raw_commits or [] if str(s).strip()]

        if not repo or not commits:
            return ActionResult(
                success=False, error="selfqa-triage requires 'repo' and a non-empty 'commits'"
            )

        verdicts = triage_commits(repo, commits)

        # The ledger write happens BEFORE the return, and a write failure fails the node. A
        # triage whose records did not land is indistinguishable from one that never ran, which
        # is the exact ambiguity these rows exist to remove — reporting success without them
        # would ship the silence back.
        recorded = 0
        run_id = str(ctx.payload.get("run_id", "") or "")
        # The engine's own instance key for THIS node, not the node id. `record_triage` refuses an
        # empty one, because `inspect_node` slices a run's ledger by `instance_path` — a row stamped
        # `triage` instead of `root.children[0]` is written and then invisible in the runs surface,
        # which is exactly the half of the skip contract these rows exist to satisfy.
        instance_path = str(ctx.payload.get("instance_path", "") or "")
        if run_id:
            from personalclaw.workflows.journal import Journal

            journal = Journal(run_id=run_id)
            try:
                for verdict in verdicts:
                    record_triage(journal, verdict, instance_path=instance_path)
                    recorded += 1
            except Exception as exc:  # noqa: BLE001 - error result, never raise
                return ActionResult(
                    success=False, error=f"selfqa-triage could not record its verdicts: {exc}"
                )
        else:
            logger.warning("selfqa-triage: no run_id in payload; verdicts not recorded")

        cap = action_config.get("max_scenarios")
        cap_int = int(cap) if isinstance(cap, (int, float, str)) and str(cap).isdigit() else None
        limit = max(1, cap_int if cap_int else _configured_cap())

        impactful = [v.to_dict() for v in verdicts if not v.skipped][:limit]
        skipped = [v.to_dict() for v in verdicts if v.skipped]

        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "verdicts": [v.to_dict() for v in verdicts],
                    "impactful": impactful,
                    "skipped": skipped,
                    "has_impactful": bool(impactful),
                    "recorded": recorded,
                }
            ),
        )


def create_provider(config: dict[str, Any] | None = None) -> "SelfQaTriageActionProvider":
    return SelfQaTriageActionProvider()
