"""The flat trigger-STORE registry (TEAM-SHARED-ENTITIES §3 — TSE-4).

Mirrors :mod:`personalclaw.trigger_sources.registry` and ``sync_transports/registry.py``: the
``trigger`` provider-type handler (``providers/registry.py::TriggerTypeHandler``) registers an
installed store here on app enable and removes it on disable. A dict at module scope, not a class —
the same shape the three sibling registries already use, so this is not a fourth idiom.

**Registered ≠ trusted with execution.** A registered store is READ, and that is all: §3 says a
trigger provider "contributes trigger rows, never execution". Nothing here hands a provider a fire,
a payload, a run or a credential, and :func:`personalclaw.triggers.provider.armable` drops every row
the provider attributes to somebody other than the owner before the service sees it.

**A faulty provider store must not stop local automations.** Every read below is wrapped: a store
that raises, hangs on its own socket or returns garbage is logged and skipped for that pass, leaving
``triggers.json`` rows arming exactly as they do with no app installed. The opposite choice —
letting a team backend's outage silence the owner's own morning automation — is the failure mode
this degradation exists to prevent, and it is the same fail-open direction ``trigger_sources``
takes for a store fault on the enable path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: name → the registered store. Registration is by the PROVIDER's name so a disable can remove
#: exactly what an enable added, and so two apps cannot silently shadow each other.
_STORES: dict[str, Any] = {}


def register_trigger_store(name: str, store: Any) -> None:
    """Register an installed ``trigger`` provider's store under ``name``. Replaces on re-enable."""
    if not name:
        raise ValueError("a trigger store must be registered under a non-empty provider name")
    _STORES[name] = store


def unregister_trigger_store(name: str) -> bool:
    """Remove one registered store. Returns whether it was there."""
    return _STORES.pop(name, None) is not None


def registered_stores() -> dict[str, Any]:
    """A copy of the registry, so a caller iterating it cannot be mutated out from under."""
    return dict(_STORES)


def provider_rows() -> list[Any]:
    """Every row every registered provider store serves, as ``LoadedTrigger``-shaped rows.

    Rows only — no ordering guarantee across providers and no de-duplication against
    ``triggers.json``: an id collision between a local row and a provider's row is the provider's
    bug to fix, and silently dropping one of the two would hide it. Each store is read
    independently so one bad provider costs its own rows and nobody else's.
    """
    out: list[Any] = []
    for name, store in list(_STORES.items()):
        try:
            rows = store.load()
        except Exception:  # noqa: BLE001 - a provider outage must not stop local automations
            logger.warning("trigger provider %r could not be read; skipping its rows", name)
            continue
        if not isinstance(rows, list):
            logger.warning("trigger provider %r returned a non-list from load(); skipping", name)
            continue
        out.extend(rows)
    return out
