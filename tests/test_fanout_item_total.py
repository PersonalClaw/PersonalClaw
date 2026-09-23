"""#3403 — a fan-out's `item_total` is ONE number, and both surfaces that carry it report it.

`item_total` is the denominator of the `[i/total]` progress marker that exists, per the WF2-R5
comment, so "a fan-out of twelve does not render as twelve identical rows". Two surfaces render
it, each used to derive it for itself, and each derivation was wrong in a different way:

* **The REST node list** (`service._nodes_of`) counted instances sharing a `spec_path`. `spec_path`
  strips EVERY marker, so a three-item fan-out inside a two-iteration loop collapsed into one group
  of six: `main` reported **6** where the answer is **3**, across six rows that each claimed to be
  item 1–3 of 6.
* **The live event stream** (`controller._item_context`, published as `workflow_node_started`)
  counted the instance map at DISPATCH, when it holds only the items dispatched so far. So the
  denominator tracked the numerator: a twelve-item fan-out streamed no marker at all, then `[2/2]`,
  `[3/3]` … `[12/12]` — a denominator carrying no information, which is exactly the failure the
  field exists to prevent.

**The fix is one denominator, computed once.** `tick._visit_foreach` is the only place the items are
resolved, so `len(items)` is computed there, travels on `ReadyNode.item_total`, is stamped onto
`NodeInstance.item_total` at dispatch, and is read by both surfaces. A count cannot be right at
dispatch time; the resolved item count can, so it is what travels. `sibling_group` (only the
instance's own trailing marker removed) replaces `spec_path` for the one quantity that genuinely
must still be counted: a LOOP's iteration coordinate, whose total nothing knows until the loop ends.

**Why every existing rail was structurally unable to see this**, named here so a future change
cannot mistake their green for coverage:

1. `test_loop_body_instance_path_resolution.py`'s
   `test_a_fanout_inside_a_loop_body_counts_its_own_items` is single-iteration *on purpose* — its
   own fixture comment says that with one iteration the item count and the instance count are the
   same number, so `item_total` has one unambiguous answer.
2. `test_workflows_run_delete.py::TestForeachProjection` fans out at TOP LEVEL with no enclosing
   loop and reads the rows only after `run_to_completion` — no loop multiplier and a full instance
   map, so neither arm can exhibit itself.
3. `web/src/pages/workflows/workflowMeta.test.ts` asserts `itemProgress({item_index: 11,
   item_total: 12}) === '[12/12]'` on a HAND-CONSTRUCTED object. `[12/12]` is precisely the string
   the broken producer emitted for the final item, asserted there as correct rendering — which it
   is, for that input. It pins the renderer and can never observe the producer.

So the fixtures here are multi-iteration and the event assertions read what the ENGINE published.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun, sibling_group, spec_path

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: A three-item fan-out inside a COUNTED loop of 2 — the shape that produced the 6. `transform`
#: bodies so nothing needs a model binding or a subagent.
_FANOUT_IN_LOOP: dict[str, Any] = {
    "name": "fanout-in-loop-n2",
    "root": {
        "kind": "loop",
        "id": "project",
        "config": {"mode": "counted", "n": 2},
        "body": {
            "kind": "sequence",
            "id": "step",
            "children": [
                {"kind": "transform", "id": "work", "config": {"expr": "plan {{iter}}"}},
                {
                    "kind": "foreach",
                    "id": "review",
                    "config": {"items": ["a", "b", "c"]},
                    "body": {"kind": "transform", "id": "one", "config": {"expr": "saw {{item}}"}},
                },
            ],
        },
    },
}

#: Twelve items, top level. The size is the point: the broken stream was `[2/2] [3/3] … [12/12]`,
#: so a three-item fixture could not tell "the denominator is the item count" from "the denominator
#: is the index plus one" for more than one row.
_TWELVE: dict[str, Any] = {
    "name": "fan-twelve",
    "root": {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": [f"item-{i}" for i in range(12)]},
        "body": {"kind": "transform", "id": "one", "config": {"expr": "saw {{item}}"}},
    },
}

#: A counted LOOP inside a fan-out body — the mirror of the first fixture. The loop's own iteration
#: coordinate and the fan-out's item coordinate share a spec path family, so this is where an
#: over-broad counting key shows up in the other direction.
_LOOP_IN_FANOUT: dict[str, Any] = {
    "name": "loop-in-fanout",
    "root": {
        "kind": "foreach",
        "id": "fan",
        "config": {"items": ["x", "y"]},
        "body": {
            "kind": "loop",
            "id": "inner",
            "config": {"mode": "counted", "n": 3},
            "body": {"kind": "transform", "id": "tick", "config": {"expr": "n {{iter}}"}},
        },
    },
}


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at a tmp home and put it back. Nothing here may touch the real home."""
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _drive(home: Path, spec: dict[str, Any]) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Run `spec` to completion, returning its run id and every event it PUBLISHED.

    The events are captured through the engine's own `publish` seam rather than reconstructed,
    because arm F2 lives at the moment of publication: a payload rebuilt after the run would be
    built against a complete instance map and could not exhibit the defect.
    """
    published: list[tuple[str, dict[str, Any]]] = []
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name=str(spec["name"])))
        store.write_spec(run.id, spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(
                publish=lambda event, payload: published.append((event, dict(payload)))
            ),
        )
        status = await controller.run_to_completion(timeout=60)
        assert status is RunStatus.COMPLETE, f"the fixture run did not finish: {status}"
        return run.id, published


def _rows(run_id: str) -> dict[str, dict[str, Any]]:
    return {str(row["instance_path"]): row for row in service._nodes_of(run_id)}


def _started(published: list[tuple[str, dict[str, Any]]], node_id: str) -> list[dict[str, Any]]:
    return [
        p for event, p in published if event == "workflow_node_started" and p["node_id"] == node_id
    ]


# ── the non-vacuity controls ─────────────────────────────────────────────────


async def test_the_fixture_really_produces_cross_iteration_fanout_instances(tmp_path: Path) -> None:
    """The premise, asserted rather than assumed.

    Every claim below is about a fan-out whose items exist under TWO iteration markers. If the
    engine stopped producing that shape — or if the loop ran once — this whole file would pass
    while proving nothing, which is the failure mode of the three rails it replaces.
    """
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        instances = sorted(store.read_state(run_id))

    items = [p for p in instances if ".body#" in p]
    assert len(items) == 6, f"expected 3 items × 2 iterations, got {items}"
    assert {p for p in items if ".body@0." in p} and {p for p in items if ".body@1." in p}, items
    # And the two keyings genuinely differ on this data — otherwise the fix below is a no-op.
    assert len({spec_path(p) for p in items}) == 1, "spec_path no longer collapses the iterations"
    assert len({sibling_group(p) for p in items}) == 2, "sibling_group no longer separates them"


async def test_the_old_keying_would_still_report_six_on_this_data(tmp_path: Path) -> None:
    """The arithmetic control from the finding, executed against live instance paths.

    `main` counted instances sharing a `spec_path`; over these nine instances that returns 6 for a
    three-item fan-out. Asserting it here is what makes the `== 3` assertions below attributable to
    the keying rather than to the fixture happening to be small.
    """
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        instances = sorted(store.read_state(run_id))

    totals: dict[str, int] = {}
    for path in instances:
        totals[spec_path(path)] = totals.get(spec_path(path), 0) + 1
    fanout_key = spec_path("root.body@0.children[1].body#0")
    assert totals[fanout_key] == 6, totals


# ── F1: the REST node list ───────────────────────────────────────────────────


async def test_the_rest_list_reports_the_item_count_not_the_iteration_sum(tmp_path: Path) -> None:
    """F1. Six rows previously claimed to be items 1–3 **of 6**; there are three items."""
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        rows = _rows(run_id)

    for iteration in (0, 1):
        items = [rows[f"root.body@{iteration}.children[1].body#{i}"] for i in range(3)]
        assert [r["item_index"] for r in items] == [0, 1, 2], items
        assert [r["item_total"] for r in items] == [3, 3, 3], (
            f"iteration {iteration} reports {[r['item_total'] for r in items]} for a three-item "
            "fan-out"
        )
        assert [r["item_label"] for r in items] == ["a", "b", "c"]
        # What a reader actually sees, spelled out: the marker `workflowMeta.itemProgress` renders.
        assert [f"[{r['item_index'] + 1}/{r['item_total']}]" for r in items] == [
            "[1/3]",
            "[2/3]",
            "[3/3]",
        ]


async def test_a_loop_iteration_still_carries_its_own_coordinate(tmp_path: Path) -> None:
    """The quantity that must still be COUNTED, and must not be collapsed into the fan-out's.

    A loop's total is unknowable until it ends (`until_dry` has no count), so an iteration's
    coordinate is the size of its expansion group. Keying that by `sibling_group` keeps it separate
    from a nested fan-out's items — the regression risk of fixing F1 by widening the key instead of
    narrowing it.
    """
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _LOOP_IN_FANOUT)
    with _isolated(home):
        rows = _rows(run_id)

    for item in range(2):
        iterations = [rows[f"root.body#{item}.body@{n}"] for n in range(3)]
        assert [r["item_index"] for r in iterations] == [0, 1, 2], iterations
        assert [r["item_total"] for r in iterations] == [3, 3, 3], (
            f"item {item}'s three loop iterations report "
            f"{[r['item_total'] for r in iterations]}"
        )


async def test_a_fanout_wrapping_a_loop_still_counts_its_own_items(tmp_path: Path) -> None:
    """The other half of the same fixture: the fan-out's own rows.

    A fan-out whose body is a CONTAINER has no instance row per item (the container body carries no
    state of its own), so the items are observed through the loop instances one level down — which
    is exactly why the denominator has to be attributable rather than counted from whatever shares
    a spec path.
    """
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _LOOP_IN_FANOUT)
    with _isolated(home):
        instances = sorted(store.read_state(run_id))

    per_item = {p.split(".body@")[0] for p in instances if ".body#" in p and ".body@" in p}
    assert per_item == {"root.body#0", "root.body#1"}, sorted(per_item)


# ── F2: the live event stream ────────────────────────────────────────────────


async def test_every_streamed_event_of_a_twelve_item_fanout_reports_twelve(tmp_path: Path) -> None:
    """F2, at full size. The stream must read `[1/12] … [12/12]`.

    The broken stream was `""`, `[2/2]`, `[3/3]` … `[12/12]`: the FIRST item carried no
    `item_total` at all (one instance in the map, so the `> 1` gate suppressed it) and every later
    one carried its own index plus one. The assertion is on all twelve, in order, because the LAST
    row of the broken stream was already `[12/12]` — the one value a single-row assertion would
    have certified as correct.
    """
    home = tmp_path / "home"
    _, published = await _drive(home, _TWELVE)

    starts = _started(published, "one")
    assert len(starts) == 12, f"expected twelve dispatches, got {len(starts)}"
    by_index = {int(p["item_index"]): p for p in starts}
    assert sorted(by_index) == list(range(12)), sorted(by_index)
    assert [by_index[i].get("item_total") for i in range(12)] == [12] * 12, [
        by_index[i].get("item_total") for i in range(12)
    ]
    rendered = [f"[{i + 1}/{by_index[i]['item_total']}]" for i in range(12)]
    assert rendered[0] == "[1/12]" and rendered[-1] == "[12/12]", rendered
    assert len(set(by_index[i]["item_total"] for i in range(12))) == 1, "the denominator moved"


async def test_the_event_and_the_rest_list_report_the_same_denominator(tmp_path: Path) -> None:
    """The whole point of one denominator: the two surfaces cannot disagree.

    `web/src/pages/workflows/workflowFold.ts` folds the event over the snapshot with
    `env.item_total ?? existing`, so the event WINS. Two independent derivations therefore meant the
    number a user saw depended on whether the page was open when the node started.
    """
    home = tmp_path / "home"
    run_id, published = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        rows = _rows(run_id)

    starts = _started(published, "one")
    assert len(starts) == 6, f"expected 3 items × 2 iterations dispatched, got {len(starts)}"
    for payload in starts:
        row = rows[str(payload["instance_path"])]
        assert payload["item_total"] == row["item_total"] == 3, (payload, row)
        assert payload["item_index"] == row["item_index"], (payload, row)


async def test_the_denominator_survives_a_reload(tmp_path: Path) -> None:
    """It is read off the persisted instance, so a page load after a restart shows the same marker.

    The count it replaces was recomputed on every read, which is what made it free to be wrong
    differently in each surface; a stamped value has to actually round-trip through the store.
    """
    home = tmp_path / "home"
    run_id, _ = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        instances = store.read_state(run_id)
        assert instances["root.body@1.children[1].body#2"].item_total == 3
        rows = _rows(run_id)
    assert rows["root.body@1.children[1].body#2"]["item_total"] == 3


async def test_a_non_iterated_node_still_carries_no_item_fields(tmp_path: Path) -> None:
    """An `item_index` on a lone node would render "[1/1]", which is noise. Unchanged."""
    home = tmp_path / "home"
    run_id, published = await _drive(home, _FANOUT_IN_LOOP)
    with _isolated(home):
        rows = _rows(run_id)

    sibling = rows["root.body@0.children[0]"]
    assert sibling["node_id"] == "work"
    assert "item_index" not in sibling and "item_total" not in sibling, sibling
    for payload in _started(published, "work"):
        assert "item_total" not in payload and "item_index" not in payload, payload
