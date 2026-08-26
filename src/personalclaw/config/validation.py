"""The JSON-Schema validation pass over raw ``config.json`` data.

Split out of ``loader.py`` because this is machinery, not a config section: it declares no
fields and holds no defaults, it only reads a loaded dict, logs what is wrong with it, and
strips the invalid values so ``AppConfig.load()`` falls back to the shipped default. Keeping
it here means a reader looking for "what happens to a bad value in config.json" has one file
to open instead of a 122-line function buried among sixty dataclasses.

Never raises. A malformed ``config.json`` must degrade field-by-field to defaults, because
the alternative is an instance that cannot start over one typo.
"""

import logging

# A HARD dependency (pyproject `[project] dependencies`), imported plainly. It used to sit
# behind a try/except that set `_HAS_JSONSCHEMA = False`, and `_validate_config_data` returned
# at its first line when that was False — so on any install without the optional [mcp] extra
# the whole validation pass (enums, types, unknown-key warning, retired-field pruning) was a
# no-op that nothing reported.
import jsonschema

logger = logging.getLogger(__name__)


def _lookup_schema_node(schema: dict, dot_path: str) -> dict | None:
    """Walk the JSON Schema tree to find the node for a dot-separated path."""
    parts = dot_path.split(".")
    node = schema
    for part in parts:
        props = node.get("properties", {})
        if part in props:
            node = props[part]
        else:
            return None
    return node


def _is_sensitive_path(schema: dict, dot_path: str) -> bool:
    """Return True if the field at *dot_path* is marked sensitive."""
    node = _lookup_schema_node(schema, dot_path)
    if node is None:
        return False
    return node.get("x-meta", {}).get("sensitive", False)


def _mask_value(value: object, sensitive: bool) -> str:
    """Return a display string for a value, masking if sensitive."""
    if sensitive:
        return '"***"'
    return repr(value)


def _dot_path_from_json_path(path: list) -> str:
    """Convert a jsonschema error path (deque of keys) to a dot-separated string."""
    return ".".join(str(p) for p in path)


def _actual_type_name(value: object) -> str:
    """Return a human-readable type name for a JSON value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _apply_field_default(data: dict, dot_path: str) -> None:
    """Remove the invalid value at *dot_path* so the loader falls back to defaults.

    Only handles top-level and one-level nested paths (e.g. ``agent.provider``).
    """
    parts = dot_path.split(".")
    if len(parts) == 1:
        data.pop(parts[0], None)
    elif len(parts) == 2:
        section = data.get(parts[0])
        if isinstance(section, dict):
            section.pop(parts[1], None)


def _validate_config_data(data: dict) -> dict:
    """Validate *data* against the config JSON Schema.

    Logs warnings for any issues found and mutates *data* in-place to
    remove invalid values (so the loader falls back to field defaults).
    Always returns *data* — never raises.
    """
    # Lazy import to avoid circular import at module level
    from personalclaw.config.schema import JSON_SCHEMA, SCHEMA_REGISTRY

    # 1. Detect unrecognized top-level keys. The SCHEMA_REGISTRY is generated from the
    # AppConfig dataclass, but two legitimate top-level sections are read DIRECTLY off
    # the raw config dict (not modeled as AppConfig fields), so they aren't in the
    # registry — allowlist them or the loader spuriously warns on every load (the config
    # is loaded very frequently → a real log flood):
    #   • providers — the LLM-provider registry (llm/registry, providers/use_cases,
    #     knowledge/embedder, the providers handler all read data["providers"]).
    #   • meta — config-file provenance written by the FS-roundtrip layer
    #     (lastTouchedVersion/lastTouchedAt).
    #   • slack — app-owned opaque data: channel-app config that its
    #     migrate_from_core() lifts into the app store on boot. Core doesn't parse
    #     it (save() preserves it verbatim until the app deletes it). Allowlisted so
    #     the frequently-called loader doesn't log-flood a warning on a
    #     mid-migration config.
    _DIRECT_READ_TOP_KEYS = {"providers", "meta", "slack"}
    # Retired fields (removed from AppConfig with zero consumers). Silently drop
    # them so a pre-removal config.json doesn't warn on every load; the next
    # save() rewrites the file without them (self-heal).
    data.pop("default_memory_store", None)
    if isinstance(data.get("agent"), dict):
        data["agent"].pop("streaming", None)
        # agent.model: the global model is governed by active_models.json
        # (Settings → Models) + per-agent AgentProfile.model — the config-level
        # field was read by nothing.
        data["agent"].pop("model", None)
    if isinstance(data.get("inbox"), dict):
        # quick_reactions: echoed by the status API, rendered nowhere.
        # message_provider: sources are contributed by channel apps now; the
        # native/filesystem fallback chain in inbox_providers is the mechanism.
        data["inbox"].pop("quick_reactions", None)
        data["inbox"].pop("message_provider", None)
    known_top_keys = {e.path for e in SCHEMA_REGISTRY if "." not in e.path and e.path != "*"}
    known_top_keys |= _DIRECT_READ_TOP_KEYS
    unknown = sorted(set(data.keys()) - known_top_keys)
    if unknown:
        logger.warning("Config: unrecognized top-level keys: %s", ", ".join(unknown))

    # 2. Detect deprecated fields and log warnings
    for entry in SCHEMA_REGISTRY:
        if not entry.deprecated:
            continue
        parts = entry.path.split(".")
        # Check if the deprecated key is present in data
        node = data
        found = True
        for p in parts:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                found = False
                break
        if found:
            logger.warning(
                "Config: deprecated field '%s': %s",
                entry.path,
                entry.help,
            )

    # 3. Normalize case-insensitive enum fields before validation
    agent = data.get("agent")
    if isinstance(agent, dict) and isinstance(agent.get("log_level"), str):
        agent["log_level"] = agent["log_level"].upper()

    # 4. Run jsonschema validation
    try:
        jsonschema.validate(data, JSON_SCHEMA)
    except jsonschema.ValidationError:
        # Collect all errors (including nested ones)
        validator_cls = jsonschema.validators.validator_for(JSON_SCHEMA)
        validator = validator_cls(JSON_SCHEMA)
        for err in validator.iter_errors(data):
            dot_path = _dot_path_from_json_path(err.absolute_path)
            if not dot_path:
                # Root-level schema error — skip
                continue

            sensitive = _is_sensitive_path(JSON_SCHEMA, dot_path)
            value = err.instance
            display_val = _mask_value(value, sensitive)

            # Determine error type
            if err.validator == "enum":
                allowed = err.schema.get("enum", [])
                logger.warning(
                    "Config: enum violation at '%s': " "allowed values %s, got %s; using default",
                    dot_path,
                    allowed,
                    display_val,
                )
                _apply_field_default(data, dot_path)
            elif err.validator == "type":
                expected = err.schema.get("type", "unknown")
                actual = _actual_type_name(value)
                logger.warning(
                    "Config: type mismatch at '%s': "
                    "expected %s, got %s (value: %s); using default",
                    dot_path,
                    expected,
                    actual,
                    display_val,
                )
                _apply_field_default(data, dot_path)
            else:
                # Generic validation error
                logger.warning(
                    "Config: validation error at '%s': %s; using default",
                    dot_path,
                    err.message,
                )
                _apply_field_default(data, dot_path)

    return data
