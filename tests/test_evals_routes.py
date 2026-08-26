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


#: Every route on this surface whose subject is a RUN. A mutating verb on any of these would
#: let a click start a matrix — hundreds of judge calls for the bench, a multi-cell replay for
#: the ablation — which is the thing the read-only posture exists to prevent.
_RUN_ROUTES = (
    "/api/evals/judge-bench",
    "/api/evals/ablation",
    "/api/evals/studies",
    "/api/evals/studies/{study_id}",
    "/api/evals/retrieval",
    "/api/evals/retrieval/card",
)

#: The ONLY write this surface has (ES-3 §5.2's hand-label card). It saves one JSON file under
#: ``evals/``: it calls no model, spends nothing, and writes to neither knowledge.db nor
#: memory.db. Enumerated rather than counted, because a bare "no POST verbs" rail forbids
#: every future write on its own — and, more importantly, would not notice a POST added to a
#: RUN route as long as some other write already existed.
_ALLOWED_WRITES = {("POST", "/api/evals/retrieval/labels")}


def test_the_route_table_offers_no_way_to_START_a_run():
    """A benchmark run is hundreds of judge calls. The absence of a run trigger is the design,
    so it is asserted rather than left to be noticed when someone adds one."""
    app = web.Application()
    E.register_evals_routes(app)
    routes = [(r.method, str(r.resource.canonical)) for r in app.router.routes()]
    assert ("GET", "/api/evals/judge-bench") in routes
    assert ("GET", "/api/evals/ablation") in routes
    # aiohttp registers HEAD alongside GET. Every OTHER verb must be an enumerated write.
    writes = {(m, path) for m, path in routes if m not in {"GET", "HEAD"}}
    assert writes == _ALLOWED_WRITES
    # ...and no run-subject route accepts one, whatever the allowlist grows to hold.
    for path in _RUN_ROUTES:
        assert path in {p for _, p in routes}, f"{path} is not registered"
        assert {m for m, p in routes if p == path} == {"GET", "HEAD"}, path


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
    study_paths = {"/api/evals/studies", "/api/evals/studies/{study_id}"}
    assert {m for m, p in routes if p in study_paths} == {"GET", "HEAD"}


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


# ── ES-3: the per-arm retrieval ablation + its hand-label card ────────────────


def _ret_req(method="GET", path="/api/evals/retrieval", **kw):
    return _req(method, path, **kw)


def test_retrieval_disabled_is_a_404_with_its_own_code(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: False)
    for coro in (
        E.api_evals_retrieval(_ret_req()),
        E.api_evals_retrieval_card(_ret_req(path="/api/evals/retrieval/card?store=knowledge")),
        E.api_evals_retrieval_labels(_ret_req("POST", "/api/evals/retrieval/labels")),
    ):
        resp = _run(coro)
        assert resp.status == 404
        assert _body(resp)["error"]["code"] == "evals_disabled"


def test_no_retrieval_run_yet_is_a_different_404_than_disabled(evals_on, monkeypatch):
    """The panel renders guidance + the label card for one of these and a load failure for the
    other, so a shared code would collapse two different user situations into one."""
    from personalclaw.evals import retrieval_bench as rb

    monkeypatch.setattr(
        rb,
        "latest_retrieval_view",
        lambda: {"stores": {kind: {"run": ""} for kind in rb.STORES}},
    )
    resp = _run(E.api_evals_retrieval(_ret_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "retrieval_absent"


def test_one_benchmarked_store_is_enough_to_publish(evals_on, monkeypatch):
    """A user who has only ever run `--store knowledge` must still see that half.

    The vacuity floor on the 404 above: if the absence check read "every store has a run"
    instead of "any store has a run", a half-measured home would 404 forever and the panel
    would tell the user to run a command they already ran.
    """
    from personalclaw.evals import retrieval_bench as rb

    view = {
        "stores": {
            "knowledge": {"run": "retrieval-knowledge-20260825T000000Z", "table": {"rows": []}},
            "memory": {"run": "", "table": None},
        },
        "k": 5,
    }
    monkeypatch.setattr(rb, "latest_retrieval_view", lambda: view)
    resp = _run(E.api_evals_retrieval(_ret_req()))
    assert resp.status == 200
    assert _body(resp)["stores"]["memory"]["run"] == ""


def test_a_retrieval_read_failure_is_a_500_not_an_empty_table(evals_on, monkeypatch):
    from personalclaw.evals import retrieval_bench as rb

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(rb, "latest_retrieval_view", boom)
    resp = _run(E.api_evals_retrieval(_ret_req()))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "retrieval_unreadable"


def test_the_card_refuses_a_missing_or_unknown_store(evals_on):
    """No default store: the two never share a corpus, so a card built for the wrong one would
    collect labels against ids the other store has never heard of."""
    for path in (
        "/api/evals/retrieval/card",
        "/api/evals/retrieval/card?store=",
        "/api/evals/retrieval/card?store=both",
    ):
        resp = _run(E.api_evals_retrieval_card(_ret_req(path=path)))
        assert resp.status == 400
        assert _body(resp)["error"]["code"] == "store_required"


def test_the_card_route_serves_a_known_store(evals_on, monkeypatch):
    from personalclaw.evals import retrieval_bench as rb

    card = {"store": "memory", "queries": [], "labelled": 0, "mined": 0}
    monkeypatch.setattr(rb, "card_for_store", lambda kind: dict(card, store=kind))
    resp = _run(E.api_evals_retrieval_card(_ret_req(path="/api/evals/retrieval/card?store=memory")))
    assert resp.status == 200
    assert _body(resp)["store"] == "memory"


def test_a_card_read_that_wrote_to_a_store_is_reported_not_swallowed(evals_on, monkeypatch):
    """§5.1's read-only clause is the whole reason the card is a separate route from the run."""
    from personalclaw.evals import retrieval_bench as rb

    def boom(kind):
        raise rb.StoreMutatedError("retrieval bench wrote to a store")

    monkeypatch.setattr(rb, "card_for_store", boom)
    resp = _run(E.api_evals_retrieval_card(_ret_req(path="/api/evals/retrieval/card?store=memory")))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "store_mutated"


class _JsonRequest:
    """A minimal request stand-in for the POST body, since `make_mocked_request` has none."""

    def __init__(self, payload):
        self._payload = payload
        self._store = {"user": "owner"}

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def __getitem__(self, key):
        return self._store[key]

    def get(self, key, default=None):
        return self._store.get(key, default)


def test_saving_labels_refuses_a_bad_body(evals_on):
    cases = [
        (_JsonRequest(ValueError("not json")), "invalid_json"),
        (_JsonRequest([1, 2]), "invalid_json"),
        (_JsonRequest({"labels": {}}), "store_required"),
        (_JsonRequest({"store": "both", "labels": {}}), "store_required"),
        (_JsonRequest({"store": "memory"}), "labels_required"),
        (_JsonRequest({"store": "memory", "labels": []}), "labels_required"),
    ]
    for request, code in cases:
        resp = _run(E.api_evals_retrieval_labels(request))
        assert resp.status == 400, code
        assert _body(resp)["error"]["code"] == code


def test_saving_an_empty_selection_is_accepted_as_a_real_judgement(evals_on, monkeypatch):
    """ "None of these answer it" is a label. If the route treated an empty list as "nothing
    submitted", the mined weak label the human just overruled would quietly survive."""
    from personalclaw.evals import retrieval_bench as rb

    seen: dict = {}

    def fake_apply(kind, labels):
        seen["kind"] = kind
        seen["labels"] = labels
        return rb.RetrievalBenchmark(
            name="retrieval-memory",
            store="memory",
            queries=(rb.QrelsQuery(query="q", relevant_ids=(), source=rb.SOURCE_HAND_LABEL),),
        )

    monkeypatch.setattr(rb, "apply_labels_for_store", fake_apply)
    resp = _run(
        E.api_evals_retrieval_labels(_JsonRequest({"store": "memory", "labels": {"q": []}}))
    )
    assert resp.status == 200
    assert seen["labels"] == {"q": []}
    body = _body(resp)
    assert body["hand_labelled"] == 1
    assert body["subject_sha256"]


def test_a_card_that_marked_nothing_at_all_is_refused(evals_on):
    """An accepted card that changed nothing would report success while the qrels stayed weak."""
    resp = _run(E.api_evals_retrieval_labels(_JsonRequest({"store": "memory", "labels": {"": []}})))
    assert resp.status == 400
    assert _body(resp)["error"]["code"] == "labels_rejected"


# ── LV-7: the skill-impact benchmark route ───────────────────────────────────


def _bench_req(**kw):
    return _req(path="/api/evals/learning-benchmark", **kw)


def test_benchmark_disabled_is_a_404_with_its_own_code(monkeypatch):
    monkeypatch.setattr(E, "_enabled", lambda: False)
    resp = _run(E.api_evals_learning_benchmark(_bench_req()))
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "evals_disabled"


def test_no_benchmark_run_yet_is_a_distinct_404_code(evals_on, monkeypatch):
    """This panel's ORDINARY state, permanently so for most users — the paired design is 100
    real model calls. It is therefore the state that must be distinguishable from a failure, and
    the message names the command rather than leaving a user to guess."""
    monkeypatch.setattr("personalclaw.evals.learning_bench.latest_report", lambda: None)
    resp = _run(E.api_evals_learning_benchmark(_bench_req()))
    assert resp.status == 404
    code = _body(resp)["error"]["code"]
    assert code == "learning_benchmark_absent"
    assert code != "evals_disabled"
    assert "scripts/learning_benchmark.py" in _body(resp)["error"]["message"]


def test_an_unreadable_benchmark_report_is_a_500_not_an_empty_table(evals_on, monkeypatch):
    def boom():
        raise OSError("bad json")

    monkeypatch.setattr("personalclaw.evals.learning_bench.latest_report", boom)
    resp = _run(E.api_evals_learning_benchmark(_bench_req()))
    assert resp.status == 500
    assert _body(resp)["error"]["code"] == "learning_benchmark_unreadable"


def test_an_unmeasured_task_reaches_the_wire_as_null_not_as_zero(evals_on, monkeypatch):
    """The route is a pass-through by design: the §5 thresholds live in `harness/`, outside the
    wheel, so nothing here CAN synthesise a verdict or a score. A `null` verdict must survive
    serialization untouched — a 0.0 substituted anywhere would read as "the skill scored
    nothing", which is the benchmark's negative answer asserted from a run that never happened."""
    report = {
        "run_id": "learnbench-x",
        "measured_tasks": 0,
        "tasks": [
            {
                "task_id": "sk_grill",
                "skill": "grill",
                "verdict": None,
                "verdict_class": None,
                "delta_points": None,
                "token_ratio": None,
            }
        ],
        "skipped": [],
    }
    monkeypatch.setattr("personalclaw.evals.learning_bench.latest_report", lambda: report)
    resp = _run(E.api_evals_learning_benchmark(_bench_req()))
    assert resp.status == 200
    body = _body(resp)
    row = body["report"]["tasks"][0]
    assert row["verdict"] is None
    assert row["verdict_class"] is None
    assert row["delta_points"] is None
    assert row["token_ratio"] is None


def test_the_whole_frozen_register_travels_with_the_report(evals_on, monkeypatch):
    """A report carrying two rows must not make a ten-task register look like a two-task one."""
    from personalclaw.evals import learning_bench as lb

    monkeypatch.setattr(lb, "latest_report", lambda: {"run_id": "x", "tasks": [], "skipped": []})
    body = _body(_run(E.api_evals_learning_benchmark(_bench_req())))
    assert len(body["register"]) == len(lb.BENCH_TASKS) == 10
    assert body["task_set_version"] == lb.TASK_SET_VERSION
    assert body["protocol_doc"] == lb.PROTOCOL_DOC
    # The variance a reproduction is judged against is SHIPPED to the reader, not implied.
    assert body["stated_variance"] == list(lb.REPRODUCTION_CONDITIONS)
