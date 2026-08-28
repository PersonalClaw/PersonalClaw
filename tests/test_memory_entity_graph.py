"""Tests for the typed entity graph over memory.db (MEMORY-GRAPH-AND-VAULT S1).

Three properties carry the design, so most of these assert one of them:

* linking is DETERMINISTIC and zero-LLM (same input → same edges, no model calls)
* an unknown name is PROPOSED, never invented into an entity
* every graph write is REVERSIBLE through the existing memory_events WAL
"""

import json
import tempfile
from pathlib import Path

import pytest

from personalclaw.memory_graph import ENTITY_TYPES, LINK_TYPES, AliasIndex, Entity
from personalclaw.memory_linker import (
    backfill,
    classify_link,
    link_record,
    seed_from_knowledge,
    seed_from_memory_facts,
)
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def store():
    s = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db", embedding_dim=3)
    s.init()
    return s


@pytest.fixture
def graph(store):
    return store.graph


def _seeded(store, graph):
    """Two entities plus a fresh matcher — the common arrangement."""
    project = graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
    person = graph.upsert_entity("Keyur Golani", "person", aliases=["@keyur"])
    store.invalidate_alias_index()
    return project, person


# ── Migration ──


class TestMigrationV7:
    def test_v7_is_applied(self, store):
        versions = {r[0] for r in store.db.execute("SELECT version FROM schema_version")}
        assert 7 in versions

    def test_graph_tables_exist(self, store):
        names = {
            r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"mem_entities", "mem_links", "mem_link_stats", "mem_entity_proposals"} <= names

    def test_migration_is_idempotent(self, store):
        """Re-running init must not duplicate or fail (ADD COLUMN has no IF NOT EXISTS)."""
        store.init()
        store.init()
        assert store.graph.summary()["entities"] == 0

    def test_legacy_knowledge_tables_are_never_dropped(self, store, caplog):
        """A table we didn't create is evidence, not garbage — warn, don't destroy."""
        store.db.execute("CREATE TABLE knowledge_facts (id TEXT)")
        store.db.execute("INSERT INTO knowledge_facts VALUES ('keep-me')")
        store.db.commit()
        from personalclaw.vector_memory import _migrate_v7

        with caplog.at_level("WARNING"):
            _migrate_v7(store.db)
        assert "knowledge_facts" in caplog.text
        rows = store.db.execute("SELECT id FROM knowledge_facts").fetchall()
        assert [r[0] for r in rows] == ["keep-me"]


# ── The alias matcher ──


class TestAliasIndex:
    def test_matches_name_and_aliases(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project", aliases=("pclaw", "p-claw")))
        for text in ("I use PersonalClaw daily", "pclaw is fast", "the p-claw repo"):
            assert [m.entity_id for m in index.find(text)] == ["e1"], text

    def test_match_is_case_insensitive(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project"))
        assert index.find("personalclaw") and index.find("PERSONALCLAW")

    def test_respects_word_boundaries(self):
        """The whole point of tokenizing: "Ann" must not match "Announcement"."""
        index = AliasIndex()
        index.add_entity(Entity("e1", "Ann", "person"))
        assert index.find("Announcement about something") == []
        assert [m.entity_id for m in index.find("Ann is here")] == ["e1"]

    def test_longest_match_wins(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "Keyur", "person"))
        index.add_entity(Entity("e2", "Keyur Golani", "person"))
        matches = index.find("spoke with Keyur Golani today")
        assert [m.entity_id for m in matches] == ["e2"]
        assert matches[0].matched == "Keyur Golani"

    def test_short_single_token_aliases_are_refused(self):
        """ "AI"/"ML" as bare words produce more false links than true ones."""
        index = AliasIndex()
        assert index.add("e1", "AI") is False
        assert index.add("e1", "ML") is False
        assert index.add("e1", "Claw") is True
        # Multi-token phrases are specific enough to bypass the floor.
        assert index.add("e2", "AI Safety") is True

    def test_empty_and_unmatched_text(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project"))
        assert index.find("") == []
        assert index.find("nothing relevant here") == []

    def test_empty_index_matches_nothing(self):
        assert AliasIndex().find("PersonalClaw") == []

    def test_one_mention_per_phrase_occurrence(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project"))
        assert len(index.find("PersonalClaw and PersonalClaw again")) == 2

    def test_context_snippet_surrounds_the_match(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project"))
        text = "x" * 300 + " PersonalClaw " + "y" * 300
        context = index.find(text)[0].context(text)
        assert "PersonalClaw" in context
        assert len(context) < len(text)

    def test_unknown_capitalized_finds_multiword_names_only(self):
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project"))
        found = index.unknown_capitalized("Dana Whitfield reviewed PersonalClaw on Tuesday")
        assert "Dana Whitfield" in found
        # A known entity is not "unknown", and lone capitalized words are noise.
        assert not any("PersonalClaw" in f for f in found)
        assert "Tuesday" not in found

    def test_determinism(self):
        """Same input, same output — the property that makes this cacheable."""
        index = AliasIndex()
        index.add_entity(Entity("e1", "PersonalClaw", "project", aliases=("pclaw",)))
        index.add_entity(Entity("e2", "Keyur Golani", "person"))
        text = "Keyur Golani shipped pclaw and PersonalClaw"
        first = [(m.entity_id, m.start, m.end) for m in index.find(text)]
        for _ in range(5):
            assert [(m.entity_id, m.start, m.end) for m in index.find(text)] == first


# ── Entities ──


class TestEntities:
    def test_create_and_read_back(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
        entities = graph.entities()
        assert len(entities) == 1
        assert entities[0].id == eid
        assert entities[0].name == "PersonalClaw"
        assert entities[0].aliases == ("pclaw",)

    def test_upsert_is_idempotent_by_name(self, graph):
        """Re-seeding must not fork every entity in two."""
        first = graph.upsert_entity("PersonalClaw", "project")
        second = graph.upsert_entity("personalclaw", "project")
        assert first == second
        assert len(graph.entities()) == 1

    def test_upsert_merges_aliases(self, graph):
        graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
        graph.upsert_entity("PersonalClaw", "project", aliases=["claw"])
        assert graph.entities()[0].aliases == ("claw", "pclaw")

    def test_unknown_entity_type_is_refused(self, graph):
        with pytest.raises(ValueError):
            graph.upsert_entity("Thing", "sandwich")

    def test_empty_name_is_refused(self, graph):
        with pytest.raises(ValueError):
            graph.upsert_entity("   ", "person")

    def test_delete_removes_links_and_stats(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        graph.add_link(
            from_kind="semantic", from_ref="project.a.note", to_entity=eid, link_type="mentions"
        )
        assert graph.delete_entity(eid) is True
        assert graph.backlinks(eid) == []
        assert graph.stats(eid)["inbound_count"] == 0
        assert graph.entities() == []

    def test_delete_is_idempotent(self, graph):
        eid = graph.upsert_entity("X Corp", "org")
        assert graph.delete_entity(eid) is True
        assert graph.delete_entity(eid) is False

    def test_every_declared_type_is_accepted(self, graph):
        for i, etype in enumerate(ENTITY_TYPES):
            assert graph.upsert_entity(f"thing-{i}", etype)


# ── Links ──


class TestLinks:
    def test_add_and_read(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        assert graph.add_link(
            from_kind="semantic", from_ref="project.a", to_entity=eid, link_type="mentions"
        )
        links = graph.links_from("semantic", "project.a")
        assert len(links) == 1
        assert links[0]["link_type"] == "mentions"
        assert graph.stats(eid)["inbound_count"] == 1

    def test_duplicate_edge_reinforces_instead_of_inserting(self, graph):
        """A record rewritten ten times must not look ten times as connected."""
        eid = graph.upsert_entity("PersonalClaw", "project")
        first = graph.add_link(
            from_kind="semantic", from_ref="project.a", to_entity=eid, link_type="mentions"
        )
        again = graph.add_link(
            from_kind="semantic", from_ref="project.a", to_entity=eid, link_type="mentions"
        )
        assert first is True and again is False
        assert len(graph.backlinks(eid)) == 1
        assert graph.stats(eid)["inbound_count"] == 1

    def test_unknown_link_type_is_refused(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        with pytest.raises(ValueError):
            graph.add_link(
                from_kind="semantic", from_ref="a", to_entity=eid, link_type="vibes_with"
            )

    def test_exactly_one_target_is_required(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        with pytest.raises(ValueError):
            graph.add_link(from_kind="semantic", from_ref="a", link_type="mentions")
        with pytest.raises(ValueError):
            graph.add_link(
                from_kind="semantic",
                from_ref="a",
                to_entity=eid,
                to_ref="b",
                link_type="references",
            )

    def test_record_to_record_edges(self, graph):
        assert graph.add_link(
            from_kind="semantic", from_ref="a", to_ref="b", link_type="references"
        )
        assert graph.links_from("semantic", "a")[0]["to_ref"] == "b"

    def test_remove_link_decrements_stats(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        graph.add_link(from_kind="semantic", from_ref="a", to_entity=eid, link_type="mentions")
        link_id = graph.backlinks(eid)[0]["id"]
        assert graph.remove_link(link_id) is True
        assert graph.stats(eid)["inbound_count"] == 0
        assert graph.remove_link(link_id) is False

    def test_drop_links_for_a_record(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        graph.add_link(from_kind="semantic", from_ref="a", to_entity=eid, link_type="mentions")
        graph.add_link(from_kind="semantic", from_ref="a", to_ref="b", link_type="references")
        assert graph.drop_links_for("semantic", "a") == 2
        assert graph.links_from("semantic", "a") == []
        assert graph.stats(eid)["inbound_count"] == 0

    def test_stats_never_go_negative(self, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        graph.add_link(from_kind="semantic", from_ref="a", to_entity=eid, link_type="mentions")
        graph.drop_links_for("semantic", "a")
        graph.drop_links_for("semantic", "a")
        assert graph.stats(eid)["inbound_count"] == 0


# ── The typed-edge cascade ──


class TestCascade:
    def _mention(self, entity_id="e1"):
        from personalclaw.memory_graph import Mention

        return Mention(entity_id, "PersonalClaw", 0, 12)

    def test_persona_key_yields_about(self):
        assert classify_link("user.persona.abc123", "PersonalClaw", self._mention(), {}) == "about"

    def test_identity_facet_key_yields_about(self):
        assert classify_link("pref.facet.identity.deadbeef", "x", self._mention(), {}) == "about"

    def test_project_key_plus_project_entity_yields_same_project(self):
        assert (
            classify_link("project.claw.note", "PersonalClaw", self._mention(), {"e1": "project"})
            == "same_project"
        )

    def test_project_key_with_a_person_does_not_claim_affiliation(self):
        assert (
            classify_link("project.claw.note", "Keyur", self._mention(), {"e1": "person"})
            != "same_project"
        )

    def test_url_yields_references(self):
        assert (
            classify_link("user.note.a", "see https://example.com", self._mention(), {})
            == "references"
        )

    def test_plain_text_falls_back_to_mentions(self):
        assert classify_link("user.note.a", "just a note", self._mention(), {}) == "mentions"

    def test_cascade_only_emits_known_types(self):
        for key, text in (
            ("user.persona.a", "x"),
            ("project.claw.b", "x"),
            ("user.note.c", "https://x.dev"),
            ("user.note.d", "plain"),
        ):
            assert classify_link(key, text, self._mention(), {"e1": "project"}) in LINK_TYPES


# ── Write-time linking ──


class TestWriteTimeLinking:
    def test_semantic_write_creates_typed_links(self, store, graph):
        project, person = _seeded(store, graph)
        assert (
            store.set_semantic(
                "project.claw.note", "Keyur Golani refactored pclaw", 0.9, "user_explicit"
            )
            is None
        )
        by_entity = {
            link["to_entity"]: link["link_type"]
            for link in graph.links_from("semantic", "project.claw.note")
            if link["to_entity"]
        }
        assert by_entity[project] == "same_project"
        assert by_entity[person] == "mentions"

    def test_links_carry_a_context_snippet(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "met Keyur Golani at the office", 0.9, "user_explicit")
        link = graph.links_from("semantic", "user.note.a")[0]
        assert "Keyur Golani" in (link["context"] or "")

    def test_rewriting_a_record_does_not_inflate_links(self, store, graph):
        _seeded(store, graph)
        for i in range(5):
            store.set_semantic(
                "project.claw.note", f"pclaw rev {i} by Keyur Golani", 0.9, "user_explicit"
            )
        assert graph.summary()["links"] == 2

    def test_rewrite_drops_links_to_names_no_longer_present(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "about Keyur Golani", 0.9, "user_explicit")
        assert len(graph.links_from("semantic", "user.note.a")) == 1
        store.set_semantic("user.note.a", "about nobody in particular", 1.0, "user_explicit")
        assert graph.links_from("semantic", "user.note.a") == []

    def test_episodic_write_links_and_groups_by_conversation(self, store, graph):
        _seeded(store, graph)
        store.write_episodic("standup with Keyur Golani", conversation_id="conv-1")
        store.write_episodic("pclaw follow-up", conversation_id="conv-1")
        ids = [r[0] for r in store.db.execute("SELECT id FROM episodic_memories")]
        assert len(ids) == 2
        for mem_id in ids:
            kinds = {link["link_type"] for link in graph.links_from("episodic", mem_id)}
            assert "mentions" in kinds
            assert "temporal_proximity" in kinds

    def test_facet_payload_text_is_matched_not_the_json(self, store, graph):
        """Facet values are dicts; the readable claim lives in `text`."""
        _seeded(store, graph)
        store.set_semantic(
            "pref.facet.identity.abc123",
            {"cls": "identity", "text": "works on PersonalClaw", "stability": 0.5},
            0.9,
            "facet",
        )
        links = graph.links_from("semantic", "pref.facet.identity.abc123")
        assert [link["link_type"] for link in links] == ["about"]

    def test_rejected_write_creates_no_links(self, store, graph):
        _seeded(store, graph)
        # A non-allowlisted key is rejected, so nothing should be linked.
        assert store.set_semantic("bogus.key", "Keyur Golani", 0.9, "user_explicit") is not None
        assert graph.summary()["links"] == 0

    def test_disabling_the_graph_stops_linking(self, store, graph):
        _seeded(store, graph)
        store.graph_enabled = False
        store.set_semantic("user.note.a", "Keyur Golani again", 0.9, "user_explicit")
        assert graph.links_from("semantic", "user.note.a") == []

    def test_linker_failure_never_fails_the_write(self, store, graph, monkeypatch):
        """A linking bug must degrade to 'no links', never reject the user's data."""
        _seeded(store, graph)
        import personalclaw.memory_linker as linker_mod

        def _boom(*a, **k):
            raise RuntimeError("linker exploded")

        monkeypatch.setattr(linker_mod, "link_record", _boom)
        assert store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit") is None
        assert store.get_semantic("user.note.a") is not None

    def test_link_record_reports_what_it_did(self, store, graph):
        """The linker's own return value — used by the backfill's before/after report."""
        _seeded(store, graph)
        report = link_record(
            graph,
            store.alias_index,
            from_kind="semantic",
            from_ref="user.note.a",
            key="user.note.a",
            text="pclaw work with Keyur Golani, plus Dana Whitfield",
        )
        assert report["links"] == 2
        assert report["mentions"] == 2
        assert report["proposals"] == 1  # Dana Whitfield is proposed, not created
        assert len(report["entities"]) == 2

    def test_write_makes_no_llm_calls(self, store, graph):
        """Zero-LLM is a hard property: an embed_fn call here would be a bug."""
        _seeded(store, graph)
        calls = []
        store.embed_fn = lambda t: (calls.append(t), [1.0, 0.0, 0.0])[1]
        before = len(calls)
        store.link_written_record(
            from_kind="semantic", from_ref="user.note.a", key="user.note.a", text="pclaw"
        )
        assert len(calls) == before


# ── The notability gate ──


class TestProposals:
    def test_unknown_name_does_not_become_an_entity(self, store, graph):
        _seeded(store, graph)
        before = len(graph.entities())
        store.set_semantic("user.note.a", "met Dana Whitfield today", 0.9, "user_explicit")
        assert len(graph.entities()) == before

    def test_promotion_needs_distinct_records(self, store, graph):
        _seeded(store, graph)
        for i in range(3):
            store.set_semantic(f"user.note.{i}", "Dana Whitfield again", 0.9, "user_explicit")
        proposals = {p["name"]: p["mention_count"] for p in graph.proposals()}
        assert proposals.get("Dana Whitfield") == 3

    def test_one_chatty_record_is_not_evidence(self, graph):
        """Repeating a name in ONE record must not reach the threshold."""
        for _ in range(9):
            graph.tally_proposal("Dana Whitfield", "user.note.a")
        assert graph.proposals() == []

    def test_below_threshold_is_hidden(self, graph):
        graph.tally_proposal("Dana Whitfield", "a")
        graph.tally_proposal("Dana Whitfield", "b")
        assert graph.proposals() == []

    def test_accept_creates_the_entity_and_clears_the_tally(self, graph):
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        eid = graph.accept_proposal("Dana Whitfield", "person")
        assert eid
        assert [e.name for e in graph.entities()] == ["Dana Whitfield"]
        assert graph.proposals() == []

    def test_reject_clears_without_creating(self, graph):
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        assert graph.reject_proposal("Dana Whitfield") is True
        assert graph.entities() == []
        assert graph.proposals() == []

    def test_accepting_links_records_that_already_mentioned_it(self, store, graph):
        """Adding an entity should connect the past, not only the future."""
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Dana Whitfield reviewed it", 0.9, "user_explicit")
        eid = graph.accept_proposal("Dana Whitfield", "person")
        store.invalidate_alias_index()
        backfill(graph)
        assert len(graph.backlinks(eid)) == 1


# ── Reversibility ──


class TestReversibility:
    def test_link_add_writes_a_wal_row(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        rows = store.db.execute(
            "SELECT * FROM memory_events WHERE event_type = 'link_add'"
        ).fetchall()
        assert rows
        payload = json.loads(rows[0]["new_value"])
        assert payload["from_ref"] == "user.note.a"
        assert payload["link_type"] in LINK_TYPES

    def test_undo_link_add_removes_the_edge(self, store, graph):
        project, _ = _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        event = store.db.execute(
            "SELECT id FROM memory_events WHERE event_type = 'link_add' ORDER BY id LIMIT 1"
        ).fetchone()
        ok, msg = store.undo_event(event["id"])
        assert ok, msg
        assert graph.links_from("semantic", "user.note.a") == []

    def test_undo_is_idempotent(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        event_id = store.db.execute(
            "SELECT id FROM memory_events WHERE event_type = 'link_add' ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        assert store.undo_event(event_id)[0] is True
        ok, msg = store.undo_event(event_id)
        assert ok and msg == "already undone"

    def test_undo_link_remove_restores_the_edge(self, store, graph):
        eid = graph.upsert_entity("PersonalClaw", "project")
        graph.add_link(
            from_kind="semantic", from_ref="user.note.a", to_entity=eid, link_type="mentions"
        )
        link_id = graph.backlinks(eid)[0]["id"]
        graph.remove_link(link_id)
        event_id = store.db.execute(
            "SELECT id FROM memory_events WHERE event_type = 'link_remove' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        ok, msg = store.undo_event(event_id)
        assert ok, msg
        assert len(graph.backlinks(eid)) == 1

    def test_unreadable_payload_is_refused_not_crashed(self, store):
        store.db.execute(
            "INSERT INTO memory_events (event_type, memory_type, memory_key, new_value, "
            "source, created_at) VALUES ('link_add', 'link', 'x', 'not json', 's', 'now')"
        )
        store.db.commit()
        event_id = store.db.execute("SELECT MAX(id) FROM memory_events").fetchone()[0]
        ok, msg = store.undo_event(event_id)
        assert ok is False
        assert "unreadable" in msg


# ── Seeding ──


class TestSeeding:
    def test_seeds_projects_from_project_keys(self, store, graph):
        store.set_semantic("project.personalclaw.tool", "uses pytest", 0.9, "user_explicit")
        assert seed_from_memory_facts(graph) >= 1
        assert any(e.entity_type == "project" for e in graph.entities())

    def test_seeds_a_person_from_an_explicit_identity_claim(self, store, graph):
        store.set_semantic(
            "pref.facet.identity.abc123",
            {"cls": "identity", "text": "My name is Dana Whitfield", "stability": 0.6},
            0.9,
            "facet",
        )
        seed_from_memory_facts(graph)
        assert [e.name for e in graph.entities() if e.entity_type == "person"] == ["Dana Whitfield"]

    def test_a_non_name_identity_facet_mints_nobody(self, store, graph):
        """ "prefers terse replies" is an identity facet with no person in it."""
        store.set_semantic(
            "pref.facet.identity.def456",
            {"cls": "identity", "text": "prefers terse replies", "stability": 0.5},
            0.9,
            "facet",
        )
        seed_from_memory_facts(graph)
        assert [e for e in graph.entities() if e.entity_type == "person"] == []

    def test_seeding_is_idempotent(self, store, graph):
        store.set_semantic("project.claw.tool", "x", 0.9, "user_explicit")
        seed_from_memory_facts(graph)
        first = len(graph.entities())
        seed_from_memory_facts(graph)
        assert len(graph.entities()) == first

    def test_corrupt_value_json_is_skipped(self, store, graph):
        store.db.execute(
            "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, "
            "updated_at, is_deleted) VALUES ('project.x.a', '{bad', 0.9, 's', 'n', 'n', 0)"
        )
        store.db.commit()
        seed_from_memory_facts(graph)  # must not raise

    def test_knowledge_seed_is_read_only_and_survives_a_missing_store(self, graph, tmp_path):
        assert seed_from_knowledge(graph, tmp_path / "nope.db") == 0

    def test_knowledge_entities_are_adopted_with_aliases(self, graph, tmp_path):
        import sqlite3

        kpath = tmp_path / "knowledge.db"
        kdb = sqlite3.connect(kpath)
        kdb.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT, entity_type TEXT, "
            "description TEXT, aliases TEXT, created_at TEXT, updated_at TEXT)"
        )
        kdb.execute(
            "INSERT INTO entities VALUES ('k1', 'Acme Corp', 'org', NULL, "
            "'[\"Acme\"]', 'n', 'n')"
        )
        kdb.commit()
        kdb.close()
        assert seed_from_knowledge(graph, kpath) == 1
        entity = graph.entities()[0]
        assert entity.name == "Acme Corp"
        assert "Acme" in entity.aliases
        # The knowledge id is kept as a hint, never as a foreign key.
        assert entity.source == "knowledge:k1"

    def test_unknown_knowledge_type_is_normalized_not_dropped(self, graph, tmp_path):
        import sqlite3

        kpath = tmp_path / "knowledge.db"
        kdb = sqlite3.connect(kpath)
        kdb.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT, entity_type TEXT, "
            "description TEXT, aliases TEXT, created_at TEXT, updated_at TEXT)"
        )
        kdb.execute(
            "INSERT INTO entities VALUES ('k1', 'Quantum', 'concept', NULL, '[]', 'n', 'n')"
        )
        kdb.commit()
        kdb.close()
        seed_from_knowledge(graph, kpath)
        assert graph.entities()[0].entity_type == "topic"


# ── Backfill ──


class TestBackfill:
    def test_links_existing_records(self, store, graph):
        store.set_semantic("project.claw.a", "pclaw notes", 0.9, "user_explicit")
        store.write_episodic("worked on pclaw", conversation_id="c1")
        # Entity added AFTER the records exist — nothing is linked yet.
        graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
        store.invalidate_alias_index()
        result = backfill(graph)
        assert result["records_processed"] == 2
        assert result["links_created"] >= 2
        assert result["after"]["links"] > result["before"]["links"]

    def test_backfill_is_idempotent(self, store, graph):
        store.set_semantic("project.claw.a", "pclaw notes", 0.9, "user_explicit")
        graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
        store.invalidate_alias_index()
        backfill(graph)
        first = graph.summary()["links"]
        backfill(graph)
        assert graph.summary()["links"] == first

    def test_backfill_on_an_empty_store(self, graph):
        result = backfill(graph)
        assert result["records_processed"] == 0
        assert result["links_created"] == 0

    def test_respects_a_limit(self, store, graph):
        for i in range(5):
            store.set_semantic(f"user.note.{i}", "pclaw", 0.9, "user_explicit")
        graph.upsert_entity("PersonalClaw", "project", aliases=["pclaw"])
        store.invalidate_alias_index()
        assert backfill(graph, limit=2)["records_processed"] <= 4


# ── Lint + summary ──


class TestLintAndSummary:
    def test_orphan_counts(self, store, graph):
        store.set_semantic("user.note.a", "nothing known here", 0.9, "user_explicit")
        counts = graph.orphan_counts()
        assert counts["semantic_orphans"] == 1
        assert counts["episodic_orphans"] == 0

    def test_phantom_entity_is_counted(self, graph):
        graph.upsert_entity("Unused Thing", "topic")
        assert graph.orphan_counts()["phantom_entities"] == 1

    def test_lint_reports_graph_flags(self, store, graph):
        from personalclaw.memory_lint import lint_memory

        store.set_semantic("user.note.a", "unlinked note", 0.9, "user_explicit")
        graph.upsert_entity("Unused Thing", "topic")
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        checks = {f["check"] for f in lint_memory(store).flags}
        assert "graph_orphans" in checks
        assert "phantom_entity" in checks
        assert "proposed_entity" in checks

    def test_no_entities_means_no_orphan_noise(self, store, graph):
        """Before any entity exists every record is trivially unlinked; saying so
        for each one would bury the health tab in unactionable noise."""
        from personalclaw.memory_lint import lint_memory

        for i in range(3):
            store.set_semantic(f"user.note.{i}", "some note", 0.9, "user_explicit")
        assert graph.summary()["entities"] == 0
        assert "graph_orphans" not in {f["check"] for f in lint_memory(store).flags}

    def test_orphans_are_reported_once_entities_exist(self, store, graph):
        from personalclaw.memory_lint import lint_memory

        graph.upsert_entity("PersonalClaw", "project")
        store.invalidate_alias_index()
        store.set_semantic("user.note.a", "unrelated note", 0.9, "user_explicit")
        assert "graph_orphans" in {f["check"] for f in lint_memory(store).flags}

    def test_lint_skips_graph_checks_when_disabled(self, store):
        from personalclaw.memory_lint import lint_memory

        store.set_semantic("user.note.a", "unlinked", 0.9, "user_explicit")
        store.graph_enabled = False
        checks = {f["check"] for f in lint_memory(store).flags}
        assert "graph_orphans" not in checks

    def test_summary_shape(self, graph):
        summary = graph.summary()
        for key in (
            "entities",
            "links",
            "linked_records",
            "proposals",
            "semantic_orphans",
            "episodic_orphans",
            "phantom_entities",
        ):
            assert key in summary


# ── Service + capability degradation ──


class TestServiceSurface:
    def _service(self, store):
        from personalclaw.memory_service import MemoryService

        return MemoryService.over_vector_store(store)

    def test_capability_flag_tracks_the_setting(self, store):
        assert store.capabilities().entity_graph is True
        store.graph_enabled = False
        assert store.capabilities().entity_graph is False

    def test_service_exposes_the_graph(self, store, graph):
        _seeded(store, graph)
        service = self._service(store)
        assert service.has_graph is True
        assert {e["name"] for e in service.graph_entities()} == {
            "PersonalClaw",
            "Keyur Golani",
        }

    def test_service_degrades_when_disabled(self, store, graph):
        _seeded(store, graph)
        store.graph_enabled = False
        service = self._service(store)
        assert service.has_graph is False
        assert service.graph_entities() == []
        assert service.graph_summary() == {}
        assert service.graph_backlinks("e-whatever") == []
        assert service.graph_add_entity("X", "person") == ""
        assert service.graph_backfill() == {}

    def test_service_add_entity_relinks_existing_records(self, store):
        store.set_semantic("user.note.a", "about Acme Corp", 0.9, "user_explicit")
        service = self._service(store)
        eid = service.graph_add_entity("Acme Corp", "org")
        service.graph_backfill()
        assert len(service.graph_backlinks(eid)) == 1

    def test_service_backlinks_answer_what_do_i_know(self, store, graph):
        project, _ = _seeded(store, graph)
        store.set_semantic("project.claw.a", "pclaw is fast", 0.9, "user_explicit")
        store.set_semantic("user.note.b", "PersonalClaw ships", 0.9, "user_explicit")
        service = self._service(store)
        refs = {link["from_ref"] for link in service.graph_backlinks(project)}
        assert refs == {"project.claw.a", "user.note.b"}


# ── Config wiring ──


class TestConfigWiring:
    def test_default_is_on(self):
        from personalclaw.config.loader import AppConfig

        assert AppConfig().memory.graph_enabled is True

    def test_round_trips_through_to_dict(self):
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig()
        cfg.memory.graph_enabled = False
        assert cfg.to_dict()["memory"]["graph_enabled"] is False

    def test_guard_polarity_means_ambiguity_stays_on(self, tmp_path, monkeypatch):
        """A guard-class flag fails ON — an unreadable value must not silently
        disable a feature the user believes is running.

        Note the two layers: the JSON-schema validator drops a wrongly-TYPED value
        before the loader sees it (so a string never reaches `_guard_flag` from
        config.json), and `_guard_flag` then decides the polarity for anything that
        does arrive. Both must land on "still enabled".
        """
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        from personalclaw.config.coercion import _guard_flag
        from personalclaw.config.loader import AppConfig, config_path

        for raw, expected in ((True, True), (False, False), ("garbage", True), (None, True)):
            config_path().write_text(
                json.dumps({"memory": {"graph_enabled": raw}}), encoding="utf-8"
            )
            assert AppConfig.load().memory.graph_enabled is expected, raw

        # The polarity itself: only an explicit false-spelling turns a guard off.
        assert _guard_flag("false") is False
        assert _guard_flag("off") is False
        assert _guard_flag("nonsense") is True
        assert _guard_flag(None) is True

    def test_patch_allowlist_includes_the_toggle(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        assert "memory.graph_enabled" in _EDITABLE_CONFIG


# ── Regressions found by driving the real API ────────────────────────────────


class TestValidationRegressions:
    def test_accepted_name_stops_being_proposed(self, graph):
        """A name that became an entity must not still ask to be accepted."""
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        assert [p["name"] for p in graph.proposals()] == ["Dana Whitfield"]
        graph.upsert_entity("Dana Whitfield", "person", source="facet")
        assert graph.proposals() == []

    def test_a_matching_alias_also_retires_a_proposal(self, graph):
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        graph.upsert_entity("D. Whitfield", "person", aliases=["Dana Whitfield"])
        assert graph.proposals() == []

    def test_stale_proposals_are_pruned_not_just_hidden(self, graph):
        for ref in ("a", "b", "c"):
            graph.tally_proposal("Dana Whitfield", ref)
        graph.upsert_entity("Dana Whitfield", "person")
        graph.proposals()
        remaining = graph.db.execute("SELECT COUNT(*) FROM mem_entity_proposals").fetchone()[0]
        assert remaining == 0

    def test_project_seed_recovers_the_authors_capitalization(self, store, graph):
        """Keys are lowercase slugs; the entity name is what the user reads."""
        store.set_semantic(
            "project.personalclaw.stack",
            "PersonalClaw runs on aiohttp and SQLite",
            0.9,
            "user_explicit",
        )
        seed_from_memory_facts(graph)
        assert [e.name for e in graph.entities() if e.entity_type == "project"] == ["PersonalClaw"]

    def test_project_seed_falls_back_to_the_slug(self, store, graph):
        """No casing evidence in the text → the slug is still better than nothing."""
        store.set_semantic("project.apollo.note", "uses pytest", 0.9, "user_explicit")
        seed_from_memory_facts(graph)
        assert [e.name for e in graph.entities() if e.entity_type == "project"] == ["apollo"]


class TestKillSwitchIsLive:
    """The Settings toggle must work on a RUNNING gateway, not only at boot.

    Found by driving the real API: the store had captured the flag at construction,
    so flipping it left the live store linking happily.
    """

    def test_config_change_takes_effect_without_a_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        from personalclaw.config.loader import config_path

        store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
        store.init()
        store.graph.upsert_entity("PersonalClaw", "project")
        store.invalidate_alias_index()
        assert store.graph_enabled is True

        config_path().write_text(json.dumps({"memory": {"graph_enabled": False}}), encoding="utf-8")
        assert store.graph_enabled is False
        store.set_semantic("user.note.a", "PersonalClaw again", 0.9, "user_explicit")
        assert store.graph.links_from("semantic", "user.note.a") == []

        config_path().write_text(json.dumps({"memory": {"graph_enabled": True}}), encoding="utf-8")
        assert store.graph_enabled is True
        store.set_semantic("user.note.b", "PersonalClaw once more", 0.9, "user_explicit")
        assert store.graph.links_from("semantic", "user.note.b")

    def test_an_explicit_pin_overrides_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
        store.init()
        store.graph_enabled = False
        assert store.graph_enabled is False
        # Setting it back to None resumes following config.
        store.graph_enabled = None
        assert store.graph_enabled is True

    def test_unreadable_config_leaves_linking_on(self, tmp_path, monkeypatch):
        """Fail-safe direction: losing free, deterministic linking is the worse
        surprise, so ambiguity keeps it running."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
        store.init()
        from personalclaw.config.loader import AppConfig

        monkeypatch.setattr(
            AppConfig, "load", staticmethod(lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        )
        assert store.graph_enabled is True


class TestSqliteDriverParity:
    """The graph must catch the exceptions its CONNECTION actually raises.

    Caught only by CI: on Linux/x86_64 the store connects through `pysqlite3`, whose
    exception classes are DISTINCT objects from the stdlib's. `memory_graph` operates
    on `vector_memory`'s connection, so importing plain `sqlite3` here made
    `except sqlite3.IntegrityError` never match — the duplicate-edge path crashed on
    CI while passing on macOS, where no pysqlite3 exists and the classes coincided.
    """

    def test_graph_and_store_share_one_sqlite_module(self):
        import personalclaw.memory_graph as graph_mod
        import personalclaw.vector_memory as store_mod

        assert graph_mod.sqlite3 is store_mod.sqlite3, (
            "memory_graph must import sqlite3 the same way vector_memory does, or its "
            "except-clauses cannot catch what the shared connection raises"
        )

    def test_duplicate_edge_is_caught_not_raised(self, store, graph):
        """The behavior that broke: inserting the same edge twice must return False,
        not propagate an IntegrityError from the driver."""
        eid = graph.upsert_entity("PersonalClaw", "project")
        assert (
            graph.add_link(from_kind="semantic", from_ref="k", to_entity=eid, link_type="mentions")
            is True
        )
        assert (
            graph.add_link(from_kind="semantic", from_ref="k", to_entity=eid, link_type="mentions")
            is False
        )
        assert len(graph.backlinks(eid)) == 1

    def test_the_integrity_error_class_is_the_connections_own(self, store):
        """Whatever driver is in play, the class we catch must be the one it raises."""
        import personalclaw.memory_graph as graph_mod

        with pytest.raises(graph_mod.sqlite3.IntegrityError):
            store.db.execute("CREATE TABLE uniq_probe (x TEXT PRIMARY KEY)")
            store.db.execute("INSERT INTO uniq_probe VALUES ('a')")
            store.db.execute("INSERT INTO uniq_probe VALUES ('a')")


# ── The graph recall arm (Session 2, §2.1–2.2) ───────────────────────────────


class TestGraphRecallArm:
    def test_query_resolves_entities_with_the_write_time_matcher(self, store, graph):
        """Symmetry matters: a query must resolve the way the record that mentioned
        the entity did, or the arm looks for links it never made."""
        project, person = _seeded(store, graph)
        assert graph.resolve_query("what about pclaw?", store.alias_index) == [project]
        assert graph.resolve_query("ask @keyur", store.alias_index) == [person]

    def test_unresolvable_query_yields_no_boosts(self, store, graph):
        _seeded(store, graph)
        assert graph.recall_refs("nothing recognizable here", index=store.alias_index) == {}

    def test_boost_reaches_the_linked_record(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani likes terse replies", 0.9, "user_explicit")
        boosts = graph.recall_refs("@keyur", index=store.alias_index)
        assert "user.note.a" in boosts
        assert boosts["user.note.a"] > 0

    def test_boost_accumulates_across_named_entities(self, store, graph):
        """A record linked to two entities the query names beats one linked to one."""
        _seeded(store, graph)
        store.set_semantic("project.claw.both", "Keyur Golani works on pclaw", 0.9, "user_explicit")
        store.set_semantic("user.note.one", "Keyur Golani alone", 0.9, "user_explicit")
        boosts = graph.recall_refs("pclaw and @keyur", index=store.alias_index)
        assert boosts["project.claw.both"] > boosts["user.note.one"]

    def test_boost_stays_small(self, store, graph):
        """β is deliberately small — connection breaks near-ties, it does not outrank
        a record that matches the words the user typed."""
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        boost = graph.recall_refs("@keyur", index=store.alias_index)["user.note.a"]
        assert boost < 0.5

    def test_recall_finds_what_similarity_cannot(self, store, graph):
        """The whole point of the arm: a nickname sharing NO characters with the
        stored text resolves only through the graph."""
        graph.upsert_entity("Ana Ortiz", "person", aliases=["Sparrow"])
        store.invalidate_alias_index()
        store.set_semantic(
            "user.note.a", "Ana Ortiz owns the billing rewrite", 0.9, "user_explicit"
        )
        store.set_semantic("user.note.b", "quarterly budget review", 0.9, "user_explicit")

        query = "what is Sparrow working on"
        store.graph_enabled = False
        without = store.get_semantic_context(query_text=query, cap=800)
        store.graph_enabled = True
        with_graph = store.get_semantic_context(query_text=query, cap=800)
        assert "billing" not in without
        assert "billing" in with_graph

    def test_word_queries_are_unaffected(self, store, graph):
        """The arm ADDS recall; it must not change what plain keyword search finds."""
        _seeded(store, graph)
        store.set_semantic(
            "user.note.pipe", "the deploy pipeline uses actions", 0.9, "user_explicit"
        )
        store.graph_enabled = False
        without = store.get_semantic_context(query_text="deploy pipeline", cap=800)
        store.graph_enabled = True
        with_graph = store.get_semantic_context(query_text="deploy pipeline", cap=800)
        assert "pipeline" in without and "pipeline" in with_graph

    def test_disabled_graph_contributes_nothing(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        store.graph_enabled = False
        assert store._graph_boosts("@keyur") == {}

    def test_a_broken_graph_degrades_instead_of_failing_recall(self, store, graph, monkeypatch):
        """Recall must never fail because the graph is unhappy."""
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")

        def _boom(*a, **k):
            raise RuntimeError("graph exploded")

        monkeypatch.setattr(type(graph), "recall_refs", _boom)
        assert store._graph_boosts("@keyur") == {}
        # And the surrounding retrieval still works.
        assert store.get_semantic_context(query_text="Keyur", cap=800)

    def test_empty_query_costs_nothing(self, store, graph):
        _seeded(store, graph)
        assert store._graph_boosts("") == {}

    def test_evidence_names_the_connecting_entity(self, store, graph):
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani prefers terse", 0.9, "user_explicit")
        evidence = graph.recall_evidence("@keyur", index=store.alias_index)
        assert evidence["user.note.a"] == ["Keyur Golani"]

    def test_evidence_is_empty_without_a_match(self, store, graph):
        _seeded(store, graph)
        assert graph.recall_evidence("unrelated words", index=store.alias_index) == {}

    def test_service_exposes_evidence_and_degrades(self, store, graph):
        from personalclaw.memory_service import MemoryService

        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        service = MemoryService.over_vector_store(store)
        assert service.graph_recall_evidence("@keyur")
        assert service.graph_recall_evidence("") == {}
        store.graph_enabled = False
        assert service.graph_recall_evidence("@keyur") == {}

    def test_graph_hits_ride_the_existing_recall_counter(self, store, graph):
        """§2.1 step 4: the graph feeds `heat()` through record_recall — it must not
        add a parallel promotion path."""
        _seeded(store, graph)
        store.set_semantic("user.note.a", "Keyur Golani", 0.9, "user_explicit")
        before = store.get_semantic("user.note.a")["recall_count"]
        store.record_recall(["user.note.a"])
        assert store.get_semantic("user.note.a")["recall_count"] == before + 1

    def test_a_named_entity_outranks_incidental_word_overlap(self, store, graph):
        """Found by driving real recall: the boost was β·log1p(inbound) ≈ 0.07, which
        LOST to the ~0.1 an unrelated record earns from stopword overlap ("is", "on").
        The record about the person asked about ranked third. Naming an entity is a
        deliberate signal and must clear that noise floor."""
        graph.upsert_entity("Ana Ortiz", "person", aliases=["Sparrow"])
        store.invalidate_alias_index()
        store.set_semantic(
            "user.note.a", "Ana Ortiz owns the billing rewrite", 0.9, "user_explicit"
        )
        store.set_semantic("user.note.z", "coffee machine is on floor three", 0.9, "user_explicit")

        block = store.get_semantic_context(query_text="what is Sparrow working on", cap=900)
        lines = [ln for ln in block.splitlines() if ln.startswith("user.note")]
        assert lines and "billing" in lines[0], f"expected the linked record first, got {lines}"

    def test_a_real_keyword_match_still_beats_the_graph(self, store, graph):
        """The floor must not invert the priority: typed words win."""
        graph.upsert_entity("Ana Ortiz", "person", aliases=["Sparrow"])
        store.invalidate_alias_index()
        store.set_semantic("user.note.a", "Ana Ortiz owns billing", 0.9, "user_explicit")
        store.set_semantic("user.note.z", "coffee machine on floor three", 0.9, "user_explicit")

        block = store.get_semantic_context(query_text="coffee machine floor", cap=900)
        lines = [ln for ln in block.splitlines() if ln.startswith("user.note")]
        assert lines and "coffee" in lines[0]
