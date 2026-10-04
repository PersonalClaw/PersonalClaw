"""The JSON-Schema validation pass over raw ``config.json`` data.

Split out of ``loader.py`` because this is machinery, not a config section: it declares no
fields and holds no defaults, it only reads a loaded dict, logs what is wrong with it, and
strips the invalid values so ``AppConfig.load()`` falls back to the shipped default. Keeping
it here means a reader looking for "what happens to a bad value in config.json" has one file
to open instead of a 122-line function buried among sixty dataclasses.

Never raises. A malformed ``config.json`` must degrade field-by-field to defaults, because
the alternative is an instance that cannot start over one typo.

**Three things happen here, in this order, and the split matters.**

1. :func:`_normalize_retired` — the deterministic, log-free rewrites. Retired keys are
   dropped, and a retired key that still carries USER INTENT is folded into the field that
   replaced it before anything else reads the dict. This is the one place the repo's
   retired-key vocabulary lives, so a legacy flag is never half-known: known to the loader's
   backfill and unknown to the validator, which is exactly the shape that produced 1144
   ``unrecognized top-level keys: auto_update`` warnings in one browsing session.
2. :func:`_diagnose_and_strip` — the jsonschema pass. Produces the warning text and removes
   invalid values, but logs NOTHING itself, so the decision of *whether to report* belongs
   to the caller.
3. :func:`_validate_config_data` (report every time — direct callers, tests) or
   :func:`validate_config_data_cached` (report once per distinct file content — the loader).
"""

import hashlib
import logging
from typing import NamedTuple

# A HARD dependency (pyproject `[project] dependencies`), imported plainly. It used to sit
# behind a try/except that set `_HAS_JSONSCHEMA = False`, and `_validate_config_data` returned
# at its first line when that was False — so on any install without the optional [mcp] extra
# the whole validation pass (enums, types, unknown-key warning, retired-field pruning) was a
# no-op that nothing reported.
import jsonschema

from personalclaw.safety_flags import yes_or_no

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


#: A field's place in ``config.json``: the keys from the top, kept apart rather than joined with
#: dots, because a key may hold a dot of its own (an agent's or a server's name).
FieldPath = tuple[str | int, ...]


def _holder(data: dict, path: FieldPath) -> tuple[dict, str] | None:
    """The object holding the value at *path*, and its key; ``None`` when a step on the way is not
    an object, or the value sits in a list."""
    if not path:
        return None
    *steps, key = path
    node: object = data
    for step in steps:
        if isinstance(node, dict):
            node = node.get(step)
        elif isinstance(node, list) and isinstance(step, int) and 0 <= step < len(node):
            node = node[step]
        else:
            return None
    if isinstance(node, dict) and isinstance(key, str):
        return node, key
    return None


def _apply_field_default(data: dict, path: FieldPath) -> None:
    """Remove the invalid value at *path* so the loader falls back to defaults.

    Only handles top-level and one-level nested paths (e.g. ``agent.provider``). A value further
    down is left for its section's own loader, which may refuse it: ``guardrails.budgets`` raises
    on a ceiling it cannot read, so the budget authorises nothing, where the default (``0``) would
    be a ceiling nobody chose. A boolean is repaired at any depth (:func:`_repair_boolean`).
    """
    if len(path) > 2:
        return
    found = _holder(data, path)
    if found is not None:
        found[0].pop(found[1], None)


def _repair_boolean(data: dict, path: FieldPath) -> None:
    """Repair a boolean at *path*, however deep, written as something else: text that spells
    false (``"false"``, ``"no"``, ``"off"``, ``"0"``) reads as ``False``, and anything else is
    removed, so the loader falls back to its default.

    At any depth, because a section's loader read what was left with ``bool()``:
    ``security.egress.allow_private: "false"`` loaded as ``True``, letting the egress guard reach
    private addresses, while the warning said "using default". One direction only: a hand-edited
    ``"false"`` meant off, and the default could switch back on what the owner switched off. Text
    that spells true is removed instead, so text never turns a setting on.
    """
    found = _holder(data, path)
    if found is None:
        return
    holder, key = found
    if yes_or_no(holder.get(key)) is False:
        holder[key] = False
    else:
        holder.pop(key, None)


#: Top-level sections read DIRECTLY off the raw config dict rather than modeled as
#: AppConfig fields, so they are absent from the dataclass-generated SCHEMA_REGISTRY.
#: Allowlisted or the loader spuriously warns on every load (the config is loaded very
#: frequently → a real log flood):
#:   • providers — the LLM-provider registry (llm/registry, providers/use_cases,
#:     knowledge/embedder, the providers handler all read data["providers"]).
#:   • meta — config-file provenance written by the FS-roundtrip layer
#:     (lastTouchedVersion/lastTouchedAt).
#:   • slack — app-owned opaque data: channel-app config that its migrate_from_core()
#:     lifts into the app store on boot. Core doesn't parse it (save() preserves it
#:     verbatim until the app deletes it). Allowlisted so the frequently-called loader
#:     doesn't log-flood a warning on a mid-migration config.
_DIRECT_READ_TOP_KEYS = frozenset({"providers", "meta", "slack"})


def _fold_legacy_update_flags(data: dict) -> None:
    """Fold the two retired update flags into the ``updates`` block that replaced them.

    ``auto_update`` (top level) and ``dashboard.update_dev_mode`` were the pre-RUM-1
    unattended-update and track-main opt-ins. RUM-5 retired both in favour of
    ``updates.auto`` / ``updates.channel``, and the backfill that honours an old home's
    intent lived in ``AppConfig.load_with_migration_state`` — reading the raw keys there
    while the validator, one call earlier, had never heard of them. So a home carrying
    ``auto_update`` was simultaneously migrated correctly and reported as containing an
    unrecognized top-level key, on every single load: 1144 of the ~2000 lines in the
    owner's 2026-09-23 gateway.log were that one warning.

    Folding here instead fixes both halves with one mechanism. The keys are consumed and
    REMOVED, so nothing downstream sees an unknown key, the warning cannot be emitted, and
    the next ``save()`` rewrites ``config.json`` without them (self-heal) — rather than the
    key being allowlisted into permanence. Reviving ``auto_update`` as a dataclass field was
    the other option and is the wrong one: ``updates.auto`` already carries the full config
    round-trip (dataclass + ``_meta`` + ``load`` + ``to_dict`` + ``_EDITABLE_CONFIG`` + the
    Settings → Updates control), so a second field for the same setting would be exactly the
    dual path the tenets forbid.

    **An explicit ``updates`` field always wins** (the RUM-1 rule): a legacy flag only maps
    in when the block does not declare the field itself. A home that never wrote either flag
    is untouched and lands on the dataclass default — ``auto="off"``, notify-only, which is
    C7's "least accident risk".

    One deliberate edge: when ``updates`` exists but is not an object there is no field to
    fold into, so the legacy keys are consumed without being honoured and the block is left
    for :func:`_diagnose_and_strip` to report and strip. The config then takes the safe
    defaults. Honouring intent read out of a block that is already unreadable is not worth
    suppressing the type warning that says so.
    """
    # Only a yes carries the intent over: either flag on turns on something the owner did not
    # choose in the new block (an unattended update, the nightly channel), and `bool("false")` is
    # True, so a hand-written "false" migrated as on.
    legacy_auto = yes_or_no(data.pop("auto_update", None)) is True
    dashboard = data.get("dashboard")
    legacy_nightly = (
        yes_or_no(dashboard.pop("update_dev_mode", None)) is True
        if isinstance(dashboard, dict)
        else False
    )
    if not (legacy_auto or legacy_nightly):
        return

    updates = data.get("updates")
    if updates is None:
        updates = {}
        data["updates"] = updates
    elif not isinstance(updates, dict):
        return

    if legacy_auto and "auto" not in updates:
        # A legacy unattended-update user stops riding raw `main` and rides the resolved
        # stable release tag — hence "staged", channel "stable".
        updates["auto"] = "staged"
    if legacy_nightly and "channel" not in updates:
        updates["channel"] = "nightly"


def _normalize_retired(data: dict) -> None:
    """Drop retired keys and fold the ones that still carry intent. Logs nothing.

    Deterministic and idempotent, which is what lets :func:`validate_config_data_cached`
    run it on every load while skipping the jsonschema pass — the rewrites here are too
    cheap to cache and must never be skipped, because a later reader would then see a key
    this build does not model.
    """
    # Retired fields (removed from AppConfig with zero consumers). Silently drop
    # them so a pre-removal config.json doesn't warn on every load; the next
    # save() rewrites the file without them (self-heal).
    data.pop("default_memory_store", None)
    # inbound: the single-surface section, replaced OUTRIGHT by
    # `external_access` (a clean break with no `inbound` back-read — see
    # `config/external_access.py`). Nothing has read it since, yet an aged home still
    # carries it and so does the shipped `six-month-home` fixture, which is how this was
    # With `auto_update` consumed, the fixture still logged
    # `unrecognized top-level keys: inbound` on every load. Same bucket, same flood.
    data.pop("inbound", None)
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
    if isinstance(data.get("security"), dict):
        # mcp_read_only_servers: the owner's trust in an MCP server's read-only labels is kept
        # with a digest of each tool she saw (`mcp_read_only_trust`). A name in this list says
        # nothing of which tools those were, so it is not carried over: the server's tools ask
        # until she trusts its labels again on the Tools page.
        data["security"].pop("mcp_read_only_servers", None)
    # Retired fields that still carry USER INTENT — consumed, not merely dropped.
    _fold_legacy_update_flags(data)

    # Normalize case-insensitive enum fields before validation.
    agent = data.get("agent")
    if isinstance(agent, dict) and isinstance(agent.get("log_level"), str):
        agent["log_level"] = agent["log_level"].upper()


def _diagnose_and_strip(data: dict) -> tuple[list[str], "_Repairs"]:
    """Every problem with *data*, as ready-to-log lines, plus the repairs made for them.

    Removes invalid values in-place (so the loader falls back to field defaults), repairs a
    boolean written as something else (text that spells false reads as ``False``), and returns
    the paths of both, so the same repairs can be re-applied later without re-running
    jsonschema. Logs nothing, never raises.
    """
    # Lazy import to avoid circular import at module level
    from personalclaw.config.schema import JSON_SCHEMA, SCHEMA_REGISTRY

    messages: list[str] = []
    stripped: list[FieldPath] = []
    booleans: list[FieldPath] = []

    # 1. Detect unrecognized top-level keys.
    known_top_keys = {e.path for e in SCHEMA_REGISTRY if "." not in e.path and e.path != "*"}
    known_top_keys |= _DIRECT_READ_TOP_KEYS
    unknown = sorted(set(data.keys()) - known_top_keys)
    if unknown:
        messages.append(f"Config: unrecognized top-level keys: {', '.join(unknown)}")

    # 2. Detect deprecated fields
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
            messages.append(f"Config: deprecated field '{entry.path}': {entry.help}")

    # 3. Run jsonschema validation
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
            path: FieldPath = tuple(err.absolute_path)
            boolean = err.validator == "type" and _expects_boolean(err.schema)

            # Determine error type
            if boolean and yes_or_no(value) is False:
                messages.append(
                    f"Config: '{dot_path}' is the text {display_val}, read as false; write it "
                    "unquoted (true or false)"
                )
            elif err.validator == "enum":
                allowed = err.schema.get("enum", [])
                messages.append(
                    f"Config: enum violation at '{dot_path}': "
                    f"allowed values {allowed}, got {display_val}; using default"
                )
            elif err.validator == "type":
                expected = err.schema.get("type", "unknown")
                actual = _actual_type_name(value)
                messages.append(
                    f"Config: type mismatch at '{dot_path}': expected {expected}, "
                    f"got {actual} (value: {display_val}); using default"
                )
            else:
                messages.append(
                    f"Config: validation error at '{dot_path}': {err.message}; using default"
                )
            if boolean:
                _repair_boolean(data, path)
                booleans.append(path)
            else:
                _apply_field_default(data, path)
                stripped.append(path)

    return messages, _Repairs(tuple(stripped), tuple(booleans))


def _expects_boolean(schema: dict) -> bool:
    kind = schema.get("type")
    return "boolean" in (kind if isinstance(kind, list) else [kind])


class _Repairs(NamedTuple):
    """What the jsonschema pass changed, by path: the values it stripped and the booleans it
    repaired. Paths only, never values, so a cache of them can never hold a config secret."""

    stripped: tuple[FieldPath, ...]
    booleans: tuple[FieldPath, ...]

    def apply(self, data: dict) -> None:
        for path in self.stripped:
            _apply_field_default(data, path)
        for path in self.booleans:
            _repair_boolean(data, path)


def _validate_config_data(data: dict) -> dict:
    """Validate *data* against the config JSON Schema.

    Logs warnings for any issues found and mutates *data* in-place to
    remove invalid values (so the loader falls back to field defaults).
    Always returns *data* — never raises.
    """
    _normalize_retired(data)
    for message in _diagnose_and_strip(data)[0]:
        logger.warning("%s", message)
    return data


#: Fingerprint -> the repairs the jsonschema pass made for that content. ONE entry:
#: ``config.json`` has one current content, and a miss costs a full (correct) pass, so a
#: larger cache would only help a process juggling several homes — which is tests, where a
#: miss is free. Values are field PATHS, never field values, so this can never become a
#: place config secrets accumulate.
_STRIP_MEMO: dict[str, _Repairs] = {}


def validate_config_data_cached(data: dict, fingerprint: str) -> dict:
    """:func:`_validate_config_data`, reported and re-validated once per *fingerprint*.

    ``AppConfig.load()`` is a pure read called from ~300 sites, so it re-parsed and
    re-validated ``config.json`` on essentially every request. Two costs, both measured on
    the owner's 2026-09-23 session: jsonschema ran over the whole sixty-section schema on
    the hot path, and a single legitimate warning was emitted 1144 times in one browsing
    window — 1144 of ~2000 log lines, which is how eight ``FileNotFoundError`` tracebacks in
    the same window went unnoticed. A warning repeated a thousand times is not a louder
    warning; it is a quieter log.

    *fingerprint* identifies the file CONTENT (the loader passes a digest of the raw text).
    On a hit the deterministic rewrites still run and the previously-computed strips are
    re-applied, so the returned dict is identical to what the full pass produces — only
    jsonschema and the logging are skipped. Any write to ``config.json`` changes the
    fingerprint, so the next load re-validates and re-reports: the cache is invalidated by
    the write itself, with nothing to remember to call.
    """
    _normalize_retired(data)
    cached = _STRIP_MEMO.get(fingerprint)
    if cached is None:
        messages, repairs = _diagnose_and_strip(data)
        for message in messages:
            logger.warning("%s", message)
        _STRIP_MEMO.clear()
        _STRIP_MEMO[fingerprint] = repairs
        return data
    cached.apply(data)
    return data


def config_fingerprint(raw: str) -> str:
    """A content fingerprint for :func:`validate_config_data_cached`.

    A digest rather than the text itself so the memo key cannot carry config values.
    """
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
