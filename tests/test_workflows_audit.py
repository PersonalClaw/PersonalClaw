"""`workflow_audit` (WF2-R10) and the v2 `run-workflow` action provider.

`audit()` covers what accumulates while the watchdog was NOT running. The load-bearing
claims:

* **`dry_run` defaults to True** — an auto-repair that runs before a human sees the
  diagnosis turns one broken run into a broken store;
* a run with a **live controller is never healed underneath it** — the controller is that
  run's only legitimate writer (WF2-R10);
* a vanished worker becomes `blocked{protocol_violation}`, not FAILED — "the worker
  disappeared" is a different fact from "the work failed";
* an expired wait is **cleared, not completed**: only the controller decides what a woken
  wait means;
* a dead gate is reported and **never auto-resolved** — nobody approved anything.

The provider's claim: a started run reports `outcome="launched"`, never plain success,
and `on_overlap` is honoured so a fast trigger cannot stack runs.
"""

from __future__ import annotations

import time

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.audit import (
    EXPIRED_WAIT_GRACE_SECS,
    STALE_RUNNING_SECS,
    Finding,
    audit,
)
from personalclaw.workflows.models import (
    InstanceState,
    NodeInstance,
    RunStatus,
    WorkflowRun,
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
    "name": "aud",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "t", "config": {"expr": 1}}],
    },
}


def _stamp(offset_secs: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - offset_secs))


def _run(status: RunStatus = RunStatus.RUNNING, spec: dict | None = None) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="aud", status=status))
    if spec is not None:
        store.write_spec(run.id, spec)
    return run


class _FakeSupervisor:
    def __init__(self, live_ids: set[str] | None = None) -> None:
        self._live = live_ids or set()

    def controller(self, run_id: str):
        return object() if run_id in self._live else None


class TestDiagnose:
    def test_a_healthy_store_reports_nothing(self) -> None:
        run = _run(spec=SPEC)
        store.write_state(run.id, {"root.children[0]": NodeInstance(path="root.children[0]")})
        report = audit()
        assert report.healthy and report.runs_scanned == 1

    def test_a_missing_spec_is_found(self) -> None:
        _run()  # no spec written
        report = audit()
        assert [f.kind for f in report.findings] == [Finding.MISSING_SPEC]

    def test_a_stale_running_node_is_found(self) -> None:
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.RUNNING,
            started_at=_stamp(STALE_RUNNING_SECS + 3600),
        )
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit()
        assert Finding.STALE_RUNNING in [f.kind for f in report.findings]

    def test_a_recently_started_node_is_not_stale(self) -> None:
        """A false 'lost' verdict kills live work, so the threshold sits well above any
        legitimate node duration."""
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]", state=InstanceState.RUNNING, started_at=_stamp(60)
        )
        store.write_state(run.id, {"root.children[0]": inst})
        assert audit().healthy

    def test_an_expired_wait_is_found(self) -> None:
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.WAITING,
            wake_at=time.time() - EXPIRED_WAIT_GRACE_SECS - 60,
        )
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit()
        assert Finding.EXPIRED_WAIT in [f.kind for f in report.findings]

    def test_a_future_deadline_is_not_expired(self) -> None:
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.WAITING,
            wake_at=time.time() + 3600,
        )
        store.write_state(run.id, {"root.children[0]": inst})
        assert audit().healthy

    def test_a_dead_gate_is_reported_when_the_run_is_not_surfaced(self) -> None:
        run = _run(spec=SPEC)
        inst = NodeInstance(path="root.children[0]", state=InstanceState.WAITING, wake_at=0.0)
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit()
        assert Finding.DEAD_GATE in [f.kind for f in report.findings]

    def test_a_gate_on_a_needs_input_run_is_legitimate(self) -> None:
        """Parked on a human with the run correctly surfaced is not a finding."""
        run = _run(status=RunStatus.NEEDS_INPUT, spec=SPEC)
        inst = NodeInstance(path="root.children[0]", state=InstanceState.WAITING, wake_at=0.0)
        store.write_state(run.id, {"root.children[0]": inst})
        assert audit().healthy

    def test_a_lost_run_is_found(self) -> None:
        run = _run(spec=SPEC)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", state=InstanceState.DONE)},
        )
        report = audit()
        assert Finding.LOST_RUN in [f.kind for f in report.findings]

    def test_an_unconsumed_cancel_is_found(self) -> None:
        run = _run(spec=SPEC)
        store.request_cancel(run.id)
        report = audit()
        assert [f.kind for f in report.findings] == [Finding.PENDING_CANCEL]


class TestDryRunSafety:
    def test_dry_run_is_the_default_and_changes_nothing(self) -> None:
        run = _run(spec=SPEC)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", state=InstanceState.DONE)},
        )
        report = audit()
        assert report.dry_run is True
        assert not any(f.healed for f in report.findings)
        assert store.get(run.id).status == RunStatus.RUNNING  # untouched

    def test_a_run_with_a_live_controller_is_never_healed(self) -> None:
        """The controller is that run's only legitimate writer (WF2-R10) — healing
        underneath it would put two writers on one run."""
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.RUNNING,
            started_at=_stamp(STALE_RUNNING_SECS + 3600),
        )
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit(dry_run=False, supervisor=_FakeSupervisor({run.id}))
        stale = [f for f in report.findings if f.kind == Finding.STALE_RUNNING]
        assert stale and not stale[0].healed  # reported, left alone
        assert store.read_state(run.id)["root.children[0]"].state == InstanceState.RUNNING


class TestHeal:
    def test_healing_a_stale_node_marks_protocol_violation(self) -> None:
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.RUNNING,
            started_at=_stamp(STALE_RUNNING_SECS + 3600),
        )
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit(dry_run=False)
        healed = [f for f in report.findings if f.kind == Finding.STALE_RUNNING]
        assert healed and healed[0].healed
        after = store.read_state(run.id)["root.children[0]"]
        # BLOCKED, not FAILED: "the worker vanished without reporting" is a different
        # fact from "the work failed", and it routes to needs-input.
        assert after.state == InstanceState.BLOCKED
        assert after.failure.terminal_reason == "protocol_violation"

    def test_healing_an_expired_wait_clears_but_does_not_complete_it(self) -> None:
        """Only the controller decides what a woken wait MEANS; healing just removes the
        wedge."""
        run = _run(spec=SPEC)
        inst = NodeInstance(
            path="root.children[0]",
            state=InstanceState.WAITING,
            wake_at=time.time() - EXPIRED_WAIT_GRACE_SECS - 60,
        )
        store.write_state(run.id, {"root.children[0]": inst})
        audit(dry_run=False)
        after = store.read_state(run.id)["root.children[0]"]
        assert after.state == InstanceState.PENDING and after.wake_at == 0.0

    def test_healing_a_lost_run_writes_the_derived_status(self) -> None:
        run = _run(spec=SPEC)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", state=InstanceState.DONE)},
        )
        audit(dry_run=False)
        assert store.get(run.id).status == RunStatus.COMPLETE

    def test_a_lost_run_with_a_failed_node_finalizes_failed(self) -> None:
        """Severity collapse matches the frontier's, so an audit and a normal completion
        cannot disagree about a mixed child set."""
        run = _run(spec=SPEC)
        store.write_state(
            run.id,
            {
                "root.children[0]": NodeInstance(
                    path="root.children[0]", state=InstanceState.FAILED
                ),
                "root.children[1]": NodeInstance(path="root.children[1]", state=InstanceState.DONE),
            },
        )
        audit(dry_run=False)
        assert store.get(run.id).status == RunStatus.FAILED

    def test_healing_a_pending_cancel_finalizes_and_clears_the_sentinel(self) -> None:
        run = _run(spec=SPEC)
        store.request_cancel(run.id)
        audit(dry_run=False)
        assert store.get(run.id).status == RunStatus.CANCELLED
        assert not store.cancel_requested(run.id)

    def test_a_dead_gate_is_never_auto_resolved(self) -> None:
        """Nobody approved anything — auto-resolving a gate would fabricate consent."""
        run = _run(spec=SPEC)
        inst = NodeInstance(path="root.children[0]", state=InstanceState.WAITING, wake_at=0.0)
        store.write_state(run.id, {"root.children[0]": inst})
        report = audit(dry_run=False)
        gate = [f for f in report.findings if f.kind == Finding.DEAD_GATE]
        assert gate and not gate[0].healed
        assert store.read_state(run.id)["root.children[0]"].state == InstanceState.WAITING


class TestUtcParsing:
    """Regression: `time.mktime` reads a struct as LOCAL time, so parsing a UTC `...Z`
    stamp with it shifted the value by the machine's offset — and here that offset exactly
    cancelled the measured age, leaving the stale-running check permanently inert."""

    def test_a_utc_stamp_parses_to_its_real_epoch(self) -> None:
        from personalclaw.workflows.audit import _epoch

        # 2026-01-01T00:00:00Z is exactly 1767225600 in UTC, in every timezone.
        assert _epoch("2026-01-01T00:00:00Z") == 1767225600.0

    def test_a_known_age_measures_correctly(self) -> None:
        from personalclaw.workflows.audit import _epoch

        age = time.time() - _epoch(_stamp(7200))
        assert 7150 < age < 7250  # ~2h, not ~0 and not shifted by a tz offset

    def test_the_controller_parses_utc_identically(self) -> None:
        """Both parsers must agree, or a run's elapsed time and its audit age disagree."""
        from personalclaw.workflows.audit import _epoch as audit_epoch
        from personalclaw.workflows.controller import _epoch as ctrl_epoch

        assert ctrl_epoch("2026-01-01T00:00:00Z") == audit_epoch("2026-01-01T00:00:00Z")


class TestReportShape:
    def test_counts_group_by_kind(self) -> None:
        _run()
        _run()
        report = audit()
        assert report.to_dict()["counts"] == {Finding.MISSING_SPEC: 2}

    def test_the_report_serializes(self) -> None:
        _run()
        d = audit().to_dict()
        assert set(d) == {"healthy", "dry_run", "runs_scanned", "counts", "findings"}


# ── the run-workflow action provider ─────────────────────────────────────────


class TestRunWorkflowProvider:
    def _provider(self):
        from personalclaw.action_providers.run_workflow_provider import (
            RunWorkflowActionProvider,
        )

        return RunWorkflowActionProvider()

    def _ctx(self):
        from personalclaw.action_providers.base import ActionContext

        return ActionContext(event="schedule", context="trigger-1")

    def test_it_is_registered_and_allowlisted_together(self) -> None:
        """A provider in one set but not the other is what makes a trigger save and then
        fail at fire time."""
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        _ensure_default_providers_registered()
        assert get_action_provider("run-workflow") is not None
        assert "run-workflow" in ALLOWED_HOOK_PROVIDERS

    async def test_a_missing_workflow_name_is_a_typed_failure(self) -> None:
        result = await self._provider().execute({}, self._ctx())
        assert not result.success and "workflow" in result.error

    async def test_an_unknown_workflow_fails_actionably(self) -> None:
        result = await self._provider().execute({"workflow": "no-such-wf"}, self._ctx())
        assert not result.success and "no-such-wf" in result.error

    async def test_a_dry_run_creates_nothing(self) -> None:
        import json

        from personalclaw.workflows import defs as defs_mod

        class P(defs_mod.WorkflowDefProvider):
            @property
            def name(self) -> str:
                return "test-pack"

            async def list_defs(self, *, limit=200, offset=0):
                return [], 0

            async def get_def(self, name):
                return dict(SPEC) if name == "aud" else None

        defs_mod.register_provider(P())
        try:
            before, _ = store.list_runs()
            result = await self._provider().execute(
                {"workflow": "aud", "dry_run": True}, self._ctx()
            )
            after, _ = store.list_runs()
            assert result.success and result.outcome == "skip"
            assert json.loads(result.stdout)["dry_run"] is True
            assert len(after) == len(before)  # nothing created
        finally:
            defs_mod.unregister_provider("test-pack")

    async def test_overlap_skip_refuses_to_stack_runs(self) -> None:
        """A per-minute trigger against a ten-minute workflow must not pile up runs."""
        import json

        from personalclaw.workflows import defs as defs_mod

        existing = store.create(WorkflowRun(id="", workflow_name="aud", status=RunStatus.RUNNING))

        class P(defs_mod.WorkflowDefProvider):
            @property
            def name(self) -> str:
                return "test-pack"

            async def list_defs(self, *, limit=200, offset=0):
                return [], 0

            async def get_def(self, name):
                return {**SPEC, "on_overlap": "skip"} if name == "aud" else None

        defs_mod.register_provider(P())
        try:
            result = await self._provider().execute({"workflow": "aud"}, self._ctx())
            assert result.success and result.outcome == "skip"
            body = json.loads(result.stdout)
            assert body["skipped"] is True and body["run_id"] == existing.id
        finally:
            defs_mod.unregister_provider("test-pack")

    async def test_a_started_run_reports_launched_not_success(self) -> None:
        """`launched` means STARTED. Reporting plain success would make an unverified run
        look verified."""
        import json

        from personalclaw.action_providers import services as svc_mod
        from personalclaw.workflows import defs as defs_mod

        launched: list[str] = []

        class FakeSupervisor:
            async def launch(self, run, spec, *, depth=0):
                launched.append(run.id)

        class FakeServices:
            workflows = FakeSupervisor()

        class P(defs_mod.WorkflowDefProvider):
            @property
            def name(self) -> str:
                return "test-pack"

            async def list_defs(self, *, limit=200, offset=0):
                return [], 0

            async def get_def(self, name):
                return dict(SPEC) if name == "aud" else None

        defs_mod.register_provider(P())
        original = svc_mod._services
        svc_mod._services = FakeServices()  # type: ignore[assignment]
        try:
            result = await self._provider().execute(
                {"workflow": "aud", "inputs": {"x": 1}}, self._ctx()
            )
            assert result.success and result.outcome == "launched"
            run_id = json.loads(result.stdout)["run_id"]
            assert launched == [run_id]
            created = store.get(run_id)
            assert created.inputs == {"x": 1}
            assert created.origin.trigger_id == "trigger-1"
        finally:
            svc_mod._services = original
            defs_mod.unregister_provider("test-pack")

    async def test_a_retried_caller_key_returns_the_same_run(self) -> None:
        import json

        from personalclaw.action_providers import services as svc_mod
        from personalclaw.workflows import defs as defs_mod
        from personalclaw.workflows.effects import START_DEDUPE

        class FakeSupervisor:
            async def launch(self, run, spec, *, depth=0):
                return None

        class FakeServices:
            workflows = FakeSupervisor()

        class P(defs_mod.WorkflowDefProvider):
            @property
            def name(self) -> str:
                return "test-pack"

            async def list_defs(self, *, limit=200, offset=0):
                return [], 0

            async def get_def(self, name):
                return dict(SPEC) if name == "aud" else None

        defs_mod.register_provider(P())
        original = svc_mod._services
        svc_mod._services = FakeServices()  # type: ignore[assignment]
        START_DEDUPE._entries.clear()
        try:
            cfg = {"workflow": "aud", "idempotency_key": "call-xyz"}
            first = await self._provider().execute(cfg, self._ctx())
            second = await self._provider().execute(cfg, self._ctx())
            first_id = json.loads(first.stdout)["run_id"]
            body = json.loads(second.stdout)
            assert body["run_id"] == first_id and body["deduped"] is True
        finally:
            svc_mod._services = original
            defs_mod.unregister_provider("test-pack")
            START_DEDUPE._entries.clear()

    async def test_no_supervisor_is_an_honest_failure(self) -> None:
        """The run row exists but nothing is driving it — saying "launched" would be a
        lie."""
        from personalclaw.action_providers import services as svc_mod
        from personalclaw.workflows import defs as defs_mod

        class P(defs_mod.WorkflowDefProvider):
            @property
            def name(self) -> str:
                return "test-pack"

            async def list_defs(self, *, limit=200, offset=0):
                return [], 0

            async def get_def(self, name):
                return dict(SPEC) if name == "aud" else None

        defs_mod.register_provider(P())
        original = svc_mod._services
        svc_mod._services = None
        try:
            result = await self._provider().execute({"workflow": "aud"}, self._ctx())
            assert not result.success and "supervisor" in result.error
        finally:
            svc_mod._services = original
            defs_mod.unregister_provider("test-pack")
