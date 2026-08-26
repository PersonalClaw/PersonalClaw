"""``second-opinion`` action provider — the stalled-node handoff (EXECUTION-ISOLATION §4.1).

One dispatchable call site for "this is stuck; ask a different brain once". The three consumers
§4.1 names — the loop watchdog's `stagnant` offer, a workflow gate node's `on_stall:
second_opinion` policy, and the manual button on the run/loop cockpit's stalled banner — all fire
THIS provider rather than each assembling their own handoff, because the acceptance rule (the
disk re-diff) must have exactly one definition. The orchestration lives in
:func:`personalclaw.proposer.service.run_second_opinion`; this file is the trigger-facing surface.

**Core-native, not an app.** The ``webhook-action`` precedent (core keeps the name, the provider
ships as an app) does not apply: this provider reads the runner catalog, resolves a sandbox
provider, spawns a subagent and writes SEL rows — none of which is on the ``personalclaw.sdk.*``
surface an app is allowed to import. So it registers in-core and its name is added to
``ALLOWED_HOOK_PROVIDERS`` in the SAME commit; a provider in one set but not the other is the
mismatch that makes a trigger validate, save, and then fail at fire time.

``action_config`` shape::

    {
        "goal": "make the integration suite green",   # required
        "stuck_at": "the same assertion fails …",     # required
        "origin_runner": "gemini-cli",                # required — the exclusion key
        "ask": "…",                  # optional; defaults to "smallest change that unblocks"
        "workspace": "/abs/path",    # optional; defaults to the payload's workspace
        "sandbox": "none",           # the stalled run's sandbox class — the proposer inherits it
        "attempts": ["verbatim error output …"],
        "files_touched": ["src/app.py"],
        "brief_dir": "/abs/path",    # where the brief file lands (default: the workspace)
        "binding_order": ["codex", "claude-code"],
        "required_capabilities": ["fs_write"],
        "timeout_secs": 300,
        "require_health": true       # false only for a user-chosen target
    }
"""

from __future__ import annotations

import json
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.action_providers.template import render_template

#: Hard ceiling on one handoff. A second opinion is a single headless turn, not a session.
_MAX_TIMEOUT_SECS = 1800.0
_DEFAULT_TIMEOUT_SECS = 300.0


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


class SecondOpinionActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "second-opinion"

    @property
    def display_name(self) -> str:
        return "Second Opinion (hand off to a different runner)"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.proposer.service import run_second_opinion

        goal = render_template(str(action_config.get("goal", "") or ""), ctx).strip()
        stuck_at = render_template(str(action_config.get("stuck_at", "") or ""), ctx).strip()
        origin_runner = str(action_config.get("origin_runner", "") or "").strip()
        if not goal or not stuck_at:
            return ActionResult(
                success=False,
                error="second-opinion requires 'goal' and 'stuck_at'",
            )
        if not origin_runner:
            # Refused rather than defaulted. With no origin runner there is nothing to exclude,
            # so the ONE property that makes this a second opinion — that it comes from a
            # different brain — would silently not hold. An empty exclusion is not a safe
            # default; it is the defect.
            return ActionResult(
                success=False,
                error=(
                    "second-opinion requires 'origin_runner' — without the runner that stalled "
                    "the different-runner exclusion cannot be enforced"
                ),
            )

        payload = ctx.payload if isinstance(ctx.payload, dict) else {}
        workspace = str(
            action_config.get("workspace") or payload.get("workspace") or payload.get("cwd") or ""
        ).strip()
        session_key = str(
            action_config.get("session_key") or payload.get("session_key") or ""
        ).strip()
        try:
            timeout_secs = float(action_config.get("timeout_secs") or _DEFAULT_TIMEOUT_SECS)
        except (TypeError, ValueError):
            timeout_secs = _DEFAULT_TIMEOUT_SECS
        timeout_secs = max(1.0, min(timeout_secs, _MAX_TIMEOUT_SECS))

        outcome = await run_second_opinion(
            goal=goal,
            stuck_at=stuck_at,
            ask=render_template(str(action_config.get("ask", "") or ""), ctx).strip(),
            workspace=workspace,
            origin_runner=origin_runner,
            sandbox=str(action_config.get("sandbox") or payload.get("sandbox") or "none"),
            session_key=session_key,
            consumer=str(action_config.get("consumer") or ctx.event or ""),
            attempts=_str_tuple(action_config.get("attempts")),
            files_touched=_str_tuple(action_config.get("files_touched")),
            brief_dir=str(action_config.get("brief_dir") or "").strip(),
            required_capabilities=_str_tuple(action_config.get("required_capabilities")),
            binding_order=_str_tuple(action_config.get("binding_order")),
            require_health=bool(action_config.get("require_health", True)),
            timeout_secs=timeout_secs,
        )
        return ActionResult(
            success=outcome.accepted,
            stdout=json.dumps(outcome.to_dict(), indent=2),
            # A rejected handoff is a FAILED action, not a quiet success: §12's mitigation is
            # that "consumers treat unverified results as failed", and a truthful failure is
            # what lets a loop decide to stop rather than believing it was helped.
            error="" if outcome.accepted else outcome.rejection,
        )
