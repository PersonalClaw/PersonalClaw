"""ConfirmationRequest: ONE durable typed record for every gate (TASKS-SOPS §4, R6 — S57).

Four flows asked the user for something and none of them said what an approval IS as data: checklist
sign-offs, workflow approval gates, needs-input questions, destructive-action confirmations. Four
bespoke paths meant four inbox surfaces and three-and-a-half autonomy policies.

One entity fixes that, and the entity is only worth having if it is durable — a record that
lived in
memory would lose every pending approval on a restart, which is precisely the moment a user
comes back
to answer one.

The properties that decide the shape:

* **Single-use resolution is a CLAIM, not a check.** `human_input.consume_continuation` owns it, and
  this session fixed it: it used to read-then-`unlink`, and measured, 8 racing resumes had multiple
  callers receive the payload in 36 of 40 trials. `os.rename` decides the winner BEFORE anything is
  read (0 of 40). Double-clicking Resume can now never replay a clarification downstream.
* **Auto-resume must not re-execute completed stages.** The run pauses ON the record;
resolution hands
  the answer to the waiting node. Re-running from the top would spend the whole run's tokens
  again and
  produce different output than the user approved.
* **`payload_preview` is redacted at CONSTRUCTION.** A preview is the field most likely to carry a
  fetched credential into an inbox row, and redacting at render time means every surface has to
  remember.
* **An expired record follows a declared policy, never a default.** Auto-rejecting a destructive
  confirmation is safe; auto-rejecting a needs-input question throws away work. So the policy is
  per-type, and it is written down.

Pure records and decisions. Persistence rides the existing continuation store — one
claim primitive, one directory, one audit trail — rather than a second one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Default lifetime of a pending confirmation. A week, because the realistic case is a user who is
#: away — and a gate that expired overnight would turn "I was travelling" into lost work.
DEFAULT_TTL_SECS = 7 * 24 * 3600

#: Characters of payload kept in the preview. An inbox row is a glance; a preview that scrolls is a
#: preview nobody reads, and the full payload is one deep link away.
MAX_PREVIEW_CHARS = 400


class ConfirmationType(str, Enum):
    """What is being asked. Three, because they expire differently and route differently.

    `DESTRUCTIVE_CONFIRM` is the one that must never auto-approve on timeout. `NEEDS_INPUT` is
    the one
    that must never auto-reject, because the answer is work the user still intends to give.
    """

    APPROVAL = "approval"
    NEEDS_INPUT = "needs_input"
    DESTRUCTIVE_CONFIRM = "destructive_confirm"


class Status(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class ExpiryPolicy(str, Enum):
    """What happens when a record ages out.

    `HOLD` is not a non-decision — it is the decision that the run waits indefinitely rather than
    guessing. For a needs-input question that is the only honest option: the user's answer does not
    become unnecessary because they were slow.
    """

    AUTO_REJECT = "auto_reject"
    HOLD = "hold"


#: Per-type expiry policy, declared rather than defaulted. Auto-rejecting a destructive
#: confirmation is
#: safe (the action does not happen); auto-rejecting a needs-input question throws away the
#: work that
#: was waiting on the answer. A single global default would have to be wrong for one of them.
EXPIRY_POLICY: dict[ConfirmationType, ExpiryPolicy] = {
    ConfirmationType.APPROVAL: ExpiryPolicy.HOLD,
    ConfirmationType.NEEDS_INPUT: ExpiryPolicy.HOLD,
    ConfirmationType.DESTRUCTIVE_CONFIRM: ExpiryPolicy.AUTO_REJECT,
}

#: Resolutions the needs-input queue accepts. `SKIP` leaves the item pending for the next pass
#: — which
#: is different from rejecting it, and a queue without it forces a user to answer in the order the
#: engine happened to ask.
RESOLUTIONS = ("approve", "reject", "skip", "quit")


@dataclass
class ConfirmationRequest:
    """One durable typed record. The whole point is that all four flows are this.

    `gate_id` and `run_id` are separate from `id` because the record outlives the node
    instance: after
    a rewind the same gate may ask again, and a reader needs to tell "this approval" from
    "this gate".
    """

    id: str
    run_id: str
    gate_id: str
    type: ConfirmationType = ConfirmationType.APPROVAL
    risk_category: str = ""
    title: str = ""
    payload_preview: str = ""
    requested_at: float = 0.0
    ttl_seconds: int = DEFAULT_TTL_SECS
    status: Status = Status.PENDING
    resolved_by: str = ""
    resolution_note: str = ""
    #: The resume token that carries this answer back to the waiting node. The claim primitive lives
    #: with the token, not here, so there is ONE place a resolution can be consumed.
    resume_token: str = ""

    @property
    def expiry_policy(self) -> ExpiryPolicy:
        return EXPIRY_POLICY.get(self.type, ExpiryPolicy.HOLD)

    def expires_at(self) -> float:
        """Absolute expiry, or 0.0 when it never expires.

        `ttl_seconds <= 0` means no expiry rather than instant expiry. An author who
        writes `ttl: 0` means "wait for me", and reading that as "expire immediately"
        would auto-resolve the very gate they were trying to hold open.
        """
        if self.ttl_seconds <= 0 or not self.requested_at:
            return 0.0
        return self.requested_at + self.ttl_seconds

    def expired(self, now: float) -> bool:
        deadline = self.expires_at()
        return bool(deadline) and now >= deadline

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "gate_id": self.gate_id,
            "type": self.type.value,
            "risk_category": self.risk_category,
            "title": self.title,
            "payload_preview": self.payload_preview,
            "requested_at": self.requested_at,
            "ttl_seconds": self.ttl_seconds,
            "status": self.status.value,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
            "resume_token": self.resume_token,
            "expires_at": self.expires_at(),
            "expiry_policy": self.expiry_policy.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConfirmationRequest:
        """Tolerant read. An unknown TYPE becomes `approval`, whose policy is HOLD.

        HOLD is the safe landing for a type this build does not recognize: the run waits for a human
        rather than auto-resolving something nobody could classify.
        """
        d = d or {}
        try:
            kind = ConfirmationType(str(d.get("type", "") or "approval"))
        except ValueError:
            kind = ConfirmationType.APPROVAL
        try:
            status = Status(str(d.get("status", "") or "pending"))
        except ValueError:
            status = Status.PENDING
        return cls(
            id=str(d.get("id", "") or ""),
            run_id=str(d.get("run_id", "") or ""),
            gate_id=str(d.get("gate_id", "") or ""),
            type=kind,
            risk_category=str(d.get("risk_category", "") or ""),
            title=str(d.get("title", "") or ""),
            payload_preview=str(d.get("payload_preview", "") or ""),
            requested_at=float(d.get("requested_at", 0.0) or 0.0),
            ttl_seconds=int(d.get("ttl_seconds", DEFAULT_TTL_SECS) or 0),
            status=status,
            resolved_by=str(d.get("resolved_by", "") or ""),
            resolution_note=str(d.get("resolution_note", "") or ""),
            resume_token=str(d.get("resume_token", "") or ""),
        )


def request_id(run_id: str, gate_id: str, epoch: int) -> str:
    """A deterministic id for one (run, gate, epoch).

    Deterministic so a re-emitted request for the same waiting gate is recognizably the same record
    rather than a second row in the inbox. The epoch is in the key because a rewind SHOULD produce a
    new request — the question is being asked about different work.
    """
    basis = f"{run_id}\n{gate_id}\n{epoch}"
    return "cr-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]  # noqa: S324 — an id


def redact_preview(payload: Any) -> str:
    """The preview, redacted at CONSTRUCTION and bounded.

    Through `security.redact` — the existing chokepoint — rather than a local pattern set: a
    second
    redactor drifts from the maintained one, and the drift surfaces as a credential in an inbox row.
    Redacting here rather than at render means a surface cannot forget.

    Fails CLOSED: if redaction is unavailable the preview is withheld, because a preview is
    the field
    most likely to carry a fetched credential and an unredacted one is worse than none.
    """
    # `None` is checked BEFORE stringifying. Measured: `str(None)` is `"None"`, which is not empty,
    # so an absent payload previewed as the literal word "None" in an inbox row — a value the user
    # would read as content the run produced.
    if payload is None:
        return ""
    text = payload if isinstance(payload, str) else str(payload)
    if not text.strip():
        return ""
    try:
        from personalclaw.security import redact

        cleaned = redact(text)
    except Exception:
        return "[preview withheld: redaction unavailable]"
    return cleaned[:MAX_PREVIEW_CHARS]


def build_request(
    *,
    run_id: str,
    gate_id: str,
    epoch: int = 0,
    kind: ConfirmationType = ConfirmationType.APPROVAL,
    title: str = "",
    payload: Any = "",
    risk_category: str = "",
    resume_token: str = "",
    now: float = 0.0,
    ttl_seconds: int | None = None,
) -> ConfirmationRequest:
    """Build a request with its preview already redacted.

    `ttl_seconds` defaults to the CONFIG value, not the module constant:
    `workflows.confirmation_ttl_secs` is live-editable, and reading the constant would make an
    owner's change to the approval lifetime storable and ignored (S61k).
    """
    if ttl_seconds is None:
        from personalclaw.workflows.settings import confirmation_ttl_secs

        ttl_seconds = confirmation_ttl_secs()
    return ConfirmationRequest(
        id=request_id(run_id, gate_id, epoch),
        run_id=run_id,
        gate_id=gate_id,
        type=kind,
        risk_category=risk_category,
        title=title or f"{gate_id} needs your decision",
        payload_preview=redact_preview(payload),
        requested_at=now,
        ttl_seconds=ttl_seconds,
        resume_token=resume_token,
    )


@dataclass
class Resolution:
    """The outcome of resolving a request.

    `resumes` is separate from `approved` because they answer different questions. A
    REJECT resolves the record AND resumes the run, down the declined path; a SKIP
    resolves nothing and leaves the item pending. Collapsing them would either strand
    a rejected run or lose a skip.
    """

    verb: str
    approved: bool = False
    resumes: bool = False
    still_pending: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb,
            "approved": self.approved,
            "resumes": self.resumes,
            "still_pending": self.still_pending,
            "note": self.note,
        }


def resolve(verb: str, *, note: str = "") -> tuple[Resolution | None, str]:
    """Interpret a resolution verb. Returns `(resolution, error)`.

    An unknown verb is REFUSED rather than treated as a reject. A typo silently rejecting an
    approval
    would decline work the user meant to allow, and the user would not know why.
    """
    word = (verb or "").strip().lower()
    if word not in RESOLUTIONS:
        return (
            None,
            f"unknown resolution {word or '(empty)'!r}; expected one of {list(RESOLUTIONS)}",
        )
    if word == "approve":
        return Resolution(verb=word, approved=True, resumes=True, note=note), ""
    if word == "reject":
        # Resolves AND resumes: the run continues down its declined path. Leaving it pending would
        # strand a run whose answer has been given.
        return Resolution(verb=word, approved=False, resumes=True, note=note), ""
    if word == "skip":
        # Leaves the item pending for the next pass — different from rejecting it. Without skip, a
        # user has to answer in the order the engine happened to ask.
        return (
            Resolution(verb=word, approved=False, resumes=False, still_pending=True, note=note),
            "",
        )
    # quit: stop asking about this one, and do not resume. The run stays paused for a human.
    return Resolution(verb=word, approved=False, resumes=False, note=note), ""


def on_expiry(request: ConfirmationRequest, now: float) -> tuple[Status, Resolution | None, str]:
    """What an expired record becomes, per its declared policy.

    A destructive confirmation AUTO-REJECTS: the action does not happen, which is the recoverable
    direction — and auto-approving a destructive action because nobody looked is the single worst
    behaviour this module could have.

    An approval or needs-input HOLDS: the answer is still wanted, and the user being slow does
    not make
    the work unnecessary.
    """
    if not request.expired(now):
        return request.status, None, ""
    policy = request.expiry_policy
    if policy is ExpiryPolicy.AUTO_REJECT:
        return (
            Status.EXPIRED,
            Resolution(verb="reject", approved=False, resumes=True, note="expired unanswered"),
            f"{request.type.value} expired and was auto-REJECTED — the action did not happen",
        )
    return (
        Status.PENDING,
        None,
        f"{request.type.value} expired but is HELD — the answer is still wanted",
    )


def requires_hitl(node_config: dict[str, Any]) -> bool:
    """Whether a stage declares `require_hitl: true`.

    Approval as a PROPERTY of the step, so an author gates a stage without structurally inserting a
    gate node — which would change the graph shape, the progress widget, and every path-addressed
    binding downstream.
    """
    return bool((node_config or {}).get("require_hitl") is True)


#: Per-stage mute: gate kinds a run may suppress for a stage the user has decided not to be asked
#: about again. Deliberately NOT including destructive confirmations — "stop asking me about
#: deletions"
#: is a request to remove the last check before an unrecoverable action.
MUTABLE_TYPES = frozenset({ConfirmationType.APPROVAL, ConfirmationType.NEEDS_INPUT})


def may_mute(kind: ConfirmationType) -> tuple[bool, str]:
    """Whether this confirmation type may be muted for a stage, and why not when it may not."""
    if kind in MUTABLE_TYPES:
        return True, ""
    return False, (
        f"{kind.value} cannot be muted — muting the last check before an unrecoverable action is "
        "the one setting that cannot be undone by changing it back"
    )


#: Tool profiles: named bundles a stage may run under, so an author picks a POSTURE rather than
#: enumerating tools. Reuses S48's capability vocabulary rather than inventing a parallel one
#: — two
#: least-privilege vocabularies would disagree about a tool, and the looser one would win.
TOOL_PROFILES: dict[str, dict[str, Any]] = {
    "read_only": {
        "capability": "research",
        "confirm": (),
        "note": "reads only; no write tool is reachable",
    },
    "write_local": {
        "capability": "mutating",
        "confirm": (ConfirmationType.APPROVAL,),
        "note": "may write in its own workspace; an approval gate precedes the first write",
    },
    "outward": {
        "capability": "mutating",
        "confirm": (ConfirmationType.APPROVAL, ConfirmationType.DESTRUCTIVE_CONFIRM),
        "note": "may publish or send; every outward action is confirmed",
    },
}


def profile(name: str) -> tuple[dict[str, Any] | None, str]:
    """Resolve a tool profile by name.

    An unknown name is REFUSED rather than defaulted to the loosest or the strictest.
    Defaulting loose
    would silently grant a stage more than the author asked for; defaulting strict would
    silently break
    a stage that needs to write, and the author would debug the wrong thing.
    """
    found = TOOL_PROFILES.get((name or "").strip().lower())
    if found is None:
        return None, f"unknown tool profile {name!r}; expected one of {sorted(TOOL_PROFILES)}"
    return dict(found), ""


def audit_fields(request: ConfirmationRequest, resolution: Resolution) -> dict[str, Any]:
    """SEL fields for a resolution.

    Every resolution is audited like any other security-relevant decision. The RESOLVER is recorded
    because "who approved this" is the question an audit exists to answer, and a log that
    records only
    that an approval happened cannot answer it.
    """
    return {
        "operation": f"confirmation.{resolution.verb}",
        "confirmation_id": request.id,
        "run_id": request.run_id,
        "gate_id": request.gate_id,
        "type": request.type.value,
        "risk_category": request.risk_category,
        "resolved_by": request.resolved_by or "unknown",
        "approved": resolution.approved,
    }


@dataclass
class DagViewCard:
    """The payload the FE DagView's declared-but-unwired `onApprove`/`onDeny` needs.

    Built here rather than in the FE so the two surfaces (inbox row and DAG node) render ONE record.
    Two builders would drift, and the drift shows as a node that offers Approve for a gate the inbox
    already resolved.
    """

    confirmation_id: str
    node_id: str
    title: str
    preview: str
    awaiting: bool
    can_approve: bool
    can_deny: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "node_id": self.node_id,
            "title": self.title,
            "preview": self.preview,
            "awaiting": self.awaiting,
            "can_approve": self.can_approve,
            "can_deny": self.can_deny,
        }


def dag_card(request: ConfirmationRequest) -> DagViewCard:
    """One request as a DAG node card.

    `awaiting` is false for a resolved record, and both verbs go false with it — a node still
    offering
    Approve after the gate was answered is how a user double-approves, which is
    exactly what the claim primitive had to be fixed to prevent.
    primitive had to be fixed to prevent.
    """
    live = request.status is Status.PENDING
    return DagViewCard(
        confirmation_id=request.id,
        node_id=request.gate_id,
        title=request.title,
        preview=request.payload_preview,
        awaiting=live,
        can_approve=live,
        can_deny=live,
    )
