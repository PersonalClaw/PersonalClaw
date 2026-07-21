"""P5b — composable workflows: ref persistence, referential integrity + cycle
rejection (server-authoritative), delete-refuse policy, ref expansion (inline-
flatten + provenance + depth cap), agent-scope scope_ref validation, and the
/graph endpoint."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows import composition as comp
from personalclaw.workflows import native, registry
from personalclaw.workflows.models import Workflow, WorkflowScope, WorkflowStep


@pytest.fixture
def store(tmp_path):
    """A native workflow provider rooted at a temp dir, registered as 'native'."""
    prov = native.NativeWorkflowProvider(storage_dir=str(tmp_path / "wf"))
    registry._providers.clear()
    registry.register_provider(prov)
    yield prov
    registry._providers.clear()


def _run(coro):
    return asyncio.run(coro)


# ── model + serialization ──


class TestRefModel:
    def test_step_is_ref_xor_title(self):
        assert WorkflowStep(id="s1", ref="wf-x").is_ref()
        assert not WorkflowStep(id="s1", title="do it").is_ref()

    def test_ref_round_trips_markdown(self, tmp_path):
        wf = Workflow(
            id="wf-1",
            name="parent",
            steps=[
                WorkflowStep(id="s1", title="first"),
                WorkflowStep(id="s2", ref="wf-child"),
            ],
        )
        md = native.assemble_markdown(wf)
        assert "@ref:wf-child" in md
        fm, body = native.parse_frontmatter(md)
        steps = native.parse_steps(body)
        assert steps[0].title == "first" and not steps[0].is_ref()
        assert steps[1].ref == "wf-child" and steps[1].is_ref()


# ── composition graph (pure) ──


class TestComposition:
    def _wf(self, wid, refs=(), **kw):
        steps = [WorkflowStep(id=f"s{i+1}", ref=r) for i, r in enumerate(refs)]
        return Workflow(id=wid, name=wid, steps=steps, **kw)

    def test_validate_refs_dangling(self):
        a = self._wf("a", refs=["ghost"])
        with pytest.raises(comp.WorkflowIntegrityError):
            comp.validate_refs(a, [a])

    def test_validate_refs_self_cycle(self):
        a = self._wf("a", refs=["a"])
        with pytest.raises(comp.WorkflowCycleError):
            comp.validate_refs(a, [a])

    def test_validate_refs_transitive_cycle(self):
        a = self._wf("a", refs=["b"])
        b = self._wf("b", refs=["a"])
        with pytest.raises(comp.WorkflowCycleError):
            comp.validate_refs(a, [a, b])

    def test_validate_refs_ok_dag(self):
        a = self._wf("a", refs=["b"])
        b = self._wf("b")
        comp.validate_refs(a, [a, b])  # no raise

    def test_referrers(self):
        a = self._wf("a", refs=["b"])
        b = self._wf("b")
        c = self._wf("c", refs=["b"])
        assert {w.id for w in comp.referrers("b", [a, b, c])} == {"a", "c"}

    def test_validate_scope_agent_needs_ref(self):
        wf = Workflow(id="a", name="a", scope=WorkflowScope.AGENT, scope_ref="")
        with pytest.raises(comp.WorkflowIntegrityError):
            comp.validate_scope(wf)
        wf.scope_ref = "reviewer"
        comp.validate_scope(wf)  # no raise

    def test_expand_inline_flatten_with_provenance(self):
        a = Workflow(
            id="a",
            name="ship",
            steps=[
                WorkflowStep(id="s1", title="build"),
                WorkflowStep(id="s2", ref="b"),
            ],
        )
        b = Workflow(
            id="b",
            name="commit",
            steps=[
                WorkflowStep(id="s1", title="stage"),
                WorkflowStep(id="s2", title="commit"),
            ],
        )
        expanded = comp.expand_steps(a, {"a": a, "b": b})
        assert [e.title for e in expanded] == ["build", "stage", "commit"]
        assert expanded[0].source_workflow == "ship" and expanded[0].depth == 0
        assert expanded[1].source_workflow == "commit" and expanded[1].depth == 1

    def test_expand_depth_cap(self):
        # Chain a→b→c→… deeper than MAX_DEPTH; expansion truncates with a marker.
        wfs = {}
        n = comp.MAX_DEPTH + 3
        for i in range(n):
            nxt = [f"w{i+1}"] if i + 1 < n else []
            wfs[f"w{i}"] = Workflow(
                id=f"w{i}",
                name=f"w{i}",
                steps=[WorkflowStep(id="s1", ref=r) for r in nxt]
                or [WorkflowStep(id="s1", title="leaf")],
            )
        expanded = comp.expand_steps(wfs["w0"], wfs)
        assert any("max workflow depth reached" in e.title for e in expanded)

    def test_expand_missing_ref_marker(self):
        a = Workflow(id="a", name="a", steps=[WorkflowStep(id="s1", ref="ghost")])
        expanded = comp.expand_steps(a, {"a": a})
        assert expanded and "missing workflow" in expanded[0].title

    def test_resolve_agent_id(self):
        assert comp.resolve_agent_id("default", "native", None) == "default"
        assert (
            comp.resolve_agent_id(None, "acp:test-cli", "researcher") == "acp:test-cli/researcher"
        )
        assert comp.resolve_agent_id(None, "acp:claude-code", "") == "acp:claude-code"


# ── registry integration (write-path enforcement) ──


class TestRegistryEnforcement:
    def test_create_rejects_dangling_ref(self, store):
        async def go():
            with pytest.raises(comp.WorkflowIntegrityError):
                await registry.create_workflow(name="parent", steps=[{"ref": "ghost"}])
            # rolled back — not persisted
            wfs, _ = await registry.list_all_workflows()
            assert all(w.name != "parent" for w in wfs)

        _run(go())

    def test_create_then_ref_persists(self, store):
        async def go():
            child = await registry.create_workflow(name="child", steps=[{"title": "x"}])
            parent = await registry.create_workflow(
                name="parent", steps=[{"title": "first"}, {"ref": child.id}]
            )
            reloaded = await registry.get_workflow(parent.id)
            assert reloaded.steps[1].ref == child.id

        _run(go())

    def test_update_cycle_rolls_back(self, store):
        async def go():
            a = await registry.create_workflow(name="a", steps=[{"title": "x"}])
            b = await registry.create_workflow(name="b", steps=[{"ref": a.id}])
            # make a reference b → cycle; must reject AND keep a's prior steps
            with pytest.raises(comp.WorkflowCycleError):
                await registry.update_workflow(a.id, steps=[{"ref": b.id}])
            reloaded = await registry.get_workflow(a.id)
            assert reloaded.steps[0].title == "x" and not reloaded.steps[0].is_ref()

        _run(go())

    def test_delete_refused_when_referenced(self, store):
        async def go():
            child = await registry.create_workflow(name="child", steps=[{"title": "x"}])
            await registry.create_workflow(name="parent", steps=[{"ref": child.id}])
            with pytest.raises(registry.WorkflowReferencedError) as ei:
                await registry.delete_workflow(child.id)
            assert any(r["name"] == "parent" for r in ei.value.referrers)

        _run(go())

    def test_agent_scope_requires_ref(self, store):
        async def go():
            with pytest.raises(comp.WorkflowIntegrityError):
                await registry.create_workflow(name="a", scope="agent", steps=[{"title": "x"}])

        _run(go())
