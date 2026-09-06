"""One masking policy for the sensitive fields of an app/provider settings schema.

A schema property flagged ``x-meta.sensitive: true`` is **write-only**: the operator may
set it and replace it, and the value never travels back out of the process. Two API
surfaces read the very same file — ``~/.personalclaw/apps/<app>/data/config.json`` — and
must therefore agree:

* ``GET/PUT /api/apps/{name}/config`` (the Apps config UI), and
* ``GET/PATCH /api/providers/{name}/config`` (the Settings → Providers schema form).

They did not. The apps route masked; the providers route — the one the Providers page
actually calls — returned the stored secret verbatim, and echoed it back on save. For the
bundled ``slack-channel`` app that meant a Bot Token and an App Token in every page-load
response body, held in React state, and revealable on screen through the form's eye
toggle. Two implementations of one rule is how that happens, so there is now one:
this module, used by both routes.

The same rule governs a *multi-instance* provider's per-instance config — a
:class:`~personalclaw.providers.instances.ExtensionInstance` — through
:func:`mask_instance`. Its ``to_dict()`` is the **persistence** serializer (the instance
store writes that exact dict to disk), so masking cannot live inside it; masking a
credential onto disk would destroy it. :func:`mask_instance` is the *wire* serializer
instead, and every instance route uses it. That distinction is the whole reason the
instance surfaces leaked for as long as they did: the route handed a disk serializer
straight to a response body.

The write half is the other half of the same rule. Once ``GET`` masks, the form PATCHes the
MASK back for any field the operator did not touch, so a PATCH must read the sentinel (and
an empty string where a value already exists) as "keep what is stored" rather than
overwrite a real credential with bullets.

Masking is about not *handing out* secrets; it is not encryption at rest. The store itself
is a plaintext file under the user's home, which the app-platform threat model addresses
separately (``docs/architecture/app-platform.md``).
"""

from __future__ import annotations

from typing import Any

#: What a set-but-withheld sensitive value renders as. Doubles as the sentinel a client
#: sends back to mean "leave it alone" — the form round-trips whatever GET returned.
SECRET_MASK = "••••••••"


def sensitive_field_names(schema: dict[str, Any]) -> set[str]:
    """Property names flagged ``x-meta.sensitive: true`` in a config/settings schema."""
    props = (schema or {}).get("properties") or {}
    if not isinstance(props, dict):
        return set()
    return {
        key
        for key, spec in props.items()
        if isinstance(spec, dict) and (spec.get("x-meta") or {}).get("sensitive")
    }


def mask_secrets(
    config: dict[str, Any], schema: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(masked_config, names_that_are_set)``.

    Every sensitive field holding a non-empty value is replaced by :data:`SECRET_MASK`;
    the second element names those fields so a UI can say "saved" without being told what
    was saved. An unset sensitive field is left as-is (empty), because "not configured" is
    not a secret and the operator needs to see the difference.
    """
    sensitive = sensitive_field_names(schema)
    masked = dict(config or {})
    were_set: list[str] = []
    for key in sensitive:
        if str(masked.get(key, "") or ""):
            masked[key] = SECRET_MASK
            were_set.append(key)
    return masked, sorted(were_set)


def mask_instance(instance: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """The WIRE form of a multi-instance provider instance: ``to_dict()``, config masked.

    *instance* is anything with a ``to_dict()`` returning the instance record (in practice
    :class:`~personalclaw.providers.instances.ExtensionInstance`, shared by the generic
    instance store and the ``mcp-tools`` one). That method is the **disk** serializer —
    :func:`~personalclaw.providers.instances.create_instance` writes its output verbatim —
    so the mask must be applied here, on the way out, and never inside it.

    ``_secret_set`` rides on the instance record rather than at the response's top level:
    the list route returns N instances, each with its own config, so a single top-level
    list could not say *which* instance a named field belongs to.
    """
    wire = dict(instance.to_dict())
    masked, secret_set = mask_secrets(wire.get("config") or {}, schema)
    wire["config"] = masked
    wire["_secret_set"] = secret_set
    return wire


def preserve_unchanged_secrets(
    incoming: dict[str, Any], existing: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Fold stored secrets back into *incoming* for fields the client did not change.

    Mutates and returns *incoming* (the callers already own that dict). A sensitive field
    is restored from *existing* when it arrives as :data:`SECRET_MASK`, or as an empty
    string while a value is stored — the two shapes a round-tripped masked form produces.
    A sensitive field the client omits entirely is left omitted, so a caller can still
    clear a credential by sending an explicit empty value for a field that has none.
    """
    if not isinstance(incoming, dict):
        return incoming
    for key in sensitive_field_names(schema):
        if key not in incoming:
            continue
        arrived = str(incoming.get(key, "") or "")
        stored = str(existing.get(key, "") or "")
        if arrived == SECRET_MASK or (arrived == "" and stored):
            if stored:
                incoming[key] = existing[key]
            else:
                incoming.pop(key, None)
    return incoming
