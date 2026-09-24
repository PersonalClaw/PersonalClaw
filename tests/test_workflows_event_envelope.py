"""The published-event envelope (WF2-R11) — what makes a live widget foldable.

Every SSE event the engine publishes carries three identity fields, stamped at the ONE
publish seam rather than at each of the twelve call sites — a call site that forgot one
would emit an event the FE cannot dedup or supersede, and that is invisible until a rewind
duplicates a row in someone's widget.

The load-bearing claims:

* **`event_id` is deterministic and unique per run**, so a re-emit after a reconnect is an
  idempotent no-op rather than a second row;
* **`seq` is monotonic**, so a consumer can detect a gap instead of folding backwards;
* **`epoch` is the RUN's epoch** (the max across instances), not any one node's — using a
  node's would let an untouched node's stale value mark a fresh event as superseded;
* a node event additionally carries `node_epoch`, the finer key a per-node supersede needs;
* the sequence is independent of the journal's, or a consumer's gap detection would fire on
  every unpublished journal write.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import NodeInstance, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


SPEC = {
    "name": "env",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}},
            {"kind": "transform", "id": "b", "config": {"expr": "got {{nodes.a.output.n}}"}},
        ],
    },
}


def _collector() -> tuple[list[tuple[str, dict]], object]:
    events: list[tuple[str, dict]] = []

    def publish(event: str, payload: dict) -> None:
        events.append((event, payload))

    return events, publish


async def _run(spec: dict = SPEC):
    events, publish = _collector()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices(publish=publish))
    status = await c.run_to_completion(timeout=20)
    return c, events, status


class TestEnvelope:
    async def test_every_event_carries_the_identity_triple(self) -> None:
        _c, events, status = await _run()
        assert status == RunStatus.COMPLETE
        assert events, "expected published events"
        for name, payload in events:
            assert payload.get("run_id"), name
            assert payload.get("event_id"), name
            assert isinstance(payload.get("seq"), int), name
            assert isinstance(payload.get("epoch"), int), name

    async def test_event_ids_are_deterministic_and_unique(self) -> None:
        """Deterministic so a re-emit is an idempotent no-op; unique so two events can never
        collapse into one in a consumer's dedup set."""
        c, events, _status = await _run()
        ids = [p["event_id"] for _n, p in events]
        assert len(set(ids)) == len(ids)
        assert all(i.startswith(f"{c.run.id}-evt-") for i in ids)

    async def test_seq_is_monotonic_from_one(self) -> None:
        _c, events, _status = await _run()
        seqs = [p["seq"] for _n, p in events]
        assert seqs == list(range(1, len(seqs) + 1))

    async def test_the_event_sequence_is_its_own_counter(self) -> None:
        """Separate from the journal's. Conflating them would make a consumer's gap detection
        fire on every journal write that was never published — the two count different things
        and only coincidentally agree on a short run."""
        c, events, _status = await _run()
        published = [p["seq"] for _n, p in events]
        # The publish counter starts at 1 and is dense — that is the property a consumer
        # relies on. The journal's counter is a DIFFERENT sequence over persisted records.
        assert published == list(range(1, len(published) + 1))
        assert c._event_seq == len(published)
        assert c.journal.seq >= 1  # both advance; neither drives the other

    async def test_the_epoch_is_the_runs_max_not_a_single_nodes(self) -> None:
        """A rewind bumps only the region it resets. Using one node's epoch as the run's
        would let an untouched node's stale value mark a fresh event as superseded."""
        events, publish = _collector()
        run = store.create(WorkflowRun(id="", workflow_name="env"))
        store.write_spec(run.id, SPEC)
        # Node `a` was rewound to epoch 3; `b` is untouched at 0.
        store.write_state(
            run.id,
            {
                "root.children[0]": NodeInstance(path="root.children[0]", epoch=3),
                "root.children[1]": NodeInstance(path="root.children[1]", epoch=0),
            },
        )
        c = RunController(run, SPEC, services=EngineServices(publish=publish))
        await c.run_to_completion(timeout=20)
        # 3, never 0 — `b` sitting at epoch 0 must not drag the run's epoch down, or a fresh
        # event would look superseded to the FE. (The very first event can precede the state
        # read, so the assertion is on the events that carry node identity.)
        node_events = [p for n, p in events if n.startswith("workflow_node_")]
        assert node_events
        assert all(p["epoch"] == 3 for p in node_events), "run epoch must be the MAX, not a node's"

    async def test_a_node_event_carries_its_own_epoch_too(self) -> None:
        """The finer key: a per-node supersede needs the NODE's epoch, which can lag the
        run's after a partial rewind."""
        _c, events, _status = await _run()
        done = [p for n, p in events if n == "workflow_node_done"]
        assert done, "expected node_done events"
        assert all("node_epoch" in p for p in done)

    async def test_no_call_site_publishes_a_bare_epoch_key(self) -> None:
        """Regression, found by this suite. `workflow_node_started` passed the NODE's epoch as
        `epoch`, overriding the envelope's RUN epoch — so `node_started` and `node_done` for
        the same node reported different run epochs, and a consumer folding the lower one
        would treat the next real event as superseded and silently stop updating.

        A node's epoch belongs under `node_epoch`. Asserted structurally because the payload
        dicts are spread into the envelope, so a re-introduction would be invisible."""
        import inspect

        from personalclaw.workflows import controller as ctrl

        source = inspect.getsource(ctrl.RunController)
        # `_publish` itself sets the envelope epoch; no OTHER line may pass one in a payload.
        offenders = [
            line.strip()
            for line in source.splitlines()
            if '"epoch":' in line and "self._run_epoch()" not in line
        ]
        assert not offenders, f"payload sets a bare epoch, overriding the envelope: {offenders}"

    async def test_a_payload_field_is_never_clobbered_by_the_envelope(self) -> None:
        """The envelope is a floor, not an override — a call site that sets a field wins.

        Deliberate: a future event that genuinely needs to report a different epoch can, and
        the structural test above is what keeps that from happening by accident."""
        events, publish = _collector()
        run = store.create(WorkflowRun(id="", workflow_name="env"))
        store.write_spec(run.id, SPEC)
        c = RunController(run, SPEC, services=EngineServices(publish=publish))
        c._publish("custom_event", {"epoch": 99, "extra": "kept"})
        _name, payload = events[-1]
        assert payload["epoch"] == 99 and payload["extra"] == "kept"

    async def test_a_broken_observer_never_kills_the_run(self) -> None:
        """A widget's publish path is not allowed to take the engine down with it."""

        def exploding(event: str, payload: dict) -> None:
            raise RuntimeError("observer down")

        run = store.create(WorkflowRun(id="", workflow_name="env"))
        store.write_spec(run.id, SPEC)
        c = RunController(run, SPEC, services=EngineServices(publish=exploding))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

    async def test_no_publisher_is_a_silent_no_op(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="env"))
        store.write_spec(run.id, SPEC)
        c = RunController(run, SPEC, services=EngineServices(publish=None))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE


class TestNodeKeyedPatches:
    async def test_node_events_identify_one_instance_each(self) -> None:
        """Node-keyed, never a whole-list rebroadcast: that is what lets two concurrent
        completions land without clobbering each other."""
        _c, events, _status = await _run()
        node_events = [p for n, p in events if n.startswith("workflow_node_")]
        assert node_events
        for payload in node_events:
            assert payload.get("instance_path"), payload
            # A full nodes_summary[] would defeat the point.
            assert "nodes" not in payload

    async def test_a_fan_out_publishes_one_event_per_instance(self) -> None:
        spec = {
            "name": "fan",
            "root": {
                "kind": "foreach",
                "id": "loop",
                "config": {"items": [1, 2, 3]},
                "body": {"kind": "transform", "id": "item", "config": {"expr": "{{item}}"}},
            },
        }
        _c, events, status = await _run(spec)
        assert status == RunStatus.COMPLETE
        paths = {p["instance_path"] for n, p in events if n == "workflow_node_done"}
        # One distinct instance path per item — not one event carrying all three.
        assert len(paths) >= 3
