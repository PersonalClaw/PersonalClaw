"""The capability ids of an app's providers: how each is named, and what a manifest may not do
with them (#3841).

Each provider an app declares is a capability, kept under ``<app>/<id>``
(``providers/registry.py``). Its ``id`` is the one it declares, or, in a manifest written before
ids, the one its type derives (:data:`DERIVED_CAPABILITY_IDS`): the singular ``provider`` reads as
the first. :func:`capability_refusals` is ``AppManifest.validate``'s part for ids and labels.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.apps.provider_config import ProviderConfig

#: The capability id a provider declared without one gets from its type: the vocabulary the
#: first-party apps' ids use, so an update that adds ids maps each old capability onto the same id.
#: A ``model`` provider derives ``model``: its own id says what it serves (``chat``,
#: ``embeddings``…), so a model app maps it with ``renamedFrom: ["model"]``.
DERIVED_CAPABILITY_IDS: dict[str, str] = {
    "action": "action",
    "agent": "agent",
    "channel": "channel",
    "inbox": "inbox",
    "knowledge": "knowledge",
    "memory": "memory",
    "model": "model",
    "notification": "notifications",
    "ocr": "ocr",
    "prompt": "prompts",
    "sandbox": "sandbox",
    "search": "search",
    "skills": "skills",
    "sync": "sync",
    "task": "tasks",
    "tool": "tools",
    "trigger": "automations",
    "trigger_source": "triggers",
    "vector_store": "vector-store",
    "workflow": "workflows",
}


def derived_capability_id(provider_type: str) -> str:
    """The id a provider of *provider_type* declared without one is known by."""
    return DERIVED_CAPABILITY_IDS.get(provider_type, provider_type.replace("_", "-"))


def capability_ids(manifest: AppManifest) -> list[str]:
    """Each provider's capability id, in :meth:`AppManifest.all_providers` order: its declared
    ``id``, else the one its type derives (:data:`DERIVED_CAPABILITY_IDS`).

    Raises :class:`ValueError` with the sentence to show when that does not name each
    capability apart: two providers of one type with no ids, or an id used twice.
    """
    paths = manifest._provider_paths()
    per_type = Counter(p.type for _, p in paths)
    ids: list[str] = []
    for path, p in paths:
        if not p.id and per_type[p.type] > 1:
            raise ValueError(
                f"{manifest.name} declares {per_type[p.type]} {p.type!r} providers and {path} "
                "has no id: give each an id"
            )
        ids.append(p.id or derived_capability_id(p.type))
    repeated = [i for i, n in Counter(ids).items() if n > 1]
    if repeated:
        raise ValueError(f"{manifest.name} uses the capability id {repeated[0]!r} twice")
    return ids


def capability_refusals(manifest: AppManifest) -> list[str]:
    """The refusals for *manifest*'s capability ids and labels, each naming its field."""
    from personalclaw.apps.manifest import KEBAB_RE

    paths = manifest._provider_paths()
    errors = [_not_a_list("retiredCapabilities", raw) for raw in manifest.not_lists.values()]
    errors += [
        _not_a_list(f"{path}.renamedFrom", p.not_lists["renamedFrom"])
        for path, p in paths
        if "renamedFrom" in p.not_lists
    ]
    for i, rid in enumerate(manifest.retiredCapabilities):
        if not KEBAB_RE.match(rid):
            errors.append(f"retiredCapabilities[{i}] must be a kebab-case id, got: {rid!r}")
    per_type = Counter(p.type for _, p in paths)
    retired = set(manifest.retiredCapabilities)
    seen_ids: dict[str, str] = {}
    named: list[tuple[str, ProviderConfig, str]] = []
    for path, p in paths:
        if p.id and not KEBAB_RE.match(p.id):
            errors.append(f"{path}.id must be kebab-case, got: {p.id!r}")
        if not p.id and per_type[p.type] > 1:
            errors.append(
                f"{path} is one of {per_type[p.type]} {p.type!r} providers with no id: "
                "give each an id"
            )
            continue
        cid = p.id or derived_capability_id(p.type)
        if cid in seen_ids:
            how = "" if p.id else f" (derived from its type {p.type!r})"
            errors.append(f"{path}.id {cid!r}{how} is already the id of {seen_ids[cid]}")
        seen_ids.setdefault(cid, path)
        if cid in retired:
            errors.append(
                f"{path}.id {cid!r} was retired (retiredCapabilities): a retired id is never "
                "declared again"
            )
        named.append((path, p, cid))
    errors += _label_refusals(named)
    errors += _rename_refusals(paths, set(seen_ids), retired)
    errors += _shared_word_refusals(paths)
    return errors


def _not_a_list(field: str, raw: object) -> str:
    return f"{field} must be a list of ids, got {json.dumps(raw, default=str)[:60]}"


def _label_refusals(named: list[tuple[str, ProviderConfig, str]]) -> list[str]:
    """A label says in plain words what its capability does: never its id, a type name, or
    another capability's label."""
    from personalclaw.apps.provider_config import PROVIDER_TYPES

    type_words = {t.replace("_", " ") for t in PROVIDER_TYPES} | set(PROVIDER_TYPES)
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path, p, cid in named:
        if not p.label:
            continue
        folded = p.label.casefold()
        if folded == cid.casefold():
            errors.append(f"{path}.label is its id: say in plain words what it does")
        elif folded in type_words:
            errors.append(
                f"{path}.label {p.label!r} is a provider type: say in plain words what it does"
            )
        if folded in seen:
            errors.append(f"{path}.label repeats {seen[folded]}.label")
        seen.setdefault(folded, path)
    return errors


def _rename_refusals(
    paths: list[tuple[str, ProviderConfig]], declared: set[str], retired: set[str]
) -> list[str]:
    """Each old id in ``renamedFrom`` moves to one capability: never one this app still
    declares, a retired one, or one another capability is renamed from too."""
    from personalclaw.apps.manifest import KEBAB_RE

    errors: list[str] = []
    renamed_by: dict[str, str] = {}
    for path, p in paths:
        for old in p.renamedFrom:
            if not KEBAB_RE.match(old):
                errors.append(f"{path}.renamedFrom must hold kebab-case ids, got: {old!r}")
            elif old in declared:
                errors.append(f"{path}.renamedFrom names {old!r}, an id this app still declares")
            elif old in retired:
                errors.append(
                    f"{path}.renamedFrom names {old!r}, a retired id (retiredCapabilities)"
                )
            elif old in renamed_by:
                errors.append(
                    f"{path}.renamedFrom names {old!r}, also renamed by {renamed_by[old]}"
                )
            renamed_by.setdefault(old, path)
    return errors


#: The types whose capabilities share one name in their per-type registry (the app's), so two of
#: one type in one app may not declare the same word: a trigger source's events, which arrive as
#: ``app:<app>:<event>``, and a model's uses, which bindings (``provider:model``) and the per-use
#: registries key by the app, so a second would replace the first and switching either off would
#: take the other's away.
_SHARED_WORDS = {
    "trigger_source": ("event", "two trigger sources in one app may not share an event name"),
    "model": ("use", "two model capabilities in one app may not serve the same use"),
}


def _shared_word_refusals(paths: list[tuple[str, ProviderConfig]]) -> list[str]:
    errors: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for path, p in paths:
        noun, rule = _SHARED_WORDS.get(p.type, ("", ""))
        for word in p.capabilities if noun else []:
            other = seen.setdefault((p.type, word), path)
            if other != path:
                errors.append(f"{path}.capabilities: the {noun} {word!r} is also {other}'s; {rule}")
    return errors
