"""The three Tier-1 native hook providers (notify / send-message / create-task)
plus the shared template renderer.

Each provider subclasses the ``ActionProvider`` ABC and returns an error *result*
(never raises) on a missing required field; the success path calls the right
in-process API with rendered args. The renderer does ``$EVENT`` / ``$CONTEXT`` /
``$<payload-key>`` substitution and leaves unknown keys verbatim.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider
from personalclaw.action_providers.notify_provider import NotifyActionProvider
from personalclaw.action_providers.send_message_provider import SendMessageActionProvider
from personalclaw.action_providers.template import render_template


def _ctx() -> ActionContext:
    return ActionContext(event="Stop", context="all done", payload={"tool_name": "bash"})


# ── bash: $variables resolve as env vars ────────────────────────────────────


def test_bash_payload_vars_resolve_via_env():
    # The advertised schedule $variables ($job_id, $now, $EVENT…) must resolve
    # inside the shell command — exported as env vars (not string-templated,
    # which would be an injection vector via e.g. $last_result content).
    from personalclaw.action_providers.bash_provider import BashActionProvider

    ctx = ActionContext(
        event="schedule:j1",
        context="prev result",
        payload={
            "job_id": "j1",
            "job_name": "pulse",
            "now": "2026-07-11T00:00:00",
            "not-an-identifier": "skipped",
        },
    )
    res = asyncio.run(
        BashActionProvider().execute({"command": 'echo "e=$EVENT id=$job_id at=$now"'}, ctx)
    )
    assert res.success
    assert res.stdout == "e=schedule:j1 id=j1 at=2026-07-11T00:00:00"


# ── renderer ──────────────────────────────────────────────────────────────


def test_render_substitutes_event_context_payload():
    out = render_template("[$EVENT] $CONTEXT tool=$tool_name", _ctx())
    assert out == "[Stop] all done tool=bash"


def test_render_unknown_key_left_verbatim():
    assert render_template("$nope stays", _ctx()) == "$nope stays"


def test_render_empty_is_empty():
    assert render_template("", _ctx()) == ""


# ── notify ──────────────────────────────────────────────────────────────────


def test_notify_missing_title_is_error_result():
    res = asyncio.run(NotifyActionProvider().execute({}, _ctx()))
    assert res.success is False and "title_template" in res.error


def test_notify_success_calls_state_notify(monkeypatch):
    calls = []
    fake_state = SimpleNamespace(notify=lambda kind, title, body: calls.append((kind, title, body)))
    import personalclaw.action_providers.notify_provider as mod

    monkeypatch.setattr(mod, "get_action_services", lambda: SimpleNamespace(state=fake_state))
    res = asyncio.run(
        NotifyActionProvider().execute(
            {"title_template": "Done: $CONTEXT", "kind": "success"}, _ctx()
        )
    )
    assert res.success is True
    assert calls == [("success", "Done: all done", "")]


def test_notify_services_unavailable_is_error(monkeypatch):
    import personalclaw.action_providers.notify_provider as mod

    monkeypatch.setattr(mod, "get_action_services", lambda: None)
    res = asyncio.run(NotifyActionProvider().execute({"title_template": "x"}, _ctx()))
    assert res.success is False and "services unavailable" in res.error


# ── create-task ───────────────────────────────────────────────────────────


def test_create_task_missing_title_is_error():
    res = asyncio.run(CreateTaskActionProvider().execute({}, _ctx()))
    assert res.success is False and "title_template" in res.error


def test_create_task_success_calls_registry(monkeypatch):
    captured = {}

    async def fake_create(provider_name="native", **fields):
        captured["provider"] = provider_name
        captured["fields"] = fields
        return SimpleNamespace(id="t-abc")

    import personalclaw.tasks.registry as reg

    monkeypatch.setattr(reg, "create_task", fake_create)
    res = asyncio.run(
        CreateTaskActionProvider().execute(
            {"title_template": "Review $CONTEXT", "priority": "high"}, _ctx()
        )
    )
    assert res.success is True and "t-abc" in res.stdout
    assert captured["fields"]["title"] == "Review all done"
    assert captured["fields"]["priority"] == "high"


def test_create_task_honors_assignee_due_labels(monkeypatch):
    """The executor passes through assignee/due/labels — these must be honored
    (they were readable in code but unconfigurable via the manifest schema until
    bug #21)."""
    captured = {}

    async def fake_create(provider_name="native", **fields):
        captured.update(fields)
        return SimpleNamespace(id="t-xyz")

    import personalclaw.tasks.registry as reg

    monkeypatch.setattr(reg, "create_task", fake_create)
    res = asyncio.run(
        CreateTaskActionProvider().execute(
            {
                "title_template": "T",
                "assignee": "kay",
                "due": "2026-07-31",
                "labels": ["follow-up", "auto"],
            },
            _ctx(),
        )
    )
    assert res.success is True
    assert captured["assignee"] == "kay"
    assert captured["due"] == "2026-07-31"
    assert captured["labels"] == ["follow-up", "auto"]


def test_create_task_manifest_schema_exposes_every_honored_field():
    """Anti-drift guard (bug #13 / #21 class): every optional config field the
    create-task executor HONORS must be declared in its manifest settingsSchema,
    or that field is silently unconfigurable via the hook config UI. This locks the
    schema↔executor contract so a future executor field can't drift unexposed.

    Reads the REPO-SOURCE manifest (the committed source of truth), not the installed
    copy under ~/.personalclaw/apps — native apps are installed copies, so a source
    edit only reaches an install on re-install/restart; the contract lives in source."""
    import json
    from pathlib import Path

    import personalclaw

    src_manifest = (
        Path(personalclaw.__file__).resolve().parent
        / "apps"
        / "native"
        / "create-task-action"
        / "app.json"
    )
    schema = json.loads(src_manifest.read_text())["provider"]["settingsSchema"]
    schema_fields = set(schema.get("properties", {}))
    # The full set the executor reads from action_config (title/body are templated).
    honored = {
        "title_template",
        "body_template",
        "provider",
        "priority",
        "project",
        "assignee",
        "due",
        "labels",
    }
    missing = honored - schema_fields
    assert not missing, f"executor honors {sorted(missing)} but the schema doesn't expose them"


# ── send-message ──────────────────────────────────────────────────────────


def test_send_message_missing_text_is_error():
    res = asyncio.run(SendMessageActionProvider().execute({}, _ctx()))
    assert res.success is False and "text_template" in res.error


def test_send_message_no_channel_falls_back_to_notify(monkeypatch):
    notified = []
    fake_state = SimpleNamespace(
        channel_delivery=None,
        notify=lambda kind, title, body: notified.append((kind, title, body)),
    )
    import personalclaw.action_providers.send_message_provider as mod

    monkeypatch.setattr(mod, "get_action_services", lambda: SimpleNamespace(state=fake_state))
    res = asyncio.run(
        SendMessageActionProvider().execute({"text_template": "ping $CONTEXT"}, _ctx())
    )
    assert res.success is True
    assert notified and notified[0][2] == "ping all done"


# ── lifecycle-trigger provider allowlist (validation) ──


def test_hook_provider_allowlist_includes_all_action_providers():
    """The lifecycle-trigger create schema must accept every registered action
    provider — incl. run-prompt/run-workflow/run-script — so a lifecycle trigger
    can run anything a schedule trigger can (the UI offers them; the backend must
    not reject them). Regression for the hardcoded allowlist that omitted them."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        list_action_providers,
    )
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    _ensure_default_providers_registered()
    registered = set(list_action_providers())
    missing = registered - set(ALLOWED_HOOK_PROVIDERS)
    assert not missing, f"action providers not accepted by lifecycle triggers: {missing}"
    # Explicitly pin the T1/T2 providers.
    assert {"run-prompt", "run-workflow"} <= set(ALLOWED_HOOK_PROVIDERS)
