"""Compaction keeps the request the running turn is answering, and what the user asked before it.

Measured on the native loop with a scripted model that reports how full each request leaves its
window, as a provider does, so the loop compacts at the start of a tool round whenever the history
crosses the threshold. Only the last eight messages were protected, and nothing looked for the
request. Once a long turn's rounds had pushed its request out of that tail, the next pass folded it
into the summary, which kept the first ten user messages of what it folded at 200 characters each:
a long request was cut to its first line, and behind ten earlier questions it was gone in one pass.
The next pass listed the first pass's summary as a request and cut it to 200 characters, so what the
first pass had kept was lost as well, and a steer sent mid-turn went the same way. The summary told
the model to act on "the latest message below", which by then was a tool result.

The rule now: the request a turn answers, every message the user added after it, and the runtime's
note for the turn are kept word for word wherever they sit. The summary lists what the user asked in
their own words, the newest word for word and older ones by their first line within a fixed budget,
and counts the rest. A later pass carries an earlier summary forward.
"""

from __future__ import annotations

import json
import re

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context import USER_REQUEST_MARKER
from personalclaw.context_compaction import compact, prune_tool_outputs, total_chars
from personalclaw.llm.events import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.llm.prompt_cache import turn_note_message
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: A long request with its instructions on later lines, the kind a first-line digest loses.
REQUEST = "\n".join(
    [
        "Please move the billing reports to the new ledger format.",
        "Keep the March totals exactly as they are: finance signed them off.",
        "Rename every column called amt_usd to amount, and keep the cents.",
        "Leave the archive folder alone, it belongs to the audit team.",
        "When you are done, write a short changelog in docs/ledger-move.md.",
        *(f"Region {i}: its report still rounds half-up, so check it twice." for i in range(16)),
    ]
)
STEER = "Also leave the Q1 forecast file alone, it is being edited right now."
#: Twelve earlier questions, more than the ten the summary used to keep, each longer than the 200
#: characters it kept of one.
EARLIER = [
    f"Question {i}: which exports does folder {i} hold, who owns each of them, and which ones "
    f"changed since the last close? List the owners by team, and say for every export in folder "
    f"{i} whether its totals still match the ledger and when it was last rebuilt."
    for i in range(12)
]
ROUNDS = 12
#: The scripted model's window, in characters. It reports the share of it each request fills, as a
#: provider reports usage, so the loop compacts when the history really crosses the Settings
#: threshold (90%): a few times in this turn, and not in the twelve before it.
WINDOW_CHARS = 18_000


class _Reports(ToolProvider):
    @property
    def name(self) -> str:
        return "reports"

    @property
    def display_name(self) -> str:
        return "Reports"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="read_report",
                description="Read one billing report.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                requires_approval=False,
                provider="reports",
            )
        ]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="row,amt_usd\n" + "1,2.50\n" * 300, error="")


class _Model:
    """Answers from a script, records every request, and reports how full each one left its
    window (:data:`WINDOW_CHARS`)."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, script: list[list[AgentEvent]]) -> None:
        self.script = script
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        used = 100.0 * total_chars(messages) / WINDOW_CHARS
        for event in self.script[min(len(self.requests), len(self.script)) - 1]:
            yield event
        yield AgentEvent(kind=EVENT_COMPLETE, context_usage_pct=used)


def _answer(text: str) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)]


def _round(i: int) -> list[AgentEvent]:
    return [
        AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=f"r{i}",
            title="read_report",
            tool_input=json.dumps({"path": f"reports/region-{i}.csv"}),
        )
    ]


async def _long_turn(*, steer_at: int | None = None) -> tuple[_Model, list[AgentEvent], int]:
    """Twelve earlier turns, then one turn of twelve tool rounds on REQUEST. Returns the model, the
    turn's events and the index of the turn's first request in ``model.requests``. *steer_at* is the
    number of the turn's model requests made before the user steers."""
    script = [_answer(f"Answer {i}: " + "the folder holds exports. " * 30) for i in range(12)]
    script += [_round(i) for i in range(ROUNDS)] + [_answer("The reports are moved.")]
    model = _Model(script)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="ledger", provider="native", model="s"),
        model_provider=model,
        tool_providers=[_Reports()],
    )
    await rt.start()
    for question in EARLIER:
        [e async for e in rt.stream(question)]
    first = len(model.requests)
    if steer_at is not None:
        pending = [STEER]
        rt.set_steer_source(
            lambda: [pending.pop()] if pending and len(model.requests) == first + steer_at else []
        )
    events = [e async for e in rt.stream(REQUEST)]
    return model, events, first


def _record(request: list[dict]) -> str:
    records = [str(m["content"]) for m in request if "CONTEXT COMPACTION" in str(m["content"])]
    assert len(records) == 1, f"one record of the folded part, not {len(records)}"
    return records[0]


# ── through the native loop: what each model request carries ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_long_turn_keeps_its_request_word_for_word_through_every_compaction():
    """🔴 Red before: once the turn's rounds had pushed the request out of the tail, the next pass
    folded it into the summary, behind the earlier questions, and it was gone."""
    model, events, first = await _long_turn()

    passes = [e for e in events if e.kind == EVENT_COMPACTION_STATUS]
    assert len(passes) >= 3, "the window must fill again and again, or this proves nothing"
    sent = model.requests[first:]
    for n, request in enumerate(sent, start=1):
        carried = [m for m in request if m.get("content") == REQUEST]
        assert len(carried) == 1, f"model request {n} carried the request {len(carried)} times"
    # The protection did the work, not the tail: rounds of this very turn were folded while its
    # request stayed (they came after it, so a tail that held the request would have held them).
    results = [m for m in sent[-1] if m.get("role") == "tool"]
    assert len(results) < ROUNDS, "no round of the turn was folded, so the tail kept the request"
    assert events[-1].kind == EVENT_COMPLETE


@pytest.mark.asyncio
async def test_a_steer_sent_mid_turn_survives_every_later_compaction():
    """🔴 Red before: the steer reached the model for a few rounds, then was folded away."""
    model, events, first = await _long_turn(steer_at=3)

    sent = model.requests[first:]
    at = next(i for i, r in enumerate(sent) if any(STEER in str(m["content"]) for m in r))
    steered = next(m["content"] for m in sent[at] if STEER in str(m["content"]))
    assert 0 < at < len(sent) - 4, "the steer must land mid-turn, with rounds still to come"
    for request in sent[at:]:
        assert [m.get("content") for m in request].count(steered) == 1
        assert [m.get("content") for m in request].count(REQUEST) == 1
    assert sum(e.kind == EVENT_COMPACTION_STATUS for e in events) >= 3


@pytest.mark.asyncio
async def test_a_later_compaction_carries_the_earlier_ones_record_forward():
    """🔴 Red before: a second pass listed the first pass's summary as a request, cut to 200
    characters, so what the first pass kept of the earlier questions was lost."""
    model, events, first = await _long_turn()

    assert sum(e.kind == EVENT_COMPACTION_STATUS for e in events) >= 2
    record = _record(model.requests[-1])
    # The first two questions sit in the protected head; every other one was folded, by more than
    # one pass, and each is still listed in full.
    for question in EARLIER[2:]:
        assert question in record, f"lost from the record: {question[:40]}"
    assert record.count("[CONTEXT COMPACTION") == 1, "a record listed inside a record"
    assert "[RESUME ACCOUNT" not in record, "an account listed as one of the user's requests"


# ── the pass itself ─────────────────────────────────────────────────────────────────────────────


def _exchange(i: int, size: int = 2000) -> list[dict]:
    call = {
        "id": f"c{i}",
        "type": "function",
        "function": {"name": "read_report", "arguments": json.dumps({"path": f"r{i}.csv"})},
    }
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
    ]


def _chat(requests: list[str]) -> list[dict]:
    """One short exchange per request, after a three-message head."""
    msgs: list[dict] = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second message"},
    ]
    for text in requests:
        msgs += [{"role": "user", "content": text}, {"role": "assistant", "content": "done " * 80}]
    return msgs


def test_a_second_pass_keeps_what_the_first_one_listed():
    """Two compactions of one long turn, at the module: the second folds the first's record."""
    earlier = [
        f"Earlier request {i}: " + "move the March export and keep its cents. " * 6
        for i in range(6)
    ]
    request = {"role": "user", "content": REQUEST}
    msgs = _chat(earlier) + [request]
    for i in range(6):
        msgs += _exchange(i)
    once = compact(msgs)
    assert _record(once), "the fixture must fold on the first pass"
    for i in range(6, 12):
        once += _exchange(i)
    twice = compact(once)

    record = _record(twice)
    assert all(text in record for text in earlier), "the second pass lost what the first kept"
    assert record.count("[CONTEXT COMPACTION") == 1
    assert [m for m in twice if m.get("content") == REQUEST] == [request]


def test_a_chat_that_fits_the_protected_tail_comes_back_as_the_pre_pass_leaves_it():
    """A short chat is not compacted differently: nothing to fold, nothing added."""
    msgs = _chat(["what is in the March export?", "and in April?"]) + _exchange(0)
    assert compact(msgs) == prune_tool_outputs(msgs)


def test_with_the_request_inside_the_tail_the_fold_is_the_head_the_record_and_the_tail():
    """The end-of-turn shape folds exactly as before: the same head, the same tail, one record."""
    msgs = _chat([f"request {i}" for i in range(10)])
    out = compact(msgs)
    assert out[:3] == msgs[:3] and out[-8:] == msgs[-8:]
    assert len(out) == 3 + 1 + 8, "nothing else kept, and no account for a fold with no calls"


def _folded(asked: list[str], out: list[dict]) -> list[int]:
    """Which of *asked* the pass folded: every one not kept as a message of its own."""
    kept = {str(m.get("content")) for m in out}
    return [i for i, text in enumerate(asked) if text not in kept]


def _word_for_word(text: str, record: str) -> bool:
    """*text* listed whole in *record*, each of its lines quoted as the record writes them."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n")) in record


def test_the_record_lists_every_earlier_request_that_fits_not_the_first_ten():
    """🔴 Red before: thirty folded requests were listed as their first ten."""
    asked = [f"Request {i}: rename the export for region {i}." for i in range(30)]
    out = compact(_chat(asked) + [{"role": "user", "content": "now"}])
    folded = _folded(asked, out)
    assert len(folded) > 10, "the fixture must fold more than the ten the old digest kept"
    record = _record(out)
    assert [i for i in folded if asked[i] not in record] == []


def test_requests_past_the_budget_are_shortened_to_their_first_line_then_counted():
    """Newest word for word, older ones by their first line, the oldest counted — never dropped
    without a word. 🔴 Red before: the eleventh and every later request vanished."""
    asked = [
        f"Request {i}: move the exports of region {i}.\n" + f"Detail for region {i}. " * 40
        for i in range(60)
    ]
    out = compact(_chat(asked) + [{"role": "user", "content": "now"}])
    folded = _folded(asked, out)
    record = _record(out)

    whole = [i for i in folded if _word_for_word(asked[i], record)]
    shortened = [i for i in folded if i not in whole and asked[i].split("\n")[0] in record]
    counted = re.search(r"(\d+) earlier requests? (?:is|are) not listed here\.", record)
    assert whole and shortened and counted, record[:400]
    assert len(whole) + len(shortened) + int(counted.group(1)) == len(folded)
    assert min(whole) > max(shortened), "the newest are the ones kept word for word"
    assert min(shortened) == int(counted.group(1)), "the oldest are the ones counted"


def test_an_earlier_turn_is_listed_in_the_users_own_words_not_its_assembled_prompt():
    """🔴 Red before: the digest kept an assembled turn's first 200 characters, the system
    prompt's opening, and none of what the user asked."""
    assembled = (
        "[AGENT SYSTEM PROMPT]\n"
        + "Follow the house rules. " * 20
        + f"\n\n{USER_REQUEST_MARKER}\nRename the March export to march-final.csv"
        + "\n\n[WIDGETS] You can render rich HTML inline."
    )
    later = [f"follow-up {i}" for i in range(6)]
    record = _record(compact(_chat([assembled, *later]) + [{"role": "user", "content": "go"}]))
    assert "Rename the March export to march-final.csv" in record
    assert "house rules" not in record


def test_the_record_says_the_request_is_kept_outside_it():
    """🔴 Red before: "act on the latest message below", and the latest message was a result."""
    request = {"role": "user", "content": REQUEST}
    msgs = _chat(EARLIER[:6]) + [request]
    for i in range(8):
        msgs += _exchange(i)
    out = compact(msgs)

    assert out[-1]["role"] == "tool"
    opening = _record(out).split("\n", 1)[0]
    assert "act on the latest message below" not in opening
    assert "most recent request is kept in full" in opening


def test_with_nothing_the_user_wrote_the_record_says_to_continue_from_the_latest_messages():
    """A feed of other agents' replies alone has no request to point at, and does not claim one."""
    msgs = [{"role": "assistant", "content": f"position {i}: " + "argued " * 40} for i in range(30)]
    opening = _record(compact(msgs, protect_head=0)).split("\n", 1)[0]
    assert "request" not in opening
    assert "most recent messages" in opening


def test_the_turns_runtime_note_survives_a_mid_turn_compaction():
    """The note the runtime adds for the turn (its tool catalog among it) is the turn's, like the
    request. 🔴 Red before: it was folded with the turn's early rounds."""
    note = turn_note_message(
        "[tool catalog] read_report, write_report and 40 more are listed here."
    )
    request = {"role": "user", "content": REQUEST}
    msgs = _chat(EARLIER[:6]) + [request, note]
    for i in range(8):
        msgs += _exchange(i)
    out = compact(msgs)

    assert any(m is note for m in out), "the turn's note was folded"
    assert [m for m in out if m is request or m is note] == [request, note], "and kept in order"


def test_a_named_request_keeps_every_message_the_user_added_after_it():
    """The runtime names the turn's request: the latest message the user wrote is then a steer,
    and both stay, in their order."""
    request = {"role": "user", "content": REQUEST}
    steer = {"role": "user", "content": f"[Steering — the user added this mid-task]\n{STEER}"}
    msgs = _chat(EARLIER[:6]) + [request, *_exchange(0), *_exchange(1), steer]
    for i in range(2, 9):
        msgs += _exchange(i)
    out = compact(msgs, request=request)

    assert [m for m in out if m is request or m is steer] == [request, steer]
