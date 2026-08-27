"""``browse`` — the autonomous web-interaction action provider (BA-3, plan §8.3/§9).

A standard :class:`~personalclaw.action_providers.base.ActionProvider`, which is the whole
point: a workflow action node, a schedule trigger, a lifecycle hook and an event trigger all
invoke it by NAME through the seams they already use, so browse inherits the denylist, the
incident kill switch, the rung ladder and the injection screen without any of those seams
learning what a browser is. Plan §9 calls this provider-fidelity wiring; the failure it
avoids is a bespoke "browse runner" beside the dispatch path, governed by nothing.

``action_config``::

    {"goal": "…", "start_url": "https://…",
     "max_steps": 20,              # optional; plan §7.2 default
     "target": "gateway",          # optional; "gateway" (default) | "user_browser"
     "cdp_url": "ws://127.0.0.1:9222/devtools/page/…",   # the page target to drive
     "screenshot_dir": "/path"}    # optional; capture-to-PATH, never base64

**Two execution targets, one selector** (BA-7, plan §(a)/§(d)). ``target`` picks WHICH browser:
``gateway`` (the default, and the only behaviour before BA-7) drives the ``cdp_url`` on this
config under the gateway's own profile; ``user_browser`` drives the operator's own browser
through the connector. The vocabulary, the connector and both refusals live in
:mod:`personalclaw.browse.target` — read its module docstring for why an unconnected
``user_browser`` task SKIPS instead of falling back, and why it can never run unattended.

**Where the guards are.** The budget is checked inside
:func:`~personalclaw.browse.loop.run_browse_loop`, immediately before each model call, and the
incident kill switch is checked here before a browser is touched. Neither lives in a helper
that only the default path calls: a gate placed one level away from where the work happens is
bypassed by the next caller that supplies its own plumbing, which is exactly how a guard ends
up shipped and inert.

**Browser lifecycle is NOT here.** The provider drives a CDP page target it is given
(``cdp_url``); launching Chrome with a persistent per-site profile is BA-4's credential-handoff
slice. Absent a target the provider returns a typed, actionable failure rather than pretending
to browse — an action that silently no-ops is worse than one that says it cannot run.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.browse.loop import (
    MAX_STEPS_DEFAULT,
    PARK_BUDGET_EXHAUSTED,
    PARK_STEP_EXHAUSTED,
    BrowseLoopResult,
    run_browse_loop,
)
from personalclaw.errors import AgentError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "browse"

#: ``ActionResult.outcome`` for a run that stopped early with work worth keeping. The engine's
#: action-node dispatch maps it to a WAITING instance, which the controller surfaces as
#: ``needs_input`` — see :func:`personalclaw.workflows.engine.dispatch_action`.
OUTCOME_NEEDS_INPUT = "needs_input"

#: ``ActionResult.outcome`` for a task that did not run and left nothing behind. The engine maps
#: it to ``NO_CHANGE`` (``workflows.engine.dispatch_action``) — a skip is not a failure, so the
#: retry machinery must not hammer a connector that simply is not attached.
OUTCOME_SKIP = "skip"

#: The model use-case axis the loop's decisions route through. ``reasoning`` rather than
#: ``background``: choosing the next action on an adversarial page is the reasoning axis's job,
#: and the background axis is bound to the cheap models digests use.
USE_CASE = "reasoning"


def _budget_check() -> tuple[str, str]:
    """The day + run budget verdict, consulted before every model call in the loop.

    Day scope first: it is the ceiling a user actually sets, and a run that would blow the day
    must not be allowed to spend its first token. Never raises — an unreadable meter answers
    ``ok`` because failing browse closed on a bookkeeping error would take out the feature
    without protecting a cent (the model-call chokepoint still meters the call itself).
    """
    try:
        from personalclaw.guardrails.budgets import (
            budget_from_config,
            current_run_budget,
            current_run_key,
            get_meter,
        )

        meter = get_meter()
        verdict, reason = meter.check_day(budget_from_config())
        if verdict.value == "exceeded":
            return "exceeded", reason
        run_key = current_run_key()
        if run_key:
            verdict, reason = meter.check_run(run_key, current_run_budget())
            if verdict.value == "exceeded":
                return "exceeded", reason
        return "ok", ""
    except Exception:
        logger.debug("browse: budget verdict unavailable", exc_info=True)
        return "ok", ""


async def _decide(prompt: str) -> str:
    """One model call through the standard one-shot seam.

    ``one_shot_completion`` resolves the provider through the use-case bridge, which wraps it
    in the model-call guard (breaker + hard timeout + attempt audit + spend metering). So the
    loop's per-step charge lands on the same meter :func:`_budget_check` reads — one number,
    not two that drift.
    """
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case=USE_CASE)


class BrowseActionProvider(ActionProvider):
    """Drive a real browser toward a goal, bounded by steps, budget and the egress policy.

    Every page the agent reads is fenced as untrusted data before it reaches the model, every
    navigation is pre-flighted through the BROWSE egress policy, and a run that exhausts its
    steps or its budget parks with its notes intact instead of failing.
    """

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Browse the web"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        goal = str(action_config.get("goal") or "").strip()
        start_url = str(action_config.get("start_url") or "").strip()
        if not goal or not start_url:
            return self._error(
                "browse needs both `goal` and `start_url`",
                why="the action config named neither a task nor a page to start from",
                fix='set config to {"goal": "…", "start_url": "https://…"}',
                started=started,
            )

        from personalclaw.browse.target import (
            TARGET_USER_BROWSER,
            UnknownBrowseTarget,
            connector_status,
            disconnected_skip,
            permits_unattended,
            resolve_cdp_url,
            resolve_target,
            unattended_origin,
            unattended_refusal,
            unknown_target_error,
        )

        try:
            target = resolve_target(action_config)
        except UnknownBrowseTarget as exc:
            typed = unknown_target_error(exc.raw)
            return ActionResult(
                success=False,
                error=typed.what,
                duration_ms=int((time.monotonic() - started) * 1000),
                agent_error=typed,
            )

        from personalclaw.guardrails.incident import incident_active

        if incident_active():
            # Refused, not failed: the kill switch is on, and a retry loop against it would
            # be a storm against a control someone deliberately pulled.
            return ActionResult(
                success=False,
                error="incident mode is active — unattended browsing is suspended",
                duration_ms=int((time.monotonic() - started) * 1000),
                agent_error=AgentError(
                    code="ERR_BROWSE_INCIDENT_ACTIVE",
                    what="browse refused to start because incident mode is active",
                    why="incident mode suspends all unattended work",
                    fix="clear incident mode in Settings → Guardrails, then re-run",
                ),
            )

        # ── BA-7: the two gates the `user_browser` target carries, at the call site ──
        #
        # Here and not only at registration, because a registration check protects rows in the
        # trigger store and NOTHING else: a workflow action node, an app route and a lifecycle
        # hook all reach this method without passing through `triggers.tools`. A gate one level
        # away from the work is bypassed by the next caller that brings its own plumbing.
        if not permits_unattended(target):
            origin = unattended_origin()
            if origin:
                typed = unattended_refusal(target, origin=origin)
                return ActionResult(
                    success=False,
                    error=typed.what,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    agent_error=typed,
                )

        cdp_url = resolve_cdp_url(target, action_config)
        if target == TARGET_USER_BROWSER:
            status = connector_status()
            if not status.connected:
                # SKIPPED, not failed, and above all NOT re-pointed at `action_config["cdp_url"]`:
                # `resolve_cdp_url` never reads that key on this branch, so the gateway profile is
                # unreachable from here rather than merely unused.
                typed = disconnected_skip(status)
                return ActionResult(
                    success=True,
                    outcome=OUTCOME_SKIP,
                    stdout=json.dumps({"skipped": True, "target": target, "reason": status.reason}),
                    stderr=f"{typed.what}. {status.fix}.",
                    duration_ms=int((time.monotonic() - started) * 1000),
                    agent_error=typed,
                )

        try:
            max_steps = int(action_config.get("max_steps") or MAX_STEPS_DEFAULT)
        except (TypeError, ValueError):
            max_steps = MAX_STEPS_DEFAULT
        max_steps = max(1, max_steps)

        session, page, closer = None, None, None
        try:
            session, page, closer = await self._open(action_config, ctx, cdp_url=cdp_url)
        except BrowseUnavailable as exc:
            return self._error(
                str(exc),
                why="browse needs a Chrome DevTools page target to drive",
                fix=(
                    "set `cdp_url` on the action config to a page target "
                    "(ws://127.0.0.1:9222/devtools/page/…)"
                ),
                started=started,
                code="ERR_BROWSE_NO_TARGET",
            )
        except Exception as exc:
            return self._error(
                f"the browse session could not be opened: {exc}",
                why="connecting to the CDP page target failed",
                fix="check that the browser is running and the `cdp_url` is current",
                started=started,
                code="ERR_BROWSE_CONNECT_FAILED",
            )

        try:
            result = await run_browse_loop(
                goal=goal,
                start_url=start_url,
                session=session,
                page=page,
                decide=_decide,
                max_steps=max_steps,
                budget_check=_budget_check,
            )
        finally:
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    logger.debug("browse: session close failed", exc_info=True)

        return self._to_result(result, started=started)

    # ── plumbing ─────────────────────────────────────────────────────────────

    async def _open(
        self, action_config: dict[str, Any], ctx: ActionContext, *, cdp_url: str
    ) -> tuple[Any, Any, Any]:
        """Connect to the RESOLVED page target and wrap it in the gated session + driver.

        ``cdp_url`` arrives resolved (``browse.target.resolve_cdp_url``) rather than being read
        from ``action_config`` here: which browser a task drives is the ONE decision BA-7 owns,
        and a second read of the config key at the connect site is how a `user_browser` task
        would end up on the gateway's profile after all.
        """
        if not cdp_url:
            raise BrowseUnavailable("no `cdp_url` is configured, so there is no browser to drive")

        from personalclaw.browse.cdp import GatedCdpSession
        from personalclaw.browse.page import CdpPageDriver
        from personalclaw.browse.transport import WebSocketCdpTransport

        transport = await WebSocketCdpTransport.connect(cdp_url)
        raw_dir = str(action_config.get("screenshot_dir") or "").strip()
        driver = CdpPageDriver(transport, screenshot_dir=Path(raw_dir) if raw_dir else None)
        session = GatedCdpSession(
            transport,
            caller_identity=f"action:{PROVIDER_NAME}",
            source=str(getattr(ctx, "event", "") or "background"),
        )
        return session, driver, transport.close

    def _to_result(self, result: BrowseLoopResult, *, started: float) -> ActionResult:
        """Project the loop's account into the provider-agnostic ActionResult.

        A PARK is ``success=True`` with ``outcome="needs_input"``, not a failure. The run did
        real work and its notes are on stdout; reporting it failed would bury the notes under
        a red error and invite the retry machinery to start over from step 1 — paying for the
        whole task again to reach the same ceiling.
        """
        duration = int((time.monotonic() - started) * 1000)
        payload = json.dumps(result.to_payload())
        if not result.ok:
            return ActionResult(
                success=False,
                stdout=payload,
                error=result.error or result.park_detail or "the browse run failed",
                duration_ms=duration,
                agent_error=AgentError(
                    code="ERR_BROWSE_FAILED",
                    what=f"browse could not pursue {result.goal!r}",
                    why=result.error or result.park_detail or "the run ended without a result",
                    fix="check the start URL against the BROWSE egress policy, then re-run",
                ),
            )
        if result.parked:
            return ActionResult(
                success=True,
                stdout=payload,
                outcome=OUTCOME_NEEDS_INPUT,
                stderr=self._park_sentence(result),
                duration_ms=duration,
            )
        return ActionResult(success=True, stdout=payload, duration_ms=duration)

    @staticmethod
    def _park_sentence(result: BrowseLoopResult) -> str:
        """What the user reads on the parked run. A sentence, because this IS a UI surface."""
        if result.park_reason == PARK_STEP_EXHAUSTED:
            head = f"Browse stopped after {result.step_count} steps without finishing"
        elif result.park_reason == PARK_BUDGET_EXHAUSTED:
            head = "Browse stopped because the model budget is spent"
        else:
            head = f"Browse stopped early ({result.park_reason})"
        kept = f"{len(result.notes)} note(s) kept" if result.notes else "no notes recorded"
        return f"{head}; {kept}. Last page: {result.final_url or 'unknown'}."

    def _error(
        self,
        message: str,
        *,
        why: str,
        fix: str,
        started: float,
        code: str = "ERR_BROWSE_CONFIG",
    ) -> ActionResult:
        return ActionResult(
            success=False,
            error=message,
            duration_ms=int((time.monotonic() - started) * 1000),
            agent_error=AgentError(code=code, what=message, why=why, fix=fix),
        )


class BrowseUnavailable(RuntimeError):
    """There is no browser target to drive — a config gap, not a run failure."""


def create_provider(config: dict[str, Any] | None = None) -> BrowseActionProvider:
    """Factory the app manifest's ``implementation`` names (plan §9).

    ``config`` is the extension's own provider config, handed over by
    ``providers.registry.ActionProviderHandler.create``. Accepted and ignored: browse takes its
    arguments per invocation (a goal is not a setting), and a factory that refused the argument
    would leave the manifest permanently disabled — the shape the generated provider reference
    caught the first time this signature was wrong.
    """
    return BrowseActionProvider()
