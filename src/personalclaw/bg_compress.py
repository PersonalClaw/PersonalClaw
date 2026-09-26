"""Background compression (Context Economy §4): shortens what the MODEL reads of an idle chat.

The always-on complement to on-demand projection. An old, idle, at-rest chat is
topic-segmented and attention-weighted on the maintenance cadence, so that when it is
picked up again the history the model is given is short — without a manual trigger.

**The chat file is never written.** The user's transcript stays exactly as they left it,
header included. What this produces is a derived record beside it
(``{key}.summary.json``, :func:`personalclaw.history.summary_record`) that context assembly
reads through :func:`personalclaw.history.model_view`. Deleting the record loses nothing:
the model then reads the transcript itself, and a later pass writes a new one. A record
stops being read the moment any message in the span it covers changes.

Design (all verified seams):
  * **Cadence** — rides the maintenance cadence (the heartbeat tick that already drives
    ``HistoryConsolidator.check_idle_sessions``); one budgeted pass per invocation
    (at most ``max_sessions`` summaries, most-idle first), never on the request path.
  * **Eligibility** — persistent chats idle > ``bg_compress_idle_days`` whose transcript
    exceeds a size floor and has no current record. Incognito/temporary chats are SKIPPED
    (the durable mark). A chat with nothing to summarize is remembered at its stamp for the
    life of the process, so it costs one read per change rather than one per pass, and it
    never uses up the budget meant for chats that have something to summarize.
  * **Attention-weighted per-topic compression** — segment via ``context_segmentation``
    (embedding drift, deterministic fallback); the model then reads the most-recent segment
    verbatim, the middle segments as their request/response pairs capped to
    ``_MIDDLE_MSG_CAP`` characters (tool rows are not read), and the oldest tier as one
    summary from the §2.4 ``compress_prose`` background model, which names every
    ``tool_result_get`` handle the span held (OP4).
  * **Prefix stability** — a record only changes what a FRESH runner is given, and only for
    chats at rest, so it never breaks a live session's KV-cache prefix (§3 invariant 3).

Nothing here is security-eventful (no SEL); actions log to the normal logger and the
savings ledger under compressor ``"bg_topic"``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from personalclaw.context_segmentation import segment_messages
from personalclaw.history import (
    TURN_ROLES,
    ConversationLog,
    model_view,
    summary_holds,
    summary_record,
)

logger = logging.getLogger(__name__)

# Don't bother below this transcript size — compression has to earn its LLM call.
_MIN_TRANSCRIPT_CHARS = 8_000
# Keep the most-recent N segments verbatim (the "current ~65%" attention tier).
_KEEP_RECENT_SEGMENTS = 1
# Per-message content cap for the middle request/response tier.
_MIDDLE_MSG_CAP = 600
# Preserve a projected result's retrieval handle through summarization (OP4).
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')

#: Chats a pass read and found nothing to summarize in: transcript path → the
#: ``(mtime_ns, size)`` it read. In memory on purpose — "nothing to do" is not worth a file
#: beside every small chat, and after a restart each one is simply read once more.
_SETTLED: dict[str, tuple[int, int]] = {}


def _idle_seconds(idle_days: float) -> float:
    return max(0.0, idle_days) * 86400.0


def _transcript_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def _collect_raw_refs(messages: list[dict]) -> list[str]:
    """Every ``tool_result_get`` handle mentioned in a span — so a summary can name
    them and the raw output stays reachable to the model (OP4)."""
    seen: list[str] = []
    for m in messages:
        for rid in _RESULT_ID_RE.findall(str(m.get("content", ""))):
            if rid not in seen:
                seen.append(rid)
    return seen


def _plan(messages: list[dict], embed_fn) -> tuple[int, int, int] | None:
    """Where the summarized span ends and the capped middle ends, counted in TURNS.

    ``(summarized, reduced, segments)``: the model reads the first ``summarized`` turns as
    one summary and turns ``summarized..reduced`` capped (see ``history.summary_record``).
    None when the conversation is below the size floor or has too few topics to leave an
    oldest tier worth folding.
    """
    turns = [m for m in messages if m.get("role") in TURN_ROLES]
    if _transcript_chars(turns) < _MIN_TRANSCRIPT_CHARS:
        return None
    segments = segment_messages(turns, embed_fn=embed_fn)
    if len(segments) <= _KEEP_RECENT_SEGMENTS + 1:
        return None
    older = segments[:-_KEEP_RECENT_SEGMENTS]
    # Oldest half → prose summary; the newer middle → request/response reduction.
    split = max(1, len(older) // 2)
    return older[split - 1].end, older[-1].end, len(segments)


def _rows_of_first_turns(messages: list[dict], count: int) -> list[dict]:
    """The transcript rows up to the first *count* turns, tool rows between them included."""
    seen = 0
    for i, m in enumerate(messages):
        if m.get("role") in TURN_ROLES:
            if seen == count:
                return messages[:i]
            seen += 1
    return messages


async def _summarize_oldest(rows: list[dict]) -> str:
    """The oldest tier as ONE summary the model reads in its place ("" when it has no turns).

    Written from the tier's turns; every ``tool_result_get`` handle in the tier's rows —
    tool rows included, which the model is never handed as history — is named after it.
    """
    turns = [m for m in rows if m.get("role") in TURN_ROLES]
    body = "\n".join(f"{m['role']}: {str(m.get('content', '')).strip()}" for m in turns)
    if not body:
        return ""
    from personalclaw.tool_providers.prose_compress import compress_prose

    summary = await compress_prose(body, raw_ref="")
    raw_refs = _collect_raw_refs(rows)
    refs_line = ""
    if raw_refs:
        refs_line = "\nRecoverable raw outputs: " + ", ".join(
            f'tool_result_get(result_id="{r}")' for r in raw_refs[:20]
        )
    return f"The first {len(turns)} messages of this conversation, condensed:\n{summary}{refs_line}"


def _stamp_of(record: dict | None) -> tuple[int, ...]:
    stamp = record.get("stamp") if isinstance(record, dict) else None
    return tuple(stamp) if isinstance(stamp, list) else ()


def _settle(
    log: ConversationLog,
    key: str,
    stamp: tuple[int, int],
    messages: list[dict],
    record: dict | None,
) -> None:
    """Remember that *key* has nothing to summarize at *stamp*, and drop a record that no
    longer describes it (it would never be read again, and it quotes older text)."""
    _SETTLED[str(log._path(key))] = stamp
    if record is not None and not summary_holds(record, messages):
        log.delete_summary(key)


async def compress_session(
    log: ConversationLog,
    key: str,
    *,
    embed_fn=None,
) -> dict | None:
    """Summarize ONE at-rest chat for the model: write its record, never its transcript.

    Returns ``{key, chars_in, chars_out, segments}`` — how much the model reads of the chat
    without and with the record — when it wrote one. None when the chat already has a
    current record, has nothing worth summarizing, or failed. Never raises: a failure on
    one chat must not abort the maintenance pass.
    """
    try:
        stamp = log.transcript_stamp(key)
        if stamp is None or _SETTLED.get(str(log._path(key))) == stamp:
            return None
        record = log.read_summary(key)
        if _stamp_of(record) == stamp:
            return None
        # The file's byte size bounds its characters, so a small file needs no read at all.
        big_enough = stamp[1] >= _MIN_TRANSCRIPT_CHARS
        messages = log.read_messages(key) if big_enough or record is not None else []
        plan = _plan(messages, embed_fn) if big_enough else None
        if plan is None:
            _settle(log, key, stamp, messages, record)
            return None
        summarized, reduced, segments = plan

        summary = await _summarize_oldest(_rows_of_first_turns(messages, summarized))
        fresh = summary_record(
            messages,
            stamp,
            summary=summary,
            summarized=summarized,
            reduced=reduced,
            reduced_cap=_MIDDLE_MSG_CAP,
        )
        chars_in = _transcript_chars(model_view(messages, None))
        chars_out = _transcript_chars(model_view(messages, fresh))
        # Only keep a record that actually shortens what the model reads.
        if not summary or chars_out >= chars_in:
            _settle(log, key, stamp, messages, record)
            return None

        log.write_summary(key, fresh)
        _record_savings(chars_in, chars_out)
        logger.info(
            "bg-compress: chat %s — the model reads %d→%d chars (%d segments), chat untouched",
            key,
            chars_in,
            chars_out,
            segments,
        )
        return {"key": key, "chars_in": chars_in, "chars_out": chars_out, "segments": segments}
    except Exception:
        logger.warning("bg-compress: failed for session %s", key, exc_info=True)
        return None


def _eligible_keys(log: ConversationLog, idle_days: float, now: float) -> list[str]:
    """Persistent, idle sessions oldest-first (most-idle first)."""
    cutoff = now - _idle_seconds(idle_days)
    rows = []
    for s in log.list_sessions():
        key = s.get("key")
        if not key:
            continue
        if s.get("memory_mode", "persistent") != "persistent":
            continue  # incognito/temporary never touched
        modified = float(s.get("modified", 0.0) or 0.0)
        if modified <= 0 or modified > cutoff:
            continue  # active / not idle enough
        rows.append((modified, key))
    rows.sort(key=lambda r: r[0])  # oldest (most idle) first
    return [key for _mtime, key in rows]


async def run_bg_compression_pass(
    log: ConversationLog | None,
    *,
    embed_fn=None,
    max_sessions: int = 3,
) -> list[dict]:
    """Run ONE budgeted background-compression pass. Reads config each call so the
    Settings toggle takes effect live. Returns the per-chat stats of the records it wrote.

    The budget is the number of summaries written. A chat with nothing to summarize costs
    none of it, so a few small, very old chats can no longer stall every pass behind them.

    Best-effort throughout: config-off, no-log, or a per-session error each degrade to
    "did nothing", never to an exception into the maintenance tick.
    """
    if log is None:
        return []
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().tools
        if not cfg.bg_compress_enabled:
            return []
        idle_days = float(cfg.bg_compress_idle_days)
    except Exception:
        logger.debug("bg-compress: config load failed — skipping pass", exc_info=True)
        return []

    # A summary never outlives its chat, whatever removed the transcript.
    log.prune_orphan_summaries()
    stats: list[dict] = []
    budget = max(1, max_sessions)
    for key in _eligible_keys(log, idle_days, time.time()):
        if len(stats) >= budget:
            break
        result = await compress_session(log, key, embed_fn=embed_fn)
        if result is not None:
            stats.append(result)
    return stats


def _record_savings(chars_in: int, chars_out: int) -> None:
    """Savings under the ``bg_topic`` compressor key (§1.3). Never raises."""
    try:
        from personalclaw.tool_providers import savings

        savings.record_saving(
            month=datetime.now().strftime("%Y-%m"),
            model="unknown",
            compressor="bg_topic",
            chars_in=chars_in,
            chars_out=chars_out,
        )
    except Exception:
        logger.debug("bg-compress savings accounting failed", exc_info=True)
