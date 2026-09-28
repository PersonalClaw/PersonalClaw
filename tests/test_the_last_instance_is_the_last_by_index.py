"""A node's LAST instance is the last by index, not by characters.

`output()` and `inspect_node()` answer for a node's last instance, because a loop or `foreach`
body produces many and the first would stand in for the whole run. They picked it with a plain
`sorted()`, which orders instance paths by characters: `body@10` sorts before `body@2`, so from
the eleventh iteration or item on they answered with iteration 9, and the node inspector showed a
run's ninth cycle as its last. `_nodes_of`, the run's node list, sorted the same way. The engine
already ordered paths by value (the bindings' window), and so did the run view; these now use the
same order, `models.instance_order`.

Real runs, driven through `RunController`, so the paths are the ones the engine produces.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

#: Eleven iterations: the eleventh is the first whose index has two digits, where a character
#: sort and a numeric one first disagree about which is last.
_LOOP: dict[str, Any] = {
    "name": "eleven-cycles",
    "root": {
        "kind": "loop",
        "id": "project",
        "config": {"mode": "counted", "n": 11},
        "body": {"kind": "transform", "id": "work", "config": {"expr": "cycle {{iter}}"}},
    },
}

#: Eleven items in one fan-out, the same boundary on the other index form.
_FANOUT: dict[str, Any] = {
    "name": "eleven-items",
    "root": {
        "kind": "foreach",
        "id": "review",
        "config": {"items": [f"item {i}" for i in range(11)]},
        "body": {"kind": "transform", "id": "one", "config": {"expr": "saw {{item}}"}},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at a tmp home and put it back (nothing may touch the real home)."""
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _drive(home: Path, spec: dict[str, Any]) -> str:
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name=str(spec["name"])))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        status = await controller.run_to_completion(timeout=60)
        assert status is RunStatus.COMPLETE, f"the fixture run did not finish: {status}"
        return run.id


@pytest.mark.parametrize(
    ("spec", "node_id", "last", "said"),
    [
        (_LOOP, "work", "root.body@10", "cycle 10"),
        (_FANOUT, "one", "root.body#10", "saw item 10"),
    ],
)
async def test_output_and_inspect_answer_for_the_last_instance_by_index(
    tmp_path: Path, spec: dict[str, Any], node_id: str, last: str, said: str
) -> None:
    home = tmp_path / "home"
    run_id = await _drive(home, spec)
    with _isolated(home):
        paths = [p for p in store.read_state(run_id) if p != "root"]
        answered = service.output(run_id, node_id)
        inspected = service.inspect_node(run_id, node_id)

    # The premise: eleven instances, and a character sort names another one last.
    assert len(paths) == 11 and sorted(paths)[-1] != last, sorted(paths)
    assert answered["instance_path"] == last and answered["output"] == said, answered
    assert inspected.get("ok") is True and inspected["instance_path"] == last, inspected


async def test_the_node_list_reads_in_index_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    run_id = await _drive(home, _LOOP)
    with _isolated(home):
        listed = [str(row["instance_path"]) for row in service._nodes_of(run_id)]

    assert listed == ["root", *(f"root.body@{i}" for i in range(11))]
