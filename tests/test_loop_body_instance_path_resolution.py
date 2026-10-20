"""#3371 — a node inside a loop/foreach BODY must be nameable and reachable on every run surface.

**The defect.** An instance path carries an iteration marker on the segment that iterates:
``root.body@0``. When the body is a CONTAINER the marker therefore sits **mid-path** —
``root.body@0.children[0]`` — but three call sites in ``workflows/service.py`` truncated at the
first ``@`` (``path.split("#")[0].split("@")[0]``), collapsing that child to ``root.body``.
Consequences, all measured live in #3371 on a `general-project` run:

* ``_nodes_of`` labelled every body sibling with the BODY's id, so ``GET
  /api/workflows/runs/{id}`` reported two rows both called ``step`` instead of ``work`` and
  ``judge``.
* ``inspect_node`` / ``output()`` matched a spec path ``b`` with ``p == b or
  p.startswith(b + "#") or p.startswith(b + "@")``. A real body-child instance path satisfies
  none of the three, so both answered ``WF_NODE_NOT_RUN`` for a node that ran and FAILED — and
  asking for the mislabelled id instead answered ``WF_NODE_NOT_TERMINAL`` *about the body*. A
  loop-body node was reachable under no id at all.
* the ``[3/12]`` fan-out counter keys off the same base, so a fan-out inside a loop body was
  counted against the BODY: a 3-item foreach beside one sibling reported ``item_total: 4``.

**Why the frontend is in scope.** The DAG row label is rendered from exactly this field —
``web/src/pages/workflows/runDag.ts:136`` reads ``row.node.node_id`` — and the chat workflow card
deep-links ``?node=<node_id>``. So a wrong ``node_id`` is not a cosmetic API detail: it is the
identifier the run view shows and the only handle a user has for opening the failing node.

**Why no existing test caught it.** Every frontend fixture uses flat instance paths
(``root.plan``, ``root.draft`` — ``web/src/app/workflowCardNodeDeepLink.test.tsx:40-42``), shapes
where ``split("@")[0]`` is a no-op. The mid-path marker only exists once a loop body holding a
container actually executes, so this file drives REAL runs through ``RunController`` and reads the
paths the engine itself produced rather than hand-writing them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun, walk

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: A counted loop whose body is a SEQUENCE — the shape every ported loop kind uses, and the only
#: shape that puts an iteration marker mid-path. `expr` varies with `{{iter}}` so the
#: identical-output thrash detector does not escalate the run before it finishes, and two
#: iterations exist so the "LAST instance" rule `inspect_node`/`output()` document is observable.
_SPEC: dict[str, Any] = {
    "name": "loop-body-paths",
    "root": {
        "kind": "loop",
        "id": "project",
        "config": {"mode": "counted", "n": 2},
        "body": {
            "kind": "sequence",
            "id": "step",
            "children": [
                {"kind": "transform", "id": "work", "config": {"expr": "work {{iter}}"}},
                {"kind": "transform", "id": "judge", "config": {"expr": "judge {{iter}}"}},
            ],
        },
    },
}

#: A fan-out INSIDE a loop body, beside one sibling. Single iteration on purpose: with one
#: iteration the foreach's item count and the number of instances sharing its body's spec path are
#: the same number, so `item_total` has one unambiguous right answer (3) and the body's inflated
#: count (4 = the sibling + three items) is unmistakably wrong.
_FANOUT_SPEC: dict[str, Any] = {
    "name": "fanout-in-loop-body",
    "root": {
        "kind": "loop",
        "id": "project",
        "config": {"mode": "counted", "n": 1},
        "body": {
            "kind": "sequence",
            "id": "step",
            "children": [
                {"kind": "transform", "id": "work", "config": {"expr": "plan"}},
                {
                    "kind": "foreach",
                    "id": "review",
                    "config": {"items": ["a", "b", "c"]},
                    "body": {"kind": "transform", "id": "one", "config": {"expr": "seen {{item}}"}},
                },
            ],
        },
    },
}


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at a tmp home and put it back.

    The destructive-test rule: nothing here may touch the real `~/.personalclaw`. Patching
    `store.config_dir` is the seam the other store-level tests use for the same reason.
    """
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _drive(home: Path, spec: dict[str, Any]) -> str:
    """Run `spec` to completion and return its run id, with the store pinned to `home`."""
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name=str(spec["name"])))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        status = await controller.run_to_completion(timeout=60)
        assert status is RunStatus.COMPLETE, f"the fixture run did not finish: {status}"
        return run.id


def _rows(run_id: str) -> dict[str, dict[str, Any]]:
    return {str(row["instance_path"]): row for row in service._nodes_of(run_id)}


async def test_the_engine_really_produces_a_mid_path_iteration_marker(tmp_path: Path) -> None:
    """The non-vacuity control. Every other assertion here is about how a MID-PATH `@N` resolves,
    so if the engine stopped emitting that shape the whole file would pass while proving nothing.

    Asserts both halves of the premise: the stored instance paths carry a marker that is not the
    last segment, and no spec path carries one — i.e. an instance→spec translation is genuinely
    required rather than an identity.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _SPEC)
    with _isolated(home):
        instances = sorted(store.read_state(run_id))

    assert instances == [
        "root",
        "root.body@0.children[0]",
        "root.body@0.children[1]",
        "root.body@1.children[0]",
        "root.body@1.children[1]",
    ], f"the engine no longer emits a mid-path iteration marker: {instances}"

    spec_paths = {p for p, _ in walk(Node.from_dict(_SPEC["root"]))}
    assert "root.body.children[0]" in spec_paths
    assert not any("@" in p or "#" in p for p in spec_paths), "a spec path carries a marker"


async def test_each_body_child_reports_its_own_node_id(tmp_path: Path) -> None:
    """Claim (a) — the identity the run view renders.

    `web/src/pages/workflows/runDag.ts:136` labels each DAG row from `row.node.node_id`, and the
    chat workflow card deep-links `?node=<node_id>`. Before the fix both children reported the
    body's id (`step`), so the run view showed two identical rows and the failing node could
    neither be named nor opened.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _SPEC)
    with _isolated(home):
        rows = _rows(run_id)

    assert rows["root.body@0.children[0]"]["node_id"] == "work"
    assert rows["root.body@0.children[1]"]["node_id"] == "judge"
    assert rows["root.body@1.children[0]"]["node_id"] == "work"
    assert rows["root.body@1.children[1]"]["node_id"] == "judge"
    # The loop itself keeps its own id — the fix must resolve a marker, not over-strip a path.
    assert rows["root"]["node_id"] == "project"
    # The body's id must not appear as a LABEL anywhere: it is a container and has no instance.
    assert "step" not in {str(r["node_id"]) for r in rows.values()}
    # And no two rows of one iteration share an id, which is the symptom a reader sees.
    iteration_zero = [
        rows[p]["node_id"] for p in ("root.body@0.children[0]", "root.body@0.children[1]")
    ]
    assert len(set(iteration_zero)) == 2, f"body siblings collapsed to one id: {iteration_zero}"


@pytest.mark.parametrize(("node_id", "child"), [("work", "children[0]"), ("judge", "children[1]")])
async def test_inspect_node_resolves_a_body_child_by_its_spec_id(
    tmp_path: Path, node_id: str, child: str
) -> None:
    """Claim (b), first half — the forensics route.

    `WF_NODE_NOT_RUN` for a node whose instance the node list shows as terminal is the exact
    contradiction #3371 reported, and it is what made a FAILED body node unreachable.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _SPEC)
    with _isolated(home):
        result = service.inspect_node(run_id, node_id)

    assert result.get("ok") is True, result
    # The LAST instance, matching what `inspect_node` documents for a fan-out.
    assert result["instance_path"] == f"root.body@1.{child}"
    assert result["node_id"] == node_id


@pytest.mark.parametrize(("node_id", "child"), [("work", "children[0]"), ("judge", "children[1]")])
async def test_output_resolves_a_body_child_by_its_spec_id(
    tmp_path: Path, node_id: str, child: str
) -> None:
    """Claim (b), second half — `output()` carries an identical matcher, so it failed identically.

    Asserting the VALUE, not just `ok`: a matcher that resolved a child to its BODY would answer
    `ok` while handing back a sibling's output, which is worse than the refusal it replaces.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _SPEC)
    with _isolated(home):
        result = service.output(run_id, node_id)

    assert result.get("ok") is True, result
    assert result["instance_path"] == f"root.body@1.{child}"
    assert result["output"] == f"{node_id} 1", result


async def test_the_body_of_a_node_asked_for_by_its_own_id_is_not_its_children(
    tmp_path: Path,
) -> None:
    """The over-match this fix removes, stated directly.

    The old matcher's `p.startswith(b + "@")` made every body DESCENDANT an instance of the body,
    so `output("step")` answered with a leaf's value. A container has no stored instance, so the
    honest answer is the refusal — and a refusal that names the container is debuggable, whereas
    a sibling's output presented as the container's is not.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _SPEC)
    with _isolated(home):
        result = service.output(run_id, "step")

    assert result.get("ok") is False, result
    assert result["code"] == "WF_NODE_NOT_RUN", result


async def test_a_fanout_inside_a_loop_body_counts_its_own_items(tmp_path: Path) -> None:
    """Claim (c) — the fan-out counter keyed off the same truncated base.

    Three items beside one sibling: before the fix every one of the four leaf rows was counted as
    an instance of `root.body`, so each foreach item rendered `[N/4]` for a three-item fan-out.
    The count has to attribute to the item's own spec path.
    """
    home = tmp_path / "home"
    run_id = await _drive(home, _FANOUT_SPEC)
    with _isolated(home):
        rows = _rows(run_id)

    items = [rows[f"root.body@0.children[1].body#{i}"] for i in range(3)]
    assert [r["node_id"] for r in items] == ["one", "one", "one"]
    assert [r["item_index"] for r in items] == [0, 1, 2]
    assert [r["item_total"] for r in items] == [3, 3, 3], [r["item_total"] for r in items]
    assert [r["item_label"] for r in items] == ["a", "b", "c"]
    # The sibling is not part of that fan-out and must carry no item counters at all.
    sibling = rows["root.body@0.children[0]"]
    assert sibling["node_id"] == "work"
    assert "item_index" not in sibling and "item_total" not in sibling, sibling
