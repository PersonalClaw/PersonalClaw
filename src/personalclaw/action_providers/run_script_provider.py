"""``run-script`` action provider — run a sandboxed Python script on a trigger.

The deterministic (zero-token) counterpart to ``invoke-agent``: instead of
spawning an LLM, it runs a ``file.py:func`` script under
``~/.personalclaw/crons/`` in the sandbox via
:func:`personalclaw.schedule_script.run_script_sandboxed`. This is the action
form of a Schedule's ``script`` exec-mode; folding the schedule bridge onto it is
P4c (#13). Defined here so the provider catalog is complete the moment the
Trigger entity (P4b) can reference it.

``action_config`` shape::

    {"script": "daily_report.py:run", "timeout": 60}

``script`` is required (``file.py[:func]`` relative to the crons dir). The
script's structured return maps to the result: ``status: ok|done|report`` →
success with its ``message`` as stdout; ``skip`` → success, silent; ``error`` →
failure with the script's ``error``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)


class RunScriptActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-script"

    @property
    def display_name(self) -> str:
        return "Run Script"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        script = (action_config.get("script") or "").strip()
        if not script:
            return ActionResult(success=False, error="run-script action is missing 'script' field")

        try:
            zt_timeout = int(action_config.get("timeout", 0) or 0)
        except (ValueError, TypeError):
            zt_timeout = 0

        from personalclaw.schedule_script import run_script_sandboxed

        start = time.monotonic()
        loop = asyncio.get_running_loop()
        # job_id/message double as the script's context: the event drives the
        # session key, the free-form context is the script's input message.
        try:
            result = await loop.run_in_executor(
                None,
                run_script_sandboxed,
                script,
                f"action:{ctx.event}",
                ctx.context,
                zt_timeout or timeout,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        elapsed = int((time.monotonic() - start) * 1000)
        status = result.get("status")
        if status in ("ok", "done", "report", "skip"):
            # ``skip`` → silent success; ``done`` → one-shot (caller removes the
            # job). ``ok``/``report`` are ordinary success. The scheduled-run
            # path reads ``outcome`` to honour these; lifecycle ignores it.
            return ActionResult(
                success=True,
                stdout=result.get("message", "") or "",
                duration_ms=elapsed,
                outcome=status if status in ("skip", "done") else "",
            )
        return ActionResult(
            success=False,
            error=result.get("error") or f"script status {status!r}",
            duration_ms=elapsed,
        )


def create_provider(config: dict[str, Any] | None = None) -> "RunScriptActionProvider":
    return RunScriptActionProvider()
