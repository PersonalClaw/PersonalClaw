"""`GET /api/learning/proposals` reports how many SKILL proposals await review (issue 321).

The Learning page said *"Nothing to review — proposals appear here when the system notices a
pattern worth offering"* while skill refinements synthesized from the user's own sessions sat
pending. Measured on `origin/main` with ONE proposal enqueued into `skills/.proposals`:

    skills.proposals.list_pending()              -> 1
    learning_report._gather_proposals()          -> [{'label': 'delegation (refine)', ...}]
    GET /api/learning/proposals                  -> total: 0, rows: 0

The first two are the identity report, which sits LOWER ON THE SAME PAGE. So the page
contradicted itself — a state created by Learning-Visibility S4 landing, which bridged this
store into the identity report and left the proposals panel behind.

**A count, never rows.** `personalclaw.skills.proposals` and `personalclaw.learning.proposals`
are different stores by design: the learning one is this endpoint's own six-kind queue, the
skill one belongs to the skill ladder with its own review UI (`#/skills?mode=proposals`), its
own accept/reject endpoints and its own inbox routing. Unioning the rows would make Learning a
THIRD owner of skill-proposal review, behind one list with two record shapes and two accept
paths. Bridging the stores properly is Learning-Visibility's, and it already owns it.

ARCC was queried (learning.db records are user data) and returned nothing applicable — VPC
subnets, DKIM, segregation of duties. Noted; standard practice applies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import learning as L


class _State:
    """The endpoint reads no state, but `request.app["state"]` must exist."""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _enqueue(slug: str = "delegation") -> Any:
    """A real pending proposal, through the real store — not a stub of it."""
    from personalclaw.skills import proposals as P

    return P.enqueue(
        slug=slug,
        description="delegate less",
        triggers="delegation",
        procedure_md="# do less",
        session_key="s1",
        created_at="2026-09-05T10:00:00",
        kind="refine",
        refine_target=slug,
    )


def _get() -> dict:
    req = make_mocked_request("GET", "/api/learning/proposals")
    req.app["state"] = _State()
    resp = asyncio.run(L.api_learning_proposals(req))
    return json.loads(resp.text or "{}")


# ── the count ────────────────────────────────────────────────────────────────────────────


def test_a_pending_skill_proposal_is_counted(home: Path):
    """🔑 The defect: this endpoint could not see them at all."""
    _enqueue()
    payload = _get()
    assert payload.get("skill_proposals_pending") == 1


def test_two_are_counted_as_two(home: Path):
    _enqueue("delegation")
    _enqueue("task-and-project")
    assert _get().get("skill_proposals_pending") == 2


def test_none_pending_is_zero_not_absent(home: Path):
    """The field is always present, so the frontend branches on a number rather than on
    whether the key exists — an absent key and a zero mean the same thing to a user and must
    not mean different things to the code."""
    payload = _get()
    assert payload.get("skill_proposals_pending") == 0


# ── the architectural line: a count, not a union ─────────────────────────────────────────


def test_the_skill_proposal_is_NOT_added_to_the_rows(home: Path):
    """🪤 What stops this becoming a third review surface.

    The rows of this endpoint are `learning.proposals` records: they carry `gate`, `replay`,
    `evidence_strength`, and they accept/reject through `/api/learning/proposals/{id}`. A skill
    proposal has none of that and accepts elsewhere. If one ever appears in `rows`, the panel
    will render a row whose accept button posts to the wrong store.
    """
    _enqueue()
    payload = _get()
    assert payload["rows"] == []
    assert payload["total"] == 0
    # …while the count still reports it. Both halves, or the assertion above passes vacuously
    # on an endpoint that simply returned nothing.
    assert payload["skill_proposals_pending"] == 1


def test_the_two_stores_are_distinct_modules(home: Path):
    """Pinned because the fix's correctness rests on it: if these ever became one module, the
    count would double-report and the comment explaining why rows stay separate would be
    describing something that no longer exists."""
    from personalclaw.learning import proposals as learning_store
    from personalclaw.skills import proposals as skill_store

    assert learning_store is not skill_store
    assert learning_store.__name__ != skill_store.__name__


# ── fail-soft ────────────────────────────────────────────────────────────────────────────


def test_an_unreadable_skill_store_reports_zero_rather_than_500(home: Path, monkeypatch):
    """Matching the listing two lines above it, whose own comment says a corrupt row must not
    empty the queue. A count is strictly less important than the queue it annotates, so it must
    never be the thing that takes the endpoint down."""
    import personalclaw.skills.proposals as skill_store

    def _boom():
        raise RuntimeError("proposals dir unreadable")

    monkeypatch.setattr(skill_store, "list_pending", _boom)
    payload = _get()
    assert payload.get("skill_proposals_pending") == 0
    assert "rows" in payload, "the endpoint stopped answering because a COUNT failed"


def test_the_helper_is_called_by_the_endpoint(home: Path):
    """🪤 Every new guard needs a non-test caller. Asserted by observing the payload change
    when the helper is stubbed — a source grep would pass on a helper that is imported and
    never invoked."""
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(L, "_skill_proposals_pending", lambda: 77)
        assert _get().get("skill_proposals_pending") == 77
    finally:
        monkey.undo()
