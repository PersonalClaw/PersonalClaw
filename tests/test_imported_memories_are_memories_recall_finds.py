"""The notes another tool was remembering come over as memories: whole, recalled, embedded.

They used to land as one line each in ``preferences.md`` — the first 220 characters of the note —
and nowhere in the memory store recall searches, so recall brought none of them back and the
embedding re-index had nothing to embed ("0 memory embeddings"). Each note is now kept as memories
of your own in the store: all of its text, split where it breaks when it is longer than one memory
holds, found by recall, and embedded by the re-index. The note's file stays beside them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.onboarding_import import ImportCategory, WriteOutcome, run_import, scan_source

#: The end of a note, past the 220 characters the old record kept.
_TAIL = "and the rotation handoff happens on Mondays at ten, local time."


#: A note longer than one memory holds: twelve paragraphs, about 3,500 characters.
_LONG_NOTE = (
    "\n\n".join(
        f"Paragraph {n}: the release checklist step {n} is written down here in full. " * 4
        for n in range(1, 13)
    )
    .replace(" \n\n", "\n\n")
    .strip()
)


def _note(n: int) -> str:
    lead = "The on-call rotation for the widgets service pairs a primary with a secondary. " * 3
    return f"---\nname: On-call rotation {n}\n---\n{lead}{_TAIL}\n"


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "pclaw-home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    claude = tmp_path / "foreign" / ".claude"
    memory = claude / "projects" / "-srv-widgets" / "memory"
    memory.mkdir(parents=True)
    (memory / "oncall.md").write_text(_note(1), encoding="utf-8")
    (memory / "release-checklist.md").write_text(f"{_LONG_NOTE}\n", encoding="utf-8")
    return {"home": home, "claude": claude}


def _import_memories(claude: Path):
    results = [scan_source("claude_code", claude)]
    picks = [
        item.fingerprint for item in results[0].items if item.category is ImportCategory.MEMORIES
    ]
    assert len(picks) == 2
    return run_import(results, fingerprints=picks)


def _store():
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore()
    store.init()
    return store


def _memories(store) -> list[str]:
    return [
        row["text"]
        for row in store.db.execute(
            "SELECT text FROM episodic_memories WHERE is_deleted = 0 ORDER BY rowid"
        ).fetchall()
    ]


def test_an_imported_note_is_a_memory_with_all_of_its_text(setup: dict[str, Path]) -> None:
    report = _import_memories(setup["claude"])
    assert {r.outcome for r in report.results} == {WriteOutcome.IMPORTED}

    store = _store()
    try:
        texts = _memories(store)
        oncall = [
            t for t in texts if t.startswith("On-call rotation 1 — from Claude Code's memory")
        ]
        assert len(oncall) == 1
        assert _TAIL in oncall[0]  # the whole note, not its first 220 characters
        assert "name: On-call rotation" not in oncall[0]  # the tool's bookkeeping stays out
        # Recall finds it — by keyword here, since no embedding model is bound.
        from personalclaw.memory_service import MemoryService

        hits = MemoryService.over_vector_store(store).recall_with_provenance(
            query_text="when is the on-call rotation handoff", limit=5
        )
        assert any(_TAIL in hit["text"] for hit in hits)
    finally:
        store.close()
    # No preference line stands in for it.
    prefs = setup["home"] / "workspace" / "memory" / "preferences.md"
    assert not prefs.exists() or "Imported from" not in prefs.read_text(encoding="utf-8")


def test_a_note_longer_than_one_memory_is_kept_as_several_and_loses_nothing(
    setup: dict[str, Path],
) -> None:
    _import_memories(setup["claude"])
    store = _store()
    try:
        parts = [t for t in _memories(store) if t.startswith("release-checklist (")]
    finally:
        store.close()
    # Every part is kept: the store dedupes memories by their opening, so each is numbered there.
    assert len(parts) > 1

    from personalclaw.vector_memory import EPISODIC_TEXT_MAX

    assert [p.split(" — ", 1)[0] for p in parts] == [
        f"release-checklist ({n} of {len(parts)})" for n in range(1, len(parts) + 1)
    ]
    assert all(" — from Claude Code's memory (Project · -srv-widgets)\n" in p for p in parts)
    assert all(len(p) <= EPISODIC_TEXT_MAX for p in parts)
    bodies = [p.split("\n", 1)[1] for p in parts]
    assert "\n\n".join(bodies) == _LONG_NOTE


def test_the_embedding_reindex_embeds_every_imported_memory(setup: dict[str, Path]) -> None:
    _import_memories(setup["claude"])
    store = _store()
    try:
        count = len(_memories(store))
        assert count >= 3
        store.embed_fn = lambda text: [float(len(text) % 7 + 1), 1.0, 0.5, 0.25]
        assert store.count_to_reembed() == count
        assert store.reembed_stale()["reembedded"] == count
    finally:
        store.close()


def test_a_store_that_refuses_the_memories_rejects_the_note_and_writes_nothing(
    setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.onboarding_import import writers
    from personalclaw.sqlite_compat import sqlite3

    def _refuse(_item):
        raise sqlite3.OperationalError("database is locked")

    remember = writers._remember
    monkeypatch.setattr(writers, "_remember", _refuse)
    report = _import_memories(setup["claude"])

    assert {r.outcome for r in report.results} == {WriteOutcome.REJECTED}
    assert all("your memory store could not take it" in r.detail for r in report.results)
    # Nothing that reads as imported: no file, no ledger entry — so importing again tries again.
    assert not (setup["home"] / "workspace" / "memory" / "imported").exists()
    assert writers.imported_items(ImportCategory.MEMORIES) == []

    monkeypatch.setattr(writers, "_remember", remember)
    again = _import_memories(setup["claude"])
    assert {r.outcome for r in again.results} == {WriteOutcome.IMPORTED}
