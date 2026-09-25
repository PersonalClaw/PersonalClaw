"""Checkpoints, `fork`, and `revert` — branching a run without mutating it.

`rewind` and `run_from` are IN-PLACE: they edit the run you are steering. `fork` is the
branching primitive, for exploratory divergence where the original result must survive
("try a stricter judge, keep the old answer"). The original run and its journal stay
**immutable**; the child gets its own run dir.

**A fork is cheap because the journal cache keys match.** Cache keys are
`(instance_path, epoch, inputs_hash, spec_hash)` — none of which is the run id — so a
child that copies the parent's journal prefix gets cache HITS on everything up to the fork
point and re-runs only what diverges. That is why the prefix is copied rather than
recomputed.

**What a fork does NOT isolate has to be said out loud (WF2-R2 am.).** Isolated: run state,
spec, journal, outputs, effect ledger. *Not* isolated: the filesystem workspace, external
resources the parent created, and anything keyed off wall-clock or randomness. So a fork
records `fork_axis` — a per-fork disambiguator threaded into the child's inputs — and
`isolation_note`s that name the shared axes. A fork that silently shared a git branch or an
output filename with its parent would corrupt both, and the honest move is to name the
limit rather than imply a sandbox that does not exist.

**`revert` is the narrow inverse of `rewind`.** Rewind resets a region forward-blind;
revert undoes ONE node's effect and refuses (409-style, with the conflict named) when later
state already depends on it. Refusing loudly beats silently unwinding a value three
downstream nodes have already consumed.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows import store
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    walk,
)

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = "checkpoints"


@dataclass
class Checkpoint:
    """A per-super-step state snapshot — a fork point.

    Stores the instance map and the spec version, NOT the outputs: outputs live in
    `outputs/` and are content-addressed by node path, so a checkpoint that copied them
    would double every run's disk cost for no gain. The instance map is what pins "which
    nodes were done, at which epoch".
    """

    id: str
    run_id: str
    spec_version: int
    created_at: str = ""
    note: str = ""
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Container-image ref committed at checkpoint time (WF2WOR-12 §4.4) — what anchors
    #: fork-from-checkpoint to WORKSPACE state, not just journal state. Empty when the run
    #: has no live container or its backend cannot snapshot; tolerated absent on old rows.
    workspace_snapshot: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "spec_version": self.spec_version,
            "created_at": self.created_at,
            "note": self.note,
            "instances": self.instances,
            "workspace_snapshot": self.workspace_snapshot,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        d = d or {}
        return cls(
            id=str(d.get("id", "") or ""),
            run_id=str(d.get("run_id", "") or ""),
            spec_version=int(d.get("spec_version", 1) or 1),
            created_at=str(d.get("created_at", "") or ""),
            note=str(d.get("note", "") or ""),
            instances=dict(d.get("instances") or {}),
            workspace_snapshot=str(d.get("workspace_snapshot", "") or ""),
        )

    def instance_map(self) -> dict[str, NodeInstance]:
        out: dict[str, NodeInstance] = {}
        for path, raw in self.instances.items():
            inst = NodeInstance.from_dict(raw)
            if not inst.path:
                inst.path = str(path)
            out[str(path)] = inst
        return out


def _checkpoint_dir(run_id: str):
    return store.run_dir(run_id) / CHECKPOINT_DIR


def next_checkpoint_id(run_id: str) -> str:
    """Zero-padded sequence, so a directory listing sorts chronologically."""
    existing = (
        sorted(_checkpoint_dir(run_id).glob("*.json")) if _checkpoint_dir(run_id).is_dir() else []
    )
    return f"{len(existing) + 1:03d}"


def save_checkpoint(
    run: WorkflowRun,
    instances: dict[str, NodeInstance],
    *,
    note: str = "",
    now: str = "",
    workspace_snapshot: str = "",
) -> Checkpoint:
    cp = Checkpoint(
        id=next_checkpoint_id(run.id),
        run_id=run.id,
        spec_version=run.spec_version,
        created_at=now,
        note=note,
        instances={p: i.to_dict() for p, i in instances.items()},
        workspace_snapshot=workspace_snapshot,
    )
    path = _checkpoint_dir(run.id) / f"{cp.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(cp.to_dict(), indent=2, ensure_ascii=False))
    return cp


def load_checkpoint(run_id: str, checkpoint_id: str) -> Checkpoint | None:
    path = _checkpoint_dir(run_id) / f"{checkpoint_id}.json"
    if not path.is_file():
        return None
    try:
        return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        logger.warning("run %s: unreadable checkpoint %s", run_id, checkpoint_id)
        return None


def list_checkpoints(run_id: str) -> list[Checkpoint]:
    directory = _checkpoint_dir(run_id)
    if not directory.is_dir():
        return []
    out: list[Checkpoint] = []
    for path in sorted(directory.glob("*.json")):
        try:
            out.append(Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            logger.debug("run %s: skipping unreadable checkpoint %s", run_id, path.name)
    return out


# ── fork ─────────────────────────────────────────────────────────────────────

#: Axes a fork does NOT isolate. Surfaced on every fork rather than buried in a doc: a
#: caller who believes they got a sandbox will corrupt both runs.
SHARED_AXES = (
    "filesystem workspace (both runs write the same paths unless the spec differs)",
    "external resources the parent already created (its committed effects still exist)",
    "wall-clock and randomness (unique-name generators must take fork_axis as a seed)",
)


@dataclass
class ForkResult:
    child: WorkflowRun
    checkpoint_id: str = ""
    fork_axis: str = ""
    isolation_notes: list[str] = field(default_factory=list)
    cached_prefix: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_run_id": self.child.id,
            "checkpoint_id": self.checkpoint_id,
            "fork_axis": self.fork_axis,
            "isolation_notes": list(self.isolation_notes),
            "cached_prefix": self.cached_prefix,
            "shared_axes": list(SHARED_AXES),
        }


def fork_run(
    parent: WorkflowRun,
    spec: dict[str, Any],
    instances: dict[str, NodeInstance],
    *,
    checkpoint_id: str = "",
    note: str = "",
    now: str = "",
) -> ForkResult:
    """Branch a NEW run. The parent is not touched.

    From a checkpoint when one is named, else from current state. The child's journal is a
    COPY of the parent's prefix: cache keys carry no run id, so the child gets hits on
    everything already done and re-runs only what diverges. Copying is what makes a fork
    cheap; recomputing would defeat the point.

    The child inherits what the parent COMPLETED and nothing else (`_inherited`). Copying the
    instance map verbatim handed a fork of a failed run its parent's FAILED nodes, so the
    child's frontier was complete before it began: Start answered 202 and the child failed
    at +0.0 s with zero provider calls — the one retry path a failed run offers, dead.
    """
    source_instances = dict(instances)
    spec_version = parent.spec_version
    workspace_snapshot = ""
    if checkpoint_id:
        cp = load_checkpoint(parent.id, checkpoint_id)
        if cp is None:
            raise ValueError(f"unknown checkpoint {checkpoint_id!r} on run {parent.id}")
        source_instances = cp.instance_map()
        spec_version = cp.spec_version
        # The workspace anchor (WF2WOR-12): the child's provisioning starts its container
        # FROM the state committed at this checkpoint, so a code-kind fork resumes the
        # filesystem the checkpoint saw — the thing journal replay alone cannot restore.
        workspace_snapshot = cp.workspace_snapshot

    fork_axis = f"{parent.id}:{checkpoint_id or 'head'}"
    child = store.create(
        WorkflowRun(
            id="",
            workflow_name=parent.workflow_name,
            status=RunStatus.DRAFT,
            spec_version=spec_version,
            inputs={**dict(parent.inputs), "__fork_axis": fork_axis},
            intent=parent.intent,
            origin=parent.origin,
            parent_run_id=parent.id,
            # The whole tree shares one root id, so "show me this run tree" stays one
            # query rather than a recursive walk (WF2-R13).
            root_run_id=parent.root_run_id or parent.id,
            branch_key=fork_axis,
            forked_from={
                "run_id": parent.id,
                "checkpoint_id": checkpoint_id,
                "note": note,
                "workspace_snapshot": workspace_snapshot,
            },
            project_id=parent.project_id,
            mode=parent.mode,
            budget=parent.budget,
        )
    )
    store.write_spec(child.id, spec)
    child_instances = _inherited(source_instances)
    store.write_state(child.id, child_instances)
    cached = _copy_journal_prefix(parent.id, child.id, child_instances)
    _copy_outputs(parent.id, child.id, child_instances)

    return ForkResult(
        child=child,
        checkpoint_id=checkpoint_id,
        fork_axis=fork_axis,
        isolation_notes=[f"NOT isolated: {axis}" for axis in SHARED_AXES],
        cached_prefix=cached,
    )


def _inherited(instances: dict[str, NodeInstance]) -> dict[str, NodeInstance]:
    """The instance map a fork's child starts from: the parent's SUCCESSES, and a fresh start for
    every other node.

    A succeeded instance carries over whole — its output is copied and its cache key still hits,
    which is what makes a fork cheap. Anything else is work the child has not done: a FAILED or
    CANCELLED node is the parent's outcome, a RUNNING or WAITING one is the parent's in-flight work
    (its subagent, its lease, its open question), and a node SKIPPED because its input never
    materialized must be re-derived once that input can exist. Each restarts PENDING at the SAME
    epoch — a lower epoch could meet a stale pre-rewind cache entry in the copied journal — and
    keeps only the `foreach` item it was, which is the one durable record of WHICH item that is.
    The frontier re-skips anything that is still genuinely unreachable: an untaken branch case is
    re-skipped from its DONE branch's recorded route, which travels with the branch.
    """
    out: dict[str, NodeInstance] = {}
    for path, inst in instances.items():
        if inst.state in SUCCESS_STATES:
            out[path] = inst
            continue
        out[path] = NodeInstance(
            path=inst.path or path,
            epoch=inst.epoch,
            item_label=inst.item_label,
            item_total=inst.item_total,
        )
    return out


def _copy_journal_prefix(parent_id: str, child_id: str, instances: dict[str, NodeInstance]) -> int:
    """Copy the parent's journal so the child's resume cache hits.

    Read-only in spirit: the child appends its own records after the copy, and the parent's
    file is never opened for writing. Returns how many cache-bearing records carried over.

    A step OUTCOME record carries over only when the child inherits that step (`_inherited`). A
    step the child re-runs has the parent's attempt at it as history, not as the child's own:
    copied whole, a retry's Introspect counted the parent's failure beside its own work ("Steps
    failed 3" over a run with one failed node). Everything else carries over as before — above
    all `effect` records, the external world's account of what fired, which a fork cannot undo
    and the committed-effect boundary (`effects.redo_blocked`) must keep reading, and the
    per-container context records (handoffs, carryover, decisions) a resumed loop starts from.
    """
    from personalclaw.workflows.journal import (
        EVENTS_FILE,
        JOURNAL_FILE,
        STEP_ATTEMPT,
        STEP_CACHED,
        STEP_CANCELLED,
        STEP_COMPLETED,
        STEP_ESCALATED,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_STARTED,
    )

    step_outcomes = {
        STEP_STARTED,
        STEP_COMPLETED,
        STEP_CACHED,
        STEP_ATTEMPT,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CANCELLED,
        STEP_ESCALATED,
    }
    inherited = {path for path, inst in instances.items() if inst.state in SUCCESS_STATES}
    carried = 0
    for filename in (JOURNAL_FILE, EVENTS_FILE):
        records = store.read_jsonl(parent_id, filename)
        for rec in records:
            if rec.get("kind") in step_outcomes and rec.get("instance_path") not in inherited:
                continue
            store.append_jsonl(child_id, filename, rec)
            if filename == JOURNAL_FILE and rec.get("kind") in (STEP_COMPLETED, STEP_CACHED):
                carried += 1
    return carried


def _copy_outputs(parent_id: str, child_id: str, instances: dict[str, NodeInstance]) -> None:
    """Copy the outputs the checkpoint's done nodes produced.

    Without these the child's cache would HIT (keys match) and then read a missing output
    file, resolving a binding to None — a silent wrong answer, which is the failure mode the
    whole slice exists to prevent.
    """
    for path, inst in instances.items():
        if inst.state not in SUCCESS_STATES:
            continue
        value = store.read_output(parent_id, path)
        if value is not None:
            store.write_output(child_id, path, value)
        prompt = store.read_output(parent_id, f"{path}::prompt")
        if prompt is not None:
            store.write_output(child_id, f"{path}::prompt", prompt)


def prune_fork(child_id: str) -> bool:
    """Delete a fork's run directory. Used when a fork is abandoned.

    Refuses any path escaping the runs root — a stored run id is not a trust boundary
    (WF2-R13 deletion-sweep contract).
    """
    target = store.run_dir(child_id).resolve()
    root = store.runs_root().resolve()
    if root not in target.parents:
        logger.error("refusing to prune %s: outside the runs root", target)
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    store.delete(child_id)
    return True


# ── revert ───────────────────────────────────────────────────────────────────


@dataclass
class RevertConflict:
    """Why a revert was refused, naming WHICH later nodes depend on the value.

    Named rather than counted: "3 nodes depend on this" leaves the user guessing, and the
    whole reason to refuse instead of cascading is that they can then decide.
    """

    node_id: str
    dependents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "dependents": list(self.dependents)}


def revert_node(
    root: Node,
    instances: dict[str, NodeInstance],
    node_id: str,
) -> tuple[list[str], RevertConflict | None]:
    """Undo ONE node, or refuse with the conflict named.

    Returns `(paths_to_reset, conflict)`. A revert is legal only when nothing downstream has
    already consumed the node's output — otherwise it would silently unwind a value later
    nodes computed from, and the correct answer is a 409 naming them, not a quiet cascade
    (that is what `rewind` is for, and the user should choose it deliberately).
    """
    from personalclaw.workflows.mutations import dependents_graph

    consumers = dependents_graph(root).get(node_id, set())
    id_to_paths: dict[str, list[str]] = {}
    for path, node in walk(root):
        if node.id:
            id_to_paths.setdefault(node.id, []).append(path)

    settled = [
        consumer
        for consumer in sorted(consumers)
        if any(
            instances[p].state in SUCCESS_STATES
            for p in id_to_paths.get(consumer, [])
            if p in instances
        )
    ]
    if settled:
        return [], RevertConflict(node_id=node_id, dependents=settled)
    return [p for p in id_to_paths.get(node_id, []) if p in instances], None


def revert_paths(
    run_id: str,
    instances: dict[str, NodeInstance],
    paths: list[str],
    *,
    version: int,
) -> int:
    """Reset exactly `paths`, archiving their outputs. Returns how many were reset."""
    count = 0
    for path in paths:
        inst = instances.get(path)
        if inst is None:
            continue
        if inst.output_ref:
            store.archive_output(run_id, path, version)
        inst.state = InstanceState.PENDING
        inst.output_ref = ""
        inst.failure = None
        inst.completed_at = None
        inst.attempt = 0
        count += 1
    return count
