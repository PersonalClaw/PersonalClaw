"""Session health detector — scans recent gateway.log / security_events.jsonl for stalled sessions.

A session is "stalled" when the UI would show it as working but the underlying agent has
already failed silently. Detected patterns:

* ``subagent_timeout``  — ``Injected timeout error for subagent ... into session KEY``
* ``prompt_stuck``      — ``ACP error in session KEY: ... 'Prompt already in progress'``

Note: ``Session KEY has dead provider — removing stale entry`` is NOT a stall signal.
It's emitted during healthy self-cleanup: the session manager detected a dead provider
and removed the stale entry so the next request cold-starts a fresh session. The user
sees no interruption.
"""

import datetime as _dt
import os
import re
import time
from pathlib import Path

# Only consider log lines from the last STALL_WINDOW_SECONDS to avoid flagging
# long-resolved incidents. 10 minutes is wide enough to catch a stuck session
# before the user would have noticed, narrow enough to avoid stale reports.
STALL_WINDOW_SECONDS = 600
_LOG_TAIL_BYTES = 256 * 1024  # scan last 256 KB of gateway.log — cheap

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("subagent_timeout", re.compile(r"Injected timeout error for subagent \S+ into session (\S+)")),
    ("prompt_stuck", re.compile(r"ACP error in session (\S+):.*Prompt already in progress")),
]

# gateway.log lines start with "HH:MM:SS " (local time). We can't reliably
# parse that to an absolute ts, so instead we use file mtime as upper bound
# and rely on _LOG_TAIL_BYTES to keep the window small.
_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) ")


def _read_tail(path: Path, nbytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
                f.readline()  # discard possibly-partial line
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _normalize_session(raw: str) -> str:
    """Strip trailing punctuation; handle 'dashboard:' prefix uniformly."""
    s = raw.rstrip(":;,.")
    return s.split("dashboard:", 1)[-1] if s.startswith("dashboard:") else s


def _line_age_seconds(line: str, file_mtime: float) -> float:
    """Estimate how many seconds ago a log line was written.

    Gateway.log lines start with ``HH:MM:SS``. We compute the delta between
    the line's time-of-day and the file mtime's time-of-day. This breaks
    across midnight but is good enough for a 10-minute window.
    """
    m = _TS_RE.match(line)
    if not m:
        return float("inf")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    line_sod = h * 3600 + mi * 60 + s  # seconds-of-day for the log line
    mtime_dt = _dt.datetime.fromtimestamp(file_mtime)
    mtime_sod = mtime_dt.hour * 3600 + mtime_dt.minute * 60 + mtime_dt.second
    delta = mtime_sod - line_sod
    if delta < 0:
        delta += 86400  # crossed midnight
    return delta


def compute_session_health(log_path: Path | None = None, now: float | None = None) -> dict[str, dict]:
    """Return ``{session_name: {reason, since_ts}}`` for sessions flagged as stalled."""
    if log_path is None:
        home = Path(os.environ.get("PERSONALCLAW_HOME") or Path.home() / ".personalclaw")
        log_path = home / "gateway.log"
    if not log_path.exists():
        return {}
    if now is None:
        now = time.time()

    try:
        file_mtime = log_path.stat().st_mtime
    except OSError:
        return {}

    tail = _read_tail(log_path, _LOG_TAIL_BYTES)
    if not tail:
        return {}

    out: dict[str, dict] = {}
    for line in tail.splitlines():
        age_from_mtime = _line_age_seconds(line, file_mtime)
        wall_age = (now - file_mtime) + age_from_mtime
        if wall_age > STALL_WINDOW_SECONDS:
            continue  # too old
        for reason, pat in _PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            session = _normalize_session(m.group(1))
            if session.startswith("_") or session.startswith("cron_") or session.startswith("cron:"):
                continue
            since_ts = file_mtime - age_from_mtime
            out[session] = {"reason": reason, "since_ts": since_ts}
    return out
