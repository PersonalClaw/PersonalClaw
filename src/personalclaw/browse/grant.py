"""Per-task browser-grant flow — BA-9 (plan §(c): per-task authorization IS the security control).

The ``user_browser`` target (BA-7) drives the operator's OWN, already-logged-in browser, so its
authorization is NOT the earned-autonomy ladder (which governs unattended trust) but a fresh,
explicit, per-TASK grant a human answers while watching. This module owns that grant lifecycle
end to end:

* :func:`request_grant` — one task, one authorization, routed through the SHIPPED fail-closed
  :class:`~personalclaw.agents.native.approval.ApprovalGate`. No answer in :data:`GRANT_TIMEOUT`
  → REJECT; a gate that is absent, or that raises, → REJECT. It NEVER fails open: a
  ``user_browser`` run that cannot prove a human authorized it does not touch the browser.
* :func:`task_group_name` — the run's tabs live in a group named after the task, so the human sees
  WHAT they authorized and can click in to take over. Core owns only the NAME and the close
  CONTRACT; the tabs and the ``close`` verb live in the BA-8 extension (apps repo).
* :func:`make_close_check` — closing that tab group is a HARD STOP, OBSERVED not requested. The
  extension turns a close into a connector disconnect (or a re-attach as a different session);
  this returns ``(True, reason)`` the moment the live connector is no longer the one the grant was
  bound to, so :func:`~personalclaw.browse.loop.run_browse_loop` parks within one step — the same
  per-step seam BA-5's kill switch uses. **Distinct from** :mod:`personalclaw.browse.killswitch`:
  that flag stops ALL unattended browse; this ends ONE attended run when its own tab closes.
* SEL audit — ``browser_grant`` at grant/deny, ``browser_revoked`` at run-end / close / kill. The
  row carries the task label, the site scope (hostnames), and a reason ONLY; NEVER a credential,
  cookie, or session token (§5.2's no-credential invariant, restated for the audit trail).

**Why a module-level gate rather than a per-session one.** A grant is keyed by a unique request
id, and one :class:`ApprovalGate` serves many concurrent request ids by construction. The browse
grant channel is therefore ONE process-global gate — the same "a live attachment is a process
property" reasoning :mod:`personalclaw.browse.target` uses for the connector — and
:func:`approve_grant` / :func:`reject_grant` resolve a specific pending request on it.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from personalclaw.agents.native.approval import APPROVE, REJECT, ApprovalGate
from personalclaw.errors import AgentError

logger = logging.getLogger(__name__)

#: The fail-closed ceiling: a grant with no human answer in this long is REJECTED (plan §(c), the
#: ApprovalGate's own 300s default, named once here so the posture reads it in one place).
GRANT_TIMEOUT = 300.0

#: SEL rows this module writes — via ``log_api_access(source="browse")``, matching the style
#: ``killswitch`` and ``browse_connector`` already use so browse audit reads one way.
SEL_SOURCE = "browse"
SEL_OP_GRANT = "browser_grant"
SEL_OP_REVOKE = "browser_revoked"

#: SEL outcome words — every value is one the audit-log vocabulary ALREADY classifies
#: (``sel.AUDIT_OUTCOME_SUCCESS`` / ``AUDIT_OUTCOME_FAMILIES``): ``granted`` and ``ok`` are success
#: words, ``rejected`` is a "denied" family word. So a browse grant row is filterable on the audit
#: surface and ``test_audit_outcome_families``'s unclassified-remainder ceiling does not move.
_OUTCOME_GRANTED = "granted"
_OUTCOME_REJECTED = "rejected"
_OUTCOME_REVOKED = "ok"

#: Distinguishes "caller passed no gate, use the channel" from "caller passed None, meaning there
#: is no approval channel" — the second is a fail-closed REJECT, and a plain ``None`` default could
#: not tell them apart.
_MISSING = object()


# ── the browse grant channel: one process-global gate, many request ids ──────────
_gate = ApprovalGate()
#: request_id → the scope-naming metadata a pending grant carries, so an approval surface can show
#: the human WHICH task and WHICH sites before they answer. Populated for the duration of a wait.
_pending: dict[str, dict[str, object]] = {}


def grant_gate() -> ApprovalGate:
    """The process-global browse grant gate. One gate serves many concurrent grants, each keyed by
    its own request id (the :class:`ApprovalGate` contract), so this needs no per-session scoping.
    """
    return _gate


def approve_grant(request_id: str) -> bool:
    """Resolve a pending grant as APPROVED — the seam a human's "allow this task" answer calls (the
    dashboard browser-control prompt / VB). Returns ``False`` if nothing was waiting on that id."""
    return _gate.approve(str(request_id))


def reject_grant(request_id: str) -> bool:
    """Resolve a pending grant as REJECTED. Returns ``False`` if nothing was waiting on that id."""
    return _gate.reject(str(request_id))


def pending_grants() -> list[dict[str, object]]:
    """The grants awaiting a human answer right now — ``{request_id, task, scope, group}`` each, so
    an approval surface can render the scope before the operator decides. A snapshot copy: a caller
    iterating it cannot be tripped by a concurrent grant resolving."""
    return [dict(meta) for meta in _pending.values()]


def scope_for_url(url: str) -> tuple[str, ...]:
    """The site scope a grant names — the host(s) the task intends to touch (plan §(c).2).

    Derived from the start URL's host so the human reviews a concrete site before granting. A
    hostname ONLY — never a path, query, or fragment: those can carry a token, and the grant is
    SEL-audited, so the no-credential invariant reaches the scope string too."""
    try:
        host = (urlsplit(url).hostname or "").strip().lower()
    except (ValueError, TypeError):
        host = ""
    return (host,) if host else ()


def task_group_name(task: str) -> str:
    """The tab-group name the run's tabs live under — named after the task so the human can see what
    they authorized (plan §(c).3). Collapsed whitespace, trimmed to a short scannable label."""
    label = " ".join((task or "").split())
    if len(label) > 80:
        label = label[:79] + "…"
    return label or "browse task"


@dataclass(frozen=True)
class BrowserGrant:
    """The outcome of one per-task authorization, and the binding a close-check reads."""

    task: str
    scope: tuple[str, ...]
    group_name: str
    request_id: str
    granted: bool
    #: WHEN it was granted (``time.monotonic()``), or ``None`` when it never was — deliberately NOT
    #: a ``0.0`` sentinel: a rejected grant has no grant instant, and ``0.0`` is a real monotonic
    #: reading a ``> 0`` test would misread as "granted". Readers test ``granted`` /
    #: ``granted_at is not None``, never a numeric floor.
    granted_at: float | None = None
    reason: str = ""
    #: The connector identity the grant is bound to, captured at grant time so close-to-kill can
    #: tell "the tab the user authorized" from a later re-attach. Empty for a rejected grant.
    bound_device_id: str = ""
    bound_cdp_url: str = ""


async def request_grant(
    *,
    task: str,
    scope: tuple[str, ...] = (),
    gate: object = _MISSING,
    request_id: str | None = None,
    timeout: float = GRANT_TIMEOUT,
    bound_device_id: str = "",
    bound_cdp_url: str = "",
) -> BrowserGrant:
    """Ask a human to authorize ONE ``user_browser`` task — fail-closed.

    Routed through the shipped :class:`ApprovalGate` (``timeout`` seconds → REJECT). REJECTS — and
    so the run never starts — when the gate is absent (``gate=None``), the answer is REJECT, the
    wait times out, or ANYTHING raises: a run that cannot prove a human authorized it does not
    touch the operator's browser. Emits a ``browser_grant`` SEL row either way.

    ``gate`` omitted uses the process-global channel (:func:`grant_gate`); pass an explicit gate to
    inject one, or an explicit ``None`` to model "no approval channel available".
    """
    rid = str(request_id or uuid.uuid4().hex[:16])
    group = task_group_name(task)
    scope = tuple(scope)
    the_gate = _gate if gate is _MISSING else gate

    decision = REJECT
    reason = ""
    _pending[rid] = {"request_id": rid, "task": group, "scope": list(scope), "group": group}
    try:
        if the_gate is None:
            reason = "no approval channel is available to authorize this task"
        elif not isinstance(the_gate, ApprovalGate):
            # A gate of the wrong shape is a wiring bug — treated as no channel, never as approval.
            reason = "the approval channel is misconfigured"
        else:
            decision = await the_gate.request(rid, timeout=timeout)
            if decision != APPROVE:
                reason = f"the grant was not approved within {int(timeout)}s"
    except Exception:
        # Fail-closed: ANY gate failure is a REJECT, never an open door.
        logger.debug("browse grant: gate failed → reject", exc_info=True)
        decision = REJECT
        reason = "the approval gate failed, so the task was refused"
    finally:
        _pending.pop(rid, None)

    granted = decision == APPROVE
    grant = BrowserGrant(
        task=task,
        scope=scope,
        group_name=group,
        request_id=rid,
        granted=granted,
        granted_at=time.monotonic() if granted else None,
        reason="" if granted else reason,
        bound_device_id=bound_device_id if granted else "",
        bound_cdp_url=bound_cdp_url if granted else "",
    )
    _audit(SEL_OP_GRANT, _OUTCOME_GRANTED if granted else _OUTCOME_REJECTED, grant=grant)
    return grant


def revoke_grant(grant: BrowserGrant, *, reason: str) -> None:
    """Record that a granted task's authorization has ended — at run completion, at close-to-kill,
    or at a kill-switch stop. Emits ``browser_revoked``.

    A no-op (no row) for a grant that was never granted: there is nothing to revoke, and a revoked
    row for a task that never ran would be a false entry in the audit trail."""
    if not grant.granted:
        return
    _audit(SEL_OP_REVOKE, _OUTCOME_REVOKED, grant=grant, reason=reason)


def make_close_check(grant: BrowserGrant, *, status_reader=None):
    """A per-step "has the user closed this task's tab group?" check for the browse loop.

    Bound to the connector the grant was authorized against. Closing the task tab group is a HARD
    STOP the extension turns into a connector disconnect (or a re-attach as a different session);
    this returns ``(True, reason)`` as soon as the live connector is no longer the one the grant
    bound to, so :func:`~personalclaw.browse.loop.run_browse_loop` parks within one step. Reads the
    live connector through :func:`~personalclaw.browse.target.connector_status`; ``status_reader``
    injects one for tests.
    """
    from personalclaw.browse.target import connector_status

    read = status_reader or connector_status

    def _check() -> tuple[bool, str]:
        try:
            st = read()
        except Exception:
            # Fail TOWARD stop: if we cannot confirm the authorized tab is still open, end the run
            # rather than keep driving a browser we can no longer see. The OPPOSITE of the kill
            # switch's fail-open default, on purpose — this control guards a live logged-in session.
            logger.debug("browse grant: connector unreadable → close-to-kill", exc_info=True)
            return True, "the browser connection could not be read; ending the run"
        if not getattr(st, "connected", False):
            return True, "the task tab group was closed"
        if grant.bound_cdp_url and getattr(st, "cdp_url", "") != grant.bound_cdp_url:
            return True, "the browser was reconnected as a different session"
        if grant.bound_device_id and getattr(st, "device_id", "") != grant.bound_device_id:
            return True, "the browser was reconnected as a different device"
        return False, ""

    return _check


def grant_denied_error(grant: BrowserGrant) -> AgentError:
    """The typed WHAT/WHY/FIX a provider returns when a ``user_browser`` task was not authorized —
    refused, not failed, and above all NOT started."""
    return AgentError(
        code="ERR_BROWSE_GRANT_DENIED",
        what="the browse task was not authorized, so it did not start",
        why=(
            grant.reason
            or "the user_browser target requires a fresh per-task grant, and one was not given"
        ),
        fix=(
            "run the task again and approve the browser-control prompt when it appears — it names "
            "the sites the task will touch; a grant left unanswered for 5 minutes is refused"
        ),
    )


def _audit(operation: str, outcome: str, *, grant: BrowserGrant, reason: str = "") -> None:
    """One SEL row per grant/revoke. Task label + scope hosts + reason ONLY — NEVER a credential,
    cookie, or token (§5.2). Never raises: an audit failure must not break a run (killswitch style).
    """
    try:
        from personalclaw.sel import sel

        parts = [f"task={grant.group_name}"]
        if grant.scope:
            parts.append("scope=" + ",".join(grant.scope))
        detail = reason or grant.reason
        if detail:
            parts.append("reason=" + detail)
        sel().log_api_access(
            caller="browse",
            operation=operation,
            outcome=outcome,
            source=SEL_SOURCE,
            resources="; ".join(parts)[:400],
        )
    except Exception:
        logger.debug("browse grant SEL audit failed for %s", operation, exc_info=True)
