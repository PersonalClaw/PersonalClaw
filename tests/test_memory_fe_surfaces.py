"""The FE-facing memory surfaces MGAV-9 added: entity topology, slots editor, export.

Each of these exists because something in the panel had no way to reach the backend:

* the proposal queue could DECIDE (``POST .../proposals`` shipped with MGAV-1) but could not
  LIST — a decision surface with no readable queue;
* ``graph_recall_evidence`` was written "for the inspect/recall surfaces" and had zero
  production callers, so a record could not say why recall surfaced it;
* the graph canvas drew records, while the Louvain pass partitions ENTITIES — so the picture
  and the topology block the model reads described different graphs;
* the slots primitive had no editor at all, which made a human-owned register machine-only.

The export test is the load-bearing one: the plan sketched an interactive `graph.html`, and
this repo's export posture forbids script in an exported document. The test pins the static
form so nobody "fixes" it back into something that executes.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from personalclaw.memory_graph_export import render_graph_html
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A real vector store on a temp path — never the user's home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    return vs


@pytest.fixture()
def svc(store):
    return MemoryService.over_vector_store(store)


# ── the entity topology (§7.2) ───────────────────────────────────────────────


def test_entity_graph_edges_carry_what_the_filters_need(svc, store):
    """Two entities named by ONE record become an edge carrying its link metadata.

    Without ``link_types``/``provenances``/``confidence`` on the edge the §7.2 filters have
    nothing to filter on — `memory_topology.cooccurrence_edges` computes the same adjacency
    but drops exactly those, which is why this is a separate query rather than a reuse.
    """
    svc.graph_add_entity("Ana", "person")
    svc.graph_add_entity("Atlas", "project")
    store.set_semantic("project.note", "Ana leads Atlas this quarter", 0.9, "user_explicit")

    graph = svc.entity_graph()
    names = {n["name"] for n in graph["nodes"]}
    assert {"Ana", "Atlas"} <= names
    edge = next((e for e in graph["edges"] if e["records"] >= 1), None)
    assert edge is not None, "one record naming both entities must produce one edge"
    assert edge["link_types"], "an edge with no link type cannot be filtered by type"
    assert edge["provenances"], "an edge with no provenance cannot be filtered by provenance"
    assert 0.0 < edge["confidence"] <= 1.0


def test_isolated_entities_stay_in_the_picture(svc, store):
    """An entity nothing links to is the orphan SIGNAL — dropping it would hide it."""
    svc.graph_add_entity("Nobody", "person")
    graph = svc.entity_graph()
    assert any(n["name"] == "Nobody" for n in graph["nodes"])
    assert graph["edges"] == []


def test_entity_graph_is_empty_not_broken_without_a_graph(store):
    """`graph_enabled: false` degrades to an empty answer, not an exception (criterion 10)."""
    store.graph_enabled = False
    svc = MemoryService.over_vector_store(store)
    assert svc.entity_graph() == {"nodes": [], "edges": []}
    assert svc.graph_proposals() == []
    assert svc.graph_record_links("sem:anything") == []


# ── per-record links + evidence (§7.1) ──────────────────────────────────────


def test_record_links_resolve_the_entity_name(svc, store):
    """A link row names the entity, not just its opaque id.

    The id IS the row; the NAME is the evidence tag the inspect view shows. A panel rendering
    `ent_9f2c` would satisfy "shows backlinks" while telling the reader nothing.
    """
    svc.graph_add_entity("Ana", "person")
    store.set_semantic("user.ana", "Ana prefers async standups", 0.9, "user_explicit")

    # `user.*` is an allowlisted semantic prefix — an unallowed key is a rejected no-op write,
    # which would make this test pass vacuously against a store that holds nothing.
    links = svc.graph_record_links("sem:user.ana")
    assert links, "the record mentions a known entity, so it must have links"
    assert links[0]["entity_name"] == "Ana"
    assert links[0]["link_type"]
    assert links[0]["provenance"]


def test_only_the_two_real_kinds_resolve(svc, store):
    """An unrecognized ref prefix returns [] rather than guessing a `from_kind`.

    ``semantic`` and ``episodic`` are the only two values anything writes. Silently mapping
    `lesson:` onto one of them would report "no links" for a record that has them — a wrong
    answer that looks exactly like a right one.
    """
    svc.graph_add_entity("Ana", "person")
    store.set_semantic("user.ana", "Ana prefers async standups", 0.9, "user_explicit")
    assert svc.graph_record_links("lesson:whatever") == []
    assert svc.graph_record_links("user.ana") == []  # no prefix at all
    assert svc.graph_record_links("sem:") == []


# ── the slots editor (§6/§7.1) ──────────────────────────────────────────────


def test_slots_list_includes_unwritten_builtins(svc):
    """Every built-in is listed, materialized or not.

    MGAV-8 keeps built-ins LAZY so a fresh install pays nothing. An editor that listed only
    written rows would show a new user nothing to edit — the register the system will actually
    read would be unreachable until something else wrote to it first.
    """
    slots = {s["name"]: s for s in svc.slots()}
    assert "persona" in slots and "self_model" in slots
    assert slots["persona"]["materialized"] is False
    assert slots["persona"]["lines"] == []
    assert slots["persona"]["cap_chars"] > 0
    assert slots["glossary"]["scope"] == "workspace"


def test_append_then_retire_round_trips_through_the_service(svc):
    """The human write path: append is visible, retire tombstones rather than deletes."""
    svc.slot_append("persona", "answers concisely")
    slots = {s["name"]: s for s in svc.slots()}
    assert slots["persona"]["materialized"] is True
    assert [line["text"] for line in slots["persona"]["lines"]] == ["answers concisely"]

    assert svc.slot_tombstone("persona", "answers concisely") is True
    slots = {s["name"]: s for s in svc.slots()}
    line = slots["persona"]["lines"][0]
    assert line["tombstoned"] is True
    assert line["tombstoned_by"] == "human", "a human tombstone is what makes the guard final"
    assert slots["persona"]["live_count"] == 0


def test_the_editor_writes_as_the_human_so_nothing_re_derives_a_deletion(svc):
    """A retired line cannot be re-added — the resurrection guard, driven from the editor."""
    from personalclaw import memory_slots

    svc.slot_append("self_notes", "user dislikes emoji")
    svc.slot_tombstone("self_notes", "user dislikes emoji")
    svc.slot_append("self_notes", "user dislikes emoji")
    live = [
        line["text"]
        for line in next(s for s in svc.slots() if s["name"] == "self_notes")["lines"]
        if not line["tombstoned"]
    ]
    assert live == [], "re-adding a human-tombstoned line must be a no-op"
    assert memory_slots.SLOT_PREFIX == "slot."


@pytest.mark.asyncio
async def test_an_over_cap_append_is_a_409_carrying_the_trim_proposal(svc, monkeypatch):
    """The cap rejection reaches the UI as a CHOICE, not as a truncation or a bare error.

    MGAV-8's contract is that nothing decides on the user's behalf which of their own lines to
    lose. That only holds if the handler forwards the proposal — a 400 with a message would
    make the editor's only honest move "give up".
    """
    from personalclaw.dashboard.handlers import memory as handlers

    monkeypatch.setattr(handlers, "_get_service", lambda _state: svc)
    cap = next(s for s in svc.slots() if s["name"] == "persona")["cap_chars"]

    request = MagicMock()
    request.match_info = {"name": "persona"}
    request.app = {"state": MagicMock()}

    async def _json():
        return {"text": "z" * (cap + 50)}

    request.json = _json
    resp = await handlers.api_memory_slot_append(request)
    assert resp.status == 409
    body = json.loads(resp.body)
    assert body["proposal"]["over_by"] > 0
    assert body["proposal"]["cap_chars"] == cap
    assert "Nothing was written" in body["proposal"]["message"]


# ── the self-contained export (§7.2) ────────────────────────────────────────

_GRAPH = {
    "nodes": [
        {"id": "e1", "name": "Ana", "entity_type": "person", "community": 0, "inbound_count": 3},
        {"id": "e2", "name": "Atlas", "entity_type": "project", "community": 0, "inbound_count": 2},
        {"id": "e3", "name": "Solo", "entity_type": "tool", "community": None, "inbound_count": 0},
    ],
    "edges": [
        {
            "from": "e1",
            "to": "e2",
            "records": 2,
            "link_types": ["mentions"],
            "provenances": ["extracted"],
            "confidence": 0.9,
        }
    ],
}


def test_the_export_carries_no_script_and_no_remote_reference():
    """The invariant that makes the artifact safe to open years later, offline.

    Asserted through the SAME guard knowledge/reports.py uses (it is imported by the renderer
    and raises), plus a direct check here so the property is visible at the test level rather
    than only as "the renderer did not throw".
    """
    doc = render_graph_html(_GRAPH, generated_at="2026-08-16 12:00 UTC")
    assert "<script" not in doc.lower()
    assert not re.search(r"\son[a-z]+\s*=", doc, re.IGNORECASE), "no inline event handlers"
    assert "http://" not in doc.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in doc


def test_the_export_draws_the_graph_and_embeds_its_data():
    """Static SVG for the picture, verbatim JSON for the data — one file, both halves."""
    doc = render_graph_html(_GRAPH, generated_at="2026-08-16 12:00 UTC")
    assert "<svg" in doc and "<circle" in doc and "<line" in doc
    assert "Ana" in doc and "Atlas" in doc
    island = re.search(r"<!-- personalclaw-memory-graph\n(.*?)\n-->", doc, re.S)
    assert island, "the data island must be present"
    parsed = json.loads(island.group(1))
    assert {n["name"] for n in parsed["nodes"]} == {"Ana", "Atlas", "Solo"}


def test_a_name_containing_a_double_hyphen_cannot_break_out_of_the_island():
    """`--` inside an HTML comment would close it early and spill JSON into the page body.

    Escaped as a JSON escape rather than a lookalike character, so the island stays parseable —
    an unparseable data island is a payload nobody can use, which defeats shipping it.
    """
    graph = {
        "nodes": [
            {
                "id": "e1",
                "name": "weird--name",
                "entity_type": "project",
                "community": 1,
                "inbound_count": 1,
            }
        ],
        "edges": [],
    }
    doc = render_graph_html(graph)
    island = re.search(r"<!-- personalclaw-memory-graph\n(.*?)\n-->", doc, re.S)
    assert island
    assert "--" not in island.group(1), "an unescaped -- would terminate the comment"
    assert json.loads(island.group(1))["nodes"][0]["name"] == "weird--name"


def test_communities_get_distinct_colours_and_unclustered_reads_neutral():
    """Colour BY community — the same partition the topology block describes.

    A renderer that hashed the entity name instead would look plausible and disagree with the
    neighbourhoods the model is told about, which is worse than no colour at all.
    """
    doc = render_graph_html(
        {
            "nodes": [
                {
                    "id": "a",
                    "name": "A",
                    "entity_type": "person",
                    "community": 0,
                    "inbound_count": 1,
                },
                {
                    "id": "b",
                    "name": "B",
                    "entity_type": "person",
                    "community": 1,
                    "inbound_count": 1,
                },
                {
                    "id": "c",
                    "name": "C",
                    "entity_type": "person",
                    "community": None,
                    "inbound_count": 0,
                },
            ],
            "edges": [],
        }
    )
    fills = set(re.findall(r'fill="(hsl\([^"]+\))"', doc))
    assert len(fills) >= 3, f"two communities + unclustered must differ, got {fills}"
    assert "hsl(0 0% 62%)" in fills, "an unclustered node reads neutral, not as a community"
