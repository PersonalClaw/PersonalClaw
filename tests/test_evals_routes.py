"""ES-4 — the judge tier-recommendation table's HTTP surface.

`GET /api/evals/judge-bench` is read-only ON PURPOSE and the tests say so: the full shipped
matrix is 540 judge calls, so a POST that started one would spend real money on a click and
hold a request open for minutes. The run is `personalclaw judge-bench`.

The load-bearing assertions are the two refusals. "No benchmark has run yet" and "the eval
substrate is off" are both 404s, and they carry DIFFERENT stable codes — one code for both
would make the panel's empty state a guess, and the panel does branch on the code to decide
between guidance and a load failure.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import evals as E


def _req(method="GET", path="/api/evals/judge-bench", *, user="owner"):
    request = make_mocked_request(method, path, app=web.Application())
    request["user"] = user
    return request


def _body(resp):
    return json.loads(resp.body.decode())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture()
def evals_on(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: True)


def test_disabled_is_a_404_with_its_own_code(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: False)
    resp = _run(E.api_evals_judge_bench(_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "evals_disabled"


def test_no_benchmark_yet_is_a_different_404_than_disabled(evals_on, monkeypatch):
    """The panel renders guidance for one of these and a load failure for the other, so a
    shared code would collapse two different user situations into one."""
    from personalclaw.evals import judge_bench as jb

    monkeypatch.setattr(jb, "latest_bench_view", lambda: None)
    resp = _run(E.api_evals_judge_bench(_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "judge_bench_absent"


def test_a_read_failure_is_a_500_not_an_empty_table(evals_on, monkeypatch):
    """An unreadable artifact tree rendered as an empty table would say "the benchmark found
    nothing", which is the opposite of what happened."""
    from personalclaw.evals import judge_bench as jb

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(jb, "latest_bench_view", boom)
    resp = _run(E.api_evals_judge_bench(_req()))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "judge_bench_unreadable"


def test_the_table_is_served_as_the_harness_computed_it(evals_on, monkeypatch):
    """Adequacy and the floors it was judged against travel WITH the rows. A frontend that
    re-derived them would eventually disagree with the harness."""
    from personalclaw.evals import judge_bench as jb

    payload = {
        "bench_id": "judge-bench-x",
        "columns": list(jb.TABLE_COLUMNS),
        "rows": [{"tier": "fast", "adequate": False, "inadequate_reasons": ["nope"]}],
        "floors": {"agreement": jb.AGREEMENT_FLOOR, "separation": jb.MIN_SEPARATION},
        "recommendations": [{"rubric_class": "convergence", "verdict": "no_adequate_tier"}],
        "pin": None,
        "runs": ["judge-bench-x"],
    }
    monkeypatch.setattr(jb, "latest_bench_view", lambda: payload)
    resp = _run(E.api_evals_judge_bench(_req()))
    assert resp.status == 200
    body = _body(resp)
    assert body["rows"][0]["adequate"] is False
    assert body["floors"]["separation"] == jb.MIN_SEPARATION
    assert body["recommendations"][0]["verdict"] == "no_adequate_tier"


def test_the_route_table_offers_no_way_to_START_a_run():
    """A benchmark run is hundreds of judge calls. The absence of a POST is the design, so it
    is asserted rather than left to be noticed when someone adds one."""
    app = web.Application()
    E.register_evals_routes(app)
    routes = [(r.method, str(r.resource.canonical)) for r in app.router.routes()]
    assert ("GET", "/api/evals/judge-bench") in routes
    assert ("GET", "/api/evals/ablation") in routes
    # aiohttp registers HEAD alongside GET; nothing else exists, and no mutating verb does.
    assert {m for m, _ in routes} == {"GET", "HEAD"}


# ── ES-5: the study surface ──────────────────────────────────────────────────


def _study_req(path: str, **match):
    request = make_mocked_request("GET", path, app=web.Application())
    request["user"] = "owner"
    request._match_info = dict(match)  # noqa: SLF001 - make_mocked_request has no match_info arg
    return request


def test_the_study_routes_are_disabled_with_the_substrate(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: False)
    for resp in (
        _run(E.api_evals_studies(_study_req("/api/evals/studies"))),
        _run(E.api_evals_study(_study_req("/api/evals/studies/st-1", study_id="st-1"))),
    ):
        assert resp.status == 404
        assert _body(resp)["error"]["code"] == "evals_disabled"


def test_an_unregistered_study_is_its_own_404_code(evals_on, monkeypatch):
    from personalclaw.evals import studies

    monkeypatch.setattr(studies, "study_view", lambda _sid: None)
    resp = _run(E.api_evals_study(_study_req("/api/evals/studies/st-x", study_id="st-x")))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "study_absent"


def test_an_unreadable_study_tree_is_a_500_not_an_empty_list(evals_on, monkeypatch):
    from personalclaw.evals import studies

    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(studies, "study_index", boom)
    resp = _run(E.api_evals_studies(_study_req("/api/evals/studies")))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "studies_unreadable"


def test_the_study_view_route_serves_the_verdict_agreement_and_per_run_rows(evals_on, monkeypatch):
    from personalclaw.evals import studies

    payload = {
        "study_id": "st-1",
        "status": "complete",
        "rubric_sha256": "abc",
        "verdict": {"verdict": "win", "agreement": 1.0, "agreement_floor": 0.6},
        "runs": [{"case_id": "c1", "pairs": [{"slot_a_arm": "old"}]}],
    }
    monkeypatch.setattr(studies, "study_view", lambda _sid: payload)
    resp = _run(E.api_evals_study(_study_req("/api/evals/studies/st-1", study_id="st-1")))
    assert resp.status == 200
    body = _body(resp)
    assert body["verdict"]["agreement"] == 1.0
    assert body["runs"][0]["pairs"][0]["slot_a_arm"] == "old"


def test_the_study_routes_offer_no_way_to_START_or_REGISTER_a_study():
    """§2.1: the human registers, the substrate runs. A POST here would let a click both
    register and run, which is the one ordering the pre-registration exists to prevent."""
    app = web.Application()
    E.register_evals_routes(app)
    routes = [(r.method, str(r.resource.canonical)) for r in app.router.routes()]
    assert ("GET", "/api/evals/studies") in routes
    assert ("GET", "/api/evals/studies/{study_id}") in routes
    assert {m for m, _ in routes} == {"GET", "HEAD"}


def test_the_study_route_never_publishes_the_locked_checks_or_the_rubric_text(
    evals_on, tmp_path, monkeypatch
):
    """🔴 §2.2 at the HTTP boundary, over a REAL study rather than a stubbed payload.

    A stub would only prove the handler forwards what it is given. This registers a study
    with real locked checks and asserts the serialized response carries none of their
    tokens — the dashboard is one `curl` from an agent's context.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.evals import studies

    rubric = "correctness (target 2)\n"
    reg = studies.register_study(
        subject={"template_id": "wf-x", "old_version": 1, "new_version": 2},
        hypothesis="the candidate is better at citing",
        inputs=["case-1"],
        rubric_text=rubric,
        locked_checks=[
            {"id": "cites_the_source", "path": "out.txt", "required_phrases": ["Source: 4711"]},
            {"id": "reply_exists", "command": "test -f out.txt"},
        ],
    )
    resp = _run(
        E.api_evals_study(_study_req(f"/api/evals/studies/{reg.study_id}", study_id=reg.study_id))
    )
    assert resp.status == 200
    blob = resp.body.decode()
    tokens = studies.locked_leak_tokens(reg.study_id)
    assert tokens, "vacuity floor: the study must actually carry locked tokens"
    for token in tokens:
        assert token not in blob, f"{token!r} is published over HTTP"
    assert rubric.strip() not in blob
    assert _body(resp)["rubric_sha256"] == reg.rubric_sha256


# ── ES-7 §3.1: the ablation report's read surface ────────────────────────────


def _abl_req(**kw):
    return _req(path="/api/evals/ablation", **kw)


def test_ablation_disabled_is_a_404_with_its_own_code(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: False)
    resp = _run(E.api_evals_ablation(_abl_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "evals_disabled"


def test_no_ablation_yet_is_a_distinct_404_code(evals_on, monkeypatch):
    """Three states send a user to three different places — the switch, the registry, and
    waiting for the cadence — so "nothing has run" cannot share a code with "evals off"."""
    monkeypatch.setattr("personalclaw.evals.ablation.latest_ablation_view", lambda: None)
    resp = _run(E.api_evals_ablation(_abl_req()))
    assert resp.status == 404
    code = _body(resp)["error"]["code"]
    assert code == "ablation_absent"
    assert code != "evals_disabled"
    assert "ablation_registry.json" in _body(resp)["error"]["message"]


def test_an_unreadable_artifact_is_a_500_not_an_empty_table(evals_on, monkeypatch):
    def boom():
        raise OSError("bad json")

    monkeypatch.setattr("personalclaw.evals.ablation.latest_ablation_view", boom)
    resp = _run(E.api_evals_ablation(_abl_req()))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "ablation_unreadable"


def test_the_ablation_view_arrives_decided(evals_on, monkeypatch):
    """The verdict, the deltas and the threshold they were compared against all come from the
    runner. A frontend that re-derived "is this a real delta" would eventually disagree with
    it, and the copy shipping the permissive answer would be the UI."""
    view = {
        "report": {"matrix_id": "ablation-x-1", "verdict": "remove", "delta": 0.004},
        "verdict_vocabulary": ["keep", "remove", "lighten"],
        "registry": [],
        "history": [],
        "last_run_ts": "2026-08-17T12:00:00+00:00",
        "cadence_days": 30,
        "due": False,
    }
    monkeypatch.setattr("personalclaw.evals.ablation.latest_ablation_view", lambda: view)
    resp = _run(E.api_evals_ablation(_abl_req()))
    assert resp.status == 200
    body = _body(resp)
    assert body["report"]["verdict"] == "remove"
    assert body["verdict_vocabulary"] == ["keep", "remove", "lighten"]
    assert body["cadence_days"] == 30


def test_the_enabled_check_fails_closed_on_an_unreadable_config(monkeypatch):
    """This surface publishes artifacts read off a home directory, so "we could not read the
    switch" must not resolve to "serve it"."""
    import personalclaw.config.loader as loader

    def boom():
        raise OSError("no config")

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(boom))
    assert E._enabled() is False
