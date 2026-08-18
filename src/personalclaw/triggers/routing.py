"""Write-back routing — a provider-served row reschedules into the store that served it (TSE-5).

TSE-4 built the read half of the ``trigger`` provider seam and stopped, deliberately, at a named
gap: a registered store's rows were LISTED but never ARMED, because the arm path PERSISTS.
``service.tick`` writes ``next_fire_at``, ``run_count`` and the health rollup back with
``store.upsert(...)``, and ``store`` there was the native one. So arming a provider's row had
exactly two possible outcomes, both bad:

* **Duplicate identity** — the reschedule lands in ``triggers.json``, so one trigger id now lives
  in two stores. Worse than untidy: the native copy then WINS every later read (see
  :meth:`RoutingTriggerStore.load`), so the team's row silently forks into a local one and the
  shared file stops describing what actually runs.
* **Fire storm** — the reschedule lands nowhere, so ``next_fire_at`` stays in the past and the row
  is due again on the very next tick, forever. Not a missed fire: an unbounded one.

This module closes both. Two pieces, and the split is the design:

1. :func:`route_upsert` / :func:`route_delete` — the WRITE decision, called from
   :class:`personalclaw.triggers.store.TriggerStore`'s own ``upsert``/``delete``. It lives there
   rather than at the arm sites because the arm path is not the only writer: the gateway's fire-
   outcome recorder, the failure-dedup path and the autopause path each construct their OWN
   ``TriggerStore`` and write a fired row back through it. Routing at seven arm sites would have
   left every one of those minting the duplicate id this exists to prevent. Funnelling through the
   native store's write path means there is no second spelling of "persist a trigger" to forget.
2. :class:`RoutingTriggerStore` — the READ merge, substituted for the store in ``tick`` and ``boot``
   so a provider's rows reach :func:`personalclaw.triggers.provider.armable` at all.

**Every routed write is verified.** The serving store is re-read and the storm-relevant field
(``next_fire_at``) compared against what was asked for. A provider whose write raised, vanished or
silently did not land is **quarantined** for the rest of the process: its rows stop being armed
(they still render, and the log says why), so a store that cannot persist a schedule costs at most
ONE extra fire instead of one per tick forever.

**Scope, stated rather than implied.** ``tick`` and ``boot`` — the clock path — merge provider rows.
The ``file``/``idle``/``web_watch``/``view`` poll loops still read the bare native store, so their
provider-served rows render without arming. That is TSE-4's position, narrowed from "every provider
row" to "provider rows of a polled kind". Their
dispatch is the gateway's, and giving them a routed row without routing that dispatch would be the
duplicate-identity write again, one layer out.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.triggers.registry import provider_rows_by_store, registered_stores

logger = logging.getLogger(__name__)

#: Provider names whose rows are withheld from the ARM path for the rest of this process, because a
#: routed write to them could not be verified. Process-global on purpose: the routing store is
#: constructed per tick and the native store per write, so a per-instance set would evaporate
#: immediately and the storm it exists to stop would simply resume on the next tick.
_QUARANTINE: dict[str, str] = {}


def quarantine_report() -> dict[str, str]:
    """A copy of ``name → reason`` per quarantined provider. For a doctor surface or a test."""
    return dict(_QUARANTINE)


def clear_quarantine() -> None:
    """Forget every quarantine. Called by tests; a real recovery is a gateway restart."""
    _QUARANTINE.clear()


def _quarantine(name: str, reason: str) -> None:
    """Withhold ``name``'s rows from the arm path, once, loudly."""
    if name in _QUARANTINE:
        return
    _QUARANTINE[name] = reason
    logger.warning(
        "trigger provider %r cannot persist a reschedule (%s) — withholding its rows from the arm "
        "path for the rest of this process so none of them can fire on a frozen schedule. They "
        "still render on the Automations page.",
        name,
        reason,
    )


def _row_id(row: Any) -> str:
    """The trigger id of a ``LoadedTrigger``-shaped row, or ``""`` when it has none."""
    return str(getattr(getattr(row, "trigger", None), "id", "") or "")


def _next_fire_of(row: Any) -> str:
    """A row's persisted ``next_fire_at``, reading through a ``LoadedTrigger`` wrapper or not."""
    return str(getattr(getattr(row, "trigger", row), "next_fire_at", "") or "")


def serving_store(trigger_id: str) -> tuple[str, Any] | None:
    """The ``(name, store)`` of the registered provider serving ``trigger_id``, or None.

    Returns None immediately when nothing is registered, which is every single-user install: the
    caller then behaves exactly as it did before this module existed, at the cost of one dict
    emptiness check and no I/O.

    🔴 A QUARANTINED PROVIDER IS STILL THE SERVING STORE. Measured, and it is the whole reason this
    does not take ``skip=_QUARANTINE``: the storm quarantine trips on the FIRST routed write of a
    tick (``tick`` persists the reschedule) and the SECOND write of the same tick (``run_count`` and
    ``last_fired_at``) then found no serving store and fell through to ``triggers.json`` — so the
    guard against a fire storm was itself minting the duplicate identity, and the rescued local copy
    went on firing on the schedule the provider had refused to keep. Quarantine withholds rows from
    the arm READ (:meth:`RoutingTriggerStore.load`); it never re-homes a write.
    """
    if not trigger_id or not registered_stores():
        return None
    for name, store, rows in provider_rows_by_store():
        for row in rows:
            if _row_id(row) == trigger_id:
                return (name, store)
    return None


def _read_back(name: str, store: Any, trigger_id: str) -> Any:
    """The row as the serving store now holds it, or None (absent, or unreadable)."""
    try:
        return store.get(trigger_id)
    except Exception:  # noqa: BLE001 - unreadable reads as "cannot verify"; the caller decides
        logger.debug("trigger provider %r raised on get(%r) during write-back", name, trigger_id)
        return None


def _target(trigger_id: str, native: Any) -> tuple[str, Any] | None:
    """The provider to route a write for ``trigger_id`` to, or None to let ``native`` have it.

    ``native`` wins a collision. If the local ``triggers.json`` already holds this id then the local
    row is the one being armed (:meth:`RoutingTriggerStore.load` drops the provider's copy), so its
    write must stay local — routing it away would update a row nothing reads.
    """
    target = serving_store(trigger_id)
    if target is None:
        return None
    try:
        if native is not None and native.get(trigger_id) is not None:
            return None
    except Exception:  # noqa: BLE001 - an unreadable native store is the native store's problem
        logger.debug("could not check the native store for %r while routing", trigger_id)
    return target


def route_upsert(trigger: Any, *, native: Any = None) -> Any:
    """Persist ``trigger`` into the provider that served it — or None to let the caller write.

    None means "no registered provider serves this id", which is the answer on every single-user
    install and for every brand-new row. Anything else is the stored row, and the caller must NOT
    also write it locally: that is the duplicate identity.

    Never raises. A provider fault must not abort a tick that is also rescheduling the owner's own
    local automations, so a raise or an unverifiable write quarantines the provider instead.
    """
    trigger_id = str(getattr(trigger, "id", "") or "")
    target = _target(trigger_id, native)
    if target is None:
        return None
    name, store = target
    intended = str(getattr(trigger, "next_fire_at", "") or "")
    try:
        saved = store.upsert(trigger)
    except Exception:  # noqa: BLE001 - see the docstring
        _quarantine(name, "upsert() raised")
        return trigger
    persisted = _read_back(name, store, trigger_id)
    if persisted is None:
        _quarantine(name, "the upserted row could not be read back")
    elif _next_fire_of(persisted) != intended:
        _quarantine(name, f"next_fire_at stayed {_next_fire_of(persisted)!r}, wanted {intended!r}")
    return saved if saved is not None else trigger


def route_delete(trigger_id: str, *, native: Any = None) -> bool | None:
    """Remove ``trigger_id`` from the provider that served it — or None to let the caller delete.

    A retirement that did not take leaves an elapsed ``next_fire_at`` on a live row, the same storm
    by another route, so a delete that left the row behind quarantines the provider too.
    """
    target = _target(trigger_id, native)
    if target is None:
        return None
    name, store = target
    try:
        gone = bool(store.delete(trigger_id))
    except Exception:  # noqa: BLE001 - a fault must not abort the tick
        _quarantine(name, "delete() raised")
        return False
    if _read_back(name, store, trigger_id) is not None:
        _quarantine(name, "delete() left the row in place")
    return gone


class RoutingTriggerStore:
    """The native store's rows PLUS every registered provider's, so the arm path can see them.

    Reads only. Writes are delegated straight to the native store, because that is where the routing
    decision lives (:func:`route_upsert`) — putting a second copy of it here would be two spellings
    of one rule, and the gateway's writers never come through this wrapper anyway.

    Duck-typed rather than declared as a
    :class:`~personalclaw.triggers.provider.TriggerStoreProvider` for the same reason
    :func:`~personalclaw.triggers.provider.armable` is: the service is driven in tests with doubles
    that implement ``load()`` and nothing else, and a subclass would have to promise a
    ``base_dir: Path`` a double does not have.

    Not a cache. ``load()`` re-reads everything every time, because that is what the arm path
    expects of a store ("another process may have written it").
    """

    def __init__(self, native: Any) -> None:
        self._native = native
        #: id → (provider name, store) for the PROVIDER-served rows of the last ``load()``. Used by
        #: :meth:`get` so a read of a provider row does not have to re-scan every store.
        self._origin: dict[str, tuple[str, Any]] = {}

    @property
    def native(self) -> Any:
        """The wrapped native store, for a caller that must reach the local file explicitly."""
        return self._native

    @property
    def base_dir(self) -> Any:
        """The NATIVE store's sidecar root — deliberately not the serving provider's.

        ``tick`` roots the claim store here, and a claim describes a LOCAL run: the provider
        contributes the rule and never the execution, so its backend must not accumulate this
        machine's in-flight state. A shared folder collecting every machine's claims would also make
        ``overlap`` refuse one owner's fire because another machine happened to hold the trigger.
        """
        return getattr(self._native, "base_dir", None)

    def load(self) -> list[Any]:
        """Native rows, then every non-quarantined provider's rows minus id collisions.

        Native FIRST and native WINS. An id served by both stores cannot be armed as two rows —
        whichever copy was rescheduled, the other is now stale under the same identity — so the
        provider's copy is dropped from the arm path and the collision logged. It stays visible on
        the Automations page, which reads :func:`~personalclaw.triggers.provider.all_rows` and
        does not de-duplicate, so the conflict is reported rather than hidden.
        """
        rows = list(self._native.load())
        origin: dict[str, tuple[str, Any]] = {}
        seen = {_row_id(row) for row in rows}
        for name, store, provider_rows in provider_rows_by_store(skip=_QUARANTINE):
            for row in provider_rows:
                tid = _row_id(row)
                if not tid:
                    logger.warning("trigger provider %r served a row with no id; skipping", name)
                    continue
                if tid in seen:
                    logger.warning(
                        "trigger id %r is served by both the local store and trigger provider %r; "
                        "arming the LOCAL row only — two rows under one identity cannot both hold "
                        "the schedule. Rename one of them.",
                        tid,
                        name,
                    )
                    continue
                seen.add(tid)
                origin[tid] = (name, store)
                rows.append(row)
        self._origin = origin
        return rows

    def get(self, trigger_id: str) -> Any:
        """One row by id, from whichever store holds it. Native takes precedence."""
        native = self._native.get(trigger_id)
        if native is not None:
            return native
        target = self._origin.get(trigger_id) or serving_store(trigger_id)
        if target is None:
            return None
        name, store = target
        return _read_back(name, store, trigger_id)

    def list_triggers(self, *, kind: str = "", include_broken: bool = True) -> list[Any]:
        """The flat LISTING view across both stores — foreign rows included, as a listing must."""
        out = list(self._native.list_triggers(kind=kind, include_broken=include_broken))
        local_ids = {str(getattr(t, "id", "") or "") for t in out}
        for _name, _store, provider_rows in provider_rows_by_store(skip=_QUARANTINE):
            for row in provider_rows:
                trigger = getattr(row, "trigger", None)
                tid = _row_id(row)
                if trigger is None or not tid or tid in local_ids:
                    continue
                if kind and str(getattr(trigger, "kind", "")) != kind:
                    continue
                if not include_broken and not getattr(row, "ok", True):
                    continue
                out.append(trigger)
        return out

    def changed_on_disk(self) -> bool:
        """Has ANY contributing store been touched since it was last read?

        Or-ed rather than native-only: ``tick`` reports this as ``store_changed``, and a backend
        that gained a row while the local file sat still is exactly what the change-notification is
        for. A provider that raises answers "no" for this pass — an unreadable store contributes no
        rows either, so claiming a change would be a lie about rows nobody can see.
        """
        if bool(getattr(self._native, "changed_on_disk", lambda: False)()):
            return True
        for name, store in registered_stores().items():
            if name in _QUARANTINE:
                continue
            try:
                if bool(store.changed_on_disk()):
                    return True
            except Exception:  # noqa: BLE001 - a provider outage must not stop local automations
                logger.debug("trigger provider %r raised on changed_on_disk()", name)
        return False

    # ── writes: delegated, because the native store is where routing happens ─────────────────

    def upsert(self, trigger: Any) -> Any:
        return self._native.upsert(trigger)

    def delete(self, trigger_id: str) -> bool:
        return bool(self._native.delete(trigger_id))


def routed(store: Any) -> Any:
    """``store`` with provider rows merged into its reads — or ``store`` itself.

    Returned unchanged when no provider store is registered, which is every single-user install: the
    wrapper would then add an indirection that merges an empty list. Also returned unchanged when it
    is already a :class:`RoutingTriggerStore`, so a nested call cannot double-wrap.
    """
    if isinstance(store, RoutingTriggerStore) or not registered_stores():
        return store
    return RoutingTriggerStore(store)
