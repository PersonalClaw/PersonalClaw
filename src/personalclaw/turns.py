"""A chat turn as types: messages that own ordered parts, and one state machine for each part.

Shared contracts §3.3-§3.5. A chat is an ordered list of :class:`Message`; a user message starts a
turn and an assistant message answers it. A message owns ordered, typed parts, one class per kind
(:data:`PART_KINDS`). A tool call is a :class:`ToolPart` whose state moves only through
:func:`advance`, along §3.4's edges, and the approval, question, sign-in and helper parts move
through the same function by their own tables. :func:`may_have_run` and its siblings are §3.5's
definitions, written once, and :func:`ending_summary` is what every surface that says what survived
a turn reads. :func:`dialogue` gives a chat's messages back as the text a text reader is handed.

Every type is a frozen dataclass. ``to_dict()`` leaves out an optional field that holds its empty
default, and ``from_dict()`` refuses a key the type does not have, naming it, so a field written by
a newer build or a typo never passes for a known one.
``tests/rails/test_part_state_has_one_writer_tree.py`` holds every other module to :func:`advance`,
and ``make turns-schema`` writes the JSON schema and the web types from these classes
(``tests/test_turns_schema_current.py``).

The model holds the fields of contracts §3.3 for the kinds here. Fields and kinds other work adds
(a native step, the data a model asks to have echoed back, an idempotency key, the fence, whose
words started a turn, and the access, offer, review, ways and call kinds) come with the change that
produces them. Nothing here imports from ``dashboard``.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Union

from personalclaw.instants import utc_now_iso
from personalclaw.turn_source import ASKED_FOR_BY, FROM_OUTSIDE, QUEUED_FROM, SOURCE_FIELDS

Role = Literal["user", "assistant"]
#: What started a turn (§3.3). ``goal`` is a goal loop's cycle; goal loops replace Auto-nudge.
Dispatch = Literal["message", "inject", "subagent", "goal", "continue"]
Outcome = Literal["complete", "stopped", "error", "interrupted"]
Risk = Literal["safe", "caution", "destructive", "unchecked"]
Effect = Literal["read", "write"]
#: §3.3.1's closed vocabulary, which ``tool_families.family_of`` assigns.
Family = Literal[
    "read", "edit", "command", "search", "fetch", "browse", "delegate", "send", "other"
]

ToolState = Literal["pending", "running", "completed", "failed", "refused", "cancelled", "unknown"]
ApprovalState = Literal["pending", "approved", "rejected", "expired", "cancelled"]
QuestionState = Literal["pending", "answered", "skipped", "expired", "cancelled"]
SignInState = Literal["pending", "signed_in", "skipped", "expired", "cancelled"]
HelperState = Literal[
    "queued",
    "waiting_on_owner",
    "running",
    "waiting_on_limit",
    "completed",
    "failed",
    "stopped",
    "declined",
    "timed_out",
    "step_limit",
    "interrupted",
]
NoticeKind = Literal[
    "stopped",
    "cut_short",
    "interrupted",
    "lost_after_steps",
    "retrying",
    "agent_switched",
    "model_switched",
    "carried_on",
    "prompt_blocked",
    "continued",
    "output_continued",
    "context_full",
    "info",
]


class IllegalTransition(ValueError):
    """A part was asked to move along an edge its state table does not have."""

    def __init__(self, from_state: str, to_state: str, kind: str = "tool") -> None:
        super().__init__(f"a {kind} part cannot move from {from_state} to {to_state}")
        self.from_state = from_state
        self.to_state = to_state
        self.kind = kind


# ── serializing: one rule for every type ───────────────────────────────────────────────────────


class _Typed:
    """``to_dict`` and a strict ``from_dict`` for a frozen dataclass of this module.

    :data:`_KEEP` names the fields written even when they hold their default (a required field
    whose value may be empty, such as an assistant message's null ``outcome`` while it runs).
    """

    _KEEP: ClassVar[frozenset[str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in dataclasses.fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if f.name not in self._KEEP and _is_default(f, value):
                continue
            out[f.name] = _encode(value)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError(f"{cls.__name__} is read from an object, not {type(data).__name__}")
        hints = typing.get_type_hints(cls)
        names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
        for key in data:
            if key not in names:
                raise ValueError(f"{cls.__name__} has no field {key!r}")
        given = {k: _decode(hints[k], v, f"{cls.__name__}.{k}") for k, v in data.items()}
        try:
            return cls(**given)
        except TypeError as exc:
            raise ValueError(f"{cls.__name__}: {exc}") from exc


def _is_default(f: dataclasses.Field[Any], value: Any) -> bool:
    if f.default is not dataclasses.MISSING:
        return bool(value == f.default)
    if f.default_factory is not dataclasses.MISSING:
        return bool(value == f.default_factory())
    return False


def _encode(value: Any) -> Any:
    if isinstance(value, _Typed):
        return value.to_dict()
    if isinstance(value, tuple | list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _decode(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is Any:
        return value
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        rest = [a for a in args if a is not type(None)]
        return _decode(rest[0], value, where) if len(rest) == 1 else value
    if origin is Literal:
        if value not in args:
            raise ValueError(f"{where}: {value!r} is not one of {', '.join(map(repr, args))}")
        return value
    if origin is tuple:
        if not isinstance(value, list | tuple):
            raise ValueError(f"{where}: a list, not {type(value).__name__}")
        return tuple(_decode(args[0], v, f"{where}[]") for v in value)
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"{where}: an object, not {type(value).__name__}")
        return dict(value)
    if isinstance(tp, type) and issubclass(tp, Part):
        return part_from_dict(value)
    if isinstance(tp, type) and issubclass(tp, _Typed):
        return tp.from_dict(value)
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(tp, type) and not isinstance(value, tp):
        raise ValueError(f"{where}: {tp.__name__}, not {type(value).__name__}")
    if tp is int and isinstance(value, bool):
        raise ValueError(f"{where}: int, not bool")
    return value


# ── the shapes a message holds ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Usage(_Typed):
    """A turn's usage, the per-turn subset of ``usage_ledger.TurnUsage`` (§17.4)."""

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    audio_input_tokens: int = 0
    audio_output_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = False
    local: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class Source(_Typed):
    """Where a message came from, with today's field names (``turn_source.SOURCE_FIELDS``) and the
    channel conversation's address, which the channels work fills."""

    source_thread: str = ""
    source_user: str = ""
    source_channel: str = ""
    source_address: str = ""


@dataclass(frozen=True)
class MessageError(_Typed):
    """A turn's terminal error: a wire code, the words, and the Settings route that fixes it."""

    _KEEP = frozenset({"code", "message", "retryable"})

    code: str
    message: str
    retryable: bool = False
    settings: str = ""
    reason: str = ""
    retry_at: str = ""


@dataclass(frozen=True)
class MessageFacts(_Typed):
    """Closed facts about a message, each optional, with the names today's rows and stamps use."""

    # On a user message.
    input_origin: Literal["", "typed", "voice", "call"] = ""
    own_words: str = ""
    ran_prompt: dict[str, Any] | None = None
    original: str = ""
    ui_label: str = ""
    injected_label: str = ""
    pastes: tuple[dict[str, Any], ...] = ()
    skills_used: tuple[dict[str, Any], ...] = ()
    image_delivery: dict[str, Any] | None = None
    image_delivery_reason: str = ""
    receipt: dict[str, Any] | None = None
    continues: str = ""
    merged: tuple[dict[str, Any], ...] = ()
    steered_into: str = ""
    after_step: int = 0
    caused_by: dict[str, Any] | None = None
    root: dict[str, Any] | None = None
    mode: Literal["", "full", "inspect_only"] = ""
    goal: dict[str, Any] | None = None
    from_outside: dict[str, Any] | None = None
    #: On a helper's report: the source of whoever asked for the work it carries on, when it was
    #: not the owner (``turn_source.ASKED_FOR_BY``).
    asked_for_by: dict[str, Any] | None = None
    input_flagged: bool = False
    # On an assistant message.
    memory_citations: tuple[Any, ...] = ()
    context_fed: dict[str, Any] | None = None
    learned: tuple[dict[str, Any], ...] = ()
    turn_telemetry: dict[str, Any] | None = None
    summary: str = ""
    finish_reason: Literal["", "length", "incomplete"] = ""
    model_substitution: str = ""
    file_changes: tuple[dict[str, Any], ...] = ()
    step_models: tuple[dict[str, Any], ...] = ()
    read_untrusted: tuple[str, ...] = ()
    read_private: tuple[str, ...] = ()
    handed_off: dict[str, Any] | None = None
    checkpoint_turn: int = 0
    checks: dict[str, Any] | None = None
    instructions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Ref(_Typed):
    """What a file or a result block points at."""

    _KEEP = frozenset({"kind", "id"})

    kind: Literal["attachment", "artifact", "path", "uri"]
    id: str


@dataclass(frozen=True)
class Block(_Typed):
    """A result's non-text content, kept as a reference."""

    _KEEP = frozenset({"kind", "ref"})

    kind: Literal["image", "audio", "resource_link", "resource"]
    ref: Ref
    name: str = ""
    mime: str = ""
    size: int = 0


@dataclass(frozen=True)
class Withheld(_Typed):
    """A result held from the model (decision 43)."""

    _KEEP = frozenset({"by", "reason", "held_ref"})

    by: Literal["screen", "policy", "account_recovery"]
    reason: str
    held_ref: str = ""


@dataclass(frozen=True)
class ToolResult(_Typed):
    """What a call returned: ``text`` is what the model received, whole."""

    _KEEP = frozenset({"text"})

    text: str = ""
    received_length: int | None = None
    content_type: str = ""
    raw_ref: str = ""
    truncated: bool = False
    original_length: int | None = None
    structured: dict[str, Any] | None = None
    blocks: tuple[Block, ...] = ()
    recovery_hints: tuple[str, ...] = ()
    withheld: Withheld | None = None


# ── parts ──────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Part(_Typed):
    """What every part carries: its id, when it was made and when it last changed. ``kind`` is the
    class's own (:data:`KIND`), written into every ``to_dict``."""

    KIND: ClassVar[str] = ""

    id: str
    at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.KIND, **super().to_dict()}


@dataclass(frozen=True)
class TextPart(Part):
    KIND = "text"
    _KEEP = frozenset({"id", "at", "updated_at", "text"})

    text: str = ""


@dataclass(frozen=True)
class ReasoningPart(Part):
    """Live only: sent as it streams, never written to the store (decision 5)."""

    KIND = "reasoning"
    _KEEP = frozenset({"id", "at", "updated_at", "text"})

    text: str = ""


@dataclass(frozen=True)
class ToolPart(Part):
    """A tool call. ``effect`` is fixed when the part is made: ``read`` exactly when
    ``task_modes.reads_only`` held at call time, else ``write`` (decision 23)."""

    KIND = "tool"
    _KEEP = frozenset(
        {
            "id",
            "at",
            "updated_at",
            "tool_call_id",
            "tool",
            "title",
            "family",
            "input",
            "risk",
            "effect",
            "state",
            "started_at",
            "ended_at",
        }
    )

    tool_call_id: str = ""
    tool: str = ""
    title: str = ""
    family: Family = "other"
    input: str = ""
    risk: Risk = "unchecked"
    effect: Effect = "write"
    state: ToolState = "pending"
    detail: str = ""
    tool_kind: str = ""
    via: str = ""
    input_obj: dict[str, Any] | None = None
    input_cut: bool = False
    purpose: str = ""
    why: Literal["", "stopped", "steered"] = ""
    decided_by: str = ""
    refused_by: str = ""
    refused_rule: str = ""
    grant_id: str = ""
    approval_part_id: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    result: ToolResult | None = None
    error: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    view: dict[str, Any] | None = None
    ungated: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalPart(Part):
    KIND = "approval"
    _KEEP = frozenset(
        {"id", "at", "updated_at", "request_id", "ask", "tool_part_id", "brief", "answers", "state"}
        | {"queued"}
    )

    request_id: str = ""
    ask: Literal["call", "owner_allow"] = "call"
    tool_part_id: str = ""
    brief: dict[str, Any] = field(default_factory=dict)
    answers: tuple[dict[str, Any], ...] = ()
    queued: bool = False
    state: ApprovalState = "pending"
    grant_agent: str = ""
    deny_effect: str = ""
    answer: str = ""
    decided_by: Literal["", "you", "nobody"] = ""
    grant_id: str = ""
    ended: str = ""


@dataclass(frozen=True)
class QuestionPart(Part):
    KIND = "question"
    _KEEP = frozenset(
        {
            "id",
            "at",
            "updated_at",
            "question_id",
            "tool_part_id",
            "questions",
            "answerable",
            "state",
        }
    )

    question_id: str = ""
    tool_part_id: str = ""
    questions: tuple[dict[str, Any], ...] = ()
    asked_by: str = ""
    answerable: bool = True
    note: str = ""
    state: QuestionState = "pending"
    answers: tuple[dict[str, Any], ...] = ()
    ended: str = ""


@dataclass(frozen=True)
class SignInPart(Part):
    """A sign-in ask (§11.2). ``need`` is ``more_access`` for a missing scope, the same card in
    other words; it is not called ``kind``, which every part already carries as its own."""

    KIND = "sign_in"
    _KEEP = frozenset(
        {"id", "at", "updated_at", "request_id", "need", "subject", "label", "scopes", "state"}
        | {"tool_part_id"}
    )

    request_id: str = ""
    need: Literal["sign_in", "more_access"] = "sign_in"
    subject: str = ""
    label: str = ""
    scopes: tuple[str, ...] = ()
    tool_part_id: str = ""
    state: SignInState = "pending"


@dataclass(frozen=True)
class NoticePart(Part):
    """A notice in a turn. Today's ``notice``, ``system`` and non-terminal ``error`` rows become
    notices; a turn's terminal error is :attr:`Message.error`."""

    KIND = "notice"
    _KEEP = frozenset({"id", "at", "updated_at", "notice", "text", "level"})

    notice: NoticeKind = "info"
    text: str = ""
    level: Literal["info", "warn"] = "info"
    settings: str = ""


@dataclass(frozen=True)
class FilePart(Part):
    """A file the owner uploaded (``in``) or the turn produced (``out``)."""

    KIND = "file"
    _KEEP = frozenset({"id", "at", "updated_at", "name", "mime", "size", "ref", "direction"})

    ref: Ref = field(default_factory=lambda: Ref(kind="path", id=""))
    direction: Literal["in", "out"] = "in"
    name: str = ""
    mime: str = ""
    size: int = 0
    version: int | None = None


@dataclass(frozen=True)
class MentionPart(Part):
    """A pick from the capability index, resolved before the turn starts (decision 31)."""

    KIND = "mention"
    _KEEP = frozenset({"id", "at", "updated_at", "target", "ref", "label", "tokens", "outcome"})

    target: Literal["file", "knowledge", "prompt", "artifact", "skill", "tool", "app", "agent"] = (
        "file"
    )
    ref: str = ""
    label: str = ""
    tokens: int | None = None
    outcome: Literal["attached", "reduced", "refused", "not_found", "handed_off"] = "attached"
    why: str = ""


@dataclass(frozen=True)
class PlanPart(Part):
    """The agent's own working list, at most one per assistant message."""

    KIND = "plan"
    _KEEP = frozenset({"id", "at", "updated_at", "entries", "source"})

    entries: tuple[dict[str, Any], ...] = ()
    source: Literal["native", "acp"] = "native"


@dataclass(frozen=True)
class HelperPart(Part):
    """A helper the turn started: its identity and how it ended, so it survives a reload."""

    KIND = "helper"
    _KEEP = frozenset({"id", "at", "updated_at", "work", "agent", "task", "mode", "state"})

    work: str = ""
    agent: str = ""
    task: str = ""
    mode: Literal["batch", "background", "cli"] = "batch"
    state: HelperState = "queued"
    wait: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    generation: int = 1
    again_of: str = ""
    follows_up: str = ""
    child_of: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    last_activity: dict[str, Any] | None = None


@dataclass(frozen=True)
class CompactionPart(Part):
    """Where a chat's history was compacted; the context work adds its span, sections and check.
    ``form`` says whether it is a model's summary or a digest (not ``kind``, the part's own)."""

    KIND = "compaction"
    _KEEP = frozenset({"id", "at", "updated_at", "trigger", "form", "text"})

    trigger: Literal["auto", "owner", "rebuild", "idle"] = "auto"
    form: Literal["summary", "digest"] = "summary"
    model_ref: str = ""
    check: Literal["", "passed", "retried", "failed", "skipped"] = ""
    summarized: int = 0
    head_through: dict[str, Any] | None = None
    through: dict[str, Any] | None = None
    kept: tuple[dict[str, Any], ...] = ()
    text: str = ""
    account: tuple[Any, ...] = ()
    sections: dict[str, Any] | None = None
    focus: str = ""
    restored: dict[str, Any] | None = None
    pruned: int = 0
    before_chars: int = 0
    after_chars: int = 0


#: Every part kind this model holds, by its ``kind``.
PART_KINDS: dict[str, type[Part]] = {
    cls.KIND: cls
    for cls in (
        TextPart,
        ReasoningPart,
        ToolPart,
        ApprovalPart,
        QuestionPart,
        SignInPart,
        NoticePart,
        FilePart,
        MentionPart,
        PlanPart,
        HelperPart,
        CompactionPart,
    )
}


def part_from_dict(data: Any) -> Part:
    """A part from its ``to_dict``, by its ``kind``; an unknown kind or key is refused."""
    if not isinstance(data, dict):
        raise ValueError(f"a part is read from an object, not {type(data).__name__}")
    body = dict(data)
    kind = body.pop("kind", None)
    if kind not in PART_KINDS:
        raise ValueError(f"no part kind {kind!r}")
    return typing.cast(Part, PART_KINDS[kind].from_dict(body))


# ── the message ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Message(_Typed):
    """One message of a chat. A user message has ``dispatch``; an assistant message answers its
    ``parent_id``, as attempt ``attempt``, exactly one attempt per parent ``shown``."""

    _KEEP = frozenset({"id", "session", "role", "ts", "source", "parts"})

    id: str
    session: str
    role: Role
    ts: str
    source: Source = field(default_factory=Source)
    parts: tuple[Part, ...] = ()
    dispatch: Literal["", "message", "inject", "subagent", "goal", "continue"] = ""
    parent_id: str = ""
    attempt: int = 0
    shown: bool = False
    settled_at: str | None = None
    outcome: Outcome | None = None
    agent: str = ""
    model_ref: str = ""
    usage: Usage | None = None
    error: MessageError | None = None
    facts: MessageFacts = field(default_factory=MessageFacts)

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        # A user message always says what started it; an answer always says which attempt it is,
        # whether it is the one shown, and how it ended (null while the turn runs).
        keys = ("dispatch",) if self.role == "user" else ("parent_id", "attempt", "shown")
        for key in keys + (("settled_at", "outcome") if self.role == "assistant" else ()):
            out[key] = _encode(getattr(self, key))
        return out


# ── state machines ─────────────────────────────────────────────────────────────────────────────

#: §3.4's edges for a tool part: from each state, where it may go.
TOOL_EDGES: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "refused", "cancelled", "unknown"}),
    "running": frozenset({"completed", "failed", "unknown", "cancelled"}),
}
TOOL_TERMINAL = frozenset({"completed", "failed", "refused", "cancelled", "unknown"})

_OPEN_HELPER = frozenset({"queued", "waiting_on_owner", "running", "waiting_on_limit"})
_HELPER_ENDINGS = frozenset(typing.get_args(HelperState)) - _OPEN_HELPER

#: The other parts that move: each state's next states. A state with no row is terminal.
EDGES: dict[str, dict[str, frozenset[str]]] = {
    "tool": TOOL_EDGES,
    "approval": {"pending": frozenset({"approved", "rejected", "expired", "cancelled"})},
    "question": {"pending": frozenset({"answered", "skipped", "expired", "cancelled"})},
    "sign_in": {"pending": frozenset({"signed_in", "skipped", "expired", "cancelled"})},
    "helper": {s: (_OPEN_HELPER - {s}) | _HELPER_ENDINGS for s in _OPEN_HELPER},
}

_StatefulPart = typing.TypeVar(
    "_StatefulPart", ToolPart, ApprovalPart, QuestionPart, SignInPart, HelperPart
)


def advance(part: _StatefulPart, to: str, *, now: str = "", **facts: Any) -> _StatefulPart:
    """*part* moved to state *to*, with *facts* set beside it: the only way a part's state changes.

    The edge must be in the part's table (:data:`EDGES`); a terminal state never moves. A tool
    part goes from ``pending`` to ``unknown`` only as a write (an agent CLI's call cut before it
    reported: the CLI may have started it), and from ``running`` to ``cancelled`` only as a read.
    ``updated_at`` is set on every move, ``started_at`` on the first ``running`` and ``ended_at``
    on a terminal state. *now* is the instant of the move (now, when it is not given).
    """
    table = EDGES.get(part.KIND)
    if table is None:
        raise IllegalTransition("", to, part.KIND)
    here = part.state
    if to not in table.get(here, frozenset()):
        raise IllegalTransition(here, to, part.KIND)
    if isinstance(part, ToolPart):
        if (here, to) == ("pending", "unknown") and part.effect != "write":
            raise IllegalTransition(here, to, "read tool")
        if (here, to) == ("running", "cancelled") and part.effect != "read":
            raise IllegalTransition(here, to, "write tool")
    names = {f.name for f in dataclasses.fields(part)}
    for key in facts:
        if key in {"id", "at", "state", "updated_at"} or key not in names:
            raise ValueError(f"a {part.KIND} part has no fact {key!r} to set")
    stamp = now or utc_now_iso()
    changes: dict[str, Any] = {"state": to, "updated_at": stamp, **facts}
    if to == "running" and "started_at" in names and getattr(part, "started_at") is None:
        # The first start: a helper that waits and runs again keeps when it began.
        changes.setdefault("started_at", stamp)
    if to not in table and "ended_at" in names:
        changes.setdefault("ended_at", stamp)
    return dataclasses.replace(part, **changes)


def is_terminal(part: Part) -> bool:
    """Whether *part*'s state can never move again (a part with no state table never moves)."""
    table = EDGES.get(part.KIND)
    return table is None or getattr(part, "state", "") not in table


# ── §3.5's definitions ─────────────────────────────────────────────────────────────────────────


def _tool_parts(message: Message) -> list[ToolPart]:
    return [p for p in message.parts if isinstance(p, ToolPart)]


def may_have_run(part: Part) -> bool:
    """A tool part that is ``running``, ``completed``, ``failed`` or ``unknown``: made, and not
    refused (today's ``_steps_made``)."""
    return isinstance(part, ToolPart) and part.state in {
        "running",
        "completed",
        "failed",
        "unknown",
    }


def completed_side_effect(part: Part) -> bool:
    """A tool part in ``completed`` whose ``effect`` is ``write``."""
    return isinstance(part, ToolPart) and part.state == "completed" and part.effect == "write"


def failed_side_effect(part: Part) -> bool:
    """A tool part in ``failed`` whose ``effect`` is ``write``: it may have done part of its
    work, so it is named, never retried blind."""
    return isinstance(part, ToolPart) and part.state == "failed" and part.effect == "write"


def outcome_unknown(part: Part) -> bool:
    """A tool part in ``unknown``."""
    return isinstance(part, ToolPart) and part.state == "unknown"


def left_work_behind(message: Message) -> bool:
    """An assistant message whose turn ended ``interrupted`` or ``error``, or was cut short,
    while at least one of its calls may have run."""
    if message.role != "assistant":
        return False
    cut_short = any(isinstance(p, NoticePart) and p.notice == "cut_short" for p in message.parts)
    ended_badly = message.outcome in {"interrupted", "error"} or cut_short
    return ended_badly and any(may_have_run(p) for p in message.parts)


@dataclass(frozen=True)
class EndingSummary(_Typed):
    """What survived a turn: the writes that completed, those that failed or whose outcome is not
    known (named, never retried blind), the reads that completed, and whether it wrote words."""

    _KEEP = frozenset({"completed", "failed", "unknown", "reads", "wrote_text"})

    completed: int = 0
    failed: tuple[ToolPart, ...] = ()
    unknown: tuple[ToolPart, ...] = ()
    reads: int = 0
    wrote_text: bool = False


def ending_summary(message: Message) -> EndingSummary:
    """What survived *message*'s turn, the one reading every surface that says so uses."""
    tools = _tool_parts(message)
    return EndingSummary(
        completed=sum(1 for p in tools if completed_side_effect(p)),
        failed=tuple(p for p in tools if failed_side_effect(p)),
        unknown=tuple(p for p in tools if outcome_unknown(p)),
        reads=sum(1 for p in tools if p.state == "completed" and p.effect == "read"),
        wrote_text=any(isinstance(p, TextPart) and p.text.strip() for p in message.parts),
    )


# ── the chat as text ───────────────────────────────────────────────────────────────────────────


def dialogue(messages: Iterable[Message]) -> list[dict[str, Any]]:
    """*messages*, a chat in order, as a text reader is handed it: ``{role, content}`` with where
    each came from (``turn_source.provenance``'s keys), for each message sent as a message
    (``dispatch: message``, its text parts joined) and for each text part of each turn's shown
    answer, with each steer where its turn took it (``facts.after_step``: after that many of the
    answer's text parts). A turn an automation, a helper's report or a goal cycle started gives its
    answer and not its message.

    For the rows ``dashboard.turn_rows.from_rows`` read the messages from, this is what
    ``history.model_view`` gives with no summary, entry for entry, so a summary's digest and every
    history built from it stay the same. One shape differs: an earlier version of an answer of
    several rows switched back in, which today's view reads in place of the newest version's first
    row and this reads as the attempt it is (``tests/test_turn_rows.py``)."""
    messages = list(messages)
    shown: dict[str, Message] = {}
    steers: dict[str, list[Message]] = {}
    for message in messages:
        if message.role == "assistant" and message.shown and message.parent_id:
            shown.setdefault(message.parent_id, message)
        elif message.role == "user" and message.facts.steered_into:
            steers.setdefault(message.facts.steered_into, []).append(message)
    said: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user" and not message.facts.steered_into:
            if message.dispatch == "message":
                said.append(_line(message))
            said += _answer(shown.get(message.id), steers.get(message.id, []))
        elif message.role == "assistant" and message.shown and not message.parent_id:
            said += _answer(message, [])
    return said


def _answer(answer: Message | None, steers: list[Message]) -> list[dict[str, Any]]:
    """A turn's shown *answer*, one entry per text part, with its *steers* in their places."""
    texts = [p for p in answer.parts if isinstance(p, TextPart)] if answer else []
    waiting = sorted(steers, key=lambda steer: steer.facts.after_step)
    out: list[dict[str, Any]] = []
    for taken, part in enumerate(texts):
        while waiting and waiting[0].facts.after_step <= taken:
            out.append(_line(waiting.pop(0)))
        out.append(_entry("assistant", part.text, typing.cast(Message, answer)))
    return out + [_line(steer) for steer in waiting]


def _line(message: Message) -> dict[str, Any]:
    text = "".join(p.text for p in message.parts if isinstance(p, TextPart))
    return _entry(message.role, text, message)


def _entry(role: str, content: str, message: Message) -> dict[str, Any]:
    """One entry: *role* and *content*, with what *message* records of where it came from, under
    the keys a row records it by (``turn_source.provenance``)."""
    entry: dict[str, Any] = {"role": role, "content": content}
    entry.update({f: v for f in SOURCE_FIELDS if (v := getattr(message.source, f))})
    facts = message.facts
    meta: dict[str, Any] = {}
    if facts.merged:
        meta[QUEUED_FROM] = [dict(item.get("source") or {}) for item in facts.merged]
    if facts.from_outside is not None:
        meta[FROM_OUTSIDE] = dict(facts.from_outside)
    if facts.asked_for_by:
        meta[ASKED_FOR_BY] = dict(facts.asked_for_by)
    if meta:
        entry["meta"] = meta
    return entry
