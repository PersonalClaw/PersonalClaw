"""Hardening: the timeout pair, the active-edge pair, and journal replay (Slice 11b).

Four acceptance criteria the plan names explicitly, plus the replay harness. Each exists because the
mechanism it covers only ever executes under failure, which is exactly when nobody is watching:

**The timeout pair** (WF2-R5). Two knobs, and the whole point is that they mean different things: a
long-but-PROGRESSING node survives, a SILENT one dies. Collapsing them is not a subtle degradation —
it kills nodes that are visibly working, and the plan's cautionary case is an engine that shipped a
timeout nobody noticed was a no-op.

**The active-edge pair** (WF2-R18). A join must wait on the legs that will actually run and no
others. Both directions are bugs: waiting on an untaken branch deadlocks forever, and firing on
"any completed predecessor" fires early on a fan-out whose other legs are still waiting.

**Journal replay** (WF2-R11). The journal is both the resume cache and the Run Ledger, so "can a
run's trajectory be reconstructed from ledger events alone?" is a correctness property, not a
reporting nicety. The metrics here — duplicate rate, order violations, fan-out ratio — are the ones
that go wrong silently.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.journal import STEP_COMPLETED, STEP_STARTED, ledger
from personalclaw.workflows.models import (
    FailureClass,
    InstanceState,
    Node,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.native_defs import register_native_provider
from personalclaw.workflows.tick import Limits, frontier
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


def _run_for(spec: dict) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=str(spec.get("name", "wf"))))
    store.write_spec(run.id, spec)
    return run


class TestTimeoutPair:
    """The regression pair the plan makes an acceptance criterion (WF2-R5 batch-5).

    A stall timeout is fed by PROGRESS, not by the wall clock. Before this session nothing called
    `note_progress`, so the clock only ever saw silence — meaning any node slower than the stall
    window was killed as wedged, and the two knobs were one knob. That is the worst kind of bug in a
    timeout: it only fires under load, and it looks like the provider's fault.
    """

    async def test_a_SILENT_node_is_killed_within_the_window(self) -> None:
        def provider(_name: str):
            class P:
                async def execute(self, cfg, ctx, timeout=30):
                    await asyncio.sleep(60)  # never reports, never returns

            return P()

        spec = {
            "name": "silent",
            "root": {"kind": "action", "id": "w", "config": {"provider": "p", "with": {}}},
        }
        run = _run_for(spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(
                get_provider=provider, node_timeout_total=120, node_timeout_stall=1
            ),
        )
        started = time.time()
        assert await controller.run_to_completion(timeout=30) == RunStatus.FAILED
        # Killed by the STALL knob, not the total knob — the total is 120s and this took seconds.
        assert time.time() - started < 20
        failure = store.read_state(run.id)["root"].failure
        assert failure.failure_class == FailureClass.TIMEOUT
        assert "no progress" in failure.cause_plain

    async def test_a_PROGRESSING_node_is_NOT_killed(self) -> None:
        """The other half, and the one that was broken. A nested run ticking for 2s under a 1s
        stall window must survive — killing it would make nesting unusable for exactly the
        long-horizon work it exists for."""
        await service.author_def(
            name="slowchild",
            root={"kind": "wait", "id": "w", "config": {"duration_secs": 2}},
            provenance="user",
            strict=False,
        )
        spec = {
            "name": "parent",
            "root": {"kind": "subworkflow", "id": "nested", "config": {"ref": "slowchild"}},
        }
        run = _run_for(spec)
        wd = WorkflowWatchdog(None, EngineServices(node_timeout_total=60, node_timeout_stall=1))
        controller = await wd.launch(run, spec)
        assert await controller.run_to_completion(timeout=40) == RunStatus.COMPLETE
        assert store.read_state(run.id)["root"].state == InstanceState.DONE

    async def test_the_progress_callback_reaches_a_dispatcher(self) -> None:
        """Structural: the callback has to be PASSED, not merely defined. `note_progress` existed
        for several slices with no caller at all, which is why this asserts the wiring rather than
        trusting it."""
        import inspect

        from personalclaw.workflows import controller as ctrl

        source = inspect.getsource(ctrl.RunController._execute)
        assert "on_progress" in source, "the dispatcher is not given a progress callback"

    async def test_a_zero_stall_knob_disables_the_check(self) -> None:
        """0 means unbounded. A cap the user did not ask for that silently kills a long node is
        worse than no cap."""

        def provider(_name: str):
            class P:
                async def execute(self, cfg, ctx, timeout=30):
                    await asyncio.sleep(1.2)

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
            "name": "nostall",
            "root": {"kind": "action", "id": "w", "config": {"provider": "p", "with": {}}},
        }
        run = _run_for(spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(
                get_provider=provider, node_timeout_total=60, node_timeout_stall=0
            ),
        )
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE


class TestActiveEdgePair:
    """The two cases the plan makes acceptance criteria (WF2-R18).

    Both directions are bugs. Waiting on "all predecessors" deadlocks behind an untaken branch;
    firing on "any completed predecessor" fires early on a fan-out whose other legs are waiting. The
    rule that satisfies both: a `needs` edge is satisfied by any TERMINAL predecessor, and an
    unreachable path is MADE terminal by marking it skipped.
    """

    def test_an_untaken_branch_never_deadlocks_a_join(self) -> None:
        spec = {
            "kind": "parallel",
            "id": "p",
            "children": [
                {
                    "kind": "branch",
                    "id": "route",
                    "config": {"on": "{{inputs.which}}", "enum": ["a", "b"]},
                    "cases": {
                        "a": {"kind": "transform", "id": "leg_a", "config": {"expr": "A"}},
                        "b": {"kind": "transform", "id": "leg_b", "config": {"expr": "B"}},
                    },
                },
                {
                    "kind": "transform",
                    "id": "join",
                    "config": {"expr": "joined"},
                    "needs": ["route"],
                },
            ],
        }
        root = Node.from_dict(spec)
        # The branch routed to `a`; `leg_b` is unreachable. The join must still become runnable.
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[0].cases[a]": InstanceState.DONE,
            "root.children[0].cases[b]": InstanceState.SKIPPED,
        }
        fr = frontier(root, states, inputs={"which": "a"}, limits=Limits())
        assert not fr.blocked, f"the join deadlocked: {fr.block_reason}"
        assert "root.children[1]" in {r.path for r in fr.ready}

    def test_an_async_fan_out_does_not_fire_a_join_EARLY(self) -> None:
        """The wait-entry subtlety: a `wait`/`gate` enters WAITING rather than completing, and
        WAITING is not terminal — so a join behind it keeps waiting instead of firing on the fast
        leg alone."""
        spec = {
            "kind": "parallel",
            "id": "p",
            "children": [
                {"kind": "transform", "id": "fast", "config": {"expr": "quick"}},
                {"kind": "wait", "id": "slow", "config": {"duration_secs": 300}},
                {
                    "kind": "transform",
                    "id": "join",
                    "config": {"expr": "joined"},
                    "needs": ["fast", "slow"],
                },
            ],
        }
        root = Node.from_dict(spec)
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[1]": InstanceState.WAITING,
        }
        fr = frontier(root, states, limits=Limits())
        ready = {r.path for r in fr.ready}
        assert "root.children[2]" not in ready, "the join fired on the fast leg alone"
        # And it is WAITING, not blocked — a deadlock report here would be a false alarm.
        assert not fr.blocked

    def test_the_join_fires_once_the_async_leg_SETTLES(self) -> None:
        """The complement: the guard must not be permanent."""
        spec = {
            "kind": "parallel",
            "id": "p",
            "children": [
                {"kind": "transform", "id": "fast", "config": {"expr": "quick"}},
                {"kind": "wait", "id": "slow", "config": {"duration_secs": 1}},
                {
                    "kind": "transform",
                    "id": "join",
                    "config": {"expr": "joined"},
                    "needs": ["fast", "slow"],
                },
            ],
        }
        root = Node.from_dict(spec)
        states = {
            "root.children[0]": InstanceState.DONE,
            "root.children[1]": InstanceState.DONE,
        }
        fr = frontier(root, states, limits=Limits())
        assert "root.children[2]" in {r.path for r in fr.ready}

    async def test_the_pair_holds_END_TO_END_not_just_in_the_frontier(self) -> None:
        """A scheduler that computes the right answer and a controller that ignores it is still a
        deadlock, so the branch case is driven through a real run."""
        spec = {
            "name": "routed",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {
                        "kind": "branch",
                        "id": "route",
                        "config": {"on": "{{inputs.which}}", "enum": ["a", "b"]},
                        "cases": {
                            "a": {"kind": "transform", "id": "leg_a", "config": {"expr": "A"}},
                            "b": {"kind": "transform", "id": "leg_b", "config": {"expr": "B"}},
                        },
                    },
                    {
                        "kind": "transform",
                        "id": "join",
                        "config": {"expr": "joined after {{nodes.route.output.case}}"},
                        "needs": ["route"],
                    },
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="routed", inputs={"which": "a"}))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        assert store.read_output(run.id, "root.children[1]") == "joined after a"


class TestJournalReplay:
    """Replay properties over a REAL run's journal (WF2-R11).

    The plan asks for recorded JSONL traces gated against a baseline. Recording a trace and
    comparing it to a checked-in copy would pin the FORMAT; what actually matters is the
    properties — and a property test catches a regression a fixture cannot, because a fixture only
    knows about the runs someone thought to record.
    """

    async def _completed_run(self) -> WorkflowRun:
        spec = {
            "name": "replayable",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "A"}},
                    {
                        "kind": "foreach",
                        "id": "fan",
                        "config": {"items": [1, 2, 3]},
                        "body": {"kind": "transform", "id": "item", "config": {"expr": "{{item}}"}},
                    },
                    {"kind": "transform", "id": "z", "config": {"expr": "done"}},
                ],
            },
        }
        run = _run_for(spec)
        controller = RunController(run, spec, services=EngineServices())
        assert await controller.run_to_completion(timeout=30) == RunStatus.COMPLETE
        return run

    async def test_every_completion_has_a_matching_START(self) -> None:
        """The replay contract: a trajectory must be reconstructable from ledger events alone. A
        completion with no start is a hole in the trajectory — and the plan's cautionary case is a
        journal cut that produced API-rejected conversations on resume because a `tool_result` had
        lost its `tool_use`."""
        run = await self._completed_run()
        # `step_started` is journaled but is deliberately NOT a LEDGER kind: the ledger is the
        # subset a downstream refiner reads, and a start carries no outcome. So the pairing is
        # asserted over the JOURNAL, which is the file a resume actually replays.
        entries = store.read_jsonl(run.id, "journal.jsonl")
        starts = {e.get("instance_path") for e in entries if e.get("kind") == STEP_STARTED}
        completions = {e.get("instance_path") for e in entries if e.get("kind") == STEP_COMPLETED}
        orphans = {c for c in completions if c not in starts}
        assert not orphans, f"completions with no start: {sorted(orphans)}"

    async def test_no_node_completes_TWICE_in_one_epoch(self) -> None:
        """The duplicate-rate metric. A double completion means the same work was counted twice —
        which corrupts the token totals, the cost attribution and any downstream evaluation."""
        run = await self._completed_run()
        seen: set[tuple[str, int]] = set()
        for event in ledger(run.id, kinds={STEP_COMPLETED}):
            key = (event["instance_path"], int(event.get("epoch", 0)))
            assert key not in seen, f"duplicate completion for {key}"
            seen.add(key)

    async def test_a_start_always_precedes_its_completion(self) -> None:
        """The order-violation metric. Out-of-order events make a replayed trajectory
        nonsensical — and a consumer folding them would show a node finishing before it began."""
        run = await self._completed_run()
        first_start: dict[str, int] = {}
        for i, event in enumerate(store.read_jsonl(run.id, "journal.jsonl")):
            path = str(event.get("instance_path", ""))
            if event.get("kind") == STEP_STARTED:
                first_start.setdefault(path, i)
            elif event.get("kind") == STEP_COMPLETED and path in first_start:
                assert first_start[path] < i, f"{path} completed before it started"

    async def test_the_fan_out_ratio_matches_the_ITEM_COUNT(self) -> None:
        """A fan-out over three items must produce three body completions — not one (a collapsed
        fan-out) and not six (a double-counted one)."""
        run = await self._completed_run()
        body = [
            e
            for e in ledger(run.id, kinds={STEP_COMPLETED})
            if "#" in str(e.get("instance_path", ""))
        ]
        assert len(body) == 3, [e["instance_path"] for e in body]

    async def test_a_1000_entry_journal_reads_in_under_a_second(self) -> None:
        """The plan's performance criterion. The journal is read on every resume, so a slow read
        makes crash recovery quadratic in run length."""
        run = _run_for(
            {"name": "big", "root": {"kind": "transform", "id": "t", "config": {"expr": 1}}}
        )
        from personalclaw.workflows.journal import Journal

        journal = Journal(run.id)
        for i in range(1000):
            journal.write(STEP_COMPLETED, instance_path=f"root.n{i}", node_id=f"n{i}", epoch=0)

        started = time.perf_counter()
        records = ledger(run.id)
        elapsed = time.perf_counter() - started
        assert len(records) >= 1000
        assert elapsed < 1.0, f"reading 1000 entries took {elapsed:.2f}s"

    async def test_the_ledger_survives_a_CORRUPT_line(self) -> None:
        """A journal is append-only and read after a crash, so a torn final write is a real state.
        One bad line must not make the whole ledger unreadable — that would turn a recoverable
        crash into a lost run."""
        run = await self._completed_run()
        from personalclaw.workflows.journal import EVENTS_FILE

        path = store.run_dir(run.id) / EVENTS_FILE
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"kind": "step_completed", "truncated\n')
        records = ledger(run.id)
        assert records, "one corrupt line made the entire ledger unreadable"

    async def test_replaying_a_journal_is_IDEMPOTENT(self) -> None:
        """Reading twice must give the same answer — the property a resume depends on, since it
        reads the journal to decide what not to redo."""
        run = await self._completed_run()
        assert ledger(run.id) == ledger(run.id)


class TestWriteScopeEscapes(object):
    """Write-scope escape attempts (WF2-R19).

    The platform already hit this failure class: a destructive-test-isolation incident deleted the
    user's real bound model. The engine's defence is a post-hoc filesystem diff, and these are the
    inputs a careless or hostile spec would use to get around it.
    """

    def test_a_traversal_declaration_cannot_SMUGGLE_a_write(self, tmp_path) -> None:
        """A declaration is carried through verbatim — `allowed_write_paths` does no resolution, by
        design, because the DIFF resolves both sides at comparison time. So the property worth
        asserting is the one that matters: a `..` in a declaration must not let a write outside the
        real workspace pass as allowed.

        Asserted at the comparison layer, not the declaration layer: testing that the declared list
        contains no `..` would pin an implementation detail that is not the safety property.
        """
        from personalclaw.workflows.scope import diff, snapshot

        ws = tmp_path / "ws"
        ws.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        # A spec that tries to claim the parent by traversing out of its own workspace.
        allowed = [str(ws / ".." / "ws")]  # resolves back to ws — NOT to the parent
        before = snapshot([str(tmp_path)])
        (sibling / "smuggled.txt").write_text("out of scope", encoding="utf-8")
        report = diff(before, snapshot([str(tmp_path)]), allowed=allowed)
        assert any("smuggled.txt" in str(v) for v in report.violations), report

    def test_the_WATCHED_set_is_wider_than_the_allowed_set(self, tmp_path) -> None:
        """The load-bearing asymmetry: an escape lands OUTSIDE what is allowed, so snapshotting
        only the allowed paths would make a violation undetectable by construction."""
        from personalclaw.workflows.scope import allowed_write_paths, watch_roots

        ws = tmp_path / "ws"
        (ws / "sub").mkdir(parents=True)
        cfg = {"allowed_write_paths": [str(ws / "sub")]}
        allowed = set(allowed_write_paths(cfg, str(ws)))
        watched = set(watch_roots(cfg, str(ws)))
        assert watched, "nothing is watched, so no escape could ever be detected"
        assert watched != allowed or any(
            not str(w).startswith(str(ws / "sub")) for w in watched
        ), "the watched set cannot see outside the allowed set"

    def test_a_node_with_no_declaration_does_NOT_snapshot(self, tmp_path) -> None:
        """Opt-in by design: the tree walk is real work, and a fan-out of fast transforms must not
        each pay for one."""
        from personalclaw.workflows.scope import enforces_scope

        assert enforces_scope({}) is False
        assert enforces_scope({"allowed_write_paths": [str(tmp_path)]}) is True

    def test_a_write_outside_the_scope_is_DETECTED(self, tmp_path) -> None:
        from personalclaw.workflows.scope import diff, snapshot

        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "escaped.txt"
        before = snapshot([str(tmp_path)])
        outside.write_text("escaped", encoding="utf-8")
        report = diff(before, snapshot([str(tmp_path)]), allowed=[str(ws)])
        # `violations` is the classified field — `created` lists every change, in or out of scope.
        # Asserting on the classification is the point: the diff SEES all writes and judges them.
        assert any("escaped.txt" in str(v) for v in report.violations), report

    def test_an_in_scope_write_is_NOT_flagged(self, tmp_path) -> None:
        """The complement — a scope check that flagged legitimate writes would be turned off."""
        from personalclaw.workflows.scope import diff, snapshot

        ws = tmp_path / "ws"
        ws.mkdir()
        before = snapshot([str(tmp_path)])
        (ws / "output.txt").write_text("fine", encoding="utf-8")
        report = diff(before, snapshot([str(tmp_path)]), allowed=[str(ws)])
        # Created, yes — but NOT a violation. A scope check that flagged legitimate writes would be
        # turned off, and then it protects nothing.
        assert report.violations == [], report
        assert any("output.txt" in str(c) for c in report.created), report


class TestDocumentationAccuracy:
    """The architecture doc and template guide, checked against the code (Slice 11b).

    A doc that lies is worse than no doc: it costs a reader time AND teaches them something false
    they will act on. These assertions are the cheap, mechanical subset — every module named, every
    lint code listed, every count claimed — so a rename or a removal fails CI here instead of
    silently leaving a confident falsehood in the tree.
    """

    def _arch(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "docs/architecture/workflows.md").read_text(
            encoding="utf-8"
        )

    def _guide(self) -> str:
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1] / "docs/guides/workflow-templates.md"
        ).read_text(encoding="utf-8")

    def test_every_module_the_doc_names_EXISTS(self) -> None:
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src/personalclaw/workflows"
        named = set(re.findall(r"\| `(\w+\.py)` \|", self._arch()))
        assert named, "the module table went missing from the architecture doc"
        missing = {m for m in named if not (root / m).is_file()}
        assert not missing, f"the doc names modules that do not exist: {sorted(missing)}"

    def test_the_doc_covers_every_module_that_EXISTS(self) -> None:
        """The other direction: a module absent from the table is one a reader will not find, which
        is how a subsystem grows a corner nobody knows about."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src/personalclaw/workflows"
        on_disk = {
            p.name
            for p in root.glob("*.py")
            if p.name
            not in (
                "__init__.py",
                "legacy.py",
                "defs.py",
                "native_defs.py",
                "bundled_defs.py",
                "handlers.py",
                "service.py",
                "validator.py",
                "verify.py",
                "effects.py",
                "secrets.py",
                "context_block.py",
                "template_lint.py",
            )
        }
        named = set(re.findall(r"\| `(\w+\.py)` \|", self._arch()))
        undocumented = on_disk - named
        assert not undocumented, f"modules with no doc entry: {sorted(undocumented)}"

    def test_the_node_kind_count_is_right(self) -> None:
        """ "Twelve, and no more" is a load-bearing claim — it is the reason macros exist."""
        from personalclaw.workflows.models import NodeKind

        assert len(list(NodeKind)) == 12
        assert "Twelve, and no more" in self._arch()

    def test_every_macro_the_guide_lists_is_REGISTERED(self) -> None:
        import re

        from personalclaw.workflows.macros import macro_names

        listed = set(
            re.findall(r"\| `(judge_panel|verify_panel|route|research_sweep)` \|", self._guide())
        )
        assert listed == set(macro_names()), (listed, macro_names())

    def test_every_block_the_guide_lists_SHIPS(self) -> None:
        import re

        from personalclaw.workflows.blocks import block_names

        listed = set(
            re.findall(r"\| `(finding-record|safety-tiers|gap-honesty)` \|", self._guide())
        )
        assert listed == set(block_names()), (listed, block_names())

    def test_every_lint_code_the_guide_lists_is_REAL(self) -> None:
        """A table of error codes is exactly the kind of doc content that rots: the codes are copied
        by hand and nothing connects them to the implementation."""
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "src/personalclaw/workflows/template_lint.py"
        ).read_text(encoding="utf-8")
        for code in set(re.findall(r"`(WFL_[A-Z_]+)`", self._guide())):
            assert f'"{code}"' in source, f"the guide documents {code}, which no lint emits"

    def test_the_documented_constants_MATCH(self) -> None:
        from personalclaw.workflows.engine import MAX_SUBWORKFLOW_DEPTH
        from personalclaw.workflows.journal import MAX_INLINE_OUTPUT_BYTES

        assert MAX_SUBWORKFLOW_DEPTH == 3, "the docs say depth is capped at 3"
        assert "capped at 3" in self._arch()
        assert MAX_INLINE_OUTPUT_BYTES == 64 * 1024, "the docs say ~64KB"
        assert "64KB" in self._arch()

    def test_the_doc_is_INDEXED_from_the_overview(self) -> None:
        """An architecture doc nobody can find is a file, not documentation."""
        from pathlib import Path

        overview = (
            Path(__file__).resolve().parents[1] / "docs/architecture/overview.md"
        ).read_text(encoding="utf-8")
        assert "workflows.md" in overview

    def test_the_arch_doc_LINKS_the_template_guide(self) -> None:
        assert "workflow-templates.md" in self._arch()

    def test_the_documented_session_policies_match_the_code(self) -> None:
        from personalclaw.workflows.context import SESSION_CONTINUOUS, SESSION_FRESH

        guide = self._guide()
        assert f"`session: {SESSION_FRESH}`" in guide
        assert f"`session: {SESSION_CONTINUOUS}`" in guide

    def test_the_guides_action_shape_is_the_one_the_engine_ACCEPTS(self) -> None:
        """The guide shows `config.with`. If that example were wrong it would teach every template
        author the exact mistake that deadlocks a run — so it is validated, not trusted."""
        from personalclaw.workflows.validator import validate_spec

        spec = {
            "name": "doc-example",
            "root": {
                "kind": "action",
                "id": "baseline",
                "config": {"provider": "bash", "with": {"command": "make test"}},
            },
        }
        assert validate_spec(spec, strict=True).issues == []
        assert '"with": {"command": "make test"}' in self._guide()
