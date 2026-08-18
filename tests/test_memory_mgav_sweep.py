"""The end-to-end memory sweep (MEMORY-GRAPH-AND-VAULT — `MGAV-9`'s last clause).

`MGAV-9`'s six other clauses shipped in Session 5; this one did not, and the reason it
did not is recorded in the plan: three of its legs — **volunteer**, **edit-vault**,
**undo** — "exercise MGAV-3/MGAV-6 machinery this atom only surfaces", so they were
never driven together. Driving them by hand once would not have fixed that. What fixes
it is a test that walks the whole chain in ORDER, feeding each leg the previous leg's
output, so a break anywhere between a write and its undo is a red test rather than a
surprise for whoever next opens the panel.

**The volunteer leg is asserted at its CALL SITE, not at the mechanism.**
`test_memory_push_reflex.py` already covers `MemoryService.push_context` and
`memory_push.resolve_candidates` thoroughly — but nothing covered
`context_engine.push_context_block` on its POSITIVE path, and nothing covered
`DefaultContextEngine.assemble` prepending the block at all. So the reflex could have
been silently unhooked from the turn (`if not kwargs.get("blocks_reads")` inverted, the
`pushed` branch dropped, `_push_settings` reading the wrong field) with the whole memory
suite still green. The two `assemble` tests here close that: they drive the seam a real
chat turn drives, and they read the toggle out of a real `config.json`.

**Degradation is asserted with a positive control beside it.** "Every graph surface came
back empty" is exactly what a test with nothing wired reports, so each degradation test
first proves the SAME calls return data on the healthy store, then flips one thing —
`memory.graph_enabled` in config, or the provider — and shows only the graph surfaces
went quiet while recall, the vault and undo kept working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.context_engine import DefaultContextEngine
from personalclaw.memory_service import MemoryService, service_for
from personalclaw.memory_vault import MemoryVault, split_page
from personalclaw.vector_memory import VectorMemoryStore

# The record the whole sweep follows, and the entity it names. One fact, one entity, so
# every leg below can be read as "what happened to THIS fact".
KEY = "project.atlas.cadence"
FACT = "Atlas ships on Fridays"
EDITED = "Atlas ships on Tuesdays"
ENTITY = "Atlas"
ALIAS = "Sparrow"
# A second record that ONLY the graph arm can surface: neither its key nor its text shares
# a word with the query below, and it reaches the entity through the ALIAS. That is what
# makes the recall leg falsifiable — `project.atlas.cadence` would come back on keyword
# overlap alone ("atlas" is in its key), so asserting on it would pass with the graph off
# and prove nothing about §2.1's third arm.
GRAPH_ONLY_KEY = "pref.facet.release.freeze"
GRAPH_ONLY_FACT = f"{ALIAS} freezes in December"
QUERY = f"what about {ENTITY}?"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home AND an isolated workspace.

    Both, deliberately: `config_dir` alone leaves `workspace_dir` unseeded, and the
    vault/consolidation paths that fall through to it would land in the real
    `~/workplace`. The config file is written empty so `AppConfig.load()` reads defaults
    from THIS directory rather than the developer's own settings.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "config.json").write_text("{}\n", encoding="utf-8")
    return tmp_path


def _set_memory_config(home: Path, **fields: Any) -> None:
    """Write `memory.*` keys into the isolated config.json, as the PATCH allowlist does.

    Writing the FILE rather than patching an `AppConfig` object is the point: both
    `VectorMemoryStore.graph_enabled` and `context_engine._push_settings` re-read config
    per call so the Settings toggles are live switches, and only a file write exercises
    that. A monkeypatched config object would pass even if the live read regressed to a
    boot-time capture.
    """
    path = home / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("memory", {}).update(fields)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.fixture()
def store(home):
    """A real record store with NO embedder — the shipped no-API-key posture.

    Deliberately not the usual `embed_fn = lambda t: [1.0, 0.0, 0.0]` stub these suites
    use: a constant embedding scores EVERY record at cosine 1.0, so every recall returns
    everything and the graph arm becomes unobservable. The first draft of this file had
    it, and `test_the_sweep_degrades_cleanly_with_the_graph_disabled` failed with the
    graph-only fact still in the block — the assertion was right and the fixture was
    lying. Without an embedder recall is keyword + graph, which is what §2.1 degrades
    between.
    """
    vs = VectorMemoryStore(db_path=home / "memory.db", embedding_dim=3)
    vs.init()
    return vs


@pytest.fixture()
def svc(store):
    return MemoryService.over_vector_store(store)


class _Builder:
    """The `ContextBuilder` collaborator `assemble` needs, and nothing more.

    A stand-in for the builder, NOT for the code under test: `assemble` +
    `push_context_block` + `service_for` + the store all run for real. `build_message`
    returns the text unchanged so any growth in the assembled message is the reflex's
    block and only the reflex's block.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.conversation_log = None  # no prior turns; the current message is the window

    def get_memory_for(self, cwd: Any, memory_store: Any) -> Any:
        return self._provider

    def build_message(self, text: str, is_new_session: bool, **kwargs: Any) -> tuple[str, None]:
        return (text, None)


class _Projection:
    """A markdown-projection provider carrying an attached vector store.

    The production shape `service_for` discovers a store through (`MemoryService._vs`
    reads `provider.vector_store`), so the service the reflex builds inside
    `push_context_block` is a real one over a real graph. Deliberately without
    `read_preferences`/`search`, which would add the FTS fallback this sweep does not
    need.
    """

    def __init__(self, vs: VectorMemoryStore) -> None:
        self.vector_store = vs


def _write_and_link(svc: MemoryService, store: VectorMemoryStore) -> str:
    """Leg 1+2 of the sweep: declare the entity, write both facts, get them linked.

    `set_semantic` returns a reject tuple rather than raising, so both writes are checked
    here — the key allowlist (`pref.*`/`project.*`/…) silently refuses anything else, and
    a sweep built on a refused write would report a graph with nothing in it as "clean".
    """
    entity_id = svc.graph_add_entity(ENTITY, "project", aliases=[ALIAS])
    store.invalidate_alias_index()
    for key, text in ((KEY, FACT), (GRAPH_ONLY_KEY, GRAPH_ONLY_FACT)):
        rejected = store.set_semantic(key, text, 0.9, "user_explicit")
        assert rejected is None, f"write: {key} was refused — {rejected}"
    return entity_id


def _events_for(store: VectorMemoryStore, key: str) -> list[dict]:
    return [e for e in store.read_events(limit=200) if e.get("memory_key") == key]


def _semantic_value(svc: MemoryService, key: str) -> Any:
    row = svc.get_semantic(key)
    return None if row is None else json.loads(row["value_json"])


def _rewrite_body(page: Path, new_body: str) -> None:
    """Replace a page's BODY, leaving its frontmatter (and its now-stale `source_hash`)
    alone — what a human editing in Obsidian actually does, and what the sync detects.

    `split_page` returns the frontmatter WITHOUT its `---` fences, so they have to be put
    back; writing `block + body` produced a page with no frontmatter at all, which the
    sync correctly declined to treat as an edited record page.
    """
    block, _ = split_page(page.read_text(encoding="utf-8"))
    page.write_text(f"---\n{block}\n---\n\n{new_body.rstrip()}\n", encoding="utf-8")


# ── the sweep ────────────────────────────────────────────────────────────────


def test_the_sweep_runs_write_link_recall_volunteer_editvault_undo_in_order(home, svc, store):
    """`MGAV-9`'s last clause, one leg at a time, each fed the previous leg's output.

    Every assertion carries the leg name, because the failure that matters here is
    "which link in the chain broke", not "something about memory is wrong".
    """
    _set_memory_config(home, graph_enabled=True, push_context=True, push_min_confidence=0.5)

    # ── write ──
    entity_id = _write_and_link(svc, store)
    assert _semantic_value(svc, KEY) == FACT, "write: the fact did not land"

    # ── link ── the write path linked both facts to the entity they name, and typed the
    # edges differently: a `project.atlas.*` key naming a project entity is affiliation,
    # while the alias mention in a `pref.*` key is only a mention.
    backlinks = {b["from_ref"]: b["link_type"] for b in svc.graph_backlinks(entity_id)}
    assert backlinks == {KEY: "same_project", GRAPH_ONLY_KEY: "mentions"}, f"link: {backlinks}"

    # ── recall ── the graph arm surfaces the record whose wording shares NOTHING with the
    # question, and the evidence tag names the entity that got it there.
    recalled = svc.semantic_context(QUERY, cap=1500)
    assert GRAPH_ONLY_FACT in recalled, f"recall: the graph arm surfaced nothing — {recalled!r}"
    evidence = svc.graph_recall_evidence(QUERY)
    assert evidence.get(GRAPH_ONLY_KEY) == [ENTITY], f"recall: no evidence tag — {evidence}"

    # ── volunteer ── the CALL SITE: what a real turn assembles, not `push_context`.
    engine = DefaultContextEngine()
    assembled = engine.assemble(
        _Builder(_Projection(store)),
        f"any news on {ENTITY}?",
        is_new_session=False,
        session_key="sweep",
    )
    assert FACT in assembled.message, f"volunteer: not in the assembled turn — {assembled.message}"
    assert assembled.injected_chars > 0, "volunteer: the block was not counted as injected"

    # ── edit-vault ── mirror out, edit the page as a human would, absorb it back.
    vault = MemoryVault(svc, home / "vault", mode="two_way")
    vault.sync()
    page = home / "vault" / "facts" / f"{KEY}.md"
    assert page.exists() and FACT in page.read_text(encoding="utf-8"), "edit-vault: no page"
    _rewrite_body(page, f"# {KEY}\n\n{EDITED}")
    absorbed = vault.sync()
    assert absorbed["absorbed"] == 1, f"edit-vault: the edit was not detected — {absorbed}"
    assert _semantic_value(svc, KEY) == EDITED, "edit-vault: the edit was not absorbed"

    # ── undo ── the vault edit is a logged memory event, so it is reversible.
    updates = [e for e in _events_for(store, KEY) if e["event_type"] == "update"]
    assert updates, f"undo: the vault edit logged no update event — {_events_for(store, KEY)}"
    ok, msg = store.undo_event(int(updates[0]["id"]))
    assert ok, f"undo: {msg}"
    assert _semantic_value(svc, KEY) == FACT, "undo: the prior value was not restored"


def test_the_push_toggle_gates_the_assembled_turn_live(home, svc, store):
    """`memory.push_context` is a live switch, asserted through `assemble`.

    The positive control is the whole point: flipping the toggle OFF must change the
    assembled message, and the only way to know an "off" result means anything is to
    have seen the same call produce the block when it was on. `_push_settings` re-reads
    config per turn precisely so this works without a restart.
    """
    _set_memory_config(home, graph_enabled=True, push_context=True, push_min_confidence=0.5)
    _write_and_link(svc, store)
    engine = DefaultContextEngine()
    builder = _Builder(_Projection(store))
    text = f"any news on {ENTITY}?"

    on = engine.assemble(builder, text, is_new_session=False, session_key="s")
    assert FACT in on.message, "the reflex was silent with the toggle ON"

    _set_memory_config(home, push_context=False)
    off = engine.assemble(builder, text, is_new_session=False, session_key="s")
    assert off.message == text, f"the toggle did not take effect live — {off.message}"
    assert off.injected_chars == 0


def test_a_temporary_session_gets_no_volunteered_memory(home, svc, store):
    """`blocks_reads` (a temporary session) skips the reflex at the seam.

    Beside the toggle test because they fail differently: this one is the branch in
    `assemble`, and it is the one an "always volunteer" refactor would quietly drop.
    """
    _set_memory_config(home, graph_enabled=True, push_context=True, push_min_confidence=0.5)
    _write_and_link(svc, store)
    engine = DefaultContextEngine()
    builder = _Builder(_Projection(store))
    text = f"any news on {ENTITY}?"

    assert FACT in engine.assemble(builder, text, is_new_session=False).message
    blocked = engine.assemble(builder, text, is_new_session=False, blocks_reads=True)
    assert blocked.message == text, f"a temporary session was volunteered memory — {blocked}"


# ── degradation ──────────────────────────────────────────────────────────────


def test_the_sweep_degrades_cleanly_with_the_graph_disabled(home, svc, store):
    """`memory.graph_enabled: false` silences the graph legs and NOTHING else.

    Structured as before/after around one config write so the empties cannot be vacuous:
    the same six calls run twice, and the assertions say which three were expected to
    change. Recall, the vault and undo are the positive control — a store that had
    simply failed to load would take them down too.
    """
    _set_memory_config(home, graph_enabled=True, push_context=True, push_min_confidence=0.5)
    entity_id = _write_and_link(svc, store)
    engine = DefaultContextEngine()
    builder = _Builder(_Projection(store))
    turn = f"any news on {ENTITY}?"
    assert svc.has_graph and svc.graph_backlinks(entity_id), "control: the graph was never live"
    assert FACT in engine.assemble(builder, turn, is_new_session=False).message

    _set_memory_config(home, graph_enabled=False)

    # The graph legs go quiet — empty shapes, not exceptions.
    assert svc.has_graph is False
    assert svc.entity_graph() == {"nodes": [], "edges": []}
    assert svc.graph_backlinks(entity_id) == []
    assert svc.graph_record_links(f"sem:{KEY}") == []
    assert svc.graph_recall_evidence(turn) == {}
    assert svc.graph_proposals() == []
    assert engine.assemble(builder, turn, is_new_session=False).message == turn

    # Recall DEGRADES rather than breaks: the keyword-reachable fact still comes back, the
    # graph-only one drops out. Both halves matter — "recall still returns something" would
    # pass even if the graph arm had stayed on, and "the graph record is gone" would pass if
    # recall had died entirely.
    degraded = svc.semantic_context(QUERY, cap=1500)
    assert FACT in degraded, f"recall broke with the graph off — {degraded!r}"
    assert GRAPH_ONLY_FACT not in degraded, f"the graph arm still ran — {degraded!r}"
    vault = MemoryVault(svc, home / "vault", mode="two_way")
    vault.sync()
    assert any(
        FACT in p.read_text(encoding="utf-8") for p in (home / "vault").rglob("*.md")
    ), "the vault stopped mirroring with the graph off"
    creates = [e for e in _events_for(store, KEY) if e["event_type"] == "create"]
    assert creates, "the write logged no event"
    assert store.undo_event(int(creates[0]["id"]))[0], "undo broke with the graph off"


def test_a_foreign_provider_reports_no_graph_instead_of_failing(home, store):
    """A provider that is not the record store has no graph — and says so.

    The provider-agnostic core's real degradation case: an app-supplied memory provider
    carries no SQLite entity graph, so `MemoryService._graph_store()` returns None and
    every §7 surface must answer with its empty shape. Asserted against the SAME calls
    on the wired store, so "everything was empty" cannot be the test wiring nothing.
    """
    _set_memory_config(home, graph_enabled=True, push_context=True, push_min_confidence=0.5)
    wired = MemoryService.over_vector_store(store)
    entity_id = _write_and_link(wired, store)
    assert wired.has_graph and wired.graph_backlinks(entity_id), "control: no graph to lose"

    class _ForeignProvider:
        """No `vector_store` attribute — the one thing `_vs` discovers."""

        def read_preferences(self) -> str:
            return FACT

        def search(self, query: str, limit: int = 8) -> list:
            return []

    foreign = service_for(_ForeignProvider())
    assert foreign.has_graph is False
    assert foreign.entity_graph() == {"nodes": [], "edges": []}
    assert foreign.graph_backlinks(entity_id) == []
    assert foreign.graph_record_links(f"sem:{KEY}") == []
    assert foreign.graph_recall_evidence(f"news on {ENTITY}") == {}
    assert foreign.graph_proposals() == []
    assert foreign.slots() == []
    assert foreign.push_context([f"any news on {ENTITY}?"], session_key="s") == ("", [])

    # The seam a real turn goes through, over the foreign provider: no block, no raise.
    engine = DefaultContextEngine()
    turn = f"any news on {ENTITY}?"
    assembled = engine.assemble(_Builder(_ForeignProvider()), turn, is_new_session=False)
    assert assembled.message == turn
