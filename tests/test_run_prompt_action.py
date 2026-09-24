"""The ``run-prompt`` action (T1) — run a saved Prompt on a trigger's cadence.

Covers: missing prompt_id (error), unknown prompt (error), bad vars type
(error), empty-render (error), and the success path (resolve → render → frame →
fire-and-forget auto-approved spawn). The saved-prompt resolution + render is
stubbed so the test stays unit-scoped; the spawn path mirrors invoke-agent's.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider


def _ctx() -> ActionContext:
    return ActionContext(event="Schedule", context="", payload={"session_key": "cron:x"})


def _services(spawn_sink, scheduled):
    fake_sub = SimpleNamespace(spawn=lambda **kw: spawn_sink.update(kw))

    def _bg(coro):
        scheduled.append(coro)
        return asyncio.ensure_future(coro)

    return SimpleNamespace(subagents=fake_sub, spawn_background=_bg)


def test_no_prompt_id_and_no_loop_md_is_error(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    # No loop.md anywhere → a bare run-prompt has nothing to run.
    monkeypatch.setattr(mod, "resolve_loop_md", lambda cwd: None)
    res = asyncio.run(RunPromptActionProvider().execute({}, _ctx()))
    assert res.success is False and "loop.md" in res.error


def test_bad_vars_type_is_error():
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "p", "vars": "nope"}, _ctx()))
    assert res.success is False and "vars" in res.error


def test_unknown_prompt_is_error(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    def _raise(prompt_id, values):
        raise LookupError(f"no saved prompt named {prompt_id!r}")

    monkeypatch.setattr(mod, "render_saved_prompt", _raise)
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "ghost"}, _ctx()))
    assert res.success is False and "ghost" in res.error


def test_empty_render_is_error(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "   ")
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "blank"}, _ctx()))
    assert res.success is False and "empty" in res.error


def test_render_error_is_value_error_and_caught(monkeypatch):
    """A PromptRenderError (e.g. unfilled required variable) must surface as a
    ValueError from render_saved_prompt AND be caught by execute() as an honest
    error result — not escape uncaught and break the action dispatch."""
    import personalclaw.action_providers.run_prompt_provider as mod

    def _raise(pid, v):
        raise ValueError("missing required variable: project")

    monkeypatch.setattr(mod, "render_saved_prompt", _raise)
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "p", "vars": {}}, _ctx()))
    assert res.success is False and "missing required variable" in res.error


def test_render_saved_prompt_normalizes_prompt_render_error():
    """The real render_saved_prompt maps a PromptRenderError -> ValueError so the
    caller's `except ValueError` contract holds (PromptRenderError is a bare
    Exception, not a ValueError subclass)."""
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    from personalclaw.prompt_providers import get_default_provider

    prov = get_default_provider()
    tmpl = PromptTemplate(
        name="_t_reqvar",
        kind="user",
        content="Hi {{who}}",
        variables=[PromptVariable(name="who", type="string", required=True)],
    )
    prov.create_prompt(tmpl)
    try:
        import pytest as _pytest

        with _pytest.raises(ValueError):
            mod.render_saved_prompt("_t_reqvar", {})  # required var unset
    finally:
        prov.delete_prompt("_t_reqvar")


def test_bundled_digest_prompt_resolves_and_renders():
    """P10: the bundled ``task-digest`` prompt is a real resolvable saved prompt that
    ``run-prompt`` can fire — resolve it and render its three vars (sources/window/
    target) through the SAME render_saved_prompt path the action uses. Proves the
    digest primitive is composition (a Prompt fired by a trigger), no new service."""
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    out = mod.render_saved_prompt(
        "task-digest",
        {"sources": "#eng, inbox", "window": "the last 24 hours", "target": "inbox"},
    )
    assert out and "{{" not in out, "digest prompt must fully render with no leftover vars"
    # The rendered directive carries the caller's chosen sources/window/target.
    assert "#eng, inbox" in out and "the last 24 hours" in out and "inbox" in out
    # It is an AGENT directive (gather → deliver), not a paste-in summarizer.
    assert "Gather" in out and "Deliver" in out


def test_bundled_digest_prompt_is_in_use_case_vocabulary():
    """P10: the digest use_case auto-registers in PROMPT_USE_CASES (derived from the
    catalog) so binding/resolution work — without polluting the model-capability vocab."""
    from personalclaw.providers.prompt_use_cases import PROMPT_USE_CASES, active_prompt_ref
    from personalclaw.providers.use_cases import USE_CASES

    assert "digest" in PROMPT_USE_CASES
    assert active_prompt_ref("digest") == "native:task-digest"
    # It's a PROMPT use_case, not a model capability — the two vocabularies stay separate.
    assert "digest" not in USE_CASES


def test_success_spawns_auto_approved_with_framing(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(
        mod, "render_saved_prompt", lambda pid, v: f"BODY for {pid} team={(v or {}).get('team')}"
    )
    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        res = await RunPromptActionProvider().execute(
            {"prompt_id": "standup", "vars": {"team": "infra"}, "agent": "PersonalClaw"},
            _ctx(),
        )
        await asyncio.sleep(0.05)
        return res

    res = asyncio.run(go())
    assert res.success is True and "standup" in res.stdout
    assert scheduled, "the prompt turn must run as a fire-and-forget background spawn"
    # The rendered prompt reaches the spawn, wrapped in autonomous framing.
    assert "BODY for standup team=infra" in spawn_sink.get("task", "")
    assert "AUTONOMOUS RUN" in spawn_sink.get("task", "")
    assert spawn_sink.get("agent") == "PersonalClaw"
    # Auto-approve so a scheduled run never stalls on a tool-approval prompt.
    assert spawn_sink.get("approval_mode") == "auto"


def test_session_opt_in_pins_parent_session(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "body")
    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        await RunPromptActionProvider().execute(
            {"prompt_id": "p", "session": "cron:pinned"}, _ctx()
        )
        await asyncio.sleep(0.05)

    asyncio.run(go())
    # Explicit session opt-in wins over the trigger payload's session_key.
    assert spawn_sink.get("parent_session_key") == "cron:pinned"


def test_default_session_is_trigger_payload(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "body")
    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        await RunPromptActionProvider().execute({"prompt_id": "p"}, _ctx())
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert spawn_sink.get("parent_session_key") == "cron:x"


def test_services_unavailable_is_error(monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "body")
    monkeypatch.setattr(
        mod,
        "get_action_services",
        lambda: SimpleNamespace(subagents=None, spawn_background=lambda c: None),
    )
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "p"}, _ctx()))
    assert res.success is False and "subagent manager unavailable" in res.error


def test_invalid_cwd_is_honest_error(monkeypatch):
    """A cwd the subagent would refuse returns an error NOW, not a false
    'launched' (the spawn validates cwd asynchronously)."""
    import personalclaw.action_providers.run_prompt_provider as mod

    monkeypatch.setattr(mod, "render_saved_prompt", lambda pid, v: "body")
    monkeypatch.setattr(
        mod, "validate_spawn_cwd", lambda cwd: "cwd is not under any allowed root: ['~/ok']"
    )
    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))
    res = asyncio.run(RunPromptActionProvider().execute({"prompt_id": "p", "cwd": "/bad"}, _ctx()))
    assert res.success is False and "allowed root" in res.error
    assert not scheduled  # never spawned


# ── loop.md default-recurring-prompt (T3) ──


def test_resolve_loop_md_project_beats_user(tmp_path, monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.config import loader

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "loop.md").write_text("PROJECT loop", encoding="utf-8")
    user = tmp_path / "home"
    user.mkdir()
    (user / "loop.md").write_text("USER loop", encoding="utf-8")
    monkeypatch.setattr(loader, "config_dir", lambda: user)

    content, label = mod.resolve_loop_md(str(proj))
    assert content == "PROJECT loop" and label.startswith("project:")


def test_resolve_loop_md_falls_back_to_user(tmp_path, monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.config import loader

    proj = tmp_path / "proj"
    proj.mkdir()  # no project loop.md
    user = tmp_path / "home"
    user.mkdir()
    (user / "loop.md").write_text("USER loop", encoding="utf-8")
    monkeypatch.setattr(loader, "config_dir", lambda: user)

    content, label = mod.resolve_loop_md(str(proj))
    assert content == "USER loop" and label == "user"


def test_resolve_loop_md_hot_reload(tmp_path, monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path / "nope")
    f = tmp_path / "loop.md"
    f.write_text("v1", encoding="utf-8")
    assert mod.resolve_loop_md(str(tmp_path))[0] == "v1"
    f.write_text("v2", encoding="utf-8")  # edit takes effect next read
    assert mod.resolve_loop_md(str(tmp_path))[0] == "v2"


def test_resolve_loop_md_none_when_absent(tmp_path, monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path / "nope")
    assert mod.resolve_loop_md(str(tmp_path)) is None


def test_loop_md_fallback_spawns(tmp_path, monkeypatch):
    import personalclaw.action_providers.run_prompt_provider as mod
    from personalclaw.config import loader

    (tmp_path / "loop.md").write_text("do the recurring thing", encoding="utf-8")
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path / "nope")
    monkeypatch.setattr(mod, "validate_spawn_cwd", lambda cwd: "")  # cwd allowed
    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        res = await RunPromptActionProvider().execute({"cwd": str(tmp_path)}, _ctx())
        await asyncio.sleep(0.05)
        return res

    res = asyncio.run(go())
    assert res.success is True and "loop.md" in res.stdout
    task = spawn_sink.get("task", "")
    assert "do the recurring thing" in task and "AUTONOMOUS RUN" in task
    assert spawn_sink.get("approval_mode") == "auto"
