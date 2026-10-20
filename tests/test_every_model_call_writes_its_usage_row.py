"""Every model call PersonalClaw makes writes its usage row.

Settings → Usage sums the usage ledger (``usage/turns.jsonl``), so a call that writes no row is
spend it cannot show. Chat titles and tags, follow-up chips, suggestions, folder icons, memory
consolidation, the side chat, a hook's agent turn, the prompt optimizer, the eval and loop judges,
the knowledge nodes, a screenshot's description and every one-shot call whose caller named nobody
(digests among them) wrote none. The guarded ones reached only the model-call log, which Settings
→ Usage could state as "not included"; the rest left no record of their spend at all.

Two halves. The rail reads every place core consumes a model's stream (an ``async for`` over a
``.stream(...)`` or ``.complete(...)`` call, or over the same call read inside
``closing_stream(...)``, and every ``stream_and_collect(...)``) and fails for one whose function
writes no row. The behaviour half drives a chore, a one-shot call and three
judges through the real resolution seam, the real guard and a scripted model, and reads the row
each wrote, and that the model-call census no longer counts the call.

What the rail cannot see: a stream assigned to a name and iterated later. ``one_shot_completion``
writes its own row, so its callers are not scanned.

A turn that ends in an error has no ``EVENT_COMPLETE``: it sends ``EVENT_SPENT`` first, the usage of
the calls it made before the error, and a third half reads that every site consuming a stream
itself writes its row from that too (a collector does it for its callers). Without it the calls a
turn made before a dollar cap stopped it reached the spend meter and the model-call log, and never
Usage.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from every_model_call_writes_its_usage_row_shared import spent_unread, unrecorded_calls

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

# ── the rail ──────────────────────────────────────────────────────────────────────────────


def test_the_rail_fails_a_stream_that_writes_nothing():
    _sites, missing = unrecorded_calls(
        "async def chore(client, prompt):\n"
        "    async for event in client.stream(prompt):\n"
        "        pass\n"
        "async def collect(client, prompt):\n"
        "    return await stream_and_collect(client, prompt)\n"
        "async def closed(client, prompt):\n"
        "    async with closing_stream(client.stream(prompt)) as events:\n"
        "        async for event in events:\n"
        "            pass\n"
    )
    assert sorted(missing) == ["chore", "closed", "collect"]


def test_the_rail_passes_a_stream_whose_function_writes_its_row():
    sites, missing = unrecorded_calls(
        "async def chore(client, prompt, who):\n"
        "    record = recorder(client, who)\n"
        "    async def inner():\n"
        "        async for event in client.stream(prompt):\n"
        "            record(event)\n"
        "    await inner()\n"
        "async def collect(client, prompt, who):\n"
        "    return await stream_and_collect(client, prompt, on_complete=recorder(client, who))\n"
        "class Provider:\n"
        "    async def send(self, prompt):\n"
        "        async for event in self.stream(prompt):\n"
        "            pass\n"
    )
    assert sites == ["chore.inner", "collect"]
    assert missing == []


# ── a turn that ends in an error ─────────────────────────────────────────────────────────


def test_the_spent_half_fails_a_site_that_reads_only_the_complete_event():
    assert spent_unread(
        "async def chore(client, prompt, record):\n"
        "    async for event in client.stream(prompt):\n"
        "        if event.kind == EVENT_COMPLETE:\n"
        "            record(event)\n"
        "async def kept(client, prompt, record):\n"
        "    async for event in client.stream(prompt):\n"
        "        if event.kind in (EVENT_COMPLETE, EVENT_SPENT):\n"
        "            record(event)\n"
        "async def wrapped(client, prompt, record):\n"
        "    async for event in spent_rows(client.stream(prompt), record):\n"
        "        if event.kind == EVENT_COMPLETE:\n"
        "            record(event)\n"
        "async def collect(client, prompt):\n"
        "    return await stream_and_collect(client, prompt, on_complete=print)\n"
    ) == ["chore"]


def test_a_stream_wrapped_to_write_its_spent_row_is_still_a_site():
    """Wrapping a stream in ``spent_rows`` must not hide it from the census: it is still a model's
    stream, and still writes its row or is named."""
    sites, missing = unrecorded_calls(
        "async def chore(client, prompt, record):\n"
        "    async for event in spent_rows(client.stream(prompt), record):\n"
        "        pass\n"
    )
    assert (sites, missing) == (["chore"], ["chore"])


# ── the rows, written ─────────────────────────────────────────────────────────────────────

ENTRY, MODEL = "rows-oai", "gpt-4o"
REF = f"{ENTRY}:{MODEL}"
TOKENS_IN, TOKENS_OUT = 12_000, 800


class _Scripted:
    """The one model every entry builds: it answers and reports its usage."""

    supports_tools = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def _answer(self):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="An answer")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)

    def stream(self, message: str):
        return self._answer()

    def complete(self, messages: list[dict], **kw: Any):
        return self._answer()


@pytest.fixture
def scripted(monkeypatch) -> None:
    """One scripted entry, bound to every axis a chore or a judge resolves."""
    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=200_000,
        ),
        lambda **_kw: _Scripted(),
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="scripted", model=MODEL))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [REF], "background": [REF], "reasoning": [REF]},
    )


def _the_one_row() -> dict:
    """The one usage row the call wrote, naming the one guarded call it counts."""
    from personalclaw.guardrails.audit import _audit_path
    from personalclaw.usage_ledger import _iter_rows

    rows = _iter_rows()
    path = _audit_path()
    attempts = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if path.is_file()
        else []
    )
    assert len(rows) == 1, f"one model call, one usage row: {rows}"
    assert len(attempts) == 1, f"and one guarded call in the model-call log: {attempts}"
    assert rows[0]["audit_ids"] == [attempts[0]["audit_id"]], "the row names the call it counts"
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    return rows[0]


def _uncounted() -> int:
    """What Settings → Usage states as not included."""
    from personalclaw.config.loader import config_dir
    from personalclaw.routing.usage import fold_files

    return int(fold_files(home=config_dir())["uncounted"]["calls"])


@pytest.mark.asyncio
async def test_a_chats_title_is_the_chats_spend(scripted):
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    from personalclaw.dashboard.chat_title import chat_chore

    text = await chat_chore(
        SimpleNamespace(key="chat-t", memory_mode="persistent"), "Title this chat."
    )

    assert text == "An answer"
    row = _the_one_row()
    assert (row["source"], row["session_key"], row["agent"]) == (
        "background",
        "dashboard:chat-t",
        LITE_AGENT_NAME,
    ), "a background chore, on the chat it was made for"
    assert (row["provider"], row["model"]) == (ENTRY, MODEL)
    assert _uncounted() == 0, "and the census no longer states it as not included"


@pytest.mark.asyncio
async def test_a_one_shot_call_that_names_nobody_is_background_spend(scripted):
    """A digest, a nav link, a reply check: one-shot calls whose callers named nobody."""
    from personalclaw.llm_helpers import one_shot_completion

    assert await one_shot_completion("Digest this.", use_case="background") == "An answer"

    row = _the_one_row()
    assert (row["source"], row["session_key"], row["agent"]) == ("background", "", "")
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_an_eval_judges_verdict_is_eval_spend(scripted):
    from personalclaw.eval.judge import LLMJudge
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    judge = LLMJudge(lambda _key: resolve_provider_for_use_case("reasoning"))
    await judge.start()
    try:
        await judge.judge_turn("a scenario", "criteria", "a question", "an answer")
    finally:
        await judge.shutdown()

    row = _the_one_row()
    assert (row["source"], row["session_key"]) == ("eval", "eval_judge")
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_a_loop_judges_verdict_is_the_loops_spend(scripted):
    from personalclaw.loop import judge as judge_mod
    from personalclaw.loop.manager import loop_spend, usage_key

    finding = {"cycle": 1, "summary": "s"}
    await judge_mod.assess_cycle("a goal", "done when", finding, [], loop_id="l1")

    row = _the_one_row()
    # Under the key the loop's spend is read by, which is its worker's chat history key.
    assert (row["source"], row["session_key"]) == ("loop", usage_key("l1"))
    assert loop_spend("l1")["turns"] == 1, "the loop's own spend holds its judge"
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_a_stage_gates_verdict_is_the_loops_spend(scripted):
    from personalclaw.loop.gates import judge_verdict
    from personalclaw.loop.manager import loop_spend, usage_key

    assert await judge_verdict("PASS or FAIL?", loop_id="l2") == "An answer"

    row = _the_one_row()
    assert (row["source"], row["session_key"]) == ("loop", usage_key("l2"))
    assert loop_spend("l2")["turns"] == 1, "the loop's own spend holds its stage gate's verdict"
    assert _uncounted() == 0
