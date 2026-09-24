"""WF2KNO-12 — the ``research-finding`` kind: indexed, listed by request only, delivered.

Every claim is asserted as an outcome a caller could observe:

* "listed by request only" runs the REAL ``GET /api/knowledge/items`` handler
  (``list_items``), never a helper that mirrors its where-clause. A test that rebuilt the
  filter would keep passing while the endpoint listed findings;
* "declared in one place" is asserted by MOVING the declaration —
  ``DEFAULT_LIST_EXCLUDED_KINDS`` is emptied and the finding must reappear. That is the only
  assertion that can tell a handler reading the set from a handler with the kind hardcoded,
  since both behave identically when the set happens to hold that one kind;
* "still findable" searches (``search_items_fts`` plus the handler's own ``?q=`` branch)
  rather than asserting an ``items`` row. The store's FTS5 is external-content with no
  triggers, so a row present in ``items`` and absent from ``items_fts`` is invisible to every
  search — asserting the row would pass with search fully broken;
* "delivered" drives ``inbox.emit_attention_item`` — the existing path that persists the
  durable row and notifies as ONE event — and asserts both halves plus the digest heading.
  No sender is added anywhere; this atom's contribution to delivery is the registry row that
  keeps the finding out of the ``system/generic`` fallback.

Isolation: a ``tmp_path`` home, a ``tmp_path`` sqlite file and a ``tmp_path`` inbox file, so
nothing reaches the real home.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.inbox import InboxStore, emit_attention_item
from personalclaw.knowledge import semantics as sem
from personalclaw.knowledge.semantics import (
    DEFAULT_LIST_EXCLUDED_KINDS,
    KIND_BUDGETS,
    KINDS,
    RESEARCH_FINDING_KIND,
    SYNTHESIZED_KINDS,
    check_persist,
)
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _NoEmbedder:
    """A present-but-unavailable embedder, so ``_get_embedder`` takes its fast path instead
    of loading a model inside a unit test. The retriever then runs FTS + graph only, which is
    the path this file asserts about."""

    @staticmethod
    def is_available() -> bool:
        return False


async def _list(store, query: dict) -> dict:
    """Drive the real list handler with a minimal fake request (no aiohttp app needed)."""
    from personalclaw.dashboard.handlers.knowledge import list_items

    class _State:
        knowledge_store = store

    class _Req:
        def __init__(self) -> None:
            self.query = query
            self.app = {"state": _State(), "knowledge_embedder": _NoEmbedder()}

    resp = await list_items(_Req())
    return json.loads(resp.body.decode())


def _write_finding(store, *, title: str = "Vector DBs converged on HNSW", body: str = "") -> str:
    """One finding written through the REAL persist path — ``check_persist`` then the
    provider's ``_upsert_item``, the only writer of the ``kind`` column.

    Deliberately not ``create_typed_item(extra={"kind": ...})``: ``update_item``'s
    ``_ITEM_COLUMNS`` allowlist does not include ``kind``, so that spelling reports success
    and stores nothing (the shape already documented at ``knowledge/updates.py``). A fixture
    built on it would leave every exclusion assertion below passing for the wrong reason.
    """
    from personalclaw.action_providers.knowledge_persist_provider import _upsert_item

    content = body or "the quoll benchmark shows recall parity at half the memory"
    check = check_persist(
        kind=RESEARCH_FINDING_KIND, title=title, content=content, citations=["t-1"]
    )
    assert check.ok, check.error
    item_id = "f" + check.content_hash[:11]
    _upsert_item(
        store,
        item_id=item_id,
        title=title,
        content=content,
        summary="",
        kind=check.normalized_kind,
        logical_key=check.logical_key,
        content_hash=check.content_hash,
        expires_at=check.expires_at,
        metadata={"citations": ["t-1"]},
        tags=[],
        creating=True,
    )
    row = store.db.execute("SELECT kind FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row and row["kind"] == RESEARCH_FINDING_KIND, "the fixture must really carry the kind"
    return item_id


# ── the kind is registered in the taxonomy ──────────────────────────────────────


def test_the_kind_is_in_the_vocabulary_and_carries_its_own_budget():
    """A kind with no budget silently inherits DEFAULT_BUDGET, which may be wildly wrong."""
    assert RESEARCH_FINDING_KIND in KINDS
    assert KIND_BUDGETS[RESEARCH_FINDING_KIND] == 16_000
    # Sized between the two neighbours it sits between on purpose: bigger than a bare
    # `insight` (it carries evidence prose and citations), smaller than the whole `report`
    # (one scheduled pass emits several findings).
    assert KIND_BUDGETS["insight"] < KIND_BUDGETS[RESEARCH_FINDING_KIND] < KIND_BUDGETS["report"]


def test_a_finding_needs_citations_because_it_is_synthesized():
    """A scheduled report runs unattended, so an unsourced finding accumulates on a cron and
    is retrieved as fact forever. The citation rule is the whole reason the kind is in
    SYNTHESIZED_KINDS."""
    assert RESEARCH_FINDING_KIND in SYNTHESIZED_KINDS
    bare = check_persist(kind=RESEARCH_FINDING_KIND, title="Finding", content="c")
    assert not bare.ok
    assert check_persist(
        kind=RESEARCH_FINDING_KIND, title="Finding", content="c", citations=["t-1"]
    ).ok
    # The opt-out stays explicit: a pass that genuinely cannot source a finding says so.
    assert check_persist(
        kind=RESEARCH_FINDING_KIND, title="Finding", content="c", unsourced=True
    ).ok


def test_an_oversize_finding_is_told_to_condense_and_retry():
    """Error-as-RETURN, so the synthesizing stage can condense under normal retry semantics.
    Identity survives, or the retry cannot tell whether it is creating or updating."""
    over = check_persist(
        kind=RESEARCH_FINDING_KIND,
        title="Finding",
        content="x" * (KIND_BUDGETS[RESEARCH_FINDING_KIND] + 1),
        citations=["t-1"],
    )
    assert not over.ok
    assert "condense and retry" in over.error
    assert over.logical_key == "research-finding:finding"


# ── indexed, not listed ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finding_is_absent_from_the_default_items_list(store):
    """The plain library list is what the owner scrolls; a weekly report must not fill it."""
    store.create_typed_item(item_type="note", title="A real note", content="quoll too")
    _write_finding(store)

    listed = await _list(store, {})
    titles = [i["title"] for i in listed["items"]]
    assert titles == ["A real note"]
    assert listed["total"] == 1, "the count must agree with the rows, not with the store"


@pytest.mark.asyncio
async def test_an_explicit_kind_filter_re_admits_it(store):
    """Not a blanket ban: a filter that silently returns nothing is worse than one that
    answers, so naming the kind drops it from the exclusion."""
    store.create_typed_item(item_type="note", title="A real note", content="quoll too")
    _write_finding(store)

    explicit = await _list(store, {"kind": RESEARCH_FINDING_KIND})
    assert [i["title"] for i in explicit["items"]] == ["Vector DBs converged on HNSW"]
    assert explicit["total"] == 1
    # And the filter is a filter, not just an un-exclusion: the ordinary note is now out.
    assert "A real note" not in [i["title"] for i in explicit["items"]]


@pytest.mark.asyncio
async def test_the_exclusion_is_declared_once_in_semantics(store, monkeypatch):
    """The handler must READ ``DEFAULT_LIST_EXCLUDED_KINDS`` rather than hardcode the kind.

    Both spellings behave identically while the set holds exactly this one kind, so the only
    assertion that separates them is to move the declaration and watch the outcome follow. A
    handler with `"research-finding"` inline keeps hiding the finding here and reds.
    """
    _write_finding(store)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.knowledge.DEFAULT_LIST_EXCLUDED_KINDS", frozenset()
    )
    listed = await _list(store, {})
    assert [i["title"] for i in listed["items"]] == ["Vector DBs converged on HNSW"]


@pytest.mark.asyncio
async def test_a_finding_is_still_findable_by_search(store):
    """Indexed, not listed — the asymmetry IS the feature, so search must not filter it."""
    _write_finding(store)
    # The library's own FTS, which is what the list endpoint's hybrid retriever reads.
    assert [r["title"] for r in store.search_items_fts("quoll", limit=50)] == [
        "Vector DBs converged on HNSW"
    ]
    found = await _list(store, {"q": "quoll"})
    assert "Vector DBs converged on HNSW" in [i["title"] for i in found["items"]]


# ── delivery rides the existing attention + digest path ─────────────────────────


def test_the_notification_pair_is_registered_and_not_the_generic_fallback():
    """An unregistered pair resolves fail-open to ``system/generic``: delivered, but with no
    severity, no rules-matrix row and its digest group folded into everything else
    uncategorized. That silent downgrade is what the registration prevents."""
    resolved = nk.resolve_kind("knowledge", "research_finding")
    assert (resolved.source, resolved.kind) == ("knowledge", "research_finding")
    assert resolved.attention is True, "a report nobody is watching cannot be a toast only"
    assert nk.kind_for_legacy_pair("knowledge", "research_finding") == nk.RESEARCH_FINDING


def test_a_written_finding_is_delivered_through_the_existing_attention_path(tmp_path):
    """One call to the EXISTING entry point yields both halves of delivery — no new sender.

    ``emit_attention_item`` is the only correct way to raise a durable request: it persists
    the inbox row and notifies as one event, so the notification is a view of the row rather
    than a second thing that can drift from it.
    """
    inbox = InboxStore(path=tmp_path / "inbox_items.json")
    state = MagicMock()
    state.notify = MagicMock()

    item_id = emit_attention_item(
        state,
        source="knowledge",
        kind="research_finding",
        title="Vector DBs converged on HNSW",
        body="the quoll benchmark shows recall parity at half the memory",
        refs={"knowledge_item_id": "k-1"},
        store=inbox,
        dedup_key="report-42:finding-1",
    )

    # The durable half: a row the owner finds later, even if the toast was missed.
    assert item_id
    row = inbox.items[item_id]
    assert row.item_kind == "research_finding"
    assert "Vector DBs converged on HNSW" in row.message

    # The delivered half: ONE notification, carrying the kind's own wire string so a rule and
    # the digest can both find it, and the knowledge item it points at.
    assert state.notify.call_count == 1
    args, kwargs = state.notify.call_args
    assert args[0] == nk.RESEARCH_FINDING
    assert kwargs["meta"]["inbox_item"] == item_id
    assert kwargs["meta"]["knowledge_item_id"] == "k-1"

    # Re-emission is idempotent: a report re-run must not stack rows or interrupt twice.
    again = emit_attention_item(
        state,
        source="knowledge",
        kind="research_finding",
        title="Vector DBs converged on HNSW",
        body="same finding, second run",
        store=inbox,
        dedup_key="report-42:finding-1",
    )
    assert again == item_id
    assert state.notify.call_count == 1


def test_a_finding_gets_its_OWN_digest_heading():
    """Registration alone would not have been enough: without a distinct wire string the
    digest would group findings under whichever kind their bare string collided with."""
    from personalclaw.notification_rules import build_digest_body

    body = build_digest_body(
        [
            {"kind": nk.RESEARCH_FINDING, "title": "Vector DBs converged on HNSW"},
            {"kind": nk.kind_for_legacy_pair("cron", "result"), "title": "Nightly sync ran"},
        ]
    )
    assert "**Research report finding** — 1" in body, body
    assert "— 2" not in body, f"the finding was folded into another group:\n{body}"


def test_the_kind_keeps_the_immediate_default_the_registry_ships():
    """A digest-by-default addition is experienced as "notifications stopped working", with
    no setting the user knowingly changed. ``digest`` is one click in the rules matrix — the
    registry row is what makes that click possible."""
    assert nk.resolve_kind("knowledge", "research_finding").default_mode == "immediate"
    # The population's CANONICAL home is
    # `test_notification_kinds::test_the_kinds_defaulting_to_something_other_than_immediate_are_EXACTLY_these`,
    # which carries the per-kind justification. This copy is the local vacuity floor: it keeps
    # the assertion above from being the only thing standing between a future kind and a quiet
    # default. Widened for `user/note` (INU-9) — a note the USER wrote defaults to `badge`,
    # because a toast tells you something you did not know and you cannot be informed of your
    # own keystrokes.
    quiet = sorted(k.key for k in nk.all_kinds() if k.default_mode != "immediate")
    assert quiet == ["system/usage_recap", "user/note"]


def test_the_semantics_module_is_the_canonical_home_of_the_string():
    """The sibling ``knowledge/research_reports.py`` aliases this rather than re-spelling it:
    a producer naming its own vocabulary is how a second, disagreeing spelling gets minted."""
    assert sem.RESEARCH_FINDING_KIND == "research-finding"
    assert DEFAULT_LIST_EXCLUDED_KINDS == frozenset({sem.RESEARCH_FINDING_KIND})
