"""Host-as-permission-authority for ACP sessions (ACP-AGENT-PARITY §2.2).

An ACP CLI decides *for itself* which of its tools ask the client for permission.
Everything it does not ask about runs before the host ever sees a decision point,
so the host's deny-list, task-mode gate and blocking PreToolUse hooks — all of
which hang off ``session/request_permission`` — simply never run for it. That is
gap 2, the safety hole, and `G27` measured it: on kiro, seven of thirteen tool
calls in one turn (its native ``todo_list``) executed with no permission request
at all, each labelled ``risk: "destructive"`` by the host, in the same turns where
the read, the write and the ``rm`` each raised a card.

This module owns the three host-side answers:

1. :func:`sanitize_mode` — never hand the CLI a mode that lets it self-approve.
   The host forwards the most-restrictive native mode (``default``) so every tool
   escalates, and refuses ``acceptEdits`` / ``dontAsk`` / ``bypassPermissions``
   unless the caller declares an explicit unattended session (§2.3, which owns
   the auto-deny-with-reason half that makes that safe).
2. :func:`command_probe` — the deny-list must see the REAL command. A permission
   frame carries a truncated human title (``"unknown"`` on codex, `G18`), so a
   deny pattern evaluated on the title alone silently misses ``git push --force``.
3. :data:`NOT_GATEABLE` — the residual set the host provably cannot gate, per
   provider, each entry carrying the observation that proved it. The honest half
   of §2.2: a gate that silently fails to cover a tool is worse than a documented
   hole, because the card's absence reads as "nothing dangerous happened". Every
   provider is enumerated — including the ones whose residual set measured EMPTY,
   so "no entry" can never be confused with "not measured".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: The most-restrictive Zed-adapter permission mode: every tool escalates to the
#: client via ``session/request_permission``, so the host gate is the authority.
#: Dialects with no permission-mode axis (kiro's default dialect returns None from
#: ``set_mode_request``) drop it — for them the host gate is the ONLY gate, which
#: is exactly §2.6's "kiro plans by host enforcement".
HOST_AUTHORITY_MODE = "default"

#: Native modes that make the CLI its own permission authority. Matched on a
#: canonical form (case-folded, ``-``/``_``/space-stripped) so ``acceptEdits``,
#: ``accept-edits`` and ``ACCEPT_EDITS`` are one value. Fail-closed by
#: construction: an unrecognized mode is NOT assumed safe (see
#: :func:`sanitize_mode`).
AUTO_APPROVE_MODES: frozenset[str] = frozenset(
    {
        "acceptedits",
        "acceptall",
        "acceptalledits",
        "dontask",
        "neverask",
        "bypasspermissions",
        "bypass",
        "yolo",
        "auto",
        "autoapprove",
        "fullauto",
        "dangerfullaccess",
        "allowall",
    }
)

#: Modes the host forwards verbatim. ``plan`` is behavioral, not an approval
#: bypass (the adapter denies execution in plan; it does not auto-allow), and
#: ``default`` IS the authority mode. Anything else is unknown to us: we do not
#: widen on an unknown value, we clamp to ``default`` and say so.
PASSTHROUGH_MODES: frozenset[str] = frozenset({"default", "plan"})


def canonical_mode(mode: str) -> str:
    """Canonical comparison form of a native mode id."""
    return "".join(ch for ch in str(mode or "").lower() if ch.isalnum())


@dataclass(frozen=True)
class ModeDecision:
    """What the host will actually forward, and why it differs from the request."""

    mode: str
    requested: str
    downgraded: bool = False
    reason: str = ""


def sanitize_mode(requested: str | None, *, unattended: bool = False) -> ModeDecision:
    """Resolve the permission mode the host is willing to forward.

    * empty → :data:`HOST_AUTHORITY_MODE`. "Whatever the CLI defaults to" is the
      hole: the host asserts the restrictive mode positively instead.
    * ``default`` / ``plan`` → forwarded verbatim.
    * an auto-approve mode → clamped to :data:`HOST_AUTHORITY_MODE`, unless
      ``unattended`` (§2.3's explicit path, which pairs it with host-side
      auto-deny so a background run resolves deterministically instead of
      wedging). Nothing wires ``unattended=True`` yet — the bridge pops
      ``unattended`` for ACP (``provider_bridge.py``), which §2.3 fixes. Until
      then every ACP session gets the authority mode, and unattended runs still
      execute because their session carries ``_trust`` (the host auto-approves
      at the gate, and the approval is AUDITED instead of invisible).
    * anything unrecognized → clamped. An adapter-specific mode we have never
      seen might be an auto-approve mode under another name, and a silent
      downgrade of safety is never the fail-open we take.
    """
    raw = str(requested or "").strip()
    canon = canonical_mode(raw)
    if not canon:
        return ModeDecision(
            mode=HOST_AUTHORITY_MODE,
            requested=raw,
            downgraded=False,
            reason="no mode requested — host asserts the restrictive mode",
        )
    if canon in PASSTHROUGH_MODES:
        return ModeDecision(mode=raw, requested=raw)
    if canon in AUTO_APPROVE_MODES:
        if unattended:
            return ModeDecision(
                mode=raw,
                requested=raw,
                reason="unattended session — auto-approve mode allowed by §2.3",
            )
        return ModeDecision(
            mode=HOST_AUTHORITY_MODE,
            requested=raw,
            downgraded=True,
            reason=(
                f"{raw!r} makes the CLI its own permission authority; the host "
                f"forwards {HOST_AUTHORITY_MODE!r} so every tool reaches the host gate"
            ),
        )
    return ModeDecision(
        mode=HOST_AUTHORITY_MODE,
        requested=raw,
        downgraded=True,
        reason=(
            f"unrecognized permission mode {raw!r} — clamped to "
            f"{HOST_AUTHORITY_MODE!r} rather than assumed safe"
        ),
    )


def command_probe(title: str, command: str) -> str:
    """The extra name the deny-list must be evaluated against, or ``""``.

    The permission frame's ``title`` is a truncated human string — ``"unknown"``
    when the adapter sends no title at all (`G18`) — while the real shell command
    lives in the cached ``tool_call`` input. Returns the command in the
    ``"Running: "`` form the hook chain already normalizes (so ``is_denied`` and
    ``is_sensitive_bash_command`` see it), or ``""`` when there is nothing new to
    check (no command, or the title already carries it verbatim).

    Deliberately one-directional: callers consult this form for a DENY verdict
    only and never for auto-approve, so widening the surface cannot happen here.
    """
    cmd = " ".join(str(command or "").split())
    if not cmd:
        return ""
    if cmd in str(title or ""):
        return ""
    return f"Running: {cmd}"


@dataclass(frozen=True)
class NotGateable:
    """One provider-scoped tool the host provably cannot pre-gate."""

    tool: str
    reason: str
    observation: str
    title_patterns: tuple[str, ...] = ()

    def matches(self, title: str) -> bool:
        low = str(title or "").lower()
        if not low:
            return False
        if self.tool.lower() in low:
            return True
        return any(p in low for p in self.title_patterns)


@dataclass(frozen=True)
class ProviderCoverage:
    """The measured gate coverage for one ACP provider.

    ``entries`` empty is a *positive* statement — "measured, residual set empty"
    — not an absence of data. ``measurement`` names the sweep that produced it so
    a stale claim is traceable to the turn that made it.
    """

    provider: str
    measurement: str
    entries: tuple[NotGateable, ...] = field(default_factory=tuple)

    @property
    def gated_universally(self) -> bool:
        return not self.entries


#: Per-provider residual not-gateable set (SC #3). Keyed by the normalized
#: provider key (see :func:`normalize_provider`).
NOT_GATEABLE: dict[str, ProviderCoverage] = {
    "kiro-cli": ProviderCoverage(
        provider="kiro-cli",
        measurement=(
            "AAP-3 sweep (K13, K15) + AAP-5 live re-drive 2026-08-18: one turn, "
            "6 tool calls, 1 gated, 5 ungated (4x todo_list + 1 file read)"
        ),
        entries=(
            NotGateable(
                tool="todo_list",
                reason=(
                    "kiro's native task-list tool emits a tool_call frame and a SEL "
                    "'invoked' row but never a session/request_permission, so no host "
                    "gate — deny-list, task-mode, PreToolUse — can run for it."
                ),
                observation=(
                    "G27: seven of thirteen tool calls in one turn ('Creating task "
                    "list: …', 'Completing #1/#2/#3') executed with no permission "
                    "request, each labelled risk='destructive' by the host, in the "
                    "same turns where the read, the write and the rm each raised a card."
                ),
                title_patterns=("creating task list", "completing #", "task list"),
            ),
            NotGateable(
                tool="fs_read",
                reason=(
                    "kiro self-approves its OWN file reads: the read raises no "
                    "session/request_permission even though the write in the same "
                    "turn does, so a read of a path the host would have questioned "
                    "is never offered for a decision."
                ),
                observation=(
                    "AAP-5 live re-drive 2026-08-18 against real kiro-cli: in one "
                    "turn 'Creating todo_probe.txt' raised a card while "
                    "'Reading todo_probe.txt:1-10' (kind='read') did not — 6 tool "
                    "calls, 1 gated, 5 ungated. Effective risk resolves to SAFE, so "
                    "this residue is labelled, never turn-aborting."
                ),
                title_patterns=("reading ",),
            ),
        ),
    ),
    "claude-code": ProviderCoverage(
        provider="claude-code",
        measurement="AAP-1 sweep — residual set measured EMPTY",
        entries=(),
    ),
    "codex": ProviderCoverage(
        provider="codex",
        measurement="AAP-2 sweep — residual set measured EMPTY",
        entries=(),
    ),
}


_PROVIDER_ALIASES: dict[str, str] = {
    "kiro": "kiro-cli",
    "kiro-cli": "kiro-cli",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude-code-agent": "claude-code",
    "claude-agent": "claude-code",
    "codex": "codex",
    "codex-cli": "codex",
    "codex-agent": "codex",
}


def normalize_provider(provider: str) -> str:
    """Normalize a provider label to a :data:`NOT_GATEABLE` key.

    Accepts the bundle name, the ``acp:<cli>`` runtime id, or the raw launch-command
    basename (``claude-code-acp``, ``codex-acp``), since the three disagree.
    """
    raw = str(provider or "").strip().lower()
    if raw.startswith("acp:"):
        raw = raw[4:]
    for suffix in ("-acp", "_acp", ".acp"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    raw = raw.strip("-_ ")
    return _PROVIDER_ALIASES.get(raw, raw)


def not_gateable_entry(provider: str, title: str) -> NotGateable | None:
    """The declared not-gateable entry for ``title`` on ``provider``, or None.

    A match means "this tool running without a card is a KNOWN, written-down
    limitation" — the host still surfaces it, but it is not evidence of a new
    hole. A miss on an ungated tool is the dangerous case: an undeclared tool the
    host was never asked about.
    """
    coverage = NOT_GATEABLE.get(normalize_provider(provider))
    if coverage is None:
        return None
    for entry in coverage.entries:
        if entry.matches(title):
            return entry
    return None


def coverage_for(provider: str) -> ProviderCoverage | None:
    """The measured coverage statement for a provider, or None if unmeasured."""
    return NOT_GATEABLE.get(normalize_provider(provider))
