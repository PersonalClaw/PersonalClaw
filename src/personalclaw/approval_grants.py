"""Who may approve a call without asking anyone, decided when the call is made.

A tool call or a spawn that needs approval is settled one of two ways. A person ANSWERS it (Allow,
Deny, or nobody in time), or a GRANT approves it without asking: the chat's Trust, YOLO, "trust
reads", an agent's "Always allow", a spawn's own ``approval_mode: "auto"``, the Allow a trigger
that starts an agent was given, the owner's Approval mode "Auto", the operator's hook settings and
patterns, the gateway's ``--approval`` flag.
This module owns the three rules every grant is held to, because each was broken somewhere:

1. **It is read when the call is made.** A grant is the owner's setting as it is NOW. The subagent
   manager copied ``agent.approval_mode`` when the gateway started, and the hook settings were
   built into one object at startup that nothing reloaded, so a change in Settings reached the next
   call only after a restart — and a revoked grant kept approving, the direction a security
   setting must never fail in. The readers here load the setting per call.
2. **The operator ceiling bounds it.** ``governance/ceiling.json``'s ``approval`` scope is the
   operator's hard bound: ``ask`` says no run on this machine approves anything without a person,
   whatever a toggle, an agent profile, a spawn argument, a workflow node or a trigger's action
   says (``guardrails.policy.ceiling_permits_approval``). Only the subagent's tool-approval grant
   consulted it; the spawn gate, the chat, the gateway's relay and the runtime's own policy did
   not. :func:`stands` is the one question each of them asks now, and a refusal is audited, so a
   downgraded grant is never indistinguishable from one never asked for.
3. **What decided is written down.** A grant's name is a closed vocabulary (the constants below),
   so the audit row of every decision can say who decided it — a person, a named grant, or nobody —
   and an Inbox note a grant settled can say which one did.

The other way a call is settled, a person's answer, waits one window wherever it is asked
(:func:`approval_window_secs`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.hooks import HooksConfig

logger = logging.getLogger(__name__)

# ── Who decided (the `decided_by` of an audit row, and `refs.retry_by` on an Inbox note) ──────

#: A person answered: the owner, from a chat's card, the Inbox, Home, Mission Control, the phone
#: or a channel.
YOU = "you"
#: Nobody did: the approval's window closed, or the work that asked was stopped first.
NOBODY = "nobody"
#: The call's own declaration: its tool declares it only reads, and a read asks nobody. A
#: native runtime never asks about one; an ACP CLI asks the host about every call, so the host
#: approves one of these itself. Not a grant, so the operator ceiling does not bound it, just as
#: it does not make a native runtime ask about a read.
DECLARED_READ = "declared_read"
#: The call's own declaration that what it starts asks the owner itself
#: (``tool_providers.base.WORK_ASKS_META_KEY``): ``subagent_run``, whose one subagent's start, or
#: whose batch's one ask naming every task, is what asks her. A native runtime never asks about
#: such a call; an ACP CLI asks the host, so the host answers it itself. Not a grant, so the
#: operator ceiling does not bound it, as with a declared read: what the call starts is asked, or
#: started by a grant the ceiling bounds.
WORK_ASKS = "work_asks"

#: The chat's own Trust (its toggle, or "This chat" on an approval card).
TRUST = "trust"
#: YOLO: the chat pill, the Settings switch, a channel's ``!yolo``.
YOLO = "yolo"
#: "Trust reads": read-only tools only.
TRUST_READS = "trust_reads"
#: An agent's persisted "Always allow for this agent" (``AgentProfile.approval_mode = "auto"``).
AGENT_FLOOR = "agent_floor"
#: The chat that started a subagent is trusted.
PARENT_TRUST = "parent_trust"
#: The spawn's own ``approval_mode: "auto"``: an automation's action, an unattended run's stage.
APPROVAL_MODE = "approval_mode"
#: The owner allowed the trigger's action to run (the create dialog, the editor, the Triggers
#: page's switch or its Allow), and the action starts an agent: the agent starts on that yes. It
#: covers the start only; the agent's own calls ask as any agent's do
#: (`triggers.grants.allows_its_agent`).
TRIGGER = "trigger_grant"
#: The owner's Settings → Agent defaults → Approval mode "Auto", for an agent no chat started. The
#: mode ships asking, so this stands only once the owner has chosen it (:func:`setting_grant`).
SETTING = "setting"
#: ``hooks.auto_approve_subagent_spawn``, which starts a subagent and covers the start alone (the
#: agent's own calls are decided as any agent's are), or ``hooks.auto_approve_subagent_tools``,
#: which approves a subagent's own calls.
HOOK_SETTING = "hook_setting"
#: A pattern in ``hooks.auto_approve_tools``.
HOOK_PATTERN = "hook_pattern"
#: ``hooks.auto_approve_sources`` lists the asking source.
SOURCE = "source"
#: The gateway was started with ``--approval yolo`` or ``--approval reads``.
CLI = "cli"
#: An app's background task starting on the ``agent`` permission the owner agreed to at install
#: (`subagent.SubagentManager._spawn_grant`), and so does any agent that task starts: a subagent,
#: a batch of them (`workflows.batch_start`), the steps of a run it starts (`apps.app_work`). It
#: covers the start alone: no agent tier approves the task's calls, so each one that needs
#: approval asks.
APP = "app_grant"
#: A workflow step the owner chose "always allow" for earlier in the run.
REMEMBERED = "remembered"
#: The owner allowed this same start of a workflow step before the gateway restarted or the run
#: was paused, and the step is resuming the attempt that answer was for
#: (`workflows.models.NodeInstance.approved_request`). It covers that one start, within the step's
#: time limit; the agent's own calls ask as any agent's do.
APPROVED_BEFORE_RESUME = "approved_before_resume"
#: A subagent batch's start was allowed, once, for all its tasks (`workflows.batch_start`): by her
#: answer to its one ask, which named each task and what each may change, or, for a batch that only
#: reads, by the grant that starts its chat's subagents (an app's batch: :data:`APP`). Each task
#: starts on that, and asks nobody again. It covers the starts only; each task's own calls ask as
#: any agent's do.
BATCH_ALLOWED = "batch_allowed"
#: A workflow run's own gate policy for an origin nobody watches (a schedule, an event).
GATE_POLICY = "gate_policy"
#: An ACP agent's own permission mode that makes its CLI approve its own calls
#: (``bypassPermissions``, ``acceptEdits``…), forwarded for an unattended session
#: (``acp.permission_authority.sanitize_mode``).
ACP_MODE = "acp_mode"
#: A session's approval policy that tells its runtime not to ask at all (``auto``/``yolo``/
#: ``acceptEdits``), whichever of the grants above set it (``session._bounded_policy``).
SESSION_POLICY = "session_policy"
#: The triage digest running a trivial-tier proposal, or one a taught always-approve rule
#: matched, without asking (``proactive.autoexec``).
AUTO_EXECUTE = "auto_execute"
#: A subagent's result announced in the chat that started it: that turn's calls approve on their
#: own (``gateway.injection_approval_policy``).
INJECTION = "result_injection"
#: The gateway has nowhere to ask (no dashboard, no channel), so nobody can say yes: it names the
#: refusal of a call no grant covered, never an approval.
NO_SURFACE = "no_approval_surface"
#: The eval runner's own allowlist: its read-only tools, and file reads outside sensitive paths.
#: It also names the runner's refusal of every other call (`eval.runner`).
EVAL_SAFE_TOOLS = "eval_safe_tools"

#: The approval scale's levels a grant is checked at (`guardrails.registries.SCALE_APPROVAL`).
#: A blanket grant is ``auto``; a pattern the OPERATOR wrote into the hook settings is
#: ``hook_based`` ("a hook decides"), which a ``hook_based`` ceiling still permits and an ``ask``
#: one does not.
LEVEL_AUTO = "auto"
LEVEL_HOOK = "hook_based"


@dataclass(frozen=True)
class ToolDecision:
    """How an approval was settled: whether the call runs, what the audit row says, and who decided.

    ``outcome`` is one of ``approved``/``auto_approved``/``rejected``/``expired``/``cancelled``;
    ``decided_by`` is :data:`YOU` (a person answered), :data:`NOBODY` (the window closed, or the
    work stopped first) or a grant's name. Truthy exactly when the call may run, so a relay
    returning one is also the ``bool`` the approval callbacks have always returned. ``ended`` is
    why one nobody answered ended, in the words its surfaces say it ("nobody answered within 2
    hours"), when the relay knows; ``""`` otherwise.
    """

    approved: bool
    outcome: str
    decided_by: str
    ended: str = ""

    def __bool__(self) -> bool:
        return self.approved


def batch_allowed(parent_run: str) -> bool:
    """Whether the run a spawn belongs to (*parent_run*, a step's ``workflow:<run_id>``) is a
    subagent batch whose start was allowed (`workflows.batch_start.CONSENT_KEY`: her answer, or the
    grant that started a batch that only reads), so its task starts on that (:data:`BATCH_ALLOWED`).
    Fails closed: a record that cannot be read is no Allow."""
    from personalclaw.workflows import store
    from personalclaw.workflows.batch_start import CONSENT_KEY
    from personalclaw.workflows.models import OriginKind
    from personalclaw.workflows.ownership import OWNED_PREFIX

    if not parent_run.startswith(OWNED_PREFIX):
        return False
    try:
        run = store.get(parent_run[len(OWNED_PREFIX) :])
    except Exception:  # noqa: BLE001 - an unreadable record is no Allow
        logger.debug("run of %s unreadable for its batch's Allow", parent_run, exc_info=True)
        return False
    return bool(
        run is not None
        and run.origin.kind == OriginKind.SUBAGENT_TOOL
        and isinstance((run.extra or {}).get(CONSENT_KEY), dict)
    )


def declared_answer(event: object) -> str:
    """Who answers a call an agent CLI asks the host about, by what its tool declares, before any
    grant or person is consulted: :data:`DECLARED_READ` for a declared read, :data:`WORK_ASKS` for
    a call whose work asks the owner itself, ``""`` for every other call (it is asked, or a grant
    approves it). The answer a native runtime gives the same call by never asking about it; each
    gate asks it only past its refusals (task mode, deny-list, hooks, tool grants, a tier)."""
    from personalclaw.task_modes import declared_level

    if declared_level(getattr(event, "risk_level", "") or "") == "safe":
        return DECLARED_READ
    return WORK_ASKS if getattr(event, "work_asks", False) is True else ""


def decision_of(answer: object) -> ToolDecision:
    """An approval callback's answer as a :class:`ToolDecision`.

    A relay that knows how its approval ended returns one; a plain ``bool`` (a caller's own
    callback) is a person's answer and is recorded as that.
    """
    if isinstance(answer, ToolDecision):
        return answer
    ok = bool(answer)
    return ToolDecision(ok, "approved" if ok else "rejected", YOU)


def stands(
    grant: str,
    *,
    caller: str,
    subject: str = "",
    level: str = LEVEL_AUTO,
    audit: bool = True,
) -> bool:
    """Whether *grant* may approve without asking, under the operator ceiling in force.

    ``caller`` and ``subject`` go on the audit row of a refusal: who was asking (a session key, a
    subagent, a source) and what (the tool, the spawn). ``audit=False`` is for a caller that asks
    the same question more than once for one decision and audits it itself.

    Never raises: the ceiling is loaded (and validated) at gateway boot, so a read here is of the
    cached, valid bound. A failure to read it anyway refuses the grant — the approval is then asked
    for, which is the direction that cannot quietly approve.
    """
    try:
        from personalclaw.guardrails.policy import ceiling_permits_approval

        permitted = ceiling_permits_approval(level)
    except Exception:  # noqa: BLE001 - see the docstring: an unreadable bound refuses
        logger.warning("could not read the operator ceiling; refusing the %s grant", grant)
        permitted = False
    if permitted:
        return True
    if audit:
        refused(grant, caller=caller, subject=subject)
    return False


def stands_for_call(
    grant: str, *, session_key: str, event: object, level: str = LEVEL_AUTO
) -> bool:
    """Whether *grant* may approve one call, the one *event* asks about in the session
    *session_key*, without asking anyone: :func:`stands`, for that call.

    No grant answers a call that reaches a host off the allowed hosts (``run_bounds``): that one is
    put to a person. A refusal by the ceiling is audited, naming the call. A refused grant falls
    through to what comes next: the call asks, or on an unattended turn is declined because nobody
    can answer it. What a chat's runner asks of an operator's hook pattern and of its Trust, YOLO
    and Trust reads, and what a channel running a conversation itself asks of the same grants
    (``chat_trust.chat_grant``).
    """
    from personalclaw.run_bounds import off_list
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    if off_list(event, session_key):
        return False
    title, _ = redact_exfiltration_urls(str(getattr(event, "title", "") or ""))
    title, _ = redact_credentials(title)
    return stands(grant, caller=session_key, subject=f"tool={title[:80]}", level=level)


def refusal_sentence() -> str:
    """What a person is told when they ask for a standing grant the ceiling refuses.

    Says where the bound lives and what changes it: the file, then a restart, since the ceiling is
    read once at boot and a running gateway never widens (`guardrails.ceiling`).
    """
    try:
        from personalclaw.guardrails.ceiling import active_ceiling, ceiling_path

        ceiling = active_ceiling()
        control = ceiling.control("approval")
        where = ceiling.source or str(ceiling_path())
        bound = f'"approval": "{getattr(control, "value", "ask")}" in {where}'
    except Exception:  # noqa: BLE001 - the sentence must not fail the refusal it explains
        bound = "governance/ceiling.json"
    return (
        f"The operator ceiling ({bound}) says tool calls on this machine ask for a person, so "
        "this can't approve them on its own. Answer each call as it asks, or change that file "
        "and restart PersonalClaw."
    )


def refused(grant: str, *, caller: str, subject: str = "") -> None:
    """Audit a grant the operator ceiling refused (best-effort, like every audit write here)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=caller or "approval",
            operation="approval.grant_refused",
            outcome="blocked",
            source="guardrails",
            resources=f"grant={grant},refused_by=governance_ceiling"
            + (f",{subject[:120]}" if subject else ""),
        )
    except Exception:  # noqa: BLE001 - an audit write never decides an approval
        logger.warning("SEL audit failed for a refused %s grant", grant, exc_info=True)


# ── The settings, as they are now ───────────────────────────────────────────────────────────


def approval_mode_now() -> str:
    """``agent.approval_mode`` as it reads now. An unreadable config reads as asking (``""``).

    What a chat's own floor reads (``chat_runner._apply_approval_floor``: "trust_reads" lets a
    chat run a read-only shell command unasked) and what a surface describes. It is never read as
    the grant of an agent no chat started: that is :func:`setting_grant`, and only it.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return str(AppConfig.load().agent.approval_mode or "")
    except Exception:  # noqa: BLE001 - fail toward asking
        logger.debug("could not read agent.approval_mode; asking", exc_info=True)
        return ""


def setting_grant() -> str:
    """:data:`SETTING` when the owner's Approval mode is "auto" now, else ``""`` (the agent asks).

    THE ONE PLACE the global setting is read as a grant for an agent no chat started: a trigger's
    Invoke Agent agent whose step does not set its own approval, a subagent started outside a
    chat. Every other unattended run gets its approval from the consent given for that run alone
    (the step's own ``approval_mode``, the Allow its trigger was given, the Mode its loop was
    started under, a workflow run's own unattended grant), never from here. The mode ships asking
    (``config.loader.AgentConfig``), so this stands only for an owner who chose "auto", with the
    consent its loosening asks (``config.editable``). Not checked against the operator ceiling:
    each caller asks :func:`stands`, as it does of every grant.
    """
    return SETTING if approval_mode_now() == "auto" else ""


def setting_sentence() -> str:
    """What the owner's Approval mode does now for an agent no chat started, in plain words: the
    Doctor's row and ``personalclaw doctor`` say this, and Settings → Agent defaults says the same.
    """
    mode = approval_mode_now()
    label = _MODE_LABELS.get(mode, _MODE_LABELS["interactive"])
    if setting_grant():
        if stands(SETTING, caller="doctor", audit=False):
            return (
                "Approval mode is Auto: an agent no chat started (a trigger's Invoke Agent agent, "
                "a subagent started outside a chat) approves every tool call it makes, file "
                "changes and shell commands included, without asking you."
            )
        return (
            "Approval mode is Auto, but the operator ceiling says every call asks: an agent no "
            "chat started asks you in your Inbox before each call that needs approval."
        )
    return (
        f"Approval mode is {label}: an agent no chat started (a trigger's Invoke Agent agent, a "
        "subagent started outside a chat) asks you in your Inbox before each call that needs "
        "approval, unless its automation or loop was allowed to run on its own."
    )


#: Each value as Settings → Agent defaults → Approval mode names it.
_MODE_LABELS = {"interactive": "Ask each time", "trust_reads": "Trust reads", "auto": "Auto"}


def approval_window_secs() -> float:
    """How long an ASKED approval waits for a person: ``agent.approval_timeout_minutes``, now.

    ONE window for every approval that waits — a chat's, a subagent's, a workflow gate's, an MCP
    server's question (which its own call ceiling cuts shorter). Read per approval, so a change in
    Settings applies to the next one asked. An unreadable config falls back to the default window
    rather than failing the approval.
    """
    from personalclaw.config.loader import APPROVAL_TIMEOUT_MINUTES_DEFAULT, AppConfig

    try:
        minutes = int(AppConfig.load().agent.approval_timeout_minutes)
    except Exception:  # noqa: BLE001 - see the docstring
        minutes = APPROVAL_TIMEOUT_MINUTES_DEFAULT
    return float(max(1, minutes) * 60)


def hooks_now() -> "HooksConfig":
    """The hook settings (``config.hooks``) as they read now.

    ``AppConfig.load`` already resolves an unreadable file to its most restrictive values, so what
    can still fail here is a hook value that does not parse, and that raises: every approval that
    reads it then asks, rather than falling back to settings nobody wrote.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.hooks import HooksConfig

    return HooksConfig.from_dict(AppConfig.load().hooks or {})
