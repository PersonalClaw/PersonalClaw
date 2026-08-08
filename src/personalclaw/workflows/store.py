"""Run store — lean SQLite rows for runs, and the on-disk run directory layout.

Same idiom as `loop/store.py`: WAL, a busy timeout, `CREATE TABLE IF NOT EXISTS` as the
additive migration ladder, and `sqlite3.Row` so reads are dict-like.

The split is deliberate. SQLite holds only what queries need — status, genealogy,
counters — and everything large (the spec, per-node outputs, the journal) lives in
`runs/<id>/` as atomically-written files. A run's journal is append-only and can reach
megabytes; putting it in a row would make every status poll pay for it.

The `(root_run_id, status)` index is what makes "show me this run tree" one query
instead of a recursive walk (WF2-R13). Subworkflow spawns and forks propagate
`root_run_id`, so a whole tree shares one key.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.workflows.models import (
    NodeInstance,
    RunStatus,
    WorkflowRun,
)

logger = logging.getLogger(__name__)


# ── paths ────────────────────────────────────────────────────────────────────


def workflows_dir() -> Path:
    return config_dir() / "workflows"


def runs_root() -> Path:
    return workflows_dir() / "runs"


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def _db_path() -> Path:
    return workflows_dir() / "runs.db"


def new_run_id() -> str:
    """8 hex chars, matching the loop/artifact id convention."""
    return secrets.token_hex(4)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── connection + schema ──────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.DatabaseError:
        logger.debug("could not set WAL/busy_timeout pragmas", exc_info=True)
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            workflow_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            spec_version INTEGER NOT NULL DEFAULT 1,
            inputs TEXT NOT NULL DEFAULT '{}',
            intent TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT '{}',
            parent_run_id TEXT,
            root_run_id TEXT NOT NULL DEFAULT '',
            spawned_by_node_id TEXT,
            branch_key TEXT,
            forked_from TEXT,
            project_id TEXT NOT NULL DEFAULT '',
            task_list_id TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'background',
            budget TEXT NOT NULL DEFAULT '{}',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            agent_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            attention TEXT,
            extra TEXT NOT NULL DEFAULT '{}'
        )""")
    # The run-tree query (WF2-R13). Without it, listing a tree scans the table.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_root_status ON runs(root_run_id, status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_name_created ON runs(workflow_name, created_at)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
    conn.commit()
    return conn


_COLUMNS = (
    "id",
    "workflow_name",
    "status",
    "spec_version",
    "inputs",
    "intent",
    "origin",
    "parent_run_id",
    "root_run_id",
    "spawned_by_node_id",
    "branch_key",
    "forked_from",
    "project_id",
    "task_list_id",
    "mode",
    "budget",
    "pinned",
    "created_at",
    "started_at",
    "completed_at",
    "elapsed_seconds",
    "total_tokens",
    "agent_count",
    "error_message",
    "attention",
    "extra",
)

#: Columns stored as JSON text. Listed once so the row↔model mapping can't drift.
_JSON_COLUMNS = frozenset({"inputs", "origin", "forked_from", "budget", "attention", "extra"})


def _row_to_run(row: sqlite3.Row) -> WorkflowRun:
    d: dict[str, Any] = {}
    for col in _COLUMNS:
        val = row[col]
        if col in _JSON_COLUMNS:
            if val in (None, ""):
                d[col] = None if col in ("forked_from", "attention") else {}
            else:
                try:
                    d[col] = json.loads(val)
                except (TypeError, ValueError):
                    # A corrupt JSON cell must not make the whole run unreadable —
                    # the run row is how a user finds a broken run to delete it.
                    logger.warning("run %s: corrupt %s cell", row["id"], col)
                    d[col] = None if col in ("forked_from", "attention") else {}
        elif col == "pinned":
            d[col] = bool(val)
        else:
            d[col] = val
    extra = d.pop("extra", {}) or {}
    run = WorkflowRun.from_dict(d)
    run.extra = extra if isinstance(extra, dict) else {}
    return run


def _run_to_params(run: WorkflowRun) -> dict[str, Any]:
    d = run.to_dict()
    params: dict[str, Any] = {}
    for col in _COLUMNS:
        if col == "extra":
            params[col] = json.dumps(run.extra or {})
            continue
        val = d.get(col)
        if col in _JSON_COLUMNS:
            params[col] = json.dumps(val) if val is not None else None
        elif col == "pinned":
            params[col] = 1 if val else 0
        else:
            params[col] = val
    return params


# ── run CRUD ─────────────────────────────────────────────────────────────────


def create(run: WorkflowRun) -> WorkflowRun:
    """Insert a run row and make its directory. Idempotent on the directory so a
    retried create after a crash does not fail."""
    if not run.id:
        run.id = new_run_id()
    if not run.root_run_id:
        run.root_run_id = run.id
    if not run.created_at:
        run.created_at = _now()
    conn = _connect()
    try:
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", _run_to_params(run))
        conn.commit()
    finally:
        conn.close()
    run_dir(run.id).mkdir(parents=True, exist_ok=True)
    return run


def get(run_id: str) -> WorkflowRun | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_run(row) if row else None


def save(run: WorkflowRun) -> WorkflowRun:
    """Upsert. The engine's tick loop is the only writer of terminal status (WF2-R10),
    but every caller uses this same path — the ownership rule is enforced there, not by
    hiding the setter."""
    conn = _connect()
    try:
        assignments = ", ".join(f"{c} = :{c}" for c in _COLUMNS if c != "id")
        cur = conn.execute(f"UPDATE runs SET {assignments} WHERE id = :id", _run_to_params(run))
        if cur.rowcount == 0:
            cols = ", ".join(_COLUMNS)
            placeholders = ", ".join(f":{c}" for c in _COLUMNS)
            conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", _run_to_params(run))
        conn.commit()
    finally:
        conn.close()
    return run


def list_runs(
    *,
    workflow_name: str = "",
    status: str | RunStatus = "",
    root_run_id: str = "",
    project_id: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[WorkflowRun], int]:
    """Filtered, paginated run list plus the total for that filter."""
    where: list[str] = []
    params: list[Any] = []
    if workflow_name:
        where.append("workflow_name = ?")
        params.append(workflow_name)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if status:
        where.append("status = ?")
        params.append(status.value if isinstance(status, RunStatus) else str(status))
    if root_run_id:
        where.append("root_run_id = ?")
        params.append(root_run_id)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    conn = _connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM runs{clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM runs{clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, max(1, limit), max(0, offset)],
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_run(r) for r in rows], int(total)


def active_runs() -> list[WorkflowRun]:
    """Runs the engine should still be driving. Used by crash recovery at startup."""
    live = (RunStatus.RUNNING.value, RunStatus.PAUSED.value, RunStatus.NEEDS_INPUT.value)
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM runs WHERE status IN ({','.join('?' * len(live))})", live
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_run(r) for r in rows]


def delete(run_id: str) -> bool:
    """Remove the row. The DIRECTORY sweep is retention's job (Slice 1) — it has to
    enumerate every sibling artifact kind and refuse paths escaping the run dir, which
    is more than a delete belongs doing."""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_for_def(workflow_name: str) -> int:
    conn = _connect()
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM runs WHERE workflow_name = ?", (workflow_name,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


# ── run directory: spec, state, outputs ──────────────────────────────────────


def write_spec(run_id: str, spec: dict[str, Any]) -> Path:
    """The live run spec. Separate from the def so a mid-flight mutation edits THIS
    run without touching the template every other run shares."""
    path = run_dir(run_id) / "spec.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(spec, indent=2, ensure_ascii=False))
    return path


def read_spec(run_id: str) -> dict[str, Any] | None:
    path = run_dir(run_id) / "spec.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("run %s: unreadable spec.json", run_id, exc_info=True)
        return None


def write_spec_history(run_id: str, version: int, record: dict[str, Any]) -> Path:
    """One record per mutation: `{ops, actor, ts, hash}`. Append-only by construction —
    each version is its own file, so a concurrent writer cannot corrupt a prior entry."""
    path = run_dir(run_id) / "spec_history" / f"v{version:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(record, indent=2, ensure_ascii=False))
    return path


def write_state(run_id: str, instances: dict[str, NodeInstance]) -> Path:
    path = run_dir(run_id) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"instances": {p: inst.to_dict() for p, inst in instances.items()}}
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def read_state(run_id: str) -> dict[str, NodeInstance]:
    """Missing or corrupt state reads as empty rather than raising: an empty instance
    map means "nothing has run", which is the safe interpretation — the frontier
    recomputes from the spec and re-derives what is ready."""
    path = run_dir(run_id) / "state.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("run %s: unreadable state.json", run_id, exc_info=True)
        return {}
    out: dict[str, NodeInstance] = {}
    for p, d in (raw.get("instances") or {}).items():
        inst = NodeInstance.from_dict(d)
        if not inst.path:
            inst.path = str(p)
        out[str(p)] = inst
    return out


def _output_filename(node_path: str) -> str:
    """Hash the node path: `root.children[0].body` has characters that are awkward in a
    filename, and deep paths would exceed length limits."""
    import hashlib

    return hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:16] + ".json"


def write_output(run_id: str, node_path: str, output: Any) -> str:
    """Persist one node's structured output. Returns the run-relative ref stored on the
    instance, so a reader never reconstructs the hash."""
    rel = f"outputs/{_output_filename(node_path)}"
    path = run_dir(run_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps({"node_path": node_path, "output": output}, indent=2, ensure_ascii=False),
    )
    return rel


def write_artifact(run_id: str, node_path: str, output: Any) -> str:
    """Persist an OFFLOADED node output under `runs/<id>/artifacts/` (WV-11).

    Same envelope and same path-hash as :func:`write_output`, so :func:`read_output` finds
    an offloaded body by node path with no extra bookkeeping. The distinction is the
    directory: a body the journal spilled (oversize or binary) lives here, and its ref does
    NOT start with `outputs/` — which is the signal every reader uses to know it is a pointer
    to fetch on demand (`{{nodes.x.artifact}}`, the `artifact_inspect` provider) rather than a
    value to inline.
    """
    rel = f"artifacts/{_output_filename(node_path)}"
    path = run_dir(run_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps({"node_path": node_path, "output": output}, indent=2, ensure_ascii=False),
    )
    return rel


def read_artifact(run_id: str, ref: str) -> Any:
    """Read an offloaded artifact body by its run-relative ref, confined to `artifacts/`.

    The ref reaches here from a template binding a model may have authored (via
    `{{nodes.x.artifact}}`), so it is NOT trusted: the resolved path must sit inside this
    run's `artifacts/` directory or the read is refused. A `../` escape, an absolute path, or
    a pointer into `outputs/` all resolve outside the artifacts root and return None — the
    provider only ever reads run-local offloaded outputs.
    """
    if not ref:
        return None
    root = run_dir(run_id).resolve()
    art_root = (root / "artifacts").resolve()
    try:
        target = (root / ref).resolve()
        target.relative_to(art_root)
    except (ValueError, OSError):
        # relative_to raises when the resolved path escapes the artifacts root — the whole
        # point of the confinement check.
        return None
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8")).get("output")
    except (OSError, ValueError):
        logger.warning("run %s: unreadable artifact %s", run_id, ref, exc_info=True)
        return None


def archive_output(run_id: str, node_path: str, version: int) -> str:
    """Move a node's output into `outputs/attic/v<NNN>/` before a rewind overwrites it.

    ARCHIVED, not deleted (WF2-R2 #5). A rewind that discarded the prior answer would make
    the edit irreversible and leave a reader unable to see what the run used to say. Named
    by the spec version that superseded it, so the attic reads as a history rather than a
    pile of orphans.

    An OFFLOADED body (WV-11) lives under `artifacts/`, not `outputs/`; it is archived from
    there too, into the SAME attic, so a rewind leaves no stale artifact a later read could
    resolve through the `outputs/`→`artifacts/` fallback.

    Returns the archive path, or "" when there was nothing to move.
    """
    filename = _output_filename(node_path)
    src = run_dir(run_id) / "outputs" / filename
    if not src.is_file():
        src = run_dir(run_id) / "artifacts" / filename
    if not src.is_file():
        return ""
    rel = f"outputs/attic/v{version:03d}/{filename}"
    dest = run_dir(run_id) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.replace(dest)
    except OSError:
        logger.warning("run %s: could not archive output for %s", run_id, node_path)
        return ""
    return rel


def read_output(run_id: str, node_path: str) -> Any:
    """A node's stored output, or None.

    Checks `outputs/` first, then falls back to `artifacts/` — an oversized or binary output
    the journal spilled (WV-11) lives in the latter. One path-hash, two candidate directories,
    so every existing caller reads an offloaded body back transparently.
    """
    filename = _output_filename(node_path)
    for sub in ("outputs", "artifacts"):
        path = run_dir(run_id) / sub / filename
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("output")
        except (OSError, ValueError):
            logger.warning("run %s: unreadable output for %s", run_id, node_path, exc_info=True)
            return None
    return None


def append_jsonl(run_id: str, filename: str, record: dict[str, Any]) -> None:
    """Append to the journal or event log. Plain append rather than atomic_write: these
    files are append-only by contract and a rewrite would be O(size) per event."""
    path = run_dir(run_id) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(run_id: str, filename: str) -> list[dict[str, Any]]:
    """Read an append-only log, skipping corrupt lines. A half-written final line is
    expected after a crash — dropping it is correct, refusing the whole file is not."""
    path = run_dir(run_id) / filename
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            logger.debug("run %s: skipping corrupt line in %s", run_id, filename)
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# ── cancel intent (sticky) ───────────────────────────────────────────────────


def request_cancel(run_id: str) -> None:
    """Persist a CANCEL *intent*. Sticky on purpose: a cancel issued while the gateway
    is down must still be honoured on restart, so it is a file rather than memory."""
    path = run_dir(run_id) / "CANCEL"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, _now())


def cancel_requested(run_id: str) -> bool:
    return (run_dir(run_id) / "CANCEL").is_file()


def clear_cancel(run_id: str) -> None:
    (run_dir(run_id) / "CANCEL").unlink(missing_ok=True)
