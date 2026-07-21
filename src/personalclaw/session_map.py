"""Persistent session-to-ACP-agent mapping.

Stores ``session_map.json`` mapping session keys to ACP agent session IDs,
with channel thread linkage for bidirectional sync.
"""

import json
import logging

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".personalclaw"


logger = logging.getLogger(__name__)

_SESSION_MAP_FILE = "session_map.json"

# ACP agent session file directory
_SESSIONS_DIR = _path_home_pclaw() / "sessions"


class SessionMap:
    """Persistent mapping of session_key → ACP agent session ID.

    Stored as ``~/.personalclaw/session_map.json``. Atomic write via tmp+rename.
    Only used for long-lived conversational sessions (channel DM, dashboard).
    Stateless sessions (cron, subagent) are excluded.

    Each entry is a dict with keys: ``sid``, ``thread_ts``, ``channel_id``.
    A reverse index ``_thread_to_session`` maps thread_ts → session_key
    for bidirectional sync lookups.
    """

    def __init__(self) -> None:
        self._path = config_dir() / _SESSION_MAP_FILE
        self._data: dict[str, dict] = {}  # key → {"sid", "thread_ts", "channel_id"}
        self._thread_to_session: dict[str, str] = {}  # thread_ts → session_key
        self._load()

    def _load(self) -> None:
        self._thread_to_session.clear()
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._data = {}
                return
            if not isinstance(raw, dict):
                self._data = {}
                return
            for key, val in raw.items():
                if isinstance(val, dict) and "sid" in val:
                    self._data[key] = val
                else:
                    continue  # skip corrupt entries
            self._rebuild_thread_index()
        else:
            self._data = {}

    def _rebuild_thread_index(self) -> None:
        """Rebuild _thread_to_session from current _data."""
        self._thread_to_session.clear()
        for key, entry in self._data.items():
            ts = entry.get("thread_ts")
            if ts:
                self._thread_to_session[ts] = key

    def _save(self) -> None:
        atomic_write(self._path, json.dumps(self._data))

    def get(self, key: str) -> str | None:
        """Return ACP agent session ID if mapping exists and .json file is present.

        Handles the dashboard history key round-trip: the original session key
        ``dashboard:chat-1-xxx`` becomes ``dashboard_chat-1-xxx`` on disk (via
        ``_safe_key``), and when resumed from history the session name becomes
        ``dashboard_chat-1-xxx``, producing session key
        ``dashboard:dashboard_chat-1-xxx``.  We try the canonical form too.
        """
        entry = self._data.get(key)
        # Fallback: dashboard history round-trip (dashboard:dashboard_X → dashboard:X)
        matched_key = key
        if not entry and key.startswith("dashboard:dashboard_"):
            canonical = "dashboard:" + key[len("dashboard:dashboard_") :]
            entry = self._data.get(canonical)
            if entry:
                matched_key = canonical
        if not entry:
            return None
        sid = entry["sid"]
        if sid and (_SESSIONS_DIR / f"{sid}.json").exists():
            jsonl = _SESSIONS_DIR / f"{sid}.jsonl"
            try:
                jsonl_size = jsonl.stat().st_size
            except FileNotFoundError:
                jsonl_size = 0
            if jsonl_size < 10:
                logger.info("Session %s has empty JSONL — pruning stale entry for %s", sid, key)
                self._remove_entry(matched_key)
                return None
            return sid
        if sid:
            self._remove_entry(matched_key)
        return None

    def _remove_entry(self, key: str) -> None:
        """Remove an entry and update reverse index."""
        entry = self._data.pop(key, None)
        if entry:
            ts = entry.get("thread_ts")
            if ts and self._thread_to_session.get(ts) == key:
                del self._thread_to_session[ts]
            self._save()

    def set(self, key: str, sid: str, *, provider: str = "", cwd: str = "") -> None:
        """Save mapping and persist to disk, preserving existing channel-link fields."""
        existing = self._data.get(key)
        if existing:
            existing["sid"] = sid
            if provider:
                existing["provider"] = provider
            if cwd:
                existing["cwd"] = cwd
        else:
            entry: dict = {"sid": sid, "thread_ts": None, "channel_id": None}
            if provider:
                entry["provider"] = provider
            if cwd:
                entry["cwd"] = cwd
            self._data[key] = entry
        self._save()

    def get_cwd(self, key: str) -> str:
        """Return the stored CWD for *key*, or '' if not set."""
        entry = self._data.get(key)
        if not entry:
            return ""
        return entry.get("cwd", "")

    def get_provider(self, key: str) -> str:
        """Return the stored provider for *key* (e.g. 'acp'), or ''."""
        entry = self._data.get(key)
        if not entry:
            return ""
        return entry.get("provider", "")

    def delete(self, key: str) -> None:
        """Remove mapping and persist."""
        self._remove_entry(key)

    def prune(self) -> int:
        """Remove entries whose session files no longer exist."""
        stale = [
            k
            for k, entry in self._data.items()
            if (entry.get("sid") and not (_SESSIONS_DIR / f"{entry['sid']}.json").exists())
            or (not entry.get("sid") and not entry.get("thread_ts"))
        ]
        for k in stale:
            del self._data[k]
        if stale:
            self._rebuild_thread_index()
            self._save()
            logger.info("Pruned %d stale session map entries", len(stale))
        return len(stale)

    def set_channel_link(self, key: str, thread_ts: str, channel_id: str | None) -> None:
        """Link a session to a channel thread. Creates entry if needed."""
        entry = self._data.get(key)
        if entry:
            if entry.get("thread_ts") == thread_ts and entry.get("channel_id") == channel_id:
                self._thread_to_session.setdefault(thread_ts, key)
                return
            old_ts = entry.get("thread_ts")
            if old_ts and old_ts != thread_ts:
                self._thread_to_session.pop(old_ts, None)
            entry["thread_ts"] = thread_ts
            entry["channel_id"] = channel_id
        else:
            self._data[key] = {
                "sid": "",
                "thread_ts": thread_ts,
                "channel_id": channel_id,
            }
        self._thread_to_session[thread_ts] = key
        self._save()

    def get_channel_link(self, key: str) -> tuple[str | None, str | None]:
        """Return (thread_ts, channel_id) for a session."""
        entry = self._data.get(key)
        if not entry:
            return None, None
        return entry.get("thread_ts"), entry.get("channel_id")

    def get_session_for_thread(self, thread_ts: str) -> str | None:
        """Return the session key linked to a channel thread_ts, or None."""
        return self._thread_to_session.get(thread_ts)

    def find_key_by_sid(self, session_id: str) -> str | None:
        """Find the session map key for a given ACP agent session ID."""
        for k, entry in self._data.items():
            sid = entry.get("sid") if isinstance(entry, dict) else entry
            if sid == session_id:
                return k
        return None
