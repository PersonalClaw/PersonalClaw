"""A link the gateway hands the dashboard opens the page it names, not Home.

Measured on the Morning triage card: "the item" pointed at `/runs/<id>`, a path the gateway
answers with the app itself, so the click landed on Home while the card's own "Run journal" link,
built by the run-link builder, opened the run. A run in the triage digest now links through that
one builder, and a knowledge result's citation link names the item's route in the dashboard.
"""

from __future__ import annotations

from types import SimpleNamespace

from personalclaw.knowledge.retrieval import _attach_locator
from personalclaw.proactive import collect
from personalclaw.triggers.delivery import status_url


def test_a_finished_run_in_the_digest_links_to_its_page(monkeypatch) -> None:
    import personalclaw.ledger as ledger
    from personalclaw.workflows import store as run_store

    run = SimpleNamespace(
        id="r-5e1f",
        created_at="2026-03-02T08:00:00+00:00",
        status="completed",
        workflow_name="general-project",
        error_message="",
    )
    monkeypatch.setattr(run_store, "list_runs", lambda limit: ([run], 1))
    monkeypatch.setattr(ledger, "read_events", lambda store, run_id, kinds: [])

    (item,) = collect.collect_runs()

    assert item.permalink == status_url(run_id="r-5e1f")
    assert item.permalink == "#/workflows/runs/r-5e1f", "not a route the dashboard renders"


def test_a_knowledge_citation_links_to_the_items_page() -> None:
    loc = _attach_locator(
        {"id": "k-17", "item_type": "note", "content": "alpha\nneedle\n"}, {"needle"}
    )
    assert loc["deep_link"].startswith("#/knowledge/item/k-17"), loc["deep_link"]
