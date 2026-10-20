"""A room's summary and a chat's history compression each write one usage row, and the model-call
census leaves out every call a usage row already counts.

Measured on ``integration``: a room member folding its context and a
chat compressing its history both call a model on the Background axis, through ``ModelCallGuard``,
so each call got a ``model_calls.jsonl`` row and charged the spend meter, and ``usage/turns.jsonl``
got nothing. Settings → Usage, its daily-budget line and a chat's own total never saw them. The
census beside the usage page counted every guarded call, those a usage row already counts
included, because no row named the call it came from: a loop's inferences were stated as "not
included" while its turn's row held their cost.

The join is the call's ``audit_id``: the guard stamps it on the ``EVENT_COMPLETE`` it yields, the
native loop carries those of each inference of its turn, and the row written from that event keeps
them.

Every model is scripted behind the real registry, the real resolution seam and the real guard; the
room fold, ``compress_thread_history``, the background compression pass and the usage fold are the
real ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

pytestmark = pytest.mark.asyncio

ENTRY, MODEL = "summary-oai", "gpt-4o"
REF = f"{ENTRY}:{MODEL}"
TOKENS_IN, TOKENS_OUT = 40_000, 2_000
SUMMARY = "the analyst argued the numbers; the skeptic doubted the premise"


def _price() -> float:
    """What the shipped table's row for the model bills the call's tokens at."""
    from personalclaw.pricing import price_row

    row = price_row(MODEL)
    assert row is not None, f"premise: the shipped table prices {MODEL}"
    return round((TOKENS_IN * row.fields["in"] + TOKENS_OUT * row.fields["out"]) / 1e6, 6)


class _Scripted:
    """The one model every entry builds: it answers with :data:`SUMMARY` and reports its usage."""

    supports_tools = False

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def _answer(self, what: str):
        self.calls.append(what)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=SUMMARY)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)

    def stream(self, message: str):
        return self._answer("stream")

    def complete(self, messages: list[dict], **kw: Any):
        return self._answer("complete")


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    """One scripted entry, bound to the chat and Background axes; the calls it answered."""
    made: list[str] = []
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
        lambda **_kw: _Scripted(made),
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="scripted", model=MODEL))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [REF], "background": [REF]},
    )
    return made


def _rows() -> list[dict]:
    from personalclaw.usage_ledger import _iter_rows

    return _iter_rows()


def _attempts() -> list[dict]:
    from personalclaw.guardrails.audit import _audit_path

    path = _audit_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _census() -> dict:
    """What Settings → Usage states as not included (``GET /api/usage``'s ``uncounted``)."""
    from personalclaw.config.loader import config_dir
    from personalclaw.routing.usage import fold_files

    return fold_files(home=config_dir())["uncounted"]


def _the_one_call_and_its_row() -> tuple[dict, dict]:
    rows, attempts = _rows(), _attempts()
    assert len(rows) == 1, f"one model call, one usage row: {rows}"
    assert len(attempts) == 1, f"and one guarded call in the model-call log: {attempts}"
    return attempts[0], rows[0]


# ── a room's summary ──


@pytest.fixture
def long_room(monkeypatch):
    """A 200-message room with two members, rooms on, and a member window it overflows."""
    from personalclaw import context_headroom as ch
    from personalclaw.config.loader import AgentProfile, AppConfig
    from personalclaw.rooms import store, turn

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {"analyst": AgentProfile(), "skeptic": AgentProfile()}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    window = ch.Window(
        tokens=264, output_reserve_tokens=64, input_tokens=200, source="window-table"
    )

    async def _small(*_args, **_kwargs):
        return window

    monkeypatch.setattr(ch, "resolve_window", _small)
    room = store.create_room("A long room")
    store.add_member(room.id, "analyst", role_blurb="argues from the numbers")
    store.add_member(room.id, "skeptic")
    for i in range(200):
        store.append_message(
            room.id, role="assistant", content=f"turn {i}: a position at length", speaker="analyst"
        )
    turn._COMPACTION_SAVES.clear()
    yield store.require_room(room.id)
    turn._COMPACTION_SAVES.clear()


async def test_a_room_summary_writes_one_row_for_the_member_that_summarized(calls, long_room):
    """🔴 Red on integration: the fold's summary was a model call and the ledger stayed empty."""
    from personalclaw.rooms import store, turn

    member = long_room.member("skeptic")
    fed = await turn.member_context(
        long_room,
        member,
        store.read_messages(long_room.id),
        since_last_turn=False,
        serving=SimpleNamespace(served_model_ref=REF),
        reach="",
    )

    assert SUMMARY in fed, "the member was fed the summary"
    assert calls == ["stream"], "one summary, one model call"
    attempt, row = _the_one_call_and_its_row()
    assert (row["source"], row["session_key"], row["agent"]) == (
        "room",
        turn.session_key(long_room.id, "skeptic"),
        "skeptic",
    ), "the member that summarized pays, and a room's spend still reads per member"
    assert (row["provider"], row["model"]) == (ENTRY, MODEL), "the entry and model it ran on"
    assert (row["input_tokens"], row["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    assert (row["cost_usd"], row["priced"]) == (_price(), True)
    assert attempt["use_case"] == "background"
    assert row["audit_ids"] == [attempt["audit_id"]], "the row names the call it counts"
    assert _census()["calls"] == 0, "so the model-call census does not count it again"


# ── a chat's history compression ──


def _long_history(turns: int = 60) -> list[dict]:
    """More than the 45,000 characters compression keeps verbatim, so a model compresses it."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " + "x" * 1000}
        for i in range(turns)
    ]


async def test_history_compression_writes_one_row_for_the_chat_it_compressed(calls):
    """🔴 Red on integration: compressing a chat's history called the lite agent's model and wrote
    no usage row."""
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    from personalclaw.context import compress_thread_history

    compressed = await compress_thread_history(
        _long_history(), "dashboard:chat-long", "What did we decide?"
    )

    assert compressed is not None and SUMMARY in compressed
    assert calls == ["stream"], "one compression, one model call"
    attempt, row = _the_one_call_and_its_row()
    assert attempt["use_case"] == "background", "asked of the Background chain, through the guard"
    assert (row["source"], row["session_key"], row["agent"]) == (
        "background",
        "dashboard:chat-long",
        LITE_AGENT_NAME,
    ), "a background chore, on the chat it was made for"
    assert (row["provider"], row["model"]) == (ENTRY, MODEL)
    assert (row["input_tokens"], row["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    assert (row["cost_usd"], row["priced"]) == (_price(), True)
    assert row["audit_ids"] == [attempt["audit_id"]], "the row names the call it counts"
    assert _census()["calls"] == 0


async def test_background_compression_of_an_idle_chat_writes_one_row(calls, tmp_path):
    """🔴 Red on integration: the idle-chat pass summarized the oldest span through
    ``compress_prose`` and wrote no usage row."""
    from personalclaw import bg_compress
    from personalclaw.history import ConversationLog

    log = ConversationLog(base_dir=tmp_path / "sessions")
    key = "dashboard_chat-idle"
    for topic in range(4):
        for i in range(6):
            log.append(key, "user", f"topic {topic} question {i} " + "x" * 300)
            log.append(key, "assistant", f"topic {topic} answer {i} " + "y" * 300)

    result = await bg_compress.compress_session(log, key, embed_fn=None)

    assert result is not None, "the pass wrote its record"
    assert calls == ["stream"]
    attempt, row = _the_one_call_and_its_row()
    assert (row["source"], row["session_key"], row["agent"]) == (
        "background",
        "dashboard:chat-idle",
        "",
    ), "under the key the chat's own turns are recorded by"
    assert (row["provider"], row["model"]) == (ENTRY, MODEL)
    assert (row["cost_usd"], row["priced"]) == (_price(), True)
    assert row["audit_ids"] == [attempt["audit_id"]]
    assert _census()["calls"] == 0


@pytest.mark.parametrize(
    ("filed_as", "recorded_as"),
    [
        ("dashboard_chat-idle", "dashboard:chat-idle"),
        ("dashboard_dashboard_chat-idle", "dashboard:chat-idle"),
        ("slack_C1_1712.3", "slack_C1_1712.3"),
    ],
    ids=["a dashboard chat", "a resume-stacked file name", "a chat of another kind"],
)
async def test_the_idle_chat_pass_names_a_chat_as_its_turns_are_recorded(filed_as, recorded_as):
    """The pass reads a chat by the name its transcript is filed under, and the key rule it
    records the row by lives below the HTTP surface (the structural rail refuses the upward
    import of ``dashboard.chat_utils`` it used to take)."""
    from personalclaw.constants import dashboard_key_from_file_form
    from personalclaw.dashboard.chat_utils import _history_key_for

    assert dashboard_key_from_file_form(filed_as) == recorded_as
    if filed_as.startswith("dashboard_"):
        assert _history_key_for(filed_as) == recorded_as, "the dashboard reads it the same way"


# ── the census counts what no row counts, and nothing else ──


async def test_a_call_that_wrote_no_row_is_still_stated_as_not_included(calls):
    """The control for the census rows above: a guarded call nobody recorded is still counted,
    with its cost, so "0 not included" is a measurement. No caller makes one now (a one-shot call
    whose caller names nobody is recorded as background work), so the unrecorded call here is the
    guard driven bare, beside a one-shot call that writes its row."""
    from personalclaw.guardrails.model_call import ModelCallGuard
    from personalclaw.llm_helpers import one_shot_completion

    await one_shot_completion("Title this chat.", use_case="background")
    bare = ModelCallGuard(_Scripted([]), use_case="background", provider_name=ENTRY, model=MODEL)
    assert [e.kind async for e in bare.stream("Summarize this.")][-1] == EVENT_COMPLETE

    attempts = _attempts()
    assert len(attempts) == 2 and len(_rows()) == 1
    census = _census()
    assert census["calls"] == 1, "the unrecorded call, and only it"
    assert census["dollars_est"] == pytest.approx(_price())
    assert census["by_use_case"] == {"background": 1}


async def test_a_loop_turns_inferences_are_counted_once(calls):
    """🔴 Red on integration: a turn on a guarded axis (a loop worker's, on ``loops``) wrote its row
    and every one of its inferences was still censused as not included, "cannot be merged without
    double-counting loops". The row now names them."""
    from personalclaw.llm_helpers import stream_and_collect
    from personalclaw.providers import provider_bridge
    from personalclaw.usage_ledger import Attribution, recorder

    runtime = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="loop:abc123",
        agent=None,
        model_override=None,
        cwd=None,
        model_axis="loops",
    )
    await runtime.start()
    who = Attribution(source="loop", session_key="loop:abc123")
    await stream_and_collect(runtime, "Next step.", on_complete=recorder(runtime, who))

    attempt, row = _the_one_call_and_its_row()
    assert attempt["use_case"] == "loops"
    assert row["audit_ids"] == [attempt["audit_id"]]
    assert _census()["calls"] == 0


async def test_the_guard_names_its_call_on_the_event_and_leaves_the_providers_own():
    from personalclaw.guardrails.model_call import ModelCallGuard

    made: list[str] = []
    inner = _Scripted(made)
    produced: list[LLMEvent] = []

    async def _own(message: str):
        async for event in inner._answer("stream"):
            produced.append(event)
            yield event

    inner.stream = _own  # type: ignore[method-assign]
    guard = ModelCallGuard(inner, use_case="background", provider_name=ENTRY, model=MODEL)
    events = [e async for e in guard.stream("hello")]

    (done,) = [e for e in events if e.kind == EVENT_COMPLETE]
    (attempt,) = _attempts()
    assert done.audit_ids == (attempt["audit_id"],)
    assert [e.audit_ids for e in produced if e.kind == EVENT_COMPLETE] == [()], "a copy is named"


async def test_a_ledger_row_with_no_join_still_folds(tmp_path: Path):
    """A row written before rows named their calls reads as naming none, never as corrupt."""
    from personalclaw.routing.usage import audit_census, ledgered_audit_ids

    rows = [{"ts": "2026-09-27T00:00:00+00:00", "audit_ids": "not-a-list"}, {"ts": "x"}]
    assert ledgered_audit_ids(rows) == frozenset()
    census = audit_census([{"audit_id": "a1", "ts": 0, "dollars_est": 0.5, "use_case": "loops"}])
    assert (census["calls"], census["dollars_est"]) == (1, 0.5)
