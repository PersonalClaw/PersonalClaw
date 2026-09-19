"""Who a notification is FOR — the addressee (MULTI-TENANCY-ENTITY `TSE2-5`).

``DashboardState.notify`` is "THE single delivery choke point for every emitter", and until
now it had exactly one destination: *this* dashboard. That is correct while every row in every
store belongs to the owner. It stops being correct the moment a shared store contributes rows
somebody else owns — which `TSE2-1`/`TSE2-2`/`TSE2-3` made real, stamping ``owner_username``
onto run-ledger rows, entity records and inbox items. A shared inbox now renders a teammate's
item (``GET /api/inbox/owners``) and, when that item wanted attention, fired a toast at the
local owner about work that was never theirs.

So notifications get the one thing they lacked: an **addressee**.

**It reuses ``owner_username``; it does not mint a second owner vocabulary.** The addressee is
the same attribution slug :func:`personalclaw.identity.current_username` produces and
:class:`personalclaw.inbox.InboxItem` / :class:`personalclaw.workflows.models.WorkflowRun`
already carry. A notification is addressed to whoever owns the thing it is about, so the
emitter supplies the item's own ``owner_username`` and nothing new has to be invented, agreed
on, or kept in sync.

**Foreign-addressed means visible-but-not-fired, and this is the trigger posture exactly.**
TEAM-SHARED-ENTITIES §2.2 settled the shape for foreign trigger rows and
:mod:`personalclaw.triggers.ownership` implements it: the harness "arms and fires ONLY the
owner's triggers … enforced structurally (a foreign row cannot tick, not 'is skipped')", while
the row stays *visible* in the management surface. Two reads, not one filter:
:func:`personalclaw.triggers.provider.armable` is the FIRE read and drops foreign rows, and
``list_triggers``/``all_rows`` is the LISTING read and keeps them. This module is the same
split for the attention path — :func:`is_locally_addressed` is the FIRE predicate, and the
notification log stays the listing.

Every semantic below is lifted from :mod:`personalclaw.triggers.ownership` rather than
re-decided, because two spellings of one rule are two rules:

* **Empty addressee reads as the owner's, and that is not a loophole.** Every note written
  before this field existed carries none, and treating those as foreign would silence every
  notification on every existing install — the loudest possible regression from a field nobody
  asked for. It also matches ``InboxItem.belongs_to`` and ``WorkflowRun.belongs_to``: with no
  attribution recorded there is nobody else the note could be for. An emitter that wants its
  note treated as foreign must SAY who it is for.
* **Empty OWNER fires everything.** A single-user install that never re-onboarded has no
  username, so it cannot meaningfully call anything foreign, and behaves exactly as it did.
* **Not a credential.** An addressee is an attribution string (``identity``'s first semantic:
  "it answers 'who wrote this row', not 'who may'"). Withholding a foreign-addressed note is a
  scoping decision about whose attention this machine spends, not an authorization check. An
  emitter that addresses its own note elsewhere silences *itself* — it cannot reach anybody
  else's screen, and there is no permission it can escape by lying.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, TypeVar

logger = logging.getLogger(__name__)

#: Preserves the caller's own element type through :func:`locally_addressed` — the digest
#: filters ``list[dict[str, Any]]`` and must get that back, not a widened ``Mapping``.
_NoteT = TypeVar("_NoteT", bound=Mapping[str, Any])

#: The note field naming WHO a notification is for. A named constant because the emitter
#: (``inbox.emit_attention_item``), the choke point (``DashboardState.notify``) and any
#: delivery backend that routes on it all have to agree on one spelling.
ADDRESSEE_KEY = "addressee"

#: The typed reason a foreign-addressed note is not fired here, written onto the note as
#: ``withheld_reason`` and carried out over ``GET /api/notifications``. Mirrors
#: :data:`personalclaw.triggers.ownership.FOREIGN_AUTHOR` — same posture, same shape of name,
#: so somebody grepping for one finds the other. A NAMED constant for the reason that one is:
#: two spellings of the same refusal read as two different mechanisms to whoever greps later.
FOREIGN_ADDRESSEE = "foreign_addressee"

#: Where the note went instead, when a delivery provider took it. ``""`` means nothing could
#: route it — see :func:`personalclaw.notification_providers.registry.deliver_to_addressee`.
ROUTED_TO_KEY = "routed_to"

#: The note field carrying :data:`FOREIGN_ADDRESSEE`.
WITHHELD_REASON_KEY = "withheld_reason"


def owner_username() -> str:
    """The owner's attribution username, or ``""`` when unset.

    Never raises and never caches, for the reasons
    :func:`personalclaw.triggers.ownership.owner_username` gives: ``current_username`` already
    degrades an unreadable config to ``""``, and caching would make a rename take effect only
    after a restart.
    """
    try:
        from personalclaw.identity import current_username

        return current_username()
    except Exception:  # noqa: BLE001 - attribution must never break delivery
        logger.debug("notification_addressing: username unreadable — every note reads as local")
        return ""


def addressee_of(note: Mapping[str, Any] | None) -> str:
    """The normalized addressee on *note*, or ``""``.

    Normalized HERE rather than at each caller so the predicate and the note's own recorded
    value cannot disagree about case or whitespace.
    """
    if not note:
        return ""
    return str(note.get(ADDRESSEE_KEY, "") or "").strip().lower()


def is_locally_addressed(note: Mapping[str, Any] | None, *, owner: str | None = None) -> bool:
    """Whether this harness may FIRE *note* — the fire predicate.

    ``owner`` is accepted so a caller filtering many notes resolves the username once;
    omitted, it is read here. Mirrors
    :func:`personalclaw.triggers.ownership.is_owner_authored` clause for clause: an empty
    addressee or an empty owner answers ``True``, and only a *disagreement* between two
    non-empty strings answers ``False``.
    """
    own = (owner if owner is not None else owner_username()).strip().lower()
    addressee = addressee_of(note)
    if not own or not addressee:
        return True
    return addressee == own


def locally_addressed(notes: Iterable[_NoteT], *, owner: str | None = None) -> list[_NoteT]:
    """*notes* minus every one addressed to somebody else. Order preserved.

    The batch form of :func:`is_locally_addressed`, mirroring
    :func:`personalclaw.triggers.ownership.owner_authored`. Used by anything that fires a
    BATCH of notes rather than one — the digest drain is the first such caller.
    """
    own = (owner if owner is not None else owner_username()).strip().lower()
    return [n for n in notes if is_locally_addressed(n, owner=own)]
