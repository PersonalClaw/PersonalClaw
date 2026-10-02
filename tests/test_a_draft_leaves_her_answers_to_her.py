"""A drafted reply answers for her only what she said or her notes say; the rest is left for her.

Measured before this: asked for "my final abstract, 120 words max, from outline-v2.md", a draft gave
the abstract from the outline and then answered the rest of the organisers' mail for her: "I'll send
a speaker photo and slides by the deadlines. I'm in for the dinner — no dietary restrictions."
Nothing she said and none of her notes say either. Its rules said to commit her to nothing she had
not said, and the model wrote around them; nothing checked what it wrote.

What a draft does now:

* every draft is written under rules that leave each question no source answers to her, marked
  ``[your answer: …]`` where the answer goes;
* the draft is checked: a second call reads the reply as a checker and names each part that answers
  for her with what neither her words nor her notes say, and each such part becomes a placeholder,
  unless it is plainly drawn from her own words or notes;
* a check that cannot be made is said, and the draft is kept as written;
* the panel lists what is left for her, and a reply that still holds a placeholder is not sent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.inbox_service import InboxService

OUTLINE = (
    "# Exactly-once is a lie: outline v2\n\n"
    "Thu 12 Nov 2026. 25 minutes + Q&A.\n\n"
    "1. The double text: one customer, two messages, one phone call\n"
    "2. What exactly-once really promises, and where it stops\n"
    "3. The test: can you explain every crash point?\n"
)

ACCEPTANCE = (
    "Great news: your talk is accepted for Thursday 12 November.\n"
    "- Please send a final abstract (max 120 words) and a speaker photo by Friday 2 October\n"
    "- Slides by Monday 9 November if you want us to load them on the venue laptop\n"
    "- There is a speakers' dinner after, let us know about dietary needs\n"
)

SAID = "Draft a reply with my final abstract, 120 words max, from outline-v2.md."

ABSTRACT = (
    "Here's my final abstract: what exactly-once really promises, and where it stops, and the "
    "test of a system: can you explain every crash point?"
)
PROMISE = "I'll send a speaker photo and slides by the deadlines."
DINNER = "I'm in for the dinner — no dietary restrictions."
#: A draft that gives the abstract from her outline, then answers the rest of the mail for her.
INVENTED = f"Thank you, I'm thrilled! {ABSTRACT} {PROMISE} {DINNER}"

PHOTO = "speaker photo and slides — when you'll send them"
DIETARY = "dinner — yes or no, dietary needs"


@pytest.fixture(autouse=True)
def _isolate_inbox_files(monkeypatch, tmp_path):
    """The inbox store saves on every draft; keep it in this test's own folder."""
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)


def _mail(**kw) -> InboxItem:
    base = dict(
        id="mail_1790000000.1",
        channel="mail",
        channel_name="Mail",
        thread_ts=None,
        message=ACCEPTANCE,
        sender_id="talks@events.example.org",
        sender_name="Talks team",
        created_at=time.time(),
        source="mail",
        can_reply=True,
    )
    base.update(kw)
    return InboxItem(**base)


def _svc(item: InboxItem) -> InboxService:
    store = InboxStore()
    store.items[item.id] = item
    return InboxService(state=InboxState(), store=store, user_name="Noor")


def _library(notes: dict[str, str], tmp_path: Path) -> None:
    """Her knowledge library, holding *notes* as the watched folder ``Talks`` took them in."""
    from personalclaw.knowledge import get_knowledge_store

    folder = tmp_path / "Notes" / "Talks"
    folder.mkdir(parents=True, exist_ok=True)
    store = get_knowledge_store()
    sid = store.create_source(
        name="Talks", provider="watched-dir", kind="dir", spec={"path": str(folder)}
    )
    for rel, text in notes.items():
        store.create_typed_item(
            item_type="note",
            title=Path(rel).name,
            content=text,
            provider="watched-dir",
            source_id=sid,
            guid=rel,
        )


class _Model:
    """A scripted model: answers in turn, keeping every prompt and what it was asked with. An
    answer that is an exception is raised instead."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    async def __call__(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, BaseException):
            raise answer
        return str(answer)


def _named(*parts: tuple[int, str]) -> str:
    """The checker's answer naming *parts*: (part number, what she needs to answer there)."""
    return json.dumps({"unsupported": [{"part": n, "needs": needs} for n, needs in parts]})


NONE_NAMED = _named()


@pytest.mark.asyncio
async def test_an_answer_she_never_gave_is_not_presented_as_hers(monkeypatch, tmp_path):
    """The model drafts the abstract from her outline and answers the rest of the mail for her.
    The check names the two answers nobody gave, and each is left for her instead."""
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    model = _Model(INVENTED, _named((3, PHOTO), (4, DIETARY)))
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    assert out is not None and out.wrote
    stored = svc.inbox.items[item.id].draft
    assert "no dietary restrictions" not in stored and "I'm in for the dinner" not in stored
    assert "by the deadlines" not in stored
    # What her outline gave, and the thanks, stay as written.
    assert stored.startswith("Thank you, I'm thrilled!") and ABSTRACT in stored
    assert stored == (
        f"Thank you, I'm thrilled! {ABSTRACT} [your answer: {PHOTO}] [your answer: {DIETARY}]"
    )
    from personalclaw.reply_answers import open_answers

    assert open_answers(stored) == [PHOTO, DIETARY]
    report = out.report()
    assert report["answered_for_you"] == 2 and report["unchecked"] is False
    assert report["words"] == len(stored.split())


@pytest.mark.asyncio
async def test_the_panel_is_never_handed_the_invented_answer_and_it_cannot_be_sent(
    monkeypatch, tmp_path
):
    """Through the routes the panel calls: the draft it is answered with leaves the dinner to her,
    and Send refuses a reply that still holds a place for her answer."""
    from personalclaw.dashboard.handlers_inbox import api_inbox_draft, api_inbox_send

    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion",
        _Model(INVENTED, _named((3, PHOTO), (4, DIETARY))),
    )

    resp = await api_inbox_draft(_draft_request(svc, item.id, {"instructions": SAID}))
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert "no dietary restrictions" not in body["item"]["draft"]
    assert body["drafting"]["answered_for_you"] == 2

    sent: list[str] = []
    resp = await api_inbox_send(_send_request(svc, item.id, body["item"]["draft"], sent))
    assert resp.status == 422
    err = json.loads(resp.body.decode())["error"]
    assert err["code"] == "reply_has_open_answer"
    assert PHOTO in err["message"] and DIETARY in err["message"]
    assert sent == [], "a reply holding a place for her answer reached the sender"
    assert svc.inbox.items[item.id].status != ItemStatus.HANDLED.value

    # Once she has written her answers in, it goes.
    hers = (
        body["item"]["draft"]
        .replace(f"[your answer: {PHOTO}]", "Photo to follow.")
        .replace(f"[your answer: {DIETARY}]", "I'll confirm the dinner by next week.")
    )
    resp = await api_inbox_send(_send_request(svc, item.id, hers, sent))
    assert resp.status == 200, resp.body
    assert sent == [hers]


@pytest.mark.asyncio
async def test_the_check_never_takes_out_what_her_notes_say(monkeypatch, tmp_path):
    """A checker that names the abstract is wrong: its words are her outline's. Only the answer
    nobody gave is left for her."""
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    model = _Model(INVENTED, _named((2, "final abstract"), (4, DIETARY)))
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    stored = svc.inbox.items[item.id].draft
    assert ABSTRACT in stored and PROMISE in stored
    assert DINNER not in stored and f"[your answer: {DIETARY}]" in stored
    assert out.report()["answered_for_you"] == 1


@pytest.mark.asyncio
async def test_her_own_words_are_never_taken_out(monkeypatch, tmp_path):
    """What she said the reply should say is hers to say: a checker naming it changes nothing."""
    item = _mail()
    svc = _svc(item)
    said = (
        "Thank them, and say I'll send the speaker photo separately and confirm the dinner later."
    )
    reply = "Thank you! I'll send the speaker photo separately and confirm the dinner later."
    model = _Model(reply, _named((2, "speaker photo")))
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=said)

    assert svc.inbox.items[item.id].draft == reply
    assert out.report()["answered_for_you"] == 0


@pytest.mark.asyncio
async def test_what_the_model_leaves_open_is_kept_and_listed(monkeypatch, tmp_path):
    """A draft that follows its rules leaves the questions to her itself; the check finds nothing
    and the placeholders are what the panel lists."""
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    reply = (
        f"Thank you! {ABSTRACT}\n\n[your answer: speaker photo — when]\n"
        "[Your Answer:   slides for the venue laptop]\n"
        f"[your answer: {DIETARY}]"
    )
    model = _Model(reply, NONE_NAMED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    from personalclaw.reply_answers import open_answers

    assert svc.inbox.items[item.id].draft == reply
    assert open_answers(reply) == [
        "speaker photo — when",
        "slides for the venue laptop",
        DIETARY,
    ]
    assert out.report()["answered_for_you"] == 0 and out.report()["unchecked"] is False


@pytest.mark.asyncio
async def test_every_draft_is_told_to_leave_her_answers_to_her_and_is_then_checked(
    monkeypatch, tmp_path
):
    """The rules every draft is written under, and the check's own prompt: the message, her
    words, her notes and the reply each quoted as data, the reply in numbered parts."""
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    model = _Model(INVENTED, NONE_NAMED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    await svc.draft_reply(item.id, instructions=SAID)

    draft_prompt, check_prompt = model.prompts
    rules = draft_prompt[draft_prompt.index("Rules for this draft") :]
    assert "Commit Noor to nothing" in rules
    assert "[your answer: dinner — yes or no, dietary needs]" in rules
    assert "ASK:" in rules

    # The checker is given what the draft was given, as data, and the reply part by part.
    assert ACCEPTANCE.splitlines()[1] in check_prompt
    assert "What exactly-once really promises" in check_prompt
    assert SAID in check_prompt
    assert f"[4] {DINNER}" in check_prompt and "[1] Thank you, I'm thrilled!" in check_prompt
    fenced = check_prompt.count("<untrusted_content")
    assert fenced >= 4, "the message, her note, her words and the reply are each fenced"
    assert '"unsupported"' in check_prompt
    # Asked on the background chain for a JSON object, as a step the page waits on.
    from personalclaw.guardrails.local_queue import Attended

    kwargs = model.kwargs[1]
    assert kwargs["use_case"] == "background" and kwargs["output_type"] is dict
    assert kwargs["attended"] == Attended("Checking the draft")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [RuntimeError("the background model is unavailable"), "It reads fine to me."],
    ids=["the call fails", "an answer that says nothing usable"],
)
async def test_a_check_that_cannot_be_made_says_so_and_keeps_the_draft(
    monkeypatch, tmp_path, answer
):
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    model = _Model(INVENTED, answer)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    assert out is not None and out.wrote
    assert svc.inbox.items[item.id].draft == INVENTED
    assert out.report()["unchecked"] is True and out.report()["answered_for_you"] == 0


@pytest.mark.asyncio
async def test_a_question_back_to_her_or_a_skip_is_not_checked(monkeypatch, tmp_path):
    item = _mail()
    svc = _svc(item)
    model = _Model("ASK: What should your final abstract say?")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    out = await svc.draft_reply(item.id)
    assert out.question and len(model.prompts) == 1

    model = _Model("SKIP")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    out = await svc.draft_reply(item.id)
    assert out.skipped and len(model.prompts) == 1
    assert out.report()["unchecked"] is False


@pytest.mark.asyncio
async def test_the_shortened_draft_is_the_one_checked(monkeypatch, tmp_path):
    _library({"Talks/outline-v2.md": OUTLINE}, tmp_path)
    item = _mail()
    svc = _svc(item)
    long = " ".join(["word"] * 130) + f" {DINNER}"
    short = f"Thank you! {DINNER}"
    model = _Model(long, short, _named((2, DIETARY)))
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    assert len(model.prompts) == 3
    assert f"[2] {DINNER}" in model.prompts[2] and "word word" not in model.prompts[2]
    assert svc.inbox.items[item.id].draft == f"Thank you! [your answer: {DIETARY}]"
    assert out.report()["words"] == len(out.item.draft.split())


@pytest.mark.parametrize(
    ("text", "labels"),
    [
        ("Thanks! [your answer: dinner — yes or no]", ["dinner — yes or no"]),
        ("[YOUR ANSWER:slides]\n[ your answer :  photo ]", ["slides", "photo"]),
        ("See [the agenda](https://example.org/agenda) [1].", []),
        ("I'll be there [your answer: arrival time", ["arrival time"]),
        ("No placeholders at all.", []),
    ],
    ids=["one", "spacing and case", "a link and a citation", "unclosed", "none"],
)
def test_what_a_reply_leaves_for_her(text, labels):
    from personalclaw.reply_answers import open_answers

    assert open_answers(text) == labels


# ── the routes ──


def _draft_request(svc: InboxService, item_id: str, body: dict):
    from unittest.mock import AsyncMock

    r = MagicMock()
    app = web.Application()
    app["state"] = MagicMock(_inbox_svc=svc)
    r.app = app
    r.match_info = {"id": item_id}
    r.json = AsyncMock(return_value=body)
    r.read = AsyncMock(return_value=json.dumps(body).encode())
    return r


def _send_request(svc: InboxService, item_id: str, text: str, sent: list[str]):
    """``POST /api/inbox/send`` for *item_id*, its source a fake that records what it sends."""
    from personalclaw.inbox_providers.base import MessageSourceProvider
    from personalclaw.inbox_providers.registry import register_source

    class _Mail(MessageSourceProvider):
        display_name = "Mail"

        @property
        def source_name(self) -> str:
            return "mail"

        async def poll(self, watched_channels, checkpoints, user_id):
            return [], {}

        async def send_reply(self, channel_id, text, thread_ts=None):
            sent.append(text)
            return True

        async def add_reaction(self, channel_id, ts, emoji):
            return False

        async def get_channel_history(self, channel_id, oldest, limit=200):
            return []

        async def resolve_user_name(self, user_id):
            return user_id

    register_source(_Mail())
    st = MagicMock()
    st._inbox_svc = svc
    st._inbox_store = svc.inbox
    st._inbox_state = svc.state
    st.broadcast_ws = lambda ev, payload: None
    app = web.Application()
    app["state"] = st
    req = make_mocked_request("POST", "/api/inbox/send", app=app)

    async def body():
        return {"id": item_id, "text": text}

    req.json = body
    return req


@pytest.fixture(autouse=True)
def _clean_sources():
    from personalclaw.inbox_providers import registry as app_sources

    app_sources._sources.clear()
    yield
    app_sources._sources.clear()
