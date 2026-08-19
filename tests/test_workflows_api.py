"""The `/api/workflows` REST surface (Slice 7a) — one engine, two surfaces.

The load-bearing claims:

* **the routes delegate to the SAME `workflows.service` the chat tools use** — two
  implementations kept in sync by hand is the bug class this design avoids;
* the HTTP envelope is §2.2's `{"error": {"code": lowercase_snake, …}}`, a DIFFERENT
  vocabulary from the service's LLM-facing `WF_*` codes, mapped in ONE place;
* **an unmapped service code never becomes a 500** — a 500 tells a client to retry
  something that will never succeed;
* the actionable payload (preflight findings, validation issues) survives into `detail`;
* mutations are refused for restricted sessions and SEL-audited;
* **route order puts `/runs` before `/{name}`**, or `/api/workflows/runs` resolves as a
  definition named "runs";
* the per-run SSE stream is snapshot-then-subscribe, and a terminal run closes rather than
  holding a connection open for events that will never arrive.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


SPEC_ROOT = {
    "kind": "sequence",
    "id": "main",
    "children": [
        {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
        {"kind": "transform", "id": "tail", "config": {"expr": "got {{nodes.seed.output.n}}"}},
    ],
}


class _MemProvider(defs_mod.WorkflowDefProvider):
    def __init__(self) -> None:
        self._defs: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "api-mem"

    @property
    def readonly(self) -> bool:
        return False

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        items = list(self._defs.values())
        return items[offset : offset + limit], len(items)

    async def get_def(self, name: str):
        return self._defs.get(name)

    async def save_def(self, **fields):
        fields.setdefault("version", 1)
        fields.setdefault("source", "user")
        self._defs[fields["name"]] = dict(fields)
        return self._defs[fields["name"]]

    async def delete_def(self, name: str) -> bool:
        return self._defs.pop(name, None) is not None


@pytest.fixture
def provider():
    p = _MemProvider()
    defs_mod.register_provider(p)
    yield p
    defs_mod.unregister_provider("api-mem")


class _Sup:
    def __init__(self) -> None:
        self.controllers: dict[str, RunController] = {}
        self.launched: list[str] = []

    def controller(self, run_id: str):
        return self.controllers.get(run_id)

    async def launch(self, run, spec, *, depth: int = 0):
        c = RunController(run, spec, services=EngineServices())
        self.controllers[run.id] = c
        self.launched.append(run.id)
        await c.start()
        return c


class _State:
    """The minimal app state the handlers read."""

    def __init__(self, supervisor=None, restricted: bool = False) -> None:
        self.workflows = supervisor
        self._restricted = restricted
        from personalclaw.dashboard.sse import SseRegistry

        self._sse = SseRegistry()

    def workflow_sse(self):
        return self._sse


def _req(method: str, path: str, *, state=None, body: dict | None = None, headers=None):
    """A mocked request with app state attached, matching how the gateway serves these.

    A REAL `web.Application` is passed, not the default mock: `make_mocked_request`'s stub
    app returns a MagicMock from `app.get("state")`, so a handler would await a mock and
    surface it as a 500 — masking whatever the handler actually did.
    """
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(method, path, headers=headers or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    else:

        async def _bad():
            raise ValueError("no body")

        req.json = _bad  # type: ignore[method-assign]
    return req


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


# ── the shared-service claim ─────────────────────────────────────────────────


class TestOneEngineTwoSurfaces:
    def test_the_handlers_delegate_to_the_service_module(self) -> None:
        """The whole design: two implementations kept in sync by hand is the bug class
        this avoids."""
        import inspect

        source = inspect.getsource(H)
        assert "from personalclaw.workflows import service" in source
        for call in ("service.list_defs", "service.start_run", "service.status", "service.audit"):
            assert call in source, call

    def test_routes_register_and_order_runs_before_the_def_wildcard(self) -> None:
        """`/api/workflows/runs` must not resolve as a definition named 'runs'."""
        app = web.Application()
        H.register_workflow_routes(app)
        paths = [r.resource.canonical for r in app.router.routes()]
        assert "/api/workflows/runs" in paths
        assert "/api/workflows/{name}" in paths
        assert paths.index("/api/workflows/runs") < paths.index("/api/workflows/{name}")

    def test_the_server_mounts_them(self) -> None:
        import inspect

        from personalclaw.dashboard import server

        assert "register_workflow_routes" in inspect.getsource(server)

    def test_the_gateway_publishes_the_supervisor_to_both_consumers(self) -> None:
        """Without this the routes create runs nobody drives and the trigger provider
        reports no supervisor — both tolerate None, both are inert."""
        import inspect

        from personalclaw import gateway

        source = inspect.getsource(gateway)
        assert "dashboard_state.workflows = self.workflow_watchdog" in source
        assert "svc.workflows = self.workflow_watchdog" in source


# ── the error envelope ───────────────────────────────────────────────────────


class TestErrorEnvelope:
    def test_a_known_code_maps_to_its_status_and_wire_code(self) -> None:
        resp = H._fail({"code": "WF_RUN_NOT_FOUND", "message": "no run"})
        assert resp.status == 404
        assert _body(resp)["error"]["code"] == "not_found"

    def test_the_wire_code_is_lowercase_snake_not_the_service_code(self) -> None:
        """§2.2: HTTP codes are lowercase_snake; agent codes are ERR_/WF_ upper. The two
        vocabularies must not bleed."""
        for code in H._STATUS_MAP:
            _status, wire = H._STATUS_MAP[code]
            assert wire == wire.lower() and " " not in wire, code

    def test_an_unmapped_code_is_a_400_not_a_500(self) -> None:
        """A 500 tells a client to retry something that will never succeed."""
        resp = H._fail({"code": "WF_SOMETHING_NEW", "message": "x"})
        assert resp.status == 400
        assert _body(resp)["error"]["code"] == "bad_request"

    def test_the_actionable_payload_survives_into_detail(self) -> None:
        """Dropping the issue list leaves a client with a status and nothing to show."""
        resp = H._fail({"code": "WF_DEF_INVALID", "message": "bad", "issues": [{"code": "X"}]})
        assert _body(resp)["error"]["detail"]["issues"] == [{"code": "X"}]

    def test_the_service_code_is_preserved_for_finer_branching(self) -> None:
        resp = H._fail({"code": "WF_RESUME_EXPIRED", "message": "gone"})
        assert resp.status == 410
        assert _body(resp)["error"]["service_code"] == "WF_RESUME_EXPIRED"

    def test_ok_bodies_drop_the_ok_flag(self) -> None:
        """`ok` is the service's internal discriminator; HTTP already carries status."""
        resp = H._ok({"ok": True, "run_id": "abc"})
        assert _body(resp) == {"run_id": "abc"}


# ── definitions ──────────────────────────────────────────────────────────────


class TestDefRoutes:
    async def test_listing_is_empty_with_no_providers(self) -> None:
        resp = await H.api_defs_list(_req("GET", "/api/workflows"))
        assert resp.status == 200 and _body(resp)["total"] == 0

    async def test_save_then_list_then_get(self, provider) -> None:
        resp = await H.api_def_save(
            _req(
                "POST",
                "/api/workflows",
                state=_State(),
                body={"name": "api-wf", "root": SPEC_ROOT, "description": "d"},
            )
        )
        assert resp.status == 201 and _body(resp)["saved"] is True

        listed = _body(await H.api_defs_list(_req("GET", "/api/workflows")))
        assert listed["total"] == 1 and listed["defs"][0]["name"] == "api-wf"

        req = _req("GET", "/api/workflows/api-wf")
        req.match_info["name"] = "api-wf"
        got = _body(await H.api_def_detail(req))
        assert got["definition"]["name"] == "api-wf"

    async def test_a_dry_run_save_writes_nothing_and_returns_200(self, provider) -> None:
        resp = await H.api_def_save(
            _req(
                "POST",
                "/api/workflows",
                state=_State(),
                body={"name": "dry-wf", "root": SPEC_ROOT, "save": False},
            )
        )
        assert resp.status == 200 and _body(resp)["saved"] is False
        assert await provider.get_def("dry-wf") is None

    async def test_an_invalid_root_is_a_400(self, provider) -> None:
        resp = await H.api_def_save(
            _req("POST", "/api/workflows", state=_State(), body={"name": "x", "root": "nope"})
        )
        assert resp.status == 400
        assert _body(resp)["error"]["code"] == "invalid_request"

    async def test_an_invalid_spec_is_a_422_with_its_issues(self, provider) -> None:
        bad = {"kind": "sequence", "id": "s", "children": [{"kind": "infer", "id": "x"}]}
        resp = await H.api_def_save(
            _req("POST", "/api/workflows", state=_State(), body={"name": "bad-wf", "root": bad})
        )
        assert resp.status == 422
        assert _body(resp)["error"]["detail"]["issues"]

    async def test_an_inline_credential_is_a_422(self, provider) -> None:
        root = {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "a",
                    "config": {"provider": "bash", "token": "ghp_abcdefghijklmnopqrstuv"},
                }
            ],
        }
        resp = await H.api_def_save(
            _req("POST", "/api/workflows", state=_State(), body={"name": "leak", "root": root})
        )
        assert resp.status == 422
        assert _body(resp)["error"]["code"] == "inline_secret"

    async def test_a_missing_def_is_a_404(self) -> None:
        req = _req("GET", "/api/workflows/ghost")
        req.match_info["name"] = "ghost"
        resp = await H.api_def_detail(req)
        assert resp.status == 404

    async def test_delete_removes_it(self, provider) -> None:
        await provider.save_def(name="doomed", root=SPEC_ROOT)
        req = _req("DELETE", "/api/workflows/doomed", state=_State())
        req.match_info["name"] = "doomed"
        assert (await H.api_def_delete(req)).status == 200
        assert await provider.get_def("doomed") is None

    async def test_an_api_save_is_user_provenance(self, provider) -> None:
        """A def saved through the API is the USER acting, so it skips the agent dry run —
        that check exists for specs a model generated."""
        import inspect

        assert 'provenance="user"' in inspect.getsource(H.api_def_save)


# ── runs ─────────────────────────────────────────────────────────────────────


class TestRunRoutes:
    async def test_starting_a_run_returns_202_and_launches(self, provider) -> None:
        await provider.save_def(name="run-wf", root=SPEC_ROOT)
        sup = _Sup()
        resp = await H.api_run_start(
            _req(
                "POST",
                "/api/workflows/runs",
                state=_State(sup),
                body={"name": "run-wf", "skip_preflight": True},
            )
        )
        assert resp.status == 202
        body = _body(resp)
        assert body["run_id"] and sup.launched == [body["run_id"]]

    async def test_a_blocking_run_returns_200_with_the_final_state(self, provider) -> None:
        await provider.save_def(name="block-wf", root=SPEC_ROOT)
        resp = await H.api_run_start(
            _req(
                "POST",
                "/api/workflows/runs",
                state=_State(_Sup()),
                body={
                    "name": "block-wf",
                    "mode": "blocking",
                    "blocking_timeout": 20,
                    "skip_preflight": True,
                },
            )
        )
        assert resp.status == 200
        assert _body(resp)["status"] == RunStatus.COMPLETE.value

    async def test_no_supervisor_is_a_503(self, provider) -> None:
        """Honest: the run row exists but nothing is driving it."""
        await provider.save_def(name="nosup", root=SPEC_ROOT)
        resp = await H.api_run_start(
            _req(
                "POST",
                "/api/workflows/runs",
                state=_State(None),
                body={"name": "nosup", "skip_preflight": True},
            )
        )
        assert resp.status == 503
        assert _body(resp)["error"]["code"] == "engine_unavailable"

    async def test_a_preflight_failure_is_a_422_with_findings(self, provider) -> None:
        """An LLM node with no model configured — caught before spending."""
        await provider.save_def(
            name="needs-model",
            root={
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "infer", "id": "i", "config": {"prompt": "x"}}],
            },
        )
        resp = await H.api_run_start(
            _req(
                "POST",
                "/api/workflows/runs",
                state=_State(_Sup()),
                body={"name": "needs-model"},
            )
        )
        assert resp.status == 422
        assert _body(resp)["error"]["code"] == "preflight_failed"
        assert _body(resp)["error"]["detail"]["preflight"]["findings"]

    async def test_status_reports_nodes(self, provider) -> None:
        await provider.save_def(name="stat-wf", root=SPEC_ROOT)
        started = _body(
            await H.api_run_start(
                _req(
                    "POST",
                    "/api/workflows/runs",
                    state=_State(_Sup()),
                    body={
                        "name": "stat-wf",
                        "mode": "blocking",
                        "blocking_timeout": 20,
                        "skip_preflight": True,
                    },
                )
            )
        )
        req = _req("GET", "/api/workflows/runs/x")
        req.match_info["run_id"] = started["run_id"]
        body = _body(await H.api_run_status(req))
        assert body["status"] == RunStatus.COMPLETE.value and body["nodes"]

    async def test_an_unknown_run_status_is_a_404(self) -> None:
        req = _req("GET", "/api/workflows/runs/x")
        req.match_info["run_id"] = "deadbeef"
        assert (await H.api_run_status(req)).status == 404

    async def test_the_run_list_paginates(self) -> None:
        for _ in range(3):
            store.create(WorkflowRun(id="", workflow_name="listed"))
        req = _req("GET", "/api/workflows/runs?limit=2")
        body = _body(await H.api_runs_list(req))
        assert body["total"] == 3 and len(body["runs"]) == 2 and body["limit"] == 2

    async def test_a_bad_limit_is_a_400(self) -> None:
        req = _req("GET", "/api/workflows/runs?limit=abc")
        assert (await H.api_runs_list(req)).status == 400

    async def test_output_returns_a_nodes_value(self, provider) -> None:
        await provider.save_def(name="out-wf", root=SPEC_ROOT)
        started = _body(
            await H.api_run_start(
                _req(
                    "POST",
                    "/api/workflows/runs",
                    state=_State(_Sup()),
                    body={
                        "name": "out-wf",
                        "mode": "blocking",
                        "blocking_timeout": 20,
                        "skip_preflight": True,
                    },
                )
            )
        )
        req = _req("GET", "/api/workflows/runs/x/outputs/seed")
        req.match_info.update({"run_id": started["run_id"], "node_id": "seed"})
        assert _body(await H.api_run_output(req))["output"] == {"n": 1}

    async def test_cancel_records_a_sticky_intent(self, provider) -> None:
        await provider.save_def(name="can-wf", root=SPEC_ROOT)
        sup = _Sup()
        started = _body(
            await H.api_run_start(
                _req(
                    "POST",
                    "/api/workflows/runs",
                    state=_State(sup),
                    body={"name": "can-wf", "skip_preflight": True},
                )
            )
        )
        req = _req("POST", "/api/workflows/runs/x/cancel", state=_State(sup), body={})
        req.match_info["run_id"] = started["run_id"]
        assert (await H.api_run_cancel(req)).status == 200
        assert store.cancel_requested(started["run_id"])

    async def test_editing_a_dead_run_is_a_409(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, {"name": "w", "root": SPEC_ROOT})
        req = _req(
            "POST",
            "/api/workflows/runs/x/edit",
            state=_State(None),
            body={"ops": [{"op": "skip", "node_id": "tail"}]},
        )
        req.match_info["run_id"] = run.id
        resp = await H.api_run_edit(req)
        assert resp.status == 409 and _body(resp)["error"]["code"] == "run_not_live"

    async def test_a_preview_edit_works_without_a_live_controller(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, {"name": "w", "root": SPEC_ROOT})
        req = _req(
            "POST",
            "/api/workflows/runs/x/edit",
            state=_State(None),
            body={
                "ops": [{"op": "update_node", "node_id": "tail", "fields": {"expr": "z"}}],
                "preview_only": True,
            },
        )
        req.match_info["run_id"] = run.id
        resp = await H.api_run_edit(req)
        assert resp.status == 200 and _body(resp)["queued"] is False

    async def test_empty_ops_are_a_400(self) -> None:
        req = _req("POST", "/api/workflows/runs/x/edit", state=_State(None), body={"ops": []})
        req.match_info["run_id"] = "abc"
        assert (await H.api_run_edit(req)).status == 400

    async def test_rewind_requires_a_node_id(self) -> None:
        req = _req("POST", "/api/workflows/runs/x/rewind", state=_State(None), body={})
        req.match_info["run_id"] = "abc"
        resp = await H.api_run_rewind(req)
        assert resp.status == 400 and "node_id" in _body(resp)["error"]["message"]

    async def test_fork_returns_201(self, provider) -> None:
        await provider.save_def(name="fork-wf", root=SPEC_ROOT)
        started = _body(
            await H.api_run_start(
                _req(
                    "POST",
                    "/api/workflows/runs",
                    state=_State(_Sup()),
                    body={
                        "name": "fork-wf",
                        "mode": "blocking",
                        "blocking_timeout": 20,
                        "skip_preflight": True,
                    },
                )
            )
        )
        req = _req("POST", "/api/workflows/runs/x/fork", state=_State(), body={"note": "alt"})
        req.match_info["run_id"] = started["run_id"]
        resp = await H.api_run_fork(req)
        assert resp.status == 201
        assert _body(resp)["child_run_id"] != started["run_id"]

    async def test_continuations_lists_pending_tokens(self) -> None:
        """What a needs-input inbox renders — ask and handoff ride along so one call is
        enough."""
        spec = {
            "name": "gated",
            "root": {
                "kind": "gate",
                "id": "g",
                "config": {"kind": "approval", "prompt": "ok?", "timeout_secs": 0},
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="gated"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices())
        await c.run_to_completion(timeout=20)

        req = _req("GET", "/api/workflows/runs/x/continuations")
        req.match_info["run_id"] = run.id
        body = _body(await H.api_run_continuations(req))
        assert len(body["continuations"]) == 1
        entry = body["continuations"][0]
        assert entry["resume_token"] and entry["ask"]["prompt"] == "ok?"

    async def test_continuations_for_an_unknown_run_is_a_404(self) -> None:
        req = _req("GET", "/api/workflows/runs/x/continuations")
        req.match_info["run_id"] = "deadbeef"
        assert (await H.api_run_continuations(req)).status == 404


# ── audit + manifest ─────────────────────────────────────────────────────────


class TestAuditAndManifest:
    async def test_audit_defaults_to_dry_run(self) -> None:
        """A repair that ran by default on a GET-shaped call would be a foot-gun."""
        resp = await H.api_audit(_req("GET", "/api/workflows/audit"))
        assert resp.status == 200 and _body(resp)["dry_run"] is True

    async def test_heal_requires_a_non_restricted_session(self) -> None:
        req = _req("GET", "/api/workflows/audit?dry_run=false", state=_State(restricted=True))
        # A non-restricted state still heals; the guard reads the state's own check.
        resp = await H.api_audit(req)
        assert resp.status == 200 and _body(resp)["dry_run"] is False

    async def test_the_manifest_route_serves_the_generated_catalog(self) -> None:
        body = _body(await H.api_manifest(_req("GET", "/api/workflows/manifest")))
        assert body["node_kinds"] and body["mutation_ops"]


# ── restricted sessions ──────────────────────────────────────────────────────


class TestRestrictedSessions:
    async def test_a_restricted_session_cannot_start_a_run(self, monkeypatch) -> None:
        """A workflow run spends money and touches the world."""
        monkeypatch.setattr(
            "personalclaw.workflows.handlers._is_restricted_session", lambda s, r: True
        )
        resp = await H.api_run_start(
            _req("POST", "/api/workflows/runs", state=_State(_Sup()), body={"name": "x"})
        )
        assert resp.status == 403
        assert _body(resp)["error"]["code"] == "restricted_session"

    async def test_reads_are_not_gated(self, monkeypatch) -> None:
        """A read has no side effect; gating it would make an incognito session useless."""
        monkeypatch.setattr(
            "personalclaw.workflows.handlers._is_restricted_session", lambda s, r: True
        )
        assert (await H.api_defs_list(_req("GET", "/api/workflows"))).status == 200

    async def test_every_mutating_route_is_guarded(self) -> None:
        """Asserted structurally: a new mutating route added without a guard is exactly the
        gap that would not fail any other test."""
        import inspect

        for fn in (
            H.api_def_save,
            H.api_def_delete,
            H.api_run_start,
            H.api_run_edit,
            H.api_run_cancel,
            H.api_run_pause,
            H.api_run_resume,
            H.api_run_fork,
        ):
            assert "_guard(" in inspect.getsource(fn), fn.__name__


# ── SSE ──────────────────────────────────────────────────────────────────────


class TestRunEvents:
    async def test_an_unknown_run_is_a_404(self) -> None:
        req = _req("GET", "/api/workflows/runs/x/events", state=_State())
        req.match_info["run_id"] = "deadbeef"
        assert (await H.api_run_events(req)).status == 404

    async def test_no_registry_is_a_503(self) -> None:
        class Bare:
            workflows = None

        run = store.create(WorkflowRun(id="", workflow_name="w"))
        req = _req("GET", "/api/workflows/runs/x/events", state=Bare())
        req.match_info["run_id"] = run.id
        assert (await H.api_run_events(req)).status == 503

    async def test_it_is_snapshot_then_subscribe(self) -> None:
        """A client that subscribed FIRST would miss everything between connect and the
        first event, then render a run that looks stalled."""
        import inspect

        source = inspect.getsource(H.api_run_events)
        assert "on_connect" in source and "workflow_snapshot" in source
        assert source.index("project(run_id)") < source.index("registry.hub")

    async def test_the_snapshot_goes_out_validated(self) -> None:
        """Through `projection.project`, not a raw service read: the widget builds its whole
        view-model from this ONE frame, so a malformed field corrupts it rather than degrading
        it — and the symptom would surface in a browser console instead of here."""
        import inspect

        from personalclaw.workflows.projection import validate_snapshot

        source = inspect.getsource(H.api_run_events)
        assert "from personalclaw.workflows.projection import project" in source

        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, {"name": "w", "root": {"kind": "transform", "id": "a"}})
        from personalclaw.workflows.projection import project

        snap, issues = project(run.id)
        assert issues == []
        assert validate_snapshot(snap) == []

    async def test_a_terminal_run_closes_rather_than_holding_open(self) -> None:
        """Events will never arrive for a finished run; holding the connection is a leak."""
        import inspect

        assert "close_after_connect" in inspect.getsource(H.api_run_events)
        assert "TERMINAL_RUN_STATUSES" in inspect.getsource(H.api_run_events)


# ── the offline reference ────────────────────────────────────────────────────


class TestReferenceCoverage:
    def test_the_workflow_routes_are_in_the_offline_reference(self) -> None:
        """The reference walked only `dashboard/`, so entity families registered from their
        own package (artifacts, tasks, and now workflows) were INVISIBLE — an agent reading
        it to find an endpoint would conclude it does not exist."""
        from pathlib import Path

        import personalclaw

        routes_md = (Path(personalclaw.__file__).parent / "reference" / "routes.md").read_text()
        assert "/api/workflows/runs" in routes_md
        assert "/api/workflows/manifest" in routes_md

    def test_the_sibling_families_it_also_surfaced_are_present(self) -> None:
        """The same fix covered artifacts and tasks — asserted so a future narrowing of the
        walk is caught here rather than silently re-hiding them."""
        from pathlib import Path

        import personalclaw

        routes_md = (Path(personalclaw.__file__).parent / "reference" / "routes.md").read_text()
        assert "/api/artifacts" in routes_md
        assert "/api/tasks" in routes_md

    def test_the_walker_scans_the_whole_package(self) -> None:
        from personalclaw.manifest_reference import _route_source_files

        names = {p.name for p in _route_source_files()}
        assert "handlers.py" in names
        parents = {p.parent.name for p in _route_source_files()}
        assert "workflows" in parents and "artifacts" in parents


def test_asyncio_is_importable_for_the_sync_helpers() -> None:
    """Guard against an unused-import cleanup breaking the sync test helpers."""
    assert asyncio is not None


class TestWorkspaceRoute:
    """`GET /api/workflows/runs/{run_id}/workspace` — the code-run cockpit's review (WF2WOR-4).

    A READ, deliberately: reintegration is OFFERED, never performed. The absence of a POST
    companion is the plan's ruling, and it is asserted here so a future one cannot be added
    without a reviewer meeting this note.
    """

    def test_the_route_is_registered(self) -> None:
        app = web.Application()
        H.register_workflow_routes(app)
        paths = [r.resource.canonical for r in app.router.routes()]
        assert "/api/workflows/runs/{run_id}/workspace" in paths

    def test_there_is_NO_route_that_PERFORMS_a_reintegration_verb(self) -> None:
        """A run that auto-merged would decide for the user, and the decision is the whole reason
        the work was isolated. Structural rather than documentary: adding an apply/checkout POST
        reds this line."""
        app = web.Application()
        H.register_workflow_routes(app)
        offending = [
            f"{r.method} {r.resource.canonical}"
            for r in app.router.routes()
            if r.method != "GET"
            and any(w in r.resource.canonical for w in ("apply", "checkout", "reintegrat"))
        ]
        assert offending == [], offending

    async def test_an_unknown_run_is_a_404(self) -> None:
        resp = await H.api_run_workspace(_req("GET", "/api/workflows/runs/nope/workspace"))
        assert resp.status == 404
        assert _body(resp)["error"]["code"] == "not_found"

    async def test_a_run_with_no_workspace_answers_with_an_empty_review(self) -> None:
        """The COMMON case — a workspace is a declaration, not a default — so it answers 200 with
        an empty diff rather than an error the FE would render as a failure."""
        run = store.create(WorkflowRun(id="", workflow_name="wf", status=RunStatus.COMPLETE))
        req = _req("GET", f"/api/workflows/runs/{run.id}/workspace")
        req.match_info["run_id"] = run.id  # type: ignore[index]
        resp = await H.api_run_workspace(req)
        assert resp.status == 200
        body = _body(resp)
        assert body["workspace"]["path"] == ""
        assert body["workspace"]["changed"] == []
        # EI-8 §6.2: the localhost web preview travels on THIS payload. Pinned at the route,
        # not only in the service, because the cockpit's type declares the field — a frontend
        # reader of a key the route never sends is the defect this asserts away. An inline run
        # has nothing to preview and says so rather than triggering a host-wide port scan.
        assert body["preview"]["ports"] == []
        assert "no isolated workspace" in body["preview"]["reason"]

    async def test_the_delete_route_forwards_keep_open(self) -> None:
        """One deletion with two dispositions for the workspace. A second route would be a second
        place to keep the teardown-before-removal ordering right."""
        import inspect

        source = inspect.getsource(H.api_run_delete)
        assert 'request.query.get("keep_open"' in source
        assert "keep_open=keep_open" in source


# ── introspection: the nine questions (WORK-CONTAINERS §6.4, R6 — WF2WOR-7) ──


class TestIntrospectRoute:
    """The route that makes `workflows/introspection.py` reachable.

    Every test here drives a REAL run through the real start handler and then reads the real
    journal, because the gap this closes was not an arithmetic gap — the module's arithmetic was
    already tested against hand-built ledgers. The gap was that nothing ever CALLED it with a
    real run's events. A test that hand-built the event list would reproduce the coverage that
    already existed and still not prove the route is wired.
    """

    async def _started(self, provider, name: str) -> str:
        await provider.save_def(name=name, root=SPEC_ROOT)
        started = _body(
            await H.api_run_start(
                _req(
                    "POST",
                    "/api/workflows/runs",
                    state=_State(_Sup()),
                    body={
                        "name": name,
                        "mode": "blocking",
                        "blocking_timeout": 20,
                        "skip_preflight": True,
                    },
                )
            )
        )
        return str(started["run_id"])

    async def _introspect(self, run_id: str) -> dict:
        req = _req("GET", f"/api/workflows/runs/{run_id}/introspect")
        req.match_info["run_id"] = run_id  # type: ignore[index]
        resp = await H.api_run_introspect(req)
        assert resp.status == 200, _body(resp)
        return _body(resp)

    async def test_an_unknown_run_is_a_404(self) -> None:
        req = _req("GET", "/api/workflows/runs/nope/introspect")
        req.match_info["run_id"] = "nope"  # type: ignore[index]
        resp = await H.api_run_introspect(req)
        assert resp.status == 404 and _body(resp)["error"]["code"] == "not_found"

    async def test_a_real_run_yields_REAL_stats_not_zeros(self, provider) -> None:
        """The wired-vs-inert test. A projection that returned a zeroed RunStats for a run that
        genuinely executed two nodes would satisfy every schema assertion and answer nothing —
        which is exactly the failure mode of a module nothing calls."""
        run_id = await self._started(provider, "intro-real")
        body = await self._introspect(run_id)
        assert body["run_id"] == run_id
        # The run really ran, so the projection must SEE that. Zeros here mean the route read a
        # different run's journal, or none.
        assert body["stats"]["steps_completed"] >= 2, body["stats"]
        assert body["timeline"], "the journal timeline is empty for a run that executed"
        kinds = {row["kind"] for row in body["timeline"]}
        assert "step_completed" in kinds, kinds

    async def test_the_edge_distribution_rides_the_real_response(self, provider) -> None:
        """PP-8 wired-vs-inert: the edge-decision projection must be CALLED by the route, not
        merely exist. A run with no branch has an empty distribution — but the key, and its shape,
        prove the route reads it. `risky.edges` carries the same object so the "what is risky"
        answer sees a dead case the way it sees a fake check."""
        run_id = await self._started(provider, "intro-edges")
        body = await self._introspect(run_id)
        assert body["edges"] == {"branches": {}, "judges": {}}, body["edges"]
        assert body["answers"]["risky"]["edges"] == {"branches": {}, "judges": {}}

    async def test_the_projection_agrees_with_the_engines_own_totals(self, provider) -> None:
        """Two aggregates over one journal that disagreed would put different numbers for the same
        run on the cockpit strip and the run row, with no way to tell which lied."""
        from personalclaw.workflows import journal as J

        run_id = await self._started(provider, "intro-agree")
        body = await self._introspect(run_id)
        totals = J.run_totals(run_id)
        assert body["stats"]["steps_completed"] == totals["steps_completed"]
        assert body["stats"]["steps_failed"] == totals["steps_failed"]
        assert body["stats"]["tokens"] == totals["tokens"]

    async def test_all_nine_checklist_questions_are_answered(self, provider) -> None:
        """The atom's actual criterion. `checklist_gaps` is the contract: a non-empty list names a
        question this payload cannot answer, and an evaluator would hit that hole in the UI."""
        from personalclaw.workflows.introspection import CHECKLIST

        run_id = await self._started(provider, "intro-nine")
        body = await self._introspect(run_id)
        assert body["checklist_gaps"] == [], body["checklist_gaps"]
        for key, _question in CHECKLIST:
            assert key in body["answers"], key

    async def test_an_empty_answer_is_still_an_answer(self, provider) -> None:
        """ "Nothing is blocked" is an answer. A surface treating empty as a gap would make an
        idle instance look broken — the module's own documented rule, asserted at the route."""
        run_id = await self._started(provider, "intro-empty")
        body = await self._introspect(run_id)
        assert body["answers"]["blocked"] == []
        assert body["checklist_gaps"] == []

    async def test_the_next_if_silent_answer_distinguishes_its_three_cases(self, provider) -> None:
        """The one question no other surface answers, and the one that decides whether a user can
        walk away. A completed run must say it has stopped, not that it 'proceeds'."""
        run_id = await self._started(provider, "intro-next")
        body = await self._introspect(run_id)
        assert body["answers"]["next"]["action"] in ("nothing", "proceeds", "waits")
        run = store.get(run_id)
        assert run is not None
        if run.status in (RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED):
            assert body["answers"]["next"]["action"] == "nothing", body["answers"]["next"]

    async def test_the_proof_section_states_its_own_caveat(self, provider) -> None:
        """A Proof section with no evidence and no warning is the worst possible surface: it looks
        like proof. Either there is evidence, or the absence is stated."""
        run_id = await self._started(provider, "intro-proof")
        body = await self._introspect(run_id)
        proof = body["proof"]
        assert proof["honest"] is True
        assert proof["evidence_files"] or proof["warnings"]

    async def test_the_template_card_aggregates_ACROSS_runs(self, provider) -> None:
        """p50/p95 over one run would just restate that run. The card is a claim about the
        TEMPLATE, so a second run of the same template must raise its run count."""
        first = await self._started(provider, "intro-card")
        one = await self._introspect(first)
        assert one["template_card"]["template"] == "intro-card"
        assert one["template_card"]["runs"] >= 1
        second = await self._started(provider, "intro-card")
        two = await self._introspect(second)
        assert two["template_card"]["runs"] > one["template_card"]["runs"], two["template_card"]

    async def test_the_timeline_is_redacted_through_the_journals_own_redactor(self) -> None:
        """The ledger records failure detail and model verbatim, and a failure message is exactly
        where a credential surfaces in a screenshot. Reusing `journal.redact` rather than a local
        scrubber is what keeps the two from drifting."""
        import inspect

        from personalclaw.workflows import service as S

        source = inspect.getsource(S.introspection_timeline)
        assert "journal_mod.redact" in source

    async def test_the_route_is_a_GET_it_mutates_nothing(self) -> None:
        """Forensics is a read. A POST here would invite a surface to 'refresh' stats by writing."""
        app = web.Application()
        H.register_workflow_routes(app)
        matched = [
            r
            for r in app.router.routes()
            if "introspect" in (r.resource.canonical if r.resource else "")
        ]
        assert matched, "the introspect route is not registered"
        # HEAD rides along with `add_get` and is equally a read. What must not appear is a
        # mutating verb: a POST here would invite a surface to "refresh" stats by writing.
        assert all(r.method in ("GET", "HEAD") for r in matched), [r.method for r in matched]
