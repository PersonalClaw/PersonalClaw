"""Stage contracts and derived parameters — what a plan review is actually reviewing.

Two mechanisms that both replace a hand-maintained artifact with a derived one, for the same
reason: a hand-maintained artifact drifts, and the drift is silent.

**Derived parameters (UP-R8).** A template's `inputs` block stops being the source of truth.
`resolve_unfilled_inputs()` computes the parameter schema as "what the tree references and nothing
supplies", so the launch form and the spec cannot disagree. Measured on the shipped library: THREE
of eighteen templates declared an input nothing read — including one where a user could set
`apply: true` on a consolidation pass and watch the node run with `apply: false`, no effect and no
error.

**Stage contracts (UP-R3).** Per-stage `scope` / `done_means` / `exclusions`, because approving "a
plan" approves a shape while approving a contract approves a claim. The rule that carries the
weight: **goal / verification / stopping-condition is the minimal triple**, and a stage with no
derivable check is marked unverifiable rather than quietly accepted — an unverifiable stage that
looks verified is how a run reports success for work nobody checked.

**Decision typing (UP-R16).** Blocking decisions pause the run and enter the needs-input inbox;
non-blocking ones land as Open Decisions on the finished summary. The auto-classification rule is
mechanical: a decision whose output feeds a downstream binding is BLOCKING, because the run cannot
proceed correctly without it; ambiguity that changes no execution path is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Kinds that do work a contract can describe. A container's contract is its children's.
WORK_KINDS = frozenset({"stage", "infer", "action", "transform", "subworkflow"})

#: Gate kinds that constitute a MACHINE-checkable verification. `approval` is deliberately absent:
#: a human saying yes is a decision, not a check, and counting it as verification would let a plan
#: satisfy the minimal triple with nothing but "ask the user".
MACHINE_VERIFIED_GATES = frozenset(
    {"judge", "expression", "verify_command", "verify_script", "ladder"}
)


@dataclass
class ParamSpec:
    """One derived parameter — a field the launch form must ask for."""

    name: str
    #: Where in the tree it is referenced, so review can show what it affects.
    used_by: list[str] = field(default_factory=list)
    required: bool = True
    declared_type: str = "string"
    help_text: str = ""
    default: Any = None
    #: True when the planner filled it rather than the user. Review highlights these, because an
    #: unvetted auto-filled value that looks user-supplied is the one nobody re-reads.
    auto_filled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "used_by": list(self.used_by),
            "required": self.required,
            "type": self.declared_type,
            "help": self.help_text,
            "default": self.default,
            "auto": self.auto_filled,
        }


def resolve_unfilled_inputs(spec: dict[str, Any]) -> list[ParamSpec]:
    """The parameter schema, DERIVED from what the tree references.

    "Unfilled" means referenced by a binding and supplied by nothing — neither a declared default
    nor a value already present. That definition is what makes the launch form and the spec
    incapable of disagreeing: there is one source, and it is the tree.

    A declared input the tree never references is NOT returned. It is drift, and returning it would
    put a control on the form that changes nothing — which is worse than omitting it, because the
    user believes they configured something.
    """
    from personalclaw.workflows.bindings import refs_in
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        logger.debug("unparseable root — no derived params", exc_info=True)
        return []

    raw_declared = spec.get("inputs")
    declared: dict[str, Any] = raw_declared if isinstance(raw_declared, dict) else {}

    # name -> the node ids that reference it. Ids rather than paths: review shows a user "this
    # affects the `synthesize` stage", and a path is engine addressing.
    used: dict[str, list[str]] = {}
    for _path, node in walk(root):
        label = node.id or node.kind.value
        for expr in refs_in(node.config or {}):
            head = expr.split("|")[0].strip()
            segments = [s for s in head.split(".") if s]
            if len(segments) >= 2 and segments[0] == "inputs":
                used.setdefault(segments[1], [])
                if label not in used[segments[1]]:
                    used[segments[1]].append(label)

    out: list[ParamSpec] = []
    for name in sorted(used):
        raw_meta = declared.get(name)
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        has_default = "default" in meta and meta.get("default") not in (None, "")
        out.append(
            ParamSpec(
                name=name,
                used_by=used[name],
                # A declared `required` wins; otherwise a default makes it optional. Defaulting to
                # REQUIRED when neither is stated is deliberate: an unasked parameter resolves to
                # nothing and the binding fails mid-run, which is a worse outcome than one question.
                required=bool(meta.get("required", not has_default)),
                declared_type=str(meta.get("type", "string") or "string"),
                help_text=str(meta.get("help", "") or ""),
                default=meta.get("default"),
            )
        )
    return out


def declared_but_unused(spec: dict[str, Any]) -> list[str]:
    """Inputs the template offers that its tree never reads.

    Surfaced rather than silently dropped: this is a template BUG, and the author needs to know.
    Measured on the shipped library, three of eighteen had one — including a consolidation template
    offering `apply` while its node hardcoded `false`.
    """
    derived = {p.name for p in resolve_unfilled_inputs(spec)}
    raw_declared = spec.get("inputs")
    declared: dict[str, Any] = raw_declared if isinstance(raw_declared, dict) else {}
    return sorted(set(declared) - derived)


def template_types(spec: dict[str, Any]) -> str:
    """The parameter contract as a commented type string, for the parameterize prompt.

    A type string rather than JSON Schema: this goes into a prompt, and a model fills a commented
    signature more reliably than it fills a schema — the comment is where the `help` text lands,
    which is the only thing that tells it what the field MEANS.
    """
    params = resolve_unfilled_inputs(spec)
    if not params:
        return "(this template takes no parameters)"
    lines = ["{"]
    for param in params:
        mark = "" if param.required else "?"
        comment = param.help_text or f"used by: {', '.join(param.used_by)}"
        lines.append(f"  {param.name}{mark}: {param.declared_type},  // {comment}")
    lines.append("}")
    return "\n".join(lines)


# ── the extraction contract (UP-R8) ──


@dataclass
class Extraction:
    """The result of filling parameters from a conversation.

    `all_filled` is deliberately RECOMPUTED rather than trusted: the plan requires re-validating
    the model's output against the schema, because a model that says `all_filled: true` while
    omitting a required field produces a run that dies on its first binding.
    """

    extracted: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    follow_up: str = ""
    failed: bool = False

    @property
    def all_filled(self) -> bool:
        return not self.missing and not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "extracted": dict(self.extracted),
            "missing": list(self.missing),
            "follow_up": self.follow_up,
            "all_filled": self.all_filled,
            "extraction_failed": self.failed,
        }


def apply_extraction(
    params: list[ParamSpec],
    raw: Any,
    *,
    declined: set[str] | None = None,
) -> Extraction:
    """Validate a model's extraction against the derived schema.

    Four rules from the plan, each answering a way this goes wrong:

    * **Only the schema decides what is filled.** A model claiming `all_filled` while omitting a
      required field is a model that has not read its own output.
    * **Latest value wins.** Re-extraction after a follow-up must be able to correct an earlier
      answer, or a typo becomes permanent.
    * **Never re-ask a declined optional.** Asking twice reads as not listening, and the user
      already answered.
    * **Extraction failure marks everything required as missing**, tagged — a failure that
      silently produced an empty dict would look like a user who said nothing.
    """
    result = Extraction()
    declined = declined or set()

    if not isinstance(raw, dict):
        result.failed = True
        result.missing = [p.name for p in params if p.required]
        result.follow_up = "extraction_failed"
        return result

    values = raw.get("extracted")
    if not isinstance(values, dict):
        values = {k: v for k, v in raw.items() if k in {p.name for p in params}}

    by_name = {p.name: p for p in params}
    for name, value in values.items():
        if name not in by_name:
            continue  # a field the schema does not have is not a parameter, whatever it looks like
        if value in (None, ""):
            continue
        result.extracted[name] = value

    for param in params:
        if param.name in result.extracted:
            continue
        if param.default not in (None, ""):
            # A default IS a filled value. Asking for something the template already answers is
            # the most common way a launch form becomes tedious.
            result.extracted[param.name] = param.default
            continue
        if param.required and param.name not in declined:
            result.missing.append(param.name)

    if result.missing:
        result.follow_up = _follow_up(result.missing, by_name)
    return result


def _follow_up(missing: list[str], by_name: dict[str, ParamSpec]) -> str:
    """ONE question covering everything still missing.

    One rather than a question per field: three sequential questions to fill three fields is three
    round-trips for information the user would have given in one sentence.
    """
    parts = []
    for name in missing:
        param = by_name.get(name)
        hint = f" ({param.help_text})" if param and param.help_text else ""
        parts.append(f"{name}{hint}")
    if len(parts) == 1:
        return f"I need one more thing: {parts[0]}"
    return "I still need: " + "; ".join(parts)


# ── stage contracts (UP-R3) ──


@dataclass
class StageContract:
    """One stage's reviewable claim."""

    node_id: str
    kind: str = ""
    scope: str = ""
    done_means: str = ""
    exclusions: list[str] = field(default_factory=list)
    #: How doneness is established: `gate`, `loop-condition`, `artifact`, or "" for unverifiable.
    verification: str = ""
    #: True when a VERIFIED stage binds to this one's output. Such a stage is checked through its
    #: consumer — the reviewer's findings are exactly what the downstream judge reads — so
    #: demanding its own gate would turn every plan into a gate per stage.
    feeds_verified: bool = False

    @property
    def verifiable(self) -> bool:
        return bool(self.verification)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "scope": self.scope,
            "done_means": self.done_means,
            "exclusions": list(self.exclusions),
            "verification": self.verification,
            "verifiable": self.verifiable,
            "feeds_verified": self.feeds_verified,
            **(
                {}
                if self.verifiable
                else {"review_note": "unverifiable — needs an approval gate or a human check"}
            ),
        }


def derive_contracts(spec: dict[str, Any]) -> list[StageContract]:
    """A contract per work node, with its verification derived from what FOLLOWS it.

    Derived rather than declared: a stage's own config cannot say whether it is verified, because
    verification lives in its successor. Asking the planner to assert it would produce a claim
    nothing checks — which is the failure this whole module is about.
    """
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return []

    nodes = [(path, node) for path, node in walk(root)]
    contracts: list[StageContract] = []
    contracts_by_id: dict[str, StageContract] = {}

    for index, (path, node) in enumerate(nodes):
        if node.kind.value not in WORK_KINDS:
            continue
        cfg = node.config or {}
        contract = StageContract(
            node_id=node.id or path,
            kind=node.kind.value,
            scope=_scope_of(node, cfg),
            done_means=str(cfg.get("done_means", "") or ""),
            exclusions=[str(e) for e in (cfg.get("exclusions") or [])],
        )
        contract.verification = _verification_for(index, path, nodes, cfg)
        contracts_by_id[contract.node_id] = contract
        if not contract.done_means and contract.verification:
            # Derive it from the verifier rather than leaving it blank: a contract whose
            # `done_means` is empty reads as "nobody decided", when in fact the gate decided.
            contract.done_means = f"established by the {contract.verification} that follows it"
        contracts.append(contract)

    _mark_feeds_verified(nodes, contracts_by_id)
    return contracts


def _mark_feeds_verified(nodes: list[tuple[str, Any]], by_id: dict[str, StageContract]) -> None:
    """Flag stages whose output a VERIFIED stage consumes.

    Read from the bindings rather than from position: "the next node" is not the consumer, and a
    reviewer three nodes upstream of the judge that reads its findings is just as verified as the
    one immediately before it.
    """
    from personalclaw.workflows.bindings import refs_in

    for _path, node in nodes:
        consumer = by_id.get(node.id or "")
        if consumer is None or not consumer.verifiable:
            continue
        for expr in refs_in(node.config or {}):
            segments = [s for s in expr.split("|")[0].strip().split(".") if s]
            if len(segments) >= 2 and segments[0] == "nodes":
                producer = by_id.get(segments[1])
                if producer is not None and producer is not consumer:
                    producer.feeds_verified = True


def _scope_of(node: Any, cfg: dict[str, Any]) -> str:
    label = str(cfg.get("label", "") or "")
    if label:
        return label
    prompt = str(cfg.get("prompt", "") or "")
    if prompt:
        return " ".join(prompt.split())[:140]
    provider = str(cfg.get("provider", "") or "")
    return f"calls `{provider}`" if provider else node.kind.value


def _verification_for(
    index: int, path: str, nodes: list[tuple[str, Any]], cfg: dict[str, Any]
) -> str:
    """What establishes that this node's work is done.

    Three sources, in order of strength. `required_artifacts` counts because a node that must
    produce a named artifact has a check the engine enforces; an approval gate does NOT count as
    machine verification, though it is recorded — a human saying yes is a decision.
    """
    if cfg.get("required_artifacts"):
        return "artifact"

    # A gate anywhere after this node in the same subtree. Scoped by path prefix so a gate in a
    # sibling branch does not get credited for verifying work it never sees.
    parent = path.rsplit(".", 1)[0] if "." in path else path
    for later_path, later in nodes[index + 1 :]:
        if not later_path.startswith(parent):
            continue
        if later.kind.value == "gate":
            gate_kind = str((later.config or {}).get("kind", "") or "")
            if gate_kind in MACHINE_VERIFIED_GATES:
                return "gate"
            if gate_kind == "approval":
                return "approval"
        if later.kind.value == "loop":
            mode = str((later.config or {}).get("mode", "") or "")
            if mode in ("until", "until_dry", "counted"):
                return "loop-condition"

    # An enclosing bounded loop verifies its body: the loop's own exit condition is the check.
    for enclosing_path, enclosing in nodes:
        if enclosing.kind.value != "loop" or not path.startswith(enclosing_path + "."):
            continue
        mode = str((enclosing.config or {}).get("mode", "") or "")
        if mode in ("until", "until_dry", "counted"):
            return "loop-condition"
    return ""


def contract_issues(contracts: list[StageContract]) -> list[str]:
    """The lint over a set of contracts.

    A workflow with NO machine-checkable stopping condition anywhere is rejected — the plan's
    minimal triple. Individual unverifiable stages are flagged, not rejected: a stage whose output
    feeds a verified successor is legitimately unverified on its own.
    """
    issues: list[str] = []
    if not contracts:
        return issues

    if not any(c.verification in ("gate", "loop-condition", "artifact") for c in contracts):
        # An ALL-DETERMINISTIC plan is exempt. Measured on `knowledge-health`: every node is a
        # zero-token action or transform, so its output already IS the check — a provider either
        # succeeded or returned a typed error the engine surfaced. Demanding a model judge over a
        # deterministic scan spends a call to form an opinion about arithmetic, and a rule that
        # fires on correct structure gets suppressed wholesale, taking the real findings with it.
        if any(c.kind in ("stage", "infer") for c in contracts):
            issues.append(
                "no stage in this plan has a machine-checkable verification — add a "
                "judge/expression gate, a bounded loop, or a required artifact. Goal, "
                "verification and stopping condition is the minimal triple"
            )

    for contract in contracts:
        if not contract.verifiable:
            if contract.feeds_verified:
                # A stage whose output is consumed by a verified stage is checked THROUGH it: the
                # reviewer's findings are what the judge reads. Flagging it would demand a gate per
                # stage, which turns a three-stage plan into a six-node ceremony.
                continue
            if contract.kind in ("action", "transform"):
                # Zero-token nodes with a deterministic contract. An action either succeeded or
                # returned an error the engine already surfaced — there is nothing for a judge to
                # form an opinion about, and demanding one would put a model call after every write.
                continue
            issues.append(
                f"stage `{contract.node_id}` is unverifiable — it needs an approval gate or a "
                "human check, or the run will report success without anyone confirming it"
            )
        elif contract.verification == "approval":
            issues.append(
                f"stage `{contract.node_id}` is verified only by a human approval — fine for a "
                "judgement call, but it is not a machine check"
            )
    return issues


# ── decision typing (UP-R16) ──


@dataclass
class DecisionNode:
    """A gate, typed by whether the run can proceed without its answer."""

    node_id: str
    blocking: bool
    reason: str
    gate_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "blocking": self.blocking,
            "severity": "blocking" if self.blocking else "open-decision",
            "reason": self.reason,
            "gate_kind": self.gate_kind,
        }


def type_decisions(spec: dict[str, Any]) -> list[DecisionNode]:
    """Classify every gate as blocking or non-blocking.

    The rule is mechanical, which is what makes it reviewable: a decision whose output FEEDS A
    DOWNSTREAM BINDING is blocking, because the run cannot proceed correctly without the answer.
    Ambiguity that changes no execution path is non-blocking and lands as an Open Decision on the
    finished summary, answerable afterwards.

    Two overrides, both in the safe direction: a destructive-risk gate and an approval gate are
    ALWAYS blocking whatever the bindings say. Auto-proceeding past "may I delete this?" because
    nothing consumed the answer is the one classification error with an unrecoverable cost.
    """
    from personalclaw.workflows.bindings import refs_in
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return []

    nodes = [(path, node) for path, node in walk(root)]

    # Which node ids anything binds to.
    consumed: set[str] = set()
    for _path, node in nodes:
        for expr in refs_in(node.config or {}):
            segments = [s for s in expr.split("|")[0].strip().split(".") if s]
            if len(segments) >= 2 and segments[0] == "nodes":
                consumed.add(segments[1])

    out: list[DecisionNode] = []
    for path, node in nodes:
        if node.kind.value != "gate":
            continue
        cfg = node.config or {}
        node_id = node.id or path
        gate_kind = str(cfg.get("kind", "") or "")
        risk = str(cfg.get("risk", "") or "").lower()

        if risk in ("destructive", "high"):
            out.append(
                DecisionNode(
                    node_id,
                    True,
                    "destructive risk — always blocking, whatever consumes it",
                    gate_kind,
                )
            )
            continue
        if gate_kind == "approval":
            out.append(
                DecisionNode(
                    node_id, True, "an approval gate exists to pause for a person", gate_kind
                )
            )
            continue
        if node_id in consumed:
            out.append(
                DecisionNode(
                    node_id,
                    True,
                    "its output feeds a downstream binding — the run needs the answer",
                    gate_kind,
                )
            )
            continue
        out.append(
            DecisionNode(
                node_id,
                False,
                "nothing binds to it and no execution path changes — answerable after the run",
                gate_kind,
            )
        )
    return out


def open_decisions(decisions: list[DecisionNode]) -> list[dict[str, Any]]:
    """The non-blocking ones, for the finished run's summary."""
    return [d.to_dict() for d in decisions if not d.blocking]
