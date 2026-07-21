"""Unified Loop persistence — one SQLite ``loops`` table + a per-``<id>`` file dir,
serving every :class:`personalclaw.loop.loop.LoopKind`.

Schema is deliberately LEAN so a new kind never needs a migration: the shared
spine fields are real columns; list/dict fields are JSON-text columns; and
everything type-specific lives in a single ``kind_config`` JSON blob. The
file-dir layout + helpers (findings, guidance, per-task guidance, plan session,
nudges, questions, stop sentinel) are the union of the former loops/ + code/
stores, since the unified manager/watchdog need all of them.

Lives under ``config_dir()/loop/`` (distinct from the legacy ``loops/`` dir) so
the two coexist until the engine cutover deletes the legacy package.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.loop.loop import KINDS, TERMINAL_STATUSES, Loop, LoopStatus
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_LOOP_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STOP_SENTINEL = "STOP"

# JSON-encoded columns (lists/dicts) vs scalar columns. kind_config is a dict blob.
_LIST_COLS = ("plan", "roster", "skill_ids", "workflow_ids", "linked_task_ids")
_DICT_COLS = ("phase_status", "strategy_config", "task_list_ids", "kind_config")


class TransitionError(RuntimeError):
    """Raised on an illegal status transition (e.g. out of a terminal state)."""


# ── paths ──


def _loops_root() -> Path:
    return config_dir() / "loop"


def _db_path() -> Path:
    return _loops_root() / "loops.db"


def valid_loop_id(loop_id: str) -> bool:
    return bool(_LOOP_ID_RE.match(loop_id or ""))


def loop_dir(loop_id: str) -> Path | None:
    """The loop's file dir (created), or None if the id is invalid. Re-resolves
    under the root + confirms containment so a crafted id can't escape."""
    if not valid_loop_id(loop_id):
        return None
    root = _loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root):
        return None
    d.mkdir(parents=True, exist_ok=True)
    (d / "findings").mkdir(exist_ok=True)
    (d / "verdicts").mkdir(exist_ok=True)
    return d


def safe_loop_dir(loop_id: str) -> Path | None:
    """Read-only variant — never creates."""
    if not valid_loop_id(loop_id):
        return None
    root = _loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root) or not d.exists():
        return None
    return d


# ── redaction ──


def _redact_str(s: str) -> str:
    cleaned, _ = redact_credentials(s)
    cleaned, _ = redact_exfiltration_urls(cleaned)
    return cleaned


def _redact_value(val: Any) -> Any:
    if isinstance(val, str):
        return _redact_str(val)
    if isinstance(val, list):
        return [_redact_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


def redact_finding(finding: dict) -> dict:
    """Redact credentials + exfiltration URLs from a worker-authored finding.
    Tolerates a non-dict (returns {}) so one malformed file can't poison a list."""
    if not isinstance(finding, dict):
        return {}
    return {k: _redact_value(v) for k, v in finding.items()}


def _redact_loop(row: dict) -> dict:
    """Redact the free-text + capability fields a worker/LLM could echo a secret
    into (task/summary/success_criteria/error + kind_config text)."""
    out = dict(row)
    for k in ("task", "summary", "success_criteria", "error_message", "name"):
        if isinstance(out.get(k), str):
            out[k] = _redact_str(out[k])
    if isinstance(out.get("kind_config"), dict):
        out["kind_config"] = _redact_value(out["kind_config"])
    if isinstance(out.get("plan"), list):
        out["plan"] = _redact_value(out["plan"])
    return out


# ── connection + schema ──


def _connect() -> sqlite3.Connection:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.DatabaseError:
        logger.debug("could not set WAL/busy_timeout pragmas", exc_info=True)
    conn.execute("""CREATE TABLE IF NOT EXISTS loops (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'goal',
            task TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            intake_rigor TEXT NOT NULL DEFAULT 'auto',
            plan TEXT NOT NULL DEFAULT '[]',
            phase_status TEXT NOT NULL DEFAULT '{}',
            execution TEXT NOT NULL DEFAULT 'solo',
            agent TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            provider_agent TEXT NOT NULL DEFAULT '',
            reasoning_effort TEXT NOT NULL DEFAULT '',
            roster TEXT NOT NULL DEFAULT '[]',
            strategy_id TEXT NOT NULL DEFAULT 'orchestrator',
            strategy_config TEXT NOT NULL DEFAULT '{}',
            skill_ids TEXT NOT NULL DEFAULT '[]',
            workflow_ids TEXT NOT NULL DEFAULT '[]',
            workspace_dir TEXT NOT NULL DEFAULT '',
            auto_teardown_on_complete INTEGER NOT NULL DEFAULT 0,
            attended INTEGER NOT NULL DEFAULT 0,
            autopilot INTEGER NOT NULL DEFAULT 1,
            max_cycles INTEGER NOT NULL DEFAULT 30,
            idle_secs INTEGER NOT NULL DEFAULT 120,
            success_criteria TEXT,
            kind_config TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ready',
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            total_cycles INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            tasks_project_id TEXT NOT NULL DEFAULT '',
            task_list_ids TEXT NOT NULL DEFAULT '{}',
            linked_task_ids TEXT NOT NULL DEFAULT '[]',
            session_key TEXT NOT NULL DEFAULT ''
        )""")
    # Idempotent column migrations for DBs created before a field was added (the
    # CREATE above is IF NOT EXISTS, so an existing table won't gain new columns).
    _ensure_columns(
        conn,
        {
            "auto_teardown_on_complete": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    conn.commit()
    return conn


def _ensure_columns(conn: sqlite3.Connection, cols: dict[str, str]) -> None:
    """ALTER TABLE ADD COLUMN for any missing column (SQLite has no ADD-IF-NOT-EXISTS)."""
    try:
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(loops)").fetchall()}
    except sqlite3.DatabaseError:
        return
    for name, decl in cols.items():
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE loops ADD COLUMN {name} {decl}")
            except sqlite3.DatabaseError:
                logger.debug("could not add column %s", name, exc_info=True)


# Columns in INSERT order (everything except the JSON-encoded ones, which are
# handled by serializing the matching Loop fields).
_SCALAR_COLS = (
    "id",
    "name",
    "kind",
    "task",
    "project_id",
    "summary",
    "intake_rigor",
    "execution",
    "agent",
    "model",
    "provider",
    "provider_agent",
    "reasoning_effort",
    "strategy_id",
    "workspace_dir",
    "auto_teardown_on_complete",
    "attended",
    "autopilot",
    "max_cycles",
    "idle_secs",
    "success_criteria",
    "status",
    "created_at",
    "started_at",
    "completed_at",
    "elapsed_seconds",
    "total_cycles",
    "error_message",
    "tasks_project_id",
    "session_key",
)
_JSON_COLS = _LIST_COLS + _DICT_COLS


def _row_to_loop(row: sqlite3.Row) -> Loop:
    d: dict[str, Any] = dict(row)
    for col in _JSON_COLS:
        raw = d.get(col)
        try:
            d[col] = (
                json.loads(raw)
                if isinstance(raw, str)
                else (raw or ({} if col in _DICT_COLS else []))
            )
        except (json.JSONDecodeError, TypeError):
            d[col] = {} if col in _DICT_COLS else []
    d["attended"] = bool(d.get("attended", 0))
    d["autopilot"] = bool(d.get("autopilot", 1))
    d["auto_teardown_on_complete"] = bool(d.get("auto_teardown_on_complete", 0))
    return Loop.from_dict(d)


def _loop_to_params(loop: Loop) -> dict[str, Any]:
    d = loop.to_dict()
    params: dict[str, Any] = {}
    for col in _SCALAR_COLS:
        v = d.get(col)
        if col in ("attended", "autopilot"):
            v = int(bool(v))
        params[col] = v
    for col in _JSON_COLS:
        params[col] = json.dumps(d.get(col, {} if col in _DICT_COLS else []))
    return params


# ── CRUD ──


def create(loop: Loop) -> Loop:
    """Insert a new loop (assigning an id + created_at if unset). Validates kind."""
    if loop.kind not in KINDS:
        raise ValueError(f"unknown loop kind: {loop.kind!r}")
    if not loop.id:
        import uuid

        loop.id = uuid.uuid4().hex[:8]
    if not loop.created_at:
        loop.created_at = time.time()
    conn = _connect()
    try:
        params = _loop_to_params(loop)
        cols = list(params)
        conn.execute(
            f"INSERT INTO loops ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    # Materialize the file dir + initial status.json so the worker has its interface.
    loop_dir(loop.id)
    write_status(loop.id, LoopStatus(loop.status))
    return loop


def get(loop_id: str) -> Loop | None:
    if not valid_loop_id(loop_id):
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM loops WHERE id = ?", (loop_id,)).fetchone()
        return _row_to_loop(row) if row else None
    finally:
        conn.close()


def list_all() -> list[Loop]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM loops ORDER BY created_at DESC").fetchall()
        return [_row_to_loop(r) for r in rows]
    finally:
        conn.close()


def list_for_project(project_id: str) -> list[Loop]:
    """Every loop scoped under ``project_id`` (the Projects-primary loop history),
    newest first. Matches BOTH project_id (explicit user scope) and tasks_project_id
    (the auto-provisioned backing project a project-less launch / a task-provisioning
    Code loop gets) — both mean "this loop lives under the project". Without the
    tasks_project_id arm, such loops were missing from the project's loop history (the
    detail page, the chat preamble's loop list, the sibling-loops brief footer), an
    inconsistency with /api/projects/{id}/linked which already matches both."""
    if not project_id:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM loops WHERE project_id = ? OR tasks_project_id = ? "
            "ORDER BY created_at DESC",
            (project_id, project_id),
        ).fetchall()
        return [_row_to_loop(r) for r in rows]
    finally:
        conn.close()


def update_status(loop_id: str, new_status: LoopStatus, **fields: Any) -> Loop:
    """Transition status + stamp timing. Raises TransitionError out of a terminal
    state, KeyError if missing. Banks the just-finished running stretch into
    elapsed_seconds whenever we LEAVE running (so displayed time excludes pauses);
    sets started_at on entering RUNNING (+ clears stale error); completed_at on a
    finished state. Extra ``fields`` are written through (JSON-encoded if needed)."""
    if not valid_loop_id(loop_id):
        raise KeyError(loop_id)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT status, started_at, elapsed_seconds FROM loops WHERE id = ?", (loop_id,)
        ).fetchone()
        if row is None:
            raise KeyError(loop_id)
        current = LoopStatus(row["status"])
        if current in TERMINAL_STATUSES and new_status != current:
            raise TransitionError(f"{current.value} -> {new_status.value}")
        now = time.time()
        sets = ["status = ?"]
        vals: list[Any] = [new_status.value]
        if current == LoopStatus.RUNNING and new_status != LoopStatus.RUNNING:
            started = row["started_at"]
            if started is not None:
                prior = row["elapsed_seconds"] or 0.0
                sets.append("elapsed_seconds = ?")
                vals.append(float(prior) + max(0.0, now - float(started)))
        if new_status == LoopStatus.RUNNING:
            sets.append("started_at = ?")
            vals.append(now)
            fields.setdefault("error_message", None)
        if new_status in (LoopStatus.COMPLETE, LoopStatus.STOPPED, LoopStatus.FAILED):
            sets.append("completed_at = ?")
            vals.append(now)
        for key, value in fields.items():
            if key in _JSON_COLS:
                value = json.dumps(value)
            elif key in ("attended", "autopilot"):
                value = int(bool(value))
            sets.append(f"{key} = ?")
            vals.append(value)
        vals.append(loop_id)
        conn.execute(f"UPDATE loops SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()
    write_status(loop_id, new_status)
    out = get(loop_id)
    if out is None:
        raise KeyError(loop_id)
    return out


# Spec fields a pre-launch loop may edit (the rest are engine-managed).
_EDITABLE_SPEC_COLS = frozenset(
    {
        "name",
        "task",
        "summary",
        "intake_rigor",
        "plan",
        "execution",
        "agent",
        "model",
        "provider",
        "provider_agent",
        "reasoning_effort",
        "roster",
        "strategy_id",
        "strategy_config",
        "skill_ids",
        "workflow_ids",
        "workspace_dir",
        "attended",
        "autopilot",
        "max_cycles",
        "idle_secs",
        "success_criteria",
        "kind_config",
    }
)


def update_spec(loop_id: str, fields: dict) -> Loop | None:
    """Patch editable spec fields on a PRE-LAUNCH loop. Returns None if the loop
    is missing OR its spec is frozen (already started) — the caller routes a
    name-only patch to :func:`rename` instead."""
    from personalclaw.loop.loop import PRELAUNCH_STATUSES

    loop = get(loop_id)
    if loop is None:
        return None
    if LoopStatus(loop.status) not in PRELAUNCH_STATUSES:
        return None
    patch = {k: v for k, v in fields.items() if k in _EDITABLE_SPEC_COLS}
    if not patch:
        return loop
    conn = _connect()
    try:
        sets, vals = [], []
        for key, value in patch.items():
            if key in _JSON_COLS:
                value = json.dumps(value)
            elif key in ("attended", "autopilot"):
                value = int(bool(value))
            sets.append(f"{key} = ?")
            vals.append(value)
        vals.append(loop_id)
        conn.execute(f"UPDATE loops SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()
    return get(loop_id)


def rebind_workspace(loop_id: str, workspace_dir: str) -> Loop | None:
    """Re-bind the workspace dir on a NON-terminal loop whose spec is otherwise
    frozen — the recovery path when a brownfield workspace went missing mid-run and
    the loop paused to NEEDS_INPUT/BLOCKED (see launch_blocker / reaper / nudge guards).
    Allowed in any non-terminal state EXCEPT running (a live worker holds the cwd);
    returns None if missing, running, or terminal. Path-safety is the caller's gate."""
    loop = get(loop_id)
    if loop is None:
        return None
    from personalclaw.loop.loop import TERMINAL_STATUSES

    if LoopStatus(loop.status) in TERMINAL_STATUSES or loop.status == LoopStatus.RUNNING.value:
        return None
    _simple_set(loop_id, "workspace_dir", str(workspace_dir or "").strip())
    return get(loop_id)


def rename(loop_id: str, name: str) -> Loop | None:
    """Metadata-only rename, allowed in ANY state (the spec freeze doesn't cover
    the display name). Blank = no-op."""
    name = (name or "").strip()[:200]
    if not name or not valid_loop_id(loop_id):
        return get(loop_id)
    conn = _connect()
    try:
        cur = conn.execute("UPDATE loops SET name = ? WHERE id = ?", (name, loop_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get(loop_id)


def set_project(loop_id: str, project_id: str) -> None:
    _simple_set(loop_id, "project_id", project_id)


def set_total_cycles(loop_id: str, total: int) -> None:
    _simple_set(loop_id, "total_cycles", int(total))


def set_session_key(loop_id: str, session_key: str) -> None:
    _simple_set(loop_id, "session_key", session_key)


def set_autopilot(loop_id: str, on: bool) -> Loop | None:
    if get(loop_id) is None:
        return None
    _simple_set(loop_id, "autopilot", int(bool(on)))
    return get(loop_id)


def _simple_set(loop_id: str, col: str, value: Any) -> None:
    if not valid_loop_id(loop_id):
        return
    conn = _connect()
    try:
        conn.execute(f"UPDATE loops SET {col} = ? WHERE id = ?", (value, loop_id))
        conn.commit()
    finally:
        conn.close()


def set_phase_status(loop_id: str, phase_key: str, state: str) -> dict:
    """Set one phase's status in the phase_status map; returns the updated map."""
    loop = get(loop_id)
    if loop is None:
        return {}
    ps = dict(loop.phase_status or {})
    ps[phase_key] = state
    _simple_set(loop_id, "phase_status", json.dumps(ps))
    return ps


def merge_kind_config(loop_id: str, patch: dict) -> dict:
    """Deep-merge ``patch`` into the loop's kind_config (RUNTIME state, status-agnostic —
    unlike update_spec which freezes post-launch). The design worker writes token overrides
    while the loop RUNS, so this is the path that persists them. Returns the merged config."""
    loop = get(loop_id)
    if loop is None:
        return {}

    def _deep(base: dict, over: dict) -> dict:
        out = dict(base)
        for k, v in (over or {}).items():
            out[k] = _deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out

    merged = _deep(dict(loop.kind_config or {}), patch or {})
    _simple_set(loop_id, "kind_config", json.dumps(merged))
    return merged


def set_tasks_links(
    loop_id: str, *, tasks_project_id: str = "", task_list_ids: dict | None = None
) -> Loop | None:
    loop = get(loop_id)
    if loop is None:
        return None
    conn = _connect()
    try:
        if tasks_project_id:
            conn.execute(
                "UPDATE loops SET tasks_project_id = ? WHERE id = ?", (tasks_project_id, loop_id)
            )
        if task_list_ids is not None:
            conn.execute(
                "UPDATE loops SET task_list_ids = ? WHERE id = ?",
                (json.dumps(task_list_ids), loop_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get(loop_id)


def link_tasks(loop_id: str, task_ids: list[str]) -> list[str]:
    """Append decomposed task ids to linked_task_ids (deduped, order-stable)."""
    loop = get(loop_id)
    if loop is None:
        return []
    merged = list(dict.fromkeys([*(loop.linked_task_ids or []), *task_ids]))
    _simple_set(loop_id, "linked_task_ids", json.dumps(merged))
    return merged


def queue_tasks(loop_id: str, task_ids: list[str]) -> list[str]:
    """Append task ids to the code-kind queue (kind_config['queued_task_ids'])."""
    return _mutate_queue(loop_id, lambda q: list(dict.fromkeys([*q, *task_ids])))


def unqueue_tasks(loop_id: str, task_ids: list[str]) -> list[str]:
    drop = set(task_ids)
    return _mutate_queue(loop_id, lambda q: [t for t in q if t not in drop])


def _mutate_queue(loop_id: str, fn) -> list[str]:
    loop = get(loop_id)
    if loop is None:
        return []
    cfg = dict(loop.kind_config or {})
    cfg["queued_task_ids"] = fn(list(cfg.get("queued_task_ids", []) or []))
    _simple_set(loop_id, "kind_config", json.dumps(cfg))
    return cfg["queued_task_ids"]


def delete(loop_id: str) -> bool:
    if not valid_loop_id(loop_id):
        return False
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM loops WHERE id = ?", (loop_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    d = safe_loop_dir(loop_id)
    if d is not None:
        import shutil

        shutil.rmtree(d, ignore_errors=True)
    return deleted


# ── redacted views ──


def get_redacted(loop_id: str) -> dict | None:
    loop = get(loop_id)
    if loop is None:
        return None
    view = _redact_loop(loop.to_dict())
    view["findings"] = get_findings(loop_id)
    view["nudges"] = get_nudges(loop_id)
    view["pending_question"] = pending_question(loop_id)
    # The third-party judge's per-cycle verdicts + the marginal-value trail back the
    # cockpit's ROI rail / verdict nodes (open-ended goals). The FE reads these off
    # the redacted view for the INITIAL render — without them the rail paints empty
    # until a live cycle_verdict event happens to land. Ported from the legacy loops
    # redacted view; empty for kinds that write no verdict (verifiable/monitor/code).
    view["verdicts"] = get_verdicts(loop_id)
    view["marginal_scores"] = get_marginal_scores(loop_id)
    # The loop's own on-disk dir — where brief/findings live and where a worker writes
    # doc deliverables (REPORT.md/MONITOR_LOG.md, idea/design artifacts) when no
    # workspace is bound. The cockpit roots its file tree + embedded terminal here when
    # workspace_dir is empty, so those docs are viewable. NOT redacted (a server-local
    # path); reported even before the dir's first write (the file API tolerates a
    # not-yet-created dir). Ported from the legacy code redacted view.
    view["files_dir"] = str(loop_dir(loop_id) or "")
    return view


def read_deliverable(loop_id: str) -> str:
    """The loop's document deliverable (redacted), by the kind's declared name —
    falling back across the known deliverable docs + the FINDINGS.md log so the
    cockpit's report panel is never blank while a loop warms up. Kind-agnostic: the
    name comes from the strategy (goal → REPORT.md/MONITOR_LOG.md; others → none)."""
    d = safe_loop_dir(loop_id)
    if d is None:
        return ""
    loop = get(loop_id)
    candidates: list[str] = []
    if loop is not None:
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        strat = kinds.get_or_none(loop.kind)
        namer = getattr(strat, "deliverable_name", None) if strat else None
        name = (namer(loop) if namer else "") or ""
        if name:
            candidates.append(name)
    candidates += ["REPORT.md", "MONITOR_LOG.md", "DESIGN.md", "FINDINGS.md"]
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        p = d / name
        try:
            if p.exists():
                return _redact_str(p.read_text())
        except OSError:
            continue
    return ""


def read_log(loop_id: str) -> str:
    """The worker's cumulative FINDINGS.md log (redacted; empty if none yet)."""
    d = safe_loop_dir(loop_id)
    if d is None:
        return ""
    p = d / "FINDINGS.md"
    try:
        return _redact_str(p.read_text()) if p.exists() else ""
    except OSError:
        return ""


def list_redacted(project_id: str = "", kind: str = "") -> list[dict]:
    """The list view: redacted rows (newest first), optionally filtered by project /
    kind. Attaches findings to each row so the per-card finding count + latest-insight
    preview match the detail view (the FE cards read ``findings.length`` + the last
    finding directly). Ported from both legacy engines. Unlike a per-row get_redacted
    it does NOT touch the filesystem per loop (no files_dir / dir creation) — the list
    path stays lean even with many loops."""
    loops = list_for_project(project_id) if project_id else list_all()
    out: list[dict] = []
    for loop in loops:
        if kind and loop.kind != kind:
            continue
        d = _redact_loop(loop.to_dict())
        d["findings"] = get_findings(loop.id)
        # The dashboard ActiveWork widget renders the loop's question inline for
        # needs_input rows. Gate the per-row fs read on that status so the list
        # stays lean (a needs_input loop is the rare, blocked-on-you case).
        if loop.status == LoopStatus.NEEDS_INPUT.value:
            d["pending_question"] = pending_question(loop.id)
        out.append(d)
    return out


# ── file-based worker interface (status / brief / guidance / findings / …) ──


def write_status(loop_id: str, status: LoopStatus, **extra: Any) -> None:
    """Mirror status to status.json — the cycle gate the worker reads each turn.
    Atomic with the DB write's intent (the SQLite row is authoritative)."""
    d = loop_dir(loop_id)
    if d is None:
        return
    payload = {"status": status.value, "ts": time.time(), **extra}
    atomic_write(d / "status.json", json.dumps(payload, indent=2))


def write_brief(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "brief.md").write_text(text)


def write_guidance(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "guidance.txt").write_text(text)


def read_guidance(loop_id: str) -> str:
    d = safe_loop_dir(loop_id)
    f = d / "guidance.txt" if d else None
    return f.read_text() if f and f.exists() else ""


def clear_guidance(loop_id: str) -> bool:
    d = safe_loop_dir(loop_id)
    f = d / "guidance.txt" if d else None
    if f and f.exists():
        f.unlink()
        return True
    return False


# Per-task guidance (parallel code-kind workers read their own file).
def valid_task_guidance_id(task_id: str) -> bool:
    return bool(_TASK_ID_RE.match(task_id or ""))


def _task_guidance_name(task_id: str) -> str | None:
    return f"guidance_{task_id}.txt" if valid_task_guidance_id(task_id) else None


def write_task_guidance(loop_id: str, task_id: str, text: str) -> None:
    name = _task_guidance_name(task_id)
    d = loop_dir(loop_id)
    if d is not None and name:
        (d / name).write_text(text)


def read_task_guidance(loop_id: str, task_id: str) -> str:
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    f = d / name if (d and name) else None
    return f.read_text() if f and f.exists() else ""


def clear_task_guidance(loop_id: str, task_id: str) -> None:
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    f = d / name if (d and name) else None
    if f and f.exists():
        f.unlink()


# Stop sentinel.
def stop_sentinel_path(loop_id: str) -> Path | None:
    d = loop_dir(loop_id)
    return (d / STOP_SENTINEL) if d is not None else None


def write_stop_sentinel(loop_id: str) -> None:
    p = stop_sentinel_path(loop_id)
    if p is not None:
        p.write_text("stop")


def clear_stop_sentinel(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    p = d / STOP_SENTINEL if d else None
    if p and p.exists():
        p.unlink()


# Findings (sequential cycle_NNN.json + parallel task_<id>_NNN.json).
def get_findings(loop_id: str) -> list[dict]:
    d = safe_loop_dir(loop_id)
    if d is None:
        return []
    fdir = d / "findings"
    if not fdir.exists():
        return []

    def _cycle_idx(p: Path) -> tuple[int, str]:
        m = re.search(r"(\d+)", p.stem)
        return (int(m.group(1)) if m else 0, p.name)

    cycle_files = sorted(fdir.glob("cycle_*.json"), key=_cycle_idx)
    task_files = sorted(fdir.glob("task_*.json"), key=lambda p: p.stat().st_mtime)
    out: list[dict] = []
    for f in [*cycle_files, *task_files]:
        try:
            parsed = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(parsed, dict):
            continue
        finding = redact_finding(parsed)
        raw_tid = finding.get("task_id")
        if isinstance(raw_tid, str):
            finding["task_id"] = raw_tid.strip() or None
        elif raw_tid is not None:
            finding["task_id"] = None
        if not finding.get("task_id"):
            name = f.name
            if name.startswith("task_") and "_" in name[5:]:
                tid = name[5:].rsplit("_", 1)[0]
                if tid:
                    finding["task_id"] = tid
        out.append(finding)
    return out


def task_finding_count(loop_id: str, task_id: str) -> int:
    if not valid_task_guidance_id(task_id):
        return 0
    d = safe_loop_dir(loop_id)
    if d is None or not (d / "findings").is_dir():
        return 0
    try:
        return sum(1 for _ in (d / "findings").glob(f"task_{task_id}_*.json"))
    except OSError:
        return 0


# Questions (attended-mode clarification).
def write_question(loop_id: str, question: str, **extra: Any) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "questions.json").write_text(
            json.dumps({"question": question, "ts": time.time(), **extra}, indent=2)
        )


def pending_question(loop_id: str) -> dict | None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if not f or not f.exists():
        return None
    try:
        q = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(q, dict):
        return None
    for k in ("question", "why"):
        if isinstance(q.get(k), str):
            q[k] = _redact_str(q[k])
    return q


def clear_question(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if f and f.exists():
        f.unlink()


# Nudges (durable steer trail).
def append_nudge(loop_id: str, text: str, sent_at_cycle: int) -> None:
    d = loop_dir(loop_id)
    if d is None:
        return
    p = d / "nudges.json"
    try:
        log = json.loads(p.read_text()) if p.exists() else []
        if not isinstance(log, list):
            log = []
    except (json.JSONDecodeError, OSError):
        log = []
    log.append(
        {
            "text": text,
            "sent_at": time.time(),
            "sent_at_cycle": sent_at_cycle,
            "applied_cycle": None,
        }
    )
    atomic_write(p, json.dumps(log, indent=2))


def mark_nudges_applied(loop_id: str, cycle: int) -> None:
    d = safe_loop_dir(loop_id)
    p = d / "nudges.json" if d else None
    if not p or not p.exists():
        return
    try:
        log = json.loads(p.read_text())
        if not isinstance(log, list):
            return
    except (json.JSONDecodeError, OSError):
        return
    changed = False
    for n in log:
        if (
            isinstance(n, dict)
            and n.get("applied_cycle") is None
            and int(n.get("sent_at_cycle", 0)) < cycle
        ):
            n["applied_cycle"] = cycle
            changed = True
    if changed:
        atomic_write(p, json.dumps(log, indent=2))


def get_nudges(loop_id: str) -> list[dict]:
    d = safe_loop_dir(loop_id)
    p = d / "nudges.json" if d else None
    if not p or not p.exists():
        return []
    try:
        log = json.loads(p.read_text())
        return [_redact_value(n) for n in log] if isinstance(log, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# Judge verdicts (open-ended goal done-ness — the third-party ROI scores, owned by
# the judge subagent, never the worker). File-based like findings.
def write_verdict(loop_id: str, cycle: int, verdict: dict) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "verdicts" / f"cycle_{cycle:03d}.json").write_text(json.dumps(verdict, indent=2))


def get_verdicts(loop_id: str) -> list[dict]:
    d = safe_loop_dir(loop_id)
    if d is None or not (d / "verdicts").exists():
        return []
    out: list[dict] = []
    for f in sorted((d / "verdicts").glob("cycle_*.json")):
        try:
            out.append(redact_finding(json.loads(f.read_text())))
        except (json.JSONDecodeError, OSError):
            continue
    return out


# Marginal-value trail (the open-ended returns-exhaustion signal) lives in
# kind_config so the lean schema needs no goal-specific column.
def record_marginal_score(loop_id: str, marginal: float) -> list[float]:
    """Append a cycle's judge marginal-value score to kind_config['marginal_scores'];
    return the full trail. The supervisor reads the last N vs the granularity dial."""
    loop = get(loop_id)
    if loop is None:
        return []
    cfg = dict(loop.kind_config or {})
    trail = list(cfg.get("marginal_scores", []) or [])
    trail.append(round(float(marginal), 2))
    cfg["marginal_scores"] = trail
    _simple_set(loop_id, "kind_config", json.dumps(cfg))
    return trail


def get_marginal_scores(loop_id: str) -> list[float]:
    loop = get(loop_id)
    return list((loop.kind_config or {}).get("marginal_scores", []) or []) if loop else []


# Quality-score trail — the absolute-quality signal (the ratchet guardrail), kept
# alongside the marginal trail so the calibrated returns-exhaustion band (P4) has a
# variance sample and the supervisor can reason about quality regressions over time.
def record_quality_score(loop_id: str, quality: float) -> list[float]:
    """Append a cycle's judge quality score to kind_config['quality_scores']; return
    the full trail. Mirrors :func:`record_marginal_score` (kind_config-backed, no
    goal-specific column)."""
    loop = get(loop_id)
    if loop is None:
        return []
    cfg = dict(loop.kind_config or {})
    trail = list(cfg.get("quality_scores", []) or [])
    trail.append(round(float(quality), 2))
    cfg["quality_scores"] = trail
    _simple_set(loop_id, "kind_config", json.dumps(cfg))
    return trail


def get_quality_scores(loop_id: str) -> list[float]:
    loop = get(loop_id)
    return list((loop.kind_config or {}).get("quality_scores", []) or []) if loop else []


def set_kind_config_key(loop_id: str, key: str, value: Any) -> None:
    """Set a single key in a loop's kind_config (read-modify-write of the JSON blob).
    Used for small supervisor-owned flags (e.g. the P4 canary's ``judge_calibrated``)
    that don't warrant their own column."""
    loop = get(loop_id)
    if loop is None:
        return
    cfg = dict(loop.kind_config or {})
    cfg[key] = value
    _simple_set(loop_id, "kind_config", json.dumps(cfg))


# Plan session (the stepwise planning walkthrough).
_PLAN_SESSION_FILE = "plan_session.json"


def read_plan_session(loop_id: str):
    from personalclaw.planning.session import PlanSession

    d = safe_loop_dir(loop_id)
    f = d / _PLAN_SESSION_FILE if d else None
    if not f or not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return PlanSession.from_dict(data)


def write_plan_session(session) -> None:
    d = loop_dir(session.project_id)
    if d is not None:
        atomic_write(d / _PLAN_SESSION_FILE, json.dumps(session.to_dict(), indent=2))


def clear_plan_session(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / _PLAN_SESSION_FILE if d else None
    if f and f.exists():
        f.unlink()


# GC: file dirs with no backing DB row (interrupted delete / dev reset).
def reap_orphan_dirs() -> int:
    """Delete per-loop file dirs under the loops root that have NO backing DB row
    (an interrupted delete, a dev DB reset, a failed draft insert that still made the
    dir). Only ``valid_loop_id``-shaped entries are touched, so ``loops.db`` + any
    sidecar is never at risk. Per-entry guarded: one bad entry (permission error,
    broken symlink) is logged + skipped rather than aborting the whole sweep + leaking
    the rest. Runs once at boot."""
    root = _loops_root()
    if not root.is_dir():
        return 0
    try:
        ids = {r.id for r in list_all()}
    except Exception:
        return 0
    reaped = 0
    import shutil

    for child in root.iterdir():
        try:
            if not child.is_dir() or not valid_loop_id(child.name) or child.name in ids:
                continue
            shutil.rmtree(child, ignore_errors=True)
            reaped += 1
            logger.info("loop: reaped orphan dir %s (no DB row)", child.name)
        except Exception:
            logger.debug("loop: failed to reap orphan dir %s", child, exc_info=True)
    return reaped
