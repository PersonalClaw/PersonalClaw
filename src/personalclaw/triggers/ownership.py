"""Who may ARM a trigger — the owner filter (TEAM-SHARED-ENTITIES §2.2 — TSE-4).

Once a ``trigger`` provider can contribute rows (see :mod:`personalclaw.triggers.provider`), the
store is no longer guaranteed to hold only rows this machine's owner wrote. §2.2 states the rule
without wiggle room: the harness **"arms and fires ONLY the owner's triggers — the filter is
``author == owner username`` at arm time, enforced structurally (a foreign row cannot tick, not
'is skipped')"**.

**Structural means the foreign row is never in the candidate set**, not that a gate declines it
later. :func:`personalclaw.triggers.provider.armable` is the one row source every arm/fire
selection in this package reads, and it applies :func:`owner_authored` before returning. A row
belonging to somebody else therefore never reaches ``due_ids``, ``by_id``, a poll loop's output or
a chain lookup — there is no code path that could decide to fire it, because nothing downstream
ever holds it.

**Empty author reads as the owner's, and that is not a loophole.** ``Trigger.author`` is new; every
row written before it existed has ``""``. Treating those as foreign would silently stop every
automation on every existing install the moment this lands — the loudest possible regression from a
field nobody asked for. It is also the honest answer, matching
:meth:`personalclaw.tasks.models.Task.belongs_to` and :func:`personalclaw.identity.current_username`
("empty degrades to today's behavior"): with no attribution recorded there is nobody else the row
could belong to. A provider that wants its rows treated as foreign must SAY whose they are, which is
the same bargain the tasks seam already struck.

**Not a credential.** ``author`` is an attribution string (``identity`` module's first semantic:
"it answers 'who wrote this row', not 'who may'"). This filter is a scoping decision about whose
work this machine performs, not an authorization check — a foreign row is refused because running
somebody else's automation on the owner's machine is wrong by intent, not because the string was
authenticated.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: The typed reason a foreign row is refused, for a ledger row or a log line. A NAMED constant
#: because two spellings of the same refusal read as two different mechanisms to whoever greps for
#: it later.
FOREIGN_AUTHOR = "foreign_author"


def owner_username() -> str:
    """The owner's attribution username, or ``""`` when unset.

    Never raises and never caches: ``identity.current_username`` already degrades an unreadable
    config to ``""``, and caching would make a rename take effect only after a restart. Read ONCE
    per arm pass by :func:`owner_authored`, not once per row, so a tick pays one config read rather
    than one per trigger.
    """
    try:
        from personalclaw.identity import current_username

        return current_username()
    except Exception:  # noqa: BLE001 - attribution must never break the arm path
        logger.debug("triggers.ownership: username unreadable — treating every row as the owner's")
        return ""


def is_owner_authored(trigger: Any, *, owner: str | None = None) -> bool:
    """Whether the local service may arm and fire ``trigger``.

    ``owner`` is accepted so a caller filtering many rows resolves the username once; omitted, it
    is read here. Duck-typed on purpose: the service accepts test doubles, and a double without an
    ``author`` attribute must read as the owner's rather than crash the tick.
    """
    own = (owner if owner is not None else owner_username()).strip().lower()
    author = str(getattr(trigger, "author", "") or "").strip().lower()
    if not own or not author:
        return True
    return author == own


def owner_authored(triggers: Iterable[Any], *, owner: str | None = None) -> list[Any]:
    """``triggers`` minus every row somebody else wrote. Order preserved."""
    own = (owner if owner is not None else owner_username()).strip().lower()
    return [t for t in triggers if is_owner_authored(t, owner=own)]
