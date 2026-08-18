"""SDK: the trigger-store contract — ``TriggerStoreProvider`` + the row/entity types.

A trigger-store app imports these from ``personalclaw.sdk.triggers`` (never from the core modules
directly) to contribute a STORE of trigger ROWS — a shared or team automation backend. It implements
:class:`TriggerStoreProvider` (list/get/upsert/delete + ``changed_on_disk`` change-notify), returns
:class:`LoadedTrigger` rows built from :class:`Trigger` entities, and registers through the
``trigger`` provider type (``providers/registry.py::TriggerTypeHandler``).

**This is not ``personalclaw.sdk.trigger_source``.** That contract contributes the STIMULUS — a live
observer that pushes events onto the bus. This one contributes the RULE — a passive store of trigger
definitions. If you are writing something with ``start``/``stop`` that notices things happening, you
want ``trigger_source``; if you are writing something that answers "which automations exist", you
want this.

**Rows, never execution.** Core does all firing, under all of its own gates. Two consequences worth
building against:

* Stamp ``Trigger.author`` with the username of whoever created each row. Core arms and fires ONLY
  rows whose author matches the local owner; everyone else's render read-only on the Automations
  page. An unattributed row (``author=""``) reads as the local owner's, so a multi-user store that
  omits the field will have every machine arming every row — attribute your rows.
* A read that raises is logged and skipped for that pass, so an outage in your backend costs your
  rows and never stops the owner's local automations. Prefer serving a cached answer to raising, and
  answer ``changed_on_disk()`` honestly so core knows when to re-read.
* **``upsert`` must really persist ``next_fire_at``, and ``get`` must show it.** When core fires one
  of your rows it writes the row's next schedule back to YOUR store — that is what makes an
  app-served trigger fire once rather than every tick — and it then re-reads the row to check the
  timestamp moved. A store that accepts the write and keeps the old value, or that cannot read the
  row back, is **quarantined**: its rows stop being armed for the rest of the process (they still
  render) and a warning is logged. So a deliberately read-only store is honest and safe — it simply
  never arms — but a store that pretends to write is the one shape that costs the owner a fire.
  ``run_count``, ``last_fired_at``, ``state`` and the health fields arrive through the same write;
  round-trip them too, or the owner's budget, spacing and autopause gates read a permanent zero.
* A row whose id also exists in the owner's local ``triggers.json`` is **not armed** — the local row
  wins, because one identity cannot hold two schedules. Both stay visible on the Automations page so
  the collision is reportable. Namespace your ids.
"""

from personalclaw.triggers.models import Issue, Trigger, parse_trigger  # noqa: F401
from personalclaw.triggers.provider import TriggerStoreProvider  # noqa: F401
from personalclaw.triggers.store import LoadedTrigger  # noqa: F401

__all__ = [
    "TriggerStoreProvider",
    "LoadedTrigger",
    "Trigger",
    "Issue",
    "parse_trigger",
]
