"""Inbox › Generate draft reads the notes her words name, and writes nothing it cannot stand on.

Measured before this: the draft was one model call over the fenced message and her words, with no
way to read anything. Asked to "give my final abstract, 120 words max, based on my outline
(Talks/exactly-once/outline-v2.md)", it could not read the outline, nothing told her so, and the
model wrote around the gap: "I'll get you the final abstract by end of week", a promise she never
made. With nothing said at all it did the same.

What the draft does now:

* a file her instruction names is looked up where chat reads without asking (the notes the
  knowledge library's watched folders took in, then the workspace) and handed to the model as her
  note, outside the sender's fence;
* a named file that cannot be read stops the draft before the model runs, and the reason names it;
* the message being answered stays data: a file it names is never read;
* the model may answer with a question for her instead of a draft, and nothing is written then;
* a word limit she gives is kept: an over-long draft is asked for once more within it, and the
  count is reported.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from personalclaw.inbox import InboxItem, InboxState, InboxStore
from personalclaw.inbox_service import InboxService

OUTLINE = (
    "# Exactly-once is a lie: outline v2\n\n"
    "Thu 12 Nov 2026. 25 minutes + Q&A.\n\n"
    "1. The double text: one customer, two messages, one phone call\n"
    "2. What exactly-once really promises, and where it stops\n"
    "Closing line: your system is exactly-once when you can explain every crash point.\n"
)

KITCHEN = "# Kitchen\n\nTiles arrive on the 14th. Budget is tight.\n"

ACCEPTANCE = (
    "Hi Noor, your talk is accepted for the 12 Nov meetup. Could you confirm the date and send "
    "your final abstract (120 words max)? The kitchen notes are at Home/kitchen-reno.md if you "
    "want to share them too."
)

SAID = (
    "Thank them, confirm 12 Nov, and give my final abstract, 120 words max, based on my "
    'outline-v2 for the "Exactly-once is a lie" talk (Talks/exactly-once/outline-v2.md).'
)


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


def _library(tmp_path: Path, notes: dict[str, str]) -> None:
    """Her knowledge library, holding *notes* as the watched folder ``Garden`` took them in."""
    from personalclaw.knowledge import get_knowledge_store

    folder = tmp_path / "Notes" / "Garden"
    folder.mkdir(parents=True, exist_ok=True)
    store = get_knowledge_store()
    sid = store.create_source(
        name="Garden", provider="watched-dir", kind="dir", spec={"path": str(folder)}
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
    """A scripted model: answers in turn, and keeps every prompt it was given."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    async def __call__(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        return self.answers.pop(0) if self.answers else ""


def _words(n: int) -> str:
    return " ".join(["word"] * n)


#: The check of a written draft (``reply_answers.check``), finding nothing it answers for her.
CHECKED = '{"unsupported": []}'


@pytest.mark.asyncio
async def test_a_draft_that_names_a_readable_note_includes_its_content(monkeypatch, tmp_path):
    _library(
        tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE, "Home/kitchen-reno.md": KITCHEN}
    )
    item = _mail()
    svc = _svc(item)
    reply = "Thank you! 12 Nov is confirmed. Abstract: " + _words(60)
    model = _Model(reply, CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    assert out is not None and not out.unread
    assert out.item.draft == reply
    prompt, _check = model.prompts
    # Her note went to the model, after the sender's fenced text and before her own words.
    assert "Closing line: your system is exactly-once" in prompt
    mail_end = prompt.index(ACCEPTANCE[:40])
    assert mail_end < prompt.index("Closing line") < prompt.rindex(SAID)
    report = out.report()
    assert [(r["name"], r["where"]) for r in report["read"]] == [
        ("Talks/exactly-once/outline-v2.md", "library")
    ]
    assert report["related"] == []
    assert report["word_limit"] == 120 and report["words"] == len(reply.split())
    assert report["question"] == ""
    # What the draft stood on is kept with it, in words that are true after a reload.
    assert "Talks/exactly-once/outline-v2.md" in out.item.context_summary


@pytest.mark.asyncio
async def test_a_draft_that_names_a_missing_note_says_so_and_writes_nothing(monkeypatch, tmp_path):
    _library(tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE})
    item = _mail(draft="An earlier draft she kept.")
    svc = _svc(item)
    model = _Model("Thanks! I'll get you the final abstract by end of week.")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    said = SAID.replace("outline-v2.md", "outline-v3.md")
    out = await svc.draft_reply(item.id, instructions=said)

    assert out is not None
    # The model never ran, so it could promise nothing, and her earlier draft is untouched.
    assert model.prompts == []
    assert svc.inbox.items[item.id].draft == "An earlier draft she kept."
    [unread] = out.unread
    assert unread.name == "Talks/exactly-once/outline-v3.md"
    sentence = out.unread_sentence()
    assert "Talks/exactly-once/outline-v3.md" in sentence
    assert "knowledge library" in sentence and "No draft was written" in sentence


@pytest.mark.asyncio
async def test_mail_text_is_treated_as_data_and_reads_nothing(monkeypatch, tmp_path):
    """The message names a note too. Only the note SHE names is read; the model gets text and no
    tool, and the message stays inside its fence."""
    _library(
        tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE, "Home/kitchen-reno.md": KITCHEN}
    )
    item = _mail()
    svc = _svc(item)
    model = _Model("Thank you! 12 Nov works. Abstract: " + _words(40), CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    prompt, _check = model.prompts
    assert "Tiles arrive on the 14th" not in prompt
    assert [r["name"] for r in out.report()["read"]] == ["Talks/exactly-once/outline-v2.md"]
    # The sender's text is inside exactly one fence that opens before it and closes after it.
    start = prompt.index("<untrusted_content")
    assert start < prompt.index(ACCEPTANCE[:40]) < prompt.index("</untrusted_content>")
    # The draft, then its check: each on the background axis, marked as one the page waits for,
    # and nothing that could hand the model a tool.
    from personalclaw.guardrails.local_queue import Attended

    drafting, checking = model.kwargs
    assert drafting == {"use_case": "background", "attended": Attended("Drafting the reply")}
    assert set(checking) == {"use_case", "output_type", "validate", "attended"}
    assert checking["use_case"] == "background"
    assert checking["attended"] == Attended("Checking the draft")

    # With nothing said, a message naming a note still reads nothing at all.
    quiet = _Model("Thank you, I'm glad it was accepted.", CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", quiet)
    out = await svc.draft_reply(item.id)
    prompt, _check = quiet.prompts
    assert "Tiles arrive on the 14th" not in prompt and "Closing line" not in prompt
    assert out.report()["read"] == [] and out.report()["related"] == []


@pytest.mark.asyncio
async def test_with_nothing_said_the_draft_commits_her_to_nothing_and_asks_instead(
    monkeypatch, tmp_path
):
    """With empty instructions the model is told it may commit her to nothing, and that where the
    reply needs her word it asks instead. A question is shown to her and nothing is written."""
    item = _mail()
    svc = _svc(item)
    model = _Model("ASK: What should your final abstract say?")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id)

    [prompt] = model.prompts
    rules = prompt[prompt.rindex("</untrusted_content>") :]
    assert "Commit Noor to nothing" in rules
    assert "ASK:" in rules
    assert out is not None
    assert out.question == "What should your final abstract say?"
    assert out.report()["question"] == "What should your final abstract say?"
    stored = svc.inbox.items[item.id]
    assert stored.draft == "" and "ASK" not in stored.draft
    assert stored.context_summary == ""


@pytest.mark.asyncio
async def test_her_word_limit_is_kept_and_reported(monkeypatch, tmp_path):
    _library(tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE})
    item = _mail()
    svc = _svc(item)
    long, short = _words(131), _words(110)
    model = _Model(long, short, CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions=SAID)

    assert len(model.prompts) == 3
    assert "within 120 words" in model.prompts[0]
    # The second ask says how far over the first one was, and gives the limit again.
    assert "131 words" in model.prompts[1] and "120" in model.prompts[1]
    assert out.item.draft == short
    assert out.report()["words"] == 110 and out.report()["word_limit"] == 120

    # Still over after the second ask: kept as written (never cut mid-sentence), and reported.
    model = _Model(_words(140), _words(125), CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    out = await svc.draft_reply(item.id, instructions=SAID)
    assert out.report()["words"] == 125 and out.report()["word_limit"] == 120


# ── what the reply panel is answered with ──


def _post(svc: InboxService, item_id: str, body: dict):
    import json
    from unittest.mock import AsyncMock, MagicMock

    from aiohttp import web

    r = MagicMock()
    app = web.Application()
    app["state"] = MagicMock(_inbox_svc=svc)
    r.app = app
    r.match_info = {"id": item_id}
    r.json = AsyncMock(return_value=body)
    r.read = AsyncMock(return_value=json.dumps(body).encode())
    return r


@pytest.mark.asyncio
async def test_the_panel_is_told_what_was_read_and_why_nothing_was_written(monkeypatch, tmp_path):
    import json

    from personalclaw.dashboard.handlers_inbox import api_inbox_draft

    _library(tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE})
    item = _mail()
    svc = _svc(item)
    reply = "Thank you! 12 Nov is confirmed. Abstract: " + _words(50)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _Model(reply, CHECKED))

    resp = await api_inbox_draft(_post(svc, item.id, {"instructions": SAID}))
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["item"]["draft"] == reply
    assert body["drafting"]["read"][0]["name"] == "Talks/exactly-once/outline-v2.md"
    assert body["drafting"]["word_limit"] == 120

    resp = await api_inbox_draft(
        _post(svc, item.id, {"instructions": SAID.replace("v2.md", "v9.md")})
    )
    assert resp.status == 422
    err = json.loads(resp.body.decode())["error"]
    assert err["code"] == "draft_source_unread"
    assert "Talks/exactly-once/outline-v9.md" in err["message"]
    assert err["detail"]["unread"][0]["name"] == "Talks/exactly-once/outline-v9.md"
    assert svc.inbox.items[item.id].draft == reply


# ── what counts as a file she named, and as her word limit ──


@pytest.mark.parametrize(
    ("said", "names"),
    [
        (SAID, ["Talks/exactly-once/outline-v2.md"]),
        ("Use outline-v2.md and ~/Notes/bio.txt.", ["outline-v2.md", "~/Notes/bio.txt"]),
        (
            'Attach nothing, but quote "Talk notes/final abstract.md".',
            ["Talk notes/final abstract.md"],
        ),
        ("Point them at https://example.org/talks/abstract.html", []),
        ("Say they can reach me at noor@example.org", []),
        ("Tell them the meetup site is example.org", []),
        ("Thank them and confirm 12 Nov.", []),
    ],
    ids=[
        "a path",
        "two names",
        "a quoted name",
        "a web address",
        "a mail address",
        "a domain",
        "none",
    ],
)
def test_the_files_her_words_name(said, names):
    from personalclaw.reply_grounding import named_files

    assert named_files(said) == names


@pytest.mark.parametrize(
    ("said", "limit"),
    [
        (SAID, 120),
        ("Keep it under 80 words.", 80),
        ("A 150-word bio, please.", 150),
        ("No more than 60 words", 60),
        ("The bio in 50 words max and the abstract in 120 words max.", None),
        ("Thank them warmly.", None),
    ],
)
def test_her_word_limit_is_read_from_her_words(said, limit):
    from personalclaw.reply_grounding import word_limit_in

    assert word_limit_in(said) == limit


@pytest.mark.asyncio
async def test_naming_no_file_gives_the_library_s_best_matches_and_says_which(
    monkeypatch, tmp_path
):
    _library(
        tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE, "Home/kitchen-reno.md": KITCHEN}
    )
    item = _mail()
    svc = _svc(item)
    model = _Model("Thank you! Abstract: " + _words(30), CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(
        item.id, instructions="Give my abstract from the exactly-once outline."
    )

    related = [r["name"] for r in out.report()["related"]]
    assert "Talks/exactly-once/outline-v2.md" in related
    assert "Closing line" in model.prompts[0]
    assert "what your knowledge library found" in out.item.context_summary


@pytest.mark.asyncio
async def test_a_file_in_her_workspace_is_read_as_chat_reads_it(monkeypatch):
    from personalclaw.config.loader import default_workspace_dir

    workspace = Path(default_workspace_dir())
    (workspace / "drafts").mkdir(parents=True, exist_ok=True)
    (workspace / "drafts" / "bio.md").write_text("Noor builds message pipelines in Toronto.\n")
    item = _mail()
    svc = _svc(item)
    model = _Model("Thank you! Here is my bio: Noor builds message pipelines in Toronto.", CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions="Accept, and add my bio from drafts/bio.md.")

    assert [(r["name"], r["where"]) for r in out.report()["read"]] == [
        ("drafts/bio.md", "workspace")
    ]
    assert "builds message pipelines" in model.prompts[0]

    # A file outside every place chat reads is not opened, and the draft says so.
    out = await svc.draft_reply(item.id, instructions="Add my bio from /etc/bio.md.")
    assert [u.name for u in out.unread] == ["/etc/bio.md"]
    assert len(model.prompts) == 2, "the unread draft asked a model"


@pytest.mark.asyncio
async def test_a_quoted_phrase_ending_in_a_note_reads_that_note(monkeypatch, tmp_path):
    _library(tmp_path, {"Talks/exactly-once/outline-v2.md": OUTLINE})
    item = _mail()
    svc = _svc(item)
    model = _Model("Thank you! Abstract: " + _words(30), CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)

    out = await svc.draft_reply(item.id, instructions="Use 'my outline-v2.md' for the abstract.")

    assert [r["found"] for r in out.report()["read"]] == ["Talks/exactly-once/outline-v2.md"]
    assert "Closing line" in model.prompts[0]


#: The status line of a note whose text the content scan refused when Knowledge took it in.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."


@pytest.mark.parametrize(
    ("held", "reason"),
    [
        (
            {"processing_status": "failed", "file_metadata": {"refused": REFUSED}},
            "it is in your knowledge library but holds no text. " + REFUSED[:-1],
        ),
        (
            {"processing_status": "failed", "processing_error": REFUSED},
            "it is in your knowledge library but holds no text. " + REFUSED[:-1],
        ),
        ({"processing_status": "queued"}, "it is in your knowledge library but holds no text yet"),
        (
            {"processing_status": "unsearchable"},
            "it is in your knowledge library but no text was read from it",
        ),
    ],
    ids=["refused", "withheld", "waiting", "read-empty"],
)
def test_a_note_with_no_text_says_why_it_has_none(tmp_path, held, reason):
    """A note Knowledge refused will never hold text, so it is not said to hold none "yet": the
    draft says why it holds none, as its page does, whether its door refused it or the scan
    withheld the text its reader made. One still waiting to be read holds none yet; one read
    with nothing found holds none at all."""
    from personalclaw.knowledge import get_knowledge_store
    from personalclaw.reply_grounding import ground

    _library(tmp_path, {"Garden/plan.md": ""})
    store = get_knowledge_store()
    (note,) = store.source_items_at("watched-dir", "Garden/plan.md")
    store.update_item(note["id"], **held)
    store.db.commit()

    grounding = ground("Answer from my Garden/plan.md.")

    assert [(u.name, u.reason) for u in grounding.unread] == [("Garden/plan.md", reason)]
