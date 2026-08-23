"""Routing proposals — the learned stage PROPOSES, a human decides (MRT-5 §6.3-6.4).

The learned scoring stage can measure that one of the user's own bindings beats another for a
kind of request. It must never act on that alone. ``routing_policy.json`` is the user's table:
the three levers in :mod:`personalclaw.routing.policy` (mode, pin, order) are theirs, and a
telemetry fold quietly rewriting lever 3 would mean the machine changed which provider sees the
user's content without anyone deciding to. So a quality gap does not write — it **enqueues a
proposal** here, with the evidence that justified it, and waits.

**Propose-don't-write is the whole design.** Nothing in this module's proposal path touches
``routing_policy.json``; ``tests/test_routing_proposals.py`` asserts the file is byte-identical
across a ``propose``. :func:`accept` is the only writer, and it is reached only from a human
decision. :func:`reject` writes no table at all — it records a suppression so the same finding
cannot re-nag.

**Shape borrowed from** :mod:`personalclaw.skills.proposals` (the other propose-only queue in
this tree): a bounded queue, a ``None`` return rather than an exception when the queue is full or
the inputs are empty, and untrusted text FENCED so a poisoned record can't direct a model that
later renders it. Nothing is imported from there — a skill proposal and a routing proposal share
a posture, not a schema.

**The store.** ``<home>/routing_proposals.json``, beside ``routing_policy.json`` and
``routing_stats.json`` — one small JSON file written with :func:`atomic_write`, the convention
every other routing store follows. It holds the queue AND the rejection ledger, because a
cooldown that lived in memory would reset on every gateway restart and therefore would not be a
cooldown. A missing or corrupt file reads as an empty queue and never raises: this module is
observability-grade like the rest of the routing package, and failing a model call because a
proposal store was unreadable would invert the priority.

**What "the same proposal" means for the cooldown** (``routing.reproposal_cooldown_days``):
``(use_case, query_class, the ref being promoted to first)``. Keying on the whole proposed order
would let a one-token reorder of the tail re-nag the moment it was rejected; keying on
``(use_case, query_class)`` alone would swallow a genuinely different finding — "try the OTHER
local model first" — for a fortnight. A rejection means "no, don't put *that* ref first for this
kind of request", so that is exactly what is suppressed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.routing import policy

logger = logging.getLogger(__name__)

#: File under the home; small JSON, atomic_write (the universal convention).
_QUEUE_FILE = "routing_proposals.json"
#: Bump when the queue's schema changes.
QUEUE_VERSION = 1
#: The proposal kind this module enqueues. One value, used consistently: the SEL operation
#: prefix and the queue record both spell it, so "what kind of proposal is this row?" has one
#: answer. Deliberately NOT a new ``notification_kinds`` pair — delivery reuses the registered
#: ``INFO`` kind rather than minting a sixteenth registry entry for one emitter.
ROUTING_PROPOSAL_KIND = "routing"
#: Cap so a chatty learned stage cannot flood the queue (mirrors skills/proposals.py's bound).
_MAX_PENDING = 50
#: Fallback when ``AppConfig`` is unreadable — the declared default of the config field.
_DEFAULT_COOLDOWN_DAYS = 14
#: Per-string cap on free-text evidence before fencing.
_EVIDENCE_TEXT_MAX = 1_000
#: How many audit ids a proposal may carry (a human inspects a sample, not the population).
_MAX_SAMPLE_IDS = 20
#: Evidence keys whose values are machine-generated identifiers, not prose — kept verbatim so a
#: reviewer can paste one into the audit reader. Everything else that is a string gets fenced.
_ID_LIST_KEYS = frozenset({"sample_audit_ids"})


@dataclass
class RoutingProposal:
    """One pending, human-reviewable routing-order change.

    ``current``/``proposed`` are both permutations of the same candidate refs — this queue
    inherits :mod:`policy`'s pure-reorder contract and never proposes adding or dropping a
    binding. ``evidence`` is what makes the proposal reviewable without re-running anything.
    """

    id: str
    use_case: str
    query_class: str
    current: list[str]
    proposed: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    kind: str = ROUTING_PROPOSAL_KIND
    status: str = "pending"  # pending | accepted | rejected | refused
    decided_at: str = ""
    refusal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """The compact view for a list surface (evidence stays out of the row)."""
        return {
            "id": self.id,
            "use_case": self.use_case,
            "query_class": self.query_class,
            "current": list(self.current),
            "proposed": list(self.proposed),
            "created_at": self.created_at,
            "status": self.status,
        }


# ── the store ───────────────────────────────────────────────────────────────────


def _queue_path(home: Path) -> Path:
    return Path(home) / _QUEUE_FILE


def _empty_queue() -> dict[str, Any]:
    return {"version": QUEUE_VERSION, "proposals": [], "rejections": {}}


def _default_home() -> Path | None:
    """The live config dir, resolved lazily so this module imports without a configured home
    (and so a test can point it at ``tmp_path``)."""
    try:
        from personalclaw.config import config_dir

        return Path(config_dir())
    except Exception:  # noqa: BLE001 — no home configured is not a routing failure
        return None


def _resolve_home(home: Path | None) -> Path | None:
    return Path(home) if home is not None else _default_home()


def load_queue(home: Path | None = None) -> dict[str, Any]:
    """Read the queue. A missing/corrupt file reads as an empty queue (never fatal)."""
    resolved = _resolve_home(home)
    if resolved is None:
        return _empty_queue()
    try:
        data = json.loads(_queue_path(resolved).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return _empty_queue()
    if not isinstance(data, dict):
        return _empty_queue()
    data.setdefault("version", QUEUE_VERSION)
    if not isinstance(data.get("proposals"), list):
        data["proposals"] = []
    if not isinstance(data.get("rejections"), dict):
        data["rejections"] = {}
    return data


def _save_queue(home: Path, queue: dict[str, Any]) -> bool:
    """Persist the queue. Returns False on a write failure rather than raising — the caller
    decides whether that loses a proposal (it does) or a decision (it must not)."""
    try:
        atomic_write(_queue_path(home), json.dumps(queue, indent=2, sort_keys=True) + "\n")
        return True
    except OSError:
        logger.debug("routing proposal queue write failed", exc_info=True)
        return False


def _record(raw: object) -> RoutingProposal | None:
    """One stored row → a proposal, or None when the row is unreadable. A single corrupt
    record must not make the whole queue unreadable."""
    if not isinstance(raw, dict):
        return None
    try:
        prop = RoutingProposal(**raw)
    except TypeError:
        logger.debug("unreadable routing proposal record dropped", exc_info=True)
        return None
    if not isinstance(prop.current, list) or not isinstance(prop.proposed, list):
        return None
    if not isinstance(prop.evidence, dict):
        prop.evidence = {}
    return prop


def _records(queue: dict[str, Any]) -> list[RoutingProposal]:
    out: list[RoutingProposal] = []
    for raw in queue.get("proposals", []):
        prop = _record(raw)
        if prop is not None:
            out.append(prop)
    return out


# ── cooldown ────────────────────────────────────────────────────────────────────


def suppression_key(use_case: str, query_class: str, proposed: list[str]) -> str:
    """The identity a rejection suppresses: ``(use_case, query_class, the promoted ref)``.

    See the module docstring for why the head ref and not the whole order: the tail is noise a
    user did not reject, and the head is the whole substance of "try this one first".
    """
    head = str(proposed[0]) if proposed else ""
    return f"{use_case}|{query_class}|{head}"


def _cooldown_days() -> int:
    try:
        from personalclaw.config import AppConfig

        return max(0, int(AppConfig.load().routing.reproposal_cooldown_days))
    except Exception:  # noqa: BLE001 — an unreadable config must not silence the cooldown
        return _DEFAULT_COOLDOWN_DAYS


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _in_cooldown(queue: dict[str, Any], key: str, now: datetime) -> bool:
    """Whether *key* was rejected recently enough to still be suppressed.

    An unparseable rejection timestamp reads as EXPIRED, not as suppressed. This module never
    writes the policy table, so the cost of one proposal too many is a notification; the cost of
    a corrupt byte silencing a real finding forever is a router that quietly stays wrong.
    """
    days = _cooldown_days()
    if days <= 0:
        return False
    rejected_at = _parse_ts(queue.get("rejections", {}).get(key))
    if rejected_at is None:
        return False
    return now < rejected_at + timedelta(days=days)


# ── evidence ────────────────────────────────────────────────────────────────────


def _fence(text: str) -> str:
    try:
        from personalclaw.security import fence_untrusted

        return fence_untrusted(text[:_EVIDENCE_TEXT_MAX], source="routing-telemetry")
    except Exception:  # noqa: BLE001 — never let fencing failure block the proposal
        return ""


def _clean_value(key: str, value: object, *, depth: int = 0) -> object:
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, str):
        return value[:_EVIDENCE_TEXT_MAX] if key in _ID_LIST_KEYS else _fence(value)
    if isinstance(value, (list, tuple)):
        items = list(value)[:_MAX_SAMPLE_IDS] if key in _ID_LIST_KEYS else list(value)
        return [_clean_value(key, v, depth=depth + 1) for v in items]
    if isinstance(value, dict) and depth < 3:
        return {str(k)[:200]: _clean_value(str(k), v, depth=depth + 1) for k, v in value.items()}
    return None


def _clean_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Make evidence storable and safe to render.

    Numbers and identifier lists pass through — they are the whole point, and a reviewer must be
    able to paste a ``sample_audit_ids`` entry straight into the audit reader. Every OTHER string
    is FENCED: ``evidence`` is a free dict, so nothing stops a caller from folding a model's own
    words into a ``note``, and a proposal is rendered back to a human (and possibly to a model
    summarising the queue) long after whoever wrote it is gone.
    """
    if not isinstance(evidence, dict):
        return {}
    return {str(k)[:200]: _clean_value(str(k), v) for k, v in evidence.items()}


# ── propose (NEVER writes routing_policy.json) ──────────────────────────────────


def _make_id(use_case: str, query_class: str, proposed: list[str], created_at: str) -> str:
    payload = f"{use_case}|{query_class}|{','.join(proposed)}|{created_at}"
    return "rp-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _now(now: str = "") -> str:
    return now or datetime.now(tz=timezone.utc).isoformat()


def propose(
    *,
    use_case: str,
    query_class: str,
    current: list[str],
    proposed: list[str],
    evidence: dict[str, Any],
    home: Path | None = None,
    now: str = "",
) -> RoutingProposal | None:
    """Enqueue a routing proposal. **NEVER writes ``routing_policy.json``.**

    Returns ``None`` — never raises — when the proposal is suppressed: empty inputs, a proposed
    order identical to the current one, a full queue, an already-pending proposal for the same
    thing, a rejection still inside ``reproposal_cooldown_days``, or no home to persist to. A
    learned stage calls this on a schedule, so "nothing to say" is the common case and must be
    cheap and quiet rather than exceptional.
    """
    resolved = _resolve_home(home)
    if resolved is None:
        return None
    proposed = [str(r) for r in (proposed or [])]
    current = [str(r) for r in (current or [])]
    if not (use_case and query_class and proposed):
        return None
    if proposed == current:
        return None  # nothing to decide

    queue = load_queue(resolved)
    key = suppression_key(use_case, query_class, proposed)
    stamp = _now(now)
    parsed_now = _parse_ts(stamp) or datetime.now(tz=timezone.utc)

    if _in_cooldown(queue, key, parsed_now):
        logger.debug("routing proposal suppressed by cooldown: %s", key)
        return None

    live = [p for p in _records(queue) if p.status == "pending"]
    if len(live) >= _MAX_PENDING:
        logger.info("routing proposal queue full (%d); dropping %s", _MAX_PENDING, key)
        return None
    if any(suppression_key(p.use_case, p.query_class, p.proposed) == key for p in live):
        logger.debug("routing proposal already pending: %s", key)
        return None

    prop = RoutingProposal(
        id=_make_id(use_case, query_class, proposed, stamp),
        use_case=use_case,
        query_class=query_class,
        current=current,
        proposed=proposed,
        evidence=_clean_evidence(evidence),
        created_at=stamp,
    )
    queue.setdefault("proposals", []).append(prop.to_dict())
    if not _save_queue(resolved, queue):
        return None
    logger.info("Queued routing proposal %s (%s)", prop.id, key)
    _notify(prop)
    return prop


def pending(*, home: Path | None = None) -> list[RoutingProposal]:
    """Every undecided proposal, oldest first. The inspectable queue a human reviews."""
    props = [p for p in _records(load_queue(home)) if p.status == "pending"]
    return sorted(props, key=lambda p: (p.created_at, p.id))


# ── accept / reject ─────────────────────────────────────────────────────────────


def _find_pending(queue: dict[str, Any], proposal_id: str) -> tuple[int, RoutingProposal] | None:
    for idx, raw in enumerate(queue.get("proposals", [])):
        prop = _record(raw)
        if prop is not None and prop.id == proposal_id and prop.status == "pending":
            return idx, prop
    return None


def accept(proposal_id: str, *, home: Path | None = None) -> bool:
    """Apply a proposal to the policy table. **The only writer in this module.**

    Refuses (returns ``False``, writing no table) when the cell's current ``basis`` is
    ``{"source": "user"}``. That is :mod:`policy`'s own stated invariant — a hand-set order is
    lever 3, which "the learned stage may later propose changing but never silently overwrite"
    (§6.3) — and the refusal is recorded on the proposal so the surface can say why rather than
    appearing to do nothing. A user who wants the proposed order still has lever 3 in front of
    them, and setting it by hand records the truth: that a person chose it.

    Writes the table, THEN logs one SEL row naming the proposal. If the SEL write raises, the
    acceptance STANDS and the failure is logged: the table change already happened, so raising
    would report a failure for a change that applied, and rolling back would throw away a
    decision a human made in order to protect an audit line. Same posture as
    ``policy._sel_policy_change`` — "an audit failure must not lose the user's edit".
    """
    resolved = _resolve_home(home)
    if resolved is None:
        return False
    queue = load_queue(resolved)
    found = _find_pending(queue, proposal_id)
    if found is None:
        return False
    idx, prop = found
    stamp = _now()

    basis = policy.order_basis(prop.use_case, prop.query_class, home=resolved)
    if basis.get("source") == "user":
        prop.status = "refused"
        prop.decided_at = stamp
        prop.refusal_reason = "a hand-set order owns this cell; set it by hand to change it"
        queue["proposals"][idx] = prop.to_dict()
        _save_queue(resolved, queue)
        logger.info("routing proposal %s refused: user-set basis", prop.id)
        return False

    policy.set_order(
        prop.use_case,
        prop.query_class,
        list(prop.proposed),
        home=resolved,
        basis={
            "source": "proposal",
            "proposal_id": prop.id,
            "accepted_at": stamp,
        },
    )

    prop.status = "accepted"
    prop.decided_at = stamp
    queue["proposals"][idx] = prop.to_dict()
    _save_queue(resolved, queue)
    _sel_decision(prop, "accept")
    logger.info("Accepted routing proposal %s", prop.id)
    return True


def reject(proposal_id: str, *, home: Path | None = None) -> bool:
    """Decline a proposal and suppress the same finding for ``reproposal_cooldown_days``.

    Writes NO policy table — a rejection means the table was right. The suppression lands in the
    same on-disk queue as the proposals, so it survives a restart; a cooldown held in memory
    would reset every time the gateway came up and would re-nag on the next fold.
    """
    resolved = _resolve_home(home)
    if resolved is None:
        return False
    queue = load_queue(resolved)
    found = _find_pending(queue, proposal_id)
    if found is None:
        return False
    idx, prop = found
    stamp = _now()
    prop.status = "rejected"
    prop.decided_at = stamp
    queue["proposals"][idx] = prop.to_dict()
    rejections = queue.setdefault("rejections", {})
    if not isinstance(rejections, dict):
        rejections = {}
        queue["rejections"] = rejections
    rejections[suppression_key(prop.use_case, prop.query_class, prop.proposed)] = stamp
    if not _save_queue(resolved, queue):
        return False
    _sel_decision(prop, "reject")
    logger.info("Rejected routing proposal %s", prop.id)
    return True


# ── audit + notification ────────────────────────────────────────────────────────


def _sel_decision(prop: RoutingProposal, decision: str) -> None:
    """SEL-record one proposal decision (§6.4).

    Routing decides which providers see which content, so a decision that changes the table — or
    that silences a class of suggestion for a fortnight — is security-relevant. Best-effort by
    design: see :func:`accept` for why an audit failure must not undo the user's decision.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="user",
            operation=f"routing.proposal.{decision}",
            outcome="success",
            source="routing_proposals",
            resources=f"{prop.id}:{prop.use_case}:{prop.query_class}:{','.join(prop.proposed)}",
        )
    except Exception:  # noqa: BLE001 — audit must never break or undo the decision
        logger.debug("routing proposal SEL record failed", exc_info=True)


def _notify(prop: RoutingProposal) -> None:
    """Surface a new proposal through the single delivery choke point.

    Reached from a fold, not a request, so the dashboard state is fetched through the same
    process-wide accessor ``guardrails/rungs.py`` uses and is ``None`` when headless. The kind is
    the already-registered ``INFO`` — a proposal is not a sixteenth notification domain, and an
    unregistered ``(source, kind)`` pair would resolve to ``system/generic`` and lose its rules
    row. Best-effort: surfacing must never fail the enqueue, which is already durable on disk.
    """
    try:
        from personalclaw import notification_kinds
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            return
        state.notify(
            notification_kinds.INFO,
            "A routing change is proposed",
            f"{prop.use_case} / {prop.query_class}: try {prop.proposed[0]} first. "
            "Nothing changed — review the evidence and decide.",
            meta={
                "kind_detail": ROUTING_PROPOSAL_KIND,
                "routing_proposal": prop.id,
                "use_case": prop.use_case,
                "query_class": prop.query_class,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("routing proposal notify failed", exc_info=True)
