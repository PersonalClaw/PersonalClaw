"""``check-work`` action provider — the engine-native half of HARNESS-CRAFT §3.2 (HC-5).

A thin spec wrapper over :func:`personalclaw.check_work.derive_and_run` — the §3.1 core
the bundled ``check-work`` skill and the SDLC post-gate hook already share — so any v2
workflow can end with a verification node that runs the SAME reconstruct → derive →
execute pass, and the three entry points cannot drift.

Doctrine carried over unchanged (ground truth over self-report): every derived check
either executes against the filesystem or is reported ``unverifiable`` — never assumed
passing. The core never shells out on its own authority and this provider injects no
``command_runner``, so a claim naming a command is reported ``unverifiable`` ("re-run it
yourself") rather than optimistically passed. Filesystem checks are bounded reads
confined under the given root (`check_work._resolve` refuses an escape).

``action_config`` shape::

    {
        "text": "the claims to verify",   # required — a stage's findings, a summary
        "root": "/where/the/work/landed", # optional; defaults to the run's workspace
        "max_checks": 4                   # optional; the core's 2-4 ceiling
    }

Output: ``{verdict, note, checks, report}`` — ``checks`` carries one
``{kind, target, needle, claim, label, how, status, evidence}`` per derived check and
``report`` is the same markdown ``render_report`` gives the skill. Verdict mapping
follows the SDLC hook's precedent exactly (``ok = verdict != "fail"``): only a check
that RAN and contradicted its claim fails the node; ``unverifiable`` and underivable
never manufacture a failure — the node adds ground truth, it does not invent doubt.
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


class CheckWorkActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "check-work"

    @property
    def display_name(self) -> str:
        return "Check Work"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        import json

        from personalclaw.check_work import MAX_CHECKS, derive_and_run, render_report

        text = str(action_config.get("text", "") or "")
        if not text.strip():
            return ActionResult(
                success=False,
                error="check-work requires 'text' — the claims to verify",
            )
        # The run workspace is where an upstream stage's files land, so it is the
        # default ground truth; an explicit `root` overrides it for work done elsewhere.
        root = str(action_config.get("root", "") or "").strip() or str(
            ctx.payload.get("workspace", "") or ""
        )
        if not root:
            return ActionResult(
                success=False,
                error=(
                    "check-work has nothing to check against: no 'root' was given and "
                    "this run has no workspace"
                ),
            )
        try:
            max_checks = int(action_config.get("max_checks") or MAX_CHECKS)
        except (TypeError, ValueError):
            max_checks = MAX_CHECKS

        try:
            report = derive_and_run(text, root=root, max_checks=max_checks)
        except Exception as exc:  # noqa: BLE001 — an error result, never a raise
            return ActionResult(
                success=False, error=f"check-work failed: {type(exc).__name__}: {exc}"
            )

        payload = json.dumps(
            {
                "verdict": report.verdict,
                "note": report.note,
                "checks": [
                    {
                        "kind": r.check.kind,
                        "target": r.check.target,
                        "needle": r.check.needle,
                        "claim": r.check.claim,
                        "label": r.check.label,
                        "how": r.check.how,
                        "status": r.status,
                        "evidence": r.evidence,
                    }
                    for r in report.results
                ],
                "report": render_report(report),
            },
            ensure_ascii=False,
        )
        if report.verdict == "fail":
            # A verification node that reports a contradiction and still succeeds is
            # decoration. FAILED, with the full report kept in the output — the same
            # verdict mapping the SDLC post-gate hook uses to hold a stage.
            failed = "; ".join(r.check.label for r in report.failed)
            return ActionResult(
                success=False,
                error=f"check-work: a derived check failed — {failed}",
                stdout=payload,
            )
        return ActionResult(success=True, stdout=payload)


def create_provider(config: dict[str, Any] | None = None) -> "CheckWorkActionProvider":
    return CheckWorkActionProvider()
