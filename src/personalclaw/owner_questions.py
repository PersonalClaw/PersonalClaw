"""The questions an agent asks its owner while it waits, and the one way her answer comes back.

An agent that needs the owner's decision to go on asks it as a question with options: PersonalClaw's
own runtime through its ``ask_user`` tool (``tool_providers/ask_user.py``), an agent CLI through its
own question tool bridged onto ACP's form elicitation (``acp/elicitation.py``). Either way the call
waits, and this registry is what it waits on:

* the question reaches her where she is: an interactive card in the chat that asked it (the
  ``question_card`` frame, and ``pending_for`` for a page that opens or reloads while it waits),
  and an Inbox row (``needs_input``), which Mission Control's Your turn lane and Home list, so she
  sees it when she is not on that chat;
* her answer (:meth:`OwnerQuestions.answer`) reaches the waiting call once. A second answer, or
  an answer once the call stopped waiting, is refused with a sentence saying which;
* every way it ends (answered, skipped, nobody answered in the owner's window, the turn stopped)
  ends it on every surface at once: the ``question_resolved`` frame, the Inbox row, and the tool
  row of the transcript, so a reload shows what she saw.

A question whose tool PersonalClaw cannot send an answer to (another runtime's own question tool)
is shown and nothing waits on it (:meth:`OwnerQuestions.show_unanswerable`).

Core code: the dashboard state arrives duck-typed, as :mod:`personalclaw.mcp_elicitation` takes it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from personalclaw.constants import DASHBOARD_SESSION_PREFIX
from personalclaw.security import redact_for_display
from personalclaw.textfmt import clip_words
from personalclaw.validation import sanitize_string, validate_ask_user_question

logger = logging.getLogger(__name__)

#: PersonalClaw's own question tool, as its runtime names the call.
ASK_USER_TOOL = "ask_user"

#: The frames every open page acts on.
QUESTION_CARD = "question_card"
QUESTION_RESOLVED = "question_resolved"

#: How a question ends. The first two are her answer; the last two end it with none.
ANSWERED = "answered"
SKIPPED = "skipped"
EXPIRED = "expired"
CANCELLED = "cancelled"
UNANSWERED = frozenset({EXPIRED, CANCELLED})
#: Shown, with no way to send an answer back (:meth:`OwnerQuestions.show_unanswerable`).
UNANSWERABLE = "unanswerable"

#: The longest answer she may type for one question.
OTHER_MAX = 2000

#: How many ended questions the registry remembers, so a late answer is told how it ended.
_ENDED_KEPT = 256

#: Why a turn's question ended when its turn was stopped.
TURN_STOPPED = "its turn was stopped"


class QuestionRefused(Exception):
    """An answer the registry will not deliver: its wire ``code`` and the sentence saying why
    (``question_not_found``, ``question_answered``, ``question_ended``,
    ``question_answer_invalid``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Answer:
    """Her answer to one question: the options she chose (by index) and what she typed."""

    selected: tuple[int, ...] = ()
    other: str = ""


@dataclass(frozen=True)
class Outcome:
    """How a question ended: one of the four outcomes, her answers when she gave them, and why
    it ended when it ended with none."""

    kind: str
    answers: tuple[Answer, ...] = ()
    ended: str = ""


@dataclass
class _Asked:
    id: str
    session: str
    tool_call_id: str
    #: As the agent asked them: what the call is told her answer was.
    questions: list[dict[str, Any]]
    #: Masked for display: what every surface shows.
    shown: list[dict[str, Any]]
    asked_by: str
    ts: float
    future: "asyncio.Future[Outcome] | None" = None
    outcome: Outcome | None = None


def normalize(raw: object) -> list[dict[str, Any]]:
    """An ask-the-user call's questions (``{"questions": [...]}``, AskUserQuestion's shape), as
    this registry holds them: ``question``, ``header``, ``multiSelect``, ``free_text`` and
    ``options`` (``label``, ``description``, ``value``). Raises ``ValidationError`` for a call
    with no question that has options."""
    import json

    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
    out = validate_ask_user_question(payload)
    for q in out:
        # The tool always takes a typed answer of her own beside its options.
        q["free_text"] = True
        for opt in q["options"]:
            opt["value"] = opt["label"]
    return out


def _shown(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The questions as a page shows them: every string the agent wrote masked, no values."""
    return [
        {
            "question": redact_for_display(q["question"]),
            "header": redact_for_display(q.get("header") or ""),
            "multiSelect": bool(q.get("multiSelect")),
            "free_text": bool(q.get("free_text")),
            "options": [
                {
                    "label": redact_for_display(o["label"]),
                    "description": redact_for_display(o.get("description") or ""),
                }
                for o in q["options"]
            ],
        }
        for q in questions
    ]


def read_answers(questions: list[dict[str, Any]], raw: object) -> tuple[Answer, ...]:
    """The answers a page sent (``[{"selected": [index…], "other": str}]``, one per question),
    checked against what was asked. Raises :class:`QuestionRefused` for one this question
    cannot take: a list of the wrong length, an option it did not offer, two options on a
    one-choice question, a typed answer it takes none of, or a question left unanswered."""

    def bad(why: str) -> QuestionRefused:
        return QuestionRefused("question_answer_invalid", why)

    if not isinstance(raw, list) or len(raw) != len(questions):
        raise bad(f"Send one answer for each of the {len(questions)} questions.")
    out: list[Answer] = []
    for n, (q, a) in enumerate(zip(questions, raw), start=1):
        if not isinstance(a, dict):
            raise bad(f"Answer {n} is not an object.")
        picked = a.get("selected", [])
        if not isinstance(picked, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in picked
        ):
            raise bad(f"Answer {n} names its options by their place in the list.")
        if len(set(picked)) != len(picked) or any(i < 0 or i >= len(q["options"]) for i in picked):
            raise bad(f"Answer {n} chooses an option question {n} does not offer.")
        if len(picked) > 1 and not q.get("multiSelect"):
            raise bad(f"Question {n} takes one choice.")
        other = a.get("other", "")
        if not isinstance(other, str):
            raise bad(f"Answer {n}'s own words are not text.")
        other = sanitize_string(other)
        if other and not q.get("free_text"):
            raise bad(f"Question {n} takes one of its options, not words of your own.")
        if len(other) > OTHER_MAX:
            raise bad(f"Answer {n} is longer than {OTHER_MAX} characters.")
        if not picked and not other:
            raise bad(f"Question {n} has no answer. Choose an option, or skip the questions.")
        out.append(Answer(tuple(picked), other))
    return tuple(out)


def answer_lines(questions: list[dict[str, Any]], outcome: Outcome, window: str = "") -> str:
    """What the call that asked is told: her answers, or how the question ended without them."""
    if outcome.kind == ANSWERED:
        lines = ["The user answered:"]
        for n, (q, a) in enumerate(zip(questions, outcome.answers), start=1):
            chose = ", ".join(q["options"][i]["label"] for i in a.selected)
            said = f'"{a.other}"' if a.other else ""
            both = f"{chose}; and wrote {said}" if chose and said else (chose or said)
            lines.append(f"{n}. {q['question']} → {both}")
        return "\n".join(lines)
    if outcome.kind == SKIPPED:
        return (
            "The user chose not to answer. Go on with your best judgement, and say in your reply "
            "what you assumed."
        )
    if outcome.kind == EXPIRED:
        return (
            f"Nobody answered within {window or 'the time a question waits'}, so the question "
            "was withdrawn. Go on without the answer, or ask in your reply."
        )
    return f"The question was withdrawn before the user answered: {outcome.ended}."


def _dashboard_key(session_key: str) -> str:
    return str(session_key or "").removeprefix(DASHBOARD_SESSION_PREFIX)


def asker(session: Any, agent: str = "") -> str:
    """Who is asking, as every surface says it: the agent (*agent*, else the chat's own), in its
    chat."""
    from personalclaw.agents.defaults import DEFAULT_NATIVE_AGENT_NAME

    who = agent or getattr(session, "agent", "") or DEFAULT_NATIVE_AGENT_NAME
    title = redact_for_display(str(getattr(session, "title", "") or "")).strip()
    return f"{who} in “{title}”" if title else f"{who} in a chat"


class OwnerQuestions:
    """The registry of the questions agents are waiting on, held by the dashboard state."""

    def __init__(self, state: Any) -> None:
        self._state = state
        #: Pending and recently ended questions, by id.
        self._asked: dict[str, _Asked] = {}
        #: Calls of PersonalClaw's own tool its chat has shown and not yet asked, per chat:
        #: ``(tool_call_id, arguments)``, so the question attaches to the call it came from.
        self._calls: dict[str, list[tuple[str, Any]]] = {}
        #: Calls an agent CLI showed whose own input is a question to her, per chat: the words of
        #: each call's questions, by its id, so its question, once asked, is known to be its own.
        self._question_calls: dict[str, dict[str, tuple[str, ...]]] = {}

    # ── who can be asked ──────────────────────────────────────────────────────────────────
    def cannot_ask(self, session_key: str) -> str:
        """Why nobody can answer a question asked from *session_key* in a chat card, or ``""``
        when its owner can. A question is put to the owner of an open chat; any other work (a
        background task, a loop or a workflow's step), a chat carried on a chat channel, and a
        chat that runs on its own have nobody at a card to answer it."""
        session = self._state.get_session(_dashboard_key(session_key))
        if session is None:
            return "this work is not a chat the user has open"
        if getattr(session, "_channel_linked", False):
            return "this chat is carried on a chat channel, where a question card is not shown"
        if getattr(session, "_unattended", None):
            return "this chat runs on its own, with nobody there to answer"
        return ""

    # ── asking ────────────────────────────────────────────────────────────────────────────
    def note_call(self, session_key: str, tool_call_id: str, arguments: Any) -> None:
        """A chat showed a call of PersonalClaw's own question tool (``ask_user``); its question
        is asked next, and attaches to this call."""
        calls = self._calls.setdefault(_dashboard_key(session_key), [])
        calls.append((str(tool_call_id or ""), arguments))
        del calls[:-8]

    def _take_call(self, session: str, arguments: Any) -> str:
        """The shown call this question came from: the latest one with its arguments, else the
        latest one (a call a gate refused before it asked leaves an older entry behind)."""
        calls = self._calls.get(session) or []
        for i in range(len(calls) - 1, -1, -1):
            if calls[i][1] == arguments:
                return calls.pop(i)[0]
        return calls.pop()[0] if calls else ""

    def note_question_call(
        self, session_key: str, tool_call_id: str, questions: list[dict[str, Any]]
    ) -> None:
        """A chat showed an agent CLI's call whose input is a question to her (its own question
        tool, which asks her through the card): keep the words it asks, for :meth:`was_put`."""
        if not tool_call_id:
            return
        calls = self._question_calls.setdefault(_dashboard_key(session_key), {})
        calls.pop(str(tool_call_id), None)
        calls[str(tool_call_id)] = tuple(q["question"] for q in questions)
        while len(calls) > 8:
            del calls[next(iter(calls))]

    def was_put(self, session_key: str, tool_call_id: str) -> bool:
        """Whether the chat's call *tool_call_id* put its own question to her on a card: an agent
        CLI's question tool whose input asks what she was asked for that call. The card was that
        call's gate: her answer, her Skip, or how the question ended without one."""
        chat = _dashboard_key(session_key)
        words = (self._question_calls.get(chat) or {}).get(str(tool_call_id or ""))
        return bool(words) and any(
            a.session == chat
            and a.tool_call_id == tool_call_id
            and tuple(q["question"] for q in a.questions) == words
            for a in self._asked.values()
        )

    async def ask(
        self,
        session_key: str,
        questions: list[dict[str, Any]],
        *,
        asked_by: str,
        tool_call_id: str = "",
        arguments: Any = None,
    ) -> Outcome:
        """Put *questions* to the owner of the chat *session_key* and wait for her answer.

        Waits the owner's window (``agent.approval_timeout_minutes``, the one every wait on her
        has); ends ``expired`` when nobody answers in it, and ``cancelled`` when the wait is
        torn down first (her Stop, the gateway stopping). ``arguments`` are the call's own, for
        a call of PersonalClaw's own tool (:meth:`note_call`)."""
        chat = _dashboard_key(session_key)
        if not tool_call_id and arguments is not None:
            tool_call_id = self._take_call(chat, arguments)
        asked = _Asked(
            id=uuid.uuid4().hex[:12],
            session=chat,
            tool_call_id=str(tool_call_id or ""),
            questions=questions,
            shown=_shown(questions),
            asked_by=redact_for_display(asked_by or "An agent"),
            ts=time.time(),
            future=asyncio.get_running_loop().create_future(),
        )
        self._asked[asked.id] = asked
        window = self._window()
        outcome: Outcome | None = None
        try:
            # Published inside the try: a wait torn down mid-publication still ends what it put up.
            self._publish(asked)
            assert asked.future is not None
            outcome = await asyncio.wait_for(asyncio.shield(asked.future), timeout=window)
            return outcome
        except asyncio.TimeoutError:
            outcome = self._end(asked, EXPIRED, f"nobody answered within {self._words(window)}")
            return outcome
        finally:
            if outcome is None:
                self._end(asked, CANCELLED, self._why_cancelled())

    def show_unanswerable(
        self, session_key: str, questions: list[dict[str, Any]], *, asked_by: str, tool_call_id: str
    ) -> None:
        """Show a question whose tool PersonalClaw cannot send an answer to, saying so. Nothing
        waits on it: the agent's own tool goes on without her, and she answers in her reply."""
        chat = _dashboard_key(session_key)
        shown = _shown(questions)
        note = (
            f"{asked_by or 'The agent'} asked this through its own question tool, which "
            "PersonalClaw cannot send an answer to, so it goes on without one. Answer in your "
            "next message if it still needs one."
        )
        card = {
            "id": uuid.uuid4().hex[:12],
            "session": chat,
            "tool_call_id": tool_call_id,
            "questions": shown,
            "asked_by": redact_for_display(asked_by or ""),
            "ts": time.time(),
            "answerable": False,
            "note": note,
        }
        self._broadcast(QUESTION_CARD, card)
        self._record(
            chat,
            tool_call_id,
            {
                **{k: card[k] for k in ("id", "questions", "asked_by", "note")},
                "outcome": UNANSWERABLE,
            },
        )

    # ── answering ─────────────────────────────────────────────────────────────────────────
    def answer(
        self, session_key: str, question_id: str, *, answers: object = None, skip: bool = False
    ) -> Outcome:
        """Deliver her answer (or her Skip) to the call waiting on *question_id*, once."""
        asked = self._asked.get(str(question_id or ""))
        if asked is None or asked.session != _dashboard_key(session_key):
            raise QuestionRefused("question_not_found", "This chat has no question with that id.")
        if asked.outcome is not None:
            if asked.outcome.kind in (ANSWERED, SKIPPED):
                raise QuestionRefused(
                    "question_answered", "This question has already been answered."
                )
            raise QuestionRefused(
                "question_ended",
                f"The agent is no longer waiting for this answer: {asked.outcome.ended}.",
            )
        outcome = (
            Outcome(SKIPPED) if skip else Outcome(ANSWERED, read_answers(asked.questions, answers))
        )
        self._settle(asked, outcome)
        return outcome

    def end_turn(self, session_key: str, *, reason: str = TURN_STOPPED) -> int:
        """The chat's turn is being stopped: every question it waits on ends, cancelled."""
        chat = _dashboard_key(session_key)
        pending = [a for a in self._asked.values() if a.session == chat and a.outcome is None]
        for asked in pending:
            self._end(asked, CANCELLED, reason)
        return len(pending)

    # ── reading ───────────────────────────────────────────────────────────────────────────
    def pending_for(self, session_key: str) -> list[dict[str, Any]]:
        """The cards of the questions the chat is waiting on, as their frames carried them."""
        chat = _dashboard_key(session_key)
        return [
            self._card(a) for a in self._asked.values() if a.session == chat and a.outcome is None
        ]

    def waiting(self, session_key: str) -> bool:
        """Whether the chat's turn is waiting on its owner's answer to a question."""
        return bool(self.pending_for(session_key))

    def close_orphaned_rows(self) -> int:
        """Close the Inbox rows of the questions the previous run asked. None survives a
        restart: the turns that waited on them are gone, so their rows ask for nothing."""
        from personalclaw.inbox import (
            GATEWAY_RESTARTED,
            OPEN_STATUSES,
            expire_attention_items,
            live_store,
        )

        store = live_store(self._state)
        if store is None:
            return 0
        orphaned = {
            str(item.refs.get("question"))
            for item in store.items.values()
            if item.status in OPEN_STATUSES
            and item.refs.get("question")
            and str(item.refs.get("question")) not in self._asked
        }
        return sum(
            expire_attention_items(self._state, {"question": qid}, ended=GATEWAY_RESTARTED)
            for qid in sorted(orphaned)
        )

    # ── internals ─────────────────────────────────────────────────────────────────────────
    def _card(self, asked: _Asked) -> dict[str, Any]:
        return {
            "id": asked.id,
            "session": asked.session,
            "tool_call_id": asked.tool_call_id,
            "questions": asked.shown,
            "asked_by": asked.asked_by,
            "ts": asked.ts,
            "answerable": True,
        }

    def _publish(self, asked: _Asked) -> None:
        self._broadcast(QUESTION_CARD, self._card(asked))
        try:
            from personalclaw.inbox import ItemKind, emit_attention_item

            first = asked.shown[0]
            more = len(asked.shown) - 1
            emit_attention_item(
                self._state,
                source="system",
                kind="agent_request",
                item_kind=ItemKind.NEEDS_INPUT.value,
                title=clip_words(first["question"], 160),
                body=f"{asked.asked_by} is waiting for your answer"
                + (f" to this and {more} more question{'s' if more > 1 else ''}." if more else "."),
                refs={
                    "question": asked.id,
                    "session": asked.session,
                    "source_label": asked.asked_by,
                },
                dedup_key=f"question:{asked.id}",
            )
        except Exception:
            # The question is what she is waiting on; losing it to the row is worse.
            logger.debug("question inbox row failed", exc_info=True)
        self._push_sessions()

    def _settle(self, asked: _Asked, outcome: Outcome) -> None:
        """Record *outcome* and wake the waiting call with it."""
        if asked.outcome is not None:
            return
        asked.outcome = outcome
        if asked.future is not None and not asked.future.done():
            asked.future.set_result(outcome)
        self._withdraw(asked)

    def _end(self, asked: _Asked, kind: str, why: str) -> Outcome:
        outcome = Outcome(kind, ended=why)
        self._settle(asked, outcome)
        return asked.outcome or outcome

    def _withdraw(self, asked: _Asked) -> None:
        """Take an ended question off every surface, saying how it ended."""
        outcome = asked.outcome
        assert outcome is not None
        logger.info(
            "question %s in chat %s ended %s%s",
            asked.id,
            asked.session,
            outcome.kind,
            f" ({outcome.ended})" if outcome.ended else "",
        )
        answers = [
            {"selected": list(a.selected), "other": redact_for_display(a.other)}
            for a in outcome.answers
        ]
        ended = {"ended": outcome.ended} if outcome.kind in UNANSWERED else {}
        self._broadcast(
            QUESTION_RESOLVED,
            {
                "id": asked.id,
                "session": asked.session,
                "tool_call_id": asked.tool_call_id,
                "outcome": outcome.kind,
                **({"answers": answers} if answers else {}),
                **ended,
            },
        )
        try:
            from personalclaw.inbox import expire_attention_items, resolve_attention_items

            if outcome.kind in UNANSWERED:
                expire_attention_items(self._state, {"question": asked.id}, ended=outcome.ended)
            else:
                resolve_attention_items(self._state, {"question": asked.id})
        except Exception:
            logger.debug("could not close the inbox row of question %s", asked.id, exc_info=True)
        self._record(
            asked.session,
            asked.tool_call_id,
            {
                "id": asked.id,
                "questions": asked.shown,
                "asked_by": asked.asked_by,
                "outcome": outcome.kind,
                **({"answers": answers} if answers else {}),
                **ended,
            },
        )
        self._push_sessions()
        while len(self._asked) > _ENDED_KEPT:
            oldest = next((k for k, a in self._asked.items() if a.outcome is not None), None)
            if oldest is None:
                break
            del self._asked[oldest]

    def _record(self, chat: str, tool_call_id: str, record: dict[str, Any]) -> None:
        """Keep how the question went on its call's row of the transcript, so a reload shows the
        card she saw."""
        if not tool_call_id:
            return
        session = self._state.get_session(chat)
        for msg in reversed(getattr(session, "messages", None) or []):
            meta = msg.get("meta")
            if msg.get("role") == "tool" and isinstance(meta, dict):
                if meta.get("tool_call_id") == tool_call_id:
                    meta["question"] = record
                    session._dirty = True
                    return

    def _broadcast(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._state.broadcast_ws(event, data)
        except Exception:
            logger.warning("could not send %s", event, exc_info=True)

    def _push_sessions(self) -> None:
        push = getattr(self._state, "push_sessions_update", None)
        if callable(push):
            try:
                push()
            except Exception:
                logger.debug("sessions update failed", exc_info=True)

    def _window(self) -> float:
        try:
            return float(self._state.approval_window_secs())
        except Exception:
            from personalclaw.approval_grants import approval_window_secs

            return approval_window_secs()

    @staticmethod
    def _words(secs: float) -> str:
        from personalclaw.auto_denials import window_words

        return window_words(secs)

    @staticmethod
    def _why_cancelled() -> str:
        from personalclaw import restart_request, shutdown_event

        if shutdown_event.is_set():
            verb = (
                "restarted"
                if restart_request.stopping_for() == restart_request.RESTARTING
                else "stopped"
            )
            return f"the gateway {verb} before anyone answered"
        return "the work that asked it stopped"
