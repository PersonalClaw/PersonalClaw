"""The `[ACTIVE WORKFLOWS]` block, the staged-turn echo, and blocking mode (Slice 6c).

The load-bearing claims:

* **never break a turn** — every injection swallows its own errors and returns "". A context
  builder that raises takes the user's whole message with it, and a corrupt workflow row
  must never cost someone their turn;
* the block orders by URGENCY, not recency: a run waiting on a human is the one actionable
  thing in the session;
* the staged echo carries the CURRENT spec + version (WF2-R20f), so a model mutates what it
  just saw rather than the spec it generated earlier — those diverge the moment anything
  else touches the run;
* the echo STRIPS credentials, because it lands in a chat turn;
* **blocking mode returns on needs_input** — waiting for terminal there deadlocks: the tool
  holds the turn that would render the ask, so the ask can never be answered;
* progress callbacks fire during a blocking wait, and a broken observer cannot affect the
  run it is watching.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import context_block as CB
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

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
    "name": "ctx",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
            {"kind": "infer", "id": "think", "config": {"prompt": "on {{nodes.seed.output.n}}"}},
        ],
    },
}


def _run(status: RunStatus, name: str = "ctx", **kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=name, status=status, **kw))
    store.write_spec(run.id, SPEC)
    return run


# ── the [ACTIVE WORKFLOWS] block ─────────────────────────────────────────────


class TestActiveWorkflowsBlock:
    def test_no_runs_means_no_block(self) -> None:
        """Silence when there is nothing to say — an empty header would be noise in every
        single turn."""
        assert CB.active_workflows_block() == ""

    def test_a_running_run_is_surfaced_with_its_id(self) -> None:
        run = _run(RunStatus.RUNNING)
        block = CB.active_workflows_block()
        assert "[ACTIVE WORKFLOWS" in block
        assert run.id in block and "running" in block

    def test_a_terminal_run_is_not_surfaced(self) -> None:
        _run(RunStatus.COMPLETE)
        assert CB.active_workflows_block() == ""

    def test_a_needs_input_run_shows_what_it_is_asking(self) -> None:
        """Telling the user a run needs them without saying what it wants is worse than
        useless — they have to go dig."""
        _run(
            RunStatus.NEEDS_INPUT,
            attention={"kind": "approval", "prompt": "Deploy to prod?"},
        )
        block = CB.active_workflows_block()
        assert "waiting on you" in block and "Deploy to prod?" in block

    def test_needs_input_sorts_before_running(self) -> None:
        """Ordered by urgency, not recency: the actionable run comes first."""
        _run(RunStatus.RUNNING, name="just-running")
        _run(RunStatus.NEEDS_INPUT, name="needs-me")
        block = CB.active_workflows_block()
        assert block.index("needs-me") < block.index("just-running")

    def test_the_run_count_is_capped_with_a_remainder(self) -> None:
        """A user with forty runs must not lose their own message to a listing."""
        for i in range(CB.MAX_RUNS_IN_BLOCK + 3):
            _run(RunStatus.RUNNING, name=f"wf-{i}")
        block = CB.active_workflows_block()
        assert "and 3 more" in block

    def test_the_block_is_length_capped(self) -> None:
        for i in range(CB.MAX_RUNS_IN_BLOCK):
            _run(
                RunStatus.NEEDS_INPUT,
                name=f"wf-{i}",
                attention={"prompt": "x" * 400},
            )
        assert len(CB.active_workflows_block()) <= CB.MAX_BLOCK_CHARS + 40

    def test_it_tells_the_model_which_tools_to_use(self) -> None:
        """A block that says a run needs input without naming the tool leaves the model
        guessing at the API."""
        _run(RunStatus.NEEDS_INPUT)
        block = CB.active_workflows_block()
        assert "workflow_status" in block and "workflow_resume" in block

    def test_a_broken_store_returns_empty_not_an_exception(self, monkeypatch) -> None:
        """NEVER BREAK A TURN. A raising context builder costs the user their message."""

        def boom() -> list:
            raise RuntimeError("db gone")

        monkeypatch.setattr("personalclaw.workflows.store.active_runs", boom)
        assert CB.active_workflows_block() == ""

    def test_a_corrupt_attention_payload_does_not_raise(self, monkeypatch) -> None:
        run = _run(RunStatus.NEEDS_INPUT)
        # attention is typed dict|None; a string is corruption from an older writer.
        raw = store.get(run.id)
        raw.attention = "not-a-dict"  # type: ignore[assignment]
        store.save(raw)
        block = CB.active_workflows_block()
        assert run.id in block  # rendered, just without an ask


# ── the staged-turn echo ─────────────────────────────────────────────────────


class TestStagedEcho:
    def test_it_carries_the_current_version_to_pass_back(self) -> None:
        """The version IS the concurrency guard — without it the model cannot use
        expect_version and a concurrent edit goes undetected."""
        run = _run(RunStatus.RUNNING)
        echo = CB.staged_spec_echo(run.id)
        assert f"spec_version {run.spec_version}" in echo
        assert f"expect_version={run.spec_version}" in echo

    def test_it_renders_both_the_tree_and_the_source(self) -> None:
        """The tree is what a model reasons over; the JSON is what it must edit precisely.
        Only one of the two invites either invented fields or unreadable structure."""
        run = _run(RunStatus.RUNNING)
        echo = CB.staged_spec_echo(run.id)
        assert "Structure:" in echo and "Source:" in echo
        assert "- sequence #main" in echo and "- infer #think" in echo
        assert "```json" in echo

    def test_it_tells_the_model_to_edit_THIS_not_what_it_remembers(self) -> None:
        run = _run(RunStatus.RUNNING)
        echo = CB.staged_spec_echo(run.id)
        assert "CURRENT state on disk" in echo
        assert "not a spec you generated earlier" in echo

    async def test_it_shows_live_node_states(self) -> None:
        """Which nodes are frozen is exactly what decides whether an edit is legal."""
        run = _run(RunStatus.RUNNING)

        async def fake(prompt, *, use_case="background", output_type=None):
            return "ok"

        c = RunController(run, SPEC, services=EngineServices(completion=fake))
        await c.run_to_completion(timeout=20)
        echo = CB.staged_spec_echo(run.id)
        assert "#seed [done]" in echo and "#think [done]" in echo

    def test_credentials_are_stripped_from_the_echo(self) -> None:
        """It lands in a chat turn; a credential here has leaked to the transcript."""
        run = store.create(WorkflowRun(id="", workflow_name="sec", status=RunStatus.RUNNING))
        store.write_spec(
            run.id,
            {
                "name": "sec",
                "root": {
                    "kind": "sequence",
                    "id": "s",
                    "children": [
                        {
                            "kind": "action",
                            "id": "a",
                            "config": {"provider": "x", "api_key": "sk-real-secret-value"},
                        }
                    ],
                },
            },
        )
        echo = CB.staged_spec_echo(run.id)
        assert "sk-real-secret-value" not in echo
        assert "_has_api_key" in echo

    def test_an_unknown_run_echoes_nothing(self) -> None:
        assert CB.staged_spec_echo("deadbeef") == ""

    def test_an_oversized_spec_degrades_with_an_instruction(self) -> None:
        """Truncating silently would have the model edit a node it never saw."""
        run = store.create(WorkflowRun(id="", workflow_name="big", status=RunStatus.RUNNING))
        store.write_spec(
            run.id,
            {
                "name": "big",
                "root": {
                    "kind": "sequence",
                    "id": "s",
                    "children": [
                        {
                            "kind": "infer",
                            "id": f"n{i}",
                            "config": {"prompt": "x" * 200},
                        }
                        for i in range(60)
                    ],
                },
            },
        )
        echo = CB.staged_spec_echo(run.id)
        assert "Source truncated" in echo and "workflow_get_def" in echo

    def test_a_broken_store_echoes_nothing_rather_than_raising(self, monkeypatch) -> None:
        def boom(run_id: str):
            raise RuntimeError("gone")

        monkeypatch.setattr("personalclaw.workflows.store.get", boom)
        assert CB.staged_spec_echo("abc") == ""

    def test_an_unparseable_tree_still_echoes_its_source(self) -> None:
        """The source is the half that matters for editing; losing both would be worse."""
        run = store.create(WorkflowRun(id="", workflow_name="bad", status=RunStatus.RUNNING))
        store.write_spec(run.id, {"name": "bad", "root": {"kind": "nonsense-kind"}})
        echo = CB.staged_spec_echo(run.id)
        assert "Source:" in echo and "nonsense-kind" in echo


class TestTreeRender:
    def test_it_renders_containers_bodies_cases_and_defaults(self) -> None:
        node = {
            "kind": "branch",
            "id": "route",
            "cases": {"hit": {"kind": "transform", "id": "h"}},
            "default": {"kind": "transform", "id": "d"},
        }
        lines = CB.render_tree(node)
        text = "\n".join(lines)
        assert "- branch #route" in text
        assert "case hit:" in text and "#h" in text
        assert "default:" in text and "#d" in text

    def test_it_renders_a_loop_body(self) -> None:
        node = {"kind": "loop", "id": "l", "body": {"kind": "transform", "id": "b"}}
        text = "\n".join(CB.render_tree(node))
        assert "body:" in text and "#b" in text

    def test_garbage_does_not_raise(self) -> None:
        assert CB.render_tree("not a node") == ["- ?"]
        assert CB.render_tree(None) == ["- ?"]


class TestStagingTools:
    def test_inspect_tools_stage_a_spec_echo(self) -> None:
        """WF2-R20f: the model's next move after an inspect is likely a mutation."""
        for name in ("workflow_status", "workflow_get_def", "workflow_observe"):
            assert CB.needs_staging(name), name

    def test_mutation_tools_do_not_re_stage(self) -> None:
        """Echoing after the edit would double the cost for no benefit — the model already
        knows what it just changed."""
        for name in ("workflow_edit", "workflow_start", "workflow_rewind"):
            assert not CB.needs_staging(name), name

    def test_the_tool_surface_appends_the_echo(self) -> None:
        from personalclaw import mcp_workflows as T

        run = _run(RunStatus.RUNNING)
        out = T._call_tool("workflow_status", {"run_id": run.id})
        assert "WORKFLOW SPEC" in out and "expect_version" in out

    def test_a_non_staging_tool_appends_nothing(self) -> None:
        from personalclaw import mcp_workflows as T

        out = T._call_tool("workflow_manifest", {})
        assert "WORKFLOW SPEC" not in out


# ── blocking mode ────────────────────────────────────────────────────────────


class TestBlockingMode:
    async def test_it_returns_the_terminal_status(self) -> None:
        run = _run(RunStatus.DRAFT)

        async def fake(prompt, *, use_case="background", output_type=None):
            return "ok"

        c = RunController(run, SPEC, services=EngineServices(completion=fake))
        assert await c.wait_for_terminal(timeout=20) == RunStatus.COMPLETE

    async def test_it_returns_on_needs_input_rather_than_deadlocking(self) -> None:
        """The load-bearing case. Waiting for TERMINAL here is a guaranteed deadlock: the
        tool holds the turn that would render the ask, so nobody can answer it."""
        spec = {
            "name": "gated",
            "root": {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "approval", "prompt": "ok?", "timeout_secs": 0},
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="gated"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices())
        status = await c.wait_for_terminal(timeout=20)
        assert status == RunStatus.NEEDS_INPUT

    async def test_progress_fires_during_a_slow_run(self) -> None:
        import asyncio

        ticks: list[dict] = []

        async def slow(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(0.6)
            return "ok"

        run = _run(RunStatus.DRAFT)
        c = RunController(run, SPEC, services=EngineServices(completion=slow))
        status = await c.wait_for_terminal(timeout=20, progress_every=0.2, on_progress=ticks.append)
        assert status == RunStatus.COMPLETE
        assert ticks, "expected at least one progress tick during a slow run"
        assert ticks[0]["run_id"] == run.id and "nodes" in ticks[0]

    async def test_a_broken_progress_observer_cannot_affect_the_run(self) -> None:
        import asyncio

        async def slow(prompt, *, use_case="background", output_type=None):
            await asyncio.sleep(0.5)
            return "ok"

        def boom(snap: dict) -> None:
            raise RuntimeError("observer exploded")

        run = _run(RunStatus.DRAFT)
        c = RunController(run, SPEC, services=EngineServices(completion=slow))
        status = await c.wait_for_terminal(timeout=20, progress_every=0.2, on_progress=boom)
        assert status == RunStatus.COMPLETE

    async def test_the_snapshot_is_cheap_and_shaped(self) -> None:
        run = _run(RunStatus.DRAFT)

        async def fake(prompt, *, use_case="background", output_type=None):
            return "ok"

        c = RunController(run, SPEC, services=EngineServices(completion=fake))
        await c.run_to_completion(timeout=20)
        snap = c.progress_snapshot()
        assert set(snap) == {"run_id", "status", "tokens", "nodes"}
        assert all(set(n) == {"instance_path", "state"} for n in snap["nodes"])

    async def test_a_blocking_start_hands_back_the_resume_token(self) -> None:
        """Otherwise the model must guess that a second call is needed AND which token —
        the ask is useless without a way to answer it."""
        from personalclaw.workflows import defs as defs_mod
        from personalclaw.workflows import service

        class Mem(defs_mod.WorkflowDefProvider):
            def __init__(self) -> None:
                self.d: dict = {}

            @property
            def name(self) -> str:
                return "ctx-mem"

            @property
            def readonly(self) -> bool:
                return False

            async def list_defs(self, *, limit: int = 200, offset: int = 0):
                return list(self.d.values()), len(self.d)

            async def get_def(self, name: str):
                return self.d.get(name)

            async def save_def(self, **f):
                self.d[f["name"]] = dict(f)
                return self.d[f["name"]]

            async def delete_def(self, name: str) -> bool:
                return self.d.pop(name, None) is not None

        class Sup:
            def __init__(self) -> None:
                self.c: dict = {}

            def controller(self, run_id: str):
                return self.c.get(run_id)

            async def launch(self, run, spec, *, depth: int = 0):
                ctl = RunController(run, spec, services=EngineServices())
                self.c[run.id] = ctl
                await ctl.start()
                return ctl

        defs_mod.register_provider(Mem())
        try:
            await service.author_def(
                name="needs-a-human",
                root={
                    "kind": "gate",
                    "id": "g",
                    "config": {"kind": "approval", "prompt": "ok?", "timeout_secs": 0},
                },
            )
            body = await service.start_run(
                name="needs-a-human",
                mode="blocking",
                supervisor=Sup(),
                blocking_timeout=20,
                skip_preflight=True,
            )
            assert body["ok"] and body["status"] == RunStatus.NEEDS_INPUT.value
            assert body["needs_input"] and body["needs_input"][0]["resume_token"]
            assert body["needs_input"][0]["ask"]["prompt"] == "ok?"
        finally:
            defs_mod.unregister_provider("ctx-mem")


# ── the context.py injection ─────────────────────────────────────────────────


class TestContextInjection:
    def test_context_py_injects_the_block(self) -> None:
        """The wiring itself: an unwired block is invisible no matter how good it is."""
        import inspect

        from personalclaw import context

        source = inspect.getsource(context)
        assert "active_workflows_block" in source

    def test_the_injection_is_wrapped_against_raising(self) -> None:
        """NEVER BREAK A TURN — asserted on the CALL SITE, not just the helper."""
        import inspect

        from personalclaw import context

        source = inspect.getsource(context)
        # The import sits inside the guarded block, so measure from the CALL and look for
        # the enclosing try/except around it.
        idx = source.index("active_workflows_block(")
        before, after = source[:idx], source[idx:]
        assert before.rstrip().endswith("=") or "try:" in before[-800:]
        assert "except Exception" in after[:600]

    def test_it_does_not_auto_create_a_project(self) -> None:
        """`resolve_project_id` auto-creates when nothing resolves; a read-only context
        block must not have side effects."""
        import inspect

        from personalclaw import context

        source = inspect.getsource(context)
        idx = source.index("active_workflows_block(")
        assert "resolve_project_id" not in source[idx : idx + 200]
