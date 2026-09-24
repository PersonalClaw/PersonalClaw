"""Project-scoped memory locality (WORK-CONTAINERS §1.6 — WF2WOR-11).

Two claims, both driven through the production paths rather than asserted on helpers:

1. **A project-owned run's memory is project-local.** The run controller binds the project's
   `context_dir` as the stage cwd, and memory is cwd-partitioned — so what the run writes
   lands under `workspace/_ext/<slug(context_dir)>` instead of the shared `_ext/_default`
   pile every project's runs would otherwise stir together. Driven by running a real
   controller to completion and then writing through the store `get_memory_for` resolves.

2. **Recall is partition-first, and locality is ORDERING ONLY — never admission.** A
   project-local session recalls from its own partition first, then from the global
   partition, whose hits are source-labeled and fenced. The load-bearing case is the
   inverse one: when the local partition has NOTHING and the global partition has the
   answer, the global hit still comes back. A locality rule that dropped it would delete a
   real recall result, which from the user's chair is indistinguishable from memory loss.
"""

from __future__ import annotations

import pytest

from personalclaw import memory_locality, security
from personalclaw.config.loader import memory_dir_for_cwd
from personalclaw.context import ContextBuilder
from personalclaw.context_engine import active_recall_block
from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.workflows import store as wf_store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """An isolated home, belt AND braces.

    `PERSONALCLAW_HOME` covers every `config_dir()` call site — including the stores that
    bound the function at import time, which a `config_dir` patch alone would miss — and the
    module-level patches cover the two stores this test drives directly. The partition cache
    is a process global: a leaked entry from another test would answer with a store rooted in
    a different home, so it is replaced per test (xdist-safe).
    """
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    from personalclaw import context as context_mod

    monkeypatch.setattr(context_mod, "_memory_stores", {})
    return home


def _action_spec() -> dict:
    return {
        "name": "locality",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "a", "config": {"provider": "recorder"}}],
        },
    }


def _recording_provider(seen: list[dict]):
    """An action provider that records the payload the engine handed it."""

    class P:
        async def execute(self, cfg, ctx, timeout=30):  # noqa: ARG002
            seen.append(dict(getattr(ctx, "payload", None) or {}))

            class R:
                success = True
                stdout = '{"ok": true}'
                outcome = ""
                exit_code = 0
                stderr = ""
                error = ""
                duration_ms = 0

            return R()

    return lambda name: P()


async def _run_in_project(project_id: str, *, cwd: str = "") -> tuple[RunController, list[dict]]:
    seen: list[dict] = []
    spec = _action_spec()
    run = wf_store.create(WorkflowRun(id="", workflow_name="locality", project_id=project_id))
    wf_store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(get_provider=_recording_provider(seen), cwd=cwd)
    )
    assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE
    return controller, seen


# ── clause 1: a project-owned run's memory lands in the project partition ────


class TestProjectPartitionBinding:
    async def test_a_project_run_writes_into_its_own_partition_not_default(self, _isolated_home):
        project = HierarchyStore().create_project(name="Widget")
        context_dir = str(HierarchyStore().context_dir(project.id))

        controller, _seen = await _run_in_project(project.id)

        # The run's stage cwd IS the project's context dir…
        assert controller.services.cwd == context_dir
        # …and memory resolved for that cwd is the project's own partition, not the shared one.
        partition = memory_dir_for_cwd(context_dir)
        assert partition != memory_dir_for_cwd(None)
        store = ContextBuilder.get_memory_for(controller.services.cwd)
        store.add_preference("widget cadence is weekly")

        written = [
            p for p in partition.rglob("*.md") if "widget cadence is weekly" in p.read_text()
        ]
        assert written, f"no memory written under the project partition {partition}"
        default_partition = memory_dir_for_cwd(None)
        leaked = [
            p
            for p in default_partition.rglob("*.md")
            if "widget cadence is weekly" in p.read_text()
        ]
        assert not leaked, "project memory leaked into the shared _default partition"

    async def test_an_explicit_cwd_wins_over_the_locality_default(self, tmp_path):
        """A worktree (or any caller-supplied cwd) is a deliberate binding; the memory-locality
        default must never move a run out of the tree it was provisioned into."""
        project = HierarchyStore().create_project(name="Bound")
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        controller, _seen = await _run_in_project(project.id, cwd=str(worktree))

        assert controller.services.cwd == str(worktree)

    async def test_a_project_less_run_keeps_an_empty_cwd(self):
        controller, _seen = await _run_in_project("")

        assert controller.services.cwd == ""

    async def test_the_owning_project_reaches_an_action_provider(self):
        """Clause 3's writer plumbing, proven at the engine seam: the run's project id is in
        the payload `knowledge-persist` reads, threaded by the controller — not something a
        template had to restate."""
        project = HierarchyStore().create_project(name="Tagged")

        _controller, seen = await _run_in_project(project.id)

        assert seen and seen[0].get("project_id") == project.id
        assert seen[0].get("run_id")


# ── clause 2: partition-first recall, ordering only ─────────────────────────


class TestPartitionFirstRecall:
    @staticmethod
    def _builder() -> ContextBuilder:
        builder = ContextBuilder()
        builder.memory.init()  # the GLOBAL partition store (registered under _default)
        return builder

    @staticmethod
    def _local_store(context_dir: str):
        store = ContextBuilder.get_memory_for(context_dir)
        return store

    def test_local_hits_come_first_and_global_hits_are_labeled_and_fenced(self):
        project = HierarchyStore().create_project(name="Widget")
        context_dir = str(HierarchyStore().context_dir(project.id))
        builder = self._builder()
        builder.memory.add_preference("zorbulon owner is Dana")
        self._local_store(context_dir).add_preference("zorbulon cadence is weekly")

        block = active_recall_block(builder, "zorbulon", cwd=context_dir, memory_store=None)

        # The FTS renderer wraps matched terms (`>>>zorbulon<<<`), so the assertions match on
        # the surrounding text — the recalled FRAGMENT, not the reconstructed sentence.
        assert "cadence is weekly" in block  # the local partition
        assert "owner is Dana" in block  # the global partition — still admitted
        assert block.index("cadence is weekly") < block.index("owner is Dana")  # ordering
        assert memory_locality.CROSS_PARTITION_SOURCE in block  # explicitly source-labeled
        # Fenced via the real fencing API — an attributed fence carries no bare marker, so
        # `is_fenced` is the only correct check here.
        assert security.is_fenced(block)

    def test_a_global_only_hit_still_surfaces_ordering_not_admission(self):
        """The clause that keeps locality honest: nothing local, answer global → returned."""
        project = HierarchyStore().create_project(name="Empty")
        context_dir = str(HierarchyStore().context_dir(project.id))
        builder = self._builder()
        builder.memory.add_preference("zorbulon owner is Dana")
        self._local_store(context_dir)  # the project partition exists and is EMPTY

        block = active_recall_block(builder, "zorbulon", cwd=context_dir, memory_store=None)

        assert "owner is Dana" in block
        assert memory_locality.CROSS_PARTITION_SOURCE in block
        assert security.is_fenced(block)

    def test_a_global_partition_session_gets_no_cross_partition_block(self):
        """A session already IN the global partition has no other partition to reach for; a
        "cross-partition" label pointing at itself would be a lie and a duplicated block."""
        builder = self._builder()
        builder.memory.add_preference("zorbulon owner is Dana")

        block = active_recall_block(builder, "zorbulon", cwd=None, memory_store=None)

        assert "owner is Dana" in block
        assert memory_locality.CROSS_PARTITION_SOURCE not in block
        assert block.count("owner is Dana") == 1

    def test_a_named_memory_provider_opts_out_of_locality(self):
        """Provider memory is not cwd-partitioned, so there is no partition pair to compose."""
        project = HierarchyStore().create_project(name="Provider")
        context_dir = str(HierarchyStore().context_dir(project.id))
        builder = self._builder()
        builder.memory.add_preference("zorbulon owner is Dana")
        local = self._local_store(context_dir)
        local.add_preference("zorbulon cadence is weekly")

        composed = memory_locality.compose_recall(
            builder,
            "zorbulon",
            cwd=context_dir,
            local="zorbulon cadence is weekly",
            memory_store="native",
        )

        assert composed == "zorbulon cadence is weekly"
        assert memory_locality.CROSS_PARTITION_SOURCE not in composed
