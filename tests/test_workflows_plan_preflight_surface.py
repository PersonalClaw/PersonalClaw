"""WF2UNI-3/WF2UNI-4 — preflight and the review surface reach `workflow_plan`, on BOTH paths.

Measured before this change: `workflows/preflight.py` had exactly two production importers
(`workflows/service.py` at authoring dry-run and at run start) and neither was the planner, so the
template-path response carried no `preflight` key and none of the report's own keys. The
consequence is the whole `plan-approved-run-dies-at-step-1` class UP-R3 exists to kill: preflight
at run start only tells a user their *approved* plan cannot run.

The plan's execution log named the real obstacle — the preflight step wanted requirements
aggregated one hop past a referenced provider, and that data does not exist: `ActionProvider`
declares no `requirements`. So the honest shape is the one asserted here — emit everything that IS
resolvable, and report the un-aggregatable class as a DISCRIMINATED warning. The anti-inertness
assertions are the point of this suite: a `preflight` key that merely exists, or an `ok=True`
produced by checking nothing, is the failure mode, not the fix.

`_review_surface` is checked on the scaffold path for the same reason (WF2UNI-4): that path's own
`next_step` tells the model to adapt the tree while the `NO_UPDATE` sentinel and the merge-by-id
ops that make an adaptation safe only ever reached the template path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import personalclaw.mcp_workflows as mw
from personalclaw.workflows import preflight as PF
from personalclaw.workflows.revision import NO_UPDATE

# A tree that resolves a model tier AND names a literal action provider, so every check has
# something real to say. `{{secret:...}}` is referenced but not declared — the union preflight
# actually resolves.
DEF_WITH_PROVIDER: dict[str, Any] = {
    "name": "shipper",
    "description": "ship it",
    "inputs": {},
    "metadata": {"requirements": {"credentials": [], "binaries": []}},
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "stage", "id": "draft", "config": {"prompt": "x", "model_tier": "standard"}},
            {
                "kind": "action",
                "id": "run",
                "config": {"provider": "bash", "command": "echo {{secret:SHIP_TOKEN}}"},
            },
        ],
    },
}

DEF_NO_ACTIONS: dict[str, Any] = {
    "name": "thinker",
    "inputs": {},
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "stage", "id": "draft", "config": {"prompt": "x", "model_tier": "fast"}}
        ],
    },
}


def _resolvable(monkeypatch: Any, *, yes: bool = True) -> None:
    """Pin the model probe so a report's shape is not a function of the dev home's providers."""
    from personalclaw.providers import provider_bridge

    monkeypatch.setattr(provider_bridge, "can_resolve_use_case", lambda _uc: yes)


def _body(rendered: str) -> dict[str, Any]:
    """`_fmt` renders `summary\\n{json}`; the summary line is prose for the model."""
    return json.loads(rendered.split("\n", 1)[1])


# ── the gap function: discriminated, and NOT unconditional ──


class TestProviderRequirementGap:
    def test_a_referenced_provider_yields_the_unchecked_requirements_finding(self) -> None:
        findings = PF.provider_requirement_gap(DEF_WITH_PROVIDER)
        codes = [f.code for f in findings]
        assert "WF_PRE_PROVIDER_REQUIREMENTS_UNCHECKED" in codes
        gap = next(f for f in findings if f.code == "WF_PRE_PROVIDER_REQUIREMENTS_UNCHECKED")
        # The provider is NAMED, so the caveat is actionable rather than a shrug.
        assert "bash" in gap.message
        assert gap.severity == PF.SEVERITY_WARNING
        assert gap.remediation

    def test_a_spec_with_no_action_nodes_yields_nothing(self) -> None:
        # The vacuity control: if this fired here the finding would be decoration, not a
        # measurement of what went unchecked.
        assert PF.provider_requirement_gap(DEF_NO_ACTIONS) == []

    def test_a_rootless_spec_yields_nothing(self) -> None:
        assert PF.provider_requirement_gap({"name": "x"}) == []

    def test_a_bound_provider_name_gets_its_own_code(self) -> None:
        spec = {
            "root": {
                "kind": "action",
                "id": "a",
                "config": {"provider": "{{inputs.which}}"},
            }
        }
        findings = PF.provider_requirement_gap(spec)
        assert [f.code for f in findings] == ["WF_PRE_PROVIDER_BOUND_UNCHECKED"]
        # A binding is skipped by `_check_action_providers` entirely, so without this the report
        # says nothing at all about a provider it never looked at.
        assert findings[0].severity == PF.SEVERITY_WARNING

    def test_the_gap_is_not_part_of_the_run_start_gate(self) -> None:
        # Run start is a GATE; a permanently-unactionable warning on every action-using run is
        # the noise that gets a rule suppressed wholesale. `preflight()` must stay silent here.
        report = PF.preflight(DEF_WITH_PROVIDER, model_probe=lambda _uc: True)
        assert not [
            f
            for f in report.findings
            if f.code
            in ("WF_PRE_PROVIDER_REQUIREMENTS_UNCHECKED", "WF_PRE_PROVIDER_BOUND_UNCHECKED")
        ]


# ── the surface emitter ──


class TestPreflightSurface:
    def test_it_emits_the_reports_own_keys(self, monkeypatch: Any) -> None:
        _resolvable(monkeypatch)
        surface = mw._preflight_surface(DEF_WITH_PROVIDER)
        assert set(surface) == {"preflight"}
        assert set(surface["preflight"]) == {"ok", "findings", "checked"}

    def test_it_checks_all_four_classes_not_just_the_cheap_ones(self, monkeypatch: Any) -> None:
        _resolvable(monkeypatch)
        checked = mw._preflight_surface(DEF_WITH_PROVIDER)["preflight"]["checked"]
        assert set(checked) == {"credentials", "binaries", "models", "action_providers"}
        # The REFERENCED secret is checked even though nothing declared it — that reference is
        # what the engine will actually try to resolve.
        assert checked["credentials"] == ["SHIP_TOKEN"]
        # `model_tier: standard` resolves through DEFAULT_MODEL_TIERS, not verbatim.
        assert checked["models"] == ["orchestration"]
        assert checked["action_providers"] == ["bash"]

    def test_an_unaggregatable_provider_requirement_is_a_finding_not_a_silent_ok(
        self, monkeypatch: Any
    ) -> None:
        """The anti-inertness assertion this atom turns on.

        `ActionProvider` declares no requirements, so what `bash` itself needs cannot be checked
        here. The report must SAY that. An `ok` with an empty finding list would be a preflight
        that checked a class it never examined, which re-observes `partial`.
        """
        _resolvable(monkeypatch)
        report = mw._preflight_surface(DEF_WITH_PROVIDER)["preflight"]
        gaps = [
            f for f in report["findings"] if f["code"] == "WF_PRE_PROVIDER_REQUIREMENTS_UNCHECKED"
        ]
        assert len(gaps) == 1
        # WARNING, so coverage honesty never refuses a plan — but never absent either.
        assert gaps[0]["severity"] == PF.SEVERITY_WARNING
        assert "bash" in gaps[0]["message"]

    def test_a_real_missing_requirement_is_an_error_that_flips_ok(self, monkeypatch: Any) -> None:
        # The other direction: the report is not merely decorative. An unresolvable model is an
        # ERROR and `ok` follows it, which is what makes a plan-time green worth anything.
        _resolvable(monkeypatch, yes=False)
        report = mw._preflight_surface(DEF_NO_ACTIONS)["preflight"]
        assert report["ok"] is False
        assert "WF_PRE_MODEL_UNRESOLVED" in [f["code"] for f in report["findings"]]

    def test_it_is_best_effort(self, monkeypatch: Any) -> None:
        from personalclaw.workflows import preflight as mod

        monkeypatch.setattr(mod, "preflight", lambda _s: (_ for _ in ()).throw(RuntimeError("no")))
        # A surface that raised must cost the user advice, never the plan.
        assert mw._preflight_surface(DEF_WITH_PROVIDER) == {}


# ── the TEMPLATE path (WF2UNI-3's clause) ──


class TestTemplatePathWiring:
    @pytest.fixture
    def planned(self, monkeypatch: Any) -> dict[str, Any]:
        _resolvable(monkeypatch)

        async def fake_get_def(name: str) -> dict[str, Any]:
            return {"ok": True, "definition": DEF_WITH_PROVIDER}

        monkeypatch.setattr(mw.service, "get_def", fake_get_def)
        return _body(mw._plan_from_template("ship the thing", "shipper"))

    def test_the_response_carries_a_preflight_report(self, planned: dict[str, Any]) -> None:
        assert planned["planner"] == "template-v1"
        assert set(planned["preflight"]) == {"ok", "findings", "checked"}

    def test_the_report_is_not_empty_scaffolding(self, planned: dict[str, Any]) -> None:
        # The key existing proves nothing; a report that checked nothing would pass that.
        assert planned["preflight"]["checked"]["action_providers"] == ["bash"]
        assert planned["preflight"]["findings"]

    def test_the_contract_review_and_review_surface_still_ride_along(
        self, planned: dict[str, Any]
    ) -> None:
        # Guard against the new emitter displacing what this path already carried.
        for key in ("stage_contracts", "decisions", "announce", "revision_grammar", "autonomy"):
            assert key in planned


# ── the SCAFFOLD path (WF2UNI-4's clause) ──


class TestScaffoldPathWiring:
    @pytest.fixture
    def planned(self, monkeypatch: Any) -> dict[str, Any]:
        _resolvable(monkeypatch)
        # No template may match, or this exercises the template path instead.
        monkeypatch.setattr(mw, "_match_library", lambda *a, **k: None)
        return _body(mw._plan({"goal": "write a haiku about cold starts"}))

    def test_it_is_actually_the_scaffold_path(self, planned: dict[str, Any]) -> None:
        assert planned["planner"] in ("scaffold-v1", "grounded-v1")
        assert planned["proposed_root"]

    def test_the_revision_grammar_reaches_the_model_here_too(self, planned: dict[str, Any]) -> None:
        # The measured asymmetry: `next_step` says "adapt this tree" while the vocabulary for a
        # safe adaptation only reached the template path.
        grammar = planned["revision_grammar"]
        assert grammar["no_update_sentinel"] == NO_UPDATE
        assert set(grammar["ops"]) == {"replace", "add", "remove", "annotate"}

    def test_the_rest_of_the_review_surface_comes_with_it(self, planned: dict[str, Any]) -> None:
        for key in ("announce", "cost_estimate", "plan_markdown", "inferred"):
            assert key in planned

    def test_the_announce_block_reads_the_reported_routing(self, planned: dict[str, Any]) -> None:
        # One dict, hoisted: the announce chips and `routing` cannot disagree, which is the one
        # drift a reader has no way to detect.
        assert planned["routing"]["intent"]["rigor"]

    def test_preflight_reaches_this_path_as_well(self, planned: dict[str, Any]) -> None:
        assert set(planned["preflight"]) == {"ok", "findings", "checked"}
        # A generated tree resolves model tiers, so the models class is real work here.
        assert planned["preflight"]["checked"]["models"]
