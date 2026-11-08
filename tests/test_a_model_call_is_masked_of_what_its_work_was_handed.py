"""A prompt sent through the model-call guard is masked of each secret value the step or automation
sending it was handed, as written or encoded, before the guard scans, records or sends it. Outside
any such work the prompt is sent as it is.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote, quote_plus

import pytest

from personalclaw import filled_secrets
from personalclaw.guardrails import wire
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

pytestmark = pytest.mark.security

#: Shapeless, so no credential or PII shape the outbound scan knows finds it.
VALUE = "mv-41b7e0c2-orchard-gate-value"
#: What the mask writes where the value was.
MASK = filled_secrets.masked(VALUE, [VALUE])


class _Model:
    """The model the guard wraps: it keeps what it is sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def stream(self, message: str):
        self.sent.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.sent.append(repr(messages))
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE)


def _guard(model: _Model) -> ModelCallGuard:
    return ModelCallGuard(model, use_case="orchestration", provider_name="orchard", model="m")


async def _by_stream(guard: ModelCallGuard, prompt: str) -> None:
    async for _ in guard.stream(prompt):
        pass


async def _completed(guard: ModelCallGuard, prompt: str) -> None:
    messages = [{"role": "system", "content": "judge"}, {"role": "user", "content": prompt}]
    async for _ in guard.complete(messages):
        pass


CALLS = {"a-prompt": _by_stream, "a-request": _completed}


@pytest.mark.asyncio
@pytest.mark.parametrize("call", sorted(CALLS))
async def test_a_model_call_is_masked_of_the_values_its_work_was_handed(call):
    """🔴 Red before: the model was sent the value."""
    model = _Model()
    recorded: list[str] = []
    real = wire.record_outbound

    def _keep(text, **kw):
        recorded.append(text)
        return real(text, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.guardrails.model_call.record_outbound", _keep)
        with filled_secrets.handed([VALUE]):
            await CALLS[call](_guard(model), f"judge the beds with {VALUE}")

    assert model.sent and all(VALUE not in sent for sent in model.sent), model.sent
    assert any(MASK in sent for sent in model.sent)
    assert recorded and all(VALUE not in text for text in recorded), "the record of what was sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("call", sorted(CALLS))
async def test_a_model_call_outside_any_work_is_sent_as_it_is(call):
    """A control: with nothing handed, the prompt reaches the model unchanged."""
    model = _Model()

    await CALLS[call](_guard(model), f"judge the beds with {VALUE}")

    assert model.sent and all(VALUE in sent for sent in model.sent)


# ── what the work holds, however a prompt carries it ───────────────────────────────────────────

#: A value with characters an address or a page writes differently.
RESERVED = "mv 41b7/e0c2&orchard<gate>+value"

#: Each form a prompt can carry the value in.
FORMS = {
    "as-written": RESERVED,
    "percent-encoded": quote(RESERVED, safe=""),
    "percent-encoded-keeping-slashes": quote(RESERVED),
    "plus-encoded": quote_plus(RESERVED),
    "html-escaped": html.escape(RESERVED),
    # As a browser writes it into an address's query or path: a space and the brackets only.
    "as-a-browser-writes-it": "mv%2041b7/e0c2&orchard%3Cgate%3E+value",
    "percent-encoded-in-lower-case": quote(RESERVED, safe="").lower(),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("form", sorted(FORMS))
async def test_a_prompt_is_masked_of_each_form_the_value_takes(form):
    """🔴 Red before: an encoded or escaped value was sent as it was."""
    model = _Model()

    with filled_secrets.handed([RESERVED]):
        await _by_stream(_guard(model), f"the page said {FORMS[form]} today")

    assert model.sent and FORMS[form] not in model.sent[0], model.sent


def test_an_html_escape_that_keeps_quotes_is_masked():
    """``html.escape(value, quote=False)``, as a page writes text: a quote kept, an ampersand
    escaped. 🔴 Red before: it was kept as it was."""
    quoted = 'mv "41b7" & e0c2-orchard'

    assert filled_secrets.masked(f"said {html.escape(quoted, quote=False)} ok", [quoted]) == (
        f"said {MASK} ok"
    )


def test_a_value_no_encoding_can_build_is_masked_and_nothing_raises():
    """A value holding an unpaired surrogate is masked as written, and a log line in its hold is
    written masked, not withheld. 🔴 Red before: building its encoded forms raised."""
    import logging

    from personalclaw.security import MaskingFormatter

    for odd in ("mv-\ud800-orchard-value", "mv-\udc00-orchard-value", "mv-\udcff-orchard-value"):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, f"ran with {odd}", None, None)

        assert filled_secrets.masked(f"a {odd} b", [odd]) == f"a {MASK} b"
        with filled_secrets.handed([odd]):
            assert MaskingFormatter("%(message)s").format(record) == f"ran with {MASK}"


#: A value with a space, an apostrophe, `?`, a backtick, braces and `&`, and how a browser writes
#: it, recorded from ``new URL(…).href`` (the WHATWG URL Standard): into a query, and into a path
#: (which a `?` would end, so the path's value holds none).
BROWSER_QUERY = ("bv 7q'1x?z`9{k}2&W", "bv%207q%271x?z`9{k}2&W")
BROWSER_PATH = ("bv 7q'1xQz`9{k}2&W", "bv%207q'1xQz%609%7Bk%7D2&W")


@pytest.mark.parametrize("vector", [BROWSER_QUERY, BROWSER_PATH], ids=["a-query", "a-path"])
def test_a_value_is_masked_as_a_browser_writes_it_into_an_address(vector):
    """🔴 Red before: a browser's percent-encoded form was kept as it was."""
    value, written = vector

    assert filled_secrets.masked(f"at /p/{written} now", [value]) == f"at /p/{MASK} now"


def _long(text: str) -> str:
    from personalclaw.guardrails.scan import SCAN_OFF_LOOP_CHARS

    return "the beds are watered. " * (SCAN_OFF_LOOP_CHARS // 20) + text


@pytest.mark.asyncio
async def test_a_long_prompt_is_masked_as_a_short_one_is():
    """A prompt past the length the guard scans off the event loop, as sent and as the guard
    records it. 🔴 Red before: it was sent with the value."""
    from personalclaw.guardrails.scan import SCAN_OFF_LOOP_CHARS
    from personalclaw.guardrails.wire import capture_wire_prompt

    model = _Model()

    with capture_wire_prompt() as recorded, filled_secrets.handed([VALUE]):
        await _by_stream(_guard(model), _long(f" {VALUE}"))

    assert len(model.sent[0]) > SCAN_OFF_LOOP_CHARS
    assert VALUE not in model.sent[0]
    assert recorded.text and VALUE not in recorded.text, "the guard's record holds no value"


@pytest.mark.asyncio
async def test_a_requests_system_message_and_tool_call_are_masked():
    """🔴 Red before: the system message and the tool call's arguments held the value."""
    model = _Model()
    messages = [
        {"role": "system", "content": f"you hold {VALUE}"},
        {"role": "user", "content": "go on"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "function": {"name": "f", "arguments": f'{{"k": "{VALUE}"}}'}}
            ],
        },
    ]

    with filled_secrets.handed([VALUE]):
        async for _ in _guard(model).complete(messages):
            pass

    assert model.sent and VALUE not in model.sent[0], model.sent


@pytest.mark.asyncio
async def test_browses_model_call_is_masked_through_its_own_path(monkeypatch):
    """Browse's own call (`_decide`, through `one_shot_completion`) reaches the guard. 🔴 Red
    before: the model was sent the value."""
    from personalclaw.action_providers import browse_provider
    from personalclaw.providers import provider_bridge

    model = _Model()
    asked: list[str] = []

    def _resolve(use_case: str, **_kw: Any) -> ModelCallGuard:
        asked.append(use_case)
        return ModelCallGuard(model, use_case=use_case, provider_name="orchard", model="m")

    monkeypatch.setattr(provider_bridge, "resolve_metered_model", _resolve)

    with filled_secrets.handed([VALUE]):
        await browse_provider._decide(f"open the page for {VALUE}")

    assert set(asked) == {browse_provider.USE_CASE}, "the guard browse asks for"
    assert model.sent and all(VALUE not in sent for sent in model.sent), model.sent


@pytest.mark.asyncio
async def test_a_lifecycle_triggers_model_call_is_masked(tmp_path, monkeypatch):
    """A lifecycle trigger's action holds what it was filled with while it runs, as a fire does.
    🔴 Red before: the action's model call was sent the value."""
    from personalclaw.action_providers import registry
    from personalclaw.action_providers.base import ActionProvider, ActionResult
    from personalclaw.config import credentials as cred
    from personalclaw.config import loader
    from personalclaw.hooks import ScriptHookStore, run_script_hook
    from personalclaw.tool_providers.base import RiskLevel

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    monkeypatch.setenv("ORCHARD_HOOK_TOKEN", "x")
    monkeypatch.delenv("ORCHARD_HOOK_TOKEN")
    cred.save_credential("ORCHARD_HOOK_TOKEN", VALUE)
    model = _Model()

    class _Asks(ActionProvider):
        """An action whose work asks a model, through the guard, with the config it is handed."""

        @property
        def name(self) -> str:
            return "orchard-asks"

        @property
        def display_name(self) -> str:
            return "Orchard asks"

        def effect(self, action_config):
            return RiskLevel.SAFE

        async def execute(self, action_config, ctx, timeout=30):
            await _by_stream(_guard(model), f"sort the beds with {action_config['token']}")
            return ActionResult(success=True, stdout="{}")

    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, "orchard-asks", _Asks())
    store = ScriptHookStore(home)
    store.create(
        {
            "id": "orchard-asks-hook",
            "name": "Orchard asks on stop",
            "event": "Stop",
            "provider": "orchard-asks",
            "provider_config": {"token": "{{secret:ORCHARD_HOOK_TOKEN}}"},
            "capabilities": {"providers": ["orchard-asks"]},
        }
    )
    result = await run_script_hook(store.get("orchard-asks-hook"), "", {"hook_event_name": "Stop"})

    assert model.sent, f"the action ran and asked its model: {result.error}"
    assert all(VALUE not in sent for sent in model.sent), model.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("length", ["short", "long"])
@pytest.mark.parametrize("call", sorted(CALLS))
async def test_a_prompt_the_mask_changed_is_journalled_as_altered(call, length):
    """What the run journals of a prompt says the prompt was altered on its way, however long.
    🔴 Red before: it read as sent as written."""
    from personalclaw.guardrails.wire import capture_wire_prompt
    from personalclaw.workflows.engine_support import journalled_prompt

    prompt = f"judge the beds with {VALUE}"
    prompt = _long(prompt) if length == "long" else prompt
    with capture_wire_prompt() as wire, filled_secrets.handed([VALUE]):
        await CALLS[call](_guard(_Model()), prompt)

    journalled = journalled_prompt(wire, prompt)
    altered = [flag for flag in journalled.values() if isinstance(flag, bool)]
    assert altered == [True], "the journal says the prompt was changed on its way"
    assert VALUE not in str(journalled["resolved_prompt"])


@pytest.mark.asyncio
async def test_a_requests_signatures_and_ids_go_as_they_are():
    """Only the text the scan reads is masked: a reasoning record's signature and a tool call's id
    go as they are, though the value's text is in them, so the provider takes the request. And the
    request is scanned once, masked. 🔴 Red before: a short value changed both, and the unmasked
    request was scanned too."""
    from personalclaw.guardrails import model_call
    from personalclaw.llm.reasoning_record import REASONING_KEY

    short = "q7"
    signed = {"type": "thinking", "thinking": "plan", "signature": "sig-q7-abc"}
    messages = [
        {"role": "user", "content": "go q7"},
        {
            "role": "assistant",
            "content": "",
            REASONING_KEY: [signed],
            "tool_calls": [{"id": "call-q7", "function": {"name": "f", "arguments": "{}"}}],
        },
    ]
    sent: list[Any] = []
    scanned: list[str] = []
    real = model_call.scan_request

    class _Keeps(_Model):
        async def complete(self, messages, **_kw: Any):
            sent.append(messages)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
            yield LLMEvent(kind=EVENT_COMPLETE)

    def _scan(request: list[dict], *, mode: str) -> Any:
        scanned.append(repr(request))
        return real(request, mode=mode)

    model = _Keeps()
    with filled_secrets.handed([short]), pytest.MonkeyPatch.context() as mp:
        mp.setattr(model_call, "scan_request", _scan)
        async for _ in _guard(model).complete(messages):
            pass

    (request,) = sent
    assert short not in request[0]["content"]
    assert request[1][REASONING_KEY] == [signed]
    assert request[1]["tool_calls"][0]["id"] == "call-q7"
    assert len(scanned) == 1 and "go q7" not in scanned[0], "scanned once, masked"
