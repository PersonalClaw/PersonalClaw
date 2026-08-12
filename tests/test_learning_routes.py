"""S78 — the Learning HTTP surface: the Proposal Inbox and the capture panel (§6.1 / §7).

This closes the plan's success criterion 1 — "One Proposal Inbox SHOWS all six proposal kinds with
provenance, evidence manifests, and risk-tier metadata; accept installs, reject dismisses — and the
model cannot accept its own proposals under any trust mode".

**Measured before writing.** Everything behind that sentence shipped in S75/S76 and had NO HTTP
surface: `inbox.build_view` and `StagingStore.week` both return fully-serialized shapes, and
grepping
found no `/api/learning` route and no Learning page. So the criterion was unmet for want of a route.

The load-bearing test is `test_an_app_scoped_token_cannot_accept`. S75 put `require_human` inside
`proposals.accept()`, and `actor` defaults to `user` — so a route that omitted the actor would hand
every caller, including an installed app, the reviewer's authority. The actor is DERIVED from the
request rather than read from the body, because a caller that could name itself `user` would make
the
gate decorative.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import learning as L

# Every kind the queue serves, derived from the enum so a new kind (the project_* trio, LEA-12) is
# covered automatically rather than dropped by a stale literal.
from personalclaw.learning.proposals import Kind as _Kind  # noqa: E402

ALL_KINDS = tuple(k.value for k in _Kind)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The proposal store under tmp_path, via the accessors the module resolves per call."""
    from personalclaw.learning import proposals as P

    monkeypatch.setattr(P, "_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_decisions_path", lambda: tmp_path / "decisions.json")
    return P


def _req(method, path, *, user=None, app=None, match=None):
    application = web.Application()
    request = make_mocked_request(method, path, match_info=match or {}, app=application)
    if user:
        request["user"] = user
    if app:
        request["app"] = app
    return request


def _body(resp):
    return json.loads(resp.body.decode())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _filed(store, pid="p1", **kw):
    base = dict(
        id=pid,
        kind="lesson_batch",
        title=f"title-{pid}",
        body="b",
        provenance="refiner",
        status="pending",
        evidence_refs=["r1"],
    )
    base.update(kw)
    prop = store.Proposal(**base)
    store._save(prop)
    return prop


# ── criterion 1, first half: the inbox SHOWS every kind ──


def test_the_inbox_serves_every_kind(store):
    for index, kind in enumerate(ALL_KINDS):
        _filed(store, f"p{index}", kind=kind)
    body = _body(_run(L.api_learning_proposals(_req("GET", "/api/learning/proposals", user="me"))))
    assert body["total"] == len(ALL_KINDS)
    assert set(body["by_kind"]) == set(ALL_KINDS)


def test_a_row_carries_provenance_and_evidence(store):
    """§6.1 names these; each absence produces a specific bad review."""
    _filed(store, "p1")
    row = _body(_run(L.api_learning_proposals(_req("GET", "/api/learning/proposals", user="me"))))[
        "rows"
    ][0]
    for field in ("provenance", "evidence_refs", "manifest_valid", "risk_tier", "reinforcements"):
        assert field in row


def test_the_counts_ride_the_response(store):
    """A filter chip a user must click to discover is empty is worse than no chip."""
    _filed(store, "a", kind="skill")
    _filed(store, "b", kind="skill")
    _filed(store, "c", kind="retirement")
    body = _body(_run(L.api_learning_proposals(_req("GET", "/api/learning/proposals", user="me"))))
    assert body["by_kind"] == {"retirement": 1, "skill": 2}
    assert "by_tier" in body and "flagged" in body


def test_filters_narrow_the_queue(store):
    _filed(store, "a", kind="skill")
    _filed(store, "b", kind="retirement")
    body = _body(
        _run(L.api_learning_proposals(_req("GET", "/api/learning/proposals?kind=skill", user="me")))
    )
    assert body["total"] == 1 and body["rows"][0]["kind"] == "skill"


def test_a_corrupt_row_does_not_empty_the_queue(store, monkeypatch):
    """Proposals are per-file; one unreadable file hiding the rest is how a backlog disappears."""
    monkeypatch.setattr(
        store, "list_pending", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    body = _body(_run(L.api_learning_proposals(_req("GET", "/api/learning/proposals", user="me"))))
    assert body["total"] == 0  # empty, not a 500


def test_one_proposal_returns_its_full_record(store):
    """The row is a summary; the detail view needs the body a reviewer actually reads."""
    _filed(store, "p1", body="the whole change")
    body = _body(
        _run(
            L.api_learning_proposal(
                _req("GET", "/api/learning/proposals/p1", match={"id": "p1"}, user="me")
            )
        )
    )
    assert body["body"] == "the whole change"


def test_a_missing_proposal_is_404(store):
    resp = _run(
        L.api_learning_proposal(
            _req("GET", "/api/learning/proposals/ghost", match={"id": "ghost"}, user="me")
        )
    )
    assert resp.status == 404


# ── criterion 1, second half: the model cannot accept its own proposals ──


def test_a_dashboard_user_can_accept(store):
    _filed(store, "p1")
    resp = _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/p1/accept", match={"id": "p1"}, user="me")
        )
    )
    assert resp.status == 200
    assert _body(resp)["ok"] is True


def test_an_app_scoped_token_cannot_accept(store):
    """THE test.

    S75 put `require_human` inside `accept()` and defaulted `actor` to `user`. A route omitting the
    actor would hand an installed app the reviewer's authority — an app acting through the API is
    exactly the "worker whose self-report needs checking" case §7 names.
    """
    _filed(store, "p1")
    resp = _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/p1/accept", match={"id": "p1"}, app="acme-app")
        )
    )
    assert resp.status == 403
    assert "never accept" in _body(resp)["error"]


def test_an_unidentified_caller_cannot_accept(store):
    """Denied rather than assumed human: the failure directions are not symmetric."""
    _filed(store, "p1")
    resp = _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/p1/accept", match={"id": "p1"})
        )
    )
    assert resp.status == 403
    assert "unrecognized actor" in _body(resp)["error"]


def test_a_refused_accept_leaves_the_proposal_pending(store):
    """A blocked decision must not consume the row — the human still has to see it."""
    _filed(store, "p1")
    _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/p1/accept", match={"id": "p1"}, app="a")
        )
    )
    assert store.get("p1") is not None
    assert store.get("p1").status == "pending"


def test_a_missing_row_is_404_and_a_refused_actor_is_403(store):
    """Collapsing them would report a permission decision as a typo and vice versa."""
    missing = _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/ghost/accept", match={"id": "ghost"}, user="me")
        )
    )
    assert missing.status == 404
    _filed(store, "p1")
    refused = _run(
        L.api_learning_proposal_accept(
            _req("POST", "/api/learning/proposals/p1/accept", match={"id": "p1"}, app="a")
        )
    )
    assert refused.status == 403


def test_the_actor_is_never_read_from_the_body(store):
    """A caller that could name itself `user` would make the gate decorative.

    Asserted against the source: `_actor` reads only `request`, and the accept handler passes its
    result — no body key reaches it.
    """
    import inspect
    import re

    # The DOCSTRING is stripped before scanning. Measured: the first version of this test failed on
    # `_actor`'s own prose explaining that it never reads the body — a scanner matching a comment
    # rather than code, which is the exact false-positive class S67 and S69 each hit once.
    src = re.sub(r'''""".*?"""''', "", inspect.getsource(L._actor), flags=re.S)
    assert "request" in src
    for smuggled in ("body", "json()", "query"):
        assert smuggled not in src, f"_actor reads {smuggled}"


def test_there_is_no_trust_override_on_the_route():
    """§7: "under ANY trust mode"."""
    import inspect

    src = inspect.getsource(L.api_learning_proposal_accept)
    for override in ("force", "trust", "yolo", "override"):
        assert f'"{override}"' not in src and f"'{override}'" not in src


# ── reject ──


def test_a_dashboard_user_can_reject(store):
    _filed(store, "p1")
    resp = _run(
        L.api_learning_proposal_reject(
            _req("DELETE", "/api/learning/proposals/p1", match={"id": "p1"}, user="me")
        )
    )
    assert resp.status == 200 and _body(resp)["ok"] is True


def test_an_app_scoped_token_cannot_reject(store):
    """An agent that could reject would clear its own bad proposals before a human read them, and
    the rejection exemplars the flywheel learns from would stop accumulating."""
    _filed(store, "p1")
    resp = _run(
        L.api_learning_proposal_reject(
            _req("DELETE", "/api/learning/proposals/p1", match={"id": "p1"}, app="a")
        )
    )
    assert resp.status == 403
    assert store.get("p1") is not None


def test_rejecting_a_missing_row_is_404(store):
    resp = _run(
        L.api_learning_proposal_reject(
            _req("DELETE", "/api/learning/proposals/ghost", match={"id": "ghost"}, user="me")
        )
    )
    assert resp.status == 404


# ── the capture panel ──


def test_the_week_panel_serves_a_bucket_per_day(store):
    body = _body(
        _run(L.api_learning_staging_week(_req("GET", "/api/learning/staging/week", user="me")))
    )
    assert body["days"] == 7 and len(body["buckets"]) == 7
    assert "silent_days" in body and "error_days" in body


def test_the_window_is_bounded(store):
    body = _body(
        _run(
            L.api_learning_staging_week(
                _req("GET", "/api/learning/staging/week?days=9999", user="me")
            )
        )
    )
    assert body["days"] == 31


def test_a_bad_days_value_is_400(store):
    resp = _run(
        L.api_learning_staging_week(_req("GET", "/api/learning/staging/week?days=nope", user="me"))
    )
    assert resp.status == 400


# ── the flywheel health panel (LEARN-R14b — WF2LEA-9) ──


@pytest.fixture
def health_home(tmp_path, monkeypatch):
    """An isolated home for the health route.

    Explicit because the route opens `StagingStore()` and `UsageStore()` on the DEFAULT
    home, and the run store reads the workflows tree — three real-home reads on a test
    that exists to prove the panel renders nothing when nothing has happened.
    """
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def test_the_health_panel_reports_unmeasured_rather_than_zero(health_home, store):
    """A fresh install has no data, and must not read as a broken flywheel."""
    body = _body(_run(L.api_learning_health(_req("GET", "/api/learning/health", user="me"))))
    assert body["composite"]["score"] is None
    assert body["composite"]["measured"] == 0
    assert body["utilization"]["mean"] is None
    assert body["surfacing"]["precision"] is None
    assert body["cost_by_op"] == []
    assert body["ablation"] == {}


def test_the_health_panel_carries_all_four_sections_the_plan_names(health_home, store):
    """§6.2's list: composite + ideal band, MAE buckets, attribution history, per-op cost."""
    body = _body(_run(L.api_learning_health(_req("GET", "/api/learning/health", user="me"))))
    assert body["composite"]["ideal_band"] == [0.5, 0.8]
    assert [b["bucket"] for b in body["judge"]["mae"]["buckets"]] == [
        "0.00-0.25",
        "0.25-0.50",
        "0.50-0.75",
        "0.75-1.00",
    ]
    assert "history" in body["attribution"] and "proposers" in body["attribution"]
    assert isinstance(body["cost_by_op"], list)


def test_the_health_panel_renders_what_the_writers_wrote(health_home, store):
    """The wire, end to end: two live writers, one response."""
    from personalclaw.learning.staging import FlushOutcome, StagingStore

    live = StagingStore()
    try:
        live.record_allocation(used_tokens=2600, budget_tokens=4000)
        live.record_flush(cadence="session_end", outcome=FlushOutcome.FLUSH_OK, cost_usd=0.05)
    finally:
        live.close()

    body = _body(_run(L.api_learning_health(_req("GET", "/api/learning/health", user="me"))))
    assert body["utilization"] == {"samples": 1, "mean": 0.65, "ideal_band": [0.5, 0.8]}
    assert body["cost_by_op"] == [{"op": "session_end", "passes": 1, "cost_usd": 0.05}]
    # 65% is inside the ideal band, so the utilization component scores 100 …
    band = next(c for c in body["composite"]["components"] if c["name"] == "utilization")
    assert band["score"] == 100.0
    # … and the composite is now measurable where it was None.
    assert body["composite"]["score"] is not None


def test_the_health_window_is_bounded(health_home, store):
    body = _body(
        _run(L.api_learning_health(_req("GET", "/api/learning/health?days=9999", user="me")))
    )
    assert body["days"] == 31


def test_a_bad_health_days_value_is_400(health_home, store):
    resp = _run(L.api_learning_health(_req("GET", "/api/learning/health?days=nope", user="me")))
    assert resp.status == 400


# ── the kill switch and route registration ──


def test_every_route_404s_when_learning_is_disabled(monkeypatch, store):
    """404 rather than 403: with learning off there is no inbox, and "forbidden" implies one exists
    behind a permission wall."""
    monkeypatch.setattr(L, "_enabled", lambda: False)
    for handler, method, match in (
        (L.api_learning_proposals, "GET", None),
        (L.api_learning_proposal, "GET", {"id": "p1"}),
        (L.api_learning_proposal_accept, "POST", {"id": "p1"}),
        (L.api_learning_proposal_reject, "DELETE", {"id": "p1"}),
        (L.api_learning_staging_week, "GET", None),
    ):
        resp = _run(handler(_req(method, "/api/learning/x", match=match, user="me")))
        assert resp.status == 404


def test_an_unreadable_config_fails_OPEN_for_the_read_surface(monkeypatch):
    """A hidden queue looks like an empty one, and proposals then accumulate unseen."""
    from personalclaw.config import loader

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(lambda *a, **k: 1 / 0))
    assert L._enabled() is True


def test_the_literal_paths_register_before_the_id_route():
    """`staging` must not be captured as a proposal id — the ordering landmine S67 and S70 each paid
    for once."""
    import inspect

    src = inspect.getsource(L.register_learning_routes)
    assert src.index('"/api/learning/staging/week"') < src.index('"/api/learning/proposals/{id}"')
    assert src.index('"/api/learning/proposals"') < src.index('"/api/learning/proposals/{id}"')


def test_the_routes_are_registered_in_the_server():
    """A handler nobody registers is a page that 404s."""
    import inspect

    from personalclaw.dashboard import server

    assert "register_learning_routes" in inspect.getsource(server)
