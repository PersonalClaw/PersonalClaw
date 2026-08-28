"""The secrets vault read model — presence, scope and consumers, never a value (EI-10).

🔴 **PRESENCE-ONLY IS STRUCTURAL HERE, NOT A REDACTION STEP.** The distinction is the whole
point of this module existing at all instead of the vault being three helpers inside a handler.

A redaction design reads the value, then filters it out on the way to the wire. That design is
one careless line from a leak — every handler holds the secret in a local, and the only thing
stopping it reaching the client is that nobody returned that local. It also leans on
``redact_credentials``, which is **not idempotent over a composed ``field: value`` line**, so a
value that reaches a formatter has already escaped the mechanism meant to catch it.

This module instead makes the value *unreachable*:

* :class:`SecretPresence` is a DIFFERENT TYPE from anything that carries a secret. It has no
  value field, no ``value`` property and no ``__getattr__``, so there is no expression over a
  ``SecretPresence`` that evaluates to a credential. A future handler that returns one whole,
  or spreads it, or logs it, cannot leak — not because it was careful, but because the object
  it holds never contained the secret.
* The only credential-store call in this module's import graph is
  :func:`~personalclaw.config.credentials.credential_names`, which reads key names on both
  backends without materialising a value (its ``.env`` half splits the line and keeps the left
  side). ``get_credential`` / ``load_credentials`` / ``_dotenv_credentials`` are deliberately
  **not imported here**, and ``tests/test_secrets_vault.py`` asserts that statically over this
  file's source — so the property is a rail, not a habit.

Redaction still runs downstream. It is the backstop; this is the mechanism.

**Three scopes, because their trust stories differ and the UI must not blur them.**

``global``  a credential in the store, usable by anything on this instance.
``project`` a credential in the SAME store under a namespaced key (:data:`PROJECT_KEY_PREFIX`),
            scoped by convention to one project. There is deliberately **no second store and no
            per-project index file**: namespacing the key means the keychain backend (SH-1), the
            ``.env`` 0600 floor, the union read, and — critically — the inventory's
            ``secret=True`` projection into ``portability.EXPORT_EXCLUDE`` all apply to a project
            secret for free. A sidecar index would have needed every one of those re-derived, and
            the one that was missed would be the one that leaked.
``host``    a credential-shaped name present in the gateway's OWN environment that the vault does
            not hold. **The value lives in the host environment, not in the vault**, so the vault
            cannot show it, rotate it, or delete it — which is exactly why it is a first-class row
            type rather than a footnote. Rendering it identically to a vault row would tell the
            user the vault is managing something it has no control over.

The secret-name TEST for a host row is ``workspace.looks_secret`` over
``workflows.secrets.SECRET_KEY_HINTS`` — reused, not restated, for the reason that module already
gives: a second list of credential-ish fragments drifts, and the drifted copy is the one that
lets a token through.

**Consumers are DERIVED, never indexed.** ``consumers_for`` walks the workflow definitions and
triggers that actually exist and reports which of them reference ``{{secret:KEY}}``, through the
two shipped reference readers (``workflows.secrets.secret_keys_referenced`` and
``triggers.secrets.references``). A maintained ``secret → consumers`` table would be a count kept
beside a table: it can disagree with the specs in two directions at once (a stale entry pointing
at a deleted workflow, and a fresh reference nothing recorded), and neither is visible from the
table alone. Derivation cannot drift from the thing it derives from.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Prefix marking a credential-store key as project-scoped. Deliberately not a legal leading
#: fragment of an env-var name a user would choose for a global secret, so the namespace can be
#: decoded unambiguously and a global secret can never be mistaken for a project one.
PROJECT_KEY_PREFIX = "PCPROJ_"

#: Separator between the project id and the user's key name inside a namespaced key. Two
#: underscores because a project id is a slug/uuid that may contain a single one, and a single-
#: underscore separator would split `proj_a__KEY` at the wrong place.
PROJECT_KEY_SEP = "__"

#: Scope identifiers. Wire values as well as internal ones — one vocabulary, so the frontend's
#: three-way branch and the backend's cannot drift into disagreeing about what a row is.
SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_HOST = "host"

#: A vault key name must look like an environment variable, because that is what it becomes:
#: `save_credential` mirrors it into `os.environ` for the children that inherit it. Validating
#: here means a name that could never be delivered is refused at the boundary rather than stored
#: and silently useless.
KEY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

#: Project ids that would make a namespaced key undecodable. A separator inside the id would put
#: the split in the wrong place; an empty id would produce a key that decodes to a nameless
#: project.
_BAD_PROJECT_ID_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def project_secret_key(project_id: str, name: str) -> str:
    """The credential-store key holding *name* for *project_id*.

    One function, so the encode and decode sides cannot drift: every writer and every reader of a
    project-scoped credential goes through this and :func:`split_project_key`.
    """
    return f"{PROJECT_KEY_PREFIX}{project_id}{PROJECT_KEY_SEP}{name}"


def split_project_key(key: str) -> tuple[str, str] | None:
    """``(project_id, name)`` for a namespaced key, or ``None`` when it is not one.

    ``None`` rather than a raise: this runs over every key in the store, most of which are global,
    so "not a project key" is the common case and not an error.
    """
    if not key.startswith(PROJECT_KEY_PREFIX):
        return None
    rest = key[len(PROJECT_KEY_PREFIX) :]
    project_id, sep, name = rest.partition(PROJECT_KEY_SEP)
    if not sep or not project_id or not name:
        return None
    return project_id, name


def valid_key_name(name: str) -> bool:
    """Is *name* usable as a vault key (i.e. as an environment variable name)?"""
    return bool(KEY_NAME_RE.match(name or ""))


def valid_project_id(project_id: str) -> bool:
    """Is *project_id* safe to embed in a namespaced key without breaking the decode?"""
    pid = project_id or ""
    if not pid or PROJECT_KEY_SEP in pid:
        return False
    return not _BAD_PROJECT_ID_RE.search(pid)


@dataclass(frozen=True)
class SecretConsumer:
    """One thing that references a secret. Identity and label only.

    Carries no config: a consumer's config is where the reference LIVES, and shipping it beside a
    secret row would put the surrounding fields — some of them credential-shaped — on a response
    whose entire contract is that it carries none.
    """

    kind: str = ""
    id: str = ""
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "label": self.label}


@dataclass(frozen=True)
class SecretPresence:
    """One vault row: that a secret EXISTS, where it is scoped, and what uses it.

    🔴 **There is no value field, and adding one would defeat the module.** The type is the
    enforcement: a handler can only serialise what a `SecretPresence` has, and it has a name, a
    scope, a project id and a consumer list. See the module docstring for why this is a type
    rather than a redaction step.

    ``frozen=True`` so a row cannot be mutated into carrying something else after construction —
    the read model is built once, from names, and is then read-only all the way to the wire.
    """

    name: str = ""
    scope: str = SCOPE_GLOBAL
    project_id: str = ""
    consumers: tuple[SecretConsumer, ...] = field(default_factory=tuple)

    @property
    def inherited_from_host(self) -> bool:
        """True for a row whose value lives in the host environment, not the vault.

        Exposed as a derived property rather than a stored flag so it cannot contradict
        ``scope``. The frontend renders these distinctly; see the module docstring.
        """
        return self.scope == SCOPE_HOST

    def to_dict(self) -> dict[str, Any]:
        """The wire shape. ``present`` is always True — a row exists because a secret does.

        Shipping the constant rather than omitting it keeps the frontend's row renderer honest
        about what it is displaying: presence, not a value it happens not to have received.
        """
        return {
            "name": self.name,
            "scope": self.scope,
            "project_id": self.project_id,
            "present": True,
            "inherited_from_host": self.inherited_from_host,
            "consumers": [c.to_dict() for c in self.consumers],
        }


def _host_secret_names() -> list[str]:
    """Credential-shaped names in the gateway's own environment.

    ``save_credential`` mirrors every stored secret into ``os.environ``, so this set overlaps the
    vault's by construction; :func:`list_presence` subtracts the vault's names, and what remains
    is genuinely host-supplied.
    """
    from personalclaw.workflows.workspace import looks_secret

    return sorted(name for name in os.environ if looks_secret(name))


def list_presence(
    *,
    project_id: str = "",
    consumers: dict[str, tuple[SecretConsumer, ...]] | None = None,
) -> list[SecretPresence]:
    """The vault's rows: globals, this project's, and inherit-from-host — presence only.

    *project_id* narrows the project rows to one project; empty means "every project's". Host and
    global rows are unconditional, because a project's secrets are resolved against the same
    process environment and the same store — a project view that hid them would show a user
    fewer credentials than their project can actually reach.

    *consumers* is injected rather than derived here so the sync read model stays sync:
    :func:`consumers_for` has to await the workflow def providers, and a module that needs an
    event loop to answer "what secrets exist" is unusable from the CLI and from a test.
    """
    from personalclaw.config.credentials import credential_names

    consumer_map = consumers or {}
    stored = credential_names()
    rows: list[SecretPresence] = []

    for key in stored:
        split = split_project_key(key)
        if split is None:
            rows.append(
                SecretPresence(
                    name=key,
                    scope=SCOPE_GLOBAL,
                    consumers=consumer_map.get(key, ()),
                )
            )
            continue
        owner, name = split
        if project_id and owner != project_id:
            continue
        rows.append(
            SecretPresence(
                name=name,
                scope=SCOPE_PROJECT,
                project_id=owner,
                consumers=consumer_map.get(key, ()),
            )
        )

    # Host rows LAST and by subtraction. A name the vault holds is a vault row even though
    # `save_credential` also put it in `os.environ` — reporting it as inherit-from-host would tell
    # the user the value is outside the vault when the vault is exactly where it is.
    vault_names = {r.name for r in rows if r.scope == SCOPE_GLOBAL}
    for name in _host_secret_names():
        if name in vault_names or split_project_key(name) is not None:
            continue
        rows.append(
            SecretPresence(name=name, scope=SCOPE_HOST, consumers=consumer_map.get(name, ()))
        )

    rows.sort(key=lambda r: (r.scope, r.project_id, r.name))
    return rows


def project_secret_names(project_id: str) -> list[str]:
    """The user-facing key names this project holds in the vault, sorted.

    This is what the project export turns into presence flags — see
    ``workflows.project_export.plan_export``'s ``secret_names`` argument. Derived from the
    credential store's key namespace, so an exported flag cannot claim a secret the store does
    not hold.
    """
    from personalclaw.config.credentials import credential_names

    out: list[str] = []
    for key in credential_names():
        split = split_project_key(key)
        if split is not None and split[0] == project_id:
            out.append(split[1])
    return sorted(out)


# ── consumer derivation ──


def _spec_of(definition: Any) -> Any:
    """The spec-ish payload of a workflow definition, whatever shape the provider returned.

    Providers hand back dataclasses, dicts, or objects with a ``spec`` attribute. Reaching for
    each in turn and falling through to the object itself means a new provider shape degrades to
    "scan the whole object", which under-reports nothing — the reference readers walk dicts and
    strings and simply find no matches in an object they cannot traverse.
    """
    for attr in ("spec", "definition", "nodes"):
        value = getattr(definition, attr, None)
        if value is not None:
            return value
    if isinstance(definition, dict):
        return definition.get("spec", definition)
    to_dict = getattr(definition, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:  # noqa: BLE001 - a provider's serializer is not this module's problem
            logger.debug("workflow def to_dict() failed while deriving consumers", exc_info=True)
    return definition


def _label_of(definition: Any, fallback: str) -> str:
    for attr in ("display_name", "title", "name"):
        value = getattr(definition, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(definition, dict):
        for attr in ("display_name", "title", "name"):
            value = definition.get(attr)
            if isinstance(value, str) and value.strip():
                return value
    return fallback


async def consumers_for() -> dict[str, tuple[SecretConsumer, ...]]:
    """``secret key → what references it``, derived from the specs that exist right now.

    Both halves go through the SHIPPED reference readers rather than a third regex:
    ``workflows.secrets.secret_keys_referenced`` for workflow definitions and
    ``triggers.secrets.references`` for trigger action configs. Those two are what actually
    RESOLVE ``{{secret:KEY}}`` at dispatch, so a name this function reports as a consumer is a
    name that will really be looked up — and one it omits is one nothing resolves. A third copy of
    the pattern could be correct on its own and still disagree with the resolver, which is the
    failure a derived index exists to make impossible.

    Keyed by the STORE key, not the display name, so a project-scoped row and a global row of the
    same name do not collide: a project secret's consumers are looked up under its namespaced key.

    Never raises. A store that cannot be read yields fewer consumer links, which degrades the
    vault to "presence without provenance" — the security properties are unaffected, and refusing
    the whole listing because one trigger file is corrupt would be a worse trade.
    """
    out: dict[str, list[SecretConsumer]] = {}

    def _add(key: str, consumer: SecretConsumer) -> None:
        bucket = out.setdefault(key, [])
        if consumer not in bucket:
            bucket.append(consumer)

    for name, label, keys in await _workflow_references():
        for key in keys:
            _add(key, SecretConsumer(kind="workflow", id=name, label=label))
    for trigger_id, label, keys in _trigger_references():
        for key in keys:
            _add(key, SecretConsumer(kind="trigger", id=trigger_id, label=label))

    return {k: tuple(v) for k, v in out.items()}


async def _workflow_references() -> list[tuple[str, str, list[str]]]:
    """``(name, label, referenced keys)`` per workflow definition across every provider."""
    from personalclaw.workflows.defs import get_provider, list_providers
    from personalclaw.workflows.secrets import secret_keys_referenced

    found: list[tuple[str, str, list[str]]] = []
    for provider_name in list_providers():
        provider = get_provider(provider_name)
        if provider is None:
            continue
        try:
            defs, _total = await provider.list_defs(limit=500, offset=0)
        except Exception:  # noqa: BLE001 - one bad provider must not blank the whole vault
            logger.debug("workflow provider %r unreadable", provider_name, exc_info=True)
            continue
        for definition in defs or []:
            ident = _label_of(definition, "")
            keys = secret_keys_referenced(_spec_of(definition))
            if keys:
                found.append((ident, ident or provider_name, keys))
    return found


def _trigger_references() -> list[tuple[str, str, list[str]]]:
    """``(id, label, referenced keys)`` per trigger, over its action config.

    The config comes from ``schedule_view._inline_action``, the same accessor the doctor's
    would-execute preview uses (``doctor._action_config_fact``), not from a ``getattr`` guess at
    the field name. A trigger's action is stored differently depending on how it was created, and
    that function is where the product already decided how to read it — a second reader here would
    report no consumers for exactly the trigger shapes it had not been taught about.
    """
    from personalclaw.triggers.schedule_view import _inline_action
    from personalclaw.triggers.secrets import references
    from personalclaw.triggers.store import TriggerStore

    found: list[tuple[str, str, list[str]]] = []
    try:
        triggers = TriggerStore().list_triggers(include_broken=True)
    except Exception:  # noqa: BLE001 - an unreadable trigger store costs links, not the listing
        logger.debug("trigger store unreadable while deriving consumers", exc_info=True)
        return found
    for trigger in triggers or []:
        try:
            action = _inline_action(trigger)
            keys = references(action.get("config"))
        except Exception:  # noqa: BLE001 - one malformed trigger must not blank the others
            logger.debug("trigger action unreadable while deriving consumers", exc_info=True)
            continue
        if not keys:
            continue
        ident = str(getattr(trigger, "id", "") or "")
        label = str(getattr(trigger, "name", "") or ident)
        found.append((ident, label, keys))
    return found
