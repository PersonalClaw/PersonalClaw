"""Persistent session-to-ACP-agent mapping.

Stores ``session_map.json`` mapping session keys to ACP agent session IDs,
with channel thread linkage for bidirectional sync.
"""

import json
import logging

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader


def config_dir():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_SESSION_MAP_FILE = "session_map.json"


def transcript_path(session_id: str):
    """Resolve a session id to its ``sessions/<sid>.jsonl`` transcript, or None.

    Resolves the home dir on EVERY call rather than at import time, for the same
    reason the deleted ``_sessions_dir`` helper did (it was a module-level constant
    bound at import time, and its last two readers — the ``<sid>.json`` gates in
    :meth:`SessionMap.get` and :meth:`SessionMap.prune` — are gone): a caller that set
    ``PERSONALCLAW_HOME`` after this module was first imported — every test, and any
    process that boots the gateway lazily — would otherwise be handed a path under the
    real home. Returns None for an empty/traversing id or a missing file, so a caller
    reads "no transcript" rather than opening something it did not name.
    """
    sid = (session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
        return None
    path = config_loader.config_dir() / "sessions" / f"{sid}.jsonl"
    return path if path.exists() else None


def read_transcript(session_id: str) -> list[dict]:
    """Parsed transcript records for *session_id*, newest last. ``[]`` when unreadable.

    Skips unparseable lines rather than raising: a transcript is append-only history
    written by several code paths over time, and one bad line must not cost the whole
    session. Metadata lines are KEPT — ``template_pipeline.mine_session`` reads the
    ``_type == "metadata"`` line for the session title.
    """
    path = transcript_path(session_id)
    if path is None:
        return []
    records: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("transcript unreadable for %s", session_id, exc_info=True)
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


class SessionMap:
    """Persistent mapping of session_key → ACP agent session ID.

    Stored as ``~/.personalclaw/session_map.json``. Atomic write via tmp+rename.
    Only used for long-lived conversational sessions (channel DM, dashboard).
    Stateless sessions (cron, subagent) are excluded.

    Each entry is a dict with keys: ``sid``, ``thread_ts``, ``channel_id``, and, for a session
    linked to a thread, ``channel_provider``: the channel the thread is on (``"telegram"``), where
    the session's answers go. A reverse index ``_thread_to_session`` maps thread_ts → session_key
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
        """Return the stored ACP agent session ID for *key*, or None.

        Handles the dashboard history key round-trip: the original session key
        ``dashboard:chat-1-xxx`` becomes ``dashboard_chat-1-xxx`` on disk (via
        ``_safe_key``), and when resumed from history the session name becomes
        ``dashboard_chat-1-xxx``, producing session key
        ``dashboard:dashboard_chat-1-xxx``.  We try the canonical form too.

        A stored id is returned as-is. This used to additionally require
        ``sessions/<sid>.json`` to exist and would DELETE the mapping when it
        didn't — and nothing anywhere writes that file, so the branch fired every
        time: it reported "no mapping" for a mapping it had just destroyed, which
        is why ``resume_sid=None`` was unexplainable from the logs (G5/O16) and
        why protocol resume could never happen even once the client stopped
        gating on its own copy of the same missing file (`G156`). Whether an id is
        still loadable is the AGENT's answer, not a local file's: ``AcpClient``
        sends ``session/load`` and falls back to ``session/new`` on refusal, so a
        dead id costs one rejected request instead of a silently erased mapping.
        """
        entry = self._data.get(key)
        # Fallback: dashboard history round-trip (dashboard:dashboard_X → dashboard:X)
        if not entry and key.startswith("dashboard:dashboard_"):
            canonical = "dashboard:" + key[len("dashboard:dashboard_") :]
            entry = self._data.get(canonical)
        if not entry:
            return None
        return entry.get("sid") or None

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

    def forget_session_id(self, key: str) -> None:
        """Forget the agent session id stored for *key*, and keep its channel link.

        A recycled session must not resume the agent session it replaced, so the id goes with
        what describes it (provider, cwd). A channel link stays: the chat is still that thread's
        chat, and its answers still go there. An entry with no link is removed.
        """
        entry = self._data.get(key)
        if not entry:
            return
        if not entry.get("thread_ts"):
            self._remove_entry(key)
            return
        kept = {"sid": "", "thread_ts": entry["thread_ts"], "channel_id": entry.get("channel_id")}
        if entry.get("channel_provider"):
            kept["channel_provider"] = entry["channel_provider"]
        self._data[key] = kept
        self._save()

    def prune(self) -> int:
        """Remove entries that name nothing — no session id AND no channel thread.

        Called once at ``SessionManager.start_pool``. It used to also drop every
        entry whose ``sessions/<sid>.json`` was missing, and since that file has no
        writer (see :meth:`get`) the effect was to wipe the whole map on every
        gateway start — the mapping a mid-conversation restart needs was gone before
        the first turn could ask for it. An id that the agent no longer holds is
        handled where the answer lives: ``session/load`` is refused and the client
        falls back to ``session/new``.
        """
        stale = [
            k
            for k, entry in self._data.items()
            if not entry.get("sid") and not entry.get("thread_ts")
        ]
        for k in stale:
            del self._data[k]
        if stale:
            self._rebuild_thread_index()
            self._save()
            logger.info("Pruned %d stale session map entries", len(stale))
        return len(stale)

    def set_channel_link(
        self, key: str, thread_ts: str, channel_id: str | None, *, channel_provider: str = ""
    ) -> None:
        """Link a session to a channel thread, or with an empty *thread_ts* unlink it. Creates
        the entry if needed.

        *channel_provider* names the channel the thread is on (``"telegram"``): a thread's id
        means nothing without the channel that issued it, and the session's answers go to that
        channel (:meth:`get_channel_provider`). A link that names none says only which thread;
        an unlink drops the channel with the thread.

        The thread index says which session a thread continues (:meth:`get_session_for_thread`),
        and reading the file again rebuilds it in the file's order, the last entry naming a
        thread winning. So a changed entry is written last: the session that linked a thread
        most recently is the one the thread continues, before a restart and after it. Its old
        thread leaves the index only while the index still names it, since another session may
        have linked that thread since, and an empty thread is never indexed.
        """
        provider = channel_provider if thread_ts else ""
        entry = self._data.get(key)
        if entry:
            if (
                entry.get("thread_ts") == thread_ts
                and entry.get("channel_id") == channel_id
                and (entry.get("channel_provider") or "") == provider
            ):
                if thread_ts:
                    self._thread_to_session.setdefault(thread_ts, key)
                return
            old_ts = entry.get("thread_ts")
            if old_ts and old_ts != thread_ts and self._thread_to_session.get(old_ts) == key:
                del self._thread_to_session[old_ts]
            del self._data[key]
            entry["thread_ts"] = thread_ts
            entry["channel_id"] = channel_id
        else:
            entry = {"sid": "", "thread_ts": thread_ts, "channel_id": channel_id}
        if provider:
            entry["channel_provider"] = provider
        else:
            entry.pop("channel_provider", None)
        self._data[key] = entry
        if thread_ts:
            self._thread_to_session[thread_ts] = key
        self._save()

    def get_channel_link(self, key: str) -> tuple[str | None, str | None]:
        """Return (thread_ts, channel_id) for a session."""
        entry = self._data.get(key)
        if not entry:
            return None, None
        return entry.get("thread_ts"), entry.get("channel_id")

    def get_channel_provider(self, key: str) -> str:
        """The channel a session's thread is on, as its link names it (``"slack"``), or ``""``
        when the session is on no thread or its link names no channel."""
        entry = self._data.get(key)
        provider = entry.get("channel_provider") if entry and entry.get("thread_ts") else ""
        return provider if isinstance(provider, str) else ""

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
