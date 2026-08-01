"""Shared prompt blocks and the template lint (Slice 9b, WF2-R15).

Six templates independently defined the Finding record THREE times. That is the problem in one
sentence: a convention repeated by hand drifts, and drift in a Finding record breaks things far
from the text — a gate predicate like "no open Critical" stops being meaningful once one stage
grades on a different ladder, and the Run Ledger stops being minable by the flywheel.

So a template writes `{{block:finding-record}}` and the text is substituted at DEFINITION time,
next to macro expansion and for the same reason: what is stored, what is validated and what the
engine runs are one tree, and no run-time component learns blocks exist.

The lint is the other half. `validate_spec` answers "will the engine run this?"; the lint answers
"does this follow the conventions that keep a growing library coherent?" — which is an authoring
standard, not a run requirement, and therefore advice for a user's own workflow and a hard gate
for the shipped library.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import blocks
from personalclaw.workflows.bundled_defs import bundled_root, template_names
from personalclaw.workflows.macros import expand_spec
from personalclaw.workflows.template_lint import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    lint_template,
)


@pytest.fixture(autouse=True)
def _isolated_def_registry():
    """Restore the def-provider registry after every test in this module.

    `defs._providers` is process-GLOBAL. A test here that registers the bundled (or native)
    provider and walks away leaves it registered for every later test in the session — which is
    exactly what broke CI: `test_workflows_tools.py` asserts "one def, from my fake provider" and
    saw seven, because six bundled templates were still visible from this module's registrations.

    Snapshot-and-restore rather than clear-on-exit, so a provider that was legitimately registered
    before this module ran survives it.
    """
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    try:
        yield
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


def _raw(name: str) -> dict:
    return json.loads((bundled_root() / name / "workflow.json").read_text(encoding="utf-8"))


class TestBlockLibrary:
    def test_the_conventions_pack_ships(self) -> None:
        """`bundled/shared/` is the plan's named location for these."""
        assert set(blocks.block_names()) >= {"finding-record", "safety-tiers", "gap-honesty"}

    def test_every_block_has_real_content(self) -> None:
        """An empty block resolves to nothing, which would silently remove a convention from
        every prompt citing it — the failure would look like the model ignoring instructions."""
        for name in blocks.block_names():
            assert len(blocks.read_block(name)) > 200, f"{name} is too thin to be a convention"

    def test_the_finding_record_defines_the_whole_ladder(self) -> None:
        """The severities are what gate predicates and the widget key on, so all four must be
        defined — not just named."""
        text = blocks.read_block("finding-record")
        for level in ("Critical", "Major", "Minor", "Nit"):
            assert level in text
        for field in ("severity", "location", "problem", "why", "recommended_fix", "status"):
            assert field in text, field

    def test_a_missing_block_reads_as_absent_not_as_a_crash(self) -> None:
        assert blocks.read_block("no-such-block") == ""


class TestResolution:
    def test_a_reference_is_replaced_by_the_real_text(self) -> None:
        out = blocks.resolve_text("Report issues.\n\n{{block:finding-record}}")
        assert "{{block:" not in out
        assert "Critical" in out

    def test_an_unknown_reference_is_an_ERROR_not_a_passthrough(self) -> None:
        """A literal `{{block:…}}` reaching a model is a convention silently not applied — the
        model either ignores it or invents what it guessed was meant, which is worse than a loud
        failure. The message lists what IS available, because a typo is the common case."""
        with pytest.raises(blocks.BlockError) as exc:
            blocks.resolve_text("{{block:finding-recrod}}")
        assert "finding-record" in str(exc.value)

    def test_resolution_recurses_through_a_whole_spec(self) -> None:
        spec = {
            "root": {
                "kind": "sequence",
                "children": [
                    {"kind": "infer", "config": {"prompt": "{{block:gap-honesty}}"}},
                    {"kind": "stage", "config": {"prompt": "plain"}},
                ],
            }
        }
        out = blocks.resolve_spec(spec)
        assert blocks.has_refs(out) is False
        assert "unanswered question" in out["root"]["children"][0]["config"]["prompt"]

    def test_it_does_not_mutate_the_input(self) -> None:
        """The save path re-reads the author's original for its response."""
        spec = {"root": {"kind": "infer", "config": {"prompt": "{{block:gap-honesty}}"}}}
        before = json.dumps(spec)
        blocks.resolve_spec(spec)
        assert json.dumps(spec) == before

    def test_a_binding_reference_is_left_alone(self) -> None:
        """The two syntaxes deliberately look alike so an author reads them the same way, but
        they resolve at different TIMES — blocks at authoring, bindings at run. A block resolver
        that ate `{{nodes.x.output}}` would break every spec."""
        text = "{{nodes.a.output}} and {{inputs.q}} and {{secret:KEY}}"
        assert blocks.resolve_text(text) == text

    def test_refs_in_finds_references_anywhere(self) -> None:
        found = blocks.refs_in(
            {"a": "{{block:safety-tiers}}", "b": ["{{block:gap-honesty}}"], "c": 42}
        )
        assert found == {"safety-tiers", "gap-honesty"}


class TestLibraryUsesTheBlocks:
    def test_no_template_defines_the_Finding_record_by_hand(self) -> None:
        """The migration this session did: three hand-written copies (two templates plus the
        judge-panel macro) became three references to one definition."""
        for name in template_names():
            text = json.dumps(_raw(name))
            assert "A Finding is {" not in text, f"{name} still defines the Finding record inline"

    def test_the_review_templates_CITE_the_block(self) -> None:
        """The complement: having removed the inline copies, the convention must actually still
        reach the model."""
        for name in ("audit-sweep", "produce-and-audit"):
            assert "{{block:finding-record}}" in json.dumps(_raw(name)), name

    def test_the_judge_panel_macro_cites_it_too(self) -> None:
        """The macro is Python, so it emits the REFERENCE and blocks resolve after expansion —
        one definition governs the library's review stages including the generated ones."""
        expanded = expand_spec(
            {
                "name": "t",
                "root": {
                    "macro": "judge_panel",
                    "id": "p",
                    "config": {"subject": "x", "lenses": ["a"]},
                },
            }
        )
        assert "{{block:finding-record}}" in json.dumps(expanded)
        resolved = blocks.resolve_spec(expanded)
        assert "Critical|Major|Minor|Nit" in json.dumps(resolved)

    def test_what_the_provider_SERVES_has_no_unresolved_references(self) -> None:
        """The invariant, same as macros: nothing downstream of the provider knows blocks exist."""
        from personalclaw.workflows.bundled_defs import read_template

        for name in template_names():
            loaded = read_template(name)
            assert loaded is not None
            assert blocks.has_refs(loaded.to_dict()) is False, name


class TestLint:
    def test_the_whole_shipped_library_is_lint_CLEAN(self) -> None:
        """Clean, not merely ok: the library is held to warnings-free too, because a warning that
        ships propagates to every template copied from it."""
        for name in template_names():
            result = lint_template(_raw(name), bundled=True)
            assert result.clean, (name, [f.to_dict() for f in result.findings])

    def test_an_inline_convention_is_an_ERROR(self) -> None:
        """An error rather than a warning because the copies do not stay identical — that is the
        whole failure mode."""
        spec = {
            "name": "t",
            "description": "x" * 60,
            "root": {
                "kind": "infer",
                "id": "a",
                "config": {"prompt": "A Finding is {severity: Critical|Major|Minor|Nit}"},
            },
        }
        findings = [f for f in lint_template(spec).findings if f.code == "WFL_INLINE_CONVENTION"]
        assert findings
        assert findings[0].severity == SEVERITY_ERROR
        # Names the block to use — "move it to shared" alone leaves the author guessing which.
        assert "finding-record" in findings[0].message
        assert findings[0].path == "a"

    def test_it_finds_a_convention_inside_a_MACRO_lens(self) -> None:
        """A lens prompt is not `config.prompt`, so a naive walk would miss it — and the judge
        panel is exactly where a hand-written Finding record would go."""
        spec = {
            "name": "t",
            "root": {
                "macro": "judge_panel",
                "id": "p",
                "config": {
                    "lenses": [{"name": "a", "prompt": "severity: Critical|Major|Minor|Nit"}]
                },
            },
        }
        assert any(f.code == "WFL_INLINE_CONVENTION" for f in lint_template(spec).findings)

    def test_a_broken_block_reference_is_reported(self) -> None:
        spec = {
            "name": "t",
            "root": {"kind": "infer", "id": "a", "config": {"prompt": "{{block:nope}}"}},
        }
        assert any(f.code == "WFL_UNKNOWN_BLOCK" for f in lint_template(spec).findings)

    def test_every_broken_reference_is_reported_at_once(self) -> None:
        """The save path raises on the first; a lint is what you want after renaming a block."""
        spec = {
            "name": "t",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "{{block:gone-one}}"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "{{block:gone-two}}"}},
                ],
            },
        }
        codes = [f for f in lint_template(spec).findings if f.code == "WFL_UNKNOWN_BLOCK"]
        assert len(codes) == 2

    def test_a_required_input_with_a_default_is_an_error(self) -> None:
        """They contradict: a default means the input can be omitted."""
        spec = {
            "name": "t",
            "description": "x" * 60,
            "inputs": {"q": {"required": True, "default": "d", "help": "h"}},
            "root": {"kind": "infer", "id": "a", "config": {"prompt": "go"}},
            "metadata": {
                "steering_examples": [{"event": "kickoff"}, {"event": "mutation"}],
            },
        }
        findings = lint_template(spec, bundled=True).findings
        assert [f.code for f in findings] == ["WFL_REQUIRED_WITH_DEFAULT"]
        assert findings[0].severity == SEVERITY_ERROR

    @pytest.mark.parametrize(
        "code,spec_over",
        [
            ("WFL_THIN_DESCRIPTION", {"description": "short"}),
            ("WFL_UNDOCUMENTED_INPUT", {"inputs": {"q": {"type": "string"}}}),
            (
                "WFL_NO_KICKOFF_EXAMPLE",
                {"metadata": {"steering_examples": [{"event": "mutation"}]}},
            ),
            (
                "WFL_NO_MUTATION_EXAMPLE",
                {"metadata": {"steering_examples": [{"event": "kickoff"}]}},
            ),
        ],
    )
    def test_shipping_gaps_are_warnings(self, code: str, spec_over: dict) -> None:
        """Warnings, because each is a gap in polish rather than a defect — but the library is
        held to zero of them, which is what makes them worth reporting at all."""
        spec = {
            "name": "t",
            "description": "x" * 60,
            "root": {"kind": "infer", "id": "a", "config": {"prompt": "go"}},
            "metadata": {"steering_examples": [{"event": "kickoff"}, {"event": "mutation"}]},
            **spec_over,
        }
        findings = [f for f in lint_template(spec, bundled=True).findings if f.code == code]
        assert findings, code
        assert findings[0].severity == SEVERITY_WARNING
        # Warnings never fail the lint's ok gate.
        assert lint_template(spec, bundled=True).ok is True

    def test_a_users_own_workflow_is_not_held_to_SHIPPING_standards(self) -> None:
        """`bundled=False`. A half-finished personal workflow is theirs to leave rough; reporting
        a missing steering example on it would be noise."""
        spec = {
            "name": "mine",
            "description": "wip",
            "root": {"kind": "infer", "id": "a", "config": {"prompt": "go"}},
        }
        assert lint_template(spec).clean is True
        assert lint_template(spec, bundled=True).clean is False

    def test_the_lint_never_raises_on_junk(self) -> None:
        """A lint that crashed would hide every finding it had already collected."""
        assert lint_template({}).findings == []
        assert lint_template({"root": "not a node"}).findings == []
        result = lint_template([])  # type: ignore[arg-type]
        assert [f.code for f in result.findings] == ["WFL_NOT_AN_OBJECT"]


class TestAuthorSurfacesTheLint:
    """The lint has to reach an author or it changes nothing."""

    def test_the_manifest_advertises_the_macros_and_blocks(self) -> None:
        """The manifest is what an authoring model reads to learn the shapes it may write. A
        macro or block absent from it is one a model will never use."""
        from personalclaw.workflows import service

        m = service.manifest()
        assert "judge_panel" in m["macros"]
        assert "finding-record" in m["shared_blocks"]

    @pytest.mark.anyio
    async def test_author_def_attaches_lint_advice_without_refusing(
        self, tmp_path, monkeypatch
    ) -> None:
        """Attached, never fatal: a user's own workflow is theirs to leave rough, but an author
        who never sees the advice cannot follow a convention nobody told them about."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
        from personalclaw.workflows import service

        result = await service.author_def(
            name="inline-convention",
            root={
                "kind": "infer",
                "id": "a",
                "config": {"prompt": "A Finding is {severity: Critical|Major|Minor|Nit}"},
            },
            save=False,
        )
        assert result["ok"] is True, result  # advice, not a refusal
        assert result["lint"]["ok"] is False
        assert any(f["code"] == "WFL_INLINE_CONVENTION" for f in result["lint"]["findings"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_an_unresolved_block_reference_is_named_as_ITSELF() -> None:
    """Reaching the validator with a `{{block:…}}` still in place means the resolve step was
    skipped or the block does not exist. Before this, it surfaced as `WF_UNKNOWN_BINDING_ROOT`
    ("binding root 'block:finding-record' is not a known source") — which sends the author looking
    for a node that was never the problem, because blocks and bindings resolve at different times.
    """
    from personalclaw.workflows.validator import validate_spec

    spec = {
        "name": "t",
        "root": {"kind": "infer", "id": "a", "config": {"prompt": "{{block:finding-record}}"}},
    }
    codes = [i.code for i in validate_spec(spec, strict=True).issues]
    assert "WF_UNRESOLVED_BLOCK" in codes
    assert "WF_UNKNOWN_BINDING_ROOT" not in codes


def test_a_real_unknown_binding_root_still_reports_as_one() -> None:
    """The complement — the block branch must not have swallowed the general case."""
    from personalclaw.workflows.validator import validate_spec

    spec = {
        "name": "t",
        "root": {"kind": "infer", "id": "a", "config": {"prompt": "{{invented.thing}}"}},
    }
    assert "WF_UNKNOWN_BINDING_ROOT" in [i.code for i in validate_spec(spec, strict=True).issues]


class TestDeclaredDefaultsAreApplied:
    """A real engine bug, found by starting a bundled template from the UI with its optional field
    left blank.

    Declared defaults were VALIDATED and then ignored. A template declaring `acceptance` with a
    default, run without it, failed three nodes in on `binding failed: unresolved reference at
    'acceptance'` — so every optional input was a landmine and a template could only be run by
    passing every key it declared, which is the opposite of what "optional" means.
    """

    def test_an_omitted_optional_input_gets_its_declared_default(self) -> None:
        from personalclaw.workflows.service import _with_declared_defaults

        spec = {"inputs": {"kind": {"default": "document"}, "subject": {"required": True}}}
        out = _with_declared_defaults(spec, {"subject": "x"})
        assert out == {"subject": "x", "kind": "document"}

    def test_an_optional_input_with_NO_default_still_gets_a_key(self) -> None:
        """Otherwise a `{{inputs.acceptance}}` binding fails on an input the template said was
        optional — and "optional" has to mean the workflow works without it."""
        from personalclaw.workflows.service import _with_declared_defaults

        out = _with_declared_defaults({"inputs": {"acceptance": {"type": "string"}}}, {})
        assert out == {"acceptance": ""}

    def test_the_callers_value_always_wins(self) -> None:
        from personalclaw.workflows.service import _with_declared_defaults

        spec = {"inputs": {"kind": {"default": "document"}}}
        assert _with_declared_defaults(spec, {"kind": "report"}) == {"kind": "report"}

    def test_an_explicit_empty_string_is_NOT_overridden(self) -> None:
        """A user who deliberately cleared a field is not asking for the default back."""
        from personalclaw.workflows.service import _with_declared_defaults

        spec = {"inputs": {"focus": {"default": "everything"}}}
        assert _with_declared_defaults(spec, {"focus": ""}) == {"focus": ""}

    def test_an_undeclared_input_is_passed_through(self) -> None:
        from personalclaw.workflows.service import _with_declared_defaults

        assert _with_declared_defaults({"inputs": {}}, {"extra": 1}) == {"extra": 1}

    def test_a_malformed_inputs_block_is_tolerated(self) -> None:
        from personalclaw.workflows.service import _with_declared_defaults

        assert _with_declared_defaults({"inputs": "not a dict"}, {"a": 1}) == {"a": 1}

    @pytest.mark.anyio
    async def test_a_bundled_template_RUNS_with_only_its_required_input(
        self, tmp_path, monkeypatch
    ) -> None:
        """The end-to-end form of the bug. Every bundled template declares optional inputs, so
        before the fix none of them could be started the way the UI starts them."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
        from personalclaw.workflows import service
        from personalclaw.workflows.bundled_defs import register_bundled_provider

        register_bundled_provider()
        result = await service.start_run(
            name="produce-and-audit",
            inputs={"subject": "a subject"},
            skip_preflight=True,
        )
        # `WF_NO_SUPERVISOR` is expected with no gateway: the run is CREATED and then not driven.
        # That is exactly the boundary under test — input completion happens at creation, before
        # any launch, so the record is complete regardless of whether anything drove it.
        assert result.get("run_id"), result
        run = __import__("personalclaw.workflows.store", fromlist=["get"]).get(result["run_id"])
        # The run RECORD carries the completed inputs, so it explains its own behaviour.
        assert run.inputs["artifact_kind"] == "document"
        assert run.inputs["acceptance"] == ""
