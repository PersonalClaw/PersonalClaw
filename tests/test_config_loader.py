"""Property-based and unit tests for config/loader.py.

Tests the AppConfig loader validation logic using hypothesis for
property-based testing.
"""

import json
import logging
import tempfile
import unittest.mock
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from personalclaw.config.loader import (
    _HAS_JSONSCHEMA,
    AgentConfig,
    AgentProfile,
    AppConfig,
    DashboardConfig,
    MemoryConfig,
    MemoryStoreConfig,
    ResolvedBindings,
    InboxConfig,
    SessionConfig,
    resolve_agent_bindings,
    resolve_memory_store_config,
)

# Logger used by the loader module — needed for capturing warnings in tests
logger = logging.getLogger("personalclaw.config.loader")

# ---------------------------------------------------------------------------
# Helpers / Strategies
# ---------------------------------------------------------------------------

# Fields with enum constraints and their allowed values
# agent.provider is intentionally NOT here — it accepts an open ``acp:<cli>``
# space (any connected ACP runtime), so it carries no closed enum constraint.
_ENUM_FIELDS: list[tuple[str, str, list[str]]] = [
    ("agent", "approval_mode", ["auto", "interactive", "trust_reads"]),
    ("agent", "sandbox", ["auto", "off"]),
    ("agent", "log_level", ["DEBUG", "INFO", "WARNING", "ERROR"]),
]

# Top-level keys recognised by the schema
_KNOWN_TOP_KEYS = {
    "agent",
    "session",
    "memory",
    "slack",
    "dashboard",
    "hooks",
    "agents",
    "default_agent",
    "memory_stores",
    "auto_update",
    # direct-read top-level sections the loader allowlists (not AppConfig fields) —
    # the unrecognized-keys property must NOT generate these (they don't warn).
    "providers",
    "meta",
}

# Skip marker for tests that require jsonschema validation
_requires_jsonschema = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema not available — validation tests require it",
)


def _load_from_dict(data: object) -> AppConfig:
    """Write *data* to a temp config file and load via AppConfig.load()."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
    ) as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)
        tmp = Path(f.name)

    try:
        with unittest.mock.patch(
            "personalclaw.config.loader.config_path",
            return_value=tmp,
        ):
            return AppConfig.load()
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".json.bak").unlink(missing_ok=True)


def _load_from_raw_string(content: str) -> AppConfig:
    """Write raw string content to a temp file and load."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        tmp = Path(f.name)

    try:
        with unittest.mock.patch(
            "personalclaw.config.loader.config_path",
            return_value=tmp,
        ):
            return AppConfig.load()
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".json.bak").unlink(missing_ok=True)


def _load_from_dict_with_logs(data: object) -> tuple[AppConfig, list[str]]:
    """Load config and capture warning log messages."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
    ) as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f)
        tmp = Path(f.name)

    try:
        with unittest.mock.patch(
            "personalclaw.config.loader.config_path",
            return_value=tmp,
        ):
            logger_local = logging.getLogger("personalclaw.config.loader")
            messages: list[str] = []
            original_warning = logger_local.warning

            def capture_warning(msg: object, *args: object) -> None:
                try:
                    messages.append(str(msg) % args)
                except Exception:
                    messages.append(str(msg))
                original_warning(msg, *args)

            with unittest.mock.patch.object(logger_local, "warning", capture_warning):
                result = AppConfig.load()
            return result, messages
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".json.bak").unlink(missing_ok=True)


def _default_config() -> AppConfig:
    """Return a default AppConfig for comparison."""
    return AppConfig()


# Hypothesis strategy for safe identifier strings (no control chars, JSON-safe)
_safe_name_st = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-"),
    min_size=1,
    max_size=15,
)

# Strategy for AgentProfile instances
_agent_profile_st = st.builds(
    AgentProfile,
    provider_agent=st.text(min_size=0, max_size=20),
    default_dir=st.text(min_size=0, max_size=30),
    memory_store=_safe_name_st,
)

# Strategy for MemoryStoreConfig instances
_memory_store_config_st = st.builds(
    MemoryStoreConfig,
    description=st.text(min_size=0, max_size=30),
)

# Hypothesis strategy for generating valid AgentConfig instances
_agent_config_st = st.builds(
    AgentConfig,
    approval_mode=st.sampled_from(["auto", "interactive", "trust_reads"]),
    # NOTE: bare "acp" is a LEGACY global default that load() migrates to
    # "native" (clean break — native is the default runtime; ACP is opt-in per
    # agent). Generate only non-migrated runtime values so the round-trip tests
    # serialization, not the one-shot legacy migration (covered separately).
    provider=st.sampled_from(["native", "acp:claude-code", "acp:test-cli"]),
    sandbox=st.sampled_from(["auto", "off"]),
    soft_stop_budget_secs=st.floats(min_value=0.5, max_value=60.0),
)

_session_config_st = st.builds(
    SessionConfig,
    timeout_secs=st.integers(min_value=60, max_value=7200),
)

_memory_config_st = st.builds(
    MemoryConfig,
    semantic_confidence_threshold=st.floats(min_value=0.0, max_value=1.0),
    episodic_dedup_threshold=st.floats(min_value=0.0, max_value=1.0),
    episodic_max_results=st.integers(min_value=1, max_value=50),
    episodic_max_count=st.integers(min_value=100, max_value=50000),
    semantic_keys=st.just([]),
    history_idle_hours=st.floats(min_value=0.5, max_value=24.0),
    history_max_days=st.integers(min_value=1, max_value=365),
    migrated=st.booleans(),
)

_dashboard_config_st = st.builds(
    DashboardConfig,
    url=st.text(min_size=0, max_size=50),
)

_inbox_config_st = st.builds(
    InboxConfig,
    enabled=st.booleans(),
    user_id=st.text(min_size=0, max_size=20),
    watched_channels=st.lists(st.text(min_size=1, max_size=15), max_size=3),
    poll_interval_seconds=st.integers(min_value=30, max_value=600),
    style_rules=st.lists(st.text(min_size=1, max_size=30), max_size=3),
    test_mode=st.booleans(),
)

_personalclaw_config_st = st.builds(
    AppConfig,
    agent=_agent_config_st,
    session=_session_config_st,
    memory=_memory_config_st,
    dashboard=_dashboard_config_st,
    inbox=_inbox_config_st,
    hooks=st.just({}),
    agents=st.dictionaries(
        keys=_safe_name_st,
        values=_agent_profile_st,
        min_size=0,
        max_size=3,
    ),
    default_agent=st.one_of(st.just(""), _safe_name_st),
    memory_stores=st.dictionaries(
        keys=_safe_name_st,
        values=_memory_store_config_st,
        min_size=0,
        max_size=3,
    ),
    auto_update=st.booleans(),
)


# ---------------------------------------------------------------------------
# Property Tests: core config load/validation
# ---------------------------------------------------------------------------


class TestConfigLoaderProperties:
    """Property-based tests for the config loader validation logic."""

    # Feature: config-schema, Property 6: AppConfig load/to_dict round-trip
    @given(config=_personalclaw_config_st)
    @settings(deadline=None)
    def test_load_to_dict_round_trip(
        self,
        config: AppConfig,
    ) -> None:
        """Calling to_dict() then load() from that dict must yield an
        equivalent AppConfig instance.

        **Validates: Requirements 2.4, 2.5, 9.4, 9.6**
        """
        d = config.to_dict()
        loaded = _load_from_dict(d)

        # Compare agent fields
        assert loaded.agent.approval_mode == config.agent.approval_mode
        assert loaded.agent.provider == config.agent.provider
        assert loaded.agent.sandbox == config.agent.sandbox

        # Compare session
        assert loaded.session.timeout_secs == config.session.timeout_secs

        # Compare memory fields
        assert loaded.memory.migrated == config.memory.migrated
        assert loaded.memory.episodic_max_results == config.memory.episodic_max_results
        assert loaded.memory.episodic_max_count == config.memory.episodic_max_count
        assert loaded.memory.history_max_days == config.memory.history_max_days

        # Compare dashboard
        assert loaded.dashboard.url == config.dashboard.url

        # Compare inbox
        assert loaded.inbox.enabled == config.inbox.enabled
        assert loaded.inbox.user_id == config.inbox.user_id
        assert loaded.inbox.watched_channels == config.inbox.watched_channels
        assert loaded.inbox.poll_interval_seconds == config.inbox.poll_interval_seconds
        assert loaded.inbox.style_rules == config.inbox.style_rules
        assert loaded.inbox.test_mode == config.inbox.test_mode

        # Compare top-level fields
        assert loaded.hooks == config.hooks
        assert loaded.auto_update == config.auto_update

    # Feature: config-schema, Property 9: Type mismatch falls back to default
    @_requires_jsonschema
    @given(
        field_idx=st.integers(min_value=0, max_value=2),
        wrong_idx=st.integers(min_value=0, max_value=3),
    )
    @settings(deadline=None)
    def test_type_mismatch_falls_back_to_default(
        self,
        field_idx: int,
        wrong_idx: int,
    ) -> None:
        """When a config value has an incorrect type, load() must fall
        back to the field's default value.

        **Validates: Requirements 6.1, 6.2**
        """
        fields = [
            ("agent", "approval_mode", "string"),
            ("agent", "yolo", "boolean"),
            ("session", "timeout_secs", "integer"),
        ]
        wrong_values = [
            42,  # wrong for string/boolean
            "not_a_num",  # wrong for integer/boolean
            True,  # wrong for string/integer
            [1, 2, 3],  # wrong for all scalar types
        ]

        section, key, expected_type = fields[field_idx]
        wrong_value = wrong_values[wrong_idx]

        # Skip cases where the wrong_value accidentally has the right type
        type_map = {"string": str, "boolean": bool, "integer": int}
        expected_py = type_map[expected_type]
        if expected_type == "integer":
            assume(not isinstance(wrong_value, int) or isinstance(wrong_value, bool))
        elif expected_type == "boolean":
            assume(not isinstance(wrong_value, bool))
        else:
            assume(not isinstance(wrong_value, expected_py))

        data: dict = {section: {key: wrong_value}}
        loaded = _load_from_dict(data)
        defaults = _default_config()

        loaded_section = getattr(loaded, section)
        default_section = getattr(defaults, section)
        assert getattr(loaded_section, key) == getattr(
            default_section, key
        ), f"Expected default for {section}.{key} after type mismatch"

    # Feature: config-schema, Property 10: Enum violation falls back to default
    @_requires_jsonschema
    @given(
        field_idx=st.integers(min_value=0, max_value=len(_ENUM_FIELDS) - 1),
        bad_value=st.text(min_size=1, max_size=20),
    )
    @settings(deadline=None)
    def test_enum_violation_falls_back_to_default(
        self,
        field_idx: int,
        bad_value: str,
    ) -> None:
        """When a config key has an enum constraint and the value is not
        in the allowed set, load() must fall back to the field's default.

        **Validates: Requirements 6.3**
        """
        section, key, allowed = _ENUM_FIELDS[field_idx]
        assume(bad_value not in allowed)

        data: dict = {section: {key: bad_value}}
        loaded = _load_from_dict(data)
        defaults = _default_config()

        loaded_section = getattr(loaded, section)
        default_section = getattr(defaults, section)
        assert getattr(loaded_section, key) == getattr(default_section, key), (
            f"Expected default for {section}.{key} after enum violation "
            f"(value={bad_value!r}, allowed={allowed})"
        )

    # Feature: config-schema, Property 11: Unrecognized keys are detected
    @_requires_jsonschema
    @given(
        extra_keys=st.lists(
            st.text(
                alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_"),
                min_size=2,
                max_size=15,
            ).filter(lambda k: k not in _KNOWN_TOP_KEYS),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(deadline=None)
    def test_unrecognized_keys_detected(
        self,
        extra_keys: list[str],
    ) -> None:
        """When config.json contains unrecognized top-level keys,
        load() must detect and warn about them.

        **Validates: Requirements 6.4**
        """
        data: dict = {k: "some_value" for k in extra_keys}
        _, messages = _load_from_dict_with_logs(data)

        unrecognized_msgs = [m for m in messages if "unrecognized top-level keys" in m]
        assert len(unrecognized_msgs) > 0, (
            f"Expected warning about unrecognized keys {extra_keys}, " f"got messages: {messages}"
        )

        warning_text = unrecognized_msgs[0]
        for k in extra_keys:
            assert k in warning_text, f"Key '{k}' not mentioned in warning: {warning_text}"

    @_requires_jsonschema
    def test_direct_read_sections_meta_providers_not_flagged(self) -> None:
        """`providers` (LLM-provider registry) and `meta` (FS-roundtrip provenance) are
        legitimate top-level sections read DIRECTLY off the raw config — not AppConfig
        fields, so absent from SCHEMA_REGISTRY. The loader must allowlist them or it
        spuriously warns on every load (the config is loaded very frequently → a log
        flood). A genuinely bogus key must still be flagged (the diagnostic still works)."""
        data = {
            "meta": {"lastTouchedVersion": "0.1.0", "lastTouchedAt": "2026-06-25T00:00:00Z"},
            "providers": [{"name": "Bedrock", "type": "bedrock", "options": {"region": "us-west-2"}}],
            "definitely_bogus_key": 1,
        }
        _, messages = _load_from_dict_with_logs(data)
        unrecognized = [m for m in messages if "unrecognized top-level keys" in m]
        joined = " ".join(unrecognized)
        assert "meta" not in joined and "providers" not in joined, (
            f"meta/providers must not be flagged: {unrecognized}"
        )
        assert "definitely_bogus_key" in joined, (
            f"a genuinely unknown key must still be flagged: {messages}"
        )

    # Feature: config-schema, Property 12: load() always returns valid AppConfig
    @given(
        content=st.one_of(
            st.text(min_size=0, max_size=200),
            st.just(""),
            st.just("null"),
            st.just("[]"),
            st.just("42"),
            st.just("{"),
            st.just('{"agent": "not_an_object"}'),
        ),
    )
    @settings(deadline=None)
    def test_load_always_returns_valid_config(
        self,
        content: str,
    ) -> None:
        """For any input content, load() must return a AppConfig
        instance without raising an exception.

        **Validates: Requirements 6.6**
        """
        result = _load_from_raw_string(content)

        assert isinstance(result, AppConfig)
        assert isinstance(result.agent, AgentConfig)
        assert isinstance(result.session, SessionConfig)
        assert isinstance(result.memory, MemoryConfig)
        assert isinstance(result.dashboard, DashboardConfig)
        assert isinstance(result.hooks, dict)
        assert isinstance(result.auto_update, bool)

    # Feature: config-schema, Property 14: Deprecated fields are accepted during loading
    @_requires_jsonschema
    @given(
        command_val=st.text(min_size=1, max_size=20),
    )
    @settings(deadline=None)
    def test_deprecated_fields_accepted_during_loading(
        self,
        command_val: str,
    ) -> None:
        """When a field is marked deprecated, load() must still accept
        and apply the provided value (not fall back to default).

        Since there are currently no deprecated fields in the config,
        this test temporarily marks ``dashboard.url`` as deprecated and
        verifies the value is still loaded.

        **Validates: Requirements 8.2**
        """
        from personalclaw.config import schema as schema_mod

        # Find and temporarily mark dashboard.url as deprecated
        target_entry = None
        for entry in schema_mod.SCHEMA_REGISTRY:
            if entry.path == "dashboard.url":
                target_entry = entry
                break
        assert target_entry is not None, "dashboard.url not in SCHEMA_REGISTRY"

        original_deprecated = target_entry.deprecated
        # Also patch JSON Schema x-meta
        slack_props = (
            schema_mod.JSON_SCHEMA.get("properties", {})
            .get("dashboard", {})
            .get("properties", {})
            .get("url", {})
        )
        original_xmeta_dep = slack_props.get("x-meta", {}).get("deprecated", False)

        try:
            object.__setattr__(target_entry, "deprecated", True)
            if "x-meta" in slack_props:
                slack_props["x-meta"]["deprecated"] = True

            data: dict = {"dashboard": {"url": command_val}}
            loaded = _load_from_dict(data)

            assert loaded.dashboard.url == command_val, (
                f"Expected deprecated field dashboard.url={command_val!r}, "
                f"got {loaded.dashboard.url!r}"
            )
        finally:
            object.__setattr__(target_entry, "deprecated", original_deprecated)
            if "x-meta" in slack_props:
                slack_props["x-meta"]["deprecated"] = original_xmeta_dep


# ---------------------------------------------------------------------------
# Agent bindings & resolver property tests
# ---------------------------------------------------------------------------


class TestAgentBindingsProperties:
    """Property-based tests for AgentProfile metadata and the resolver."""

    # Property: New dataclass metadata completeness
    @given(
        cls_idx=st.integers(min_value=0, max_value=1),
    )
    @settings(deadline=None)
    def test_new_dataclass_metadata_completeness(
        self,
        cls_idx: int,
    ) -> None:
        """All fields of AgentProfile and MemoryStoreConfig carry required
        metadata (label, help).

        **Validates: Requirements 1.1, 5.1**
        """
        import dataclasses

        classes = [AgentProfile, MemoryStoreConfig]
        cls = classes[cls_idx]

        fields = dataclasses.fields(cls)
        assert len(fields) > 0, f"{cls.__name__} has no fields"

        for f in fields:
            meta = dict(f.metadata) if f.metadata else {}
            assert "label" in meta, f"{cls.__name__}.{f.name} missing 'label' in metadata"
            assert isinstance(meta["label"], str), f"{cls.__name__}.{f.name} label must be str"
            assert len(meta["label"]) > 0, f"{cls.__name__}.{f.name} label must not be empty"
            assert "help" in meta, f"{cls.__name__}.{f.name} missing 'help' in metadata"
            assert isinstance(meta["help"], str), f"{cls.__name__}.{f.name} help must be str"
            assert len(meta["help"]) > 0, f"{cls.__name__}.{f.name} help must not be empty"

    # Property: Config serialization round-trip for agents/memory_stores
    @given(config=_personalclaw_config_st)
    @settings(deadline=None)
    def test_config_serialization_round_trip(
        self,
        config: AppConfig,
    ) -> None:
        """For any valid AppConfig with agents/stores, to_dict() → load()
        produces an equivalent instance.

        **Validates: Requirements 9.4, 11.5**
        """
        d = config.to_dict()
        loaded = _load_from_dict(d)

        # Compare agents — load() may add a default native agent if none exist
        if config.agents:
            for name in config.agents:
                assert name in loaded.agents
                assert loaded.agents[name].provider_agent == config.agents[name].provider_agent
                assert loaded.agents[name].default_dir == config.agents[name].default_dir
                assert loaded.agents[name].memory_store == config.agents[name].memory_store
        else:
            # Empty agents → load() seeds a default native agent
            assert len(loaded.agents) >= 1

        # default_agent always names a real agent after load()
        assert loaded.default_agent in loaded.agents

        # Compare memory_stores
        if config.memory_stores:
            assert set(loaded.memory_stores.keys()) == set(config.memory_stores.keys())
            for name in config.memory_stores:
                assert (
                    loaded.memory_stores[name].description == config.memory_stores[name].description
                )
        else:
            # Empty memory_stores → default entry synthesized
            assert "default" in loaded.memory_stores

        # Compare core fields still round-trip
        assert loaded.agent.approval_mode == config.agent.approval_mode
        assert loaded.agent.provider == config.agent.provider
        assert loaded.session.timeout_secs == config.session.timeout_secs
        assert loaded.auto_update == config.auto_update

    # Property: Serialization format correctness
    @given(config=_personalclaw_config_st)
    @settings(deadline=None)
    def test_serialization_format_correctness(
        self,
        config: AppConfig,
    ) -> None:
        """For any config, to_dict() output has agents as dict-of-dicts and
        memory_stores as dict-of-dicts.

        **Validates: Requirements 11.1, 11.3, 11.4**
        """
        d = config.to_dict()

        # agents is a dict of dicts with expected keys
        assert isinstance(d["agents"], dict)
        for name, agent_dict in d["agents"].items():
            assert isinstance(agent_dict, dict)
            assert "provider_agent" in agent_dict
            assert "default_dir" in agent_dict
            assert "memory_store" in agent_dict

        # memory_stores is a dict of dicts with expected keys
        assert isinstance(d["memory_stores"], dict)
        for name, ms_dict in d["memory_stores"].items():
            assert isinstance(ms_dict, dict)
            assert "description" in ms_dict

        # default_agent is present; retired default_memory_store stays gone
        assert "default_agent" in d
        assert isinstance(d["default_agent"], str)
        assert "default_memory_store" not in d

    # Property: Memory store merge correctness
    @given(
        top_level=st.fixed_dictionaries(
            {},
            optional={
                "semantic_confidence_threshold": st.floats(min_value=0.0, max_value=1.0),
                "episodic_dedup_threshold": st.floats(min_value=0.0, max_value=1.0),
                "episodic_max_results": st.integers(min_value=1, max_value=50),
                "history_max_days": st.integers(min_value=1, max_value=365),
                "migrated": st.booleans(),
            },
        ),
        store_overrides=st.fixed_dictionaries(
            {},
            optional={
                "description": st.text(min_size=0, max_size=30),
                "episodic_max_results": st.one_of(st.just(None), st.integers(min_value=1, max_value=50)),
                "history_max_days": st.one_of(st.just(None), st.integers(min_value=1, max_value=365)),
            },
        ),
    )
    @settings(deadline=None)
    def test_memory_store_merge_correctness(
        self,
        top_level: dict,
        store_overrides: dict,
    ) -> None:
        """For any top-level memory dict and partial store override dict,
        resolve_memory_store_config produces a merged dict where
        store-level values override and unspecified fields inherit from
        top-level.

        **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
        """
        merged = resolve_memory_store_config(top_level, store_overrides)

        # Unspecified fields inherit from top-level
        for key, value in top_level.items():
            if key not in store_overrides:
                assert merged[key] == value, (
                    f"Key '{key}' should inherit from top-level "
                    f"(expected {value!r}, got {merged.get(key)!r})"
                )

        # Explicit non-empty, non-None store values override
        for key, value in store_overrides.items():
            if key == "description":
                # description is store-only metadata, must not appear in merged
                assert key not in merged or merged.get(key) == top_level.get(
                    key
                ), "'description' should be skipped during merge"
                continue
            if value != "" and value is not None:
                assert merged[key] == value, (
                    f"Key '{key}' should be overridden by store "
                    f"(expected {value!r}, got {merged.get(key)!r})"
                )

        # Empty string and None values do not override
        for key, value in store_overrides.items():
            if key == "description":
                continue
            if value == "" or value is None:
                if key in top_level:
                    assert merged[key] == top_level[key], (
                        f"Key '{key}' with empty/None value should inherit from top-level "
                        f"(expected {top_level[key]!r}, got {merged.get(key)!r})"
                    )

        # Original top_level dict must not be mutated
        assert merged is not top_level

    # Property: Resolver returns correct bindings for the named agent
    @given(
        agent_name=_safe_name_st,
        store_name=_safe_name_st,
        provider_agent_name=st.text(min_size=1, max_size=20),
        agent_dir=st.text(min_size=1, max_size=30),
        store_desc=st.text(min_size=0, max_size=20),
    )
    @settings(deadline=None)
    def test_resolver_correct_bindings(
        self,
        agent_name: str,
        store_name: str,
        provider_agent_name: str,
        agent_dir: str,
        store_desc: str,
    ) -> None:
        """For configs with a valid agent, resolve_agent_bindings returns the
        agent's working dir, memory store name, and provider agent.

        **Validates: Requirements 7.1, 7.2, 7.5**
        """
        config = AppConfig(
            agents={
                agent_name: AgentProfile(
                    provider_agent=provider_agent_name,
                    default_dir=agent_dir,
                    memory_store=store_name,
                ),
            },
            default_agent=agent_name,
            memory_stores={
                store_name: MemoryStoreConfig(
                    description=store_desc,
                )
            },
        )

        # Resolve via explicit agent_name
        result = resolve_agent_bindings(config, agent_name=agent_name)
        assert isinstance(result, ResolvedBindings)
        assert result.workspace_dir == Path(agent_dir)
        assert result.memory_store_name == store_name
        assert result.provider_agent == provider_agent_name

        # Resolve via default_agent (no explicit agent_name)
        result2 = resolve_agent_bindings(config)
        assert result2.workspace_dir == Path(agent_dir)
        assert result2.memory_store_name == store_name
        assert result2.provider_agent == provider_agent_name

    # Property: Resolver falls back on a missing memory store reference
    @given(
        agent_name=_safe_name_st,
        missing_store=_safe_name_st,
        fallback_store_name=_safe_name_st,
        agent_dir=st.text(min_size=1, max_size=30),
    )
    @settings(deadline=None)
    def test_resolver_fallback_on_missing_store(
        self,
        agent_name: str,
        missing_store: str,
        fallback_store_name: str,
        agent_dir: str,
    ) -> None:
        """When an agent references a non-existent memory store, the resolver
        falls back to the filesystem store (empty store name).

        **Validates: Requirements 7.3, 7.4, 2.3**
        """
        assume(missing_store != fallback_store_name)

        config = AppConfig(
            agents={
                agent_name: AgentProfile(
                    provider_agent="some-agent",
                    default_dir=agent_dir,
                    memory_store=missing_store,
                ),
            },
            default_agent=agent_name,
            memory_stores={fallback_store_name: MemoryStoreConfig()},
        )

        result = resolve_agent_bindings(config, agent_name=agent_name)

        # Agent's own working dir is honoured
        assert result.workspace_dir == Path(agent_dir)
        # Missing store falls back to filesystem (empty name)
        assert result.memory_store_name == ""

    # Property: Agents parsing accepts duplicate provider_agent values
    @given(
        agents_data=st.dictionaries(
            keys=_safe_name_st,
            values=st.fixed_dictionaries(
                {
                    "provider_agent": st.sampled_from(["personalclaw", "oncall-agent", "custom", ""]),
                    "default_dir": st.text(min_size=0, max_size=15),
                    "memory_store": _safe_name_st,
                },
            ),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(deadline=None)
    def test_agents_parsing_with_duplicate_provider_agent_values(
        self,
        agents_data: dict[str, dict[str, str]],
    ) -> None:
        """For any agents dict with optional duplicate provider_agent values,
        load() parses all entries without error.

        **Validates: Requirements 1.3, 1.7**
        """
        raw_config: dict = {"agents": agents_data}
        cfg = _load_from_dict(raw_config)

        # All agent entries must be parsed (load() may add a default too)
        for name, raw_entry in agents_data.items():
            parsed = cfg.agents[name]
            assert isinstance(parsed, AgentProfile)
            assert parsed.provider_agent == raw_entry["provider_agent"]
            assert parsed.default_dir == raw_entry["default_dir"]
            assert parsed.memory_store == raw_entry["memory_store"]


# ---------------------------------------------------------------------------
# Resolver / default-agent unit tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Unit tests for resolver edge cases."""

    def test_empty_config_seeds_a_default_agent(self) -> None:
        """Empty config → load() seeds a default native agent and points
        default_agent at it."""
        cfg = _load_from_dict({})

        assert len(cfg.agents) >= 1
        assert cfg.default_agent in cfg.agents

        result = resolve_agent_bindings(cfg)
        assert isinstance(result, ResolvedBindings)
        assert isinstance(result.workspace_dir, Path)

    def test_missing_memory_stores_synthesizes_default(self) -> None:
        """Missing memory_stores section synthesizes a default store."""
        raw_config: dict = {
            "memory": {"episodic_max_results": 8},
        }
        cfg = _load_from_dict(raw_config)

        assert "default" in cfg.memory_stores
        assert isinstance(cfg.memory_stores["default"], MemoryStoreConfig)

    def test_retired_system_agent_pruned_and_persisted(self, tmp_path: Path) -> None:
        """A retired system agent left in an existing config.json is pruned on load
        (backend-cleanup §4) AND the prune is persisted via write-back — a genuine
        one-time migration, not a load-time-only mask. A user-created agent and the
        reserved system agents are untouched."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "agents": {
                "PersonalClaw": {"provider": "native"},
                "my-helper": {"provider": "native"},          # user agent — keep
                "personalclaw-autonomous": {"provider": "native"},  # retired — prune
            },
            "default_agent": "PersonalClaw",
        }))
        with unittest.mock.patch(
            "personalclaw.config.loader.config_dir", return_value=tmp_path
        ):
            cfg = AppConfig.load()

        # In-memory: retired gone, user + default kept, reserved seeded.
        assert "personalclaw-autonomous" not in cfg.agents
        assert "my-helper" in cfg.agents
        assert "PersonalClaw" in cfg.agents

        # Persisted: the retired key is gone from disk (write-back ran), not just
        # filtered in memory — reloading a fresh config must not resurrect it.
        on_disk = json.loads(cfg_file.read_text())
        assert "personalclaw-autonomous" not in on_disk["agents"]
        assert "my-helper" in on_disk["agents"]

    def test_resolver_with_empty_agent_name_uses_default(self) -> None:
        """Resolver with empty/None agent name uses default_agent."""
        config = AppConfig(
            agents={
                "mydefault": AgentProfile(
                    provider_agent="personalclaw",
                    default_dir="ws-dir",
                    memory_store="default",
                ),
            },
            default_agent="mydefault",
            memory_stores={"default": MemoryStoreConfig()},
        )

        result = resolve_agent_bindings(config, agent_name="")
        assert result.provider_agent == "personalclaw"
        assert result.workspace_dir == Path("ws-dir")

        result2 = resolve_agent_bindings(config, agent_name=None)
        assert result2.provider_agent == "personalclaw"
        assert result2.workspace_dir == Path("ws-dir")

    def test_non_existent_agent_name_resolves_via_default(self) -> None:
        """An agent name not in config.agents resolves via default_agent."""
        config = AppConfig(
            agents={
                "real": AgentProfile(
                    provider_agent="personalclaw",
                    default_dir="ws-dir",
                    memory_store="default",
                ),
            },
            default_agent="real",
            memory_stores={"default": MemoryStoreConfig()},
        )

        result_unknown = resolve_agent_bindings(config, agent_name="ghost")
        result_default = resolve_agent_bindings(config, agent_name="real")

        assert result_unknown.workspace_dir == result_default.workspace_dir
        assert result_unknown.memory_store_name == result_default.memory_store_name
        assert result_unknown.provider_agent == result_default.provider_agent
        assert result_unknown.effective_memory_config == result_default.effective_memory_config


class TestResourceIndependence:
    """MemoryStoreConfig must not carry agent/workspace fields."""

    def test_memory_store_config_has_no_agent_or_workspace_fields(self) -> None:
        """MemoryStoreConfig has no workspace/agent fields.

        **Validates: Requirements 5.6**
        """
        import dataclasses

        ms_fields = {f.name for f in dataclasses.fields(MemoryStoreConfig)}
        forbidden = {
            "workspace",
            "default_dir",
            "default_workspace",
            "provider_agent",
            "agent",
            "agents",
        }
        overlap = ms_fields & forbidden
        assert not overlap, f"MemoryStoreConfig has agent/workspace fields: {overlap}"


# ---------------------------------------------------------------------------
# Persistent log_level config field
# ---------------------------------------------------------------------------


class TestPersistentLogLevel:
    """Tests for the persistent log_level config field."""

    def test_default_log_level_is_warning(self) -> None:
        """When no log_level is specified, default is WARNING."""
        cfg = _load_from_dict({})
        assert cfg.agent.log_level == "WARNING"

    def test_log_level_loaded_from_config(self) -> None:
        """log_level is read from agent section."""
        cfg = _load_from_dict({"agent": {"log_level": "DEBUG"}})
        assert cfg.agent.log_level == "DEBUG"

    def test_log_level_case_insensitive(self) -> None:
        """log_level is uppercased on load."""
        cfg = _load_from_dict({"agent": {"log_level": "info"}})
        assert cfg.agent.log_level == "INFO"

    def test_log_level_round_trips_through_to_dict(self) -> None:
        """log_level survives save/load round-trip."""
        cfg = _load_from_dict({"agent": {"log_level": "ERROR"}})
        d = cfg.to_dict()
        assert d["agent"]["log_level"] == "ERROR"


class TestMemoryConfigBehaviorFlags:
    """Memory behavior flags must round-trip on load (regression: the explicit
    MemoryConfig(...) mapping dropped these, so saved toggles never took effect
    and always read their dataclass defaults)."""

    def test_proactive_commitments_loads_from_config(self) -> None:
        cfg = _load_from_dict({"memory": {"proactive_commitments": True}})
        assert cfg.memory.proactive_commitments is True

    def test_proactive_commitments_defaults_off(self) -> None:
        assert _load_from_dict({}).memory.proactive_commitments is False

    def test_l1_manifest_can_be_disabled(self) -> None:
        # l1_manifest defaults True; a saved False must actually load as False.
        cfg = _load_from_dict({"memory": {"l1_manifest": False}})
        assert cfg.memory.l1_manifest is False

    def test_active_recall_can_be_disabled(self) -> None:
        cfg = _load_from_dict({"memory": {"active_recall": False}})
        assert cfg.memory.active_recall is False

    def test_proactive_max_per_day_loads(self) -> None:
        cfg = _load_from_dict({"memory": {"proactive_commitments_max_per_day": 7}})
        assert cfg.memory.proactive_commitments_max_per_day == 7

    def test_auto_promote_flags_load(self) -> None:
        cfg = _load_from_dict({"memory": {"auto_promote_enabled": False, "auto_promote_every_n": 9}})
        assert cfg.memory.auto_promote_enabled is False
        assert cfg.memory.auto_promote_every_n == 9


# ---------------------------------------------------------------------------
# Soft-stop config field
# ---------------------------------------------------------------------------


class TestSoftStopBudget:
    """Tests for agent.soft_stop_budget_secs config field."""

    def test_soft_stop_budget_default(self) -> None:
        """Default AgentConfig has soft_stop_budget_secs == 10.0."""
        cfg = AgentConfig()
        assert cfg.soft_stop_budget_secs == 10.0

    def test_soft_stop_budget_valid_range(self) -> None:
        """AgentConfig accepts soft_stop_budget_secs within [0.5, 60.0]."""
        cfg = AgentConfig(soft_stop_budget_secs=10.0)
        assert cfg.soft_stop_budget_secs == 10.0

    def test_soft_stop_budget_too_low(self, caplog) -> None:
        """AgentConfig clamps soft_stop_budget_secs below 0.5 to 0.5 with a warning."""
        with caplog.at_level(logging.WARNING, logger="personalclaw.config.loader"):
            cfg = AgentConfig(soft_stop_budget_secs=0.1)
        assert cfg.soft_stop_budget_secs == 0.5
        assert "out of range" in caplog.text

    def test_soft_stop_budget_too_high(self, caplog) -> None:
        """AgentConfig clamps soft_stop_budget_secs above 60.0 to 60.0 with a warning."""
        with caplog.at_level(logging.WARNING, logger="personalclaw.config.loader"):
            cfg = AgentConfig(soft_stop_budget_secs=120.0)
        assert cfg.soft_stop_budget_secs == 60.0
        assert "out of range" in caplog.text

    def test_soft_stop_budget_appears_in_schema(self) -> None:
        """Generated config baseline includes soft_stop_budget_secs."""
        from personalclaw.config.schema import SCHEMA_REGISTRY

        paths = [e.path for e in SCHEMA_REGISTRY]
        assert "agent.soft_stop_budget_secs" in paths

        entry = next(e for e in SCHEMA_REGISTRY if e.path == "agent.soft_stop_budget_secs")
        assert entry.type == "number"
        assert entry.default_value == 10.0


class TestDashboardMcpProbeTimeout:
    """Tests for the dashboard.mcp_probe_timeout_secs config field."""

    def test_dashboard_mcp_probe_timeout_default(self) -> None:
        """DashboardConfig defaults mcp_probe_timeout_secs to 15."""
        cfg = DashboardConfig()
        assert cfg.mcp_probe_timeout_secs == 15

    def test_dashboard_mcp_probe_timeout_from_json(self) -> None:
        """Loading config with mcp_probe_timeout_secs reads the value."""
        content = json.dumps({"dashboard": {"mcp_probe_timeout_secs": 30}})
        cfg = _load_from_raw_string(content)
        assert cfg.dashboard.mcp_probe_timeout_secs == 30

    def test_dashboard_mcp_probe_timeout_invalid_falls_back(self) -> None:
        """Non-int mcp_probe_timeout_secs falls back to default 15."""
        content = json.dumps({"dashboard": {"mcp_probe_timeout_secs": "fast"}})
        cfg = _load_from_raw_string(content)
        assert cfg.dashboard.mcp_probe_timeout_secs == 15
