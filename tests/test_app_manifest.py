"""Tests for personalclaw.apps.manifest — AppManifest parser and validator."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw.apps.manifest import (
    AppManifest,
    CliConfig,
    Dependencies,
    MarketplaceDependencies,
    SetupConfig,
    version_tuple,
)

# ---------------------------------------------------------------------------
# version_tuple — the single app-version comparator (APE-7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("1.2.0", "1.0.0"),
        ("2.0.0", "1.9.9"),
        ("1.0.10", "1.0.9"),  # numeric, not lexical
        ("1.1.0", "1.0.5"),
    ],
)
def test_version_tuple_orders_newer_greater(a, b):
    assert version_tuple(a) > version_tuple(b)


def test_version_tuple_equal_versions_compare_equal():
    assert version_tuple("1.2.3") == version_tuple("1.2.3")


def test_version_tuple_tolerates_v_prefix_and_suffix():
    assert version_tuple("v1.2.3") == version_tuple("1.2.3")
    assert version_tuple("1.2.3+build.5") == version_tuple("1.2.3")
    assert version_tuple("1.2.3-rc1") == version_tuple("1.2.3")


def test_version_tuple_bad_value_sorts_lowest():
    # A malformed version must never read as a newer release.
    assert version_tuple("not-a-version") == (0,)
    assert version_tuple("") == (0,)
    assert version_tuple("1.2.0") > version_tuple("garbage")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_manifest(**overrides) -> dict:
    """Return a minimal valid manifest dict with optional overrides."""
    base = {
        "name": "test-app",
        "version": "1.0.0",
        "displayName": "Test App",
        "description": "A test app",
        "author": "tester",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_minimal(self):
        m = AppManifest.from_dict(_valid_manifest())
        assert m.validate() == []

    def test_missing_name(self):
        m = AppManifest.from_dict(_valid_manifest(name=""))
        errors = m.validate()
        assert any("name" in e for e in errors)

    def test_missing_version(self):
        m = AppManifest.from_dict(_valid_manifest(version=""))
        errors = m.validate()
        assert any("version" in e for e in errors)

    def test_missing_display_name(self):
        m = AppManifest.from_dict(_valid_manifest(displayName=""))
        errors = m.validate()
        assert any("displayName" in e for e in errors)

    def test_missing_description(self):
        m = AppManifest.from_dict(_valid_manifest(description=""))
        errors = m.validate()
        assert any("description" in e for e in errors)

    def test_invalid_name_format(self):
        m = AppManifest.from_dict(_valid_manifest(name="Not_Kebab"))
        errors = m.validate()
        assert any("kebab-case" in e for e in errors)

    def test_invalid_version_format(self):
        m = AppManifest.from_dict(_valid_manifest(version="not-semver"))
        errors = m.validate()
        assert any("semver" in e for e in errors)

    def test_legacy_agents_skills_sops_silently_ignored(self):
        m = AppManifest.from_dict(
            _valid_manifest(agents=["../evil.json"], skills=["../../etc"], sops=["x.md"])
        )
        errors = m.validate()
        assert not any("agents" in e or "skills" in e or "sops" in e for e in errors)

    def test_path_traversal_ui_entry(self):
        m = AppManifest.from_dict(
            _valid_manifest(
                ui={"pages": [{"route": "/x", "label": "X", "entryPoint": "../bad.js"}]}
            )
        )
        errors = m.validate()
        assert any("path traversal" in e for e in errors)

    def test_cron_missing_name(self):
        m = AppManifest.from_dict(_valid_manifest(crons=[{"every": 60, "message": "hi"}]))
        errors = m.validate()
        assert any("cron" in e and "name" in e for e in errors)

    def test_cron_missing_schedule(self):
        m = AppManifest.from_dict(_valid_manifest(crons=[{"name": "job1"}]))
        errors = m.validate()
        assert any("every" in e or "cron_expr" in e for e in errors)

    def test_ui_page_missing_route(self):
        m = AppManifest.from_dict(_valid_manifest(ui={"pages": [{"label": "X"}]}))
        errors = m.validate()
        assert any("route" in e for e in errors)

    def test_ui_page_missing_label(self):
        m = AppManifest.from_dict(_valid_manifest(ui={"pages": [{"route": "/x"}]}))
        errors = m.validate()
        assert any("label" in e for e in errors)

    def test_valid_with_all_fields(self):
        m = AppManifest.from_dict(
            {
                "name": "sample-dashboard",
                "version": "0.2.0",
                "displayName": "Sample Dashboard",
                "description": "Example app exercising every manifest field",
                "author": "tester",
                "license": "MIT",
                "minPersonalClawVersion": "1.3.0",
                "mcpServers": {"example-mcp": {"command": "example-mcp", "args": ["serve"]}},
                "crons": [{"name": "refresh", "every": 3600, "message": "refresh data"}],
                "ui": {
                    "pages": [{"route": "/apps/sample", "label": "Dashboard", "icon": "Shield"}]
                },
                "backend": {"entryPoint": "backend/app.py"},
                "permissions": {"mcpTools": ["example_tool"], "storage": True},
                "setup": {"onInstall": "backend/setup.py:on_install"},
                "tags": ["dashboard"],
            }
        )
        assert m.validate() == []
        assert m.name == "sample-dashboard"
        assert len(m.crons) == 1
        assert len(m.ui.pages) == 1
        assert m.permissions.storage is True


# ---------------------------------------------------------------------------
# Serialization round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_minimal_round_trip(self):
        original = _valid_manifest()
        m = AppManifest.from_dict(original)
        serialized = m.to_dict()
        m2 = AppManifest.from_dict(serialized)
        assert m2.to_dict() == serialized

    def test_full_round_trip(self):
        original = {
            "name": "my-app",
            "version": "2.1.0",
            "displayName": "My App",
            "description": "Does things",
            "author": "dev",
            "license": "Apache-2.0",
            "minPersonalClawVersion": "2.0.0",
            "mcpServers": {"srv": {"command": "run"}},
            "crons": [{"name": "j1", "every": 300, "agent": "a", "message": "go"}],
            "ui": {
                "pages": [
                    {
                        "route": "/apps/my-app",
                        "label": "Main",
                        "icon": "Star",
                        "entryPoint": "ui/bundle.js",
                        "mountFunction": "mountMain",
                    }
                ],
                "sidebar": {"section": "Tools", "order": 5},
            },
            "backend": {"entryPoint": "backend/app.py", "port": "9000", "healthCheck": "/ping"},
            "permissions": {
                "mcpTools": ["ToolA"],
                "storage": True,
                "network": True,
                "memory": "shared",
                "cron": True,
            },
            "setup": {
                "onInstall": "setup.py:init",
                "configSchema": {"type": "object", "properties": {"key": {"type": "string"}}},
            },
            "tags": ["dev", "tools"],
        }
        m = AppManifest.from_dict(original)
        serialized = json.loads(m.to_json())
        m2 = AppManifest.from_dict(serialized)
        assert m2.to_dict() == m.to_dict()

    def test_extra_fields_preserved(self):
        data = _valid_manifest(customField="hello", anotherOne=42)
        m = AppManifest.from_dict(data)
        assert m.extra == {"customField": "hello", "anotherOne": 42}
        serialized = m.to_dict()
        assert serialized["customField"] == "hello"
        assert serialized["anotherOne"] == 42
        # Round-trip preserves extra
        m2 = AppManifest.from_dict(serialized)
        assert m2.extra == m.extra


# ---------------------------------------------------------------------------
# CLI seams + loggerRoots (Plan 32 — cli.setup / cli.doctor / loggerRoots)
# ---------------------------------------------------------------------------


class TestCliAndLoggerRoots:
    # --- P1: round-trip for cli + loggerRoots ---
    def test_cli_and_logger_roots_round_trip(self):
        original = _valid_manifest(
            cli={"setup": "cli_setup:run", "doctor": "cli_doctor:probe"},
            loggerRoots=["slack_runtime", "slack_events"],
        )
        m = AppManifest.from_dict(original)
        assert m.cli.setup == "cli_setup:run"
        assert m.cli.doctor == "cli_doctor:probe"
        assert m.loggerRoots == ["slack_runtime", "slack_events"]
        serialized = m.to_dict()
        assert serialized["cli"] == {"setup": "cli_setup:run", "doctor": "cli_doctor:probe"}
        assert serialized["loggerRoots"] == ["slack_runtime", "slack_events"]
        m2 = AppManifest.from_dict(serialized)
        assert m2.to_dict() == serialized

    def test_cli_config_direct_round_trip(self):
        cfg = CliConfig(setup="mod:fn", doctor="d:probe")
        assert CliConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()

    # --- P2: absent fields default empty and are omitted from output ---
    def test_cli_and_logger_roots_default_empty(self):
        m = AppManifest.from_dict(_valid_manifest())
        assert m.cli.setup == ""
        assert m.cli.doctor == ""
        assert m.loggerRoots == []
        serialized = m.to_dict()
        assert "cli" not in serialized
        assert "loggerRoots" not in serialized

    def test_partial_cli_config(self):
        # Only setup declared → doctor stays empty, only setup serialized.
        m = AppManifest.from_dict(_valid_manifest(cli={"setup": "cli_setup:run"}))
        assert m.cli.setup == "cli_setup:run"
        assert m.cli.doctor == ""
        assert m.to_dict()["cli"] == {"setup": "cli_setup:run"}

    def test_cli_non_dict_ignored(self):
        m = AppManifest.from_dict(_valid_manifest(cli="not-a-dict"))
        assert m.cli.setup == ""
        assert m.cli.doctor == ""

    def test_logger_roots_falsy_entries_dropped(self):
        m = AppManifest.from_dict(_valid_manifest(loggerRoots=["ok", "", None, "two"]))
        assert m.loggerRoots == ["ok", "two"]

    # --- P3: unknown-field preservation still works alongside the new fields ---
    def test_unknown_fields_preserved_with_cli(self):
        data = _valid_manifest(
            cli={"setup": "cli_setup:run"},
            loggerRoots=["slack_runtime"],
            futureField={"nested": [1, 2]},
            anotherUnknown="x",
        )
        m = AppManifest.from_dict(data)
        # cli + loggerRoots are typed, NOT in extra
        assert "cli" not in m.extra
        assert "loggerRoots" not in m.extra
        # genuinely-unknown fields land in extra and survive a round-trip
        assert m.extra == {"futureField": {"nested": [1, 2]}, "anotherUnknown": "x"}
        serialized = m.to_dict()
        assert serialized["futureField"] == {"nested": [1, 2]}
        assert serialized["cli"] == {"setup": "cli_setup:run"}
        m2 = AppManifest.from_dict(serialized)
        assert m2.extra == m.extra
        assert m2.to_dict() == serialized

    # --- P4: existing manifests without the new fields still parse cleanly ---
    def test_existing_manifest_still_parses(self):
        m = AppManifest.from_dict(
            _valid_manifest(
                crons=[{"name": "j", "every": 60, "agent": "a", "message": "go"}],
                permissions={"storage": True},
            )
        )
        assert m.validate() == []
        assert m.cli.to_dict() == {}
        assert m.loggerRoots == []


# ---------------------------------------------------------------------------
# Parsing edge cases
# ---------------------------------------------------------------------------


class TestParsing:
    def test_from_empty_dict(self):
        m = AppManifest.from_dict({})
        assert m.name == ""
        assert m.version == ""
        errors = m.validate()
        assert len(errors) >= 4  # all 4 required fields missing

    def test_crons_non_dict_entries_skipped(self):
        m = AppManifest.from_dict(
            _valid_manifest(crons=["not-a-dict", {"name": "ok", "every": 60}])
        )
        assert len(m.crons) == 1
        assert m.crons[0].name == "ok"

    def test_ui_non_dict_ignored(self):
        m = AppManifest.from_dict(_valid_manifest(ui="not-a-dict"))
        assert m.ui.pages == []

    def test_backend_non_dict_ignored(self):
        m = AppManifest.from_dict(_valid_manifest(backend="not-a-dict"))
        assert m.backend.entryPoint == ""

    def test_from_json_file(self, tmp_path):
        data = _valid_manifest()
        p = tmp_path / "app.json"
        p.write_text(json.dumps(data))
        m = AppManifest.from_json_file(p)
        assert m.name == "test-app"
        assert m.validate() == []

    def test_from_json_file_not_object(self, tmp_path):
        p = tmp_path / "app.json"
        p.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="JSON object"):
            AppManifest.from_json_file(p)


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

# Strategy for valid kebab-case names
_kebab_name = st.from_regex(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", fullmatch=True).filter(
    lambda s: 1 <= len(s) <= 60
)

# Strategy for semver strings
_semver = st.tuples(st.integers(0, 99), st.integers(0, 99), st.integers(0, 99)).map(
    lambda t: f"{t[0]}.{t[1]}.{t[2]}"
)

# Strategy for simple JSON-safe extra values
_extra_value = st.one_of(
    st.text(max_size=20),
    st.integers(-1000, 1000),
    st.booleans(),
    st.lists(st.text(max_size=10), max_size=5),
)


class TestPropertyBased:

    @given(
        name=st.one_of(st.just(""), _kebab_name),
        version=st.one_of(st.just(""), _semver),
        display_name=st.one_of(st.just(""), st.text(min_size=1, max_size=30)),
        description=st.one_of(st.just(""), st.text(min_size=1, max_size=50)),
    )
    @settings(max_examples=100)
    def test_validation_detects_missing_required_fields(
        self, name: str, version: str, display_name: str, description: str
    ):
        """Property 1: validate() returns an error for each missing required field."""
        m = AppManifest(
            name=name,
            version=version,
            displayName=display_name,
            description=description,
        )
        errors = m.validate()
        if not name:
            assert any("name" in e for e in errors)
        if not version:
            assert any("version" in e for e in errors)
        if not display_name:
            assert any("displayName" in e for e in errors)
        if not description:
            assert any("description" in e for e in errors)

    @given(
        name=_kebab_name,
        version=_semver,
        display_name=st.text(min_size=1, max_size=30),
        description=st.text(min_size=1, max_size=50),
        extra_keys=st.lists(
            st.text(
                alphabet=st.characters(categories=("L", "N")),
                min_size=1,
                max_size=15,
            ).filter(
                lambda k: k
                not in {
                    "name",
                    "version",
                    "displayName",
                    "description",
                    "author",
                    "license",
                    "minPersonalClawVersion",
                    "agents",
                    "skills",
                    "sops",
                    "mcpServers",
                    "crons",
                    "ui",
                    "backend",
                    "permissions",
                    "setup",
                    "tags",
                }
            ),
            max_size=5,
            unique=True,
        ),
        extra_vals=st.lists(_extra_value, max_size=5),
    )
    @settings(max_examples=100)
    def test_serialization_round_trip(
        self,
        name: str,
        version: str,
        display_name: str,
        description: str,
        extra_keys: list[str],
        extra_vals: list,
    ):
        """Property 2: from_dict(json.loads(to_json())) produces equivalent to_dict()."""
        extra = dict(zip(extra_keys, extra_vals))
        data = {
            "name": name,
            "version": version,
            "displayName": display_name,
            "description": description,
            **extra,
        }
        m1 = AppManifest.from_dict(data)
        serialized = json.loads(m1.to_json())
        m2 = AppManifest.from_dict(serialized)
        assert m2.to_dict() == m1.to_dict()


# ---------------------------------------------------------------------------
# SetupConfig lifecycle hooks tests
# ---------------------------------------------------------------------------


class TestSetupConfigHooks:
    def test_new_hooks_round_trip(self):
        cfg = SetupConfig(
            onInstall="bash setup.sh",
            onUpdate="bash update.sh",
            onUninstall="bash uninstall.sh",
            onEnable="bash enable.sh",
            onDisable="bash disable.sh",
        )
        d = cfg.to_dict()
        assert d["onUpdate"] == "bash update.sh"
        assert d["onEnable"] == "bash enable.sh"
        assert d["onDisable"] == "bash disable.sh"
        restored = SetupConfig.from_dict(d)
        assert restored.onUpdate == cfg.onUpdate
        assert restored.onEnable == cfg.onEnable
        assert restored.onDisable == cfg.onDisable

    def test_empty_hooks_omitted(self):
        cfg = SetupConfig(onInstall="bash setup.sh")
        d = cfg.to_dict()
        assert "onUpdate" not in d
        assert "onEnable" not in d
        assert "onDisable" not in d

    def test_configurable_timeouts(self):
        cfg = SetupConfig(onEnable="bash e.sh", onEnableTimeout=120, onDisableTimeout=60)
        d = cfg.to_dict()
        assert d["onEnableTimeout"] == 120
        assert d["onDisableTimeout"] == 60
        restored = SetupConfig.from_dict(d)
        assert restored.onEnableTimeout == 120
        assert restored.onDisableTimeout == 60

    def test_default_timeouts_omitted(self):
        cfg = SetupConfig(onEnable="bash e.sh")
        d = cfg.to_dict()
        assert "onEnableTimeout" not in d
        assert "onDisableTimeout" not in d

    def test_manifest_with_new_hooks(self):
        m = AppManifest.from_dict(
            _valid_manifest(
                setup={
                    "onInstall": "bash setup.sh",
                    "onUpdate": "bash update.sh",
                    "onEnable": "bash enable.sh",
                    "onDisable": "bash disable.sh",
                    "onEnableTimeout": 90,
                }
            )
        )
        assert m.setup.onUpdate == "bash update.sh"
        assert m.setup.onEnable == "bash enable.sh"
        assert m.setup.onEnableTimeout == 90


# ---------------------------------------------------------------------------
# Dependencies tests
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_empty_dependencies(self):
        deps = Dependencies.from_dict({})
        assert deps.managedBy == "gateway"
        assert deps.marketplace.mcp == []
        assert deps.commands == []

    def test_full_dependencies_round_trip(self):
        data = {
            "managedBy": "app",
            "marketplace": {
                "mcp": ["aws-docs-mcp"],
                "skills": ["SomeSkill"],
                "agents": ["SomeAgent"],
            },
            "commands": ["node", "python3"],
        }
        deps = Dependencies.from_dict(data)
        assert deps.managedBy == "app"
        assert deps.marketplace.mcp == ["aws-docs-mcp"]
        assert deps.commands == ["node", "python3"]
        d = deps.to_dict()
        restored = Dependencies.from_dict(d)
        assert restored.managedBy == deps.managedBy
        assert restored.marketplace.mcp == deps.marketplace.mcp
        assert restored.commands == deps.commands

    def test_default_managed_by_omitted(self):
        deps = Dependencies(marketplace=MarketplaceDependencies(mcp=["x"]))
        d = deps.to_dict()
        assert "managedBy" not in d  # default "gateway" omitted

    def test_mixed_string_and_object_entries(self):
        deps = Dependencies.from_dict(
            {
                "marketplace": {
                    "mcp": [
                        "simple-mcp",
                        {"id": "custom-mcp", "managedBy": "app"},
                    ]
                }
            }
        )
        assert len(deps.marketplace.mcp) == 2
        assert deps.marketplace.mcp[0] == "simple-mcp"
        assert deps.marketplace.mcp[1] == {"id": "custom-mcp", "managedBy": "app"}

    def test_manifest_with_dependencies(self):
        m = AppManifest.from_dict(
            _valid_manifest(
                dependencies={
                    "managedBy": "gateway",
                    "marketplace": {"mcp": ["aws-docs"]},
                    "commands": ["node"],
                }
            )
        )
        assert m.dependencies.managedBy == "gateway"
        assert m.dependencies.marketplace.mcp == ["aws-docs"]
        assert m.dependencies.commands == ["node"]
        # Round-trip through manifest
        d = m.to_dict()
        assert "dependencies" in d
        m2 = AppManifest.from_dict(d)
        assert m2.dependencies.marketplace.mcp == ["aws-docs"]


# ---------------------------------------------------------------------------
# Property tests for new dataclasses
# ---------------------------------------------------------------------------


class TestManifestNewProperties:
    # Feature: app-classification-redesign, Property 3: Manifest 数据类序列化往返一致性
    @given(
        on_install=st.text(max_size=30),
        on_update=st.text(max_size=30),
        on_uninstall=st.text(max_size=30),
        on_enable=st.text(max_size=30),
        on_disable=st.text(max_size=30),
        enable_timeout=st.integers(1, 600),
        disable_timeout=st.integers(1, 600),
    )
    @settings(max_examples=200)
    def test_setup_config_round_trip_property(
        self,
        on_install,
        on_update,
        on_uninstall,
        on_enable,
        on_disable,
        enable_timeout,
        disable_timeout,
    ):
        """**Validates: Requirements 4.2**"""
        cfg = SetupConfig(
            onInstall=on_install,
            onUpdate=on_update,
            onUninstall=on_uninstall,
            onEnable=on_enable,
            onDisable=on_disable,
            onEnableTimeout=enable_timeout,
            onDisableTimeout=disable_timeout,
        )
        d = cfg.to_dict()
        restored = SetupConfig.from_dict(d)
        assert restored.onInstall == cfg.onInstall
        assert restored.onUpdate == cfg.onUpdate
        assert restored.onUninstall == cfg.onUninstall
        assert restored.onEnable == cfg.onEnable
        assert restored.onDisable == cfg.onDisable
        assert restored.onEnableTimeout == cfg.onEnableTimeout
        assert restored.onDisableTimeout == cfg.onDisableTimeout

    # Feature: app-classification-redesign, Property 3: Dependencies 序列化往返一致性
    @given(
        managed_by=st.sampled_from(["gateway", "app"]),
        mcp_deps=st.lists(st.from_regex(r"[a-z][a-z0-9\-]{0,20}", fullmatch=True), max_size=5),
        skill_deps=st.lists(
            st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,20}", fullmatch=True), max_size=5
        ),
        commands=st.lists(st.from_regex(r"[a-z][a-z0-9]{0,10}", fullmatch=True), max_size=5),
    )
    @settings(max_examples=200)
    def test_dependencies_round_trip_property(self, managed_by, mcp_deps, skill_deps, commands):
        """**Validates: Requirements 5.2**"""
        deps = Dependencies(
            managedBy=managed_by,
            marketplace=MarketplaceDependencies(mcp=mcp_deps, skills=skill_deps),
            commands=commands,
        )
        d = deps.to_dict()
        restored = Dependencies.from_dict(d)
        # Semantic equivalence: field values match even if dict structure differs
        assert restored.managedBy == deps.managedBy
        assert restored.marketplace.mcp == deps.marketplace.mcp
        assert restored.marketplace.skills == deps.marketplace.skills
        assert restored.commands == deps.commands

    # Feature: app-classification-redesign, Property 4: 单依赖项 managedBy 覆盖
    @given(
        default_managed=st.sampled_from(["gateway", "app"]),
        override_managed=st.sampled_from(["gateway", "app"]),
    )
    @settings(max_examples=100)
    def test_managed_by_override_property(self, default_managed, override_managed):
        """**Validates: Requirements 5.5**"""
        deps = Dependencies.from_dict(
            {
                "managedBy": default_managed,
                "marketplace": {
                    "mcp": [
                        "simple-dep",
                        {"id": "override-dep", "managedBy": override_managed},
                    ]
                },
            }
        )
        # String entry uses default
        entry0 = deps.marketplace.mcp[0]
        assert isinstance(entry0, str)
        # Object entry preserves its own managedBy
        entry1 = deps.marketplace.mcp[1]
        assert isinstance(entry1, dict)
        assert entry1["managedBy"] == override_managed


class TestProviderConfigEntity:
    """The optional ``entity`` sub-group field on ProviderConfig (action providers
    sub-group by it in Settings → Providers)."""

    def test_entity_round_trips(self):
        from personalclaw.apps.manifest import ProviderConfig

        pc = ProviderConfig(
            type="action",
            implementation="mod:create_provider",
            entity="task",
        )
        d = pc.to_dict()
        assert d["entity"] == "task"
        assert ProviderConfig.from_dict(d).entity == "task"

    def test_entity_omitted_when_empty(self):
        from personalclaw.apps.manifest import ProviderConfig

        pc = ProviderConfig(type="model", implementation="mod:f")
        assert "entity" not in pc.to_dict()
        assert ProviderConfig.from_dict({"type": "model", "implementation": "mod:f"}).entity == ""


def _handler_type_gaps(provider_types: set[str], handlers: set[str]) -> dict[str, list[str]]:
    """Both #47 directions at once, over injectable inputs so each can be proven.

    - ``handler_not_declarable``: a live handler whose type PROVIDER_TYPES omits —
      install-BLOCKED (``ProviderConfig.validate`` rejects the manifest). Loud.
    - ``declarable_no_handler``: a declarable type the runtime registers no handler
      for — installs clean, then does nothing. SILENT, and the direction the guard
      could not see before INU-8.
    """
    return {
        "handler_not_declarable": sorted(handlers - provider_types),
        "declarable_no_handler": sorted(provider_types - handlers),
    }


def _live_handler_types() -> set[str]:
    """The types the runtime ACTUALLY registers, read from the built registry.

    Not scraped from the source with a regex: ``register_type_handler("x", H())``
    is often written across several lines, and a single-line regex silently saw only
    14 of the 18 registrations — under-reporting the very thing this guard measures.
    """
    from personalclaw.providers.registry import get_provider_registry

    # _type_handlers is the registry's internal map; reading it IS the measurement
    # (mirrors tests/test_entity_seam_handlers.py), so the private access is the point.
    return set(get_provider_registry()._type_handlers)


class TestProviderTypesMatchHandlers:
    """#47: PROVIDER_TYPES (the manifest validator's allowlist) MUST equal the set of
    provider types the runtime actually registers a handler for — in BOTH directions.
    A type with a live handler but missing from PROVIDER_TYPES is install-blocked
    (ProviderConfig.validate rejects it) — the split-era #1 'action rejected' class.
    'prompt' regressed this way (PromptTypeHandler existed; PROVIDER_TYPES omitted it).
    The reverse — declarable with no handler — installs clean and then silently does
    nothing; ``inbox`` sat in a weaker form of that state until INU-8 (a handler that
    ran the factory and discarded the instance), which is why this now asserts both
    directions and why every seam-served type must NAME the mechanism serving it."""

    def test_provider_types_equal_registered_handlers(self):
        from personalclaw.apps.manifest import PROVIDER_TYPES

        handlers = _live_handler_types()
        assert handlers, "no type handlers registered — test needs updating"
        gaps = _handler_type_gaps(set(PROVIDER_TYPES), handlers)
        assert not gaps["handler_not_declarable"], (
            f"provider types with a live handler but MISSING from PROVIDER_TYPES "
            f"(install-blocked, #47/#1 class): {gaps['handler_not_declarable']}"
        )
        assert not gaps["declarable_no_handler"], (
            f"provider types declarable in a manifest with NO runtime handler "
            f"(installs clean, then silently dead — the #47 class): "
            f"{gaps['declarable_no_handler']}. Give it a handler, remove it from "
            f"PROVIDER_TYPES, or register an EntitySeamHandler whose source_of_truth "
            f"names the mechanism that really serves it."
        )

    def test_guard_sees_a_declarable_type_with_no_handler(self):
        """The reverse direction must actually be able to fail: a synthetic declarable
        type with no handler is reported (the old one-directional assertion could not
        see this class at all)."""
        gaps = _handler_type_gaps({"model", "ghost_type"}, {"model"})
        assert gaps["declarable_no_handler"] == ["ghost_type"]
        assert gaps["handler_not_declarable"] == []
        # ...and the forward direction still fails on its own class.
        forward = _handler_type_gaps({"model"}, {"model", "orphan_handler"})
        assert forward["handler_not_declarable"] == ["orphan_handler"]

    def test_seam_served_types_name_the_real_mechanism(self):
        """A type served by a no-op EntitySeamHandler is 'allowlisted' only because it
        carries the mechanism that really serves it in ``source_of_truth``. That reason
        lives in CODE at the registration, not in a test-side allowlist that can drift
        from it — so no type is left silently declarable-and-dead."""
        from personalclaw.apps.manifest import PROVIDER_TYPES
        from personalclaw.providers.registry import EntitySeamHandler, get_provider_registry

        handlers = dict(get_provider_registry()._type_handlers)
        seam_types = {
            t
            for t, h in handlers.items()
            if isinstance(h, EntitySeamHandler) and t in PROVIDER_TYPES
        }
        assert seam_types, "expected at least one seam-served type (agent/notification/skills)"
        for t in sorted(seam_types):
            reason = getattr(handlers[t], "source_of_truth", "")
            assert reason and reason.strip(), (
                f"{t!r} is declarable and served by a no-op seam handler with no reason: "
                f"it must name where the entity really lives, or leave PROVIDER_TYPES"
            )

    def test_prompt_provider_manifest_validates(self):
        """Direct regression: a prompt-type provider manifest must pass validation."""
        from personalclaw.apps.manifest import ProviderConfig

        pc = ProviderConfig(type="prompt", implementation="provider:create_provider")
        errors = pc.validate()
        assert not any(
            "provider.type" in e for e in errors
        ), f"prompt provider.type rejected: {errors}"
