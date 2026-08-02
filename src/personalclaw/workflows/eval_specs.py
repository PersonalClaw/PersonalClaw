"""Per-template eval specs, derived rather than authored (UP-R13.3, S45).

Session 40's routing fixtures assert which template an intent picks. They say nothing about whether
that template, once picked, produces a good plan — and a routing suite that passes while every
template is subtly wrong is exactly the false confidence the old loop classifiers had.

This module closes that gap the same way session 42 closed the parameter gap: by DERIVING the
benchmark from the template artifact instead of maintaining it beside it. One declarative artifact
compiles into both the runnable plan and its eval config, so the eval cannot drift from the template
— the drift is what makes a hand-maintained benchmark worse than none, because a stale expectation
gets "fixed" by loosening the assertion.

What is derivable and what is not is a hard line here:

* **Derivable** — the fixture intents (from the template's own keywords and example outputs), the
  expected parameterization (from the input schema session 42 already derives from the tree), the
  structural acceptance checks (from the node kinds and gates that are literally present).
* **NOT derivable** — whether the OUTPUT is any good. That needs a judge, and this module never
  pretends otherwise: `EvalSpec.graded_checks` names what a judge would have to grade and leaves it
  to LEARNING-FLYWHEEL, which owns the judge harness.

Pure functions. No LLM, no I/O, no clock — an eval spec that needed a model call to build would be
one nobody runs in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.contracts import (
    MACHINE_VERIFIED_GATES,
    derive_contracts,
    resolve_unfilled_inputs,
)

#: Fixture intents per template. Enough to catch a routing or parameterization regression, few
#: enough that the suite stays free — this runs on every CI pass and a suite that costs minutes is
#: one that gets marked slow and then skipped.
FIXTURES_PER_TEMPLATE = 3


@dataclass
class Fixture:
    """One representative intent and what it should produce.

    `expected_params` is the required subset, not the full set: asserting the exact set would make
    every added optional parameter a test failure, which trains a maintainer to update expectations
    without reading them.
    """

    intent: str
    expected_template: str
    expected_params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "expected_template": self.expected_template,
            "expected_params": list(self.expected_params),
        }


@dataclass
class EvalSpec:
    """One template's derived benchmark.

    The three check lists are separate because they fail for different reasons and cost different
    amounts. Structural checks are free and deterministic; parameterization checks are free and
    catch the launch-form drift; graded checks cost a model call and are the only ones that can be
    wrong about a passing template.
    """

    template: str
    fixtures: list[Fixture] = field(default_factory=list)
    structural_checks: list[str] = field(default_factory=list)
    parameterization_checks: list[str] = field(default_factory=list)
    graded_checks: list[str] = field(default_factory=list)
    #: Why this template has no graded checks, when it has none. An empty list with no explanation
    #: reads as "nothing to grade", which is a claim.
    graded_note: str = ""

    @property
    def free(self) -> bool:
        """Whether this spec runs with no model call. The CI-eligible subset."""
        return not self.graded_checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "fixtures": [f.to_dict() for f in self.fixtures],
            "structural_checks": list(self.structural_checks),
            "parameterization_checks": list(self.parameterization_checks),
            "graded_checks": list(self.graded_checks),
            "graded_note": self.graded_note,
            "free": self.free,
        }


def derive_eval_spec(name: str, spec: dict[str, Any], metadata: dict[str, Any]) -> EvalSpec:
    """Derive one template's eval spec from the template itself.

    Everything comes from the artifact: fixtures from the match metadata a user would actually type,
    parameterization from the tree's own bindings, structural checks from the nodes that are
    present. Nothing is asserted that the template does not itself claim — an eval that tested an
    aspiration would fail on a correct template.
    """
    fixtures = _fixtures(name, spec, metadata)
    return EvalSpec(
        template=name,
        fixtures=fixtures,
        structural_checks=_structural_checks(spec),
        parameterization_checks=_param_checks(spec),
        graded_checks=_graded_checks(spec, metadata),
        graded_note=_graded_note(spec, metadata),
    )


def _fixtures(name: str, spec: dict[str, Any], metadata: dict[str, Any]) -> list[Fixture]:
    """Fixture intents built from the template's own matchable surface.

    Keywords first (what a user types), then example outputs phrased as a request (an intent
    resembles its desired output more than it resembles prose about a workflow — session 40's T2
    finding). Required parameters come from the derived schema so a fixture cannot ask for a
    parameter the tree does not read.
    """
    required = [p.name for p in resolve_unfilled_inputs(spec) if p.required]
    intents: list[str] = []
    keywords = [str(k) for k in (metadata.get("keywords") or []) if str(k).strip()]
    if keywords:
        intents.append(" ".join(keywords[:4]))
    for output in (metadata.get("example_outputs") or [])[:2]:
        text = str(output).strip()
        if text:
            intents.append(f"I need {text[0].lower() + text[1:] if text else text}")
    if not intents:
        # No matchable surface is itself the finding — a fixture from the name at least keeps the
        # template in the suite, so its missing metadata shows up as a low-confidence match rather
        # than as absence from the report.
        intents.append(name.replace("-", " "))
    return [
        Fixture(intent=intent, expected_template=name, expected_params=list(required))
        for intent in intents[:FIXTURES_PER_TEMPLATE]
    ]


def _structural_checks(spec: dict[str, Any]) -> list[str]:
    """Checks derived from nodes that are actually present.

    Each is phrased as an assertion a runner can make against a produced plan. Only present
    structure is asserted: a check that the plan "should have a gate" on a template with no gate
    would be a test of what the template ought to be, which is a design opinion masquerading as a
    regression.
    """
    checks: list[str] = []
    nodes = _walk(spec.get("root"))
    kinds = {n.get("kind") for n in nodes}
    node_ids = [str(n.get("id")) for n in nodes if n.get("id")]
    if node_ids:
        checks.append(f"every node id present: {', '.join(sorted(node_ids)[:8])}")
    contracts = derive_contracts(spec)
    verified = [c for c in contracts if c.verification in MACHINE_VERIFIED_GATES or c.verifiable]
    if verified:
        checks.append(f"{len(verified)} of {len(contracts)} stages remain machine-verified")
    if "loop" in kinds:
        checks.append("every loop keeps a bounded exit condition")
    if "foreach" in kinds:
        checks.append("every foreach keeps its items binding")
    if "gate" in kinds:
        gate_ids = sorted(str(n.get("id")) for n in nodes if n.get("kind") == "gate")
        checks.append(f"gates survive parameterization: {', '.join(gate_ids)}")
    return checks


def _param_checks(spec: dict[str, Any]) -> list[str]:
    """Checks over the derived launch form.

    These catch the two failures session 42 measured on the real library, in both directions: a
    declared input nothing reads (a control that silently does nothing) and a binding with no
    declared input (a run that dies on its first binding).
    """
    params = resolve_unfilled_inputs(spec)
    if not params:
        return ["takes no parameters — launch form stays empty"]
    checks = []
    required = [p.name for p in params if p.required]
    optional = [p.name for p in params if not p.required]
    if required:
        checks.append(f"required parameters asked: {', '.join(sorted(required))}")
    if optional:
        checks.append(f"optional parameters defaulted, not asked: {', '.join(sorted(optional))}")
    checks.append("no declared input goes unread by the tree")
    return checks


def _spends_tokens(spec: dict[str, Any]) -> bool:
    """Whether any node makes a model call, using the engine's OWN `LLM_KINDS`.

    Measured: testing for `stage` alone reported `deep-research` — eight `infer` calls — as fully
    deterministic, so it was filed as needing no judge. An `infer` is one bounded model call, which
    is exactly the kind of output only a judge can assess. Reusing the engine's set means a
    thirteenth node kind cannot quietly land on the wrong side of this line.
    """
    from personalclaw.workflows.models import LLM_KINDS

    llm = {kind.value for kind in LLM_KINDS}
    return any(node.get("kind") in llm for node in _walk(spec.get("root")))


def _graded_checks(spec: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """What a judge would have to grade, named rather than graded.

    Derived from the template's own `example_outputs`: the template's claim about what it produces
    IS the acceptance criterion, and grading anything else would be inventing a standard the
    template never claimed. Templates whose nodes are all deterministic get nothing here — an
    action either succeeded or returned a typed error, and asking a model for an opinion about
    arithmetic spends a call to add doubt.
    """
    if not _spends_tokens(spec):
        return []
    checks = []
    for output in (metadata.get("example_outputs") or [])[:3]:
        text = str(output).strip()
        if text:
            checks.append(f"output is recognizably: {text}")
    return checks


def _graded_note(spec: dict[str, Any], metadata: dict[str, Any]) -> str:
    if not _spends_tokens(spec):
        return "all nodes are deterministic — the outputs are facts, not opinions"
    if not (metadata.get("example_outputs") or []):
        return "no example_outputs declared, so there is no stated standard to grade against"
    return ""


def _walk(root: Any) -> list[dict[str, Any]]:
    """Every node in the tree, via the ENGINE's own walk.

    Measured: a hand-rolled walk over guessed key names (`branches`, `then`, `otherwise`) found 4 of
    13 nodes in `deep-research` and 2 of 8 in `audit-sweep` — the engine's branch children live
    under `cases`/`default_case`, so every branch subtree was silently skipped. The eval then
    reported `deep-research` as having no model-bearing nodes at all, which is a confident false
    claim about the exact thing the eval exists to check.

    Typing a spec through `Node.from_dict` first is what makes the walk correct rather than
    plausible: the node algebra is defined in one place, and a second traversal that has to be kept
    in sync with it is a traversal that will drift.
    """
    from personalclaw.workflows.models import Node, walk

    if not isinstance(root, dict):
        return []
    try:
        typed = Node.from_dict(root)
    except Exception:
        return []
    return [node.to_dict() for _path, node in walk(typed)]


def suite(specs: list[EvalSpec]) -> dict[str, Any]:
    """The whole suite as one artifact, with the free/graded split made explicit.

    `free_fixtures` is what CI runs. Reporting the graded count separately rather than folding it in
    is what keeps a passing CI run from being read as "every template was evaluated" — separating
    validation failures from ungraded quality is what makes the eval actionable at all.
    """
    free = [s for s in specs if s.free]
    graded = [s for s in specs if not s.free]
    return {
        "templates": len(specs),
        "free_fixtures": sum(len(s.fixtures) for s in specs),
        "free_only_templates": [s.template for s in free],
        "needs_judge": [s.template for s in graded],
        "structural_checks": sum(len(s.structural_checks) for s in specs),
        "graded_checks": sum(len(s.graded_checks) for s in specs),
        "specs": [s.to_dict() for s in specs],
    }


def derive_library_suite() -> dict[str, Any]:
    """Derive the suite for the whole bundled library.

    The entry point CI and the flywheel both call. It reads the shipped templates directly so a new
    template joins the suite by existing — a benchmark you have to remember to register is one that
    silently omits the newest template, which is the one most likely to be wrong.
    """
    from personalclaw.workflows import bundled_defs

    specs: list[EvalSpec] = []
    for name in sorted(bundled_defs.template_names()):
        definition = bundled_defs.read_template(name)
        if definition is None:
            continue
        specs.append(derive_eval_spec(name, _as_spec(definition), _as_metadata(definition)))
    return suite(specs)


def _as_spec(definition: Any) -> dict[str, Any]:
    root = getattr(definition, "root", None)
    inputs: dict[str, Any] = {}
    for key, value in (getattr(definition, "inputs", None) or {}).items():
        inputs[key] = value.to_dict() if hasattr(value, "to_dict") else value
    root_dict: dict[str, Any] = {}
    if root is not None:
        root_dict = root.to_dict() if hasattr(root, "to_dict") else root
    return {"inputs": inputs, "root": root_dict}


def _as_metadata(definition: Any) -> dict[str, Any]:
    meta = getattr(definition, "metadata", None)
    if meta is None:
        return {}
    return meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)
