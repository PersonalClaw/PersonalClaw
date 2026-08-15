"""Proactive triage — the scheduled digest and its approval memory.

PA-1 ships the pure, engine-independent half: the approval-rule model, the
deterministic matcher the triage stage consults, the reply grammar the digest
thread parses, and the escalating suppression cooldowns. Nothing here touches
the network, the clock (every function takes ``now``), or an LLM.
"""

from personalclaw.proactive.approval import (
    APPROVAL_KEY_PREFIX,
    COOLDOWN_LADDER_SECONDS,
    HELP_TEXT,
    ApprovalRule,
    Decision,
    MatchResult,
    ParsedReply,
    ReplyAction,
    SuppressionState,
    Verdict,
    clear_suppression,
    escalate_suppression,
    match_rules,
    parse_reply,
    rule_from_row,
    rule_key,
    rule_matches,
    rule_to_value,
    rules_from_rows,
    suppression_active,
)

__all__ = [
    "APPROVAL_KEY_PREFIX",
    "COOLDOWN_LADDER_SECONDS",
    "HELP_TEXT",
    "ApprovalRule",
    "Decision",
    "MatchResult",
    "ParsedReply",
    "ReplyAction",
    "SuppressionState",
    "Verdict",
    "clear_suppression",
    "escalate_suppression",
    "match_rules",
    "parse_reply",
    "rule_from_row",
    "rule_key",
    "rule_matches",
    "rule_to_value",
    "rules_from_rows",
    "suppression_active",
]
