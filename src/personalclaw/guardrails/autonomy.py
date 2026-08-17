"""Earned-autonomy rung ladder (AUTONOMY-GUARDRAILS §5, amendment 2026-07-26).

The shipped safety floor (§1-§4) is binary: an unattended run is HEADLESS read-only,
or a write is a creation-time grant. There is no graduated middle and no track
record — a reply draft approved unchanged forty times still asks every time. This
module adds the graduated middle ON TOP of that floor. It never relaxes it: a rung
does not override :func:`~personalclaw.guardrails.denylist.check_action`, a budget
pause, or :func:`~personalclaw.guardrails.incident.incident_active`.

**The ladder** (:data:`RUNGS`, ordered): ``draft_only`` → ``one_tap`` →
``auto_with_undo`` → ``autonomous``. Every autonomous WRITE action carries a stable
type key (``inbox.reply_draft``, ``sessions.auto_tag``, ``app:<name>.<action>``) and
an :class:`ActionTypeSpec` declaring the floor it starts at and the ceiling it can
never pass.

**Asymmetric by design — the property that makes this safe:**

* **Promotion is ALWAYS a user click.** :func:`promotion_eligibility` is a *derived
  proposal*: it reports that a type has earned the next rung and nothing else. It
  writes nothing, and no code path in this module promotes on its own.
* **Demotion is automatic and immediate.** :func:`demote` drops the type back to its
  floor on the FIRST rejection, undo, or 👎, and starts a cooldown during which the
  type cannot become eligible again.

**The track record is DERIVED, never stored as an opinion.** Eligibility is recomputed
on every call from two evidence sources that already exist:

* the SEL (``security_events.jsonl``) — human approval verdicts on tool invocations,
  attributed to a type by the ``action_type`` metadata key the dispatch seams stamp
  (:data:`SEL_ACTION_TYPE_KEY`) or by an ``operation`` equal to the type key;
* FEEDBACK-SIGNAL records (``feedback.jsonl``) — a 👎 on that type's output, attributed
  by a ``producer_id`` equal to the type key.

Only *grants* and *demotions* persist, to ``~/.personalclaw/autonomy_rungs.json``
(``atomic_write``, joins the snapshot ``CORE_FILES``). Storing the derived record
would let a stale count outlive the evidence it summarised, which is exactly the
opinion this module refuses to hold.

**Fail closed, everywhere.** An unreadable or corrupt store grants nothing (it cannot
prove a grant, so there is none). An unknown rung name never resolves above the floor.
An unregistered type key resolves to ``draft_only`` — no declaration, no autonomy.
Auto-approvals deliberately do NOT count as evidence: counting them would let a type
that already runs unattended manufacture its own promotion case.

Thresholds come from the ``guardrails.autonomy`` config subsection (operator-wide
defaults); a type that declares its own :class:`PromotionRule` on its spec keeps it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.guardrails.incident import incident_active

logger = logging.getLogger(__name__)

# ── the ladder ────────────────────────────────────────────────────────────────

RUNG_DRAFT_ONLY = "draft_only"
RUNG_ONE_TAP = "one_tap"
RUNG_AUTO_WITH_UNDO = "auto_with_undo"
RUNG_AUTONOMOUS = "autonomous"

#: The ordered ladder. Index IS the rank — a rung is "above" another when its index
#: is higher. Closed vocabulary: a name outside it is unknown and resolves to the
#: floor (see :func:`rung_rank`).
RUNGS: tuple[str, ...] = (
    RUNG_DRAFT_ONLY,
    RUNG_ONE_TAP,
    RUNG_AUTO_WITH_UNDO,
    RUNG_AUTONOMOUS,
)

#: The SEL ``metadata`` key a dispatch seam stamps with the action-type key, so an
#: approval verdict recorded by the existing approval machinery can be attributed to
#: a type without a second event stream. One name, defined once, read here.
SEL_ACTION_TYPE_KEY = "action_type"

# Approval outcomes that count as EVIDENCE (a human said yes). ``auto_approved`` and
# ``auto_approved_spawn`` are deliberately absent: an action that already ran without
# a human is not a track record, and counting it would let a promoted type bootstrap
# itself up the remaining ladder.
_HUMAN_APPROVED_OUTCOMES = frozenset({"approved"})
# Rejection outcomes. Prefix-matched because the seams write qualified variants
# (``rejected_invalid_cwd``, ``rejected_spawn``, ``rejected_excluded``).
_REJECTED_PREFIXES = ("rejected", "denied", "not_auto_approved")

# How far back into the (append-only, high-rate) SEL tail one eligibility computation
# reads. The window is also bounded by days; this bounds the work.
_SEL_SCAN_LIMIT = 5_000
# Demotion history kept per type — enough for the ladder panel to answer "why is this
# back at draft?" without letting one flapping type grow the store without bound.
_MAX_DEMOTIONS = 20
_MAX_CAUSE_CHARS = 200

_STORE_FILENAME = "autonomy_rungs.json"


def rung_rank(rung: str) -> int:
    """Ladder index of ``rung``, or ``-1`` when the name is not on the ladder.

    Every caller treats a negative rank as "unknown, therefore the floor" — an
    unrecognised rung must never resolve above what the type starts with.
    """
    try:
        return RUNGS.index(rung)
    except ValueError:
        return -1


# ── declarations ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromotionRule:
    """The evidence bar one type must clear before its next rung is *proposed*.

    Defaults are the plan's: ten clean approvals spread over at least seven days with
    zero rejections, then a fourteen-day cooldown after any demotion. A type may
    declare a stricter (or looser) rule on its spec; otherwise the operator's
    ``guardrails.autonomy`` config supplies these.
    """

    clean_approvals: int = 10
    min_days: int = 7
    max_rejections: int = 0
    cooldown_days: int = 14


@dataclass(frozen=True)
class ActionTypeSpec:
    """One registered action type: its key, its floor, and its hard ceiling.

    ``floor`` is where the type sits with no grant — the rung it can always use.
    ``ceiling`` is the rung it can never pass however much evidence accrues; it
    defaults to ``one_tap`` so a newly declared type asks, and so anything that leaves
    the machine ceilings below ``autonomous`` unless its declaration says otherwise.

    ``leaves_machine`` marks a type whose effect is visible outside this machine (a
    sent message, an external write). It is not decoration: such a type is never
    *proposed* for ``autonomous`` by :func:`promotion_eligibility`, however permissive
    its ceiling — reaching that rung has to be an explicit owner decision, not the
    tail end of a derived ladder.

    ``providers`` names the action-provider identities this type governs — the ONE
    thing a dispatch seam actually holds (``hook.provider`` / ``trigger.action_provider``).
    Carrying it on the DECLARATION is what keeps the seams free of per-action branching:
    a seam asks :func:`action_type_for_provider` and gets whatever the declaration said,
    so an app-contributed action is routed by the same three lines that route ``bash``.
    """

    key: str
    floor: str = RUNG_DRAFT_ONLY
    ceiling: str = RUNG_ONE_TAP
    leaves_machine: bool = False
    promotion: PromotionRule = field(default_factory=PromotionRule)
    providers: tuple[str, ...] = ()


# Process-global registry, the ``_PROFILES`` pattern from ``policy.py``. Populated at
# declaration sites (provider registration, inbox affordances, app manifests) — see
# ``register_action_type``. Reset between tests by ``reset_action_types``.
_REGISTRY: dict[str, ActionTypeSpec] = {}

# Dispatch identity → type key, derived from ``ActionTypeSpec.providers``. A pure index
# over the registry (never a second source of truth): every write goes through
# ``register_action_type`` and every stale entry is dropped when a key re-registers.
_PROVIDER_INDEX: dict[str, str] = {}


def register_action_type(spec: ActionTypeSpec) -> None:
    """Declare one action type. Re-registering the same key replaces its spec.

    Rejects a spec whose floor or ceiling is not on the ladder rather than storing an
    unusable declaration: an unknown rung name in a registration is a programming
    error at the declaration site, and silently coercing it would hide which types are
    actually governed.

    A provider name may be claimed by at most one type. A second claim REPLACES the
    first and says so in the log: two declarations for one dispatch identity means one
    of them is silently ungoverned, and the quiet version of that is the shape a seam
    can never tell apart from "nothing declared it".
    """
    if not spec.key:
        raise ValueError("action type key must be non-empty")
    for label, value in (("floor", spec.floor), ("ceiling", spec.ceiling)):
        if rung_rank(value) < 0:
            raise ValueError(
                f"{spec.key}: unknown {label} rung {value!r} (expected one of {RUNGS})"
            )
    _REGISTRY[spec.key] = spec
    # Drop this key's previous claims first, so a re-registration that narrowed its
    # ``providers`` list does not leave the dropped name pointing at it.
    for name in [n for n, k in _PROVIDER_INDEX.items() if k == spec.key]:
        del _PROVIDER_INDEX[name]
    for name in spec.providers:
        held = _PROVIDER_INDEX.get(name)
        if held is not None and held != spec.key:
            logger.warning(
                "action provider %r was governed by %s — %s now claims it", name, held, spec.key
            )
        _PROVIDER_INDEX[name] = spec.key


def action_type(key: str) -> ActionTypeSpec | None:
    """The registered spec for ``key``, or ``None`` when nothing declared it."""
    return _REGISTRY.get(key)


def action_type_for_provider(provider_name: str) -> ActionTypeSpec | None:
    """The spec governing a provider-dispatched action, or ``None`` when undeclared.

    The seam-facing lookup: a dispatch point holds a provider NAME, not a type key.
    ``None`` means no declaration claims this provider — the caller keeps the
    pre-ladder behaviour (the creation-time grant, denylist and capability fence still
    apply). It deliberately does NOT mean "draft_only": treating every undeclared
    provider as withheld would stop every hook and trigger in the tree, which is an
    outage dressed as a safety control.
    """
    key = _PROVIDER_INDEX.get((provider_name or "").strip())
    return _REGISTRY.get(key) if key else None


def unregister_action_type(key: str) -> None:
    """Drop a declaration and every provider claim it held.

    The mirror of an app being disabled or uninstalled: its provider leaves the dispatch
    registry in the same breath, and a declaration that outlived it would keep claiming a
    name a DIFFERENT app could later register — which is how one app inherits another's
    earned rung. Unknown key is a no-op.
    """
    _REGISTRY.pop(key, None)
    for name in [n for n, k in _PROVIDER_INDEX.items() if k == key]:
        del _PROVIDER_INDEX[name]


def registered_action_types() -> tuple[ActionTypeSpec, ...]:
    """Every declared spec, ordered by key — the ladder panel's inventory."""
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def reset_action_types() -> None:
    """Drop every registration — invoked by an autouse test fixture so a type declared
    by one test never leaks into the next (the SEL/breaker/incident discipline)."""
    _REGISTRY.clear()
    _PROVIDER_INDEX.clear()


# ── the store (grants + demotions ONLY) ───────────────────────────────────────


@dataclass(frozen=True)
class Demotion:
    """One automatic demotion: when, why, and when re-eligibility reopens."""

    at: str = ""  # ISO 8601 UTC
    cause: str = ""
    cooldown_until: str = ""  # ISO 8601 UTC


@dataclass(frozen=True)
class RungGrant:
    """The persisted half of one type's state: the accepted grant + its demotions.

    ``evidence_window`` is the human-readable description of the record the user was
    shown when they clicked (``"12 approvals over 9 days"``), kept for the audit trail
    — NOT a cached count anything recomputes from. Nothing here is derived.
    """

    key: str
    rung: str = RUNG_DRAFT_ONLY
    granted_at: str = ""
    evidence_window: str = ""
    demotions: tuple[Demotion, ...] = ()


def _store_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _STORE_FILENAME


def _parse_grant(key: str, raw: object) -> RungGrant | None:
    """One store entry → a grant, or ``None`` when it cannot be proven.

    Fail closed per entry rather than per file: one malformed record must not erase the
    grants beside it, and it must not become a grant either.
    """
    if not isinstance(raw, dict):
        return None
    rung = str(raw.get("rung", "") or "")
    if rung_rank(rung) < 0:
        # An unknown rung name is not evidence of anything. Dropping the entry means
        # the type falls back to its declared floor.
        logger.warning("autonomy_rungs.json: %s has unknown rung %r — ignored", key, rung)
        return None
    demotions: list[Demotion] = []
    for d in raw.get("demotions") or ():
        if not isinstance(d, dict):
            continue
        demotions.append(
            Demotion(
                at=str(d.get("at", "") or ""),
                cause=str(d.get("cause", "") or ""),
                cooldown_until=str(d.get("cooldown_until", "") or ""),
            )
        )
    return RungGrant(
        key=key,
        rung=rung,
        granted_at=str(raw.get("granted_at", "") or ""),
        evidence_window=str(raw.get("evidence_window", "") or ""),
        demotions=tuple(demotions[-_MAX_DEMOTIONS:]),
    )


def _load_store() -> dict[str, RungGrant]:
    """Read the whole store from disk. NO in-process mirror on purpose.

    The file holds grants, which are rare and tiny, and a mirror is the shape that
    would make a stale grant outlive a demotion written by another process. An
    unreadable or non-object file yields an EMPTY store — a grant that cannot be read
    is a grant that cannot be proven.
    """
    path = _store_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("autonomy_rungs.json unreadable — no rung grants apply", exc_info=True)
        return {}
    if not isinstance(data, dict):
        logger.warning("autonomy_rungs.json is not an object — no rung grants apply")
        return {}
    out: dict[str, RungGrant] = {}
    for key, raw in data.items():
        grant = _parse_grant(str(key), raw)
        if grant is not None:
            out[str(key)] = grant
    return out


def _save_store(store: dict[str, RungGrant]) -> None:
    payload = {
        key: {
            "rung": g.rung,
            "granted_at": g.granted_at,
            "evidence_window": g.evidence_window,
            "demotions": [
                {"at": d.at, "cause": d.cause, "cooldown_until": d.cooldown_until}
                for d in g.demotions
            ],
        }
        for key, g in sorted(store.items())
    }
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def rung_state(key: str) -> RungGrant | None:
    """The persisted grant + demotion history for ``key``, or ``None``.

    The read side of the store: what the ladder panel renders and what the cooldown
    check consults. Never returns a derived field.
    """
    return _load_store().get(key)


# ── configuration ─────────────────────────────────────────────────────────────


def _config_rule() -> PromotionRule:
    """The operator's default evidence bar from ``guardrails.autonomy``.

    Lazy + best-effort so the module stays importable without a loaded config; a read
    failure falls back to the dataclass defaults, which are the plan's stated bar.
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().guardrails.autonomy
    except Exception:  # noqa: BLE001 — a config fault must not decide autonomy
        logger.debug("autonomy config read failed (using default thresholds)", exc_info=True)
        return PromotionRule()
    return PromotionRule(
        clean_approvals=cfg.clean_approvals,
        min_days=cfg.min_days,
        max_rejections=cfg.max_rejections,
        cooldown_days=cfg.cooldown_days,
    )


def _config_window_days() -> int:
    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().guardrails.autonomy.evidence_window_days
    except Exception:  # noqa: BLE001
        logger.debug("autonomy window config read failed (using 30d)", exc_info=True)
        return 30


def _rule_for(key: str) -> PromotionRule:
    """The evidence bar for one type: its own declared rule, else the operator's.

    A spec that left ``promotion`` at the default inherits ``guardrails.autonomy`` —
    so the config knob a user turns actually moves the bar for every ordinary type,
    while a type that declared a deliberate rule keeps it.
    """
    spec = _REGISTRY.get(key)
    if spec is not None and spec.promotion != PromotionRule():
        return spec.promotion
    return _config_rule()


# ── resolution ────────────────────────────────────────────────────────────────


def _clamp(rung: str, floor: str, ceiling: str) -> str:
    """``rung`` confined to ``[floor, ceiling]``, with every unknown name failing low."""
    fi = max(rung_rank(floor), 0)
    ci = rung_rank(ceiling)
    if ci < 0:
        ci = 0  # an unknown ceiling permits nothing above draft_only
    if ci < fi:
        # A malformed declaration (ceiling below floor). The lower bound wins: a
        # ceiling is a refusal, and a refusal outranks a convenience.
        fi = ci
    ri = rung_rank(rung)
    if ri < 0:
        ri = fi
    return RUNGS[min(max(ri, fi), ci)]


#: The highest ceiling an UNTRUSTED declaration (an app manifest) may ask for when the
#: action leaves the machine. ``autonomous`` means "executes silently, no undo handle,
#: no notification" — for an effect the user cannot take back from this machine that has
#: to be an in-tree decision, not a line in a manifest the installer skimmed.
MAX_UNTRUSTED_CEILING = RUNG_AUTO_WITH_UNDO


def clamp_untrusted_ceiling(key: str, ceiling: str, *, leaves_machine: bool) -> str:
    """The ceiling an app-supplied declaration actually gets, clamped and AUDITED.

    Core's own specs are reviewed in-tree, so a core type that declares
    ``ceiling=autonomous`` for a machine-leaving action has made the explicit raise the
    ladder asks for. An app's manifest has had no such review, so the same claim is
    clamped to :data:`MAX_UNTRUSTED_CEILING`.

    **The clamp is loud on purpose.** A silent downgrade is a recorded finding in this
    tree (``_validate_agent``): the app keeps working, nobody learns its declaration was
    overruled, and the manifest goes on claiming a rung it never had. So a clamp warns
    AND writes a SEL row naming both the asked-for and the granted ceiling.
    """
    if not leaves_machine:
        return ceiling
    if rung_rank(ceiling) <= rung_rank(MAX_UNTRUSTED_CEILING):
        return ceiling
    logger.warning(
        "autonomy: %s declares ceiling %r but its app reaches the network — clamped to %s",
        key,
        ceiling,
        MAX_UNTRUSTED_CEILING,
    )
    _audit(
        "autonomy_ceiling_clamped",
        key=key,
        detail=f"declared={ceiling} granted={MAX_UNTRUSTED_CEILING} reason=leaves_machine",
    )
    return MAX_UNTRUSTED_CEILING


def _cooldown_until(grant: RungGrant | None) -> str:
    """The latest ``cooldown_until`` on record, or ``""``."""
    if grant is None or not grant.demotions:
        return ""
    return max((d.cooldown_until for d in grant.demotions), default="")


def cooldown_date(until: str) -> str:
    """``cooldown_until`` as the DATE a user can read, or ``""`` if it does not parse.

    The wire keeps the full ISO instant; a sentence shown to a person gets the date. Day
    granularity is the honest granularity here — a cooldown is configured in whole days
    (``rule.cooldown_days``), so naming a minute would imply a precision the rule does not
    have. Returns ``""`` rather than guessing, because :func:`_in_cooldown` deliberately
    treats an unparseable timestamp as STILL RUNNING: the cooldown is real in that case,
    and only its end date is unknown, so the sentence has to be able to omit the date
    without omitting the cooldown.
    """
    parsed = _parse_iso(until)
    return parsed.date().isoformat() if parsed else ""


def _in_cooldown(grant: RungGrant | None, now: datetime) -> bool:
    until = _cooldown_until(grant)
    if not until:
        return False
    parsed = _parse_iso(until)
    if parsed is None:
        # An unparseable cooldown is treated as STILL RUNNING. The alternative reads a
        # corrupt timestamp as permission, which is the wrong direction for a control
        # whose whole job is to hold a type back after it misbehaved.
        logger.warning("autonomy cooldown timestamp %r unparseable — treated as active", until)
        return True
    return now < parsed


def granted_rung(key: str) -> str:
    """The rung the STORE grants, floor-and-ceiling clamped — no incident clamp.

    Separate from :func:`resolve_rung` because an incident is a temporary suspension,
    not a demotion: the ladder panel must still be able to say "this type is granted
    auto-with-undo, currently held at one-tap by the active incident".
    """
    spec = _REGISTRY.get(key)
    if spec is None:
        return RUNG_DRAFT_ONLY
    grant = _load_store().get(key)
    if grant is None or _in_cooldown(grant, datetime.now(timezone.utc)):
        return _clamp(spec.floor, spec.floor, spec.ceiling)
    return _clamp(grant.rung, spec.floor, spec.ceiling)


def resolve_rung(key: str) -> str:
    """The rung one action type may actually use right now.

    Floor plus any accepted grant, clamped to the type's ceiling, then clamped again
    to ``one_tap`` while an incident is active. An UNREGISTERED key resolves to
    ``draft_only``: nothing declared this type, so nothing licensed it.

    The incident clamp deliberately outranks the floor — ``personalclaw incident on``
    means nothing executes-with-undo and nothing runs autonomous, including a type
    whose declared floor sits higher. That is what makes it a kill switch rather than
    a suggestion.
    """
    if _REGISTRY.get(key) is None:
        return RUNG_DRAFT_ONLY
    resolved = granted_rung(key)
    if incident_active() and rung_rank(resolved) > rung_rank(RUNG_ONE_TAP):
        return RUNG_ONE_TAP
    return resolved


# ── derived eligibility ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Eligibility:
    """A recomputed promotion proposal. Never persisted, never a promotion.

    ``eligible`` says the evidence bar is cleared and ``next_rung`` is what a click
    would grant. ``reason`` always carries the user-facing explanation — for the
    ineligible case it says what is missing, which is the half a user actually needs.
    """

    key: str
    current_rung: str = RUNG_DRAFT_ONLY
    next_rung: str = ""
    eligible: bool = False
    clean_approvals: int = 0
    rejections: int = 0
    observed_days: float = 0.0
    reason: str = ""
    cooldown_until: str = ""


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_matches(event: dict, key: str) -> bool:
    """Is this SEL event about ``key``?

    Two attributions, both explicit: the ``action_type`` metadata a dispatch seam
    stamps on the approval event, or an ``operation`` that IS the type key. Nothing is
    inferred from a tool name — a guess here would credit one type with another's
    track record.
    """
    meta = event.get("metadata")
    if isinstance(meta, dict) and str(meta.get(SEL_ACTION_TYPE_KEY, "")) == key:
        return True
    return str(event.get("operation", "")) == key


def _sel_evidence(key: str, cutoff: datetime) -> tuple[int, int, float]:
    """(human approvals, rejections, observed span in days) for ``key`` since ``cutoff``.

    Reads the SEL tail only. The span is measured between the FIRST and LAST approval
    in the window, so "ten approvals over seven days" means what it says — ten
    approvals in one afternoon spans zero days and does not qualify.
    """
    from personalclaw.sel import sel

    approvals = 0
    rejections = 0
    stamps: list[datetime] = []
    for event in sel().recent(_SEL_SCAN_LIMIT):
        if not _event_matches(event, key):
            continue
        when = _parse_iso(str(event.get("timestamp", "")))
        if when is None or when < cutoff:
            continue
        outcome = str(event.get("outcome", ""))
        if outcome in _HUMAN_APPROVED_OUTCOMES:
            approvals += 1
            stamps.append(when)
        elif outcome.startswith(_REJECTED_PREFIXES):
            rejections += 1
    span = (max(stamps) - min(stamps)).total_seconds() / 86_400.0 if len(stamps) > 1 else 0.0
    return approvals, rejections, span


def _feedback_rejections(key: str, window_days: int) -> int:
    """👎 verdicts on this type's output in the window (FEEDBACK-SIGNAL, plan 58).

    Attribution is ``producer_id == key``: the surface that records feedback on an
    action's output stamps the action-type key as the producing artifact, so the
    thumbs and the approval verdicts land on the same identity without a mapping
    table. Counts the CURRENT verdict per target, so a re-thumb up cancels a down.
    """
    from personalclaw.feedback import producer_stats

    return sum(
        int(row.get("downs", 0))
        for (_kind, pid), row in producer_stats(window_days=window_days).items()
        if pid == key
    )


def promotion_eligibility(key: str) -> Eligibility:
    """Whether ``key`` has EARNED its next rung — recomputed, never read from disk.

    Derived on every call from the SEL approval verdicts and the FEEDBACK-SIGNAL
    thumbs for this type. Writes nothing: the return value is a proposal for a human
    to accept with :func:`grant_rung`, and the only thing this module ever persists is
    that accepted click (and any demotion).

    Ineligible, with the reason said plainly, when: the type is not registered; an
    incident is active; a demotion cooldown is still running; the type is already at
    its ceiling; the next rung would be ``autonomous`` for a type that leaves the
    machine; or the evidence bar is not met. Any failure to READ the evidence is also
    ineligible — an unreadable record proves nothing.
    """
    spec = _REGISTRY.get(key)
    if spec is None:
        return Eligibility(key=key, reason="This action type is not registered.")

    current = resolve_rung(key)
    grant = _load_store().get(key)
    now = datetime.now(timezone.utc)

    if incident_active():
        return Eligibility(
            key=key,
            current_rung=current,
            reason="An incident is active — autonomy stays held until it is resumed.",
        )

    cooldown_until = _cooldown_until(grant)
    if _in_cooldown(grant, now):
        # Every other branch of this sentence QUANTIFIES what is missing — "4 of 10 clean
        # approvals so far", "Approvals span 2.5 of the 14 days required". This one held the
        # concrete number (it is set on this very Eligibility) and did not say it, so the
        # panel told a demoted user they were in cooldown and gave them no way to learn when
        # it lifts. `explain_refused_grant` already names the date for the same fact, so the
        # two server-composed explanations of one thing disagreed.
        when = cooldown_date(cooldown_until)
        return Eligibility(
            key=key,
            current_rung=current,
            cooldown_until=cooldown_until,
            reason=(
                f"A recent demotion is still in cooldown until {when}."
                if when
                else "A recent demotion is still in cooldown."
            ),
        )

    base = granted_rung(key)
    next_index = rung_rank(base) + 1
    ceiling_index = max(rung_rank(spec.ceiling), 0)
    if next_index > ceiling_index or next_index >= len(RUNGS):
        return Eligibility(
            key=key,
            current_rung=current,
            reason=f"Already at its ceiling ({spec.ceiling}).",
        )
    next_rung = RUNGS[next_index]
    if next_rung == RUNG_AUTONOMOUS and spec.leaves_machine:
        return Eligibility(
            key=key,
            current_rung=current,
            reason=(
                "This action leaves the machine — fully autonomous has to be granted "
                "deliberately, never proposed from a track record."
            ),
        )

    rule = _rule_for(key)
    window_days = max(rule.min_days, _config_window_days())
    try:
        approvals, rejections, observed = _sel_evidence(key, now - timedelta(days=window_days))
        rejections += _feedback_rejections(key, window_days)
    except Exception:  # noqa: BLE001 — unreadable evidence proves nothing
        logger.warning("autonomy evidence read failed for %s", key, exc_info=True)
        return Eligibility(
            key=key,
            current_rung=current,
            reason="The track record could not be read, so nothing is proven yet.",
        )

    partial = Eligibility(
        key=key,
        current_rung=current,
        next_rung=next_rung,
        clean_approvals=approvals,
        rejections=rejections,
        observed_days=observed,
    )
    if rejections > rule.max_rejections:
        return replace(
            partial,
            reason=(
                f"{rejections} rejection(s) in the last {window_days} days "
                f"(at most {rule.max_rejections} allowed)."
            ),
        )
    if approvals < rule.clean_approvals:
        return replace(
            partial,
            reason=(f"{approvals} of {rule.clean_approvals} clean approvals so far."),
        )
    if observed < rule.min_days:
        return replace(
            partial,
            reason=(f"Approvals span {observed:.1f} of the {rule.min_days} days required."),
        )
    return replace(
        partial,
        eligible=True,
        reason=(
            f"{approvals} clean approvals over {observed:.1f} days with "
            f"{rejections} rejection(s)."
        ),
    )


# ── grant (the click) and demote (automatic) ───────────────────────────────────


def grant_rung(key: str, rung: str, *, evidence_window: str = "") -> str | None:
    """Record the user's accepted promotion. Returns the granted rung, or ``None``.

    This is the ONLY way a type moves up, and it exists to be called from a click.
    It enforces the structural rails and nothing else: the type must be registered,
    the rung must be on the ladder, at or below the ceiling, above what is already
    granted, and no demotion cooldown may be running. It deliberately does NOT
    require :func:`promotion_eligibility` — the owner's explicit decision has to stay
    expressible, and eligibility gates the *proposal*, not the person.
    """
    spec = _REGISTRY.get(key)
    if spec is None:
        logger.warning("autonomy grant refused: %s is not a registered action type", key)
        return None
    if rung_rank(rung) < 0:
        logger.warning("autonomy grant refused: unknown rung %r for %s", rung, key)
        return None
    if rung_rank(rung) > max(rung_rank(spec.ceiling), 0):
        logger.warning(
            "autonomy grant refused: %s ceilings at %s, cannot grant %s",
            key,
            spec.ceiling,
            rung,
        )
        return None
    store = _load_store()
    existing = store.get(key)
    now = datetime.now(timezone.utc)
    if _in_cooldown(existing, now):
        logger.warning("autonomy grant refused: %s is in demotion cooldown", key)
        return None
    if rung_rank(rung) <= rung_rank(granted_rung(key)):
        logger.info("autonomy grant is a no-op: %s already resolves at or above %s", key, rung)
        return None
    store[key] = RungGrant(
        key=key,
        rung=rung,
        granted_at=now.isoformat(),
        evidence_window=evidence_window[:_MAX_CAUSE_CHARS],
        demotions=existing.demotions if existing else (),
    )
    _save_store(store)
    _audit("autonomy_granted", key=key, detail=f"rung={rung} evidence={evidence_window}")
    logger.info("autonomy: %s granted rung %s", key, rung)
    return rung


def demote(key: str, cause: str) -> Demotion:
    """Drop ``key`` back to its floor NOW and start its cooldown.

    Called on the first rejection, undo, or 👎 for the type — no threshold, no
    averaging. The grant is replaced by a floor entry that carries the demotion
    history, so the type resolves at its floor again and cannot become eligible until
    the cooldown lapses. Works for a type with no grant on record: the cooldown is the
    point, and it must apply whether or not this type had climbed yet.
    """
    rule = _rule_for(key)
    spec = _REGISTRY.get(key)
    floor = spec.floor if spec is not None else RUNG_DRAFT_ONLY
    now = datetime.now(timezone.utc)
    record = Demotion(
        at=now.isoformat(),
        cause=(cause or "")[:_MAX_CAUSE_CHARS],
        cooldown_until=(now + timedelta(days=max(0, rule.cooldown_days))).isoformat(),
    )
    store = _load_store()
    existing = store.get(key)
    store[key] = RungGrant(
        key=key,
        rung=floor,
        granted_at="",
        evidence_window="",
        demotions=((existing.demotions if existing else ()) + (record,))[-_MAX_DEMOTIONS:],
    )
    _save_store(store)
    _audit(
        "autonomy_demoted", key=key, detail=f"cause={record.cause} until={record.cooldown_until}"
    )
    logger.warning("autonomy: %s demoted to %s (%s)", key, floor, record.cause or "no cause given")
    return record


def _audit(operation: str, *, key: str, detail: str) -> None:
    """SEL-audit a grant/demotion — the same treatment a skill install gets."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="autonomy",
            operation=f"guardrails.{operation}",
            outcome="ok",
            source="guardrails",
            resources=f"{key} {detail}"[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("autonomy SEL audit failed", exc_info=True)
