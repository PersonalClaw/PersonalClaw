"""Attempt-level JSONL audit trail for the model-call chokepoint (§2.1).

One line per ATTEMPT (not per request) in ``~/.personalclaw/model_calls.jsonl`` —
so a request that retried once and then fell back writes three lines sharing one
``audit_id``. This is harness mechanics (a file under the config dir), NOT a
memory entry or knowledge item (§7 memory/knowledge boundary): nothing here
writes to ``memory.db`` / ``knowledge.db``.

The file is append-mostly with a trim at 2× the line cap (the ``notifications.jsonl``
pattern the plan cites): each write appends, and when the file crosses ``2 × cap``
lines it is rewritten to the last ``cap`` lines. Trimming at 2× rather than every
write keeps the hot background path append-only in the common case.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.guardrails.failure import FailureMode

logger = logging.getLogger(__name__)

_AUDIT_FILENAME = "model_calls.jsonl"
_LINE_CAP = 5000  # keep the most recent N attempts; trim triggers at 2× this


def _audit_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _AUDIT_FILENAME


@dataclass
class AttemptRecord:
    """One attempt's audit row. Field order is the on-disk column order."""

    audit_id: str
    ts: float
    use_case: str
    provider: str
    model: str
    attempt: int
    failure_mode: str = FailureMode.NONE.value
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    dollars_est: float = 0.0
    estimated: bool = False  # dollars/tokens are heuristic, not provider-reported
    passed: bool = False
    strategy: str = "direct"  # direct | retry | fallback
    degraded: bool = False  # satisfied only by a fallback ref (discount downstream)
    # The routing query class (MODEL-ROUTING-TELEMETRY §2) this attempt served, from the
    # pure classifier — "" when routing/classification didn't run. The stats layer folds
    # per (use_case, query_class), so it's a first-class column, not an ``extra`` field.
    query_class: str = ""
    extra: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        d = asdict(self)
        # ``extra`` is spread inline so ad-hoc fields read like columns; drop it
        # when empty so the common row stays lean.
        extra = d.pop("extra", None) or {}
        d.update(extra)
        return json.dumps(d, separators=(",", ":"), default=str)


def now_ms() -> float:
    """Monotonic-ish millisecond clock for latency spans (wall clock for ``ts``)."""
    return time.monotonic() * 1000.0


def record_attempt(rec: AttemptRecord) -> None:
    """Append one attempt row, trimming the file when it crosses 2× the cap.

    Best-effort and never raises: an audit-write failure must not break a model
    call (the call is the product; the audit is observability). Failures log at
    WARNING so a broken trail is diagnosable rather than silent.
    """
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = rec.to_json_line() + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _maybe_trim(path)
    except Exception:
        logger.warning("model-call audit write failed", exc_info=True)


def _maybe_trim(path: Path) -> None:
    """Rewrite ``path`` to its last ``_LINE_CAP`` lines once it exceeds 2× the cap."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= 2 * _LINE_CAP:
        return
    kept = lines[-_LINE_CAP:]
    atomic_write(path, "".join(kept))


def read_recent(limit: int = 1000) -> list[dict]:
    """Return up to ``limit`` most-recent attempt rows (oldest→newest), parsed.

    Powers the derived health view (§2.5). Malformed lines are skipped, not
    fatal — a partially-written tail must not blank the whole panel.
    """
    path = _audit_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    rows: list[dict] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows
