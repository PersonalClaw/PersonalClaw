"""A recall says HOW it ranked, once, in one vocabulary (issue 521).

The defect: the Memory Studio "ranked deep recall" tab rendered identically whether
the vector arm scored the results or a keyword fallback did, because
``/api/memory/recall`` returned only ``{result, query, deep}`` — and ``deep`` is the
REQUEST's depth flag echoed back, not a property of the recall. Measured against a
real gateway on an isolated home, the payload key set was byte-identical with an
embedding model bound and with none: ``['deep', 'query', 'result']`` both times.

Three rails here, in increasing order of what they'd let through:

1. **Derived capability census** — every field of ``MemoryCapabilities`` is
   classified by ``memory_ranking`` (recall-relevant axis, or explicitly not), so a
   new capability cannot land with recall silently unable to describe it.
2. **Derived surface census** — every handler in the memory API that calls a
   ranking scorer must return ``ranking``. Derived by parsing the module's AST for
   the scorer calls, not from a hand-written list of endpoint names.
3. **One vocabulary** — the authored degradation clause exists in exactly one
   source file across the shipped tree.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.memory_ranking import (
    AXES,
    FALLS_BACK_CLAUSE,
    MODES,
    NON_RANKING_CAPABILITIES,
    RecallRanking,
    ranking_payload,
    recall_ranking,
)
from personalclaw.memory_record import MemoryCapabilities

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
_WEB = pathlib.Path(__file__).resolve().parents[1] / "web" / "src"
_HANDLER = _SRC / "dashboard" / "handlers" / "memory.py"

#: The service/store methods that DO the ranking. A handler that calls one of these is
#: presenting ranked results and therefore owes the disclosure.
RANKING_SCORERS = frozenset(
    {
        "semantic_context",
        "get_semantic_context",
        "recall_with_provenance",
        "get_episodic_context",
        "rank_episodic",
        "search_episodic",
    }
)


# ── rail 1: the axis table is derived from MemoryCapabilities ─────────────────


def test_every_capability_field_is_classified():
    """Adding a capability must force a decision about what it means for recall."""
    fields = {f.name for f in dataclasses.fields(MemoryCapabilities)}
    # Vacuity floor: a census over an empty field set would pass while checking nothing.
    assert len(fields) >= 5, f"MemoryCapabilities looks empty — census is vacuous: {fields}"
    assert {"vector", "entity_graph", "full_text_search"} <= fields

    classified = {a.field for a in AXES} | set(NON_RANKING_CAPABILITIES)
    unclassified = fields - classified
    assert not unclassified, (
        f"MemoryCapabilities field(s) {sorted(unclassified)} are not classified in "
        "personalclaw/memory_ranking.py. Either add an _Axis (the capability changes how a "
        "recall ranks, so every surface must be able to say so) or add it to "
        "NON_RANKING_CAPABILITIES with the reason it does not."
    )
    invented = classified - fields
    assert not invented, f"memory_ranking classifies non-existent capabilities: {sorted(invented)}"


def test_every_axis_is_a_real_dataclass_field_on_recall_ranking():
    """The wire shape is derived from the axis table, not typed twice."""
    ranking_fields = {f.name for f in dataclasses.fields(RecallRanking)}
    assert {a.field for a in AXES} == ranking_fields


# ── the disclosure itself, in every state ─────────────────────────────────────


def test_full_capability_reads_as_semantic_and_not_degraded():
    r = recall_ranking(MemoryCapabilities(vector=True, full_text_search=True, entity_graph=True))
    assert r.mode == "semantic"
    assert r.degraded is False
    assert FALLS_BACK_CLAUSE not in r.summary
    assert "semantic similarity" in r.summary


def test_no_embedder_reads_as_keyword_and_names_the_missing_model():
    """The measured production state: a store with no embed_fn still returns results."""
    r = recall_ranking(MemoryCapabilities(vector=False, full_text_search=True, entity_graph=True))
    assert r.mode == "keyword"
    assert r.degraded is True
    assert FALLS_BACK_CLAUSE in r.summary
    assert "no embedding model is bound" in r.summary


def test_graph_off_is_disclosed_even_when_the_vector_arm_ran():
    r = recall_ranking(MemoryCapabilities(vector=True, full_text_search=True, entity_graph=False))
    assert r.mode == "semantic"
    assert r.degraded is True
    assert "entity graph is off" in r.summary
    # A store that DID rank semantically must not claim it fell back to search alone.
    assert FALLS_BACK_CLAUSE not in r.summary


def test_no_retrieval_arm_at_all_reads_as_unranked():
    r = recall_ranking(MemoryCapabilities(vector=False, full_text_search=False, entity_graph=False))
    assert r.mode == "unranked"
    assert r.label == "no ranking available"
    # "falls back to search alone" would be a lie: there is no search either.
    assert FALLS_BACK_CLAUSE not in r.summary


def test_mode_is_a_member_of_the_closed_vocabulary_in_every_combination():
    seen = set()
    for vec in (True, False):
        for fts in (True, False):
            for graph in (True, False):
                r = recall_ranking(
                    MemoryCapabilities(vector=vec, full_text_search=fts, entity_graph=graph)
                )
                assert r.mode in MODES
                assert r.summary and r.summary[0].isupper() and r.summary.endswith(".")
                seen.add(r.mode)
    assert seen == set(MODES), f"some mode is unreachable from real capabilities: {seen}"


def test_payload_carries_the_axes_the_mode_and_the_sentence():
    d = ranking_payload(MemoryCapabilities(vector=False, full_text_search=True, entity_graph=False))
    assert d["vector"] is False and d["full_text_search"] is True and d["entity_graph"] is False
    assert d["mode"] == "keyword" and d["degraded"] is True
    assert isinstance(d["summary"], str) and isinstance(d["label"], str)


# ── rail 2: every ranked surface reports it (derived from the AST) ────────────


def _handlers_that_rank() -> dict[str, str]:
    """``{handler name: source}`` for each memory handler that calls a ranking scorer."""
    source = _HANDLER.read_text()
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("api_"):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in RANKING_SCORERS
            ):
                out[node.name] = ast.get_source_segment(source, node) or ""
                break
    return out


def test_every_handler_that_ranks_returns_the_ranking():
    ranked = _handlers_that_rank()
    # Vacuity floor: if the AST walk finds nothing, the rail passes while checking nothing.
    assert len(ranked) >= 3, f"scorer census found too few handlers to be real: {sorted(ranked)}"
    assert "api_memory_recall" in ranked, "the issue-521 endpoint fell out of the census"
    # The needle is the OWNER CALL, not the `"ranking"` key. Mutation-checked: with the
    # key alone, reverting api_memory_recall to its pre-fix payload still passed, because
    # the blocked-read branch's `"ranking": None` satisfied the substring — the census was
    # green while the surface was back to lying.
    silent = [name for name, src in ranked.items() if "_ranking_payload(" not in src]
    assert not silent, (
        f"{silent} present ranked results without saying how they ranked. Add "
        '`"ranking": _ranking_payload(svc)` to the response — that is the whole of '
        "issue 521: a ranked list that renders identically degraded and healthy."
    )


# ── rail 3: exactly one vocabulary for the degradation ───────────────────────


def test_the_degradation_clause_lives_in_exactly_one_file():
    owners = sorted(
        p.relative_to(_SRC).as_posix()
        for p in _SRC.rglob("*.py")
        if FALLS_BACK_CLAUSE in p.read_text(errors="ignore")
    )
    assert owners == ["memory_ranking.py"], (
        f"the recall-degradation clause is authored in {owners}. It must exist only in "
        "personalclaw/memory_ranking.py — every surface reads the composed sentence from "
        "there, which is why the Recall tab and the entity-graph section can no longer "
        "disagree about what a degraded recall means."
    )


def test_no_frontend_file_authors_the_degradation_clause():
    """The sentence is server-composed; the frontend renders it verbatim."""
    scanned = [
        p
        for p in _WEB.rglob("*.ts*")
        if ".test." not in p.name and ".behaviour." not in p.name and ".doc." not in p.name
    ]
    # Vacuity floor: prove the scan actually read the panel this issue is about.
    panel = _WEB / "pages" / "settings" / "MemoryPanel.tsx"
    assert panel in scanned and "RankingNote" in panel.read_text()
    offenders = [
        p.relative_to(_WEB).as_posix()
        for p in scanned
        if FALLS_BACK_CLAUSE in p.read_text(errors="ignore")
    ]
    assert not offenders, (
        f"{offenders} author the recall-degradation clause client-side. Render "
        "`ranking.summary` from the API instead — a second copy is how the two panels "
        "drifted apart in the first place."
    )


def test_recall_ranking_reaches_the_frontend_through_one_component():
    """A third surface must adopt `RankingNote`, not write its own note.

    Scoped to files that name the wire type: adding one is the moment a new surface
    decides how to render the disclosure, and that decision belongs in the shared
    component (move it into `web/src/ui/` when a page outside MemoryPanel needs it).
    """
    consumers = sorted(
        p.relative_to(_WEB).as_posix()
        for p in _WEB.rglob("*.ts*")
        if ".test." not in p.name and "RecallRanking" in p.read_text(errors="ignore")
    )
    assert consumers == ["lib/api.ts", "pages/settings/MemoryPanel.tsx"], (
        f"RecallRanking is now referenced by {consumers}. A new consumer must render it "
        "through the one RankingNote component (promote it to web/src/ui/ first) rather "
        "than composing its own wording."
    )


# ── the endpoints, driven ─────────────────────────────────────────────────────


class _Req:
    """Minimal aiohttp-request stand-in for the memory handlers."""

    def __init__(self, app, query=None, session_key="dashboard:chat-1"):
        self.app = app
        self.query = query or {}
        self.headers = {"X-Session-Key": session_key}
        self.method = "GET"


def _store(tmp_path, *, embedder: bool, graph: bool = True):
    """A real store under ``tmp_path`` — never the user's home.

    ``graph`` is PINNED rather than left to ``graph_enabled``'s live config read, so the
    disclosure asserted here is a function of the store, not of the host's config.json.
    """
    from personalclaw.vector_memory import VectorMemoryStore

    tmp_path.mkdir(parents=True, exist_ok=True)
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    store._graph_enabled = graph
    if embedder:
        store.embed_fn = lambda text: [0.1] * 8
        store._embedding_dim = 8
    return store


@pytest.mark.parametrize("embedder,expect_mode", [(True, "semantic"), (False, "keyword")])
@pytest.mark.asyncio
async def test_recall_endpoint_reports_which_arm_ran(tmp_path, embedder, expect_mode):
    """The measured defect, closed: the two states now differ in the payload."""
    from personalclaw.dashboard.handlers.memory import api_memory_recall
    from personalclaw.memory_service import MemoryService

    svc = MemoryService.over_vector_store(_store(tmp_path, embedder=embedder))
    app = {"state": MagicMock(_sessions={})}
    with (
        patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc),
        patch("personalclaw.dashboard.handlers.memory._blocks_reads_session", return_value=False),
    ):
        resp = await api_memory_recall(_Req(app, {"q": "the deploy decision"}))
    body = json.loads(resp.body.decode())
    assert body["ranking"]["mode"] == expect_mode
    assert body["ranking"]["vector"] is embedder
    # The pre-fix payload's only other signal cannot answer the question: `deep` is False
    # in BOTH states because it is the request's own flag, echoed back.
    assert body["deep"] is False


@pytest.mark.asyncio
async def test_the_two_states_no_longer_produce_the_same_payload(tmp_path):
    """The measured defect, stated directly.

    Before the fix, the recall payload's key set was ``['deep', 'query', 'result']`` with
    an embedding model bound and with none — identical, so no frontend could tell the
    states apart. Now the ranking differs, which is the only thing the tab needed.
    """
    from personalclaw.dashboard.handlers.memory import api_memory_recall
    from personalclaw.memory_service import MemoryService

    bodies = []
    for embedder in (True, False):
        svc = MemoryService.over_vector_store(
            _store(tmp_path / ("with" if embedder else "without"), embedder=embedder)
        )
        app = {"state": MagicMock(_sessions={})}
        with (
            patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc),
            patch(
                "personalclaw.dashboard.handlers.memory._blocks_reads_session", return_value=False
            ),
        ):
            resp = await api_memory_recall(_Req(app, {"q": "the deploy decision"}))
        bodies.append(json.loads(resp.body.decode()))

    bound, unbound = bodies
    assert bound["ranking"] != unbound["ranking"]
    assert bound["ranking"]["summary"] != unbound["ranking"]["summary"]
    assert bound["ranking"]["degraded"] is False
    assert unbound["ranking"]["degraded"] is True


@pytest.mark.asyncio
async def test_a_blocked_read_reports_no_ranking_rather_than_an_all_false_one(tmp_path):
    """A temporary session runs no recall, so there is nothing to describe."""
    from personalclaw.dashboard.handlers.memory import api_memory_recall

    app = {"state": MagicMock(_sessions={})}
    with (
        patch(
            "personalclaw.dashboard.handlers.memory._get_service",
            side_effect=AssertionError("a blocked read must not reach the service"),
        ),
        patch("personalclaw.dashboard.handlers.memory._blocks_reads_session", return_value=True),
    ):
        resp = await api_memory_recall(_Req(app, {"q": "anything"}))
    body = json.loads(resp.body.decode())
    assert body["ranking"] is None
    assert body["result"] == "No matching memory found."


@pytest.mark.asyncio
async def test_context_preview_reports_the_same_disclosure(tmp_path):
    from personalclaw.dashboard.handlers.memory import api_memory_context_preview

    store = _store(tmp_path, embedder=False)
    app = {"state": MagicMock()}
    with patch("personalclaw.dashboard.handlers.memory._get_provider", return_value=store):
        resp = await api_memory_context_preview(_Req(app, {"q": "timezone"}))
    body = json.loads(resp.body.decode())
    assert body["ranking"]["mode"] == "keyword"
    assert FALLS_BACK_CLAUSE in body["ranking"]["summary"]


@pytest.mark.asyncio
async def test_episodic_search_reports_the_same_disclosure(tmp_path):
    from personalclaw.dashboard.handlers.memory import api_memory_episodic_search
    from personalclaw.memory_service import MemoryService

    svc = MemoryService.over_vector_store(_store(tmp_path, embedder=False))
    app = {"state": MagicMock()}
    with patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc):
        resp = await api_memory_episodic_search(_Req(app, {"q": "deploy"}))
    body = json.loads(resp.body.decode())
    assert body["ranking"]["mode"] == "keyword"


@pytest.mark.asyncio
async def test_entities_endpoint_serves_the_sentence_the_graph_section_renders(tmp_path):
    """The section that first authored this wording now reads it from the server."""
    from personalclaw.dashboard.handlers.memory import api_memory_entities
    from personalclaw.memory_service import MemoryService

    svc = MemoryService.over_vector_store(_store(tmp_path, embedder=False))
    app = {"state": MagicMock()}
    with patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc):
        resp = await api_memory_entities(_Req(app))
    body = json.loads(resp.body.decode())
    assert FALLS_BACK_CLAUSE in body["ranking"]["summary"]


def test_a_provider_that_cannot_answer_under_claims_rather_than_raising():
    """Fail-open: a broken capability probe must not take the recall down with it."""
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    broken = MagicMock()
    broken.capabilities.side_effect = RuntimeError("provider exploded")
    d = _ranking_payload(broken)
    assert d["mode"] == "unranked" and d["degraded"] is True


def test_the_inbound_status_line_reads_the_same_owner():
    """`pc_status` used to compose its own wording for this one fact."""
    tools = (_SRC / "inbound" / "tools.py").read_text()
    assert "recall_ranking(caps).label" in tools
    assert "memory_ranking" in tools
