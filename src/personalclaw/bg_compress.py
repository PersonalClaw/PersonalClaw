"""Continuous background compression service (Context Economy §4).

The always-on complement to on-demand projection: old, idle, at-rest session
history is topic-segmented and attention-weighted compressed on the maintenance
cadence so long sessions and high-volume channels stay fast — without a manual
compaction trigger.

Design (all verified seams):
  * **Cadence** — rides the existing maintenance cadence (the heartbeat tick that
    already drives ``HistoryConsolidator.check_idle_sessions``); one budgeted pass
    per invocation (``max_sessions`` oldest-first), never on the request path.
  * **Eligibility** — persistent sessions idle > ``bg_compress_idle_days`` whose
    transcript exceeds a size floor and hasn't already been bg-compressed at its
    current length. Incognito/temporary sessions are SKIPPED (the durable mark).
  * **Attention-weighted per-topic compression** — segment via
    ``context_segmentation`` (embedding drift, deterministic fallback), then: the
    most-recent segment is kept verbatim; middle segments reduce to their
    request/response pairs (tool rows dropped — they carry raw_refs); the oldest
    tier is bulk-summarized via the §2.4 ``compress_prose`` background model.
  * **Reversibility** — the rewrite archives every dropped line under
    ``reason="bg_compress"`` (recoverable), and the summary line names nothing the
    raw_ref preservation (OP4) doesn't already keep reachable.
  * **Prefix stability** — only touches sessions AT REST (no live in-flight turn),
    so it never breaks an active session's KV-cache prefix (§3 invariant 3).

Nothing here is security-eventful (no SEL); actions log to the normal logger and
the savings ledger under compressor ``"bg_topic"``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from personalclaw.context_segmentation import Segment, segment_messages
from personalclaw.history import ConversationLog

logger = logging.getLogger(__name__)

# Don't bother below this transcript size — compression has to earn its LLM call.
_MIN_TRANSCRIPT_CHARS = 8_000
# Keep the most-recent N segments verbatim (the "current ~65%" attention tier).
_KEEP_RECENT_SEGMENTS = 1
# Per-message content cap for the middle request/response tier (tool noise dropped).
_MIDDLE_MSG_CAP = 600
# Preserve a projected result's retrieval handle through summarization (OP4).
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')


def _idle_seconds(idle_days: float) -> float:
    return max(0.0, idle_days) * 86400.0


def _transcript_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def _collect_raw_refs(messages: list[dict]) -> list[str]:
    """Every ``tool_result_get`` handle mentioned in a span — so a summary can name
    them and the raw stays reachable after the span's lines are dropped (OP4)."""
    seen: list[str] = []
    for m in messages:
        for rid in _RESULT_ID_RE.findall(str(m.get("content", ""))):
            if rid not in seen:
                seen.append(rid)
    return seen


def _reduce_middle(seg: Segment) -> list[dict]:
    """Middle tier: keep user + assistant turns (capped), drop tool rows. Their raw
    stays reachable via any raw_ref already inline in the assistant text."""
    out: list[dict] = []
    for m in seg.messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = str(m.get("content", ""))
        if len(content) > _MIDDLE_MSG_CAP:
            content = content[:_MIDDLE_MSG_CAP] + " …"
        out.append({**m, "content": content})
    return out


async def _summarize_oldest(segments: list[Segment]) -> dict | None:
    """Bulk-summarize the oldest tier into ONE reference message. Returns the summary
    message dict, or None when there's nothing to summarize."""
    msgs: list[dict] = []
    for seg in segments:
        msgs.extend(seg.messages)
    if not msgs:
        return None
    body_parts: list[str] = []
    for m in msgs:
        role = m.get("role", "")
        if role in ("user", "assistant"):
            body_parts.append(f"{role}: {str(m.get('content', '')).strip()}")
    body = "\n".join(body_parts)
    raw_refs = _collect_raw_refs(msgs)

    from personalclaw.tool_providers.prose_compress import compress_prose

    summary = await compress_prose(body, raw_ref="")
    refs_line = ""
    if raw_refs:
        refs_line = "\nRecoverable raw outputs: " + ", ".join(
            f'tool_result_get(result_id="{r}")' for r in raw_refs[:20]
        )
    content = (
        "[CONTEXT ECONOMY — background-compressed earlier conversation, reference only. "
        "Older turns were archived (recoverable) and summarized below.]\n"
        f"{summary}{refs_line}"
    )
    return {"role": "system", "content": content, "cls": "bg_compress_summary"}


async def compress_session(
    log: ConversationLog,
    key: str,
    *,
    embed_fn=None,
) -> dict | None:
    """Topic-compress ONE at-rest session in place (archive → rewrite).

    Returns a stats dict ``{key, chars_in, chars_out, segments}`` when it compressed,
    or None when the session was ineligible or unchanged. Never raises — a failure on
    one session must not abort the maintenance pass.
    """
    try:
        messages = log._read_messages(key)  # metadata line excluded
        if not messages:
            return None
        chars_in = _transcript_chars(messages)
        if chars_in < _MIN_TRANSCRIPT_CHARS:
            return None

        segments = segment_messages(messages, embed_fn=embed_fn)
        # Need enough segments to have an "oldest tier" worth folding.
        if len(segments) <= _KEEP_RECENT_SEGMENTS + 1:
            return None

        recent = segments[-_KEEP_RECENT_SEGMENTS:]
        middle_and_old = segments[:-_KEEP_RECENT_SEGMENTS]
        # Oldest half → prose summary; the newer middle → request/response reduction.
        split = max(1, len(middle_and_old) // 2)
        oldest = middle_and_old[:split]
        middle = middle_and_old[split:]

        rebuilt: list[dict] = []
        summary_msg = await _summarize_oldest(oldest)
        if summary_msg is not None:
            rebuilt.append(summary_msg)
        for seg in middle:
            rebuilt.extend(_reduce_middle(seg))
        for seg in recent:
            rebuilt.extend(seg.messages)

        chars_out = _transcript_chars(rebuilt)
        # Only rewrite if we actually shrank it (and left something behind).
        if not rebuilt or chars_out >= chars_in:
            return None

        log.rewrite_session(key, rebuilt, reason="bg_compress")
        _record_savings(chars_in, chars_out)
        logger.info(
            "bg-compress: session %s %d→%d chars (%d segments)",
            key,
            chars_in,
            chars_out,
            len(segments),
        )
        return {"key": key, "chars_in": chars_in, "chars_out": chars_out, "segments": len(segments)}
    except Exception:
        logger.warning("bg-compress: failed for session %s", key, exc_info=True)
        return None


def _eligible_keys(log: ConversationLog, idle_days: float, now: float) -> list[str]:
    """Persistent, idle, non-trivial sessions oldest-first (most-idle first)."""
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
    Settings toggle takes effect live. Returns the per-session stats it produced.

    Best-effort throughout: config-off, no-log, or a per-session error each degrade
    to "did nothing", never to an exception into the maintenance tick.
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

    now = time.time()
    keys = _eligible_keys(log, idle_days, now)
    if not keys:
        return []
    stats: list[dict] = []
    for key in keys[: max(1, max_sessions)]:
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
