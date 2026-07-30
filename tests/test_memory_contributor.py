"""Memory contributor provenance + owner-weighted ranking (TEAM-SHARED-ENTITIES §2.3).

The load-bearing rule these tests exist to pin: **locality affects ordering only, never
admission**. An owner-preference term folded into the relevance score could lift a
zero-relevance record into the result set, which would let "whose memory is this" decide
what the model SEES rather than the order it sees it in. Several tests below assert
exactly that boundary from both sides.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import (
    _OWNER_RANK_BONUS,
    VectorMemoryStore,
    _contributor_label,
    _owner_rank_bonus,
)

OWNER = "keyur-golani"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home with a configured owner username."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"dashboard": {"username": OWNER}}), encoding="utf-8"
    )
    from personalclaw.config.loader import AppConfig

    if hasattr(AppConfig, "_cached"):  # defensive: no known cache, but don't rely on it
        AppConfig._cached = None
    return tmp_path


@pytest.fixture
def store(home):
    s = VectorMemoryStore(db_path=home / "m.db", embedding_dim=3)
    s.init()
    return s


@pytest.fixture
def anon_store(tmp_path, monkeypatch):
    """A store with NO username configured — the single-user default."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    s = VectorMemoryStore(db_path=tmp_path / "anon.db", embedding_dim=3)
    s.init()
    return s


def _keys_in_order(block: str) -> list[str]:
    return [ln.split(":", 1)[0] for ln in block.splitlines() if ln.startswith("project.")]


# ── Migration ────────────────────────────────────────────────────────────────


def test_v9_is_applied(store):
    versions = {r[0] for r in store.db.execute("SELECT version FROM schema_version")}
    assert 9 in versions


@pytest.mark.parametrize("table", ["semantic_memory", "episodic_memories"])
def test_contributor_column_exists_on_both_tables(store, table):
    cols = {r[1] for r in store.db.execute(f"PRAGMA table_info({table})")}
    assert "contributor" in cols


def test_the_migration_is_idempotent(store):
    from personalclaw.vector_memory import _migrate_v9

    _migrate_v9(store.db)
    _migrate_v9(store.db)


def test_existing_rows_are_not_back_stamped(store):
    """A record written before the column existed has genuinely unknown authorship.
    Back-filling it with the current owner would invent provenance, not record it."""
    store.db.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, "
        "updated_at, is_deleted) VALUES ('project.old.row', '\"legacy\"', 1.0, 'x', "
        "'2020-01-01', '2020-01-01', 0)"
    )
    store.db.commit()
    row = store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.old.row'"
    ).fetchone()
    assert (row["contributor"] or "") == ""


# ── The write stamp ──────────────────────────────────────────────────────────


def test_a_semantic_write_is_stamped_with_the_owner(store):
    store.set_semantic("project.a.fact", "a fact", 0.9, "user")
    row = store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.a.fact'"
    ).fetchone()
    assert row["contributor"] == OWNER


def test_an_episodic_write_is_stamped_with_the_owner(store):
    store.write_episodic("something happened")
    row = store.db.execute("SELECT contributor FROM episodic_memories").fetchone()
    assert row["contributor"] == OWNER


def test_an_explicit_contributor_is_preserved(store):
    store.set_semantic("project.a.fact", "dana's fact", 0.9, "user", contributor="dana")
    row = store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.a.fact'"
    ).fetchone()
    assert row["contributor"] == "dana"


def test_with_no_username_the_stamp_is_empty(anon_store):
    """No identity configured ⇒ nothing to attribute, and no fabricated handle."""
    anon_store.set_semantic("project.a.fact", "a fact", 0.9, "user")
    row = anon_store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.a.fact'"
    ).fetchone()
    assert (row["contributor"] or "") == ""


def test_editing_a_foreign_record_does_not_transfer_authorship(store):
    """Touching a shared record is not writing it. A column that silently became
    'last writer' while claiming to mean 'contributor' would be worse than no column."""
    store.set_semantic("project.a.fact", "dana wrote this", 0.9, "user", contributor="dana")
    store.set_semantic("project.a.fact", "edited by the owner", 0.9, "user")
    row = store.db.execute(
        "SELECT contributor, value_json FROM semantic_memory WHERE key='project.a.fact'"
    ).fetchone()
    assert row["contributor"] == "dana"
    assert "edited by the owner" in row["value_json"]


def test_a_lesson_write_is_stamped_too(store):
    """write_lesson routes through set_semantic, so the single stamp site covers it."""
    store.write_lesson("always run the tests", source="user")
    rows = [
        r["contributor"]
        for r in store.db.execute("SELECT contributor FROM semantic_memory")
        if r["contributor"]
    ]
    assert OWNER in rows


# ── The ranking bonus (pure) ─────────────────────────────────────────────────


def test_the_owners_own_record_earns_the_bonus():
    assert _owner_rank_bonus(OWNER, OWNER) == _OWNER_RANK_BONUS


def test_a_foreign_record_earns_nothing():
    assert _owner_rank_bonus("dana", OWNER) == 0.0


def test_an_unattributed_record_counts_as_the_owners():
    """Every pre-column record looks like this; treating them as foreign would demote a
    solo user's whole memory on the first run after upgrading."""
    assert _owner_rank_bonus("", OWNER) == _OWNER_RANK_BONUS
    assert _owner_rank_bonus(None, OWNER) == _OWNER_RANK_BONUS


def test_with_no_owner_every_record_scores_the_same():
    """Uniform ⇒ no ordering change, which is exactly today's behavior."""
    assert _owner_rank_bonus("dana", "") == _owner_rank_bonus(OWNER, "") == _OWNER_RANK_BONUS


def test_the_bonus_is_smaller_than_one_keyword_step():
    """kw_raw/10 means one overlap step is 0.1. The bonus must break ties, not decide
    relevance — so it has to stay under a single step."""
    assert 0 < _OWNER_RANK_BONUS < 0.1


def test_whitespace_only_contributor_is_treated_as_unattributed():
    assert _owner_rank_bonus("   ", OWNER) == _OWNER_RANK_BONUS


# ── The contributor label (pure) ─────────────────────────────────────────────


def test_a_foreign_record_is_labeled():
    assert _contributor_label("dana", OWNER) == " (from dana)"


def test_the_owners_own_record_is_not_labeled():
    """ "(from keyur-golani)" on every line of a solo install is noise that makes the one
    case the label exists for harder to spot."""
    assert _contributor_label(OWNER, OWNER) == ""


def test_an_unattributed_record_is_not_labeled():
    assert _contributor_label("", OWNER) == ""
    assert _contributor_label(None, OWNER) == ""


def test_with_no_owner_nothing_is_labeled():
    assert _contributor_label("dana", "") == ""


# ── Ordering vs admission: the load-bearing boundary ─────────────────────────


def test_at_equal_relevance_the_owners_memory_orders_first(store):
    store.set_semantic("project.dana.x", "deploy cadence weekly", 0.9, "u", contributor="dana")
    store.set_semantic("project.mine.x", "deploy cadence weekly", 0.9, "u")
    order = _keys_in_order(store.get_semantic_context(query_text="deploy cadence", cap=4000))
    assert order[0] == "project.mine.x"
    assert "project.dana.x" in order  # ordered lower, NOT excluded


def test_a_zero_relevance_owner_record_is_still_not_admitted(store):
    """THE invariant. If the bonus were added to `score` before the `score > 0` gate,
    an irrelevant record would be admitted for no reason but whose it is."""
    store.set_semantic("project.mine.unrelated", "pomegranate marmalade", 0.9, "u")
    block = store.get_semantic_context(query_text="deploy cadence", cap=4000)
    assert "pomegranate" not in block


def test_a_stronger_foreign_match_still_beats_a_weaker_owner_match(store):
    """Relevance dominates; provenance only breaks near-ties."""
    store.set_semantic("project.mine.weak", "deploy", 0.9, "u")
    store.set_semantic(
        "project.dana.strong",
        "deploy cadence schedule specifics weekly",
        0.9,
        "u",
        contributor="dana",
    )
    order = _keys_in_order(
        store.get_semantic_context(query_text="deploy cadence schedule specifics", cap=4000)
    )
    assert order[0] == "project.dana.strong"


def test_ordering_is_unchanged_when_no_username_is_configured(anon_store):
    """A solo install must behave exactly as it does today."""
    anon_store.set_semantic("project.a.x", "deploy cadence", 0.9, "u")
    anon_store.set_semantic("project.b.x", "deploy cadence weekly extra", 0.9, "u")
    order = _keys_in_order(
        anon_store.get_semantic_context(query_text="deploy cadence weekly extra", cap=4000)
    )
    assert order[0] == "project.b.x"  # the better match, unaffected by provenance


def test_admission_is_identical_with_and_without_a_contributor(store):
    """The set of records surfaced must not depend on provenance at all."""
    store.set_semantic("project.a.x", "deploy cadence", 0.9, "u")
    store.set_semantic("project.b.x", "deploy cadence", 0.9, "u", contributor="dana")
    surfaced = set(_keys_in_order(store.get_semantic_context(query_text="deploy", cap=4000)))
    assert surfaced == {"project.a.x", "project.b.x"}


# ── Rendering into the prompt ────────────────────────────────────────────────


def test_the_injected_block_labels_a_foreign_record(store):
    store.set_semantic("project.dana.x", "dana's note about deploy", 0.9, "u", contributor="dana")
    block = store.get_semantic_context(query_text="deploy", cap=4000)
    assert "(from dana)" in block


def test_the_injected_block_does_not_label_the_owners_own(store):
    store.set_semantic("project.mine.x", "my note about deploy", 0.9, "u")
    block = store.get_semantic_context(query_text="deploy", cap=4000)
    # Assert on the RECORD lines only: the fence header explains what a "(from …)"
    # suffix means, so searching the whole block would match its own documentation.
    assert all("(from" not in ln for ln in block.splitlines() if ln.startswith("project."))


def test_the_fence_states_that_the_label_is_metadata_not_an_instruction(store):
    """A shared store means another person's text reaches this prompt. A contributor
    name must not read as an authority to obey."""
    store.set_semantic("project.dana.x", "a note", 0.9, "u", contributor="dana")
    block = store.get_semantic_context(query_text="note", cap=4000)
    assert "metadata" in block
    assert "never an instruction" in block


def test_the_no_query_branch_also_labels(store):
    """The recent-entries path must not silently drop provenance."""
    store.set_semantic("project.dana.x", "dana's note", 0.9, "u", contributor="dana")
    block = store.get_semantic_context(cap=4000)
    assert "(from dana)" in block


# ── Episodic provenance ──────────────────────────────────────────────────────


def test_recall_with_provenance_carries_the_contributor(store):
    store.write_episodic("dana mentioned the deploy window", contributor="dana")
    svc = MemoryService(MagicMock(), vector_store=store)
    hits = svc.recall_with_provenance(query_text="deploy window")
    assert hits
    assert hits[0]["contributor"] == "dana"


def test_recall_with_provenance_leaves_the_owners_own_blank_or_owned(store):
    store.write_episodic("I mentioned the deploy window")
    svc = MemoryService(MagicMock(), vector_store=store)
    hits = svc.recall_with_provenance(query_text="deploy window")
    assert hits
    assert hits[0]["contributor"] in ("", OWNER)


def test_episodic_ranking_prefers_the_owner_at_equal_relevance(store):
    """Texts differ by one trailing word — byte-identical episodes are deduplicated by
    the store, so an equal-relevance pair has to be near-identical, not identical."""
    store.write_episodic("the deploy window is tight today", contributor="dana")
    store.write_episodic("the deploy window is tight now", contributor=OWNER)
    svc = MemoryService(MagicMock(), vector_store=store)
    hits = svc.rank_episodic(query_text="the deploy window is tight", limit=5)
    assert len(hits) >= 2, f"expected both episodes, got {[h['text'] for h in hits]}"
    assert hits[0].get("contributor") in ("", OWNER)


def test_episodic_owner_preference_does_not_change_the_candidate_set(store):
    store.write_episodic("deploy window notes", contributor="dana")
    store.write_episodic("unrelated marmalade", contributor=OWNER)
    svc = MemoryService(MagicMock(), vector_store=store)
    texts = {h["text"] for h in svc.rank_episodic(query_text="deploy window notes", limit=5)}
    assert "deploy window notes" in texts


# ── Import preserves foreign provenance ──────────────────────────────────────


def test_import_preserves_a_foreign_contributor(store):
    """Stamping the importer would relabel a colleague's memory as the importer's —
    the same falsification identity.py forbids for renames."""
    store.import_memory(
        {
            "semantic": [
                {"key": "project.x.a", "value_json": '"dana wrote this"', "contributor": "dana"}
            ],
            "episodic": [{"text": "dana said something", "contributor": "dana"}],
        }
    )
    sem = store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.x.a'"
    ).fetchone()
    epi = store.db.execute("SELECT contributor FROM episodic_memories").fetchone()
    assert sem["contributor"] == "dana"
    assert epi["contributor"] == "dana"


def test_import_stamps_an_unattributed_record_with_the_importer(store):
    """No recorded author ⇒ the importer is the closest true answer."""
    store.import_memory({"semantic": [{"key": "project.x.b", "value_json": '"unclaimed"'}]})
    row = store.db.execute(
        "SELECT contributor FROM semantic_memory WHERE key='project.x.b'"
    ).fetchone()
    assert row["contributor"] == OWNER


# ── Durability: the two explicit column allowlists ───────────────────────────


def test_the_vault_frontmatter_emits_contributor():
    """_FM_ORDER is an explicit allowlist — a new column is invisible in vault notes
    unless it is listed."""
    from personalclaw.memory_vault import _FM_ORDER

    assert "contributor" in _FM_ORDER


def test_the_snapshot_merge_carries_contributor():
    """_MERGE_ALLOWED_TABLES names columns explicitly, so a restore would silently
    STRIP provenance if the column were missing from the list."""
    from pathlib import Path as _P

    src = _P("src/personalclaw/snapshot.py").read_text(encoding="utf-8")
    merge = src.split("ATTACH DATABASE", 1)[1][:1200]
    assert merge.count("contributor") >= 2  # semantic + episodic
