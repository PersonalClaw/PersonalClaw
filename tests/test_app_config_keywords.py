"""#616 — declared schema constraints are enforced, not silently inert.

Both config validators honored only ``type`` and ``enum``; ``minimum``/``maximum``/
``exclusiveMinimum``/``exclusiveMaximum``/``pattern``/``minLength``/``maxLength`` were accepted
in a manifest and ignored, so an author writing ``"minimum": 1`` got silent acceptance of
``-99`` and a path-traversal-shaped string sailed through a ``^[a-z]{2}$`` pattern.

These rails drive the issue's own measured schema through the app path plus the guard edges:
constraints apply only to correctly-typed values (no comparison crashes), and a broken manifest
regex is the AUTHOR's defect — logged and skipped, never a wall in front of the user. The
shared-authority half (the provider path, which is the reachable one) is in
``test_config_schema_validator.py``.
"""

from __future__ import annotations

from personalclaw.apps.app_config import validate_config

# The issue's measured schema, verbatim.
SCHEMA = {
    "type": "object",
    "properties": {
        "timeout_secs": {"type": "integer", "minimum": 1, "maximum": 120},
        "lang": {"type": "string", "pattern": "^[a-z]{2}$", "maxLength": 2},
    },
}


class TestNumericBounds:
    def test_below_minimum_rejected(self):
        errs = validate_config({"timeout_secs": -99}, SCHEMA)
        assert any("at least 1" in e for e in errs)

    def test_above_maximum_rejected(self):
        errs = validate_config({"timeout_secs": 99999}, SCHEMA)
        assert any("at most 120" in e for e in errs)

    def test_in_range_accepted(self):
        assert validate_config({"timeout_secs": 30}, SCHEMA) == []

    def test_exclusive_bounds(self):
        schema = {
            "properties": {
                "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}
            }
        }
        assert any("greater than 0" in e for e in validate_config({"ratio": 0}, schema))
        assert any("less than 1" in e for e in validate_config({"ratio": 1}, schema))
        assert validate_config({"ratio": 0.5}, schema) == []

    def test_wrong_typed_value_gets_type_error_not_a_crash(self):
        # The bound must not be compared against a string — the type error is the only
        # complaint, and nothing raises.
        errs = validate_config({"timeout_secs": "soon"}, SCHEMA)
        assert any("must be an integer" in e for e in errs)
        assert not any("at least" in e for e in errs)

    def test_bool_is_not_a_number_for_bounds(self):
        # bool subclasses int; it already fails the type check and must not be range-checked
        # as 0/1.
        errs = validate_config({"timeout_secs": True}, SCHEMA)
        assert any("not a boolean" in e for e in errs)
        assert not any("at least" in e for e in errs)


class TestStringConstraints:
    def test_pattern_rejects_the_traversal_string(self):
        errs = validate_config({"lang": "../../etc/passwd"}, SCHEMA)
        assert any("does not match the required format" in e for e in errs)

    def test_pattern_accepts_a_conforming_value(self):
        assert validate_config({"lang": "en"}, SCHEMA) == []

    def test_max_length_rejected(self):
        errs = validate_config({"lang": "eng"}, SCHEMA)
        assert any("at most 2 characters" in e for e in errs)

    def test_min_length(self):
        schema = {"properties": {"name": {"type": "string", "minLength": 3}}}
        assert any("at least 3 characters" in e for e in validate_config({"name": "ab"}, schema))
        assert validate_config({"name": "abc"}, schema) == []

    def test_broken_manifest_pattern_skips_never_blocks(self):
        # A regex only the APP AUTHOR can fix must not wall off the user's write: the check is
        # skipped with a warning; siblings still apply.
        schema = {
            "properties": {"lang": {"type": "string", "pattern": "[unclosed", "maxLength": 2}}
        }
        assert validate_config({"lang": "en"}, schema) == []
        errs = validate_config({"lang": "eng"}, schema)
        assert any("at most 2 characters" in e for e in errs)


class TestExistingContractUntouched:
    def test_type_enum_unknown_required_still_enforced(self):
        schema = {
            "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
            "required": ["mode"],
        }
        assert any("must be one of" in e for e in validate_config({"mode": "c"}, schema))
        assert any("Missing required field" in e for e in validate_config({}, schema))
        assert any(
            "unknown config key" in e for e in validate_config({"mode": "a", "x": 1}, schema)
        )
        assert validate_config({"mode": "a"}, schema) == []
