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
    # ONE edge list, three rules and one scheduler over it: `PP-1` asks whether the producer
    # can be ordered first, `PP-3` whether the path the reader takes through its output can
    # exist, `PP-2` whether a hand-written `needs` agrees with what the bindings already say —
    # and `tick.ordering_for` re-reads the same derivation to admit work. Deriving it twice is
    # the defect all three atoms exist to remove.
    edges = dep_ordering_edges(nodes, ids_seen)
    _validate_dep_ordering(res, edges)
    _validate_output_contract(res, nodes, edges)
    _validate_needs(res, edges)
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
            # `needs` is no longer sibling-scoped (`PP-2`). The reason the restriction gave —
            # "cross-container edges would make the tree a graph and break the frontier's
            # locality" — stopped holding once ordering is DERIVED from bindings: the frontier
            # already resolves every producer against the whole (global) state map, so a
            # cross-container edge is honoured, not refused. Existence is checked globally in
            # `_validate_binding_targets`; whether the edge can be HONOURED is `_validate_needs`.

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
        # A loop MAY declare a `SupervisorPolicy` (PP-14). It is authoring-time validated but
        # deliberately not yet read by the engine — PP-15 is the wiring owner.
        if "supervisor" in cfg:
            _validate_supervisor(res, path, cfg.get("supervisor"))

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
        # Either form is a transform: an `expr` binding, or a `skeleton` artifact whose stored
        # body IS the template (AMBIENT-SURFACES §2.1's render transform). Requiring `expr`
        # unconditionally would make the skeleton form unauthorable — and inlining a whole
        # dashboard body into a spec is exactly the layout/data split the skeleton exists to keep.
        if not cfg.get("expr") and not cfg.get("skeleton"):
            _add(
                res,
                "WF_MISSING_EXPR",
                "transform needs an `expr` binding (or a `skeleton` artifact to render)",
                path,
            )

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


def _validate_supervisor(res: ValidationResult, path: str, raw: Any) -> None:
    """Authoring-time validation of a loop's `SupervisorPolicy` declaration (PP-14).

    The closed field set IS the contract: an UNKNOWN top-level field is an error, while a
    missing one is tolerated by the parser's defaults. Bad enum VALUES are flagged too, each
    against the vocabulary that already owns it (reused, never re-minted). This is a pure
    authoring check — nothing here reads a parsed policy, and the engine is unchanged (PP-15
    is the wiring owner). Imported lazily so `validator` stays import-cheap and cycle-free.
    """
    # Local import: keeps the declaration module (and its transitive loop/judge imports) off
    # `validator`'s import path, matching the lazy-import pattern already used for PIPES below.
    from personalclaw.workflows.supervisor_policy import (
        FAILURE_CLASS_VALUES,
        HITL_POSTURE_VALUES,
        LADDER_RUNG_VALUES,
        POLICY_FIELDS,
        SUPERVISOR_MODEL_TIERS,
    )

    if not isinstance(raw, dict):
        _add(res, "WF_SUPERVISOR_NOT_OBJECT", "supervisor must be an object", path)
        return
    for key in raw:
        if key not in POLICY_FIELDS:
            _add(res, "WF_SUPERVISOR_UNKNOWN_FIELD", f"unknown supervisor field {key!r}", path)
    tier = raw.get("judge_model_tier")
    if tier is not None and str(tier) not in SUPERVISOR_MODEL_TIERS:
        _add(
            res,
            "WF_SUPERVISOR_BAD_TIER",
            f"judge_model_tier {tier!r} must be reasoning|standard|fast",
            path,
        )
    ladder = raw.get("escalation_ladder")
    if isinstance(ladder, list):
        for rung in ladder:
            if str(rung) not in LADDER_RUNG_VALUES:
                _add(res, "WF_SUPERVISOR_BAD_RUNG", f"unknown escalation rung {rung!r}", path)
    mutations = raw.get("failure_mutations")
    if isinstance(mutations, dict):
        for cls in mutations:
            if str(cls) not in FAILURE_CLASS_VALUES:
                _add(
                    res,
                    "WF_SUPERVISOR_BAD_FAILURE_CLASS",
                    f"unknown failure class {cls!r}",
                    path,
                )
    hitl = raw.get("hitl_posture")
    if hitl is not None and str(hitl) not in HITL_POSTURE_VALUES:
        _add(res, "WF_SUPERVISOR_BAD_HITL", f"hitl_posture {hitl!r} must be afk|hitl", path)


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
    """Every `{{nodes.<id>…}}` and every `needs` entry must name a node that exists.

    A typo in a binding is a run that fails at ready-time with a BindingError; catching it
    now is free.

    `needs` is resolved against the WHOLE spec (`PP-2`), not against the enclosing
    `parallel`'s siblings. The sibling-only rule this replaces refused a cross-container edge
    outright, on the grounds that it "would make the tree a graph and break the frontier's
    locality" — but the frontier now honours a derived ordering edge between any two nodes, so
    the graph is global by construction and the only thing left to check here is that the
    target EXISTS. Whether the edge can be HONOURED is `_validate_needs`' job.
    """
    for path, node in nodes:
        for dep in node_deps(node.config or {}):
            if dep not in ids:
                _add(
                    res,
                    "WF_UNKNOWN_NODE_REF",
                    f"binding references unknown node id {dep!r}",
                    path,
                )
        for need in node.needs:
            if need not in ids:
                _add(
                    res,
                    "WF_UNKNOWN_NEEDS",
                    f"needs {need!r} names no node in this spec",
                    path,
                )


#: A `DepEdge` derived from a `{{nodes.<producer>…}}` reference — dataflow.
EDGE_BINDING = "binding"
#: A `DepEdge` derived from a hand-written `needs` — ordering the author asserted, which may
#: or may not correspond to any dataflow (`PP-2`).
EDGE_NEEDS = "needs"


@dataclass(frozen=True)
class DepEdge:
    """One ordering edge between two nodes, and whether the engine can honour it.

    ONE list carries both origins (`PP-2`): a `{{nodes.<producer>…}}` binding and a
    hand-written `needs` are the same relation seen from two sides, and deriving them
    separately is precisely the two-edge-lists defect this plan exists to remove. Every
    consumer — the ordering rule, the contract rule, the `needs` cross-check and the
    frontier's admission gate — reads this one list and filters on `origin`.

    Materialized as a value rather than turned straight into an issue, because a rule that
    examined no edges is indistinguishable from a rule that examined many and liked them
    all — both report nothing. Only a caller that can count the edges considered can tell
    the two apart, which is what the vacuity floor in the tests does.
    """

    reader_path: str
    #: The reader's author-facing id, or `""` — `id` is optional on every node.
    reader_id: str
    producer_path: str
    producer_id: str
    #: Can the engine hold the reader until the producer is terminal? False only when the
    #: spec's own structure makes that impossible (see `_ordering_verdict`).
    ordered: bool
    #: The satisfying structure when `ordered`, the contradiction when not.
    reason: str
    #: The distinct paths this reader takes THROUGH the producer's output, as segment
    #: tuples: `{{nodes.p.output.a.b}}` → `("a", "b")`, a bare `{{nodes.p.output}}` → `()`.
    #: Empty overall when the binding never reaches `output` at all (`{{nodes.p.artifact}}`
    #: is a dependency but not an output read, and `output_contract` says nothing about it).
    #: Always empty for an `EDGE_NEEDS` edge — a `needs` reads nothing.
    output_reads: tuple[tuple[str, ...], ...] = ()
    #: `EDGE_BINDING` or `EDGE_NEEDS`.
    origin: str = EDGE_BINDING


def _parent_slots(nodes: list[tuple[str, Node]]) -> dict[str, tuple[str, str, int, str]]:
    """child path → (parent path, slot, child index, case label).

    Derived from the walk output rather than by splitting the path strings back apart: a
    `cases[<label>]` label is author-supplied and may itself contain a dot or a bracket, so
    parsing a path is not safe. Child paths are re-derived exactly as `walk` derives them,
    which keeps the two constructions from drifting.
    """
    out: dict[str, tuple[str, str, int, str]] = {}
    for path, node in nodes:
        for i, child in enumerate(node.children):
            out[f"{path}.children[{i}]"] = (path, "children", i, "")
        if node.body is not None:
            out[f"{path}.body"] = (path, "body", -1, "")
        for label, _case in node.cases.items():
            out[f"{path}.cases[{label}]"] = (path, "cases", -1, label)
        if node.default_case is not None:
            out[f"{path}.default"] = (path, "default", -1, "default")
    return out


def _chain(path: str, parents: dict[str, tuple[str, str, int, str]]) -> list[str]:
    """`[path, parent, …, root]`. Bounded by `_MAX_DEPTH`-ish tree height in practice, and
    by the visited set against a malformed map."""
    out = [path]
    seen = {path}
    cur = path
    while cur in parents:
        cur = parents[cur][0]
        if cur in seen:  # pragma: no cover — defensive; `walk` cannot produce a cycle
            break
        seen.add(cur)
        out.append(cur)
    return out


def _slot_label(slot: tuple[str, str, int, str]) -> str:
    """How an author would point at this slot in the spec."""
    _parent, kind, index, label = slot
    if kind == "children":
        return f"child {index}"
    if kind == "cases":
        return f"case {label!r}"
    if kind == "default":
        return "the default case"
    return "the body"


def _needs_reaches(parent: Node, from_idx: int, to_idx: int) -> bool:
    """Does child `from_idx` transitively `needs` child `to_idx` of this parallel?

    Only the parallel's DIRECT children are consulted, because that is exactly what
    admission consults (`tick._visit_parallel` gates on `child.needs`). A `needs` declared
    anywhere else is inert, and treating it as an ordering edge here would make the
    validator promise something the scheduler does not deliver.
    """
    by_id = {c.id: i for i, c in enumerate(parent.children) if c.id}
    if not parent.children[to_idx].id:
        return False  # nothing can `needs` an anonymous child
    seen = {from_idx}
    stack = [from_idx]
    while stack:
        for need in parent.children[stack.pop()].needs:
            nxt = by_id.get(need)
            if nxt is None or nxt in seen:
                continue
            if nxt == to_idx:
                return True
            seen.add(nxt)
            stack.append(nxt)
    return False


def _ordering_verdict(
    reader_path: str,
    producer_path: str,
    tree: dict[str, Node],
    parents: dict[str, tuple[str, str, int, str]],
) -> tuple[bool, str]:
    """CAN the engine hold `reader_path` until `producer_path` is terminal?

    The comparison happens at the DIVERGENCE POINT — the lowest common ancestor — and
    between the two branches of it that contain the reader and the producer, not between
    the two nodes themselves. That is what makes "an earlier sibling within an enclosing
    `sequence`" mean what an author expects: a producer buried inside an earlier sibling
    (in a `parallel`, a `foreach` body, a `branch` case) is still terminal when that
    sibling is, because a container completes only after its children do.

    **`PP-2` widened the question this answers.** It used to be "does the spec ALREADY order
    the producer first", where the only orderings that counted were container order and a
    sibling `needs` — so two concurrent legs of a `parallel` were unordered and the author had
    to hand-write the edge. The frontier now enforces the derived edge itself
    (`tick.ordering_for`), so concurrency is no longer a refusal: the reader simply waits.
    What remains False is the set of shapes where NO amount of waiting helps, because the
    structure itself contradicts the edge — the producer encloses or is enclosed by the
    reader, a `sequence` runs it after the reader, or a `branch` makes the two exclusive.
    Those are the deadlocks, and refusing them at authoring time is the whole point.

    Deliberately NOT decided here: whether the producer actually RAN. A producer inside a
    taken-or-not `branch` case is orderable either way; the frontier resolves the liveness
    half from state at run time by skipping a reader whose producer went SKIPPED.
    """
    if reader_path == producer_path:
        return False, "the binding reads the reader's own output"

    r_chain = _chain(reader_path, parents)
    p_chain = _chain(producer_path, parents)
    if producer_path in r_chain:
        return False, (
            f"it ENCLOSES the reader ({producer_path}), and a container's output is not "
            "available until after the children that produce it"
        )
    if reader_path in p_chain:
        return False, f"it runs INSIDE the reader ({producer_path}), so it cannot precede it"

    r_index = {p: i for i, p in enumerate(r_chain)}
    lca = ""
    p_branch = producer_path
    for cur in p_chain[1:]:
        if cur in r_index:
            lca = cur
            break
        p_branch = cur
    if not lca:  # pragma: no cover — both chains end at `root`, so an LCA always exists
        return False, "the reader and the producer are in unrelated trees"
    r_branch = r_chain[r_index[lca] - 1]

    parent = tree.get(lca)
    p_slot, r_slot = parents[p_branch], parents[r_branch]
    kind = parent.kind if parent is not None else None
    both_children = p_slot[1] == "children" and r_slot[1] == "children"

    if kind is NodeKind.SEQUENCE and both_children:
        if p_slot[2] < r_slot[2]:
            return True, f"the sequence at {lca} runs child {p_slot[2]} before child {r_slot[2]}"
        return False, (
            f"the sequence at {lca} runs it AFTER the reader (child {p_slot[2]} vs child "
            f"{r_slot[2]}) — move it before the reader"
        )

    if kind is NodeKind.PARALLEL and both_children:
        assert parent is not None
        if _needs_reaches(parent, r_slot[2], p_slot[2]):
            return True, f"a `needs` chain inside the parallel at {lca}"
        # Concurrent legs of a parallel. Before `PP-2` this was a refusal telling the author
        # to hand-write `needs` — and it could only ever be satisfied by an edge between the
        # parallel's DIRECT children, which is why a diamond whose legs rejoin inside nested
        # containers was inexpressible. The frontier holds the reader on this edge now, so the
        # parallel's concurrency is a fact about the container, not about the edge.
        return True, (
            f"the ordering edge holds the reader until it finishes, across the concurrent "
            f"legs of the parallel at {lca}"
        )

    if kind is NodeKind.BRANCH:
        return False, (
            f"the branch at {lca} runs {_slot_label(p_slot)} and {_slot_label(r_slot)} "
            "exclusively — only one of the two ever runs"
        )

    return False, (
        f"nothing can order {_slot_label(p_slot)} before {_slot_label(r_slot)} in the "
        f"{kind.value if kind else 'node'} at {lca}"
    )


def _output_reads(config: dict[str, Any]) -> dict[str, tuple[tuple[str, ...], ...]]:
    """producer id → the distinct paths this config reads under that producer's `output`.

    Built on `bindings.refs_in`, the same scan `node_deps` is built on, so the paths and the
    dependency set cannot disagree about which producers a node reads — the whole point of
    hanging this off `DepEdge` rather than deriving a second reference list.

    A bare `{{nodes.p.output}}` yields the EMPTY tuple: it is a read, but there is no path
    to check against a contract. A ref that never reaches `output` yields nothing at all.
    """
    out: dict[str, list[tuple[str, ...]]] = {}
    for expr in refs_in(config):
        head = expr.split("|")[0].strip()
        segs = [s for s in head.split(".") if s]
        if len(segs) < 3 or segs[0] != "nodes" or segs[2] != "output":
            continue
        paths = out.setdefault(segs[1], [])
        path = tuple(segs[3:])
        if path not in paths:  # the same ref may appear under several config keys
            paths.append(path)
    return {pid: tuple(paths) for pid, paths in out.items()}


def dep_ordering_edges(nodes: list[tuple[str, Node]], ids: dict[str, str]) -> list[DepEdge]:
    """THE ordering graph: every binding-derived dependency plus every declared `needs`.

    Uses `bindings.node_deps` — the same derivation the resume cache, the stale-inputs
    check and the mutation cascade use — so this rule cannot disagree with them about what
    a binding depends on. A reference to an unknown id is skipped: `WF_UNKNOWN_NODE_REF`
    (and `WF_UNKNOWN_NEEDS`) already own that, and reporting both would make one typo two
    errors.

    Both origins land in ONE list because there is one relation here, not two (`PP-2`). The
    engine used to keep a hand-maintained `needs` list for admission and a binding graph for
    everything else, with nothing checking they agreed; that disagreement is what `PP-1` made
    visible and what this deletes. `frontier()` consumes this same list, so a reader cannot be
    admitted under one notion of "ordered first" while the validator checked another.
    """
    parents = _parent_slots(nodes)
    tree = dict(nodes)
    out: list[DepEdge] = []
    for path, node in nodes:
        config = node.config or {}
        reads = _output_reads(config)
        for dep in sorted(node_deps(config)):
            producer_path = ids.get(dep)
            if producer_path is None:
                continue
            ordered, reason = _ordering_verdict(path, producer_path, tree, parents)
            out.append(
                DepEdge(path, node.id, producer_path, dep, ordered, reason, reads.get(dep, ()))
            )
        for need in node.needs:
            producer_path = ids.get(need)
            if producer_path is None:
                continue
            ordered, reason = _ordering_verdict(path, producer_path, tree, parents)
            out.append(DepEdge(path, node.id, producer_path, need, ordered, reason, (), EDGE_NEEDS))
    return out


def dep_edges_for_root(root: Node) -> list[DepEdge]:
    """`dep_ordering_edges` for a whole tree — the shape a caller outside the validator has.

    Mirrors `validate_node_tree`'s first-wins id map, so an inspection and a validation
    cannot disagree about which node a duplicated id names (the duplicate itself is
    `WF_DUPLICATE_NODE_ID`'s business).
    """
    nodes = walk(root)
    ids: dict[str, str] = {}
    for path, node in nodes:
        if node.id and node.id not in ids:
            ids[node.id] = path
    return dep_ordering_edges(nodes, ids)


def _validate_dep_ordering(res: ValidationResult, edges: list[DepEdge]) -> None:
    """Refuse a binding whose producer is not ordered before its reader (`PP-1`).

    The engine keeps two edge lists and nothing checked they agree. Admission reads
    `needs` and container order only (`tick._visit_parallel`); bindings are a separate
    graph feeding the resume cache, the stale-inputs check and the mutation cascade. So a
    spec could bind `{{nodes.x.output}}` from a node that runs beside — or before — `x`,
    and the run died at ready-time with *"binding failed: check the referenced node id and
    field exist"* pointing the author at an id that was perfectly correct. Locally
    plausible, globally wrong, and only discoverable by running it.

    `PP-1` added no runtime behaviour — the check existed purely so the disagreement was a
    typed authoring-time refusal instead of a mid-run failure. `PP-2` then removed the
    disagreement itself by making the frontier consume this very list.

    Runs AFTER `_kahn_levels` and stays quiet when a cycle was found: on a cyclic graph the
    only advice this rule can give ("order the producer first") is the advice that closes
    the loop, so `WF_CYCLE` is both the truer fact and the one an author must fix first.

    **`PP-2` narrowed what reaches this rule.** The frontier now holds a reader until the
    producers its bindings name are terminal, so "they run concurrently" is no longer an
    unordered edge — it is an edge the scheduler honours. What is left is the set of
    structural contradictions no wait can resolve, where the run would hang instead of
    failing at ready-time. The rule did not become weaker; the engine grew the guarantee the
    rule used to have to demand from the author. `EDGE_NEEDS` edges are `_validate_needs`'
    business, not this rule's.
    """
    if any(i.code == "WF_CYCLE" for i in res.issues):
        return
    for edge in edges:
        if edge.ordered or edge.origin != EDGE_BINDING:
            continue
        reader = repr(edge.reader_id) if edge.reader_id else edge.reader_path
        _add(
            res,
            "WF_UNORDERED_DEP",
            (
                f"{reader} binds {{{{nodes.{edge.producer_id}…}}}}, but nothing guarantees "
                f"{edge.producer_id!r} has finished when {reader} is admitted: {edge.reason}"
            ),
            edge.reader_path,
        )


def _validate_needs(res: ValidationResult, edges: list[DepEdge]) -> None:
    """Check a hand-written `needs` AGAINST the derived set instead of trusting it (`PP-2`).

    `needs` used to be the engine's only admission input and was refused unless it named a
    sibling of the same `parallel`. Now that ordering is derived from bindings, a `needs` has
    exactly one job left: to express ordering that is NOT dataflow — a lock, an external
    side-effect sequence, "publish only after the announcement went out". Two things follow.

    **A `needs` the structure cannot honour is an ERROR.** `needs: ["later"]` on the first
    child of a `sequence` that runs `later` third is not an ordering, it is a contradiction:
    the frontier would hold the reader for a producer the sequence will not start until the
    reader finishes, and the run hangs. Before `PP-2` this was invisible — `needs` outside a
    `parallel` was inert, so the author's declared ordering silently did nothing at all.
    Honouring it globally is what turns that dead declaration into either a real edge or a
    real error, and the error is much the better of the two.

    **A `needs` the bindings ALREADY imply is a WARNING.** `needs: ["a"]` on a node whose own
    config reads `{{nodes.a.output}}` is a second edge list one entry long: it restates what
    the binding says, and the two can now only ever drift apart. Deleting it changes nothing
    about the schedule, which is what makes this the safe half to report.

    **Deviation from the plan's step 3, recorded deliberately.** The plan asked for the
    opposite warning — on a `needs` ABSENT from the derived set — as "either real non-dataflow
    ordering or a stale edge". But absence is now the field's ONLY legitimate use, so that
    warning would fire on every correct `needs` in the tree while carrying no way to say "yes,
    I meant it". `PP-3`'s warning in this same file was scoped for exactly that reason: a rule
    that fires on the normal case is how an author learns to skim validator output. The origin
    tag is on every `DepEdge`, so an inspection surface can still show which `needs` carry no
    dataflow without spending the author's attention on it at every save.

    Censused before shipping: ZERO of the bundled templates declare a `needs` at all, so both
    the error and the warning have measured volume zero on the shipped library.
    """
    cyclic = any(i.code == "WF_CYCLE" for i in res.issues)
    # Exact reader→producer pairs only. A `needs` on a CONTAINER whose child happens to bind
    # the producer is not redundant — it holds the container's other children back too, which
    # the child's own derived edge does not — so containment must not count here.
    derived = {
        (e.reader_path, e.producer_path) for e in edges if e.origin == EDGE_BINDING and e.ordered
    }
    for edge in edges:
        if edge.origin != EDGE_NEEDS:
            continue
        reader = repr(edge.reader_id) if edge.reader_id else edge.reader_path
        if not edge.ordered:
            if not cyclic:
                _add(
                    res,
                    "WF_UNSATISFIABLE_NEEDS",
                    (
                        f"{reader} declares needs {edge.producer_id!r}, but that edge can never "
                        f"be honoured: {edge.reason}"
                    ),
                    edge.reader_path,
                )
        elif (edge.reader_path, edge.producer_path) in derived:
            _add(
                res,
                "WF_REDUNDANT_NEEDS",
                (
                    f"{reader} declares needs {edge.producer_id!r} and also binds "
                    f"{{{{nodes.{edge.producer_id}…}}}} — the binding already orders it, so the "
                    "`needs` can go; keep `needs` for ordering that is not dataflow"
                ),
                edge.reader_path,
                SEVERITY_WARNING,
            )


@dataclass(frozen=True)
class ContractRead:
    """One path taken through a producer's output, judged against that producer's contract.

    Materialized as a value for the same reason `DepEdge` is: a rule that resolved NOTHING
    against a real contract reports exactly what a rule that resolved fifty satisfiable
    paths reports — silence. Only a caller that can count the reads whose `guaranteed` is
    non-`None` can tell the two apart, which is what the vacuity floor in the tests does.
    """

    reader_path: str
    #: The reader's author-facing id, or `""` — `id` is optional on every node.
    reader_id: str
    producer_id: str
    producer_path: str
    #: Segments after `output`; never empty (a bare `output` read has no path to judge).
    path: tuple[str, ...]
    #: Does the producer declare an `output_contract` at all?
    declared: bool
    #: The keys the contract GUARANTEES, or `None` when it makes no key-level promise.
    guaranteed: tuple[str, ...] | None
    #: `False` only when a key-level promise exists and this path's first segment is not in
    #: it. Unknowable-therefore-allowed is `True`, so the error path can never be the
    #: default.
    satisfiable: bool


def _declared_contracts(nodes: list[tuple[str, Node]]) -> dict[str, dict[str, Any]]:
    """node id → its `output_contract`. First id wins, mirroring `validate_node_tree`'s id
    map, so a duplicated id resolves to the same node in both (the duplicate itself is
    `WF_DUPLICATE_NODE_ID`'s business)."""
    out: dict[str, dict[str, Any]] = {}
    for _path, node in nodes:
        contract = (node.config or {}).get("output_contract")
        if node.id and isinstance(contract, dict) and contract and node.id not in out:
            out[node.id] = contract
    return out


def _guaranteed_keys(contract: dict[str, Any]) -> tuple[str, ...] | None:
    """The keys a contract PROMISES will be present, or `None` when it promises none.

    Both halves are required, matching `engine.check_output_contract` exactly:
    `required_keys` on its own is the shape `batch_compile.schema_to_contract` emits for a
    schema that declared no `type` (`required` present, `must_be_json` absent) — an
    under-declared contract, not a wrong binding. Refusing on it would refuse the author
    who described their output least, which is the opposite of the intent.
    """
    if not contract.get("must_be_json"):
        return None
    required = contract.get("required_keys")
    if not isinstance(required, list) or not required:
        return None
    return tuple(str(k) for k in required)


def output_contract_reads(
    nodes: list[tuple[str, Node]], edges: list[DepEdge]
) -> list[ContractRead]:
    """Every `{{nodes.<id>.output.<path>}}` read, resolved against its producer's contract.

    Only the FIRST segment is judged. `required_keys` is a promise about the top level of an
    object and says nothing about what lives inside `output.findings`, so checking deeper
    would mean inventing nesting vocabulary the engine cannot enforce — the one thing this
    atom must not do.
    """
    contracts = _declared_contracts(nodes)
    out: list[ContractRead] = []
    for edge in edges:
        contract = contracts.get(edge.producer_id)
        guaranteed = _guaranteed_keys(contract) if contract is not None else None
        for path in edge.output_reads:
            if not path:  # a bare `output` read — nothing to resolve
                continue
            out.append(
                ContractRead(
                    reader_path=edge.reader_path,
                    reader_id=edge.reader_id,
                    producer_id=edge.producer_id,
                    producer_path=edge.producer_path,
                    path=path,
                    declared=contract is not None,
                    guaranteed=guaranteed,
                    satisfiable=guaranteed is None or path[0] in guaranteed,
                )
            )
    return out


def contract_reads_for_root(root: Node) -> list[ContractRead]:
    """`output_contract_reads` for a whole tree — the shape a caller outside the validator
    has. Mirrors `dep_edges_for_root`, including its first-wins id map."""
    nodes = walk(root)
    ids: dict[str, str] = {}
    for path, node in nodes:
        if node.id and node.id not in ids:
            ids[node.id] = path
    return output_contract_reads(nodes, dep_ordering_edges(nodes, ids))


def _validate_output_contract(
    res: ValidationResult, nodes: list[tuple[str, Node]], edges: list[DepEdge]
) -> None:
    """Cross-check `output_contract` against the bindings that READ it (`PP-3`).

    `engine.check_output_contract` is a good gate that runs before any binding resolves, and
    it only ever looks at the producer. The reader was checked independently and the edge
    between them never at all: a spec could bind `{{nodes.a.output.summary}}` where `a`'s
    own contract guarantees `{"findings"}`, and the first symptom was `_walk_path` raising
    *"unresolved reference at 'summary'"* mid-run. Note that a `| default(…)` pipe does NOT
    rescue it — `_walk_path` raises before any pipe runs — so an unsatisfiable path is a
    dead run, which is why it is an ERROR.

    An unordered edge (`PP-1`) and an unsatisfiable path are reported TOGETHER when both
    hold, unlike the unknown-id case PP-1 suppresses: a typo'd id is one defect wearing two
    hats, whereas ordering and key-correctness are two independent fixes.

    **The warning is scoped to specs that use contracts, and that is a deviation worth
    naming.** Censused over the bundled library: 19 templates, 18 of them carrying 145
    distinct `.output` reads (100 at a sub-path), and ZERO declaring an `output_contract`. An
    unconditional warning would therefore fire 77 times across 18 of 19 shipped templates
    (49 with the sub-path scoping alone) — every template warning on every validation, which
    is how an author learns to skim past validator output. It would also red
    `test_it_validates_STRICTLY`, whose stated contract is that a bundled template ships no
    warning at all. So the warning fires only where the author has already adopted the
    mechanism — a spec with at least one `output_contract` somewhere, the shape
    `batch_compile` emits — and names the producer they left out. Measured volume on the
    shipped library: zero. Contracts arriving in the library later turn it on for exactly
    the templates that gained them.
    """
    reads = output_contract_reads(nodes, edges)
    spec_uses_contracts = bool(_declared_contracts(nodes))

    # producer id → (its path, [(reader label, dotted path) …]) for the aggregate warning.
    missing: dict[str, tuple[str, list[str]]] = {}
    for read in reads:
        reader = repr(read.reader_id) if read.reader_id else read.reader_path
        dotted = ".".join(read.path)
        if not read.satisfiable:
            assert read.guaranteed is not None  # `satisfiable` is True when it is None
            _add(
                res,
                "WF_UNSATISFIABLE_OUTPUT_REF",
                (
                    f"{reader} reads {{{{nodes.{read.producer_id}.output.{dotted}}}}}, but "
                    f"{read.producer_id!r} declares output_contract.required_keys "
                    f"{list(read.guaranteed)} — {read.path[0]!r} is not among the keys it "
                    "guarantees, so the binding cannot resolve"
                ),
                read.reader_path,
            )
        elif not read.declared and spec_uses_contracts:
            _, readers = missing.setdefault(read.producer_id, (read.producer_path, []))
            entry = f"{reader} reads output.{dotted}"
            if entry not in readers:
                readers.append(entry)

    for producer_id, (producer_path, readers) in missing.items():
        _add(
            res,
            "WF_UNCONTRACTED_OUTPUT_REF",
            (
                f"{producer_id!r} declares no output_contract, so nothing checks the paths "
                f"read from it: {'; '.join(readers)}"
            ),
            producer_path,
            SEVERITY_WARNING,
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
