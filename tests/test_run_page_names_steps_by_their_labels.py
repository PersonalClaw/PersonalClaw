"""The run page names each step the way the run's own sentences do: by its label.

A step's failure line and the run's ending name a step by its author's label ("“Check the draft”
failed: the draft is empty, so nothing after it ran" — `ending_sentence._step`), while
`GET /api/workflows/runs/{id}` carried only each node's id, so the page's rows, graph, dialogs and
escalation panel said `check` beside a sentence about “Check the draft”. Each node row now carries
the label its node declares, and the page reads it.
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

_SPEC: dict[str, Any] = {
    "name": "labelled-steps",
    "root": {
        "kind": "sequence",
        "id": "all",
        "children": [
            {
                "kind": "transform",
                "id": "gather",
                "label": "Gather the sources",
                "config": {"expr": "sources"},
            },
            {"kind": "transform", "id": "plain", "config": {"expr": "no label here"}},
            {
                "kind": "foreach",
                "id": "review",
                "label": "Review each source",
                "config": {"items": ["a", "b"]},
                "body": {
                    "kind": "transform",
                    "id": "one",
                    "label": "Read one source",
                    "config": {"expr": "seen {{item}}"},
                },
            },
            {
                "kind": "gate",
                "id": "check",
                "label": "Check the draft",
                "config": {"kind": "expression", "expr": "1 == 2", "message": "the draft is empty"},
            },
        ],
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _ran(home: Path) -> dict[str, Any]:
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name=str(_SPEC["name"])))
        store.write_spec(run.id, _SPEC)
        status = await RunController(run, _SPEC, services=EngineServices()).run_to_completion(
            timeout=60
        )
        assert status is RunStatus.FAILED, f"the fixture's check must fail the run: {status}"
        result = service.status(run.id)
    assert result.get("ok"), result
    return result


def _by_id(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in result["nodes"]:
        rows.setdefault(str(row["node_id"]), []).append(row)
    return rows


async def test_each_row_carries_the_label_its_step_declares(tmp_path: Path) -> None:
    """🔴 Red on main: no row carried a label, so the page could only show ids."""
    rows = _by_id(await _ran(tmp_path))
    assert [r.get("label") for r in rows["gather"]] == ["Gather the sources"]
    # Every item of a fan-out is its body's step, named as that step.
    assert [r.get("label") for r in rows["one"]] == ["Read one source", "Read one source"]
    assert [r.get("label") for r in rows["check"]] == ["Check the draft"]


async def test_a_step_with_no_label_carries_none(tmp_path: Path) -> None:
    """A step without one has nothing better than its id, which the page shows."""
    rows = _by_id(await _ran(tmp_path))
    assert "label" not in rows["plain"][0]


async def test_the_live_stream_names_a_started_step_as_its_row_does(tmp_path: Path) -> None:
    """🔴 Red before: `workflow_node_started` carried no label, so a view folding the stream (the
    chat's progress card) could name a step the snapshot had not listed only by its id. It now
    carries the same label the row does, and nothing for a step without one."""
    published: list[tuple[str, dict[str, Any]]] = []
    with _isolated(tmp_path):
        run = store.create(WorkflowRun(id="", workflow_name=str(_SPEC["name"])))
        store.write_spec(run.id, _SPEC)
        services = EngineServices(publish=lambda event, payload: published.append((event, payload)))
        await RunController(run, _SPEC, services=services).run_to_completion(timeout=60)
    started: dict[str, list[Any]] = {}
    for event, payload in published:
        if event == "workflow_node_started":
            started.setdefault(str(payload["node_id"]), []).append(payload.get("label"))
    assert started["gather"] == ["Gather the sources"], started
    assert started["one"] == ["Read one source", "Read one source"], started
    assert started["plain"] == [None], "a step with no label is named by nothing more than its id"


async def test_the_row_names_the_step_the_way_the_runs_ending_does(tmp_path: Path) -> None:
    """🔴 Red on main. The run's ending quotes the failed step by its label; the row the page shows
    beside that sentence must carry the same name."""
    result = await _ran(tmp_path)
    (check,) = _by_id(result)["check"]
    assert f"“{check.get('label')}” failed" in result["error"], (result["error"], check)
