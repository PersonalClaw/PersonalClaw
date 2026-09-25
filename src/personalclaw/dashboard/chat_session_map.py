"""SESSION MAP — the DURABLE half: ``GET /api/chat/sessions/{session}/map`` plus the
per-turn telemetry that survives a reload (SEMANTIC-SESSION-MAP §B.3, atom ``SSM-2``).

**What this module owns.** One derivation and one persisted shape:

* :func:`session_map_marks` — the transcript → ``mark[]`` derivation the endpoint
  serves. It is the SERVER-SIDE mirror of ``web/src/pages/chat/sessionMap.ts``
  (``sessionMapMarks``, atom ``SSM-1``): same closed ``kind`` vocabulary, same field
  names, same jump coordinate. The endpoint answers a JSON array that is directly
  assignable to SSM-1's ``SessionMark[]``, because the whole point of a second producer
  is a second SOURCE for one shape — never a second shape.
* :data:`TURN_TELEMETRY_KEY` / :func:`build_turn_telemetry` /
  :func:`stamp_turn_telemetry` — the structured per-turn cost/token/duration/context
  record. Today that telemetry exists only as a live ``activity_event {kind:"stats"}``
  **sentence** (``chat_runner``'s "Turn complete" line), so a reload lost it entirely.
  It now rides the assistant message's ``meta`` — the same seam ``memory_citations``
  and ``skills_used`` already use, which ``chat_persistence`` writes and both restore
  paths read back — so no new file, no new channel, and no new persisted shape beyond
  one additive key.
* :data:`TURN_SUMMARY_KEY` / :func:`build_turn_summary` / :func:`summarize_session_turn`
  / :func:`stamp_turn_summary` — the per-turn SUMMARY LABEL (atom ``SSM-3``). §B.3 asks
  for a label that makes a mark "convey *meaning* rather than the opening words"; an
  assistant mark's preview is otherwise the reply's first 140 characters, which on a
  rail is usually a preamble. The label rides the same ``meta`` seam as the telemetry,
  and :func:`session_map_marks` prefers it over :func:`preview_text` when present.

**Why the derivation lives in core at all.** ``visibleIndex`` is the backend's
``at_message_index`` (what ``POST .../fork`` and edit-resend speak). The frontend
recovers it by re-deriving the whole turn model from the transcript
(``hydrateTurns``); a durable endpoint hands the same coordinate back without that
round trip, which is what lets a map row carry a preview and telemetry before the
transcript is hydrated.

**What the persisted transcript can and cannot say.** The closed vocabulary has seven
kinds; a PERSISTED transcript can only witness five of them (:data:`PERSISTED_MARK_KINDS`).
``subagent`` rides a parallel WS stream that is never written to the conversation log,
and ``activity`` segments are live-only for the same reason. ``approval`` survives a
restart once it is decided: ``chat_persistence``'s ``_persistable`` writes a resolved
``permission`` row and holds back one still waiting for an answer, so a pending approval
is marked only while it is in the live buffer. This is stated rather than papered over:
a consumer that needs the live kinds reads SSM-1 over the hydrated turns; one that needs
durability reads this endpoint.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiohttp import web

from personalclaw.dashboard.chat_persistence import _rehydrate_session_from_history
from personalclaw.dashboard.chat_utils import _prepare_messages, full_session_messages
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

#: The CLOSED mark vocabulary, mirroring ``SESSION_MARK_KINDS`` in
#: ``web/src/pages/chat/sessionMap.ts``. Adding a kind is a contract change on BOTH
#: sides; the set is duplicated (not derived) because the two producers are in two
#: languages, and the mirror is asserted by ``tests/test_session_map_endpoint.py``.
SESSION_MARK_KINDS: tuple[str, ...] = (
    "user",
    "assistant",
    "tool",
    "approval",
    "error",
    "subagent",
    "activity",
)

#: The subset a PERSISTED transcript can witness. ``subagent``/``activity`` are
#: live-only (they never enter the conversation log), so this endpoint never invents
#: them — an empty tail is honest, a fabricated mark is not.
PERSISTED_MARK_KINDS: tuple[str, ...] = ("user", "assistant", "tool", "approval", "error")

#: The ``meta`` key the per-turn telemetry record is persisted under, on the LAST
#: assistant message of the turn. Absent = this turn reported no telemetry (an old
#: message, or a turn whose provider supplied nothing) — never "zero cost".
TURN_TELEMETRY_KEY = "turn_telemetry"

#: The ``meta`` key the per-turn summary LABEL is persisted under, on the turn's last
#: assistant message — the same seam and the same reason as :data:`TURN_TELEMETRY_KEY`
#: (one turn, one record, on the message the turn ends with). Absent = this turn had
#: nothing to say that its own opening words do not already say, and the mark falls back
#: to :func:`preview_text`. Absence is therefore a real answer, never an empty label.
TURN_SUMMARY_KEY = "summary"

#: A summary is a RAIL LABEL, deliberately shorter than :data:`PREVIEW_CAP`: it occupies
#: the same one-line slot the preview would, and a label that spends the whole preview
#: budget reads as a truncated sentence rather than a title.
SUMMARY_CAP = 120

#: How many distinct tool names the outcome clause spells out before it counts the rest.
_SUMMARY_TOOLS_NAMED = 3

#: Same cap as ``PREVIEW_CAP`` in ``sessionMap.ts`` — a map row, a hover card and an
#: accessible name are the same one-line budget on both producers.
PREVIEW_CAP = 140

# ── previewText, mirrored ────────────────────────────────────────────────────────
# Transform-for-transform port of `web/src/lib/previewText.ts`, in ORDER (the order is
# load-bearing: `**` before `*`). A preview needs the marks GONE, not the tree honored,
# so this is deliberately not a markdown parser — and deliberately NOT
# `voice_reply.strip_markdown`, which is a SPEECH stripper that replaces a code fence
# with the words "(code block)". Reusing that here would have made the two map
# producers print different previews for one turn.
_PREVIEW_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Fenced code: keep the code, drop the fence lines.
    (re.compile(r"^```[^\n]*$", re.MULTILINE), ""),
    # Line-leading structural marks: headings, blockquotes, list bullets.
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),
    (re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", re.MULTILINE), ""),
    # Links and images: keep the human text, drop the URL.
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    # Inline emphasis / code marks.
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"\*([^*\n]+)\*"), r"\1"),
    (re.compile(r"(^|\W)_([^_\n]+)_(?=\W|$)"), r"\1\2"),
    (re.compile(r"`([^`]+)`"), r"\1"),
)

_WHITESPACE_RUN = re.compile(r"\s+")


def preview_text(md: str | None, cap: int | None = None) -> str:
    """One-line plain-text preview of markdown-ish content.

    The Python peer of ``previewText`` (``web/src/lib/previewText.ts``). Same
    substitutions, same order, same one-line collapse, same ``…`` truncation at
    ``cap - 1``, so a mark's preview does not depend on which producer built it.
    """
    s = md or ""
    for pattern, repl in _PREVIEW_SUBS:
        s = pattern.sub(repl, s)
    s = _WHITESPACE_RUN.sub(" ", s).strip()
    if cap and len(s) > cap:
        return f"{s[: cap - 1]}…"
    return s


def _tool_preview(tool: str, detail: str = "", tool_input: str = "") -> str:
    """``sessionMap.ts``'s ``toolPreview``: the stable tool name plus its one-liner."""
    parts = [p for p in (tool, detail or tool_input) if p]
    return preview_text(" — ".join(parts), PREVIEW_CAP)


def _tool_name(meta: dict[str, Any], content: str) -> str:
    """``chatTypes.ts``'s ``toolName``: ``meta.tool`` (or the content), emoji-stripped."""
    raw = str(meta.get("tool") or content or "tool")
    # The TS strips a leading emoji-presentation run; Python's `re` has no
    # `\p{Extended_Pictographic}`, so the equivalent codepoint ranges are spelled out.
    raw = re.sub(r"^[\U0001f000-\U0001faff☀-➿️‍]+\s*", "", raw)
    return raw.strip() or "tool"


# ── turn hydration (the server-side mirror of chatTypes.hydrateTurns) ────────────


class _Turn:
    """One hydrated turn: its jump coordinate, its text, and its sub-event marks."""

    __slots__ = ("role", "visible_index", "ts", "texts", "subs")

    def __init__(self, role: str) -> None:
        self.role = role
        #: ``None`` until a user/assistant message stamps it — mirrors the TS, where an
        #: assistant turn conjured by a leading tool row has no ``visibleIndex`` and
        #: ``sessionMapMarks`` falls back to the turn's array position.
        self.visible_index: int | None = None
        self.ts: str = ""
        self.texts: list[str] = []
        self.subs: list[dict[str, Any]] = []


def _hydrate_turns(messages: list[dict[str, Any]]) -> list[_Turn]:
    """Fold prepared transcript messages into turns exactly as ``hydrateTurns`` does.

    The two collapses that make a turn's array position NOT the backend coordinate are
    reproduced here, because ``visibleIndex`` is only correct if both are:

    * a native-loop re-injection (a user message identical to the last one with no
      assistant text since) consumes a visible slot but produces no turn;
    * consecutive assistant messages merge into ONE turn whose coordinate is the LAST
      message folded in (``at_message_index`` is inclusive, so branching at an
      assistant turn must carry the whole answer).
    """
    turns: list[_Turn] = []
    seen_tool_ids: set[str] = set()
    visible = -1
    last_user_text = ""
    assistant_text_since_user = False

    def last_assistant() -> _Turn:
        if turns and turns[-1].role == "assistant":
            return turns[-1]
        turn = _Turn("assistant")
        turns.append(turn)
        return turn

    for m in messages:
        role = m.get("role", "")
        raw_meta = m.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        content = str(m.get("content", "") or "")
        if role == "user":
            visible += 1
            text = content.strip()
            if text == last_user_text and not assistant_text_since_user:
                continue  # loop re-injection: the slot is consumed, the turn is not
            turn = _Turn("user")
            # The DISPLAYED text, on the same precedence the bubble uses: a genui
            # widget action's human label, else the pre-optimize original, else the
            # content the model saw. A preview must read like the transcript.
            turn.texts.append(str(meta.get("ui_label") or meta.get("original") or content))
            turn.visible_index = visible
            turn.ts = str(m.get("ts", "") or "")
            turns.append(turn)
            last_user_text = text
            assistant_text_since_user = False
        elif role == "assistant":
            visible += 1
            turn = last_assistant()
            turn.visible_index = visible
            # Unlike the frontend — where `assistantTurn()` carries no timestamp at all,
            # so every assistant mark reads `ts: ''` — the durable side HAS the message's
            # persisted ts. §A.3 wants a real timestamp in the hover card, so it is
            # served here rather than dropped for symmetry with a frontend gap.
            turn.ts = str(m.get("ts", "") or "") or turn.ts
            turn.texts.append(content)
            assistant_text_since_user = True
        elif role == "tool":
            turn = last_assistant()
            tool_id = str(meta.get("tool_call_id") or "")
            if tool_id and tool_id in seen_tool_ids:
                # A result/completion update for a call already marked — the TS merges it
                # into the existing segment, so it must NOT mint a second mark.
                continue
            if tool_id:
                seen_tool_ids.add(tool_id)
            ok = meta.get("ok")
            sub: dict[str, Any] = {
                "kind": "tool",
                "preview": _tool_preview(
                    _tool_name(meta, content),
                    str(meta.get("detail") or ""),
                    str(meta.get("input") or ""),
                ),
            }
            # `ok` is carried ONLY when the call failed (§A.2 paints a failed tool mark
            # `--color-danger`), matching SSM-1's optional `ok?: false`.
            if ok is False:
                sub["ok"] = False
            turn.subs.append(sub)
        elif role == "permission":
            turn = last_assistant()
            turn.subs.append(
                {
                    "kind": "approval",
                    "preview": _tool_preview(
                        _tool_name(meta, content),
                        "",
                        str(meta.get("input") or meta.get("tool_input") or ""),
                    ),
                }
            )
        elif role == "error":
            turn = last_assistant()
            turn.subs.append({"kind": "error", "preview": preview_text(content, PREVIEW_CAP)})
        # every other role (system, streaming) contributes no mark — same as the TS.
    return turns


def session_map_marks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive the ordered session-map marks from prepared transcript *messages*.

    *messages* is the output of :func:`~personalclaw.dashboard.chat_utils._prepare_messages`
    — i.e. exactly what ``GET /api/chat/sessions/{session}`` serves — so a mark's
    ``visibleIndex`` indexes the SAME transcript the frontend hydrates. Feeding this a
    different list is how ``visibleIndex`` would come to mean two things.

    Emits one mark per turn (``kind`` = its role) followed by its sub-event marks in
    segment order, each inheriting the owning turn's role, coordinate and timestamp —
    the ordering ``sessionMapMarks`` defines. An assistant turn carrying persisted
    telemetry also carries it on its TURN mark (never on a sub-event mark: the record
    describes the whole turn).
    """
    marks: list[dict[str, Any]] = []
    turns = _hydrate_turns(messages)
    telemetry_by_visible = _meta_by_visible_index(messages, TURN_TELEMETRY_KEY)
    summary_by_visible = _meta_by_visible_index(messages, TURN_SUMMARY_KEY)
    for position, turn in enumerate(turns):
        visible_index = turn.visible_index if turn.visible_index is not None else position
        body = "\n".join(turn.texts).strip()
        # The persisted summary label WINS over the raw opening words (SSM-3). That is the
        # whole point of persisting one: `preview_text(body)` is the first 140 characters
        # of the turn, and a label says what the turn was about and what it did. Absent =
        # this turn had no outcome worth a label, so the opening words are the honest
        # answer and the fallback is the normal case, not a degraded one.
        summary = summary_by_visible.get(visible_index)
        mark: dict[str, Any] = {
            "markIndex": len(marks),
            "kind": turn.role,
            "role": turn.role,
            "visibleIndex": visible_index,
            "ts": turn.ts,
            "preview": str(summary) if summary else preview_text(body, PREVIEW_CAP),
        }
        telemetry = telemetry_by_visible.get(visible_index)
        if telemetry is not None:
            mark["telemetry"] = telemetry
        marks.append(mark)
        for sub in turn.subs:
            marks.append(
                {
                    "markIndex": len(marks),
                    **sub,
                    "role": turn.role,
                    "visibleIndex": visible_index,
                    "ts": turn.ts,
                }
            )
    return marks


def _meta_by_visible_index(messages: list[dict[str, Any]], key: str) -> dict[int, Any]:
    """Map each visible-list index to the truthy ``meta[key]`` on that message.

    Walks the same visible cursor the derivation does (every user/assistant message
    consumes one slot), so a record lands on the mark for the message that carries it.
    One walker for both per-turn keys — telemetry and the summary label ride the same
    seam, and two copies of this cursor could come to disagree about which slot a
    message owns.
    """
    out: dict[int, Any] = {}
    visible = -1
    for m in messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        visible += 1
        meta = m.get("meta")
        if not isinstance(meta, dict):
            continue
        value = meta.get(key)
        if value:
            out[visible] = value
    return out


# ── per-turn telemetry: build + stamp ────────────────────────────────────────────


def build_turn_telemetry(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cost_usd: float,
    priced: bool,
    duration_ms: int,
    context_pct: float | None,
    events: int,
    tool_calls: int,
    model: str,
) -> dict[str, Any] | None:
    """The structured per-turn telemetry record, or ``None`` when the turn reported none.

    The shape is fixed and every key is always present, so a consumer never has to
    guess whether a missing key means zero:

    * ``priced`` is the honesty flag the live "Turn complete" line already carries —
      ``cost_usd: 0.0`` with ``priced: false`` means "no price row for this model",
      never "this turn was free".
    * ``context_pct`` is ``None`` when the provider measured nothing. Folding that into
      ``0.0`` is the exact defect ``G8``/``test_context_pct_honesty.py`` was written to
      stop, and persisting it would make the lie durable.

    ``None`` (no record at all) when the turn reported no events, no tool calls and no
    tokens — the same gate the live stats line uses, so a turn that says nothing live
    does not persist a row of zeros.
    """
    if not (events or tool_calls or input_tokens or output_tokens):
        return None
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cache_creation_tokens": int(cache_creation_tokens),
        "cost_usd": float(cost_usd),
        "priced": bool(priced),
        "duration_ms": int(duration_ms),
        "context_pct": None if context_pct is None else round(float(context_pct), 1),
        "events": int(events),
        "tool_calls": int(tool_calls),
        "model": str(model or ""),
    }


def _stamp_on_last_assistant(session: Any, key: str, value: Any) -> bool:
    """Stamp ``meta[key] = value`` on *session*'s LAST assistant message.

    Returns whether a message was stamped. The LAST assistant message, because a
    tool-using turn flushes several assistant segments and a per-turn record describes
    the whole turn; nothing at all when the turn produced no assistant message (a
    cancelled or tool-only turn has nowhere honest to put one).

    Must be called BEFORE the turn's ``save_session_to_history`` — that function
    rewrites the whole transcript file from the buffer, so a key stamped after it is
    in-memory only and dies at the next reload, which is the very failure the durable
    half of this module exists to fix.
    """
    if not value:
        return False
    for msg in reversed(getattr(session, "messages", []) or []):
        if msg.get("role") != "assistant":
            continue
        meta = msg.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta[key] = value
        msg["meta"] = meta
        return True
    return False


def stamp_turn_telemetry(session: Any, telemetry: dict[str, Any] | None) -> bool:
    """Stamp *telemetry* onto the session's last assistant message ``meta`` (``SSM-2``)."""
    return _stamp_on_last_assistant(session, TURN_TELEMETRY_KEY, telemetry)


def stamp_turn_summary(session: Any, summary: str | None) -> bool:
    """Stamp the turn *summary* label onto the session's last assistant message (``SSM-3``).

    The same message the telemetry rides, for the same reason, under the same
    before-the-save constraint.
    """
    return _stamp_on_last_assistant(session, TURN_SUMMARY_KEY, summary)


# ── per-turn summary label: the deterministic whole-turn derivation (SSM-3) ───────
#
# This is an EXTRACTIVE label, not a generated one: it reads the turn that just
# finished and states what the turn was about and what it did. No model is called.
# That is a deliberate scope choice, recorded in the plan's execution log — a summary
# produced by a model call would add a per-turn cost and a provider dependency to every
# completed turn (a user-facing behaviour change), and it could not be observed at all
# on a workspace with no provider bound. The information that makes this label worth
# persisting is the part no raw transcript line contains: which tools the turn ran and
# how it ended.

#: Openers that spend a label's budget without narrowing what the turn is ABOUT.
#: Matched only as a LEADING run and only on a word boundary, so "Hi" cannot eat
#: "Highlight the diff" and "so" is deliberately absent (it opens real clauses).
_FILLER_OPENER = re.compile(
    r"^(?:(?:hey|hi|hello|yo|thanks|thank you|please|quick question|sorry|alright|"
    r"good (?:morning|afternoon|evening)|morning)\b[\s,!.:;—-]*)+",
    re.IGNORECASE,
)

#: A sentence break, used to keep the subject to the ASK rather than the paragraph
#: that follows it.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

#: A markdown ATX heading — the reply's own summary of itself, and the best label a
#: tool-less turn can offer. Read from the RAW reply, because ``_PREVIEW_SUBS`` strips
#: the very marker this needs to find.
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(\S.*?)\s*#*\s*$", re.MULTILINE)


def _summary_subject(request: str) -> str:
    """The request distilled to the clause that says what the turn is ABOUT.

    Reads the WHOLE request, not its first line: a message that opens "Hey!" on its own
    line has its subject on the next one, and the mark's existing preview already shows
    the opening words. Politeness is dropped, then the first sentence is kept.
    """
    flat = preview_text(request)
    if not flat:
        return ""
    core = _FILLER_OPENER.sub("", flat).strip() or flat
    return _SENTENCE_BREAK.split(core, maxsplit=1)[0].strip() or core


def _first_heading(reply: str) -> str:
    """The reply's first ATX heading, if it wrote one."""
    match = _ATX_HEADING.search(reply or "")
    return preview_text(match.group(1)) if match else ""


def _summary_outcome(*, tool_names: list[str], failed_tools: int, errored: bool, reply: str) -> str:
    """What the turn DID — the half of the label no raw transcript line can state."""
    clauses: list[str] = []
    if tool_names:
        # Order-preserving distinct: a turn that reads six files ran ONE tool.
        distinct = list(dict.fromkeys(n for n in tool_names if n))
        named = distinct[:_SUMMARY_TOOLS_NAMED]
        clause = "ran " + ", ".join(named)
        remainder = len(distinct) - len(named)
        if remainder:
            clause += f" +{remainder} more"
        if failed_tools:
            clause += f" ({failed_tools} failed)"
        clauses.append(clause)
    else:
        heading = _first_heading(reply)
        if heading:
            clauses.append(heading)
    if errored:
        clauses.append("ended in an error")
    return " — ".join(clauses)


def build_turn_summary(
    *,
    request: str,
    reply: str,
    tool_names: list[str],
    failed_tools: int = 0,
    errored: bool = False,
) -> str | None:
    """The one-line summary label for a completed turn, or ``None`` when it has none.

    ``None`` — not an empty string, and not a restatement — whenever the turn produced
    no OUTCOME to report (no tool ran, the reply wrote no heading, nothing errored).
    Without an outcome clause the label could only echo words already on the rail, and a
    mark is better served by falling back to :func:`preview_text` than by carrying a
    second copy of the same sentence. This is the same honesty gate
    :func:`build_turn_telemetry` applies when it refuses to persist a row of zeros.

    The label is redacted HERE, at the write. ``_prepare_messages`` redacts a message's
    ``content`` on the way out but never its ``meta``, so a label composed from raw
    request/reply text and persisted unredacted would be served verbatim — bypassing the
    scrub that the preview it replaces gets for free. Redacting the composed line once,
    rather than its fragments, also keeps clear of ``redact_credentials``' known
    non-idempotency over an already-redacted composition.
    """
    outcome = _summary_outcome(
        tool_names=tool_names, failed_tools=failed_tools, errored=errored, reply=reply
    )
    if not outcome:
        return None
    subject = _summary_subject(request)
    label = " — ".join(part for part in (subject, outcome) if part)
    label, _ = redact_exfiltration_urls(label)
    label, _ = redact_credentials(label)
    return preview_text(label, SUMMARY_CAP) or None


def summarize_session_turn(session: Any) -> str | None:
    """Build the summary label for the turn at the END of *session*'s message buffer.

    The buffer is the source rather than a set of turn-scoped counters, because by the
    time a turn is stamped the buffer already holds the whole turn — the user row, every
    tool row and every flushed assistant segment. Reading it keeps this derivation and
    :func:`_hydrate_turns` agreeing about what one turn IS, including the ``ui_label`` /
    ``original`` / ``content`` precedence for what the user actually saw and the
    ``tool_call_id`` de-duplication that stops a result update counting as a second call.
    """
    messages = list(getattr(session, "messages", []) or [])
    start: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            start = index
            break
    if start is None:
        return None  # no user row = no turn boundary to summarize

    request = ""
    replies: list[str] = []
    tool_names: list[str] = []
    failed_tools = 0
    errored = False
    seen_tool_ids: set[str] = set()
    for msg in messages[start:]:
        role = msg.get("role", "")
        raw_meta = msg.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        content = str(msg.get("content", "") or "")
        if role == "user":
            if not request:
                request = str(meta.get("ui_label") or meta.get("original") or content)
        elif role == "assistant":
            replies.append(content)
        elif role == "tool":
            tool_id = str(meta.get("tool_call_id") or "")
            if tool_id and tool_id in seen_tool_ids:
                continue
            if tool_id:
                seen_tool_ids.add(tool_id)
            tool_names.append(_tool_name(meta, content))
            if meta.get("ok") is False:
                failed_tools += 1
        elif role == "error":
            errored = True
    return build_turn_summary(
        request=request,
        reply="\n".join(replies),
        tool_names=tool_names,
        failed_tools=failed_tools,
        errored=errored,
    )


# ── the endpoint ─────────────────────────────────────────────────────────────────


async def api_chat_session_map(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/map — the durable session-map marks.

    Answers a JSON ARRAY of marks (``SessionMark[]`` on the frontend side), ordered
    turn-by-turn: each turn's mark, then its sub-event marks. A resident session is
    served from its live buffer; a session that is only on disk is rehydrated first, so
    the map is available for a chat the gateway has not reopened — which is the whole
    reason a durable endpoint exists.

    Indexes the SAME message list ``GET /api/chat/sessions/{session}`` serves (via
    :func:`full_session_messages` + ``_prepare_messages``), because ``visibleIndex`` is
    ``at_message_index``: derived from any other list, the coordinate would silently
    address a different message than ``POST .../fork`` does.
    """
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name) or _rehydrate_session_from_history(state, name)
    if not session:
        return json_error("session_not_found", status=404)
    prepared = _prepare_messages(full_session_messages(state, session), session.running)
    return web.json_response(session_map_marks(prepared))
