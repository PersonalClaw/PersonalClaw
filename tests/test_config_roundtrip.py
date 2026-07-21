"""Tests that AppConfig.save() preserves all dataclass fields.

Regression test for the bug where to_dict() omitted inbox and skills —
causing save() to silently drop them from config.json, and for the dual
bug-class where a field present in to_dict() but missing from load()'s
field-by-field mapping silently reverts to its default on every reload
(and the next save() then wipes the user's value from the file).
"""

import json
from dataclasses import fields, is_dataclass
from unittest.mock import patch

import pytest

from personalclaw.config.loader import AppConfig, ProjectionRuleConfig


@pytest.fixture()
def cfg_file(tmp_path):
    """Redirect config_path() to a temp file for isolation."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=p):
        yield p


def test_to_dict_includes_all_dataclass_fields():
    """Every field on AppConfig must appear in to_dict() output."""
    cfg = AppConfig()
    d = cfg.to_dict()
    for f in fields(AppConfig):
        assert f.name in d, f"to_dict() missing field: {f.name}"


def test_save_load_roundtrip_inbox(cfg_file):
    """Inbox config must survive a save/load cycle."""
    cfg = AppConfig()
    cfg.inbox.enabled = True
    cfg.inbox.poll_interval_seconds = 30
    cfg.inbox.style_rules = ["never commit to dates"]
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["inbox"]["enabled"] is True
    assert raw["inbox"]["poll_interval_seconds"] == 30
    assert raw["inbox"]["style_rules"] == ["never commit to dates"]


def test_save_load_roundtrip_skills(cfg_file):
    """Skills config must survive a save/load cycle."""
    cfg = AppConfig()
    cfg.skills.max_triggered = 5
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["skills"]["max_triggered"] == 5


# ---------------------------------------------------------------------------
# Exhaustive leaf-field round-trip: save() → load() must preserve EVERY field.
#
# A field added to a config dataclass but omitted from AppConfig.load()'s
# explicit mapping passes to_dict()/save() (asdict covers it) yet silently
# reads its default after reload — the exact gap that hid
# agent.spawn_min_memory_gb, dashboard.widget_density and the inbox retention
# trio. This walks every leaf generically so any future omission fails here.
# ---------------------------------------------------------------------------

# Sections whose leaves are walked generically. hooks/agents/memory_stores are
# dict-typed top-level fields with their own migration/seeding semantics in
# load() — covered by dedicated tests elsewhere, not leaf-walkable.
_SECTIONS = [
    "agent",
    "session",
    "loops",
    "memory",
    "dashboard",
    "inbox",
    "tools",
    "skills",
    "workflows",
    "learning",
    "security",
]

# Values for fields the generic flip/append rules can't produce: enum members,
# __post_init__ clamp ranges, load()-side migrations ("acp" would be migrated
# to native — use the open acp:<cli> form), sanitizers (bot_name), and
# structured fields.
_SPECIAL = {
    ("agent", "approval_mode"): "trust_reads",
    ("agent", "sandbox"): "off",
    ("agent", "log_level"): "DEBUG",
    ("agent", "provider"): "acp:claude-code",
    ("agent", "bot_name"): "TestBot",
    ("agent", "soft_stop_budget_secs"): 12.5,
    ("dashboard", "widget_density"): "less",
    ("dashboard", "terminal"): {"enabled": False, "persist": True},
    ("dashboard", "dashboard_layout"): {"widgets": [], "v": 1},
    ("inbox", "poll_interval_seconds"): 90,
    ("skills", "auto_similarity_threshold"): 0.5,
    ("workflows", "match_threshold"): 0.5,
    ("tools", "projection_rules"): [
        ProjectionRuleConfig(name="t", match_regex="^x", strategy="log")
    ],
}


def _non_default(section: str, name: str, default):
    """Produce a valid value that differs from *default*."""
    if (section, name) in _SPECIAL:
        return _SPECIAL[(section, name)]
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 7
    if isinstance(default, float):
        return default + 1.5
    if isinstance(default, str):
        return f"{default}-x" if default else "test-value"
    if isinstance(default, list):
        return list(default) + ["extra-item"]
    raise AssertionError(
        f"no non-default rule for {section}.{name} ({type(default).__name__}) — "
        f"add a _SPECIAL entry"
    )


def _mutate_leaves(section: str, obj, prefix: str = "") -> dict:
    """Set every leaf of a section dataclass to a non-default value.

    Returns {dotted_path: expected_value} for later comparison. Recurses into
    nested dataclasses (e.g. security.egress).
    """
    expected: dict = {}
    for f in fields(obj):
        default = getattr(obj, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(default) and not isinstance(default, type):
            expected.update(_mutate_leaves(section, default, prefix=f"{path}."))
            continue
        value = _non_default(section, path, default)
        assert value != default, f"{section}.{path}: test value equals default"
        setattr(obj, f.name, value)
        expected[path] = value
    return expected


def _read_leaf(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def test_every_leaf_field_survives_save_load(cfg_file):
    """save() → load() must return every leaf field unchanged."""
    cfg = AppConfig()
    expected: dict[str, dict] = {}
    for section in _SECTIONS:
        expected[section] = _mutate_leaves(section, getattr(cfg, section))
    # Scalar top-level fields (dict-typed ones excluded — see _SECTIONS note).
    cfg.auto_update = False
    cfg.timezone = "Europe/Berlin"
    cfg.snapshot_dir = "test-value"
    cfg.observe_max_messages = 207
    cfg.observe_ttl_hours = 169.5
    cfg.save()

    loaded = AppConfig.load()

    diffs: list[str] = []
    for section, leaves in expected.items():
        for dotted, want in leaves.items():
            got = _read_leaf(getattr(loaded, section), dotted)
            if got != want:
                diffs.append(f"{section}.{dotted}: saved {want!r} but loaded {got!r}")
    for name, want in [
        ("auto_update", False),
        ("timezone", "Europe/Berlin"),
        ("snapshot_dir", "test-value"),
        ("observe_max_messages", 207),
        ("observe_ttl_hours", 169.5),
    ]:
        got = getattr(loaded, name)
        if got != want:
            diffs.append(f"{name}: saved {want!r} but loaded {got!r}")
    assert not diffs, "load() drops saved fields:\n" + "\n".join(diffs)


def test_load_fallbacks_match_dataclass_defaults(cfg_file):
    """An empty config section must load exactly the dataclass defaults.

    Guards the default-drift class (memory.auto_promote_every_n was 10 in the
    dataclass but 5 in load()'s .get() fallback): loading {} must equal
    constructing AppConfig() for every leaf.
    """
    loaded = AppConfig.load()  # cfg_file fixture starts as {}
    pristine = AppConfig()
    diffs: list[str] = []
    for section in _SECTIONS:
        for f in fields(getattr(pristine, section)):
            got = getattr(getattr(loaded, section), f.name)
            want = getattr(getattr(pristine, section), f.name)
            if got != want:
                diffs.append(
                    f"{section}.{f.name}: dataclass default {want!r} "
                    f"but empty-config load gives {got!r}"
                )
    assert not diffs, "load() fallback drift vs dataclass defaults:\n" + "\n".join(diffs)
