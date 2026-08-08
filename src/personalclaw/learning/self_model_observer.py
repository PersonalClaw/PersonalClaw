"""The self-model observer — the call site S72 deliberately left unbuilt (LEARN-R21 / §2.6 — LEA-8).

`learning/self_model.py` shipped the pure decisions (reinforcement, caps, promotion, the snapshot)
and recorded that it builds no store: "The observer's writes go through `MemoryService`, and the
proposals go through `learning.proposals`." This module is that observer. It is the ONLY writer of
the `user.selfmodel.*` rows `self_model.snapshot()` reads, so the two halves stop being inert.

**The reaction is only knowable one turn later — so it is measured, never guessed.** `Reaction`
names the signal "something the user DID"; the module's own `Observation.evidence` refuses to read
tool-success as acceptance. The one honest positive signal is the user *moving on to the next
step* — a subsequent non-correction turn — and the one honest negative is a subsequent correction.
Neither is visible at the end of the turn judged. So each turn's `(route, tools, outcome)` is
PARKED, and the FOLLOWING turn's correction-signal resolves it to an `ACCEPTED`/`CORRECTED` one. A
same-turn `NEUTRAL` default would be honest too, but it contributes nothing to confidence — the
reinforcement engine would never cross a threshold and the whole propose path would be dead code,
which is the exact inert-surface outcome this atom exists to remove.

**Propose, never install.** A crossed threshold files a `lesson_batch` PROPOSAL through the shared
human-gated queue, exactly like a lesson — this module never writes a `principle` entry. The live
`principle` rows the snapshot injects are written only when the user ACCEPTS, by the installer in
`dashboard/handlers/learning.py`. Everything written here is pre-promotion evidence rows
(`candidate.*`, the `retrospection` ring, the per-session `pending` observation), under the caps.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from personalclaw.learning.self_model import (
    CAPS,
    FACETS,
    KEY_PREFIX,
    MIN_SEEN_COUNT,
    Entry,
    Facet,
    Observation,
    Reaction,
    Reinforcement,
    build_proposal,
    plan_promotion,
    reinforce,
    trim_ring,
)

logger = logging.getLogger(__name__)

#: Storage confidence for a self-model bookkeeping row. These are not claims ABOUT the user (whose
#: writes must clear `semantic_confidence_threshold`); they are the harness's own internal state, so
#: they store at full confidence to pass validation and let a newer write win the conflict check.
_ROW_CONFIDENCE = 1.0
_ROW_SOURCE = "self_model"

#: Non-facet sub-namespaces under `user.selfmodel.*`. Kept OUT of `FACETS` on purpose: the snapshot
#: producer reconstructs an `Entry` only for a facet it recognises, so these evidence rows never
#: leak into the injected block the way a mis-classified row would.
_CANDIDATE = "candidate"
_PENDING = "pending"

#: How many recent observations a candidate row retains. `_MAX_VALUE_BYTES` (4096) caps a semantic
#: value, and `reinforce()` appends unboundedly; `seen_count`/`score` are the durable accumulator,
#: so only the recent window of observations is persisted — and `build_proposal` reads just 3.
_KEEP_OBSERVATIONS = 8


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _slug(text: str) -> str:
    """A key-safe short hash of a pattern/session string.

    `_validate_key` demands `^[a-z][a-z0-9_.]*[a-z0-9]$` with no consecutive dots, and a pattern
    like "PersonalClaw + edit_file,read_file" satisfies none of that. A hex digest is the stable,
    collision-resistant, always-valid key — the shape `memory_service` uses for its derived keys.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def observed_pattern(route: str, tools: tuple[str, ...]) -> str:
    """The coarse working-habit signature a turn reinforces.

    Route plus the SORTED distinct tool set, so "the harness handled this route with edit+read and
    it kept working" accretes evidence across turns instead of scattering into one-shot rows.
    Deliberately NOT the turn's content — §2.6's self-model is about working patterns, and a
    per-turn key would never recur to cross a threshold.
    """
    tool_sig = ",".join(sorted({t for t in tools if t})) or "no-tools"
    return f"{route or 'direct'} + {tool_sig}"


# ── row (de)serialization ──


def _candidate_key(pattern: str) -> str:
    return f"{KEY_PREFIX}.{_CANDIDATE}.{_slug(pattern)}"


def _pending_key(session_key: str) -> str:
    return f"{KEY_PREFIX}.{_PENDING}.{_slug(session_key or 'default')}"


def _retrospection_entry(observation: Observation) -> Entry:
    """One observation, as a retrospection ring entry (evidence, never injected).

    The body is the observation's shape, not its content: `retrospection` is excluded from
    `SNAPSHOT_FACETS`, so this is kept for promotion evidence, and a transcript here would make the
    self-model the transcript-with-a-cap the module warns against.
    """
    outcome = "success" if observation.succeeded else "failure"
    body = f"{observation.reaction} after {outcome} via {observation.route or 'direct'}"
    return Entry(
        facet=Facet.RETROSPECTION.value,
        key=_slug(f"{observation.pattern}|{observation.at}"),
        body=body,
        seen_count=1,
        confidence=1.0 if observation.succeeded else 0.0,
        created_at=observation.at,
        last_seen_at=observation.at,
    )


def _entry_from_row(key: str, value: Any) -> Entry | None:
    """Reconstruct a live `Entry` from a stored `user.selfmodel.<facet>.<key>` row, or None.

    Only rows whose facet segment is a real `Facet` become entries; a `candidate`/`pending` row
    parses to an unknown facet and is skipped, which is what keeps the evidence rows out of
    `over_cap`, `trim_ring`, and the snapshot without a separate exclusion list.
    """
    parts = key.split(".", 3)  # ["user", "selfmodel", facet, rest]
    if len(parts) < 4 or parts[2] not in FACETS:
        return None
    if not isinstance(value, dict):
        return None
    return Entry(
        facet=parts[2],
        key=str(value.get("key") or parts[3]),
        body=str(value.get("body") or ""),
        seen_count=int(value.get("seen_count") or 0),
        confidence=float(value.get("confidence") or 0.0),
        evidence=list(value.get("evidence") or []),
        created_at=str(value.get("created_at") or ""),
        last_seen_at=str(value.get("last_seen_at") or ""),
    )


def _load_value(service, key: str) -> Any:
    row = service.get_semantic(key)
    if not row:
        return None
    try:
        return json.loads(row["value_json"])
    except (KeyError, TypeError, ValueError):
        return None


def _write_row(service, key: str, value: dict) -> None:
    """Best-effort self-model row write. A rejection (e.g. an injection-pattern false hit in a tool
    name) is logged, never raised — a bookkeeping write must not cost the user their turn."""
    result = service.set_semantic(key, value, _ROW_CONFIDENCE, _ROW_SOURCE)
    if result is not None:
        code, reason = result
        logger.debug("self-model row %s rejected (%s): %s", key, code, reason)


def load_live_entries(service) -> list[Entry]:
    """Every live `user.selfmodel.<facet>.<key>` entry, for the snapshot producer and displacement.

    Public because the ambient producer (`context._self_model_snapshot`) needs the exact same read —
    two readers reconstructing entries two ways is how the injected snapshot and the promotion
    planner would come to disagree about what the self-model holds.
    """
    entries: list[Entry] = []
    for row in service.get_all_semantic():
        key = str(row.get("key") or "")
        if not key.startswith(f"{KEY_PREFIX}."):
            continue
        try:
            value = json.loads(row["value_json"])
        except (KeyError, TypeError, ValueError):
            continue
        entry = _entry_from_row(key, value)
        if entry is not None:
            entries.append(entry)
    return entries


def _load_candidate(service, pattern: str) -> Reinforcement | None:
    value = _load_value(service, _candidate_key(pattern))
    if not isinstance(value, dict):
        return None
    observations = [
        Observation(
            pattern=str(o.get("pattern") or pattern),
            route=str(o.get("route") or ""),
            tools=tuple(o.get("tools") or ()),
            succeeded=bool(o.get("succeeded", True)),
            reaction=str(o.get("reaction") or Reaction.NEUTRAL.value),
            at=str(o.get("at") or ""),
        )
        for o in value.get("observations") or []
        if isinstance(o, dict)
    ]
    return Reinforcement(
        pattern=pattern,
        seen_count=int(value.get("seen_count") or 0),
        score=float(value.get("score") or 0.0),
        observations=observations,
    )


def _save_candidate(service, record: Reinforcement) -> None:
    _write_row(
        service,
        _candidate_key(record.pattern),
        {
            "pattern": record.pattern,
            "seen_count": record.seen_count,
            "score": round(record.score, 4),
            # Retain only the recent window — the value has a hard 4KB ceiling and the counters
            # above already carry the durable evidence the thresholds test.
            "observations": [o.to_dict() for o in record.observations[-_KEEP_OBSERVATIONS:]],
        },
    )


def _append_retrospection(service, observation: Observation, live: list[Entry]) -> None:
    """Write the observation into the retrospection ring, then trim the ring to its cap.

    `trim_ring` decides which entries SURVIVE; this then DELETES the rows it dropped, because the
    ring is a store, not an in-memory list — leaving the overflow on disk would let `over_cap`
    report a ring that quietly outgrew its cap.
    """
    entry = _retrospection_entry(observation)
    _write_row(service, entry.memory_key, _entry_value(entry))
    ring = [e for e in live if e.facet == Facet.RETROSPECTION.value] + [entry]
    survivors = {e.memory_key for e in trim_ring(ring)}
    for e in ring:
        if e.memory_key not in survivors:
            service.delete_semantic(e.memory_key, source=_ROW_SOURCE)


def _entry_value(entry: Entry) -> dict:
    """The stored form of an `Entry` — `to_dict` minus the derived `memory_key` (not a field, so it
    must not round-trip back into the constructor)."""
    value = entry.to_dict()
    value.pop("memory_key", None)
    return value


# ── the observer ──


def _reaction_to_previous(correction: bool) -> str:
    """This turn's message, read as the user's reaction to the PREVIOUS turn's work.

    A correction is the strongest negative the module has; anything else is the user MOVING ON —
    which is exactly `Reaction.ACCEPTED`'s definition ("moved on to the next step"), and the only
    positive signal that is something the user DID rather than something the harness assumed.
    """
    return Reaction.CORRECTED.value if correction else Reaction.ACCEPTED.value


def observe_turn(
    service,
    *,
    session_key: str,
    route: str,
    tools: tuple[str, ...],
    succeeded: bool,
    correction: bool,
    staging_store=None,
    min_evidence: int = MIN_SEEN_COUNT,
    now: str = "",
) -> dict:
    """Fold one completed turn into the self-model. Returns a small report dict.

    The turn just seen is PARKED (its reaction is not yet observable). The previously-parked turn is
    RESOLVED with this turn's reaction, staged as a complete `(route, tools, outcome, reaction)`
    tuple, folded into its pattern's reinforcement, and — if the habit crosses §2.6's thresholds —
    filed as a `lesson_batch` PROPOSAL (never installed).

    Best-effort: never raises into the turn. Keys ``resolved``/``staged``/``proposed``/``pattern``.
    """
    report = {"resolved": False, "staged": False, "proposed": False, "pattern": ""}
    if service is None or not getattr(service, "has_vector", False):
        return report
    stamp = now or _now()
    try:
        pending = _load_value(service, _pending_key(session_key))
        if isinstance(pending, dict) and pending.get("pattern"):
            report.update(
                _resolve_pending(
                    service,
                    pending=pending,
                    correction=correction,
                    staging_store=staging_store,
                    min_evidence=min_evidence,
                )
            )
        # Park THIS turn for the next turn to resolve.
        pattern = observed_pattern(route, tools)
        _write_row(
            service,
            _pending_key(session_key),
            {
                "pattern": pattern,
                "route": route,
                "tools": sorted({t for t in tools if t}),
                "succeeded": bool(succeeded),
                "at": stamp,
            },
        )
    except Exception:  # noqa: BLE001 - a bookkeeping failure must never break the turn
        logger.debug("self-model observer failed", exc_info=True)
    return report


def _resolve_pending(
    service,
    *,
    pending: dict,
    correction: bool,
    staging_store,
    min_evidence: int,
) -> dict:
    """Resolve one parked observation with the now-observable reaction. Impure (writes rows)."""
    out = {"resolved": True, "staged": False, "proposed": False, "pattern": str(pending["pattern"])}
    observation = Observation(
        pattern=str(pending["pattern"]),
        route=str(pending.get("route") or ""),
        tools=tuple(pending.get("tools") or ()),
        succeeded=bool(pending.get("succeeded", True)),
        reaction=_reaction_to_previous(correction),
        at=str(pending.get("at") or _now()),
    )
    # (1) staging log — the append-only capture of the complete tuple (§2.6's first sentence).
    out["staged"] = _stage_observation(observation, staging_store)
    # (2) fold into the pattern's reinforcement + the capped retrospection ring.
    live = load_live_entries(service)
    record = reinforce(_load_candidate(service, observation.pattern), observation)
    _save_candidate(service, record)
    _append_retrospection(service, observation, live)
    # (3) propose — never install — when the habit is a reinforced, reliable principle.
    plan = plan_promotion(facet=Facet.PRINCIPLE.value, reinforcement=record, current=live)
    proposal = build_proposal(facet=Facet.PRINCIPLE.value, reinforcement=record, plan=plan)
    if proposal is not None:
        out["proposed"] = _file_proposal(proposal, record, min_evidence=min_evidence)
    return out


def _stage_observation(observation: Observation, staging_store) -> bool:
    """Append the resolved observation to the home-scoped staging log. Best-effort."""
    from personalclaw.learning.gate import Cadence
    from personalclaw.learning.staging import get_store

    store = staging_store if staging_store is not None else get_store()
    content = (
        f"self-model observation: {observation.pattern} → "
        f"{observation.reaction} after {'success' if observation.succeeded else 'failure'}"
    )
    return bool(
        store.stage(
            cadence=Cadence.PER_TURN.value,
            kind="self_model",
            content=content,
            meta=observation.to_dict(),
        )
    )


def _file_proposal(proposal, record: Reinforcement, *, min_evidence: int) -> bool:
    """Enqueue the principle as a `lesson_batch` proposal through the shared human-gated queue.

    The `target` is `user.selfmodel.principle` so `enqueue`'s fingerprint MATCHES
    `self_model.proposal_fingerprint` — a self-model principle the user already declined then
    collides with its own prior decision in the shared store instead of re-nagging under a new hash.

    `min_evidence` is §2.6's `MIN_SEEN_COUNT`, not the generic ≥3 floor: `plan_promotion` already
    enforced §2.6's conjunction (seen ≥ 2 AND confidence ≥ 0.72) to even PRODUCE this proposal, so
    re-gating at 3 here would silently discard a habit the module's own threshold judged promotable
    — dead code on the propose path, the exact inert outcome this atom removes.
    """
    from personalclaw.learning import proposals

    _verdict, filed = proposals.enqueue(
        kind=proposals.Kind.LESSON_BATCH.value,
        title=f"Observed working habit: {proposal.pattern}"[:120],
        body=proposal.body,
        target=f"{KEY_PREFIX}.{proposal.facet}",
        provenance="inferred",
        # The accept-installer discriminates self-model principles from ordinary lesson_batch
        # proposals on THIS cadence — a durable record field, not a tag a reviewer could edit.
        source_cadence="self_model",
        evidence_refs=list(proposal.evidence),
        evidence_strength="correlated",
        confidence=proposal.confidence,
        tags=["self_model", proposal.facet],
        occurrences=record.seen_count,
        min_evidence=min_evidence,
    )
    return filed is not None


# ── the accept-time installer (the writer the snapshot producer reads) ──


def is_self_model_proposal(proposal_dict: dict) -> bool:
    """Whether an accepted `lesson_batch` proposal is a self-model principle this installer owns.

    Keyed on `source_cadence` (a durable record field) rather than a tag a reviewer might strip —
    the accept handler routes only these to `install_accepted_principle`, so an ordinary
    correction-derived lesson_batch is left to its own (no-op) install path.
    """
    return str(proposal_dict.get("source_cadence") or "") == "self_model"


def _principle_slug(pattern: str) -> str:
    """A stable, key-valid slug for a promoted principle's live entry key.

    Joined with UNDERSCORES, not hyphens: `_validate_key` accepts `^[a-z][a-z0-9_.]*[a-z0-9]$`, so a
    hyphenated slug is rejected as KEY_FORMAT and the write silently no-ops. Prefers a readable slug
    of the pattern's words (so the key is legible in the store), else a hash when there are none.
    """
    words = re.findall(r"[a-z0-9]+", pattern.lower())
    slug = "_".join(words)[:48].strip("_")
    return slug or _slug(pattern)


def install_accepted_principle(service, proposal_dict: dict) -> bool:
    """Write an ACCEPTED principle into `user.selfmodel.principle.*` — the ONLY live-write path.

    Called by `dashboard/handlers/learning.py`'s accept installer AFTER `require_human` passed —
    this is the human installing, the one path §2.6 permits to write a principle. Enforces the cap
    on disk the way `plan_promotion` did on paper: if writing this entry would exceed the principle
    cap, the weakest EXISTING principle is displaced first, so a hand-edited or stale store can
    never leave the tier over its cap.
    """
    if service is None or not getattr(service, "has_vector", False):
        return False
    body = str(proposal_dict.get("body") or "").strip()
    if not body:
        return False
    pattern = str(proposal_dict.get("title") or body)
    entry = Entry(
        facet=Facet.PRINCIPLE.value,
        key=_principle_slug(pattern),
        body=body,
        seen_count=int(proposal_dict.get("reinforcements") or 0),
        confidence=float(proposal_dict.get("confidence") or 0.0),
        evidence=list(proposal_dict.get("evidence_refs") or []),
        created_at=_now(),
        last_seen_at=_now(),
    )
    _displace_for_cap(service, incoming=entry)
    _write_row(service, entry.memory_key, _entry_value(entry))
    return True


def _displace_for_cap(service, *, incoming: Entry) -> None:
    """Drop the weakest existing principle if admitting `incoming` would exceed the cap.

    Skips a row that shares `incoming`'s key (a re-accept UPDATES in place, not counted against the
    cap twice). "Weakest" matches `plan_promotion`'s own order — confidence, then seen_count, then
    age — so the disk enforcement and the planner agree on who yields.
    """
    cap = CAPS.get(Facet.PRINCIPLE.value, 0)
    existing = [
        e
        for e in load_live_entries(service)
        if e.facet == Facet.PRINCIPLE.value and e.key != incoming.key
    ]
    if len(existing) < cap:
        return
    weakest = min(existing, key=lambda e: (e.confidence, e.seen_count, e.created_at))
    service.delete_semantic(weakest.memory_key, source=_ROW_SOURCE)
