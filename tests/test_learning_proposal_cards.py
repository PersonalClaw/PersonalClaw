"""A learning proposal's card leads with what it asks, in the words its producer wrote.

The Inbox row, the bell and a notification used to lead with the kind's label ("New template
proposed: weekly-status-re…"), so two proposals of one kind read alike, and Needs you's card had
nothing else to show. ``learning.cards.card_for`` reads each producer's proposal and returns its
headline, its one-line detail, the name of the thing it is about, what its Accept does, and its
kind's label as the tag. The row carries it as ``refs.card`` and leads with its headline.

Every example is pinned in ``web/src/test/fixtures/needsYouKinds.json``, which the web's card tests
read too. So the server and the cards say one thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.learning import cards, proposals, review

FIXTURE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "test" / "fixtures" / "needsYouKinds.json"
)
EXAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))["learning"]


def _filed(example: dict) -> proposals.Proposal:
    filed = dict(example["filed"])
    filed.setdefault("id", "lp-" + example["variant"].replace(".", "-"))
    return proposals.Proposal(**filed)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(proposals, "_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(proposals, "_decisions_path", lambda: tmp_path / "decisions.json")
    (tmp_path / "proposals").mkdir()


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["variant"] for e in EXAMPLES])
def test_each_producers_card_reads_as_the_fixture_pins_it(example):
    assert cards.card_for(_filed(example)) == example["card"]


def test_the_fixture_covers_every_producer_variant():
    pinned = {e["card"]["variant"] for e in EXAMPLES}
    built = set(cards._BUILDERS) - {"project_file", "project_skill"}
    assert built <= pinned, sorted(built - pinned)
    assert {"retirement", "tier"} <= pinned, "the defaults are pinned too"


def test_no_card_line_carries_an_em_dash():
    for example in EXAMPLES:
        card = example["card"]
        for text in (card["headline"], card["detail"], card["accept"]["sentence"]):
            assert "—" not in text, (example["variant"], text)


def test_a_credential_in_a_proposal_never_reaches_its_card():
    [masked] = [e for e in EXAMPLES if e["variant"] == "masked"]
    card = cards.card_for(_filed(masked))
    assert "fake-anthropic-test" not in json.dumps(card)
    assert "[REDACTED: " in card["headline"]


def test_a_cut_line_keeps_its_mask_whole():
    prop = proposals.Proposal(
        id="lp-cut",
        kind="knowledge_draft",
        title="Rotate the deploy key "
        + "word " * 18
        + "API_KEY=fake-anthropic-test-"
        + "B" * 95
        + " soon",
        body="",
    )
    headline = cards.card_for(prop)["headline"]
    assert len(headline) <= cards.HEADLINE_CHARS
    opened = headline.rfind("[REDACTED")
    assert opened == -1 or "]" in headline[opened:], headline


def test_a_body_it_cannot_read_falls_back_to_the_masked_title():
    prop = proposals.Proposal(
        id="lp-odd",
        kind="lesson_batch",
        title="Lesson from an odd row",
        body="",
        tags=["lesson_conflict"],
        change_manifest={"targeted_fix": "not a list"},
    )
    card = cards.card_for(prop)
    assert card["headline"] == "Lesson from an odd row"
    assert card["label"] == proposals._KIND_LABELS["lesson_batch"]


def test_every_kind_has_its_label_as_the_cards_tag():
    for kind in proposals.Kind:
        prop = proposals.Proposal(id=f"lp-{kind.value}", kind=kind.value, title="A title", body="")
        assert cards.card_for(prop)["label"] == proposals._KIND_LABELS[kind.value]


def test_a_project_file_over_one_that_is_there_asks_once_more(monkeypatch):
    from personalclaw.learning import project_context_review

    monkeypatch.setattr(project_context_review, "project_name", lambda _pid: "Home Lab")
    monkeypatch.setattr(project_context_review, "context_file_exists", lambda _t: True)
    prop = proposals.Proposal(
        id="lp-file",
        kind="project_file",
        title="The VLAN IDs changed when you moved the NAS",
        body="# Network\n\nVLAN 10 servers",
        target="proj-1\x1fnetwork.md",
        source_cadence="project_context_review",
        tags=["project_context", "project_file"],
    )
    card = cards.card_for(prop)
    assert card["headline"] == "context/network.md: updated"
    assert card["name"] == "Home Lab"
    assert card["accept"] == {
        "effect": "installs",
        "verb": "Write file",
        "sentence": "Writes context/network.md in Home Lab, replacing what is there",
        "confirm": True,
    }


def test_a_conflicts_reject_keeps_yours():
    [conflict] = [e for e in EXAMPLES if e["variant"] == "lesson.conflict"]
    assert review.reject_for(_filed(conflict)) == {
        "verb": "Keep mine",
        "sentence": "Yours stays; the learned one isn't saved.",
    }


# ── the Inbox row leads with it ─────────────────────────────────────────────────────────────────


def _row(pid: str):
    from personalclaw.inbox import InboxStore

    inbox = InboxStore()
    inbox.load()
    [row] = [i for i in inbox.items.values() if i.refs.get("learning_proposal") == pid]
    return row


def test_the_inbox_row_leads_with_the_headline_and_tags_it_with_the_label():
    [named] = [e for e in EXAMPLES if e["variant"] == "template.named"]
    filed = {k: v for k, v in named["filed"].items() if k not in ("id", "kind")}
    _verdict, prop = proposals.enqueue(kind="template", provenance="inferred", **filed)
    row = _row(prop.id)
    card = named["card"]
    assert row.message.split("\n\n")[0] == card["headline"]
    assert row.message.split("\n\n")[1] == f"{card['label']} · {card['detail']}"
    assert row.refs["card"] == card


def test_the_rows_preview_stops_at_2000_on_a_paragraph():
    body = "\n\n".join(["Paragraph " + "x" * 300] * 10)
    _verdict, prop = proposals.enqueue(
        kind="knowledge_draft", title="A long draft", body=body, provenance="inferred"
    )
    preview = _row(prop.id).refs["proposal"]["preview"]
    assert len(preview) <= proposals.PREVIEW_CHARS + 3
    assert preview.endswith("\n\n…")
    assert preview[: -len("\n\n…")].endswith("x" * 10)
