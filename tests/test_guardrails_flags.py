"""Fail-safe guard-flag parsing + the safe-default schema test (AUTONOMY-GUARDRAILS §5).

The tenet: config flags guarding destructive/trust behavior parse missing/null/
unknown as ENABLED (``guard_flag``), and guard-class DATACLASS fields default to
their SAFE value (a config typo → the dataclass default → must stay safe).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

from personalclaw.guardrails.flags import guard_flag

# ── guard_flag: fail-safe parsing ────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),  # missing → enabled
        (True, True),
        (False, False),  # explicit bool honored
        ("", True),  # empty string → enabled (fail-safe)
        ("0", False),
        ("false", False),
        ("False", False),
        ("  OFF ", False),
        ("no", False),
        ("disable", False),
        ("disabled", False),
        ("n", False),
        ("f", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("garbage", True),  # unknown token → enabled (fail-safe)
        (1, True),
        (0, False),
        (object(), True),  # unknown shape → enabled
    ],
)
def test_guard_flag_fail_safe(value, expected):
    assert guard_flag(value) is expected


# ── Safe-default schema test: guard-class fields default SAFE ────────────────


def _walk_guard_class_fields(dc, prefix=""):
    """Yield (dotted_path, default_value, meta) for every guard-class field in a
    dataclass tree (recursing into nested dataclass defaults)."""
    for f in fields(dc):
        default = getattr(dc, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(default) and not isinstance(default, type):
            yield from _walk_guard_class_fields(default, prefix=f"{path}.")
            continue
        meta = f.metadata or {}
        if meta.get("guard_class"):
            yield path, default, meta


def test_guard_class_fields_default_safe():
    """Every _meta field tagged guard_class must default to one of its safe_values.

    A config typo is stripped by _validate_config_data → the dataclass default
    applies, so a guard-class field's default MUST be safe (§5). This test fails
    the build if a future guard-class field ships with a leaky default."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    found = list(_walk_guard_class_fields(cfg))
    # At least the one we know about must be present (guards against the tag being
    # silently dropped in a refactor).
    assert any(
        p == "guardrails.scan_mode" for p, _, _ in found
    ), "guardrails.scan_mode lost its guard_class tag"
    for path, default, meta in found:
        safe = meta.get("safe_values")
        assert safe, f"{path} is guard_class but declares no safe_values"
        assert (
            default in safe
        ), f"guard-class field {path} defaults to {default!r}, not a safe value {safe!r}"


def test_scan_mode_default_is_not_leaky():
    """Explicit regression: the outbound scan must default to redact, never warn
    (warn would send secrets to a remote provider)."""
    from personalclaw.config.loader import AppConfig

    assert AppConfig().guardrails.scan_mode == "redact"
