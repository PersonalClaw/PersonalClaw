"""What an agent CLI says one turn of its session cost, read off ACP's own reports.

ACP has an agent say what its session's work has cost so far on its usage update (a
``session/update`` whose ``sessionUpdate`` is ``usage_update``: ``cost`` ``{amount, currency}``,
the session's running total) and, in the protocol's unstable part, its token totals on its answer
to a prompt (``PromptResponse.usage``: ``inputTokens``, ``outputTokens``, ``cachedReadTokens``,
``cachedWriteTokens``, across the session's turns). Core read only the usage update's context
window, so an agent CLI's turn came to its ``EVENT_COMPLETE`` with no cost and no tokens: no Usage
row was written for it, the day's meter charged it nothing on a metered axis, and the usage page
never showed it.

A turn's own is what the totals grew by while it ran: a turn the agent said nothing of adds
nothing, which its event says as it says no cost (``cost_usd`` 0). A session this process opened
(``session/new``) starts at nothing. A resumed one's first report also holds turns this process
never ran, so it sets where counting starts, and its turn says nothing. A cost in another unit than
USD is not read, and a total that went down (the agent began counting again) is no turn's: that
turn says nothing, and counting goes on from it.

What the CLIs send, from their published adapters (2026-11-23): Claude Code's (``claude-agent-acp``)
both, at the end of every turn; Gemini CLI's only its context window; Codex's (``codex-acp``) and
Kiro CLI's could not be read from what they publish. Whatever one says is read the same way, and one
that says nothing leaves its turns uncounted, which the places that show spend say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

from personalclaw.acp.types import UPDATE_USAGE, JsonRpcMessage

#: The token totals ACP's ``Usage`` names, each with the event field its turn's share goes on.
#: The first two are required by the protocol; the cache counts are optional and read as 0.
TOKEN_FIELDS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cachedReadTokens", "cache_read_tokens"),
    ("cachedWriteTokens", "cache_creation_tokens"),
)


def _is_count(n: object) -> TypeGuard[int | float]:
    """A number a report says, at or above 0, and not a bool (``isinstance(True, int)`` passes)."""
    return isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0


def reported_cost(msg: JsonRpcMessage) -> float | None:
    """The session's cost so far, in USD, off ACP's usage update *msg*, or None when *msg* is not
    one or says no cost in USD."""
    params = msg.params or {}
    update = params.get("update", {}) if isinstance(params, dict) else {}
    if not isinstance(update, dict) or update.get("sessionUpdate") != UPDATE_USAGE:
        return None
    cost = update.get("cost")
    if not isinstance(cost, dict) or cost.get("currency") != "USD":
        return None
    amount = cost.get("amount")
    return float(amount) if _is_count(amount) else None


def reported_tokens(result: object) -> dict[str, int] | None:
    """The session's token totals off the agent's answer to a prompt (*result*'s ``usage``), by the
    event field each goes on, or None when it says none, or says one that is not a count."""
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict) or "inputTokens" not in usage or "outputTokens" not in usage:
        return None
    counts = {ours: usage.get(theirs, 0) for theirs, ours in TOKEN_FIELDS}
    if not all(_is_count(n) for n in counts.values()):
        return None
    return {ours: int(n) for ours, n in counts.items()}


@dataclass
class SessionSpend:
    """One ACP session's running totals as its agent reported them, and what a turn added."""

    #: The session's cost so far in USD, and its token totals; None until the agent says.
    cost: float | None = None
    tokens: dict[str, int] | None = None
    #: What they read when the turn began.
    _cost_from: float | None = None
    _tokens_from: dict[str, int] | None = None

    def opened_here(self) -> None:
        """This process opened the session (``session/new``): its totals start at nothing."""
        self.cost = 0.0
        self.tokens = {ours: 0 for _theirs, ours in TOKEN_FIELDS}

    def turn_began(self) -> None:
        """A turn starts: what it adds is counted from the totals as they read now."""
        self._cost_from = self.cost
        self._tokens_from = dict(self.tokens) if self.tokens is not None else None

    def saw(self, msg: JsonRpcMessage) -> None:
        """Keep the session's cost so far when *msg* is a usage update that says it."""
        cost = reported_cost(msg)
        if cost is not None:
            self.cost = cost

    def answered(self, result: object) -> None:
        """Keep the session's token totals when the agent's answer *result* says them."""
        tokens = reported_tokens(result)
        if tokens is not None:
            self.tokens = tokens

    def turn(self) -> dict[str, Any]:
        """What this turn added, as the fields of its ``EVENT_COMPLETE``: ``cost_usd`` and the token
        counts, each only when the total it adds to was known when the turn began."""
        out: dict[str, Any] = {}
        start, now = self._cost_from, self.cost
        if start is not None and now is not None and now >= start:
            out["cost_usd"] = round(now - start, 9)
        before, after = self._tokens_from, self.tokens
        if before is not None and after is not None:
            grew = {k: after[k] - before.get(k, 0) for k in after}
            if all(n >= 0 for n in grew.values()):
                out.update(grew)
        return out
