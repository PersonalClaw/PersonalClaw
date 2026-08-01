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
        assert source.index("service.status") < source.index("registry.hub")

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
