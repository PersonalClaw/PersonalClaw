"""Tests for the agent-facing native task tools (task_*, project_*,
task_list_create) on the builtin tool provider."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.models import coerce_task_field


@pytest.fixture
def provider(tmp_path):
    registry._providers.clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    store = tmp_path / "home"
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=store),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=store),
    ):
        yield NativeBuiltinToolProvider(ws)
    registry._providers.clear()


@pytest.mark.asyncio
async def test_task_tools_listed(provider):
    names = {t.name for t in await provider.list_tools()}
    assert {
        "task_create",
        "task_list",
        "task_get",
        "task_update",
        "task_ready",
        "task_search",
        "project_create",
        "project_list",
        "task_list_create",
    } <= names


@pytest.mark.asyncio
async def test_task_create_and_get(provider):
    r = await provider.invoke("task_create", {"title": "Write the spec", "priority": "high"})
    assert r.success and "created task" in r.output
    tid = r.output.split("created task ")[1].split(":")[0]
    g = await provider.invoke("task_get", {"id": tid})
    assert g.success and "Write the spec" in g.output


@pytest.mark.asyncio
async def test_task_create_requires_title(provider):
    r = await provider.invoke("task_create", {"title": "   "})
    assert not r.success and "title" in r.error


@pytest.mark.asyncio
async def test_task_list_and_search(provider):
    await provider.invoke("task_create", {"title": "Alpha task"})
    await provider.invoke("task_create", {"title": "Beta task"})
    lst = await provider.invoke("task_list", {})
    assert lst.success and "Alpha task" in lst.output and "Beta task" in lst.output
    found = await provider.invoke("task_search", {"query": "alpha"})
    assert found.success and "Alpha task" in found.output and "Beta task" not in found.output


@pytest.mark.asyncio
async def test_task_update_status_and_complete_gate(provider):
    r = await provider.invoke(
        "task_create",
        {"title": "Ship", "exit_criteria": [{"description": "tests pass", "met": False}]},
    )
    tid = r.output.split("created task ")[1].split(":")[0]
    # Completing while a criterion is unmet is rejected with a helpful hint.
    blocked = await provider.invoke("task_update", {"id": tid, "status": "done"})
    assert not blocked.success and "exit criteria" in blocked.error
    # Mark the criterion met, then completing succeeds.
    await provider.invoke(
        "task_update", {"id": tid, "exit_criteria": [{"description": "tests pass", "met": True}]}
    )
    ok = await provider.invoke("task_update", {"id": tid, "status": "done"})
    assert ok.success and "[done]" in ok.output


@pytest.mark.asyncio
async def test_task_update_reads_criteria_sent_as_json_text_and_the_task_closes(provider):
    """A model sent `exit_criteria` as the TEXT of its JSON list, twice, once right after reading
    the tool's schema. It was stored as one criterion whose description was that JSON, which no
    update could meet, so the task stayed open and the worker told the user to close it by hand."""
    r = await provider.invoke(
        "task_create",
        {"title": "Review the README", "exit_criteria": [{"description": "Storage is documented"}]},
    )
    tid = r.output.split("created task ")[1].split(":")[0]
    refused = await provider.invoke("task_update", {"id": tid, "status": "done"})
    assert not refused.success and "Storage is documented" in refused.error
    sent = json.dumps([{"description": "Storage is documented", "met": True}])
    done = await provider.invoke(
        "task_update", {"id": tid, "exit_criteria": sent, "status": "done"}
    )
    assert done.success, done.error
    assert "[done]" in done.output and "1/1 criteria" in done.output
    got = await provider.invoke("task_get", {"id": tid})
    assert "[x] Storage is documented" in got.output and "description" not in got.output


@pytest.mark.asyncio
async def test_task_create_reads_each_list_sent_as_json_text(provider):
    a = await provider.invoke("task_create", {"title": "Draft"})
    aid = a.output.split("created task ")[1].split(":")[0]
    b = await provider.invoke(
        "task_create",
        {
            "title": "Publish",
            "labels": '["docs", "release"]',
            "exit_criteria": '["tests pass", "README updated"]',
            "action_plan": '["build", "upload"]',
            "depends_on": json.dumps([aid]),
        },
    )
    assert b.success, b.error
    assert "0/2 criteria" in b.output
    bid = b.output.split("created task ")[1].split(":")[0]
    task = await registry.get_task(bid)
    assert task.labels == ["docs", "release"]
    assert [c["description"] for c in task.exit_criteria] == ["tests pass", "README updated"]
    assert [s["content"] for s in task.action_plan] == ["build", "upload"]
    assert task.prerequisite_ids() == [aid]
    # A sentence is still the one criterion it always was.
    c = await provider.invoke("task_create", {"title": "Tidy", "exit_criteria": "desk is clear"})
    assert "0/1 criteria" in c.output


@pytest.mark.asyncio
async def test_task_search_reads_its_filters_sent_as_text(provider):
    await provider.invoke("task_create", {"title": "Open errand"})
    d = await provider.invoke("task_create", {"title": "Done errand"})
    did = d.output.split("created task ")[1].split(":")[0]
    await provider.invoke("task_update", {"id": did, "status": "done"})
    for status in ('["done"]', "done", ["done"]):
        found = await provider.invoke("task_search", {"query": "errand", "status": status})
        assert "Done errand" in found.output and "Open errand" not in found.output, status
    tagged = await provider.invoke("task_create", {"title": "Tagged errand", "labels": ["home"]})
    assert tagged.success
    found = await provider.invoke("task_search", {"query": "errand", "tags": '["home"]'})
    assert "Tagged errand" in found.output and "Open errand" not in found.output


@pytest.mark.asyncio
async def test_every_list_a_task_tool_takes_is_declared_an_array(provider):
    """A model reads the schema, not the handler: each argument the store keeps as a list is
    declared an ARRAY with its items, so the model is told to send the list itself."""
    defs = {d.name: d for d in await provider.list_tools()}
    for tool in ("task_create", "task_update"):
        props = defs[tool].parameters["properties"]
        lists = {
            name
            for name in props
            if name not in ("id", "title")
            and isinstance(
                coerce_task_field(
                    "dependencies" if name == "depends_on" else name, None, strict=False
                ),
                list,
            )
        }
        assert lists == {"labels", "exit_criteria", "action_plan", "depends_on"}, tool
        for name in lists:
            assert props[name]["type"] == "array", (tool, name)
            assert props[name]["items"]["type"] in ("string", "object"), (tool, name)
    search = defs["task_search"].parameters["properties"]
    for name in ("status", "priority", "tags"):
        assert search[name] == {"type": "array", "items": {"type": "string"}}, name


@pytest.mark.asyncio
async def test_task_update_coerces_status_synonyms(provider):
    # An LLM commonly emits "complete"/"todo"/"in-progress" instead of the canonical
    # done/open/in_progress — coerce them so the worker doesn't loop on a confusing
    # (mislabeled) ValueError and leave the cockpit rail stale.
    r = await provider.invoke("task_create", {"title": "Do it"})
    tid = r.output.split("created task ")[1].split(":")[0]
    ip = await provider.invoke("task_update", {"id": tid, "status": "in-progress"})
    assert ip.success and "[in_progress]" in ip.output
    done = await provider.invoke("task_update", {"id": tid, "status": "complete"})
    assert done.success and "[done]" in done.output


@pytest.mark.asyncio
async def test_task_update_rejects_unknown_status_with_clear_hint(provider):
    r = await provider.invoke("task_create", {"title": "X"})
    tid = r.output.split("created task ")[1].split(":")[0]
    bad = await provider.invoke("task_update", {"id": tid, "status": "frobnicated"})
    assert not bad.success
    assert "not a valid status" in bad.error
    assert any("open, in_progress, done" in h for h in (bad.recovery_hints or []))


@pytest.mark.asyncio
async def test_task_ready_respects_dependencies(provider):
    a = await provider.invoke("task_create", {"title": "A"})
    aid = a.output.split("created task ")[1].split(":")[0]
    await provider.invoke("task_create", {"title": "B", "depends_on": [aid]})
    ready = await provider.invoke("task_ready", {})
    assert "A" in ready.output and "B" not in ready.output.replace("B-", "")
    # finish A → B becomes ready
    await provider.invoke("task_update", {"id": aid, "status": "done"})
    ready2 = await provider.invoke("task_ready", {})
    assert "B" in ready2.output


@pytest.mark.asyncio
async def test_project_and_task_list_create_and_derived_label(provider):
    p = await provider.invoke("project_create", {"name": "Website"})
    assert p.success and "Website" in p.output
    pid = p.output.split("id=")[1].rstrip(")")
    tl = await provider.invoke("task_list_create", {"name": "Launch", "project_id": pid})
    assert tl.success
    tlid = tl.output.split("id=")[1].split(",")[0]
    t = await provider.invoke("task_create", {"title": "Build it", "task_list_id": tlid})
    assert t.success and "@Website" in t.output  # project label derived from the list

    lst = await provider.invoke("project_list", {})
    assert "Website" in lst.output and "Launch" in lst.output


@pytest.mark.asyncio
async def test_task_create_rejects_cycle(provider):
    a = await provider.invoke("task_create", {"title": "A"})
    aid = a.output.split("created task ")[1].split(":")[0]
    b = await provider.invoke("task_create", {"title": "B", "depends_on": [aid]})
    bid = b.output.split("created task ")[1].split(":")[0]
    # A depends on B and B already depends on A → cycle, rejected.
    r = await provider.invoke("task_update", {"id": aid, "depends_on": [bid]})
    assert not r.success and "cycle" in r.error.lower()
