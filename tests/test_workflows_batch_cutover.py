"""The batch-spawn CUTOVER and the seams that make a compiled batch real (WF2WOR-5 C2).

`batch_compile` was a complete compiler with zero callers: a full compilation layer compiles nothing
until something routes into it, and N independent fire-and-forget spawns are not a batch — they have
no run record, so they cannot be one widget, cannot survive a restart, and cannot be retried per
branch. These tests hold the four seams that close that gap, and each is written against the
property rather than the implementation:

* **The cutover** — N>=2 compiles; N=1 stays a raw spawn.
* **Restart survival** — the widget rebuilds FROM DISK. Asserted by re-reading persisted state with
  fresh objects, never by inspecting the live one: a live object proves the process still remembers,
  which is the thing a restart destroys.
* **The lease** — a second execution is REFUSED. Asserted on the refusal, not on the presence of a
  lease row: a lease that records a claim without preventing the second run is worse than none,
  because it looks like protection.
* **The tool-handler seam** — a denied tool is refused at the handler, with the flag written by the
  spawn path rather than by the test.
"""

import json

import pytest

from personalclaw.workflows import batch_compile, leases, roster
from personalclaw.workflows.batch_compile import Capability, LeafTask

# ── helpers ──────────────────────────────────────────────────────────────────


def leaf(name: str, **kw) -> LeafTask:
    """A leaf whose declarations are long enough to satisfy `contract_lint`."""
    base = {
        "task": f"investigate the {name} subsystem thoroughly",
        "objective": f"determine how {name} behaves under load",
        "output_format": "a markdown list of concrete findings",
        "boundary": "do not modify any source file",
    }
    base.update(kw)
    return LeafTask(**base)  # type: ignore[arg-type]


# ── clause 1: the cutover ────────────────────────────────────────────────────


def test_two_tasks_ROUTE_THROUGH_the_compiler_rather_than_two_spawns(monkeypatch):
    """The whole point of the atom: N>=2 becomes ONE compiled run, not N spawns.

    Asserted at the seam a batch actually crosses — the POSTs the tool makes. Two independent
    `/api/spawn` calls and one compiled run are indistinguishable in the tool's return string but
    completely different in what exists afterwards, so the calls are what the test reads.
    """
    from personalclaw import mcp_subagents

    posts: list[tuple[str, dict]] = []

    def fake_post(path: str, body: dict) -> dict:
        posts.append((path, body))
        if path == "/api/workflows/runs":
            return {"ok": True, "run_id": "run-abc"}
        return {"ok": True}

    monkeypatch.setattr(mcp_subagents, "_post", fake_post)
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")

    out = mcp_subagents._call_tool_inner(
        "subagent_run",
        {
            "tasks": [
                {
                    "task": "investigate the cache subsystem thoroughly",
                    "objective": "determine how the cache behaves under load",
                    "output_format": "a markdown list of concrete findings",
                    "boundary": "do not modify any source file",
                },
                {
                    "task": "investigate the queue subsystem thoroughly",
                    "objective": "determine how the queue behaves under load",
                    "output_format": "a markdown list of concrete findings",
                    "boundary": "do not modify any source file",
                },
            ]
        },
    )

    paths = [p for p, _ in posts]
    assert "/api/spawn" not in paths, f"a batch still fire-and-forget spawned: {paths}"
    assert "/api/workflows" in paths, "the compiled spec was never persisted"
    assert "/api/workflows/runs" in paths, "no run was started for the batch"
    assert "run-abc" in out


def test_a_SINGLE_task_stays_a_raw_spawn(monkeypatch):
    """N=1 keeps today's behaviour. A run record plus project resolution on "go check X" is
    ceremony the personal feel does not survive — `COMPILE_THRESHOLD` owns that line."""
    from personalclaw import mcp_subagents

    posts: list[str] = []

    def fake_post(path: str, body: dict) -> dict:
        posts.append(path)
        return {"id": "sub1"}

    monkeypatch.setattr(mcp_subagents, "_post", fake_post)
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")

    mcp_subagents._call_tool_inner("subagent_run", {"task": "go check the disk usage"})
    assert posts == ["/api/spawn"]


def test_the_agents_LENGTH_CHECK_survives_the_cutover(monkeypatch):
    """A pre-existing guarantee the cutover must not drop: mismatched `agents` is still refused,
    and refused BEFORE anything is persisted."""
    from personalclaw import mcp_subagents

    posts: list[str] = []
    monkeypatch.setattr(mcp_subagents, "_post", lambda p, b: posts.append(p) or {})
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")

    out = mcp_subagents._call_tool_inner(
        "subagent_run", {"tasks": ["a", "b", "c"], "agents": ["one"]}
    )
    assert "must match tasks length" in out
    assert posts == []


def test_an_UNDER_SPECIFIED_batch_is_refused_with_the_findings(monkeypatch):
    """Plain-string tasks carry no contract, so `contract_lint` refuses them — and the refusal
    must SAY what to supply. A batch that failed with "did not compile" and no findings would send
    the caller back to read the compiler."""
    from personalclaw import mcp_subagents

    posts: list[str] = []
    monkeypatch.setattr(mcp_subagents, "_post", lambda p, b: posts.append(p) or {})
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")

    out = mcp_subagents._call_tool_inner("subagent_run", {"tasks": ["do a thing", "do another"]})
    assert "leaf_contract_missing" in out
    assert "objective" in out and "boundary" in out
    assert posts == [], "an uncompiled batch must not persist anything"


def test_a_NESTED_batch_is_refused_at_the_seam(monkeypatch):
    """`depth_lint` refuses a batch inside a batch. The DEPTH must come from the env the engine
    wrote, not from a tool argument — a depth the caller supplies is a depth a leaf can
    understate, which would make the refusal advisory."""
    from personalclaw import mcp_subagents
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    posts: list[str] = []
    monkeypatch.setattr(mcp_subagents, "_post", lambda p, b: posts.append(p) or {})
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "leaf:1")

    out = mcp_subagents._call_tool_inner(
        "subagent_run",
        {
            "tasks": [
                {
                    "task": "investigate the cache subsystem thoroughly",
                    "objective": "determine how the cache behaves under load",
                    "output_format": "a markdown list of concrete findings",
                    "boundary": "do not modify any source file",
                },
                {
                    "task": "investigate the queue subsystem thoroughly",
                    "objective": "determine how the queue behaves under load",
                    "output_format": "a markdown list of concrete findings",
                    "boundary": "do not modify any source file",
                },
            ]
        },
    )
    assert "nested_batch" in out
    assert posts == []


def test_the_tool_SCHEMA_accepts_a_contract_object():
    """The contract has to be expressible through the tool that needs it. Before the cutover
    `tasks` accepted strings only, so an N>=2 batch could never satisfy `contract_lint` — the
    compiler would have refused every call it was finally wired to."""
    from personalclaw.validation import SPAWN_RUN_SCHEMA, validate_tool_args

    cleaned = validate_tool_args(
        {"tasks": [{"task": "a", "objective": "b"}, "a plain string"]}, SPAWN_RUN_SCHEMA
    )
    assert isinstance(cleaned["tasks"][0], dict)
    assert cleaned["tasks"][1] == "a plain string"


def test_a_LIST_ITEM_of_the_wrong_type_is_still_rejected():
    """Widening `tasks` to accept objects must not widen it to accept anything."""
    from personalclaw.validation import SPAWN_RUN_SCHEMA, ValidationError, validate_tool_args

    with pytest.raises(ValidationError):
        validate_tool_args({"tasks": [123]}, SPAWN_RUN_SCHEMA)


# ── clause 1: restart survival, proven FROM DISK ─────────────────────────────


def test_the_widget_rebuilds_FROM_DISK_after_a_restart(tmp_path, monkeypatch):
    """Restart survival, asserted the only way that means anything: throw the live objects away
    and rebuild from the file.

    A test that read the run back out of the in-memory store would prove the PROCESS still
    remembers, which is exactly what a gateway restart destroys. So this writes through the real
    store, clears every cached handle, and re-reads.

    Both halves are re-read from disk, and the SECOND one is the one that matters. The widget
    survives because the compiled spec is persisted as a workflow DEFINITION and the run references
    it by `workflow_name` — so the def is the artifact a restart has to recover. Taking the branch
    ids from the in-memory `CompileResult` would pass even if def persistence were broken entirely,
    because the compile result is still sitting in the process. The loop closed here is the real
    one: run row → `workflow_name` → persisted spec → stable branch ids, which is what makes
    per-branch retry provable ACROSS a restart rather than only within the process.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    import asyncio
    import importlib

    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import native_defs
    from personalclaw.workflows import store as store_mod

    store_mod = importlib.reload(store_mod)
    monkeypatch.setattr(store_mod, "config_dir", lambda: tmp_path, raising=False)

    from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

    result = batch_compile.compile_batch([leaf("cache"), leaf("queue")], run_name="batch-restart")
    assert result.compiled and result.ok
    compiled_ids = [c["id"] for c in result.spec["root"]["children"]]

    provider = native_defs.NativeWorkflowDefProvider()
    defs_mod.register_provider(provider)
    asyncio.run(provider.save_def(**result.spec))

    created = store_mod.create(
        WorkflowRun(
            id="",
            workflow_name="batch-restart",
            status=RunStatus.RUNNING,
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL),
        )
    )
    run_id = created.id
    assert run_id

    # The restart: drop every in-process handle to the run AND to the compiled spec.
    del created
    store_mod = importlib.reload(store_mod)
    monkeypatch.setattr(store_mod, "config_dir", lambda: tmp_path, raising=False)

    reloaded = store_mod.get(run_id)
    assert reloaded is not None, "the run did not survive the restart"
    assert reloaded.workflow_name == "batch-restart"

    # The SPEC half: fetch the def back by the name the surviving run row points at.
    recovered = asyncio.run(native_defs.NativeWorkflowDefProvider().get_def(reloaded.workflow_name))
    assert recovered is not None, "the run row points at a def that is not on disk"
    persisted = recovered.to_dict()
    persisted_ids = [c["id"] for c in persisted["root"]["children"]]
    assert persisted_ids == compiled_ids, "the persisted branch ids drifted from the compiled ones"
    assert len(set(persisted_ids)) == 2


def test_a_FAILED_save_never_starts_a_run(monkeypatch):
    """A run row pointing at a def that was never saved is a widget that survives as a BROKEN row —
    worse than not surviving, because the board shows recoverable work that cannot be recovered.

    Two independent guards, and this asserts the first: `_run_compiled_batch` returns on a save
    error before it POSTs the start. The second is `service.start_run`, which resolves the def via
    `_raw_def` and answers `WF_DEF_NOT_FOUND` — so even a start that somehow raced a missing def is
    refused rather than minting an orphan row."""
    from personalclaw import mcp_subagents

    posts: list[str] = []

    def fake_post(path: str, body: dict) -> dict:
        posts.append(path)
        if path == "/api/workflows":
            return {"error": "disk full"}
        return {"ok": True, "run_id": "should-not-happen"}

    monkeypatch.setattr(mcp_subagents, "_post", fake_post)
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")

    declared = {
        "objective": "determine how the subsystem behaves",
        "output_format": "a markdown list of findings",
        "boundary": "do not modify any source file",
    }
    out = mcp_subagents._call_tool_inner(
        "subagent_run",
        {
            "tasks": [
                {"task": "investigate the cache subsystem", **declared},
                {"task": "investigate the queue subsystem", **declared},
            ]
        },
    )
    assert "could not persist" in out
    assert posts == ["/api/workflows"], "a run was started against an unsaved def"


def test_every_branch_is_INDIVIDUALLY_ADDRESSABLE_for_retry():
    """Per-branch retry rides the EXISTING `run-from` route over the compiled node ids rather than
    a new retry mechanism. The ids must therefore be stable and unique — two leaves sharing an id
    would make "retry this branch" ambiguous, and the engine would re-run whichever it found."""
    result = batch_compile.compile_batch([leaf("cache"), leaf("cache")])
    ids = [c["id"] for c in result.spec["root"]["children"]]
    assert len(set(ids)) == 2, f"two same-named leaves collided: {ids}"


# ── clause 3: the isolated workspace is actually PROVISIONED ─────────────────


def test_the_compiled_spec_declares_the_workspace_the_APPLIER_READS():
    """The key mismatch that made this clause inert.

    The applier is RUN-level and fully wired (`controller._provision_workspace` →
    `provisioning.provision`); its gate is `provisioning.declares_workspace`, which reads a
    TOP-LEVEL `workspace:` block. The compiler only wrote `postures[node]["workspace_mode"]`, a
    render surface no applier reads — so provisioning silently no-opped for every compiled batch.
    Asserted through `declares_workspace` (the real reader) rather than by eyeballing the key,
    because "the key is present" is the decoration this replaces."""
    from personalclaw.workflows import provisioning

    result = batch_compile.compile_batch([leaf("cache"), leaf("queue")])
    assert provisioning.declares_workspace(result.spec) is True

    spec, issues = provisioning.resolve_spec(result.spec)
    assert spec.isolated is True, "a non-isolated mode would leave the branches on the real tree"
    assert [i.code for i in issues if i.fatal] == [], "a fatal issue REFUSES the run"


def test_the_workspace_block_SURVIVES_persistence(tmp_path, monkeypatch):
    """The second half of the same defect, and the one that made the first fix decoration.

    `native_defs.save_def` builds its payload from an ALLOWLIST, and `service.author_def` builds its
    spec from another one. Measured: a compiled batch declaring `workspace: {mode: scratch}`
    round-tripped to a persisted def with NO block at all, so `declares_workspace` answered False at
    run start — the declaration was correct at both ends and erased in the middle. The applier reads
    the PERSISTED def, so persistence is where this has to be proven."""
    import asyncio

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import native_defs, provisioning, service

    defs_mod.register_provider(native_defs.NativeWorkflowDefProvider())

    result = batch_compile.compile_batch([leaf("cache"), leaf("queue")], run_name="batch-ws")
    authored = asyncio.run(
        service.author_def(
            name="batch-ws",
            root=result.spec["root"],
            strict=False,
            provenance="user",
            workspace=result.spec[batch_compile.WORKSPACE_KEY],
        )
    )
    assert authored.get("ok"), authored

    recovered = asyncio.run(native_defs.NativeWorkflowDefProvider().get_def("batch-ws"))
    assert recovered is not None
    assert (
        provisioning.declares_workspace(recovered.to_dict()) is True
    ), "the workspace block was dropped by the save allowlist — the applier will find nothing"


def test_a_crash_surviving_batch_takes_the_SUSPENDED_path_not_adoption(tmp_path, monkeypatch):
    """Which restart path a compiled batch takes, MEASURED rather than assumed.

    Declaring a workspace changes the answer, so the atom has to state it. `stamp_run` records
    `worktree_path` for every isolated mode; `watchdog._substrate_for` reads exactly that key and
    `worktrees.substrate_for` reports `kind="worktree"` for anything it finds there. So a batch that
    survives a gateway kill is SUSPENDED (PAUSED, with a Resume affordance) rather than
    auto-adopted — §5.2's designed behaviour for an isolated substrate, not a regression: the work
    is on disk and recoverable, and the sweep refuses to abort recoverable work.

    Before the workspace was declared, a batch had no recorded path and the sweep left it to
    adoption. Both are "survives a restart"; this pins WHICH, so a future reader is not surprised by
    a paused batch."""
    import pathlib

    from personalclaw.workflows import containers, provisioning, worktrees
    from personalclaw.workflows.models import RunStatus, WorkflowRun
    from personalclaw.workflows.workspace import Mode, WorkspaceSpec

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    workspace = pathlib.Path(tmp_path / "ws" / "batch")
    workspace.mkdir(parents=True, exist_ok=True)
    run = WorkflowRun(id="r1", workflow_name="batch-x", status=RunStatus.RUNNING)
    provisioning.stamp_run(
        run,
        provisioning.Provisioned(path=str(workspace), isolated=True),
        WorkspaceSpec(mode=Mode(batch_compile.BATCH_WORKSPACE_MODE)),
    )
    assert run.extra.get("worktree_path"), "stamp_run recorded no recoverable substrate path"

    substrate = worktrees.substrate_for(provisioning.inspect_run(run))
    assert substrate.isolated is True and substrate.alive is True
    decision = containers.sweep_decision(run, substrate)
    assert decision.status is RunStatus.PAUSED
    assert decision.board_state is containers.BoardState.SUSPENDED


def test_scratch_is_an_ISOLATED_mode_so_the_substrate_is_recoverable():
    """`stamp_run` records `worktree_path` for every isolated mode, not just worktree — which is
    what lets §5.2's boot sweep recognise a crash-survivor's substrate. `scratch` must therefore be
    in `ISOLATED_MODES`, or a restarted batch would look substrate-less."""
    from personalclaw.workflows.workspace import ISOLATED_MODES, Mode

    assert Mode(batch_compile.BATCH_WORKSPACE_MODE) in ISOLATED_MODES


# ── clause 3: the lease REFUSES a second execution ───────────────────────────


def test_a_SECOND_worker_is_REFUSED_the_same_node(tmp_path, monkeypatch):
    """The security-relevant half of "no double-execution".

    Asserted on the REFUSAL, not on the existence of a lease row: a lease that records a claim and
    still lets the second worker run is worse than no lease, because the recorded claim makes it
    look protected. So this takes a claim as one holder and asserts the second holder is turned
    away with a reason.
    """
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    granted, _ = leases.acquire_claim("run1:cache_0", "worker-a")
    assert granted is not None and granted.holder == "worker-a"

    second, reason = leases.acquire_claim("run1:cache_0", "worker-b")
    assert second is None, "a second worker was allowed to execute the same node"
    assert reason


def test_the_claim_is_PER_NODE_so_the_fanout_still_fans_out(tmp_path, monkeypatch):
    """A run-scoped claim would serialize the very fan-out the lease protects. Two branches of ONE
    run must both be claimable — the lease prevents double-execution of a branch, not concurrency
    between branches."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    first, _ = leases.acquire_claim("run1:cache_0", "worker-a")
    second, _ = leases.acquire_claim("run1:queue_1", "worker-b")
    assert first is not None and second is not None


def test_dispatch_stage_takes_the_claim_BEFORE_it_spawns(tmp_path, monkeypatch):
    """The ORDER is the control. A claim taken after the spawn records the claim without preventing
    the double execution — both workers would already have spawned by the time either looked. So
    the second dispatch must not reach `spawn` at all.
    """
    import asyncio

    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_stage
    from personalclaw.workflows.models import InstanceState, Node, NodeKind

    spawns: list[str] = []

    class FakeSubagents:
        def spawn(self, **kw):
            spawns.append(kw.get("task", ""))
            return type("Info", (), {"id": "sub1", "error": ""})()

    node = Node(kind=NodeKind.STAGE, id="cache_0", config={"prompt": "do the thing"})
    ctx = BindingContext()

    first = asyncio.run(
        dispatch_stage(node, ctx, subagents=FakeSubagents(), run_id="run-lease", depth=0)
    )
    assert first.state is InstanceState.RUNNING
    assert len(spawns) == 1

    second = asyncio.run(
        dispatch_stage(node, ctx, subagents=FakeSubagents(), run_id="run-lease", depth=0)
    )
    assert len(spawns) == 1, "the second dispatch SPAWNED — the lease did not prevent it"
    assert second.state is InstanceState.DEGRADED
    assert "not executing twice" in (second.degraded_reason or "")


# ── clause 2: the tool-handler seam ──────────────────────────────────────────


def test_an_ORCHESTRATION_tool_is_denied_to_a_leaf_at_every_depth(monkeypatch):
    """A leaf that can spawn fans out without a budget, and the depth counter alone would let it
    happen once per level. Denied at the HANDLER because that is the only place a tool call can
    actually be refused — a filtered list computed at compile time is documentation."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "2")
    for tool in sorted(batch_compile.ORCHESTRATION_TOOLS):
        assert mcp_shared.leaf_tool_denial(tool), f"{tool} was not denied to a leaf"


def test_the_PARENT_is_not_restricted(monkeypatch):
    """Depth 0 is the parent, not a leaf. If the seam restricted it, `subagent_run` itself would be
    denied and the batch could never be launched at all."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "0")
    assert mcp_shared.leaf_tool_denial("subagent_run") == ""


def test_a_RESEARCH_leaf_is_denied_write_tools(monkeypatch):
    """The capability class, enforced. `is_write_tool` owns the classification — restating it here
    would create a second policy that drifts from the one the compiler linted against."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    monkeypatch.setenv(mcp_shared.LEAF_READ_ONLY_KEY, "1")
    assert mcp_shared.leaf_tool_denial("artifact_update")
    # A read tool stays available: a research leaf that cannot read cannot research.
    assert mcp_shared.leaf_tool_denial("memory_recall") == ""


def test_a_MUTATING_leaf_may_write_but_still_may_not_orchestrate(monkeypatch):
    """The two rules are independent. Collapsing them would either give a mutating leaf the ability
    to fan out or deny a declared writer its writes."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    monkeypatch.delenv(mcp_shared.LEAF_READ_ONLY_KEY, raising=False)
    assert mcp_shared.leaf_tool_denial("artifact_update") == ""
    assert mcp_shared.leaf_tool_denial("workflow_start")


def test_the_denial_is_enforced_through_call_tool_with_logging(monkeypatch):
    """The seam is the chokepoint every in-process MCP tool call crosses. Asserted by driving the
    real handler wrapper: a denial the wrapper does not apply is a denial that does not exist."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    called: list[str] = []

    out = mcp_shared.call_tool_with_logging(
        "subagent_run",
        {},
        lambda n, a: a,
        lambda n, a: called.append(n) or "ran",
        session_key="mcp_core",
        downstream_service="test",
    )
    assert called == [], "a denied tool still reached its handler"
    assert out.startswith("Error:")


def test_the_leaf_ENV_is_secret_filtered():
    """A leaf processes content the parent fetched — the untrusted direction — so the parent's
    credentials are exactly what must not travel with it. The secret TEST is reused from
    `workspace.looks_secret`; a second list of credential-ish fragments would drift, and the copy
    that drifted would be the one letting a token through."""
    from personalclaw import mcp_shared

    env = mcp_shared.leaf_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/u",
            "OPENAI_API_KEY": "sk-live",
            "GITHUB_TOKEN": "ghp_x",
            "DB_PASSWORD": "hunter2",
        },
        {"__wf_depth": "1", "__wf_run_id": "r1"},
    )
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    for leaked in ("OPENAI_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD"):
        assert leaked not in env, f"{leaked} reached the leaf env"
    assert env["__wf_depth"] == "1"


def test_the_spawn_path_WRITES_the_flags_the_seam_reads(monkeypatch):
    """The inert-control check. A gate on a value nobody writes is not a gate, so this asserts the
    WRITER: `leaf_spawn_env` must emit the depth and the read-only flag the handler reads, with the
    child's depth one deeper than the parent's."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY, leaf_spawn_env
    from personalclaw.workflows.models import Node, NodeKind

    node = Node(kind=NodeKind.STAGE, id="cache_0", config={})
    env = leaf_spawn_env(node, {"capability": "research"}, run_id="r1", depth=0)
    assert env[WF_DEPTH_KEY] == "1", "the child did not get a deeper depth than its parent"
    assert env[mcp_shared.LEAF_READ_ONLY_KEY] == "1"
    assert env["__wf_node_id"] == "cache_0"

    mutating = leaf_spawn_env(node, {"capability": "mutating"}, run_id="r1", depth=0)
    assert mcp_shared.LEAF_READ_ONLY_KEY not in mutating


def test_the_compiler_EMITS_the_capability_the_engine_reads():
    """The compile→enforce chain, end to end at the contract level. `capability` was deliberately
    withheld from node config while nothing read it; it is emitted now that `leaf_spawn_env` reads
    it. A batch compiled without it would run every leaf unrestricted."""
    result = batch_compile.compile_batch(
        [leaf("cache"), leaf("queue", capability=Capability.MUTATING, writes=["out/queue.md"])]
    )
    configs = {c["id"]: c["config"] for c in result.spec["root"]["children"]}
    values = sorted(c["capability"] for c in configs.values())
    assert values == ["mutating", "research"]


def test_compile_result_no_longer_claims_tool_denials_are_UNENFORCED():
    """`unenforced()` is believed, so it must stay true in both directions. Leaving the tool-denial
    line in it after building the seam would understate the system; the mirror risk is claiming a
    control that does not exist, which is why `workspace_mode` stays listed."""
    result = batch_compile.compile_batch([leaf("cache"), leaf("queue")])
    pending = " ".join(result.unenforced())
    assert "tool denials" not in pending
    # `workspace_mode` also LEFT the pending list once the top-level `workspace:` block made the
    # run-start applier actually provision. `timeout_secs` stays: there is genuinely no per-node
    # timeout override to bind to, so claiming it would be the mirror error.
    assert "workspace_mode" not in pending
    assert "timeout_secs" in pending
    enforced = " ".join(result.enforced())
    assert "leaf_tool_denial" in enforced or "tool denials" in enforced
    assert "no double-execution" in enforced


def test_forbidden_declarations_stays_EMPTY():
    """The standing persona prohibition (amendment (a)). Checked here too because this module is
    where a future author wiring a "role" through the batch seam would most plausibly add one."""
    assert batch_compile.forbidden_declarations() == []


# ── clause 4: the agent roster + drift check ─────────────────────────────────


class _Profile:
    def __init__(self, description="", model="", tools=None, skills=None):
        self.description = description
        self.model = model
        self.tools = tools or []
        self.skills = skills or []


def test_a_batch_naming_an_UNKNOWN_agent_is_refused_at_compile(monkeypatch):
    """The roster's PRODUCTION consumer, asserted through `compile_batch` rather than by calling
    `roster.*` — a test that drives the roster directly is what made it look shipped while no
    module in `src/` imported it.

    An unknown agent is an ERROR because `subagent._validate_agent` would fail the spawn anyway
    (C1.3 made it a typed error, never a silent downgrade). Catching it at compile costs nothing;
    catching it at spawn has already minted a run whose branches all fail on one typo."""
    monkeypatch.setattr(roster, "catalog", lambda agents=None: [])

    result = batch_compile.compile_batch([leaf("cache", agent="no-such-agent"), leaf("queue")])
    assert result.ok is False
    codes = [f.code for f in result.findings]
    assert "unknown_agent" in codes
    assert any("no-such-agent" in f.message for f in result.findings)


def test_a_DISPLAY_NAME_reference_lands_in_the_spec_as_the_CONFIG_KEY(monkeypatch):
    """Slug-matching resolves the reference; the CONFIG KEY is what gets persisted.

    This is the distinction the clause turns on, and writing the slug instead would have been a
    runtime break rather than a hardening: `engine.dispatch_stage` reads `config["agent"]` and hands
    it to `spawn`, whose `_validate_agent` checks membership in `AppConfig.agents` — a dict keyed by
    the config key. `my-researcher` is not a key of `{"My Researcher": ...}`, so a persisted slug
    would fail every multi-word agent with "unknown agent". Slugs are the MATCHING key; the config
    key is the BINDING value."""
    entry = roster.RosterEntry(slug="my-researcher", name="My Researcher")
    monkeypatch.setattr(roster, "catalog", lambda agents=None: [entry])

    result = batch_compile.compile_batch([leaf("cache", agent="my researcher"), leaf("queue")])
    assert result.ok is True, [f.message for f in result.findings]
    configs = {c["id"]: c["config"] for c in result.spec["root"]["children"]}
    bound = [c["agent"] for c in configs.values() if "agent" in c]
    assert bound == ["My Researcher"], f"expected the config key, got {bound}"


def test_an_UNPINNED_leaf_emits_no_agent_key(monkeypatch):
    """Homogeneous by default: no pin means inherit the parent's binding. An always-present
    `agent: ""` would read as a pin to nothing in particular."""
    monkeypatch.setattr(roster, "catalog", lambda agents=None: [])
    result = batch_compile.compile_batch([leaf("cache"), leaf("queue")])
    assert result.ok is True
    for child in result.spec["root"]["children"]:
        assert "agent" not in child["config"]


def test_the_roster_projects_over_the_SAME_source_validate_agent_consults():
    """One source for "which agents exist". `roster.catalog` reads `AppConfig.load().agents` and
    `subagent._validate_agent` checks `AppConfig.load().agents` — the same dict. Asserted by AST so
    a future edit that introduced a second enumeration trips here rather than drifting silently."""
    import ast
    import inspect

    from personalclaw import subagent as subagent_mod

    def _reads_appconfig_agents(fn) -> bool:
        tree = ast.parse(inspect.getsource(fn).lstrip())
        return any(
            isinstance(n, ast.Attribute) and n.attr == "agents" for n in ast.walk(tree)
        ) and "AppConfig" in inspect.getsource(fn)

    assert _reads_appconfig_agents(roster.catalog)
    assert _reads_appconfig_agents(subagent_mod._validate_agent)


def test_the_roster_is_a_PROJECTION_over_config_agents():
    """Not a second registry. `AgentDefinition`s live in config, so a roster storing its own copy
    would be a second source of truth that drifts the moment a user renames one."""
    entries = roster.catalog({"My Researcher": _Profile(description="reads things")})
    assert [e.slug for e in entries] == ["my-researcher"]
    assert entries[0].name == "My Researcher", "the config key must survive as the spawn name"
    assert entries[0].description == "reads things"


def test_slugs_are_RENAME_PROOF_and_display_names_are_presentation_only():
    """Templates reference agents by slug precisely so a display-name change does not break them.
    Case is folded so two agents differing only in case cannot occupy two rows."""
    assert roster.slugify("Deep Researcher") == "deep-researcher"
    assert roster.slugify("deep researcher") == roster.slugify("DEEP RESEARCHER")
    assert roster.slugify("") == "agent"


def test_reserved_system_agents_are_ALWAYS_active():
    """A reserved system agent is part of the platform; a user's own agent is offered only when
    something names it, which is what keeps a simple run's roster small."""
    from personalclaw.agents.defaults import LOOP_WORKER_AGENT_NAME

    entries = roster.catalog({LOOP_WORKER_AGENT_NAME: _Profile(), "mine": _Profile()})
    by_slug = {e.slug: e for e in entries}
    assert by_slug[roster.slugify(LOOP_WORKER_AGENT_NAME)].activation == "always"
    assert by_slug[roster.slugify(LOOP_WORKER_AGENT_NAME)].reserved is True
    assert by_slug["mine"].activation == "conditional"
    assert all(e.activation in roster.ACTIVATIONS for e in entries)


def test_the_DRIFT_CHECK_names_the_slug_that_broke():
    """Returns the unresolved names rather than a bool: a check reporting only "something drifted"
    sends a reader back to grep for it."""
    agents = {"researcher": _Profile()}
    assert roster.unresolved_slugs(["researcher"], agents) == []
    assert roster.unresolved_slugs(["researcher", "deleted-one"], agents) == ["deleted-one"]


def test_the_drift_check_walks_EVERY_node_depth():
    """`agent` is a per-node config key, so checking only the root would pass a spec whose fifth
    leaf points at an agent the user deleted — discovered at run time, the most expensive moment.

    Walks a hand-built nested spec rather than a compiled one: `compile_batch` now REFUSES an
    unresolvable agent (that is the point of `agent_lint`), so it can no longer produce the drifted
    spec this walker exists to catch. The drift the walker still has to find comes from templates
    authored elsewhere and from an agent deleted AFTER a spec was written."""
    spec = {
        "root": {
            "kind": "parallel",
            "children": [
                {"kind": "stage", "id": "a", "config": {"agent": "researcher"}},
                {
                    "kind": "sequence",
                    "id": "nested",
                    "children": [
                        {"kind": "stage", "id": "deep", "config": {"agent": "Deep Auditor"}}
                    ],
                },
            ],
        }
    }
    found = roster.referenced_slugs(spec)
    assert found == ["deep-auditor", "researcher"], "a nested leaf's agent was missed"
    assert roster.unresolved_slugs(found, {"researcher": _Profile()}) == ["deep-auditor"]


def test_every_BUNDLED_TEMPLATE_references_a_resolvable_agent():
    """The drift check pointed at the real corpus — this is the gate that fails when a template
    names an agent that no longer exists. Bundled templates are the set we control, so a
    reference here is a defect rather than a user's own choice."""
    from personalclaw.workflows import defs as defs_mod

    unresolved: dict[str, list[str]] = {}
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None or not getattr(provider, "readonly", False):
            continue
        for spec in _bundled_specs(provider):
            slugs = roster.referenced_slugs(spec)
            missing = roster.unresolved_slugs(slugs)
            if missing:
                unresolved[str(spec.get("name", "?"))] = missing
    assert unresolved == {}, f"templates reference unknown agent slugs: {unresolved}"


def _bundled_specs(provider) -> list[dict]:
    """The provider's specs, tolerantly — a provider that cannot enumerate is skipped rather than
    failing the gate, because this test's subject is slug drift and not provider health."""
    import asyncio
    import inspect

    try:
        listed = provider.list_defs()
        if inspect.isawaitable(listed):
            listed = asyncio.run(listed)
    except Exception:
        return []
    specs = []
    for item in listed or []:
        raw = item if isinstance(item, dict) else getattr(item, "to_dict", lambda: {})()
        if isinstance(raw, dict):
            specs.append(raw)
    return specs


def test_the_roster_ROUND_TRIPS_through_its_dict():
    """A projection nobody can serialize is a projection no surface can render."""
    entry = roster.catalog({"mine": _Profile(description="d", model="fast", tools=["read"])})[0]
    raw = json.loads(json.dumps(entry.to_dict()))
    assert raw["slug"] == "mine"
    assert raw["capabilities"] == ["read"]
    assert raw["model_tier_hint"] == "fast"
