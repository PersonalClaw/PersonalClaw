"""Security Event Log — immutable, tamper-evident audit trail for tool invocations.

Records structured JSON events for every tool/MCP action with:
- Timestamp (ISO 8601 UTC)
- Caller identity (session key, agent, source interface)
- Operation type (tool_call, tool_approved, tool_rejected, tool_denied, mcp_call)
- Resources affected (tool name, tool kind, arguments summary)
- Outcome (approved, rejected, denied, completed, failed)
- Downstream service (MCP server name if applicable)
- HMAC-SHA256 integrity chain (each entry signs over previous hash)

Storage: ``~/.personalclaw/security_events.jsonl`` (append-only JSONL)
Retention: configurable, default 365 days.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


def _default_dir() -> Path:
    """Resolve the SEL log directory at instantiation time.

    Honors ``PERSONALCLAW_HOME`` so containerized deployments writing to
    ``/data`` see their logs persisted to the mounted volume rather than
    the entrypoint-seeded ``/home/personalclaw/.personalclaw`` directory.
    """
    override = os.environ.get("PERSONALCLAW_HOME")
    if override:
        p = Path(override).expanduser().resolve()
        if p != Path("/") and p.parts[:2] not in (("/", "usr"), ("/", "System"), ("/", "etc")):
            return p
    return Path.home() / ".personalclaw"


_SEL_FILE = "security_events.jsonl"
_RETENTION_DAYS = 365
_HMAC_KEY_FILE = "sel_hmac.key"
_MAX_ARG_LEN = 500
# Default tamper-check window: verify the most recent N entries instead of the
# whole (unbounded, append-only) chain, so the audit UI stays responsive.
_VERIFY_WINDOW = 5000
# Hard size cap for the on-disk log. The chain is append-only and high-rate, so a
# size bound (not just age) keeps reads/verify fast. Comfortably above the verify
# window so a prune never erases the whole verifiable tail.
_MAX_ENTRIES = 50000


@dataclass
class SecurityEvent:
    """A single auditable security event."""

    event_id: str
    timestamp: str  # ISO 8601 UTC
    event_type: str  # tool_invocation, tool_approval, tool_denial, mcp_call, api_access
    caller_identity: str  # session key or user identifier
    agent: str  # agent name (personalclaw, custom, etc.)
    source: str  # channel, dashboard, cli, cron, subagent, background
    operation: str  # tool name or API operation
    tool_kind: str = ""  # execute_bash, fs_write, mcp, etc.
    outcome: str = ""  # approved, rejected, denied, completed, failed
    resources: str = ""  # affected resources summary (truncated)
    downstream_service: str = ""  # MCP server name if applicable
    request_id: str = ""  # ACP permission request ID
    error: str = ""
    prev_hash: str = ""  # HMAC chain — hash of previous entry
    entry_hash: str = ""  # HMAC of this entry (computed on write)
    metadata: dict = field(default_factory=dict)


class SecurityEventLog:
    """Append-only, HMAC-chained security event log.

    Thread-safe. Singleton pattern — all callers share one instance.
    """

    _instance: "SecurityEventLog | None" = None
    _init_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, base_dir: Path | None = None) -> "SecurityEventLog":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def __init__(self, base_dir: Path | None = None) -> None:
        if self._initialized:
            return
        self._dir = base_dir or _default_dir()
        self._path = self._dir / _SEL_FILE
        self._lock = threading.Lock()
        self._hmac_key = self._load_or_create_hmac_key()
        self._last_hash = self._read_last_hash()
        self._forward_callback: Callable[[dict], None] | None = None
        self._initialized = True

    def set_forward_callback(self, callback: Callable[[dict], None] | None) -> None:
        """Register an optional callback to forward events to a centralized log system."""
        with self._lock:
            self._forward_callback = callback

    def _load_or_create_hmac_key(self) -> bytes:
        key_path = self._dir / _HMAC_KEY_FILE
        self._dir.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return key_path.read_bytes()
        key = os.urandom(32)
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key

    def _read_last_hash(self) -> str:
        if not self._path.exists():
            return ""
        try:
            # Read last non-empty line
            with open(self._path, "rb") as f:
                f.seek(0, 2)
                pos = f.tell()
                if pos == 0:
                    return ""
                # Scan backward for last newline
                buf = b""
                while pos > 0:
                    pos = max(pos - 4096, 0)
                    f.seek(pos)
                    buf = f.read() + buf
                    lines = buf.split(b"\n")
                    for line in reversed(lines):
                        line = line.strip()
                        if line:
                            data = json.loads(line)
                            return data.get("entry_hash", "")
            return ""
        except Exception:
            return ""

    def _tail_lines(self, max_lines: int) -> list[str]:
        """Return up to ``max_lines`` trailing non-empty lines, reading only the
        end of the file. The SEL log is append-only and grows without bound (every
        gateway/channel/mcp action appends), so reads MUST stay O(tail) — never load
        the whole file just to show recent events or sample-verify the chain.
        """
        if not self._path.exists():
            return []
        try:
            with open(self._path, "rb") as f:
                f.seek(0, 2)
                pos = f.tell()
                buf = b""
                newlines = 0
                # Read backward in chunks until we've seen enough line breaks (one
                # extra so the first captured line is whole), or hit the start.
                while pos > 0 and newlines <= max_lines:
                    step = min(65536, pos)
                    pos -= step
                    f.seek(pos)
                    buf = f.read(step) + buf
                    newlines = buf.count(b"\n")
            lines = [ln.strip() for ln in buf.split(b"\n")]
            text_lines = [ln.decode("utf-8", "replace") for ln in lines if ln]
            return text_lines[-max_lines:]
        except Exception:
            return []

    def _compute_hash(self, event: SecurityEvent) -> str:
        # Hash over all fields except entry_hash itself
        d = asdict(event)
        d.pop("entry_hash", None)
        payload = json.dumps(d, sort_keys=True).encode()
        return hmac.new(self._hmac_key, payload, hashlib.sha256).hexdigest()

    def log(self, event: SecurityEvent) -> None:
        """Append an event to the log with HMAC chain integrity."""
        with self._lock:
            event.prev_hash = self._last_hash
            event.entry_hash = self._compute_hash(event)
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event)) + "\n")
            self._last_hash = event.entry_hash
            callback = self._forward_callback
        if callback:
            try:
                from personalclaw.security import redact

                def _redact_deep(obj: object) -> object:
                    if isinstance(obj, str):
                        return redact(obj)
                    if isinstance(obj, dict):
                        return {k: _redact_deep(v) for k, v in obj.items()}
                    if isinstance(obj, (list, tuple)):
                        return type(obj)(_redact_deep(i) for i in obj)
                    return obj

                callback(_redact_deep(asdict(event)))  # type: ignore[arg-type]
            except Exception:
                logger.warning("forward_callback failed", exc_info=True)

    def log_tool_invocation(
        self,
        *,
        session_key: str,
        agent: str = "personalclaw",
        source: str = "",
        tool_name: str,
        tool_kind: str = "",
        outcome: str,
        request_id: str | int = "",
        downstream_service: str = "",
        resources: str = "",
        error: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Convenience: log a tool invocation event."""
        self.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="tool_invocation",
                caller_identity=session_key,
                agent=agent,
                source=source or _infer_source(session_key),
                operation=tool_name,
                tool_kind=tool_kind,
                outcome=outcome,
                request_id=str(request_id),
                downstream_service=downstream_service,
                resources=resources[:_MAX_ARG_LEN] if resources else "",
                error=error[:_MAX_ARG_LEN] if error else "",
                metadata=metadata or {},
            )
        )

    def log_api_access(
        self,
        *,
        caller: str,
        operation: str,
        outcome: str,
        source: str = "dashboard",
        resources: str = "",
        error: str = "",
    ) -> None:
        """Convenience: log a dashboard/API access event."""
        self.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="api_access",
                caller_identity=caller,
                agent="",
                source=source,
                operation=operation,
                outcome=outcome,
                resources=resources[:_MAX_ARG_LEN] if resources else "",
                error=error[:_MAX_ARG_LEN] if error else "",
            )
        )

    def verify_integrity(self, max_entries: int | None = _VERIFY_WINDOW) -> tuple[int, int]:
        """Verify the per-entry HMAC chain. Returns (checked_entries, valid_entries).

        An entry is counted as valid when its standalone HMAC matches the
        recomputed payload digest. We tolerate `prev_hash` mismatches (chain
        breaks) silently because PersonalClaw's gateway, channel, and mcp
        processes each write to the same log without IPC; their writes
        interleave and the per-process ``_last_hash`` doesn't survive
        cross-process ordering. The HMAC over each individual record is still
        verifiable so a single tampered record stands out clearly.

        The SEL log is append-only and unbounded — every gateway/channel/mcp action
        appends an entry — so a full walk is O(n) and grows without limit (it had
        reached >1M entries, taking 20s+ and hanging the audit UI). By default we
        verify only the most recent ``max_entries`` (the live-tamper-detection
        window); pass ``max_entries=None`` for an exhaustive offline check.
        """
        if not self._path.exists():
            return 0, 0
        if max_entries is None:
            lines: list[str] = [
                ln.strip()
                for ln in self._path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        else:
            lines = self._tail_lines(max_entries)
        checked = 0
        valid = 0
        for line in lines:
            checked += 1
            try:
                data = json.loads(line)
                stored_hash = data.pop("entry_hash", "")
                payload = json.dumps(data, sort_keys=True).encode()
                expected = hmac.new(self._hmac_key, payload, hashlib.sha256).hexdigest()
                if hmac.compare_digest(stored_hash, expected):
                    valid += 1
                else:
                    logger.warning("SEL HMAC mismatch at entry %d", checked)
            except (json.JSONDecodeError, Exception):
                logger.warning("SEL parse error at entry %d", checked)
        return checked, valid

    def rotate(self, archive: bool = True) -> dict:
        """Rotate the SEL log to start a fresh HMAC chain.

        When ``archive`` is True (default) the existing log is renamed with a
        timestamp suffix; otherwise it is deleted. Use this to clear a chain
        break and start a clean HMAC chain. Returns a dict with the
        before/after entry count and the archive path (if any).
        """
        from datetime import datetime, timezone

        with self._lock:
            entries_before = 0
            archive_path: Path | None = None
            if self._path.exists():
                try:
                    entries_before = sum(
                        1
                        for line in self._path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                except OSError:
                    entries_before = 0
                if archive:
                    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    archive_path = self._path.with_name(
                        f"{self._path.stem}.{ts}.bak{self._path.suffix}"
                    )
                    try:
                        self._path.rename(archive_path)
                    except OSError:
                        archive_path = None
                        self._path.unlink(missing_ok=True)
                else:
                    self._path.unlink(missing_ok=True)
            self._last_hash = ""
        return {
            "rotated": True,
            "entries_before": entries_before,
            "entries_after": 0,
            "archive_path": str(archive_path) if archive_path else "",
        }

    def recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent events (newest first), reading only the file tail."""
        result: list[dict] = []
        for line in reversed(self._tail_lines(limit)):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(result) >= limit:
                break
        return result

    def prune(self, keep_days: int = _RETENTION_DAYS, max_entries: int = _MAX_ENTRIES) -> int:
        """Trim the log. Returns the number of entries removed.

        Two bounds, both applied (whichever drops more wins per entry):
        - age: drop entries older than ``keep_days``.
        - size: keep at most the newest ``max_entries``.

        The size cap is the real defense — the log is append-only and high-rate
        (every gateway/channel/mcp action, including dashboard polls, appends), so an
        age-only prune still lets the file grow to millions of entries within the
        retention window and makes reads/verify crawl. Pass ``max_entries<=0`` to
        disable the size cap.
        """
        if not self._path.exists():
            return 0
        from datetime import timedelta

        cutoff_str = (datetime.now(tz=timezone.utc) - timedelta(days=keep_days)).isoformat()

        lines = self._path.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        removed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("timestamp", "") < cutoff_str:
                    removed += 1
                    continue
            except json.JSONDecodeError:
                removed += 1
                continue
            kept.append(line)

        # Size cap: keep only the newest max_entries (entries are appended in order).
        if max_entries > 0 and len(kept) > max_entries:
            removed += len(kept) - max_entries
            kept = kept[-max_entries:]

        if removed:
            with self._lock:
                atomic_write(self._path, "\n".join(kept) + "\n" if kept else "")
                self._last_hash = self._read_last_hash()
            logger.info(
                "SEL pruned %d entries (keep_days=%d, max_entries=%d)",
                removed,
                keep_days,
                max_entries,
            )
        return removed


def _infer_source(session_key: str) -> str:
    """Infer the source interface from a session key."""
    if session_key.startswith("dashboard:"):
        return "dashboard"
    if session_key.startswith("cron:"):
        return "cron"
    if session_key.startswith("subagent:"):
        return "subagent"
    if session_key == "_bg":
        return "background"
    if session_key == "cli_chat":
        return "cli"
    return "channel"


def sel() -> SecurityEventLog:
    """Module-level accessor for the singleton SEL instance."""
    return SecurityEventLog()
