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

   The registry distinguishes **declared** from **excused**, which is not the same
   axis. A residual can be measured and written down without being blessed, so
   there are exactly three states:

   * *measured, residual empty* — ``entries=()``, :attr:`ProviderCoverage.
     gated_universally` is True: everything this provider does reaches the gate.
   * *measured, residual non-empty and ACCEPTED* (``state=ACCEPTED``) — a known
     upstream limitation we have blessed. The host may go quiet about it: the
     transcript says "documented limitation" and SEL records
     ``ungated_declared``.
   * *measured, residual non-empty and NOT accepted* (``state=UNACCEPTED``, the
     default) — the host cannot gate it and we have **not** blessed it. It stays
     exactly as loud as an undeclared hole: the ``(ungated: …)`` transcript line,
     the plain ``ungated`` SEL outcome, and the turn abort for a non-safe mutation
     under ask/plan. Writing a hole down must never be a way to silence it.

   Only the accepted state excuses the absence of a card, so consumers ask
   :func:`not_gateable_entry` whether a residual is *declared* and
   :attr:`NotGateable.state` whether it is *excused*. Conflating the two is the
   original defect: two providers declared ``residual set measured EMPTY`` while
   runtime was persisting plain ``ungated`` SEL rows for them, and populating the
   registry naively would have muted those rows instead of fixing the claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

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


class ResidualState(str, Enum):
    """Whether a measured residual has been blessed — *declared* vs *excused*.

    Two members, both entry-scoped: the third registry state ("measured, residual
    set empty") is a statement about a PROVIDER, carried by ``entries=()``, so it
    would be nonsense as an entry's own state. The values are prose because they
    are read by operators: §2.7's parity doc renders this field verbatim.
    """

    ACCEPTED = "measured, accepted — a documented limitation; the host labels it and stays quiet"
    UNACCEPTED = (
        "measured, NOT accepted — the host cannot gate it and nobody blessed it, so it stays loud"
    )


@dataclass(frozen=True)
class NotGateable:
    """One provider-scoped tool the host provably cannot pre-gate.

    ``state`` is the difference between *declared* and *excused*. It defaults to
    :attr:`ResidualState.UNACCEPTED` — fail-loud by construction, so an entry added
    without an explicit judgement keeps the operator-visible signal rather than
    silently muting it. Set it to ``ACCEPTED`` only for a residual we have decided
    to live with; the entry's ``reason`` then has to say why that is acceptable.
    """

    tool: str
    reason: str
    observation: str
    title_patterns: tuple[str, ...] = ()
    state: ResidualState = ResidualState.UNACCEPTED

    @property
    def accepted(self) -> bool:
        """Derived read-side convenience — the state itself lives in ``state``."""
        return self.state is ResidualState.ACCEPTED

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

    ``gated_universally`` is derived from ``entries``, never asserted separately, so
    it cannot drift away from the entries: declaring any residual — accepted or not
    — makes it False.
    """

    provider: str
    measurement: str
    entries: tuple[NotGateable, ...] = field(default_factory=tuple)

    @property
    def gated_universally(self) -> bool:
        return not self.entries

    @property
    def unaccepted_residual(self) -> tuple[NotGateable, ...]:
        """The measured holes we have NOT blessed — the ones that stay loud.

        The operator-facing question the registry exists to answer: "what can this
        provider do that the host never got asked about, and which of those have we
        not agreed to live with?"
        """
        return tuple(e for e in self.entries if not e.accepted)


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
                # Accepted: kiro's task list is bookkeeping inside kiro's own
                # process — it mutates no host state, so labelling it is enough.
                state=ResidualState.ACCEPTED,
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
                # Accepted: effective risk resolves to SAFE for a read, so the
                # label carries the whole signal — nothing to abort, nothing to
                # deny. Blessed on that basis, not on kiro's behalf.
                state=ResidualState.ACCEPTED,
            ),
        ),
    ),
    "claude-code": ProviderCoverage(
        provider="claude-code",
        measurement=(
            "AAP-5 Phase-1 SEL re-read (O96): 7 persisted rows with "
            "outcome='ungated', provider='claude-code', across 4 sessions and 2 "
            "tool titles. RETRACTS the earlier AAP-1 zero-residual claim, which "
            "runtime disproved: chat_runner records "
            "'ungated_declared' whenever not_gateable_entry() matched, so a plain "
            "'ungated' row is proof the registry held nothing for that title."
        ),
        entries=(
            NotGateable(
                tool="Terminal",
                reason=(
                    "claude-code runs its shell tool without emitting a "
                    "session/request_permission for it. The host's deny-list, "
                    "task-mode gate and blocking PreToolUse hooks all hang off that "
                    "frame, so none of them ran. NOT accepted: a shell command that "
                    "reaches the OS with no host decision point is not a limitation "
                    "we are willing to go quiet about."
                ),
                observation=(
                    "O97: the execute-kind share of O96's 7 'ungated' rows carries "
                    "title='Terminal' and reason='no session/request_permission for "
                    "this tool_call'."
                ),
            ),
            NotGateable(
                tool="Read File",
                reason=(
                    "claude-code self-approves its own file reads — the same missing "
                    "frame — so a read of a path the host would have questioned is "
                    "never offered for a decision. NOT accepted: effective risk "
                    "resolves to SAFE so it never aborts a turn, but nobody ever "
                    "blessed it, and an unblessed hole stays loud."
                ),
                observation=(
                    "O98: 'Read File' is the second of the two titles in O96's 7-row "
                    "'ungated' set for provider='claude-code'."
                ),
            ),
        ),
    ),
    "codex": ProviderCoverage(
        provider="codex",
        measurement=(
            "AAP-5 Phase-1 live drive (O99-O102): 4 plain 'ungated' rows on "
            "provider='codex' — a read, an in-workspace write, an out-of-workspace "
            "write and a network call. RETRACTS the earlier AAP-2 zero-residual claim."
        ),
        entries=(
            NotGateable(
                tool="codex-native",
                reason=(
                    "codex is its own first-line permission authority: under "
                    "HOST_AUTHORITY_MODE='default' it escalates almost nothing, so "
                    "its whole native tool surface — reads, writes, shell, network — "
                    "can execute before the host has a decision point. NOT accepted: "
                    "an out-of-workspace write that completed with no card is the "
                    "exact shape §2.2 exists to make loud."
                ),
                observation=(
                    "O99-O101: four plain 'ungated' rows in one AAP-5 Phase-1 drive — "
                    "a read, an in-workspace write, an out-of-workspace write "
                    "('printf … > /private/tmp/aap2b-outside-probe.txt', which "
                    "EXECUTED) and a network call ('curl https://example.com'). "
                    "Vacuity floor for the same drive (O102): codex DOES escalate on "
                    "retry, and 'git push' was escalated and correctly deny-listed — "
                    "so 'escalates almost nothing' measures codex, not a dead harness."
                ),
                # Deliberately empty (G120): Phase 1 recorded codex's ACTIONS, not the
                # tool_call titles it sent, so there is no measured string to match on.
                # This entry is therefore documentation-shaped — it makes
                # gated_universally False and enumerates the hole with its evidence,
                # but never matches a live title. Safe precisely because it is
                # unaccepted: matching only ever decides whether to go QUIET.
                title_patterns=(),
            ),
        ),
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
    limitation" — it is not evidence of a NEW hole. A miss on an ungated tool is
    the undeclared case: a tool the host was never asked about and never measured.

    A match does **not** by itself excuse the missing card. Ask ``entry.state``
    (or its ``entry.accepted`` shorthand) for that: only an accepted residual may
    go quiet. Callers
    deciding how loud to be must key on the accepted bit, never on
    ``entry is not None`` — the two answers diverge for every measured-but-unblessed
    hole, which is most of this registry.
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
