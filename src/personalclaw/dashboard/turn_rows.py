"""A chat's rows, as it keeps them today, read as the typed turn (shared contracts §3.10).

A chat's transcript is rows (``_ChatSession.append``: ``{role, content, cls, ts, source…, meta}``).
:func:`from_rows` reads them as ``turns.Message`` values, the mapping the converter will use, and
the one the run-again question reads a replaced attempt through (``dashboard.repeated_steps``):

* **Turns** come from ``turn_source.by_turn``, the one segmentation. The row that starts one is a
  user message: ``user`` sent as a ``message``, ``inject``, ``subagent``, and ``nudge`` as a
  ``goal`` cycle. An older turn the gateway started on its helpers' failures, a ``user`` row of the
  ``auto-go`` class, is a ``message``, as its saved row reads once its class is gone. A steer
  (``turn_source.STEERED``) is a user message with ``facts.steered_into`` and ``facts.after_step``,
  how many of the answer's text parts came before it (a row records no step), and starts no turn.
* **Attempts.** The rest of a turn is its answer: one attempt of typed parts, in row order. An
  answer Regenerate kept earlier versions of (``variants``) is that many attempts of one parent:
  each earlier version one text part, the newest the rows, ``shown`` from ``variant_idx``.
* **Tool calls.** The rows with one ``meta.tool_call_id`` are one tool part, moved through
  ``turns.advance``. Its effect and risk are what its row records (``meta.effect``, ``meta.risk``),
  else its approval card's when the card established it reads, else what *declared* gives for its
  tool read as the approval gate reads it (``task_modes.resolve_effective_risk``), else
  ``unchecked`` and ``write``: fail closed. A call its approval refused (rejected, expired,
  cancelled), or a gate's line on it says was not run (``step_notes.NOT_RUN``), is ``refused``; one
  done is ``completed``, or ``failed`` when ``ok`` is false; one never done, in a turn that ended,
  is ``unknown`` as a write and ``cancelled`` as a read. A line about a call is its notes, and a
  line about none a notice.
* **Endings.** The start row's ``meta.turn.outcome`` when it records one; else a stop card is
  ``stopped``; an error row in the chat runner's words for a turn the gateway ended
  (``TURN_INTERRUPTED_NOTICES``) is ``interrupted``; any other error row is ``error``, the last of
  them the answer's ``error`` (``turn_failed``) and the others warnings; one in its words for a
  reply cut short (``TURN_CUT_SHORT_NOTICE``) is a ``cut_short`` notice of a ``stopped`` turn; else
  ``complete``. Rows are read at rest: every turn has ended.
* **Ids.** A row's ``meta.message_id`` when it holds one; otherwise ``ids.stable_id`` from the
  row's time and ``<session>|<role>|<ts>|<tool_call_id>|<n>``, where *n* counts the rows before it
  of the same role and time, so a row is the same id on every read.

A ``queued`` row (a message waiting for its turn) is passed over, and so is a row of a role not in
:data:`ROLES`. :func:`rewinds` reads the tails an edit and replay kept (``rewound``) the same way.
The words and keys it reads are the modules' own (``turn_source``, ``own_words``,
``chat_session_map``, ``chat_runner``, ``step_notes``, ``approval_state``, ``owner_questions``),
which is why it sits in ``dashboard/`` beside the chat that writes the rows: core does not import
the HTTP surface (``scripts/generate_structural_baseline.py``).
"""

from __future__ import annotations

import functools
import json
import os
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from personalclaw import ids, turns
from personalclaw.instants import as_instant
from personalclaw.tool_families import family_of
from personalclaw.turn_source import (
    ASKED_FOR_BY,
    FROM_OUTSIDE,
    QUEUED_FROM,
    by_turn,
    provenance,
    source_of,
    starts_a_turn,
)

#: What a tool declares for one call, as the approval gate reads it:
#: ``(title, tool_kind, tool_input) -> declared``.
Declared = Callable[[str, str, object], object]

#: The roles of the rows this reads. A ``queued`` row is read and dropped.
ROLES = frozenset(
    {
        "user",
        "inject",
        "subagent",
        "nudge",
        "assistant",
        "tool",
        "permission",
        "error",
        "notice",
        "system",
        "file",
        "queued",
    }
)

#: What started a turn, by the role of the row that starts it (``turn_source.TURN_STARTS``).
_DISPATCH: Mapping[str, turns.Dispatch] = {
    "user": "message",
    "inject": "inject",
    "subagent": "subagent",
    "nudge": "goal",
}

#: The time an id is made from when a row's own does not name an instant.
_EPOCH = "1970-01-01T00:00:00+00:00"

#: What an approval part keeps of its card as its brief.
_BRIEF = ("risk", "reach", "confirms", "asked_for", "blast_radius")

#: The closed words a fact of a message may hold.
_FACT_TYPES = typing.get_type_hints(turns.MessageFacts)


@dataclass(frozen=True)
class Rewind:
    """A tail an edit and replay set aside (``rewound``): the messages it kept, and when, on the
    message it was cut at."""

    message_id: str
    ts: str
    messages: tuple[turns.Message, ...]


@dataclass(frozen=True)
class _Words:
    """What the rows are read by, from the modules that write them."""

    interrupted: frozenset[str]
    cut_short: str
    about_call: str
    note: str
    not_run: str
    answers: frozenset[str]
    unanswered: frozenset[str]
    question_endings: frozenset[str]
    unanswerable: str
    own_words: str
    ran_prompt: str
    pastes: str
    stamps: tuple[str, ...]


@functools.cache
def _words() -> _Words:
    from personalclaw import owner_questions as asked
    from personalclaw.dashboard import chat_session_map as stamped
    from personalclaw.dashboard.approval_state import SESSION_APPROVAL_ACTIONS, UNANSWERED_OUTCOMES
    from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE, TURN_INTERRUPTED_NOTICES
    from personalclaw.dashboard.step_notes import ABOUT_CALL, NOT_RUN, NOTE
    from personalclaw.own_words import OWN_WORDS, PASTES, RAN_PROMPT

    return _Words(
        interrupted=frozenset(TURN_INTERRUPTED_NOTICES.values()),
        cut_short=TURN_CUT_SHORT_NOTICE,
        about_call=ABOUT_CALL,
        note=NOTE,
        not_run=NOT_RUN,
        answers=frozenset(SESSION_APPROVAL_ACTIONS),
        unanswered=frozenset(UNANSWERED_OUTCOMES),
        question_endings=frozenset({asked.ANSWERED, asked.SKIPPED, *asked.UNANSWERED}),
        unanswerable=asked.UNANSWERABLE,
        own_words=OWN_WORDS,
        ran_prompt=RAN_PROMPT,
        pastes=PASTES,
        stamps=(
            stamped.TURN_TELEMETRY_KEY,
            stamped.TURN_SUMMARY_KEY,
            stamped.FINISH_REASON_KEY,
            stamped.MODEL_SUBSTITUTION_KEY,
            stamped.LEARNED_KEY,
            stamped.CONTEXT_FED_KEY,
        ),
    )


def from_rows(
    rows: Iterable[Mapping[str, Any]], *, session_key: str, declared: Declared | None = None
) -> list[turns.Message]:
    """*rows*, a chat's rows in order, as its typed messages (see the module docstring).

    *declared* says what a call's tool declares, for a call whose row records neither its effect
    nor its risk: the run-again question passes the chat's live declarations. Without it, such a
    call is ``unchecked`` and a write."""
    return _Reader(session_key, session_key, declared).read(rows)


def rewinds(rows: Iterable[Mapping[str, Any]], *, session_key: str) -> list[Rewind]:
    """The tails an edit and replay kept on *rows* (``rewound``), each read as messages of its
    own, with ids of its own, on the message whose row keeps it."""
    reader = _Reader(session_key, session_key, None)
    rows = list(rows)
    reader.read(rows)
    out: list[Rewind] = []
    for row in rows:
        chain = row.get("rewound") if isinstance(row, Mapping) else None
        if id(row) not in reader.message_ids or not isinstance(chain, list):
            continue
        for k, snap in enumerate(chain):
            kept = snap.get("messages") if isinstance(snap, Mapping) else None
            if not isinstance(kept, list):
                continue
            seed = f"{reader.seeds[id(row)]}|rewound|{k}"
            messages = _Reader(session_key, seed, None).read(kept)
            out.append(
                Rewind(reader.message_ids[id(row)], str(snap.get("ts") or ""), tuple(messages))
            )
    return out


# ── reading one row's fields ───────────────────────────────────────────────────────────────────


def _meta(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    meta = row.get("meta") if isinstance(row, Mapping) else None
    return meta if isinstance(meta, Mapping) else {}


def _json(text: object) -> dict[str, Any]:
    """A row's ``cls`` or ``content`` that carries a record as JSON, or ``{}``."""
    if not isinstance(text, str) or not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _str(meta: Mapping[str, Any], key: str) -> str:
    value = meta.get(key)
    return value if isinstance(value, str) else ""


def _obj(meta: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = meta.get(key)
    return dict(value) if isinstance(value, Mapping) else None


def _items(meta: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    value = meta.get(key)
    return tuple(value) if isinstance(value, list) else ()


def _names(meta: Mapping[str, Any], key: str) -> list[str]:
    return [v for v in _items(meta, key) if isinstance(v, str) and v]


def _closed(value: object, words: Any) -> Any:
    """*value* when it is one of the closed *words* (a ``Literal``), else ``None``."""
    return value if value in typing.get_args(words) else None


def _instant(stamp: object) -> str:
    """*stamp* as an instant with its offset (``instants.as_instant``), or the epoch when it names
    none: an id and a part's time need one."""
    at = as_instant(stamp)
    if isinstance(at, str) and at:
        try:
            if datetime.fromisoformat(at).tzinfo is not None:
                return at
        except ValueError:
            pass
    return _EPOCH


def _variants(row: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw = row.get("variants") if isinstance(row, Mapping) else None
    return [v for v in raw if isinstance(v, Mapping)] if isinstance(raw, list) else []


@dataclass
class _Call:
    """What the rows with one call's id say of it, read as one: the first title, the latest input,
    kind and detail, and done once any says so."""

    first: Mapping[str, Any]
    last: Mapping[str, Any]
    title: str = ""
    kind: str = ""
    tool_input: str = ""
    detail: str = ""
    purpose: str = ""
    ungated: str = ""
    done: bool = False
    failed: bool = False
    risk: str = ""
    effect: str = ""
    result: Mapping[str, Any] | None = None
    error: dict[str, Any] | None = None
    question: dict[str, Any] | None = None

    def take(self, row: Mapping[str, Any], title: str) -> None:
        meta = _meta(row)
        self.last = row
        self.title = self.title or title
        for name, key in (("kind", "kind"), ("tool_input", "input"), ("detail", "detail")):
            setattr(self, name, _str(meta, key) or getattr(self, name))
        self.purpose = _str(meta, "purpose") or self.purpose
        self.ungated = _str(meta, "ungated") or self.ungated
        self.risk = _str(meta, "risk") or self.risk
        self.effect = _str(meta, "effect") or self.effect
        self.question = _obj(meta, "question") or self.question
        if meta.get("done"):
            self.done, self.result = True, meta
        self.failed = self.failed or meta.get("ok") is False
        self.error = _obj(meta, "agent_error") or self.error


class _Reader:
    """One read of a chat's rows: *session* names the chat, *seed* begins every id's seed."""

    def __init__(self, session: str, seed: str, declared: Declared | None) -> None:
        from personalclaw.chat_traces import attachment_roots

        self.session = session
        self.seed = seed
        self.declared = declared
        self.words = _words()
        self.roots = attachment_roots()
        self.seeds: dict[int, str] = {}
        self.message_ids: dict[int, str] = {}

    def read(self, rows: Iterable[Mapping[str, Any]]) -> list[turns.Message]:
        kept = [
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("role") in ROLES - {"queued"}
        ]
        seen: dict[tuple[str, str], int] = {}
        for row in kept:
            role, ts = str(row.get("role")), str(row.get("ts") or "")
            n = seen[(role, ts)] = seen.get((role, ts), -1) + 1
            call = _meta(row).get("tool_call_id") or ""
            self.seeds[id(row)] = f"{self.seed}|{role}|{ts}|{call}|{n}"
        messages: list[turns.Message] = []
        for turn in by_turn(kept):
            messages += self._turn(turn)
        return messages

    def _id(self, prefix: str, row: Mapping[str, Any], extra: str = "") -> str:
        seed = self.seeds[id(row)] + (f"|{extra}" if extra else "")
        return ids.stable_id(prefix, _instant(row.get("ts")), seed)

    # ── a turn ──

    def _turn(self, turn: list[Mapping[str, Any]]) -> list[turns.Message]:
        start = turn[0] if starts_a_turn(turn[0]) else None
        asked = self._user(start) if start is not None else None
        answer: list[Mapping[str, Any]] = []
        steers: list[tuple[Mapping[str, Any], int]] = []
        for row in turn[1:] if start is not None else turn:
            if row.get("role") == "user":  # a steer: any other user row starts a turn
                steers.append((row, sum(1 for r in answer if r.get("role") == "assistant")))
            else:
                answer.append(row)
        out = [asked] if asked is not None else []
        if answer:
            out += self._attempts(answer, asked, start)
        into = asked.id if asked is not None else ""
        out += [self._user(row, steered_into=into, after_step=n) for row, n in steers]
        return out

    def _user(
        self, row: Mapping[str, Any], *, steered_into: str = "", after_step: int = 0
    ) -> turns.Message:
        meta, words = _meta(row), self.words
        at = _instant(row.get("ts"))
        kept = _meta(provenance(row))
        merged = kept.get(QUEUED_FROM)
        facts = turns.MessageFacts(
            input_origin=_closed(meta.get("input_origin"), _FACT_TYPES["input_origin"]) or "",
            own_words=_str(meta, words.own_words),
            ran_prompt=_obj(meta, words.ran_prompt),
            original=_str(meta, "original"),
            ui_label=_str(meta, "ui_label"),
            injected_label=(
                str(_json(row.get("cls")).get("cronLabel") or "")
                if row.get("role") == "inject"
                else ""
            ),
            pastes=tuple(dict(p) for p in _items(meta, words.pastes) if isinstance(p, Mapping)),
            skills_used=tuple(
                dict(s) for s in _items(meta, "skills_used") if isinstance(s, Mapping)
            ),
            image_delivery=_obj(meta, "image_delivery"),
            image_delivery_reason=_str(meta, "image_delivery_reason"),
            merged=tuple({"source": s} for s in merged) if isinstance(merged, list) else (),
            steered_into=steered_into,
            after_step=after_step,
            from_outside=kept.get(FROM_OUTSIDE),
            asked_for_by=kept.get(ASKED_FOR_BY),
        )
        own_id = meta.get("message_id")
        message_id = (
            own_id if isinstance(own_id, str) and ids.is_id("msg", own_id) else self._id("msg", row)
        )
        self.message_ids[id(row)] = message_id
        made: dict[str, Any] = {"at": at, "updated_at": at}
        parts: list[turns.Part] = [
            turns.TextPart(id=self._id("p", row), text=str(row.get("content") or ""), **made)
        ]
        for path in _names(meta, "files"):
            name, part_id = os.path.basename(path), self._id("p", row, str(len(parts)))
            if os.path.realpath(path).startswith(self.roots):
                ref = turns.Ref(kind="attachment", id=path)
                parts.append(turns.FilePart(id=part_id, ref=ref, direction="in", name=name, **made))
            else:
                parts.append(
                    turns.MentionPart(id=part_id, target="file", ref=path, label=name, **made)
                )
        for target, key in (("knowledge", "knowledge"), ("artifact", "artifacts")):
            for picked in _names(meta, key):
                parts.append(
                    turns.MentionPart(
                        id=self._id("p", row, str(len(parts))),
                        target=typing.cast(Any, target),
                        ref=picked,
                        label=picked,
                        **made,
                    )
                )
        return turns.Message(
            id=message_id,
            session=self.session,
            role="user",
            ts=at,
            source=turns.Source(**source_of(row)),
            parts=tuple(parts),
            dispatch="message" if steered_into else _DISPATCH[str(row.get("role"))],
            facts=facts,
        )

    # ── an answer ──

    def _attempts(
        self,
        rows: list[Mapping[str, Any]],
        asked: turns.Message | None,
        start: Mapping[str, Any] | None,
    ) -> list[turns.Message]:
        """The answer's attempts: each earlier version Regenerate kept, then the newest."""
        parent = asked.id if asked is not None else ""
        said = next((r for r in rows if r.get("role") == "assistant"), rows[0])
        source = turns.Source(**source_of(said))
        keyed = next((r for r in rows if r.get("role") == "assistant" and _variants(r)), None)
        variants = _variants(keyed)
        shown = keyed.get("variant_idx") if keyed is not None else None
        if not isinstance(shown, int) or isinstance(shown, bool) or not 0 <= shown < len(variants):
            shown = len(variants) - 1
        out: list[turns.Message] = []
        kept_on = self.seeds[id(keyed)] if keyed is not None else ""
        for n, version in enumerate(variants[:-1]):
            at = _instant(version.get("ts"))
            seed = f"{kept_on}|variant|{n}"
            # The version shown is the text its row holds now.
            text = (keyed or {}).get("content") if n == shown else version.get("content")
            part = turns.TextPart(
                id=ids.stable_id("p", at, seed), at=at, updated_at=at, text=str(text or "")
            )
            out.append(
                turns.Message(
                    id=ids.stable_id("msg", at, seed),
                    session=self.session,
                    role="assistant",
                    ts=at,
                    source=source,
                    parts=(part,),
                    parent_id=parent,
                    attempt=n + 1,
                    shown=n == shown,
                    settled_at=at,
                    outcome="complete",
                )
            )
        outcome, failed_by = self._ending(rows, start)
        newest_text = (
            str(variants[-1].get("content") or "")
            if keyed is not None and shown != len(variants) - 1
            else None
        )
        out.append(
            turns.Message(
                id=self._id("msg", rows[0]),
                session=self.session,
                role="assistant",
                ts=_instant(rows[0].get("ts")),
                source=source,
                parts=tuple(self._parts(rows, failed_by, keyed, newest_text)),
                parent_id=parent,
                attempt=max(1, len(variants)),
                shown=shown == len(variants) - 1,
                settled_at=_instant(rows[-1].get("ts")),
                outcome=outcome,
                error=(
                    turns.MessageError(
                        code="turn_failed",
                        message=str(failed_by.get("content") or ""),
                        retryable=True,
                    )
                    if failed_by is not None
                    else None
                ),
                facts=self._answer_facts(rows),
            )
        )
        return out

    def _ending(
        self, rows: list[Mapping[str, Any]], start: Mapping[str, Any] | None
    ) -> tuple[turns.Outcome, Mapping[str, Any] | None]:
        """How the turn ended, and the error row that is its answer's error when it failed."""
        words = self.words
        recorded = _meta(start).get("turn")
        outcome = _closed(
            recorded.get("outcome") if isinstance(recorded, Mapping) else None, turns.Outcome
        )
        said = [str(r.get("content") or "") for r in rows if r.get("role") == "error"]
        failed = [
            r
            for r in rows
            if r.get("role") == "error"
            and str(r.get("content") or "") not in words.interrupted | {words.cut_short}
        ]
        if outcome is None:
            if any(_is_stop_card(r) for r in rows):
                outcome = "stopped"
            elif any(text in words.interrupted for text in said):
                outcome = "interrupted"
            elif failed:
                outcome = "error"
            elif words.cut_short in said:
                outcome = "stopped"
            else:
                outcome = "complete"
        return outcome, failed[-1] if outcome == "error" and failed else None

    def _parts(
        self,
        rows: list[Mapping[str, Any]],
        failed_by: Mapping[str, Any] | None,
        keyed: Mapping[str, Any] | None,
        keyed_text: str | None,
    ) -> list[turns.Part]:
        """The newest attempt's parts, in row order. *keyed* is the row Regenerate kept the earlier
        versions on, whose own text is *keyed_text* when an earlier version is the one shown."""
        from personalclaw.dashboard.chat_utils import strip_status_sentinel

        words = self.words
        calls: dict[str, _Call] = {}
        cards: dict[str, dict[str, Any]] = {}
        approvals: dict[str, str] = {}
        refused: set[str] = set()
        notes: dict[str, list[str]] = {}
        for row in rows:
            meta, role = _meta(row), row.get("role")
            if role == "tool" and (call_id := str(meta.get("tool_call_id") or "")):
                title = strip_status_sentinel(str(row.get("content") or ""))
                calls.setdefault(call_id, _Call(first=row, last=row)).take(row, title)
            elif role == "permission" and (
                call_id := str(_json(row.get("cls")).get("tool_call_id") or "")
            ):
                card = cards[call_id] = _json(row.get("cls"))
                approvals[call_id] = self._id("p", row)
                if card.get("resolved") == "rejected" or card.get("resolved") in words.unanswered:
                    refused.add(call_id)
        for row in rows:
            meta = _meta(row)
            about = _str(meta, words.about_call)
            if row.get("role") == "tool" and not meta.get("tool_call_id") and about in calls:
                note = _str(meta, words.note) or str(row.get("content") or "")
                notes.setdefault(about, []).append(note)
                if note.startswith(words.not_run):
                    refused.add(about)
        tool_ids = {call_id: self._id("p", call.first) for call_id, call in calls.items()}
        parts: list[turns.Part] = []
        made: set[str] = set()
        for row in rows:
            meta, role = _meta(row), row.get("role")
            at = _instant(row.get("ts"))
            text = str(row.get("content") or "")
            stamp: dict[str, Any] = {"id": self._id("p", row), "at": at, "updated_at": at}
            if role == "assistant":
                if row is keyed and keyed_text is not None:
                    text = keyed_text
                parts.append(turns.TextPart(text=text, **stamp))
            elif role == "tool" and (call_id := str(meta.get("tool_call_id") or "")):
                if call_id not in made:
                    made.add(call_id)
                    tool = self._tool(
                        calls[call_id],
                        tool_ids[call_id],
                        refused=call_id in refused,
                        notes=notes.get(call_id, []),
                        card=cards.get(call_id),
                        approval_id=approvals.get(call_id, ""),
                    )
                    parts += tool
            elif role == "tool":
                if _str(meta, words.about_call) not in calls:
                    said = _str(meta, words.note) or text
                    parts.append(turns.NoticePart(notice="info", text=said, **stamp))
            elif role == "permission":
                parts.append(self._approval(row, tool_ids, stamp))
            elif role == "error" and row is not failed_by:
                notice: turns.NoticeKind = (
                    "interrupted"
                    if text in words.interrupted
                    else "cut_short" if text == words.cut_short else "info"
                )
                parts.append(turns.NoticePart(notice=notice, text=text, level="warn", **stamp))
            elif role == "system":
                if _is_stop_card(row):
                    parts.append(turns.NoticePart(notice="stopped", **stamp))
                else:
                    parts.append(turns.NoticePart(notice="info", text=text, **stamp))
            elif role == "notice":
                parts.append(turns.NoticePart(notice="info", text=text, **stamp))
            elif role == "file":
                parts.append(_delivered(_json(row.get("content")), text, stamp))
        return parts

    def _tool(
        self,
        call: _Call,
        part_id: str,
        *,
        refused: bool,
        notes: list[str],
        card: Mapping[str, Any] | None,
        approval_id: str,
    ) -> list[turns.Part]:
        """The call's tool part, in the state its rows say it reached, and its question's part."""
        declared = self.declared(call.title, call.kind, call.tool_input) if self.declared else ""
        risk, effect = self._reading(call, card, declared)
        at, end = _instant(call.first.get("ts")), _instant(call.last.get("ts"))
        part = turns.ToolPart(
            id=part_id,
            at=at,
            updated_at=at,
            tool_call_id=str(_meta(call.first).get("tool_call_id")),
            tool=call.title,
            title=call.title,
            family=family_of(call.title, call.kind, declared, call.tool_input),
            input=call.tool_input,
            risk=risk,
            effect=effect,
            detail=call.detail,
            tool_kind=call.kind,
            purpose=call.purpose,
            approval_part_id=approval_id,
            ungated=call.ungated,
            notes=tuple(notes),
        )
        if refused:
            part = turns.advance(part, "refused", now=end)
        elif call.done:
            part = turns.advance(part, "running", now=at)
            ended: dict[str, Any] = {"result": _result(call.result or {})}
            if call.failed and call.error is not None:
                ended["error"] = call.error
            part = turns.advance(part, "failed" if call.failed else "completed", now=end, **ended)
        else:
            part = turns.advance(part, "unknown" if effect == "write" else "cancelled", now=end)
        parts: list[turns.Part] = [part]
        if call.question is not None:
            parts.append(self._question(call.question, part, call.first))
        return parts

    def _reading(
        self, call: _Call, card: Mapping[str, Any] | None, declared: object
    ) -> tuple[turns.Risk, turns.Effect]:
        """The call's risk and effect: as its row records them, else as its approval card
        established a read, else as its tool's declaration reads, else unchecked and a write."""
        effect = _closed(call.effect, turns.Effect)
        if effect is not None:
            return _closed(call.risk, turns.Risk) or "unchecked", effect
        if card is not None and card.get("is_read_only") == "1":
            return _closed(card.get("risk"), turns.Risk) or "safe", "read"
        if self.declared is None:
            return "unchecked", "write"
        from personalclaw.task_modes import resolve_effective_risk

        risk = _closed(
            resolve_effective_risk(declared, call.title, call.kind, call.tool_input), turns.Risk
        )
        read: turns.Risk = risk or "unchecked"
        return read, "read" if read == "safe" else "write"

    def _question(
        self, record: Mapping[str, Any], tool: turns.ToolPart, row: Mapping[str, Any]
    ) -> turns.QuestionPart:
        words = self.words
        part = turns.QuestionPart(
            id=self._id("p", row, "question"),
            at=tool.at,
            updated_at=tool.at,
            question_id=str(record.get("id") or ""),
            tool_part_id=tool.id,
            questions=tuple(dict(q) for q in _items(record, "questions") if isinstance(q, Mapping)),
            asked_by=_str(record, "asked_by"),
            answerable=record.get("outcome") != words.unanswerable,
            note=_str(record, "note"),
        )
        ending = record.get("outcome")
        if ending in words.question_endings:
            answers = tuple(dict(a) for a in _items(record, "answers") if isinstance(a, Mapping))
            part = turns.advance(
                part, str(ending), now=tool.updated_at, answers=answers, ended=_str(record, "ended")
            )
        return part

    def _approval(
        self, row: Mapping[str, Any], tool_ids: Mapping[str, str], stamp: dict[str, Any]
    ) -> turns.ApprovalPart:
        words = self.words
        card = _json(row.get("cls"))
        part = turns.ApprovalPart(
            request_id=str(card.get("request_id") or ""),
            tool_part_id=tool_ids.get(str(card.get("tool_call_id") or ""), ""),
            brief={key: card[key] for key in _BRIEF if key in card},
            grant_agent=str(card.get("grant_agent") or ""),
            deny_effect=str(card.get("deny_effect") or ""),
            **stamp,
        )
        resolved = card.get("resolved")
        if resolved in words.unanswered:
            return turns.advance(part, str(resolved), now=part.at, decided_by="nobody")
        if resolved in words.answers:
            ended = "rejected" if resolved == "rejected" else "approved"
            return turns.advance(part, ended, now=part.at, answer=str(resolved), decided_by="you")
        return part

    def _answer_facts(self, rows: list[Mapping[str, Any]]) -> turns.MessageFacts:
        """The stamps on the answer's rows, as its facts, the latest of each. A learned item a row
        recorded before items had a state was kept."""
        telemetry, summary, finish, substitution, learned, fed = self.words.stamps
        stamps: dict[str, Any] = {}
        for row in rows:
            if row.get("role") == "assistant":
                keys = (*self.words.stamps, "memory_citations", "file_changes")
                stamps.update({k: v for k in keys if (v := _meta(row).get(k))})
        return turns.MessageFacts(
            turn_telemetry=_obj(stamps, telemetry),
            summary=_str(stamps, summary),
            finish_reason=_closed(stamps.get(finish), _FACT_TYPES["finish_reason"]) or "",
            model_substitution=_str(stamps, substitution),
            learned=tuple(
                {"state": "kept", **item}
                for item in _items(stamps, learned)
                if isinstance(item, Mapping)
            ),
            context_fed=_obj(stamps, fed),
            memory_citations=_items(stamps, "memory_citations"),
            file_changes=tuple(
                dict(c) for c in _items(stamps, "file_changes") if isinstance(c, Mapping)
            ),
        )


def _is_stop_card(row: Mapping[str, Any]) -> bool:
    """A Stop's record, which travels as JSON in a ``system`` row (``chat_handlers``'
    ``_open_stop_event``)."""
    return row.get("role") == "system" and _json(row.get("content")).get("kind") == "stop_event"


def _result(meta: Mapping[str, Any]) -> turns.ToolResult:
    """What a call returned, as its row keeps it."""
    length = meta.get("original_length")
    return turns.ToolResult(
        text=_str(meta, "output"),
        content_type=_str(meta, "content_type"),
        raw_ref=_str(meta, "raw_ref"),
        truncated=meta.get("truncated") is True,
        original_length=(
            length if isinstance(length, int) and not isinstance(length, bool) else None
        ),
        recovery_hints=tuple(str(h) for h in _items(meta, "recovery_hints")),
    )


def _delivered(record: Mapping[str, Any], text: str, stamp: dict[str, Any]) -> turns.Part:
    """A file a tool delivered (a ``file`` row), as the turn's output file; a row that does not
    say which file is a notice of what it does say."""
    path = record.get("path")
    if not isinstance(path, str) or not path:
        return turns.NoticePart(notice="info", text=text, **stamp)
    size = record.get("size")
    return turns.FilePart(
        ref=turns.Ref(kind="path", id=path),
        direction="out",
        name=_str(record, "filename") or os.path.basename(path),
        mime=_str(record, "content_type"),
        size=size if isinstance(size, int) and not isinstance(size, bool) else 0,
        **stamp,
    )


__all__ = ["ROLES", "Declared", "Rewind", "from_rows", "rewinds"]
