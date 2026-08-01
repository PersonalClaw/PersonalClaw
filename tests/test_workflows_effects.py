"""The effect ledger (WF2-R1) — external side effects get identity and a redo boundary.

The scenario every assertion here defends: a run fires a Slack message (or provisions a
VM), then crashes, resumes, rewinds, or forks. Without effect identity the replay
double-fires. The load-bearing claims:

* ATTEMPTED lands BEFORE dispatch — a crash mid-effect leaves "may have fired" evidence;
* a COMMITTED effect from a prior epoch BLOCKS re-execution unless `redo_effects: true`;
* with `redo_effects: true`, the declared teardown runs FIRST, with the committed
  output id, and a failed teardown still blocks (unknown external state);
* the boundary survives a process restart, because it is reconstructed from the ledger,
  not from controller memory;
* a same-epoch retry is NOT blocked — same idempotency key, receiver-side dedupe;
* caller-key dedupe returns the existing run id inside the TTL window.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.effects import (
    CallerDedupe,
    EffectRecord,
    EffectStatus,
    committed_effect,
    effect_history,
    idempotency_key,
    output_id_of,
    parse_byoi_output,
    redo_blocked,
    run_teardown,
)
from personalclaw.workflows.journal import EFFECT
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


def _make_run(spec: dict, **kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec.get("name", "wf"), **kw))
    store.write_spec(run.id, spec)
    return run


class _Result:
    def __init__(self, success=True, stdout="", outcome="", error="", exit_code=0):
        self.success = success
        self.stdout = stdout
        self.outcome = outcome
        self.error = error
        self.exit_code = exit_code
        self.stderr = ""
        self.agent_error = None


def _provider(result, calls=None):
    class P:
        async def execute(self, cfg, ctx, timeout=30):
            if calls is not None:
                calls.append(cfg)
            return result

    return lambda name: P()


def _action_spec(config: dict | None = None) -> dict:
    return {
        "name": "fx",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "send",
                    "config": {"provider": "notify", **(config or {})},
                }
            ],
        },
    }


# ── key + pure helpers ───────────────────────────────────────────────────────


class TestIdentity:
    def test_key_is_deterministic_and_epoch_sensitive(self) -> None:
        a = idempotency_key("r1", "root.children[0]", 0)
        assert a == idempotency_key("r1", "root.children[0]", 0)
        # A rewound region re-executes deliberately — it must present a NEW identity.
        assert a != idempotency_key("r1", "root.children[0]", 1)
        assert a != idempotency_key("r2", "root.children[0]", 0)

    def test_committed_effect_is_the_standing_commitment(self) -> None:
        recs = [
            EffectRecord(idempotency_key="k1", effect_status=EffectStatus.ATTEMPTED),
            EffectRecord(idempotency_key="k1", effect_status=EffectStatus.COMMITTED, epoch=0),
        ]
        found = committed_effect(recs)
        assert found is not None and found.idempotency_key == "k1"

    def test_a_compensated_commitment_is_retired(self) -> None:
        """Teardown ran: the resource is gone, so re-execution is no longer a
        double-fire and the boundary must clear."""
        recs = [
            EffectRecord(idempotency_key="k1", effect_status=EffectStatus.COMMITTED, epoch=0),
            EffectRecord(idempotency_key="k1", effect_status=EffectStatus.COMPENSATED),
        ]
        assert committed_effect(recs) is None

    def test_redo_blocked_only_across_epochs(self) -> None:
        committed = EffectRecord(idempotency_key="k", epoch=0)
        # Same epoch: same key, receiver dedupes — the retry contract, not a double-fire.
        assert not redo_blocked({}, committed, epoch=0)
        assert redo_blocked({}, committed, epoch=1)
        assert not redo_blocked({"redo_effects": True}, committed, epoch=1)
        assert not redo_blocked({}, None, epoch=1)


class TestByoiContract:
    def test_exactly_one_json_object_parses(self) -> None:
        assert parse_byoi_output('{"id": "vm-1", "host": "x"}') == {"id": "vm-1", "host": "x"}

    def test_two_objects_are_ambiguous_and_rejected(self) -> None:
        """Two objects = which id does the teardown get? Guessing wrong orphans a
        resource; ambiguity is a contract violation."""
        assert parse_byoi_output('{"id": "a"}\n{"id": "b"}') is None

    def test_non_object_and_garbage_are_rejected(self) -> None:
        assert parse_byoi_output("[1, 2]") is None
        assert parse_byoi_output("not json") is None
        assert parse_byoi_output("") is None

    def test_output_id_reads_the_id_field(self) -> None:
        assert output_id_of({"id": "vm-9"}) == "vm-9"
        assert output_id_of("plain text") == ""
        assert output_id_of(None) == ""


class TestTeardownRunner:
    async def test_injected_runner_is_used_and_gets_the_output_id(self) -> None:
        seen: list[tuple[str, str]] = []

        async def runner(cmd, output_id):
            seen.append((cmd, output_id))
            return True, "gone"

        ok, detail = await run_teardown("destroy-vm", "vm-1", runner=runner)
        assert ok and detail == "gone"
        assert seen == [("destroy-vm", "vm-1")]

    async def test_a_raising_runner_reports_not_raises(self) -> None:
        async def runner(cmd, output_id):
            raise RuntimeError("boom")

        ok, detail = await run_teardown("destroy-vm", "vm-1", runner=runner)
        assert not ok and "boom" in detail

    async def test_subprocess_teardown_receives_id_as_argv_and_env(self, tmp_path) -> None:
        marker = tmp_path / "seen.txt"
        script = tmp_path / "teardown.sh"
        script.write_text('#!/bin/sh\necho "$1|$EFFECT_OUTPUT_ID" > ' + str(marker) + "\n")
        script.chmod(0o755)
        ok, _ = await run_teardown(str(script), "vm-42")
        assert ok
        assert marker.read_text().strip() == "vm-42|vm-42"

    async def test_missing_command_is_a_typed_failure(self) -> None:
        ok, detail = await run_teardown("/no/such/binary-xyz", "vm-1")
        assert not ok and "not found" in detail


# ── controller integration ───────────────────────────────────────────────────


class TestEffectLifecycle:
    async def test_attempted_then_committed_on_success(self) -> None:
        spec = _action_spec()
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_provider(_Result(stdout='{"id": "msg-1"}'))),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        history = effect_history(run.id)
        statuses = [r.effect_status for r in history["root.children[0]"]]
        assert statuses == [EffectStatus.ATTEMPTED, EffectStatus.COMMITTED]
        committed = committed_effect(history["root.children[0]"])
        assert committed.output_id == "msg-1"

    async def test_attempted_lands_even_when_the_action_fails(self) -> None:
        """The ATTEMPTED record is the crash-evidence half of the contract: it must be
        on disk whether or not an outcome ever arrives."""
        spec = _action_spec({"retry": {"max_attempts": 1}})
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_provider(_Result(success=False, error="down"))),
        )
        await c.run_to_completion(timeout=20)
        statuses = [r.effect_status for r in effect_history(run.id).get("root.children[0]", [])]
        assert statuses == [EffectStatus.ATTEMPTED]

    async def test_a_skip_outcome_is_ledgered_skipped(self) -> None:
        spec = _action_spec()
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_provider(_Result(outcome="skip"))),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        statuses = [r.effect_status for r in effect_history(run.id)["root.children[0]"]]
        assert statuses == [EffectStatus.ATTEMPTED, EffectStatus.SKIPPED]
        # Nothing fired, so nothing stands committed and a redo needs no gate.
        assert committed_effect(effect_history(run.id)["root.children[0]"]) is None

    async def test_a_retry_records_retried_between_attempts(self) -> None:
        calls: list[dict] = []

        class Flaky:
            def __init__(self):
                self.n = 0

            async def execute(self, cfg, ctx, timeout=30):
                self.n += 1
                calls.append(cfg)
                if self.n == 1:
                    return _Result(success=False, error="rate limit 429")
                return _Result(stdout='{"id": "ok-2"}')

        flaky = Flaky()
        spec = _action_spec({"retry": {"max_attempts": 2}})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(get_provider=lambda name: flaky))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        recs = effect_history(run.id)["root.children[0]"]
        statuses = [r.effect_status for r in recs]
        assert statuses == [
            EffectStatus.ATTEMPTED,
            EffectStatus.RETRIED,
            EffectStatus.ATTEMPTED,
            EffectStatus.COMMITTED,
        ]
        # Same epoch throughout: every record carries the SAME idempotency key, which is
        # what lets an idempotent receiver collapse the two dispatches into one effect.
        assert len({r.idempotency_key for r in recs}) == 1


class TestRedoBoundary:
    def _completed_with_effect(self, run_id: str, spec: dict) -> None:
        """Simulate a prior epoch-0 completion whose effect committed."""
        from personalclaw.workflows.journal import Journal

        j = Journal(run_id)
        key = idempotency_key(run_id, "root.children[0]", 0)
        j.effect(
            "root.children[0]",
            idempotency_key=key,
            effect_status=EffectStatus.COMMITTED.value,
            epoch=0,
            node_id="send",
            provider="notify",
            output_id="vm-7",
            compensation_ref="destroy-vm",
        )

    async def test_reexecution_across_epochs_is_blocked_without_redo_effects(self) -> None:
        """The heart of WF2-R1: a rewind that crosses a committed effect refuses to
        silently re-fire it."""
        spec = _action_spec()
        run = _make_run(spec)
        self._completed_with_effect(run.id, spec)
        # Epoch bumped to 1 (a rewind happened); node back to pending.
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", epoch=1)},
        )
        fired: list[dict] = []
        c = RunController(
            run,
            spec,
            services=EngineServices(
                get_provider=_provider(_Result(stdout='{"id": "vm-8"}'), calls=fired)
            ),
        )
        status = await c.run_to_completion(timeout=20)
        assert fired == []  # the provider was NEVER dispatched
        assert status == RunStatus.FAILED
        inst = store.read_state(run.id)["root.children[0]"]
        assert inst.state == InstanceState.BLOCKED
        assert inst.failure.terminal_reason == "committed_effect"
        assert "redo_effects" in inst.failure.remediation

    async def test_redo_effects_runs_teardown_first_then_reexecutes(self) -> None:
        spec = _action_spec({"redo_effects": True})
        run = _make_run(spec)
        self._completed_with_effect(run.id, spec)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", epoch=1)},
        )
        torn: list[tuple[str, str]] = []

        async def teardown(cmd, output_id):
            torn.append((cmd, output_id))
            return True, "gone"

        fired: list[dict] = []
        c = RunController(
            run,
            spec,
            services=EngineServices(
                get_provider=_provider(_Result(stdout='{"id": "vm-8"}'), calls=fired),
                teardown_runner=teardown,
            ),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        # Teardown got the COMMITTED resource's id, then the node re-fired.
        assert torn == [("destroy-vm", "vm-7")]
        assert len(fired) == 1
        recs = effect_history(run.id)["root.children[0]"]
        statuses = [r.effect_status for r in recs]
        assert statuses[0] == EffectStatus.COMMITTED  # the simulated prior epoch
        assert EffectStatus.COMPENSATED in statuses
        new_commit = committed_effect(recs)
        assert new_commit.epoch == 1 and new_commit.output_id == "vm-8"

    async def test_a_failed_teardown_blocks_instead_of_stacking_resources(self) -> None:
        spec = _action_spec({"redo_effects": True})
        run = _make_run(spec)
        self._completed_with_effect(run.id, spec)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", epoch=1)},
        )

        async def teardown(cmd, output_id):
            return False, "instance still terminating"

        fired: list[dict] = []
        c = RunController(
            run,
            spec,
            services=EngineServices(
                get_provider=_provider(_Result(stdout='{"id": "vm-8"}'), calls=fired),
                teardown_runner=teardown,
            ),
        )
        status = await c.run_to_completion(timeout=20)
        assert fired == []  # unknown external state: do NOT provision a second resource
        assert status == RunStatus.FAILED
        inst = store.read_state(run.id)["root.children[0]"]
        assert inst.failure.terminal_reason == "teardown_failed"

    async def test_the_boundary_survives_a_restart(self) -> None:
        """A fresh controller must reconstruct the boundary from the LEDGER — controller
        memory dies with the process, and the whole point is surviving that."""
        spec = _action_spec()
        run = _make_run(spec)
        self._completed_with_effect(run.id, spec)
        store.write_state(
            run.id,
            {"root.children[0]": NodeInstance(path="root.children[0]", epoch=1)},
        )
        # Brand-new controller, nothing shared with any prior one.
        c = RunController(
            run, spec, services=EngineServices(get_provider=_provider(_Result(stdout="{}")))
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        inst = store.read_state(run.id)["root.children[0]"]
        assert inst.failure.terminal_reason == "committed_effect"

    async def test_non_action_nodes_never_consult_the_gate(self) -> None:
        """Only ACTION dispatches are side-effecting. A transform re-running across
        epochs is the memoized-replay design working, not a double-fire."""
        spec = {
            "name": "pure",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "transform", "id": "t", "config": {"expr": {"v": 1}}}],
            },
        }
        run = _make_run(spec)
        store.write_state(
            run.id, {"root.children[0]": NodeInstance(path="root.children[0]", epoch=2)}
        )
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE


class TestEffectEventShape:
    async def test_effect_events_reach_the_ledger_file_with_full_identity(self) -> None:
        spec = _action_spec()
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_provider(_Result(stdout='{"id": "m-1"}'))),
        )
        await c.run_to_completion(timeout=20)
        from personalclaw.workflows.journal import EVENTS_FILE

        events = [r for r in store.read_jsonl(run.id, EVENTS_FILE) if r.get("kind") == EFFECT]
        assert len(events) == 2
        for e in events:
            assert e["idempotency_key"] == idempotency_key(run.id, "root.children[0]", 0)
            assert e["node_id"] == "send"
            assert e["provider"] == "notify"
            assert e["event_id"]  # deterministic id — replays dedupe


# ── caller dedupe ────────────────────────────────────────────────────────────


class TestCallerDedupe:
    def test_a_retried_caller_key_returns_the_existing_run(self) -> None:
        clock = {"t": 100.0}
        cache = CallerDedupe(ttl_secs=900, clock=lambda: clock["t"])
        cache.remember("tool-call-abc", "run-1")
        assert cache.lookup("tool-call-abc") == "run-1"

    def test_expiry_forgets(self) -> None:
        clock = {"t": 100.0}
        cache = CallerDedupe(ttl_secs=10, clock=lambda: clock["t"])
        cache.remember("k", "run-1")
        clock["t"] = 111.0
        assert cache.lookup("k") is None

    def test_empty_keys_never_dedupe(self) -> None:
        cache = CallerDedupe()
        cache.remember("", "run-1")
        assert cache.lookup("") is None

    def test_sweep_evicts_stale_entries_on_write(self) -> None:
        clock = {"t": 0.0}
        cache = CallerDedupe(ttl_secs=5, clock=lambda: clock["t"])
        cache.remember("old", "run-1")
        clock["t"] = 100.0
        cache.remember("new", "run-2")
        assert "old" not in cache._entries
        assert cache.lookup("new") == "run-2"
