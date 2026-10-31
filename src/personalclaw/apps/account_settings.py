"""An app's settings in two parts: the account every capability shares, and each capability's own
(#3842).

An app's **account settings** (a token, a server, a region) are entered once and read by every
capability; a **capability's settings** are read by that capability alone. A manifest declares the
first in a top-level ``settings`` schema and the second in each provider's ``settingsSchema``, and
:func:`settings_refusals` is ``AppManifest.validate``'s part for them.

The account part is stored where an app's settings always were, ``data/config.json``, and a
capability's own in ``data/capabilities/<id>.json`` (``providers/settings.py``). A manifest that
declares no ``settings`` keeps today's meaning: every setting it stores is shared, so all of it is
the account's, and an older core reads that file exactly as it always did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.apps.manifest import AppManifest


def _properties(schema: Any) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    return props if isinstance(props, dict) else {}


def account_schema(manifest: AppManifest) -> dict[str, Any]:
    """The schema of what every capability shares: ``settings``, else ``setup.configSchema``, which
    it replaces (kept until the v0.4.0 cut, #5665)."""
    return manifest.settings or manifest.setup.configSchema or {}


def capability_schemas(manifest: AppManifest) -> dict[str, dict[str, Any]]:
    """Each capability's own settings schema, by its id; none when the ids do not resolve."""
    try:
        ids = manifest.capability_ids()
    except ValueError:
        return {}
    return {cid: (p.settingsSchema or {}) for cid, p in zip(ids, manifest.all_providers())}


def capability_fields(manifest: AppManifest) -> dict[str, str]:
    """Each setting that belongs to one capability alone, and that capability's id.

    Only an app that declares ``settings`` has any: one that does not keeps every setting shared,
    which is what its single settings file always meant. In one that does, each setting is the
    account's or exactly one capability's (:func:`settings_refusals` refuses the rest)."""
    if not manifest.settings:
        return {}
    shared = set(_properties(manifest.settings))
    homes: dict[str, str] = {}
    for cid, schema in capability_schemas(manifest).items():
        for name in _properties(schema):
            if name not in shared:
                homes.setdefault(name, cid)
    return homes


def settings_refusals(manifest: AppManifest) -> list[str]:
    """What the manifest may not do with its account and capability settings, each naming the
    field."""
    from personalclaw.apps.core_features import ACCOUNT_SETTINGS

    if "settings" in manifest.not_schemas:
        return [f"settings must be a JSON schema object, got {manifest.not_schemas['settings']!r}"]
    if not manifest.settings:
        return []
    errors: list[str] = []
    if manifest.setup.configSchema:
        errors.append(
            "settings replaces setup.configSchema: declare the account settings in settings alone"
        )
    if not isinstance(manifest.settings.get("properties", {}), dict):
        errors.append("settings.properties must be an object of settings")
    if ACCOUNT_SETTINGS not in manifest.requiresCoreFeatures:
        errors.append(
            f"an app that declares settings must list {ACCOUNT_SETTINGS!r} in "
            "requiresCoreFeatures, so a core that keeps one settings file refuses it rather than "
            "running it without its capabilities' settings"
        )
    shared = _properties(manifest.settings)
    declared_by: dict[str, str] = {}
    for path, provider in manifest._provider_paths():
        if provider.multiInstance:
            errors.append(
                f"{path}.multiInstance: an app that declares settings keeps more than one account "
                "itself, each a copy of its settings, so none of its providers may be "
                "multiInstance"
            )
        for name in _properties(provider.settingsSchema):
            if name in shared:
                errors.append(
                    f"{path}.settingsSchema.properties.{name} shadows the account setting "
                    f"settings.properties.{name}: a capability's own settings may not reuse an "
                    "account setting's name"
                )
            elif name in declared_by:
                errors.append(
                    f"{path}.settingsSchema.properties.{name} is declared by "
                    f"{declared_by[name]}.settingsSchema too: each setting is the account's or "
                    "one capability's, so rename it or move it to settings"
                )
            else:
                declared_by[name] = path
    return errors


# ── what a capability needs of its account, and an account's connections (#3842) ─────────────


def _required_from(manifest: AppManifest, provider: Any) -> dict[str, Any]:
    """The properties a capability's ``requiresAccountFields`` may name: the account's, or a
    ``multiInstance`` capability's own instance schema."""
    return _properties(provider.settingsSchema if provider.multiInstance else manifest.settings)


def account_field_refusals(manifest: AppManifest) -> list[str]:
    """What the manifest may not do with ``requiresAccountFields`` and ``connections``, each
    naming the field."""
    from personalclaw.apps.manifest import KEBAB_RE

    errors: list[str] = []
    for path, provider in manifest._provider_paths():
        if "requiresAccountFields" in provider.not_lists:
            errors.append(f"{path}.requiresAccountFields must be a list of account setting names")
            continue
        if not provider.requiresAccountFields:
            continue
        if not provider.multiInstance and not manifest.settings:
            errors.append(
                f"{path}.requiresAccountFields: an app with no account settings has no account "
                "fields to require; declare them in settings"
            )
            continue
        declared = _required_from(manifest, provider)
        where = f"{path}.settingsSchema" if provider.multiInstance else "settings"
        for name in provider.requiresAccountFields:
            if name not in declared:
                errors.append(
                    f"{path}.requiresAccountFields names {name}, which {where} does not declare"
                )
    if "connections" in manifest.not_lists:
        return [*errors, "connections must be a list of {id, label} objects"]
    if not manifest.connections:
        return errors
    if not manifest.settings:
        errors.append(
            "connections are an account's, and only an app that declares account settings has "
            "accounts; declare settings, or leave connections out"
        )
    if len(manifest.connections) < 2:
        errors.append(
            "connections lists an account's connections when it has more than one: with one, "
            "leave it out and the account's health is that connection's"
        )
    seen: set[str] = set()
    for i, entry in enumerate(manifest.connections):
        cid = entry.get("id") if isinstance(entry, dict) else None
        label = entry.get("label") if isinstance(entry, dict) else None
        if not isinstance(cid, str) or not KEBAB_RE.match(cid):
            errors.append(f"connections[{i}].id must be a kebab-case id, got: {cid!r}")
        elif cid in seen:
            errors.append(f"connections[{i}].id {cid!r} is declared twice")
        else:
            seen.add(cid)
        if not isinstance(label, str) or not label.strip():
            errors.append(f"connections[{i}].label must name the connection in plain words")
    return errors


def connection_ids(manifest: AppManifest) -> list[str]:
    """The ids of an account's connections, in order; none when it has one."""
    return [str(c["id"]) for c in manifest.connections if isinstance(c, dict) and c.get("id")]


def _is_set(value: Any, held: set[str]) -> bool:
    """Whether a stored setting holds something: a secret's reference counts when the credential
    store holds its key, so no value is read."""
    from personalclaw.config import secret_refs

    key = secret_refs.ref_key(value)
    if key is not None:
        return key in held
    return value not in (None, "", [], {})


def missing_account_fields(
    app: str,
    capability: str,
    account: str | None = None,
    manifest: AppManifest | None = None,
    held: set[str] | None = None,
) -> list[str]:
    """The fields *capability* requires (``requiresAccountFields``) that *account* (the first,
    with ``None``) leaves empty, in the order declared. *held* is the credential store's key
    names, when the caller has read them already (a list of every app's rows reads them once).

    None for a capability that requires nothing, or a ``multiInstance`` one: its accounts are
    its instances, and its ``requiresAccountFields`` is checked against its instance schema but
    not enforced yet. Each instance's runs under its account when each account runs its own
    (#3843), so an app should not count on it before then."""
    from personalclaw.config.credentials import credential_names
    from personalclaw.providers.settings import load_stored_account

    if manifest is None:
        from personalclaw.apps.app_manager import _manifest_of

        manifest = _manifest_of(app)
    if manifest is None:
        return []
    try:
        ids = manifest.capability_ids()
    except ValueError:
        return []
    provider = dict(zip(ids, manifest.all_providers())).get(capability)
    if provider is None or provider.multiInstance or not provider.requiresAccountFields:
        return []
    stored = load_stored_account(app, account)
    if held is None:
        held = set(credential_names())
    return [f for f in provider.requiresAccountFields if not _is_set(stored.get(f), held)]


def needs_setup_sentence(manifest: AppManifest, fields: list[str]) -> str:
    """What a capability that is not built says while *fields* are empty in its account: each by
    the title its schema gives it, else its name."""
    props = _properties(manifest.settings)
    names = [
        str((props.get(f) or {}).get("title") or f) if isinstance(props.get(f), dict) else f
        for f in fields
    ]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    verb = "is" if len(names) == 1 else "are"
    return f"Needs setup: the account's {listed} {verb} empty."
