"""The bundled template library (Slice 9a, WF2 §6).

Six templates shipped inside the package. The tests here are mostly a **contract over the
library itself** rather than over code, because the failure modes are all of the "ships broken
and nobody notices until a user tries it" kind:

* a template that does not validate is a template that fails at every run start — and nothing
  else in the suite would parse these files;
* a template referencing an action provider that is not registered fails after a `stage` has
  already spent tokens;
* a template whose macro cannot expand is invisible in the listing, so a user sees five
  templates and no error;
* and the packaging: the JSON files must be declared as package data, or the WHEEL ships an
  empty library while the editable install looks perfect. That one is only observable from
  `pyproject.toml`, which is why it is asserted here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from personalclaw.workflows.blocks import resolve_spec
from personalclaw.workflows.bundled_defs import (
    BundledWorkflowDefProvider,
    bundled_root,
    read_template,
    register_bundled_provider,
    template_names,
)
from personalclaw.workflows.macros import expand_spec, has_macros
from personalclaw.workflows.models import Node, WorkflowDef, valid_name, walk
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: The six the plan's §6 table names. Asserted as a SET so a template silently disappearing
#: from the wheel is a failure rather than a smaller listing.
EXPECTED = {
    # The general-purpose library (WF2 Slice 9a).
    "audit-sweep",
    "code-implementation",
    "deep-research",
    "design-review",
    "produce-and-audit",
    "project-planning",
    # The loop-kind families (LOOPS-EVOLUTION §"Per-Kind Template Designs"): descendants
    # of the five loop kinds the plan replaces. `deep-research` above doubles as the
    # research-loop descendant, which is why there are five here rather than six.
    "goal-pursuit-open-ended",
    "goal-pursuit-verifiable",
    "general-project",
    "design-project",
    "diagnose-run",
    # The knowledge maintenance trio (KNOWLEDGE-SYNTHESIS §3.4), ordered cheapest first: health
    # is zero-LLM and gates the other two, because linting a stub spends a model call to
    # discover it is a stub.
    "knowledge-health",
    "knowledge-lint",
    "gap-healing",
}


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


def _pipeline(spec: dict) -> dict:
    """Macros expanded THEN blocks resolved — the exact order `author_def` and the bundled
    provider use. Validating the raw spec instead would flag a `{{block:…}}` as an unknown
    binding root, which is a test artifact rather than a template defect."""
    return resolve_spec(expand_spec(spec))


class TestLibraryContents:
    def test_every_declared_template_ships(self) -> None:
        assert set(template_names()) == EXPECTED

    def test_every_name_is_a_valid_def_name(self) -> None:
        """The name becomes a directory and a URL path segment."""
        for name in template_names():
            assert valid_name(name), name

    def test_the_directory_name_matches_the_declared_name(self) -> None:
        """A mismatch would make `get_def(<dir>)` return a def calling itself something else —
        and the UI would then link to a name that 404s."""
        for name in template_names():
            assert _raw(name).get("name") == name


@pytest.mark.parametrize("name", sorted(EXPECTED))
class TestEachTemplate:
    def test_it_validates_STRICTLY(self, name: str) -> None:
        """Strict, so a template does not ship a warning it then propagates to every run made
        from it. This is the only test that parses these files at all."""
        result = validate_spec(_pipeline(_raw(name)), strict=True)
        assert result.issues == [], [i.to_dict() for i in result.issues]

    def test_it_loads_as_a_WorkflowDef(self, name: str) -> None:
        loaded = read_template(name)
        assert isinstance(loaded, WorkflowDef)
        assert loaded.name == name

    def test_it_is_served_with_macros_already_expanded(self, name: str) -> None:
        """The invariant: nothing downstream of this provider knows macros exist."""
        loaded = read_template(name)
        assert loaded is not None
        assert has_macros(loaded.to_dict()) is False

    def test_it_declares_itself_bundled(self, name: str) -> None:
        """`source` drives the UI's read-only affordances (no delete, "instantiate to edit")."""
        loaded = read_template(name)
        assert loaded is not None
        assert loaded.source == "bundled"

    def test_it_has_a_description_a_user_can_choose_from(self, name: str) -> None:
        """The template picker shows this line and nothing else — an empty one makes the
        template indistinguishable from its neighbours."""
        desc = str(_raw(name).get("description", ""))
        assert len(desc) > 40, f"{name}: description is too thin to choose by"

    def test_every_declared_input_is_documented(self, name: str) -> None:
        """An input with no `help` shows a bare field name in the run dialog, and the user has
        to read the spec to learn what it wants."""
        for key, param in (_raw(name).get("inputs") or {}).items():
            assert str(param.get("help", "")).strip(), f"{name}.{key} has no help text"

    def test_a_required_input_has_no_default(self, name: str) -> None:
        """Contradictory otherwise: a default means it can be omitted."""
        for key, param in (_raw(name).get("inputs") or {}).items():
            if param.get("required"):
                assert param.get("default") in (None, ""), f"{name}.{key} is required AND defaulted"

    def test_it_carries_steering_examples(self, name: str) -> None:
        """Metadata the widget surfaces and `workflow_plan` uses as few-shot (WF2-R15). A
        template with none is one a model has to guess how to drive."""
        examples = _raw(name).get("metadata", {}).get("steering_examples") or []
        assert examples, f"{name} has no steering_examples"
        kinds = {e.get("event") for e in examples}
        # Both a kickoff and a mid-flight example: the second is what teaches a model that
        # editing a running workflow is a normal thing to do.
        assert "kickoff" in kinds, f"{name} has no kickoff example"
        assert "mutation" in kinds, f"{name} has no mid-flight mutation example"

    def test_every_binding_reference_resolves_within_the_spec(self, name: str) -> None:
        """A `{{nodes.x.output}}` naming a node that does not exist is a mid-run binding error —
        after the upstream nodes already spent their tokens. The validator checks this; this test
        exists so the failure is attributed to THIS template by name."""
        result = validate_spec(_pipeline(_raw(name)), strict=True)
        unknown = [i for i in result.issues if i.code == "WF_UNKNOWN_NODE_REF"]
        assert not unknown, [i.to_dict() for i in unknown]

    def test_every_action_node_names_a_registered_provider(self, name: str) -> None:
        """An unregistered provider fails at dispatch — typically after a `stage` above it has
        already done real work. `ALLOWED_HOOK_PROVIDERS` is the registered catalog's mirror."""
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        root = Node.from_dict(_pipeline(_raw(name))["root"])
        for path, node in walk(root):
            if node.kind.value == "action":
                provider = str((node.config or {}).get("provider", ""))
                assert provider in ALLOWED_HOOK_PROVIDERS, f"{name} at {path}: {provider!r}"

    def test_a_high_risk_template_says_so(self, name: str) -> None:
        """The Store shows `risk` as the install-consent surface. A template that writes files
        and runs commands while declaring `low` misrepresents what accepting it means."""
        raw = _raw(name)
        root = Node.from_dict(_pipeline(raw)["root"])
        writes = any(
            n.kind.value == "action" and str((n.config or {}).get("provider", "")) == "bash"
            for _p, n in walk(root)
        )
        risk = str(raw.get("metadata", {}).get("risk", "low"))
        if writes:
            assert risk in ("medium", "high"), f"{name} runs commands but declares risk={risk}"


class TestConventions:
    """Cross-template conventions (WF2-R15). A six-template library only stays coherent if the
    shapes agree; these are the ones a reviewer would otherwise have to check by hand."""

    def test_every_review_stage_uses_the_canonical_Finding_record(self) -> None:
        """`{severity, location, problem, why, recommended_fix, status}` everywhere, so a gate
        predicate like "no open Critical" is uniform, the widget renders findings identically,
        and the Run Ledger is minable by the flywheel."""
        for name in template_names():
            text = json.dumps(_pipeline(_raw(name)))
            if "Finding" not in text:
                continue
            for field in ("severity", "location", "problem", "why", "recommended_fix", "status"):
                assert field in text, f"{name}: Finding record is missing {field!r}"
            assert "Critical|Major|Minor|Nit" in text, f"{name}: non-canonical severity ladder"

    def test_the_code_template_captures_a_baseline_before_it_mutates(self) -> None:
        """Without it, a failure after the change cannot be told apart from one that was already
        there — and someone debugs the wrong commit."""
        root = Node.from_dict(_pipeline(_raw("code-implementation"))["root"])
        order = [(p, n) for p, n in walk(root)]
        baseline_at = next(i for i, (_p, n) in enumerate(order) if n.id == "baseline")
        first_mutating = next(i for i, (_p, n) in enumerate(order) if n.kind.value == "stage")
        assert baseline_at < first_mutating, "the baseline must precede the first mutating node"

    def test_the_triage_first_pattern_drives_a_branch(self) -> None:
        """The blessed opening shape: an `infer` classification whose output selects among entry
        subgraphs, so a small task is not put through the deep path."""
        for name in ("produce-and-audit", "deep-research"):
            expanded = _pipeline(_raw(name))
            root = Node.from_dict(expanded["root"])
            kinds = {n.id: n.kind.value for _p, n in walk(root)}
            assert kinds.get("triage") == "infer", f"{name} has no triage classifier"
            branches = [n for _p, n in walk(root) if n.kind.value == "branch"]
            assert branches, f"{name} triages but never branches on it"
            assert any(
                "nodes.triage.output" in str((b.config or {}).get("on", "")) for b in branches
            ), f"{name}: no branch reads the triage verdict"

    def test_a_verification_gate_is_engine_executed_not_model_declared(self) -> None:
        """The code template's gate runs a COMMAND. A gate that asked the model whether it was
        done would make done-ness self-reported, which is the failure the gate exists for."""
        root = Node.from_dict(_pipeline(_raw("code-implementation"))["root"])
        gates = [n for _p, n in walk(root) if n.kind.value == "gate"]
        assert any(str((g.config or {}).get("kind")) == "verify_command" for g in gates)


class TestProvider:
    async def test_it_lists_every_template(self) -> None:
        provider = BundledWorkflowDefProvider()
        defs, total = await provider.list_defs()
        assert total == len(EXPECTED)
        assert {d.name for d in defs} == EXPECTED

    async def test_pagination_windows_without_losing_the_total(self) -> None:
        """The total counts what SHIPS, so a paginated UI can render "6 templates" on page 1."""
        provider = BundledWorkflowDefProvider()
        defs, total = await provider.list_defs(limit=2, offset=0)
        assert len(defs) == 2
        assert total == len(EXPECTED)

    async def test_an_unknown_name_returns_None_rather_than_raising(self) -> None:
        assert await BundledWorkflowDefProvider().get_def("no-such-template") is None

    async def test_a_traversal_name_is_refused(self) -> None:
        """The name reaches here from a URL path segment."""
        assert await BundledWorkflowDefProvider().get_def("../../etc/passwd") is None

    async def test_it_is_read_only(self) -> None:
        """A user's edit written into the package directory would be somewhere
        `pip install --upgrade` silently overwrites."""
        provider = BundledWorkflowDefProvider()
        assert provider.readonly is True
        with pytest.raises(NotImplementedError):
            await provider.save_def(name="x", root={})
        with pytest.raises(NotImplementedError):
            await provider.delete_def("audit-sweep")

    async def test_registration_is_idempotent(self) -> None:
        """It runs on every boot."""
        from personalclaw.workflows.defs import get_provider

        register_bundled_provider()
        first = get_provider("bundled")
        register_bundled_provider()
        assert get_provider("bundled") is first

    async def test_the_bundled_provider_does_not_shadow_the_writable_one(self) -> None:
        """`author_def` picks the first NON-readonly provider. A read-only provider that
        registered as writable would make every save fail with "read-only"."""
        from personalclaw.workflows.defs import get_provider, list_providers
        from personalclaw.workflows.native_defs import register_native_provider

        register_bundled_provider()
        register_native_provider()
        writable = [
            n for n in list_providers() if (p := get_provider(n)) is not None and not p.readonly
        ]
        assert "native" in writable
        assert "bundled" not in writable

    def test_a_corrupt_template_is_skipped_not_fatal(self, tmp_path, monkeypatch) -> None:
        """The listing is how a user finds the broken one; one bad file must not hide five good
        ones."""
        fake = tmp_path / "bundled"
        (fake / "broken").mkdir(parents=True)
        (fake / "broken" / "workflow.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr("personalclaw.workflows.bundled_defs.bundled_root", lambda: fake)
        assert read_template("broken") is None

    def test_a_template_cannot_claim_to_be_user_authored(self, tmp_path, monkeypatch) -> None:
        """`source` is forced, not trusted from the file: a hand-edited bundled template
        presenting itself as user-authored would get a delete button pointing at the package."""
        fake = tmp_path / "bundled"
        (fake / "sneaky").mkdir(parents=True)
        (fake / "sneaky" / "workflow.json").write_text(
            json.dumps(
                {
                    "name": "sneaky",
                    "source": "user",
                    "root": {"kind": "transform", "id": "t", "config": {"expr": 1}},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("personalclaw.workflows.bundled_defs.bundled_root", lambda: fake)
        loaded = read_template("sneaky")
        assert loaded is not None and loaded.source == "bundled"


def test_the_templates_are_declared_as_package_data() -> None:
    """Only observable from `pyproject.toml`: without this line the WHEEL ships an empty
    template library while an editable install looks perfect, and the first person to notice is
    a user who ran `pip install personalclaw`.

    This line previously read `workflows/bundled/*/WORKFLOW.md` — a filename nothing ever
    produced, so it matched nothing at all.
    """
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"\[tool\.setuptools\.package-data\](.*?)\n\[", text, re.S)
    assert block, "could not find the package-data block"
    assert "workflows/bundled/*/workflow.json" in block.group(1)


class TestActionArgShape:
    """A real bug this session hit, and the guard that now catches it at authoring time.

    `code-implementation` wrote its bash arguments FLAT beside `provider`. The engine reads a
    provider's arguments from `config.with` (`dispatch_action`), so bash received an empty config
    and reported "missing 'command' field" — for a command visibly right there in the spec.

    Worse than the wrong message: the failed action then made every downstream binding on
    `{{nodes.baseline.output}}` fail too, and the run died as "deadlocked". Three cascading
    symptoms, none of them naming the actual mistake. Hence a validator check.
    """

    def test_flat_action_arguments_are_refused_by_name(self) -> None:
        from personalclaw.workflows.validator import validate_spec as v

        bad = {
            "name": "t",
            "root": {
                "kind": "action",
                "id": "baseline",
                "config": {"provider": "bash", "command": "make test", "allow_failure": True},
            },
        }
        issues = [i for i in v(bad).issues if i.code == "WF_ACTION_ARGS_NOT_NESTED"]
        assert issues, "the shape that failed live must not validate"
        # Names WHAT to move — "arguments go under with" alone leaves the author hunting.
        assert "command" in issues[0].message

    def test_the_correct_shape_validates_strictly(self) -> None:
        from personalclaw.workflows.validator import validate_spec as v

        good = {
            "name": "t",
            "root": {
                "kind": "action",
                "id": "b",
                "config": {"provider": "bash", "with": {"command": "make test"}},
            },
        }
        assert v(good, strict=True).issues == []

    def test_an_argumentless_provider_is_only_a_warning(self) -> None:
        """Some providers genuinely need no arguments; refusing them would be wrong."""
        from personalclaw.workflows.validator import validate_spec as v

        result = v(
            {
                "name": "t",
                "root": {
                    "kind": "action",
                    "id": "b",
                    "config": {"provider": "notification-digest"},
                },
            }
        )
        assert result.ok is True
        assert [i.code for i in result.issues] == ["WF_ACTION_NO_ARGS"]

    def test_every_bundled_action_node_nests_its_arguments(self) -> None:
        """The library-wide version of the same check: no template may ship the broken shape."""
        for name in template_names():
            root = Node.from_dict(_pipeline(_raw(name))["root"])
            for path, node in walk(root):
                if node.kind.value != "action":
                    continue
                cfg = node.config or {}
                stray = [k for k in cfg if k not in ("provider", "with", "context", "payload")]
                assert not stray, f"{name} at {path}: arguments outside `with`: {stray}"


def test_the_shared_blocks_are_declared_as_package_data() -> None:
    """Same class of bug as the templates' own line, with a worse failure: a template's
    `{{block:…}}` reference that cannot resolve is a hard ERROR, so a wheel missing the blocks
    would make every review template fail to load rather than merely lose a convention.
    """
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"\[tool\.setuptools\.package-data\](.*?)\n\[", text, re.S)
    assert block, "could not find the package-data block"
    assert "workflows/bundled/shared/*.md" in block.group(1)


def test_the_shared_directory_is_not_mistaken_for_a_template() -> None:
    """`bundled/shared/` sits beside the template directories. It holds no `workflow.json`, which
    is what keeps it out of the listing — but a future change that globbed directories instead of
    checking for the file would silently list "shared" as a template a user could run.
    """
    assert "shared" not in template_names()
    assert not (bundled_root() / "shared" / "workflow.json").exists()


def test_the_whole_library_passes_the_conventions_lint() -> None:
    """The library-wide gate (WF2-R15). `validate_spec` answers "will it run"; the lint answers
    "does it follow the conventions" — and the shipped library is held to CLEAN (no warnings
    either), because a warning that ships propagates to every template copied from it.

    Reported all at once rather than per-template, so a convention change shows its full blast
    radius in one CI failure instead of six sequential ones.
    """
    from personalclaw.workflows.template_lint import lint_template

    problems: list[str] = []
    for name in template_names():
        for finding in lint_template(_raw(name), bundled=True).findings:
            problems.append(f"{name}: [{finding.severity}] {finding.code} {finding.message}")
    assert not problems, "\n".join(problems)
