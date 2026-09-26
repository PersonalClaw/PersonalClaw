"""Secret settings live in the credential store; a settings FILE carries only a reference.

🔴 **THE DEFECT.** Three kinds of settings file persisted a credential inline, in plaintext:

* core ``config.json`` ``providers[].options`` — the key typed in Settings → Providers → Add
  instance landed as ``options.api_key`` (measured on the real image, at mode 0644);
* an app's own settings, ``apps/<app>/data/config.json`` — slack-channel's Bot and App tokens,
  which a keep-data uninstall then parked, tokens and all, in ``apps/.<app>.data``;
* a multi-instance provider's records, ``extensions/<app>/instances/<id>.json``.

Every snapshot and every export captures all three, so each plaintext key also left the machine
in every archive. The config round-trip contract already said secrets go in the credential store;
nothing at those writers enforced it.

**The mechanism.** A settings record has two forms. The LOGICAL form is what every caller reads
and writes — real values, exactly as before. The STORED form is what reaches the disk: each
secret field's value is saved to the credential store (:mod:`personalclaw.config.credentials`,
the keychain / ``.env`` store the Secrets panel and the HF token already use — not a second
store) under a key this record OWNS, and the field holds ``{{secret:<key>}}`` instead — the
reference syntax workflows and triggers already use (``workflows.secrets.SECRET_BINDING_RE``).
:func:`store` turns logical into stored at the writers; :func:`resolve` turns stored into logical
at the readers. A provider factory, a channel transport and a settings form therefore keep
receiving the value, and no app has to change to stop persisting its secrets.

**Which fields are secret.** The union of the fields the owning app DECLARES
``x-meta.sensitive`` and every field whose NAME is credential-shaped
(:func:`~personalclaw.apps.secret_fields.is_credential_field_name`, the one definition the
manifest rail also uses). The declaration is the truth when the schema is available; the name
still catches the field when it is not (a provider type whose app has not loaded yet).

**Ownership.** The store key encodes its owner — ``PCSECRET_PROVIDER_…``, ``PCSECRET_APP_…``,
``PCSECRET_INSTANCE_…`` — deterministically, so re-saving a record re-uses its key and deleting
the record (or uninstalling the app) removes exactly what it owned, by prefix. A reference to a
key the record does NOT own (``{{secret:MY_VAULT_KEY}}``, typed by hand to share a Secrets-panel
credential) is resolved like any other but never deleted: it belongs to the vault.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.config.credentials import (
    OWNED_KEY_PREFIX,
    credential_names,
    delete_credential,
    get_credential,
    save_credential,
)
from personalclaw.workflows.secrets import SECRET_BINDING_RE

logger = logging.getLogger(__name__)


# ── references ──────────────────────────────────────────────────────────────


def make_ref(key: str) -> str:
    """The reference a settings file holds in place of the value stored under ``key``."""
    return "{{secret:" + key + "}}"


def ref_key(value: Any) -> str | None:
    """The store key ``value`` references, when ``value`` is exactly one reference."""
    if not isinstance(value, str):
        return None
    match = SECRET_BINDING_RE.fullmatch(value.strip())
    return match.group(1) if match else None


# ── owners ──────────────────────────────────────────────────────────────────


def _segment(text: str, limit: int) -> str:
    """``text`` as an env-var-safe segment. Never contains ``__`` — that is the separator."""
    seg = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()[:limit].rstrip("_")
    return seg or "X"


def _digest(text: str) -> str:
    """A short, stable disambiguator: two names that segment alike still get two keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8].upper()


@dataclass(frozen=True)
class SecretOwner:
    """The settings record a stored secret belongs to. ``prefix`` ends in ``__``."""

    prefix: str

    def key(self, field_name: str) -> str:
        return f"{self.prefix}{_segment(field_name, 32)}"

    def owns(self, key: str) -> bool:
        return key.startswith(self.prefix)


def _app_part(app: str) -> str:
    return f"{_segment(app, 32)}_{_digest(app)}"


def _instances_prefix(app: str) -> str:
    return f"{OWNED_KEY_PREFIX}INSTANCE_{_app_part(app)}__"


def provider_owner(name: str) -> SecretOwner:
    """A ``config.json`` ``providers[]`` instance, by its name."""
    return SecretOwner(f"{OWNED_KEY_PREFIX}PROVIDER_{_segment(name, 32)}_{_digest(name)}__")


def app_owner(app: str) -> SecretOwner:
    """An app's own settings file (``apps/<app>/data/config.json``)."""
    return SecretOwner(f"{OWNED_KEY_PREFIX}APP_{_app_part(app)}__")


def instance_owner(app: str, instance_id: str) -> SecretOwner:
    """One record of a multi-instance provider (``extensions/<app>/instances/<id>.json``)."""
    return SecretOwner(
        f"{_instances_prefix(app)}{_segment(instance_id, 16)}_{_digest(instance_id)}__"
    )


def app_owned_prefixes(app: str) -> tuple[str, ...]:
    """Every prefix an app's secrets are stored under: its settings and its instances."""
    return (app_owner(app).prefix, _instances_prefix(app))


# ── which fields ────────────────────────────────────────────────────────────


def secret_fields(settings: Mapping[str, Any], declared: Iterable[str] = ()) -> set[str]:
    """The declared sensitive fields, plus every credential-shaped field name present."""
    from personalclaw.apps.secret_fields import is_credential_field_name

    names = set(declared)
    names.update(k for k in settings if is_credential_field_name(k))
    return names


def _app_manifest(app: str) -> Any:
    """The app's manifest: from the live provider registry, else the installed copy on disk."""
    try:
        from personalclaw.providers.registry import get_provider_registry

        ext = get_provider_registry().get(app)
        if ext is not None:
            return ext.manifest
    except Exception:  # noqa: BLE001 — a registry that cannot answer falls back to disk
        logger.debug("provider registry unreadable while resolving %s's schema", app)
    try:
        from personalclaw.apps.manager import APP_MANIFEST_FILENAME, app_dir
        from personalclaw.apps.manifest import AppManifest

        path = app_dir(app) / APP_MANIFEST_FILENAME
        if path.is_file():
            return AppManifest.from_json_file(path)
    except Exception:  # noqa: BLE001 — no manifest means "declares nothing", never a crash
        logger.debug("manifest of %s unreadable while resolving its schema", app, exc_info=True)
    return None


def declared_app_fields(app: str) -> set[str]:
    """Fields ``app`` declares ``x-meta.sensitive`` — in every provider's ``settingsSchema``
    and in ``setup.configSchema``, the two schemas that describe its one settings file."""
    from personalclaw.apps.secret_fields import sensitive_field_names

    manifest = _app_manifest(app)
    if manifest is None:
        return set()
    names = sensitive_field_names(getattr(manifest.setup, "configSchema", None) or {})
    for provider in manifest.all_providers():
        names |= sensitive_field_names(provider.settingsSchema or {})
    return names


def declared_provider_type_fields(ptype: str) -> set[str]:
    """Fields the model app registering ``ptype`` declares ``x-meta.sensitive`` — the form
    Settings → Providers → Add instance renders from that app's ``settingsSchema``."""
    from personalclaw.apps.secret_fields import sensitive_field_names

    try:
        from personalclaw.providers.registry import get_provider_registry, model_provider_type

        extensions = get_provider_registry().list_by_type("model")
    except Exception:  # noqa: BLE001 — no registry: the name rule still applies
        return set()
    names: set[str] = set()
    for ext in extensions:
        if model_provider_type(ext) == ptype:
            names |= sensitive_field_names(ext.provider_config.settingsSchema or {})
    return names


# ── the two forms ───────────────────────────────────────────────────────────


def resolve(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """The LOGICAL form: every top-level field holding a reference gets the stored value.

    A reference whose key the store does not hold resolves to ``""``: the field reads as UNSET,
    so a provider falls back to its env var or reports "no API key configured" — it never sends
    the placeholder upstream as though it were a credential.
    """
    out = dict(settings or {})
    for name, value in out.items():
        key = ref_key(value)
        if key is not None:
            out[name] = get_credential(key)
    return out


def store(
    settings: Mapping[str, Any] | None,
    *,
    owner: SecretOwner,
    declared: Iterable[str] = (),
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The STORED form of LOGICAL ``settings``, with each secret value moved into the store.

    ``previous`` is the record's stored form before this write: an owned key it referenced that
    this form no longer does is deleted, so a rotated-away or cleared credential does not linger.
    The reference is written only after the store has provably kept the value — a write that
    would otherwise record a pointer to nothing fails instead (:class:`OSError`).

    Raises :class:`ValueError` for a value the store cannot hold faithfully: ``.env`` is one
    ``KEY=VALUE`` per line, so a multi-line secret would split into lines that parse as OTHER
    credentials. Surrounding whitespace is dropped — it is never part of a key or a token, and a
    pasted value often carries a trailing newline.
    """
    out = dict(settings or {})
    for name in sorted(secret_fields(out, declared)):
        value = out.get(name)
        if not isinstance(value, str) or ref_key(value) is not None:
            continue
        value = value.strip()
        if not value:
            continue
        if any(ch in value for ch in ("\n", "\r", "\x00")):
            raise ValueError(
                f"{name}: a multi-line value cannot be kept in the credential store — "
                "enter it as a single line"
            )
        key = owner.key(name)
        save_credential(key, value)
        if get_credential(key) != value:
            raise OSError(f"the credential store did not keep {name!r}; nothing was written")
        out[name] = make_ref(key)
    _delete_orphans(previous, out, owner)
    return out


def _delete_orphans(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any], owner: SecretOwner
) -> None:
    before = {k for v in (previous or {}).values() if (k := ref_key(v)) and owner.owns(k)}
    after = {k for v in current.values() if (k := ref_key(v))}
    for key in sorted(before - after):
        delete_credential(key)


def owned_field_names(stored: Mapping[str, Any] | None, owner: SecretOwner) -> list[str]:
    """Names of the fields in a STORED record that reference a key ``owner`` holds.

    Presence only — the value is never read, so a list surface can say "a key is saved"
    without holding one.
    """
    held = set(credential_names())
    return sorted(
        name
        for name, value in (stored or {}).items()
        if (k := ref_key(value)) and owner.owns(k) and k in held
    )


def count_owned(prefixes: Iterable[str]) -> int:
    """How many stored secrets live under ``prefixes``. Names only; no value is read."""
    wanted = tuple(prefixes)
    return sum(1 for k in credential_names() if k.startswith(wanted))


def purge(prefixes: Iterable[str]) -> int:
    """Delete every stored secret under ``prefixes`` — an uninstalled app, a deleted record."""
    wanted = tuple(prefixes)
    doomed = [k for k in credential_names() if k.startswith(wanted)]
    for key in doomed:
        delete_credential(key)
    return len(doomed)


# ── core config.json providers[] ────────────────────────────────────────────


def store_provider_options(
    name: str, ptype: str, options: Mapping[str, Any], previous: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """:func:`store` for one ``config.json`` ``providers[]`` record's ``options``."""
    return store(
        options,
        owner=provider_owner(name),
        declared=declared_provider_type_fields(ptype),
        previous=previous,
    )


def resolve_provider_records(records: Any) -> list[dict[str, Any]]:
    """``config.json`` ``providers[]`` records with their ``options`` in LOGICAL form.

    For the readers that hand a record's options to something that uses them (a catalog, a
    media adapter, an app's scanner). Records are copied, never mutated in place: a caller that
    later writes the document must write the STORED form it read.
    """
    out: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        copy = dict(record)
        if isinstance(record.get("options"), dict):
            copy["options"] = resolve(record["options"])
        out.append(copy)
    return out


# ── the one-time move (gateway boot) ────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    from personalclaw.atomic_write import atomic_write

    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)


def _move_config_providers(path: Path, *, point_only: bool) -> bool:
    """``providers[].options`` of one config document. ``point_only`` (``config.json.bak``)
    replaces a plaintext secret with a reference to the LIVE record's key without storing the
    backup's value — a backup can hold an older key, and storing it would overwrite a rotation."""
    doc = _read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("providers"), list):
        return False
    changed = False
    for record in doc["providers"]:
        if not isinstance(record, dict) or not isinstance(record.get("options"), dict):
            continue
        name = str(record.get("name") or "")
        if not name:
            continue
        options = record["options"]
        if point_only:
            owner = provider_owner(name)
            fields = secret_fields(options, declared_provider_type_fields(str(record.get("type"))))
            moved = dict(options)
            for field_name in fields:
                value = moved.get(field_name)
                if isinstance(value, str) and value.strip() and ref_key(value) is None:
                    moved[field_name] = make_ref(owner.key(field_name))
        else:
            moved = store_provider_options(name, str(record.get("type") or ""), options, options)
        if moved != options:
            record["options"] = moved
            changed = True
    if changed:
        _write_json(path, doc)
    return changed


def _move_app_settings(path: Path, app: str) -> bool:
    data = _read_json(path)
    if not isinstance(data, dict):
        return False
    moved = store(data, owner=app_owner(app), declared=declared_app_fields(app), previous=data)
    if moved == data:
        return False
    _write_json(path, moved)
    return True


def _strip_parked_settings(path: Path) -> bool:
    """A keep-data copy of an UNINSTALLED app: its secrets are dropped, not moved. Uninstall
    removes an app's secrets; these were parked by a release that did not."""
    data = _read_json(path)
    if not isinstance(data, dict):
        return False
    doomed = [
        name
        for name in secret_fields(data)
        if isinstance(data.get(name), str) and data[name] and ref_key(data[name]) is None
    ]
    if not doomed:
        return False
    for name in doomed:
        del data[name]
    _write_json(path, data)
    return True


def _move_instance_record(path: Path, app: str) -> bool:
    record = _read_json(path)
    if not isinstance(record, dict) or not isinstance(record.get("config"), dict):
        return False
    config = record["config"]
    owner = instance_owner(app, str(record.get("id") or path.stem))
    moved = store(config, owner=owner, declared=declared_app_fields(app), previous=config)
    if moved == config:
        return False
    record["config"] = moved
    _write_json(path, record)
    return True


def migrate_plaintext_secrets() -> list[str]:
    """Move every plaintext secret an earlier release left in a settings file into the store.

    Called once at gateway boot, after extensions load (so every app's declared fields are
    known) and before the provider registry reads ``config.json``. Returns the home-relative
    paths it rewrote. Idempotent — a file with nothing to move is not written — and fail-safe
    per file: an error leaves that file exactly as it was and is logged; the key it holds keeps
    working in plaintext until the next boot retries, because a move that half-happened must not
    cost the user a key they typed. A file it rewrites is 0600 in a 0700 directory, through
    ``atomic_write``; a file it does not rewrite keeps its mode — permissions change on the
    write path only.
    """
    from personalclaw.apps.manager import apps_dir
    from personalclaw.config.loader import config_dir

    home = config_dir()
    steps: list[tuple[Path, Any]] = [
        (home / "config.json", lambda p: _move_config_providers(p, point_only=False)),
        (home / "config.json.bak", lambda p: _move_config_providers(p, point_only=True)),
    ]
    apps = apps_dir()
    if apps.is_dir():
        for settings in sorted(apps.glob("*/data/config.json")):
            app = settings.parent.parent.name
            if not app.startswith("."):
                steps.append((settings, lambda p, a=app: _move_app_settings(p, a)))
        # A keep-data uninstall parks the app's `data/` AS `apps/.<app>.data`.
        for parked in sorted(apps.glob(".*.data/config.json")):
            steps.append((parked, _strip_parked_settings))
    extensions = home / "extensions"
    if extensions.is_dir():
        for record in sorted(extensions.glob("*/instances/*.json")):
            app = record.parent.parent.name
            steps.append((record, lambda p, a=app: _move_instance_record(p, a)))

    rewritten: list[str] = []
    seen: set[Path] = set()
    for path, step in steps:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            if step(path):
                rewritten.append(str(path.relative_to(home)))
        except Exception:  # noqa: BLE001 — one unreadable record must not block the others
            logger.warning(
                "could not move the secrets out of %s; it is unchanged", path, exc_info=True
            )
    if rewritten:
        logger.info("Moved plaintext secrets into the credential store: %s", ", ".join(rewritten))
    return rewritten
