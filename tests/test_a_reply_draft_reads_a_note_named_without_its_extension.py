"""A reply draft reads the note her words name by its file name alone, with no extension.

Told "Accept, and give my abstract from talk-outline", Inbox › Generate draft did not read
`talk-outline.md` and the model asked where the outline was. A word was a file name only with an
extension (`named_files`), so "talk-outline" read nothing, and with no file named the draft got
the library's best matches for her words instead, which need not hold it.

A joined word ("talk-outline", "q3_plan", "Talks/talk-outline") is now also looked for as a note's
file name without its extension, where named files are looked for. One match is read as a named
note. More than one is unread, naming them. None is a word, not a name, and reads nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.reply_grounding import LIBRARY, WORKSPACE, ground

OUTLINE = "# Talk outline\n\n1. The double text\nClosing line: explain every crash point.\n"


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


def test_a_note_named_without_its_extension_is_read(tmp_path):
    _library(tmp_path, {"Talks/talk-outline.md": OUTLINE})

    grounding = ground("Accept, and give my abstract from talk-outline.")

    assert [(n.name, n.found, n.where) for n in grounding.named] == [
        ("talk-outline", "Talks/talk-outline.md", LIBRARY)
    ]
    assert "Closing line" in grounding.named[0].text
    assert grounding.unread == [] and grounding.related == []


def test_its_folder_may_be_written_too(tmp_path):
    _library(tmp_path, {"Talks/talk-outline.md": OUTLINE, "Old/talk-outline.md": "old"})

    grounding = ground("Use Talks/talk-outline for the abstract.")

    assert [n.found for n in grounding.named] == ["Talks/talk-outline.md"]


def test_one_in_her_workspace_is_read_as_chat_reads_it():
    from personalclaw.config.loader import default_workspace_dir

    workspace = Path(default_workspace_dir())
    (workspace / "drafts").mkdir(parents=True, exist_ok=True)
    (workspace / "drafts" / "speaker_bio.txt").write_text("Noor builds message pipelines.\n")

    grounding = ground("Add my bio from drafts/speaker_bio.")

    assert [(n.name, n.found, n.where) for n in grounding.named] == [
        ("drafts/speaker_bio", "drafts/speaker_bio.txt", WORKSPACE)
    ]


def test_two_notes_by_that_name_are_unread_naming_them(tmp_path):
    _library(tmp_path, {"Talks/talk-outline.md": OUTLINE, "Old/talk-outline.md": "old"})

    grounding = ground("Give my abstract from talk-outline.")

    assert grounding.named == []
    ((name, reason),) = [(u.name, u.reason) for u in grounding.unread]
    assert name == "talk-outline"
    assert "Talks/talk-outline.md" in reason and "Old/talk-outline.md" in reason, reason


@pytest.mark.parametrize(
    "said",
    ["Send a follow-up by end-of-week.", "Confirm 2026-11-12 and thank them.", "Use my outline."],
)
def test_a_joined_word_that_names_no_note_reads_nothing(tmp_path, said):
    _library(tmp_path, {"Talks/talk-outline.md": OUTLINE})

    grounding = ground(said)

    assert grounding.named == [] and grounding.unread == []


def test_a_name_with_its_extension_is_read_once(tmp_path):
    _library(tmp_path, {"Talks/talk-outline.md": OUTLINE})

    grounding = ground("Base it on my talk-outline (Talks/talk-outline.md).")

    assert [n.found for n in grounding.named] == ["Talks/talk-outline.md"]
    assert grounding.unread == []


def test_a_name_read_without_its_extension_counts_toward_the_bound(tmp_path):
    """A draft reads at most `MAX_NAMED` of the files she names, those named without their
    extension among them."""
    from personalclaw.reply_grounding import MAX_NAMED

    files = {f"n{i}.md": f"note {i}" for i in range(MAX_NAMED)}
    _library(tmp_path, {**files, "Talks/talk-outline.md": OUTLINE})
    said = "Use " + ", ".join(files) + " and talk-outline."

    grounding = ground(said)

    assert len(grounding.named) == MAX_NAMED, [n.found for n in grounding.named]
    assert [u.name for u in grounding.unread] == ["talk-outline"], grounding.unread
    assert f"at most {MAX_NAMED}" in grounding.unread[0].reason


@pytest.mark.parametrize("word", ["__init__", "talk_", "_draft"])
def test_underscores_alone_join_nothing(tmp_path, word):
    _library(tmp_path, {f"{word}.md": "a note"})

    assert ground(f"Use {word} for it.").named == []
