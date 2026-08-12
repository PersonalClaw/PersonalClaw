"""Spec validation — structural checks with typed, stable issue codes.

Two rules shape this module (WF2-R12):

* **It never throws on a malformed spec.** A validator that raises on the first problem
  forces an LLM author into one-error-per-turn ping-pong. This accumulates every issue
  and returns them together, so a repromptable error message can list all of them.
* **Codes are stable strings.** `WF_UNKNOWN_NODE_KIND` is a contract an agent branches
  on; rewording the human message must never change the code.

`strict` separates "this spec cannot run" from "this spec is questionable". Warnings
inform an author; errors block a save. The untrusted-origin lint (WF2-R9) is an ERROR by
design — that is the seam that stops trigger payloads flowing unfenced into prompts, and
making it advisory would leave it to template-author discipline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.bindings import BindingError, node_deps, refs_in
from personalclaw.workflows.models import (
    LLM_KINDS,
    GateKind,
    ItemErrorPolicy,
    JoinMode,
    LoopMode,
    Node,
    NodeKind,
    valid_name,
    walk,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class Issue:
    code: str
    message: str
    path: str = ""
    severity: str = SEVERITY_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    #: Kahn levels: nodes that may run concurrently. Empty when the spec has errors.
    levels: list[list[str]] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "levels": [list(lv) for lv in self.levels],
        }

    def summary(self) -> str:
        """One repromptable message listing every problem — the whole reason issues are
        accumulated rather than raised."""
        if self.ok and not self.warnings:
            return "Spec is valid."
        lines: list[str] = []
        for issue in self.issues:
            where = f" at {issue.path}" if issue.path else ""
            lines.append(f"[{issue.severity}] {issue.code}{where}: {issue.message}")
        return "\n".join(lines)


#: Config keys that carry a prompt an untrusted value must not reach unfenced.
_PROMPT_KEYS = frozenset({"prompt", "system", "instruction", "instructions", "message"})

#: Binding roots whose content is attacker-influenced: trigger payloads and fetched web
#: content. A ref from one of these into a prompt must pass a sanitization pipe.
_UNTRUSTED_ROOTS = frozenset({"trigger", "payload", "webhook", "fetched"})

#: Pipes that make untrusted content safe to interpolate.
_SANITIZING_PIPES = frozenset({"xml_escape", "truncate", "slugify", "json", "tojson"})

_MAX_DEPTH = 12
_MAX_NODES = 500


def _add(res: ValidationResult, code: str, msg: str, path: str = "", sev: str = SEVERITY_ERROR):
    res.issues.append(Issue(code=code, message=msg, path=path, severity=sev))


def validate_node_tree(root: Node, *, strict: bool = False) -> ValidationResult:
    """Validate a parsed node tree. Never raises."""
    res = ValidationResult()
    nodes = walk(root)

    if len(nodes) > _MAX_NODES:
        _add(
            res,
            "WF_SPEC_TOO_LARGE",
            f"spec has {len(nodes)} nodes; the cap is {_MAX_NODES}",
            "root",
        )

    ids_seen: dict[str, str] = {}
    for path, node in nodes:
        depth = path.count(".")
        if depth > _MAX_DEPTH:
            _add(res, "WF_SPEC_TOO_DEEP", f"nesting exceeds {_MAX_DEPTH}", path)

        # Duplicate ids break binding resolution — `{{nodes.x.output}}` becomes ambiguous.
        if node.id:
            if node.id in ids_seen:
                _add(
                    res,
                    "WF_DUPLICATE_NODE_ID",
                    f"node id {node.id!r} is already used at {ids_seen[node.id]}",
                    path,
                )
            else:
                ids_seen[node.id] = path

        _validate_shape(res, path, node, tree=dict(nodes))
        _validate_bindings(res, path, node, strict=strict)

    _validate_binding_targets(res, nodes, ids_seen)
    levels = _kahn_levels(res, nodes, ids_seen)
    if res.ok:
        res.levels = levels
    return res


def _in_reapable_parallel(path: str, tree: dict[str, Node]) -> bool:
    """Is this node inside a `parallel` that can reap it?

    `join: any` or a met-able `quorum`, AND at least one non-watcher sibling to be the leg that
    finishes. A parallel of nothing but watchers can never satisfy its own join, so it is just
    as immortal as a bare loop.
    """
    matches = list(re.finditer(r"\.children\[\d+\]", path))
    if not matches:
        return False
    parent_path = path[: matches[-1].start()]
    parent = tree.get(parent_path)
    if parent is None or parent.kind != NodeKind.PARALLEL:
        return False
    join = str((parent.config or {}).get("join", "all") or "all")
    if join not in (JoinMode.ANY.value, JoinMode.QUORUM.value):
        return False
    return any(
        not (
            c.kind == NodeKind.LOOP
            and str((c.config or {}).get("mode", "") or "") == LoopMode.UNTIL_CANCELLED.value
        )
        for c in parent.children
    )


def _has_wait(node: Node) -> bool:
    """Does this subtree contain a `wait`? Subtree, not direct child: the wait is normally
    inside the watcher body's `sequence`, which is the shape every template in the plan uses."""
    return any(n.kind == NodeKind.WAIT for _p, n in walk(node))


def _positive_int(raw: Any) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool) and raw > 0


def _validate_shape(
    res: ValidationResult, path: str, node: Node, *, tree: dict[str, Node] | None = None
) -> None:
    """Kind-specific structural requirements.

    `tree` is the whole path→node map, needed by the rules that are about a node's RELATIONSHIP
    to the spec around it — a watcher's reapability depends on its parent's join mode, which is
    unknowable from the node alone.
    """
    kind = node.kind
    cfg = node.config or {}

    if kind in (NodeKind.SEQUENCE, NodeKind.PARALLEL):
        if not node.children:
            _add(res, "WF_EMPTY_CONTAINER", f"{kind.value} has no children", path)
        if kind == NodeKind.PARALLEL:
            raw_join = str(cfg.get("join", "all") or "all")
            try:
                join = JoinMode(raw_join)
            except ValueError:
                _add(res, "WF_BAD_JOIN", f"unknown join mode {raw_join!r}", path)
                join = JoinMode.ALL
            if join == JoinMode.QUORUM:
                n = cfg.get("quorum")
                if not isinstance(n, int) or n < 1 or n > max(1, len(node.children)):
                    _add(
                        res,
                        "WF_BAD_QUORUM",
                        f"quorum must be an int in 1..{len(node.children)}",
                        path,
                    )
            # `needs` may only reference SIBLINGS — cross-container edges would make the
            # tree a graph and break the frontier's locality.
            sibling_ids = {c.id for c in node.children if c.id}
            for child in node.children:
                for need in child.needs:
                    if need not in sibling_ids:
                        _add(
                            res,
                            "WF_UNKNOWN_NEEDS",
                            f"needs {need!r} is not a sibling in this parallel block",
                            path,
                        )

    elif kind == NodeKind.FOREACH:
        if node.body is None:
            _add(res, "WF_MISSING_BODY", "foreach has no body", path)
        if not cfg.get("items"):
            _add(res, "WF_MISSING_ITEMS", "foreach needs an `items` binding", path)
        raw = str(cfg.get("on_item_error", "skip") or "skip")
        try:
            ItemErrorPolicy(raw)
        except ValueError:
            _add(res, "WF_BAD_ITEM_ERROR", f"unknown on_item_error {raw!r}", path)

    elif kind == NodeKind.LOOP:
        if node.body is None:
            _add(res, "WF_MISSING_BODY", "loop has no body", path)
        raw_mode = str(cfg.get("mode", "counted") or "counted")
        try:
            mode = LoopMode(raw_mode)
        except ValueError:
            _add(res, "WF_BAD_LOOP_MODE", f"unknown loop mode {raw_mode!r}", path)
            mode = LoopMode.COUNTED
        if mode == LoopMode.COUNTED:
            n = cfg.get("n")
            if not isinstance(n, int) or n < 1:
                _add(res, "WF_BAD_LOOP_COUNT", "counted loop needs a positive `n`", path)
        elif mode == LoopMode.UNTIL and not cfg.get("condition"):
            _add(res, "WF_MISSING_CONDITION", "until loop needs a `condition`", path)
        elif mode == LoopMode.UNTIL_DRY:
            streak = cfg.get("streak", 1)
            if not isinstance(streak, int) or streak < 1:
                _add(res, "WF_BAD_STREAK", "until_dry needs a positive `streak`", path)
        elif mode == LoopMode.UNTIL_CANCELLED:
            # A watcher has no self-terminating condition, so SOMETHING outside it must be able
            # to stop it. `reap_watchers` does that only inside a `join: any`/`quorum` parallel;
            # anywhere else the loop is immortal and the run never completes — a silent hang,
            # which is the worst outcome for an unattended run.
            if not _in_reapable_parallel(path, tree or {}) and not cfg.get("max_iterations"):
                _add(
                    res,
                    "WF_UNREAPABLE_WATCHER",
                    "until_cancelled needs either a `join: any`/`quorum` parallel sibling to "
                    "reap it or a `max_iterations` cap — otherwise the run never ends",
                    path,
                )
            body = node.body
            if body is not None and not _has_wait(body):
                # A watcher with no wait in its body spins as fast as the model answers, which
                # burns a whole budget in minutes. The one long-run failure that is expensive
                # rather than merely slow.
                _add(
                    res,
                    "WF_WATCHER_NO_WAIT",
                    "until_cancelled body has no `wait` — it will cycle as fast as the model "
                    "responds and exhaust the run budget",
                    path,
                )

    elif kind == NodeKind.BRANCH:
        if not cfg.get("on"):
            _add(res, "WF_MISSING_ON", "branch needs an `on` binding", path)
        if not node.cases:
            _add(res, "WF_EMPTY_BRANCH", "branch has no cases", path)
        # Case coverage: when the author declares the enum, every value needs a case
        # (or a default). Unmatched-at-runtime is a typed BindingError, and catching it
        # at validation time is much cheaper than mid-run.
        enum = cfg.get("enum")
        if isinstance(enum, list) and enum:
            missing = [str(v) for v in enum if str(v) not in node.cases]
            if missing and node.default_case is None:
                _add(
                    res,
                    "WF_BRANCH_COVERAGE",
                    f"no case for {', '.join(missing)} and no default",
                    path,
                )

    elif kind in LLM_KINDS:
        if not cfg.get("prompt"):
            _add(res, "WF_MISSING_PROMPT", f"{kind.value} needs a `prompt`", path)
        tier = cfg.get("model_tier")
        if tier is not None and tier not in ("reasoning", "standard", "fast"):
            _add(
                res,
                "WF_BAD_MODEL_TIER",
                f"model_tier {tier!r} must be reasoning|standard|fast",
                path,
            )

    elif kind == NodeKind.TRANSFORM:
        if not cfg.get("expr"):
            _add(res, "WF_MISSING_EXPR", "transform needs an `expr` binding", path)

    elif kind == NodeKind.VISUALIZE:
        # The agency-free data→genui primitive (AMBIENT-SURFACES §5.3): its input is a
        # `data` binding, not a `prompt` (it is pinned to the reasoning axis, so no
        # model_tier either). Missing `data` fails at run time with an unhelpful binding
        # error, so catch the authoring mistake here.
        if not cfg.get("data"):
            _add(res, "WF_MISSING_DATA", "visualize needs a `data` binding", path)

    elif kind == NodeKind.ACTION:
        if not cfg.get("provider"):
            _add(res, "WF_MISSING_PROVIDER", "action needs a `provider`", path)
        elif "with" not in cfg and "config" not in cfg:
            # The engine reads a provider's arguments from `config.with` (`dispatch_action`).
            # Arguments written FLAT beside `provider` reach the provider as an empty config, and
            # it then reports its own required field missing — for a value that is visibly right
            # there in the spec. Caught here because the run-time symptom points at the provider
            # rather than at the authoring mistake, and because everything downstream of the
            # failed action then fails on an unresolved binding, burying the cause.
            extras = [k for k in cfg if k not in ("provider", "context", "payload")]
            if extras:
                # Arguments ARE present, just in the wrong place — the run would fail, so this is
                # an error naming exactly what to move.
                _add(
                    res,
                    "WF_ACTION_ARGS_NOT_NESTED",
                    "action arguments go under `config.with` — move "
                    + ", ".join(sorted(extras))
                    + " into it",
                    path,
                )
            else:
                # No arguments at all: legitimate for a provider that needs none, so a warning
                # rather than a refusal.
                _add(
                    res,
                    "WF_ACTION_NO_ARGS",
                    "action has no `config.with` — fine if the provider needs no arguments",
                    path,
                    SEVERITY_WARNING,
                )

    elif kind == NodeKind.WAIT:
        seal = cfg.get("seal")
        if seal is not None and not isinstance(seal, dict):
            _add(res, "WF_BAD_SEAL", "wait `seal` must be an object", path)
            seal = None
        if isinstance(seal, dict):
            # One mistake, one issue. An empty `seal: {}` previously reported three
            # (missing-wait + bad-seal + no-flush), and three issues for one typo is how a
            # validation report stops being read.
            if not _positive_int(seal.get("threshold")) and not _positive_int(seal.get("tokens")):
                _add(
                    res,
                    "WF_BAD_SEAL",
                    "buffer seal needs a positive `threshold` (items) or `tokens`",
                    path,
                )
            elif not _positive_int(seal.get("flush_stale_after_secs")):
                # Without a stale flush, a slow trickle never seals: the buffer sits below
                # threshold indefinitely and the synthesis the watcher exists for never runs.
                # Nothing errors — the watcher just quietly does nothing, forever.
                _add(
                    res,
                    "WF_SEAL_NO_FLUSH",
                    "buffer seal has no `flush_stale_after_secs` — a trickle of items would "
                    "never reach the threshold and never synthesize",
                    path,
                    SEVERITY_WARNING,
                )
        elif not (cfg.get("duration_secs") or cfg.get("until_ts")):
            _add(res, "WF_MISSING_WAIT", "wait needs duration_secs, until_ts or seal", path)

    elif kind == NodeKind.GATE:
        raw = str(cfg.get("kind", "") or "")
        try:
            gate = GateKind(raw)
        except ValueError:
            _add(res, "WF_BAD_GATE_KIND", f"unknown gate kind {raw!r}", path)
            gate = None
        if gate in (GateKind.VERIFY_COMMAND, GateKind.VERIFY_SCRIPT) and not cfg.get("verify"):
            _add(res, "WF_MISSING_VERIFY", f"{raw} gate needs a `verify` block", path)
        if gate == GateKind.EXPRESSION and not cfg.get("expr"):
            _add(res, "WF_MISSING_EXPR", "expression gate needs an `expr`", path)

    elif kind == NodeKind.SUBWORKFLOW:
        ref = str(cfg.get("ref", "") or "")
        if not ref:
            _add(res, "WF_MISSING_REF", "subworkflow needs a `ref`", path)
        else:
            base = ref.split("@", 1)[0]
            if not valid_name(base):
                _add(res, "WF_BAD_REF", f"subworkflow ref {ref!r} is not a valid name", path)


def _validate_bindings(res: ValidationResult, path: str, node: Node, *, strict: bool) -> None:
    """Parse every binding and apply the untrusted-origin lint."""
    for key, value in (node.config or {}).items():
        for expr in refs_in(value):
            # Malformed pipes / unknown pipes are caught by parsing the chain.
            for raw_pipe in [p.strip() for p in expr.split("|")[1:]]:
                name = raw_pipe.split("(", 1)[0].strip()
                from personalclaw.workflows.bindings import PIPES

                if name and name not in PIPES:
                    _add(res, "WF_UNKNOWN_PIPE", f"unknown pipe {name!r}", path)

            head = expr.split("|")[0].strip()
            root_seg = head.split(".")[0].strip()

            # The untrusted-origin lint (WF2-R9). An ERROR, not a warning: this is the
            # mechanical seam that keeps trigger payloads from reaching a prompt
            # unfenced, and advisory-only would leave it to author discipline.
            if key in _PROMPT_KEYS and root_seg in _UNTRUSTED_ROOTS:
                pipes = {p.split("(", 1)[0].strip() for p in expr.split("|")[1:]}
                if not (pipes & _SANITIZING_PIPES):
                    _add(
                        res,
                        "WF_UNFENCED_UNTRUSTED",
                        (
                            f"{root_seg!r} is untrusted input flowing into {key!r} "
                            "unsanitized — add a sanitization pipe "
                            "(xml_escape/truncate/json)"
                        ),
                        path,
                    )

            if (
                strict
                and root_seg
                not in (
                    "inputs",
                    "nodes",
                    "item",
                    "iter",
                    "last",
                )
                and not head.startswith("secret:")
                and root_seg not in _UNTRUSTED_ROOTS
            ):
                if head.startswith("block:"):
                    # A shared block reference (WF2-R15) that was never resolved. Reported as
                    # ITSELF rather than as an unknown binding root: the two are resolved at
                    # different times (blocks at authoring, bindings at run), so "not a known
                    # source" sends the author looking for a node that was never the problem.
                    # Reaching the validator at all means the resolve step was skipped or the
                    # block does not exist — both worth naming precisely.
                    _add(
                        res,
                        "WF_UNRESOLVED_BLOCK",
                        f"shared block reference {head!r} was not resolved — the block may not "
                        "exist, or this spec bypassed the authoring path that substitutes them",
                        path,
                        SEVERITY_WARNING,
                    )
                else:
                    _add(
                        res,
                        "WF_UNKNOWN_BINDING_ROOT",
                        f"binding root {root_seg!r} is not a known source",
                        path,
                        SEVERITY_WARNING,
                    )

    # Inline secret-shaped values (WF2-R14): a literal key in a spec would be persisted
    # to the journal and later read by the flywheel.
    for key, value in (node.config or {}).items():
        if isinstance(value, str) and _looks_like_secret(value):
            _add(
                res,
                "WF_INLINE_SECRET",
                f"{key!r} looks like an inline credential — use {{{{secret:KEY}}}}",
                path,
            )


def _looks_like_secret(value: str) -> bool:
    """Delegates to `secrets.py`, the ONE credential-shape list.

    This function used to carry its own prefix tuple. Two lists of vendor key shapes
    inevitably drift — one gets a new provider and the other silently stops catching it —
    and "which lint runs where" becomes unanswerable. The shapes live in `secrets.py`
    (the documented provider-boundary keep) and both the save-time lint and this
    validator read them from there.
    """
    from personalclaw.workflows.secrets import looks_like_credential

    v = value.strip()
    if " " in v:
        return False
    return looks_like_credential(v)


def _validate_binding_targets(
    res: ValidationResult, nodes: list[tuple[str, Node]], ids: dict[str, str]
) -> None:
    """Every `{{nodes.<id>…}}` must name a node that exists. A typo here is a run that
    fails at ready-time with a BindingError; catching it now is free."""
    for path, node in nodes:
        for dep in node_deps(node.config or {}):
            if dep not in ids:
                _add(
                    res,
                    "WF_UNKNOWN_NODE_REF",
                    f"binding references unknown node id {dep!r}",
                    path,
                )


def _kahn_levels(
    res: ValidationResult, nodes: list[tuple[str, Node]], ids: dict[str, str]
) -> list[list[str]]:
    """Group node ids into DATA-dependency levels, and detect cycles.

    Edges come from BINDINGS (`{{nodes.a.output}}`) and from `needs` only. This is not a
    schedule: container semantics (a `sequence` running children in order, a `loop`
    repeating its body) are the frontier's job in Slice 1, so a container with no
    bindings correctly lands in level 0 beside nodes it structurally precedes.

    The purpose here is narrower and worth keeping narrow — reject a spec whose data
    dependencies are cyclic BEFORE it can deadlock a run, and give an author a rough
    concurrency picture. Reading these levels as an execution order would be wrong.
    """
    deps: dict[str, set[str]] = {nid: set() for nid in ids}
    for path, node in nodes:
        if not node.id:
            continue
        for dep in node_deps(node.config or {}):
            if dep in ids:
                deps[node.id].add(dep)
        for need in node.needs:
            if need in ids:
                deps[node.id].add(need)

    levels: list[list[str]] = []
    remaining = dict(deps)
    resolved: set[str] = set()
    while remaining:
        ready = sorted(n for n, d in remaining.items() if not (d - resolved))
        if not ready:
            cyc = sorted(remaining)
            _add(
                res,
                "WF_CYCLE",
                f"dependency cycle among: {', '.join(cyc)}",
                ids.get(cyc[0], "root") if cyc else "root",
            )
            return []
        levels.append(ready)
        resolved.update(ready)
        for n in ready:
            remaining.pop(n, None)
    return levels


def validate_spec(spec: dict[str, Any], *, strict: bool = False) -> ValidationResult:
    """Validate a raw spec dict — the entry point for authoring tools.

    Parsing is part of validation: an unknown node kind or an absent root is reported as
    an issue rather than propagated as an exception, because the caller is usually
    handing the result straight back to an LLM author.
    """
    res = ValidationResult()
    if not isinstance(spec, dict):
        _add(res, "WF_NOT_AN_OBJECT", "spec must be a JSON object")
        return res

    name = str(spec.get("name", "") or "")
    if name and not valid_name(name):
        _add(
            res,
            "WF_BAD_NAME",
            "name must be lowercase alphanumeric with hyphens, 1-63 chars",
        )

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        _add(res, "WF_MISSING_ROOT", "spec needs a `root` node")
        return res

    try:
        root = Node.from_dict(root_raw)
    except ValueError as exc:
        _add(res, "WF_UNKNOWN_NODE_KIND", str(exc), "root")
        return res
    except BindingError as exc:  # pragma: no cover — defensive
        _add(res, "WF_BAD_BINDING", str(exc), "root")
        return res

    tree_res = validate_node_tree(root, strict=strict)
    res.issues.extend(tree_res.issues)
    _validate_wip_invariant(res, spec, root)
    res.levels = tree_res.levels if res.ok else []
    return res


def _validate_wip_invariant(res: ValidationResult, spec: dict[str, Any], root: Node) -> None:
    """Refuse a spec that declares WIP=1 and also declares a wider fan-out (R5b).

    `single_active_feature` is a RUN-level invariant, so the runtime enforces it whatever a
    `foreach` says for itself (see `tick._visit_foreach`). That leaves one bad outcome:
    a template that reads `max_concurrency: 3` while the engine runs it one at a time. A
    reader believes the template, so the contradiction is refused HERE rather than silently
    clamped at runtime — an author who wants three at a time has to drop the invariant, and
    an author who wants the invariant has to stop claiming three.
    """
    from personalclaw.workflows.execution_hints import from_runtime_hints

    if not from_runtime_hints(spec.get("runtime_hints")).single_active_feature:
        return
    for path, node in walk(root):
        if node.kind is not NodeKind.FOREACH:
            continue
        raw = (node.config or {}).get("max_concurrency")
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        if raw > 1:
            _add(
                res,
                "WF_WIP_CONTRADICTION",
                (
                    f"`single_active_feature` declares WIP=1, but this foreach declares "
                    f"max_concurrency={raw} — the engine will run one item at a time, so the "
                    "declaration is false. Drop one of the two."
                ),
                path,
            )
