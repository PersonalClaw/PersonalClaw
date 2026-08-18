"""The trigger-store seam — the ``trigger`` provider type's contract (TEAM-SHARED-ENTITIES §3).

§3 asks for a ``TriggerStore`` interface extracted from the unified service's persistence
("list/get/upsert/delete + change-notification"), with the native implementation wrapping
``triggers.json`` and all of its preserved conventions. That native implementation already exists
and keeps its shipped name: :class:`personalclaw.triggers.store.TriggerStore`. This module is the
ABSTRACTION it now satisfies, so a provider app can supply rows from somewhere else — a team
backend, a synced file, a fixture — without the service knowing.

``trigger`` IS NOT ``trigger_source`` — read this before merging them
--------------------------------------------------------------------

The two types sit adjacent in ``PROVIDER_TYPES`` and are easy to conflate. They contribute
different halves of an automation, and neither can substitute for the other:

* ``trigger_source`` (AUTOMATION-SUBSTRATE AUTO-A4, :mod:`personalclaw.trigger_sources`) supplies
  the **stimulus**. It is a live observer the app runs: ``start``/``stop``, pushing typed
  :class:`~personalclaw.trigger_sources.base.SourceEvent` payloads onto the one event bus under a
  namespaced source, which the owner's OWN ``kind: event`` trigger rows then match. The app decides
  WHEN something happened; the owner still decides what to do about it.
* ``trigger`` (this module, TEAM-SHARED-ENTITIES §3) supplies the **rule**. It is a passive STORE of
  trigger ROWS — definitions, with their kind, schedule, gates and action. The app decides WHICH
  automations exist; it never observes anything and it never executes anything.

So the axes are orthogonal: source is push, store is pull; source is events, store is rows; a source
is live machinery with a lifecycle, a store is persistence with a change notification. A single app
may reasonably register as both, and a trigger row served by a ``trigger`` provider may well be
bound to an event emitted by a ``trigger_source`` provider — which is precisely why collapsing them
into one type would leave no way to say that.

What a ``trigger`` provider may and may not do
----------------------------------------------

**It contributes rows, never execution.** §3: "A trigger provider contributes trigger rows, never
execution — the local ``TriggerService`` does all firing". The provider is asked for rows and is
never handed a fire, a payload, a run or a credential. Every gate the local machine applies —
capability allowlist, budget, quiet hours, kill switch, injection screen — applies unchanged,
because the row travelled but the fire path did not.

**And only the OWNER's rows ever arm.** A shared store legitimately contains other people's
triggers. :func:`armable` is the ONE row source every arm/fire selection in this package reads, and
it drops foreign rows before returning them, so a foreign row is never in the candidate set rather
than being declined by a gate later — see :mod:`personalclaw.triggers.ownership` for why that
distinction is the whole point.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.triggers.ownership import owner_authored

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import LoadedTrigger


class TriggerStoreProvider(ABC):
    """The persistence contract the trigger service talks to.

    Named ``…Provider`` to match every other app-facing contract in the tree
    (``TriggerSourceProvider``, ``InboxProvider``, ``SyncTransport``) and to leave the concrete
    name :class:`personalclaw.triggers.store.TriggerStore` where its callers already point — a
    rename there would be churn across the gateway and CLI for no semantic gain.

    Five methods, which is exactly §3's list. ``load`` is the read primitive (it returns rows WITH
    their parse issues, because a broken row must stay visible), ``list_triggers`` is the flat
    listing view, and ``changed_on_disk`` is the change-notification: "another writer has touched
    this store since you last read it". A provider backed by a network store answers it from an
    etag or a version counter rather than an mtime — the contract is the QUESTION, not the file
    stat.
    """

    @property
    @abstractmethod
    def base_dir(self) -> Path:
        """Root for this store's sidecars (claims, watch state).

        Part of the contract rather than an implementation detail: ``service.tick`` derives the
        claim-store root from it precisely so a store rooted at a temp dir cannot write runtime
        state into the real home.
        """

    @abstractmethod
    def load(self) -> list["LoadedTrigger"]:
        """Every row, INCLUDING broken ones, each carrying its parse issues."""

    @abstractmethod
    def list_triggers(self, *, kind: str = "", include_broken: bool = True) -> list["Trigger"]:
        """The rows as a flat list, optionally filtered by kind.

        A LISTING view — it is what a management surface renders, so it includes foreign rows.
        The arm path must use :func:`armable` instead.
        """

    @abstractmethod
    def get(self, trigger_id: str) -> "LoadedTrigger | None":
        """One row by id, or None."""

    @abstractmethod
    def upsert(self, trigger: "Trigger") -> "Trigger":
        """Insert or replace one row, read-modify-write under whatever lock the impl owns."""

    @abstractmethod
    def delete(self, trigger_id: str) -> bool:
        """Remove one row. Returns whether it was there."""

    @abstractmethod
    def changed_on_disk(self) -> bool:
        """Change-notify: has another writer touched this store since this instance read it?"""


def armable(store: Any) -> list["Trigger"]:
    """The rows the local service may arm and fire — **the arm path's only row source**.

    Two filters, deliberately fused into one function so neither can be applied without the other:

    * broken rows are dropped (``parse_trigger`` already forced them ``enabled=False``, so they
      were inert anyway — dropping them here just stops seven call sites each re-deriving the
      ``row.ok`` check), and
    * foreign rows are dropped (``author != owner``), which is §2.2's structural requirement.

    Every arm/fire selection inside this package calls this — the clock walk and boot re-arm in
    ``service``, the ``file``/``idle``/``web_watch``/``view`` poll loops, and the chain lookups.
    That is what makes "a foreign row cannot tick" a property of the code rather than a promise:
    nothing downstream is ever handed one, so nothing downstream can decide to fire it.

    Duck-typed on ``store`` (not annotated as :class:`TriggerStoreProvider`) because the service is
    called with test doubles that implement ``load()`` and nothing else, and tightening the
    annotation here would type-error every one of them without making a single fire safer.

    🔴 Reads the store it is GIVEN — deliberately not :func:`all_rows`, and that is what makes a
    provider's row armable rather than what stops it. The arm path PERSISTS (``service.tick`` writes
    ``next_fire_at``, ``run_count`` and the health rollup back through ``store.upsert``), so the row
    source and the write destination MUST be the same object: read from one store and write to
    another and you get either two rows under one id in two files, diverging, or a
    ``next_fire_at`` that never advances — due again on the very next tick, a fire storm and not
    a missed fire. So TSE-5 wired both ends: ``tick`` and ``boot`` substitute
    :func:`personalclaw.triggers.routing.routed` for their store before calling this, which is how a
    provider's row gets here at all, and :meth:`personalclaw.triggers.store.TriggerStore.upsert`
    routes that row's write back to the provider that served it — at the store, not at the arm
    sites, because the gateway's fire-outcome recorder writes a fired row back too. Handed a native
    store — every poll loop, every chain lookup, every test double — this returns exactly the local
    rows it always did.
    """
    return owner_authored(_ok_triggers(store.load()))


def all_rows(store: Any) -> list[Any]:
    """Every row from the native store PLUS every registered ``trigger`` provider's rows.

    The LISTING read: broken rows and foreign rows are both included, because a management surface
    that hid either would leave the user unable to see what exists — and unlike the arm path it does
    NOT drop an id collision between the two stores, because the page that could show the user their
    conflict is the last place to hide it. Contrast :func:`armable`, the ARM read, which is handed a
    :func:`personalclaw.triggers.routing.routed` store so that whatever it returns can also be
    written back where it came from.
    """
    from personalclaw.triggers.registry import provider_rows

    rows = list(store.load())
    rows.extend(provider_rows())
    return rows


def _ok_triggers(rows: Any) -> list[Any]:
    """The parseable rows' triggers. Broken rows load ``enabled=False`` anyway; dropping them here
    stops seven arm sites each re-deriving the ``row.ok`` check."""
    return [r.trigger for r in rows if getattr(r, "ok", True)]
