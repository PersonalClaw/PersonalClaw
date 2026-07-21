"""Persistent conversation history — JSONL per session + LLM consolidation.

Session files: ~/.personalclaw/sessions/{safe_key}.jsonl
Each entry tracks provenance (source_thread, source_user) for citation.
Files auto-rotate at 512KB, keeping last 200 lines.
"""

import asyncio
import json
import logging
import math
import re
import time as _time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw.atomic_write import atomic_write
from personalclaw.concurrency import single_flight
from personalclaw.config.loader import config_dir
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.session import BACKGROUND_KEY
from personalclaw.skills import AutoSkillProvenance

if TYPE_CHECKING:
    from personalclaw.learn import LessonStore
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import MemoryService
    from personalclaw.session import SessionManager
    from personalclaw.skills import SkillsLoader
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

SESSIONS_DIR_NAME = "sessions"
ARCHIVE_DIR_NAME = "archive"
ARCHIVE_RETENTION_DAYS = 7
_CONSOLIDATION_THRESHOLD = 30  # preferences/projects update threshold (messages)
_PLAN_CONSOLIDATION_THRESHOLD = 20  # plan lessons: consolidate after N new events
_SESSION_MAX_BYTES = 2 * 1024 * 1024  # 2MB
_SESSION_KEEP_LINES = 200
SEARCH_MIN_CHARS = 2  # shortest query string that triggers backend search
_TITLE_BOOST = 10  # field-boost multiplier for title matches in search_sessions
_SEARCH_SCAN_WINDOW = 500  # cap files scanned per search to bound I/O


def _sessions_dir() -> Path:
    return config_dir() / SESSIONS_DIR_NAME


def _archive_dir(base: Path | None = None) -> Path:
    return (base or _sessions_dir()) / ARCHIVE_DIR_NAME


def _archive_lines(
    key: str, lines: list[str], reason: str, base: Path | None = None
) -> Path | None:
    """Append dropped message lines to archive/{key}.{YYYYMMDD-HHMMSS}.jsonl. Returns path or None."""  # noqa: E501
    if not lines:
        return None
    import itertools

    adir = _archive_dir(base)
    adir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    safekey = _safe_key(key)
    header = (
        json.dumps(
            {
                "_type": "archive",
                "reason": reason,
                "archived_at": now.isoformat(),
                "count": len(lines),
            }
        )
        + "\n"
    )
    payload = header + "".join(lines)
    # Atomic exclusive-create to avoid TOCTOU clobber when two archives land in the same second.
    # Use '__' delimiter so keys containing dots (e.g. a channel thread_ts) don't confuse rfind('.') parsing.  # noqa: E501
    for n in itertools.count():
        if n > 1000:
            raise RuntimeError(f"Failed to create archive file after {n} attempts")
        candidate = adir / f"{safekey}__{stamp}{f'-{n}' if n else ''}.jsonl"
        try:
            with candidate.open("x", encoding="utf-8") as f:
                f.write(payload)
            break
        except FileExistsError:
            continue
    logger.info(
        "Archived %d lines from session %s to %s (reason=%s)",
        len(lines),
        key,
        candidate.name,
        reason,
    )
    _cleanup_old_archives(base=base)
    return candidate


_last_cleanup: float = 0.0


def _cleanup_old_archives(
    retention_days: int = ARCHIVE_RETENTION_DAYS, base: Path | None = None
) -> int:
    """Delete archive files older than retention_days. Rate-limited to once per hour."""
    global _last_cleanup
    import time as _time

    now = _time.time()
    if now - _last_cleanup < 3600:
        return 0
    _last_cleanup = now
    adir = _archive_dir(base)
    if not adir.exists():
        return 0
    cutoff = now - retention_days * 86400
    removed = 0
    for p in adir.glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("Cleaned %d expired archive files (>%dd)", removed, retention_days)
    return removed


def _safe_key(key: str) -> str:
    """Convert a session key (e.g. a channel thread_ts) to a safe filename."""
    return re.sub(r"[^\w\-.]", "_", key)


class ConversationLog:
    """Append-only JSONL conversation store with provenance and rotation."""

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or _sessions_dir()
        # mtime-based message cache: key → (mtime, messages)
        self._msg_cache: dict[str, tuple[float, list[dict]]] = {}
        # mtime-based metadata cache: key → (mtime, metadata)
        self._meta_cache: dict[str, tuple[float, dict]] = {}

    def init(self) -> None:
        """Create sessions directory if missing."""
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{_safe_key(key)}.jsonl"

    def has_log(self, key: str) -> bool:
        """Return True if a conversation log file exists for *key*."""
        return self._path(key).exists()

    def append(
        self,
        key: str,
        role: str,
        content: str,
        tools: list[str] | None = None,
        source_thread: str | None = None,
        source_user: str | None = None,
        agent: str | None = None,
        tab_id: str | None = None,
    ) -> None:
        """Append a message with optional provenance to the session log.

        If the session file does not yet exist, it will be created with an
        initial metadata line.  When *agent* is supplied, the agent name is
        recorded in that metadata so the session can be resumed under the
        correct agent later.  (Has no effect if the file already exists;
        use :meth:`update_metadata` to change the agent after creation.)
        """
        path = self._path(key)
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            meta: dict = {
                "_type": "metadata",
                "created_at": datetime.now().isoformat(),
                "last_consolidated": 0,
            }
            if agent:
                meta["agent"] = agent
            if tab_id:
                meta["tab_id"] = tab_id
            path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

        msg: dict = {
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(),
        }
        if tools:
            msg["tools"] = tools
        if source_thread:
            msg["source_thread"] = source_thread
        if source_user:
            msg["source_user"] = source_user

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")

        # Invalidate cache since file changed
        self._invalidate_cache(key)

        # Rotate if file exceeds size limit
        self._maybe_rotate(path)

    def recent(
        self,
        key: str,
        max_messages: int = 20,
        roles: set[str] | None = None,
    ) -> list[dict]:
        """Return last *max_messages* entries as ``[{role, content}]``.

        When *roles* is provided, only messages with matching roles are
        counted toward the limit.  This filters out low-signal entries
        (e.g. tool display titles) so the budget is spent on user and
        assistant content.
        """
        messages = self._read_messages(key)
        if roles:
            messages = [m for m in messages if m["role"] in roles]
        return [{"role": m["role"], "content": m["content"]} for m in messages[-max_messages:]]

    def recent_with_provenance(self, key: str, max_messages: int = 3) -> list[dict]:
        """Return recent entries with source_thread provenance for cross-session citation."""
        messages = self._read_messages(key)
        with_source = [m for m in messages if m.get("source_thread")]
        result: list[dict] = []
        for m in with_source[-max_messages:]:
            snippet = m["content"][:150] + "…" if len(m["content"]) > 150 else m["content"]
            result.append(
                {
                    "source_thread": m["source_thread"],
                    "ts": m.get("ts", "?"),
                    "snippet": snippet,
                }
            )
        return result

    def get_unconsolidated(self, key: str) -> tuple[list[dict], int]:
        """Return (messages_after_last_consolidated, total_message_count)."""
        messages = self._read_messages(key)
        offset = self._read_metadata(key).get("last_consolidated", 0)
        return messages[offset:], len(messages)

    def mark_consolidated(self, key: str, offset: int) -> None:
        """Rewrite metadata line with updated last_consolidated offset."""
        path = self._path(key)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines:
            return
        meta = json.loads(lines[0])
        meta["last_consolidated"] = offset
        meta["updated_at"] = datetime.now().isoformat()
        lines[0] = json.dumps(meta) + "\n"
        atomic_write(path, "".join(lines))
        self._invalidate_cache(key)

    def unconsolidated_count(self, key: str) -> int:
        """Count messages not yet processed by the consolidator."""
        messages = self._read_messages(key)
        offset = self._read_metadata(key).get("last_consolidated", 0)
        return max(0, len(messages) - offset)

    def load_transcript(self, key: str) -> str:
        """Load full session as formatted text for LLM summarization."""
        messages = self._read_messages(key)
        if not messages:
            return ""
        lines: list[str] = []
        for m in messages:
            role = m["role"].title()
            lines.append(f"{role}: {m['content']}")
        return "\n\n".join(lines)

    @staticmethod
    def _canonical_key(key: str) -> str:
        """Collapse stacked ``dashboard_`` prefixes to a single one.

        Files like ``dashboard_dashboard_chat-1-123`` are duplicates of
        ``dashboard_chat-1-123`` caused by resume round-trips.  Return
        the canonical (single-prefix) form so callers can deduplicate.
        """
        if not key.startswith("dashboard_"):
            return key
        stripped = key
        while stripped.startswith("dashboard_"):
            stripped = stripped[len("dashboard_") :]
        return f"dashboard_{stripped}" if stripped else key

    def list_sessions(self) -> list[dict]:
        """Return metadata for all session files, newest first.

        Deduplicates stacked ``dashboard_`` prefix files, keeping the
        most recently modified version.  Uses mtime-based metadata cache
        when available, falling back to reading only the first line for
        title extraction.
        """
        sessions: list[dict] = []
        if not self._dir.exists():
            return sessions
        # Deduplicate stacked dashboard_ prefixes by canonical key, keeping newer
        by_canon: dict[str, dict] = {}
        for path in self._dir.glob("*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            # Skip symlinks — these are handoff aliases pointing to the real session
            if path.is_symlink():
                continue
            key = path.stem
            meta: dict = {
                "key": key,
                "messages": max(1, int(stat.st_size / 200)),
                "modified": stat.st_mtime,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
            # Try metadata cache first (populated by _read_metadata calls)
            cached_meta = self._meta_cache.get(key)
            if cached_meta and cached_meta[0] == stat.st_mtime:
                d = cached_meta[1]
                if d.get("created_at"):
                    meta["created"] = d["created_at"]
                if d.get("title"):
                    meta["title"] = d["title"]
                if d.get("agent"):
                    meta["agent"] = d["agent"]
                meta["memory_mode"] = d.get("memory_mode", "persistent")
            else:
                # Read only the first line for metadata
                try:
                    with open(path, encoding="utf-8") as f:
                        first_line = f.readline().strip()
                    if first_line:
                        d = json.loads(first_line)
                        if d.get("_type") == "metadata":
                            if d.get("created_at"):
                                meta["created"] = d["created_at"]
                            if d.get("title"):
                                meta["title"] = d["title"]
                            if d.get("agent"):
                                meta["agent"] = d["agent"]
                            meta["memory_mode"] = d.get("memory_mode", "persistent")
                            self._meta_cache[key] = (stat.st_mtime, d)
                except Exception:
                    pass
            # Ensure memory_mode is always present (old sessions lack it)
            meta.setdefault("memory_mode", "persistent")
            # Extract first user message as title fallback
            if "title" not in meta:
                msg_cached = self._msg_cache.get(key)
                if msg_cached and msg_cached[0] == stat.st_mtime:
                    for m in msg_cached[1]:
                        if m.get("role") == "user" and m.get("content"):
                            meta["title"] = m["content"][:80]
                            break
                else:
                    try:
                        with open(path, encoding="utf-8") as f:
                            for i, ln in enumerate(f):
                                if i > 20:
                                    break
                                ln = ln.strip()
                                if not ln:
                                    continue
                                try:
                                    d = json.loads(ln)
                                except json.JSONDecodeError:
                                    continue
                                if d.get("role") == "user" and d.get("content"):
                                    meta["title"] = d["content"][:80]
                                    break
                    except Exception:
                        pass
            if "title" not in meta:
                meta["title"] = key
            # Deduplicate: keep newer entry per canonical key
            canon = self._canonical_key(key)
            existing = by_canon.get(canon)
            if existing is None or stat.st_mtime >= existing["modified"]:
                by_canon[canon] = meta
        sessions = list(by_canon.values())
        sessions.sort(key=lambda s: s.get("modified", 0), reverse=True)
        return sessions

    def search_sessions(self, query: str, limit: int = 50) -> list[dict]:
        """Return session metadata for files whose message content matches *query*.

        Case-insensitive substring match over each message's ``content``
        field using full Unicode case folding via :meth:`str.casefold`
        (so e.g. German ``ß`` folds to ``ss``).  Matching only on parsed
        ``content`` avoids false positives from JSON structural elements
        (e.g. the word ``"user"`` matching every ``"role": "user"`` line).

        Ranking (higher is better)::

            score = (title_matches * _TITLE_BOOST)
                  + (content_matches / sqrt(1 + doc_chars / 1024))

        Title matches get a strong field boost - titles are short and
        intentional, so a hit there is strong evidence.  Content matches
        are normalized by a sqrt length factor so a long session with a
        casual mention doesn't outrank a short, focused one.  (Simpler
        than BM25's ``(1-b) + b*(dl/avgdl)`` because we avoid the
        two-pass scan needed for corpus stats.)  Sessions with zero
        matches are dropped.  Ties break by recency (existing
        ``list_sessions`` order - newest first).  Caps results at *limit*.
        Only the ``_SEARCH_SCAN_WINDOW`` most recent files are scored, so
        I/O stays bounded even with hundreds of sessions.
        """
        if not query or limit <= 0 or not self._dir.exists():
            return []
        needle = query.casefold()
        scored: list[tuple[float, int, dict]] = []  # (score, -rank, meta)
        for rank, meta in enumerate(self.list_sessions()[:_SEARCH_SCAN_WINDOW]):
            # Restricted (incognito/temporary) sessions promise to stay out of
            # history — they must not be discoverable through content search.
            if meta.get("memory_mode") in ("incognito", "temporary"):
                continue
            path = self._path(meta["key"])
            content_hits = 0
            doc_chars = 0
            texts: list[str] = []
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        # Always parse - a raw-line fast path would miss
                        # queries containing JSON-escapable chars
                        # (backslash, quote, newline) because the on-disk
                        # line has escaped forms while the parsed content
                        # has literal chars.  Skip unparseable lines to
                        # avoid matching on structural JSON keys.
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        raw = obj.get("content") if isinstance(obj, dict) else None
                        text = raw if isinstance(raw, str) else ""
                        if text:
                            # Accumulate length only for content text so
                            # the length normalizer's denominator matches
                            # the hit counter's numerator (no penalty for
                            # verbose metadata / tool-call lines).
                            doc_chars += len(text)
                            texts.append(text)
            except OSError:
                continue
            # Casefold + count once per file instead of per line: a 200-line
            # session produces one temporary casefolded string instead of 200,
            # bounding GC pressure under rapid-fire search keystrokes.  The
            # ``\x00`` separator can't appear in user queries, so cross-line
            # false matches are impossible.
            if texts:
                content_hits = "\x00".join(texts).casefold().count(needle)
            title_hits = (meta.get("title") or "").casefold().count(needle)
            if not title_hits and not content_hits:
                continue
            length_norm = math.sqrt(1 + doc_chars / 1024)
            score = title_hits * _TITLE_BOOST + content_hits / length_norm
            # Negate rank so a smaller (newer) rank wins ties after score desc sort.
            scored.append((score, -rank, meta))
        scored.sort(reverse=True)
        return [meta for _, _, meta in scored[:limit]]

    def recent_from_source(
        self, source_prefix: str, exclude_key: str = "", max_messages: int = 20
    ) -> list[dict]:
        """Return recent messages from sessions matching *source_prefix*.

        Optimized: only scans the 5 most recently modified files and reads
        only the last 50 lines from each, avoiding full-file I/O on large
        session histories.
        """
        if not self._dir.exists():
            return []
        safe_exclude = _safe_key(exclude_key) if exclude_key else ""
        safe_prefix = _safe_key(source_prefix)
        # Collect matching paths and sort by mtime (newest first)
        paths: list[Path] = []
        for path in self._dir.glob(f"{safe_prefix}*.jsonl"):
            if safe_exclude and path.stem == safe_exclude:
                continue
            paths.append(path)
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        candidates: list[dict] = []
        included = 0
        _max_scan = 50  # bound I/O even with many ephemeral sessions
        for path in paths[:_max_scan]:
            if included >= 5:
                break
            # Single-pass read: check metadata head, then read remainder via same handle
            is_restricted = False
            try:
                with open(path, encoding="utf-8") as f:
                    head_lines = []
                    for _, line in zip(range(5), f):
                        head_lines.append(line)
                        try:
                            d = json.loads(line.strip())
                            if d.get("_type") == "metadata" and d.get("memory_mode") in (
                                "incognito",
                                "temporary",
                            ):
                                is_restricted = True
                                break
                        except (json.JSONDecodeError, ValueError):
                            pass
                    if is_restricted:
                        continue
                    raw = "".join(head_lines) + f.read()
            except OSError:
                continue
            included += 1
            lines = raw.splitlines()
            for line in lines[-50:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("_type") == "metadata":
                    continue
                candidates.append(data)
        # Sort by timestamp and return most recent
        candidates.sort(key=lambda m: m.get("ts", ""))
        return [{"role": m["role"], "content": m["content"]} for m in candidates[-max_messages:]]

    def read_messages(self, key: str) -> list[dict]:
        """Public access to session messages."""
        return self._read_messages(key)

    def read_messages_chained(self, key: str) -> list[dict]:
        """Read messages from all session files sharing the same ``tab_id``.

        Returns messages from the current file only if no ``tab_id`` is set
        (legacy sessions).  Otherwise finds all sibling files with the same
        ``tab_id``, sorts chronologically, and concatenates their messages.

        Uses a ``_tab_id_index`` cache (built lazily, invalidated on save)
        to avoid scanning every file on each call.
        """
        meta = self.get_metadata(key)
        tid = meta.get("tab_id")
        if not tid:
            return self._read_messages(key)
        if not hasattr(self, "_tab_id_index"):
            self._tab_id_index: dict[str, list[str]] = {}
        if tid not in self._tab_id_index:
            self._rebuild_tab_id_index()
            if tid not in self._tab_id_index:
                self._tab_id_index[tid] = []  # sentinel: prevent repeated rebuilds
        keys = self._tab_id_index.get(tid, [])
        if not keys:
            return self._read_messages(key)
        all_msgs: list[dict] = []
        for k in keys:
            all_msgs.extend(self._read_messages(k))
        return all_msgs or self._read_messages(key)

    def _rebuild_tab_id_index(self) -> None:
        """Scan all dashboard session files and build tab_id → [keys] mapping."""
        index: dict[str, list[str]] = {}
        for path in sorted(self._dir.glob("dashboard_chat-*.jsonl")):
            try:
                with path.open(encoding="utf-8") as f:
                    first_line = f.readline()
                m = json.loads(first_line)
                tid = m.get("tab_id")
                if tid:
                    index.setdefault(tid, []).append(path.stem.replace("_", ":", 1))
            except Exception:
                continue
        self._tab_id_index = index

    def invalidate_tab_id_cache(self) -> None:
        """Clear the tab_id index so it's rebuilt on next chained read."""
        if hasattr(self, "_tab_id_index"):
            self._tab_id_index.clear()

    def delete_session(self, key: str) -> bool:
        """Delete a session file. Returns True if deleted."""
        path = self._path(key)
        if path.exists():
            path.unlink()
            self._invalidate_cache(key)
            self.invalidate_tab_id_cache()
            return True
        return False

    def set_title(self, key: str, title: str) -> None:
        """Persist a title into the session's metadata line."""
        self.update_metadata(key, {"title": title})

    def update_metadata(self, key: str, fields: dict) -> None:
        """Merge *fields* into the session's metadata line and persist."""
        path = self._path(key)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines:
            return
        try:
            meta = json.loads(lines[0])
            if meta.get("_type") != "metadata":
                return
        except json.JSONDecodeError:
            return
        meta.update(fields)
        lines[0] = json.dumps(meta) + "\n"
        atomic_write(path, "".join(lines), fsync=True)
        self._invalidate_cache(key)

    def _read_messages(self, key: str) -> list[dict]:
        """Read all non-metadata entries from a session JSONL file.

        Uses mtime-based caching to avoid re-parsing unchanged files.
        """
        path = self._path(key)
        if not path.exists():
            self._msg_cache.pop(key, None)
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        cached = self._msg_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        messages: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("_type") == "metadata":
                continue
            messages.append(data)
        self._msg_cache[key] = (mtime, messages)
        return messages

    def _invalidate_cache(self, key: str) -> None:
        """Invalidate caches for a key after a write operation."""
        self._msg_cache.pop(key, None)
        self._meta_cache.pop(key, None)

    def get_metadata(self, key: str) -> dict:
        """Return session metadata for *key*."""
        return self._read_metadata(key)

    def _read_metadata(self, key: str) -> dict:
        """Read the metadata line (first line) from a session JSONL file.

        Uses mtime-based caching to avoid re-reading unchanged files.
        """
        path = self._path(key)
        if not path.exists():
            self._meta_cache.pop(key, None)
            return {}
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._meta_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        first = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        if not first:
            return {}
        try:
            data = json.loads(first)
            meta = data if data.get("_type") == "metadata" else {}
        except json.JSONDecodeError:
            meta = {}
        self._meta_cache[key] = (mtime, meta)
        return meta

    def sliding_window(self, key: str, keep_recent: int = 5) -> tuple[list[dict], list[dict]]:
        """Split messages into (older, recent) for compaction.

        *keep_recent* is the number of recent user/assistant pairs to retain.
        Returns ``(older_messages, recent_messages)``.
        """
        messages = self._read_messages(key)
        # keep_recent pairs = keep_recent * 2 individual messages
        split = max(0, len(messages) - keep_recent * 2)
        return messages[:split], messages[split:]

    def rewrite_session(self, key: str, messages: list[dict]) -> None:
        """Rewrite session JSONL with only the given messages."""
        path = self._path(key)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Archive only messages being dropped (old content minus what's being kept).
        # Compare by normalized JSON (sort_keys) to be resilient to key ordering changes.
        if path.exists():
            old_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if old_lines and '"_type"' in old_lines[0]:
                old_lines = old_lines[1:]
            kept_serialized = {json.dumps(m, sort_keys=True) for m in messages}
            dropped = []
            for ln in old_lines:
                if not ln.strip():
                    continue
                try:
                    normalized = json.dumps(json.loads(ln), sort_keys=True)
                except (json.JSONDecodeError, ValueError):
                    dropped.append(ln)  # corrupted line → archive it
                    continue
                if normalized not in kept_serialized:
                    dropped.append(ln)
            try:
                _archive_lines(key, dropped, reason="compact", base=self._dir)
            except Exception:
                logger.warning("Failed to archive dropped lines for %s", key, exc_info=True)
        # Preserve select fields from original metadata
        orig_meta = self.get_metadata(key) or {}
        meta = {
            "_type": "metadata",
            "created_at": orig_meta.get("created_at", datetime.now().isoformat()),
            "last_consolidated": orig_meta.get("last_consolidated", 0),
            "compacted_at": datetime.now().isoformat(),
        }
        if orig_meta.get("memory_mode"):
            meta["memory_mode"] = orig_meta["memory_mode"]
        lines = [json.dumps(meta) + "\n"]
        for m in messages:
            lines.append(json.dumps(m) + "\n")
        atomic_write(path, "".join(lines))
        self._invalidate_cache(key)

    def _maybe_rotate(self, path: Path) -> None:
        """Rotate session file if it exceeds size limit."""
        try:
            if path.stat().st_size <= _SESSION_MAX_BYTES:
                return
        except OSError:
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= _SESSION_KEEP_LINES:
            return
        # Keep metadata line + last N message lines
        meta_line = lines[0] if lines and '"_type"' in lines[0] else ""
        kept = lines[-_SESSION_KEEP_LINES:]
        dropped_start = 1 if meta_line else 0
        # Edge case: if len(lines) <= _SESSION_KEEP_LINES + dropped_start, the slice
        # is empty and _archive_lines returns None (noop). The guard above already
        # returns when len(lines) <= _SESSION_KEEP_LINES, so this only fires when
        # there are genuinely more lines than we keep.
        try:
            _archive_lines(
                path.stem,
                lines[dropped_start:-_SESSION_KEEP_LINES],
                reason="rotate",
                base=self._dir,
            )
        except Exception:
            logger.warning("Failed to archive rotated lines for %s", path.stem, exc_info=True)

        # Reset last_consolidated since offsets are now invalid
        if meta_line:
            try:
                meta = json.loads(meta_line)
                meta["last_consolidated"] = 0
                meta["rotated_at"] = datetime.now().isoformat()
                meta_line = json.dumps(meta) + "\n"
            except json.JSONDecodeError:
                pass

        content = meta_line + "".join(kept)
        atomic_write(path, content)
        # Invalidate cache — offsets changed
        safe = path.stem
        self._invalidate_cache(safe)
        logger.info("Rotated session file %s (%d → %d lines)", path.name, len(lines), len(kept))


# ── Module-level helpers for auto skill eligibility ──
#
# Kept at module level so they're trivially unit-testable without
# instantiating HistoryConsolidator.

# Canonical tool titles that indicate a read targeting a sensitive path.
# Supplements is_sensitive_path() and is_sensitive_bash_command() which
# handle the actual runtime blocking — this is a second-layer defense
# that refuses to extract a skill if the session tried to access a
# sensitive path, even when the attempt was denied at hook time.
_SENSITIVE_TOOL_PATTERNS: tuple[str, ...] = (
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    ".docker/config",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".personalclaw/.env",
    "169.254.169.254",  # IMDS
)


_TOOL_ROLES: frozenset[str] = frozenset({"tool", "tool_call", "tool_result"})


def _count_tool_call_messages(messages: list[dict]) -> int:
    """Count messages that represent tool invocations under either schema.

    Two recording formats exist:
    - Channel-pipeline schema: assistant messages carry a ``tools`` list field.
    - Dashboard pipeline: separate messages with ``role`` in {"tool", "tool_call",
      "tool_result"} and the tool name embedded in ``content``.

    A message matching EITHER condition counts once (no double-counting).
    """
    count = 0
    for msg in messages:
        tools = msg.get("tools")
        if isinstance(tools, list) and tools:
            count += 1
        elif msg.get("role") in _TOOL_ROLES:
            count += 1
    return count


def _session_touched_sensitive(messages: list[dict]) -> bool:
    """Return True if any tool call in the session referenced a sensitive path.

    Checks both recording schemas:
    - Channel-pipeline schema: substring match over each entry in ``msg["tools"]`` list.
    - Dashboard: substring match over ``content`` when ``role`` indicates a tool event.

    Designed to be conservative — a false positive just means we skip
    auto-creation for this session.
    """
    for msg in messages:
        # Channel-pipeline schema: tools list on assistant messages
        tools = msg.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, str):
                    continue
                lower = tool.lower()
                for pattern in _SENSITIVE_TOOL_PATTERNS:
                    if pattern in lower:
                        return True
        # Dashboard schema: role="tool" with tool info in content
        if msg.get("role") in _TOOL_ROLES:
            content = msg.get("content", "")
            if isinstance(content, str):
                lower = content.lower()
                for pattern in _SENSITIVE_TOOL_PATTERNS:
                    if pattern in lower:
                        return True
    return False


class HistoryConsolidator:
    """Summarize old messages into structured memory via LLM.

    Two consolidation paths:
    - Preferences/projects: triggered by message count (30 messages)
    - Daily history: triggered by idle time (3h default) or end of day
    """

    def __init__(
        self,
        log: ConversationLog,
        memory: "MemoryStore",
        sessions: "SessionManager | None" = None,
        lesson_store: "LessonStore | None" = None,
        history_idle_secs: float = 3 * 3600,
        vector_store: "VectorMemoryStore | None" = None,
        migrated: bool = False,
        # ── Auto skill creation ──
        # All-default so callers unaware of this feature continue to work.
        skills_loader: "SkillsLoader | None" = None,
        auto_skills_enabled: bool = False,
        auto_refine_enabled: bool = False,
        auto_min_tool_calls: int = 5,
        auto_similarity_threshold: float = 0.85,
    ) -> None:
        self._log = log
        self._memory = memory
        self._sessions = sessions
        self._lesson_store = lesson_store
        self._history_idle_secs = history_idle_secs
        self._vector_store = vector_store
        self._memory_service: "MemoryService | None" = None  # lazily built over the store
        self._migrated = migrated
        self._skills_loader = skills_loader
        self._auto_skills_enabled = auto_skills_enabled
        self._auto_refine_enabled = auto_refine_enabled
        self._auto_min_tool_calls = auto_min_tool_calls
        self._auto_similarity_threshold = auto_similarity_threshold
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        # Track last activity per session for idle-based history consolidation
        self._last_activity: dict[str, float] = {}
        self._history_consolidated: dict[str, float] = {}  # key → last history consolidation time
        # Separate offset for prefs-only consolidation (doesn't advance main offset)
        self._prefs_offset: dict[str, int] = {}
        self._last_plan_event_count: int = 0  # event count at last plan consolidation
        self._plan_consolidation_backoff: float = 0.0  # monotonic time to retry after failure
        self._plan_next_check: float = 0.0  # monotonic time for next threshold check
        # Autonomous episodic→semantic promotion: count history consolidations to
        # fire promotion every Nth, with a min-interval floor (monotonic time).
        self._consolidation_count: int = 0
        self._last_promote_monotonic: float = 0.0

    @property
    def _svc(self):
        """The MemoryService over this consolidator's record/vector store (L3).

        The consolidator is an L3 write path — it operates directly on the
        record store, so the service wraps the vector store it was handed rather
        than a markdown projection. Cached; no-op when no store is wired."""
        if self._memory_service is None:
            from personalclaw.memory_service import MemoryService

            self._memory_service = MemoryService.over_vector_store(self._vector_store)
        return self._memory_service

    @property
    def _proactive_commitments(self) -> bool:
        """Whether proactive commitment extraction is opted in (M5e). Read fresh
        from config each call so the Settings toggle takes effect live, like the
        auto-promote gate — the feature is OFF by default (creepy when wrong)."""
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().memory.proactive_commitments)

    @property
    def _proactive_commitments_max(self) -> int:
        """Hard per-day cap on active proactive commitments per agent (M5e)."""
        from personalclaw.config.loader import AppConfig

        return max(1, int(AppConfig.load().memory.proactive_commitments_max_per_day))

    def maybe_consolidate(self, key: str) -> None:
        """Fire preferences/projects consolidation if message threshold exceeded."""
        self._last_activity[key] = _time.time()
        if key in self._running:
            return
        total = len(self._log._read_messages(key))
        prefs_off = self._prefs_offset.get(key, 0)
        if total - prefs_off < _CONSOLIDATION_THRESHOLD:
            return
        self._running.add(key)
        t = asyncio.create_task(self._consolidate(key, include_history=False))
        self._tasks.add(t)

        def _on_done(fut: asyncio.Task, k: str = key, off: int = total) -> None:  # type: ignore[type-arg]  # noqa: E501
            self._tasks.discard(fut)
            if not fut.cancelled() and fut.exception() is None:
                self._prefs_offset[k] = off

        t.add_done_callback(_on_done)

    async def consolidate_now(self, key: str) -> bool:
        """Run history consolidation + auto-skill extraction for one session NOW.

        The synchronous-await public entry point for explicit triggers (idle-
        expiry callback, a channel "End session", `personalclaw consolidate`) — as
        opposed to the fire-and-forget idle poll. Always uses the **history**
        path (``include_history=True``), since auto-skill extraction is gated on
        it (see ``_consolidate`` ``auto_skills_eligible``). Respects the running
        guard so it never double-runs against the idle poll; ``_consolidate``
        clears the guard in its ``finally``. Returns True if it ran, False if a
        consolidation was already in flight for this key.
        """
        if key in self._running:
            return False
        self._running.add(key)
        await self._consolidate(key, include_history=True)
        return True

    # The explicit session-end seam (E11): an idle-expire / channel-end / CLI
    # trigger calls this. Distinct from the fire-and-forget poll so call sites
    # read intentionally ("consolidate this ending session") AND so SEALING only
    # fires at real session end (M5c) — never on a mid-session idle consolidation.
    async def consolidate_session(self, key: str) -> bool:
        """Consolidate an ENDING session, then SEAL it: distill the session's
        working memory into a durable in-scope record and sweep unpromoted
        session-scoped records (memory-architecture.md §3.5). Sealing deepens
        tier (working→episodic) at scope=session — it does NOT write to global;
        the heat gate (run on the maintenance cadence) is the only path to global."""
        ran = await self.consolidate_now(key)
        try:
            swept = self._svc.seal_session(key)
            if swept:
                logger.info("Sealed session %s — swept %d unpromoted record(s)", key, swept)
        except Exception:
            logger.debug("session seal failed for %s", key, exc_info=True)
        # Mirror memory → markdown vault at the natural post-seal boundary (the
        # mem-fs-mirror freshness trigger). No-op when the vault is disabled;
        # never raises (best-effort, guarded internally).
        try:
            from personalclaw.memory_vault import mirror_after_consolidation

            mirror_after_consolidation(self._svc)
        except Exception:
            logger.debug("memory vault mirror failed for %s", key, exc_info=True)
        return ran

    def check_idle_sessions(self) -> None:
        """Check all tracked sessions for idle-based history consolidation."""
        now = _time.time()
        for key, last in list(self._last_activity.items()):
            if (
                now - last < self._history_idle_secs
                or self._log.unconsolidated_count(key) < 1
                or now - self._history_consolidated.get(key, 0) < self._history_idle_secs
                or key in self._running
            ):
                continue
            self._running.add(key)
            captured_now = now
            t = asyncio.create_task(self._consolidate(key, include_history=True))
            self._tasks.add(t)

            def _on_idle_done(
                fut: asyncio.Task,  # type: ignore[type-arg]
                k: str = key,
                ts: float = captured_now,
            ) -> None:
                self._tasks.discard(fut)
                if not fut.cancelled() and fut.exception() is None:
                    self._history_consolidated[k] = ts

            t.add_done_callback(_on_idle_done)

        # Consolidate plan lessons (global, not per-session) — only after 20+ new events
        _now = _time.monotonic()
        if (
            "plan_lessons" not in self._running
            and _now >= self._plan_consolidation_backoff
            and _now >= self._plan_next_check
        ):
            try:
                from personalclaw.context_management import plan_memory_path

                path = plan_memory_path()
                if path.exists():
                    self._running.add("plan_lessons")
                    t = asyncio.create_task(self._consolidate_plan_lessons(path))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                else:
                    self._plan_next_check = _now + 60
            except Exception:
                self._plan_next_check = _now + 60
                logger.debug("Plan lesson check failed", exc_info=True)

    async def _consolidate_plan_lessons(self, path: Path) -> None:
        """Consolidate plan memory into plan_lessons.md using LLM."""
        try:

            def _count_lines(p: Path) -> int:
                with open(p, encoding="utf-8") as f:
                    return sum(1 for _ in f)

            count = await asyncio.to_thread(_count_lines, path)
            if count < self._last_plan_event_count:
                self._last_plan_event_count = 0  # file truncated/rotated
            if count - self._last_plan_event_count < _PLAN_CONSOLIDATION_THRESHOLD:
                self._plan_next_check = _time.monotonic() + 60
                return

            from personalclaw.context_management import (
                build_plan_consolidation_prompt,
                save_plan_lessons,
            )
            from personalclaw.llm_helpers import stream_and_collect

            prompt = build_plan_consolidation_prompt()
            if not prompt:
                self._plan_next_check = _time.monotonic() + 60
                return
            if not self._sessions:
                self._plan_next_check = _time.monotonic() + 60
                return
            session_key = BACKGROUND_KEY
            try:
                client, _, _ = await self._sessions.get_or_create(
                    session_key, agent="personalclaw-lite"
                )
                result = await stream_and_collect(client, prompt)
                save_plan_lessons(result)
                self._last_plan_event_count = count
                self._plan_next_check = _time.monotonic() + 60
            finally:
                self._sessions.release(session_key)
                await self._sessions.recycle_background()
        except Exception:
            self._plan_consolidation_backoff = _time.monotonic() + 300  # 5 min cooldown
            logger.warning("Plan lesson consolidation failed", exc_info=True)
        finally:
            self._running.discard("plan_lessons")

    async def _consolidate(self, key: str, include_history: bool = True) -> None:
        """Run LLM consolidation for a session, single-flight across processes.

        The in-memory ``self._running`` guard prevents double-runs within this
        process; the :func:`single_flight` lock prevents a second process (the
        ``personalclaw consolidate`` CLI, the eval runner) from consolidating the
        same key concurrently — which would race on the history metadata offset,
        the vector store, and the lesson store. If another process holds the
        lock we skip (clearing the in-memory guard the caller set), since a
        concurrent consolidation of the same key is redundant, not queued work.
        """
        with single_flight(f"consolidate:{key}") as acquired:
            if not acquired:
                logger.info(
                    "Consolidation for %s already running in another process — skipping",
                    key,
                )
                self._running.discard(key)
                return
            await self._consolidate_locked(key, include_history=include_history)

    async def _consolidate_locked(self, key: str, include_history: bool = True) -> None:
        """Run LLM consolidation for a session (holding the single-flight lock)."""
        try:
            unconsolidated, total = self._log.get_unconsolidated(key)
            if not unconsolidated:
                return

            # Resolve workspace-scoped memory from session metadata
            meta = self._log.get_metadata(key)
            ws_name = meta.get("workspace")
            if ws_name:
                from personalclaw.context import ContextBuilder

                memory = ContextBuilder.get_memory_for(ws_name)
            else:
                memory = self._memory

            def _fmt(m: dict) -> str:
                tools = f" [tools: {', '.join(m['tools'])}]" if m.get("tools") else ""
                return f"[{m.get('ts', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}"

            conversation = "\n".join(_fmt(m) for m in unconsolidated)

            current_prefs = memory.read_preferences()
            current_projects = memory.read_projects()

            # Build prompt keys dynamically based on consolidation type. Each key's
            # instruction prose is a bundled ``consolidation-key-*`` snippet; the
            # selection logic (which keys apply) stays here.
            from personalclaw.prompt_providers.runtime import render_snippet_block

            keys: list[str] = []
            if include_history:
                keys.append(render_snippet_block("consolidation-key-history"))

            # Structured memory extraction (when the record store is available)
            has_vector = self._svc.has_vector
            if has_vector:
                current_semantic = self._svc.get_all_semantic()
                semantic_json = (
                    json.dumps(
                        [
                            {k: e[k] for k in ("key", "value_json", "confidence")}
                            for e in current_semantic
                        ],
                        indent=1,
                    )
                    if current_semantic
                    else "[]"
                )
                keys.append(render_snippet_block("consolidation-key-semantic"))
                keys.append(render_snippet_block("consolidation-key-episodic"))

            # Markdown memory (used when not migrated to structured memory)
            if not self._migrated:
                keys.append(render_snippet_block("consolidation-key-preferences"))
                keys.append(render_snippet_block("consolidation-key-projects"))

            if include_history:
                keys.append(render_snippet_block("consolidation-key-lessons"))

            # Agent self-persona (M5e): the agent's own positive growth notes —
            # always available on the history path. Distinct from lessons (which
            # record what NOT to do); this records who the agent is becoming.
            if include_history and has_vector:
                keys.append(render_snippet_block("consolidation-key-self-persona"))

            # Commitments (M5e — O-A4): inferred proactive check-ins. GUARDRAILED —
            # only extracted when the user opted in (off by default). The 'creepy
            # when wrong' class, so the prompt demands high-confidence + genuinely
            # useful time-bound follow-ups the user did NOT ask to be reminded of.
            if include_history and has_vector and self._proactive_commitments:
                keys.append(
                    render_snippet_block(
                        "consolidation-key-commitments",
                        {"max_commitments": self._proactive_commitments_max},
                    )
                )

            # ── Auto skill creation ──
            # Only eligible when the feature is enabled, we have a loader to
            # write to, we're on the history path (so prefs-only doesn't retrigger
            # extraction), and the session has enough tool calls to be non-trivial.
            auto_skills_eligible = (
                include_history
                and self._auto_skills_enabled
                and self._skills_loader is not None
                and _count_tool_call_messages(unconsolidated) >= self._auto_min_tool_calls
                and not _session_touched_sensitive(unconsolidated)
            )
            if auto_skills_eligible:
                keys.append(render_snippet_block("consolidation-key-new-skill"))
                if self._auto_refine_enabled:
                    keys.append(render_snippet_block("consolidation-key-refined-skill"))

            numbered = "\n\n".join(f"{i+1}. {k}" for i, k in enumerate(keys))
            # The envelope (intro + section ordering + closing instruction) is the
            # bundled ``task-memory-consolidation`` prompt; the optional context
            # sections are assembled here in the same order as before.
            semantic_block = (
                f"\n\n## Current Semantic Memory\n{semantic_json}" if has_vector else ""
            )
            markdown_blocks = ""
            if not self._migrated:
                markdown_blocks = (
                    f"\n\n## Current Preferences\n{current_prefs or '(empty)'}"
                    f"\n\n## Current Projects\n{current_projects or '(empty)'}"
                )
            from personalclaw.prompt_providers.runtime import render_use_case_prompt

            prompt = (
                render_use_case_prompt(
                    "memory_consolidation",
                    {
                        "numbered_keys": numbered,
                        "semantic_block": semantic_block,
                        "markdown_blocks": markdown_blocks,
                        "conversation": conversation,
                    },
                )
                or ""
            )

            result = await self._call_llm(prompt)
            if not result:
                return

            if entry := result.get("history_entry"):
                memory.append_history(entry)
                logger.info("Consolidated %d messages for %s", len(unconsolidated), key)
                # Session working memory (M5c): reuse this distilled summary as
                # the always-injected rolling session memory — one distillation
                # pass, not a second summarizer (decision #5). scope=session, so
                # it's injected every turn for THIS session and swept on seal.
                try:
                    self._svc.write_working_memory(key, entry)
                except Exception:
                    logger.debug("working-memory write failed for %s", key, exc_info=True)

            # Structured memory writes
            if self._svc.has_vector:
                self._write_structured_memory(result, key)

            # Markdown writes (skipped when migrated to structured memory)
            if not self._migrated:
                if prefs := result.get("preferences_update"):
                    if prefs.strip() != current_prefs.strip():
                        memory.write_preferences(prefs)

                if projects := result.get("projects_update"):
                    if projects.strip() != current_projects.strip():
                        memory.write_projects(projects)

            if (self._lesson_store or self._svc.has_vector) and (
                raw_lessons := result.get("lessons")
            ):
                self._save_lessons(raw_lessons)

            # Agent self-persona + commitments (M5e) — agent-scoped. The agent
            # name is normalized to the canonical default when the session didn't
            # pin one (the common dashboard case), so capture keys on the SAME
            # string the context read path uses — otherwise writes and reads
            # disagree and nothing is ever surfaced. Best-effort; never blocks the
            # rest of consolidation.
            if self._svc.has_vector and include_history:
                from personalclaw.agents.defaults import normalize_agent_name

                agent = normalize_agent_name(meta.get("agent"))
                self._write_self_persona(result, agent)
                if self._proactive_commitments:
                    self._write_commitments(result, agent, key)

            # Auto skill creation / refinement.
            # Guarded by flag + eligibility — failures are logged, never fatal.
            if auto_skills_eligible:
                try:
                    self._process_auto_skills(result, key)
                except Exception:
                    logger.warning("Auto-skill processing failed for %s", key, exc_info=True)

            # Only advance the consolidated offset for history consolidation.
            # Prefs-only consolidation uses a separate in-memory offset.
            if include_history:
                self._log.mark_consolidated(key, total)
                # Autonomous self-learning: periodically promote repeated episodic
                # memories to durable semantic facts. Piggybacks on consolidation
                # (no new scheduler), guarded so a flood/stack can't happen.
                try:
                    self._maybe_promote_episodic(memory)
                except Exception:
                    logger.warning("Episodic promotion failed for %s", key, exc_info=True)
                # Category-TTL sweep: age out short-lived categorized memories
                # (debug/event/decision) on the same maintenance cadence. Durable
                # facts/prefs + user_explicit globals are never touched.
                try:
                    expired = self._svc.expire_by_category()
                    if expired:
                        logger.info("Category-TTL expired %d memory record(s)", expired)
                except Exception:
                    logger.debug("Category-TTL sweep failed for %s", key, exc_info=True)
                # Heat-gated promotion (M5c): the conservative GLOBAL gate — promote
                # in-scope records that earned cross-session heat to scope=global.
                # Runs HERE (maintenance cadence), never at session-end, so global
                # never fills with one-off session noise.
                try:
                    promoted_scope = self._svc.promote_by_heat()
                    if promoted_scope:
                        logger.info("Heat-promoted %d record(s) to global scope", promoted_scope)
                except Exception:
                    logger.debug("Heat promotion failed for %s", key, exc_info=True)
                # Failure-pattern synthesis (M5d): collapse clusters of same-root-
                # cause procedural failures into one prior so the class never
                # bloats into a tool-call log. The anti-noise mechanism.
                try:
                    synth = self._svc.synthesize_failures()
                    if synth:
                        logger.info("Synthesized %d procedural failure prior(s)", synth)
                except Exception:
                    logger.debug("Failure synthesis failed for %s", key, exc_info=True)
                # Daily-digest nodes (mem-tree, descoped): roll up each completed
                # day's episodic activity into one 'what happened on day D' record.
                # Idempotent (keyed by date) + extractive by default, so it adds no
                # LLM cost to the maintenance cadence.
                try:
                    digested = self._svc.build_daily_digest()
                    if digested:
                        logger.info("Built %d daily-digest node(s)", digested)
                except Exception:
                    logger.debug("Daily-digest build failed for %s", key, exc_info=True)

        except Exception:
            logger.exception("Consolidation failed for %s", key)
            raise
        finally:
            self._running.discard(key)

    def _maybe_promote_episodic(self, memory) -> None:
        """Run autonomous episodic→semantic promotion every Nth consolidation.

        Anti-runaway: gated on the every-N counter AND a 30-min min-interval AND a
        cross-process single-flight lock (so the gateway + CLI can't both promote
        at once) AND a per-run cap. Each run is SEL-audited (no silent caps).
        """
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().memory
        if not getattr(cfg, "auto_promote_enabled", True):
            return
        from personalclaw.memory_service import service_for

        svc = service_for(memory)
        if not svc.can_vector_search:
            return
        self._consolidation_count += 1
        if self._consolidation_count % max(1, cfg.auto_promote_every_n) != 0:
            return
        now = _time.monotonic()
        if self._last_promote_monotonic and now - self._last_promote_monotonic < 1800:
            return  # min-interval floor (30 min) regardless of consolidation rate

        from personalclaw.concurrency import single_flight

        with single_flight("mem-promote-episodic") as acquired:
            if not acquired:
                return  # another process is already promoting
            self._last_promote_monotonic = now
            promoted = svc.promote_episodic_patterns(max_promotions=cfg.auto_promote_max_per_run)
        if promoted:
            logger.info("Autonomous promotion: %d episodic→semantic", promoted)
            try:
                sel().log_api_access(
                    caller="consolidator:auto_promote",
                    operation="memory.promote_episodic",
                    outcome="allowed",
                    resources=f"promoted={promoted}",
                )
            except Exception:
                logger.debug("SEL audit failed for auto-promotion", exc_info=True)

    def _save_lessons(self, raw: object) -> None:
        """Save extracted lessons from consolidation result."""
        if not isinstance(raw, list):
            return

        # Prefer the record store (dedup-aware) over JSONL
        if self._svc.has_vector:
            count = 0
            for item in raw:
                if isinstance(item, dict) and item.get("rule"):
                    ok = self._svc.write_lesson(
                        rule=item["rule"],
                        category=item.get("category", "knowledge"),
                        negative=item.get("negative"),
                        source="consolidation",
                    )
                    if ok:
                        count += 1
            if count:
                logger.info("Extracted %d lesson(s) from chat (record store)", count)
            return

        if not self._lesson_store:
            return
        from datetime import timezone as _tz

        from personalclaw.learn import Lesson

        count = 0
        for item in raw:
            if isinstance(item, dict) and item.get("rule"):
                self._lesson_store.save(
                    Lesson(
                        ts=datetime.now(tz=_tz.utc).isoformat(),
                        rule=item["rule"],
                        category=item.get("category", "knowledge"),
                        negative=item.get("negative"),
                    )
                )
                count += 1
        if count:
            logger.info("Extracted %d lesson(s) from chat", count)

    def _write_structured_memory(self, result: dict, key: str) -> None:
        """Write semantic + episodic entries from consolidation result."""
        if not self._svc.has_vector:
            return
        from personalclaw.vector_memory import (
            _MAX_EPISODIC_PER_CONSOLIDATION,
            _MAX_SEMANTIC_PER_CONSOLIDATION,
        )

        source = f"consolidation:{key}"

        # Semantic entries
        semantic_items = result.get("semantic")
        if isinstance(semantic_items, list):
            written = 0
            deleted = 0
            for item in semantic_items[:_MAX_SEMANTIC_PER_CONSOLIDATION]:
                if not isinstance(item, dict) or "key" not in item:
                    continue
                # Handle deletion of stale keys
                if item.get("delete"):
                    if self._svc.delete_semantic(item["key"], source):
                        deleted += 1
                    continue
                conf = float(item.get("confidence", 0.5))
                # Confidence 1.0 means user explicitly stated it — escalate source
                # so it can overwrite previous user_explicit entries
                item_source = "user_explicit" if conf >= 1.0 else source
                err = self._svc.set_semantic(
                    item["key"],
                    item.get("value"),
                    conf,
                    item_source,
                )
                if err is None:
                    written += 1
            if written or deleted:
                logger.info("Semantic consolidation: %d written, %d deleted", written, deleted)

        # Episodic entries
        episodic_items = result.get("episodic")
        if isinstance(episodic_items, list):
            written = 0
            for item in episodic_items[:_MAX_EPISODIC_PER_CONSOLIDATION]:
                if not isinstance(item, dict) or "text" not in item:
                    continue
                ep_ok = self._svc.write_episodic(
                    item["text"],
                    conversation_id=key,
                    tags=item.get("tags", []),
                    importance=float(item.get("importance", 0.5)),
                    source=source,
                )
                if ep_ok:
                    written += 1
            if written:
                logger.info("Wrote %d episodic entries from consolidation", written)

    def _write_self_persona(self, result: dict, agent: str) -> None:
        """Write extracted agent self-persona traits (M5e), scoped to ``agent``.

        Best-effort: a positive self-model injected always-on for this agent.
        Each trait is redacted + bounded; recurrence reinforces heat via the
        service's per-trait key."""
        traits = result.get("self_persona")
        if not isinstance(traits, list):
            return
        written = 0
        for trait in traits[:4]:
            if not isinstance(trait, str):
                continue
            safe, _ = redact_exfiltration_urls(trait.strip()[:120])
            safe, _ = redact_credentials(safe)
            if not safe:
                continue
            try:
                if self._svc.record_persona(agent=agent, trait=safe):
                    written += 1
            except Exception:
                logger.debug("self_persona write failed", exc_info=True)
        if written:
            logger.info("Wrote %d self-persona trait(s) for agent %s", written, agent)

    def _write_commitments(self, result: dict, agent: str, key: str) -> None:
        """Write extracted proactive commitments (M5e — O-A4), GUARDRAILED.

        Only reached when the user opted in (``_proactive_commitments``). The
        service enforces the real guardrails (enabled + confidence>=0.8 + per-day
        cap); this path supplies them and redacts the check-in text. The channel
        is the heartbeat deliver-target (``dashboard:<bare-session-name>``) so the
        check-in lands back in the originating conversation; the consolidation key
        is the FULL session key (``dashboard:chat-…``), so strip its prefix before
        re-prefixing or the deliver-target doubles (``dashboard:dashboard:…``) and
        the session never resolves."""
        commitments = result.get("commitments")
        if not isinstance(commitments, list):
            return
        channel = f"dashboard:{key.removeprefix('dashboard:').removeprefix('dashboard_')}"
        max_per_day = self._proactive_commitments_max
        written = 0
        for item in commitments[:max_per_day]:
            if not isinstance(item, dict):
                continue
            text = item.get("text", "")
            due = item.get("due_window", "")
            if not isinstance(text, str) or not isinstance(due, str):
                continue
            try:
                conf = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            safe, _ = redact_exfiltration_urls(text.strip()[:300])
            safe, _ = redact_credentials(safe)
            if not safe or not due:
                continue
            try:
                if self._svc.record_commitment(
                    agent=agent,
                    channel=channel,
                    text=safe,
                    due_window=due,
                    confidence=conf,
                    enabled=True,
                    max_per_day=max_per_day,
                ):
                    written += 1
            except Exception:
                logger.debug("commitment write failed", exc_info=True)
        if written:
            logger.info("Recorded %d proactive commitment(s) for agent %s", written, agent)

    def _process_auto_skills(self, result: dict, key: str) -> None:
        """Extract + write auto-generated skills from the consolidation result.

        Handles both ``new_skill`` and ``refined_skill`` result keys.  Each
        is validated, redacted via ``security.redact_*``, then deduped
        against existing skills (for new creation) before being written
        through ``SkillsLoader``.  Every successful write emits a SEL audit
        event via ``sel().log_tool_invocation``.
        """
        if self._skills_loader is None:
            return

        def _redact(text: object) -> str:
            """Run the same two-pass redaction used for channel/dashboard output."""
            if not isinstance(text, str):
                return ""
            safe, _ = redact_exfiltration_urls(text)
            safe, _ = redact_credentials(safe)
            return safe

        # Create path
        new_skill = result.get("new_skill")
        if isinstance(new_skill, dict):
            slug = str(new_skill.get("slug", "")).strip()
            description = _redact(new_skill.get("description", ""))
            triggers = _redact(new_skill.get("triggers", ""))
            procedure_md = _redact(new_skill.get("procedure_md", ""))
            if not (slug and description and procedure_md):
                # Required fields missing (or stripped empty by redaction).
                # Audit the rejection so operators can see that a create
                # attempt happened but lacked the minimum inputs.
                logger.info(
                    "Auto-skill create skipped: empty slug/description/procedure "
                    "after redaction (slug=%r)",
                    slug,
                )
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_create",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={
                        "slug": slug or "(empty)",
                        "reason": "empty_after_redaction",
                    },
                )
            else:
                similar = self._skills_loader.find_similar(
                    description, threshold=self._auto_similarity_threshold
                )
                if similar:
                    logger.info(
                        "Auto-skill synthesis skipped: '%s' overlaps existing skill '%s'",
                        slug,
                        similar,
                    )
                    sel().log_tool_invocation(
                        session_key=key,
                        tool_name="auto_skill_create",
                        tool_kind="skills",
                        outcome="rejected",
                        metadata={
                            "slug": slug,
                            "reason": "similar_exists",
                            "existing": similar,
                        },
                    )
                else:
                    # Propose-only (skill-evolution-proposal-only): autonomous
                    # synthesis NEVER writes live — it enqueues a human-reviewable
                    # proposal. A person accepts (→ live auto/ skill) or rejects it
                    # from the Skill-proposals inbox. No auto-install path exists.
                    from personalclaw.skills import proposals as _proposals

                    prop = _proposals.enqueue(
                        slug=slug,
                        description=description,
                        triggers=triggers,
                        procedure_md=procedure_md,
                        session_key=key,
                        created_at=AutoSkillProvenance.now_iso(),
                        source_excerpt=procedure_md,
                    )
                    if prop is not None:
                        logger.info("Queued skill proposal %s from session %s", prop.id, key)
                        sel().log_tool_invocation(
                            session_key=key,
                            tool_name="auto_skill_propose",
                            tool_kind="skills",
                            outcome="invoked",
                            metadata={"proposal_id": prop.id, "slug": slug},
                        )
                    else:
                        logger.info(
                            "Auto-skill proposal rejected for slug '%s' (queue full/invalid)",
                            slug,
                        )
                        sel().log_tool_invocation(
                            session_key=key,
                            tool_name="auto_skill_propose",
                            tool_kind="skills",
                            outcome="rejected",
                            metadata={"slug": slug, "reason": "queue_full_or_invalid"},
                        )

        # Refine path (only if explicitly enabled)
        if not self._auto_refine_enabled:
            return
        refined = result.get("refined_skill")
        if isinstance(refined, dict):
            name = str(refined.get("name", "")).strip()
            if not self._skills_loader.is_auto_generated(name):
                logger.info("Auto-skill refine rejected for %s: not in auto namespace", name)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "not_auto_namespace"},
                )
                return
            description = _redact(refined.get("description", ""))
            triggers = _redact(refined.get("triggers", ""))
            procedure_md = _redact(refined.get("procedure_md", ""))
            if not description or not procedure_md:
                logger.info(
                    "Auto-skill refine skipped for %s: empty description/procedure "
                    "after redaction",
                    name,
                )
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "empty_after_redaction"},
                )
                return
            # Refresh the human-facing reuse_count snapshot from the sidecar
            # usage counter (the live source of truth is skills/usage.py; the
            # frontmatter just mirrors it at the already-rewriting refine seam).
            try:
                from personalclaw.skills.usage import SkillUsageStore

                _reuse = SkillUsageStore().get(name).count
            except Exception:
                _reuse = 0
            provenance = AutoSkillProvenance(
                session_key=key,
                created_at=AutoSkillProvenance.now_iso(),
                refined_at=AutoSkillProvenance.now_iso(),
                reuse_count=_reuse,
            )
            ok = self._skills_loader.update_auto_skill(
                name,
                description=description,
                triggers=triggers,
                procedure_md=procedure_md,
                provenance=provenance,
            )
            if ok:
                logger.info("Auto-refined skill %s from session %s", name, key)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="invoked",
                    metadata={"name": name},
                )
            else:
                # update_auto_skill returned False: oversized procedure,
                # file missing, or other internal rejection.  Audit it so
                # operators can trace why a refine was proposed but not
                # applied.
                logger.info("Auto-skill refine rejected for %s (update_failed)", name)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "update_failed"},
                )

    async def _call_llm(self, prompt: str) -> dict | None:
        """Call LLM for consolidation via the persistent background session.

        Uses the shared background ACP agent process (no spawn/teardown cost).
        Returns parsed JSON dict or None on failure.
        """
        if not self._sessions:
            logger.warning("LLM consolidation skipped — no session manager")
            return None

        from personalclaw.llm_helpers import stream_and_collect_json

        session_key = BACKGROUND_KEY
        try:
            client, _is_new, _resumed = await self._sessions.get_or_create(
                session_key, agent="personalclaw-lite"
            )
            return await stream_and_collect_json(client, prompt)
        except Exception:
            logger.warning("LLM consolidation call failed", exc_info=True)
            return None
        finally:
            self._sessions.release(session_key)
            await self._sessions.recycle_background()
