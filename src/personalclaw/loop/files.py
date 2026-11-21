"""The per-loop FILE store — everything under ``config_dir()/loop/<id>/``.

``loop/store.py`` used to be TWO stores sharing one module — the SQLite
``loops`` row and this per-loop file dir (ledger events, worker findings, guidance,
questions, nudges, verdicts, plan session, stop sentinel). The 2026-08-27 measurement
put the file half at ~48 functions with 19 consuming modules, 7 of them touching no row
at all — so the split is along the real seam: this module owns the FILES, ``store.py``
owns the ROW, and the redacted views (which compose both) stay with the row.

Two deliberate couplings, inherited from the measurement and kept visible:

* :func:`reap_orphan_dirs` uses the ROW store's ``list_all`` as its GC oracle (a file
  dir with no backing row is an orphan). The import is function-local — the only
  files→row edge, and it points that way because the row is authoritative.
* ``loops.db`` lives under the same root this module owns (``loops_root``), because
  the root IS the durability artifact (durability/inventory.py, snapshot, merge) —
  splitting the code must not move a byte on disk.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.atomic_write import atomic_json_write, atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.ledger import EVENTS_FILE, JUDGE_VERDICT, STEP_COMPLETED, STEP_SKIPPED
from personalclaw.security import redact_for_display, redact_values_for_display
from personalclaw.workflows.models import stamp_epoch


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:  # a type hint only — a runtime import would close a cycle with loop.loop
    from personalclaw.loop.loop import LoopStatus

logger = logging.getLogger(__name__)

_LOOP_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STOP_SENTINEL = "STOP"

#: A loop's own folder, home-relative: a planner with no workspace to work in works here
#: (``planning.runner``), so it has a memory partition of its own, which goes with the folder
#: (``store.delete``, :func:`reap_orphan_dirs`, and ``memory_locality.settle_partitions`` at the
#: start).
SESSION_FOLDERS = ("loop/*",)


# ── paths ──


def loops_root() -> Path:
    return config_dir() / "loop"


def valid_loop_id(loop_id: str) -> bool:
    return bool(_LOOP_ID_RE.match(loop_id or ""))


def loop_dir(loop_id: str) -> Path | None:
    """The loop's file dir (created), or None if the id is invalid. Re-resolves
    under the root + confirms containment so a crafted id can't escape."""
    if not valid_loop_id(loop_id):
        return None
    root = loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root):
        return None
    d.mkdir(parents=True, exist_ok=True)
    # The worker still writes its per-cycle deliverable to findings/ — that file is its OUTPUT,
    # ingested once into the ledger by :func:`record_cycle_findings`. verdicts/ is gone: a verdict
    # is now a `judge_verdict` ledger event, never a second file store.
    (d / "findings").mkdir(exist_ok=True)
    return d


def safe_loop_dir(loop_id: str) -> Path | None:
    """Read-only variant — never creates."""
    if not valid_loop_id(loop_id):
        return None
    root = loops_root().resolve()
    d = (root / loop_id).resolve()
    if not d.is_relative_to(root) or not d.exists():
        return None
    return d


def file_inside(folder: str | Path, name: str) -> Path | None:
    """*name* as a file inside *folder*, or None — symlinks and ``..`` resolved first.

    For a loop's deliverable, whose name a goal or research loop takes from its
    ``primary_deliverable``, a name the owner types: one that climbs out of the folder is not the
    loop's deliverable, so it is neither read nor graduated. The same containment the artifact
    store applies to a pointer (`file_roots.admit`), so a deliverable the watchdog graduates is one
    the store would admit.
    """
    from personalclaw.file_roots import admit

    root = os.path.realpath(str(folder))
    admitted = admit(os.path.join(root, name), [root])
    return Path(admitted) if admitted is not None and os.path.isfile(admitted) else None


# ── ledger store ──
#
# The four calls a :class:`personalclaw.ledger.writer.LedgerWriter` appends through, keyed by
# loop_id. `loop.journal.LoopJournal` binds its `_store` to THIS module, so the loop is a second
# producer of the platform ledger without the ledger primitive ever importing `personalclaw.loop`.


def _ledger_filename(node_path: str) -> str:
    import hashlib

    return hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:16] + ".json"


def ledger_file(loop_id: str, filename: str) -> Path | None:
    """Where the loop keeps its journal or event log *filename*, or None for an invalid id, which
    has no folder and gets nothing written (``LedgerStore.ledger_file``)."""
    d = loop_dir(loop_id)
    return d / filename if d is not None else None


def append_jsonl(loop_id: str, filename: str, record: dict[str, Any]) -> None:
    """Append to the loop's journal or event log. Plain append: append-only by contract."""
    path = ledger_file(loop_id, filename)
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(loop_id: str, filename: str) -> list[dict[str, Any]]:
    """Read an append-only log, skipping a half-written final line (expected after a crash)."""
    d = safe_loop_dir(loop_id)
    path = d / filename if d else None
    if not path or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            logger.debug("loop %s: skipping corrupt line in %s", loop_id, filename)
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def write_output(loop_id: str, node_path: str, output: Any) -> str:
    """Persist an inline ledger output under `loop/<id>/outputs/`. Returns the loop-relative ref."""
    d = loop_dir(loop_id)
    if d is None:
        return ""
    rel = f"outputs/{_ledger_filename(node_path)}"
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path, json.dumps({"node_path": node_path, "output": output}, indent=2, ensure_ascii=False)
    )
    return rel


def write_artifact(loop_id: str, node_path: str, output: Any) -> str:
    """Persist an OFFLOADED ledger output under `loop/<id>/artifacts/` (the spill path)."""
    d = loop_dir(loop_id)
    if d is None:
        return ""
    rel = f"artifacts/{_ledger_filename(node_path)}"
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path, json.dumps({"node_path": node_path, "output": output}, indent=2, ensure_ascii=False)
    )
    return rel


# ── redaction ──


def redact_finding(finding: dict) -> dict:
    """A worker-authored finding masked for display (`security.redact_values_for_display`).
    Tolerates a non-dict (returns {}) so one malformed file can't poison a list."""
    if not isinstance(finding, dict):
        return {}
    return dict(redact_values_for_display(finding))


# ── file-based worker interface (status / brief / guidance / findings / …) ──


def write_status(loop_id: str, status: LoopStatus, **extra: Any) -> None:
    """Mirror status to status.json — the cycle gate the worker reads each turn.
    Atomic with the DB write's intent (the SQLite row is authoritative)."""
    d = loop_dir(loop_id)
    if d is None:
        return
    payload = {"status": status.value, "ts": time.time(), **extra}
    atomic_write(d / "status.json", json.dumps(payload, indent=2))


def status_since(loop_id: str, status: str) -> float:
    """When the loop's status was last written as *status* (``write_status``'s ``ts``), or ``0.0``
    when its ``status.json`` says another status, or nothing it can read."""
    d = safe_loop_dir(loop_id)
    try:
        data = json.loads((d / "status.json").read_text(encoding="utf-8")) if d else {}
    except (OSError, ValueError):
        return 0.0
    if not isinstance(data, dict) or data.get("status") != status:
        return 0.0
    try:
        return float(data.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def write_credited_cycles(loop_id: str, cycles: int) -> None:
    """Record how many cycles the watchdog has CREDITED (notified, judged, run the kind's hook for).

    Durable because crediting is not idempotent — it notifies "Cycle N complete", runs the done
    signal (a judge model call) and the kind's per-cycle hook (a code loop's SDLC stage advance).
    The watchdog's in-memory baseline is empty after a gateway restart, so without this record it
    re-credited the latest cycle on its first poll: measured 2026-09-25, "Cycle 1/30 complete" was
    notified twice across one restart. Its own file rather than a key in ``status.json``, which
    every status transition rewrites.
    """
    d = loop_dir(loop_id)
    if d is not None:
        atomic_write(d / "credited.json", json.dumps({"cycles": int(cycles)}))


def credited_cycles(loop_id: str) -> int:
    """The cycles already credited (:func:`write_credited_cycles`); 0 when none is recorded.

    Tolerant: an absent file is a loop never credited (its first cycle is still to be), and an
    unreadable one reads as 0 — re-crediting one cycle is recoverable, silently never crediting a
    real one is not.
    """
    d = safe_loop_dir(loop_id)
    f = d / "credited.json" if d else None
    if f is None or not f.is_file():
        return 0
    try:
        value = json.loads(f.read_text()).get("cycles", 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return value if isinstance(value, int) and value >= 0 else 0


def write_brief(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        (d / "brief.md").write_text(text)


#: The most steers a worker's pending file keeps; the oldest go first. A loop whose stage worker
#: never runs (a parallel loop's shared file) is steered all the same, and its file stays bounded.
_PENDING_KEPT = 50


def _add_pending(f: Path, text: str) -> None:
    """Add the owner's steer *text* to the worker's pending steer file *f*, after the steers
    already waiting there, with the time it was sent: two steers sent before the worker's next
    cycle both reach it, in the order they were sent (:func:`hand_over_steer`)."""
    steers = [*_read_pending(f), {"text": text, "sent_at": time.time()}][-_PENDING_KEPT:]
    atomic_write(f, "".join(json.dumps(s) + "\n" for s in steers))


def _read_pending(f: Path) -> list[dict]:
    """The steers waiting in the pending file *f*, oldest first: one JSON object a line. A file
    written before steers were kept one a line holds one steer, its words the whole file."""
    if not f.is_file():
        return []
    try:
        raw = f.read_text()
        sent = f.stat().st_mtime
    except OSError:
        return []
    steers = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            steer = json.loads(line)
        except ValueError:
            steer = None
        if not isinstance(steer, dict) or not isinstance(steer.get("text"), str):
            return [{"text": raw.strip(), "sent_at": sent}] if raw.strip() else []
        steers.append(steer)
    return steers


def write_guidance(loop_id: str, text: str) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        _add_pending(d / "guidance.txt", text)


def read_guidance(loop_id: str) -> str:
    """The words of the steers waiting for the stage worker, oldest first."""
    d = safe_loop_dir(loop_id)
    return "\n\n".join(s["text"] for s in _read_pending(d / "guidance.txt")) if d else ""


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
        _add_pending(d / name, text)


def read_task_guidance(loop_id: str, task_id: str) -> str:
    """The words of the steers waiting for the worker of *task_id*, oldest first."""
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    return "\n\n".join(s["text"] for s in _read_pending(d / name)) if (d and name) else ""


def seed_task_steers(loop_id: str, task_id: str) -> int:
    """Give the worker of *task_id*, as it starts, every steer the owner sent to the whole loop
    (the steer history's loop entries), oldest first, that is not already waiting for it. A loop's
    steer reached only the task workers running when it was sent, so each task that started after
    it ran without it. How many were added."""
    name = _task_guidance_name(task_id)
    d = loop_dir(loop_id)
    if d is None or name is None:
        return 0
    f = d / name
    waiting = {s["text"] for s in _read_pending(f)}
    added = [
        {"text": str(n["text"]), "sent_at": float(n.get("sent_at") or 0.0)}
        for n in _read_nudges(d / "nudges.json")
        if isinstance(n, dict) and not n.get("task_id") and n.get("text") not in waiting
    ]
    if added:
        steers = sorted([*_read_pending(f), *added], key=lambda s: float(s.get("sent_at") or 0))
        atomic_write(f, "".join(json.dumps(s) + "\n" for s in steers[-_PENDING_KEPT:]))
    return len(added)


def clear_task_guidance(loop_id: str, task_id: str) -> None:
    """The task's pending steer and the steers it was handed, gone with its worker."""
    name = _task_guidance_name(task_id)
    d = safe_loop_dir(loop_id)
    f = d / name if (d and name) else None
    if f and f.exists():
        f.unlink()
    if d is not None and name:
        delivered = _read_delivered(d)
        if delivered.pop(task_id, None) is not None:
            atomic_write(d / DELIVERED_STEERS_FILE, json.dumps(delivered, indent=2))


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
def finding_file(loop_folder: str, task_id: str = "") -> str:
    """The file a worker writes its cycle's finding to, in its loop's folder: a stage worker's
    ``findings/cycle_NNN.json``, a task worker's ``findings/task_<id>_NNN.json`` (NNN the next
    number). The ONE spelling: every worker prompt and the re-prompt name the file through here,
    and these are the two names :func:`_read_raw_finding_files` ingests. A task worker works in
    its own checkout, so the path is whole: a relative one is written into the checkout."""
    name = f"task_{task_id}_NNN.json" if task_id else "cycle_NNN.json"
    return f"{loop_folder}/findings/{name}" if loop_folder else f"findings/{name}"


def _read_raw_finding_files(loop_id: str) -> list[dict]:
    """The worker's per-cycle deliverable files, in ledger order (cycle_* by index, then task_*
    by mtime), redacted and with `task_id` resolved from the filename. INGEST-ONLY — the reader
    the public projections serve is the ledger, populated once by :func:`record_cycle_findings`;
    this raw scan is how a not-yet-ledgered file gets there. `_source_file` keys the idempotency.
    """
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
        finding["_source_file"] = f.name
        out.append(finding)
    return out


_STAGE_DECOR_RE = re.compile(r"^\s*(stage\s*)?\d+(\s*/\s*\d+)?\s*[—:\-–]\s*", re.IGNORECASE)


def _canonical_stage(label: str, plan: list) -> str | None:
    """Resolve a finding's stage label to the plan phase it names, or None.

    The `stage` field on a worker finding is UNCONSTRAINED MODEL OUTPUT — observed in
    three shapes on one loop ('1 — Write bell_times.py', '2 — Verify & QA',
    'Stage 2/2 — Verify & QA'), none equal to a phase's stage id or title, so every
    downstream exact-match attribution failed silently (issue 642: a 12-cycle blocked
    loop whose entire reasoning trail was invisible). The PLAN is the authority: match
    exact id/title first, then with the numeric decoration stripped and case folded.
    Returns the phase's canonical key (stage id, else title) — the value the engine
    itself keys phases by.
    """
    want = label.strip()
    if not want:
        return None
    norm = _STAGE_DECOR_RE.sub("", want).strip().lower()
    for exact in (True, False):
        for p in plan:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("stage", "")).strip()
            title = str(p.get("title", "")).strip()
            if exact:
                if want in (pid, title) and (pid or title):
                    return pid or title
            elif norm and norm in (pid.lower(), title.lower()):
                return pid or title
    return None


def record_cycle_findings(loop_id: str) -> int:
    """Ingest any worker finding files not yet on the ledger, as `step_started`/`step_completed`.

    The ONE write of a cycle's finding into the durable store: the worker authors the file,
    this turns it into ledger events, and every reader projects from the ledger. Idempotent — keyed
    by the source filename, so the watchdog calling it each poll (and across restarts) never
    double-emits. Returns how many new findings were ledgered.
    """
    from personalclaw.loop.journal import LoopJournal

    raw = _read_raw_finding_files(loop_id)
    if not raw:
        return 0
    already = {
        str(e.get("source_file") or "")
        for e in read_jsonl(loop_id, EVENTS_FILE)
        if e.get("kind") == STEP_COMPLETED
    }
    journal = LoopJournal(loop_id)
    # Canonicalize model-authored stage labels against the plan ONCE, at the single
    # write into the durable store — every projection then matches by construction
    # (the raw label survives as stage_label for display/debugging). Function-local
    # import for the same reason the GC's row edge is: a module-level one would close
    # a cycle with store.py's module-level import of this module.
    from personalclaw.loop import store as _row_store

    loop = _row_store.get(loop_id)
    plan = list(loop.plan or []) if loop else []
    filed = 0
    for finding in raw:
        src = str(finding.get("_source_file") or "")
        if src and src in already:
            continue
        stage_raw = finding.get("stage")
        if plan and isinstance(stage_raw, str) and stage_raw.strip():
            canon = _canonical_stage(stage_raw, plan)
            if canon and canon != stage_raw:
                finding = {**finding, "stage": canon, "stage_label": stage_raw}
        cycle_val = finding.get("cycle")
        try:
            cycle = int(cycle_val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # A worker that omitted `cycle` still gets a monotonic ordinal, so the trajectory
            # keeps an order rather than collapsing every such finding onto cycle 0.
            cycle = len(already) + filed + 1
        journal.cycle(cycle, finding)
        filed += 1
    return filed


def record_breaker_trip(loop_id: str, cycle: int, reason: str) -> None:
    """A stall → a `breaker_trip` ledger event."""
    from personalclaw.loop.journal import LoopJournal

    LoopJournal(loop_id).breaker_trip(cycle, reason)


def record_declined_cycle(
    loop_id: str, cycle: int, *, task_id: str, reason: str, steps: list[str]
) -> None:
    """A cycle its owner ended with a Deny → a `step_skipped` ledger event by the user."""
    from personalclaw.loop.journal import LoopJournal

    LoopJournal(loop_id).declined(cycle, task_id=task_id, reason=reason, steps=steps)


def get_declined_cycles(loop_id: str) -> list[dict]:
    """The cycles its owner ended with a Deny (:func:`record_declined_cycle`), oldest first, as
    the loop's page lists them: the cycle, the task, the sentence and what she declined, its words
    masked for display as a question's are."""
    return [
        {
            "cycle": rec.get("cycle"),
            "task_id": rec.get("task_id") or "",
            "reason": redact_for_display(str(rec.get("reason") or "")),
            "declined": [redact_for_display(str(s)) for s in rec.get("declined") or []],
            "ts": rec.get("ts"),
        }
        for rec in read_jsonl(loop_id, EVENTS_FILE)
        if rec.get("kind") == STEP_SKIPPED and rec.get("actor") == "user"
    ]


def record_watcher_reaped(loop_id: str, *, cycles: int, reason: str) -> None:
    """A reap → a `watcher_reaped` ledger event: a running watcher cut off early."""
    from personalclaw.loop.journal import LoopJournal

    LoopJournal(loop_id).watcher_reaped(cycles=cycles, reason=reason)


def get_findings(loop_id: str) -> list[dict]:
    """A loop's findings, PROJECTED off the ledger — the `step_completed` events'
    carried finding payloads, in emit order. The old findings/ file glob is gone as a store; the
    worker's files are an ingest source, not the reader's second store."""
    return findings_and_last_cycle(loop_id)[0]


def findings_and_last_cycle(loop_id: str) -> tuple[list[dict], float | None]:
    """A loop's findings (:func:`get_findings`) and when its newest cycle ran, in epoch seconds
    (``None`` before its first), from ONE read of its ledger, so a list row pays one scan for both.

    A cycle is a finding its worker filed (`step_completed`) or a cycle its owner ended with a Deny
    (:func:`record_declined_cycle`). A breaker trip or a judge's verdict is about a cycle, not one
    of its own. Its time is the stamp the ledger wrote on the event."""
    findings: list[dict] = []
    last: float | None = None
    for rec in read_jsonl(loop_id, EVENTS_FILE):
        kind = rec.get("kind")
        if kind == STEP_COMPLETED:
            finding = rec.get("finding")
            if isinstance(finding, dict):
                findings.append({k: v for k, v in finding.items() if k != "_source_file"})
        elif not (kind == STEP_SKIPPED and rec.get("actor") == "user"):
            continue
        at = stamp_epoch(rec.get("ts")) or None
        if at is not None and (last is None or at > last):
            last = at
    return findings, last


def cycles_completed(loop_id: str) -> int:
    """How many cycles this loop has completed — the ledger's `step_completed` count.

    Replaces the retired `loops.total_cycles` column: the column was a stored
    copy of exactly this number, so every reader now asks the projection. Delegates to
    :func:`personalclaw.loop.journal.cycles_completed`, which routes through the ledger's own
    aggregate — the same indirection `record_cycle_findings`/`record_breaker_trip` use, and for
    the same reason (one vocabulary, one place it is defined).

    Callers that already hold :func:`get_findings` should use ``len(findings)`` instead of paying
    a second scan: the two are equal by construction and a rail pins that.
    """
    from personalclaw.loop.journal import cycles_completed as _count

    return _count(loop_id)


def task_finding_count(loop_id: str, task_id: str) -> int:
    if not valid_task_guidance_id(task_id):
        return 0
    return sum(
        1
        for rec in read_jsonl(loop_id, EVENTS_FILE)
        if rec.get("kind") == STEP_COMPLETED and str(rec.get("task_id") or "") == task_id
    )


# Questions (attended-mode clarification).
#: ``asked_by`` on a question the code kind's scheduler asks (a merge to approve, a git identity to
#: set) rather than a worker: the scheduler asks it again for as long as it holds, so a Resume
#: never stops on one that no longer does (``watchdog.LoopWatchdog._handle_question``).
SCHEDULER_QUESTION = "scheduler"


def _waiting_moved() -> None:
    """What the loop asks you was written or cleared: the list of what waits on you moved, even
    when the loop's status did not (a scheduler asking again while it waits)."""
    from personalclaw import waiting

    waiting.changed_elsewhere()


def write_question(loop_id: str, question: str, **extra: Any) -> None:
    d = loop_dir(loop_id)
    if d is not None:
        atomic_json_write(d / "questions.json", {"question": question, "ts": time.time(), **extra})
        _waiting_moved()


def pending_question(loop_id: str) -> dict | None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if not f or not f.exists():
        return None
    try:
        q = json.loads(f.read_text())
        asked_at = f.stat().st_mtime
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(q, dict):
        return None
    # When it was asked, as a number: a worker writes its own question, with no time or a time in
    # words of its own ("2026-11-12T18:30:00"), and then its file's is read. The waiting list and
    # the loop's pause (`manager.question_holds`) read this one time.
    ts = q.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts):
        q["ts"] = float(ts)
    else:
        q["ts"] = asked_at
    for k in ("question", "why"):
        if isinstance(q.get(k), str):
            q[k] = redact_for_display(q[k])
    return q


def clear_question(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / "questions.json" if d else None
    if f and f.exists():
        f.unlink()
        _waiting_moved()


# Merges (a code loop's): what its scheduler merged into the workspace, for the loop's page.
MERGES_FILE = "merges.jsonl"


def record_merge(loop_id: str, record: dict[str, Any]) -> None:
    """Note that a task's work was merged into the workspace (append-only)."""
    append_jsonl(loop_id, MERGES_FILE, {**record, "at": time.time()})


def get_merges(loop_id: str) -> list[dict[str, Any]]:
    """Every merge the loop made, oldest first, its words masked for display as a question's are."""
    out = []
    for rec in read_jsonl(loop_id, MERGES_FILE):
        for key in ("title", "into", "branch"):
            if isinstance(rec.get(key), str):
                rec[key] = redact_for_display(rec[key])
        if isinstance(rec.get("commits"), list):
            rec["commits"] = [redact_for_display(str(c)) for c in rec["commits"]]
        out.append(rec)
    return out


# Nudges (durable steer trail).
def append_nudge(loop_id: str, text: str, sent_at_cycle: int, task_id: str = "") -> None:
    """One steer on the loop's steer history: its words, when and at which cycle it was sent,
    and the task it steers (``""``: the loop, every worker of it). :func:`hand_over_steer` marks
    when it reached a worker, and :func:`credit_delivered_steers` the cycle it was applied in."""
    d = loop_dir(loop_id)
    if d is None:
        return
    p = d / "nudges.json"
    log = _read_nudges(p)
    log.append(
        {
            "text": text,
            "sent_at": time.time(),
            "sent_at_cycle": sent_at_cycle,
            "task_id": task_id,
            "delivered_at": None,
            "applied_cycle": None,
        }
    )
    atomic_write(p, json.dumps(log, indent=2))


def _read_nudges(p: Path) -> list:
    try:
        log = json.loads(p.read_text()) if p.exists() else []
    except (json.JSONDecodeError, OSError):
        return []
    return log if isinstance(log, list) else []


def _steers_worker(entry: dict, channel: str) -> bool:
    """Whether the history *entry* steers the worker of *channel* (a task's id, ``""`` for the
    stage worker): its own task's, or the loop's, which every worker is given."""
    return str(entry.get("task_id") or "") in ("", channel)


def _mark_nudges(d: Path, channel: str, *, delivered_at: float = 0.0, credited: tuple = ()) -> None:
    """Mark the history entries steering *channel*: handed over at *delivered_at*, or, for
    *credited* ``(cycle, finding time)``, applied at that cycle once handed over before it."""
    p = d / "nudges.json"
    log = _read_nudges(p)
    changed = False
    for n in log:
        if not isinstance(n, dict) or not _steers_worker(n, channel):
            continue
        if delivered_at and not n.get("delivered_at"):
            n["delivered_at"] = delivered_at
            changed = True
        if credited and n.get("applied_cycle") is None:
            cycle, at = credited
            if n.get("delivered_at") and float(n["delivered_at"]) <= at:
                n["applied_cycle"] = cycle
                changed = True
    if changed:
        atomic_write(p, json.dumps(log, indent=2))


# Steers handed over (the gateway's own record, `owner_only.LOOP`): the owner's steer reaches its
# worker in the cycle's own message, from the gateway, never by the worker reading and deleting
# the steer file.
DELIVERED_STEERS_FILE = "delivered_steers.json"


def _read_delivered(d: Path) -> dict[str, list[dict]]:
    p = d / DELIVERED_STEERS_FILE
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): [s for s in v if isinstance(s, dict)]
        for k, v in data.items()
        if isinstance(v, list)
    }


def hand_over_steer(loop_id: str, task_id: str = "") -> str:
    """The owner's steer for the worker of *task_id* (``""``: the stage worker), as the gateway
    hands it over in that worker's next cycle message; ``""`` when there is none.

    The pending steer file (``guidance.txt``, ``guidance_<task>.txt``) is taken: its words are
    recorded as handed over (:data:`DELIVERED_STEERS_FILE`), its history entries marked delivered,
    and the file removed, by the gateway. Every steer handed over to this worker that no finding
    since has credited (:func:`credit_delivered_steers`) is handed over again, oldest first, so a
    steer given to a turn that failed is not lost. Called only for a cycle that is delivered."""
    d = loop_dir(loop_id)
    name = _task_guidance_name(task_id) if task_id else "guidance.txt"
    if d is None or name is None:
        return ""
    delivered = _read_delivered(d)
    pending = d / name
    steers = [s for s in _read_pending(pending) if str(s.get("text") or "").strip()]
    if steers:
        now, findings = time.time(), _findings_of(d, task_id)
        delivered.setdefault(task_id, []).extend(
            {
                "text": str(s["text"]).strip(),
                "sent_at": float(s.get("sent_at") or now),
                "delivered_at": now,
                "findings": findings,
            }
            for s in steers
        )
        atomic_write(d / DELIVERED_STEERS_FILE, json.dumps(delivered, indent=2))
        _mark_nudges(d, task_id, delivered_at=now)
    if pending.exists():
        pending.unlink()
    return _said(delivered.get(task_id, []))


def _said(steers: list[dict]) -> str:
    """The steers handed to a worker, as its cycle message says them: one steer as its words; two
    or more oldest first, each with when it was sent, and that where they disagree the later one
    is the one to follow."""
    seen: dict[str, float] = {}
    for s in steers:
        text = str(s.get("text") or "")
        if text:
            seen.pop(text, None)  # a steer sent again counts from its latest sending
            seen[text] = float(s.get("sent_at") or s.get("delivered_at") or 0.0)
    if len(seen) <= 1:
        return next(iter(seen), "")
    lines = [
        f"{n}. (sent {time.strftime('%Y-%m-%d %H:%M', time.localtime(at))}) {text}"
        for n, (text, at) in enumerate(seen.items(), start=1)
    ]
    return (
        f"{len(seen)} steers, oldest first. Where two of them disagree, follow the later one.\n"
        + "\n".join(lines)
    )


def credit_delivered_steers(loop_id: str, *, cycle: int) -> None:
    """Credit each steer a worker was handed with the first finding of that worker written after
    the hand-over (its findings on disk outnumber those at the hand-over): a task's finding
    credits its task's steers, a stage finding the stage worker's. A credited steer is not handed
    over again, and its history entries are applied at *cycle*. A finding written before the
    hand-over credits nothing."""
    d = safe_loop_dir(loop_id)
    if d is None:
        return
    delivered = _read_delivered(d)
    changed = False
    for channel, steers in list(delivered.items()):
        now = _findings_of(d, channel)
        credited = [s for s in steers if now > int(s.get("findings") or 0)]
        if credited:
            changed = True
            last = max(float(s.get("delivered_at") or 0.0) for s in credited)
            _mark_nudges(d, channel, credited=(cycle, last))
        kept = [s for s in steers if s not in credited]
        if kept:
            delivered[channel] = kept
        else:
            delivered.pop(channel)
    if changed:
        atomic_write(d / DELIVERED_STEERS_FILE, json.dumps(delivered, indent=2))


def _findings_of(d: Path, channel: str) -> int:
    """How many finding files the worker of *channel* has written in the loop's folder *d*: a
    task's own (``task_<id>_NNN.json``), or the stage worker's (``cycle_NNN.json``). Counted on
    disk, where the worker writes them, so a finding is counted the moment it exists."""
    folder = d / "findings"
    if not folder.is_dir():
        return 0
    pattern = f"task_{channel}_*.json" if channel else "cycle_*.json"
    return sum(1 for f in folder.glob(pattern) if f.is_file())


def get_nudges(loop_id: str) -> list[dict]:
    d = safe_loop_dir(loop_id)
    p = d / "nudges.json" if d else None
    if not p or not p.exists():
        return []
    try:
        log = json.loads(p.read_text())
        return list(redact_values_for_display(log)) if isinstance(log, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# Judge verdicts (open-ended goal done-ness — the third-party ROI scores, owned by
# the judge subagent, never the worker). A `judge_verdict` ledger event, not a
# second file store: the verdict dict is `{"cycle": n, **JudgeVerdict.to_dict()}`, so the
# ledger record carries the reconciled vocabulary at top level.
def write_verdict(loop_id: str, cycle: int, verdict: dict) -> None:
    from personalclaw.loop.journal import LoopJournal

    LoopJournal(loop_id).verdict({"cycle": cycle, **verdict})


def get_verdicts(loop_id: str) -> list[dict]:
    """Projected off the ledger — the `judge_verdict` events' payloads, in emit order."""
    from personalclaw.loop.journal import strip_meta

    return [
        strip_meta(rec)
        for rec in read_jsonl(loop_id, EVENTS_FILE)
        if rec.get("kind") == JUDGE_VERDICT
    ]


# Plan session (the stepwise planning walkthrough).
_PLAN_SESSION_FILE = "plan_session.json"
#: The planner's own folder inside a loop's (:func:`planner_dir`).
_PLANNER_DIR = "planner"


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


def planner_dir(loop_id: str) -> Path | None:
    """The folder a loop's planner works in, inside the loop's own (created): its files' root,
    apart from the files the gateway keeps for the loop."""
    d = loop_dir(loop_id)
    if d is None:
        return None
    p = d / _PLANNER_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_plan_session(loop_id: str, session) -> None:
    """Write *session* as the plan of the loop *loop_id* names: the loop its caller is about, never
    the one the session's own ``project_id`` names. A session that names another loop is not
    written, and is said."""
    if str(getattr(session, "project_id", "") or "") != loop_id:
        logger.warning(
            "a plan session of loop %r was not written as loop %r's", session.project_id, loop_id
        )
        return
    d = loop_dir(loop_id)
    if d is not None:
        atomic_write(d / _PLAN_SESSION_FILE, json.dumps(session.to_dict(), indent=2))
        _waiting_moved()  # a step drafted for your review, or reviewed


def clear_plan_session(loop_id: str) -> None:
    d = safe_loop_dir(loop_id)
    f = d / _PLAN_SESSION_FILE if d else None
    if f and f.exists():
        f.unlink()
        _waiting_moved()


# GC: file dirs with no backing DB row (interrupted delete / dev reset).
def reap_orphan_dirs() -> int:
    """Delete per-loop file dirs under the loops root that have NO backing DB row
    (an interrupted delete, a dev DB reset, a failed draft insert that still made the
    dir). Only ``valid_loop_id``-shaped entries are touched, so ``loops.db`` + any
    sidecar is never at risk. Per-entry guarded: one bad entry (permission error,
    broken symlink) is logged + skipped rather than aborting the whole sweep + leaking
    the rest. Runs once at boot."""
    root = loops_root()
    if not root.is_dir():
        return 0
    # The ONE files->row edge, function-local on purpose: the row is the GC oracle
    # (a file dir with no backing row is an orphan), and a module-level import here
    # would close a cycle with store.py's module-level import of this module.
    from personalclaw.loop import store as _row_store

    try:
        ids = {r.id for r in _row_store.list_all()}
    except Exception:
        return 0
    reaped = 0
    import shutil

    from personalclaw.memory_locality import drop_partition

    for child in root.iterdir():
        try:
            if not child.is_dir() or not valid_loop_id(child.name) or child.name in ids:
                continue
            shutil.rmtree(child, ignore_errors=True)
            drop_partition(str(child))
            reaped += 1
            logger.info("loop: reaped orphan dir %s (no DB row)", child.name)
        except Exception:
            logger.debug("loop: failed to reap orphan dir %s", child, exc_info=True)
    return reaped
