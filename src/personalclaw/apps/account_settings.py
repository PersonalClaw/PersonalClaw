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
