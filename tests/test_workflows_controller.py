"""The controller and journal — end-to-end runs against a temp home.

These are the integration tests for the slice: a real spec, a real journal on disk, real
state persistence, with only the model call faked. Everything writes under a monkeypatched
`config_dir`, so nothing here can touch a real home.

The load-bearing assertions:

* **the resume cache re-runs exactly the binding closure** (WF2-A1) — after editing one
  node's prompt, that node and its dependents re-run and nothing else does, asserted from
  the LEDGER rather than from logs;
* **budget is pre-charged on resume** (WF2-R4) — a resumed run inherits its spend, or a
  crash loop becomes unbounded;
* **timeouts actually fire** (WF2-R5) — the studied cautionary case is an engine that
  shipped a no-op node timeout nobody noticed, because timeouts only run under failure;
* **credentials never reach the journal** — it is read by the flywheel, shipped in bug
  reports, and rendered in a UI.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.journal import CacheKey, Journal, inputs_hash, spec_region_hash
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    Node,
    NodeInstance,
    RunBudget,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.tick import Limits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config dir. Destructive-by-nature tests (retention sweeps)
    must never see a real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _make_run(spec: dict, inputs: dict | None = None, **kw) -> WorkflowRun:
    run = store.create(
        WorkflowRun(id="", workflow_name=spec.get("name", "wf"), inputs=inputs or {}, **kw)
    )
    store.write_spec(run.id, spec)
    return run


def _echo(tag: str = ""):
    calls: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None):
        calls.append(prompt)
        return f"{tag}out{len(calls)}"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


SEQ_SPEC = {
    "name": "seq",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 7}}},
            {
                "kind": "infer",
                "id": "think",
                "config": {"prompt": "double {{nodes.seed.output.n}}"},
            },
            {"kind": "transform", "id": "final", "config": {"expr": "got {{nodes.think.output}}"}},
        ],
    },
}


class TestHappyPath:
    async def test_a_sequence_completes_and_threads_bindings(self) -> None:
        run = _make_run(SEQ_SPEC)
        fn = _echo()
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=fn))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert fn.calls == ["double 7"]
        assert c._outputs["final"] == "got out1"

    async def test_state_is_persisted_for_every_node(self) -> None:
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        on_disk = store.read_state(run.id)
        assert len(on_disk) == 3
        assert all(i.state == InstanceState.DONE for i in on_disk.values())

    async def test_the_run_row_carries_terminal_metadata(self) -> None:
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        saved = store.get(run.id)
        assert saved.status == RunStatus.COMPLETE
        assert saved.started_at and saved.completed_at
        assert saved.total_tokens > 0

    async def test_events_are_published_for_the_widget(self) -> None:
        seen: list[tuple[str, dict]] = []
        run = _make_run(SEQ_SPEC)
        c = RunController(
            run,
            SEQ_SPEC,
            services=EngineServices(completion=_echo(), publish=lambda e, p: seen.append((e, p))),
        )
        await c.run_to_completion(timeout=20)
        kinds = [e for e, _ in seen]
        assert "workflow_run_update" in kinds
        assert kinds.count("workflow_node_started") == 3
        assert kinds.count("workflow_node_done") == 3
        assert all("run_id" in p for _, p in seen)

    async def test_a_broken_observer_cannot_kill_a_run(self) -> None:
        def boom(event, payload):
            raise RuntimeError("observer exploded")

        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo(), publish=boom))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE


class TestArtifactOffload:
    """WV-11 end to end: a node whose output offloads populates `node_artifacts`, so a
    downstream `{{nodes.x.artifact}}` resolves to a live pointer."""

    #: A big output goes to the model node; the transform below binds its ARTIFACT ref.
    _BIG_SPEC = {
        "name": "offload",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "infer", "id": "big", "config": {"prompt": "make it"}},
                {
                    "kind": "transform",
                    "id": "pointer",
                    "config": {"expr": "ref={{nodes.big.artifact}}"},
                },
            ],
        },
    }

    async def test_node_artifacts_populates_and_the_binding_resolves(self) -> None:
        async def big(prompt, *, use_case="background", output_type=None):
            return "B" * (J.MAX_INLINE_OUTPUT_BYTES + 1000)

        run = _make_run(self._BIG_SPEC)
        c = RunController(run, self._BIG_SPEC, services=EngineServices(completion=big))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

        # The writer: the offloaded node's id maps to an artifacts/ ref (not outputs/).
        arts = c._node_artifacts()
        assert arts is not None and "big" in arts
        assert arts["big"].startswith("artifacts/")

        # The reader: the downstream transform resolved `{{nodes.big.artifact}}` to that ref.
        assert c._outputs["pointer"] == f"ref={arts['big']}"

    async def test_a_small_output_populates_no_artifact(self) -> None:
        """An inline output does NOT get an artifact pointer — `node_artifacts` is only the
        offloaded set, and a phantom entry would resolve `{{nodes.x.artifact}}` to an
        outputs/ path the inspect/provider machinery does not treat as an artifact."""
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        assert c._node_artifacts() is None


class TestResumeCache:
    """WF2-A1: the acceptance bar is that this is answerable from the LEDGER."""

    async def _run_once(self, spec, fn, run=None):
        run = run or _make_run(spec)
        run.status = RunStatus.RUNNING
        c = RunController(run, spec, services=EngineServices(completion=fn))
        for inst in c.instances.values():
            inst.state = InstanceState.PENDING  # re-schedule everything
        await c.run_to_completion(timeout=20)
        return run, c

    def _spec(self, second: str) -> dict:
        return {
            "name": "cache",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "n1", "config": {"prompt": "first"}},
                    {"kind": "infer", "id": "n2", "config": {"prompt": second}},
                    {
                        "kind": "infer",
                        "id": "n3",
                        "config": {"prompt": "third {{nodes.n2.output}}"},
                    },
                ],
            },
        }

    async def test_an_unchanged_resume_makes_zero_model_calls(self) -> None:
        spec = self._spec("second")
        fn = _echo()
        run, _ = await self._run_once(spec, fn)
        assert len(fn.calls) == 3
        await self._run_once(spec, fn, store.get(run.id))
        assert len(fn.calls) == 3, "a cached node re-ran"
        cached = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_CACHED]
        assert len(cached) == 3
        assert all(r.get("cached") is True for r in cached)

    async def test_editing_a_prompt_re_runs_exactly_the_binding_closure(self) -> None:
        """n2's own spec changed, and n3's INPUTS changed. n1 is outside the closure and
        must stay cached — that is the difference between a targeted re-run and redoing
        the whole run."""
        fn = _echo()
        run, _ = await self._run_once(self._spec("second"), fn)
        before = len(fn.calls)
        await self._run_once(self._spec("second EDITED"), fn, store.get(run.id))
        assert len(fn.calls) - before == 2
        assert fn.calls[-2] == "second EDITED"
        assert fn.calls[-1].startswith("third ")

    async def test_a_cached_failure_is_never_served(self) -> None:
        """Replaying a cached failure would make a transient error permanent across a
        resume."""
        jr = Journal("r1")
        key = CacheKey(path="root", epoch=0, inputs_hash="h", spec_hash="s")
        jr.step_failed("root", "n", epoch=0, failure=Failure(cause_plain="x"))
        jr.write(J.STEP_COMPLETED, cache_key=key.to_str(), state=InstanceState.FAILED.value)
        assert Journal("r1").lookup(key) is None

    async def test_the_cache_key_needs_all_four_parts(self) -> None:
        """Dropping any one produces a cache that serves stale answers."""
        a = CacheKey("root", 0, "i1", "s1")
        for other in (
            CacheKey("root2", 0, "i1", "s1"),
            CacheKey("root", 1, "i1", "s1"),
            CacheKey("root", 0, "i2", "s1"),
            CacheKey("root", 0, "i1", "s2"),
        ):
            assert a.to_str() != other.to_str()

    async def test_a_rewind_invalidates_by_path_prefix(self) -> None:
        jr = Journal("r2")
        for path in ("root.children[0]", "root.children[1]", "root.children[1].body"):
            jr.write(
                J.STEP_COMPLETED,
                cache_key=CacheKey(path, 0, "h", "s").to_str(),
                state=InstanceState.DONE.value,
            )
        assert jr.invalidate_prefix("root.children[1]") == 2
        assert jr.lookup(CacheKey("root.children[0]", 0, "h", "s")) is not None


class TestSpecHashing:
    def test_a_child_edit_does_not_invalidate_its_parent(self) -> None:
        """Children are excluded from the region hash deliberately: editing a child must
        invalidate the child, not silently re-run its completed container."""
        parent_a = {
            "kind": "sequence",
            "id": "s",
            "config": {"x": 1},
            "children": [{"kind": "transform", "id": "c", "config": {"expr": "1"}}],
        }
        parent_b = {
            "kind": "sequence",
            "id": "s",
            "config": {"x": 1},
            "children": [{"kind": "transform", "id": "c", "config": {"expr": "2"}}],
        }
        assert spec_region_hash(parent_a) == spec_region_hash(parent_b)

    def test_the_node_s_own_config_does_invalidate_it(self) -> None:
        a = {"kind": "infer", "id": "i", "config": {"prompt": "one"}}
        b = {"kind": "infer", "id": "i", "config": {"prompt": "two"}}
        assert spec_region_hash(a) != spec_region_hash(b)

    def test_hashing_is_key_order_independent(self) -> None:
        assert inputs_hash({"a": 1, "b": 2}) == inputs_hash({"b": 2, "a": 1})


class TestRetry:
    async def test_a_retryable_failure_retries_within_its_budget(self) -> None:
        attempts = {"n": 0}

        async def flaky(prompt, *, use_case="background", output_type=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("network unreachable")
            return "recovered"

        spec = {
            "name": "rt",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "go", "retry": {"max_attempts": 3}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=flaky))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert attempts["n"] == 3

    async def test_a_non_retryable_failure_is_tried_exactly_once(self) -> None:
        """Retrying a permission error burns budget to reach the same failure."""
        attempts = {"n": 0}

        async def denied(prompt, *, use_case="background", output_type=None):
            attempts["n"] += 1
            raise PermissionError("unauthorized")

        spec = {
            "name": "nort",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "go", "retry": {"max_attempts": 5}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=denied))
        assert await c.run_to_completion(timeout=25) == RunStatus.FAILED
        assert attempts["n"] == 1

    async def test_exhausted_retries_journal_retries_exhausted(self) -> None:
        async def always_flaky(prompt, *, use_case="background", output_type=None):
            raise ConnectionError("network down")

        spec = {
            "name": "ex",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "go", "retry": {"max_attempts": 2}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=always_flaky))
        await c.run_to_completion(timeout=25)
        failures = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_FAILED]
        assert any(r.get("retries_exhausted") for r in failures)
        assert failures[-1]["failure_signature"]["failing_node"] == "i"

    async def test_no_retry_modes_blocks_a_class_that_would_otherwise_retry(self) -> None:
        attempts = {"n": 0}

        async def flaky(prompt, *, use_case="background", output_type=None):
            attempts["n"] += 1
            raise ConnectionError("network unreachable")

        spec = {
            "name": "nrm",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {
                    "prompt": "go",
                    "retry": {"max_attempts": 4, "no_retry_modes": ["network"]},
                },
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=flaky))
        await c.run_to_completion(timeout=25)
        assert attempts["n"] == 1


class TestTimeouts:
    """WF2-R5: both knobs proven live, not decorative."""

    async def test_timeout_total_actually_fires(self) -> None:
        async def hang(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(30)
            return "never"

        spec = {"name": "to", "root": {"kind": "infer", "id": "i", "config": {"prompt": "go"}}}
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(completion=hang, node_timeout_total=1, node_timeout_stall=0),
        )
        started = time.time()
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert time.time() - started < 10
        assert c.instances["root"].failure.failure_class == FailureClass.TIMEOUT

    async def test_a_long_but_progressing_node_is_not_killed(self) -> None:
        async def slow(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(1.5)
            return "finished slowly"

        spec = {"name": "ok", "root": {"kind": "infer", "id": "i", "config": {"prompt": "go"}}}
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(completion=slow, node_timeout_total=30, node_timeout_stall=20),
        )
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE


class TestPerNodeStallWindow:
    """🔴 `timeout_stall_secs` was DECLARED BY FOUR SHIPPED TEMPLATES AND READ BY NOTHING (S147).

    `design-project.refine` asks 600s, `general-project.project` 900s,
    `goal-pursuit-open-ended.work` 900s and `goal-pursuit-verifiable.work` 1200s. But
    `_enforce_stall_timeouts` consulted only `services.node_timeout_stall`, so every one silently
    got the 300s default. That fails in the WRONG DIRECTION: `timeout_stall` is meant to catch a
    SILENT node, not a slow one, and a node whose author measured it needing 20 minutes was
    cancelled at 5.
    """

    def _window(self, cfg: dict, *, run_default: int = 300) -> int:
        from personalclaw.workflows.controller import RunController

        class _Svc:
            node_timeout_stall = run_default

        class _Fake:
            services = _Svc()

            def __init__(self, root):
                self.root = root

        _Fake._node_stall_window = RunController._node_stall_window
        root = Node.from_dict(
            {
                "kind": "sequence",
                "id": "r",
                "children": [{"kind": "stage", "id": "work", "config": cfg}],
            }
        )
        return _Fake(root)._node_stall_window("root.children[0]")

    def test_a_node_can_RAISE_its_own_window(self) -> None:
        assert self._window({"timeout_stall_secs": 1200}) == 1200
        assert self._window({"timeout_stall_secs": 600}) == 600

    def test_a_node_can_NOT_lower_it_below_the_run_default(self) -> None:
        """The run-level value is the operator's floor for how long a silent node may sit; letting a
        bundled template shorten it would let a spec tighten an operator's policy."""
        assert self._window({"timeout_stall_secs": 60}) == 300

    @pytest.mark.parametrize("raw", [0, -5, "x", None, ""])
    def test_an_absent_or_invalid_value_falls_back_to_the_default(self, raw) -> None:
        """Never DISABLES the check — a malformed knob must not switch a safety timeout off."""
        cfg = {} if raw is None else {"timeout_stall_secs": raw}
        assert self._window(cfg) == 300

    def test_an_unknown_path_falls_back_rather_than_raising(self) -> None:
        from personalclaw.workflows.controller import RunController

        class _Svc:
            node_timeout_stall = 300

        class _Fake:
            services = _Svc()

            def __init__(self, root):
                self.root = root

        _Fake._node_stall_window = RunController._node_stall_window
        root = Node.from_dict({"kind": "sequence", "id": "r", "children": []})
        assert _Fake(root)._node_stall_window("root.children[9]") == 300

    def test_every_shipped_template_override_is_now_honoured(self) -> None:
        """The four real declarations, read from the bundled library rather than restated — a
        hand-copied list would stop tracking the templates it is meant to protect."""
        import json
        import pathlib

        declared: list[tuple[str, int]] = []
        for path in sorted(
            pathlib.Path("src/personalclaw/workflows/bundled").glob("*/workflow.json")
        ):
            spec = json.loads(path.read_text())

            def walk(node: object) -> None:
                if isinstance(node, dict):
                    cfg = node.get("config")
                    if isinstance(cfg, dict) and "timeout_stall_secs" in cfg:
                        declared.append((path.parent.name, int(cfg["timeout_stall_secs"])))
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(spec)

        assert (
            declared
        ), "no bundled template declares timeout_stall_secs — did the key get renamed?"
        for name, want in declared:
            assert self._window({"timeout_stall_secs": want}) == want, name


class TestWaitAndGates:
    async def test_a_wait_resolves_at_its_deadline(self) -> None:
        """The controller resolves it rather than re-dispatching: a dispatcher is
        stateless, so re-entry would recompute `now + duration` and wait forever."""
        spec = {"name": "w", "root": {"kind": "wait", "id": "w", "config": {"duration_secs": 1}}}
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        started = time.time()
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert 0.8 <= time.time() - started < 8

    async def test_an_unanswered_gate_surfaces_needs_input(self) -> None:
        spec = {
            "name": "g",
            "root": {"kind": "gate", "id": "g", "config": {"kind": "approval", "prompt": "ok?"}},
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.NEEDS_INPUT
        assert c.run.attention["kind"] == "approval"

    async def test_a_wait_deadline_survives_a_restart(self) -> None:
        """Found by driving a real run: the deadline lived only in the controller's
        memory, so a restart left every waiting run parked forever with nothing scheduled
        to wake it — the run reported `needs_input` and never recovered."""
        spec = {
            "name": "wpersist",
            "root": {"kind": "wait", "id": "w", "config": {"duration_secs": 300}},
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        await c.start()
        for _ in range(40):
            await asyncio.sleep(0.1)
            if c.instances.get("root", NodeInstance("root")).state == InstanceState.WAITING:
                break
        await c.stop()

        on_disk = store.read_state(run.id)["root"]
        assert on_disk.state == InstanceState.WAITING
        assert on_disk.wake_at > 0, "the deadline was not persisted"

        # A fresh controller (a restarted gateway) must still know when to wake it.
        c2 = RunController(store.get(run.id), spec, services=EngineServices())
        assert c2._next_wake_delay() is not None

    async def test_a_woken_wait_persists_its_output(self) -> None:
        """Also found live: the output was written to memory only, so after a restart a
        binding on the wait node resolved to None."""
        spec = {
            "name": "wout",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "wait", "id": "w", "config": {"duration_secs": 1}},
                    {
                        "kind": "transform",
                        "id": "after",
                        "config": {"expr": "{{nodes.w.output}}"},
                    },
                ],
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert store.read_output(run.id, "root.children[0]") == {"waited": True}
        assert store.read_state(run.id)["root.children[0]"].output_ref

    async def test_a_timed_out_gate_fails_rather_than_wedging(self) -> None:
        """An unattended run must surface this, and it is NOT a pass — nobody approved
        anything (WF2-R7)."""
        spec = {
            "name": "gt",
            "root": {"kind": "gate", "id": "g", "config": {"kind": "approval", "timeout_secs": 1}},
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert c.instances["root"].failure.terminal_reason == "timed_out_unattended"


class TestCancellation:
    async def test_a_sticky_cancel_intent_terminates_the_run(self) -> None:
        async def slow(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(10)
            return "x"

        spec = {
            "name": "c",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "1"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "2"}},
                ],
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=slow))
        await c.start()
        await asyncio.sleep(0.2)
        c.request_cancel()
        assert await c.run_to_completion(timeout=20) == RunStatus.CANCELLED

    async def test_the_intent_survives_with_no_controller_running(self) -> None:
        """A cancel issued while the gateway is down must still be honoured, so it is a
        file rather than in-memory state."""
        run = _make_run(SEQ_SPEC)
        store.request_cancel(run.id)
        assert store.cancel_requested(run.id)
        store.clear_cancel(run.id)
        assert not store.cancel_requested(run.id)

    async def test_stop_leaves_a_run_resumable_rather_than_failed(self) -> None:
        """A gateway shutdown is a process event, not a run outcome."""

        async def slow(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(10)
            return "x"

        spec = {"name": "s", "root": {"kind": "infer", "id": "i", "config": {"prompt": "p"}}}
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=slow))
        await c.start()
        await asyncio.sleep(0.2)
        await c.stop()
        assert store.get(run.id).status == RunStatus.RUNNING


class TestBudget:
    async def test_a_breach_pauses_resumably_rather_than_failing(self) -> None:
        """SOFT budgets: killing the run would discard completed work the user paid for."""

        async def chatty(prompt, *, use_case="background", output_type=None):
            return "x" * 4000

        spec = {
            "name": "b",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": f"n{i}", "config": {"prompt": "p"}} for i in range(4)
                ],
            },
        }
        run = _make_run(spec, budget=RunBudget(max_tokens=100))
        c = RunController(run, spec, services=EngineServices(completion=chatty))
        assert await c.run_to_completion(timeout=25) == RunStatus.PAUSED
        assert "budget" in c.run.error_message

    async def test_resume_pre_charges_from_the_ledger(self) -> None:
        """WF2-R4 invariant #1: without this a crash loop mints a fresh budget every
        restart and spends without bound."""
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        spent = store.get(run.id).total_tokens
        assert spent > 0

        fresh = store.get(run.id)
        fresh.total_tokens = 0  # simulate a row that lost its counter
        fresh.status = RunStatus.RUNNING
        c2 = RunController(fresh, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c2._prepare()
        assert c2.run.total_tokens >= spent


class TestRedaction:
    async def test_a_credential_in_node_output_never_reaches_disk(self) -> None:
        """The journal is read by the flywheel, shipped in bug reports, and rendered in a
        UI — a credential reaching it leaks to all three."""
        secret = "sk-" + "a" * 40

        async def leaky(prompt, *, use_case="background", output_type=None):
            return f"your key is {secret} keep it safe"

        spec = {
            "name": "red",
            "root": {"kind": "infer", "id": "i", "config": {"prompt": "give me a key"}},
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=leaky))
        await c.run_to_completion(timeout=20)
        blob = "".join(
            p.read_text(errors="replace") for p in store.run_dir(run.id).rglob("*") if p.is_file()
        )
        assert secret not in blob

    def test_redact_walks_nested_structures(self) -> None:
        secret = "ghp_" + "b" * 36
        out = J.redact({"a": [{"token": secret}], "b": secret})
        assert secret not in str(out)

    def test_every_journal_record_is_redacted_on_the_way_out(self) -> None:
        """The write path itself, not just node outputs. A ledger field carrying a
        credential — a failure message quoting a request header, a resolved prompt with an
        inlined key — leaks exactly as badly as an output would."""
        secret = "sk-" + "c" * 40
        jr = Journal("redwrite")
        jr.step_failed(
            "root",
            "n",
            epoch=0,
            failure=Failure(cause_plain=f"auth failed for {secret}"),
        )
        jr.write("custom", nested={"deep": [secret]})
        for name in (J.JOURNAL_FILE, J.EVENTS_FILE):
            for rec in store.read_jsonl("redwrite", name):
                assert secret not in str(rec), name

    def test_the_returned_record_is_the_redacted_one(self) -> None:
        """`write()` returns what it persisted, so a caller echoing the record to an event
        stream cannot re-leak what the journal just scrubbed."""
        secret = "xoxb-" + "d" * 30
        rec = Journal("redret").write("custom", token=secret)
        assert secret not in str(rec)

    def test_redaction_leaves_ordinary_text_alone(self) -> None:
        assert J.redact("just a normal sentence") == "just a normal sentence"


class TestLedger:
    async def test_step_completed_carries_every_flywheel_required_field(self) -> None:
        """These are emission REQUIREMENTS — a downstream refiner is starved without
        them."""
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        rec = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_COMPLETED][0]
        for f in (
            "node_id",
            "instance_path",
            "duration_secs",
            "tokens",
            "retries",
            "model",
            "provider",
            "cost_usd",
            "resolved_prompt_ref",
            "epoch",
        ):
            assert f in rec, f

    async def test_the_resolved_prompt_is_journaled_for_replay(self) -> None:
        """Acceptance bar: prompt → output must be reconstructable from the ledger."""
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        infer = [
            r for r in J.ledger(run.id) if r["kind"] == J.STEP_COMPLETED and r["node_id"] == "think"
        ][0]
        ref = infer["resolved_prompt_ref"]
        assert ref
        assert store.read_output(run.id, "root.children[1]::prompt") == "double 7"

    async def test_event_ids_are_deterministic_so_a_re_emit_is_idempotent(self) -> None:
        jr = Journal("evt")
        a = jr.write("x")
        b = jr.write("x")
        assert a["event_id"] == "evt-evt-1"
        assert b["event_id"] == "evt-evt-2"

    async def test_ledger_kinds_land_in_both_files(self) -> None:
        jr = Journal("both")
        jr.step_skipped("root", "n", epoch=0)
        assert len(store.read_jsonl("both", J.JOURNAL_FILE)) == 1
        assert len(store.read_jsonl("both", J.EVENTS_FILE)) == 1

    async def test_a_non_ledger_kind_stays_out_of_events(self) -> None:
        jr = Journal("one")
        jr.write(J.STEP_STARTED, instance_path="root")
        assert len(store.read_jsonl("one", J.JOURNAL_FILE)) == 1
        assert store.read_jsonl("one", J.EVENTS_FILE) == []

    async def test_run_totals_aggregate_the_ledger(self) -> None:
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        totals = J.run_totals(run.id)
        assert totals["steps_completed"] == 3
        assert totals["steps_failed"] == 0
        assert totals["tokens"] > 0

    async def test_an_oversized_output_spills_and_leaves_a_typed_stub(self) -> None:
        """A reader must be able to tell the data exists rather than parsing a truncated
        string."""
        jr = Journal("big")
        ref, preview = jr.store_output("root", "x" * (J.MAX_INLINE_OUTPUT_BYTES + 100))
        assert isinstance(preview, dict)
        assert preview["result_omitted"] is True
        assert preview["output_ref"] == ref
        assert len(store.read_output("big", "root")) > J.MAX_INLINE_OUTPUT_BYTES


class TestBranchAndJoinIntegration:
    async def test_an_untaken_branch_does_not_deadlock_a_live_run(self) -> None:
        """WF2-R18 regression #1, end to end."""
        spec = {
            "name": "j",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {
                        "kind": "branch",
                        "id": "router",
                        "config": {"on": "{{inputs.kind}}"},
                        "cases": {
                            "bug": {"kind": "transform", "id": "fix", "config": {"expr": "fixed"}},
                            "feat": {
                                "kind": "transform",
                                "id": "build",
                                "config": {"expr": "built"},
                            },
                        },
                    },
                    {
                        "kind": "transform",
                        "id": "merge",
                        "needs": ["router"],
                        "config": {"expr": "merged after {{nodes.router.output.case}}"},
                    },
                ],
            },
        }
        run = _make_run(spec, {"kind": "bug"})
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert c._outputs["merge"] == "merged after bug"
        assert "build" not in c._outputs
        assert c.instances["root.children[0].cases[feat]"].state == InstanceState.SKIPPED

    async def test_an_async_fan_out_join_waits_for_the_slowest_leg(self) -> None:
        """WF2-R18 regression #2, end to end: the timing IS the assertion."""
        spec = {
            "name": "f",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "fast", "config": {"expr": "quick"}},
                    {"kind": "wait", "id": "slow1", "config": {"duration_secs": 1}},
                    {"kind": "wait", "id": "slow2", "config": {"duration_secs": 2}},
                    {
                        "kind": "transform",
                        "id": "join",
                        "needs": ["fast", "slow1", "slow2"],
                        "config": {"expr": "joined {{nodes.fast.output}}"},
                    },
                ],
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        started = time.time()
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert time.time() - started >= 1.8, "the join fired before the slow legs finished"
        assert c._outputs["join"] == "joined quick"

    async def test_a_skipped_subtree_is_skipped_all_the_way_down(self) -> None:
        """Skipping only the case root would leave its children pending, and the derived
        container state would then read the branch as unfinished forever."""
        spec = {
            "name": "sub",
            "root": {
                "kind": "branch",
                "id": "r",
                "config": {"on": "{{inputs.k}}"},
                "cases": {
                    "a": {"kind": "transform", "id": "ca", "config": {"expr": "1"}},
                    "b": {
                        "kind": "sequence",
                        "id": "cb",
                        "children": [
                            {"kind": "transform", "id": "x", "config": {"expr": "2"}},
                            {"kind": "transform", "id": "y", "config": {"expr": "3"}},
                        ],
                    },
                },
            },
        }
        run = _make_run(spec, {"k": "a"})
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        skipped = [p for p, i in c.instances.items() if i.state == InstanceState.SKIPPED]
        assert "root.cases[b]" in skipped
        assert "root.cases[b].children[0]" in skipped
        assert "root.cases[b].children[1]" in skipped


class TestForeachAndLoopIntegration:
    async def test_a_foreach_runs_one_body_instance_per_item(self) -> None:
        spec = {
            "name": "fe",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "seed", "config": {"expr": ["a", "b", "c"]}},
                    {
                        "kind": "foreach",
                        "id": "fan",
                        "config": {"items": "{{nodes.seed.output}}"},
                        "body": {"kind": "infer", "id": "w", "config": {"prompt": "do {{item}}"}},
                    },
                ],
            },
        }
        run = _make_run(spec)
        fn = _echo()
        c = RunController(run, spec, services=EngineServices(completion=fn))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert sorted(fn.calls) == ["do a", "do b", "do c"]

    async def test_a_counted_loop_runs_exactly_n_iterations(self) -> None:
        spec = {
            "name": "lp",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 3},
                "body": {"kind": "transform", "id": "b", "config": {"expr": "i{{iter}}"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert len([p for p in c.instances if "@" in p]) == 3

    async def test_until_dry_exits_on_a_clean_sweep(self) -> None:
        calls = {"n": 0}

        async def sweeper(prompt, *, use_case="background", output_type=None):
            calls["n"] += 1
            return "" if calls["n"] >= 2 else "found something"

        spec = {
            "name": "dry",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "until_dry", "streak": 1, "max_iterations": 6},
                "body": {"kind": "infer", "id": "b", "config": {"prompt": "sweep {{iter}}"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=sweeper))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert calls["n"] == 2

    async def test_loop_iterations_are_journaled_for_the_circuit_breaker(self) -> None:
        """N identical error signatures in a row is a thrash the breaker can detect at
        zero LLM cost — it needs these records to do it."""
        spec = {
            "name": "it",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 2},
                "body": {"kind": "transform", "id": "b", "config": {"expr": "x"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        await c.run_to_completion(timeout=25)
        iters = [r for r in J.ledger(run.id) if r["kind"] == J.ITERATION]
        assert len(iters) == 2
        assert {r["iteration"] for r in iters} == {0, 1}


class TestLaneEnforcement:
    async def test_a_lane_cap_serializes_over_capacity_work(self) -> None:
        order: list[tuple[str, str]] = []

        async def track(prompt, *, use_case="background", output_type=None):
            order.append(("start", prompt))
            await asyncio.sleep(0.3)
            order.append(("end", prompt))
            return "x"

        spec = {
            "name": "ln",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "pa"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "pb"}},
                ],
            },
        }
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(
                completion=track, lane_limits=Limits(lanes={"llm": 1, "io": 1, "compute": 8})
            ),
        )
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert order[0][0] == "start" and order[1][0] == "end"

    async def test_independent_lanes_do_not_block_each_other(self) -> None:
        """The whole point of typed lanes: a saturated io lane must not stall llm work."""
        spec = {
            "name": "mix",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "i", "config": {"prompt": "p"}},
                    {"kind": "transform", "id": "t", "config": {"expr": "1"}},
                ],
            },
        }
        run = _make_run(spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(
                completion=_echo(), lane_limits=Limits(lanes={"llm": 1, "io": 1, "compute": 8})
            ),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE


class TestResilienceIntegration:
    """Slice 2's mechanisms as the controller actually drives them."""

    async def test_a_retry_receives_the_correction_hint(self) -> None:
        """The whole mechanism: a blind retry reproduces the same failure, so the next
        attempt must be told what went wrong."""
        prompts: list[str] = []

        async def flaky(prompt, *, use_case="background", output_type=None):
            prompts.append(prompt)
            if len(prompts) == 1:
                raise ConnectionError("network unreachable")
            return "recovered"

        spec = {
            "name": "hint",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "do the thing", "retry": {"max_attempts": 3}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=flaky))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        assert len(prompts) == 2
        assert "PREVIOUS ATTEMPTS FAILED" in prompts[1]
        assert "CORRECTION:" in prompts[1]

    async def test_a_foreach_retry_does_not_leak_into_sibling_prompts(self) -> None:
        """The spec node is shared across every item, so the hint must go on a COPY."""
        prompts: list[str] = []
        failed_once = {"done": False}

        async def flaky(prompt, *, use_case="background", output_type=None):
            prompts.append(prompt)
            if "b" in prompt and not failed_once["done"]:
                failed_once["done"] = True
                raise ConnectionError("network unreachable")
            return "ok"

        spec = {
            "name": "fehint",
            "root": {
                "kind": "foreach",
                "id": "f",
                "config": {"items": ["a", "b", "c"], "on_item_error": "skip"},
                "body": {
                    "kind": "infer",
                    "id": "w",
                    "config": {"prompt": "item {{item}}", "retry": {"max_attempts": 2}},
                },
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=flaky))
        await c.run_to_completion(timeout=30)
        polluted = [p for p in prompts if "item a" in p and "PREVIOUS ATTEMPTS" in p]
        assert not polluted, "one item's failure leaked into a sibling's prompt"

    async def test_attempt_records_reach_the_ledger(self) -> None:
        async def always_flaky(prompt, *, use_case="background", output_type=None):
            raise ConnectionError("network down")

        spec = {
            "name": "att",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "go", "retry": {"max_attempts": 3}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=always_flaky))
        await c.run_to_completion(timeout=25)
        attempts = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_ATTEMPT]
        assert len(attempts) >= 1
        assert attempts[0]["failure_class"] == "network"
        assert attempts[0]["error_signature"]

    async def test_exhausted_retries_produce_the_escalation_artifact(self) -> None:
        """Not just a failure: five named options let a human act."""

        async def always_flaky(prompt, *, use_case="background", output_type=None):
            raise ConnectionError("network down")

        spec = {
            "name": "esc",
            "root": {
                "kind": "infer",
                "id": "i",
                "config": {"prompt": "go", "retry": {"max_attempts": 2}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=always_flaky))
        await c.run_to_completion(timeout=25)
        esc = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_ESCALATED]
        assert len(esc) == 1
        assert esc[0]["reason"] == "retries_exhausted"
        assert len(esc[0]["options"]) == 5
        assert c.run.attention["kind"] == "escalation"

    async def test_the_breaker_stops_a_thrashing_loop_with_no_model_calls(self) -> None:
        """A 20-iteration loop returning identical output must not run 20 times."""
        spec = {
            "name": "thrash",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 20, "identical_streak": 2},
                "body": {"kind": "transform", "id": "b", "config": {"expr": "same every time"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=30) == RunStatus.ESCALATED
        iterations = [p for p in c.instances if "@" in p]
        assert len(iterations) <= 4, f"the thrash ran {len(iterations)} times"
        assert c.instances["root"].state == InstanceState.ESCALATED

    async def test_escalated_is_distinct_from_failed(self) -> None:
        """ "I gave up, a human must decide" is a different fact from "this broke"."""
        spec = {
            "name": "esc2",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 10, "identical_streak": 2},
                "body": {"kind": "transform", "id": "b", "config": {"expr": "x"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        status = await c.run_to_completion(timeout=30)
        assert status == RunStatus.ESCALATED
        assert status != RunStatus.FAILED

    async def test_the_budget_warning_fires_once_before_the_cap(self) -> None:
        warnings: list[dict] = []

        async def chatty(prompt, *, use_case="background", output_type=None):
            return "x" * 400

        spec = {
            "name": "warn",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": f"n{i}", "config": {"prompt": "p"}} for i in range(6)
                ],
            },
        }
        run = _make_run(spec, budget=RunBudget(max_tokens=400))
        c = RunController(
            run,
            spec,
            services=EngineServices(
                completion=chatty,
                publish=lambda e, p: warnings.append(p) if p.get("budget_warning") else None,
            ),
        )
        assert await c.run_to_completion(timeout=30) == RunStatus.PAUSED
        assert len(warnings) == 1, "the warning must fire exactly once per run"


class TestProjectOverviewOnComplete:
    """The completion hook (WORK-CONTAINERS §6.1): a COMPLETE run auto-revises its
    project's living overview and appends a decisions-ledger line — DETERMINISTICALLY (an
    appended line, not an LLM summary; DEVIATION recorded in the plan log). The hook is
    best-effort: `_finish` is the single terminal writer and must never raise.
    """

    @pytest.fixture(autouse=True)
    def _project_home(self, _isolated_home, monkeypatch):
        # The project store binds `config_dir` BY VALUE at import (from config.loader), so
        # patching the loader module alone leaves it pointed at the real home. Patch both
        # the loader and the hierarchy module's own binding — project_context resolves the
        # context dir through HierarchyStore, so that one is the load-bearing patch.
        import personalclaw.config.loader as cfg
        import personalclaw.tasks.hierarchy as hierarchy

        monkeypatch.setattr(cfg, "config_dir", lambda: _isolated_home)
        monkeypatch.setattr(hierarchy, "config_dir", lambda: _isolated_home)
        return _isolated_home

    def _project(self):
        from personalclaw.tasks.hierarchy import HierarchyStore

        return HierarchyStore().create_project("Board Test", brief="ship the board")

    async def test_a_completed_run_appends_to_the_overview_and_ledger(self) -> None:
        from personalclaw import project_context

        project = self._project()
        run = _make_run(SEQ_SPEC, project_id=project.id)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        overview = project_context.read_overview(project.id)
        assert "seq" in overview and "complete" in overview
        decisions = project_context.read_ledger(project.id, "decisions")
        assert any("seq" in line and "complete" in line for line in decisions)

    async def test_a_run_with_no_project_writes_nothing(self) -> None:
        # project_id is empty → the hook is a no-op (the guard), and the run still completes.
        run = _make_run(SEQ_SPEC)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

    async def test_a_raising_overview_writer_does_not_break_finish(self, monkeypatch) -> None:
        from personalclaw import project_context

        project = self._project()

        def boom(*a, **k):
            raise RuntimeError("overview store is down")

        monkeypatch.setattr(project_context, "write_overview", boom)
        run = _make_run(SEQ_SPEC, project_id=project.id)
        c = RunController(run, SEQ_SPEC, services=EngineServices(completion=_echo()))
        # `_finish` must not raise despite the broken writer — the run still completes.
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

    async def test_a_failed_run_does_not_revise_the_overview(self) -> None:
        from personalclaw import project_context

        project = self._project()

        async def denied(prompt, *, use_case="background", output_type=None):
            from personalclaw.workflows.models import Failure, FailureClass

            raise Failure(FailureClass.PERMISSION_DENIED, "nope")

        spec = {
            "name": "failing",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "infer", "id": "n", "config": {"prompt": "p"}}],
            },
        }
        run = _make_run(spec, project_id=project.id)
        c = RunController(run, spec, services=EngineServices(completion=denied))
        assert await c.run_to_completion(timeout=25) == RunStatus.FAILED
        # Only COMPLETE revises the overview; a FAILED run leaves it empty.
        assert project_context.read_overview(project.id) == ""


class TestWorkspaceProvisioningAtRunStart:
    """The §4.1 run-start hook (WF2WOR-4). `_prepare` is where a spec's `workspace:` block stops
    being a declaration nobody reads and becomes the directory the run's stages work in.

    The load-bearing properties, each of which fails silently without a test:

    * a FATAL declaration REFUSES the run rather than running in an unchosen mode (the
      ignored-fatal-issue shape this program keeps finding);
    * a spec with NO block provisions nothing — a workspace is a declaration, not a default, and
      defaulting it took every crash-survivor run out of the adoption path;
    * `services.cwd` is REPOINTED at the workspace, or the isolation would be a directory nothing
      ran in.
    """

    @pytest.fixture(autouse=True)
    def _home(self, _isolated_home, monkeypatch):
        import personalclaw.config.loader as cfg
        import personalclaw.tasks.hierarchy as hierarchy

        monkeypatch.setattr(cfg, "config_dir", lambda: _isolated_home)
        monkeypatch.setattr(hierarchy, "config_dir", lambda: _isolated_home)
        monkeypatch.setenv("PERSONALCLAW_HOME", str(_isolated_home))
        return _isolated_home

    @staticmethod
    def _spec(workspace: dict | None) -> dict:
        spec: dict = {
            "name": "wsrun",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "transform", "id": "a", "config": {"expr": {"n": 1}}}],
            },
        }
        if workspace is not None:
            spec["workspace"] = workspace
        return spec

    async def test_an_unknown_MODE_refuses_the_run(self) -> None:
        """`parse_workspace` marks it fatal because defaulting would run in a mode nobody chose —
        and `in_place` touches the real tree. Refused through `_finish`, the single terminal
        writer, so the row is honest rather than left RUNNING forever."""
        spec = self._spec({"mode": "kubernetes"})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert "workspace declaration refused" in run.error_message
        # No node ran: the refusal happened before the first tick scheduled anything.
        assert all(i.state == InstanceState.PENDING for i in c.instances.values())

    async def test_a_greedy_preserve_pattern_refuses_the_run(self) -> None:
        """`**` copies the whole tree into the workspace it is being isolated FROM, which defeats
        the isolation — so it is fatal, not a warning."""
        spec = self._spec({"mode": "worktree", "preserve_patterns": ["**"]})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert "preserve pattern" in run.error_message

    async def test_a_spec_with_NO_workspace_block_provisions_nothing(self) -> None:
        """Measured: provisioning every run made every stale RUNNING run look isolated to the boot
        sweep, so a journal-resumable crash-survivor would be SUSPENDED instead of adopted."""
        spec = self._spec(None)
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo(), cwd="/tmp"))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert "worktree_path" not in run.extra
        assert "workspace" not in run.extra
        assert c.services.cwd == "/tmp", "an undeclared workspace leaves the cwd alone"

    async def test_a_declared_scratch_workspace_lands_on_the_record_and_the_cwd(self) -> None:
        """`worktree_path` had a live READER (`watchdog._substrate_for`) and zero writers before
        this atom. The cwd repoint is what makes the isolation real rather than decorative."""
        import os

        spec = self._spec({"mode": "scratch"})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE

        path = run.extra["worktree_path"]
        assert path and os.path.isdir(path)
        assert c.services.cwd == path, "the stages ran IN the workspace"
        assert run.extra["workspace"]["mode"] == "scratch"
        assert run.extra["workspace"]["isolated"] is True
        # It round-trips on disk, which is what a restart replays.
        assert store.get(run.id).extra["worktree_path"] == path

    async def test_setup_runs_through_the_INJECTED_runner_never_a_real_subprocess(self) -> None:
        """`teardown_runner` is the established injection seam (its docstring: "injected so tests
        never run real teardown subprocesses"). Reused for setup rather than adding a second
        seam — two injection points would let one path escape into a real spawn."""
        spec = self._spec({"mode": "scratch", "setup": "npm ci\nmake build"})
        run = _make_run(spec)
        seen: list[tuple[str, str]] = []

        async def runner(command: str, cwd: str) -> tuple[bool, str]:
            seen.append((command, cwd))
            return True, "ok"

        c = RunController(
            run, spec, services=EngineServices(completion=_echo(), teardown_runner=runner)
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert [s[0] for s in seen] == ["npm ci", "make build"]
        assert run.extra["workspace"]["setup"]["ran"] == ["npm ci", "make build"]

    async def test_a_setup_FAILURE_still_completes_the_run(self) -> None:
        """S52's contract, driven end to end: `blocked_run` is False by construction, so a failed
        `npm ci` costs an explanation on the record and not the run."""
        spec = self._spec({"mode": "scratch", "setup": "npm ci"})
        run = _make_run(spec)

        async def runner(command: str, cwd: str) -> tuple[bool, str]:
            return False, "ENOTFOUND registry.example"

        c = RunController(
            run, spec, services=EngineServices(completion=_echo(), teardown_runner=runner)
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert run.extra["workspace"]["setup"]["failed"], "the failure is recorded"
        assert run.extra["workspace"]["setup"]["blocked_run"] is False

    async def test_the_provisioning_outcome_is_JOURNALLED(self) -> None:
        """A run that fell back from `worktree` to scratch behaves differently from one that got
        the isolation it asked for, and a refiner reading the ledger cannot tell them apart
        without the record."""
        spec = self._spec({"mode": "scratch"})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        await c.run_to_completion(timeout=20)
        kinds = [e.get("kind") for e in J.ledger(run.id)]
        assert J.WORKSPACE_PROVISIONED in kinds

    async def test_a_provisioning_CRASH_costs_the_isolation_not_the_run(self, monkeypatch) -> None:
        """Guarded on everything except the deliberate refusal: a run that could not start because
        a `mkdir` failed would be strictly worse than one that runs in the project workspace."""
        from personalclaw.workflows import provisioning

        async def boom(*a, **k):
            raise RuntimeError("the filesystem is on fire")

        monkeypatch.setattr(provisioning, "provision", boom)
        spec = self._spec({"mode": "scratch"})
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=_echo()))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert "worktree_path" not in run.extra
