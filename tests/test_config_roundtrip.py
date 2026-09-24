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

from personalclaw.config.loader import AppConfig, ProjectionRuleConfig, SkillCatalogConfig


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


def test_save_load_roundtrip_companion(cfg_file):
    """Companion config (CA-4) must survive a save/load cycle to disk AND back through load()."""
    cfg = AppConfig()
    cfg.companion.discovery_enabled = True
    cfg.companion.instance_name = "Living room Mac"
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["companion"]["discovery_enabled"] is True
    assert raw["companion"]["instance_name"] == "Living room Mac"

    loaded = AppConfig.load()
    assert loaded.companion.discovery_enabled is True
    assert loaded.companion.instance_name == "Living room Mac"


def test_companion_fields_in_editable_allowlist():
    """CA-4: both companion fields are PATCH-editable (the write path of the round-trip)."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG.get("companion.discovery_enabled") == {"type": "bool"}
    assert _EDITABLE_CONFIG.get("companion.instance_name", {}).get("type") == "str"


def test_companion_discovery_defaults_off():
    """Announcing a service on the LAN is an opt-in — discovery must default OFF."""
    assert AppConfig().companion.discovery_enabled is False


def test_save_load_roundtrip_local_models(cfg_file):
    """Local-model knobs (LMMV-5) survive a save/load cycle to disk AND back."""
    cfg = AppConfig()
    cfg.local_models.pressure_warn_pct = 70
    cfg.local_models.sidecar_restart_max = 5
    cfg.local_models.memory_reserve_gb = 6.5
    cfg.local_models.hide_unrunnable_models = False
    cfg.local_models.whoami_ttl_s = 1200
    cfg.local_models.selftest_timeout_s = 45
    cfg.save()

    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["local_models"] == {
        "pressure_warn_pct": 70,
        "sidecar_restart_max": 5,
        "memory_reserve_gb": 6.5,
        "hide_unrunnable_models": False,
        "whoami_ttl_s": 1200,
        "selftest_timeout_s": 45,
    }

    loaded = AppConfig.load()
    assert loaded.local_models.pressure_warn_pct == 70
    assert loaded.local_models.sidecar_restart_max == 5
    assert loaded.local_models.memory_reserve_gb == 6.5
    assert loaded.local_models.hide_unrunnable_models is False
    assert loaded.local_models.whoami_ttl_s == 1200
    assert loaded.local_models.selftest_timeout_s == 45


def test_local_models_fields_in_editable_allowlist():
    """LMMV-5/LMMV-8: every knob is PATCH-editable (the write path of the round-trip)."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["local_models.pressure_warn_pct"] == {
        "type": "int",
        "min": 1,
        "max": 100,
    }
    assert _EDITABLE_CONFIG["local_models.sidecar_restart_max"]["type"] == "int"
    assert _EDITABLE_CONFIG["local_models.memory_reserve_gb"] == {
        "type": "float",
        "min": 0.0,
        "max": 64.0,
    }
    assert _EDITABLE_CONFIG["local_models.hide_unrunnable_models"] == {"type": "bool"}
    assert _EDITABLE_CONFIG["local_models.whoami_ttl_s"] == {"type": "int", "min": 0, "max": 86400}
    assert _EDITABLE_CONFIG["local_models.selftest_timeout_s"] == {
        "type": "int",
        "min": 5,
        "max": 600,
    }


def test_the_fit_reserve_defaults_to_three_gb_and_the_filter_defaults_on():
    """LMMV-8: the shipped defaults the fit readers fall back to must be the REAL ones."""
    cfg = AppConfig()
    assert cfg.local_models.memory_reserve_gb == 3.0
    assert cfg.local_models.hide_unrunnable_models is True


def test_the_fit_readers_read_the_configured_values_not_their_fallbacks(cfg_file):
    """LMMV-8's fifth round-trip point: ``fit``'s two readers see a WRITTEN value.

    Both helpers fall back on any exception, so a missing field would make them look
    healthy while reporting the shipped default forever. Writing a value no fallback
    could produce is what separates "wired" from "silently defaulting".
    """
    from personalclaw.local_models import fit

    assert fit.configured_reserve_gb() == 3.0
    assert fit.hide_unrunnable_default() is True

    cfg_file.write_text(
        json.dumps({"local_models": {"memory_reserve_gb": 11.25, "hide_unrunnable_models": False}}),
        encoding="utf-8",
    )
    assert fit.configured_reserve_gb() == 11.25
    assert fit.hide_unrunnable_default() is False


@pytest.mark.asyncio
async def test_an_out_of_range_reserve_is_rejected_by_patch_not_clamped(cfg_file):
    """A reserve edit outside 0-64 GB gets the normal typed error, never a quiet clamp.

    A clamp here would be the worst outcome: the PATCH reports success while the stored
    number differs from the one the user typed, and every later fit verdict is computed
    from a budget they never chose.
    """
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)

    async with TestClient(TestServer(app)) as client:
        for bad in (-1.0, 65.0):
            resp = await client.patch(
                "/api/config/personalclaw",
                json={"path": "local_models.memory_reserve_gb", "value": bad},
            )
            assert resp.status == 400
            assert "between 0.0 and 64.0" in json.dumps(await resp.json())
        # Nothing was written: a rejected edit leaves the shipped default in place.
        assert AppConfig.load().local_models.memory_reserve_gb == 3.0

        resp = await client.patch(
            "/api/config/personalclaw",
            json={"path": "local_models.memory_reserve_gb", "value": 5.5},
        )
        assert resp.status == 200
        assert AppConfig.load().local_models.memory_reserve_gb == 5.5


def test_a_nonsense_reserve_in_config_json_is_clamped_to_the_same_window(cfg_file):
    """A hand-edited config.json still loads: the read path clamps what PATCH refuses."""
    cfg_file.write_text(
        json.dumps({"local_models": {"memory_reserve_gb": -9, "hide_unrunnable_models": True}}),
        encoding="utf-8",
    )
    assert AppConfig.load().local_models.memory_reserve_gb == 0.0

    cfg_file.write_text(json.dumps({"local_models": {"memory_reserve_gb": 4096}}), encoding="utf-8")
    assert AppConfig.load().local_models.memory_reserve_gb == 64.0


def test_a_nonsense_pressure_threshold_is_clamped_to_a_real_percentage(cfg_file):
    """A threshold of 0 would warn forever and 900 could never warn — both read as broken."""
    cfg_file.write_text(
        json.dumps({"local_models": {"pressure_warn_pct": 900, "sidecar_restart_max": -4}}),
        encoding="utf-8",
    )
    loaded = AppConfig.load()
    assert loaded.local_models.pressure_warn_pct == 100
    assert loaded.local_models.sidecar_restart_max == 0


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
    "sandbox",
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
    "guardrails",
    "resilience",
    "evals",
    "packs",
    "companion",
    "local_models",
    "proactive",
    "apps",
    "updates",
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
    # stream_reveal is enum-constrained (smooth|immediate) — a generated "smooth-x"
    # would fail load()'s validation and fall back to the default.
    ("dashboard", "stream_reveal"): "immediate",
    ("dashboard", "terminal"): {"enabled": False, "persist": True},
    ("inbox", "poll_interval_seconds"): 90,
    # loops.judge_use_case is constrained to the use-case vocabulary (WF2LOO-17) — a
    # generated "reasoning-x" would (correctly) be refused by load() and collapse back to
    # `reasoning`. `code_tools` is a real non-default axis that proves the field
    # round-trips; `loops` deliberately is NOT used here, since a fixture should not model
    # "the judge is back on the worker's binding" as the normal case.
    ("loops", "judge_use_case"): "code_tools",
    # memory.push_min_confidence is a probability clamped to [0,1] by load() — the
    # generic rule's out-of-range value would (correctly) come back clamped.
    ("memory", "push_min_confidence"): 0.55,
    # memory.vault_mode is enum-constrained (off|mirror|two_way) — a generated "off-x"
    # would (correctly) be refused by load() and fall back through the legacy
    # `vault_enabled` read to `off`, exactly as `stream_reveal` above. `two_way` is the
    # real non-default that proves the field round-trips.
    ("memory", "vault_mode"): "two_way",
    ("skills", "auto_similarity_threshold"): 0.5,
    # surface_mode_default is enum-constrained (off|passive|suggest) — a generated "off-x" would
    # (correctly) be refused by load() and fall back to `off`, exactly as `stream_reveal` above.
    # Declaring a real member proves the field ROUND-TRIPS without asserting that the coercion is a
    # bug.
    ("workflows", "surface_mode_default"): "suggest",
    # workflows.match_threshold is a cosine floor clamped to [0,1] by load() — the generic rule's
    # 0.62 + 1.5 = 2.12 would (correctly) come back clamped, so supply an in-range non-default.
    ("workflows", "match_threshold"): 0.75,
    # workspace_default_mode is enum-constrained (scratch|worktree|in_place|container) — a
    # generated "scratch-x" would (correctly) fall back to `scratch`. `worktree` is the real
    # non-default that proves the field round-trips; `in_place` deliberately is NOT used here,
    # since a test fixture should not be the thing that models "isolation off" as normal.
    ("workflows", "workspace_default_mode"): "worktree",
    ("tools", "projection_rules"): [
        ProjectionRuleConfig(name="t", match_regex="^x", strategy="log")
    ],
    # packs.skill_catalogs is a list[SkillCatalogConfig] (AGENT-PACKS §6). load() keeps only
    # entries with a non-empty url, so supply a real one — the generic list rule would append
    # a bare string that load() filters out.
    ("packs", "skill_catalogs"): [
        SkillCatalogConfig(name="taps", url="https://example.com/index.json", kind="index")
    ],
    # tools.group_defaults is a dict[str, list[str]] (surface → active tool groups);
    # load() keeps only str→list[str] entries, so supply that shape.
    ("tools", "group_defaults"): {"background": ["core", "memory"]},
    # guardrails.scan_mode is an enum-constrained str — a generated "redact-x"
    # would fail load()'s validation and fall back to the default.
    ("guardrails", "scan_mode"): "block",
    # resilience.mid_turn_policy is enum-constrained — a generated value would fail
    # load()'s validation and fall back to the default.
    ("resilience", "mid_turn_policy"): "cancel_and_replace",
    # learning.identity_report_cadence is enum-constrained (LV-4: monthly|weekly|off) — the
    # generated "monthly-x" is refused by load() and falls back to the default, which this
    # test correctly reported as a dropped field. `weekly` is the non-default that round-trips;
    # `off` is deliberately not used here, since a fixture should not model "the periodic report
    # is switched off" as the normal case.
    ("learning", "identity_report_cadence"): "weekly",
    # security.autonomy_denylist is a list[dict] — the generic list rule would
    # append a bare string, which load() filters out (isinstance dict). Supply a
    # real rule dict so the round-trip preserves it.
    ("security", "autonomy_denylist"): [
        {"paths": ["~/.ssh/**"], "actions": ["credential-read"], "verdict": "block"}
    ],
    # updates.channel is enum-constrained (RUM-1: stable|beta|nightly) — a generated
    # "stable-x" is coerced back to `stable` by load()'s _safe_choice and would read as a
    # dropped field. `beta` is the real non-default that proves the field round-trips;
    # `nightly` is deliberately not used here, since a fixture should not model the
    # git-only track-main channel as the normal case.
    ("updates", "channel"): "beta",
    # updates.auto is enum-constrained (RUM-1: off|staged) — a generated "off-x" is coerced
    # back to `off`. `staged` is the real non-default that proves the field round-trips.
    ("updates", "auto"): "staged",
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
        ("timezone", "Europe/Berlin"),
        ("snapshot_dir", "test-value"),
        ("observe_max_messages", 207),
        ("observe_ttl_hours", 169.5),
    ]:
        got = getattr(loaded, name)
        if got != want:
            diffs.append(f"{name}: saved {want!r} but loaded {got!r}")
    assert not diffs, "load() drops saved fields:\n" + "\n".join(diffs)


def test_a_legal_zero_is_not_lost_to_the_default_or_chain(cfg_file):
    """#2952: five `_EDITABLE_CONFIG` keys declare `min: 0`, so 0 is a legal PATCH value —
    but `load()` read each one back with `X or DEFAULT`, and `0 or DEFAULT` is `DEFAULT`
    because `.get(key, DEFAULT)` already returns the *present* value `0`, which `or` then
    discards. `0` is the meaningful setting for every one of these (no confidence floor, no
    tie-break floor, no minimum interval, no brief at all) — a write that reports success
    and silently stores something else is the one outcome `_EDITABLE_CONFIG`'s own docstring
    rules out.

    Each assertion here failed on the pre-fix shape: saving 0 and reloading came back as the
    shipped default (0.7 / 0.6 / 0.62 / 6 / 800 respectively).
    """
    cfg = AppConfig()
    cfg.memory.push_min_confidence = 0.0
    cfg.evals.judge_agreement_floor = 0.0
    cfg.workflows.match_threshold = 0.0
    cfg.knowledge.consolidate_min_hours = 0
    cfg.knowledge.session_brief_max_tokens = 0
    cfg.save()

    loaded = AppConfig.load()
    assert loaded.memory.push_min_confidence == 0.0
    assert loaded.evals.judge_agreement_floor == 0.0
    assert loaded.workflows.match_threshold == 0.0
    assert loaded.knowledge.consolidate_min_hours == 0
    assert loaded.knowledge.session_brief_max_tokens == 0


def test_evals_editable_allowlist_excludes_the_capture_flag():
    """EVALUATION-SUBSTRATE §10 — the runtime-editable evals subset is in the PATCH
    allowlist, but the privacy-sensitive input-capture flag is deliberately NOT
    (mirroring external_access.mcp.allow_remote's exclusion)."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert "evals.enabled" in _EDITABLE_CONFIG
    assert "evals.study_default_k" in _EDITABLE_CONFIG
    assert "evals.judge_agreement_floor" in _EDITABLE_CONFIG
    assert "evals.ablation_cadence_days" in _EDITABLE_CONFIG
    assert "evals.default_budget_usd" in _EDITABLE_CONFIG
    assert "evals.bakeoff_capture_enabled" not in _EDITABLE_CONFIG


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


# ---------------------------------------------------------------------------
# RELEASE-UPDATE-MECHANISM RUM-1 — the `updates` block + legacy backfill.
#
# The generic leaf-walk above already proves all six fields survive save/load and
# `test_every_leaf_field_survives_save_load` covers the round-trip; these assert the
# two points that walk cannot see — the PATCH write path and the load-time legacy
# mapping — each on a known-true AND a known-false case so none can pass vacuously.
# ---------------------------------------------------------------------------


def test_updates_defaults_are_release_tracking_and_notify_only():
    """RUM-1: the shipped defaults — stable channel, no pin, notify-only, checks on 12h."""
    u = AppConfig().updates
    assert u.channel == "stable"
    assert u.pin == ""
    assert u.auto == "off"
    assert u.check_enabled is True
    assert u.check_interval_hours == 12
    assert u.last_version == ""


def test_updates_fields_in_editable_allowlist():
    """RUM-1: every field is PATCH-editable (the write path of the round-trip)."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["updates.channel"] == {
        "type": "enum",
        "values": ["stable", "beta", "nightly"],
    }
    assert _EDITABLE_CONFIG["updates.auto"] == {"type": "enum", "values": ["off", "staged"]}
    assert _EDITABLE_CONFIG["updates.check_enabled"] == {"type": "bool"}
    assert _EDITABLE_CONFIG["updates.check_interval_hours"] == {"type": "int", "min": 1, "max": 168}
    assert _EDITABLE_CONFIG["updates.pin"]["type"] == "str"
    assert _EDITABLE_CONFIG["updates.last_version"]["type"] == "str"


def test_legacy_auto_update_true_maps_to_staged_on_stable(cfg_file):
    """RUM-1 backfill: a home with legacy `auto_update=true` (and no `updates` block)
    loads to `updates.auto="staged"` + `channel="stable"` — the existing unattended-update
    git user stops riding raw main and starts riding stable release tags."""
    cfg_file.write_text(json.dumps({"auto_update": True}), encoding="utf-8")
    u = AppConfig.load().updates
    assert u.auto == "staged"
    assert u.channel == "stable"


def test_legacy_auto_update_false_maps_to_off(cfg_file):
    """RUM-1 backfill (known-false case): `auto_update=false` maps to notify-only."""
    cfg_file.write_text(json.dumps({"auto_update": False}), encoding="utf-8")
    assert AppConfig.load().updates.auto == "off"


def test_legacy_update_dev_mode_true_maps_to_nightly(cfg_file):
    """RUM-1 backfill: a home with legacy `dashboard.update_dev_mode=true` loads to
    `channel="nightly"` — the git-only track-main opt-in becomes the nightly channel."""
    cfg_file.write_text(json.dumps({"dashboard": {"update_dev_mode": True}}), encoding="utf-8")
    assert AppConfig.load().updates.channel == "nightly"


def test_legacy_update_dev_mode_false_stays_stable(cfg_file):
    """RUM-1 backfill (known-false case): dev-mode off leaves the default stable channel."""
    cfg_file.write_text(json.dumps({"dashboard": {"update_dev_mode": False}}), encoding="utf-8")
    assert AppConfig.load().updates.channel == "stable"


def test_explicit_updates_block_wins_over_legacy_flags(cfg_file):
    """RUM-1: an explicit `updates` field always wins over the legacy source, so the
    backfill is a one-time floor and never overrides a chosen value. A backwards mapping
    (legacy overriding the block) would read auto="staged"/channel="nightly" here."""
    cfg_file.write_text(
        json.dumps(
            {
                "auto_update": True,
                "dashboard": {"update_dev_mode": True},
                "updates": {"auto": "off", "channel": "stable"},
            }
        ),
        encoding="utf-8",
    )
    u = AppConfig.load().updates
    assert u.auto == "off"
    assert u.channel == "stable"


def test_every_apps_field_is_patchable_or_has_a_write_path():
    """The wiring point this file CANNOT see: the PATCH allowlist.

    The five points a config field must reach are dataclass+_meta, load(), to_dict(), a
    write path, and (if user-facing) a control. The rails above cover the first three, so a
    field with no `_EDITABLE_CONFIG` entry leaves this file fully green while the Settings
    toggle 400s. `apps.*` is user-facing config with no dedicated PUT, so every field in the
    section must be in the allowlist."""
    from personalclaw.config.loader import AppsConfig
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    missing = [f.name for f in fields(AppsConfig) if f"apps.{f.name}" not in _EDITABLE_CONFIG]
    assert not missing, f"apps config fields with no PATCH write path: {missing}"
    assert _EDITABLE_CONFIG["apps.registry_source_enabled"]["type"] == "bool"


def test_no_agent_sandbox_config_field_on_any_surface():
    """#364 — the Settings → Agent defaults → "Sandbox" control was removed, and the
    removal has to be complete on EVERY surface, not just the dataclass.

    The control wrote `agent.sandbox` ("auto"/"off") and nothing read it: sandboxing is
    decided by the `mode` argument threaded into `sandbox.wrap_argv`, which arrives from
    per-provider options or a literal at each of its call sites — `AppConfig.agent.sandbox`
    never entered that chain. A settings switch labelled "Sandbox" that makes no sandbox
    decision is a false promise about a security control, so the field, its PATCH allowlist
    entry, its frontend control, its docs row and the docstring that claimed the wiring
    existed all go together. Wiring it instead would have required choosing which of
    `wrap_argv`'s five call sites a global toggle overrides — including two deliberately
    hardcoded boundaries (`knowledge_providers/pack_parse.py`'s "strict" for untrusted
    pack parsing, `schedule_script.py`'s "standard") — which is a policy this codebase has
    not decided and a test may not mint.

    Asserted across all five surfaces in one rail on purpose: a per-surface check passes
    while any single surface still carries the field, which is the exact shape that leaves
    a "removed" control still reachable.
    """
    from pathlib import Path

    from personalclaw.config.loader import AgentConfig
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    repo = Path(__file__).resolve().parent.parent

    assert "sandbox" not in {
        f.name for f in fields(AgentConfig)
    }, "AgentConfig.sandbox is back: a config field nothing reads, under a security label"
    assert "agent.sandbox" not in _EDITABLE_CONFIG, "agent.sandbox is PATCH-writable again"
    assert "sandbox" not in AppConfig().to_dict()["agent"], "agent.sandbox is serialized again"

    panel = (repo / "web/src/pages/settings/AgentDefaultsPanel.tsx").read_text(encoding="utf-8")
    assert 'field="sandbox"' not in panel, "the Agent defaults Sandbox control is back"

    docs = (repo / "docs/reference/configuration.md").read_text(encoding="utf-8")
    assert "agent.sandbox" not in docs, "docs still document agent.sandbox as a real setting"

    sandbox_src = (repo / "src/personalclaw/sandbox.py").read_text(encoding="utf-8")
    assert "agent.sandbox" not in sandbox_src, (
        "sandbox.py still claims agent.sandbox governs the backend — the docstring that "
        "made this field look wired in the first place"
    )


def test_the_sandbox_decision_has_exactly_one_input():
    """The positive half of the rail above: `wrap_argv`'s `mode` is the single input to the
    sandbox decision, and it reaches `detect_backend` as `config_mode`. Without this, the
    absence rail above could be satisfied by deleting the field AND the enforcement."""
    import inspect

    from personalclaw import sandbox

    assert "mode" in inspect.signature(sandbox.wrap_argv).parameters
    assert "config_mode" in inspect.signature(sandbox.detect_backend).parameters
    assert sandbox.wrap_argv(["true"], mode="off") == (["true"], None)


def test_no_dashboard_layout_config_field_on_any_surface():
    """#529 — the second half of a clean break that only removed one side.

    The customizable-bento dashboard was retired in the v2 launcher-forward redesign and
    the frontend half was deleted with it. The backend half outlived it by 46 days: a
    10-id widget registry, a 45-line validator clamping to a "12-col grid" that no longer
    existed, a PUT allowlist entry, a write branch, a GET echo, the config field, and the
    typed client member. Measured before deleting, `dashboard_layout` had **nine** live
    sites across `src/` and `web/src` and **zero readers** — no code positioned, sized or
    hid anything from it. So `PUT {"dashboard_layout": {"widgets": [{"id": "tasks", …,
    "hidden": true}], "v": 1}}` returned 200, persisted, round-tripped — and the Tasks
    widget stayed visible. That is worse than an absent field: the stored data looks
    authoritative, so an app or MCP tool reading it would trust a layout the dashboard
    demonstrably ignores.

    One rail across every surface on purpose, following
    :func:`test_no_agent_sandbox_config_field_on_any_surface`: a per-surface check passes
    while any single surface still carries the field, which is the exact shape that left
    a "retired" mechanism fully writable for 46 days.
    """
    from pathlib import Path

    from personalclaw.config.loader import DashboardConfig
    from personalclaw.dashboard.handlers import files as F
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    repo = Path(__file__).resolve().parent.parent

    assert "dashboard_layout" not in {
        f.name for f in fields(DashboardConfig)
    }, "DashboardConfig.dashboard_layout is back: a persisted grid layout with no renderer"
    assert (
        "dashboard_layout" not in AppConfig().to_dict()["dashboard"]
    ), "dashboard.dashboard_layout is serialized into config.json again"
    assert (
        "dashboard.dashboard_layout" not in _EDITABLE_CONFIG
    ), "dashboard.dashboard_layout is PATCH-writable again"
    for gone in ("_sanitize_dashboard_layout", "_DASHBOARD_WIDGET_IDS"):
        assert not hasattr(F, gone), f"{gone} is back — the validator without a consumer"

    handlers = (repo / "src/personalclaw/dashboard/handlers/files.py").read_text(encoding="utf-8")
    assert (
        "dashboard_layout" not in handlers
    ), "the dashboard-config PUT allowlist or its GET echo names dashboard_layout again"
    client = (repo / "web/src/lib/api.ts").read_text(encoding="utf-8")
    assert "dashboard_layout?" not in client, "the typed client field is back"
    docs = (repo / "docs/reference/configuration.md").read_text(encoding="utf-8")
    assert (
        "dashboard.dashboard_layout" not in docs
    ), "docs still document dashboard.dashboard_layout as a real setting"


def test_the_dashboard_config_put_still_rejects_an_unknown_field():
    """The positive half of the rail above. Deleting the allowlist ENTRY must not be
    confused with deleting the allowlist: the endpoint's refusal of unknown keys is what
    makes a re-added `dashboard_layout` a 400 rather than a silently ignored write."""
    import asyncio
    import json as _json

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import files as F

    req = make_mocked_request(
        "PUT", "/api/dashboard/config", payload={"dashboard_layout": {"widgets": [], "v": 1}}
    )

    async def _json_body():
        return {"dashboard_layout": {"widgets": [], "v": 1}}

    req.json = _json_body  # type: ignore[method-assign]
    with patch.object(F, "_sel"):
        resp = asyncio.run(F.api_dashboard_config(req))
    assert resp.status == 400
    assert "dashboard_layout" in _json.loads(resp.body.decode())["error"]
