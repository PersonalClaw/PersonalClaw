"""The user-facing half of the earned-autonomy ladder (AUTONOMY-GUARDRAILS §6.1).

:mod:`~personalclaw.guardrails.autonomy` decides (ladder, declarations, grants, derived
eligibility) and :mod:`~personalclaw.guardrails.rungs` routes a dispatch seam. Both are
machine-facing. This module is the third piece: everything a **person** does with the
ladder, and the only place the ladder writes anything a user asked for.

**Three things live here.**

*The inventory* (:func:`ladder_view`) — one row per declared action type carrying the rung
it resolves at, where that rung came from (a declared floor, a grant you clicked, an
incident holding it down), the recomputed track record, and the demotion history. This is
what ``GET /api/autonomy`` serialises and the Settings panel renders, and it is
:func:`~personalclaw.guardrails.autonomy.promotion_eligibility`'s read-path caller.

*The proposal* (:func:`propose_promotions`) — the ladder's upward path is a click, so a
type that has EARNED its next rung has to say so somewhere the user already looks. This
files one deduped inbox proposal per (type, rung) and is driven by the gateway's daily
scan. It never promotes; it cannot promote, because nothing in this module calls
:func:`~personalclaw.guardrails.autonomy.grant_rung`.

*The undo* (:func:`reverse_action`) — ``auto_with_undo`` executed something and told the
user they can take it back. This is the executor that actually takes it back:

* The handle is the PROVIDER's (``ActionResult.reversal``) and stays opaque here. Only the
  provider knows what "undo" means for its own effect, so this dispatches
  :meth:`~personalclaw.action_providers.base.ActionProvider.reverse` on the provider the
  recorded action type declares, and never interprets the handle itself.
* A caller supplies a RECORD ID, never a handle. The handle comes out of our own
  persisted state (``autonomy_reversals.json``, written when the action ran), so a request
  cannot ask to reverse something the system never did — which is the entitlement hazard
  an undo endpoint has.
* **Fail closed, and say which.** An unknown record, an already-reversed one, a handle
  that will not parse, a type whose declaration is gone, a provider that cannot reverse
  that handle kind, a provider that refuses — each is a NAMED refusal that leaves the
  effect in place and, critically, does **not** demote. A demotion on a bogus handle would
  let a malformed request degrade a type's autonomy, which is a downgrade an attacker gets
  for free.
* A SUCCESSFUL reversal demotes the type immediately (``demote``): the user taking an
  automatic action back is the rejection the ladder listens to.

Every refusal and every success is logged AND SEL-audited. A silent clamp is a standing
finding in this tree; an undo that quietly does nothing would be the same defect with a
button on it.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.guardrails.autonomy import (
    RUNGS,
    Demotion,
    demote,
    granted_rung,
    promotion_eligibility,
    registered_action_types,
    resolve_rung,
    rung_rank,
    rung_state,
)

logger = logging.getLogger(__name__)

_STORE_FILENAME = "autonomy_reversals.json"

#: How many reversal records the store keeps. An undo handle is a small liability that
#: accumulates: it names something that still exists and can still be deleted. Bounding
#: the ring is what keeps that liability from growing forever on a machine that fires
#: unattended actions all day. The oldest records fall off first.
_MAX_RECORDS = 50

_MAX_HANDLE_CHARS = 200
_MAX_LABEL_CHARS = 120
#: A handle's KIND names which provider can reverse it. Closed shape on purpose: it is the
#: one part of an otherwise opaque handle this module reads.
_HANDLE_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: Sequences a handle may never contain. Nothing in this module puts a handle into a path,
#: a shell or a query — it goes to ``provider.reverse()`` and nowhere else — but a provider
#: that DOES resolve its handle against the filesystem must not be reachable with an
#: escape, and the cheapest place to stop that is the one boundary every handle crosses.
_HANDLE_FORBIDDEN = ("..", "/", "\\", "\x00")
_RECORD_ID_RE = re.compile(r"^rev_[0-9a-f]{16}$")


# ── the reversal record store ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ReversalRecord:
    """One executed ``auto_with_undo`` action that can still be taken back."""

    id: str
    action_type: str
    rung: str
    handle: str
    label: str = ""
    created_at: str = ""
    reversed_at: str = ""

    @property
    def pending(self) -> bool:
        return not self.reversed_at


@dataclass(frozen=True)
class ReversalOutcome:
    """The result of an undo attempt. ``code`` is the machine-readable refusal name."""

    ok: bool = False
    code: str = ""
    reason: str = ""
    action_type: str = ""
    demoted: bool = False


def _store_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _STORE_FILENAME


def parse_handle(handle: str) -> tuple[str, str] | None:
    """``"task:native:abc"`` → ``("task", "native:abc")``, or ``None`` when unusable.

    Bounded and closed: a printable, length-capped string whose first segment is a
    lowercase kind and whose remainder is non-empty and free of path-escape sequences.
    ``None`` is the fail-closed answer at BOTH ends — the writer refuses to record a
    handle it cannot parse (so no undo is ever offered for one), and the executor refuses
    to act on one that somehow reached the store anyway.
    """
    text = (handle or "").strip()
    if not text or len(text) > _MAX_HANDLE_CHARS:
        return None
    if not text.isprintable():
        return None
    if any(bad in text for bad in _HANDLE_FORBIDDEN):
        return None
    kind, sep, rest = text.partition(":")
    if not sep or not rest.strip():
        return None
    if not _HANDLE_KIND_RE.match(kind):
        return None
    return kind, rest


def _parse_record(raw: object) -> ReversalRecord | None:
    """One store entry → a record, or ``None`` when it cannot be trusted.

    Per-entry rather than per-file, matching ``autonomy._parse_grant``: one malformed row
    must neither erase the records beside it nor become an undo button.
    """
    if not isinstance(raw, dict):
        return None
    rid = str(raw.get("id", "") or "")
    if not _RECORD_ID_RE.match(rid):
        return None
    handle = str(raw.get("handle", "") or "")
    if parse_handle(handle) is None:
        logger.warning("autonomy_reversals.json: %s has an unusable handle — ignored", rid)
        return None
    rung = str(raw.get("rung", "") or "")
    return ReversalRecord(
        id=rid,
        action_type=str(raw.get("action_type", "") or ""),
        rung=rung if rung_rank(rung) >= 0 else "",
        handle=handle,
        label=str(raw.get("label", "") or "")[:_MAX_LABEL_CHARS],
        created_at=str(raw.get("created_at", "") or ""),
        reversed_at=str(raw.get("reversed_at", "") or ""),
    )


def _load_records() -> list[ReversalRecord]:
    """Every stored record, oldest first. Unreadable store → no records.

    No in-process mirror, for ``autonomy``'s reason: a mirror is how a record already
    reversed in another process comes back as an offer to reverse it again.
    """
    path = _store_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning("autonomy_reversals.json unreadable — no undo is offered", exc_info=True)
        return []
    rows = data.get("records") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        logger.warning("autonomy_reversals.json has no record list — no undo is offered")
        return []
    out: list[ReversalRecord] = []
    for raw in rows:
        record = _parse_record(raw)
        if record is not None:
            out.append(record)
    return out


def _save_records(records: list[ReversalRecord]) -> None:
    payload = {
        "records": [
            {
                "id": r.id,
                "action_type": r.action_type,
                "rung": r.rung,
                "handle": r.handle,
                "label": r.label,
                "created_at": r.created_at,
                "reversed_at": r.reversed_at,
            }
            for r in records[-_MAX_RECORDS:]
        ]
    }
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2) + "\n")


def record_reversal_handle(*, action_type: str, rung: str, handle: str, label: str = "") -> str:
    """Persist one reversible execution and return its record id (``""`` when refused).

    Called from :func:`~personalclaw.guardrails.rungs.record_reversal` — the seam path an
    ``auto_with_undo`` action already goes through — so the record has a production writer
    at every dispatch point rather than only in a test.

    Refuses (returning ``""``) a handle that will not parse. That is deliberate at the
    WRITE end: the notification's undo affordance is rendered from the record id, so a
    provider that returned garbage produces no button at all instead of a button whose
    only possible outcome is a refusal.
    """
    if parse_handle(handle) is None:
        logger.warning(
            "autonomy: %s returned an unusable reversal handle — no undo recorded", action_type
        )
        return ""
    record = ReversalRecord(
        id=f"rev_{secrets.token_hex(8)}",
        action_type=action_type,
        rung=rung,
        handle=handle.strip(),
        label=(label or action_type)[:_MAX_LABEL_CHARS],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        records = _load_records()
        records.append(record)
        _save_records(records)
    except OSError:
        logger.warning("autonomy: could not persist a reversal record", exc_info=True)
        return ""
    return record.id


def reversal_records() -> tuple[ReversalRecord, ...]:
    """Every stored record, NEWEST first — what the ladder panel lists."""
    return tuple(reversed(_load_records()))


def reversal_record(record_id: str) -> ReversalRecord | None:
    """One record by id, or ``None``. An id that is not even shaped like one never hits
    the store — it cannot match, and refusing early keeps the shape assertion in one
    place."""
    if not _RECORD_ID_RE.match(record_id or ""):
        return None
    for record in _load_records():
        if record.id == record_id:
            return record
    return None


def _mark_reversed(record_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    records = _load_records()
    updated = [
        ReversalRecord(
            id=r.id,
            action_type=r.action_type,
            rung=r.rung,
            handle=r.handle,
            label=r.label,
            created_at=r.created_at,
            reversed_at=now if r.id == record_id else r.reversed_at,
        )
        for r in records
    ]
    _save_records(updated)


# ── the undo executor ─────────────────────────────────────────────────────────


def _audit(operation: str, *, outcome: str, resources: str) -> None:
    """SEL-audit an undo attempt — success and refusal alike, so neither is silent."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="autonomy",
            operation=f"guardrails.{operation}",
            outcome=outcome,
            source="guardrails",
            resources=resources[:200],
        )
    except Exception:  # noqa: BLE001 — an audit failure must not change the outcome
        logger.debug("autonomy undo audit failed", exc_info=True)


def _refuse(code: str, reason: str, *, record_id: str, action_type: str = "") -> ReversalOutcome:
    logger.warning("autonomy undo refused (%s): %s [%s]", code, reason, record_id)
    _audit(
        "autonomy_reverse_refused",
        outcome="denied",
        resources=f"record={record_id} type={action_type} code={code}",
    )
    return ReversalOutcome(ok=False, code=code, reason=reason, action_type=action_type)


def _reverser_for(action_type: str, kind: str):
    """The action provider that can reverse a ``kind`` handle for ``action_type``.

    Resolution is bounded by the DECLARATION: only the providers the recorded action type
    claims are candidates (``ActionTypeSpec.providers``), and of those only one that says
    it reverses this handle kind. So a handle cannot be steered at an arbitrary provider,
    and a provider cannot be asked to reverse a kind it never claimed.
    """
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.guardrails.autonomy import action_type as _spec

    # Both registries, made self-sufficient rather than assumed — the same reasoning
    # ``route_provider_action`` gives. An undo request can arrive at a process that has
    # never dispatched an action, and "no provider is registered" would surface to the user
    # as "nothing can undo this any more", i.e. a permanent refusal caused by import order.
    # (Default provider registration also registers the core declarations.)
    _ensure_default_providers_registered()
    spec = _spec(action_type)
    if spec is None:
        return None
    for name in spec.providers:
        provider = get_action_provider(name)
        if provider is None:
            continue
        if kind in tuple(getattr(provider, "reversal_kinds", ()) or ()):
            return provider
    return None


async def reverse_action(record_id: str) -> ReversalOutcome:
    """Take back one ``auto_with_undo`` action, then demote its type.

    The undo click's whole implementation. On success the effect is gone AND the type is
    back at its floor with a cooldown running — one click, both halves, because a user who
    undoes an automatic action is telling the ladder it climbed too far.

    Every failure path leaves the effect ALONE and the type's rung UNTOUCHED.
    """
    record = reversal_record(record_id)
    if record is None:
        return _refuse(
            "unknown_record",
            "That undo is no longer on record, so there is nothing to take back.",
            record_id=record_id,
        )
    if not record.pending:
        return _refuse(
            "already_reversed",
            "This action was already undone.",
            record_id=record.id,
            action_type=record.action_type,
        )
    parsed = parse_handle(record.handle)
    if parsed is None:
        # Belt and braces: the writer refuses an unparseable handle and `_parse_record`
        # drops one, so reaching here means the store was edited by hand.
        return _refuse(
            "malformed_handle",
            "The record of what this action created is not usable, so it cannot be undone "
            "automatically.",
            record_id=record.id,
            action_type=record.action_type,
        )
    kind, _rest = parsed
    provider = _reverser_for(record.action_type, kind)
    if provider is None:
        return _refuse(
            "no_reverser",
            f"Nothing installed can undo a {kind!r} action any more — the provider that "
            "created it is not available.",
            record_id=record.id,
            action_type=record.action_type,
        )
    try:
        result = await provider.reverse(record.handle)
    except Exception as exc:  # noqa: BLE001 — a raising provider is a refusal, not a 500
        return _refuse(
            "provider_failed",
            f"{provider.display_name} could not undo it: {type(exc).__name__}: {exc}",
            record_id=record.id,
            action_type=record.action_type,
        )
    if not getattr(result, "success", False):
        return _refuse(
            "provider_refused",
            str(getattr(result, "error", "") or "")
            or f"{provider.display_name} could not undo it.",
            record_id=record.id,
            action_type=record.action_type,
        )

    _mark_reversed(record.id)
    record_demotion: Demotion = demote(record.action_type, "you undid an action it took on its own")
    _audit(
        "autonomy_reversed",
        outcome="ok",
        resources=(
            f"record={record.id} type={record.action_type} handle={record.handle} "
            f"demoted_until={record_demotion.cooldown_until}"
        ),
    )
    logger.info(
        "autonomy: reversed %s (%s) and demoted %s",
        record.handle,
        record.id,
        record.action_type,
    )
    return ReversalOutcome(
        ok=True,
        reason=str(getattr(result, "stdout", "") or "") or "Undone.",
        action_type=record.action_type,
        demoted=True,
    )


# ── the inventory the panel renders ───────────────────────────────────────────


def _authority_sentence(spec, resolved: str, granted: str, state, held: bool) -> str:
    """WHERE this type's current rung came from, in one sentence.

    The chip's whole job (done_when 3) is to answer "why is this allowed to run by itself?"
    at a glance, and the honest answer is never the rung name alone — it is the rung PLUS
    its provenance. There are exactly three provenances: an incident is holding a granted
    rung down, you granted it (and here is the record you were shown), or nobody has
    promoted it and it is running at the rung it was declared with. Composed here so the
    chip, its tooltip and the ladder panel cannot describe the same authority differently.
    """
    from personalclaw.guardrails.rungs import RUNG_LABELS

    def _label(rung: str) -> str:
        return RUNG_LABELS.get(rung, rung)

    if held:
        return (
            f"Granted {_label(granted)}, held at {_label(resolved)} while the incident "
            "kill switch is active."
        )
    if state is not None and state.granted_at and rung_rank(granted) > rung_rank(spec.floor):
        when = state.granted_at[:10]
        evidence = f" — {state.evidence_window}" if state.evidence_window else ""
        return f"You promoted this to {_label(granted)} on {when}{evidence}."
    return (
        f"Runs at {_label(resolved)} because that is the rung it was declared with; "
        "it has never been promoted."
    )


def _type_row(spec) -> dict:
    """One action type, as the panel needs it: the rung, WHERE it came from, the record."""
    resolved = resolve_rung(spec.key)
    granted = granted_rung(spec.key)
    state = rung_state(spec.key)
    el = promotion_eligibility(spec.key)
    held = rung_rank(granted) > rung_rank(resolved)
    return {
        "key": spec.key,
        "floor": spec.floor,
        "ceiling": spec.ceiling,
        "leaves_machine": spec.leaves_machine,
        "providers": list(spec.providers),
        "resolved_rung": resolved,
        "granted_rung": granted,
        # The one derived flag the panel cannot compute from the two rungs without
        # re-deriving the incident clamp: "granted higher, held here for now".
        "held_by_incident": held,
        "authority": _authority_sentence(spec, resolved, granted, state, held),
        "granted_at": state.granted_at if state else "",
        "evidence_window": state.evidence_window if state else "",
        "demotions": [
            {"at": d.at, "cause": d.cause, "cooldown_until": d.cooldown_until}
            for d in (state.demotions if state else ())
        ],
        "eligible": el.eligible,
        "next_rung": el.next_rung,
        "record": el.reason,
        "clean_approvals": el.clean_approvals,
        "rejections": el.rejections,
        "observed_days": round(el.observed_days, 1),
        "cooldown_until": el.cooldown_until,
    }


def ladder_view() -> dict:
    """The whole ladder as one serialisable dict — every declared type plus the undo list.

    Blocking (it reads the SEL tail once per type), so the HTTP handler runs it off the
    event loop. Registers the core declarations first: a panel that enumerated only what
    the current process happened to have registered would show a DIFFERENT inventory
    depending on which surface the user hit first.
    """
    from personalclaw.guardrails.incident import incident_active
    from personalclaw.guardrails.rungs import ensure_core_action_types

    ensure_core_action_types()
    return {
        "rungs": list(RUNGS),
        "incident_active": incident_active(),
        "types": [_type_row(spec) for spec in registered_action_types()],
        "reversals": [
            {
                "id": r.id,
                "action_type": r.action_type,
                "rung": r.rung,
                "label": r.label,
                "created_at": r.created_at,
                "reversed_at": r.reversed_at,
            }
            for r in reversal_records()
        ],
    }


def explain_refused_grant(key: str, rung: str) -> str:
    """Why a grant that :func:`~personalclaw.guardrails.autonomy.grant_rung` ALREADY
    refused was refused.

    Explanation only, and deliberately called *after* the refusal: ``grant_rung`` owns the
    decision (registration, ladder membership, ceiling, cooldown, no-op) and re-deciding it
    here would be a second authorization path to keep in sync. This reads the same state to
    produce the sentence that decision did not carry.
    """
    from personalclaw.guardrails.autonomy import action_type as _spec

    spec = _spec(key)
    if spec is None:
        return f"{key} is not a registered action type."
    if rung_rank(rung) < 0:
        return f"{rung!r} is not a rung on the ladder."
    if rung_rank(rung) > rung_rank(spec.ceiling):
        return f"{key} can never go above {spec.ceiling} — that is its declared ceiling."
    state = rung_state(key)
    cooldown = max((d.cooldown_until for d in (state.demotions if state else ())), default="")
    if cooldown:
        el = promotion_eligibility(key)
        if el.cooldown_until:
            return (
                f"{key} was demoted recently — it cannot be promoted again until "
                f"{el.cooldown_until}."
            )
    return f"{key} already runs at {granted_rung(key)} or higher."


# ── the promotion proposal ────────────────────────────────────────────────────


def propose_promotions() -> list[str]:
    """File one inbox proposal per action type that has EARNED its next rung.

    The ladder only climbs on a click, which means the offer has to travel to the user;
    a proposal that only exists inside a Settings panel is one nobody sees. Returns the
    keys proposed.

    Deduped per (type, next rung) by :func:`~personalclaw.inbox.emit_attention_item`, so a
    scan that runs every few hours leaves ONE standing row per earned rung however many
    times it runs — and a type that climbs again later gets a new row, because the dedup
    key carries the rung.

    Writes nothing to the rung store. This function cannot promote: it does not call
    ``grant_rung``, and ``test_nothing_but_the_api_grants_a_rung`` pins that.
    """
    from personalclaw.guardrails.rungs import ensure_core_action_types

    ensure_core_action_types()
    proposed: list[str] = []
    for spec in registered_action_types():
        try:
            el = promotion_eligibility(spec.key)
        except Exception:  # noqa: BLE001 — one unreadable type must not stop the scan
            logger.warning("autonomy: eligibility scan failed for %s", spec.key, exc_info=True)
            continue
        if not el.eligible or not el.next_rung:
            continue
        if _file_proposal(spec.key, el.next_rung, el.reason):
            proposed.append(spec.key)
    return proposed


def _file_proposal(key: str, next_rung: str, record: str) -> bool:
    """Raise the standing proposal row for one earned rung. Best-effort."""
    from personalclaw.guardrails.rungs import RUNG_LABELS

    try:
        from personalclaw.inbox import emit_attention_item
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        try:
            state = get_dashboard_state()
        except Exception:  # noqa: BLE001 — headless: the row still persists
            state = None
        emit_attention_item(
            state,
            source="skills",
            kind="proposal",
            item_kind="proposal",
            title=f"{key} has earned {RUNG_LABELS.get(next_rung, next_rung)}",
            body=(
                f"{record} You can promote it in Settings → Guardrails, or leave it where "
                "it is. Nothing changes until you do."
            ),
            refs={"action_type": key, "rung": next_rung, "surface": "settings/guardrails"},
            dedup_key=f"autonomy_promotion:{key}:{next_rung}",
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("autonomy: could not file a promotion proposal for %s", key, exc_info=True)
        return False
