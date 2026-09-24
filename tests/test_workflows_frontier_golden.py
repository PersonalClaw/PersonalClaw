"""Golden-file proof that `frontier()` still decides exactly what it always did (PP-11).

PP-11 moves the frontier's admission step behind an ordered list of `AdmissionPolicy` objects.
"Pure refactor" is a claim a passing suite cannot settle: the suite asserts the properties someone
thought to assert, and the whole risk of rewriting a scheduler is the decision nobody wrote down —
an admission order, a tie between two caps, which refusals get a name. So the bar is the decisions
themselves, captured as bytes.

Two captures, both committed as fixtures BEFORE the extraction and diffed after:

* **`bundled.jsonl`** — the atom's stated bar. Every bundled template, driven through a
  deterministic tick trajectory under four admission scenarios (default caps, saturated lanes,
  starved lanes, run-level WIP=1). This is what a user's library actually schedules.
* **`policies.jsonl`** — a small synthetic matrix, because no bundled template declares
  `max_concurrency`, so the bundled capture alone would leave one of the three policies
  unexercised — and, worse, would leave the CAP TIE unexercised (`max_concurrency: 1` under
  WIP=1, where both container policies bind at the same number and only one of them gets to name
  the refusal).

`frontier()` is pure, so the trajectory driver is the only thing that could inject
nondeterminism: it completes every admitted node, skips every `to_skip` path, and releases
WAITING only when nothing else moved. Nothing is normalized away — there is no clock or id in a
frontier decision to normalize, which is itself the property this module protects.

Regenerating these fixtures is a deliberate act, not a convenience: run this module as a script,
with the tree under test pinned —

    PYTHONPATH=$PWD/src python tests/test_workflows_frontier_golden.py

The `PYTHONPATH` is not optional. A bare `python tests/…py` resolves `personalclaw` through the
venv's editable install, which points at the main checkout, so from a git worktree it would capture
a DIFFERENT tree's decisions and commit them as this branch's proof. (`pytest` is immune —
`pyproject.toml`'s `pythonpath = ["src", "."]` is resolved relative to rootdir.) There is no
environment variable that rewrites these files, because a golden rewritten by the run under test
blesses whatever that run did.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.models import InstanceState, Node, NodeKind, walk
from personalclaw.workflows.tick import Frontier, Limits, frontier

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "frontier_golden"

#: Bound on the trajectory. Every bundled template settles well inside this; the cap exists so a
#: template that cannot progress produces a finite capture instead of hanging the suite.
MAX_TICKS = 24

#: Handed to every completed node as its output. Deliberately rich enough that downstream
#: `branch` selectors route and downstream `foreach` bindings resolve to a real list — an
#: `items` that resolved to nothing would make the fan-out policies unobservable.
_NODE_OUTPUT: dict[str, Any] = {
    "ok": True,
    "case": "yes",
    "text": "golden",
    "value": 1,
    "count": 3,
    "items": ["alpha", "beta", "gamma"],
    "prompts": ["p1", "p2", "p3"],
    "findings": ["f1"],
    "decision": "proceed",
}

#: Run inputs. Templates declare inputs and bind them into items/selectors; an unresolvable input
#: would silently shrink the captured decision set.
_INPUTS: dict[str, Any] = {
    "topic": "golden",
    "goal": "golden",
    "url": "https://example.invalid/doc",
    "path": "/tmp/golden",
    "query": "golden",
    "items": ["alpha", "beta", "gamma"],
    "count": 3,
}

#: The four admission scenarios. `busy` pre-saturates the lanes from `running_lanes`, which is the
#: only way to observe `deferred` on a template whose ready set is smaller than a default cap.
SCENARIOS: list[dict[str, Any]] = [
    {"name": "default", "limits": None, "running_lanes": None, "wip": False},
    {
        "name": "starved",
        "limits": {"llm": 1, "io": 1, "compute": 1},
        "running_lanes": None,
        "wip": False,
    },
    {
        "name": "busy",
        "limits": None,
        "running_lanes": {"llm": 4, "io": 2, "compute": 64},
        "wip": False,
    },
    {"name": "wip", "limits": None, "running_lanes": None, "wip": True},
]


def _limits_for(spec: dict[str, int] | None) -> Limits | None:
    return None if spec is None else Limits(lanes=dict(spec))


#: `{{nodes.triage.output.tier}}` — the shape every bundled branch uses for its selector.
_SELECTOR = re.compile(r"\{\{\s*nodes\.([A-Za-z0-9_.\-]+)\.output\.([A-Za-z0-9_\-]+)")


def _routing_seeds(root: Node) -> dict[str, dict[str, Any]]:
    """Derive, from the spec itself, the outputs that make every `branch` actually route.

    A generic stub output resolves no real selector — `deep-research` routes on
    `output.tier ∈ {lookup, survey, investigation}` and `produce-and-audit` on the same key with
    different labels — so a fixed dictionary would leave routing (and therefore `to_skip`, and
    therefore the join-gating half of the frontier) entirely out of the capture. Seeding each
    selector's source field with that branch's FIRST declared case label is deterministic, derived
    from the spec rather than hand-maintained, and drives one case down every branch while the
    untaken siblings land in `to_skip`.
    """
    seeds: dict[str, dict[str, Any]] = {}
    for _path, node in walk(root):
        if node.kind != NodeKind.BRANCH or not node.cases:
            continue
        match = _SELECTOR.search(str((node.config or {}).get("on", "")))
        if match is None:
            continue
        seeds.setdefault(match.group(1), {})[match.group(2)] = next(iter(node.cases))
    return seeds


# ── the synthetic policy matrix ───────────────────────────────────────────────


def _fanout_spec(name: str, *, max_concurrency: int | None) -> dict[str, Any]:
    """A `foreach` over five literal items whose body is a two-stage sequence.

    Two stages rather than one on purpose: an item holds its concurrency slot from its first
    launched node until its whole body is terminal, so a single-stage body would never show the
    difference between "slot held across stages" and "slot released between them".
    """
    config: dict[str, Any] = {"items": ["i0", "i1", "i2", "i3", "i4"]}
    if max_concurrency is not None:
        config["max_concurrency"] = max_concurrency
    return {
        "name": name,
        "root": {
            "id": "root",
            "kind": "sequence",
            "children": [
                {
                    "id": "fan",
                    "kind": "foreach",
                    "config": config,
                    "body": {
                        "id": "body",
                        "kind": "sequence",
                        "children": [
                            {"id": "stage-a", "kind": "transform", "config": {"expr": "1"}},
                            {"id": "stage-b", "kind": "transform", "config": {"expr": "2"}},
                        ],
                    },
                }
            ],
        },
    }


#: `(spec name, max_concurrency, wip)`. The last row is the tie: `max_concurrency: 1` with the
#: run-level WIP=1 invariant also in force. Both container policies bind at 1, and today's code
#: (`cap = 1 if wip else _max_concurrency(node)`) gives WIP the refusal's name — so the refusal
#: lands in `wip_held`, not nowhere. That tie is the single most refactor-fragile decision in the
#: whole admission step, which is why it is a golden row rather than a comment.
POLICY_MATRIX: list[tuple[str, int | None, bool]] = [
    ("uncapped", None, False),
    ("uncapped-wip", None, True),
    ("cap2", 2, False),
    ("cap2-wip", 2, True),
    ("cap1", 1, False),
    ("cap1-wip", 1, True),
]


# ── the deterministic trajectory driver ──────────────────────────────────────


def _snapshot(spec: str, scenario: str, tick: int, fr: Frontier) -> dict[str, Any]:
    """One frontier decision, flattened to a stable shape.

    Every field of `Frontier` is captured. `ready`/`deferred` carry lane and iteration context
    because "which lane admitted it" and "which item is this" are the admission decision — a
    capture of bare paths would compare equal after a refactor that admitted the right count of
    the wrong things.
    """

    def _nodes(items: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "path": r.path,
                "node_id": r.node_id,
                "kind": str(getattr(r.node.kind, "value", r.node.kind)),
                "lane": r.lane,
                "has_item": r.has_item,
                "item": r.item,
                "iter_index": r.iter_index,
            }
            for r in items
        ]

    return {
        "spec": spec,
        "scenario": scenario,
        "tick": tick,
        "ready": _nodes(fr.ready),
        "deferred": _nodes(fr.deferred),
        "wip_held": list(fr.wip_held),
        "running": list(fr.running),
        "waiting": list(fr.waiting),
        "to_skip": list(fr.to_skip),
        "complete": fr.complete,
        "blocked": fr.blocked,
        "block_reason": fr.block_reason,
        "outcome": None if fr.outcome is None else fr.outcome.value,
    }


def _trajectory(spec_name: str, root: Node, scenario: dict[str, Any]) -> list[str]:
    """Drive one spec under one scenario until it settles, recording every decision.

    The driver is the only moving part, so it is deliberately dull: complete everything admitted,
    skip everything the frontier asked to skip, and release WAITING only when neither of those
    moved anything. No clock, no randomness, no dict iteration order that is not already sorted
    by `frontier()` itself.
    """
    states: dict[str, InstanceState] = {}
    outputs: dict[str, Any] = {}
    lines: list[str] = []
    limits = _limits_for(scenario["limits"])
    seeds = _routing_seeds(root)
    for tick in range(MAX_TICKS):
        fr = frontier(
            root,
            states,
            limits=limits,
            outputs=outputs,
            inputs=_INPUTS,
            running_lanes=scenario["running_lanes"],
            single_active_feature=scenario["wip"],
        )
        lines.append(
            json.dumps(
                _snapshot(spec_name, scenario["name"], tick, fr), ensure_ascii=False, default=str
            )
        )
        if fr.complete or fr.blocked:
            break
        progressed = False
        for path in fr.to_skip:
            if states.get(path) != InstanceState.SKIPPED:
                states[path] = InstanceState.SKIPPED
                progressed = True
        for item in fr.ready:
            states[item.path] = InstanceState.DONE
            outputs.setdefault(item.node_id, {**_NODE_OUTPUT, **seeds.get(item.node_id, {})})
            progressed = True
        if not progressed:
            for path in fr.waiting:
                states[path] = InstanceState.DONE
                progressed = True
        if not progressed:
            break
    return lines


def _capture_bundled() -> list[str]:
    lines: list[str] = []
    for name in template_names():
        wf = read_template(name)
        assert wf is not None, f"bundled template {name} did not load"
        for scenario in SCENARIOS:
            lines.extend(_trajectory(name, wf.root, scenario))
    return lines


def _capture_policies() -> list[str]:
    """Each matrix row twice: once with room in every lane, once with the lanes starved.

    The starved pass is what makes lane pressure and the container cap interact in the capture, so
    a refactor that let one policy shadow the other has somewhere to go red.
    """
    lines: list[str] = []
    for label, cap, wip in POLICY_MATRIX:
        root = Node.from_dict(_fanout_spec(label, max_concurrency=cap)["root"])
        roomy = {
            "name": "wip" if wip else "default",
            "limits": None,
            "running_lanes": None,
            "wip": wip,
        }
        lines.extend(_trajectory(label, root, roomy))
        starved = dict(roomy, name="starved", limits={"llm": 1, "io": 1, "compute": 1})
        lines.extend(_trajectory(label, root, starved))
    return lines


# ── the comparison ───────────────────────────────────────────────────────────


def _assert_golden(name: str, actual: list[str]) -> None:
    path = GOLDEN_DIR / f"{name}.jsonl"
    assert path.is_file(), (
        f"{path} is missing — regenerate with `python {Path(__file__).name}` and commit it BEFORE "
        "refactoring, never after"
    )
    expected = path.read_text(encoding="utf-8").splitlines()
    if expected == actual:
        return
    for i, (want, got) in enumerate(zip(expected, actual)):
        if want != got:
            raise AssertionError(
                f"{name}.jsonl diverged at line {i + 1}\n  golden: {want}\n  actual: {got}"
            )
    raise AssertionError(
        f"{name}.jsonl length changed: golden has {len(expected)} lines, "
        f"the frontier produced {len(actual)}"
    )


def test_every_bundled_template_still_schedules_byte_identically():
    """The atom's stated bar: the bundled library's frontier decisions, byte for byte."""
    _assert_golden("bundled", _capture_bundled())


def test_the_container_policy_matrix_still_schedules_byte_identically():
    """`max_concurrency`, WIP=1, and the tie where both bind at the same number."""
    _assert_golden("policies", _capture_policies())


def test_the_golden_captured_every_admission_outcome():
    """A vacuity guard: goldens that captured nothing interesting would still compare equal.

    Every field the admission step writes must appear somewhere in the fixtures, and every policy
    must be observably binding. Without this a capture that (say) never resolved a `foreach`'s
    items would happily pass forever while proving nothing about two of the three policies.
    """
    bundled = [json.loads(x) for x in _read_golden("bundled")]
    policies = [json.loads(x) for x in _read_golden("policies")]
    assert bundled and policies

    assert any(f["ready"] for f in bundled), "no template ever admitted work"
    assert any(f["deferred"] for f in bundled), "lane pressure was never observed"
    assert any(f["to_skip"] for f in bundled), "no branch ever routed"
    assert any(f["complete"] for f in bundled), "no template ever ran to completion"
    assert any(f["blocked"] for f in bundled), "the deadlock branch was never captured"

    assert any(f["wip_held"] for f in policies), "the WIP=1 invariant never held an item"
    assert any(f["deferred"] for f in policies), "the starved-lane pass admitted everything"
    # `max_concurrency` binding without WIP: capped rows must at some tick launch strictly fewer
    # item bodies than the uncapped row does at the same tick.
    capped = {
        f["tick"]
        for f in policies
        if f["spec"] == "cap2" and f["scenario"] == "default" and len(f["ready"]) == 2
    }
    assert capped, "max_concurrency: 2 never limited a tick to two items"
    uncapped = {
        f["tick"]
        for f in policies
        if f["spec"] == "uncapped" and f["scenario"] == "default" and len(f["ready"]) == 5
    }
    assert uncapped, "the uncapped fan-out never launched all five items at once"

    # The tie: `max_concurrency: 1` + WIP=1. The refusal must carry WIP's name.
    tie = [f for f in policies if f["spec"] == "cap1-wip" and f["wip_held"]]
    assert tie, (
        "the cap tie was never captured: with max_concurrency=1 AND the run-level WIP=1 "
        "invariant, the refusal must still be named `wip_held` — a declared invariant being "
        "enforced, not anonymous container pressure"
    )
    # And its non-WIP twin must refuse the same items ANONYMOUSLY — that asymmetry is the
    # `wip_held` vs `deferred` distinction this atom is forbidden to collapse.
    twin = [f for f in policies if f["spec"] == "cap1" and f["scenario"] == "default"]
    assert twin, "the cap1 control row is missing"
    assert not any(f["wip_held"] or f["deferred"] for f in twin), (
        "max_concurrency alone must not name its refusals: an unstarted item of a capped foreach "
        "is not `deferred` (that means lane pressure) and not `wip_held` (no invariant was "
        "declared)"
    )


def _read_golden(name: str) -> list[str]:
    return (GOLDEN_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()


def test_the_frontier_module_reads_no_clock_and_does_no_io():
    """Purity as a rail rather than a docstring promise.

    `frontier()`'s purity is what makes `rewind` tractable: state is re-derived from scratch every
    tick, so a scheduler that consulted the wall clock would decide differently on replay and the
    journal's guarantees would be worthless. Checked statically (an AST scan, not a runtime probe)
    because the impurity that matters is the one on a rarely-taken branch, which no single call
    would execute.
    """
    import personalclaw.workflows.admission as admission_mod
    import personalclaw.workflows.tick as tick_mod

    banned_modules = {
        "time",
        "datetime",
        "random",
        "os",
        "pathlib",
        "secrets",
        "socket",
        "subprocess",
        "sqlite3",
        "uuid",
        "threading",
        "asyncio",
        "logging",
        "personalclaw.workflows.store",
        "personalclaw.workflows.journal",
    }
    banned_calls = {"open", "input", "print", "id"}

    offenders: list[str] = []
    for mod in (tick_mod, admission_mod):
        path = Path(mod.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in banned_calls:
                    offenders.append(f"{path.name}:{node.lineno} calls {node.func.id}()")
                continue
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if name in banned_modules or root in banned_modules:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, (
        "the frontier and its admission policies must stay pure — no clock, no I/O, no "
        "randomness: " + "; ".join(offenders)
    )


def test_the_frontier_is_deterministic_across_repeated_calls():
    """The runtime half of purity: same inputs, same decision, and no argument mutation."""
    root = Node.from_dict(_fanout_spec("determinism", max_concurrency=2)["root"])
    states: dict[str, InstanceState] = {"root.fan.body#0.stage-a": InstanceState.DONE}
    before = dict(states)
    inputs = dict(_INPUTS)
    first = _snapshot(
        "d", "d", 0, frontier(root, states, inputs=inputs, single_active_feature=True)
    )
    second = _snapshot(
        "d", "d", 0, frontier(root, states, inputs=inputs, single_active_feature=True)
    )
    assert first == second
    assert states == before, "frontier() mutated the state map it was handed"
    assert inputs == _INPUTS, "frontier() mutated the inputs it was handed"


# ── deliberate regeneration ──────────────────────────────────────────────────


def _regenerate() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, lines in (("bundled", _capture_bundled()), ("policies", _capture_policies())):
        (GOLDEN_DIR / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {name}.jsonl ({len(lines)} decisions)")
    print(f"goldens in {GOLDEN_DIR}")


if __name__ == "__main__":
    _regenerate()
