"""Memory formation — Extract→Gather→Decide, holder attribution, Louvain topology.

MGAV-5. Every test here targets a failure mode that would be INVISIBLE in normal use:

* a supersede that physically deleted the old row looks exactly like a supersede that
  kept it, right up until the user asks what the old value was;
* a contradiction silently resolved looks like a confident answer;
* a non-deterministic Louvain looks like a graph whose topology "keeps evolving";
* an attribution axis that never persists looks like a feature that is simply subtle.

So the assertions are on OUTCOMES (rows, counts, rendered text), never on whether a
field was populated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw import memory_formation as mf
from personalclaw import memory_holder, memory_lint, memory_topology
from personalclaw.vector_memory import VectorMemoryStore

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A real store on tmp_path — never the user's home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    vs.graph_enabled = True
    yield vs


def _raw(store, key: str) -> dict | None:
    """The row for ``key`` bypassing the is_deleted filter — what "still readable" means."""
    row = store.db.execute("SELECT * FROM semantic_memory WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def _cand(index: int, key: str, value: object, **kw) -> mf.Candidate:
    return mf.Candidate(
        index=index, key=key, value=value, confidence=kw.pop("confidence", 0.9), **kw
    )


def _events(store, event_type: str) -> list[dict]:
    return [
        dict(r)
        for r in store.db.execute(
            "SELECT * FROM memory_events WHERE event_type = ? ORDER BY id", (event_type,)
        ).fetchall()
    ]


# ── Clause A1: SUPERSEDE never physically deletes ─────────────────────────────


class TestNoPhysicalDeletes:
    def test_supersede_keeps_the_old_row_readable(self, store):
        """The core data-safety property: the superseded row is still THERE.

        Asserts the row, its chain and its WAL entry — a hard-delete implementation
        passes a naive "the new value is live" check, so that alone proves nothing.
        """
        store.set_semantic("project.x.editor", "prefers the vim editor", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.editor_now", "prefers the emacs editor")])
        assert cands[0].overlaps, "Gather did not see the collision — premise broken"
        decisions = {
            0: mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="project.x.editor")
        }
        report = mf.apply_decisions(store, cands, decisions, source="test")

        assert report.superseded == 1
        old = _raw(store, "project.x.editor")
        assert old is not None, "the superseded row was PHYSICALLY DELETED"
        assert old["value_json"] == '"prefers the vim editor"', "the old VALUE was destroyed"
        assert old["is_deleted"] == 1
        assert old["superseded_by"] == "project.x.editor_now"
        assert old["invalidated_at"]
        chain = store.get_supersession_chain("project.x.editor")
        assert [c["key"] for c in chain] == ["project.x.editor", "project.x.editor_now"]
        assert len(_events(store, "supersede")) == 1

    def test_row_count_never_drops_across_a_formation_pass(self, store):
        """A formation pass may add rows; it must never remove one.

        The population-level version of the property above, so a supersede that deletes
        a DIFFERENT row than the one it names is also caught.
        """
        for key, val in (("project.x.a", "1"), ("project.x.b", "2"), ("project.x.c", "3")):
            store.set_semantic(key, val, 0.9, "seed")
        before = store.db.execute("SELECT COUNT(*) AS n FROM semantic_memory").fetchone()["n"]
        cands = mf.gather(store, [_cand(0, "project.x.d", "4"), _cand(1, "project.x.e", "5")])
        decisions = {
            0: mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="project.x.a"),
            1: mf.Decision(index=1, verdict=mf.VERDICT_SUPERSEDE, target="project.x.b"),
        }
        mf.apply_decisions(store, cands, decisions, source="test")
        after = store.db.execute("SELECT COUNT(*) AS n FROM semantic_memory").fetchone()["n"]
        assert after == before + 2, "a formation pass removed rows from the table"

    def test_extract_phase_delete_is_a_soft_tombstone(self, store):
        """Even the extract-phase `delete: true` leaves the row readable."""
        store.set_semantic("user.pet_name", "Rex", 0.9, "seed")
        cands = [_cand(0, "user.pet_name", None, delete=True)]
        mf.apply_decisions(store, cands, cands and {}, source="test")
        row = _raw(store, "user.pet_name")
        assert row is not None and row["value_json"] == '"Rex"'
        assert row["is_deleted"] == 1


# ── Clause A2: unsure ⇒ keep BOTH, flagged in lint ────────────────────────────


class TestKeepBothConflicts:
    def test_unsure_supersede_keeps_both_rows_and_flags_them(self, store):
        store.set_semantic("project.x.status", "on track", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.state", "slipping")])
        decisions = {
            0: mf.Decision(
                index=0,
                verdict=mf.VERDICT_SUPERSEDE,
                target="project.x.status",
                unsure=True,
                reason="both could be current",
            )
        }
        report = mf.apply_decisions(store, cands, decisions, source="test")

        assert report.superseded == 0, "an unsure verdict retired a row anyway"
        assert store.get_semantic("project.x.status") is not None
        assert store.get_semantic("project.x.state") is not None
        assert report.conflicts == [("project.x.state", "project.x.status")]
        flagged = mf.conflicts(store)
        assert len(flagged) == 1
        assert flagged[0]["reason"] == "both could be current"

    def test_a_kept_both_conflict_surfaces_in_the_lint(self, store):
        store.set_semantic("project.x.status", "on track", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.state", "slipping")])
        mf.apply_decisions(
            store,
            cands,
            {
                0: mf.Decision(
                    index=0, verdict=mf.VERDICT_SUPERSEDE, target="project.x.status", unsure=True
                )
            },
            source="test",
        )
        report = memory_lint.lint_memory(store)
        keep_both = [f for f in report.flags if f["check"] == "keep_both"]
        assert keep_both, "an undecided contradiction was invisible in the lint"
        assert keep_both[0]["key"] == "project.x.state"
        assert "project.x.status" in keep_both[0]["detail"]

    def test_the_conflict_is_recorded_in_the_wal_too(self, store):
        """The graph edge is what the lint reads; the WAL is what survives graph-off."""
        store.set_semantic("project.x.a", "1", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.b", "2")])
        mf.apply_decisions(
            store,
            cands,
            {
                0: mf.Decision(
                    index=0, verdict=mf.VERDICT_SUPERSEDE, target="project.x.a", unsure=True
                )
            },
            source="test",
        )
        wal = _events(store, mf.CONFLICT_EVENT)
        assert len(wal) == 1
        assert wal[0]["memory_key"] == "project.x.b" and wal[0]["old_value"] == "project.x.a"

    def test_a_resolved_conflict_stops_being_flagged(self, store):
        """A flag that outlives its cause trains the user to ignore the lint."""
        store.set_semantic("project.x.a", "1", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.b", "2")])
        mf.apply_decisions(
            store,
            cands,
            {
                0: mf.Decision(
                    index=0, verdict=mf.VERDICT_SUPERSEDE, target="project.x.a", unsure=True
                )
            },
            source="test",
        )
        assert len(mf.conflicts(store)) == 1
        store.supersede_semantic("project.x.a", "project.x.b", "user_explicit")
        assert mf.conflicts(store) == []


# ── Clause A3: verdict mapping + the ONE added call ───────────────────────────


class TestVerdictMapping:
    def test_noop_writes_nothing(self, store):
        store.set_semantic("project.x.a", "1", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.a2", "1")])
        report = mf.apply_decisions(
            store, cands, {0: mf.Decision(index=0, verdict=mf.VERDICT_NOOP)}, source="test"
        )
        assert report.noop == 1 and report.added == 0
        assert store.get_semantic("project.x.a2") is None

    def test_update_writes_onto_the_target_key(self, store):
        store.set_semantic("project.x.editor", "vim", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "project.x.text_editor", "emacs")])
        report = mf.apply_decisions(
            store,
            cands,
            {0: mf.Decision(index=0, verdict=mf.VERDICT_UPDATE, target="project.x.editor")},
            source="test",
        )
        assert report.updated == 1
        assert json.loads(store.get_semantic("project.x.editor")["value_json"]) == "emacs"
        assert store.get_semantic("project.x.text_editor") is None

    def test_no_verdict_falls_back_to_add(self, store):
        """Fail-safe: a missing/garbled verdict must never lose the memory."""
        cands = mf.gather(store, [_cand(0, "pref.new", "x")])
        report = mf.apply_decisions(store, cands, {}, source="test")
        assert report.added == 1
        assert store.get_semantic("pref.new") is not None

    def test_an_unknown_verdict_is_discarded_not_obeyed(self, store):
        cands = [_cand(0, "pref.new", "x")]
        parsed = mf.parse_decisions({"verdicts": [{"index": 0, "verdict": "DELETE"}]}, cands)
        assert parsed == {}, "an out-of-vocabulary verdict was accepted"

    def test_supersede_of_an_unknown_target_degrades_to_add(self, store):
        cands = mf.gather(store, [_cand(0, "pref.new", "x")])
        final = mf.adjudicate(
            cands[0], mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="pref.ghost")
        )
        assert final.verdict == mf.VERDICT_ADD

    def test_no_overlaps_means_no_decide_call_at_all(self, store):
        """The "one extra CHEAP call" claim: no collisions ⇒ no prompt is even built."""
        cands = mf.gather(store, [_cand(0, "pref.brand_new_thing", "x")])
        assert cands[0].overlaps == []
        assert mf.build_decide_prompt(cands) == ""

    def test_decide_prompt_carries_the_overlaps(self, store):
        store.set_semantic("pref.editor", "vim", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "pref.editor", "emacs")])
        assert cands[0].overlaps[0].why == "same_key"
        prompt = mf.build_decide_prompt(cands)
        assert "SUPERSEDE" in prompt and "pref.editor" in prompt and "same_key" in prompt


class TestGather:
    def test_same_key_is_always_the_first_overlap(self, store):
        store.set_semantic("pref.editor", "vim editor choice", 0.9, "seed")
        store.set_semantic("pref.other", "vim editor choice", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "pref.editor", "emacs editor choice")])
        assert cands[0].overlaps[0].key == "pref.editor"
        assert cands[0].overlaps[0].why == "same_key"

    def test_keyword_overlap_finds_a_differently_keyed_duplicate(self, store):
        store.set_semantic("pref.editor", "prefers the vim editor", 0.9, "seed")
        cands = mf.gather(store, [_cand(0, "pref.text_editor", "prefers the vim editor")])
        assert [o.key for o in cands[0].overlaps] == ["pref.editor"]
        assert cands[0].overlaps[0].why == "keyword"

    def test_gather_is_deterministic(self, store):
        for i in range(6):
            store.set_semantic(f"pref.k{i}", "prefers the vim editor daily", 0.9, "seed")
        runs = [
            [
                o.key
                for o in mf.gather(store, [_cand(0, "pref.new", "prefers the vim editor daily")])[
                    0
                ].overlaps
            ]
            for _ in range(4)
        ]
        assert len(set(map(tuple, runs))) == 1


# ── Clause B: holder attribution ──────────────────────────────────────────────


class TestHolderAxis:
    def test_claim_prefix_is_allowlisted(self, store):
        assert store.set_semantic("claim.deploy_slips", "the deploy slips", 0.9, "seed") is None
        assert store.get_semantic("claim.deploy_slips") is not None

    def test_weight_is_quantized_and_capped_per_holder_class(self):
        assert memory_holder.normalize_weight("external", 0.93) == pytest.approx(0.55)
        assert memory_holder.normalize_weight("user", 0.93) == pytest.approx(0.75)
        assert memory_holder.normalize_weight("person:e-1", 1.0) == pytest.approx(0.75)
        # On the 0.05 grid, under the cap.
        assert memory_holder.normalize_weight("external", 0.42) == pytest.approx(0.40)
        assert memory_holder.normalize_weight("user", 0.31) == pytest.approx(0.30)

    def test_a_plain_fact_is_never_re_weighted(self, store):
        """Introducing the axis must not down-rank a row that never used it."""
        store.set_semantic("pref.a", "1", 0.9, "seed")
        row = _raw(store, "pref.a")
        assert row["holder"] == ""
        assert row["weight"] == pytest.approx(1.0)
        assert memory_holder.render_fact_line("pref.a", "1") == "pref.a: 1"

    def test_a_capped_weight_is_stored_capped(self, store):
        store.set_semantic("claim.x", "y", 0.9, "seed", holder="external", weight=0.95)
        row = _raw(store, "claim.x")
        assert row["holder"] == "external"
        assert row["weight"] == pytest.approx(0.55)

    def test_a_plain_rewrite_does_not_erase_an_existing_holder(self, store):
        """`holder=None` means "don't touch" — not "convert this claim to a fact"."""
        store.set_semantic("claim.x", "y", 0.9, "seed", holder="external", weight=0.4)
        store.set_semantic("claim.x", "z", 0.95, "seed")
        row = _raw(store, "claim.x")
        assert row["holder"] == "external" and row["weight"] == pytest.approx(0.4)

    def test_the_fact_block_renders_the_attribution_and_the_fence(self, store):
        alex = store.graph.upsert_entity("Alex", "person")
        store.invalidate_alias_index()
        store.set_semantic("pref.plain", "kept", 0.9, "seed")
        store.set_semantic(
            "claim.deploy", "the deploy slips", 0.9, "seed", holder=f"person:{alex}", weight=0.4
        )
        block = store.get_l1_manifest()
        assert "claim.deploy: the deploy slips [Alex believes, weight 0.40]" in block
        assert "pref.plain: kept\n" in block, "a plain fact's rendering changed"
        assert "is a CLAIM someone holds" in block, "an attributed claim rendered with no fence"

    def test_the_fence_is_absent_when_nothing_is_attributed(self, store):
        store.set_semantic("pref.plain", "kept", 0.9, "seed")
        assert "is a CLAIM someone holds" not in store.get_l1_manifest()

    def test_query_scored_fact_block_also_attributes(self, store):
        store.set_semantic("claim.deploy", "the deploy slips", 0.9, "seed", holder="external")
        block = store.get_semantic_context(query_text="deploy")
        assert "[reported externally, weight" in block

    def test_the_axis_off_persists_no_holder(self, store):
        """The flag gates the AXIS, not just its display."""
        cands = mf.candidates_from_extract(
            [
                {
                    "key": "claim.x",
                    "value": "y",
                    "confidence": 0.9,
                    "holder": "external",
                    "weight": 0.5,
                }
            ],
            holder_attribution=False,
            limit=20,
        )
        assert cands[0].holder == ""
        mf.apply_decisions(store, cands, {}, source="test", holder_attribution=False)
        assert _raw(store, "claim.x")["holder"] == ""

    def test_the_axis_on_persists_the_holder(self, store):
        cands = mf.candidates_from_extract(
            [
                {
                    "key": "claim.x",
                    "value": "y",
                    "confidence": 0.9,
                    "holder": "external",
                    "weight": 0.5,
                }
            ],
            holder_attribution=True,
            limit=20,
        )
        mf.apply_decisions(store, cands, {}, source="test", holder_attribution=True)
        row = _raw(store, "claim.x")
        assert row["holder"] == "external" and row["weight"] == pytest.approx(0.5)


class TestHolderPrecedenceAtTheDecisionPoint:
    """§4.2's real guarantee: precedence decides an OUTCOME, not a ranking."""

    def _competing_pair(self, store, *, incumbent_holder: str, challenger_holder: str):
        store.set_semantic(
            "claim.ship_date", "ships Friday", 0.9, "seed", holder=incumbent_holder, weight=0.7
        )
        cands = mf.gather(
            store,
            [
                _cand(
                    0,
                    "claim.ship_date_alt",
                    "slips to Monday",
                    holder=challenger_holder,
                    weight=0.5,
                )
            ],
        )
        decisions = {
            0: mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="claim.ship_date")
        }
        return mf.apply_decisions(store, cands, decisions, source="test", holder_attribution=True)

    def test_an_external_claim_cannot_supersede_a_user_statement(self, store):
        report = self._competing_pair(store, incumbent_holder="user", challenger_holder="external")
        assert report.superseded == 0, "an outside rumour retired what the user said"
        assert store.get_semantic("claim.ship_date") is not None
        assert report.conflicts == [("claim.ship_date_alt", "claim.ship_date")]

    def test_a_user_statement_does_supersede_an_external_claim(self, store):
        """The vacuity guard: precedence must not block EVERY supersede."""
        report = self._competing_pair(store, incumbent_holder="external", challenger_holder="user")
        assert report.superseded == 1
        assert store.get_semantic("claim.ship_date") is None
        assert _raw(store, "claim.ship_date")["superseded_by"] == "claim.ship_date_alt"
        assert report.conflicts == []

    def test_equal_precedence_leaves_the_models_verdict_alone(self, store):
        report = self._competing_pair(store, incumbent_holder="", challenger_holder="assistant")
        assert report.superseded == 1

    def test_precedence_ordering_is_user_over_compiled_over_external(self):
        assert memory_holder.precedence("user") > memory_holder.precedence("")
        assert memory_holder.precedence("") == memory_holder.precedence("assistant")
        assert memory_holder.precedence("assistant") > memory_holder.precedence("external")
        assert memory_holder.precedence("assistant") > memory_holder.precedence("person:e-1")


# ── Clause C: deterministic seeded Louvain + the topology block ───────────────

#: A co-occurrence graph whose partition genuinely DEPENDS on the visit order — found by
#: sweeping random graphs (dense enough that local moving has real choices). A clean
#: two-cluster fixture would make the determinism tests vacuous: they would pass with the
#: seed removed entirely.
_SEED_SENSITIVE_RECORDS = [
    (0, 3),
    (0, 3),
    (0, 4),
    (0, 4),
    (0, 5),
    (0, 5),
    (0, 6),
    (1, 2),
    (1, 4),
    (1, 4),
    (1, 5),
    (2, 3),
    (2, 3),
    (2, 6),
    (2, 7),
    (4, 5),
    (4, 5),
    (5, 7),
]


def _seed_graph(store, records=_SEED_SENSITIVE_RECORDS, n=8):
    graph = store.graph
    for i in range(n):
        graph.upsert_entity(f"Ent{i}", "project", entity_id=f"e-{i:02d}")
    for idx, (a, b) in enumerate(records):
        for node in (a, b):
            graph.add_link(
                from_kind="semantic",
                from_ref=f"rec{idx}",
                to_entity=f"e-{node:02d}",
                link_type="mentions",
            )
    return store


class TestLouvainDeterminism:
    def test_the_same_graph_yields_the_same_communities_across_runs(self, store):
        _seed_graph(store)
        first = memory_topology.detect_communities(store.db)
        for _ in range(5):
            assert memory_topology.detect_communities(store.db) == first

    def test_the_seed_is_load_bearing(self, store):
        """Vacuity guard for the two determinism tests.

        If no seed changed the partition on this graph, "deterministic BECAUSE seeded"
        would be an unfalsifiable claim and the run-to-run test would prove nothing.
        """
        _seed_graph(store)
        partitions = {
            tuple(sorted(memory_topology.detect_communities(store.db, seed=s).items()))
            for s in range(20)
        }
        assert len(partitions) > 1, (
            "no seed changed the partition — the determinism tests above are vacuous "
            "and the fixture graph needs more structure"
        )

    def test_determinism_holds_across_PROCESSES(self, store, tmp_path):
        """Two interpreters, two hash seeds, one answer.

        The in-process test cannot catch a set/dict iteration order leak, because one
        process has ONE PYTHONHASHSEED. This is the test that can.
        """
        _seed_graph(store)
        db_path = str(store._db_path)
        code = (
            "import json;"
            "from pathlib import Path;"
            "from personalclaw.vector_memory import VectorMemoryStore;"
            "from personalclaw import memory_topology as mt;"
            f"vs=VectorMemoryStore(db_path=Path({db_path!r}), embedding_dim=3);"
            "vs.init();vs.graph_enabled=True;"
            "print(json.dumps(sorted(mt.detect_communities(vs.db).items())))"
        )
        outs = []
        for hashseed in ("0", "12345"):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": SRC, "PYTHONHASHSEED": hashseed, "PATH": "/usr/bin:/bin"},
                cwd=str(tmp_path),
            )
            assert proc.returncode == 0, proc.stderr
            outs.append(proc.stdout.strip().splitlines()[-1])
        assert outs[0] == outs[1], "communities depend on the interpreter's hash seed"

    def test_communities_are_canonically_numbered(self, store):
        """Community 0 is the largest — so the numbers mean something to a reader."""
        graph = store.graph
        for name in ("A", "B", "C", "D", "E"):
            graph.upsert_entity(f"Ent{name}", "project", entity_id=f"e-{name}")
        for idx, pair in enumerate([("A", "B"), ("B", "C"), ("A", "C"), ("D", "E"), ("D", "E")]):
            for node in pair:
                graph.add_link(
                    from_kind="semantic",
                    from_ref=f"r{idx}",
                    to_entity=f"e-{node}",
                    link_type="mentions",
                )
        communities = memory_topology.detect_communities(store.db)
        assert communities["e-A"] == communities["e-B"] == communities["e-C"] == 0
        assert communities["e-D"] == communities["e-E"] == 1

    def test_a_clean_two_cluster_graph_is_actually_split(self, store):
        """Vacuity guard on the detector itself: it must not return one blob."""
        graph = store.graph
        for name in "PQRST":
            graph.upsert_entity(f"Ent{name}", "project", entity_id=f"e-{name}")
        for idx, pair in enumerate([("P", "Q"), ("Q", "R"), ("P", "R"), ("S", "T"), ("S", "T")]):
            for node in pair:
                graph.add_link(
                    from_kind="semantic",
                    from_ref=f"r{idx}",
                    to_entity=f"e-{node}",
                    link_type="mentions",
                )
        assert len(set(memory_topology.detect_communities(store.db).values())) == 2


class TestTopologyPersistenceAndBlock:
    def test_communities_are_written_into_mem_link_stats(self, store):
        _seed_graph(store)
        written = memory_topology.write_communities(store)
        assert written == 8
        rows = store.db.execute(
            "SELECT entity_id, community FROM mem_link_stats ORDER BY entity_id"
        ).fetchall()
        assert all(r["community"] is not None for r in rows)
        assert len(rows) == 8

    def test_writing_communities_is_idempotent(self, store):
        _seed_graph(store)
        memory_topology.write_communities(store)
        before = {
            r["entity_id"]: r["community"]
            for r in store.db.execute("SELECT entity_id, community FROM mem_link_stats").fetchall()
        }
        memory_topology.write_communities(store)
        after = {
            r["entity_id"]: r["community"]
            for r in store.db.execute("SELECT entity_id, community FROM mem_link_stats").fetchall()
        }
        assert before == after

    def test_the_block_is_bounded_and_names_communities(self, store):
        graph = store.graph
        # Six 3-cliques → six communities, more than the block may name.
        for cluster in range(6):
            ids = [f"e-{cluster}{i}" for i in range(3)]
            for eid in ids:
                graph.upsert_entity(f"Entity{eid}", "project", entity_id=eid)
            for idx, (a, b) in enumerate([(0, 1), (1, 2), (0, 2)]):
                for node in (ids[a], ids[b]):
                    graph.add_link(
                        from_kind="semantic",
                        from_ref=f"r{cluster}-{idx}",
                        to_entity=node,
                        link_type="mentions",
                    )
        memory_topology.write_communities(store)
        block = memory_topology.topology_block(store.db)
        assert block
        assert len(block) <= memory_topology.TOPOLOGY_BLOCK_MAX_CHARS
        assert block.count("\n") - 2 <= memory_topology.TOPOLOGY_MAX_COMMUNITIES + 1

    def test_no_block_when_the_graph_has_fewer_than_two_communities(self, store):
        graph = store.graph
        for name in ("A", "B"):
            graph.upsert_entity(f"Ent{name}", "project", entity_id=f"e-{name}")
        for node in ("e-A", "e-B"):
            graph.add_link(
                from_kind="semantic", from_ref="r0", to_entity=node, link_type="mentions"
            )
        memory_topology.write_communities(store)
        assert memory_topology.topology_block(store.db) == ""

    def test_block_reads_the_persisted_column_not_a_recomputation(self, store):
        """The block and the visualization must agree, so both read one column."""
        _seed_graph(store)
        assert (
            memory_topology.topology_block(store.db) == ""
        ), "block rendered with no persisted communities"
        memory_topology.write_communities(store)
        assert memory_topology.topology_block(store.db)


class TestTopologyInjectionGate:
    def _service(self, store):
        from personalclaw.memory_service import MemoryService

        return MemoryService.over_vector_store(store)

    def test_the_block_is_off_by_default(self, store, monkeypatch):
        _seed_graph(store)
        memory_topology.write_communities(store)
        assert self._service(store).topology_block() == ""

    def test_the_toggle_admits_the_block_into_get_context(self, store, monkeypatch):
        from personalclaw.config.loader import AppConfig

        _seed_graph(store)
        memory_topology.write_communities(store)
        store.set_semantic("pref.a", "1", 0.9, "seed")

        cfg = AppConfig.load()
        cfg.memory.graph_topology_in_context = True
        monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: cfg))

        svc = self._service(store)
        assert "Memory topology" in svc.topology_block()
        assert "Memory topology" in svc.get_context(l1_manifest=True)

    def test_graph_off_means_no_block_even_with_the_toggle_on(self, store, monkeypatch):
        from personalclaw.config.loader import AppConfig

        _seed_graph(store)
        memory_topology.write_communities(store)
        cfg = AppConfig.load()
        cfg.memory.graph_topology_in_context = True
        monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: cfg))
        store.graph_enabled = False
        assert self._service(store).topology_block() == ""


# ── The consolidation seam: exactly ONE added structured call ─────────────────


class TestConsolidationSeam:
    def _consolidator(self, store, tmp_path):
        from personalclaw.history import HistoryConsolidator

        log = MagicMock()
        log.get_unconsolidated = MagicMock(
            return_value=([{"role": "user", "content": "I use emacs now", "ts": "2026-08-15"}], 1)
        )
        log.get_metadata = MagicMock(return_value={})
        memory = MagicMock()
        memory.read_preferences = MagicMock(return_value="")
        memory.read_projects = MagicMock(return_value="")
        return HistoryConsolidator(
            log=log, memory=memory, sessions=None, vector_store=store, migrated=True
        )

    @pytest.mark.asyncio
    async def test_a_colliding_candidate_costs_exactly_one_extra_call(self, store, tmp_path):
        """Extract + Decide == 2 calls. A third would be a redesign, not this atom."""
        store.set_semantic("pref.editor", "vim", 0.9, "seed")
        consolidator = self._consolidator(store, tmp_path)
        responses = [
            {"semantic": [{"key": "pref.editor", "value": "emacs", "confidence": 0.9}]},
            {"verdicts": [{"index": 0, "verdict": "UPDATE", "target": "pref.editor"}]},
        ]
        calls: list[str] = []

        async def fake_llm(prompt: str):
            calls.append(prompt)
            return responses[len(calls) - 1] if len(calls) <= len(responses) else None

        consolidator._call_llm = fake_llm  # type: ignore[assignment]
        await consolidator._consolidate_locked("k", include_history=False)

        assert len(calls) == 2, f"expected extract+decide, got {len(calls)} model calls"
        assert "SUPERSEDE" in calls[1], "the second call was not the Decide prompt"
        assert json.loads(store.get_semantic("pref.editor")["value_json"]) == "emacs"

    @pytest.mark.asyncio
    async def test_a_non_colliding_candidate_costs_no_extra_call(self, store, tmp_path):
        consolidator = self._consolidator(store, tmp_path)
        calls: list[str] = []

        async def fake_llm(prompt: str):
            calls.append(prompt)
            return {"semantic": [{"key": "pref.brand_new", "value": "x", "confidence": 0.9}]}

        consolidator._call_llm = fake_llm  # type: ignore[assignment]
        await consolidator._consolidate_locked("k", include_history=False)
        assert len(calls) == 1
        assert store.get_semantic("pref.brand_new") is not None

    @pytest.mark.asyncio
    async def test_a_failed_decide_still_writes_the_memories(self, store, tmp_path):
        """The degradation that matters: adjudication is optional, the facts are not."""
        store.set_semantic("pref.editor", "vim", 0.9, "seed")
        consolidator = self._consolidator(store, tmp_path)
        calls: list[str] = []

        async def fake_llm(prompt: str):
            calls.append(prompt)
            if len(calls) == 1:
                return {
                    "semantic": [
                        {"key": "pref.editor_new", "value": "emacs vim", "confidence": 0.9}
                    ]
                }
            return None  # Decide failed

        consolidator._call_llm = fake_llm  # type: ignore[assignment]
        await consolidator._consolidate_locked("k", include_history=False)
        assert len(calls) == 2
        assert store.get_semantic("pref.editor_new") is not None
        assert store.get_semantic("pref.editor") is not None, "a failed Decide retired a row"
