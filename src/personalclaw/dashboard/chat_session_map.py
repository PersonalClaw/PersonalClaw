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

**Why the derivation lives in core at all.** ``visibleIndex`` is the backend's
``at_message_index`` (what ``POST .../fork`` and edit-resend speak). The frontend
recovers it by re-deriving the whole turn model from the transcript
(``hydrateTurns``); a durable endpoint hands the same coordinate back without that
round trip, which is what lets a map row carry a preview and telemetry before the
transcript is hydrated.

**What the persisted transcript can and cannot say.** The closed vocabulary has seven
kinds; a PERSISTED transcript can only witness five of them (:data:`PERSISTED_MARK_KINDS`).
``subagent`` rides a parallel WS stream that is never written to the conversation log,
and ``activity`` segments are live-only for the same reason. ``approval`` is emitted
when the permission row is still in the live buffer — ``chat_persistence``'s
``_NON_TRANSCRIPT_ROLES`` drops ``permission`` on save, so it does not survive a
restart. This is stated rather than papered over: a consumer that needs the live kinds
reads SSM-1 over the hydrated turns; one that needs durability reads this endpoint.
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
    telemetry_by_visible = _telemetry_by_visible_index(messages)
    for position, turn in enumerate(turns):
        visible_index = turn.visible_index if turn.visible_index is not None else position
        body = "\n".join(turn.texts).strip()
        mark: dict[str, Any] = {
            "markIndex": len(marks),
            "kind": turn.role,
            "role": turn.role,
            "visibleIndex": visible_index,
            "ts": turn.ts,
            "preview": preview_text(body, PREVIEW_CAP),
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


def _telemetry_by_visible_index(messages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Map each visible-list index to the telemetry persisted on that message's ``meta``.

    Walks the same visible cursor the derivation does (every user/assistant message
    consumes one slot), so the record lands on the mark for the message that carries it.
    """
    out: dict[int, dict[str, Any]] = {}
    visible = -1
    for m in messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        visible += 1
        meta = m.get("meta")
        if not isinstance(meta, dict):
            continue
        record = meta.get(TURN_TELEMETRY_KEY)
        if isinstance(record, dict) and record:
            out[visible] = record
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


def stamp_turn_telemetry(session: Any, telemetry: dict[str, Any] | None) -> bool:
    """Stamp *telemetry* onto the session's LAST assistant message ``meta``.

    Returns whether a message was stamped. The last assistant message, because a
    tool-using turn flushes several assistant segments and the record describes the
    whole turn; nothing at all when the turn produced no assistant message (a
    cancelled or tool-only turn has nowhere honest to put it).

    Must be called BEFORE the turn's ``save_session_to_history`` — that function
    rewrites the whole transcript file from the buffer, so a key stamped after it is
    in-memory only and dies at the next reload, which is the very failure this atom
    exists to fix.
    """
    if not telemetry:
        return False
    for msg in reversed(getattr(session, "messages", []) or []):
        if msg.get("role") != "assistant":
            continue
        meta = msg.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta[TURN_TELEMETRY_KEY] = telemetry
        msg["meta"] = meta
        return True
    return False


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
