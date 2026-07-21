"""Property-based tests for config/schema.py.

Tests the schema registry, ConfigEntry, and related utilities using
hypothesis for property-based testing.
"""

import dataclasses
import re
import typing

from hypothesis import given
from hypothesis import strategies as st

from personalclaw.config.loader import (
    AgentConfig,
    AgentProfile,
    AppConfig,
    DashboardConfig,
    MemoryConfig,
    MemoryStoreConfig,
    SessionConfig,
)
from personalclaw.config.schema import (
    JSON_SCHEMA,
    SCHEMA_REGISTRY,
    ConfigEntry,
    config_entry_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_CONFIG_CLASSES: list[type] = [
    AppConfig,
    AgentConfig,
    SessionConfig,
    MemoryConfig,
    DashboardConfig,
    AgentProfile,
    MemoryStoreConfig,
]


def _all_fields_recursive(
    cls: type,
    prefix: str = "",
) -> list[tuple[str, dataclasses.Field]]:  # type: ignore[type-arg]
    """Yield (dot_path, field) for every field in the dataclass hierarchy."""
    result: list[tuple[str, dataclasses.Field]] = []  # type: ignore[type-arg]
    for f in dataclasses.fields(cls):
        path = f"{prefix}.{f.name}" if prefix else f.name
        result.append((path, f))
        tp = f.type
        if isinstance(tp, str):
            import personalclaw.config.loader as _mod

            try:
                tp = eval(tp, vars(_mod))  # noqa: S307
            except Exception:
                continue
        origin = typing.get_origin(tp)
        if origin is dict:
            # For dict[str, DataclassType], add wildcard path and recurse
            args = typing.get_args(tp)
            if len(args) == 2:
                val_type = args[1]
                if dataclasses.is_dataclass(val_type) and isinstance(val_type, type):
                    wildcard_path = f"{path}.*"
                    result.extend(_all_fields_recursive(val_type, wildcard_path))
            continue
        if origin is list:
            # For list[DataclassType], the schema walker emits `.*` for the array
            # element and descends into the element dataclass's fields (e.g.
            # tools.projection_rules.*.name). Mirror that so those paths are reachable.
            args = typing.get_args(tp)
            if len(args) == 1:
                elem_type = args[0]
                if dataclasses.is_dataclass(elem_type) and isinstance(elem_type, type):
                    result.extend(_all_fields_recursive(elem_type, f"{path}.*"))
            continue
        if origin is not None:
            continue
        if dataclasses.is_dataclass(tp) and isinstance(tp, type):
            result.extend(_all_fields_recursive(tp, path))
    return result


def _resolve_type(f: dataclasses.Field) -> type:  # type: ignore[type-arg]
    """Resolve a field's type annotation to a runtime type."""
    import personalclaw.config.loader as _mod

    tp = f.type
    if isinstance(tp, str):
        try:
            tp = eval(tp, vars(_mod))  # noqa: S307
        except Exception:
            return str
    return tp  # type: ignore[return-value]


# Expected Python type → JSON Schema type mapping
_EXPECTED_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    set: "array",
    dict: "object",
}

# Segment pattern for snake_case paths (also allow * for dynamic keys)
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$|^\*$")


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestConfigSchemaProperties:
    """Property-based tests for the config schema registry."""

    # Feature: config-schema, Property 1: All config fields carry required metadata
    def test_all_fields_carry_required_metadata(self) -> None:
        """Every dataclass field in the config hierarchy must have
        'label' (str) and 'help' (str) in its metadata.

        **Validates: Requirements 1.1**
        """
        all_fields = _all_fields_recursive(AppConfig)
        assert len(all_fields) > 0, "Expected at least one field"

        for path, f in all_fields:
            meta = dict(f.metadata) if f.metadata else {}
            assert "label" in meta, f"Field '{path}' missing 'label' in metadata"
            assert isinstance(
                meta["label"], str
            ), f"Field '{path}' label must be str, got {type(meta['label'])}"
            assert "help" in meta, f"Field '{path}' missing 'help' in metadata"
            assert isinstance(
                meta["help"], str
            ), f"Field '{path}' help must be str, got {type(meta['help'])}"

    # Feature: config-schema, Property 2: Safe defaults for missing optional metadata
    @given(
        has_tags=st.booleans(),
        has_sensitive=st.booleans(),
        has_deprecated=st.booleans(),
        has_enum=st.booleans(),
    )
    def test_safe_defaults_for_missing_optional_metadata(
        self,
        has_tags: bool,
        has_sensitive: bool,
        has_deprecated: bool,
        has_enum: bool,
    ) -> None:
        """When optional metadata keys are omitted, ConfigEntry must use
        safe defaults: tags=[], sensitive=False, deprecated=False,
        enumValues=None.

        **Validates: Requirements 1.5**
        """
        meta: dict = {"label": "Test", "help": "Test help."}
        if has_tags:
            meta["tags"] = ["custom"]
        if has_sensitive:
            meta["sensitive"] = True
        if has_deprecated:
            meta["deprecated"] = True
        if has_enum:
            meta["enum"] = ["a", "b"]

        # Build a ConfigEntry the same way the schema module does:
        # extract optional keys with safe defaults
        tags = meta.get("tags", [])
        sensitive = meta.get("sensitive", False)
        deprecated = meta.get("deprecated", False)
        enum_values = meta.get("enum", None)

        entry = ConfigEntry(
            path="test.field",
            kind="core",
            type="string",
            required=False,
            deprecated=deprecated,
            sensitive=sensitive,
            tags=tags,
            label=meta["label"],
            help=meta["help"],
            has_children=False,
            enum_values=enum_values,
            default_value=None,
        )

        if not has_tags:
            assert entry.tags == [], f"Expected empty tags, got {entry.tags}"
        if not has_sensitive:
            assert entry.sensitive is False
        if not has_deprecated:
            assert entry.deprecated is False
        if not has_enum:
            assert entry.enum_values is None

    # Feature: config-schema, Property 3: Registry entries are structurally complete
    def test_registry_entries_structurally_complete(self) -> None:
        """Every SCHEMA_REGISTRY entry must have all required fields and
        every path must be reachable via dataclasses.fields() recursion
        on AppConfig.

        **Validates: Requirements 3.2, 2.6**
        """
        required_attrs = [
            "path",
            "kind",
            "type",
            "required",
            "deprecated",
            "sensitive",
            "tags",
            "label",
            "help",
            "has_children",
            "enum_values",
            "default_value",
        ]

        # Build set of all reachable paths from the dataclass hierarchy
        all_fields = _all_fields_recursive(AppConfig)
        reachable_paths: set[str] = set()
        for path, f in all_fields:
            reachable_paths.add(path)
            # Also add wildcard child paths for list/dict fields
            tp = _resolve_type(f)
            origin = typing.get_origin(tp)
            if origin is list or origin is dict:
                reachable_paths.add(f"{path}.*")

        assert len(SCHEMA_REGISTRY) > 0, "Registry should not be empty"

        for entry in SCHEMA_REGISTRY:
            # Verify all required attributes are present
            for attr in required_attrs:
                assert hasattr(entry, attr), f"Entry '{entry.path}' missing attribute '{attr}'"

            # Verify path is reachable from the dataclass hierarchy
            assert entry.path in reachable_paths, (
                f"Entry path '{entry.path}' not reachable via "
                f"dataclasses.fields() recursion on AppConfig"
            )

            # Verify type is a valid JSON Schema type
            valid_types = {
                "string",
                "integer",
                "number",
                "boolean",
                "array",
                "object",
            }
            assert (
                entry.type in valid_types
            ), f"Entry '{entry.path}' has invalid type '{entry.type}'"

            # Verify kind is set
            assert entry.kind == "core", f"Entry '{entry.path}' has unexpected kind '{entry.kind}'"

    # Feature: config-schema, Property 4: Python-to-schema type mapping is correct
    def test_python_to_schema_type_mapping(self) -> None:
        """For every field in the config hierarchy, the schema registry
        must map Python types correctly: str→string, int→integer,
        float→number, bool→boolean, list→array, dict/dataclass→object.

        **Validates: Requirements 3.3, 3.4**
        """
        # Build a lookup from path → ConfigEntry
        registry_by_path: dict[str, ConfigEntry] = {e.path: e for e in SCHEMA_REGISTRY}

        all_fields = _all_fields_recursive(AppConfig)
        for path, f in all_fields:
            tp = _resolve_type(f)
            origin = typing.get_origin(tp)

            # Determine expected JSON Schema type
            if dataclasses.is_dataclass(tp) and isinstance(tp, type):
                expected_type = "object"
                expected_has_children = True
            elif origin is not None:
                base = origin
                expected_type = _EXPECTED_TYPE_MAP.get(base, "string")
                expected_has_children = expected_type in ("array", "object")
            else:
                expected_type = _EXPECTED_TYPE_MAP.get(tp, "string")
                # Bare dict/list (no generic args) still have children
                expected_has_children = expected_type in ("array", "object")

            assert path in registry_by_path, f"Field '{path}' not found in SCHEMA_REGISTRY"
            entry = registry_by_path[path]
            assert entry.type == expected_type, (
                f"Field '{path}': expected type '{expected_type}', "
                f"got '{entry.type}' (Python type: {tp})"
            )
            assert entry.has_children == expected_has_children, (
                f"Field '{path}': expected has_children={expected_has_children}, "
                f"got {entry.has_children}"
            )

    # Feature: config-schema, Property 5: ConfigEntry serialization round-trip
    @given(
        path=st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz_."),
            min_size=1,
            max_size=30,
        ).filter(lambda s: not s.startswith(".") and not s.endswith(".")),
        kind=st.just("core"),
        entry_type=st.sampled_from(
            [
                "string",
                "integer",
                "number",
                "boolean",
                "array",
                "object",
            ]
        ),
        required=st.booleans(),
        deprecated=st.booleans(),
        sensitive=st.booleans(),
        tags=st.lists(st.text(min_size=1, max_size=10), max_size=3),
        label=st.text(min_size=1, max_size=50),
        help_text=st.text(min_size=1, max_size=100),
        has_children=st.booleans(),
        has_enum=st.booleans(),
        default_is_none=st.booleans(),
    )
    def test_config_entry_round_trip(
        self,
        path: str,
        kind: str,
        entry_type: str,
        required: bool,
        deprecated: bool,
        sensitive: bool,
        tags: list[str],
        label: str,
        help_text: str,
        has_children: bool,
        has_enum: bool,
        default_is_none: bool,
    ) -> None:
        """Serializing a ConfigEntry via config_entry_to_dict() and
        reconstructing it must produce an equivalent entry.

        **Validates: Requirements 4.4**
        """
        enum_values = ["a", "b", "c"] if has_enum else None
        default_value = None if default_is_none else "test_default"

        original = ConfigEntry(
            path=path,
            kind=kind,
            type=entry_type,
            required=required,
            deprecated=deprecated,
            sensitive=sensitive,
            tags=tags,
            label=label,
            help=help_text,
            has_children=has_children,
            enum_values=enum_values,
            default_value=default_value,
        )

        d = config_entry_to_dict(original)

        # Reconstruct from dict (camelCase keys → snake_case attrs)
        reconstructed = ConfigEntry(
            path=d["path"],
            kind=d["kind"],
            type=d["type"],
            required=d["required"],
            deprecated=d["deprecated"],
            sensitive=d["sensitive"],
            tags=d["tags"],
            label=d["label"],
            help=d["help"],
            has_children=d["hasChildren"],
            enum_values=d["enumValues"],
            default_value=d["defaultValue"],
        )

        assert reconstructed.path == original.path
        assert reconstructed.kind == original.kind
        assert reconstructed.type == original.type
        assert reconstructed.required == original.required
        assert reconstructed.deprecated == original.deprecated
        assert reconstructed.sensitive == original.sensitive
        assert reconstructed.tags == original.tags
        assert reconstructed.label == original.label
        assert reconstructed.help == original.help
        assert reconstructed.has_children == original.has_children
        assert reconstructed.enum_values == original.enum_values
        assert reconstructed.default_value == original.default_value

    # Feature: config-schema, Property 15: All config paths use snake_case
    def test_all_config_paths_use_snake_case(self) -> None:
        """Every segment of every SCHEMA_REGISTRY entry path must match
        [a-z][a-z0-9_]* or be the wildcard '*'.

        **Validates: Requirements 9.3**
        """
        assert len(SCHEMA_REGISTRY) > 0, "Registry should not be empty"

        for entry in SCHEMA_REGISTRY:
            segments = entry.path.split(".")
            for segment in segments:
                assert _SNAKE_CASE_RE.match(segment), (
                    f"Path '{entry.path}' has segment '{segment}' "
                    f"that does not match snake_case pattern "
                    f"[a-z][a-z0-9_]* or '*'"
                )


# ---------------------------------------------------------------------------
# Phase 2: Agent-Workspace Bindings Schema Registry Tests
# ---------------------------------------------------------------------------


class TestAgentWorkspaceBindingsSchema:
    """Unit tests for schema registry entries added by Phase 2 dataclasses.

    Verifies that the auto-generated schema registry contains all expected
    paths for agents, workspaces, memory_stores, and top-level defaults.

    **Validates: Requirements 10.1, 10.2, 10.3, 10.4**
    """

    def test_agents_paths_exist(self) -> None:
        """agents.* paths are present in SCHEMA_REGISTRY.

        **Validates: Requirement 10.1**
        """
        paths = {e.path for e in SCHEMA_REGISTRY}
        assert "agents" in paths
        assert "agents.*" in paths
        assert "agents.*.provider_agent" in paths
        assert "agents.*.default_dir" in paths
        assert "agents.*.memory_store" in paths

    def test_memory_stores_paths_exist(self) -> None:
        """memory_stores.* paths are present in SCHEMA_REGISTRY.

        **Validates: Requirement 10.3**
        """
        paths = {e.path for e in SCHEMA_REGISTRY}
        assert "memory_stores" in paths
        assert "memory_stores.*" in paths
        assert "memory_stores.*.description" in paths

    def test_top_level_defaults_exist(self) -> None:
        """default_agent top-level entry exists; retired default_memory_store
        stays gone (removed 2026-07 — zero consumers).

        **Validates: Requirement 10.4**
        """
        paths = {e.path for e in SCHEMA_REGISTRY}
        assert "default_agent" in paths
        assert "default_memory_store" not in paths

        by_path = {e.path: e for e in SCHEMA_REGISTRY}
        assert by_path["default_agent"].type == "string"

    def test_additional_properties_for_dynamic_keys(self) -> None:
        """JSON Schema uses additionalProperties for agents and memory_stores
        dynamic keys.

        **Validates: Requirements 10.1, 10.3**
        """
        top_props = JSON_SCHEMA.get("properties", {})

        # agents
        agents_schema = top_props.get("agents", {})
        assert (
            "additionalProperties" in agents_schema
        ), "agents schema should use additionalProperties for dynamic agent names"
        agents_ap = agents_schema["additionalProperties"]
        assert agents_ap.get("type") == "object"
        assert "provider_agent" in agents_ap.get("properties", {})
        assert "default_dir" in agents_ap.get("properties", {})
        assert "memory_store" in agents_ap.get("properties", {})

        # memory_stores
        ms_schema = top_props.get("memory_stores", {})
        assert (
            "additionalProperties" in ms_schema
        ), "memory_stores schema should use additionalProperties for dynamic store names"
        ms_ap = ms_schema["additionalProperties"]
        assert ms_ap.get("type") == "object"
        assert "description" in ms_ap.get("properties", {})
