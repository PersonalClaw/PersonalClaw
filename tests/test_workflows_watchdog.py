"""The watchdog — crash recovery, orphan reaping, and retention.

Everything here is about what happens ACROSS process lifetimes, which is the part a
controller cannot cover: after a restart no run has a controller, so a run left in
RUNNING sits there forever and a user reads that as "still working" while nothing is.

Retention gets the strictest tests in this file because it is the only destructive path
in the slice. `run_id` reaches the sweep from a stored row, and a row is not a trust
boundary — a `..`-shaped id must delete nothing.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    NodeInstance,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.watchdog import (
    WorkflowWatchdog,
    _sweep_run_dir,
    prune_runs,
    registry_key,
)

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
    "name": "wd",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "a", "config": {"expr": "1"}},
            {"kind": "transform", "id": "b", "config": {"expr": "2"}},
        ],
    },
}


def _run(status=RunStatus.RUNNING, spec=SPEC, **kw) -> WorkflowRun:
    run = store.create(
        WorkflowRun(id="", workflow_name=spec.get("name", "wd"), status=status, **kw)
    )
    store.write_spec(run.id, spec)
    store.save(run)
    return run


class TestRegistryKey:
    def test_the_key_mirrors_the_loop_convention(self) -> None:
        assert registry_key("abc123") == "workflow:abc123"


class TestAdoption:
    async def test_a_running_run_with_no_controller_is_adopted(self) -> None:
        """After a restart every live run is in exactly this state."""
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert wd.controller(run.id) is not None
        await wd.stop()

    async def test_an_adopted_run_is_driven_to_completion(self) -> None:
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        controller = wd.controller(run.id)
        assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE
        await wd.stop()

    async def test_a_run_is_never_adopted_twice(self) -> None:
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        first = wd.controller(run.id)
        await wd._poll_once()
        assert wd.controller(run.id) is first
        await wd.stop()

    async def test_an_externally_registered_controller_is_not_re_adopted(self) -> None:
        """A chat tool that starts a run owns its controller; the watchdog must not build
        a second one and put two writers on the journal."""
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        mine = RunController(run, SPEC, services=EngineServices())
        wd.register(mine)
        await wd._poll_once()
        assert wd.controller(run.id) is mine
        await wd.stop()

    async def test_a_run_with_an_unreadable_spec_fails_loudly(self) -> None:
        run = _run()
        (store.run_dir(run.id) / "spec.json").unlink()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        saved = store.get(run.id)
        assert saved.status == RunStatus.FAILED
        assert "spec" in saved.error_message
        await wd.stop()

    async def test_a_finished_controller_is_dropped_from_the_registry(self) -> None:
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        await wd.controller(run.id).run_to_completion(timeout=20)
        await wd._poll_once()
        assert wd.controller(run.id) is None
        await wd.stop()


class TestOrphanReaping:
    async def test_a_run_whose_work_finished_but_status_did_not_is_reaped(self) -> None:
        """The process died between the last node and the terminal write."""
        run = _run()
        store.write_state(
            run.id,
            {
                "root.children[0]": NodeInstance("root.children[0]", InstanceState.DONE),
                "root.children[1]": NodeInstance("root.children[1]", InstanceState.DONE),
            },
        )
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert store.get(run.id).status == RunStatus.COMPLETE
        assert wd.controller(run.id) is None  # reaped, not adopted
        await wd.stop()

    async def test_a_failed_terminal_state_reaps_to_failed(self) -> None:
        run = _run()
        store.write_state(
            run.id,
            {
                "root.children[0]": NodeInstance("root.children[0]", InstanceState.DONE),
                "root.children[1]": NodeInstance("root.children[1]", InstanceState.FAILED),
            },
        )
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert store.get(run.id).status == RunStatus.FAILED
        await wd.stop()

    async def test_a_partially_complete_run_is_adopted_not_reaped(self) -> None:
        """Reaping mid-flight work would discard the remaining nodes."""
        run = _run()
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance("root.children[0]", InstanceState.DONE)},
        )
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert wd.controller(run.id) is not None
        await wd.stop()

    async def test_an_adopted_run_resumes_without_re_running_finished_work(self) -> None:
        """The whole point of adoption: a restart must not redo completed nodes, and an
        earlier node's output has to survive into a later node's bindings."""
        spec = {
            "name": "wd",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "FIRST"}},
                    {"kind": "wait", "id": "pause", "config": {"duration_secs": 1}},
                    {
                        "kind": "transform",
                        "id": "b",
                        "config": {"expr": "saw {{nodes.a.output}}"},
                    },
                ],
            },
        }
        run = _run(spec=spec)
        first = RunController(run, spec, services=EngineServices())
        await first.start()
        for _ in range(40):
            import asyncio as _a

            await _a.sleep(0.1)
            inst = first.instances.get("root.children[1]")
            if inst and inst.state.value == "waiting":
                break
        await first.stop()
        assert first.instances["root.children[0]"].state == InstanceState.DONE

        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        adopted = wd.controller(run.id)
        assert adopted is not None, "the watchdog abandoned a live run"
        assert await adopted.run_to_completion(timeout=30) == RunStatus.COMPLETE
        assert adopted._outputs["b"] == "saw FIRST"
        await wd.stop()

    async def test_a_run_with_no_state_at_all_is_adopted(self) -> None:
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert wd.controller(run.id) is not None
        await wd.stop()


class TestStickyCancel:
    async def test_a_cancel_with_no_controller_is_honoured(self) -> None:
        """A cancel issued while the gateway was down must not be lost."""
        run = _run()
        store.request_cancel(run.id)
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        assert store.get(run.id).status == RunStatus.CANCELLED
        assert not store.cancel_requested(run.id)  # intent consumed
        await wd.stop()

    async def test_a_live_controller_cancels_itself(self) -> None:
        """Two writers on one run is the failure mode; the controller owns its own
        terminal write."""
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        mine = RunController(run, SPEC, services=EngineServices())
        wd.register(mine)
        store.request_cancel(run.id)
        await wd._poll_once()
        # The watchdog deferred rather than writing the status itself.
        assert store.cancel_requested(run.id)
        await wd.stop()


class TestPublisher:
    async def test_events_reach_the_per_run_sse_key(self) -> None:
        published: list[tuple[str, str, dict]] = []

        class _Registry:
            def publish(self, key, event, data):
                published.append((key, event, data))

        class _State:
            def workflow_sse(self):
                return _Registry()

        run = _run()
        wd = WorkflowWatchdog(_State(), EngineServices())
        controller = await wd.launch(run, SPEC)
        await controller.run_to_completion(timeout=20)
        assert published
        assert all(k == f"workflow:{run.id}" for k, _, _ in published)
        assert "workflow_node_done" in {e for _, e, _ in published}
        await wd.stop()

    async def test_a_broken_sse_registry_cannot_kill_a_run(self) -> None:
        class _State:
            def workflow_sse(self):
                raise RuntimeError("sse is down")

        run = _run()
        wd = WorkflowWatchdog(_State(), EngineServices())
        controller = await wd.launch(run, SPEC)
        assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE
        await wd.stop()

    async def test_a_run_update_also_signals_the_ws(self) -> None:
        """WS envelopes are refetch SIGNALS, not payloads (the DashboardLive convention)."""
        signals: list[dict] = []

        class _State:
            def workflow_sse(self):
                class R:
                    def publish(self, *a):
                        pass

                return R()

            def _broadcast(self, msg):
                signals.append(msg)

        run = _run()
        wd = WorkflowWatchdog(_State(), EngineServices())
        controller = await wd.launch(run, SPEC)
        await controller.run_to_completion(timeout=20)
        assert signals
        assert all(s["type"] == "workflow_run_update" for s in signals)
        assert all(set(s) == {"type", "run_id"} for s in signals), "payload, not a signal"
        await wd.stop()


class TestRetention:
    def test_old_terminal_runs_are_pruned_oldest_first(self) -> None:
        for i in range(5):
            r = store.create(
                WorkflowRun(
                    id="",
                    workflow_name="keeper",
                    status=RunStatus.COMPLETE,
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                )
            )
            store.save(r)
        assert prune_runs("keeper", keep=2) == 3
        remaining, total = store.list_runs(workflow_name="keeper")
        assert total == 2
        # Newest survive.
        assert {r.created_at for r in remaining} == {"2026-01-05T00:00:00Z", "2026-01-04T00:00:00Z"}

    def test_a_pinned_run_is_never_pruned(self) -> None:
        for i in range(4):
            r = store.create(
                WorkflowRun(
                    id="",
                    workflow_name="pin",
                    status=RunStatus.COMPLETE,
                    pinned=(i == 0),
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                )
            )
            store.save(r)
        prune_runs("pin", keep=1)
        remaining, _ = store.list_runs(workflow_name="pin")
        assert any(r.pinned for r in remaining)

    def test_a_still_running_run_is_never_pruned(self) -> None:
        for i in range(4):
            r = store.create(
                WorkflowRun(
                    id="",
                    workflow_name="live",
                    status=RunStatus.RUNNING if i == 0 else RunStatus.COMPLETE,
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                )
            )
            store.save(r)
        prune_runs("live", keep=1)
        remaining, _ = store.list_runs(workflow_name="live")
        assert any(r.status == RunStatus.RUNNING for r in remaining)

    def test_pruning_under_the_cap_is_a_no_op(self) -> None:
        r = store.create(WorkflowRun(id="", workflow_name="few", status=RunStatus.COMPLETE))
        store.save(r)
        assert prune_runs("few", keep=10) == 0

    def test_the_directory_goes_before_the_row(self) -> None:
        """Deleting the row first would orphan megabytes of journal with nothing left to
        find it by."""
        for i in range(3):
            r = store.create(
                WorkflowRun(
                    id="",
                    workflow_name="dirs",
                    status=RunStatus.COMPLETE,
                    created_at=f"2026-01-0{i + 1}T00:00:00Z",
                )
            )
            store.save(r)
            store.write_spec(r.id, SPEC)
        dirs_before = {p.name for p in store.runs_root().iterdir() if p.is_dir()}
        prune_runs("dirs", keep=1)
        dirs_after = {p.name for p in store.runs_root().iterdir() if p.is_dir()}
        rows, _ = store.list_runs(workflow_name="dirs")
        assert len(dirs_after) == 1
        assert {r.id for r in rows} == dirs_after
        assert len(dirs_before) == 3


class TestSweepContainment:
    """The only destructive path in the slice. A stored row is not a trust boundary."""

    def test_a_traversal_id_deletes_nothing(self, tmp_path) -> None:
        sentinel = store.runs_root().parent / "MUST_SURVIVE.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("do not delete me")
        for bad in ("..", "../..", "../MUST_SURVIVE.txt", "/etc", "../../../"):
            assert _sweep_run_dir(bad) is False, bad
        assert sentinel.exists()
        assert sentinel.read_text() == "do not delete me"

    def test_the_runs_root_itself_is_refused(self) -> None:
        store.runs_root().mkdir(parents=True, exist_ok=True)
        assert _sweep_run_dir(".") is False
        assert store.runs_root().exists()

    def test_a_legitimate_run_dir_is_swept(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="ok"))
        store.write_spec(run.id, SPEC)
        assert store.run_dir(run.id).exists()
        assert _sweep_run_dir(run.id) is True
        assert not store.run_dir(run.id).exists()

    def test_an_absent_dir_is_reported_as_swept(self) -> None:
        """Idempotence: a retried prune after a partial failure must not stall."""
        assert _sweep_run_dir("never-existed") is True


class TestLifecycle:
    async def test_start_and_stop_are_idempotent(self) -> None:
        wd = WorkflowWatchdog(None, EngineServices())
        wd.start()
        wd.start()
        await wd.stop()
        await wd.stop()

    async def test_stop_halts_every_adopted_controller(self) -> None:
        run = _run()
        wd = WorkflowWatchdog(None, EngineServices())
        await wd._poll_once()
        await wd.stop()
        assert wd.controller(run.id) is None
