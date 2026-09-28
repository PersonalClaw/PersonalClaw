"""No read shows what another read of the same thing masks.

The schedule list masked a schedule's prompt as ``message`` and sent the same prompt raw inside
``action.config.task_template``, so a key in the prompt was readable from the list: by the
Automations page, by an app allowed to read automations, by a channel's ``/cron list``, by the CLI
and by the agent's own tools. Measured on ``main`` (with #3707 and #3697), every read below handed
out the key, a lifecycle trigger's and a store trigger's action included. The same shape held for a
tag or a memory fact's key: the list masks it, so the name the page sends back is the marker, and
removing the tag or deleting the fact did nothing.

The rest of this file is the census of that shape: a read that shows raw what another read of the
same thing masks. A code loop's command, an artifact's name, tags and text preview, an MCP server's
definition and error, a saved prompt, a notification, a loop's name, a workflow run, the memory
history, a lesson and a plan. Each test fails on ``main``.

The contract: every read masks through one function (`security.redact_for_display`,
`redact_values_for_display` for a blob; a workflow run's reads use the journal's redactor, the one
its other surfaces already use), and every save that sends a masked field back gets it restored
(`keep_masked_*`, `stored_name` for a name) from the copy stored when it is written.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from mcp_owner_allowed import confirmed

# A credential-shaped literal the redactor recognises. Not a real key.
SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"
#: An MCP server's command that cannot resolve: nothing a fixture names is ever started.
UNRESOLVABLE = "/nonexistent/pc-fixture-mcp"
# A URL carrying data out in its query, which the display mask replaces as a whole.
EXFIL = "https://collect.example.net/p?d=" + ("Q" * 60)


def _run(coro):
    return asyncio.run(coro)


def _body(resp) -> Any:
    return json.loads(resp.body.decode() if hasattr(resp, "body") else resp.text)


# ── Automations: every kind, every read ─────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with the three trigger backends the Automations page lists."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.hooks import ScriptHookStore
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    hooks = ScriptHookStore(config_dir=tmp_path)
    monkeypatch.setattr(T, "_hook_store", lambda s: hooks)
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    state = MagicMock()
    state._hook_store = hooks
    return T, state, tmp_path, hooks


def _req(
    method: str, path: str, state: Any, *, body=None, match_info=None, query=None, headers=None
):
    app = web.Application()
    app["state"] = state
    full = path + ("?" + query if query else "")
    req = make_mocked_request(
        method, full, headers=headers or {}, match_info=match_info or {}, app=app
    )
    req["user"] = "tester"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _prompt_schedule(
    home_dir: Path, *, id: str = "digest", name: str = "Nightly digest", granted: bool = True
):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home_dir).upsert(
        Trigger(
            id=id,
            name=name,
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            # Allowed to run its action (`triggers.grants`), as one the owner saved is.
            capabilities={"providers": ["invoke-agent"]} if granted else {},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": f"Summarize the day. The key is {SECRET}."},
                }
            },
        )
    )


def _listed(T, state) -> list[dict]:
    return _body(_run(T.api_triggers(_req("GET", "/api/triggers", state))))["triggers"]


def test_the_schedule_list_masks_the_prompt_inside_the_action_too(home):
    T, state, home_dir, _ = home
    _prompt_schedule(home_dir)
    row = next(t for t in _listed(T, state) if t["id"] == "schedule:digest")
    assert MASK in row["message"], "precondition: the list masks the prompt as `message`"
    assert MASK in row["action"]["config"]["task_template"]
    assert SECRET not in json.dumps(row)


def test_a_command_schedule_shows_its_command_masked(home):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    T, state, home_dir, _ = home
    command = f"curl -s -H 'x-api-key: {SECRET}' https://api.example.com/usage"
    TriggerStore(base_dir=home_dir).upsert(
        Trigger(
            id="usage",
            name="Usage",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"inline": {"provider": "bash", "config": {"command": command}}},
        )
    )
    row = next(t for t in _listed(T, state) if t["id"] == "schedule:usage")
    assert SECRET not in json.dumps(row)


def test_a_store_trigger_row_masks_its_name_what_it_watches_and_its_action(home):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    T, state, home_dir, _ = home
    TriggerStore(base_dir=home_dir).upsert(
        Trigger(
            id="web_watch:feed",
            name=f"Feed {SECRET}",
            kind="web_watch",
            spec={"url": f"https://example.com/feed?key={SECRET}", "poll_interval": 3600},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": f"Summarize it with {SECRET}."},
                }
            },
        )
    )
    row = next(t for t in _listed(T, state) if t["kind"] == "store")
    assert SECRET not in json.dumps(row)


def _prompt_hook(hooks) -> Any:
    from personalclaw.triggers import grants

    hook = hooks.create(
        {
            "name": "On stop",
            "event": "Stop",
            "provider": "invoke-agent",
            "provider_config": {"task_template": f"Report back using {SECRET}."},
        }
    )
    # Allowed to run its action (`triggers.grants`), as one the owner saved is.
    grants.give(hook)
    return hooks.update(hook.id, {"capabilities": hook.capabilities})


def test_a_lifecycle_row_masks_its_action(home):
    T, state, _home_dir, hooks = home
    hook = _prompt_hook(hooks)
    row = next(t for t in _listed(T, state) if t["id"] == f"lifecycle:{hook.id}")
    assert SECRET not in json.dumps(row)


def test_editing_a_lifecycle_trigger_from_its_masked_row_keeps_the_key(home):
    T, state, _home_dir, hooks = home
    hook = _prompt_hook(hooks)
    row = next(t for t in _listed(T, state) if t["id"] == f"lifecycle:{hook.id}")
    # What LifecycleDetail.tsx sends on Save: the row's fields, the name edited.
    body = {"name": "On stop, quietly", "event": row["event"], "matcher": row["matcher"]}
    body["action"] = {"provider": row["action"]["provider"], "config": row["action"]["config"]}
    resp = _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                f"/api/triggers/{row['id']}",
                state,
                body=body,
                match_info={"id": row["id"]},
                headers={"If-Match": row["revision"]},
            )
        )
    )
    assert resp.status == 200, resp.text
    saved = hooks.get(hook.id)
    assert saved.name == "On stop, quietly"
    assert saved.provider_config["task_template"] == f"Report back using {SECRET}."


def test_renaming_a_schedule_from_its_masked_row_keeps_prompt_and_command(home):
    from personalclaw.triggers.store import TriggerStore

    T, state, home_dir, _ = home
    _prompt_schedule(home_dir)
    row = next(t for t in _listed(T, state) if t["id"] == "schedule:digest")
    resp = _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                f"/api/triggers/{row['id']}",
                state,
                body={"name": "Evening digest", "action": row["action"]},
                match_info={"id": row["id"]},
                headers={"If-Match": row["revision"]},
            )
        )
    )
    assert resp.status == 200, resp.text
    stored = TriggerStore(base_dir=home_dir).get("digest").trigger
    assert stored.name == "Evening digest"
    assert stored.workflow["inline"]["config"]["task_template"] == (
        f"Summarize the day. The key is {SECRET}."
    )


def test_a_save_keeps_a_hidden_value_another_save_changed_while_it_was_checked(home, monkeypatch):
    """The revision check compares masked rows, so a save that changed only a hidden value passes
    it. The marker must then stand for the value stored when the save writes, not the one read
    before its action was checked."""
    from personalclaw.triggers.store import TriggerStore

    T, state, home_dir, _ = home
    _prompt_schedule(home_dir)
    row = next(t for t in _listed(T, state) if t["id"] == "schedule:digest")
    rotated = SECRET.replace("A", "D")
    checked = T._action_problem

    async def _rotated_meanwhile(action, *, stored=None):
        store = TriggerStore(base_dir=home_dir)
        current = store.get("digest").trigger
        prompt = f"Summarize the day. The key is {rotated}."
        current.workflow["inline"]["config"]["task_template"] = prompt
        store.upsert(current)
        return await checked(action, stored=stored)

    monkeypatch.setattr(T, "_action_problem", _rotated_meanwhile)
    resp = _run(
        T.api_trigger_detail(
            _req(
                "PUT",
                f"/api/triggers/{row['id']}",
                state,
                body={"name": "Evening digest", "action": row["action"]},
                match_info={"id": row["id"]},
                headers={"If-Match": row["revision"]},
            )
        )
    )
    assert resp.status == 200, resp.text
    stored = TriggerStore(base_dir=home_dir).get("digest").trigger
    assert stored.name == "Evening digest"
    assert stored.workflow["inline"]["config"]["task_template"] == (
        f"Summarize the day. The key is {rotated}."
    )


def test_a_dry_run_shows_what_it_would_run_masked(home):
    from personalclaw.dashboard.handlers import trigger_runs

    _T, state, home_dir, _ = home
    _prompt_schedule(home_dir)
    resp = _run(
        trigger_runs.api_trigger_run(
            _req(
                "POST",
                "/api/triggers/schedule:digest/run",
                state,
                body={"dry_run": True},
                match_info={"id": "schedule:digest"},
                query="dry_run=1",
            )
        )
    )
    assert resp.status == 200, resp.text
    answer = _body(resp)
    assert answer.get("would_run"), "precondition: the dry run says what it would run"
    assert SECRET not in json.dumps(answer)


def test_the_week_grid_masks_what_the_list_masks(home):
    T, state, home_dir, _ = home
    _prompt_schedule(home_dir, name=f"Digest {SECRET}")
    state.crons.list_jobs.return_value = []
    resp = _run(T.api_triggers_week(_req("GET", "/api/triggers/week", state)))
    week = _body(resp)
    assert week["occurrences"], "precondition: the grid plots the schedule"
    assert SECRET not in json.dumps(week)


def test_the_agent_listing_or_dry_running_automations_sees_no_key(home):
    from personalclaw.triggers import tools
    from personalclaw.triggers.store import TriggerStore

    _T, _state, home_dir, _ = home
    # Not yet allowed to run its action, so the dry run also quotes the refusal a real run gets.
    _prompt_schedule(home_dir, name=f"Digest {SECRET}", granted=False)
    store = TriggerStore(base_dir=home_dir)
    listed = tools.list_automations(store)
    assert "digest" in listed.text, "precondition: the automation is listed"
    assert SECRET not in listed.text + json.dumps(listed.data)
    dry = tools.run(store, trigger_id="digest", dry_run=True)
    assert dry.ok and "not allowed" in dry.text, dry.text
    assert SECRET not in dry.text + json.dumps(dry.data)


def test_the_cli_lists_a_schedule_masked(home, monkeypatch, capsys):
    import argparse

    from personalclaw import cli_commands

    _T, _state, home_dir, _ = home
    _prompt_schedule(home_dir)
    monkeypatch.setattr(cli_commands, "config_dir", lambda: home_dir)
    cli_commands._cron(argparse.Namespace(cron_action="list"))
    out = capsys.readouterr().out
    assert "digest" in out, "precondition: the CLI lists it"
    # The line cuts the prompt at 60 characters, so the key's head is the tell, not all of it.
    assert "sk-ant-" not in out and MASK in out


def test_a_channel_app_listing_automations_gets_the_masked_row(home):
    """`/cron list` in the Slack, Telegram and Discord apps reads `sdk.channel.to_schedule_row`."""
    from personalclaw.sdk.channel import to_schedule_row
    from personalclaw.triggers.store import TriggerStore

    _T, _state, home_dir, _ = home
    _prompt_schedule(home_dir)
    row = to_schedule_row(TriggerStore(base_dir=home_dir).get("digest").trigger)
    assert SECRET not in json.dumps(row)


def test_investigating_a_schedule_run_shows_the_schedule_masked(home):
    from personalclaw.investigate import _resolve_schedule_run
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    _T, _state, home_dir, _ = home
    _prompt_schedule(home_dir, name=f"Digest {SECRET}")
    ScheduleRunStore(home_dir).append_sync(ScheduleRun(run_id="r1", job_id="digest"))
    ctx = _run(_resolve_schedule_run("digest:r1", MagicMock()))
    assert ctx is not None and "Prompt/message" in ctx.snapshot
    assert SECRET not in ctx.snapshot + ctx.title


# ── A masked name names the stored one ──────────────────────────────────────────────────────


@pytest.fixture
def artifacts(tmp_path):
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


def _artifacts_app() -> web.Application:
    from personalclaw.artifacts.handlers import register_artifact_routes

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    return app


@pytest.mark.asyncio
async def test_removing_a_masked_tag_chip_removes_the_real_tag(artifacts):
    from aiohttp.test_utils import TestClient, TestServer

    art = artifacts.create(name="Notes", content="body", kind="markdown", tags=["ops", SECRET])
    async with TestClient(TestServer(_artifacts_app())) as client:
        shown = (await (await client.get(f"/api/artifacts/{art.slug}")).json())["tags"]
        assert MASK in shown and SECRET not in shown, "precondition: the chip shows the mask"
        # ArtifactViewer.saveTags: the chips' difference from the rendered list is the edit.
        resp = await client.patch(
            f"/api/artifacts/{art.slug}", json={"add_tags": [], "remove_tags": [MASK]}
        )
        assert resp.status == 200, await resp.text()
    assert artifacts.get(art.slug).tags == ["ops"]


@pytest.mark.asyncio
async def test_a_marker_that_names_no_tag_is_refused_not_added(artifacts):
    from aiohttp.test_utils import TestClient, TestServer

    art = artifacts.create(name="Notes", content="body", kind="markdown", tags=["ops"])
    async with TestClient(TestServer(_artifacts_app())) as client:
        resp = await client.patch(
            f"/api/artifacts/{art.slug}", json={"add_tags": [MASK], "remove_tags": []}
        )
        assert resp.status == 409, await resp.text()
    assert artifacts.get(art.slug).tags == ["ops"]


class _Facts:
    """A semantic store with the calls the memory routes make."""

    def __init__(self, facts: dict[str, Any]):
        self.facts = dict(facts)

    def get_all_semantic(self):
        return [{"key": k, "value_json": json.dumps(v)} for k, v in self.facts.items()]

    def get_semantic(self, key):
        return (
            {"key": key, "value_json": json.dumps(self.facts[key])} if key in self.facts else None
        )

    def set_semantic(self, key, value, confidence=1.0, source="user_explicit", **_kw):
        self.facts[key] = value
        return None

    def delete_semantic(self, key, source="user_explicit"):
        return self.facts.pop(key, None) is not None


def _memory_call(handler, method: str, path: str, facts: _Facts, *, body=None, match_info=None):
    from personalclaw.dashboard.handlers import memory as M

    app = web.Application()
    app["state"] = MagicMock()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    with (
        patch.object(M, "_get_service", return_value=facts),
        patch.object(M, "_is_restricted_session", return_value=False),
    ):
        return _run(handler(req))


def _listed_key(facts: _Facts) -> str:
    from personalclaw.dashboard.handlers import memory as M

    resp = _memory_call(M.api_memory_semantic, "GET", "/api/memory/semantic", facts)
    entries = _body(resp)["entries"]
    return next(e["key"] for e in entries if MASK in e["key"])


def test_writing_a_fact_back_by_its_masked_key_updates_that_fact(monkeypatch):
    from personalclaw.dashboard.handlers import memory as M

    monkeypatch.setattr(M, "_owner_handle", lambda: "")
    key = f"deploy.token.{SECRET}"
    facts = _Facts({key: "rotate monthly"})
    shown = _listed_key(facts)
    resp = _memory_call(
        M.api_memory_semantic_write,
        "PUT",
        "/api/memory/semantic",
        facts,
        body={"key": shown, "value": "rotate weekly"},
    )
    assert resp.status == 200, resp.text
    assert facts.facts == {key: "rotate weekly"}


def test_deleting_a_fact_by_its_masked_key_deletes_it(monkeypatch):
    from personalclaw.dashboard.handlers import memory as M

    monkeypatch.setattr(M, "_owner_handle", lambda: "")
    key = f"deploy.token.{SECRET}"
    facts = _Facts({key: "rotate monthly", "editor": "vim"})
    shown = _listed_key(facts)
    resp = _memory_call(
        M.api_memory_semantic_delete,
        "DELETE",
        f"/api/memory/semantic/{shown}",
        facts,
        match_info={"key": shown},
    )
    assert resp.status == 200, resp.text
    assert facts.facts == {"editor": "vim"}


def test_a_marker_that_names_no_fact_is_refused_not_stored(monkeypatch):
    from personalclaw.dashboard.handlers import memory as M

    facts = _Facts({"editor": "vim"})
    resp = _memory_call(
        M.api_memory_semantic_write,
        "PUT",
        "/api/memory/semantic",
        facts,
        body={"key": f"deploy.token.{MASK}", "value": "x"},
    )
    assert resp.status == 409, resp.text
    assert facts.facts == {"editor": "vim"}


# ── A loop's deliverable: where Files opens it, and nowhere else ────────────────────────────


@pytest.fixture
def loops(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    return tmp_path


def _goal_loop(**kind_config):
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    loop = store.create(
        Loop(
            id="",
            name="Research",
            kind="goal",
            task="write the report",
            kind_config={"goal_type": "open_ended", **kind_config},
        )
    )
    return loop, loop_files.loop_dir(loop.id)


def test_a_goal_loops_deliverable_opens_in_files(loops):
    """Its completion graduates REPORT.md from the loop's own folder, and the artifact's "Source
    file" opens that path in Files, which refused it."""
    from personalclaw.dashboard.handlers.files import _validate_dashboard_path

    _loop, folder = _goal_loop()
    report = folder / "REPORT.md"
    report.write_text("the report")
    assert _validate_dashboard_path(str(report)) == os.path.realpath(report)


def test_an_app_is_not_shown_a_loops_own_folder(loops):
    """A loop's folder holds the brief its worker reads every cycle, and steering a loop is the
    owner's: a greenfield code loop's was an app's to browse and edit."""
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.file_roots import dashboard_roots
    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    _loop, folder = _goal_loop()
    (folder / "REPORT.md").write_text("the report")
    code = store.create(Loop(id="", name="Slugify", kind="code", task="write slugify.py"))
    code_folder = loop_files.loop_dir(code.id)
    owner_roots = [r for _label, r in dashboard_roots()]
    assert os.path.realpath(code_folder) in owner_roots, "precondition: the owner browses it"
    with scoped_to_app("probe-app"):
        roots = [r for _label, r in dashboard_roots()]
    assert os.path.realpath(folder) not in roots
    assert os.path.realpath(code_folder) not in roots


def test_the_watchdog_reads_no_deliverable_named_out_of_the_loops_folder(loops):
    from personalclaw.loop.watchdog import LoopWatchdog

    outside = loops / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("not the loop's")
    loop, folder = _goal_loop(primary_deliverable="../../outside/notes.md")
    assert (folder / "../../outside/notes.md").resolve() == (outside / "notes.md").resolve()
    assert LoopWatchdog(MagicMock(), MagicMock())._deliverable_file(loop) is None
    # A symlink planted in the folder under the deliverable's own name is refused as well.
    loop2, folder2 = _goal_loop()
    (folder2 / "REPORT.md").symlink_to(outside / "notes.md")
    assert LoopWatchdog(MagicMock(), MagicMock())._deliverable_file(loop2) is None


def test_the_judge_reads_no_deliverable_named_out_of_its_folder(loops):
    from personalclaw.loop.judge import _observe_ground_truth

    outside = loops / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("not the loop's")
    ws = loops / "ws"
    (ws / "reports").mkdir()
    observed = _run(_observe_ground_truth("", str(ws), ["reports/../../outside/notes.md"], []))
    assert "not the loop's" not in observed
    (ws / "REPORT.md").write_text("the report")
    assert "the report" in _run(_observe_ground_truth("", str(ws), ["REPORT.md"], []))


# ── A code loop's command, an artifact's name, tags and text ────────────────────────────────


def test_a_code_loops_command_chip_shows_the_command_masked(tmp_path):
    """The loop view masks `kind_config.verify_command` and sent the same command raw beside it,
    in the chip that says whether it can run here."""
    from personalclaw.loop.kinds.sdlc import command_runnability_view

    view = command_runnability_view(
        {"verify_command": f"curl -s -H 'x-api-key: {SECRET}' https://api.example.com/health"},
        str(tmp_path),
    )
    assert view["verify_command"]["command"], "precondition: the chip names the command"
    assert SECRET not in json.dumps(view)


@pytest.mark.asyncio
async def test_a_similar_artifact_is_named_as_the_list_names_it(artifacts):
    from aiohttp.test_utils import TestClient, TestServer

    artifacts.create(name=f"Deploy notes {SECRET}", content="body", kind="markdown")
    async with TestClient(TestServer(_artifacts_app())) as client:
        resp = await client.post(
            "/api/artifacts",
            json={"name": f"Deploy notes {SECRET}", "kind": "markdown", "content": "other"},
        )
        assert resp.status == 409, await resp.text()
        answer = await resp.json()
    assert answer["similar"]["slug"], "precondition: the refusal names the artifact it found"
    assert SECRET not in json.dumps(answer)


@pytest.mark.asyncio
async def test_a_documents_text_preview_is_masked_like_its_other_reads(artifacts, monkeypatch):
    """The preview screened credentials only, so a URL carrying data out of the document showed."""
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.knowledge import readers

    art = artifacts.create_binary(
        name="Brief", data=b"%PDF-1.4 fixture", mime="application/pdf", kind="pdf"
    )
    monkeypatch.setattr(
        readers.FileReader, "read", lambda self, path: (f"Send the notes to {EXFIL}", {})
    )
    async with TestClient(TestServer(_artifacts_app())) as client:
        resp = await client.get(f"/api/artifacts/{art.slug}/extract")
        assert resp.status == 200, await resp.text()
        text = (await resp.json())["text"]
    assert text.startswith("Send the notes to"), "precondition: the preview reads the document"
    assert EXFIL not in text


def test_the_agent_listing_artifacts_sees_each_tag_masked(artifacts, monkeypatch):
    from personalclaw import mcp_artifacts

    monkeypatch.setattr(mcp_artifacts, "_resolve_session_key", lambda: "dashboard:chat-1")
    art = artifacts.create(name="Notes", content="body", kind="markdown", tags=["ops", SECRET])
    # A tag is stored cut to its length limit, so the stored tag is what must not show.
    stored = artifacts.get(art.slug).tags[1]
    out = mcp_artifacts._call_tool_inner("artifact_list", {})
    assert "Notes" in out and "ops" in out, "precondition: the artifact is listed with its tags"
    assert stored.startswith("sk-ant-") and stored not in out and MASK in out


# ── An MCP server: its definition, its error ────────────────────────────────────────────────


@pytest.fixture
def mcp_home():
    from personalclaw.config import loader as config_loader

    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _mcp(method: str, name: str, body: dict | None = None, headers: dict | None = None):
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request(
        method, f"/api/mcp/servers/{name}", headers=headers or {}, match_info={"name": name}
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return _run(mcp_mod.api_mcp_server_detail(req))


def test_a_servers_edit_form_shows_its_arguments_masked_and_a_save_keeps_them(mcp_home):
    added = _mcp(
        "PUT", "search", confirmed({"command": UNRESOLVABLE, "args": ["--api-key", SECRET]})
    )
    assert added.status == 200, added.text
    read = json.loads(_mcp("GET", "search").text)
    assert MASK in read["args"] and SECRET not in json.dumps(read)
    # What the edit form sends: the definition it was seeded with, one argument added.
    resp = _mcp(
        "PUT",
        "search",
        confirmed({"command": read["command"], "args": [*read["args"], "--verbose"]}),
        headers={"If-Match": read["revision"]},
    )
    assert resp.status == 200, resp.text
    spec = json.loads((mcp_home / "mcp.json").read_text())["mcpServers"]["search"]
    assert spec["args"] == ["--api-key", SECRET, "--verbose"]


def test_a_servers_save_keeps_an_argument_another_save_changed_while_it_waited(
    mcp_home, monkeypatch
):
    """The marker stands for the value saved when this save writes, read under the file lock."""
    from personalclaw.dashboard.handlers import mcp as mcp_mod

    added = _mcp(
        "PUT", "search", confirmed({"command": UNRESOLVABLE, "args": ["--api-key", SECRET]})
    )
    assert added.status == 200, added.text
    read = json.loads(_mcp("GET", "search").text)
    rotated = SECRET.replace("A", "D")
    lock = mcp_mod._get_mcp_lock

    class _SavedMeanwhile:
        """Another tab saves a new key while this save waits for the lock."""

        async def __aenter__(self):
            path = mcp_home / "mcp.json"
            data = json.loads(path.read_text())
            data["mcpServers"]["search"]["args"] = ["--api-key", rotated]
            path.write_text(json.dumps(data))
            self._lock = lock()
            return await self._lock.__aenter__()

        async def __aexit__(self, *exc):
            return await self._lock.__aexit__(*exc)

    monkeypatch.setattr(mcp_mod, "_get_mcp_lock", _SavedMeanwhile)
    resp = _mcp(
        "PUT",
        "search",
        confirmed({"command": read["command"], "args": [*read["args"], "--verbose"]}),
        headers={"If-Match": read["revision"]},
    )
    assert resp.status == 200, resp.text
    spec = json.loads((mcp_home / "mcp.json").read_text())["mcpServers"]["search"]
    assert spec["args"] == ["--api-key", rotated, "--verbose"]


def test_a_servers_connection_error_is_masked_on_every_read():
    """The list masked it at the route, and the probe routes and the detail read did not."""
    from personalclaw.mcp_discovery import McpServerInfo

    info = McpServerInfo(name="search", command="npx", status="error", error=f"401: {SECRET}")
    shown = info.to_dict()
    assert shown["error"].startswith("401"), "precondition: the error is shown"
    assert SECRET not in json.dumps(shown)


# ── A saved prompt, a notification, a loop's name ───────────────────────────────────────────


def test_saving_a_prompt_answers_with_it_masked_like_its_read(monkeypatch):
    from personalclaw.dashboard.handlers import api_prompt_detail, api_prompt_save
    from personalclaw.dashboard.handlers.prompts import _get_default_prompt_provider
    from personalclaw.prompt_providers.base import PromptTemplate

    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())
    _get_default_prompt_provider().create_prompt(
        PromptTemplate.from_dict(
            {"name": "zz-digest", "kind": "user", "title": "T", "content": f"key: {SECRET}"}
        )
    )

    def _prompt_req(body=None, revision=None):
        r = MagicMock()
        r.match_info = {"name": "zz-digest"}
        r.query = {}
        r.headers = {"If-Match": revision} if revision is not None else {}

        async def _json():
            return body

        r.json = _json
        return r

    shown = _body(_run(api_prompt_detail(_prompt_req())))
    assert MASK in shown["content"], "precondition: the read masks the prompt"
    body = {"name": "zz-digest", "kind": "user", "title": "New", "content": shown["content"]}
    resp = _run(api_prompt_save(_prompt_req(body=body, revision=shown["revision"])))
    assert resp.status == 200, resp.text
    assert SECRET not in resp.text


def test_a_notification_shows_its_items_text_masked_like_the_row(monkeypatch):
    from personalclaw import inbox

    monkeypatch.setattr(inbox, "_verification_opted_in", lambda source, kind: False)
    state = MagicMock()
    inbox.emit_attention_item(
        state,
        source="skills",
        kind="proposal",
        title=f"Rotate the key {SECRET}?",
        body=f"It was pasted as {SECRET}.",
        store=MagicMock(),
    )
    assert state.notify.called, "precondition: the item notified"
    assert SECRET not in json.dumps([str(arg) for arg in state.notify.call_args.args])


def test_a_loops_notifications_its_chats_origin_and_its_folder_name_it_masked(loops):
    from personalclaw.dashboard.chat_handlers import _origin_label
    from personalclaw.file_roots import dashboard_roots
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop
    from personalclaw.loop.watchdog import LoopWatchdog

    loop = store.create(
        Loop(
            id="",
            name=f"Rotate {SECRET}",
            kind="goal",
            task="rotate it",
            kind_config={"goal_type": "open_ended"},
        )
    )
    assert store.get_redacted(loop.id)["name"] == f"Rotate {MASK}", "precondition: masked list"
    assert LoopWatchdog(MagicMock(), MagicMock())._loop_name(loop.id) == f"Rotate {MASK}"
    assert _origin_label("loop", loop.id) == f"Rotate {MASK}"
    # Files lists the loop's folder, where it keeps REPORT.md, under the loop's name.
    labels = [label for label, _root in dashboard_roots() if label.startswith("Loop")]
    assert labels and all(label.startswith("Loop: Rotate [REDACTED") for label in labels)


def test_a_projects_board_names_its_loops_masked(loops):
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop
    from personalclaw.tasks.hierarchy_handlers import _loop_rows

    store.create(Loop(id="", name="", kind="goal", task=f"use {SECRET}", project_id="p1"))
    rows = _loop_rows("p1")
    assert rows and rows[0]["title"].startswith("use"), "precondition: the board lists the loop"
    assert "sk-ant-" not in json.dumps(rows)


# ── A workflow run: its list, status, live events and node output ──────────────────────────


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


def _failed_run() -> str:
    from personalclaw.workflows import store
    from personalclaw.workflows.models import (
        Failure,
        InstanceState,
        NodeInstance,
        RunStatus,
        WorkflowRun,
    )

    run = WorkflowRun(
        id=store.new_run_id(),
        workflow_name="digest",
        status=RunStatus.FAILED,
        title=f"Digest {SECRET}",
        intent=f"Summarize the day with {SECRET}",
        inputs={"token": SECRET},
        error_message=f"401 for {SECRET}",
        attention={"reason": f"the key {SECRET} was refused"},
    )
    store.save(run)
    store.write_spec(run.id, {"root": {"kind": "action", "id": "fetch"}})
    failed = NodeInstance(
        path="root", state=InstanceState.FAILED, failure=Failure(cause_plain=f"curl: {SECRET}")
    )
    store.write_state(run.id, {"root": failed})
    store.write_output(run.id, "root", {"body": f"token={SECRET}"})
    return run.id


def _wf(handler, path: str, **match_info):
    app = web.Application()
    app["state"] = None
    req = make_mocked_request("GET", path, match_info=match_info, app=app)
    return _run(handler(req))


def test_the_run_list_shows_a_run_masked_like_the_loops_page(runs):
    from personalclaw.workflows import handlers as H

    run_id = _failed_run()
    listed = _body(_wf(H.api_runs_list, "/api/workflows/runs"))["runs"]
    row = next(r for r in listed if r["id"] == run_id)
    assert row["error_message"].startswith("401"), "precondition: the row says why it failed"
    assert SECRET not in json.dumps(row)


def test_a_runs_status_and_its_node_output_are_masked_like_the_inspect_drawer(runs):
    from personalclaw.workflows import handlers as H

    run_id = _failed_run()
    status = _body(_wf(H.api_run_status, "/api/workflows/runs/x", run_id=run_id))
    assert status["error"] and status["nodes"][0]["failure"], "precondition: it says what failed"
    assert SECRET not in json.dumps(status)
    output = _wf(
        H.api_run_output, "/api/workflows/runs/x/outputs/fetch", run_id=run_id, node_id="fetch"
    )
    assert output.status == 200, output.text
    assert "token=" in output.text and SECRET not in output.text


@pytest.mark.asyncio
async def test_a_runs_live_snapshot_is_masked_like_its_status(runs):
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.sse import SseRegistry
    from personalclaw.workflows.handlers import register_workflow_routes

    run_id = _failed_run()
    app = web.Application()
    registry = SseRegistry()
    app["state"] = MagicMock(workflow_sse=lambda: registry)
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/workflows/runs/{run_id}/events")
        assert resp.status == 200, await resp.text()
        stream = await resp.text()
    assert "workflow_snapshot" in stream and "401 for" in stream, "precondition: a snapshot"
    assert SECRET not in stream


def test_a_live_run_event_is_masked_like_the_journal_row_it_mirrors():
    from types import SimpleNamespace

    from personalclaw.workflows.controller import RunController

    seen: list[tuple[str, dict]] = []
    controller = SimpleNamespace(
        services=SimpleNamespace(publish=lambda event, body: seen.append((event, body))),
        run=SimpleNamespace(id="run-1"),
        _event_seq=0,
        _run_epoch=lambda: 0,
    )
    RunController._publish(controller, "workflow_run_update", {"error": f"401 for {SECRET}"})
    assert seen and seen[0][1]["error"].startswith("401"), "precondition: the event went out"
    assert SECRET not in json.dumps(seen)


# ── The memory history and a lesson ─────────────────────────────────────────────────────────


def test_the_memory_history_shows_a_facts_values_masked_like_the_list():
    from personalclaw.dashboard.handlers import memory as M

    class _Events(_Facts):
        def get_events(self, limit=50, offset=0):
            return [
                {
                    "id": 1,
                    "event_type": "update",
                    "key": "deploy.token",
                    "old_value": json.dumps(f"old {SECRET}"),
                    "new_value": json.dumps(f"new {SECRET}"),
                }
            ]

    resp = _memory_call(M.api_memory_events, "GET", "/api/memory/events", _Events({}))
    events = _body(resp)["events"]
    assert events and events[0]["key"] == "deploy.token", "precondition: the history lists it"
    assert SECRET not in json.dumps(events)


class _Lessons:
    """The lesson calls the lessons routes make, over rule strings."""

    def __init__(self, *rules: str):
        self.rules = list(rules)

    def get_lessons(self):
        return [
            {"key": f"lesson.{i}", "value_json": json.dumps(r), "updated_at": ""}
            for i, r in enumerate(self.rules)
        ]

    def lesson_standings(self, rows):
        return {}

    def delete_lesson(self, rule_substring):
        kept = [r for r in self.rules if rule_substring.lower() not in r.lower()]
        deleted = len(kept) != len(self.rules)
        self.rules = kept
        return deleted


def _lessons_call(handler, method: str, lessons: _Lessons, *, body=None):
    from personalclaw.dashboard.handlers import schedule as S

    state = MagicMock()
    req = _req(method, "/api/lessons", state, body=body)
    with (
        patch.object(S, "_blocks_reads_session", return_value=False),
        patch.object(S, "_get_memory", return_value=MagicMock()),
        patch("personalclaw.memory_service.service_for", return_value=lessons),
    ):
        return _run(handler(req))


def test_the_lessons_list_masks_a_rule_and_deleting_it_by_what_it_showed_deletes_it():
    from personalclaw.dashboard.handlers import schedule as S

    lessons = _Lessons(f"Deploy with {SECRET}", "Prefer small commits")
    shown = _body(_lessons_call(S.api_lessons, "GET", lessons))["lessons"]
    rule = next(item["rule"] for item in shown if item["rule"].startswith("Deploy"))
    assert SECRET not in json.dumps(shown) and MASK in rule
    # MemoryPanel deletes a lesson by the rule the list showed it as.
    resp = _lessons_call(S.api_lessons_delete, "DELETE", lessons, body={"rule": rule})
    assert _body(resp) == {"ok": True}, resp.text
    assert lessons.rules == ["Prefer small commits"]


class _Graphed(_Lessons):
    """Facts and lessons as the memory graph reads them."""

    has_vector = True
    has_graph = True

    def __init__(self, facts: dict[str, Any], *rules: str):
        super().__init__(*rules)
        self.facts = facts
        self.linked: list[str] = []

    def get_all_semantic(self):
        return [{"key": k, "value_json": json.dumps(v)} for k, v in self.facts.items()]

    def get_semantic(self, key):
        return {"key": key} if key in self.facts else None

    def graph_record_links(self, ref):
        self.linked.append(ref)
        return []


def test_the_memory_graph_names_each_node_as_the_lists_do(monkeypatch):
    """The Studio finds a list row's node by its handle, which quoted the key or rule unmasked."""
    from personalclaw.dashboard.handlers import memory as M

    key = "user.deploy.token.ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    svc = _Graphed({key: "rotate monthly"}, f"Deploy with {SECRET} only from CI")
    mem = MagicMock()
    mem.read_preferences.return_value = ""
    mem.read_projects.return_value = ""
    mem.read_recent_history.return_value = ""
    monkeypatch.setattr(M, "_get_memory", lambda state: mem)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda m: svc)
    graph = _body(_memory_call(M.api_memory_graph, "GET", "/api/memory/graph", _Facts({})))
    refs = sorted(n["ref"] for n in graph["nodes"])
    assert refs == [
        f"lesson:Deploy with {MASK} only from CI",
        f"sem:user.deploy.token.{MASK}",
    ], refs
    assert "sk-ant-" not in json.dumps(graph) and "ghp_" not in json.dumps(graph)
    # The fact's links are asked for by the handle the list shows.
    shown = next(r for r in refs if r.startswith("sem:"))
    app = web.Application()
    app["state"] = MagicMock()
    req = make_mocked_request("GET", f"/api/memory/record-links?ref={shown}", app=app)
    with patch.object(M, "_get_service", return_value=svc):
        resp = _run(M.api_memory_record_links(req))
    assert resp.status == 200, resp.text
    assert svc.linked == [f"sem:{key}"]


# ── A plan: the walkthrough's read and an edit made from it ────────────────────────────────


def _plan_step(markdown: str):
    from personalclaw.planning import session as PS

    step = PS.PlanStep(id="step-0", kind="design", title="Design")
    step.status = PS.StepStatus.AWAITING_REVIEW.value
    step.artifact = {"markdown": markdown}
    return step


def test_a_loops_plan_is_masked_and_an_edit_made_from_it_keeps_the_key(loops):
    """A plan is drafted from the task, which the loop's own read masks."""
    from personalclaw.dashboard.handlers import loop_routes as H
    from personalclaw.loop import files as loop_files
    from personalclaw.planning import session as PS

    loop, _folder = _goal_loop()
    sess = PS.PlanSession(project_id=loop.id, created_at=1.0)
    sess.steps = [_plan_step(f"Call the API with {SECRET}.")]
    loop_files.write_plan_session(sess)
    state = MagicMock()
    read = _body(
        _run(H.api_loop_plan_session(_req("GET", "/x", state, match_info={"id": loop.id})))
    )["session"]
    step = read["steps"][0]
    assert MASK in step["artifact"]["markdown"] and SECRET not in json.dumps(read)
    resp = _run(
        H.api_loop_plan_edit(
            _req(
                "POST",
                "/x",
                state,
                body={"step_id": "step-0", "markdown": step["artifact"]["markdown"] + " Retry."},
                match_info={"id": loop.id},
                headers={"If-Match": f'"{step["revision"]}"'},
            )
        )
    )
    assert resp.status == 200, resp.text
    assert SECRET not in resp.text
    saved = loop_files.read_plan_session(loop.id).steps[0].artifact["markdown"]
    assert saved == f"Call the API with {SECRET}. Retry."


def test_a_chats_plan_is_masked_and_an_edit_made_from_it_keeps_the_key(loops):
    from types import SimpleNamespace

    from personalclaw.dashboard import chat_plan as CP
    from personalclaw.planning import session as PS

    sess = PS.PlanSession(project_id="dashboard:c1", created_at=1.0)
    sess.steps = [_plan_step(f"Call the API with {SECRET}.")]
    CP.write(sess, {})
    state = MagicMock()
    state._sessions = {"c1": SimpleNamespace(key="dashboard:c1")}
    read = _body(
        _run(CP.api_chat_plan_session(_req("GET", "/x", state, match_info={"session": "c1"})))
    )["session"]
    step = read["steps"][0]
    assert MASK in step["artifact"]["markdown"] and SECRET not in json.dumps(read)
    resp = _run(
        CP.api_chat_plan_edit(
            _req(
                "POST",
                "/x",
                state,
                body={"step_id": "step-0", "markdown": step["artifact"]["markdown"] + " Retry."},
                match_info={"session": "c1"},
                headers={"If-Match": f'"{step["revision"]}"'},
            )
        )
    )
    assert resp.status == 200, resp.text
    assert CP.read("dashboard:c1")[0].steps[0].artifact["markdown"] == (
        f"Call the API with {SECRET}. Retry."
    )
