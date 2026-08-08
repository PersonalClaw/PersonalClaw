"""Connector catalog + requirements resolution (AGENT-PACKS §3.3, AP-3).

A pack declares the MCP connectors its skills/templates expect — never their credentials
(§2.2: a ``connectors.json`` entry is schema-banned from carrying a value-bearing field).
On import, each declaration must be RESOLVED against this machine, three ways:

* **configure** — the declared connector is known (in the seeded catalog or self-contained):
  collect the required credential, store it in the credential store (the WORK-R19 fallback:
  :meth:`CredentialStore.save` until a dedicated project-secrets store lands), and write the
  MCP server through :mod:`providers.mcp_instances` (the existing multi-instance seam with
  its injectable-key guard). A credential NEVER lands in the pack or a plaintext config
  field — it goes to the credential store, keyed by name, and the server spec references it
  by env-var name only.
* **substitute** — the user points the requirement at a DIFFERENT catalog entry of the SAME
  ``category`` (their own search MCP instead of the author's). No new server is written; the
  substitute already exists locally, and the connector reference resolves to it.
* **skip** — the requirement is recorded UNMET. Dependent components still install; the pack
  degrades with a machine-readable marker (:data:`MISSING_PREFIX` + name) the pack detail
  page reads to show which connector-dependent parts are unavailable — the degraded-
  completion idiom (a skipped dep is not a crash).

The connector catalog itself is ``<home>/connector_catalog.json`` (§9), seeded with a small
bundled starter set on first read, user-extendable, optionally refreshed from
``packs.connector_catalog_url`` (a later atom drives the refresh; AP-3 only reads the URL).

Fail closed: an unknown resolution mode, a configure with no credential value, or a
substitute to a different-category entry all refuse rather than write a half-configured
server.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

#: The machine-readable degraded-completion marker for a skipped connector (§3.3). A
#: connector-dependent feature reads ``connector_missing:<name>`` to know it is unavailable
#: — a stable code, never prose, so a UI can branch on it (WORKFLOWS-V2's blessed idiom).
MISSING_PREFIX = "connector_missing:"

#: The catalog store file (§9), seeded on first read, user-extendable.
CATALOG_FILE = "connector_catalog.json"


def missing_marker(name: str) -> str:
    """The ``connector_missing:<name>`` marker for a skipped connector ``name``."""
    return f"{MISSING_PREFIX}{name}"


# ── catalog data model ──────────────────────────────────────────────────────


@dataclass
class CatalogEntry:
    """One known connector in the catalog (§3.3): how to configure it, what it needs.

    ``category`` is the substitution axis — a substitute must share it. ``transport`` +
    ``command``/``url`` are the MCP-server template written on configure. ``required_credentials``
    are the credential names the configure path collects; each becomes a credential-store
    descriptor and an ``env`` var on the server spec, NEVER an inline value.
    """

    name: str
    category: str
    transport: str = "stdio"  # "stdio" | "sse"
    command: str = ""
    url: str = ""
    args: list[str] = field(default_factory=list)
    required_credentials: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CatalogEntry":
        return cls(
            name=str(raw.get("name", "") or ""),
            category=str(raw.get("category", "") or ""),
            transport=str(raw.get("transport", "stdio") or "stdio"),
            command=str(raw.get("command", "") or ""),
            url=str(raw.get("url", "") or ""),
            args=[str(a) for a in (raw.get("args") or []) if str(a).strip()],
            required_credentials=[
                str(c) for c in (raw.get("required_credentials") or []) if str(c).strip()
            ],
            description=str(raw.get("description", "") or ""),
        )


#: The bundled starter catalog (§3.3 "seeded with a bundled starter set"). Provider-agnostic
#: by construction: each entry is a CATEGORY of capability (filesystem/search/fetch/database)
#: with a generic reference MCP server, never a vendor's proprietary service. A pack author
#: substitutes their own same-category connector; this is only the floor so a fresh install
#: can configure the common shapes without hand-writing mcp.json.
_SEED_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        name="filesystem",
        category="filesystem",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        description="Local filesystem access scoped to a directory you choose.",
    ),
    CatalogEntry(
        name="fetch",
        category="fetch",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-fetch"],
        description="Fetch and read web pages as markdown.",
    ),
    CatalogEntry(
        name="web-search",
        category="search",
        transport="sse",
        url="",
        required_credentials=["SEARCH_API_KEY"],
        description="A web-search MCP server (bring your own endpoint + API key).",
    ),
    CatalogEntry(
        name="postgres",
        category="database",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        required_credentials=["DATABASE_URL"],
        description="Read-only SQL over a Postgres database.",
    ),
)


def _catalog_path(home: Path | None = None) -> Path:
    return (home or config_dir()) / CATALOG_FILE


def seed_catalog(home: Path | None = None, *, force: bool = False) -> list[CatalogEntry]:
    """Write the bundled starter catalog to ``<home>/connector_catalog.json`` if absent.

    Returns the entries now on disk. Idempotent: an existing catalog is left untouched (the
    user may have extended it) unless ``force`` — the done_when's "store seeded". Writes
    atomically so a crash never leaves a torn catalog.
    """
    from personalclaw.atomic_write import atomic_write

    path = _catalog_path(home)
    if path.exists() and not force:
        return load_catalog(home)
    payload = [e.to_dict() for e in _SEED_CATALOG]
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return list(_SEED_CATALOG)


def load_catalog(home: Path | None = None) -> list[CatalogEntry]:
    """Load the connector catalog, seeding the bundled set on first read.

    A missing store is seeded (so ``done_when`` "catalog seeded" holds the first time any
    code reads it); a present-but-unreadable store falls back to the seed set rather than
    an empty catalog (fail toward a usable floor, never crash the import).
    """
    path = _catalog_path(home)
    if not path.is_file():
        return seed_catalog(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("connector catalog unreadable at %s; using bundled seed", path)
        return list(_SEED_CATALOG)
    if not isinstance(raw, list):
        return list(_SEED_CATALOG)
    return [CatalogEntry.from_dict(r) for r in raw if isinstance(r, dict) and r.get("name")]


def catalog_lookup(name: str, home: Path | None = None) -> CatalogEntry | None:
    """The catalog entry named ``name``, or ``None`` if the connector is unknown here."""
    for entry in load_catalog(home):
        if entry.name == name:
            return entry
    return None


def catalog_by_category(category: str, home: Path | None = None) -> list[CatalogEntry]:
    """Every catalog entry in ``category`` — the substitution candidate set (§3.3)."""
    return [e for e in load_catalog(home) if e.category == category]


# ── resolution ──────────────────────────────────────────────────────────────


@dataclass
class ConnectorResolution:
    """The outcome of resolving one ``connectors.json`` declaration (§3.3).

    ``mode`` is ``configure`` | ``substitute`` | ``skip``. ``server_name`` is the mcp.json
    key written (configure) or the substitute's name (substitute); empty on skip. ``marker``
    is the ``connector_missing:<name>`` degraded-completion code, set ONLY on skip.
    ``credentials_saved`` names the credential-store keys the configure path wrote (never
    their values) — the audit surface proving a credential reached the store, not the pack.
    """

    name: str
    mode: str
    server_name: str = ""
    marker: str = ""
    credentials_saved: list[str] = field(default_factory=list)
    error: str = ""  # set only when a configure/substitute degraded to skip (importer path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "server_name": self.server_name,
            "marker": self.marker,
            "credentials_saved": list(self.credentials_saved),
            "error": self.error,
        }


class ConnectorResolutionError(Exception):
    """A connector resolution could not be completed and wrote nothing (fail closed)."""


def _save_credentials(names: list[str], values: dict[str, str]) -> list[str]:
    """Persist each required credential to the credential store (the WORK-R19 fallback).

    Writes a ``static_token`` descriptor per name whose ``value_env`` is the credential's
    own name — so the resolved secret is read back from the env at spawn time and the server
    spec need only reference the env var, never carry the value. The plaintext value is
    written ONLY into the 0o600 credential store (via :meth:`CredentialStore.save`), never a
    config field or the pack. Returns the names written.

    Raises :class:`ConnectorResolutionError` if a required credential has no value — a
    half-configured connector (server written, credential missing) fails on first use for a
    reason nobody can name, so refuse before writing the server.
    """
    from personalclaw.llm.credentials import CredentialStore

    home = config_dir()
    missing = [n for n in names if not values.get(n)]
    if missing:
        raise ConnectorResolutionError(
            f"configure requires credential value(s) for: {', '.join(sorted(missing))}"
        )
    if not names:
        return []
    store = CredentialStore(home)
    # Merge into the existing descriptor map so we never clobber unrelated credentials.
    descriptors: dict[str, dict[str, object]] = {c.name: {"type": c.kind} for c in store.list()}
    for name in names:
        # The secret rides ONLY into the credential store's env-file, keyed by the credential
        # name; the descriptor points value_env at that same name. No inline value in the
        # descriptor (which is world-readable-ish JSON) — the store's .env is the 0o600 sink.
        descriptors[name] = {"type": "static_token", "value_env": name}
    store.save(descriptors)
    # Persist the actual secret to <home>/.env under the same key (0o600), the resolution
    # chain's file fallback that value_env → env → .env reads back.
    from personalclaw.config.loader import save_credential

    for name in names:
        save_credential(name, values[name])
    return list(names)


def _write_server(entry: CatalogEntry, credential_names: list[str]) -> str:
    """Write ``entry`` as an mcp.json server through the mcp_instances seam. Returns the
    server name. The server references each credential by env-var name (``{"env": {NAME:
    "${NAME}"}}`` shape is provider-specific; we set the env keys so the spawn reads them
    from the process env the credential store populated), never an inline secret."""
    from personalclaw.providers import mcp_instances

    cfg: dict[str, Any] = {
        "transport": entry.transport,
        "command": entry.command,
        "args": " ".join(entry.args),
        "endpoint": entry.url,
    }
    inst = mcp_instances.create_instance(entry.name, cfg)
    # Attach credential env-var references AFTER creation so the injectable-key guard on the
    # server NAME ran first. Env values are the credential names the store populates in the
    # process env — the spec carries references, the secrets live in the store.
    if credential_names:
        _attach_env_refs(entry.name, credential_names)
    return inst.id


def _attach_env_refs(server_name: str, credential_names: list[str]) -> None:
    """Point the server's ``env`` at the credential names (references, not values).

    Reads back the freshly-written server spec and sets ``env[NAME] = "${NAME}"`` for each
    required credential, so the MCP client substitutes the value from the environment the
    credential store populated. The credential VALUE is never written here.
    """
    from personalclaw.providers import mcp_instances

    path = mcp_instances._mcp_json_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    spec = data.get("mcpServers", {}).get(server_name)
    if not isinstance(spec, dict):
        return
    env = spec.get("env")
    if not isinstance(env, dict):
        env = {}
    for name in credential_names:
        env[name] = f"${{{name}}}"
    spec["env"] = env
    from personalclaw.agent import _atomic_json_write

    _atomic_json_write(path, data)


def resolve_connector(
    declaration: dict[str, Any],
    *,
    mode: str,
    credentials: dict[str, str] | None = None,
    substitute: str | None = None,
    home: Path | None = None,
) -> ConnectorResolution:
    """Resolve one pack ``connectors.json`` declaration via ``mode`` (§3.3).

    ``declaration`` is ``{name, category, ...}``. ``mode``:

    * ``configure`` — look ``name`` up in the catalog (or fall back to the declaration's own
      transport/command when self-contained), collect ``credentials`` for its required set,
      save them to the credential store, and write the server. Refuses if a required
      credential has no value.
    * ``substitute`` — ``substitute`` is the name of a same-category catalog entry; the
      reference resolves to it (no server written). Refuses if the substitute is a different
      category or unknown.
    * ``skip`` — record the ``connector_missing:<name>`` marker; write nothing.

    Returns a :class:`ConnectorResolution`. Raises :class:`ConnectorResolutionError` on any
    refusal, having written nothing (fail closed).
    """
    name = str(declaration.get("name", "") or "").strip()
    category = str(declaration.get("category", "") or "").strip()
    if not name:
        raise ConnectorResolutionError("connector declaration has no name")

    if mode == "skip":
        return ConnectorResolution(name=name, mode="skip", marker=missing_marker(name))

    if mode == "substitute":
        sub = (substitute or "").strip()
        if not sub:
            raise ConnectorResolutionError("substitute mode needs a substitute connector name")
        entry = catalog_lookup(sub, home)
        if entry is None:
            raise ConnectorResolutionError(f"substitute {sub!r} is not in the connector catalog")
        if category and entry.category != category:
            # A substitute of a DIFFERENT category would silently rewire the pack to a
            # connector it can't use — refuse rather than write a wrong reference.
            raise ConnectorResolutionError(
                f"substitute {sub!r} is category {entry.category!r}, not {category!r}"
            )
        return ConnectorResolution(name=name, mode="substitute", server_name=entry.name)

    if mode == "configure":
        entry = catalog_lookup(name, home)
        if entry is None:
            # Self-contained: the pack's own declaration carries the transport/command, so
            # build a catalog-shaped entry from it (still no credential values — those come
            # from `credentials`).
            entry = CatalogEntry(
                name=name,
                category=category,
                transport=str(declaration.get("transport", "stdio") or "stdio"),
                command=str(declaration.get("command", "") or ""),
                url=str(declaration.get("url", "") or ""),
                args=[str(a) for a in (declaration.get("args") or []) if str(a).strip()],
                required_credentials=[
                    str(c)
                    for c in ((declaration.get("auth") or {}).get("required_credentials") or [])
                    if str(c).strip()
                ],
            )
        saved = _save_credentials(entry.required_credentials, credentials or {})
        server_name = _write_server(entry, saved)
        return ConnectorResolution(
            name=name, mode="configure", server_name=server_name, credentials_saved=saved
        )

    raise ConnectorResolutionError(f"unknown resolution mode {mode!r}")


def resolve_requirements(
    declarations: list[dict[str, Any]],
    choices: dict[str, dict[str, Any]] | None = None,
    *,
    home: Path | None = None,
) -> list[ConnectorResolution]:
    """Resolve every ``connectors.json`` declaration, defaulting to ``skip`` (§3.3).

    ``choices`` maps a connector name to its resolution input
    ``{mode, credentials?, substitute?}``; a declaration with no choice degrades to ``skip``
    (a connector-dependent part is marked unavailable, the pack still installs). A single
    declaration's refusal raises — the caller decides whether that aborts the batch; the
    importer treats a configure/substitute refusal as a hard error but never lets a plain
    skip fail.
    """
    choices = choices or {}
    out: list[ConnectorResolution] = []
    for decl in declarations:
        name = str(decl.get("name", "") or "").strip()
        choice = choices.get(name, {})
        mode = str(choice.get("mode", "skip") or "skip")
        out.append(
            resolve_connector(
                decl,
                mode=mode,
                credentials=choice.get("credentials"),
                substitute=choice.get("substitute"),
                home=home,
            )
        )
    return out


def resolve_for_import(
    declarations: list[dict[str, Any]],
    choices: dict[str, dict[str, Any]] | None = None,
    *,
    home: Path | None = None,
) -> list[ConnectorResolution]:
    """Resolve every declaration for the IMPORTER — never raises; a failure degrades to skip.

    :func:`resolve_requirements` raises on a bad configure/substitute so an interactive UI
    can surface the error and let the user retry. The importer instead must not abort a
    whole pack install because one connector could not be configured: a resolution failure
    degrades to a ``skip`` with the ``connector_missing:<name>`` marker AND the error text
    recorded, so the dependent components still install and the pack detail page shows
    exactly which connector is unavailable and why (§3.3 degraded-completion). Seeds the
    catalog on first touch so the store exists after any import that resolves a connector.
    """
    if declarations:
        seed_catalog(home)  # done_when 1: the catalog store is seeded on first use
    choices = choices or {}
    out: list[ConnectorResolution] = []
    for decl in declarations:
        name = str(decl.get("name", "") or "").strip()
        choice = choices.get(name, {})
        mode = str(choice.get("mode", "skip") or "skip")
        try:
            out.append(
                resolve_connector(
                    decl,
                    mode=mode,
                    credentials=choice.get("credentials"),
                    substitute=choice.get("substitute"),
                    home=home,
                )
            )
        except ConnectorResolutionError as exc:
            out.append(
                ConnectorResolution(
                    name=name or "<unnamed>",
                    mode="skip",
                    marker=missing_marker(name or "<unnamed>"),
                    error=str(exc),
                )
            )
    return out
