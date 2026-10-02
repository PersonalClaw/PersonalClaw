"""A trigger's action that stopped for a person.

An action can stop on something only a person can lift: browse at a sign-in page returns
`outcome="needs_input"` with the card the handoff composes ("Sign in to example.com, then
confirm", and what it tried). Inside a workflow run the engine parks the step and asks through the
run's gate (`workflows.gate_answers`). A trigger's action had no such seam: its fire recorded
`success` in the trigger's history, and nothing anyone could answer was raised — or, for a session
that had expired, a site-level row that resumed nothing. This is that seam for a trigger.

* :func:`settle` — called by the run recorder (`run_record.record_run`), for an autonomous fire
  and for the Run button alike, once the run's row is
  written. A result that parked raises the park (:func:`raise_park`): one per trigger, and ONE
  Inbox row carrying the action's own card and the park's token — deduped per trigger while it is
  open, with the same token while it is, so a trigger that fires again before anyone answers asks
  nothing new. A result that ran through withdraws an open one (:func:`withdraw`): the question it
  asked is moot, and a row that stays answerable after that runs the action for nothing.
* :func:`claim` — the answer's single-use consume, so a double click cannot run the action twice.
  Approve then runs the trigger's action once, with the answer on that one dispatch
  (`ActionContext.answer`), the way an approved in-run park runs its step again
  (`dashboard/handlers/trigger_runs.api_trigger_answer`). Deny closes the question until the trigger
  next runs.

A question is answerable for :func:`answerable_secs`, the window a workflow's resume token has.
Its token lived for as long as nobody answered, so a link to it could run the action months after
the question it asked stopped meaning anything. Past the window its answer is refused and its row
closed, and the trigger asks again, with a new token, the next time it stops.

The run's history row says it is waiting, and on what (:func:`waiting_line`): the run did not do
what it was asked yet, so it is not recorded as a success (`schedule_history` status `waiting`).

The park holds the trigger's id and the card, never the action's config: Approve re-reads the
trigger and runs what it declares at that moment, through the Run button's own dispatch, so a
secret the config names is resolved at run time and never written here.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The registered notification pair a decision is raised under (`notification_kinds`: "every
#: emitter of this pair parks work on the user's answer" — a workflow gate, a browse sign-in
#: handoff). A second pair for the same kind of question would give the user two switches for it.
SOURCE = "loop"


def answerable_secs() -> float:
    """How long a trigger's question can be answered: the window a workflow's resume token has
    (`human_input.DEFAULT_RESUME_TTL_SECS`, "long enough to answer tomorrow morning; short enough
    that a year-old token cannot resurrect a run whose world has moved on") — read where it is
    defined, so the two kinds of question cannot drift apart."""
    from personalclaw.workflows.human_input import DEFAULT_RESUME_TTL_SECS

    return float(DEFAULT_RESUME_TTL_SECS)


def expired(park: "TriggerPark", *, now: float = 0.0) -> bool:
    """Whether *park*'s question is past answering. One with no creation time cannot be bounded,
    so it is past it too."""
    now = now or time.time()
    return park.created_at <= 0 or now - park.created_at >= answerable_secs()


def parks_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "trigger_parks"


def _path(trigger_id: str) -> Path:
    # A trigger id carries `:` (`schedule:clock:nightly`), so the file is named by its digest; the
    # id itself is inside the record.
    from personalclaw.record_ids import record_path

    digest = hashlib.sha256(trigger_id.encode("utf-8")).hexdigest()[:24]
    return record_path(parks_dir(), digest, kind="trigger park")


@dataclass
class TriggerPark:
    """One trigger's open question: the token that answers it, and the action's own card."""

    token: str
    trigger_id: str
    #: The `NeedsInputItem` the action composed, as `to_dict()` — what the Inbox row shows.
    card: dict[str, Any] = field(default_factory=dict)
    #: The question in a line: the card's blocker, else what the action said when it stopped.
    question: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "trigger_id": self.trigger_id,
            "card": dict(self.card),
            "question": self.question,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TriggerPark:
        card = d.get("card")
        return cls(
            token=str(d.get("token") or ""),
            trigger_id=str(d.get("trigger_id") or ""),
            card=dict(card) if isinstance(card, dict) else {},
            question=str(d.get("question") or ""),
            created_at=float(d.get("created_at") or 0.0),
        )


def parked(result: Any) -> bool:
    """Did this action result stop for a person? `success=True` with `outcome="needs_input"` —
    a park is not a failure, and nothing else says so."""
    return (
        result is not None
        and bool(getattr(result, "success", False))
        and str(getattr(result, "outcome", "") or "") == "needs_input"
    )


def question_of(result: Any) -> str:
    """What a parked result is waiting on, in a line — the history row's reason and the card's
    headline. The card's blocker when the action composed one (browse's sign-in handoff), else what
    the action said on stopping."""
    card = _card_of(result)
    blocker = str(card.get("blocker") or "").strip() if card else ""
    if blocker:
        return blocker
    return str(getattr(result, "stderr", "") or "").strip() or "The action stopped and needs you."


def waiting_line(result: Any) -> str:
    """The history row's words for a run that parked: that it waits on you, then the question —
    "Waiting for you. Sign in to example.com, then confirm — …"."""
    return f"Waiting for you. {question_of(result)}"


def _ran_through(result: Any) -> bool:
    """Did the action do what it was asked? A plain success (or a one-shot's `done`) — not a park,
    a skip, or work it only started or queued."""
    return (
        result is not None
        and bool(getattr(result, "success", False))
        and str(getattr(result, "outcome", "") or "") in ("", "done")
    )


def _card_of(result: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or "") or "{}")
    except (TypeError, ValueError):
        return {}
    card = payload.get("needs_input") if isinstance(payload, dict) else None
    return card if isinstance(card, dict) else {}


def load(trigger_id: str) -> TriggerPark | None:
    path = _path(trigger_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    park = TriggerPark.from_dict(data) if isinstance(data, dict) else None
    return park if park is not None and park.trigger_id == trigger_id else None


def settle(trigger: Any, result: Any, *, state: Any = None) -> None:
    """What a finished run of `trigger` means for its question: ask it if the run parked, withdraw
    it if the run went through, and leave it alone otherwise — a failure or a skip did not get
    past what the question asks about. Never raises: the run already happened, and its record (the
    caller's next writes) matters more than the question."""
    try:
        if parked(result):
            raise_park(trigger, result, state=state)
        elif _ran_through(result):
            withdraw(str(getattr(trigger, "id", "") or ""), state=state)
    except Exception:
        logger.warning(
            "trigger %s: could not settle its question", getattr(trigger, "id", ""), exc_info=True
        )


def withdraw(trigger_id: str, *, state: Any = None) -> bool:
    """Drop `trigger_id`'s open question and close its row. False when it had none — the common
    case, answered from the park file alone, so a run that never parked reads no Inbox."""
    if not trigger_id or not _path(trigger_id).exists():
        return False
    try:
        _path(trigger_id).unlink()
    except OSError:
        return False
    close_row(state if state is not None else _dashboard_state(), trigger_id)
    return True


def raise_park(trigger: Any, result: Any, *, state: Any = None) -> TriggerPark | None:
    """Record that `trigger`'s action stopped for a person, and ask them — once.

    An open park for the trigger is KEPT, token and all: its Inbox row is still the answerable one,
    and a second fire before anyone answers is the same question. Never raises — the fire already
    happened, and losing the question is better than losing the fire's record.
    """
    trigger_id = str(getattr(trigger, "id", "") or "")
    if not trigger_id or not parked(result):
        return None
    try:
        park = load(trigger_id)
        if park is None or expired(park):
            # An expired question is asked afresh: a new token, and the row re-raised with it.
            park = TriggerPark(
                token=secrets.token_urlsafe(24),
                trigger_id=trigger_id,
                card=_card_of(result),
                question=question_of(result),
                created_at=time.time(),
            )
            _write(park)
        _raise_row(trigger, park, state=state)
        return park
    except Exception:
        logger.warning("trigger %s: could not record its park", trigger_id, exc_info=True)
        return None


def claim(trigger_id: str, token: str) -> TriggerPark | None:
    """Consume the park `token` answers. None when there is none, or `token` is not its token
    (already answered, or a stale link). Single-use by an atomic rename, so exactly one of two
    concurrent answers wins."""
    park = load(trigger_id)
    if park is None or not token or not secrets.compare_digest(park.token, str(token)):
        return None
    if expired(park):
        # Past answering: the row is closed rather than left offering an answer nothing takes.
        withdraw(trigger_id)
        return None
    path = _path(trigger_id)
    claimed = path.with_suffix(".claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return None
    with contextlib.suppress(OSError):
        claimed.unlink()
    return park


def close_row(state: Any, trigger_id: str) -> int:
    """Close the trigger's open question row — answered, or its park is gone."""
    from personalclaw.inbox import resolve_attention_items

    return resolve_attention_items(
        state if state is not None else _dashboard_state(), {"trigger_park": trigger_id}
    )


def _write(park: TriggerPark) -> None:
    from personalclaw.atomic_write import atomic_write

    path = _path(park.trigger_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(park.to_dict(), indent=2))


def _dashboard_state() -> Any:
    """The running gateway's state, for a recorder that has none to hand (the Run button's): the
    Inbox service's live store is on it, and a row written around that store is one the service
    never shows and its next save erases (`inbox.live_store`)."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    with contextlib.suppress(Exception):
        return get_dashboard_state()
    return None


def _raise_row(trigger: Any, park: TriggerPark, *, state: Any = None) -> str:
    """The ONE Inbox row: the action's card, answerable in place (`TriggerParkActions`)."""
    from dataclasses import replace

    from personalclaw.inbox import emit_attention_item
    from personalclaw.workflows.attention import ask_body
    from personalclaw.workflows.needs_input import BlockKind, NeedsInputItem, card_refs

    if state is None:
        state = _dashboard_state()
    name = str(getattr(trigger, "name", "") or park.trigger_id)
    card = (
        NeedsInputItem.from_dict(park.card)
        if park.card
        else NeedsInputItem(run_id="", node_id="", blocker=park.question)
    )
    # The answer is Approve — run it again, now — or Deny, exactly as the in-run card offers, so it
    # is an approval whatever the action called it, and never the card's prose choices.
    card = replace(
        card,
        blocker=card.blocker or park.question,
        block_kind=BlockKind.APPROVAL,
        choices=[],
        resume_token=park.token,
        created_at=card.created_at or park.created_at,
    )
    refs = {
        # The card's own refs, less the run keys it has none for: an empty `workflow` would read as
        # a gate to anything that only asks whether the key is there.
        **{key: value for key, value in card_refs(card).items() if value},
        "trigger": park.trigger_id,
        "trigger_name": name,
        "trigger_park": park.trigger_id,
    }
    return emit_attention_item(
        state,
        source=SOURCE,
        kind="needs_input",
        item_kind="needs_input",
        title=park.question,
        body=ask_body({"kind": "approval"}, {"attempted": card.attempted}),
        refs=refs,
        dedup_key=f"trigger_park:{park.trigger_id}",
    )
