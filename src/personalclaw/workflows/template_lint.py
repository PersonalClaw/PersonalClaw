"""Deterministic template lint — the conventions check (WF2-R15).

`validate_spec` answers "will the engine run this?". This answers a different question: "does
this template follow the conventions that keep a growing library coherent?" A spec can be
perfectly valid and still be a bad template — no description to choose it by, an input with no
help text, a Finding record written by hand instead of cited, no mid-flight steering example.

Why a lint rather than more validation rules: these are **authoring** standards, not run
requirements. Making them validation errors would refuse a user's own half-finished workflow,
which is theirs to leave rough. Making them nothing at all is how a six-template library becomes
a twelve-template library where four templates each define severity differently.

Deterministic on purpose — no model call. Every finding is a structural fact about the spec, so
the lint gives the same answer in CI as on a laptop, and the CI failure names the exact template
and node.

Severity here means:

* **error** — would mislead or block a user of the template (a duplicated convention that has
  already drifted, a reference that cannot resolve);
* **warning** — a real gap worth fixing before shipping (no steering example, thin description).

The `bundled` library is held to `error`-free AND `warning`-free by test. A user's own template is
only ever advised.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import blocks

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

#: A description shorter than this cannot distinguish one template from another in a picker.
MIN_DESCRIPTION = 40

#: Conventions that MUST be cited rather than restated. The pattern is what a hand-written copy
#: looks like; the block is what should have been referenced instead. Keyed by the convention so a
#: finding can name both.
_DUPLICATED_CONVENTIONS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "the Finding record",
        re.compile(r"A Finding is\s*\{|severity:\s*Critical\|Major\|Minor\|Nit", re.I),
        "finding-record",
    ),
)


@dataclass
class LintFinding:
    """One convention deviation, addressed to a node where it has one."""

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
class LintResult:
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing at ERROR level was found. Warnings do not fail a lint — the
        bundled-library test asserts on `clean` instead."""
        return not any(f.severity == SEVERITY_ERROR for f in self.findings)

    @property
    def clean(self) -> bool:
        """True when nothing at all was found. What the shipped library is held to."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "clean": self.clean,
            "findings": [f.to_dict() for f in self.findings],
        }


def lint_template(spec: dict[str, Any], *, bundled: bool = False) -> LintResult:
    """Lint one template spec. Never raises — a lint that crashed on a malformed spec would
    hide every finding it had already collected.

    ``bundled`` enables the checks that only make sense for a SHIPPED template: a user's own
    workflow does not need steering examples or a picker-ready description, and reporting those
    would be noise on something they are still writing.
    """
    res = LintResult()
    if not isinstance(spec, dict):
        res.findings.append(LintFinding("WFL_NOT_AN_OBJECT", "a template must be an object"))
        return res

    _check_block_refs(res, spec)
    _check_duplicated_conventions(res, spec)
    _check_anti_patterns(res, spec)
    if bundled:
        _check_shipping_metadata(res, spec)
    return res


def _check_block_refs(res: LintResult, spec: dict[str, Any]) -> None:
    """Every `{{block:…}}` must name a block that exists.

    The drift this catches: a block renamed or removed while a template still cites it. The save
    path raises on it, but a lint reports EVERY broken reference at once instead of the first —
    which is what you want when you have just renamed a block.
    """
    available = set(blocks.block_names())
    for name in sorted(blocks.refs_in(spec)):
        if name not in available:
            res.findings.append(
                LintFinding(
                    "WFL_UNKNOWN_BLOCK",
                    f"references shared block {name!r}, which does not exist "
                    f"(available: {', '.join(sorted(available)) or 'none'})",
                )
            )


def _check_duplicated_conventions(res: LintResult, spec: dict[str, Any]) -> None:
    """A convention written by hand where a shared block exists.

    This is the rule the plan states as "repeated boilerplate moves to shared", and it is an
    ERROR rather than a warning because the copies do not stay identical. Three hand-written
    Finding records is how a gate predicate like "no open Critical" quietly stops meaning the
    same thing in two stages.
    """
    for label, pattern, block in _DUPLICATED_CONVENTIONS:
        for path, text in _prompts(spec):
            if pattern.search(text):
                res.findings.append(
                    LintFinding(
                        "WFL_INLINE_CONVENTION",
                        f"{label} is written inline — cite {{{{block:{block}}}}} instead, or the "
                        f"copies drift apart",
                        path,
                    )
                )


def _check_shipping_metadata(res: LintResult, spec: dict[str, Any]) -> None:
    """The metadata a SHIPPED template needs to be findable and drivable."""
    description = str(spec.get("description", "") or "").strip()
    if len(description) < MIN_DESCRIPTION:
        res.findings.append(
            LintFinding(
                "WFL_THIN_DESCRIPTION",
                f"the description is {len(description)} chars — the picker shows this line and "
                f"nothing else, so under {MIN_DESCRIPTION} makes templates hard to tell apart",
                severity=SEVERITY_WARNING,
            )
        )

    for key, param in (spec.get("inputs") or {}).items():
        if not isinstance(param, dict):
            continue
        if not str(param.get("help", "")).strip():
            res.findings.append(
                LintFinding(
                    "WFL_UNDOCUMENTED_INPUT",
                    f"input {key!r} has no `help` — the run dialog shows a bare field name",
                    severity=SEVERITY_WARNING,
                )
            )
        if param.get("required") and param.get("default") not in (None, ""):
            res.findings.append(
                LintFinding(
                    "WFL_REQUIRED_WITH_DEFAULT",
                    f"input {key!r} is required AND has a default — a default means it can be "
                    f"omitted, so the two contradict each other",
                )
            )

    examples = (spec.get("metadata") or {}).get("steering_examples") or []
    events = {str(e.get("event", "")) for e in examples if isinstance(e, dict)}
    if "kickoff" not in events:
        res.findings.append(
            LintFinding(
                "WFL_NO_KICKOFF_EXAMPLE",
                "no `kickoff` steering example — the widget surfaces these and `workflow_plan` "
                "uses them as few-shot, so without one a model guesses how to drive it",
                severity=SEVERITY_WARNING,
            )
        )
    if "mutation" not in events:
        res.findings.append(
            LintFinding(
                "WFL_NO_MUTATION_EXAMPLE",
                "no mid-flight `mutation` steering example — that is the one that teaches a model "
                "editing a RUNNING workflow is normal",
                severity=SEVERITY_WARNING,
            )
        )


def _prompts(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Every prompt string in a spec, paired with its node path.

    Walks the raw dict rather than a parsed `Node`, so the lint works on a spec the validator
    would reject — which is exactly when an author most wants to see every problem at once.
    """
    out: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id", "") or "?")
        here = f"{path}.{node_id}" if path else node_id
        cfg = node.get("config") or {}
        for key in ("prompt", "expr", "criteria", "guidance"):
            value = cfg.get(key)
            if isinstance(value, str) and value:
                out.append((here, value))
        # A macro's lens prompts live outside `config.prompt`, so a hand-written convention there
        # would otherwise be invisible to the lint.
        for lens in cfg.get("lenses") or []:
            if isinstance(lens, dict) and isinstance(lens.get("prompt"), str):
                out.append((here, lens["prompt"]))
        for child in node.get("children") or []:
            walk(child, here)
        walk(node.get("body"), here)
        for case in (node.get("cases") or {}).values():
            walk(case, here)
        walk(node.get("default"), here)

    walk(spec.get("root"), "")
    return out


# ── The five anti-patterns (LOOPS-EVOLUTION R6b) ──
#
# Named rules rather than review advice, because each one is a shape that LOOKS like a
# working template and is not. A reviewer reading a 200-line spec will not reliably spot
# a judge that cannot reject; a rule will.

#: Every move a durable autonomous template has to make somewhere in its graph. The audit
#: does not demand all five — a review template legitimately persists nothing — it demands
#: that an ABSENCE be visible, because an unnoticed absence is how a template ships
#: without the one move it needed.
FIVE_MOVES = ("discovery", "handoff", "verification", "persistence", "scheduling")


def _all_nodes(node: Any) -> list[dict[str, Any]]:
    """Every node in the tree, including branch cases and loop bodies."""
    if not isinstance(node, dict):
        return []
    out = [node]
    for child in node.get("children") or []:
        out.extend(_all_nodes(child))
    if node.get("body"):
        out.extend(_all_nodes(node["body"]))
    for case in (node.get("cases") or {}).values():
        if isinstance(case, dict):
            out.extend(_all_nodes(case))
        elif isinstance(case, list):
            for item in case:
                out.extend(_all_nodes(item))
    return out


#: Node-id stems that mark a verifier. Broader than "judge" on purpose: the shipped
#: library already verifies under other names (`verify_refute` adversarially refutes each
#: finding; `completeness_critic` asks what was missed), and a rule that only recognised
#: the word "judge" reported those templates as BLIND — which is the opposite of true and
#: would have taught authors that the rule is noise.
_VERIFIER_STEMS = (
    "judge",
    "verify",
    "critic",
    "accept",
    "review",
    "refute",
    "check",
    # A gap analysis IS verification for a research loop: "what did we fail to establish"
    # is the research equivalent of "does this pass". `deep-research` closes its sweep on
    # `round_gaps`, and without this stem the rule called that loop blind.
    "gap",
    "audit",
)


def _is_judge(node: dict[str, Any]) -> bool:
    """Does this node CHECK someone else's work?

    Identified by id stem or gate kind, NOT by `tools_posture: verify` alone — a stage can
    be read-only because it reads a ledger without being a verifier at all.
    """
    cfg = node.get("config") or {}
    node_id = str(node.get("id", "")).lower()
    if cfg.get("kind") in ("judge", "verify_command", "verify_script"):
        return True
    return any(stem in node_id for stem in _VERIFIER_STEMS)


def _check_anti_patterns(res: LintResult, spec: dict[str, Any]) -> None:
    """The five named anti-patterns. Each is a shape that reads as working and is not."""
    root = spec.get("root")
    if not isinstance(root, dict):
        return
    nodes = _all_nodes(root)
    loops = [n for n in nodes if n.get("kind") == "loop"]
    judges = [n for n in nodes if _is_judge(n)]

    # NODDING — a judge that cannot reject. `tools_posture` other than `verify` means it
    # cannot read what it judges; no verdict field means nothing downstream can route on
    # its answer. Either way the gate is decorative.
    for judge in judges:
        cfg = judge.get("config") or {}
        posture = cfg.get("tools_posture")
        # `infer` is ONE bounded model call with no tools and no session BY DEFINITION, so
        # demanding `tools_posture: verify` from it is a category error. The rule targets a
        # `stage` judge — the kind that gets a session and could have been handed write
        # tools. Flagging every infer-based verifier reported the shipped adversarial
        # panels as nodding, which is the opposite of what they are.
        if judge.get("kind") == "stage" and posture is not None and posture != "verify":
            res.findings.append(
                LintFinding(
                    "WFL_NODDING_JUDGE",
                    f"judge {judge.get('id')!r} has tools_posture {posture!r}: it cannot "
                    "independently read what it judges, so its verdict carries no information",
                    severity=SEVERITY_ERROR,
                )
            )
        schema = cfg.get("schema") or {}
        prompt = cfg.get("prompt") or ""
        # The requirement is a ROUTABLE typed field, not the literal name "verdict". The
        # shipped refuter returns `refuted: boolean`, which a loop can branch on perfectly
        # well; insisting on one name reported a working adversarial verifier as nodding.
        # What actually matters is that SOMETHING typed comes back — a schema of only
        # free text is what a loop cannot route on.
        routable = {"verdict", "refuted", "passed", "ok", "approved", "accepted", "score"}
        has_routable = any(key in schema for key in routable) or "verdict" in prompt
        only_prose = schema and all(
            str(v).lower() in ("string", "str", "text") for v in schema.values()
        )
        if schema and not has_routable and only_prose:
            res.findings.append(
                LintFinding(
                    "WFL_NODDING_JUDGE",
                    f"judge {judge.get('id')!r} returns only free text — a loop cannot route "
                    "on prose, so its verdict cannot change what happens next",
                    severity=SEVERITY_ERROR,
                )
            )

    # SELF-JUDGED — a work stage that decides its own completion. A warning rather than an
    # error because a template author may genuinely want it, but it is the platform's
    # oldest rule and an opt-out should be visible.
    for node in nodes:
        if node.get("kind") != "stage" or _is_judge(node):
            continue
        schema = (node.get("config") or {}).get("schema") or {}
        if "done" in schema and not spec.get("self_judged"):
            res.findings.append(
                LintFinding(
                    "WFL_SELF_JUDGED",
                    f"stage {node.get('id')!r} reports its own `done`: no agent certifies its "
                    "own work. Set `self_judged: true` on the template to opt out explicitly",
                    severity=SEVERITY_WARNING,
                )
            )

    # AMNESIAC — a loop whose body never reads what the previous iteration produced. It
    # will redo the same first step forever, and each iteration will look productive.
    for loop in loops:
        body_text = json.dumps(loop.get("body") or {})
        # `{{nodes.X}}` inside a loop body is ALSO cross-iteration state: it reads a
        # sibling's accumulated output. Only accepting `last`/`iter` reported the shipped
        # `deep-research` and `audit-sweep` sweeps as amnesiac when both do carry state
        # forward — a false positive on the library is how a rule gets ignored.
        carries_state = any(token in body_text for token in ("{{last.", "{{iter.", "{{nodes."))
        if not carries_state:
            res.findings.append(
                LintFinding(
                    "WFL_AMNESIAC_LOOP",
                    f"loop {loop.get('id')!r} never binds `last` or `iter`: each iteration "
                    "starts blind, so it will repeat its first step indefinitely",
                    severity=SEVERITY_WARNING,
                )
            )

    # BLIND — a loop with no judge anywhere downstream. Unlike NODDING (a judge that
    # cannot reject) this is the absence of one entirely.
    if loops and not judges:
        res.findings.append(
            LintFinding(
                "WFL_BLIND_LOOP",
                "this template loops but has no judge or gate: it will converge on whatever "
                "the worker finds easiest to claim",
                severity=SEVERITY_ERROR,
            )
        )

    # TANGLED — a loop with no bound on it. A `condition` alone never terminates when the
    # condition is unreachable; a cap alone always burns the full budget.
    for loop in loops:
        cfg = loop.get("config") or {}
        raw_cap = cfg.get("max_iterations")
        # A BINDING is a valid cap: `max_iterations: "{{inputs.rounds}}"` is resolved at
        # run start, and requiring a literal int reported the shipped `deep-research` as
        # unbounded when its cap is simply user-supplied.
        has_cap = (isinstance(raw_cap, int) and not isinstance(raw_cap, bool) and raw_cap >= 1) or (
            isinstance(raw_cap, str) and "{{" in raw_cap
        )
        # `streak` alone is a valid until_dry exit: the engine terminates on N clean
        # iterations, and `progress_field` merely names which field it reads. Requiring
        # the field flagged the shipped `audit-sweep` as unbounded when it is not.
        has_exit = bool(
            cfg.get("condition") or cfg.get("progress_field") or cfg.get("streak") or cfg.get("n")
        )
        if not has_cap:
            res.findings.append(
                LintFinding(
                    "WFL_TANGLED_LOOP",
                    f"loop {loop.get('id')!r} has no `max_iterations`: an unreachable exit "
                    "condition then runs until something else kills it",
                    severity=SEVERITY_ERROR,
                )
            )
        if not has_exit:
            res.findings.append(
                LintFinding(
                    "WFL_TANGLED_LOOP",
                    f"loop {loop.get('id')!r} has no exit condition, only a cap: it will always "
                    "run to its limit, which makes the limit the behaviour rather than the guard",
                    severity=SEVERITY_WARNING,
                )
            )

    # MANUAL — an LLM stage doing work a `transform` or `action` does for zero tokens.
    for node in nodes:
        if node.get("kind") not in ("stage", "infer"):
            continue
        prompt = ((node.get("config") or {}).get("prompt") or "").lower()
        if not prompt:
            continue
        mechanical = (
            "reformat this json",
            "convert this to json",
            "extract the field",
            "rename the keys",
            "sort this list",
            "count the items",
        )
        if any(phrase in prompt for phrase in mechanical):
            res.findings.append(
                LintFinding(
                    "WFL_MANUAL_WORK",
                    f"{node.get('kind')} {node.get('id')!r} asks a model to do deterministic "
                    "data reshaping — a `transform` node does it for zero tokens and cannot "
                    "hallucinate the answer",
                    severity=SEVERITY_WARNING,
                )
            )


# ── The five-moves audit ──


def five_moves_audit(spec: dict[str, Any]) -> dict[str, Any]:
    """Where does each of the five moves live in this graph?

    Reports rather than judges. The point is to make an ABSENCE visible: a review template
    that persists nothing is fine, but a long-running autonomous template that schedules
    nothing will never wake up again, and that is the kind of gap nobody notices in a
    200-line spec.
    """
    root = spec.get("root")
    if not isinstance(root, dict):
        return {move: [] for move in FIVE_MOVES}

    nodes = _all_nodes(root)
    found: dict[str, list[str]] = {move: [] for move in FIVE_MOVES}

    for node in nodes:
        node_id = str(node.get("id", ""))
        kind = node.get("kind")
        cfg = node.get("config") or {}
        prompt = (cfg.get("prompt") or "").lower()
        posture = cfg.get("tools_posture")

        # discovery — reads the world before acting
        if kind in ("stage", "infer") and any(
            w in prompt for w in ("read", "search", "analyze", "understand", "inspect", "walk")
        ):
            found["discovery"].append(node_id)
        # handoff — carries state across an iteration boundary
        if "{{last." in json.dumps(cfg) or "handoff" in prompt or "carryover" in prompt:
            found["handoff"].append(node_id)
        # verification — checks the work independently
        if (
            _is_judge(node)
            or posture == "verify"
            or cfg.get("kind")
            in (
                "verify_command",
                "verify_script",
            )
        ):
            found["verification"].append(node_id)
        # persistence — writes something durable
        if kind == "action" or posture == "full":
            found["persistence"].append(node_id)
        # scheduling — arranges its own future
        if kind == "wait" or any(
            w in prompt for w in ("schedule", "check back", "recurring", "next check")
        ):
            found["scheduling"].append(node_id)

    return found


def audit_report(spec: dict[str, Any]) -> dict[str, Any]:
    """The five-moves audit plus the anti-pattern findings, for CI and the template page."""
    lint = lint_template(spec, bundled=bool(spec.get("source") == "bundled"))
    moves = five_moves_audit(spec)
    return {
        "template": spec.get("name", ""),
        "moves": moves,
        "absent_moves": [m for m, where in moves.items() if not where],
        "anti_patterns": [
            f.to_dict() for f in lint.findings if f.code.startswith("WFL_") and "_" in f.code
        ],
        "clean": lint.clean,
    }
