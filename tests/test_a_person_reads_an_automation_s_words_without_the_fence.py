"""The fence is for a model: a person reads an automation's words without it.

Text from outside reaches a model inside ``<untrusted_content …>`` markers (``security.
fence_untrusted``), and an automation hands its action that text fenced: a stored trigger's
payload, a lifecycle hook's words and what its action printed. Where that text is shown to a
person — a notification, a message on a chat channel, a hook's Test on the Triggers page — the
markers are noise around the words, so they come off there and the words are kept, as the
dashboard already takes them off where it shows fenced text (``web/src/lib/untrustedFence.ts``).
What a model is handed keeps them.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.action_providers.template import without_fence
from personalclaw.security import fence_untrusted, is_fenced


def _fenced(text: str) -> str:
    return fence_untrusted(
        text, source="trigger:w", source_type="web_watch", source_id="https://shop.example"
    )


# ── the helper ──


def test_without_fence_keeps_the_words_and_takes_the_markers_off():
    assert without_fence(_fenced("Spring sale on seeds")) == "Spring sale on seeds"
    assert without_fence("<untrusted_content>\nplain\n</untrusted_content>") == "plain"
    assert without_fence(f"New: {_fenced('Seeds')} and more") == "New: Seeds and more"


def test_a_marker_quoted_inside_the_words_stays_as_it_was_quoted():
    """The fence escapes a marker found inside what it wraps, so only its own markers come off."""
    shown = without_fence(_fenced("a page that quotes <untrusted_content> in its text"))
    assert shown == "a page that quotes &lt;untrusted_content&gt; in its text"


def test_a_value_rendered_from_a_list_loses_its_markers_too():
    """A payload list renders as its repr, where the fence's line breaks read ``\\n``."""
    shown = without_fence(str([_fenced("Seeds"), _fenced("Bulbs")]))
    assert shown == "['Seeds', 'Bulbs']"


def test_text_with_no_fence_is_left_as_it_is():
    assert without_fence("Agent finished: all done") == "Agent finished: all done"
    assert without_fence("") == ""


# ── a notification and a channel message ──


def test_a_notification_shows_a_fenced_value_s_words_without_the_fence(monkeypatch):
    from personalclaw.action_providers import notify_provider
    from personalclaw.action_providers.notify_provider import NotifyActionProvider

    notes: list[tuple[str, str, str]] = []
    state = SimpleNamespace(notify=lambda kind, title, body, **_: notes.append((kind, title, body)))
    monkeypatch.setattr(
        notify_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )
    ctx = ActionContext(
        event="trigger.fired",
        context=_fenced("The model provider stopped answering."),
        payload={"new_items": [_fenced("Spring sale on seeds")]},
    )
    result = asyncio.run(
        NotifyActionProvider().execute(
            {"title_template": "New on the shop", "body_template": "$CONTEXT / $new_items"}, ctx
        )
    )
    assert result.success, result.error
    ((_, title, body),) = notes
    assert title == "New on the shop"
    assert body == "The model provider stopped answering. / ['Spring sale on seeds']", body


def test_a_channel_message_shows_a_fenced_value_s_words_without_the_fence(monkeypatch):
    from personalclaw.action_providers import send_message_provider
    from personalclaw.action_providers.send_message_provider import SendMessageActionProvider

    notes: list[tuple[str, str, str]] = []
    state = SimpleNamespace(
        notify=lambda kind, title, body, **_: notes.append((kind, title, body)),
        channel_delivery=None,
    )
    monkeypatch.setattr(
        send_message_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )
    ctx = ActionContext(event="Stop", context=_fenced("Merged the parser change."))
    result = asyncio.run(
        SendMessageActionProvider().execute({"text_template": "Agent done: $CONTEXT"}, ctx)
    )
    assert result.success, result.error
    ((_, _, text),) = notes
    assert text == "Agent done: Merged the parser change.", text


# ── a hook's Test on the Triggers page ──


class _Prints:
    async def execute(self, config, ctx, timeout=30):
        return ActionResult(success=True, stdout="Open pull requests: 3.")


def test_a_hook_s_test_shows_what_it_printed_and_the_turn_gets_it_fenced(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import trigger_runs
    from personalclaw.hooks import (
        HOOK_EVENT_USER_PROMPT_SUBMIT,
        ScriptHook,
        ScriptHookStore,
        run_script_hook,
    )
    from personalclaw.triggers import grants

    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: _Prints() if name == "invoke-agent" else real(name)
    )
    hook = ScriptHook(
        id="h-test",
        name="Pull requests",
        event=HOOK_EVENT_USER_PROMPT_SUBMIT,
        provider="invoke-agent",
        provider_config={"task_template": "count open pull requests"},
    )
    grants.give(hook)
    store = ScriptHookStore(config_dir=tmp_path)
    store.create(hook.to_dict())

    # What the turn is handed: fenced, with the hook as its source.
    fired = asyncio.run(run_script_hook(hook, "What is on today?"))
    assert is_fenced(fired.stdout) and "source=trigger:lifecycle:h-test " in fired.stdout

    # What the owner reads on the Test: the words.
    app = web.Application()
    app["state"] = MagicMock(_hook_store=store)
    request = make_mocked_request(
        "POST",
        "/api/triggers/lifecycle:h-test/test",
        match_info={"id": "lifecycle:h-test"},
        app=app,
    )

    async def _json():
        return {}

    request.json = _json  # type: ignore[assignment]
    response = asyncio.run(trigger_runs.api_trigger_test(request))
    shown = json.loads(response.body)["result"]
    assert shown["stdout"] == "Open pull requests: 3.", shown
