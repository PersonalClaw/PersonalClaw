"""Cache-origin reaches the run's own node list, not only the SSE stream (issue 2769 / WV-10).

`WF2-A1` emits `cached` on `workflow_node_done` for one stated reason (`workflows/journal.py`):
*"did my edit actually re-run anything?" is the first question a user asks after a mid-flight
edit, and the answer has to come from the ledger, not from reading logs.* The flag rode the live
event stream and nothing else — so the answer existed for as long as the tab stayed open and
vanished on reload, because `service.status()`'s node rows never carried it.

The projection lives on `NodeInstance` rather than being re-derived from the ledger per request:
`status()` is a hot pure read (the run view refetches on every coalesced lifecycle event), and
scanning a whole run's `events.jsonl` to answer "was this row cached?" would put a file read per
node list on that path.

Two things that make the projection honest, and both are asserted below:

* **it is CLEARED by a re-run.** Stamped at exactly the two points in `_launch` that decide a
  node's outcome-origin — True on a cache hit, False on a fresh dispatch — so a resume that
  genuinely re-executes a node cannot leave the previous epoch's answer behind. Doing it at the
  six rewind reset sites instead would be six chances to miss one.
* **it is reported only for a TERMINAL instance.** Between a rewind and the re-dispatch an
  instance is PENDING while still holding the previous epoch's stamp; reporting it then would
  mark a row cached before it has run.

🪤 A REWIND DOES NOT PRODUCE A CACHE HIT on the rewound node, and a test that expects one is
measuring the wrong mechanism (`test_workflows_lifecycle_e2e.py` records that mistake being made
and corrected). A rewind bumps the node's epoch and the epoch is part of the cache key, so the
rewound region correctly MISSES. The real hit is the crash-resume shape used here: instances back
to PENDING at the SAME epoch, with the journal's completed records still keyed to it.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


SPEC = {
    "name": "cachevis",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "infer", "id": "n1", "config": {"prompt": "first"}},
            {"kind": "infer", "id": "n2", "config": {"prompt": "second"}},
        ],
    },
}


def _echo():
    calls: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None):
        calls.append(prompt)
        return f"out{len(calls)}"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


async def _run(spec: dict, fn, run: WorkflowRun | None = None) -> WorkflowRun:
    """One pass of the crash-resume shape: everything re-scheduled at its current epoch."""
    run = run or store.create(WorkflowRun(id="", workflow_name=str(spec["name"])))
    store.write_spec(run.id, spec)
    run.status = RunStatus.RUNNING
    c = RunController(run, spec, services=EngineServices(completion=fn))
    for inst in c.instances.values():
        inst.state = InstanceState.PENDING
    await c.run_to_completion(timeout=20)
    return run


def _rows(run_id: str) -> dict[str, dict]:
    return {r["instance_path"]: r for r in service.status(run_id)["nodes"]}


def _leaf(run_id: str, node_id: str) -> dict:
    """One leaf's row, keyed by the node id the spec declares rather than by its instance path —
    the path spelling is the projection's business, and hard-coding it makes this file break on a
    change it is not testing."""
    rows = [r for r in service.status(run_id)["nodes"] if r["node_id"] == node_id]
    assert len(rows) == 1, f"expected exactly one {node_id!r} row, got {rows}"
    return rows[0]


class TestCacheOriginInTheNodeList:
    async def test_a_first_run_marks_nothing(self) -> None:
        """The vacuity floor for every assertion below. If a fresh run reported `cached`, the
        flag would be a constant rather than an answer."""
        fn = _echo()
        run = await _run(SPEC, fn)
        assert len(fn.calls) == 2
        rows = _rows(run.id)
        assert rows, "the status read returned no nodes"
        for path, row in rows.items():
            assert "cached" not in row, f"{path} claims a cache hit on its first execution: {row}"

    async def test_an_unchanged_resume_marks_every_re_dispatched_node(self) -> None:
        """The scenario the flag exists for, read from the node list a page load renders."""
        fn = _echo()
        run = await _run(SPEC, _echo())
        run = await _run(SPEC, fn, store.get(run.id))
        # Cross-check against the ledger, which is the durable record the projection mirrors —
        # if these two disagree the projection is lying, not merely stale.
        assert len(fn.calls) == 0, "a cached node re-ran, so this is not the cached case"
        assert len([r for r in J.ledger(run.id) if r["kind"] == J.STEP_CACHED]) == 2

        leaves = {p: r for p, r in _rows(run.id).items() if r["node_id"] in {"n1", "n2"}}
        assert len(leaves) == 2, leaves
        for path, row in leaves.items():
            assert row.get("cached") is True, f"{path} was served from cache and does not say so"

    async def test_a_RE_RUN_clears_it_rather_than_carrying_it_forward(self) -> None:
        """The false fix: a flag that only ever goes True. Editing `n2`'s prompt puts it back in
        the binding closure, so it genuinely re-executes on the next pass — and a row that still
        said `cached` would be a wrong answer to the one question the flag answers, wearing the
        badge that makes it look like a right one."""
        fn = _echo()
        run = await _run(SPEC, fn)
        run = await _run(SPEC, fn, store.get(run.id))
        assert _leaf(run.id, "n2").get("cached") is True  # vacuity floor

        edited = {
            "name": "cachevis",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "n1", "config": {"prompt": "first"}},
                    {"kind": "infer", "id": "n2", "config": {"prompt": "second EDITED"}},
                ],
            },
        }
        before = len(fn.calls)
        run = await _run(edited, fn, store.get(run.id))
        assert len(fn.calls) - before == 1, "n2 did not actually re-run"

        n2 = _leaf(run.id, "n2")
        assert "cached" not in n2, f"a re-run node still claims cache: {n2}"
        # …and its untouched sibling is still outside the closure, so it IS still cached. Without
        # this the test would also pass on a fix that simply stopped reporting the flag.
        assert _leaf(run.id, "n1").get("cached") is True

    async def test_a_NON_TERMINAL_instance_reports_nothing(self) -> None:
        """The rewind-then-pause window: the stamp is the previous epoch's answer and the node has
        not run yet, so the row must say nothing rather than mark work that has not happened."""
        run = store.create(WorkflowRun(id="", workflow_name="cachevis"))
        store.write_spec(run.id, SPEC)
        store.write_state(
            run.id,
            {
                "s.children[0]": NodeInstance(
                    path="s.children[0]", state=InstanceState.DONE, cached=True
                ),
                "s.children[1]": NodeInstance(
                    path="s.children[1]", state=InstanceState.PENDING, cached=True
                ),
            },
        )
        rows = _rows(run.id)
        assert rows["s.children[0]"].get("cached") is True  # vacuity floor
        assert "cached" not in rows["s.children[1]"], rows["s.children[1]"]


class TestTheStampSurvivesAReload:
    def test_it_round_trips_through_the_persisted_instance(self) -> None:
        """It is PERSISTED, like `item_label` and for the same reason: a gateway restart that
        re-adopts a finished run must still be able to answer where its output came from."""
        inst = NodeInstance(path="s.children[0]", state=InstanceState.DONE, cached=True)
        assert NodeInstance.from_dict(inst.to_dict()).cached is True
        assert NodeInstance.from_dict(NodeInstance(path="p").to_dict()).cached is False

    def test_a_state_file_written_before_the_field_existed_reads_as_fresh(self) -> None:
        """The honest default for a run that predates the field: nothing claimed a cache hit, so
        nothing gets a badge."""
        legacy = NodeInstance(path="p", state=InstanceState.DONE).to_dict()
        legacy.pop("cached")
        assert NodeInstance.from_dict(legacy).cached is False
