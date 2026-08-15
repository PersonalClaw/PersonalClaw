"""Approval memory: the rule model, the matcher, the reply grammar, the cooldowns.

PROACTIVE-ASSISTANT §1.4. Four pure pieces, no I/O, no clock, no LLM:

* :class:`ApprovalRule` + :func:`rule_key` / :func:`rule_to_value` /
  :func:`rule_from_row` — the ``user.approval.<md5-12>`` semantic-row encoding,
  following the ``user.commitment.<md5-12>`` precedent exactly.
* :func:`match_rules` — the routing decision for one proposal. **Total and
  deterministic**: same rules + same ``pattern_key`` + same ``now`` always give
  the same answer, and "nothing matched" is its own answer, never a fabricated
  approval.
* :func:`parse_reply` — the digest reply grammar. The grammar IS the safety
  boundary (§6): an unparseable reply gets a help line, never an interpretation.
* :func:`escalate_suppression` / :func:`suppression_active` — the 24h → 7d → 30d
  ladder for a pattern the user keeps declining without formalizing a rule.

**Why a matcher and not a dict lookup.** A rule is written at whatever
specificity the user taught it at (``archive`` vs ``archive:sender:x``), so the
lookup is a *segment-prefix* match over a colon-delimited pattern, and several
rules can match one proposal at once. That makes the conflict rules load-bearing:

1. **Deny wins at ANY specificity.** A single matching deny decides the
   proposal even if a longer, more specific approve also matches. The asymmetry
   is deliberate: over-generalizing a deny costs the user a proposal they can
   still ask for; over-generalizing an approve costs them an action they never
   sanctioned.
2. **Then most-specific approve wins** — the longest matching pattern.
3. **Ties break on ``(pattern, key)`` ascending.** Two rules of equal
   specificity and equal verdict are interchangeable for the decision, but the
   *named rule* lands in a ledger row, so which one is named must be stable
   across processes. Row keys are unique, so the order is total.
4. **Expired rules never match**, and suppression is consulted only when no
   approve/deny rule matched — an explicit rule always beats a cooldown.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

#: The reserved semantic-key prefix. Mirrors ``user.commitment.`` (memory_service)
#: and resolves to ``MemoryKind.APPROVAL`` in ``memory_record._kind_from_key``.
APPROVAL_KEY_PREFIX = "user.approval."

#: Pattern segment separator. A pattern is ``<action_type>[:<qualifier>...]``,
#: e.g. ``archive`` / ``archive:sender`` / ``archive:sender:noreply.github.com``.
SEGMENT_SEP = ":"

#: The escalating suppression ladder (§1.4): first decline 24h, second 7d, third
#: and every decline after 30d. Clamped at the top rung on purpose — an unbounded
#: ladder would silently become a permanent deny the user never taught.
COOLDOWN_LADDER_SECONDS: tuple[int, ...] = (24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600)


class Verdict(str, Enum):
    """What a stored rule says about its pattern."""

    APPROVE = "approve"  # always-approve: auto-execute matching proposals
    DENY = "deny"  # always-deny: silently skip, naming the rule in the ledger
    SUPPRESSED = "suppressed"  # shadow row: a cooldown, not a taught rule


class Decision(str, Enum):
    """The routing answer for one proposal. ``NO_DECISION`` is a real answer."""

    AUTO_APPROVE = "auto_approve"  # a taught approve rule matched
    DENY = "deny"  # a taught deny rule matched (wins over any approve)
    SUPPRESS = "suppress"  # an active cooldown: do not re-propose this window
    NO_DECISION = "no_decision"  # nothing matched → queue pending in the digest


@dataclass(frozen=True)
class ApprovalRule:
    """One ``user.approval.*`` row, decoded.

    ``key`` is the row key (``user.approval.<md5-12>``) and is what a ledger row
    names. ``pattern`` is the raw human-readable pattern it hashes from.
    """

    pattern: str
    verdict: Verdict
    action_type: str = ""
    scope: str = "global"
    created_from_digest: str | None = None
    hit_count: int = 0
    last_hit_at: str | None = None
    expires_at: str | None = None
    #: Only meaningful for ``verdict=SUPPRESSED`` shadow rows.
    decline_count: int = 0
    cooldown_until: str | None = None
    #: Explicit per-rule graduation for external sends (§1.6 bound 2). Off means
    #: an approve rule for a send-capable action still produces a draft.
    send_capable: bool = False
    key: str = ""

    def __post_init__(self) -> None:
        if not self.pattern.strip():
            raise ValueError("approval rule pattern must be non-empty")
        object.__setattr__(self, "pattern", _normalize_pattern(self.pattern))
        object.__setattr__(self, "verdict", Verdict(self.verdict))
        if not self.key:
            object.__setattr__(self, "key", rule_key(self.pattern))
        if not self.action_type:
            object.__setattr__(self, "action_type", self.pattern.split(SEGMENT_SEP)[0])

    @property
    def specificity(self) -> int:
        """How narrow the pattern is: its segment count. Higher = more specific."""
        return len(self.pattern.split(SEGMENT_SEP))

    def is_expired(self, *, now: datetime) -> bool:
        expiry = _parse_ts(self.expires_at)
        return expiry is not None and expiry <= now


@dataclass(frozen=True)
class MatchResult:
    """The decision plus the rule that produced it (for the ledger row)."""

    decision: Decision
    rule: ApprovalRule | None = None
    reason: str = ""

    @property
    def auto_executes(self) -> bool:
        return self.decision is Decision.AUTO_APPROVE


@dataclass(frozen=True)
class SuppressionState:
    """A pattern's cooldown position: which rung, and until when."""

    pattern: str
    decline_count: int
    cooldown_until: str | None

    @property
    def rung_seconds(self) -> int:
        idx = min(max(self.decline_count, 1), len(COOLDOWN_LADDER_SECONDS)) - 1
        return COOLDOWN_LADDER_SECONDS[idx]


# ── key / value encoding ─────────────────────────────────────────────────────


def _normalize_pattern(pattern: str) -> str:
    """Lowercase, trim, collapse blank segments — so one pattern has one key."""
    parts = [p.strip().lower() for p in pattern.strip().split(SEGMENT_SEP)]
    return SEGMENT_SEP.join([p for p in parts if p])


def rule_key(pattern: str) -> str:
    """``user.approval.<md5-12(pattern)>`` — the commitments precedent verbatim.

    md5 here is a *key derivation*, not a security primitive: the pattern itself
    is stored in ``value_json``, so nothing depends on the digest being hard to
    invert. It matches ``user.commitment.<md5-12>`` so the two prefixes stay
    legible side by side.
    """
    normalized = _normalize_pattern(pattern)
    if not normalized:
        raise ValueError("approval rule pattern must be non-empty")
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]  # noqa: S324
    return f"{APPROVAL_KEY_PREFIX}{digest}"


def rule_to_value(rule: ApprovalRule) -> dict[str, Any]:
    """The ``value_json`` payload for a rule row."""
    return {
        "pattern": rule.pattern,
        "verdict": rule.verdict.value,
        "action_type": rule.action_type,
        "scope": rule.scope,
        "created_from_digest": rule.created_from_digest,
        "hit_count": rule.hit_count,
        "last_hit_at": rule.last_hit_at,
        "expires_at": rule.expires_at,
        "decline_count": rule.decline_count,
        "cooldown_until": rule.cooldown_until,
        "send_capable": rule.send_capable,
    }


def rule_from_row(key: str, value: Any) -> ApprovalRule | None:
    """Decode a stored row. Returns ``None`` for anything unreadable.

    Refusing beats repairing: a row whose verdict is missing or unrecognized has
    no defensible reading, and defaulting it to ``approve`` is the exact failure
    this module exists to make unreachable. An undecodable row is treated as
    absent, so the proposal falls through to ``NO_DECISION`` (pending).
    """
    if not isinstance(key, str) or not key.startswith(APPROVAL_KEY_PREFIX):
        return None
    payload: Any = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    pattern = payload.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    try:
        verdict = Verdict(str(payload.get("verdict")))
    except ValueError:
        return None
    return ApprovalRule(
        pattern=pattern,
        verdict=verdict,
        action_type=str(payload.get("action_type") or ""),
        scope=str(payload.get("scope") or "global"),
        created_from_digest=payload.get("created_from_digest"),
        hit_count=int(payload.get("hit_count") or 0),
        last_hit_at=payload.get("last_hit_at"),
        expires_at=payload.get("expires_at"),
        decline_count=int(payload.get("decline_count") or 0),
        cooldown_until=payload.get("cooldown_until"),
        send_capable=bool(payload.get("send_capable")),
        key=key,
    )


# ── matching ─────────────────────────────────────────────────────────────────


def rule_matches(rule: ApprovalRule, pattern_key: str) -> bool:
    """True when the rule's segments are a segment-wise prefix of ``pattern_key``.

    Segment-wise, not string-wise: ``archive:sender`` must not match
    ``archive:sender-domain:x``, which a ``startswith`` would wrongly accept and
    which is how an over-broad approve rule would sneak in.
    """
    target = _normalize_pattern(pattern_key)
    if not target:
        return False
    want = rule.pattern.split(SEGMENT_SEP)
    have = target.split(SEGMENT_SEP)
    return len(want) <= len(have) and have[: len(want)] == want


def _sort_key(rule: ApprovalRule) -> tuple[int, str, str]:
    # Most specific first; then the documented total tie-break.
    return (-rule.specificity, rule.pattern, rule.key)


def match_rules(
    rules: Iterable[ApprovalRule],
    pattern_key: str,
    *,
    now: datetime,
) -> MatchResult:
    """Route one proposal against the stored rules. Total and deterministic.

    Precedence: expired rules are dropped → any matching DENY wins (most
    specific of them is the one named) → most specific matching APPROVE → an
    active SUPPRESSED cooldown → ``NO_DECISION``.
    """
    now = _as_utc(now)
    live = [r for r in rules if not r.is_expired(now=now) and rule_matches(r, pattern_key)]
    denies = sorted((r for r in live if r.verdict is Verdict.DENY), key=_sort_key)
    if denies:
        rule = denies[0]
        return MatchResult(
            Decision.DENY,
            rule,
            f"deny rule {rule.key} ({rule.pattern}) matched",
        )
    approves = sorted((r for r in live if r.verdict is Verdict.APPROVE), key=_sort_key)
    if approves:
        rule = approves[0]
        return MatchResult(
            Decision.AUTO_APPROVE,
            rule,
            f"approve rule {rule.key} ({rule.pattern}) matched",
        )
    cooling = sorted(
        (
            r
            for r in live
            if r.verdict is Verdict.SUPPRESSED
            and suppression_active(
                SuppressionState(r.pattern, r.decline_count, r.cooldown_until), now=now
            )
        ),
        key=_sort_key,
    )
    if cooling:
        rule = cooling[0]
        return MatchResult(
            Decision.SUPPRESS,
            rule,
            f"suppression {rule.key} ({rule.pattern}) active until {rule.cooldown_until}",
        )
    return MatchResult(Decision.NO_DECISION, None, "no approval rule matched")


# ── suppression cooldowns ────────────────────────────────────────────────────


def escalate_suppression(
    state: SuppressionState | None,
    *,
    pattern: str,
    now: datetime,
) -> SuppressionState:
    """One more decline of ``pattern`` → the next rung of the ladder.

    24h, then 7d, then 30d for every further decline (clamped). Escalation is
    driven by the decline COUNT, not by wall-clock gaps, so a user who declines
    the same thing across three separate digests lands on 30d exactly as one who
    declines it three times in one.
    """
    now = _as_utc(now)
    normalized = _normalize_pattern(pattern)
    count = (state.decline_count if state else 0) + 1
    idx = min(count, len(COOLDOWN_LADDER_SECONDS)) - 1
    until = now + timedelta(seconds=COOLDOWN_LADDER_SECONDS[idx])
    return SuppressionState(normalized, count, until.isoformat())


def suppression_active(state: SuppressionState | None, *, now: datetime) -> bool:
    """True while the cooldown is still running. No timestamp = not suppressed."""
    if state is None:
        return False
    until = _parse_ts(state.cooldown_until)
    return until is not None and until > _as_utc(now)


def clear_suppression(rule: ApprovalRule) -> ApprovalRule:
    """Accepting during a cooldown clears it (§1.4) — count and clock both reset.

    Resetting the COUNT too is the point: the ladder measures a run of declines,
    and one acceptance ends that run. Keeping the count would silently put the
    next single decline back on the 30d rung.
    """
    return replace(rule, decline_count=0, cooldown_until=None)


# ── reply grammar ────────────────────────────────────────────────────────────


class ReplyAction(str, Enum):
    """What a parsed digest reply asks for."""

    APPROVE_ONCE = "approve_once"
    DENY_ONCE = "deny_once"
    APPROVE_ALWAYS = "approve_always"  # act + persist an approve rule
    DENY_ALWAYS = "deny_always"  # act (skip) + persist a deny rule
    APPROVE_ALL = "approve_all"
    DENY_ALL = "deny_all"
    HELP = "help"
    UNPARSEABLE = "unparseable"  # → help line, never an interpretation


HELP_TEXT = (
    "Reply with: `3 yes` / `3 no` to act on item 3 once · "
    "`always yes 3` / `always no 3` to act and remember the pattern · "
    "`yes all` / `no all` for every pending item · `help` for this line."
)

_VERBS: dict[str, bool] = {"yes": True, "y": True, "no": False, "n": False}
#: A token is alphanumeric, full stop. Anything else (``-1``, ``3rd``, ``#4``)
#: makes the whole reply unparseable rather than being silently scrubbed into a
#: token that parses — ``yes -1`` must not read as ``yes 1``.
_TOKEN_RE = re.compile(r"^[a-z0-9]+$")
#: Trailing sentence punctuation is the one thing stripped ("3 yes." → "3 yes").
_TRAILING = ".,;:!"
_HELP_FORMS = {"?", "??", "help", "help?", "h"}


def _tokenize(raw: str) -> list[str] | None:
    """Split into alphanumeric tokens, or ``None`` if any token is not one."""
    tokens: list[str] = []
    for rough in raw.strip().lower().split():
        token = rough.strip(_TRAILING)
        if not token:
            continue
        if not _TOKEN_RE.match(token):
            return None
        tokens.append(token)
    return tokens


@dataclass(frozen=True)
class ParsedReply:
    """The parse. ``approves`` is False for everything except an explicit yes."""

    action: ReplyAction
    ordinal: int | None = None
    raw: str = ""
    error: str | None = None

    @property
    def approves(self) -> bool:
        return self.action in (
            ReplyAction.APPROVE_ONCE,
            ReplyAction.APPROVE_ALWAYS,
            ReplyAction.APPROVE_ALL,
        )

    @property
    def persists_rule(self) -> bool:
        return self.action in (ReplyAction.APPROVE_ALWAYS, ReplyAction.DENY_ALWAYS)

    @property
    def applies_to_all(self) -> bool:
        return self.action in (ReplyAction.APPROVE_ALL, ReplyAction.DENY_ALL)


def _unparseable(raw: str, why: str) -> ParsedReply:
    return ParsedReply(ReplyAction.UNPARSEABLE, None, raw, f"{why} {HELP_TEXT}")


def parse_reply(text: str | None, *, max_ordinal: int | None = None) -> ParsedReply:
    """Parse one digest reply. Rejects; never guesses.

    Accepted (case- and punctuation-insensitive, any whitespace):

    * ``3 yes`` / ``yes 3`` / ``3 no`` / ``no 3`` — act once on item 3
    * ``always yes 3`` / ``always no 3`` — act and persist a rule
    * ``yes all`` / ``no all`` — act on every pending item
    * ``help`` / ``?``

    Anything else — extra words, two ordinals, ``always yes all``, an ordinal
    outside ``1..max_ordinal``, a bare ``yes`` with no target — is
    ``UNPARSEABLE`` carrying a help line. There is deliberately no path from a
    malformed reply to an approval: every ``return`` that approves is reached
    only by a fully-recognized form.
    """
    raw = text or ""
    if raw.strip().lower() in _HELP_FORMS:
        return ParsedReply(ReplyAction.HELP, None, raw)
    tokens = _tokenize(raw)
    if tokens is None:
        return _unparseable(raw, f"Could not read {raw.strip()!r}.")
    if not tokens:
        return _unparseable(raw, "Empty reply.")

    always = False
    if tokens[0] == "always":
        always = True
        tokens = tokens[1:]
    if len(tokens) != 2:
        return _unparseable(raw, f"Could not read {raw.strip()!r}.")

    # Exactly one verb and exactly one target, in either order.
    verbs = [t for t in tokens if t in _VERBS]
    targets = [t for t in tokens if t not in _VERBS]
    if len(verbs) != 1 or len(targets) != 1:
        return _unparseable(raw, f"Could not read {raw.strip()!r}.")
    approve = _VERBS[verbs[0]]
    target = targets[0]

    if target == "all":
        if always:
            # A blanket "always" would mint a rule per pending item from one
            # word. Refuse: the user names the pattern they are teaching.
            return _unparseable(raw, "`always` needs an item number, not `all`.")
        return ParsedReply(ReplyAction.APPROVE_ALL if approve else ReplyAction.DENY_ALL, None, raw)

    if not target.isdigit():
        return _unparseable(raw, f"{target!r} is not an item number.")
    ordinal = int(target)
    if ordinal < 1 or (max_ordinal is not None and ordinal > max_ordinal):
        upper = f"1-{max_ordinal}" if max_ordinal is not None else "1 or higher"
        return _unparseable(raw, f"Item {ordinal} is not in this digest ({upper}).")
    if always:
        action = ReplyAction.APPROVE_ALWAYS if approve else ReplyAction.DENY_ALWAYS
    else:
        action = ReplyAction.APPROVE_ONCE if approve else ReplyAction.DENY_ONCE
    return ParsedReply(action, ordinal, raw)


# ── time helpers (tolerant of naive strings, never guessing a zone) ───────────


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)


def rules_from_rows(rows: Sequence[tuple[str, Any]]) -> list[ApprovalRule]:
    """Decode ``(key, value)`` pairs, dropping unreadable rows."""
    out: list[ApprovalRule] = []
    for key, value in rows:
        rule = rule_from_row(key, value)
        if rule is not None:
            out.append(rule)
    return out
