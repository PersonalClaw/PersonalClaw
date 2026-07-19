"""Native filesystem workflow provider — CRUD + markdown round-trip."""

from __future__ import annotations

import tempfile

import pytest

from personalclaw.workflows.native import (
    NativeWorkflowProvider,
    assemble_markdown,
    parse_frontmatter,
    parse_steps,
    slugify,
)
from personalclaw.workflows.models import Workflow, WorkflowScope, WorkflowStep


def _prov() -> NativeWorkflowProvider:
    return NativeWorkflowProvider(storage_dir=tempfile.mkdtemp())


# ── markdown parse / assemble ────────────────────────────────────────────────


def test_parse_frontmatter_splits_scalars_and_body():
    text = "---\nid: wf-1\nname: x\n---\n\n# x\n\n1. step one\n"
    fm, body = parse_frontmatter(text)
    assert fm["id"] == "wf-1"
    assert fm["name"] == "x"
    assert "step one" in body


def test_parse_steps_numbered_and_blockquote_instruction():
    body = "# t\n\n1. Run tests\n   > use the test command\n2. Commit\n"
    steps = parse_steps(body)
    assert [s.title for s in steps] == ["Run tests", "Commit"]
    assert steps[0].id == "s1" and steps[1].id == "s2"
    assert steps[0].instruction == "use the test command"
    assert steps[1].instruction == ""


def test_assemble_then_parse_roundtrips_steps():
    wf = Workflow(
        id="wf-x", name="demo", description="d", scope=WorkflowScope.GLOBAL,
        steps=[WorkflowStep(id="s1", title="A", instruction="do A"), WorkflowStep(id="s2", title="B")],
    )
    md = assemble_markdown(wf)
    fm, body = parse_frontmatter(md)
    steps = parse_steps(body)
    assert fm["name"] == "demo"
    assert [s.title for s in steps] == ["A", "B"]
    assert steps[0].instruction == "do A"


def test_slugify():
    assert slugify("Git Commit Flow!") == "git-commit-flow"
    assert slugify("") == "workflow"


# ── provider CRUD ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_writes_workflow_md():
    prov = _prov()
    wf = await prov.create_workflow(
        name="git-commit", description="flow", scope="workspace", scope_ref="/repo/a",
        match_text="commit changes", tags=["git"],
        steps=[{"title": "Test"}, {"title": "Commit", "instruction": "conventional"}],
    )
    assert wf.id.startswith("wf-")
    assert wf.name == "git-commit"
    assert wf.scope == WorkflowScope.WORKSPACE
    assert len(wf.steps) == 2
    got = await prov.get_workflow(wf.id)
    assert got is not None
    assert got.steps[1].instruction == "conventional"
    assert got.provider == "native"


@pytest.mark.asyncio
async def test_create_junk_name_is_slugified():
    # slugify is forgiving: punctuation-only / empty names fall back to a safe
    # default rather than erroring, so the user always gets a valid workflow.
    prov = _prov()
    wf = await prov.create_workflow(name="!!!")
    assert wf.name == "workflow"
    wf2 = await prov.create_workflow(name="My Cool Flow!")
    assert wf2.name == "my-cool-flow"


@pytest.mark.asyncio
async def test_list_filters_by_scope_and_ref():
    prov = _prov()
    await prov.create_workflow(name="a", scope="workspace", scope_ref="/repo/a")
    await prov.create_workflow(name="b", scope="workspace", scope_ref="/repo/b")
    await prov.create_workflow(name="g", scope="global")
    on_a, total_a = await prov.list_workflows(scope=WorkflowScope.WORKSPACE, scope_ref="/repo/a")
    assert total_a == 1 and on_a[0].name == "a"
    on_b, total_b = await prov.list_workflows(scope=WorkflowScope.WORKSPACE, scope_ref="/repo/b")
    assert total_b == 1 and on_b[0].name == "b"
    glob, total_g = await prov.list_workflows(scope=WorkflowScope.GLOBAL)
    assert total_g == 1 and glob[0].name == "g"
    allwf, total = await prov.list_workflows()
    assert total == 3


@pytest.mark.asyncio
async def test_list_filters_by_tag():
    prov = _prov()
    await prov.create_workflow(name="a", tags=["git", "vcs"])
    await prov.create_workflow(name="b", tags=["docs"])
    tagged, total = await prov.list_workflows(tag="git")
    assert total == 1 and tagged[0].name == "a"


@pytest.mark.asyncio
async def test_update_changes_fields_and_steps():
    prov = _prov()
    wf = await prov.create_workflow(name="x", description="old", steps=[{"title": "A"}])
    upd = await prov.update_workflow(
        wf.id, description="new", steps=[{"title": "B"}, {"title": "C"}]
    )
    assert upd.description == "new"
    assert [s.title for s in upd.steps] == ["B", "C"]
    assert upd.steps[0].id == "s1" and upd.steps[1].id == "s2"
    # update is durable
    got = await prov.get_workflow(wf.id)
    assert got.description == "new" and len(got.steps) == 2


@pytest.mark.asyncio
async def test_update_unknown_returns_none():
    prov = _prov()
    assert await prov.update_workflow("wf-nope", description="x") is None


@pytest.mark.asyncio
async def test_delete_removes_dir():
    prov = _prov()
    wf = await prov.create_workflow(name="x")
    assert await prov.delete_workflow(wf.id) is True
    assert await prov.get_workflow(wf.id) is None
    assert await prov.delete_workflow(wf.id) is False
