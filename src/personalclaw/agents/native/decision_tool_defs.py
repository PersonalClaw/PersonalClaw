"""The decision-journal tool schemas, kept out of :mod:`builtin_tools`.

Extracted at integration for a structural reason, not a stylistic one.
``tests/test_structural_baseline.py::test_the_watch_band_is_not_sitting_on_a_cliff``
demands >=100 lines of headroom below the 2800-line watch band, and PA-4's three tools
pushed ``builtin_tools.py`` to 2701 — headroom 99, red. That rail's own docstring records
this same file already forcing the band from 2500 to 2800, and its instruction is to
"Split that file now ... Do NOT silently widen it."

So the three ``ToolDefinition``s live here. Nothing about the tools changed: the category
mapping, the dispatch methods and the call-site rails all stay in ``builtin_tools``, and
this module only owns their schemas. The domain vocabulary is still read from
:mod:`personalclaw.decisions` rather than spelled out, so the tool cannot advertise a
domain that module rejects.
"""

from __future__ import annotations

from typing import Any

from personalclaw.tool_providers.base import RiskLevel, ToolDefinition


def _decision_domains() -> tuple[str, ...]:
    """Read the domain vocabulary from the module that OWNS it (lazily, as before)."""
    from personalclaw.decisions import DECISION_DOMAINS

    return DECISION_DOMAINS


def _decision_grades() -> tuple[str, ...]:
    """The resolution-grade vocabulary, read from the module that OWNS it."""
    from personalclaw.decisions import RESOLUTION_GRADES

    return RESOLUTION_GRADES


def decision_tool_definitions(provider: str, s: dict[str, Any]) -> list[ToolDefinition]:
    """The three decision-journal tools, in the order ``builtin_tools`` listed them."""
    return [
        # ── Decision journal (PROACTIVE-ASSISTANT §2.2) ──
        # Beside the knowledge tools and in the SAME category, because a decision IS a
        # knowledge item — a separate app would make the journal removable independently
        # of the library its entries live in.
        ToolDefinition(
            name="log_decision",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Record a decision the user is making, with the prediction they expect, "
                "and schedule ONE review at its horizon. Offer this when you notice a "
                "decision being made — never log one silently. Args: summary (str, "
                "required — the decision in one line), expectation (str, required — what "
                "the user predicts will happen), confidence (number 0-1, required), "
                f"domain ({'|'.join(_decision_domains())}, default 'other'), "
                "content (str — the reasoning, context and stakes, free prose), "
                "review_horizon (str YYYY-MM-DD — defaults to the configured horizon), "
                "tags (list of str)."
            ),
            parameters={
                **s,
                "properties": {
                    "summary": {"type": "string"},
                    "expectation": {"type": "string"},
                    "confidence": {"type": "number"},
                    "domain": {"type": "string", "enum": list(_decision_domains())},
                    "content": {"type": "string"},
                    "review_horizon": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "expectation", "confidence"],
            },
        ),
        ToolDefinition(
            name="decision_list",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "List the user's logged decisions. Args: status "
                "('pending'|'resolved'|'abandoned'|'overdue' — 'overdue' means pending "
                "past its review horizon), domain (str), limit (int, default 25)."
            ),
            parameters={
                **s,
                "properties": {
                    "status": {"type": "string"},
                    "domain": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        ),
        ToolDefinition(
            name="decision_resolve",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Capture what actually happened for a logged decision. Writes the "
                "expectation-vs-outcome lesson to memory. Args: id (str, required), "
                "outcome (str, required — what actually happened, in the user's own "
                f"words), grade ({'|'.join(_decision_grades())}, required; 'too_early' "
                "defers the review instead of resolving it). Never invent an outcome — "
                "ask the user."
            ),
            parameters={
                **s,
                "properties": {
                    "id": {"type": "string"},
                    "outcome": {"type": "string"},
                    "grade": {"type": "string", "enum": list(_decision_grades())},
                },
                "required": ["id", "outcome", "grade"],
            },
        ),
    ]
