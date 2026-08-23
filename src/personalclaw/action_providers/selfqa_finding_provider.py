"""``selfqa-file-finding`` action provider — the Self-QA loop's filing step.

A failing as-a-user scenario must leave **one** Inbox item and **one** Task (SELF-VERIFICATION
Success Criterion #6). Both sinks already exist and neither is new here: the item goes through
`native_source.post_to_inbox` (the in-core push sink) and the Task through the native
`TaskProvider` via `tasks.registry.create_task`. What this provider adds is a single dispatchable
call site for the pair, so the count is enforced by code rather than by a template author
remembering to write exactly two nodes.

**Why a provider at all**, given §5's "no new action provider": that sentence forbids inventing a
*`qa-run`* provider to fire the QA workflow — `run-workflow` covers that, and this provider does
not touch it. §5 also states the rule for the case it explicitly contemplates ("if a later
revision did add an action provider, it MUST be added to `ALLOWED_HOOK_PROVIDERS`"), which is
followed. The alternative was a `stage` node instructing a subagent to call two tools "exactly
once each" — a count no test can assert and no engine enforces, which is precisely the
one-is-a-ceiling failure the criterion is about. §5's other constraint, "no new source/provider"
for Inbox/Tasks, is honored literally: this adds neither an inbox source nor a task provider.

``action_config`` shape::

    {
        "sha": "abc123…",              # required — the commit under test
        "scenario_id": "s1",           # required — stable within the commit
        "title": "Sending a message …", # required — the Inbox line and Task title
        "scenario_text": "…",          # the as-a-user steps
        "repro_steps": ["…"],          # optional
        "evidence_ref": "artifact:…",  # optional — the evidence bundle
        "fix_branch": "pclaw/selfqa-…" # optional — never merged, never pushed
    }
"""

from __future__ import annotations

from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.template import render_template


class SelfQaFindingActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "selfqa-file-finding"

    @property
    def display_name(self) -> str:
        return "File Self-QA Finding"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.selfqa.findings import ScenarioFinding, file_finding

        sha = str(action_config.get("sha", "") or "").strip()
        scenario_id = str(action_config.get("scenario_id", "") or "").strip()
        title = render_template(str(action_config.get("title", "") or ""), ctx).strip()
        if not sha or not scenario_id or not title:
            return ActionResult(
                success=False,
                error="selfqa-file-finding requires 'sha', 'scenario_id' and 'title'",
            )

        repro = action_config.get("repro_steps")
        finding = ScenarioFinding(
            sha=sha,
            scenario_id=scenario_id,
            title=title,
            scenario_text=render_template(str(action_config.get("scenario_text", "") or ""), ctx),
            repro_steps=[str(s) for s in repro] if isinstance(repro, list) else [],
            evidence_ref=str(action_config.get("evidence_ref", "") or ""),
            fix_branch=str(action_config.get("fix_branch", "") or ""),
        )

        try:
            filed = await file_finding(finding)
        except Exception as exc:  # noqa: BLE001 - error result, never raise
            return ActionResult(success=False, error=f"selfqa-file-finding failed: {exc}")

        if filed.already_filed:
            # A resumed run replaying this node is not a failure and must not file a second
            # copy. `skip` suppresses delivery, so the user is not told twice either.
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=f"finding {finding.key} was already filed",
            )

        return ActionResult(
            success=True,
            stdout=(
                f"filed finding {finding.key}: "
                f"inbox={filed.inbox_item_id or '<none>'} task={filed.task_id}"
            ),
        )


def create_provider(config: dict[str, Any] | None = None) -> "SelfQaFindingActionProvider":
    return SelfQaFindingActionProvider()
