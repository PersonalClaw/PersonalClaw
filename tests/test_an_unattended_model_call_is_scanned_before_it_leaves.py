"""A model call made with structured messages is scanned before it leaves the machine.

The model-call guard scans a prompt for secrets and personal details before it goes to a model off
this machine, at the mode the setting asks for (``guardrails.scan_mode``, ``redact`` as shipped).
Its ``complete()`` path only classified the request: a loop's planner and workers, a subagent, the
knowledge pipeline's model nodes and a browse step's image reading all send their messages there,
so a credential in a file a loop read reached the provider as written, and ``block`` refused
nothing. Each text part (a system, user or tool message's, and what a model wrote, which can echo
text a model on this machine was sent unscanned) is now scanned as a one-shot prompt is, on a copy,
the same way every call, and a long scan runs off the event loop. A model on this machine still
warns only.

Every call goes to a fake provider that records what it was sent; nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from personalclaw.guardrails import scan as outbound_scan
from personalclaw.guardrails.audit import read_recent
from personalclaw.guardrails.failure import FailureMode, SecretLeakBlocked
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.guardrails.wire import capture_wire_prompt
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider
from personalclaw.llm.registry import ProviderEntry, get_default_registry

#: A credential the outbound scan recognises by its shape (the documented example key id).
_KEY = "AKIAIOSFODNN7EXAMPLE"
_READ = f"AWS_ACCESS_KEY_ID={_KEY}\nregion=us-east-1"


class _Provider(ModelProvider):
    """A model that answers "ok" and keeps every request's messages as it was sent them."""

    def __init__(self) -> None:
        self.sent: list[list[dict]] = []
        self.prompts: list[str] = []

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        self.prompts.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=2)

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.sent.append(messages)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=2)

    async def approve_tool(self, request_id) -> None:
        pass

    async def reject_tool(self, request_id) -> None:
        pass

    def context_usage_pct(self) -> float:
        return 0.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The guard audits every attempt and a block writes a security event: keep both here."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _guard(scan_mode: str, *, endpoint: str = "https://api.example.com/v1", kind: str = "") -> Any:
    """A guard over a fake model whose configured entry sends to *endpoint*."""
    name = "here" if "localhost" in endpoint else "elsewhere"
    get_default_registry().register_entry(
        ProviderEntry(
            name=name,
            type=kind or "openai_compatible",
            model="",
            options={"endpoint": endpoint},
        )
    )
    return wrap_model_call_guard(
        _Provider(), use_case="background", provider_name=name, model="m", scan_mode=scan_mode
    )


def _loop_turn(read: str = _READ) -> list[dict]:
    """A loop's request after it read a file: the instructions, the task, the model's call, and
    the tool's result."""
    return [
        {"role": "system", "content": "You maintain the deploy scripts."},
        {"role": "user", "content": "Check the deploy settings."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": read},
    ]


async def _complete(guard: Any, messages: list[dict]) -> str:
    await guard.start()
    return "".join([e.text async for e in guard.complete(messages) if e.kind == EVENT_TEXT_CHUNK])


def _sent_text(guard: Any) -> str:
    return repr(guard._inner.sent[-1])


@pytest.mark.asyncio
async def test_a_credential_in_a_tool_result_is_replaced_before_it_is_sent(home):
    """🔴 Red before: the tool result reached the provider with the credential in it."""
    guard = _guard("redact")
    messages = _loop_turn()

    assert await _complete(guard, messages) == "ok"

    sent = guard._inner.sent[-1]
    assert _KEY not in _sent_text(guard), "the credential left the machine as written"
    assert "region=us-east-1" in sent[3]["content"], "only the credential is replaced"
    assert sent[3]["tool_call_id"] == "c1"
    assert messages[3]["content"] == _READ, "the loop's own history is not changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"role": "system", "content": f"Deploy with {_KEY}."},
        {"role": "user", "content": f"Deploy with {_KEY}."},
        {"role": "user", "content": [{"type": "text", "text": f"Deploy with {_KEY}."}]},
    ],
    ids=["system", "user", "user-part"],
)
async def test_every_text_part_this_machine_supplies_is_scanned(home, message):
    """🔴 Red before: each of these went as written."""
    guard = _guard("redact")

    await _complete(guard, [message])

    assert _KEY not in _sent_text(guard)


@pytest.mark.asyncio
async def test_the_same_history_is_sent_as_the_same_bytes_on_the_next_call(home):
    """A loop sends its whole history again on each call: what it sent before must be sent the
    same way, or a prompt cache's prefix breaks on every call."""
    guard = _guard("redact")
    first = _loop_turn()
    await _complete(guard, first)
    later = [
        *first,
        {"role": "assistant", "content": "Read it."},
        {"role": "user", "content": "Go"},
    ]

    await _complete(guard, later)

    before, after = guard._inner.sent
    assert after[: len(before)] == before


@pytest.mark.asyncio
async def test_block_refuses_the_call_and_records_it(home):
    """🔴 Red before: the call went out with the credential in it."""
    guard = _guard("block")

    with capture_wire_prompt() as wire:
        with pytest.raises(SecretLeakBlocked):
            await _complete(guard, _loop_turn())

    assert guard._inner.sent == [], "a blocked call reached the provider"
    assert read_recent()[-1]["failure_mode"] == FailureMode.SECRET_LEAK.value
    assert wire.blocked and "credential" in wire.categories and wire.text == ""


@pytest.mark.asyncio
async def test_a_redacted_call_says_so_where_its_prompt_is_recorded(home):
    guard = _guard("redact")

    with capture_wire_prompt() as wire:
        await _complete(guard, _loop_turn())

    assert wire.captured and wire.redacted and "credential" in wire.categories
    assert _KEY not in wire.text


#: What a tool reads beside a credential: an address the task names, and a timestamp the phone
#: check would read as a number.
_PERSONAL = "notify ana@example.com\ncreated_at: 1730437200"


@pytest.mark.asyncio
async def test_an_email_and_a_number_are_noted_not_replaced(home):
    """Tool output is full of digit runs the phone check reads as numbers (sizes, ids, epoch
    stamps), and an address a task names is one the model must use: in what a loop sends, they
    are noted, and only the secret is replaced."""
    guard = _guard("redact")

    with capture_wire_prompt() as wire:
        await _complete(guard, _loop_turn(f"{_READ}\n{_PERSONAL}"))

    sent = guard._inner.sent[-1][3]["content"]
    assert _KEY not in sent
    assert "ana@example.com" in sent and "1730437200" in sent
    assert {"credential", "email", "phone"} <= set(wire.categories)


@pytest.mark.asyncio
async def test_block_sends_a_request_holding_only_an_email_and_an_id(home):
    guard = _guard("block")
    messages = _loop_turn('{"owner": "ana@example.com", "id": 4127917181}')

    assert await _complete(guard, messages) == "ok"

    assert guard._inner.sent[-1] == messages


@pytest.mark.asyncio
async def test_a_one_shot_prompt_still_has_its_email_replaced(home):
    """The control: a one-shot prompt keeps the whole ladder, personal details included."""
    guard = _guard("redact")
    await guard.start()

    async for _ in guard.stream("Summarise the thread with ana@example.com"):
        pass

    assert "ana@example.com" not in guard._inner.prompts[-1]


@pytest.mark.asyncio
async def test_a_model_on_this_machine_warns_only(home, ollama_app):
    """The control: a model here keeps the warn rule, so the redaction and block above are the
    scan doing its job and not a guard that rewrites every request."""
    guard = _guard("block", endpoint="http://localhost:11434", kind="ollama")
    messages = _loop_turn()

    assert await _complete(guard, messages) == "ok"

    assert guard._inner.sent[-1] == messages


@pytest.mark.asyncio
async def test_what_a_model_wrote_is_scanned_too(home):
    """A loop's history can hold an answer from a model on this machine, which only warns, or from
    a call made before its history was scanned; either can carry the text on to a model off it."""
    guard = _guard("redact")
    said = {"role": "assistant", "content": f"The file sets {_KEY}."}

    await _complete(guard, [{"role": "user", "content": "What is set?"}, said])

    assert _KEY not in _sent_text(guard)
    assert said["content"] == f"The file sets {_KEY}.", "the loop's own history is not changed"


@pytest.mark.asyncio
async def test_a_tool_calls_arguments_are_scanned_and_still_read_as_json(home):
    guard = _guard("redact")
    wrote = json.dumps({"path": "deploy.env", "content": _READ})
    call = {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": wrote}}

    await _complete(
        guard,
        [{"role": "user", "content": "Save it."}, {"role": "assistant", "tool_calls": [call]}],
    )

    sent = guard._inner.sent[-1][1]["tool_calls"][0]
    arguments = json.loads(sent["function"]["arguments"])
    assert _KEY not in arguments["content"] and arguments["path"] == "deploy.env"
    assert sent["id"] == "c1" and sent["function"]["name"] == "write_file"


class _WhereScanned:
    """Each text the outbound scan reads, by whether the event loop was running in its thread."""

    def __init__(self, monkeypatch) -> None:
        self.on_loop: list[int] = []
        self.off_loop: list[int] = []
        real = outbound_scan.scan_outbound

        def spy(text: str, **how: str):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self.off_loop.append(len(text))
            else:
                self.on_loop.append(len(text))
            return real(text, **how)

        monkeypatch.setattr(outbound_scan, "scan_outbound", spy)


@pytest.mark.asyncio
async def test_a_long_request_is_scanned_off_the_event_loop(home, monkeypatch):
    """A long text holds the event loop for as long as it takes to scan (some 150 ms for 200,000
    characters), and every other request the gateway serves waits it out."""
    where = _WhereScanned(monkeypatch)
    guard = _guard("redact")
    long_read = f"{uuid.uuid4().hex} {_READ} " + "the deploy settings and their history " * 1000

    await _complete(guard, _loop_turn(long_read))

    assert len(long_read) in where.off_loop, "a long text was scanned on the event loop"
    assert _KEY not in _sent_text(guard)


@pytest.mark.asyncio
async def test_each_call_scans_only_the_text_it_adds(home, monkeypatch):
    """A loop sends its whole history on every call; what was scanned before is not scanned again,
    so the history it sends next is short and scanned on the loop."""
    guard = _guard("redact")
    first = _loop_turn(f"{uuid.uuid4().hex} " + "the deploy settings " * 2000)
    await _complete(guard, first)
    where = _WhereScanned(monkeypatch)
    added = {"role": "user", "content": f"Now check the staging file {uuid.uuid4().hex}."}

    await _complete(guard, [*first, added])

    assert where.on_loop == [len(added["content"])] and where.off_loop == []
