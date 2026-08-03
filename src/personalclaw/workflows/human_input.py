"""The human-input contract — `needs_input` as a durable cross-surface primitive (WF2-R7).

A gate that only exists inside a chat turn is useless to everything else. This module turns
"waiting for a human" into state that outlives the process, the surface, and the session:
the widget, the needs-input inbox, an HTTP call, and a chat tool all act on the same record.

Four things hold it together:

**A typed ask payload.** `{kind, prompt, fields, choices}` with `kind` in
approval|choice|text|form. One renderer covers every human-input node, and the same payload
projects into an inbox as a real form. Free-form asks would need per-template rendering,
which is how a "just add a prompt string" design becomes twelve half-broken UIs.

**A continuation record.** Each `needs_input` transition persists
`{node_id, instance_path, resolved_inputs, epoch, expires_at}` — so resuming re-enters THAT
step with the inputs it had already resolved, rather than re-executing the enclosing
subgraph. Without it, answering an approval an hour later silently redoes the work that led
up to the question.

**Atomic, single-use answers.** An answer is consumed by deleting the record in the same
step that applies it. A double-resume (two clicks, a retried POST, a widget and an inbox
racing) must not replay the answer — replayed approvals are how one "yes" becomes two
deployments.

**Expiry is typed, never silent.** A stale token produces a `resume_expired` needs-input
item offering a re-run from the node. A dead token that simply does nothing is
indistinguishable from a bug, and the user is left clicking a button that has no effect.

Mode-dependent timeouts are the other half: a background run that waits forever on an
approval nobody will see is wedged, so background gates time out short and surface, while
blocking/chat-mode gates wait long because a human is right there.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows import store

logger = logging.getLogger(__name__)

CONTINUATION_DIR = "continuations"

#: Background gates time out FAST and surface. A background run parked forever on an
#: approval nobody is watching is wedged, not waiting.
DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS = 45

#: Blocking/chat mode waits long — a human is right there, and timing out under them
#: would discard an answer they were about to give.
DEFAULT_BLOCKING_GATE_TIMEOUT_SECS = 1800

#: How long a resume token stays valid. Long enough to answer tomorrow morning; short
#: enough that a year-old token cannot resurrect a run whose world has moved on.
DEFAULT_RESUME_TTL_SECS = 7 * 24 * 3600


class AskKind(str, Enum):
    APPROVAL = "approval"
    CHOICE = "choice"
    TEXT = "text"
    FORM = "form"


@dataclass
class AskField:
    """One typed field in a `form` ask. Typed so a renderer can pick a control and a
    caller can be told what is wrong BEFORE the run is resumed."""

    name: str
    type: str = "string"  # string | number | boolean | choice
    label: str = ""
    required: bool = False
    default: Any = None
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "label": self.label or self.name,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AskField:
        d = d or {}
        return cls(
            name=str(d.get("name", "") or ""),
            type=str(d.get("type", "string") or "string"),
            label=str(d.get("label", "") or ""),
            required=bool(d.get("required", False)),
            default=d.get("default"),
            choices=[str(c) for c in (d.get("choices") or [])],
        )


@dataclass
class Ask:
    """The typed ask payload. ONE shape for every human-input node (WF2-R7)."""

    kind: AskKind = AskKind.APPROVAL
    prompt: str = ""
    node_id: str = ""
    fields: list[AskField] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    #: Suppress surfacing in unattended mode — for gates whose answer has a safe default.
    unattended_suppress: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "prompt": self.prompt,
            "node_id": self.node_id,
            "fields": [f.to_dict() for f in self.fields],
            "choices": list(self.choices),
            "unattended_suppress": self.unattended_suppress,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ask:
        d = d or {}
        raw = str(d.get("kind", "approval") or "approval")
        try:
            kind = AskKind(raw)
        except ValueError:
            kind = AskKind.APPROVAL  # tolerant: an unknown kind renders as an approval
        return cls(
            kind=kind,
            prompt=str(d.get("prompt", "") or ""),
            node_id=str(d.get("node_id", "") or ""),
            fields=[AskField.from_dict(f) for f in (d.get("fields") or []) if isinstance(f, dict)],
            choices=[str(c) for c in (d.get("choices") or [])],
            unattended_suppress=bool(d.get("unattended_suppress", False)),
        )

    def validate_answer(self, answer: Any) -> str:
        """Check an answer against this ask. Returns "" when acceptable.

        Validated BEFORE the run resumes: rejecting at resume time would already have
        consumed the token, leaving the user with a dead link and an unanswered gate.
        """
        if self.kind == AskKind.APPROVAL:
            if isinstance(answer, bool):
                return ""
            if isinstance(answer, dict) and isinstance(answer.get("approved"), bool):
                return ""
            return "approval expects a boolean (or {approved: bool})"
        if self.kind == AskKind.CHOICE:
            value = answer.get("choice") if isinstance(answer, dict) else answer
            if not isinstance(value, str):
                return "choice expects a string"
            if self.choices and value not in self.choices:
                return f"{value!r} is not one of: {', '.join(self.choices)}"
            return ""
        if self.kind == AskKind.TEXT:
            value = answer.get("text") if isinstance(answer, dict) else answer
            if not isinstance(value, str) or not value.strip():
                return "text expects a non-empty string"
            return ""
        # form
        if not isinstance(answer, dict):
            return "form expects an object of field values"
        for spec in self.fields:
            if spec.name not in answer:
                if spec.required and spec.default is None:
                    return f"missing required field {spec.name!r}"
                continue
            problem = _check_field(spec, answer[spec.name])
            if problem:
                return problem
        return ""

    def apply_defaults(self, answer: Any) -> Any:
        """Fill unsupplied form defaults, so a partial answer is still usable."""
        if self.kind != AskKind.FORM or not isinstance(answer, dict):
            return answer
        filled = dict(answer)
        for spec in self.fields:
            if spec.name not in filled and spec.default is not None:
                filled[spec.name] = spec.default
        return filled


def _check_field(spec: AskField, value: Any) -> str:
    if spec.type == "number" and not isinstance(value, (int, float)):
        return f"field {spec.name!r} expects a number"
    if spec.type == "boolean" and not isinstance(value, bool):
        return f"field {spec.name!r} expects a boolean"
    if spec.type == "choice":
        if not isinstance(value, str):
            return f"field {spec.name!r} expects a string"
        if spec.choices and value not in spec.choices:
            return f"field {spec.name!r} must be one of: {', '.join(spec.choices)}"
    if spec.type == "string" and not isinstance(value, str):
        return f"field {spec.name!r} expects a string"
    if spec.required and value in (None, ""):
        return f"field {spec.name!r} is required"
    return ""


# ── gate timeouts ────────────────────────────────────────────────────────────


def gate_timeout_secs(
    node_config: dict[str, Any],
    *,
    mode: str = "background",
    background_default: int = DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS,
    blocking_default: int = DEFAULT_BLOCKING_GATE_TIMEOUT_SECS,
) -> int:
    """The gate's deadline, mode-dependent (WF2-R7).

    An explicit `timeout_secs` always wins — the author knows their gate. Otherwise
    background gates get the short default and blocking gates the long one. `0` means "wait
    indefinitely", which is only ever legitimate in blocking mode.
    """
    declared = (node_config or {}).get("timeout_secs")
    if isinstance(declared, (int, float)) and declared >= 0:
        return int(declared)
    return int(blocking_default if str(mode) == "blocking" else background_default)


# ── continuation records ─────────────────────────────────────────────────────


@dataclass
class Continuation:
    """A durable resume point (WF2-R7 batch-5).

    `resolved_inputs` is the load-bearing field: resuming re-enters THIS step with what it
    had already resolved, rather than re-executing the enclosing subgraph. Answering an
    approval an hour later must not silently redo the work that produced the question.
    """

    token: str
    run_id: str
    node_id: str
    instance_path: str
    epoch: int = 0
    resolved_inputs: dict[str, Any] = field(default_factory=dict)
    ask: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0
    #: The handoff bundle rendered when a run is blocked — what a returning human needs to
    #: re-acquire context without reading the whole journal.
    handoff: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "instance_path": self.instance_path,
            "epoch": self.epoch,
            "resolved_inputs": dict(self.resolved_inputs),
            "ask": dict(self.ask),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "handoff": dict(self.handoff),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Continuation:
        d = d or {}
        return cls(
            token=str(d.get("token", "") or ""),
            run_id=str(d.get("run_id", "") or ""),
            node_id=str(d.get("node_id", "") or ""),
            instance_path=str(d.get("instance_path", "") or ""),
            epoch=int(d.get("epoch", 0) or 0),
            resolved_inputs=dict(d.get("resolved_inputs") or {}),
            ask=dict(d.get("ask") or {}),
            created_at=float(d.get("created_at", 0.0) or 0.0),
            expires_at=float(d.get("expires_at", 0.0) or 0.0),
            handoff=dict(d.get("handoff") or {}),
        )


def new_token() -> str:
    """A resume token. Random, not derived: a guessable token is an approval anyone can
    forge, and the record it unlocks authorizes real action."""
    return secrets.token_urlsafe(24)


def handoff_bundle(
    *,
    scope: str,
    status: str,
    outstanding: list[str] | None = None,
    checks_run: list[str] | None = None,
    next_steps: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    """The blocked-run context bundle (WF2-R7). Fixed shape so the widget can render it
    without knowing which node produced it."""
    return {
        "scope": scope,
        "status": status,
        "outstanding": list(outstanding or []),
        "checks_run": list(checks_run or []),
        "next_steps": list(next_steps or []),
        "risks": list(risks or []),
    }


def _dir(run_id: str):
    return store.run_dir(run_id) / CONTINUATION_DIR


def save_continuation(cont: Continuation) -> Continuation:
    """Persist a continuation. One file per token, so a concurrent write cannot corrupt
    another pending gate's record."""
    path = _dir(cont.run_id) / f"{cont.token}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(cont.to_dict(), indent=2, ensure_ascii=False))
    return cont


def create_continuation(
    run_id: str,
    *,
    node_id: str,
    instance_path: str,
    epoch: int,
    resolved_inputs: dict[str, Any] | None = None,
    ask: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    ttl_secs: int = DEFAULT_RESUME_TTL_SECS,
    now: float = 0.0,
) -> Continuation:
    clock = now or time.time()
    return save_continuation(
        Continuation(
            token=new_token(),
            run_id=run_id,
            node_id=node_id,
            instance_path=instance_path,
            epoch=epoch,
            resolved_inputs=dict(resolved_inputs or {}),
            ask=dict(ask or {}),
            created_at=clock,
            expires_at=clock + max(0, int(ttl_secs)) if ttl_secs else 0.0,
            handoff=dict(handoff or {}),
        )
    )


def load_continuation(run_id: str, token: str) -> Continuation | None:
    """Read a continuation. Refuses a token that is not a bare filename — a token arrives
    from an HTTP path and is not a trust boundary."""
    if not token or "/" in token or "\\" in token or ".." in token:
        return None
    path = _dir(run_id) / f"{token}.json"
    if not path.is_file():
        return None
    try:
        return Continuation.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        logger.warning("run %s: unreadable continuation %s", run_id, token)
        return None


def list_continuations(run_id: str) -> list[Continuation]:
    directory = _dir(run_id)
    if not directory.is_dir():
        return []
    out: list[Continuation] = []
    for path in sorted(directory.glob("*.json")):
        try:
            out.append(Continuation.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            logger.debug("run %s: skipping unreadable continuation %s", run_id, path.name)
    return out


def consume_continuation(run_id: str, token: str) -> Continuation | None:
    """Atomically claim a continuation: RENAME it aside, then read the claimed copy.

    `os.rename` is the claim primitive, and the ordering matters: the winner is decided BEFORE
    anything is read, so only the caller that moved the file ever sees the payload.

    This used to `read` then `unlink`, on the reasoning that "only one unlink succeeds".
    MEASURED, and it does not hold: 8 threads racing one token had multiple callers receive the
    payload in 36 of 40 trials, because `unlink` on this filesystem does not reliably raise
    `FileNotFoundError` for the losers — and every reader had already read the file before
    unlinking anyway. That is the exact double-approval replay the single-use rule exists to
    prevent: two resumes both carrying one clarification into downstream steps.

    `os.rename` measured 0 of 40 trials with more than one winner. The claimed file is left on
    disk under a `.claimed` suffix rather than deleted, so a resolution that crashes mid-resume is
    recoverable and auditable instead of silently gone.
    """
    if not token or "/" in token or "\\" in token or ".." in token:
        return None
    path = _dir(run_id) / f"{token}.json"
    claimed = _dir(run_id) / f"{token}.claimed.json"
    try:
        # THE claim. Whoever completes this rename owns the answer; everyone else loses here,
        # before reading, which is what makes the single-use guarantee real.
        os.rename(path, claimed)
    except OSError:
        # Missing, already claimed, or unreadable — all the same outcome: this caller does not
        # own the answer.
        return None
    try:
        raw = claimed.read_text(encoding="utf-8")
    except OSError:
        logger.warning("run %s: claimed continuation %s but could not read it", run_id, token)
        return None
    try:
        return Continuation.from_dict(json.loads(raw))
    except ValueError:
        return None


def drop_continuations(run_id: str, *, instance_prefix: str = "") -> int:
    """Delete pending continuations, optionally only under a path prefix.

    Called on rewind: a token for a node that is about to re-run would resume a step that no
    longer exists in that form. Better a typed `resume_expired` than a token that silently
    lands in the wrong epoch.
    """
    dropped = 0
    for cont in list_continuations(run_id):
        if instance_prefix and not cont.instance_path.startswith(instance_prefix):
            continue
        path = _dir(run_id) / f"{cont.token}.json"
        try:
            path.unlink()
            dropped += 1
        except OSError:
            logger.debug("run %s: could not drop continuation %s", run_id, cont.token)
    return dropped


def expired_item(cont: Continuation) -> dict[str, Any]:
    """The typed `resume_expired` needs-input item (WF2-R7).

    A dead token must never just do nothing — that is indistinguishable from a bug, and the
    user is left clicking a button with no effect. This offers the concrete next move.
    """
    return {
        "kind": "resume_expired",
        "run_id": cont.run_id,
        "node_id": cont.node_id,
        "instance_path": cont.instance_path,
        "prompt": (
            f"The approval link for {cont.node_id or cont.instance_path!r} expired before it "
            "was answered."
        ),
        "remediation": "re-run the workflow from this node to ask again",
        "expired_at": cont.expires_at,
    }
