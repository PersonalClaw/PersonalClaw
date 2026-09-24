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

**A THIRD read path has no schema to consult.** ``personalclaw config get`` prints
``config.json``, whose credential-bearing blocks are exactly the ones core does not model —
``providers`` (the only copy of an API key entered in the dashboard) and the legacy ``slack``
block. Neither arrives with an ``x-meta.sensitive`` declaration: ``providers`` is a raw list
of instance records, and ``slack`` is not a provider extension at all, so there is no
``settingsSchema`` anywhere to read. That path is served by
:func:`mask_secrets_in_document` / :func:`preserve_unchanged_secrets_in_document`, which
*derive* a schema from field names (:func:`is_credential_field_name`) and then delegate the
actual masking and restoring to the two functions above — so the policy still has one
implementation even though it now has two ways of learning which fields it applies to.
Deriving beats resolving installed-app schemas for this surface for one measured reason:
a schema lookup goes **vacuous** when the owning app is not installed, which silently turns
the mask off for exactly the config an operator is most likely to be inspecting by hand.
"""

from __future__ import annotations

import re
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


# ── Deriving sensitivity from a NAME: the org's one definition ────────────────────────────────

#: Words that name a credential. Consulted where there is no ``x-meta.sensitive`` declaration
#: to read — never in place of one. Word-matched rather than substring-matched, because
#: ``token`` as a substring also names every budget field in the config
#: (``max_tokens_per_day``, ``context_budget_tokens``) and masking an integer ceiling would
#: corrupt a config while protecting nothing.
_CREDENTIAL_WORDS = frozenset(
    {
        "apikey",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "secret",
        "secrets",
        "token",
    }
)

#: ``key`` on its own is an identifier, not a credential — ``cache_key``, ``sort_key``,
#: ``semantic_keys``. It becomes a credential in the company of one of these.
_KEY_QUALIFIERS = frozenset(
    {"access", "api", "auth", "client", "encryption", "private", "secret", "service", "signing"}
)


def _words(name: object) -> list[str]:
    """*name*'s words, lowercased, IN ORDER — position is part of the rule below."""
    return [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]


def is_credential_field_name(name: object) -> bool:
    """Does *name* name a credential, judged from the name alone?

    **The single definition of "credential-shaped" in this repository.** Two consumers ask
    this one question and must get one answer:

    * the maskers below, for a field no schema describes (``personalclaw config get``
      prints ``config.json``, whose credential-bearing blocks — ``providers``, the legacy
      ``slack`` block — arrive with no ``settingsSchema`` anywhere to read); and
    * ``tests/test_app_manifest_secret_fields.py``, the rail that requires a
      credential-shaped app setting to declare ``x-meta.sensitive``.

    Those two used to disagree, and the disagreement shipped: the rail matched an *unanchored
    substring* list containing ``token``, so ``max_tokens`` — an integer request parameter —
    was a credential and the rail reddened on two shipped manifests by default in any
    workspace holding both clones. Neither remedy it offered was available (annotating an
    integer ``x-meta.sensitive`` renders it ``type="password"`` and write-only; ``max_tokens``
    is the provider API's own parameter name), which is what two answers to one question
    costs.

    **The rule: a credential noun must be the name's LAST word.** That single constraint is
    what separates a credential from a field *about* one, in both directions:

    * ``max_tokens``, ``token_limit``, ``secrets_dir``, ``api_key_env``, ``keyring_backend``
      and ``context_budget_tokens`` are ordinary fields — a ceiling, a path, an env-var name.
      A rule keyed on mere presence masks all of them.
    * ``sort_key``, ``cache_key`` and ``semantic_keys`` are identifiers, so ``key`` also needs
      a qualifier from :data:`_KEY_QUALIFIERS` — ``api_key`` and ``signing_key`` are
      credentials, ``sort_key`` is a dict lookup.
    * a trailing ``id`` names the same subject, so it is transparent: ``access_key_id`` and
      ``aws_access_key_id`` are half of an AWS credential pair, while ``client_id`` and
      ``user_id`` resolve to ``client``/``user`` and stay public.

    Measured against ``AppConfig().to_dict()`` (337 names, 26 of them non-empty strings), it
    matches nothing in the modelled config, so the blocks it reaches are the unmodeled ones
    that actually hold credentials. Measured over all 100 first-party manifests (257 setting
    occurrences, 108 distinct leaf names) it flags 8 names across 28 sites, every one a real
    credential. The floors that pin both directions live in
    ``tests/test_provider_config_secrets.py`` and ``tests/test_app_manifest_secret_fields.py``.
    """
    words = _words(name)
    if len(words) > 1 and words[-1] == "id":
        words = words[:-1]
    if not words:
        return False
    last = words[-1]
    if last in _CREDENTIAL_WORDS:
        return True
    return bool(last in {"key", "keys"} and set(words) & _KEY_QUALIFIERS)


def _schema_for(names: set[str]) -> dict[str, Any]:
    """The synthetic schema :func:`mask_secrets` and :func:`preserve_unchanged_secrets` read."""
    return {"type": "object", "properties": {n: {"x-meta": {"sensitive": True}} for n in names}}


def _join(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


def mask_secrets_in_document(document: Any) -> tuple[Any, list[str]]:
    """*document* with every credential-named string masked, plus the paths that were masked.

    The whole-document counterpart to :func:`mask_secrets`, for a nested config file rather
    than one flat schema-described object. Non-mutating: the caller keeps its original.

    Two rules, both deliberately biased toward over-masking, because the read side can afford
    it — ``config get --reveal`` is the escape hatch and
    :func:`preserve_unchanged_secrets_in_document` restores by path, so an over-masked field
    still round-trips losslessly:

    * a credential-named field holding a non-empty **string** is masked;
    * a credential-named field holding a dict or list has **every** string inside it masked,
      whatever those inner fields are called — ``credentials: {"github": "…"}`` is a real
      shape and none of its inner names is a tell.
    """
    masked_paths: list[str] = []
    return _mask_node(document, "", False, masked_paths), masked_paths


def _mask_node(node: Any, path: str, inherited: bool, out: list[str]) -> Any:
    if isinstance(node, dict):
        secret_names = {
            key
            for key, value in node.items()
            if isinstance(value, str) and value and (inherited or is_credential_field_name(key))
        }
        masked, were_set = mask_secrets(node, _schema_for(secret_names))
        out.extend(_join(path, name) for name in were_set)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                masked[key] = _mask_node(
                    value,
                    _join(path, key),
                    inherited or is_credential_field_name(key),
                    out,
                )
        return masked
    if isinstance(node, list):
        result = []
        for index, value in enumerate(node):
            here = f"{path}[{index}]"
            if inherited and isinstance(value, str) and value:
                out.append(here)
                result.append(SECRET_MASK)
            else:
                result.append(_mask_node(value, here, inherited, out))
        return result
    return node


def preserve_unchanged_secrets_in_document(incoming: Any, stored: Any) -> tuple[Any, list[str]]:
    """Fold *stored* credentials back into *incoming* wherever a mask arrived, by PATH.

    Returns ``(incoming, unresolved)`` — *incoming* mutated in place (the caller owns it) and
    every path still holding :data:`SECRET_MASK` afterwards. **A non-empty ``unresolved`` must
    refuse the write**: persisting a mask would replace a disclosure bug with the deletion of
    the only copy of a credential, which is strictly worse. That is not hypothetical — it is
    what masking ``config get`` alone would have done to the documented
    ``config get > f.json`` → ``config set --file f.json`` loop, because
    :func:`~personalclaw.config.loader.merge_unmodeled_top_keys` is key-level and shallow, so
    the operator's file already *has* ``providers`` and the merge restores nothing.

    Matched by path rather than by name, so the write side cannot drift from whatever the read
    side chose to mask. List elements pair by their own ``name``/``id`` when both sides carry
    one (``providers`` records do), and otherwise by index **only while both lists are the same
    length** — an inserted or removed element makes index pairing a guess, and a wrong guess
    would write one instance's credential into another. No pair means no restore, which the
    ``unresolved`` refusal then turns into a loud failure rather than a silent swap.
    """
    _restore_node(incoming, stored)
    return incoming, mask_bearing_paths(incoming)


def _identity(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    for field in ("name", "id"):
        value = record.get(field)
        if isinstance(value, str) and value:
            return f"{field}={value}"
    return None


def _restore_node(incoming: Any, stored: Any) -> None:
    if isinstance(incoming, dict) and isinstance(stored, dict):
        names = {key for key in stored if is_credential_field_name(key)}
        names |= {key for key, value in incoming.items() if value == SECRET_MASK}
        preserve_unchanged_secrets(incoming, stored, _schema_for(names))
        for key, value in incoming.items():
            if isinstance(value, (dict, list)):
                _restore_node(value, stored.get(key))
        return
    if isinstance(incoming, list) and isinstance(stored, list):
        by_identity = {
            ident: record for record in stored if (ident := _identity(record)) is not None
        }
        for index, value in enumerate(incoming):
            ident = _identity(value)
            if ident is not None and by_identity:
                # An identified record pairs by identity or not at all: falling back to the
                # index here is how a renamed-then-reordered list would restore the wrong
                # instance's credential.
                partner = by_identity.get(ident)
            elif len(incoming) == len(stored):
                partner = stored[index]
            else:
                partner = None
            if partner is None:
                continue
            if isinstance(value, str):
                if value == SECRET_MASK and isinstance(partner, str) and partner:
                    incoming[index] = partner
            else:
                _restore_node(value, partner)


def mask_bearing_paths(document: Any) -> list[str]:
    """Every path in *document* whose value is :data:`SECRET_MASK`.

    The fail-closed check a write path runs before ``atomic_write``: one rule ("a mask never
    reaches disk") stated once, rather than each writer reasoning about which of its fields
    could have been masked.
    """
    found: list[str] = []
    _collect_masks(document, "", found)
    return sorted(found)


def _collect_masks(node: Any, path: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_masks(value, _join(path, key), out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _collect_masks(value, f"{path}[{index}]", out)
    elif node == SECRET_MASK:
        out.append(path)
