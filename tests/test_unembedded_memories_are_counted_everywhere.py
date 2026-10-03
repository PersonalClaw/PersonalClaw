"""A memory no model embedded is counted wherever memory says what search reads by keyword.

#3762 made semantic search read such a memory by keyword beside its vector results, and
``GET /api/memory/stats`` counted it (``unembedded``). Nothing else did. With a model bound and
such memories in the store, the recall disclosure said "Ranked by semantic similarity" and nothing
about them, the Memory page's Embedded stat named the provider, and the Doctor's memory row said
"memory.db healthy". Search read those memories by keyword the whole time.

Each surface now reads one count, ``vector_memory.embedding_coverage``, and says it in one sentence,
``memory_ranking.keyword_read_note``. Driven with the recording embedding provider of
``test_an_embedding_rebind_reaches_every_store``: nothing bound, then a model bound without the
re-index a binding through the PUT starts.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import types

import pytest

from tests.test_an_embedding_rebind_reaches_every_store import (  # noqa: F401 (the fixture)
    KESTREL,
    OSPREY,
    A,
    C,
    _bind,
    _main_service,
    _main_state,
    recorded,
)

pytestmark = pytest.mark.usefixtures("recorded")

DAWN = "An osprey dives at dawn"

#: The sentence for one memory no model embedded.
ONE_UNEMBEDDED = (
    "1 memory not embedded yet is read by keyword until the re-index in Settings → Models "
    "embeds it."
)


def _home():
    from personalclaw.config.loader import config_dir

    return config_dir()


def _one_unembedded(state) -> None:
    """One memory written while nothing was bound, then one embedded by the model bound after."""
    _main_service(state).write_episodic(OSPREY, source="user_explicit")  # nothing bound
    _bind(A)
    _main_service(state).write_episodic(KESTREL, source="user_explicit")


def _stats_route(state) -> dict:
    """``GET /api/memory/stats`` as the Memory page reads it."""
    from personalclaw.dashboard.handlers.memory import api_memory_stats

    request = types.SimpleNamespace(app={"state": state}, query={})  # the memory every chat shares
    return json.loads(asyncio.run(api_memory_stats(request)).text)


def _doctor_row():
    from personalclaw.resilience import doctor

    return asyncio.run(doctor._probe_memory(doctor.DoctorContext(home=_home())))


def test_the_recall_disclosure_counts_a_memory_no_model_embedded():
    """🔴 Red on main: "Ranked by semantic similarity …", not degraded, and not a word about the
    memory search read by keyword."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    state = _main_state(_home())
    _one_unembedded(state)
    ranking = _ranking_payload(_main_service(state))

    assert (ranking["mode"], ranking["degraded"], ranking["unembedded"]) == ("semantic", True, 1)
    assert ranking["summary"].startswith("Ranked by semantic similarity over embeddings")
    assert ranking["summary"].endswith(" " + ONE_UNEMBEDDED)


def test_with_no_memory_embedded_yet_the_disclosure_says_the_recall_was_by_keyword():
    """🔴 Red on main: "semantic", while no stored memory held a vector the query could be compared
    with, so the whole recall was by keyword."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    state = _main_state(_home())
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    _main_service(state).write_episodic(KESTREL, source="user_explicit")
    _bind(A)
    ranking = _ranking_payload(_main_service(state))

    assert (ranking["mode"], ranking["degraded"]) == ("keyword", True)
    assert ranking["summary"].startswith("Keyword-ranked only: no memory is embedded yet")
    assert ranking["summary"].endswith(" The re-index in Settings → Models embeds the 2 memories.")


def test_another_models_vectors_and_the_memories_with_none_are_one_count():
    """Both are read by keyword until the re-index embeds them, so they are one number, with the
    breakdown in the sentence."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    state = _main_state(_home())
    _one_unembedded(state)  # OSPREY: no vector; KESTREL: A's
    _main_service(state).write_episodic(DAWN, source="user_explicit")  # A's
    _bind(C)  # A's two are another model's now
    _main_service(state).write_episodic("A heron wades in the shallows", source="user_explicit")

    stats = _stats_route(state)
    ranking = _ranking_payload(_main_service(state))

    assert (stats["embedded_count"], stats["embedded_stale"], stats["unembedded"]) == (1, 2, 1)
    assert stats["read_by_keyword"] == 3
    note = (
        "3 memories not embedded by the model bound now (2 by another embedding model, 1 not at "
        "all) are read by keyword until the re-index in Settings → Models embeds them."
    )
    assert stats["read_by_keyword_note"] == note
    assert ranking["summary"].endswith(" " + note)


def test_the_memory_page_and_the_doctor_say_the_disclosures_count():
    """🔴 Red on main: the stats carried the count and no sentence, and the Doctor's row said
    "memory.db healthy" about a store search read partly by keyword."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload
    from personalclaw.resilience.doctor import MEMORY_INDEX_FIX

    state = _main_state(_home())
    _one_unembedded(state)
    state.context_builder.memory.vector_store.save_faiss_index()  # what the Doctor reads offline

    stats = _stats_route(state)
    row = _doctor_row()

    assert (stats["read_by_keyword"], stats["read_by_keyword_note"]) == (1, ONE_UNEMBEDDED)
    assert _ranking_payload(_main_service(state))["summary"].endswith(stats["read_by_keyword_note"])
    assert (row.ok, row.detail, row.evidence["unembedded"]) == (False, ONE_UNEMBEDDED, 1)
    assert row.fix_id == MEMORY_INDEX_FIX, "the Fix embeds them with the model bound now"


def test_the_doctors_fix_embeds_them_and_the_row_is_healthy_again():
    from personalclaw.resilience import fixes
    from personalclaw.resilience.doctor import MEMORY_INDEX_FIX

    state = _main_state(_home())
    _one_unembedded(state)
    store = state.context_builder.memory.vector_store
    store.serve_recall()

    preview = fixes.get_fix(MEMORY_INDEX_FIX).dry_preview()
    applied = fixes.apply_fix(MEMORY_INDEX_FIX)

    assert preview.startswith("Would first embed the 1 memory the model bound now has not embedded")
    assert applied["ok"] and applied["result"].startswith("Embedded 1 memory with"), applied
    assert _doctor_row().ok is True
    assert _stats_route(state)["read_by_keyword"] == 0


def test_without_faiss_the_doctor_still_counts_them_and_the_fix_embeds_them(monkeypatch):
    """🔴 Red on main: the desktop build ships without faiss, and there the row said "faiss is not
    installed …" and nothing about the memory search read by keyword."""
    from personalclaw.resilience import fixes
    from personalclaw.resilience.doctor import MEMORY_INDEX_FIX

    monkeypatch.setattr("personalclaw.vector_memory.faiss_available", lambda: False)
    state = _main_state(_home())
    _one_unembedded(state)

    row = _doctor_row()
    preview = fixes.get_fix(MEMORY_INDEX_FIX).dry_preview()
    applied = fixes.apply_fix(MEMORY_INDEX_FIX)

    assert (row.ok, row.detail, row.fix_id) == (False, ONE_UNEMBEDDED, MEMORY_INDEX_FIX)
    assert preview.startswith("Would embed the 1 memory the model bound now has not embedded")
    assert applied["result"] == (
        "Embedded 1 memory with the model bound now. faiss is not installed, so semantic recall "
        "searches the stored vectors directly."
    )
    after = _doctor_row()
    assert after.ok is True and after.detail.startswith("faiss is not installed"), after


def test_with_no_model_bound_nothing_waits_on_a_re_index():
    """Control: with nothing bound nothing is compared, so nothing is waiting, anywhere."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    state = _main_state(_home())
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    state.context_builder.memory.vector_store.save_faiss_index()

    stats = _stats_route(state)
    ranking = _ranking_payload(_main_service(state))

    assert (stats["read_by_keyword"], stats["read_by_keyword_note"]) == (0, "")
    assert ranking["mode"] == "keyword" and "not embedded" not in ranking["summary"]
    assert _doctor_row().ok is True


def test_the_count_reads_a_database_the_store_has_not_migrated(tmp_path):
    """The Doctor opens memory.db read-only, so it cannot migrate it: with no model column, every
    vector is one that records no model."""
    from personalclaw.vector_memory import embedding_coverage

    db = sqlite3.connect(tmp_path / "memory.db")
    db.execute(
        "CREATE TABLE episodic_memories (id TEXT, text TEXT, embedding BLOB, is_deleted INTEGER, "
        "created_at TEXT)"
    )
    rows = [
        ("a", "an osprey", b"\0" * 8, 0, "2026-01-01"),
        ("b", "a kestrel", b"\0" * 8, 0, "2026-01-02"),
        ("c", "a heron", None, 0, "2026-01-03"),
        ("d", "gone", None, 1, "2026-01-04"),
    ]
    db.executemany("INSERT INTO episodic_memories VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()

    unbound = embedding_coverage(db, "", bound=False)
    bound = embedding_coverage(db, "rec:emb-a", bound=True)

    assert (unbound.comparable, unbound.other_model, unbound.unembedded) == (2, 0, 0)
    assert (bound.comparable, bound.other_model, bound.unembedded) == (0, 2, 1)
    assert bound.read_by_keyword == 3


def test_a_vector_the_model_wrote_at_another_width_is_counted_with_them(tmp_path):
    """🔴 Red on main: the model's output changed width, and the stats counted its older vectors as
    embedded. Search compares a vector only at the width the model writes now, so it reads those by
    keyword, and every surface counts them with the rest."""
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    store.write_episodic("An early note from the old embedding width", embedding=[1.0, 0, 0, 0])
    store.write_episodic("A second note from before the switch", embedding=[0, 1.0, 0, 0])
    store.embed_fn = lambda text: [1.0] * 8  # the same model, eight wide now
    store.write_episodic("The first note at the new width")
    stats = store.memory_stats()
    store.close()

    assert (stats["embedded_count"], stats["embedded_stale"], stats["unembedded"]) == (1, 2, 0)
    assert stats["read_by_keyword"] == 2
