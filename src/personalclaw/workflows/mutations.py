"""Mid-flight mutation — typed ops, the binding cascade, and a transactional batch.

A user steering a live run edits it. That is the whole point of the feature, and it is
also the most dangerous thing the engine allows, so three rules hold everything up:

**The cascade follows BINDINGS, not the tree (WF2-R2).** The obvious implementation resets
"the node and its descendants" — its *tree* descendants. But data flows through
`{{nodes.<id>.output}}`, and a later SIBLING binding the edited node's output is not a tree
descendant. Reset only the subtree and that sibling keeps a stale input: a silently
inconsistent run, which is worse than a loud failure because nothing looks wrong. So the
closure is computed over the binding-dependency graph.

**A rejected batch writes NOTHING (WF2-R20e).** Validation runs against a *candidate copy*
of the spec; the live spec is replaced only once the whole batch has applied and
re-validated. A half-applied batch would leave a spec no one authored — neither what the
user had nor what they asked for.

**Nothing frozen may be edited.** RUNNING and terminal nodes reject, except `rewind` and
`run_from`, which exist precisely to unfreeze. The time-of-check gap is real (nodes
complete while a user reads a preview), so the caller re-verifies immediately before
applying — `validate_batch` is pure and cheap enough to run twice.

Epoch bumps are reserved for FORCE. A rewind that does not change a node's inputs should
replay from cache, not re-run: the inputs-hash tier in the journal key already handles
"same inputs, same answer", and bumping the epoch unconditionally would throw that away
and re-run the expensive half of the graph for nothing.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.models import (
    FROZEN_STATES,
    TERMINAL_STATES,
    InstanceState,
    Node,
    NodeInstance,
    walk,
)

logger = logging.getLogger(__name__)


class OpKind(str, Enum):
    """The mutation vocabulary. Closed on purpose — an open op set cannot be validated,
    and validation is what makes mid-flight editing safe."""

    UPDATE_NODE = "update_node"
    INSERT = "insert"
    DELETE = "delete"
    MOVE = "move"
    SET_INPUT = "set_input"
    SKIP = "skip"
    REWIND = "rewind"
    RUN_FROM = "run_from"
    FORK = "fork"
    INLINE_SUBWORKFLOW = "inline_subworkflow"


#: Ops that deliberately UNFREEZE. Every other op targeting a frozen node is rejected —
#: these two exist to reset completed work, so the frozen check must not apply to them.
UNFREEZING_OPS = frozenset({OpKind.REWIND, OpKind.RUN_FROM})

#: Ops that do not target a node at all, so node-level checks do not apply.
NODELESS_OPS = frozenset({OpKind.SET_INPUT, OpKind.FORK})

#: LLM-friendly aliases normalized to the canonical op name (WF2-R20d). A model that
#: writes `edit_node` meant `update_node`; rejecting it teaches nothing and costs a turn.
_OP_ALIASES = {
    "edit_node": "update_node",
    "update": "update_node",
    "patch_node": "update_node",
    "add": "insert",
    "add_node": "insert",
    "insert_node": "insert",
    "remove": "delete",
    "remove_node": "delete",
    "delete_node": "delete",
    "move_node": "move",
    "relocate": "move",
    "set_inputs": "set_input",
    "override_input": "set_input",
    "skip_node": "skip",
    "reset": "rewind",
    "redo": "rewind",
    "rerun_from": "run_from",
    "run_after": "run_from",
    "branch": "fork",
}

#: Field-name aliases inside `update_node.fields` (WF2-R20d).
_FIELD_ALIASES = {
    "model": "model_tier",
    "tier": "model_tier",
    "instructions": "prompt",
    "text": "prompt",
    "task": "prompt",
}


def normalize_op_kind(raw: str) -> str:
    key = str(raw or "").strip().lower()
    return _OP_ALIASES.get(key, key)


def normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {_FIELD_ALIASES.get(str(k), str(k)): v for k, v in (fields or {}).items()}


@dataclass
class Op:
    """One typed mutation. `raw` keeps the original payload for the audit trail — a
    normalized op is what applied, but what the author WROTE is what a later reader needs
    to understand the intent."""

    kind: OpKind
    node_id: str = ""
    parent_id: str = ""
    index: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    node: dict[str, Any] | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    redo_effects: bool = False
    force: bool = False
    note: str = ""
    checkpoint_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def positional(self) -> bool:
        """Identified by `parent_id + index` rather than by a unique `node_id`."""
        return bool(self.parent_id) and self.index is not None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.kind.value}
        for key, value in (
            ("node_id", self.node_id),
            ("parent_id", self.parent_id),
            ("index", self.index),
            ("fields", self.fields),
            ("node", self.node),
            ("overrides", self.overrides),
            ("redo_effects", self.redo_effects),
            ("force", self.force),
            ("note", self.note),
            ("checkpoint_id", self.checkpoint_id),
        ):
            if value not in (None, "", {}, False):
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Op:
        """Parse one op, tolerating aliases. Raises `ValueError` on an unknown kind: the
        engine cannot apply what it cannot name, and guessing would apply the WRONG edit
        to a live run."""
        raw = dict(d or {})
        kind_raw = raw.get("op") or raw.get("kind") or ""
        canonical = normalize_op_kind(str(kind_raw))
        try:
            kind = OpKind(canonical)
        except ValueError as exc:
            raise ValueError(f"unknown mutation op {kind_raw!r}") from exc
        index = raw.get("index")
        return cls(
            kind=kind,
            node_id=str(raw.get("node_id", "") or raw.get("id", "") or ""),
            parent_id=str(raw.get("parent_id", "") or raw.get("parent", "") or ""),
            index=int(index) if isinstance(index, (int, float)) else None,
            fields=normalize_fields(raw.get("fields") or {}),
            node=raw.get("node") if isinstance(raw.get("node"), dict) else None,
            overrides=dict(raw.get("overrides") or raw.get("inputs") or {}),
            redo_effects=bool(raw.get("redo_effects", False)),
            force=bool(raw.get("force", False)),
            note=str(raw.get("note", "") or ""),
            checkpoint_id=str(raw.get("checkpoint_id", "") or ""),
            raw=raw,
        )


@dataclass
class Issue:
    """A rejection reason. `code` is stable so a chat tool can reprompt on it rather than
    re-deriving intent from prose."""

    code: str
    message: str
    node_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "node_id": self.node_id}


@dataclass
class CascadePreview:
    """What a batch would re-run (WF2-R2 #2).

    Mandatory before applying: a user who edits one prompt and unknowingly re-runs twelve
    completed stages has been billed for a surprise. `stale` is the other half — nodes
    whose inputs changed but which are NOT being re-run get a journaled flag rather than
    silently serving an answer computed from different inputs.
    """

    rerun: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Nodes in the cascade with a COMMITTED external effect. Surfaced, never silently
    #: re-fired (§2 effect ledger) — this is what `redo_effects` gates.
    committed_effects: list[str] = field(default_factory=list)
    #: True when the cascade re-runs already-completed work, which needs confirmation.
    needs_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rerun": list(self.rerun),
            "stale": list(self.stale),
            "skipped": list(self.skipped),
            "committed_effects": list(self.committed_effects),
            "needs_confirmation": self.needs_confirmation,
        }


@dataclass
class BatchResult:
    """The outcome of validating (and optionally applying) a batch."""

    ok: bool = True
    issues: list[Issue] = field(default_factory=list)
    ops: list[Op] = field(default_factory=list)
    preview: CascadePreview = field(default_factory=CascadePreview)
    spec: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "ops": [o.to_dict() for o in self.ops],
            "preview": self.preview.to_dict(),
        }


# ── the binding-dependency graph ─────────────────────────────────────────────


def dependents_graph(root: Node) -> dict[str, set[str]]:
    """node id → the ids that CONSUME its output.

    Built from bindings, not from the tree. This inversion is the correctness core of
    WF2-R2: it is what finds the later sibling reading an edited node's output, which a
    tree walk cannot see.
    """
    consumers: dict[str, set[str]] = {}
    for _path, node in walk(root):
        if not node.id:
            continue
        for dep in node_deps(node.config or {}):
            consumers.setdefault(dep, set()).add(node.id)
    return consumers


def binding_closure(root: Node, seeds: set[str]) -> set[str]:
    """Every node transitively downstream of `seeds` through bindings, seeds included.

    A breadth-first walk over the consumer graph, cycle-safe by construction (`seen`) —
    the validator forbids binding cycles, but a closure that hangs on a malformed spec
    would take the whole run's controller with it.
    """
    consumers = dependents_graph(root)
    seen: set[str] = set()
    queue = [s for s in seeds if s]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(consumers.get(current, set()) - seen)
    return seen


def cascade_preview(
    root: Node,
    instances: dict[str, NodeInstance],
    seeds: set[str],
    *,
    effects: dict[str, Any] | None = None,
) -> CascadePreview:
    """What editing `seeds` implies. Pure — safe to call for a preview and again for the
    TOCTOU re-verify."""
    preview = CascadePreview()
    closure = binding_closure(root, seeds)
    id_to_paths: dict[str, list[str]] = {}
    for path, node in walk(root):
        if node.id:
            id_to_paths.setdefault(node.id, []).append(path)

    for node_id in sorted(closure):
        states = [instances[p].state for p in id_to_paths.get(node_id, []) if p in instances]
        if any(st in TERMINAL_STATES for st in states):
            preview.rerun.append(node_id)
            if states and all(st == InstanceState.SKIPPED for st in states):
                preview.skipped.append(node_id)
        elif states:
            preview.rerun.append(node_id)

    # Completed work in the cascade is what a user must knowingly accept.
    preview.needs_confirmation = any(
        instances[p].state in TERMINAL_STATES
        for node_id in closure
        for p in id_to_paths.get(node_id, [])
        if p in instances
    )

    for node_id in sorted(closure):
        for path in id_to_paths.get(node_id, []):
            records = (effects or {}).get(path) or []
            if _has_committed(records):
                preview.committed_effects.append(node_id)
                break
    return preview


def _has_committed(records: list[Any]) -> bool:
    from personalclaw.workflows.effects import committed_effect

    try:
        return committed_effect(records) is not None
    except Exception:  # a malformed ledger must not block a preview
        logger.debug("cascade preview: effect history unreadable", exc_info=True)
        return False


# ── batch validation ─────────────────────────────────────────────────────────


def parse_batch(raw_ops: list[dict[str, Any]]) -> tuple[list[Op], list[Issue]]:
    """Parse every op, collecting failures rather than raising on the first.

    All-at-once because a model that got two ops wrong should learn both in one
    reprompt; failing on the first costs a turn per mistake.
    """
    ops: list[Op] = []
    issues: list[Issue] = []
    for i, raw in enumerate(raw_ops or []):
        try:
            ops.append(Op.from_dict(raw))
        except ValueError as exc:
            issues.append(Issue(code="WF_MUT_UNKNOWN_OP", message=f"op[{i}]: {exc}"))
    return ops, issues


def validate_batch(
    ops: list[Op],
    root: Node,
    instances: dict[str, NodeInstance],
) -> list[Issue]:
    """Every structural rule from WF2-R20, checked before anything is written."""
    issues: list[Issue] = []
    ids = {node.id for _p, node in walk(root) if node.id}
    id_to_paths: dict[str, list[str]] = {}
    for path, node in walk(root):
        if node.id:
            id_to_paths.setdefault(node.id, []).append(path)

    # (a) unique-anchor XOR positional — never mixed in one batch.
    anchored = [o for o in ops if o.node_id and o.kind not in NODELESS_OPS]
    positional = [o for o in ops if o.positional and not o.node_id]
    if anchored and positional:
        issues.append(
            Issue(
                code="WF_MUT_MIXED_ADDRESSING",
                message=(
                    "batch mixes node_id addressing with parent_id+index addressing; "
                    "use one scheme per batch so indices cannot shift under an anchor"
                ),
            )
        )

    # (b) no two ops on the same node.
    seen: dict[str, int] = {}
    for op in ops:
        if not op.node_id:
            continue
        seen[op.node_id] = seen.get(op.node_id, 0) + 1
    for node_id, count in sorted(seen.items()):
        if count > 1:
            issues.append(
                Issue(
                    code="WF_MUT_OVERLAPPING_EDITS",
                    message=f"{count} ops target node {node_id!r}; combine them into one",
                    node_id=node_id,
                )
            )

    for op in ops:
        if op.kind in NODELESS_OPS:
            if op.kind == OpKind.SET_INPUT and not op.overrides:
                issues.append(
                    Issue(code="WF_MUT_EMPTY_OVERRIDES", message="set_input has no overrides")
                )
            continue

        if op.kind == OpKind.INSERT:
            if not op.node:
                issues.append(
                    Issue(code="WF_MUT_INSERT_NO_NODE", message="insert has no `node` payload")
                )
            elif not _parsable(op.node):
                issues.append(
                    Issue(
                        code="WF_MUT_INSERT_BAD_NODE",
                        message="insert `node` is not a valid node spec",
                    )
                )
            if op.parent_id and op.parent_id not in ids:
                issues.append(
                    Issue(
                        code="WF_MUT_UNKNOWN_PARENT",
                        message=f"insert parent {op.parent_id!r} does not exist",
                        node_id=op.parent_id,
                    )
                )
            continue

        if not op.node_id:
            issues.append(
                Issue(
                    code="WF_MUT_NO_TARGET",
                    message=f"{op.kind.value} needs a node_id",
                )
            )
            continue
        if op.node_id not in ids:
            issues.append(
                Issue(
                    code="WF_MUT_UNKNOWN_NODE",
                    message=f"node {op.node_id!r} does not exist in this run's spec",
                    node_id=op.node_id,
                )
            )
            continue

        # Frozen-region invariant. rewind/run_from are exempt BY DESIGN — unfreezing
        # completed work is exactly what they are for.
        if op.kind not in UNFREEZING_OPS:
            frozen = [
                p
                for p in id_to_paths.get(op.node_id, [])
                if p in instances and instances[p].state in FROZEN_STATES
            ]
            if frozen:
                state = instances[frozen[0]].state.value
                issues.append(
                    Issue(
                        code="WF_MUT_FROZEN_NODE",
                        message=(
                            f"node {op.node_id!r} is {state} and cannot be edited; "
                            "rewind it first if you need to change it"
                        ),
                        node_id=op.node_id,
                    )
                )

        if op.kind == OpKind.UPDATE_NODE and not op.fields:
            issues.append(
                Issue(
                    code="WF_MUT_EMPTY_UPDATE",
                    message=f"update_node on {op.node_id!r} has no fields",
                    node_id=op.node_id,
                )
            )
        if op.kind == OpKind.MOVE:
            if not op.parent_id:
                issues.append(
                    Issue(
                        code="WF_MUT_MOVE_NO_PARENT",
                        message=f"move of {op.node_id!r} names no new parent",
                        node_id=op.node_id,
                    )
                )
            elif op.parent_id not in ids:
                issues.append(
                    Issue(
                        code="WF_MUT_UNKNOWN_PARENT",
                        message=f"move target parent {op.parent_id!r} does not exist",
                        node_id=op.parent_id,
                    )
                )
            elif _is_descendant(root, op.node_id, op.parent_id):
                issues.append(
                    Issue(
                        code="WF_MUT_MOVE_INTO_SELF",
                        message=(
                            f"cannot move {op.node_id!r} into its own subtree — "
                            "that would detach the graph"
                        ),
                        node_id=op.node_id,
                    )
                )
    return issues


def _parsable(node_dict: dict[str, Any]) -> bool:
    try:
        Node.from_dict(node_dict)
        return True
    except (ValueError, TypeError):
        return False


def _is_descendant(root: Node, ancestor_id: str, candidate_id: str) -> bool:
    """Is `candidate_id` inside `ancestor_id`'s subtree? Guards move-into-self, which
    would silently detach a whole region from the graph."""
    for _path, node in walk(root):
        if node.id != ancestor_id:
            continue
        for _sub, inner in walk(node):
            if inner.id == candidate_id and inner is not node:
                return True
    return False


# ── application (on a candidate copy) ────────────────────────────────────────


def apply_batch(
    ops: list[Op],
    spec: dict[str, Any],
    instances: dict[str, NodeInstance],
) -> tuple[dict[str, Any], list[Issue]]:
    """Apply ops to a DEEP COPY and return `(candidate_spec, issues)`.

    The copy is the atomic-failure contract (WF2-R20e): the caller swaps the live spec in
    only on success, so a rejected batch leaves the prior spec as the single source of
    truth with no partial application to unwind.

    Coordinate-preserving order (WF2-R20c): structural ops apply in DESCENDING index, so
    an earlier op cannot shift the coordinates a later one names.
    """
    candidate = copy.deepcopy(spec)
    issues: list[Issue] = []

    structural = [o for o in ops if o.kind in (OpKind.INSERT, OpKind.DELETE, OpKind.MOVE)]
    others = [o for o in ops if o not in structural]
    structural.sort(key=lambda o: (o.index if o.index is not None else -1), reverse=True)

    for op in [*others, *structural]:
        try:
            _apply_one(op, candidate, instances)
        except _ApplyError as exc:
            issues.append(Issue(code=exc.code, message=str(exc), node_id=exc.node_id))
    return candidate, issues


class _ApplyError(Exception):
    def __init__(self, code: str, message: str, node_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.node_id = node_id


def _apply_one(op: Op, spec: dict[str, Any], instances: dict[str, NodeInstance]) -> None:
    if op.kind == OpKind.SET_INPUT:
        spec.setdefault("inputs", {})
        if isinstance(spec["inputs"], dict):
            spec["inputs"].update(op.overrides)
        return
    if op.kind in (OpKind.FORK, OpKind.REWIND, OpKind.RUN_FROM):
        # State-level ops: they change instance state and run identity, not the spec.
        # Slice 4c owns the state half; the spec is untouched here by design.
        return

    root = spec.get("root")
    if not isinstance(root, dict):
        raise _ApplyError("WF_MUT_NO_ROOT", "spec has no root node")

    if op.kind == OpKind.UPDATE_NODE:
        target = _find_node_dict(root, op.node_id)
        if target is None:
            raise _ApplyError("WF_MUT_UNKNOWN_NODE", f"node {op.node_id!r} vanished", op.node_id)
        config = target.setdefault("config", {})
        if not isinstance(config, dict):
            raise _ApplyError("WF_MUT_BAD_CONFIG", f"node {op.node_id!r} config is not an object")
        for key, value in op.fields.items():
            # `id` and `kind` are identity, not content: changing them mid-run would
            # orphan every binding and journal key that names the node.
            if key in ("id", "kind"):
                raise _ApplyError(
                    "WF_MUT_IMMUTABLE_FIELD",
                    f"cannot change {key!r} on a live node — it is the node's identity",
                    op.node_id,
                )
            config[key] = value
        return

    if op.kind == OpKind.SKIP:
        # Recorded on the spec so a resumed run re-derives the same decision; the
        # instance-state write is the controller's (single-writer rule).
        target = _find_node_dict(root, op.node_id)
        if target is None:
            raise _ApplyError("WF_MUT_UNKNOWN_NODE", f"node {op.node_id!r} vanished", op.node_id)
        target.setdefault("config", {})["__skipped"] = True
        return

    if op.kind == OpKind.DELETE:
        if not _remove_node(root, op.node_id):
            raise _ApplyError(
                "WF_MUT_DELETE_FAILED", f"could not remove node {op.node_id!r}", op.node_id
            )
        return

    if op.kind == OpKind.INSERT:
        parent = _find_node_dict(root, op.parent_id) if op.parent_id else root
        if parent is None:
            raise _ApplyError(
                "WF_MUT_UNKNOWN_PARENT", f"parent {op.parent_id!r} vanished", op.parent_id
            )
        children = parent.setdefault("children", [])
        if not isinstance(children, list):
            raise _ApplyError(
                "WF_MUT_NOT_A_CONTAINER",
                f"node {op.parent_id!r} cannot hold children",
                op.parent_id,
            )
        at = len(children) if op.index is None else max(0, min(int(op.index), len(children)))
        children.insert(at, copy.deepcopy(op.node or {}))
        return

    if op.kind == OpKind.MOVE:
        detached = _detach_node(root, op.node_id)
        if detached is None:
            raise _ApplyError(
                "WF_MUT_MOVE_FAILED", f"could not detach node {op.node_id!r}", op.node_id
            )
        parent = _find_node_dict(root, op.parent_id)
        if parent is None:
            raise _ApplyError(
                "WF_MUT_UNKNOWN_PARENT", f"parent {op.parent_id!r} vanished", op.parent_id
            )
        children = parent.setdefault("children", [])
        if not isinstance(children, list):
            raise _ApplyError(
                "WF_MUT_NOT_A_CONTAINER",
                f"node {op.parent_id!r} cannot hold children",
                op.parent_id,
            )
        at = len(children) if op.index is None else max(0, min(int(op.index), len(children)))
        children.insert(at, detached)
        return

    if op.kind == OpKind.INLINE_SUBWORKFLOW:
        # Needs the def registry to resolve the referenced body; Slice 10 owns nested
        # execution, so this is a typed refusal rather than a silent no-op.
        raise _ApplyError(
            "WF_MUT_UNSUPPORTED",
            "inline_subworkflow is not supported yet (nested runs land in Slice 10)",
            op.node_id,
        )


def _find_node_dict(node: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if node.get("id") == node_id:
        return node
    for child in _child_dicts(node):
        found = _find_node_dict(child, node_id)
        if found is not None:
            return found
    return None


def _child_dicts(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        if isinstance(child, dict):
            out.append(child)
    body = node.get("body")
    if isinstance(body, dict):
        out.append(body)
    for case in (node.get("cases") or {}).values():
        if isinstance(case, dict):
            out.append(case)
    default = node.get("default")
    if isinstance(default, dict):
        out.append(default)
    return out


def _remove_node(node: dict[str, Any], node_id: str) -> bool:
    children = node.get("children")
    if isinstance(children, list):
        for i, child in enumerate(children):
            if isinstance(child, dict) and child.get("id") == node_id:
                children.pop(i)
                return True
    for key in ("body", "default"):
        holder = node.get(key)
        if isinstance(holder, dict) and holder.get("id") == node_id:
            node.pop(key)
            return True
    cases = node.get("cases")
    if isinstance(cases, dict):
        for label, case in list(cases.items()):
            if isinstance(case, dict) and case.get("id") == node_id:
                cases.pop(label)
                return True
    for child in _child_dicts(node):
        if _remove_node(child, node_id):
            return True
    return False


def _detach_node(node: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """Remove and RETURN the node — move is a detach plus an insert, and doing it in two
    halves keeps the coordinate arithmetic in one place."""
    children = node.get("children")
    if isinstance(children, list):
        for i, child in enumerate(children):
            if isinstance(child, dict) and child.get("id") == node_id:
                return children.pop(i)  # type: ignore[no-any-return]
    for key in ("body", "default"):
        holder = node.get(key)
        if isinstance(holder, dict) and holder.get("id") == node_id:
            node.pop(key)
            return holder
    cases = node.get("cases")
    if isinstance(cases, dict):
        for label, case in list(cases.items()):
            if isinstance(case, dict) and case.get("id") == node_id:
                return cases.pop(label)  # type: ignore[no-any-return]
    for child in _child_dicts(node):
        found = _detach_node(child, node_id)
        if found is not None:
            return found
    return None


# ── the whole transaction ────────────────────────────────────────────────────


def prepare_batch(
    raw_ops: list[dict[str, Any]],
    spec: dict[str, Any],
    instances: dict[str, NodeInstance],
    *,
    effects: dict[str, Any] | None = None,
) -> BatchResult:
    """Parse → validate → apply-to-copy → re-validate the spec. Writes nothing.

    The caller (the controller's mutation queue, Slice 4b) commits `result.spec` only when
    `result.ok`. Re-validating the CANDIDATE is what catches a batch that is individually
    legal but collectively broken — a delete that orphans a binding, or a move that
    introduces a cycle.
    """
    result = BatchResult()
    ops, parse_issues = parse_batch(raw_ops)
    result.ops = ops
    result.issues.extend(parse_issues)

    try:
        root = Node.from_dict(spec.get("root") or {})
    except ValueError as exc:
        result.ok = False
        result.issues.append(Issue(code="WF_MUT_BAD_SPEC", message=f"unreadable spec: {exc}"))
        return result

    result.issues.extend(validate_batch(ops, root, instances))
    if result.issues:
        result.ok = False
        return result

    seeds = {o.node_id for o in ops if o.node_id}
    if any(o.kind == OpKind.SET_INPUT for o in ops):
        # An input override reaches every node that reads an input, so the cascade seeds
        # from the whole graph rather than from one node.
        seeds |= {n.id for _p, n in walk(root) if n.id}
    result.preview = cascade_preview(root, instances, seeds, effects=effects)

    candidate, apply_issues = apply_batch(ops, spec, instances)
    result.issues.extend(apply_issues)
    if apply_issues:
        result.ok = False
        return result

    from personalclaw.workflows.validator import validate_spec

    validation = validate_spec(candidate)
    if not validation.ok:
        result.ok = False
        for err in validation.errors:
            result.issues.append(
                Issue(
                    code=str(getattr(err, "code", "WF_MUT_INVALID_RESULT")),
                    message=f"resulting spec is invalid: {getattr(err, 'message', err)}",
                )
            )
        return result

    result.spec = candidate
    return result


# ── spec history ─────────────────────────────────────────────────────────────


def history_record(
    ops: list[Op],
    *,
    actor: str,
    version: int,
    spec: dict[str, Any],
    preview: CascadePreview | None = None,
) -> dict[str, Any]:
    """One audit-trail entry (WF2-R20 / safety-protocol #6).

    Carries the STRUCTURED ops rather than a textual diff: a later refiner needs to know
    what KIND of correction a human made, which a diff destroys. The spec hash lets a
    reader confirm the recorded ops produced the spec on disk.
    """
    from personalclaw.workflows.journal import hash_value

    return {
        "version": version,
        "actor": actor,
        "ops": [o.to_dict() for o in ops],
        "raw_ops": [o.raw for o in ops],
        "spec_hash": hash_value(spec),
        "preview": (preview or CascadePreview()).to_dict(),
    }


def next_epoch(instances: dict[str, NodeInstance], paths: list[str], *, force: bool) -> int:
    """The epoch a rewound region should carry.

    Bumped ONLY on force (WF2-R2 #4). Without force, the inputs-hash tier in the journal
    key decides: a rewind that did not change a node's inputs replays its cached output
    rather than paying to recompute the same answer. Bumping unconditionally would discard
    that memoization and re-run the expensive half of the graph for nothing.
    """
    current = max((instances[p].epoch for p in paths if p in instances), default=0)
    return current + 1 if force else current
