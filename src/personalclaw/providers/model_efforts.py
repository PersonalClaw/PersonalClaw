"""The reasoning-effort levels a native chat's model declares: the one answer its turns and the
effort endpoint both read.

The composer lists an effort only for a model that declares it (``efforts`` on
``GET /api/models/chat``) and sets a chat whose model lacks its level back to Default, but only
while a composer has the chat open. A turn sent through the API or another channel ran at whatever
level the chat had stored, and ``POST /api/chat/sessions/{session}/reasoning-effort`` stored any
well-formed level on a native chat. Both ask here now, of the model the chat is bound to:

* Auto names no model, so it declares none.
* A model on core's own wire, one whose provider type's app registered a branded spec so core
  builds its requests, declares the levels that wire sends it
  (:func:`~personalclaw.llm.branded_specs.wire_efforts`).
* A model whose app runs a wire of its own declares the levels the app's listing names for it.

Where nothing says, the answer is ``None``: a model whose app could not be asked, does not list it,
or names no levels for it (a listing that names none cannot be told from one that says nothing of
efforts), and a ref that names no provider instance. The turn then sends the level as stored and
the endpoint takes it, because core does not guess what another wire takes.

An agent CLI's chat is not judged here: its runtime declares its own levels (``supported_efforts``)
and the endpoint holds a level to those.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

#: How long one app's listing answers for its models before a turn reads it again. A listing can
#: be a network call, and a turn must not pay it every time.
_TTL_SECS = 600.0
#: The longest a turn or the endpoint waits on an app's listing before it says nothing.
_LIST_TIMEOUT_SECS = 8.0

#: Each app instance's listing, keyed by its name and a digest of its settings: when it was read,
#: and each listed model's declared levels (``None`` when it could not be read).
_listed: dict[tuple[str, str], tuple[float, dict[str, tuple[str, ...]] | None]] = {}


def forget_listings() -> None:
    """Forget every listing read (tests). An instance whose settings change is read afresh without
    this: its settings are part of the key."""
    _listed.clear()


def native_runtime(provider_kind: str) -> bool:
    """Whether the runtime ``provider_kind`` names, as an agent's bindings resolve it
    (``ResolvedBindings.provider``), is the native agent this rule is for. ``""`` resolves to the
    global default, as the provider bridge resolves it; one that can't be read is not judged."""
    kind = str(provider_kind or "")
    if not kind:
        try:
            from personalclaw.config.loader import AppConfig

            kind = AppConfig.load().agent.provider or "native"
        except Exception:  # noqa: BLE001 — an unreadable config leaves the level as it was
            logger.debug("could not read the default agent runtime", exc_info=True)
            return False
    return not kind.startswith("acp")


def core_wire_efforts(provider_type: str, model_id: str) -> tuple[str, ...] | None:
    """The levels core's own wire sends ``model_id`` on an instance of ``provider_type``, or
    ``None`` when that type's app runs a wire of its own (it registered no branded spec)."""
    from personalclaw.llm.branded_specs import registered_spec, wire_efforts
    from personalclaw.llm.registry import canonical_provider_type

    spec = registered_spec(canonical_provider_type(provider_type))
    return wire_efforts(spec.protocol, model_id) if spec is not None else None


def _bound(model_ref: str) -> tuple[Any, str] | None:
    """The provider entry and the model id ``model_ref`` names, read as the native runtime reads a
    chat's model (``provider_bridge._resolve_from_config_registry``): ``Provider:model`` when the
    prefix names an entry, else ``Provider/model``. ``None`` for a bare id, which the chain's
    provider serves, and for a prefix that names no entry."""
    from personalclaw.llm.registry import get_default_registry

    entries = {e.name: e for e in get_default_registry().list_entries()}
    if ":" in model_ref and model_ref.split(":", 1)[0] in entries:
        name, model_id = model_ref.split(":", 1)
    elif "/" in model_ref:
        name, model_id = model_ref.split("/", 1)
    else:
        return None
    entry = entries.get(name)
    if entry is None or not model_id.strip():
        return None
    return entry, model_id


async def _listing(entry: Any) -> dict[str, tuple[str, ...]] | None:
    """Each model ``entry``'s app lists, with the levels it declares for it; ``None`` when the
    listing cannot be read (no catalog, its last connection check failed, it failed or outran
    :data:`_LIST_TIMEOUT_SECS`). Read once per :data:`_TTL_SECS`."""
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.connection import (
        FAILED,
        get_connection_board,
        settings_fingerprint,
    )

    fingerprint = settings_fingerprint(entry.type, entry.options)
    key = (entry.name, fingerprint)
    now = time.monotonic()
    hit = _listed.get(key)
    if hit is not None and now - hit[0] < _TTL_SECS:
        return hit[1]
    last = get_connection_board().peek(entry.name, fingerprint)
    if last is not None and last.state == FAILED:
        return None  # its listing would fail too, and re-send a key its vendor refused
    catalog = get_default_registry().build_catalog(entry)
    if catalog is None:
        return None
    levels: dict[str, tuple[str, ...]] | None
    try:
        rows = await asyncio.wait_for(catalog.list_models(), _LIST_TIMEOUT_SECS)
    except Exception:  # noqa: BLE001 — an unreadable listing says nothing of efforts
        logger.debug(
            "models of %s could not be listed for their efforts", entry.name, exc_info=True
        )
        levels = None
    else:
        levels = {}
        for row in rows:
            known = levels.get(row.id, ())
            levels[row.id] = known + tuple(e for e in row.efforts or [] if e not in known)
    _listed[key] = (now, levels)
    return levels


async def declared_efforts(model_ref: str) -> tuple[str, ...] | None:
    """The levels a native chat bound to ``model_ref`` declares, lowest first: ``()`` for Auto,
    ``None`` where nothing says (see the module's docstring)."""
    ref = str(model_ref or "").strip()
    if not ref:
        return ()
    bound = _bound(ref)
    if bound is None:
        return None
    entry, model_id = bound
    wire = core_wire_efforts(entry.type, model_id)
    if wire is not None:
        return wire
    return (await _listing(entry) or {}).get(model_id) or None


async def turn_effort(model_ref: str, effort: str) -> str:
    """The level a native chat's turn sends: its stored ``effort`` when its model declares it, or
    when nothing says what the model takes; else none, so the turn runs at Default."""
    if not effort:
        return ""
    declared = await declared_efforts(model_ref)
    return effort if declared is None or effort in declared else ""


def _levels(levels: tuple[str, ...]) -> str:
    return levels[0] if len(levels) == 1 else f"{', '.join(levels[:-1])} and {levels[-1]}"


async def undeclared_effort(model_ref: str, effort: str) -> str | None:
    """Why a native chat bound to ``model_ref`` cannot be set to ``effort``, naming the model and
    the levels it declares; ``None`` when it can. Default (``""``) can always be set."""
    if not effort:
        return None
    declared = await declared_efforts(model_ref)
    if declared is None or effort in declared:
        return None
    ref = str(model_ref or "").strip()
    if not ref:
        return (
            f"Auto names no model, so it declares no reasoning efforts and {effort} can't be set. "
            "Pick a model that declares it, or leave the effort at Default."
        )
    bound = _bound(ref)
    model = f"“{bound[1]}” on {bound[0].name}" if bound is not None else f"“{ref}”"
    if not declared:
        return (
            f"{model} declares no reasoning efforts, so {effort} can't be set. Leave the effort "
            "at Default."
        )
    return f"{model} declares the reasoning efforts {_levels(declared)}, and {effort} isn't one."
