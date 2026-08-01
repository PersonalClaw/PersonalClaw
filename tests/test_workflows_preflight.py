"""Run-start preflight (WF2-R12) and the manifest drift gate.

Preflight's whole value is timing: a missing credential caught at start costs nothing,
caught at node 7 has already paid for six nodes of model calls. The load-bearing claims:

* **missing is an ERROR; unverifiable is a WARNING** — refusing a run because the CHECKER
  was unavailable is its own outage, so the two cases must never collapse;
* referenced `{{secret:KEY}}` names are checked even when nobody declared them — the
  reference is what the engine will actually try to resolve;
* the model check reuses `can_resolve_use_case`, the same probe behind onboarding, so
  preflight cannot greenlight a run the bridge can't resolve;
* a BOUND provider name is skipped rather than guessed at;
* **the manifest matches the engine's own enums** — a drifted catalog makes an author write
  specs the engine rejects, which is the manifest-vs-reality bug class this repo keeps
  refinding.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import preflight as PF

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


SPEC_LLM = {
    "name": "llm",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "infer", "id": "fast", "config": {"prompt": "x", "model_tier": "fast"}},
            {
                "kind": "stage",
                "id": "deep",
                "config": {"prompt": "y", "model_tier": "reasoning"},
            },
        ],
    },
}


def _spec(root: dict, **meta) -> dict:
    return {"name": "w", "root": root, "metadata": meta}


# ── credentials ──────────────────────────────────────────────────────────────


class TestCredentials:
    def test_a_missing_declared_credential_is_an_error(self) -> None:
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"credentials": ["GH_TOKEN"]}),
            credential_resolver=lambda k: False,
        )
        assert not result.ok
        assert [f.code for f in result.errors] == ["WF_PRE_CREDENTIAL_MISSING"]
        assert "GH_TOKEN" in result.errors[0].message

    def test_a_present_credential_passes(self) -> None:
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"credentials": ["GH_TOKEN"]}),
            credential_resolver=lambda k: True,
        )
        assert result.ok

    def test_a_referenced_secret_is_checked_even_if_undeclared(self) -> None:
        """The reference is what the engine will actually resolve; a spec can reference a
        key nobody remembered to declare."""
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "action",
                        "id": "a",
                        "config": {"provider": "bash", "token": "{{secret:UNDECLARED}}"},
                    }
                ],
            }
        )
        result = PF.preflight(
            spec, credential_resolver=lambda k: False, provider_lookup=lambda n: object()
        )
        assert "UNDECLARED" in result.checked["credentials"]
        assert not result.ok

    def test_an_unavailable_store_warns_rather_than_blocking(self) -> None:
        """Refusing a run because the CHECKER was unavailable is its own outage."""

        def exploding(key: str) -> bool:
            raise RuntimeError("store offline")

        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"credentials": ["K"]}),
            credential_resolver=exploding,
        )
        # A per-key failure is skipped, not converted into a false "missing".
        assert result.ok

    def test_no_requirements_means_nothing_to_check(self) -> None:
        result = PF.preflight(_spec({"kind": "sequence", "id": "s"}))
        assert result.ok and result.checked["credentials"] == []


# ── binaries ─────────────────────────────────────────────────────────────────


class TestBinaries:
    def test_a_missing_binary_is_an_error(self) -> None:
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"binaries": ["gh"]}),
            which=lambda b: None,
        )
        assert not result.ok
        assert result.errors[0].code == "WF_PRE_BINARY_MISSING"
        assert "install gh" in result.errors[0].remediation

    def test_a_present_binary_passes(self) -> None:
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"binaries": ["gh"]}),
            which=lambda b: "/usr/bin/gh",
        )
        assert result.ok

    def test_the_real_which_finds_a_real_binary(self) -> None:
        """One check against the real PATH, so the injected default is not the only path
        ever exercised."""
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"binaries": ["sh"]})
        )
        assert result.ok


# ── models ───────────────────────────────────────────────────────────────────


class TestModels:
    def test_every_llm_nodes_use_case_is_collected(self) -> None:
        seen: list[str] = []

        def probe(uc: str) -> bool:
            seen.append(uc)
            return True

        result = PF.preflight(SPEC_LLM, model_probe=probe)
        assert result.ok
        # fast → background, reasoning → reasoning (the default tier map).
        assert set(seen) == {"background", "reasoning"}

    def test_an_unresolvable_use_case_is_an_error(self) -> None:
        result = PF.preflight(SPEC_LLM, model_probe=lambda uc: False)
        assert not result.ok
        assert {f.code for f in result.errors} == {"WF_PRE_MODEL_UNRESOLVED"}
        assert len(result.errors) == 2  # one per distinct use case

    def test_a_judge_gate_resolves_on_the_reasoning_tier(self) -> None:
        """Matching engine.dispatch_gate — a judge reasons, so preflight must check the tier
        the engine will actually use, not the node's absent declaration."""
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "gate", "id": "j", "config": {"kind": "judge", "prompt": "ok?"}}
                ],
            }
        )
        result = PF.preflight(spec, model_probe=lambda uc: True)
        assert result.checked["models"] == ["reasoning"]

    def test_a_spec_with_no_llm_nodes_checks_no_models(self) -> None:
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "transform", "id": "t", "config": {"expr": 1}}],
            }
        )
        result = PF.preflight(spec, model_probe=lambda uc: False)
        assert result.ok and result.checked["models"] == []

    def test_a_custom_tier_map_is_honoured(self) -> None:
        spec = dict(SPEC_LLM)
        spec["defaults"] = {"model_tiers": {"fast": "orchestration"}}
        seen: list[str] = []
        PF.preflight(spec, model_probe=lambda uc: seen.append(uc) or True)
        assert "orchestration" in seen

    def test_it_uses_the_same_probe_as_onboarding(self) -> None:
        """A private capability check could disagree with what the bridge really resolves,
        and then preflight would greenlight an unrunnable run."""
        import inspect

        source = inspect.getsource(PF._check_models)
        assert "can_resolve_use_case" in source


# ── action providers ─────────────────────────────────────────────────────────


class TestActionProviders:
    def test_an_unknown_provider_is_an_error(self) -> None:
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "action", "id": "a", "config": {"provider": "nope"}}],
            }
        )
        result = PF.preflight(spec, provider_lookup=lambda n: None)
        assert not result.ok
        assert result.errors[0].code == "WF_PRE_PROVIDER_UNKNOWN"

    def test_a_registered_provider_passes(self) -> None:
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "action", "id": "a", "config": {"provider": "bash"}}],
            }
        )
        assert PF.preflight(spec, provider_lookup=lambda n: object()).ok

    def test_a_bound_provider_name_is_skipped_not_guessed(self) -> None:
        """It resolves at dispatch; guessing at a binding's future value would produce a
        false failure."""
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "action", "id": "a", "config": {"provider": "{{inputs.which}}"}}
                ],
            }
        )
        result = PF.preflight(spec, provider_lookup=lambda n: None)
        assert result.ok and result.checked["action_providers"] == []

    def test_the_real_registry_knows_bash(self) -> None:
        spec = _spec(
            {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "action", "id": "a", "config": {"provider": "bash"}}],
            }
        )
        assert PF.preflight(spec).ok


# ── result shape ─────────────────────────────────────────────────────────────


class TestResultShape:
    def test_warnings_never_block(self) -> None:
        result = PF.PreflightResult(
            findings=[PF.Finding(code="X", message="m", severity=PF.SEVERITY_WARNING)]
        )
        assert result.ok and result.warnings and not result.errors

    def test_an_unparseable_spec_is_left_to_the_validator(self) -> None:
        """Reporting it twice under two vocabularies would just be noise."""
        result = PF.preflight({"name": "w", "root": {"kind": "nonsense"}})
        assert result.ok

    def test_a_spec_with_no_root_is_tolerated(self) -> None:
        assert PF.preflight({"name": "w"}).ok

    def test_it_serializes_with_findings_and_what_was_checked(self) -> None:
        body = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"binaries": ["zzz-absent"]}),
            which=lambda b: None,
        ).to_dict()
        assert set(body) == {"ok", "findings", "checked"}
        assert body["ok"] is False
        assert body["checked"]["binaries"] == ["zzz-absent"]

    def test_a_finding_separates_diagnosis_from_next_step(self) -> None:
        """Collapsing them leaves the user with a problem and no action."""
        result = PF.preflight(
            _spec({"kind": "sequence", "id": "s"}, requirements={"credentials": ["K"]}),
            credential_resolver=lambda k: False,
        )
        finding = result.errors[0]
        assert finding.message and finding.remediation
        assert finding.message != finding.remediation


# ── the start_run gate ───────────────────────────────────────────────────────


class TestStartRunIsGated:
    """The behaviour every `skip_preflight=True` in the tool tests implies: without the
    skip, a run that cannot possibly work is refused BEFORE it starts."""

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
        return home

    async def test_a_run_needing_an_unconfigured_model_is_refused(self) -> None:
        """The whole point: caught here it costs nothing; caught at node 7 it has already
        paid for six nodes."""
        from personalclaw.workflows import defs as defs_mod
        from personalclaw.workflows import service

        class Mem(defs_mod.WorkflowDefProvider):
            def __init__(self) -> None:
                self.d: dict = {}

            @property
            def name(self) -> str:
                return "pf-mem"

            @property
            def readonly(self) -> bool:
                return False

            async def list_defs(self, *, limit: int = 200, offset: int = 0):
                return list(self.d.values()), len(self.d)

            async def get_def(self, name: str):
                return self.d.get(name)

            async def save_def(self, **f):
                self.d[f["name"]] = dict(f)
                return self.d[f["name"]]

            async def delete_def(self, name: str) -> bool:
                return self.d.pop(name, None) is not None

        defs_mod.register_provider(Mem())
        try:
            await service.author_def(name="needs-model", root=SPEC_LLM["root"])
            body = await service.start_run(name="needs-model", supervisor=object())
            assert not body["ok"] and body["code"] == "WF_RUN_PREFLIGHT_FAILED"
            assert body["preflight"]["findings"]
        finally:
            defs_mod.unregister_provider("pf-mem")

    async def test_a_model_free_workflow_starts_without_a_model(self) -> None:
        """Preflight must not demand what a spec does not use — a pure-transform workflow
        needs no model at all."""
        from personalclaw.workflows import defs as defs_mod
        from personalclaw.workflows import service

        class Mem(defs_mod.WorkflowDefProvider):
            def __init__(self) -> None:
                self.d: dict = {}

            @property
            def name(self) -> str:
                return "pf-mem2"

            @property
            def readonly(self) -> bool:
                return False

            async def list_defs(self, *, limit: int = 200, offset: int = 0):
                return list(self.d.values()), len(self.d)

            async def get_def(self, name: str):
                return self.d.get(name)

            async def save_def(self, **f):
                self.d[f["name"]] = dict(f)
                return self.d[f["name"]]

            async def delete_def(self, name: str) -> bool:
                return self.d.pop(name, None) is not None

        class Sup:
            def __init__(self) -> None:
                self.launched: list[str] = []

            def controller(self, run_id: str):
                return None

            async def launch(self, run, spec, *, depth: int = 0):
                self.launched.append(run.id)

                class C:
                    pass

                return C()

        defs_mod.register_provider(Mem())
        try:
            await service.author_def(
                name="pure",
                root={
                    "kind": "sequence",
                    "id": "s",
                    "children": [{"kind": "transform", "id": "t", "config": {"expr": 1}}],
                },
            )
            sup = Sup()
            body = await service.start_run(name="pure", supervisor=sup)
            assert body["ok"] and sup.launched
        finally:
            defs_mod.unregister_provider("pf-mem2")


# ── the manifest drift gate ──────────────────────────────────────────────────


class TestManifestDrift:
    """The manifest is GENERATED, and this is the CI gate that keeps it honest. A drifted
    catalog makes an author write specs the engine rejects — the manifest-vs-reality bug
    class this codebase keeps refinding."""

    def test_node_kinds_match_the_enum_exactly(self) -> None:
        from personalclaw.workflows.models import NodeKind
        from personalclaw.workflows.service import manifest

        assert {k["kind"] for k in manifest()["node_kinds"]} == {k.value for k in NodeKind}

    def test_container_flags_match_the_real_set(self) -> None:
        from personalclaw.workflows.models import CONTAINER_KINDS
        from personalclaw.workflows.service import manifest

        containers = {k["kind"] for k in manifest()["node_kinds"] if k["container"]}
        assert containers == {k.value for k in CONTAINER_KINDS}

    def test_lanes_match_lane_for(self) -> None:
        from personalclaw.workflows.models import NodeKind, lane_for
        from personalclaw.workflows.service import manifest

        for entry in manifest()["node_kinds"]:
            assert entry["lane"] == lane_for(NodeKind(entry["kind"])), entry["kind"]

    def test_gate_kinds_match_the_enum(self) -> None:
        from personalclaw.workflows.models import GateKind
        from personalclaw.workflows.service import manifest

        assert set(manifest()["gate_kinds"]) == {g.value for g in GateKind}

    def test_mutation_ops_match_the_enum(self) -> None:
        from personalclaw.workflows.mutations import OpKind
        from personalclaw.workflows.service import manifest

        assert set(manifest()["mutation_ops"]) == {o.value for o in OpKind}

    def test_pipes_match_the_real_pipe_table(self) -> None:
        from personalclaw.workflows.bindings import PIPES
        from personalclaw.workflows.service import manifest

        assert set(manifest()["pipes"]) == set(PIPES)

    def test_instance_states_and_run_statuses_match(self) -> None:
        from personalclaw.workflows.models import InstanceState, RunStatus
        from personalclaw.workflows.service import manifest

        body = manifest()
        assert set(body["instance_states"]) == {s.value for s in InstanceState}
        assert set(body["run_statuses"]) == {s.value for s in RunStatus}

    def test_loop_and_item_error_modes_match(self) -> None:
        from personalclaw.workflows.models import ItemErrorPolicy, JoinMode, LoopMode
        from personalclaw.workflows.service import manifest

        body = manifest()
        assert set(body["join_modes"]) == {j.value for j in JoinMode}
        assert set(body["loop_modes"]) == {m.value for m in LoopMode}
        assert set(body["item_error_policies"]) == {p.value for p in ItemErrorPolicy}

    def test_the_spec_semver_is_reported(self) -> None:
        from personalclaw.workflows.models import SPEC_SEMVER
        from personalclaw.workflows.service import manifest

        assert manifest()["spec_semver"] == SPEC_SEMVER
