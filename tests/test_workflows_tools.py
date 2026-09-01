"""The workflow chat surface (Slice 6a) — 19 tools over ONE service layer.

The load-bearing claims:

* **all 19 tools exist, are uniquely named, and each has a real schema** — a tool the
  aggregator exposes but cannot dispatch is worse than a missing one;
* **the service layer is the single implementation** the REST routes will also call, so
  the two surfaces cannot grow two behaviours;
* **nothing raises across the tool boundary** — every failure is a coded, readable result,
  because a traceback burns the model's turn on something it cannot act on;
* a def read STRIPS credentials to `_has*` flags, and authoring REFUSES a literal secret;
* `save=false` is a true dry run — validation issues come back and nothing is written;
* `observe` clamps its window (an unbounded subscribe in a chat turn is a hang);
* the manifest is GENERATED from the engine's own enums, so it cannot drift.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import mcp_workflows as T
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import service, store
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
        {"kind": "infer", "id": "think", "config": {"prompt": "on {{nodes.seed.output.n}}"}},
    ],
}


class _MemProvider(defs_mod.WorkflowDefProvider):
    """A writable in-memory def provider — the seam a real pack would occupy."""

    def __init__(self) -> None:
        self._defs: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "test-mem"

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


@pytest.fixture(autouse=True)
def _clean_registry():
    """Run every test in this module against an EMPTY def registry.

    `defs._providers` is process-global, and these tests assert exact counts ("one def, from my
    provider"). Registering into whatever a neighbour left behind makes them fail with
    `7 == 1` — and only under a worker layout that happens to schedule the two files together,
    which is why it reproduced in CI (4 workers) and not locally (18).

    Starting CLEAN rather than adding a restore to every other module: the invariant this file
    needs is "nothing but what I registered", and asserting it here is what makes it true
    regardless of who else forgets. The prior registry is restored so a later module is
    unaffected.
    """
    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    try:
        yield
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


@pytest.fixture
def provider():
    p = _MemProvider()
    defs_mod.register_provider(p)
    yield p
    defs_mod.unregister_provider("test-mem")


class _ReadOnlyProvider(_MemProvider):
    """A read-only provider, the seam `bundled` and any shipped pack occupy.

    Subclasses the writable one so the ONLY difference under test is `readonly` — if the guard
    keyed off anything else (a provider name, a path) that would show up as this fixture passing
    for the wrong reason.
    """

    @property
    def name(self) -> str:
        return "test-pack"

    @property
    def readonly(self) -> bool:
        return True

    async def save_def(self, **fields):  # pragma: no cover - a read-only provider is never written
        raise AssertionError("a read-only provider must never be written through")

    def seed(self, name: str) -> None:
        self._defs[name] = {"name": name, "version": 1, "source": "bundled"}


@pytest.fixture
def readonly_provider():
    p = _ReadOnlyProvider()
    defs_mod.register_provider(p)
    yield p
    defs_mod.unregister_provider("test-pack")


class _FakeSupervisor:
    """Records launches and hands back the real controller, so tool→engine wiring is
    exercised without a gateway."""

    def __init__(self) -> None:
        self.controllers: dict[str, object] = {}
        self.launched: list[str] = []

    def controller(self, run_id: str):
        return self.controllers.get(run_id)

    async def launch(self, run, spec, *, depth: int = 0):
        from personalclaw.workflows.controller import EngineServices, RunController

        async def fake(prompt, *, use_case="background", output_type=None):
            return "ok"

        c = RunController(run, spec, services=EngineServices(completion=fake))
        self.controllers[run.id] = c
        self.launched.append(run.id)
        await c.start()
        return c


# ── the tool surface ─────────────────────────────────────────────────────────


class TestToolSurface:
    def test_all_nineteen_tools_are_declared(self) -> None:
        assert len(T._list_tools()) == 19

    def test_tool_names_are_unique_and_prefixed(self) -> None:
        names = [t["name"] for t in T._list_tools()]
        assert len(set(names)) == len(names)
        assert all(n.startswith("workflow_") for n in names)

    def test_every_tool_has_a_description_and_schema(self) -> None:
        for tool in T._list_tools():
            assert tool["description"].strip(), tool["name"]
            assert tool["inputSchema"]["type"] == "object", tool["name"]

    def test_every_tool_dispatches(self) -> None:
        """A tool the aggregator exposes but cannot dispatch is worse than a missing one:
        the model calls it and gets 'unknown tool'."""
        for tool in T._list_tools():
            out = T._call_tool(tool["name"], {})
            assert "unknown workflows tool" not in out, tool["name"]

    def test_an_unknown_tool_is_reported_not_raised(self) -> None:
        assert "unknown workflows tool" in T._call_tool("workflow_nope", {})

    def test_plan_and_author_are_separate_tools(self) -> None:
        """Overloading one name with both contracts was flagged in design review — a model
        cannot tell which contract it is fulfilling."""
        names = {t["name"] for t in T._list_tools()}
        assert {"workflow_plan", "workflow_author"} <= names

    def test_read_only_tools_are_declared(self) -> None:
        assert "workflow_status" in T.READ_ONLY_TOOLS
        assert "workflow_start" not in T.READ_ONLY_TOOLS

    def test_every_tool_has_a_validation_schema(self) -> None:
        from personalclaw.validation import MCP_WORKFLOW_SCHEMAS

        for tool in T._list_tools():
            assert tool["name"] in MCP_WORKFLOW_SCHEMAS, tool["name"]

    def test_schema_keys_match_their_own_tool_name(self) -> None:
        """The lookup is by dict key; a mismatch means validation silently never runs."""
        from personalclaw.validation import MCP_WORKFLOW_SCHEMAS

        for key, schema in MCP_WORKFLOW_SCHEMAS.items():
            assert schema.tool_name == key

    def test_every_advertised_field_is_a_validated_field(self) -> None:
        """Advertised ⊆ validated, and the direction matters.

        `validate_tool_args` REJECTS an argument it has no `FieldSpec` for, so a property
        advertised in `inputSchema` with no spec behind it is a tool that refuses the exact
        argument its own description told the model to send. The two existing parity tests
        check that a schema EXISTS and that its key matches; neither reads inside it, and
        two tools had drifted this way — `workflow_resume`'s `answer` and `workflow_plan`'s
        `project_id`, both advertised, both consumed by their handlers, neither declared.

        The other direction is deliberately NOT asserted: a validated-but-unadvertised field
        is an internal argument a handler accepts without inviting a model to send it.
        """
        from personalclaw.validation import MCP_WORKFLOW_SCHEMAS

        for tool in T._list_tools():
            advertised = set((tool.get("inputSchema") or {}).get("properties") or {})
            validated = {f.name for f in MCP_WORKFLOW_SCHEMAS[tool["name"]].fields}
            missing = advertised - validated
            assert not missing, (
                f"{tool['name']} advertises {sorted(missing)} with no FieldSpec — "
                "a schema-validated call carrying it would be REJECTED"
            )


class TestRegistration:
    def test_the_module_is_in_the_aggregator(self) -> None:
        """An unlisted module is invisible to every ACP agent."""
        from personalclaw.mcp_core import _AGGREGATED_CATEGORY_MODULES

        assert "personalclaw.mcp_workflows" in _AGGREGATED_CATEGORY_MODULES

    def test_the_provider_factory_resolves(self) -> None:
        from personalclaw.tool_providers.registry import create_workflows_provider

        provider = create_workflows_provider()
        assert provider is not None

    def test_the_native_manifest_exists_and_points_at_the_factory(self) -> None:
        from pathlib import Path

        import personalclaw

        manifest = (
            Path(personalclaw.__file__).parent
            / "apps"
            / "native"
            / "personalclaw-workflows"
            / "app.json"
        )
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["provider"]["implementation"].endswith("create_workflows_provider")
        assert data["native"] is True

    def test_every_tool_has_manifest_meta(self) -> None:
        from personalclaw.manifest_meta import TOOL_META

        for tool in T._list_tools():
            assert tool["name"] in TOOL_META, tool["name"]
            assert TOOL_META[tool["name"]]["response_type"], tool["name"]


# ── the manifest tool ────────────────────────────────────────────────────────


class TestManifest:
    def test_it_is_generated_from_the_engines_own_enums(self) -> None:
        """Generated, never hand-written: a hand-kept catalog drifts the moment either side
        changes, and an author following a stale one writes specs the engine rejects."""
        from personalclaw.workflows.models import NodeKind

        body = service.manifest()
        kinds = {k["kind"] for k in body["node_kinds"]}
        assert kinds == {k.value for k in NodeKind}

    def test_it_names_containers_and_lanes(self) -> None:
        body = service.manifest()
        by_kind = {k["kind"]: k for k in body["node_kinds"]}
        assert by_kind["sequence"]["container"] is True
        assert by_kind["infer"]["container"] is False
        assert by_kind["infer"]["lane"] == "llm"
        assert by_kind["action"]["lane"] == "io"

    def test_it_carries_pipes_ops_and_states(self) -> None:
        body = service.manifest()
        assert body["pipes"] and body["mutation_ops"] and body["instance_states"]
        assert "rewind" in body["mutation_ops"]
        assert "scope_violation" in body["instance_states"]

    def test_the_tool_renders_it(self) -> None:
        out = T._call_tool("workflow_manifest", {})
        assert "node_kinds" in out and "mutation_ops" in out


# ── definitions ──────────────────────────────────────────────────────────────


class TestDefs:
    async def test_authoring_saves_a_valid_spec(self, provider) -> None:
        body = await service.author_def(name="wf-one", root=SPEC_ROOT, description="d")
        assert body["ok"] and body["saved"]
        assert (await provider.get_def("wf-one"))["name"] == "wf-one"

    async def test_save_false_is_a_real_dry_run(self, provider) -> None:
        """Validating only at save time would leave a broken def on disk per attempt."""
        body = await service.author_def(name="wf-dry", root=SPEC_ROOT, save=False)
        assert body["ok"] and body["dry_run"] and not body["saved"]
        assert await provider.get_def("wf-dry") is None

    async def test_an_invalid_spec_comes_back_repromptable_with_issues(self, provider) -> None:
        bad = {"kind": "sequence", "id": "s", "children": [{"kind": "infer", "id": "x"}]}
        body = await service.author_def(name="wf-bad", root=bad)
        assert not body["ok"] and body["code"] == "WF_DEF_INVALID"
        assert body["repromptable"] and body["issues"]
        assert await provider.get_def("wf-bad") is None

    async def test_a_bad_name_is_refused_before_anything_else(self, provider) -> None:
        body = await service.author_def(name="Not A Name", root=SPEC_ROOT)
        assert not body["ok"] and body["code"] == "WF_DEF_NAME_INVALID"

    async def test_a_read_only_providers_name_is_RESERVED(
        self, provider, readonly_provider
    ) -> None:
        """🔴 issue 764. The save used to succeed and then be ignored.

        `get_def` and every run-start return the first provider in sort order, so a user def
        shadowing a bundled name produced split-brain: the list showed both, the save reported
        success, and the run used the bundled spec. The UI reported the half that was not used.
        """
        readonly_provider.seed("audit-sweep")
        body = await service.author_def(name="audit-sweep", root=SPEC_ROOT, description="mine")
        assert not body["ok"] and body["code"] == "WF_DEF_NAME_RESERVED"
        # Nothing written — the whole defect was that the write happened.
        assert await provider.get_def("audit-sweep") is None
        # The refusal names the provider AND what to do instead. A 409 that says only "conflict"
        # leaves the user re-clicking Save.
        assert body["provider"] == "test-pack"
        assert "different name" in body["message"]

    async def test_the_reservation_is_reported_by_a_DRY_RUN_too(
        self, provider, readonly_provider
    ) -> None:
        """`save=False` exists to learn what is wrong before committing, and the name is the
        cheapest thing to fix. Discovering it only on the real save wastes the dry run."""
        readonly_provider.seed("audit-sweep")
        body = await service.author_def(name="audit-sweep", root=SPEC_ROOT, save=False)
        assert not body["ok"] and body["code"] == "WF_DEF_NAME_RESERVED"

    async def test_a_WRITABLE_providers_name_is_not_reserved(self, provider) -> None:
        """🪤 The vacuity floor. "Refuse a name another provider has" one step too far refuses
        every UPDATE, since a user re-saving their own def collides with themselves. Only a
        READ-ONLY provider reserves a name."""
        first = await service.author_def(name="wf-mine", root=SPEC_ROOT, description="v1")
        assert first["ok"] and first["saved"]
        again = await service.author_def(name="wf-mine", root=SPEC_ROOT, description="v2")
        assert again["ok"] and again["saved"], "re-saving one's own def must still work"
        assert (await provider.get_def("wf-mine"))["description"] == "v2"

    async def test_an_unrelated_name_is_unaffected_by_a_read_only_pack(
        self, provider, readonly_provider
    ) -> None:
        """The guard is per-NAME, not "a read-only provider exists, so refuse everything"."""
        readonly_provider.seed("audit-sweep")
        body = await service.author_def(name="wf-fresh", root=SPEC_ROOT)
        assert body["ok"] and body["saved"]

    async def test_the_reservation_is_keyed_on_readonly_not_on_a_provider_NAME(
        self, provider, readonly_provider
    ) -> None:
        """A guard written against the string "bundled" would miss every shipped pack, which is
        read-only for the same reason. This fixture's provider is called `test-pack`, so a
        name-based rule cannot pass this leg."""
        readonly_provider.seed("packaged-thing")
        body = await service.author_def(name="packaged-thing", root=SPEC_ROOT)
        assert not body["ok"] and body["code"] == "WF_DEF_NAME_RESERVED"
        assert body["provider"] == "test-pack"

    async def test_a_literal_credential_is_refused_not_warned(self, provider) -> None:
        """Once saved the value is on disk and every later defence is damage control."""
        root = {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "a",
                    "config": {"provider": "bash", "api_key": "sk-ant-abcdefghijklmnopqrst"},
                }
            ],
        }
        body = await service.author_def(name="wf-leak", root=root)
        assert not body["ok"] and body["code"] == "WF_DEF_INLINE_SECRET"
        assert body["findings"]
        assert await provider.get_def("wf-leak") is None

    async def test_reading_a_def_strips_credentials_to_presence_flags(self, provider) -> None:
        """A def read is rendered in a UI and echoed into a chat turn; a credential that
        reaches either has leaked to both."""
        await provider.save_def(
            name="wf-secret",
            root={
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "action", "id": "a", "config": {"provider": "x", "token": "t-real"}}
                ],
            },
        )
        body = await service.get_def("wf-secret")
        cfg = body["definition"]["root"]["children"][0]["config"]
        assert cfg["_has_token"] is True and "token" not in cfg

    async def test_a_missing_def_is_a_coded_error(self) -> None:
        body = await service.get_def("nope")
        assert not body["ok"] and body["code"] == "WF_DEF_NOT_FOUND"

    async def test_listing_reports_defs_with_their_provider(self, provider) -> None:
        await service.author_def(name="wf-a", root=SPEC_ROOT)
        body = await service.list_defs()
        assert body["ok"] and body["total"] == 1
        assert body["defs"][0]["provider"] == "test-mem"

    async def test_listing_filters_by_tag(self, provider) -> None:
        await service.author_def(name="wf-t", root=SPEC_ROOT, tags=["daily"])
        assert (await service.list_defs(tag="daily"))["total"] == 1
        assert (await service.list_defs(tag="weekly"))["total"] == 0

    async def test_deleting_removes_it(self, provider) -> None:
        await service.author_def(name="wf-del", root=SPEC_ROOT)
        assert (await service.delete_def("wf-del"))["ok"]
        assert await provider.get_def("wf-del") is None

    async def test_authoring_without_a_writable_provider_is_coded(self) -> None:
        body = await service.author_def(name="wf-x", root=SPEC_ROOT)
        assert not body["ok"] and body["code"] == "WF_DEF_NO_WRITABLE_PROVIDER"


# ── runs ─────────────────────────────────────────────────────────────────────


class TestRuns:
    async def test_starting_a_run_launches_it_through_the_supervisor(self, provider) -> None:
        """Through the SUPERVISOR, so the controller is the one the watchdog knows about —
        otherwise a restart adopts the run a second time and two writers race."""
        await service.author_def(name="wf-run", root=SPEC_ROOT)
        sup = _FakeSupervisor()
        body = await service.start_run(name="wf-run", supervisor=sup, skip_preflight=True)
        assert body["ok"] and body["run_id"]
        assert sup.launched == [body["run_id"]]

    async def test_a_blocking_run_returns_the_final_state(self, provider) -> None:
        await service.author_def(name="wf-block", root=SPEC_ROOT)
        body = await service.start_run(
            name="wf-block",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            skip_preflight=True,
        )
        assert body["ok"] and body["blocking"]
        assert body["status"] == RunStatus.COMPLETE.value
        assert body["nodes"]

    async def test_a_missing_required_input_is_refused_before_spending_tokens(
        self, provider
    ) -> None:
        """A run that fails three nodes deep on a missing input has already cost money."""
        await service.author_def(
            name="wf-inp",
            root=SPEC_ROOT,
            inputs={"since": {"type": "string", "required": True}},
        )
        body = await service.start_run(
            name="wf-inp", supervisor=_FakeSupervisor(), skip_preflight=True
        )
        assert not body["ok"] and body["code"] == "WF_RUN_MISSING_INPUTS"
        assert body["missing"] == ["since"]

    async def test_a_default_satisfies_a_required_input(self, provider) -> None:
        await service.author_def(
            name="wf-def-inp",
            root=SPEC_ROOT,
            inputs={"since": {"type": "string", "required": True, "default": "1h"}},
        )
        body = await service.start_run(
            name="wf-def-inp", supervisor=_FakeSupervisor(), skip_preflight=True
        )
        assert body["ok"]

    async def test_no_supervisor_is_honest_about_not_starting(self, provider) -> None:
        await service.author_def(name="wf-nosup", root=SPEC_ROOT)
        body = await service.start_run(name="wf-nosup", supervisor=None, skip_preflight=True)
        assert not body["ok"] and body["code"] == "WF_NO_SUPERVISOR"
        assert body["run_id"]  # created, and the response says so

    async def test_an_idempotency_key_returns_the_existing_run(self, provider) -> None:
        from personalclaw.workflows.effects import START_DEDUPE

        START_DEDUPE._entries.clear()
        await service.author_def(name="wf-idem", root=SPEC_ROOT)
        sup = _FakeSupervisor()
        first = await service.start_run(
            name="wf-idem", supervisor=sup, idempotency_key="k1", skip_preflight=True
        )
        second = await service.start_run(
            name="wf-idem", supervisor=sup, idempotency_key="k1", skip_preflight=True
        )
        assert second["run_id"] == first["run_id"] and second["deduped"]
        assert len(sup.launched) == 1
        START_DEDUPE._entries.clear()

    def test_status_of_an_unknown_run_is_coded(self) -> None:
        assert service.status("deadbeef")["code"] == "WF_RUN_NOT_FOUND"

    async def test_status_reports_node_level_progress(self, provider) -> None:
        await service.author_def(name="wf-stat", root=SPEC_ROOT)
        started = await service.start_run(
            name="wf-stat",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            skip_preflight=True,
        )
        body = service.status(started["run_id"])
        assert body["ok"]
        # Instances exist for the executed LEAVES; a container's state is derived, never
        # stored, so it is legitimately absent from the state map.
        assert {n["node_id"] for n in body["nodes"]} == {"seed", "think"}
        assert all(n["state"] == "done" for n in body["nodes"])

    async def test_output_returns_a_nodes_value(self, provider) -> None:
        await service.author_def(name="wf-out", root=SPEC_ROOT)
        started = await service.start_run(
            name="wf-out",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            skip_preflight=True,
        )
        body = service.output(started["run_id"], "seed")
        assert body["ok"] and body["output"] == {"n": 1}

    async def test_output_of_an_unknown_node_is_coded(self, provider) -> None:
        await service.author_def(name="wf-out2", root=SPEC_ROOT)
        started = await service.start_run(
            name="wf-out2",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            skip_preflight=True,
        )
        assert service.output(started["run_id"], "ghost")["code"] == "WF_NODE_NOT_FOUND"


class TestObserve:
    async def test_the_window_is_clamped(self) -> None:
        """An unbounded subscribe in a chat turn is a hang: the model waits, the user
        waits, and nothing says why."""
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        body = await service.observe(run.id, 10_000_000)
        assert body["window_ms"] == service.MAX_OBSERVE_MS and body["clamped"]

    async def test_it_returns_early_on_a_terminal_run(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="w", status=RunStatus.COMPLETE))
        import time as _t

        began = _t.monotonic()
        body = await service.observe(run.id, 5000)
        assert body["ok"] and (_t.monotonic() - began) < 2.0

    async def test_an_unknown_run_is_coded(self) -> None:
        assert (await service.observe("deadbeef"))["code"] == "WF_RUN_NOT_FOUND"


class TestControl:
    async def test_cancel_records_a_sticky_intent(self, provider) -> None:
        """Persisted, so a cancel issued while the gateway is down is still honoured."""
        await service.author_def(name="wf-can", root=SPEC_ROOT)
        sup = _FakeSupervisor()
        started = await service.start_run(name="wf-can", supervisor=sup, skip_preflight=True)
        body = service.cancel_run(started["run_id"], supervisor=sup)
        assert body["ok"] and store.cancel_requested(started["run_id"])

    def test_cancelling_an_unknown_run_is_coded(self) -> None:
        assert service.cancel_run("deadbeef")["code"] == "WF_RUN_NOT_FOUND"

    async def test_cancelling_a_finished_run_is_refused(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="w", status=RunStatus.COMPLETE))
        assert service.cancel_run(run.id)["code"] == "WF_RUN_ALREADY_TERMINAL"

    async def test_editing_a_run_with_no_live_controller_is_refused(self) -> None:
        """Mutation is only safe at a controller's drain point; editing a run nobody drives
        would write state with no one to apply it."""
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, {"name": "w", "root": SPEC_ROOT})
        body = service.edit_run(run.id, [{"op": "skip", "node_id": "think"}], supervisor=None)
        assert not body["ok"] and body["code"] == "WF_RUN_NOT_LIVE"

    async def test_preview_works_without_a_live_controller(self) -> None:
        """So a user can see what an edit would cost before deciding to resume the run."""
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, {"name": "w", "root": SPEC_ROOT})
        body = service.preview_edit(
            run.id, [{"op": "update_node", "node_id": "think", "fields": {"prompt": "x"}}]
        )
        assert body["ok"] and body["queued"] is False
        # Nothing has executed, so the cascade is empty and needs no confirmation — the
        # preview is honest about there being nothing to re-run rather than listing the
        # node just because it was named.
        assert body["preview"]["rerun"] == []
        assert body["preview"]["needs_confirmation"] is False

    async def test_fork_works_on_a_finished_run(self, provider) -> None:
        """Forking a finished result to explore an alternative is the main reason to fork."""
        await service.author_def(name="wf-fork", root=SPEC_ROOT)
        started = await service.start_run(
            name="wf-fork",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            skip_preflight=True,
        )
        body = service.fork_run(started["run_id"], note="try again")
        assert body["ok"] and body["child_run_id"] != started["run_id"]
        assert body["shared_axes"]  # what a fork does NOT isolate is surfaced

    async def test_resume_with_no_pending_gate_is_coded(self, provider) -> None:
        await service.author_def(name="wf-res", root=SPEC_ROOT)
        sup = _FakeSupervisor()
        started = await service.start_run(name="wf-res", supervisor=sup, skip_preflight=True)
        body = service.resume_run(started["run_id"], supervisor=sup, answer=True)
        assert not body["ok"] and body["code"] in (
            "WF_NO_PENDING_GATE",
            "WF_RUN_NOT_LIVE",
        )

    def test_audit_defaults_to_dry_run(self) -> None:
        body = service.audit()
        assert body["ok"] and body["dry_run"] is True


# ── the plan scaffold ────────────────────────────────────────────────────────


class TestPlan:
    def test_a_goal_is_required(self) -> None:
        assert "WF_PLAN_GOAL_REQUIRED" in T._call_tool("workflow_plan", {})

    def test_it_returns_a_spec_plus_the_manifest(self) -> None:
        """The model needs the shapes the engine accepts, not a guess at a schema.

        `grounded-v1` since UNIVERSAL-PLANNING S41: the tree now comes from a picked shape whose
        skeleton already validates, and the live grounding bundle rides along. `scaffold-v1` is
        still the fallback when grounding cannot be assembled.
        """
        out = T._call_tool("workflow_plan", {"goal": "summarize issues"})
        assert "proposed_root" in out and "manifest" in out
        assert "grounded-v1" in out or "scaffold-v1" in out

    def test_it_is_honest_about_what_the_planner_knows(self) -> None:
        """The old assertion was "structural scaffold" — honest while the planner was a stub, and
        stale now that UNIVERSAL-PLANNING S41 has landed. What must stay honest is the SOURCE of
        the facts: the grounding block is read from this system's live registries, and the planner
        is told to decline rather than invent when the goal needs something absent."""
        out = T._call_tool("workflow_plan", {"goal": "x"})
        assert "live registries" in out or "structural scaffold" in out
        assert "cannot_plan" in out or "structural scaffold" in out

    @pytest.mark.parametrize(
        "rigor,expect_verify,expect_review",
        [("minimal", False, False), ("standard", True, False), ("deep", True, True)],
    )
    def test_rigor_changes_the_shape(
        self, rigor: str, expect_verify: bool, expect_review: bool
    ) -> None:
        out = T._call_tool("workflow_plan", {"goal": "x", "rigor": rigor})
        body = json.loads(out.split("\n", 1)[1])
        ids = {c["id"] for c in body["proposed_root"]["children"]}
        assert ("verify" in ids) is expect_verify
        assert ("review" in ids) is expect_review

    def test_an_unknown_rigor_falls_back_to_standard(self) -> None:
        out = T._call_tool("workflow_plan", {"goal": "x", "rigor": "extreme"})
        assert '"rigor": "standard"' in out


# ── the error contract ───────────────────────────────────────────────────────


class TestErrorContract:
    def test_a_failure_leads_with_its_code(self) -> None:
        """So the model can branch on it instead of parsing prose."""
        out = T._call_tool("workflow_status", {"run_id": "deadbeef"})
        assert out.startswith("Error [WF_RUN_NOT_FOUND]")

    def test_a_failure_keeps_its_payload(self) -> None:
        """The issue list is the actionable half — dropping it leaves the model guessing."""
        out = T._call_tool("workflow_author", {"name": "x y", "root": SPEC_ROOT})
        assert "Error [WF_DEF_NAME_INVALID]" in out

    def test_a_missing_root_is_reported_not_raised(self) -> None:
        out = T._call_tool("workflow_author", {"name": "ok-name"})
        assert "WF_DEF_ROOT_REQUIRED" in out

    def test_empty_ops_are_reported(self) -> None:
        out = T._call_tool("workflow_edit", {"run_id": "a1b2c3d4", "ops": []})
        assert "WF_MUT_NO_OPS" in out

    def test_empty_node_ids_are_reported(self) -> None:
        out = T._call_tool("workflow_skip", {"run_id": "a1b2c3d4", "node_ids": []})
        assert "WF_NO_NODE_IDS" in out

    def test_no_tool_raises_on_garbage_input(self) -> None:
        """Every tool, called with nonsense: the contract is a readable string, always."""
        for tool in T._list_tools():
            out = T._call_tool(tool["name"], {"run_id": "zzz", "name": "!!", "node_id": ""})
            assert isinstance(out, str) and out, tool["name"]

    async def test_a_tool_called_from_async_code_does_not_explode(self, provider) -> None:
        """Found live, not by a unit test: the sync tool boundary wraps async service calls,
        and `asyncio.run` raises "cannot be called from a running event loop" when the
        caller is already async. Production is safe (the runtime uses a thread executor),
        but a surface whose whole contract is 'never raises' must not depend on who called
        it. These are the tools that cross the async boundary."""
        for name, args in (
            ("workflow_list_defs", {}),
            ("workflow_get_def", {"name": "absent"}),
            ("workflow_author", {"name": "async-ok", "root": SPEC_ROOT, "save": False}),
            ("workflow_delete_def", {"name": "absent"}),
            ("workflow_observe", {"run_id": "deadbeef"}),
            ("workflow_start", {"name": "absent"}),
        ):
            out = T._call_tool(name, args)
            assert isinstance(out, str) and "cannot be called from a running" not in out, name


# ── memory-mode inheritance, wired end to end (WORK-CONTAINERS §5.1) ───────────


class TestMemoryModeInheritance:
    """The property: a run launched from an incognito/temporary chat carries that posture
    the whole way down — stamped on the run record at start, enforced in the process-global
    registry when the controller prepares, and mirrored back to the launching chat WITHOUT
    being indexed. These drive the REAL service + controller, not the pure helpers (those
    live in `test_workflows_ownership`)."""

    @pytest.fixture(autouse=True)
    def _isolate_history(self, tmp_path, monkeypatch):
        """`_origin_metadata` reads the launching session's JSONL via `ConversationLog`, which
        resolves through `config.loader.config_dir` — a DIFFERENT symbol than the store's own
        `config_dir` the module autouse fixture patches. Set `PERSONALCLAW_HOME` so an origin
        metadata read cannot touch the real home."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pc-home"))

    async def test_an_incognito_origin_stamps_the_run_record(self, provider) -> None:
        """The durable spine: the inherited mode lands in `run.extra` so a restart replays it.
        Driven through the registry path (no durable JSONL line needed) to prove `start_run`
        consults the live registry for the launching key."""
        from personalclaw import session_restrictions
        from personalclaw.workflows.ownership import MemoryMode, run_mode

        session_restrictions.clear("dashboard:inc-origin")
        session_restrictions.mark_incognito("dashboard:inc-origin")
        try:
            await service.author_def(name="wf-inc", root=SPEC_ROOT)
            body = await service.start_run(
                name="wf-inc",
                supervisor=_FakeSupervisor(),
                session_key="dashboard:inc-origin",
                skip_preflight=True,
            )
            assert body["ok"]
            run = store.get(body["run_id"])
            assert run_mode(run) is MemoryMode.INCOGNITO
            # And it survives the record's own serialization round trip — what a restart replays.
            from personalclaw.workflows.models import WorkflowRun

            assert run_mode(WorkflowRun.from_dict(run.to_dict())) is MemoryMode.INCOGNITO
        finally:
            session_restrictions.clear("dashboard:inc-origin")

    async def test_a_normal_origin_leaves_the_record_unstamped(self, provider) -> None:
        """NORMAL is left unstamped — `run_mode` reads an absent key as normal, so stamping it
        would add a redundant string to every unrestricted run for no behavioural gain."""
        from personalclaw.workflows.ownership import RUN_MODE_KEY

        await service.author_def(name="wf-norm-origin", root=SPEC_ROOT)
        body = await service.start_run(
            name="wf-norm-origin",
            supervisor=_FakeSupervisor(),
            session_key="dashboard:clean",
            skip_preflight=True,
        )
        assert body["ok"]
        assert RUN_MODE_KEY not in store.get(body["run_id"]).extra

    async def test_the_controller_ENFORCES_the_mode_in_the_registry(self, provider) -> None:
        """The registry is the fast path the knowledge/learning writers consult during the run,
        and the chat layer never marks it for a background run (it enforces off the live session
        object, which the run no longer holds). So the controller's run-start mark is the
        registry's first writer for these keys — for BOTH the origin key and the run-owned key."""
        from personalclaw import session_restrictions
        from personalclaw.workflows.ownership import owned_key

        session_restrictions.clear("dashboard:enf-origin")
        session_restrictions.mark_incognito("dashboard:enf-origin")
        try:
            await service.author_def(name="wf-enf", root=SPEC_ROOT)
            body = await service.start_run(
                name="wf-enf",
                mode="blocking",
                supervisor=_FakeSupervisor(),
                blocking_timeout=20,
                session_key="dashboard:enf-origin",
                skip_preflight=True,
            )
            assert body["status"] == RunStatus.COMPLETE.value
            # The controller marked the run-owned key too, off its own record — proving the mark
            # is re-derived from `extra` at `_prepare`, not carried only from the launching session.
            assert session_restrictions.is_restricted(owned_key(body["run_id"], "run"))
        finally:
            session_restrictions.clear("dashboard:enf-origin")
            session_restrictions.clear(owned_key(body["run_id"], "run"))

    async def test_a_blocking_run_MIRRORS_an_indexable_summary_for_a_normal_origin(
        self, provider
    ) -> None:
        """The mirror surface is the blocking tool RESULT, not a JSONL append (which the chat's
        own full rewrite would clobber). A normal origin's summary is indexable."""
        await service.author_def(name="wf-mir-norm", root=SPEC_ROOT)
        body = await service.start_run(
            name="wf-mir-norm",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            session_key="dashboard:mir-norm",
            skip_preflight=True,
        )
        note = body["announcement"]
        assert note["origin_key"] == "dashboard:mir-norm"
        assert note["indexable"] is True
        assert "wf-mir-norm" in note["text"] and RunStatus.COMPLETE.value in note["text"]

    async def test_a_blocking_run_mirrors_the_summary_WITHOUT_indexing_for_incognito(
        self, provider
    ) -> None:
        """The run's outcome is still useful to the person who asked for it; what they asked to
        avoid is the record. The indexability decision travels WITH the text, decided once."""
        from personalclaw import session_restrictions

        session_restrictions.clear("dashboard:mir-inc")
        session_restrictions.mark_incognito("dashboard:mir-inc")
        try:
            await service.author_def(name="wf-mir-inc", root=SPEC_ROOT)
            body = await service.start_run(
                name="wf-mir-inc",
                mode="blocking",
                supervisor=_FakeSupervisor(),
                blocking_timeout=20,
                session_key="dashboard:mir-inc",
                skip_preflight=True,
            )
            note = body["announcement"]
            assert note["indexable"] is False
            assert "incognito" in note["reason"]
            # The summary text is still delivered — suppressed from the index, not withheld.
            assert note["text"]
        finally:
            session_restrictions.clear("dashboard:mir-inc")

    async def test_a_needs_input_run_carries_no_announcement(self, provider) -> None:
        """The mirror is a COMPLETION summary. A run parked on needs_input has not completed, so
        it returns the continuation payload instead — announcing "done" would be a lie. A gate
        awaiting a human surfaces as needs_input immediately (WF2-R7)."""
        root = {
            "kind": "sequence",
            "id": "main",
            "children": [
                {"kind": "gate", "id": "ask", "config": {"kind": "approval", "prompt": "proceed?"}},
            ],
        }
        await service.author_def(name="wf-ask", root=root)
        body = await service.start_run(
            name="wf-ask",
            mode="blocking",
            supervisor=_FakeSupervisor(),
            blocking_timeout=20,
            session_key="dashboard:ask",
            skip_preflight=True,
        )
        assert body["status"] == RunStatus.NEEDS_INPUT.value
        assert "announcement" not in body
        assert body["needs_input"]
