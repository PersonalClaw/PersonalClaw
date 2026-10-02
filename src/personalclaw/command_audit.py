"""The record of a command refused before it ran, on a path that runs a command someone wrote.

A loop's or a workflow's check, a workflow step or teardown, a bash action and an app's setup hook
each ask the shell denylist (``security.denied_command``) before they run anything, and a refusal
there is written here: one audit row and one WARNING line, the same for every path, so Settings →
Audit log shows the refusal and the control that made it, never a run. A tool call's refusal is
recorded by the host that audits the call (``llm.events.TOOL_META_REFUSED_RULE``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from personalclaw.security import DeniedCommand, redact_for_display
from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)


def audit_command_refusal(
    command: str,
    refused: DeniedCommand | str,
    *,
    source: str,
    operation: str,
    control: str = "",
    metadata: dict | None = None,
) -> None:
    """One audit row and one WARNING line for a command refused before it ran: which path ran it,
    which control refused it and the rule that control applied, in the words the refusal gave.

    *refused* is the shell denylist's answer (:class:`~personalclaw.security.DeniedCommand`, whose
    pattern is the rule), or another control's sentence with *control* naming it
    (``sensitive_path``), the sentence then being the rule. The row is ``refused``, in the audit
    log's Denied family.

    Best-effort on purpose: an audit fault must never turn a refusal into a run. It is logged at
    WARNING rather than swallowed, so a control that stopped being recorded is visible. The
    command is the evidence, masked as any text a person reads is (``redact_for_display``): a
    command can carry a credential its run was handed, and the audit log is not a place to keep
    one.
    """
    if isinstance(refused, DeniedCommand):
        reason, control = refused.refusal(), "shell_denylist"
        rule = refused.pattern or refused.why()
    else:
        reason, rule = refused, refused
    rule = redact_for_display(rule)[:300]
    logger.warning("%s %s: refused by %s before it ran: %s", source, operation, control, rule)
    try:
        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="command_refused",
                caller_identity="",
                agent="personalclaw",
                source=source,
                operation=operation,
                tool_kind="execute_bash",
                outcome="refused",
                resources=redact_for_display(reason),
                metadata={
                    **(metadata or {}),
                    "control": control,
                    "rule": rule,
                    "command": redact_for_display(command)[:400],
                },
            )
        )
    except Exception:  # noqa: BLE001 - never let auditing decide whether a refusal holds
        logger.warning("a command was refused (%s) but its audit row failed", reason, exc_info=True)
