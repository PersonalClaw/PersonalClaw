"""Secret settings live in the credential store; a settings FILE carries only a reference.

🔴 **THE DEFECT.** Five kinds of settings record persisted a credential inline, in plaintext:

* core ``config.json`` ``providers[].options`` — the key typed in Settings → Providers → Add
  instance landed as ``options.api_key`` (measured on the real image, at mode 0644);
* an app's own settings, ``apps/<app>/data/config.json`` — slack-channel's Bot and App tokens,
  which a keep-data uninstall then parked, tokens and all, in ``apps/.<app>.data``;
* a multi-instance provider's records, ``extensions/<app>/instances/<id>.json``;
* an MCP server's ``env`` and ``headers`` in ``mcp.json``, copied into the agent config
  ``agents/personalclaw.json`` (see "MCP server specs" below);
* the webhook token, ``hooks.webhook_token`` in ``config.json`` (see "core config.json").

Every snapshot and every export captures all of them, so each plaintext key also left the machine
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
``PCSECRET_INSTANCE_…``, ``PCSECRET_MCP_…``, ``PCSECRET_CONFIG_…`` — deterministically, so
re-saving a record re-uses its key and deleting
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


# ── MCP server specs: mcp.json and the agent config ─────────────────────────
#
# An MCP server's ``env`` block (and a remote server's ``headers``) is where its API key goes.
# Both documents that hold server specs — ``mcp.json``, the store the native client spawns from,
# and ``agents/personalclaw.json``, which the rebuild copies every spec into — keep references;
# every path that adds or changes a server writes through :func:`write_mcp_document`, and the
# three places a server is started (``mcp_client``, the discovery probe, the rebuild's command
# lookup) resolve at that moment.
#
# **Which values are secret: every one, unless the server marks a variable plain.** A server's
# environment is free-form, and its secrets have no naming convention a rule could rely on:
# ``DATABASE_URL`` carries a password, ``NOTION_INTEGRATION`` is a token, ``GH_PAT`` matches no
# credential noun. A name rule that misses leaves the secret in plaintext in every export, silently;
# storing a value that was not secret costs only that it stays on this machine. So every value is
# stored, except a variable named in the spec's ``plainEnv`` list (the Add form's "Plain values"
# field), which stays inline and travels with an export — unless its name is credential-shaped,
# which no marking overrides. Header values are always stored: headers are how a remote server
# authenticates, and nothing in the product writes a plain one.

#: Spec key listing the ``env`` variables that stay inline — settings that are not secret.
MCP_PLAIN_ENV = "plainEnv"

_MCP_OWNED_PREFIX = f"{OWNED_KEY_PREFIX}MCP_"
_MCP_PARTS = {"env": "ENV", "headers": "HDR"}


@dataclass(frozen=True)
class _ExactNameOwner(SecretOwner):
    """An owner whose field names are case-sensitive and unbounded — environment variables and
    headers. ``API_KEY`` and ``api_key`` are two variables, and two long names can share their
    first 32 characters, so the key carries a digest of the exact name as well as its segment."""

    def key(self, field_name: str) -> str:
        return f"{self.prefix}{_segment(field_name, 32)}_{_digest(field_name)}"


def mcp_server_prefix(server: str) -> str:
    """Every key an MCP server's secrets are stored under starts with this."""
    return f"{_MCP_OWNED_PREFIX}{_segment(server, 32)}_{_digest(server)}__"


def _mcp_owner(server: str, part: str) -> SecretOwner:
    return _ExactNameOwner(f"{mcp_server_prefix(server)}{_MCP_PARTS[part]}__")


def plain_env_names(spec: Mapping[str, Any]) -> set[str]:
    """The ``env`` variables ``spec`` marks plain."""
    listed = spec.get(MCP_PLAIN_ENV)
    return {n for n in listed if isinstance(n, str)} if isinstance(listed, list) else set()


def _unstorable(value: Any) -> bool:
    return (
        isinstance(value, str)
        and ref_key(value) is None
        and any(ch in value.strip() for ch in ("\n", "\r", "\x00"))
    )


def store_mcp_spec(server: str, spec: Mapping[str, Any], *, strict: bool) -> dict[str, Any]:
    """The STORED form of one MCP server spec: each ``env`` value (bar the plain ones) and each
    ``headers`` value saved under a key the server owns, the field holding the reference.

    ``strict`` is for a value a user is typing now: one the store cannot hold faithfully (a
    multi-line value — ``.env`` is one ``KEY=VALUE`` per line) raises :class:`ValueError` and
    nothing is stored, so the caller can refuse it. Otherwise that value is left inline and
    logged: the write chokepoint and the boot move must never drop a server, or the rest of its
    secrets, over one value they cannot move.
    """
    if strict:
        # Refused BEFORE anything is stored: a refusal halfway through would leave the values
        # it had already saved in the store with no file referencing them.
        for part in _MCP_PARTS:
            values = spec.get(part)
            for name, value in values.items() if isinstance(values, Mapping) else ():
                if _unstorable(value):
                    raise ValueError(
                        f"{name}: a multi-line value cannot be kept in the credential store — "
                        "enter it as a single line"
                    )
    out = dict(spec)
    for part in _MCP_PARTS:
        values = spec.get(part)
        if not isinstance(values, Mapping) or not values:
            continue
        movable = dict(values)
        inline: dict[str, Any] = {}
        for name in [n for n, v in movable.items() if _unstorable(v)]:
            inline[name] = movable.pop(name)
            logger.warning(
                "MCP server %r: %s %s holds a multi-line value, which the credential store "
                "cannot keep; it stays in the file",
                server,
                part,
                name,
            )
        plain = plain_env_names(spec) if part == "env" else set()
        stored = store(
            movable,
            owner=_mcp_owner(server, part),
            declared=[n for n in movable if n not in plain],
        )
        out[part] = {n: inline[n] if n in inline else stored[n] for n in values}
    return out


def resolve_mcp_values(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """An ``env`` or ``headers`` map in LOGICAL form, for the moment a server is started."""
    out: dict[str, Any] = {}
    for name, value in (values or {}).items():
        key = ref_key(value)
        if key is None:
            out[name] = value
            continue
        # A reference the store cannot answer is DROPPED, not passed on as ``""``: the child then
        # inherits the gateway's own variable of that name, if any, exactly as it would had the
        # spec never named it — and never receives the placeholder as though it were a token.
        real = get_credential(key)
        if real:
            out[name] = real
    return out


def resolve_mcp_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The LOGICAL form of one MCP server spec — for the moment a server is started, never for
    a file. ``plainEnv`` is dropped: it describes the stored form and means nothing to a child."""
    out = {k: v for k, v in spec.items() if k != MCP_PLAIN_ENV}
    for part in _MCP_PARTS:
        values = spec.get(part)
        if isinstance(values, Mapping):
            out[part] = resolve_mcp_values(values)
    return out


def foreign_mcp_spec(spec: Mapping[str, Any], *, with_secrets: bool) -> dict[str, Any]:
    """A spec for ANOTHER tool's config file (Claude Code's), which cannot read this store.

    ``with_secrets`` is the user putting a server into that tool's scope on purpose: the values
    go with it, in the only form that tool reads. Without it — a copy nobody asked for — every
    stored value is left out, and only the plain ones travel.
    """
    if with_secrets:
        return resolve_mcp_spec(spec)
    out = {k: v for k, v in spec.items() if k != MCP_PLAIN_ENV}
    for part in _MCP_PARTS:
        values = spec.get(part)
        if isinstance(values, Mapping):
            out[part] = {n: v for n, v in values.items() if ref_key(v) is None}
    return out


def _mcp_refs(doc: Any) -> set[str]:
    """Every owned MCP key a ``{"mcpServers": …}`` document references."""
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    keys: set[str] = set()
    for spec in servers.values() if isinstance(servers, dict) else ():
        for part in _MCP_PARTS:
            values = spec.get(part) if isinstance(spec, dict) else None
            for value in values.values() if isinstance(values, dict) else ():
                key = ref_key(value)
                if key and key.startswith(_MCP_OWNED_PREFIX):
                    keys.add(key)
    return keys


def mcp_documents() -> tuple[Path, Path]:
    """The two files that hold MCP server specs: ``mcp.json`` and the agent config."""
    from personalclaw.agent import AGENT_FILENAME, agents_dir
    from personalclaw.config.loader import config_dir

    return config_dir() / "mcp.json", agents_dir() / AGENT_FILENAME


def write_mcp_document(path: Path, data: dict[str, Any]) -> None:
    """Write ``mcp.json`` or the agent config with every server in STORED form.

    Every server in ``data`` has its secrets moved into the store before the file is written
    (``data`` is updated in place, so a caller reading it afterwards reads what reached the
    disk). A secret the write stopped referencing is then deleted — unless the OTHER document
    still references it: both files hold the same server under the same owned keys, and a
    removal lands in one of them first.
    """
    from personalclaw.agent import _atomic_json_write

    previous = _read_json(path)
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        for name, spec in servers.items():
            if isinstance(spec, dict):
                servers[name] = store_mcp_spec(str(name), spec, strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, data)
    doomed = _mcp_refs(previous) - _mcp_refs(data)
    for other in mcp_documents():
        if doomed and other != path:
            doomed -= _mcp_refs(_read_json(other))
    for key in sorted(doomed):
        delete_credential(key)


# ── core config.json: the webhook token ─────────────────────────────────────

#: Secret-bearing sections of ``config.json`` and the fields each DECLARES secret (a
#: credential-shaped name in the section is caught too). ``hooks.webhook_token`` is the bearer
#: token ``POST /api/hooks/agent`` checks.
_CONFIG_SECRET_FIELDS: dict[str, tuple[str, ...]] = {"hooks": ("webhook_token",)}


def config_owner(section: str) -> SecretOwner:
    """A secret-bearing section of core ``config.json``."""
    return SecretOwner(f"{OWNED_KEY_PREFIX}CONFIG_{_segment(section, 32)}__")


def store_config_secrets(
    doc: dict[str, Any], previous: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The STORED form of a whole ``config.json`` document, updated in place and returned.

    For every writer that can put a secret into the file: ``AppConfig.save()`` (which every API
    handler that saves the config, and the boot-time config migration, go through) and the CLI's
    ``config set`` / ``--file`` / ``unset``. A value typed into the file by hand is moved at the
    next boot (:func:`migrate_plaintext_secrets`). ``previous`` is the document on disk before the
    write, so a secret this write drops — ``config unset hooks.webhook_token`` — is deleted from
    the store.
    """
    for section, declared in _CONFIG_SECRET_FIELDS.items():
        before = previous.get(section) if isinstance(previous, Mapping) else None
        before = before if isinstance(before, Mapping) else None
        values = doc.get(section)
        if isinstance(values, dict):
            doc[section] = store(
                values, owner=config_owner(section), declared=declared, previous=before
            )
        else:
            _delete_orphans(before, {}, config_owner(section))
    return doc


# ── the one-time move (gateway boot) ────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    from personalclaw.atomic_write import atomic_write

    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)


def _point_at(values: Mapping[str, Any], fields: Iterable[str], owner: SecretOwner) -> dict:
    """``values`` with each plaintext secret in ``fields`` replaced by a reference to the key
    ``owner`` keeps it under — WITHOUT storing the plaintext (see ``point_only``)."""
    moved = dict(values)
    for field_name in fields:
        value = moved.get(field_name)
        if isinstance(value, str) and value.strip() and ref_key(value) is None:
            moved[field_name] = make_ref(owner.key(field_name))
    return moved


def _move_config_document(path: Path, *, point_only: bool) -> bool:
    """``providers[].options`` and the secret-bearing sections (``hooks``) of one config
    document. ``point_only`` (``config.json.bak``) replaces a plaintext secret with a reference
    to the LIVE record's key without storing the backup's value — a backup can hold an older
    key, and storing it would overwrite a rotation."""
    doc = _read_json(path)
    if not isinstance(doc, dict):
        return False
    changed = False
    for section, declared in _CONFIG_SECRET_FIELDS.items():
        values = doc.get(section)
        if not isinstance(values, dict):
            continue
        owner = config_owner(section)
        if point_only:
            moved = _point_at(values, secret_fields(values, declared), owner)
        else:
            moved = store(values, owner=owner, declared=declared)
        if moved != values:
            doc[section] = moved
            changed = True
    records = doc.get("providers")
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or not isinstance(record.get("options"), dict):
            continue
        name = str(record.get("name") or "")
        if not name:
            continue
        options = record["options"]
        if point_only:
            fields = secret_fields(options, declared_provider_type_fields(str(record.get("type"))))
            moved = _point_at(options, fields, provider_owner(name))
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


def _point_mcp_at_live(spec: dict[str, Any], live: Any) -> dict[str, Any]:
    """The agent config's copy of a server ``mcp.json`` also defines: each plaintext value in it
    is pointed at ``mcp.json``'s key for that variable instead of being stored. The rebuild lays
    ``mcp.json``'s spec over this copy, so ``mcp.json`` holds the value that is live — storing the
    copy's (possibly older) value under the same key would overwrite it."""
    if not isinstance(live, Mapping):
        return spec
    out = dict(spec)
    for part in _MCP_PARTS:
        mine, theirs = spec.get(part), live.get(part)
        if isinstance(mine, Mapping) and isinstance(theirs, Mapping):
            out[part] = {
                name: (
                    theirs[name]
                    if isinstance(value, str)
                    and ref_key(value) is None
                    and ref_key(theirs.get(name))
                    else value
                )
                for name, value in mine.items()
            }
    return out


def _move_mcp_document(path: Path, live: Path | None) -> bool:
    """Every server spec in ``mcp.json`` or the agent config (``live``: the ``mcp.json`` whose
    keys the agent config's copies point at)."""
    doc = _read_json(path)
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    if not isinstance(servers, dict):
        return False
    live_doc = _read_json(live) if live is not None else None
    live_servers = live_doc.get("mcpServers") if isinstance(live_doc, dict) else None
    changed = False
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        twin = live_servers.get(name) if isinstance(live_servers, dict) else None
        moved = store_mcp_spec(str(name), _point_mcp_at_live(spec, twin), strict=False)
        if moved != spec:
            servers[name] = moved
            changed = True
    if changed:
        _write_json(path, doc)
    return changed


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
    mcp_json, agent_config = mcp_documents()
    steps: list[tuple[Path, Any]] = [
        (home / "config.json", lambda p: _move_config_document(p, point_only=False)),
        (home / "config.json.bak", lambda p: _move_config_document(p, point_only=True)),
        # `mcp.json` first: the agent config's copies point at the keys it stores.
        (mcp_json, lambda p: _move_mcp_document(p, live=None)),
        (agent_config, lambda p: _move_mcp_document(p, live=mcp_json)),
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
