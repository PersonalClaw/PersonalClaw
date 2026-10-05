"""The monotonic template version store, who saved each version, and run-records-version.

The version store's whole promise is that history is APPEND-ONLY: a save or an accepted diff adds
a version and never rewrites or truncates a prior snapshot, and each version says who saved it.
There is no pointer that moves what runs without a save: a restore is an edit saved as the newest
version. These tests drive save→new-version and accept→new-version, plus the reproducibility
contract (a run records the version it executed) end to end through the real run-workflow action
provider.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import store, versions
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec(version: int, *, node_id: str = "only", expr: str = "done") -> dict[str, Any]:
    return {
        "name": "sample",
        "version": version,
        "root": {"kind": "transform", "id": node_id, "config": {"expr": expr}},
    }


# ── monotonic append + rollback (the falsification target) ───────────────────


def test_record_version_appends_monotonically_with_who_saved_each() -> None:
    assert versions.record_version("sample", _spec(1), saved_by=versions.OWNER) == 1
    assert versions.record_version("sample", _spec(2), saved_by=versions.REFINER) == 2
    assert [r.version for r in versions.list_versions("sample")] == [1, 2]
    assert versions.latest_version("sample") == 2
    assert versions.get_version("sample", 1).saved_by == versions.OWNER
    assert versions.get_version("sample", 2).saved_by == versions.REFINER


def test_a_version_that_names_no_door_is_refused() -> None:
    """Who saved a version decides whether an automation follows it, so a record must say."""
    with pytest.raises(ValueError):
        versions.record_version("sample", _spec(1), saved_by="")
    with pytest.raises(ValueError):
        versions.record_version("sample", _spec(1), saved_by="the caller says so")
    assert versions.list_versions("sample") == []


def test_record_never_overwrites_an_existing_snapshot() -> None:
    versions.record_version("sample", _spec(1, expr="first"), saved_by=versions.OWNER)
    # A repeat record of the same version number does NOT rewrite the stored bytes.
    versions.record_version("sample", _spec(1, expr="second"), saved_by=versions.AGENT)
    assert versions.get_version("sample", 1).spec["root"]["config"]["expr"] == "first"
    assert versions.get_version("sample", 1).saved_by == versions.OWNER


def test_a_digest_is_what_a_version_runs_and_nothing_else() -> None:
    """Two versions that run alike share a digest, whatever their number or saver; a changed
    step, and nothing but what changes a run, changes it."""
    one = _spec(1, expr="done")
    renumbered = {**_spec(7, expr="done"), "updated_at": "2026-01-01T00:00:00Z"}
    published = {**_spec(2, expr="done"), "metadata": {"a2a_published": True}}
    changed = _spec(3, expr="different")

    assert versions.digest(one) == versions.digest(renumbered) == versions.digest(published)
    assert versions.digest(changed) != versions.digest(one)


# ── typed-op diff + maturity (the Versions tab surface) ──────────────────────


def test_diff_emits_typed_ops_between_versions() -> None:
    versions.record_version("sample", _spec(1, node_id="a"), saved_by=versions.OWNER)
    two = _spec(2, node_id="a", expr="changed")
    two["root"] = {
        "kind": "sequence",
        "id": "root",
        "children": [
            {"kind": "transform", "id": "a", "config": {"expr": "changed"}},
            {"kind": "transform", "id": "b", "config": {"expr": "new"}},
        ],
    }
    versions.record_version("sample", two, saved_by=versions.OWNER)
    ops = versions.diff("sample", 1, 2)
    kinds = {(o["op"], o.get("node_id")) for o in ops}
    assert ("insert", "b") in kinds
    assert ("update_node", "a") in kinds


def test_diff_of_a_missing_version_is_empty_not_an_error() -> None:
    versions.record_version("sample", _spec(1), saved_by=versions.OWNER)
    assert versions.diff("sample", 1, 7) == []


def test_maturity_is_l0_for_a_bare_template_and_l3_when_proven() -> None:
    bare = versions.template_maturity(_spec(1))
    assert bare["level"] == 0 and bare["label"] == "draft"

    proven_spec = {
        "name": "sample",
        "version": 1,
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "work", "config": {}},
                {"kind": "stage", "id": "check", "config": {"judge_contract": True}},
            ],
        },
        "runtime_hints": {
            "execution": {"escalation": {"attempt_cap": 3}, "breaker": {"no_progress_stop": 5}},
            "judge": {"stop_condition": {"consecutive_clean": 1}},
        },
    }
    mature = versions.template_maturity(proven_spec, clean_runs=5, evaluator_rejected=True)
    assert mature["level"] == 3 and mature["label"] == "mature"
    # Same spec, but the gate has never rejected a bad run → not yet proven.
    unproven = versions.template_maturity(proven_spec, clean_runs=5, evaluator_rejected=False)
    assert unproven["level"] == 2


# ── the live writer: save_def records a version ──────────────────────────────


@pytest.mark.anyio
async def test_save_def_records_a_version_snapshot() -> None:
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    provider = NativeWorkflowDefProvider()
    root = {"kind": "transform", "id": "only", "config": {"expr": "hi"}}
    await provider.save_def(name="authored", root=root, description="x", _saved_by=versions.OWNER)
    await provider.save_def(name="authored", root=root, description="y")

    # save_def advances version on every save (1 then 2), and snapshots each with who saved it:
    # a save that names no door is no owner's.
    assert [r.version for r in versions.list_versions("authored")] == [1, 2]
    assert [r.saved_by for r in versions.list_versions("authored")] == ["owner", "agent"]


# ── run pins the executed version (reproducibility) ──────────────────────────


class _StubDefs(defs_mod.WorkflowDefProvider):
    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        return "wf2lea6-stub"

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [self._spec], 1

    async def get_def(self, name: str):
        return self._spec if name == self._spec["name"] else None


@pytest.mark.anyio
async def test_a_trigger_fired_run_records_the_def_version_it_executed(monkeypatch) -> None:
    """The gap the change names: the trigger-fired path constructed a run WITHOUT spec_version,
    so a hook-launched refiner run always recorded version 1. With the fix, the launched run
    records the def's real version — proven via the queue policy, which persists a run and does
    not start it. Fired by an automation its owner allowed, which runs that version."""
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers import grants
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    spec = {
        "name": "sample",
        "version": 4,
        "on_overlap": "queue",
        "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}},
    }
    defs_mod.register_provider(_StubDefs(spec))
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=WorkflowWatchdog()),
    )
    automation = Trigger(
        id="trigger-wf2lea6",
        name="refine nightly",
        kind="manual",
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": "sample"}}},
    )
    asked = grants.question(automation)
    assert asked is not None and asked.shown is not None, asked
    allowed = grants.allowing(automation, asked.shown)
    assert grants.give(automation, allowing=allowed) == ["run-workflow"]
    TriggerStore(base_dir=config_dir()).upsert(automation)
    # A prior RUNNING run forces the `queue` branch: a durable DRAFT row, nothing launched.
    prior = store.create(WorkflowRun(id="", workflow_name="sample", status=RunStatus.RUNNING))
    store.write_spec(prior.id, spec)
    try:
        ctx = cast(Any, SimpleNamespace(trigger_id="trigger-wf2lea6"))
        result = await RunWorkflowActionProvider().execute({"workflow": "sample"}, ctx)
        run_id = json.loads(result.stdout)["run_id"]
        assert store.get(run_id).spec_version == 4
    finally:
        defs_mod.unregister_provider("wf2lea6-stub")


@pytest.mark.anyio
async def test_accepting_a_template_diff_records_a_new_refiner_version() -> None:
    """Accept → new template VERSION: applying an accepted refiner diff saves the target
    through the writable provider, which appends an immutable v2 saved by the refiner."""
    from personalclaw.dashboard.handlers.learning import _apply_accepted_template_diff
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    provider = NativeWorkflowDefProvider()
    defs_mod.register_provider(provider)
    try:
        root = {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "build", "config": {"prompt": "go"}}],
        }
        await provider.save_def(name="refine-me", root=root, description="a refinable template")
        assert versions.latest_version("refine-me") == 1

        prop = SimpleNamespace(
            kind="template_diff",
            target="refine-me",
            change_manifest={
                "targeted_fix": [
                    {"op": "update_node", "node_id": "build", "fields": {"model_tier": "reasoning"}}
                ]
            },
        )
        result = await _apply_accepted_template_diff(prop)

        assert result["applied"] is True and result["version"] == 2
        assert versions.latest_version("refine-me") == 2
        assert versions.get_version("refine-me", 2).saved_by == versions.REFINER
        # History intact: v1 still there.
        assert versions.get_version("refine-me", 1) is not None
    finally:
        defs_mod.unregister_provider(provider.name)


# ── the HTTP endpoints the FE tabs call ──────────────────────────────────────


def _req(method: str, path: str, *, match_info=None, body=None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = None
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _resp_body(resp):
    return json.loads(resp.body.decode())


@pytest.mark.anyio
async def test_the_versions_endpoint_says_who_saved_each() -> None:
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import handlers as H
    from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

    provider = NativeWorkflowDefProvider()
    defs_mod.register_provider(provider)
    try:
        root = {"kind": "transform", "id": "only", "config": {"expr": "a"}}
        await provider.save_def(
            name="ep", root=root, description="endpoint template one", _saved_by=versions.OWNER
        )
        await provider.save_def(
            name="ep", root=root, description="endpoint template two", _saved_by=versions.REFINER
        )

        resp = await H.api_def_versions(
            _req("GET", "/api/workflows/ep/versions", match_info={"name": "ep"})
        )
        body = _resp_body(resp)
        assert [(v["version"], v["saved_by"]) for v in body["versions"]] == [
            (1, "owner"),
            (2, "refiner"),
        ]
        assert "pinned" not in body
        assert "level" in body["maturity"]

        detail = await H.api_def_version_detail(
            _req("GET", "/api/workflows/ep/versions/1", match_info={"name": "ep", "version": "1"})
        )
        assert _resp_body(detail)["saved_by"] == "owner"

        # The ledger endpoint answers even with no runs recorded.
        ledger = await H.api_def_ledger(
            _req("GET", "/api/workflows/ep/ledger", match_info={"name": "ep"})
        )
        assert _resp_body(ledger)["runs"] == []
    finally:
        defs_mod.unregister_provider(provider.name)


def test_the_versions_and_refine_routes_are_registered() -> None:
    from aiohttp import web

    from personalclaw.workflows import handlers as H

    app = web.Application()
    H.register_workflow_routes(app)
    paths = {r.resource.canonical for r in app.router.routes()}
    assert "/api/workflows/{name}/versions" in paths
    assert "/api/workflows/{name}/automations" in paths
    assert "/api/workflows/{name}/ledger" in paths
    assert "/api/workflows/{name}/refine" in paths
    # No pointer moves what runs without a save: a restore is an edit saved as the newest version.
    assert "/api/workflows/{name}/versions/repin" not in paths


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
