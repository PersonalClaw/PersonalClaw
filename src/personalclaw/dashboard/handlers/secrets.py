"""The secrets vault surface — ``/api/secrets``, presence in, values one-way only (EI-10).

Three routes:

* ``GET    /api/secrets`` — every row the vault knows: global, per-project, and inherit-from-host,
  each with its derived consumer links. **No value, in any row, ever.**
* ``POST   /api/secrets`` — store or replace one secret's value.
* ``DELETE /api/secrets`` — remove one.

🔴 **THE WRITE PATH IS ONE-WAY, AND THAT IS STRUCTURAL.** A value enters through ``POST`` and goes
straight into the credential store. Nothing in this module can read one back:
:mod:`personalclaw.secrets_vault` builds the whole read model from key NAMES
(``credentials.credential_names``), and ``get_credential`` is not imported here or there. So the
response to a ``POST`` that just stored a token is assembled from the same name-only read model as
a ``GET`` — the value the handler was handed one line earlier is not in the object it serialises,
because that object is a :class:`~personalclaw.secrets_vault.SecretPresence` and the type has no
value field. This is deliberately not "redact on the way out": ``redact_credentials`` is not
idempotent over a composed ``field: value`` line, so a design that let the value reach the
serializer would be relying on the backstop as the mechanism.

**Owner-only, categorically** — the same flat refusal as ``security_credentials`` and
``security_audit``. The app-permission middleware is an allowlist, so an app that merely declared
``/api/secrets`` would pass it; an app must not learn even the NAMES of the owner's credentials,
which is why the refusal is here and not left to a permission scope.

**Why ``DELETE`` needs no ``confirm``, unlike the credential-store migration next door.** That
one moves every credential between backends and can lose them all; this removes one named secret
the user picked from a list, and the name is in the request. A confirm flag on a single-row delete
is the kind of ceremony that trains people to send ``confirm: true`` without reading, which is how
the flag stops meaning anything on the route where it matters.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.secrets_vault import (
    SCOPE_HOST,
    SecretPresence,
    consumers_for,
    list_presence,
    project_secret_key,
    valid_key_name,
    valid_project_id,
)
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _refuse_app(request: web.Request) -> web.Response | None:
    """Refuse an app-scoped token, or ``None`` when the caller is the owner.

    Same shape as ``security_credentials._refuse_app`` — including the SEL denial record, so a
    refused app attempt is auditable rather than a silent 403.
    """
    app_name = request.get("app", "")
    if not app_name:
        return None
    try:
        sel().log_api_access(
            caller=f"app:{app_name}",
            operation=f"{request.method} {request.path}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error="the secrets vault is owner-only",
        )
    except Exception:
        pass
    return json_error(
        "credentials_owner_only",
        message="the secrets vault is the owner's, not an app's",
        status=403,
    )


async def _vault_rows(project_id: str = "") -> list[SecretPresence]:
    """The vault's rows with consumer links attached.

    One place, so the three routes cannot render different row shapes — a POST that answered with
    a row lacking consumer links would make the frontend's optimistic update disagree with the
    next GET.
    """
    return list_presence(project_id=project_id, consumers=await consumers_for())


def _store_key(name: str, project_id: str) -> str:
    """The credential-store key for a (name, scope) pair."""
    return project_secret_key(project_id, name) if project_id else name


def _empty_state_hint(rows: list[SecretPresence]) -> str:
    """What to do next when the vault is empty — a sentence, not a status.

    "No secrets yet" alone reads as "secrets are broken" on a page whose whole subject is
    credentials. An empty vault is a normal, healthy state, and the sentence a user needs is the
    next action, not a restatement of the count. Composed here rather than in the frontend because
    a server-composed sentence is the product surface: the CLI and the dashboard must say the same
    thing about the same state.
    """
    if rows:
        return ""
    return (
        "No secrets stored yet. Add one here, then reference it from a workflow or automation "
        "as {{secret:NAME}} — the value is written once and never read back out."
    )


async def api_secrets_list(request: web.Request) -> web.Response:
    """GET /api/secrets — the vault, presence only.

    ``?project_id=`` narrows the project-scoped rows to one project. Global and host rows are
    always included: a project resolves ``{{secret:KEY}}`` against the same store and the same
    process environment, so hiding them would show the user fewer credentials than their project
    can actually reach.
    """
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    project_id = str(request.query.get("project_id") or "").strip()
    if project_id and not valid_project_id(project_id):
        return json_error("secret_project_invalid", status=400)
    rows = await _vault_rows(project_id)
    return web.json_response(
        {
            "secrets": [r.to_dict() for r in rows],
            "counts": {
                "total": len(rows),
                "global": sum(1 for r in rows if r.scope == "global"),
                "project": sum(1 for r in rows if r.scope == "project"),
                "host": sum(1 for r in rows if r.scope == SCOPE_HOST),
            },
            "empty_hint": _empty_state_hint(rows),
        }
    )


async def api_secrets_put(request: web.Request) -> web.Response:
    """POST /api/secrets — store one secret's value. The response carries presence, not the value.

    Body: ``{"name": "GITHUB_TOKEN", "value": "…", "project_id": "…"?}``. An existing name in the
    same scope is REPLACED, which is how rotation works — there is no separate rotate verb,
    because "write the new value" is the whole operation and a second endpoint would be a second
    write path to keep in step with the first.
    """
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)

    name = str(body.get("name") or "").strip()
    project_id = str(body.get("project_id") or "").strip()
    # `value` is read into a local exactly once, handed to the store, and never put in a response,
    # a log line or an error message. The `str()` is not a coercion of convenience — a JSON number
    # or bool would otherwise reach `save_credential`, which writes `KEY=value` into a 0600 .env
    # and would produce a line the parser reads back as a different type.
    value = body.get("value")
    value = "" if value is None else str(value)

    if not valid_key_name(name):
        return json_error("secret_name_invalid", status=400)
    if project_id and not valid_project_id(project_id):
        return json_error("secret_project_invalid", status=400)
    if not value:
        return json_error("secret_value_required", status=400)

    # Storing a name the host environment already supplies is ALLOWED, and is how a user takes
    # ownership of an inherited credential: `list_presence` subtracts vault names from the host
    # set, so the new vault row REPLACES the inherit-from-host row rather than appearing beside
    # it. Refusing here was the first draft's behaviour and it made host rows read as a lockout.
    from personalclaw.config.credentials import save_credential

    save_credential(_store_key(name, project_id), value)
    del value  # the local is dead from here; nothing below may reference it

    rows = await _vault_rows(project_id)
    stored = _find_row(rows, name, project_id)
    return web.json_response(
        {
            "secret": stored.to_dict() if stored is not None else {},
            "secrets": [r.to_dict() for r in rows],
        }
    )


async def api_secrets_delete(request: web.Request) -> web.Response:
    """DELETE /api/secrets?name=&project_id= — remove one secret from the vault.

    A host-inherited row is refused rather than silently no-oped: its value is in the gateway's
    environment, so the vault genuinely cannot remove it, and reporting success would leave the
    user believing a credential is gone while every run still resolves it.
    """
    denied = _refuse_app(request)
    if denied is not None:
        return denied
    name = str(request.query.get("name") or "").strip()
    project_id = str(request.query.get("project_id") or "").strip()
    if not valid_key_name(name):
        return json_error("secret_name_invalid", status=400)
    if project_id and not valid_project_id(project_id):
        return json_error("secret_project_invalid", status=400)

    if not project_id and _is_host_only(name, await _vault_rows()):
        return json_error("secret_host_readonly", status=409)

    from personalclaw.config.credentials import delete_credential

    if not delete_credential(_store_key(name, project_id)):
        return json_error("secret_absent", status=404)

    rows = await _vault_rows(project_id)
    return web.json_response(
        {
            "deleted": name,
            "project_id": project_id,
            "secrets": [r.to_dict() for r in rows],
        }
    )


def _find_row(rows: list[SecretPresence], name: str, project_id: str) -> SecretPresence | None:
    """The row for one (name, scope) pair, or None."""
    for row in rows:
        if row.name == name and row.project_id == project_id and row.scope != SCOPE_HOST:
            return row
    return None


def _is_host_only(name: str, rows: list[SecretPresence]) -> bool:
    """Is *name* present ONLY as an inherit-from-host row?

    Reads the assembled rows rather than `os.environ` directly, so the answer comes from the same
    subtraction `list_presence` performs. Asking the environment here would report a stored vault
    secret as host-inherited, because `save_credential` mirrors every write into `os.environ`.
    """
    return any(r.name == name and r.scope == SCOPE_HOST for r in rows) and not any(
        r.name == name and r.scope != SCOPE_HOST and not r.project_id for r in rows
    )


def register_secrets_routes(app: web.Application) -> None:
    """Register the secrets vault surface."""
    app.router.add_get("/api/secrets", api_secrets_list)
    app.router.add_post("/api/secrets", api_secrets_put)
    app.router.add_delete("/api/secrets", api_secrets_delete)


__all__ = [
    "api_secrets_delete",
    "api_secrets_list",
    "api_secrets_put",
    "register_secrets_routes",
]
