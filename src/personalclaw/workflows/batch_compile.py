"""Batch `subagent_run` compiled to a run, with the hardening contract (WORK-CONTAINERS §3, S48).

Today a batch spawn is fire-and-forget: no journal, no per-branch retry, no resume after restart, no
fork. Compiling `tasks[]` into an inline `parallel[stage...]` run gives it all of those for free —
same tool signature, so no agent-visible migration.

The threshold rule is the whole ergonomic story: **N=1 stays a raw spawn, N≥2 compiles.** Ad-hoc "go
check X while I keep chatting" is chat-native delegation, and forcing a run record plus project
resolution onto it is ceremony that kills the personal feel.

What the compiled run ships with (R2):

* **Isolation by default**, with a compile-time lint that warns when more than one concurrent worker
  holds write access to the same path. Two workers writing one file is a lost update, and it is
  invisible: both report success.
* **Dual depth enforcement** — statically (a batch inside a batch is rejected at compile) and
  dynamically (the `__wf_depth` counter the engine already carries). Today's no-recursion rule is
  PROMPT-level only, so a leaf that decided to fan out again could do it.
* **Capability classes.** A leaf declares `research` or `mutating`; research leaves get a read-only
  tool surface. A research leaf with write tools is a leaf that can only surprise you.
* **Typed leaf outputs**, compiled into the engine's EXISTING `output_contract` rather than a second
  checker — two validators over one field would disagree eventually, and the one that fired last
  would win silently.
* **Per-leaf error isolation.** One leaf failing never rejects the batch: the whole point of five
  parallel investigations is that four still return.

Pure compilation. `compile_batch` takes task descriptions and returns a spec plus lint findings; the
caller starts the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Below this, a batch is not a batch. One task compiles to nothing — the caller spawns it directly,
#: which is what keeps a one-off delegation free of a run record it does not need.
COMPILE_THRESHOLD = 2

#: A compiled batch is a fan-out, not a program. Past this many leaves the parallel widget is
#: unreadable and the concurrency cap means most leaves are queued anyway — a caller wanting more
#: should author a workflow, where the shape is explicit.
MAX_LEAVES = 24

#: Per-leaf timeout. A batch where one leaf can hang forever is a batch that never reports, so the
#: bound is per-leaf rather than per-run: four leaves that finished should not wait on the fifth.
DEFAULT_LEAF_TIMEOUT_SECS = 900


class Capability(str, Enum):
    """What a leaf is allowed to do.

    `RESEARCH` is the default because it is the safe direction to be wrong in: a research leaf that
    needed to write fails visibly and is re-declared, while a mutating leaf that only needed to read
    has ambient write access nobody asked for.
    """

    RESEARCH = "research"
    MUTATING = "mutating"


#: : Tool families a research-class leaf may NOT use. Named as families rather than exact
#: tool names so
#: : a newly-added write tool is denied by default — an allowlist of writers would silently
#: admit every
#: tool added after it was written.
_WRITE_TOOL_MARKERS = (
    "write",
    "edit",
    "create",
    "update",
    "delete",
    "remove",
    "publish",
    "persist",
    "commit",
    "push",
    "send",
    "post",
    "bash",
    "shell",
    "run_script",
)

#: : Orchestration tools no leaf may use at ANY depth. A leaf that can spawn is a leaf that
#: can fan out
#: without a budget, and the depth counter alone would let it happen once per level.
ORCHESTRATION_TOOLS = frozenset(
    {
        "subagent_run",
        "workflow_start",
        "workflow_author",
        "workflow_plan",
        "workflow_fork",
        "workflow_run_from",
        "schedule_create",
    }
)


def is_write_tool(name: str) -> bool:
    """Whether a tool name looks like it mutates.

    Deliberately over-inclusive: a read tool wrongly classified as a writer costs a research
    leaf one
    declaration, while a writer wrongly classified as a reader gives a research leaf silent write
    access. The asymmetry decides the direction of the guess.
    """
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _WRITE_TOOL_MARKERS)


def leaf_tool_posture(capability: Capability, declared: list[str] | None = None) -> dict[str, Any]:
    """The tool surface for one leaf.

    Returns the posture rather than a filtered list, because filtering has to happen at the tool
    HANDLER seam (there is no per-context tool filtering to hook) — a list computed here and not
    enforced there would be a control that looks like enforcement and is documentation.
    """
    declared_writers = {str(t) for t in (declared or []) if is_write_tool(str(t))}
    denied = set(ORCHESTRATION_TOOLS)
    if capability is Capability.RESEARCH:
        return {
            "capability": capability.value,
            "read_only": True,
            "denied_tools": sorted(denied),
            # An author who declares a specific write tool for a research leaf has made a decision;
            # overriding it would make the declaration pointless. Everything UNDECLARED
            # stays denied,
            # which is what "read-only by default" has to mean to be worth anything.
            "allowed_writers": sorted(declared_writers),
            "note": (
                "research-class leaf: write tools are denied unless explicitly declared; "
                "orchestration tools are denied at every depth"
            ),
        }
    return {
        "capability": capability.value,
        "read_only": False,
        "denied_tools": sorted(denied),
        "note": "mutating leaf: writes allowed, orchestration tools denied at every depth",
    }


@dataclass
class LeafTask:
    """One task in a batch, with everything the compile needs to harden it."""

    task: str
    agent: str = ""
    capability: Capability = Capability.RESEARCH
    #: Paths this leaf will write. Declared, not inferred — the single-writer lint can only catch a
    #: : collision it was told about, and an inferred path list would produce confident
    #: false warnings.
    writes: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    timeout_secs: int = DEFAULT_LEAF_TIMEOUT_SECS

    def node_id(self, index: int) -> str:
        """A stable, readable node id.

        Derived from the task text plus the index: the text makes the progress widget
        legible, and the
        index guarantees uniqueness when two tasks start with the same words.
        """
        words = re.findall(r"[a-z0-9]+", (self.task or "").lower())[:4]
        stem = "_".join(words) or "task"
        return f"{stem}_{index}"[:48]


@dataclass
class LintFinding:
    """One compile-time finding.

    `severity` matters: a `warn` compiles and renders in review, an `error` refuses. The
    distinction is
    what keeps the lint from being either ignorable or obstructive.
    """

    code: str
    severity: str  # warn | error
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def schema_to_contract(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Compile a leaf's `output_schema` into the engine's EXISTING `output_contract`.

    Not a second validator. The engine already checks `output_contract` before any
    `{{nodes.x.output}}` binding resolves; adding a parallel checker would mean two validators over
    one field, and the one that ran last would silently win.

    Only the parts the engine can mechanically check are compiled. A schema field with no contract
    equivalent is DROPPED rather than approximated — an approximated check that passes
    malformed data
    is worse than no check, because it is believed.
    """
    if not isinstance(schema, dict) or not schema:
        return {}
    contract: dict[str, Any] = {}
    if schema.get("type") in ("object", "array"):
        contract["must_be_json"] = True
    required = schema.get("required")
    if isinstance(required, list) and required:
        contract["required_keys"] = [str(k) for k in required]
    return contract


def single_writer_lint(leaves: list[LeafTask]) -> list[LintFinding]:
    """Warn when two concurrent leaves declare a write to the same path.

    A warning, not an error: the author may know the writes are to disjoint regions of one
    directory,
    and refusing would block a legitimate fan-out. But it must be SAID — two workers writing
    one file
    is a lost update, and it is invisible because both leaves report success.
    """
    by_path: dict[str, list[str]] = {}
    for index, leaf in enumerate(leaves):
        for path in leaf.writes:
            by_path.setdefault(str(path), []).append(leaf.node_id(index))
    findings: list[LintFinding] = []
    for path, holders in sorted(by_path.items()):
        if len(holders) > 1:
            findings.append(
                LintFinding(
                    code="multi_writer",
                    severity="warn",
                    message=(
                        f"{len(holders)} concurrent leaves declare writes to {path!r} "
                        f"({', '.join(holders)}) — concurrent writes to one path lose updates "
                        "silently, since both leaves report success"
                    ),
                )
            )
    return findings


def capability_lint(leaves: list[LeafTask]) -> list[LintFinding]:
    """Findings about capability declarations.

    A research-class leaf that declares writes is a contradiction the author must resolve:
    either the leaf mutates (declare it) or the paths are wrong. Compiling it either way
    would silently pick one of the two meanings.
    """
    findings: list[LintFinding] = []
    for index, leaf in enumerate(leaves):
        if leaf.capability is Capability.RESEARCH and leaf.writes:
            findings.append(
                LintFinding(
                    code="research_leaf_writes",
                    severity="error",
                    message=(
                        f"leaf {leaf.node_id(index)!r} is capability=research but declares "
                        f"writes to {', '.join(leaf.writes)} — declare capability=mutating, "
                        "or drop the writes"
                    ),
                )
            )
    return findings


def depth_lint(depth: int) -> list[LintFinding]:
    """Static half of dual depth enforcement.

    A batch inside a batch is refused at COMPILE, not only counted at runtime: today's no-recursion
    rule is prompt-level, so a leaf that decided to fan out again would succeed once per
    level before
    any counter noticed. Static rejection is what makes the rule a rule.
    """
    if depth > 0:
        return [
            LintFinding(
                code="nested_batch",
                severity="error",
                message=(
                    f"a batch spawn at depth {depth} is refused: a leaf may call single "
                    "`subagent_run` but not a batch, or one request fans out without a budget"
                ),
            )
        ]
    return []


@dataclass
class CompileResult:
    """The compiled run, its findings, and whether it may start.

    `spec` is present even when `ok` is False, so a review surface can show what WOULD have
    run beside
    the reason it will not. A refusal with no artifact leaves the author guessing at what
    the compiler
    understood.
    """

    spec: dict[str, Any] = field(default_factory=dict)
    findings: list[LintFinding] = field(default_factory=list)
    compiled: bool = False
    #: Per-node posture the SPAWN PATH must apply — tool denials, workspace mode, per-leaf timeout.
    #: Kept OUT of the node config on purpose: none of it is read by the engine, and a config key
    #: that looks like a control while enforcing nothing is worse than an honest external contract.
    #: `unenforced()` names exactly which parts still need a seam.
    postures: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def unenforced(self) -> list[str]:
        """The posture items no seam applies yet.

        Returned rather than logged so a caller cannot believe the compile enforced them. A batch
        compiled with a read-only posture that nothing enforces is a batch running with ambient
        write access and a reassuring payload.
        """
        pending = []
        if self.postures:
            pending.append(
                "tool denials + read-only posture: needs the tool-handler depth-flag seam "
                "(no per-context tool filtering exists today)"
            )
            pending.append("workspace_mode: needs the §4.1 workspace provisioning block (S49)")
            pending.append(
                "timeout_secs: the engine's node timeout is per-RUN "
                "(`services.node_timeout_total`); there is no per-node override to bind to"
            )
        return pending

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiled": self.compiled,
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "spec": self.spec,
            "postures": self.postures,
            "unenforced": self.unenforced(),
        }


def compile_batch(
    leaves: list[LeafTask],
    *,
    depth: int = 0,
    run_name: str = "subagent-batch",
    project_id: str = "",
) -> CompileResult:
    """Compile `tasks[]` into a `parallel[stage...]` spec with the hardening contract.

    Returns `compiled=False` for a single task: N=1 stays a raw spawn, because a run record plus
    project resolution on "go check X" is ceremony the personal feel does not survive.
    """
    findings = depth_lint(depth)
    if len(leaves) < COMPILE_THRESHOLD:
        return CompileResult(findings=findings, compiled=False)
    if len(leaves) > MAX_LEAVES:
        findings.append(
            LintFinding(
                code="too_many_leaves",
                severity="error",
                message=(
                    f"{len(leaves)} leaves exceeds the {MAX_LEAVES} cap — author a workflow, where "
                    "the shape is explicit, rather than fanning out past what the widget can show"
                ),
            )
        )
    findings.extend(capability_lint(leaves))
    findings.extend(single_writer_lint(leaves))

    children: list[dict[str, Any]] = []
    postures: dict[str, dict[str, Any]] = {}
    for index, leaf in enumerate(leaves):
        node_id = leaf.node_id(index)
        # ONLY keys the engine actually reads. Measured: an earlier version also emitted
        # `workspace`, `capability`, `tool_posture` and `timeout_secs` into the node config
        # — grepping
        # the engine found NOTHING reads any of them, and `on_error: "continue"` is not a value it
        # recognizes either (it checks for `fail_run` and defaults to `null_continue`). Four config
        # keys that look like controls and enforce nothing, in a module whose whole subject is
        # least-privilege. Unenforced declarations now travel in `postures` below, where their name
        # says what they are.
        config: dict[str, Any] = {"prompt": leaf.task, "model_tier": "standard"}
        if leaf.agent:
            config["agent"] = leaf.agent
        contract = schema_to_contract(leaf.output_schema)
        if contract:
            config["output_contract"] = contract
        children.append({"kind": "stage", "id": node_id, "config": config})
        postures[node_id] = {
            **leaf_tool_posture(leaf.capability),
            "workspace_mode": "scratch",
            "timeout_secs": int(leaf.timeout_secs),
        }

    spec = {
        "name": run_name,
        # Per-leaf error isolation, MEASURED rather than assumed. The first version of this comment
        # claimed the default already gave it; driving `derive_state` showed the opposite — a
        # `parallel` with `join: all` (the default) goes FAILED as soon as one child fails,
        # so one bad
        # leaf would have sunk the whole batch. `join: quorum` with `quorum: 1` is what actually
        # yields "four still return": verified DONE with one leaf failed and one done.
        "root": {
            "kind": "parallel",
            "id": "batch",
            "config": {"join": "quorum", "quorum": 1},
            "children": children,
        },
        # `subagent-tool` origin is what collapses these rows on the Work board (S46) and puts them
        # on the subagent prune cadence rather than the workflow one.
        "origin": {"kind": "subagent-tool"},
        "project_id": project_id,
    }
    return CompileResult(spec=spec, findings=findings, compiled=True, postures=postures)


def lineage_env(*, run_id: str, project_id: str, node_id: str, depth: int) -> dict[str, str]:
    """Parent lineage threaded into a leaf's spawn environment.

    Every memory/knowledge/artifact write a leaf makes is tagged with this, so a child announces to
    the correct surface and the flywheel gets provenance. `__wf_depth` rides in the same env as the
    existing `__hook_depth` pattern rather than a new mechanism — one threading convention,
    so a leaf
    that reads one reads both.
    """
    return {
        "__wf_depth": str(int(depth)),
        "__wf_run_id": str(run_id),
        "__wf_project_id": str(project_id),
        "__wf_node_id": str(node_id),
    }


#: Control tokens and thinking markup stripped from any transcript projection. A recall view that
#: leaked tool XML would put attacker-controlled markup in front of a model that treats markup as
#: structure — and the leaf's transcript is exactly where untrusted content arrives.
_STRIP_PATTERNS = (
    re.compile(r"<thinking>.*?</thinking>", re.S | re.I),
    re.compile(r"<[^>]*>.*?</[^>]*>", re.S | re.I),
    re.compile(r"<function_calls>.*?</function_calls>", re.S | re.I),
    re.compile(r"<function_results>.*?</function_results>", re.S | re.I),
)

#: Recall views are bounded. An unbounded projection of a long leaf transcript would push the thing
#: the reader opened it for out of the window it renders in.
MAX_RECALL_CHARS = 4000


def recall_view(transcript: str, *, limit: int = MAX_RECALL_CHARS) -> dict[str, Any]:
    """A safety-filtered projection of a leaf transcript.

    Redaction runs through `security.redact` — the EXISTING chokepoint — rather than a local pattern
    set, because a second redactor would drift from the one that is maintained, and the
    drift shows up
    as a credential in a UI.

    Both flags are returned. A truncated projection presented as complete is a reader believing they
    saw the end of a run they did not.
    """
    text = transcript or ""
    stripped = text
    for pattern in _STRIP_PATTERNS:
        stripped = pattern.sub("", stripped)
    control_stripped = stripped != text

    try:
        from personalclaw.security import redact

        redacted_text = redact(stripped)
    except Exception:
        # A redactor that failed must not produce an UNREDACTED view. Failing closed costs the
        # projection; failing open costs a credential.
        return {
            "text": "",
            "truncated": False,
            "redacted": True,
            "control_stripped": control_stripped,
            "error": "redaction unavailable — view withheld rather than shown unredacted",
        }
    was_redacted = redacted_text != stripped

    truncated = len(redacted_text) > limit
    return {
        "text": redacted_text[:limit],
        "truncated": truncated,
        "redacted": was_redacted,
        "control_stripped": control_stripped,
    }
