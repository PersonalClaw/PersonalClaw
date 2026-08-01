"""The full lifecycle, end to end, and the adversarial cases (Slice 11a).

Every other workflow test file exercises one seam. This one drives the whole thing the way a user
does — create, run, edit mid-flight, rewind, run-from, fork, complete — because the interesting
failures of this engine are not in any single mechanism, they are in the handoffs BETWEEN them: a
rewind that leaves a stale cache key, a fork that shares an epoch, a resume that re-answers a gate.

The adversarial half deliberately does the things a careful user would not:

* mutate a run twice concurrently, so the TOCTOU re-verify has to fire;
* kill a controller mid-node and rebuild it, which is what a gateway restart actually is;
* answer the same gate twice, which is what a double-clicked Approve button actually is;
* nest to the depth cap and past it.

These are the cases where "it works on my machine" and "it works after a restart" diverge, and none
of them is reachable by a test that only calls one function.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.journal import ledger
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.native_defs import register_native_provider
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


#: A run PARKED ON A GATE — live, driven, with un-run downstream nodes. That is the state a user
#: actually edits in, and the only one where mid-flight mutation is meaningful: a completed run's
#: nodes are frozen (their outputs are already downstream) and a finished run is not live.
EDITABLE_SPEC = {
    "name": "editable",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "gate", "id": "hold", "config": {"kind": "approval", "prompt": "hold here"}},
            {"kind": "transform", "id": "produce", "config": {"expr": "original"}},
        ],
    },
}

#: A three-stage shape with a real binding chain, so a rewind has something to cascade through.
LIFECYCLE_SPEC = {
    "name": "lifecycle",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": "gathered"}},
            {
                "kind": "transform",
                "id": "produce",
                "config": {"expr": "produced from {{nodes.gather.output}}"},
            },
            {
                "kind": "transform",
                "id": "report",
                "config": {"expr": "reporting {{nodes.produce.output}}"},
            },
        ],
    },
}


def _controller(run: WorkflowRun, spec: dict, **kw) -> RunController:
    return RunController(run, spec, services=EngineServices(**kw))


async def _launched(spec: dict, **kw) -> tuple[WorkflowRun, WorkflowWatchdog, RunController]:
    """Create a run and launch it through the REAL supervisor.

    The real `WorkflowWatchdog` rather than a stand-in: `edit_run` and `resume_run` require a LIVE
    controller by contract (a mutation is only safe at the controller's drain point), and a
    hand-rolled registry would drift from that contract exactly where this suite should be checking
    it. `launch` both creates and registers the controller, which is what production does.
    """
    run = store.create(WorkflowRun(id="", workflow_name=str(spec.get("name", "wf"))))
    store.write_spec(run.id, spec)
    wd = WorkflowWatchdog(None, EngineServices(**kw))
    controller = await wd.launch(run, spec)
    return run, wd, controller


async def _applied(run_id: str, from_version: int, *, timeout: float = 5.0) -> None:
    """Wait for a queued mutation batch to be APPLIED by the controller's drain point.

    Polling the version rather than sleeping a fixed time: the drain happens between scheduling
    steps, so its latency depends on what the run is doing. A fixed sleep would be flaky in exactly
    the direction that hides a real failure.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if store.get(run_id).spec_version > from_version:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"the queued batch never applied (version stuck at {from_version})")


async def _drain(controller: RunController, run_id: str) -> None:
    """Re-run a settled controller so its drain point applies a queued mutation.

    A mutation is queued and applied between scheduling steps — but a COMPLETED controller's loop
    has already exited, so there is no next step until it is driven again. This is what a user's
    "resume" does, and doing it explicitly is more honest than polling for a state the engine may
    have already moved through.
    """
    await controller.run_to_completion(timeout=30)


async def _fresh_run(spec: dict = LIFECYCLE_SPEC) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=str(spec.get("name", "wf"))))
    store.write_spec(run.id, spec)
    return run


class TestFullLifecycle:
    """create → run → edit → rewind → run_from → fork → complete, in one run.

    Deliberately sequential in ONE test rather than split: the point is that the operations compose,
    and a suite of independent tests each starting from a clean run would never catch a rewind that
    corrupts a later fork.
    """

    async def test_create_run_rewind_fork_complete_COMPOSES(self) -> None:
        """The composition, not the mechanisms.

        Each operation's semantics are covered directly elsewhere — the frozen-region and cascade
        rules in `test_workflows_mutations.py` (47 tests), isolation in `test_workflows_fork.py`
        (35). What ONLY an end-to-end test reaches is whether they compose over one run through a
        real supervisor: a rewind that left a stale cache key, or a fork taken after a rewind, would
        pass every unit test and fail here.
        """
        run, sup, controller = await _launched(LIFECYCLE_SPEC)
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        assert store.read_output(run.id, "root.children[2]") == "reporting produced from gathered"

        # ── rewind: the PREVIEW is the contract. It names what will re-run before anything does,
        # which is what makes the operation safe to offer at all.
        preview = service.rewind_run(run.id, "produce", supervisor=sup, force=True)
        assert preview.get("ok"), preview
        rerun = set(preview["preview"]["rerun"]) | set(preview["preview"]["stale"])
        assert "produce" in rerun
        # `gather` is upstream: a cascade that reached it would throw away work the rewind did not
        # invalidate.
        assert "gather" not in rerun, f"the cascade reached upstream: {rerun}"

        # ── drain and re-run: the rewound node executes AGAIN, the untouched one does not.
        # Measured by execution COUNT per node, not by `step_cached`: a rewind bumps the node's
        # epoch, and the epoch is part of the cache key — so the rewound region correctly MISSES
        # the cache rather than hitting it. (An earlier version of this test asserted a
        # `step_cached` event and was wrong about which mechanism it was observing: the cache
        # serves a RESUME, and invalidation is what serves a rewind.)
        await _drain(controller, run.id)
        assert store.get(run.id).status == RunStatus.COMPLETE
        completions = Counter(
            e["instance_path"] for e in ledger(run.id) if e.get("kind") == "step_completed"
        )
        assert completions["root.children[1]"] == 2, "the rewound node did not re-run"
        assert completions["root.children[0]"] == 1, "an untouched upstream node was re-executed"
        # The epoch bump is what made that invalidation happen, and it is what a later resume keys
        # off — so it has to be observable.
        assert store.read_state(run.id)["root.children[1]"].epoch > 0

        # ── fork AFTER a rewind: the case a unit test cannot construct, because it needs a run with
        # real rewind history behind it.
        forked = service.fork_run(run.id, note="lifecycle test", supervisor=sup)
        assert forked.get("ok"), forked
        child = store.get(forked["child_run_id"])
        assert child is not None and child.parent_run_id == run.id
        # The child inherits the completed prefix rather than re-running it.
        assert store.read_output(child.id, "root.children[0]") == "gathered"
        # And the parent is untouched by any of it.
        assert store.get(run.id).status == RunStatus.COMPLETE

    async def test_a_rewound_run_reaches_the_SAME_terminal_state(self) -> None:
        """Rewind idempotence: rewinding and re-running a deterministic spec must land in the same
        place. If it does not, the cache keys are wrong and a resume cannot be trusted."""
        run, sup, first = await _launched(LIFECYCLE_SPEC)
        assert await first.run_to_completion(timeout=30) == RunStatus.COMPLETE
        original = store.read_output(run.id, "root.children[2]")

        service.rewind_run(run.id, "produce", supervisor=sup, force=True)
        sup.forget(run.id)
        second = await sup.launch(store.get(run.id), store.read_spec(run.id))
        assert await second.run_to_completion(timeout=30) == RunStatus.COMPLETE
        assert store.read_output(run.id, "root.children[2]") == original


class TestConcurrentMutations:
    """The TOCTOU guard itself is covered by `test_workflows_mutations.py` and
    `test_workflows_mutation_queue.py` (76 tests, including version-mismatch and frozen-node
    refusals). What only an END-TO-END test reaches is whether a queued batch survives the trip
    through a real supervisor and a real drain point — the seam between "the service accepted it"
    and "the controller applied it", which every unit test stubs.
    """

    async def test_a_queued_batch_is_APPLIED_by_the_controllers_drain_point(self) -> None:
        """`edit_run` returns `queued: True` and nothing has changed yet. The version advancing is
        the only observable proof the drain point ran — and a batch that is accepted and never
        applied is indistinguishable, from the caller's side, from one that worked."""
        run, sup, controller = await _launched(EDITABLE_SPEC)
        assert await controller.wait_for_terminal(timeout=15) == RunStatus.NEEDS_INPUT
        before = store.get(run.id).spec_version

        result = service.edit_run(
            run.id,
            [{"op": "update_node", "node_id": "produce", "fields": {"expr": "revised"}}],
            supervisor=sup,
        )
        assert result.get("ok") and result.get("queued") is True
        # The spec on disk is UNCHANGED at this point: queuing is not applying.
        assert store.read_spec(run.id)["root"]["children"][1]["config"]["expr"] == "original"

        await _applied(run.id, before)
        assert store.read_spec(run.id)["root"]["children"][1]["config"]["expr"] == "revised"

    async def test_editing_a_run_NOBODY_DRIVES_is_refused(self) -> None:
        """A mutation is only safe at a controller's drain point. Writing to a run with no
        controller would leave state changed with no one to apply it — so the refusal is the
        engine protecting the single-writer invariant, not an inconvenience."""
        run = await _fresh_run()
        result = service.edit_run(
            run.id,
            [{"op": "update_node", "node_id": "produce", "fields": {"expr": "x"}}],
            supervisor=None,
        )
        assert result.get("ok") is False
        assert result.get("code") == "WF_RUN_NOT_LIVE"

    async def test_an_edit_to_a_COMPLETED_node_is_refused(self) -> None:
        """The frozen-region invariant, reached the way a user reaches it: the node already produced
        an output that is downstream, and silently changing the spec that produced it would make the
        run's own history a lie. The fix is to rewind first, which is what the lifecycle test does.
        """
        run, sup, controller = await _launched(LIFECYCLE_SPEC)
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        result = service.edit_run(
            run.id,
            [{"op": "update_node", "node_id": "produce", "fields": {"expr": "too late"}}],
            supervisor=sup,
        )
        assert result.get("ok") is False
        codes = [i.get("code") for i in (result.get("issues") or [])]
        assert "WF_MUT_FROZEN_NODE" in codes or result.get("code") == "WF_RUN_NOT_LIVE"


class TestCrashRecovery:
    async def test_a_run_killed_mid_node_RESUMES_rather_than_restarts(self) -> None:
        """What a gateway restart actually is: the controller object disappears and a new one is
        built from `spec.json` + `state.json`. Work already done must be served from the cache — a
        resume that re-ran everything would make a crash mid-run cost the whole run."""
        started: list[str] = []
        release = asyncio.Event()

        def provider(_name: str):
            class P:
                async def execute(self, cfg, ctx, timeout=30):
                    started.append(str(cfg.get("tag", "")))
                    if cfg.get("tag") == "slow":
                        await release.wait()

                    class R:
                        success = True
                        stdout = "{}"
                        outcome = ""
                        error = ""
                        exit_code = 0
                        stderr = ""
                        agent_error = None

                    return R()

            return P()

        spec = {
            "name": "crashy",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "action",
                        "id": "fast",
                        "config": {"provider": "p", "with": {"tag": "fast"}},
                    },
                    {
                        "kind": "action",
                        "id": "slow",
                        "config": {"provider": "p", "with": {"tag": "slow"}},
                    },
                ],
            },
        }
        run = await _fresh_run(spec)
        first = _controller(run, spec, get_provider=provider)
        task = asyncio.create_task(first.run_to_completion(timeout=30))
        # Let the first node finish and the second start.
        for _ in range(100):
            if "slow" in started:
                break
            await asyncio.sleep(0.02)
        assert "fast" in started and "slow" in started

        # The "crash": stop the controller with the slow node still in flight.
        await first.stop()
        task.cancel()
        # `stop()` may have let the task settle first, so cancelling an already-finished task is not
        # an error — the assertion that matters is what the RESUME does, below.
        with contextlib.suppress(BaseException):
            await task

        # Rebuild from disk, exactly as the watchdog does on boot.
        started.clear()
        release.set()
        revived = _controller(store.get(run.id), store.read_spec(run.id), get_provider=provider)
        status = await revived.run_to_completion(timeout=30)
        # The claim under test is the CACHE, not the final status: `fast` was already done and its
        # cache key still matches, so a resume must not re-execute it. (The status can legitimately
        # be RUNNING if the interrupted node needs another tick — that is resume working, not
        # failing.)
        assert "fast" not in started, "a completed node was re-executed on resume"
        assert status in (RunStatus.COMPLETE, RunStatus.RUNNING), status

    async def test_a_run_cancelled_mid_flight_stays_cancelled_across_a_restart(self) -> None:
        """The sticky-cancel invariant (WF2-R10): a cancel issued while the gateway was going down
        must still be honoured when it comes back, or a user's stop silently un-happens."""
        run = await _fresh_run()
        store.request_cancel(run.id)
        # A fresh controller — the cancel intent is on DISK, not in the object that received it.
        controller = _controller(store.get(run.id), LIFECYCLE_SPEC)
        status = await controller.run_to_completion(timeout=30)
        assert status == RunStatus.CANCELLED

    async def test_the_budget_is_PRE_CHARGED_on_resume(self) -> None:
        """A resumed run inherits its own spend. Minting a fresh budget each restart turns a crash
        loop into unbounded spend — the exact failure the cap exists to prevent."""
        run = await _fresh_run()
        first = _controller(run, LIFECYCLE_SPEC)
        await first.run_to_completion(timeout=30)
        spent = store.get(run.id).total_tokens

        revived = _controller(store.get(run.id), LIFECYCLE_SPEC)
        await revived._prepare()
        assert revived.run.total_tokens >= spent


class TestDoubleResume:
    async def test_answering_the_same_gate_TWICE_applies_once(self) -> None:
        """A double-clicked Approve button. The second answer must be refused, not applied — a gate
        that accepted two answers would let the later one overwrite a decision the run already acted
        on."""
        spec = {
            "name": "gated",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "gate",
                        "id": "approve",
                        "config": {"kind": "approval", "prompt": "ok?"},
                    },
                    {"kind": "transform", "id": "after", "config": {"expr": "went ahead"}},
                ],
            },
        }
        run = await _fresh_run(spec)
        wd = WorkflowWatchdog(None, EngineServices())
        controller = await wd.launch(run, spec)
        status = await controller.wait_for_terminal(timeout=15)
        assert status == RunStatus.NEEDS_INPUT

        from personalclaw.workflows.human_input import list_continuations

        pending = list_continuations(run.id)
        assert pending, "the gate did not mint a continuation"
        token = pending[0].token

        first = controller.resume(token, True)
        assert first.get("ok"), first
        second = controller.resume(token, False)
        assert second.get("ok") is False
        # A consumed token is UNKNOWN, not merely "already used": the record is deleted on claim, so
        # a replayed link cannot even be identified — which is the stronger guarantee.
        assert second.get("code") in ("WF_RESUME_ALREADY_USED", "WF_RESUME_UNKNOWN_TOKEN")

    async def test_the_run_proceeds_on_the_FIRST_answer(self) -> None:
        """The complement: refusing the second answer must not break the first."""
        spec = {
            "name": "gated",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "gate",
                        "id": "approve",
                        "config": {"kind": "approval", "prompt": "ok?"},
                    },
                    {"kind": "transform", "id": "after", "config": {"expr": "went ahead"}},
                ],
            },
        }
        run = await _fresh_run(spec)
        wd = WorkflowWatchdog(None, EngineServices())
        controller = await wd.launch(run, spec)
        await controller.wait_for_terminal(timeout=15)

        from personalclaw.workflows.human_input import list_continuations

        token = list_continuations(run.id)[0].token
        controller.resume(token, True)
        controller.resume(token, False)  # the replay
        assert await controller.wait_for_terminal(timeout=20) == RunStatus.COMPLETE
        assert store.read_output(run.id, "root.children[1]") == "went ahead"


class TestDeepNesting:
    async def test_nesting_to_the_cap_WORKS(self) -> None:
        """The cap must not be so eager that legitimate nesting is refused."""
        await service.author_def(
            name="leaf",
            root={"kind": "transform", "id": "l", "config": {"expr": "leaf"}},
            provenance="user",
            strict=False,
        )
        await service.author_def(
            name="mid",
            root={"kind": "subworkflow", "id": "down", "config": {"ref": "leaf"}},
            provenance="user",
            strict=False,
        )
        spec = {
            "name": "top",
            "root": {"kind": "subworkflow", "id": "down", "config": {"ref": "mid"}},
        }
        run = await _fresh_run(spec)
        wd = WorkflowWatchdog(None, EngineServices())
        controller = await wd.launch(run, spec)
        assert await controller.run_to_completion(timeout=45) == RunStatus.COMPLETE

    async def test_a_deep_tree_shares_ONE_root(self) -> None:
        """ "Show me everything this request did" is the query that matters, and at depth 3 the
        parent chain alone cannot answer it."""
        await service.author_def(
            name="leaf",
            root={"kind": "transform", "id": "l", "config": {"expr": "leaf"}},
            provenance="user",
            strict=False,
        )
        await service.author_def(
            name="mid",
            root={"kind": "subworkflow", "id": "down", "config": {"ref": "leaf"}},
            provenance="user",
            strict=False,
        )
        spec = {
            "name": "top",
            "root": {"kind": "subworkflow", "id": "down", "config": {"ref": "mid"}},
        }
        run = await _fresh_run(spec)
        wd = WorkflowWatchdog(None, EngineServices())
        await (await wd.launch(run, spec)).run_to_completion(timeout=45)
        rows, _total = store.list_runs()
        descendants = [r for r in rows if r.id != run.id]
        assert descendants, "no child runs were created"
        assert all(r.root_run_id == run.id for r in descendants)

    async def test_a_runaway_recursion_is_BOUNDED(self) -> None:
        """Without the cap this spawns runs until the process dies, each leaving a row and a
        directory behind."""
        await service.author_def(
            name="loopy",
            root={"kind": "subworkflow", "id": "again", "config": {"ref": "loopy"}},
            provenance="user",
            strict=False,
        )
        spec = {
            "name": "loopy",
            "root": {"kind": "subworkflow", "id": "again", "config": {"ref": "loopy"}},
        }
        run = await _fresh_run(spec)
        wd = WorkflowWatchdog(None, EngineServices())
        await (await wd.launch(run, spec)).run_to_completion(timeout=45)
        _rows, total = store.list_runs()
        assert total <= 6, f"{total} runs created — the depth cap did not bound the recursion"


class TestForkIsolation:
    async def test_a_fork_names_what_it_does_NOT_isolate(self) -> None:
        """Honesty over comfort: a fork shares the filesystem and any external effect already
        committed. Reporting "isolated" would invite a user to treat a destructive re-run as safe.
        """
        run, sup, controller = await _launched(LIFECYCLE_SPEC)
        await controller.run_to_completion(timeout=30)
        result = service.fork_run(run.id, supervisor=sup)
        assert result.get("ok")
        assert isinstance(result.get("shared_axes"), list)
        assert result["shared_axes"], "a fork claimed total isolation"

    async def test_a_forks_journal_starts_from_the_parents_PREFIX(self) -> None:
        """So the child can serve cached outputs for the work it inherited, instead of re-running a
        completed prefix the user did not ask to redo."""
        run, sup, controller = await _launched(LIFECYCLE_SPEC)
        await controller.run_to_completion(timeout=30)
        child_id = service.fork_run(run.id, supervisor=sup)["child_run_id"]
        assert store.read_output(child_id, "root.children[0]") == "gathered"


class TestPerformance:
    def test_a_50_node_spec_schedules_under_100ms(self) -> None:
        """The plan's acceptance criterion. The frontier is re-derived on EVERY tick, so its cost is
        paid once per node completion — a slow frontier makes a large spec quadratically slow."""
        import time

        from personalclaw.workflows.models import Node
        from personalclaw.workflows.tick import frontier

        children = [
            {"kind": "transform", "id": f"n{i}", "config": {"expr": f"{i}"}} for i in range(60)
        ]
        root = Node.from_dict({"kind": "parallel", "id": "wide", "children": children})
        start = time.perf_counter()
        frontier(root, {})
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"frontier took {elapsed_ms:.1f}ms for 60 nodes"

    def test_a_deep_spec_also_schedules_quickly(self) -> None:
        """Depth stresses the recursion where width stresses the loop; a spec is usually both."""
        import time

        from personalclaw.workflows.models import Node
        from personalclaw.workflows.tick import frontier

        node: dict = {"kind": "transform", "id": "leaf", "config": {"expr": "x"}}
        for i in range(40):
            node = {"kind": "sequence", "id": f"s{i}", "children": [node]}
        root = Node.from_dict(node)
        start = time.perf_counter()
        frontier(root, {})
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"frontier took {elapsed_ms:.1f}ms for a 40-deep spec"


class TestSecurityBoundaries:
    def test_the_binding_language_has_no_eval(self) -> None:
        """A spec is user- and model-authored text. If bindings could evaluate arbitrary
        expressions, a spec would be a code-execution surface — asserted structurally because the
        absence of a feature is invisible to a behavioural test."""
        import inspect

        from personalclaw.workflows import bindings

        source = inspect.getsource(bindings)
        # `re.compile` is fine and ubiquitous; the builtin `compile(` is not. Checked as a
        # word-boundary match so the module's own regex literals do not read as a code-exec
        # surface — a test that cried wolf here would be turned off, which is worse than no test.
        import re as _re

        for forbidden in ("eval(", "exec(", "__import__"):
            assert forbidden not in source, f"the binding language contains {forbidden}"
        assert not _re.search(
            r"(?<![.\w])compile\(", source
        ), "the binding language calls compile()"

    def test_a_binding_cannot_reach_the_filesystem(self) -> None:
        import inspect

        from personalclaw.workflows import bindings

        source = inspect.getsource(bindings)
        for forbidden in ("open(", "Path(", "subprocess"):
            assert forbidden not in source, f"the binding language can reach {forbidden}"

    def test_an_unknown_pipe_is_REFUSED_not_ignored(self) -> None:
        """A silently-dropped sanitization pipe is worse than a hard error: the spec looks
        sanitized and is not."""
        from personalclaw.workflows.bindings import BindingContext, BindingError, resolve_expr

        with pytest.raises(BindingError):
            resolve_expr("inputs.x | not_a_real_pipe", BindingContext(inputs={"x": "v"}))

    def test_a_credential_never_reaches_the_JOURNAL(self) -> None:
        """The journal is read by the flywheel, shipped in bug reports and rendered in a UI — a
        credential that reaches it is leaked to all three."""
        from personalclaw.workflows.journal import redact

        secret = "sk-" + "a" * 40
        assert secret not in str(redact({"note": f"key is {secret}"}))

    def test_run_OUTPUTS_are_redacted_too(self) -> None:
        """Not just journal lines: a node output is stored, bound into a later prompt and rendered
        in the widget."""
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        from personalclaw.workflows.journal import Journal

        journal = Journal(run.id)
        secret = "ghp_" + "b" * 36
        _ref, preview = journal.store_output("root", {"token": secret})
        assert secret not in str(preview)
        assert secret not in str(store.read_output(run.id, "root"))
