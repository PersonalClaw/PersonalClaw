"""The ``sandbox.cgroup_scopes`` config round-trip (PHF-2).

The cgroup tier is enforcement machinery in ``sandbox.py``; this file covers the
CONFIG contract that makes it reachable, point by point. A partially wired field is
the exact failure this repo's round-trip contract exists to prevent — a knob that
loads but has no ``_meta`` cannot be rendered, one that has ``_meta`` but no
``load()`` wiring silently reverts to its default on every reload, and one absent
from ``_EDITABLE_CONFIG`` is unreachable from the API and therefore from any UI.

Every test drives config through a ``config_path`` patched at ``tmp_path``; nothing
here touches the real home.
"""

from dataclasses import fields
from unittest.mock import patch

import pytest

from personalclaw.config.loader import AppConfig, SandboxConfig

FIELD_NAME = "cgroup_scopes"


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """Redirect config_path() to a temp file, and point the home at tmp_path too."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=p):
        yield p


def _sandbox_field(name: str):
    """Return the declared ``SandboxConfig`` field *name*, failing loudly if absent."""
    matches = [f for f in fields(SandboxConfig) if f.name == name]
    assert matches, f"SandboxConfig declares no field named {name!r}"
    return matches[0]


# --- the dataclass ---------------------------------------------------------------


def test_the_cgroup_tier_defaults_off():
    """An opt-in hardening tier must never arrive enabled.

    Enabling it changes how EVERY agent-influenced spawn is launched (through a
    transient systemd scope), so an install that never asked for it must not get it.
    """
    assert SandboxConfig().cgroup_scopes is False
    assert AppConfig().sandbox.cgroup_scopes is False


def test_the_field_carries_renderable_metadata():
    """No ``_meta`` label/help ⇒ a knob only a source-reader can find."""
    meta = _sandbox_field(FIELD_NAME).metadata
    assert meta.get("label", "").strip(), "cgroup_scopes has no _meta label"
    assert meta.get("help", "").strip(), "cgroup_scopes has no _meta help"
    # The help must say the two things that decide whether to touch it at all.
    help_text = meta["help"].lower()
    assert "linux" in help_text, "the help must disclose that the tier is Linux-only"


# --- load() ----------------------------------------------------------------------


def test_load_reads_the_flag_from_config_json(cfg_file):
    """``{"sandbox": {"cgroup_scopes": true}}`` on disk must reach the dataclass.

    This is the wiring point that fails silently: without it the field keeps its
    default forever and the next save() writes the default back over the user's value.
    """
    cfg_file.write_text('{"sandbox": {"cgroup_scopes": true}}', encoding="utf-8")

    loaded = AppConfig.load()

    assert loaded.sandbox.cgroup_scopes is True


def test_an_absent_section_loads_the_declared_default(cfg_file):
    """An empty config must produce the dataclass default, not a load()-side drift."""
    assert AppConfig.load().sandbox.cgroup_scopes is SandboxConfig().cgroup_scopes


def test_the_flag_survives_a_save_load_cycle(cfg_file):
    """save() must write the key and load() must read it back (both directions)."""
    import json

    cfg = AppConfig()
    cfg.sandbox.cgroup_scopes = True
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["sandbox"]["cgroup_scopes"] is True
    assert AppConfig.load().sandbox.cgroup_scopes is True


@pytest.mark.parametrize(
    "garbage",
    [
        "false",
        "no",
        "off",
        "disabled",
        "",
        "   ",
        "maybe",
        "TRUE-ish",
        [],
        ["true"],
        {},
        None,
        0,
        2,
        -1,
        3.7,
    ],
)
def test_garbage_does_not_crash_load_or_enable_the_tier(cfg_file, garbage):
    """A junk value must degrade to the declared default (OFF), never raise.

    Two rails deliver this, and both matter. The derived ``JSON_SCHEMA`` types the
    field as ``boolean``, so ``_validate_config_data`` strips any non-boolean value
    before the field mapping runs; ``_expose_flag`` then fails OFF on the resulting
    absence. Same net effect as the numeric siblings' ``_safe_int``: an unparseable
    value resolves to the declared default instead of making config.json unloadable.
    """
    import json

    cfg_file.write_text(json.dumps({"sandbox": {"cgroup_scopes": garbage}}), encoding="utf-8")

    loaded = AppConfig.load()  # must not raise

    assert loaded.sandbox.cgroup_scopes is False, f"{garbage!r} silently enabled the cgroup tier"


@pytest.mark.parametrize("truthy_string", ["true", "True", "yes", "on", "1"])
def test_a_truthy_STRING_is_rejected_rather_than_honoured(cfg_file, caplog, truthy_string):
    """Measured behaviour: only a real JSON boolean opts in — ``"true"`` does NOT.

    Not the obvious outcome, so it is pinned here. The schema layer is generated from
    the dataclass annotation, so declaring ``cgroup_scopes: bool`` alone made every
    non-boolean spelling a typed violation: the value is dropped with a warning and
    the default stands. A user who writes ``"cgroup_scopes": "true"`` therefore does
    NOT get the tier — they get a log line. Asserting the warning is what keeps this
    a documented contract instead of a silent surprise.
    """
    import json
    import logging

    cfg_file.write_text(json.dumps({"sandbox": {"cgroup_scopes": truthy_string}}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="personalclaw.config.loader"):
        loaded = AppConfig.load()

    assert loaded.sandbox.cgroup_scopes is False
    assert any(
        "sandbox.cgroup_scopes" in r.getMessage() and "expected boolean" in r.getMessage()
        for r in caplog.records
    ), "a non-boolean was dropped with no warning — the user has no way to learn why"


def test_the_derived_schema_types_the_flag_as_a_boolean():
    """The rail above is only real if the field is actually IN the derived schema.

    Vacuity guard for the validation layer: a field the schema walker never reached
    would accept any junk silently, and the garbage sweep above would then be passing
    for the wrong reason (``_expose_flag`` alone) without anyone noticing.
    """
    from personalclaw.config.schema import JSON_SCHEMA

    sandbox_props = JSON_SCHEMA["properties"]["sandbox"]["properties"]
    assert FIELD_NAME in sandbox_props, "the schema walker never reached cgroup_scopes"
    assert sandbox_props[FIELD_NAME]["type"] == "boolean"


# --- to_dict() -------------------------------------------------------------------


def test_to_dict_carries_the_flag():
    """``to_dict()`` is automatic via ``asdict`` — prove it, don't assume it."""
    sandbox = AppConfig().to_dict()["sandbox"]
    assert FIELD_NAME in sandbox, "to_dict() dropped cgroup_scopes"
    assert sandbox[FIELD_NAME] is False


# --- the PATCH write path --------------------------------------------------------


def test_the_flag_is_patch_editable():
    """Absent from ``_EDITABLE_CONFIG`` ⇒ unreachable from the API or any UI."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG.get("sandbox.cgroup_scopes") == {"type": "bool"}


def test_no_sandbox_allowlist_key_is_dead():
    """Vacuity guard: every ``sandbox.*`` allowlist key must name a real field.

    The allowlist key is spelled as a string, so a dataclass rename can strand it
    matching nothing — a PATCH that 400s on the new name while the dead entry sits
    there looking wired. Derive the key from the field object instead of retyping it,
    and check the whole section so this guard cannot pass on an empty match set.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    declared = {f.name for f in fields(SandboxConfig)}
    keys = [k for k in _EDITABLE_CONFIG if k.startswith("sandbox.")]
    assert keys, "no sandbox.* keys in _EDITABLE_CONFIG — this guard would be vacuous"

    dead = [k for k in keys if k.split(".", 1)[1] not in declared]
    assert not dead, f"_EDITABLE_CONFIG keys naming no SandboxConfig field: {dead}"

    # ...and the key for THIS field is derived from the field name, not retyped.
    derived = f"sandbox.{_sandbox_field(FIELD_NAME).name}"
    assert derived in _EDITABLE_CONFIG, f"{derived} is not PATCH-editable"
