"""Every new message is sorted in the background, and its verdict names the prompt that made it.

Before this, nothing ever sorted a message: every mail row read "Needs reply · Needs review" (the
row's stamped default) with Mark accurate / Mark wrong under it, and a verdict she gave there was
credited to the sorting prompt, which had never run. These tests drive the sorter with a scripted
model: a message arrives unsorted, a burst is sorted a batch per call, a verdict lands once and
names its prompt, and every way the sorter cannot sort says so on the row or waits, never inventing
a verdict.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace

import pytest

from personalclaw.inbox import InboxState, InboxStore, emit_attention_item, redact_item
from personalclaw.inbox_providers.base import IncomingMessage
from personalclaw.inbox_service import InboxService, polled_item_id
from personalclaw.inbox_sorting import BATCH_MAX, LEFT_OUT, UNREADABLE, InboxSorter

_MAIL = SimpleNamespace(source_name="mail-inbox")
_PROMPT = "native:task-inbox-classify"


def _mail(n: int, text: str = "Could you send the slides before Thursday?") -> IncomingMessage:
    return IncomingMessage(
        id=f"<m{n}@example.org>",
        channel_id="dana@example.com",
        channel_name="dana@example.com",
        text=f"{text} ({n})",
        sender_id="ravi@example.org",
        sender_name="Ravi",
        timestamp=1_790_000_000.0 + n,
        kind="email",
    )


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    from personalclaw import inbox_service as mod

    monkeypatch.setattr(mod, "_dashboard_state", lambda: None)
    monkeypatch.setattr(mod, "operator_name", lambda: "Dana")
    monkeypatch.setattr(
        "personalclaw.providers.entity_routes.load_inbox_settings", lambda: {}, raising=False
    )
    return InboxService(
        state=InboxState(tmp_path / "state.json"), store=InboxStore(tmp_path / "inbox.json")
    )


class Model:
    """A scripted background model: answers each call from ``answer(prompt)``, recording it."""

    def __init__(self, answer=None) -> None:
        self.calls: list[dict] = []
        self._answer = answer or self.every_message

    @staticmethod
    def numbers(prompt: str) -> list[int]:
        return [int(n) for n in re.findall(r"^(\d+)\. <untrusted_content", prompt, re.M)]

    def every_message(self, prompt: str) -> str:
        verdicts = [
            {"message": n, "classification": "needs_reply", "confidence": "high"}
            for n in self.numbers(prompt)
        ]
        return json.dumps({"verdicts": verdicts})

    async def __call__(self, prompt: str, **kw) -> str:
        self.calls.append({"prompt": prompt, **kw})
        answer = self._answer(prompt)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _use(monkeypatch, model: Model) -> Model:
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    return model


async def _drain(sorter: InboxSorter) -> None:
    while await sorter.sort_pending():
        pass


def _row(svc: InboxService, n: int):
    return svc.inbox.items[polled_item_id("mail-inbox", _mail(n))]


# ── a message arrives unsorted, and is sorted by one call that names its prompt ──


@pytest.mark.asyncio
async def test_a_new_message_arrives_unsorted_and_is_sorted_with_its_maker(svc, monkeypatch):
    model = _use(monkeypatch, Model())
    svc._ingest([_mail(1)], source=_MAIL)
    row = _row(svc, 1)
    assert (row.classification, row.confidence, row.classified_by) == ("", "", "")
    assert "classification" not in (redact_item(row.to_dict()).get("feedback_producers") or {})

    await _drain(svc.sorter)

    row = _row(svc, 1)
    assert (row.classification, row.confidence) == ("needs_reply", "high")
    assert row.classified_by == _PROMPT and row.classify_error == ""
    assert redact_item(row.to_dict())["feedback_producers"]["classification"] == {
        "producer_kind": "prompt",
        "producer_id": _PROMPT,
    }
    assert len(model.calls) == 1
    call = model.calls[0]
    assert call["use_case"] == "background" and call["output_type"] is dict
    # The message is quoted as data, its channel inside the fence with it.
    assert "Could you send the slides before Thursday? (1)" in call["prompt"]
    fenced = call["prompt"].split("1. ", 1)[1]
    assert fenced.startswith("<untrusted_content") and "Channel: dana@example.com" in fenced


@pytest.mark.asyncio
async def test_arriving_wakes_the_sorter_with_no_one_asking(svc, monkeypatch):
    _use(monkeypatch, Model())
    svc.sorter = InboxSorter(svc.inbox, settle_secs=0)
    svc.sorter.start()
    try:
        svc._ingest([_mail(1)], source=_MAIL)
        deadline = time.monotonic() + 5
        while not _row(svc, 1).classification and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        svc.sorter.stop()
    assert _row(svc, 1).classification == "needs_reply"


# ── batched: a burst is a few calls, never one per message ──


@pytest.mark.asyncio
async def test_a_burst_is_sorted_a_batch_per_call(svc, monkeypatch):
    model = _use(monkeypatch, Model())
    count = 2 * BATCH_MAX + 3
    svc._ingest([_mail(n) for n in range(count)], source=_MAIL)

    await _drain(svc.sorter)

    assert [len(Model.numbers(c["prompt"])) for c in model.calls] == [BATCH_MAX, BATCH_MAX, 3]
    assert all(_row(svc, n).classification == "needs_reply" for n in range(count))


# ── idempotent: once per message, and her verdict wins ──


@pytest.mark.asyncio
async def test_a_message_is_sorted_once(svc, monkeypatch):
    model = _use(monkeypatch, Model())
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    await _drain(svc.sorter)
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_her_verdict_given_while_the_model_reads_is_kept(svc, monkeypatch):
    item_id = polled_item_id("mail-inbox", _mail(1))

    def answer(prompt: str) -> str:
        # She sorts it herself while the call is out.
        svc.inbox.update(item_id, classification="noise", confidence="user")
        return Model().every_message(prompt)

    _use(monkeypatch, Model(answer))
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)

    row = svc.inbox.items[item_id]
    assert (row.classification, row.confidence, row.classified_by) == ("noise", "user", "")


# ── a failure says so on the row, and invents nothing ──


@pytest.mark.asyncio
async def test_an_answer_that_cannot_be_read_says_so(svc, monkeypatch):
    from personalclaw.guardrails.failure import OutputContractError

    model = _use(monkeypatch, Model(lambda prompt: OutputContractError("dict", "sure thing!")))
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    await _drain(svc.sorter)

    row = _row(svc, 1)
    assert (row.classification, row.confidence, row.classified_by) == ("", "", "")
    assert row.classify_error == UNREADABLE
    assert len(model.calls) == 1  # not sent again by itself


@pytest.mark.asyncio
async def test_a_failed_call_says_why(svc, monkeypatch):
    _use(monkeypatch, Model(lambda prompt: RuntimeError("the model is not loaded")))
    svc._ingest([_mail(1), _mail(2)], source=_MAIL)
    await _drain(svc.sorter)
    for n in (1, 2):
        row = _row(svc, n)
        assert row.classification == ""
        assert row.classify_error.startswith("The background model could not sort it.")
        assert "the model is not loaded" in row.classify_error


@pytest.mark.asyncio
async def test_an_answer_that_sorts_nothing_fails_the_call_so_the_chain_moves_on(svc, monkeypatch):
    """The call is told what a usable answer is, so a model whose answer sorts none of the
    messages is the chain's to move on from, not a set of rows to fail."""
    model = _use(monkeypatch, Model())
    svc._ingest([_mail(1), _mail(2)], source=_MAIL)
    await _drain(svc.sorter)
    check = model.calls[0]["validate"]
    assert check('{"verdicts": []}') and check("sure thing!")
    assert check('{"verdicts": [{"message": 3, "classification": "fyi"}]}')  # not one it sent
    assert check('{"verdicts": [{"message": 2, "classification": "fyi"}]}') == ""


@pytest.mark.asyncio
async def test_a_message_the_answer_left_out_says_so(svc, monkeypatch):
    def answer(prompt: str) -> str:
        return json.dumps(
            {"verdicts": [{"message": 1, "classification": "fyi", "confidence": "needs_review"}]}
        )

    _use(monkeypatch, Model(answer))
    svc._ingest([_mail(1), _mail(2)], source=_MAIL)
    await _drain(svc.sorter)
    assert (_row(svc, 1).classification, _row(svc, 1).confidence) == ("fyi", "needs_review")
    assert _row(svc, 2).classification == "" and _row(svc, 2).classify_error == LEFT_OUT


# ── it waits, sending nothing, where every background pass waits ──


@pytest.mark.asyncio
async def test_nothing_is_sent_in_incident_mode(svc, monkeypatch):
    model = _use(monkeypatch, Model())
    monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    assert model.calls == []
    assert _row(svc, 1).classification == "" and _row(svc, 1).classify_error == ""
    assert "Incident mode" in svc.health()["sorting"]["held"]


@pytest.mark.asyncio
async def test_nothing_is_sent_while_sorting_is_off(svc, monkeypatch):
    from personalclaw.config import loader

    (loader.config_dir() / "config.json").write_text(
        json.dumps({"inbox": {"sort_messages": False}})
    )
    model = _use(monkeypatch, Model())
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    assert model.calls == []
    assert _row(svc, 1).classification == ""
    assert svc.health()["sorting"] == {
        "held": "Sorting is off in Settings › Inbox, so new messages stay unsorted.",
        "waiting": 1,
    }


@pytest.mark.asyncio
async def test_a_call_the_daily_ceiling_refuses_leaves_the_messages_waiting(svc, monkeypatch):
    from personalclaw.guardrails.failure import BudgetExceededError

    refusal = BudgetExceededError("day", "dollars", 1.0, 1.0)
    _use(monkeypatch, Model(lambda prompt: refusal))
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    row = _row(svc, 1)
    assert row.classification == "" and row.classify_error == ""  # waiting, not failed
    assert svc.health()["sorting"]["held"] == refusal.sentence()


@pytest.mark.asyncio
async def test_no_background_model_leaves_the_messages_waiting(svc, monkeypatch):
    from personalclaw.providers.provider_bridge import ProviderResolutionError

    refusal = ProviderResolutionError("No model is set up for background work.")
    _use(monkeypatch, Model(lambda prompt: refusal))
    svc._ingest([_mail(1)], source=_MAIL)
    await _drain(svc.sorter)
    row = _row(svc, 1)
    assert row.classification == "" and row.classify_error == ""
    assert svc.health()["sorting"]["held"] == (
        "Sorting waits for the background model: No model is set up for background work."
    )


# ── only messages are sorted ──


@pytest.mark.asyncio
async def test_only_messages_are_sent(svc, monkeypatch):
    """A notice, a proposal or her note carries no verdict and is never read; nor is an agent's
    own post, whose text may come from a Temporary or Incognito chat."""
    from personalclaw.inbox_providers import native_source as ns

    model = _use(monkeypatch, Model())
    state = SimpleNamespace(
        _inbox_svc=svc, broadcast_ws=lambda *a, **k: None, notify=lambda *a, **k: None
    )
    emit_attention_item(
        state, source="loop", kind="needs_input", title="Pick a branch", store=svc.inbox
    )
    posted = ns.post_to_inbox(
        "Shall I merge?", kind="question", reply_target="chat:temp", state=state
    )

    await _drain(svc.sorter)

    assert model.calls == []
    assert posted is not None and posted.classification == "needs_reply"
    assert posted.confidence == "" and posted.classified_by == ""
    assert "classification" not in (redact_item(posted.to_dict()).get("feedback_producers") or {})
