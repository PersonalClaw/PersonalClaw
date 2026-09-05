"""One validator for a declared config/settings schema (#616, and #491's message half).

Two implementations of the same JSON-Schema subset existed side by side and had drifted:

    app_config.validate_config       app manifest `configSchema`      — no bounds
    ProviderSettings.validate        provider manifest `settingsSchema` — no bounds, AND it
                                     accepted True for an `integer`, because bool is an int
                                     subclass

Which one matters is measurable, and the answer is the opposite of where the issue points:
**0 of 30 core natives and 0 of 54 first-party apps declare a `configSchema`, while 64 declare
a provider `settingsSchema`** (9 with `required`). So the reachable copy was the one with the
boolean hole — and two SHIPPED manifests declare bounds it ignored (asserted below, read from
the real manifests so the census cannot rot).

The two OBJECT-level policies differ on purpose and stay at their call sites; only the
per-property rules are shared. Both halves are asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps.app_config import validate_config
from personalclaw.apps.schema_validate import ENFORCED_KEYWORDS, field_label, validate_properties
from personalclaw.providers.settings import ProviderSettings

LABELLED = {
    "api_key": {"type": "string", "minLength": 8, "x-meta": {"label": "API key"}},
    "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "x-meta": {"label": "Timeout"}},
    "region": {"type": "string", "enum": ["us", "eu"], "x-meta": {"label": "Region"}},
    "endpoint": {"type": "string", "pattern": "^https://", "x-meta": {"label": "Endpoint"}},
}
SCHEMA = {"type": "object", "properties": LABELLED, "required": ["api_key"]}

NATIVE_APPS = Path(__file__).resolve().parents[1] / "src/personalclaw/apps/native"


def _only(errors: list[str]) -> str:
    assert len(errors) == 1, errors
    return errors[0]


def _settings_schema(app: str) -> dict:
    manifest = json.loads((NATIVE_APPS / app / "app.json").read_text(encoding="utf-8"))
    return manifest["provider"]["settingsSchema"]


# ── the bounds that were inert in BOTH paths ───────────────────────────────────────────────


def test_minimum_is_enforced():
    assert "at least 1" in _only(validate_properties({"timeout": 0}, LABELLED))


def test_maximum_is_enforced():
    assert "at most 600" in _only(validate_properties({"timeout": 6000}, LABELLED))


def test_minLength_is_enforced():
    assert "at least 8 characters" in _only(validate_properties({"api_key": "short"}, LABELLED))


def test_maxLength_is_enforced():
    props = {"note": {"type": "string", "maxLength": 3}}
    assert "at most 3 characters" in _only(validate_properties({"note": "abcd"}, props))


def test_pattern_is_enforced():
    assert "required format" in _only(validate_properties({"endpoint": "http://x"}, LABELLED))


def test_exclusive_bounds_are_enforced():
    props = {"ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}}
    assert "greater than 0" in _only(validate_properties({"ratio": 0}, props))
    assert "less than 1" in _only(validate_properties({"ratio": 1}, props))


def test_a_value_inside_every_bound_passes():
    # Vacuity guard: the constraints must not reject legitimate input.
    ok = {"api_key": "abcdefgh", "timeout": 30, "region": "eu", "endpoint": "https://x"}
    assert validate_properties(ok, LABELLED, ["api_key"]) == []


def test_an_authors_broken_regex_does_not_make_the_field_unfillable():
    # Their bug, not the user's — refusing every value would be worse than skipping the check.
    props = {"x": {"type": "string", "pattern": "([unclosed"}}
    assert validate_properties({"x": "anything"}, props) == []


# ── the defect was live on SHIPPED manifests, not just hypothetical ────────────────────────


@pytest.mark.parametrize(
    "app, field, bad",
    [
        ("native-vector-memory", "confidence_threshold", 5),
        ("native-vector-memory", "confidence_threshold", -1),
        ("native-vector-memory", "confidence_threshold", True),
        ("browse-action", "max_steps", 0),
        ("browse-action", "max_steps", -5),
    ],
)
def test_a_shipped_schemas_declared_bound_is_now_honored(app, field, bad):
    """Read from the real manifests: these two declare bounds the provider path ignored.

    Measured before the fix — ``validate({"confidence_threshold": 5})`` returned ``[]`` for a
    field declaring ``[0.0, 1.0]``. If someone relaxes a manifest, this rail fails loudly rather
    than quietly asserting nothing.
    """
    schema = _settings_schema(app)
    props = schema["properties"]
    assert field in props, f"{app} no longer declares {field}"
    assert any(
        k in props[field] for k in ("minimum", "maximum")
    ), f"{app}.{field} no longer declares a bound"
    assert validate_properties({field: bad}, props), f"{app}.{field}={bad!r} must be refused"


def test_those_shipped_fields_still_accept_their_documented_values():
    # The other half of the same claim: enforcement must not break a legitimate setting.
    vm = _settings_schema("native-vector-memory")["properties"]
    assert validate_properties({"confidence_threshold": 0.75}, vm) == []
    ba = _settings_schema("browse-action")["properties"]
    assert validate_properties({"max_steps": 12}, ba) == []


# ── the boolean hole the reachable path had ────────────────────────────────────────────────


def test_True_is_not_an_integer():
    # `bool` is an `int` subclass, so the provider path stored a boolean in a numeric field.
    assert "not a boolean" in _only(validate_properties({"timeout": True}, LABELLED))


def test_False_is_not_a_number():
    props = {"ratio": {"type": "number"}}
    assert "not a boolean" in _only(validate_properties({"ratio": False}, props))


def test_a_boolean_field_still_takes_a_boolean():
    props = {"flag": {"type": "boolean"}}
    assert validate_properties({"flag": True}, props) == []


# ── messages name the LABEL, on both paths ─────────────────────────────────────────────────


def test_a_missing_required_key_names_its_label():
    assert (
        _only(validate_properties({}, LABELLED, ["api_key"])) == "Missing required field: API key"
    )


def test_a_type_error_names_its_label_not_the_schema_key():
    # #491's complaint: the only feedback was a 400 naming the schema key, not the form's label.
    msg = _only(validate_properties({"timeout": "soon"}, LABELLED))
    assert msg.startswith("Timeout:") and "timeout" not in msg


def test_field_label_falls_back_to_the_key():
    assert field_label({"type": "string"}, "api_key") == "api_key"
    assert field_label(None, "api_key") == "api_key"
    assert field_label({"x-meta": {"label": "  "}}, "api_key") == "api_key"


def test_one_fault_is_reported_once():
    # A wrong type must not also report the bound it could never satisfy.
    assert len(validate_properties({"timeout": "soon"}, LABELLED)) == 1


# ── both call sites delegate, and each keeps its OWN object-level policy ────────────────────


def test_the_app_path_enforces_the_bounds_now():
    errors = validate_config({"api_key": "short"}, SCHEMA)
    assert any("at least 8 characters" in e for e in errors), errors


def test_the_provider_path_enforces_the_bounds_now():
    errors = ProviderSettings.validate({"api_key": "short"}, SCHEMA)
    assert any("at least 8 characters" in e for e in errors), errors


def test_both_paths_agree_word_for_word():
    # The whole point of one validator: the same input cannot produce two dialects.
    bad = {"api_key": "abcdefgh", "timeout": True}
    assert validate_config(bad, SCHEMA) == ProviderSettings.validate(bad, SCHEMA)


def test_the_app_path_still_refuses_an_unknown_key():
    # Its own policy, deliberately kept: an app's config is exactly what its manifest declares.
    errors = validate_config({"api_key": "abcdefgh", "nope": 1}, SCHEMA)
    assert any("unknown config key" in e for e in errors), errors


def test_the_provider_path_still_IGNORES_an_unknown_key():
    # Also deliberate: a stored config may carry a key from an older manifest, and refusing it
    # would make the whole config unsavable.
    assert ProviderSettings.validate({"api_key": "abcdefgh", "legacy": 1}, SCHEMA) == []


def test_the_provider_path_still_skips_validation_with_no_schema():
    assert ProviderSettings.validate({"anything": 1}, {}) == []


def test_the_app_path_still_takes_no_config_with_no_schema():
    assert validate_config({"anything": 1}, {}) != []


# ── the keyword set is named, so a future keyword cannot be half-added ─────────────────────


def test_every_enforced_keyword_is_actually_enforced():
    """The list is what a rail (and an author) can trust. Each entry gets a violating value that
    must produce an error, so a keyword cannot be listed without being implemented."""
    cases: dict[str, tuple[dict, dict]] = {
        "type": ({"a": {"type": "integer"}}, {"a": "x"}),
        "enum": ({"a": {"type": "string", "enum": ["y"]}}, {"a": "x"}),
        "minimum": ({"a": {"type": "integer", "minimum": 5}}, {"a": 1}),
        "maximum": ({"a": {"type": "integer", "maximum": 5}}, {"a": 9}),
        "exclusiveMinimum": ({"a": {"type": "integer", "exclusiveMinimum": 5}}, {"a": 5}),
        "exclusiveMaximum": ({"a": {"type": "integer", "exclusiveMaximum": 5}}, {"a": 5}),
        "minLength": ({"a": {"type": "string", "minLength": 5}}, {"a": "x"}),
        "maxLength": ({"a": {"type": "string", "maxLength": 1}}, {"a": "xx"}),
        "pattern": ({"a": {"type": "string", "pattern": "^y"}}, {"a": "x"}),
    }
    assert set(cases) == set(ENFORCED_KEYWORDS), "a keyword is listed but unproven, or vice versa"
    for keyword, (props, values) in cases.items():
        assert validate_properties(values, props), f"{keyword} is declared enforced but is inert"


def test_no_second_per_property_validator_survives():
    """Clean break: the provider copy is deleted, not left beside the shared one."""
    import inspect

    from personalclaw.providers import settings as provider_settings

    src = inspect.getsource(provider_settings.ProviderSettings.validate)
    assert "validate_properties" in src, "the provider path must delegate"
    for gone in ("expected string", "expected integer", "expected number"):
        assert gone not in src, f"the drifted provider copy still carries {gone!r}"
