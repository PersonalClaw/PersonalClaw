"""The ``/plan/*`` POST family obeys the loop lifecycle (#412).

🔴 WHY THIS RAIL EXISTS. ``PATCH /api/loops/{id}`` refuses an action its source status does not
allow — ``409 Cannot pause a loop in 'planning' state`` — out of ``loop.loop:ACTION_SOURCE_STATES``.
The five ``/plan/*`` POSTs drive the SAME status machine (``advance_plan`` writes ``PLANNING``,
``finalize_plan`` writes ``REVIEW``) and spoke none of that vocabulary. Measured on
``origin/main`` d4bf938, with an isolated ``PERSONALCLAW_HOME``:

* ``POST /plan/retry`` on a RUNNING loop → ``202``, status ``running`` → ``planning``. ``pause``,
  ``resume`` and ``start`` then all 409, so the in-flight run cannot be resumed by any means; the
  loop's frozen spec re-opens (``PUT {task, max_cycles}`` → ``200`` on a loop with ``started_at``
  set); and on a STOPPED loop the same call DELETES the ``STOP`` kill-switch file that
  ``autonudge`` honours, silently un-issuing a user's stop.
* ``POST /plan/approve`` on a RUNNING loop → ``202``, status ``running`` → ``review`` — back into a
  PRELAUNCH phase, where ``start`` is a legal action again.
* ``POST /plan/comment`` on a RUNNING loop → ``202``, and it reverted the step's
  ``awaiting_review`` gate to ``pending`` BEFORE kicking the planner — which is why the guard has
  to run before the write, not beside it.
* ``POST /plan/edit`` on a RUNNING loop → ``200``, rewriting a plan artifact of a launched loop.

**The parametrisation is DERIVED, never listed.** Both buckets come from
``loop.loop:PRELAUNCH_STATUSES``, itself derived from ``LOOP_PHASES``, so a status added to the
enum is covered by one of them the day it lands — a hand-written case list would leave it silently
untested, which is how ``plan/retry`` shipped beside a guarded ``plan/start`` in the first place.
``test_the_two_buckets_partition_the_status_vocabulary`` asserts that property rather than trusting
it.

**The route list is DERIVED too** (``test_every_plan_route_is_under_this_rail``): it is read off the
router, so a sixth ``/plan/*`` POST cannot ship outside this file's coverage.

🪤 Vacuity floors, because "always refuses" would satisfy every refusal assertion here:
``TestAdmissible`` proves each route still WORKS from a pre-launch status, and it asserts the very
mutation the refusal tests assert the absence of (the session file changes / the planner is kicked),
so "nothing was mutated" is a measurement rather than a hope.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import files as loop_files
from personalclaw.loop import store
from personalclaw.loop.loop import PRELAUNCH_STATUSES, LoopStatus
from personalclaw.planning import session as PS

# ── the two derived buckets ───────────────────────────────────────────────────────────────
#: Statuses the planning walkthrough may be driven FROM — the loop's spec is still being built.
_ADMISSIBLE = sorted(s.value for s in PRELAUNCH_STATUSES)
#: Every other status in the vocabulary. Derived as the complement so a new status lands in one
#: bucket or the other automatically; neither list is written out.
_INADMISSIBLE = sorted(s.value for s in LoopStatus if s not in PRELAUNCH_STATUSES)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeSse:
    def publish(self, *a, **k):
        pass


class _FakeState:
    conversation_log = None

    def push_refresh(self, *kinds):
        pass

    def loop_sse(self):
        return _FakeSse()


class _FakeSvc:
    """Enough nudge service for ``_kick_plan_advance``'s availability check (503 otherwise)."""

    async def add(self, **kw):
        return None

    async def update(self, *a, **kw):
        pass

    async def remove(self, *a, **kw):
        pass

    def get_by_session(self, key):
        return None


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    """Every loop row + file dir under tmp_path — never the real home."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: _FakeSvc())
    return tmp_path


@pytest.fixture
def advanced(monkeypatch):
    """Records the loop ids ``_kick_plan_advance``'s background task actually planned for,
    so a kicked planner is observable (and its ABSENCE on a refusal is too)."""
    seen: list[str] = []

    async def _fake_advance(state, svc, lid):
        seen.append(lid)
        return "gated"

    monkeypatch.setattr("personalclaw.loop.plan_walkthrough.advance_plan", _fake_advance)
    return seen


def _req(method, path, *, body=None, match_info=None):
    app = web.Application()
    app["state"] = _FakeState()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "alice"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _create(status: str) -> str:
    """A real goal loop forced to ``status`` (``update_status`` only refuses transitions OUT
    of a terminal state, so every member of the vocabulary is reachable from ``ready``)."""
    r = _run(
        H.api_loop_create(
            _req(
                "POST",
                "/api/loops",
                body={"kind": "goal", "task": "investigate the latency regression in checkout"},
            )
        )
    )
    assert r.status == 201, _body(r)
    cid = _body(r)["id"]
    if status != LoopStatus.READY.value:
        store.update_status(cid, LoopStatus(status))
    assert store.get(cid).status == status
    return cid


def _seed_session(cid: str) -> None:
    """One step sitting at the review gate — the state approve/comment/edit act on."""
    import time as _time

    sess = PS.PlanSession(project_id=cid, created_at=_time.time())
    sess.steps = [
        PS.PlanStep(id="step-0", kind="intent", title="Intent", objective="restate the goal")
    ]
    sess.steps[0].status = PS.StepStatus.AWAITING_REVIEW.value
    sess.steps[0].artifact = {"markdown": "ORIGINAL"}
    sess.design_error = "the planner timed out"
    loop_files.write_plan_session(sess)


def _session_bytes(cid: str) -> str:
    p = loop_files.loop_dir(cid) / "plan_session.json"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ── the routes, keyed by the path the router registers ────────────────────────────────────
#: ``path -> (handler, body, ok_status)``. Verified against the router itself by
#: ``test_every_plan_route_is_under_this_rail``, so this table cannot fall behind a new route.
_PLAN_ROUTES = {
    "/api/loops/{id}/plan/start": (H.api_loop_plan_start, {}, 202),
    "/api/loops/{id}/plan/retry": (H.api_loop_plan_retry, {}, 202),
    "/api/loops/{id}/plan/approve": (H.api_loop_plan_approve, {"step_id": "step-0"}, 202),
    "/api/loops/{id}/plan/comment": (
        H.api_loop_plan_comment,
        {"step_id": "step-0", "text": "redo it"},
        202,
    ),
    "/api/loops/{id}/plan/edit": (
        H.api_loop_plan_edit,
        {"step_id": "step-0", "markdown": "REWRITTEN"},
        200,
    ),
}
_ROUTE_IDS = sorted(_PLAN_ROUTES)


def _call(path: str, cid: str):
    handler, body, _ok = _PLAN_ROUTES[path]
    return _run(
        handler(_req("POST", path.replace("{id}", cid), body=dict(body), match_info={"id": cid}))
    )


def test_the_two_buckets_partition_the_status_vocabulary():
    """The derived-parametrisation floor: neither bucket may be empty, and together they must
    BE the enum — that is what makes a newly added status covered by this file automatically."""
    assert _ADMISSIBLE, "no pre-launch statuses — PRELAUNCH_STATUSES import drift?"
    assert _INADMISSIBLE, "every status reads as pre-launch — the refusal cases would be vacuous"
    assert set(_ADMISSIBLE) | set(_INADMISSIBLE) == {s.value for s in LoopStatus}
    assert not set(_ADMISSIBLE) & set(_INADMISSIBLE)


def test_every_plan_route_is_under_this_rail():
    """The route list is read off the router, so a sixth ``/plan/*`` POST reds here rather than
    shipping unguarded — which is exactly how ``plan/retry`` came to sit beside a guarded
    ``plan/start`` for as long as it did."""
    app = web.Application()
    H.register_unified_loop_routes(app)
    registered = {
        r.resource.canonical: r.handler
        for r in app.router.routes()
        if r.method == "POST" and "/plan/" in (r.resource.canonical or "")
    }
    assert registered, "no /plan/* POST routes found — router drift, this census is vacuous"
    assert set(registered) == set(_PLAN_ROUTES), (
        f"the /plan/* POST family changed: router {sorted(registered)} vs this rail "
        f"{sorted(_PLAN_ROUTES)}. Every route in it drives the loop status machine, so a new one "
        "needs the same lifecycle guard and the same coverage."
    )
    for path, handler in registered.items():
        assert handler is _PLAN_ROUTES[path][0], f"{path} now dispatches a different handler"


class TestRefusedFromEveryNonPrelaunchStatus:
    @pytest.mark.parametrize("path", _ROUTE_IDS)
    @pytest.mark.parametrize("status", _INADMISSIBLE)
    def test_409_names_the_status(self, path, status, advanced):
        cid = _create(status)
        _seed_session(cid)
        r = _call(path, cid)
        assert r.status == 409, f"{path} from '{status}' → {r.status} {_body(r)}"
        err = _body(r)["error"]
        assert status in err, f"the refusal must name the status the client is in: {err!r}"
        assert err == f"Cannot replan a loop in '{status}' state", err

    @pytest.mark.parametrize("path", _ROUTE_IDS)
    @pytest.mark.parametrize("status", _INADMISSIBLE)
    def test_nothing_is_mutated_on_refusal(self, path, status, advanced):
        """A refusal that has already written is a partial action wearing a 409. Two of these
        routes wrote the planning session on their way in, and ``plan/retry`` cleared the
        recorded design error before kicking, so all three observables are checked."""
        cid = _create(status)
        _seed_session(cid)
        before = _session_bytes(cid)
        _call(path, cid)
        _run(asyncio.sleep(0))  # a kicked fire-and-forget task would run here
        assert store.get(cid).status == status, "the loop's status moved on a refused call"
        assert _session_bytes(cid) == before, "the planning session was written on a refused call"
        sess = loop_files.read_plan_session(cid)
        assert sess.steps[0].status == PS.StepStatus.AWAITING_REVIEW.value
        assert sess.steps[0].artifact == {"markdown": "ORIGINAL"}
        assert sess.design_error == "the planner timed out", "clear_design_error ran on a refusal"
        assert advanced == [], "a refused call still kicked the planner"


class TestAdmissibleFromEveryPrelaunchStatus:
    """The vacuity floor. Every assertion above is satisfied by a route that refuses
    unconditionally, so each one must be shown to still work from a pre-launch status — and to
    perform the very mutation the refusal tests measure the absence of."""

    @pytest.mark.parametrize("path", _ROUTE_IDS)
    @pytest.mark.parametrize("status", _ADMISSIBLE)
    def test_accepted_and_acts(self, path, status, advanced):
        cid = _create(status)
        _seed_session(cid)
        before = _session_bytes(cid)
        expected = _PLAN_ROUTES[path][2]
        r = _call(path, cid)
        assert r.status == expected, f"{path} from '{status}' → {r.status} {_body(r)}"
        _run(asyncio.sleep(0))  # let the fire-and-forget advance land
        if path.endswith("/plan/edit"):
            # No planner round-trip by design — it writes the artifact and stays at the gate.
            assert _session_bytes(cid) != before, "plan/edit did not write the artifact"
            assert loop_files.read_plan_session(cid).steps[0].artifact == {"markdown": "REWRITTEN"}
        else:
            assert advanced == [cid], f"{path} did not kick the planner"
        if path.endswith("/plan/approve"):
            assert loop_files.read_plan_session(cid).steps[0].status == "approved"
        if path.endswith("/plan/comment"):
            # awaiting_review → running: the comment sent the step back for a re-draft, which
            # is the gate-destroying write the refusal cases assert never happens.
            step = loop_files.read_plan_session(cid).steps[0]
            assert step.status == PS.StepStatus.RUNNING.value
            assert [c["text"] for c in step.comments] == ["redo it"]
        if path.endswith("/plan/retry"):
            assert loop_files.read_plan_session(cid).design_error == "", "retry kept design_error"


class TestGuardOrderAndShape:
    def test_a_gone_loop_still_404s_before_the_lifecycle_check(self, advanced):
        """The guard needs the row to read a status, so it must not turn a missing loop into a
        409. 404 stays the answer for every route in the family."""
        for path in _ROUTE_IDS:
            r = _call(path, "deadbeef")
            assert r.status == 404, f"{path} on a deleted loop → {r.status} {_body(r)}"

    def test_a_malformed_body_still_400s_before_the_lifecycle_check(self, advanced):
        """400 before 409, matching ``PATCH``'s own order (``Unknown action`` precedes the
        source-state refusal): a request that is malformed is malformed in any state."""
        cid = _create(LoopStatus.RUNNING.value)
        for path in (
            "/api/loops/{id}/plan/approve",
            "/api/loops/{id}/plan/comment",
            "/api/loops/{id}/plan/edit",
        ):
            handler = _PLAN_ROUTES[path][0]
            r = _run(
                handler(_req("POST", path.replace("{id}", cid), body={}, match_info={"id": cid}))
            )
            assert r.status == 400, f"{path} with no step_id → {r.status} {_body(r)}"

    def test_the_refusal_matches_the_patch_action_vocabulary(self, advanced):
        """One sentence shape for one class of refusal. ``PATCH`` says
        ``Cannot pause a loop in 'planning' state``; the plan family must not invent a second
        phrasing (it used to answer ``Loop spec is frozen (already started)``, which named no
        status at all)."""
        cid = _create(LoopStatus.PLANNING.value)
        patch = _run(
            H.api_loop_action(
                _req("PATCH", f"/api/loops/{cid}", body={"action": "pause"}, match_info={"id": cid})
            )
        )
        assert patch.status == 409
        patch_err = _body(patch)["error"]
        assert patch_err == "Cannot pause a loop in 'planning' state", patch_err

        store.update_status(cid, LoopStatus.RUNNING)
        plan = _call("/api/loops/{id}/plan/retry", cid)
        assert plan.status == 409
        plan_err = _body(plan)["error"]
        # ONE template, with only the verb and the status filled in differently.
        template = re.compile(r"^Cannot (?P<verb>[a-z ]+) a loop in '(?P<status>[a-z_]+)' state$")
        for err, status in ((patch_err, "planning"), (plan_err, "running")):
            m = template.match(err)
            assert m, f"{err!r} is a second refusal phrasing — one class, one sentence"
            assert m.group("status") == status
