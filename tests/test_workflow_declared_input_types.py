"""A workflow's declared input types are ENFORCED, not documentation (issue 327).

`inputs: {apply: {type: boolean}, min_cluster_size: {type: number}}` was read by three writers and
no reader: `ParamSpec.to_dict()` ships the type to the MCP tool listing, `template_types()` writes
it into a prompt, and the launch form printed it as a caption. Nothing checked a caller's value
against it. Measured on `origin/main` by executing the real run-start path:

    the then-declared-only required-input check                    -> []
    service._with_declared_defaults(spec, {"apply": "banana", ...})   -> unchanged
    start_run(..., inputs={"apply": "banana", "min_cluster_size": "not-a-number"})
        -> run b9aae0d3 CREATED, store row: {"apply": "banana", "min_cluster_size": "not-a-number"}

Because nothing enforced the type, every CONSUMER rolled its own coercion — and they disagree. One
declared boolean given the string `"1"`, measured three ways on `origin/main`:

    knowledge_maintain_provider._truthy("1")                  -> True   (its own allowlist)
    audit-sweep's gate      `{{inputs.fix}} == true`           -> False  (expression equality)
    self-qa's branch  `on: {{inputs.fix_branch_enabled}}`      -> no case matched, the run FAILS

The third is the expensive one: `_select_case` returns None, the engine raises a USER routing
failure, and it does so after triage, scenario-gen, execute and the evidence action have already
run — i.e. after paid model calls. `start_run`'s own comment on the required-input check says
"Refused BEFORE tokens are spent"; the same reasoning had stopped one line short of types.

ARCC was queried before writing this (an API accepting caller-controlled values that can enable a
write path). SAX-04/SAX-05 guidance applied: validate at the layer every path crosses rather than
only at the client, use an allowlist not a blocklist, keep validation separate from business logic,
and do not echo unbounded input back in an error message.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import contracts, service

# ── the coercion table ───────────────────────────────────────────────────────────────────


def spec_of(**declared: dict[str, Any]) -> dict[str, Any]:
    return {"inputs": dict(declared)}


BOOL = spec_of(apply={"type": "boolean", "default": False})
NUM = spec_of(size={"type": "number"})


class TestBoolean:
    def test_a_string_that_is_not_a_boolean_is_refused(self) -> None:
        """🔑 The defect. `"banana"` was stored verbatim, and `conditions.truthy("banana")` is
        True — so a declared boolean defaulting to False could be switched ON by any word."""
        coerced, errors = contracts.coerce_declared_inputs(BOOL, {"apply": "banana"})
        assert errors == ["apply: expected boolean, got 'banana'"]
        assert coerced == {"apply": "banana"}, "the caller's value is returned unchanged on refusal"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("True", True),
            ("yes", True),
            ("on", True),
            ("1", True),
            ("y", True),
            ("false", False),
            ("no", False),
            ("off", False),
            ("0", False),
            ("n", False),
            (" TRUE ", True),
            (True, True),
            (False, False),
        ],
    )
    def test_every_recognised_spelling_becomes_a_real_bool(self, raw: Any, expected: bool) -> None:
        """The vocabulary is `safety_flags`', imported rather than copied: two lists would drift
        the moment either grew a word, and this one already had to agree with a safety control."""
        coerced, errors = contracts.coerce_declared_inputs(BOOL, {"apply": raw})
        assert errors == []
        assert coerced["apply"] is expected

    def test_a_number_is_not_a_boolean_here(self) -> None:
        """Deliberately STRICTER than `strict_bool`, which coerces `1` -> True. A form, a shell and
        a JSON body can all spell yes with a word; an integer under a declared boolean is a caller
        confusing two types, and this is the one place that can still say so."""
        _coerced, errors = contracts.coerce_declared_inputs(BOOL, {"apply": 1})
        assert errors == ["apply: expected boolean, got 1"]


class TestNumber:
    def test_a_string_that_is_not_a_number_is_refused(self) -> None:
        _coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": "not-a-number"})
        assert errors == ["size: expected number, got 'not-a-number'"]

    @pytest.mark.parametrize(
        "raw,expected", [("7", 7), (" 7 ", 7), ("7.5", 7.5), (7, 7), (7.5, 7.5), ("-3", -3)]
    )
    def test_a_numeric_string_is_coerced_because_that_is_what_a_form_sends(
        self, raw: Any, expected: float
    ) -> None:
        coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": raw})
        assert errors == []
        assert coerced["size"] == expected

    def test_an_integral_float_string_lands_as_an_int(self) -> None:
        """`"7"` -> `7`, never `7.0`: the value is stored on the run record and rendered into
        prompts, and `min_cluster_size: 7.0` reads as a different setting than the one typed."""
        coerced, _errors = contracts.coerce_declared_inputs(NUM, {"size": "7"})
        assert coerced["size"] == 7 and isinstance(coerced["size"], int)

    @pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "1e400"])
    def test_a_non_finite_number_is_refused(self, raw: str) -> None:
        """🪤 `float("nan")` and `float("1e400")` both PARSE. Neither survives JSON, and NaN makes
        every downstream comparison false — a threshold no test would ever meet, silently."""
        coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": raw})
        assert errors, f"{raw!r} was accepted as a number"
        assert not isinstance(coerced["size"], float) or math.isfinite(coerced["size"])

    def test_a_bool_is_not_a_number(self) -> None:
        """`bool` is an `int` subclass, so an unguarded `isinstance(v, int)` accepts `True` as 1."""
        _coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": True})
        assert errors == ["size: expected number, got True"]


class TestIntegerStringArrayObject:
    def test_an_integer_refuses_a_fraction(self) -> None:
        spec = spec_of(n={"type": "integer"})
        assert contracts.coerce_declared_inputs(spec, {"n": "2.5"})[1] == [
            "n: expected integer, got '2.5'"
        ]
        assert contracts.coerce_declared_inputs(spec, {"n": "2"})[0]["n"] == 2

    def test_a_string_takes_a_scalar_and_renders_it_the_way_the_engine_would(self) -> None:
        """`true`, not Python's `True`: the stored value must equal what a binding would produce
        (`bindings._stringify`), or the run record and the run disagree."""
        spec = spec_of(s={"type": "string"})
        assert contracts.coerce_declared_inputs(spec, {"s": True})[0]["s"] == "true"
        assert contracts.coerce_declared_inputs(spec, {"s": 3})[0]["s"] == "3"

    def test_a_container_under_a_declared_string_is_refused_not_dumped(self) -> None:
        """JSON-dumping it would hide the caller's mistake behind a value that looks deliberate,
        and the message names the shape rather than echoing the contents."""
        spec = spec_of(s={"type": "string"})
        _coerced, errors = contracts.coerce_declared_inputs(spec, {"s": {"a": 1}})
        assert errors == ["s: expected string, got a JSON object"]

    def test_an_array_accepts_a_list_or_the_json_a_textarea_produces(self) -> None:
        spec = spec_of(xs={"type": "array"})
        assert contracts.coerce_declared_inputs(spec, {"xs": ["a"]})[0]["xs"] == ["a"]
        assert contracts.coerce_declared_inputs(spec, {"xs": '["a", "b"]'})[0]["xs"] == ["a", "b"]

    def test_an_array_refuses_a_bare_word_and_refuses_a_json_object(self) -> None:
        spec = spec_of(xs={"type": "array"})
        assert contracts.coerce_declared_inputs(spec, {"xs": "a"})[1] == [
            "xs: expected array, got 'a'"
        ]
        assert contracts.coerce_declared_inputs(spec, {"xs": '{"a": 1}'})[
            1
        ], "a JSON OBJECT satisfied a declared array"

    def test_an_object_accepts_a_dict_or_its_json(self) -> None:
        spec = spec_of(o={"type": "object"})
        assert contracts.coerce_declared_inputs(spec, {"o": {"a": 1}})[0]["o"] == {"a": 1}
        assert contracts.coerce_declared_inputs(spec, {"o": '{"a": 1}'})[0]["o"] == {"a": 1}


# ── what it deliberately does NOT check ──────────────────────────────────────────────────


class TestPassThroughs:
    def test_an_undeclared_key_has_no_declared_type_to_check(self) -> None:
        """A template whose tree reads `inputs.x` while its declaration block omits `x` is real —
        `resolve_unfilled_inputs` exists for exactly that drift. Refusing the key would make those
        templates unrunnable."""
        coerced, errors = contracts.coerce_declared_inputs(BOOL, {"extra": ["anything"]})
        assert errors == [] and coerced["extra"] == ["anything"]

    def test_an_unrecognised_declared_type_fails_OPEN(self) -> None:
        """🪤 The one direction this fails open, and it fails open on the DECLARATION. A template
        declaring `type: bool` (or a typo) is an AUTHORING bug its user cannot fix — refusing every
        run of it would punish the wrong person. `template_lint` is where an author hears about it.
        """
        spec = spec_of(flag={"type": "bool"})
        assert contracts.coerce_declared_inputs(spec, {"flag": "banana"}) == (
            {"flag": "banana"},
            [],
        )
        assert (
            "bool" not in contracts.DECLARED_TYPES
        ), "if `bool` becomes a declared type, this test is asserting the wrong thing"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_is_this_system_s_unset_marker_and_belongs_to_the_presence_check(
        self, blank: Any
    ) -> None:
        """`_with_declared_defaults` writes `""` for every optional input with no default, so a
        blank value means "declared but unset" here. Whether that is ACCEPTABLE is
        `apply_extraction`'s question; answering it twice is how two checks come to disagree —
        and a required blank is already refused by the form's `missingRequired`."""
        coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": blank})
        assert errors == [] and coerced["size"] == blank

    def test_a_declaration_that_is_not_a_dict_is_ignored(self) -> None:
        assert contracts.coerce_declared_inputs({"inputs": "not a dict"}, {"a": 1}) == (
            {"a": 1},
            [],
        )
        assert contracts.coerce_declared_inputs({}, {"a": 1}) == ({"a": 1}, [])
        assert contracts.coerce_declared_inputs({"inputs": {"a": "not a dict"}}, {"a": 1})[1] == []


class TestErrorMessages:
    def test_every_offending_key_is_reported_in_one_pass(self) -> None:
        """A form that shows one error at a time makes the user submit four times to learn four
        things. Sorted so the message is stable enough to assert on."""
        spec = spec_of(a={"type": "boolean"}, b={"type": "number"}, c={"type": "array"})
        _coerced, errors = contracts.coerce_declared_inputs(spec, {"a": "x", "b": "y", "c": "z"})
        assert errors == [
            "a: expected boolean, got 'x'",
            "b: expected number, got 'y'",
            "c: expected array, got 'z'",
        ]

    def test_a_long_value_is_truncated_rather_than_reflected_whole(self) -> None:
        """ARCC's input-validation guidance: an error message must not become an echo of an
        unbounded caller-controlled payload. Plain inputs are not a secret store — an inline secret
        is refused at save (`WF_DEF_INLINE_SECRET`) — but they are still caller text."""
        _coerced, errors = contracts.coerce_declared_inputs(NUM, {"size": "z" * 5000})
        assert len(errors[0]) < 120, errors[0]
        assert errors[0].endswith("…")


# ── the door: `start_run` refuses before it creates a run ────────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home. `PERSONALCLAW_HOME` alone is not enough for every resolver in this
    import graph, so `config_dir` is patched too."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    from personalclaw.workflows import bundled_defs

    bundled_defs.register_bundled_provider()
    return tmp_path


def _start(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(service.start_run(supervisor=None, skip_preflight=True, **kwargs))


class TestStartRun:
    def test_a_mistyped_input_is_refused_and_NO_RUN_IS_CREATED(self, home: Path) -> None:
        """🔑 The end-to-end defect. Measured on `origin/main`: the run was created and its stored
        inputs were `{"apply": "banana", "min_cluster_size": "not-a-number"}`."""
        from personalclaw.workflows import store

        before = store.list_runs(workflow_name="knowledge-lint", limit=5)[1]
        result = _start(
            name="knowledge-lint",
            inputs={"apply": "banana", "min_cluster_size": "not-a-number"},
        )
        assert result["ok"] is False
        assert result["code"] == "WF_RUN_INPUT_TYPE"
        assert result["input_errors"] == [
            "apply: expected boolean, got 'banana'",
            "min_cluster_size: expected number, got 'not-a-number'",
        ]
        assert (
            store.list_runs(workflow_name="knowledge-lint", limit=5)[1] == before
        ), "a run was created for inputs the engine was going to choke on"

    def test_a_well_typed_string_input_lands_on_the_run_as_a_real_type(self, home: Path) -> None:
        """The other half: this must not become a wall a working caller cannot cross. A chat
        planner, a shell and an HTML form all send `"7"`, and the run record shows `7`."""
        from personalclaw.workflows import store

        result = _start(name="knowledge-lint", inputs={"apply": "yes", "min_cluster_size": "7"})
        # No supervisor in a test process, so the run is created and then not driven.
        assert result["code"] == "WF_NO_SUPERVISOR", result
        run = [r for r in store.list_runs(workflow_name="knowledge-lint", limit=5)[0]][0]
        assert run.inputs["apply"] is True
        assert run.inputs["min_cluster_size"] == 7

    def test_the_check_runs_at_the_door_before_preflight_spends_anything(self, home: Path) -> None:
        """Ordering matters: preflight resolves models and credentials, so a mistyped input must be
        refused before it, not after. Asserted by NOT skipping preflight — in a bare home preflight
        fails, so seeing the type error proves which ran first."""
        result = asyncio.run(
            service.start_run(name="knowledge-lint", inputs={"apply": "banana"}, supervisor=None)
        )
        assert result["code"] == "WF_RUN_INPUT_TYPE", result

    def test_the_coercion_is_reached_from_start_run(self, home: Path) -> None:
        """🪤 Every new guard needs a non-test caller. Asserted by observing the RESULT change when
        the module function is stubbed — a source grep would pass on an import that is never
        invoked, and this fix is worthless unless this exact call happens."""
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(
                contracts,
                "coerce_declared_inputs",
                lambda _spec, _provided: ({}, ["sentinel: stubbed"]),
            )
            result = _start(name="knowledge-lint", inputs={"apply": True})
            assert result["input_errors"] == ["sentinel: stubbed"]
        finally:
            monkey.undo()

    def test_the_service_code_is_mapped_to_a_400(self, home: Path) -> None:
        """An unmapped code falls to a generic 400 already, but the wire code is what a client
        branches on — and `_STATUS_MAP` is where a reader looks for what this refusal IS."""
        from personalclaw.workflows import handlers as H

        assert H._STATUS_MAP["WF_RUN_INPUT_TYPE"] == (400, "invalid_inputs")
        resp = H._fail(
            {"code": "WF_RUN_INPUT_TYPE", "message": "x", "input_errors": ["apply: bad"]}
        )
        assert resp.status == 400


# ── why one vocabulary at the door beats three at the consumers ───────────────────────────


class TestTheDialectsItReplaces:
    def test_the_three_consumers_disagreed_about_one_declared_boolean(self) -> None:
        """🪤 The measurement that chose the fix's SHAPE. These are the real functions, and the
        string `"1"` still means three different things to them — which is exactly why the type is
        resolved once at the door instead of at each consumer. If a future change makes them agree,
        this test should be deleted, not adjusted: it exists to justify a location.
        """
        from personalclaw.action_providers import knowledge_maintain_provider as provider
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.conditions import evaluate

        assert provider._truthy("1") is True
        assert evaluate("{{inputs.fix}} == true", BindingContext(inputs={"fix": "1"})) is False

        from personalclaw.workflows.models import Node
        from personalclaw.workflows.tick import _select_case

        branch = Node.from_dict(
            {
                "kind": "branch",
                "id": "fix-route",
                "config": {"on": "{{inputs.fix}}", "enum": ["true", "false"]},
                "cases": {
                    "true": {"kind": "action", "id": "fix", "config": {}},
                    "false": {"kind": "action", "id": "skip", "config": {}},
                },
            }
        )
        assert (
            _select_case(branch, BindingContext(inputs={"fix": "1"})) is None
        ), "the branch matched a case, so the mid-run routing failure this cites is gone"
        # And with the door doing its job, all three receive the same real bool.
        coerced, errors = contracts.coerce_declared_inputs(
            spec_of(fix={"type": "boolean"}), {"fix": "1"}
        )
        assert errors == [] and coerced["fix"] is True
        assert provider._truthy(True) is True
        assert evaluate("{{inputs.fix}} == true", BindingContext(inputs={"fix": True})) is True
        assert _select_case(branch, BindingContext(inputs={"fix": True}))[0] == "true"


class TestEveryBundledTemplateStillTypechecks:
    def test_every_declared_default_satisfies_its_own_declared_type(self) -> None:
        """A shipped template whose default its own type rejects would refuse to run the moment a
        caller passed that default back — which the launch form now does, because it seeds the
        field from the default. Asserted over the whole library rather than one template."""
        from personalclaw.workflows import bundled_defs

        bad: list[str] = []
        for name in bundled_defs.template_names():
            spec = bundled_defs.read_template(name).to_dict()
            declared = spec.get("inputs") or {}
            supplied = {
                key: meta.get("default")
                for key, meta in declared.items()
                if isinstance(meta, dict) and meta.get("default") is not None
            }
            _coerced, errors = contracts.coerce_declared_inputs(spec, supplied)
            bad += [f"{name}: {e}" for e in errors]
        assert bad == []

    def test_every_declared_type_in_the_library_is_one_the_door_enforces(self) -> None:
        """The fail-open branch above is a safety net, not a resting place: if a SHIPPED template
        declares a type outside the vocabulary, its inputs are unchecked and nobody would know."""
        from personalclaw.workflows import bundled_defs

        seen: set[str] = set()
        for name in bundled_defs.template_names():
            declared = bundled_defs.read_template(name).to_dict().get("inputs") or {}
            for meta in declared.values():
                if isinstance(meta, dict) and meta.get("type"):
                    seen.add(str(meta["type"]))
        assert seen, "no declared types were found — the scanner read nothing"
        assert seen <= contracts.DECLARED_TYPES, sorted(seen - contracts.DECLARED_TYPES)
