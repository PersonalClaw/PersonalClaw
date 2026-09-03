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

**The credential handoff** (BA-4, plan §5). Three things happen here and nowhere else:

* **§5.3, before a model call is spent.** :func:`~personalclaw.browse.handoff.session_state` is
  consulted on ``start_url``. A site whose recorded session has gone stale parks IMMEDIATELY, which
  is the whole value of a pre-run check — parking on step 14 wastes thirteen steps the user paid
  for. A site with no profile at all does NOT park unless ``start_url`` is itself a sign-in page:
  "we have never logged in here" is the normal state of every public page on the web, and parking on
  it would make the handoff fire on every run.
* **The park routes through the SHIPPED needs-input gate.** A ``login_required`` park is a park:
  ``_to_result`` already maps every park to ``outcome="needs_input"``, which the engine's
  action-node dispatch maps to WAITING and ``workflows/attention.py`` projects into the inbox.
  BA-4 adds a reason and a card, not a second park/resume.
* **"Authenticated" is OBSERVED, never asserted.** :func:`~personalclaw.browse.handoff.record_login`
  is called when a run that started without a fresh session COMPLETES — that is plan §5.2's own
  wording for the invariant ("it only knows 'I am now authenticated' by observing that the
  post-login page contains the expected content"). It is also what makes the second run cheap:
  run 1 records the session, run 2 reads ``fresh`` and never asks the human again.

**Browser lifecycle is still NOT here**, and BA-4 does not change that. ``browse/transport.py``
records the decision: core does not discover Chrome, does not own a ``--user-data-dir`` process and
does not supervise one. So the handoff hands the caller the exact argv that binds a headful window
to the site's persistent profile (:func:`~personalclaw.browse.handoff.chrome_launch_args`) instead
of launching it — which keeps the profile choice unforgeable (a caller cannot accidentally open the
login window against a different profile than the run will read) while leaving the process where it
already lives. Absent a target the provider returns a typed, actionable failure rather than
pretending to browse — an action that silently no-ops is worse than one that says it cannot run.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.browse.handoff import PARK_LOGIN_REQUIRED
from personalclaw.browse.loop import (
    MAX_STEPS_DEFAULT,
    PARK_BUDGET_EXHAUSTED,
    PARK_KILLED,
    PARK_STEP_EXHAUSTED,
    BrowseLoopResult,
    BrowseStep,
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


def _kill_check() -> tuple[bool, str]:
    """The browse kill-switch verdict, consulted before every model call in the loop (BA-5).

    Never raises — an unreadable flag answers "not killed" (the switch is opt-in; see
    :func:`personalclaw.browse.killswitch.get_kill`), so a bookkeeping hiccup cannot halt browse
    on its own. The flag itself is the deliberate control.
    """
    try:
        from personalclaw.browse.killswitch import get_kill

        st = get_kill()
        return st.active, st.reason
    except Exception:
        logger.debug("browse: kill verdict unavailable", exc_info=True)
        return False, ""


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

        from personalclaw.browse.killswitch import browse_killed, get_kill

        if browse_killed():
            # Refused, not failed — the same posture as the incident check above: a human pulled
            # the browse kill switch, and a retry loop against it would be a storm against a control
            # they deliberately engaged. Distinct from incident: this stops ONLY browse.
            kill = get_kill()
            return ActionResult(
                success=False,
                error="the browse kill switch is engaged — unattended browsing is stopped",
                duration_ms=int((time.monotonic() - started) * 1000),
                agent_error=AgentError(
                    code="ERR_BROWSE_KILLED",
                    what="browse refused to start because the kill switch is engaged",
                    why=kill.reason or "a human stopped unattended browsing from the mirror panel",
                    fix="release the kill switch (the browse mirror's Resume) then re-run",
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

        # ── BA-4 §5.3: the pre-run session check, before a browser or a token is spent ──
        from personalclaw.browse.handoff import (
            REASON_NO_SESSION,
            REASON_SESSION_EXPIRED,
            SESSION_ABSENT,
            SESSION_EXPIRED,
            SESSION_FRESH,
            ensure_profile,
            looks_like_login_url,
            session_state,
        )

        state_before = session_state(start_url)
        if state_before != SESSION_FRESH and (
            state_before != SESSION_ABSENT or looks_like_login_url(start_url)
        ):
            reason = (
                REASON_SESSION_EXPIRED if state_before == SESSION_EXPIRED else REASON_NO_SESSION
            )
            return self._login_park(start_url, reason=reason, ctx=ctx, started=started)
        # Create the profile directory before the run rather than after, so a run that authenticates
        # mid-flight has somewhere to persist the session it just earned.
        ensure_profile(start_url)

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
                on_step=self._mirror_sink(ctx),
                kill_check=_kill_check,
            )
        finally:
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    logger.debug("browse: session close failed", exc_info=True)

        return self._to_result(
            result, started=started, ctx=ctx, start_url=start_url, session_before=state_before
        )

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _mirror_sink(self, ctx: ActionContext) -> Callable[[BrowseStep, str], None]:
        """A per-step sink that relays each completed step to the live mirror (BA-5).

        Bound to this run's ``run_id`` (from the structured event payload) so a watcher can tell
        concurrent browse runs apart. It only RELAYS what the loop already produced — the SCREENED
        URL, the rendered action line (a credential ``TYPE`` is already ``[withheld]`` by the loop),
        and the screenshot PATH — so the mirror exposes nothing the run did not already record, and
        the seam swallows a relay failure so watching a run can never break it.
        """
        run_id = str((getattr(ctx, "payload", None) or {}).get("run_id") or "")

        def _sink(step: BrowseStep, screenshot: str) -> None:
            from personalclaw.browse.mirror import broadcast_browse_step

            broadcast_browse_step(
                {
                    "run_id": run_id,
                    "step_n": step.index,
                    "url": step.url,
                    "action": step.action,
                    "screenshot": screenshot,
                    "note": step.note,
                }
            )

        return _sink

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

    # ── BA-4: the credential handoff ─────────────────────────────────────────

    def _login_park(
        self, url: str, *, reason: str, ctx: ActionContext, started: float
    ) -> ActionResult:
        """Park on the needs-input gate because a HUMAN must authenticate (plan §5.2).

        ``success=True`` with ``outcome="needs_input"``, exactly like every other park: a login wall
        is not a failure, and reporting one would invite the retry machinery to re-run the task
        against a wall that will still be there.

        Also writes ``auth_state=expired`` into the profile's ``.meta.json``. That is the state BA-5
        renders a persistent banner from, and writing it at the moment the wall is OBSERVED is what
        makes that atom a rendering job rather than a re-derivation.
        """
        from personalclaw.browse.handoff import (
            REASON_SESSION_EXPIRED,
            chrome_launch_args,
            mark_expired,
            request_login,
        )

        # `run_id` from the structured event payload — `ActionContext` has no such attribute, and
        # `payload` is where the dataclass docstring says structured event data lives. Empty when
        # nothing supplied one, which the needs-input card tolerates: an unbound card is answerable
        # from any surface, the correct posture for a run the user started themselves.
        handoff = request_login(
            url,
            reason=reason,
            run_id=str((getattr(ctx, "payload", None) or {}).get("run_id") or ""),
            node_id=PROVIDER_NAME,
        )
        if reason == REASON_SESSION_EXPIRED:
            mark_expired(url)
            # BA-5 §(c): the moment auth_state=expired is written, SURFACE it — a persistent banner
            # and a needs_input inbox item — so an expired session is visible whether or not this
            # run was dispatched through the workflow engine's own attention projection (a schedule
            # tick, a hook, a manual run never touch that path). Best-effort inside the seam.
            from personalclaw.browse.mirror import surface_auth_expired

            surface_auth_expired(url)
        payload = handoff.to_payload()
        # The argv the caller needs to open the headful window on the RIGHT profile. Handed over
        # rather than executed — see the module docstring on why core does not launch Chrome.
        payload["headful_launch_args"] = chrome_launch_args(url, headful=True)
        return ActionResult(
            success=True,
            outcome=OUTCOME_NEEDS_INPUT,
            stdout=json.dumps(payload),
            stderr=handoff.sentence,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _to_result(
        self,
        result: BrowseLoopResult,
        *,
        started: float,
        ctx: ActionContext,
        start_url: str = "",
        session_before: str = "",
    ) -> ActionResult:
        """Project the loop's account into the provider-agnostic ActionResult.

        A PARK is ``success=True`` with ``outcome="needs_input"``, not a failure. The run did
        real work and its notes are on stdout; reporting it failed would bury the notes under
        a red error and invite the retry machinery to start over from step 1 — paying for the
        whole task again to reach the same ceiling.
        """
        from personalclaw.browse.handoff import (
            REASON_CREDENTIAL_FIELD,
            SESSION_FRESH,
            record_login,
        )

        if result.parked and result.park_reason == PARK_LOGIN_REQUIRED:
            # The loop hit the wall MID-RUN (the agent tried to type into a credential field). Same
            # card, same gate, same sentence as the §5.3 pre-run park: one handoff, two triggers.
            return self._login_park(
                start_url or result.final_url,
                reason=REASON_CREDENTIAL_FIELD,
                ctx=ctx,
                started=started,
            )
        if result.ok and not result.parked and start_url and session_before != SESSION_FRESH:
            # 🔴 §5.2's own definition of "authenticated": the run completed against a site whose
            # session was not known-good when it started, so the session on disk WORKS. Recorded
            # here and only here — a `record_login` the provider called unconditionally would claim
            # a session for every public page, and one nobody called at all would leave the profile
            # permanently stale and re-prompt the user on every run.
            try:
                record_login(start_url)
            except Exception:
                logger.debug("browse: could not record the login", exc_info=True)
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
        elif result.park_reason == PARK_KILLED:
            head = "Browse was stopped by the kill switch"
        elif result.park_reason == PARK_LOGIN_REQUIRED:
            # Unreachable via this method today — a login park is answered by `_login_park`, whose
            # sentence names the site and the handoff. Kept because `_park_sentence` is the
            # exhaustive projection of the park vocabulary, and the `else` branch below would print
            # the raw reason code ("Browse stopped early (login_required)") to a user if a later
            # caller reached here first. A park reason with no sentence is a leaked identifier on a
            # product surface.
            head = "Browse stopped because the site needs you to sign in"
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
