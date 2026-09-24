"""The knowledge PUSH half — ``sharing_policy: shared`` items flow OUT (TSE2-4).

WORK-CONTAINERS §1.6 shipped the vocabulary (:mod:`personalclaw.knowledge.project_scope`:
``project_id`` / ``run_id`` / ``sharing_policy``) and the *inbound* provider contract
(:meth:`~personalclaw.knowledge_providers.base.KnowledgeProvider.ingest`). What was missing
was the other direction: an item the owner marked ``shared`` had nowhere to go, so "shared"
meant only "visible in another of MY projects" and never "visible to the team store".

This module is the outbound half, and it is the ONLY place the gate lives:

* **The gate is the policy, read once.** ``private`` — the default — never leaves the
  machine. A provider cannot widen that, because a provider is never asked about a private
  item; :func:`push_shared_item` returns before it reaches one.
* **Attribution rides, internal bookkeeping does not.** The push carries the four
  shared-store keys (``contributor`` + the §1.6 scope trio) and nothing else. An item's
  ``claims`` / ``citations`` / ``conflicts`` / ``extraction`` blobs are the harness's own
  workings, and the safe direction for anything crossing to a third-party store is "stays
  home" — the same reading of un-declared intent that makes ``private`` the default policy.
* **``contributor`` is the SHIPPED name**, not a new one: it is the column
  :mod:`personalclaw.vector_memory` already attributes semantic memory with (TSE §2.3) and
  the field :func:`personalclaw.identity.contributor_label` already renders. A second
  vocabulary for "who wrote this" is exactly what the shared-store conformance contract
  exists to prevent.
* **Never raises.** A shared knowledge write must succeed whether or not a team store is
  installed, reachable, or willing — the push is a courtesy on top of a local write that
  has already happened.

The way BACK in is the same four keys: :func:`inbound_attribution` is what the source
engine carries from a polled item onto the row it writes, and :func:`fence_source` is how
the contributor rides the **existing** federated-source label into a prompt
(:meth:`personalclaw.knowledge.session_brief.SessionBrief.render`, which already fences).
The label says whose text it is; the fence is what makes it un-actable. Both, always —
``docs/architecture/shared-store-provider-conformance.md`` clause 2.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw import identity
from personalclaw.knowledge.project_scope import (
    PROJECT_ID_KEY,
    RUN_ID_KEY,
    SHARING_POLICY_KEY,
    SharingPolicy,
    as_metadata_dict,
    item_scope,
)

logger = logging.getLogger(__name__)

#: Who wrote the item. The SAME key ``vector_memory`` attributes semantic memory with and
#: ``identity.contributor_label`` renders — reused, never re-minted.
CONTRIBUTOR_KEY = "contributor"

#: The keys a shared item carries in BOTH directions: out on a push, back in on a poll.
#: Everything else in an item's ``file_metadata`` is local bookkeeping and stays home.
ATTRIBUTION_KEYS = (CONTRIBUTOR_KEY, PROJECT_ID_KEY, RUN_ID_KEY, SHARING_POLICY_KEY)

#: The native library is not a peer to push to. Pushing an item the native store just wrote
#: back into the native store would be a loop that ends in a duplicate, and the "team store"
#: the push exists for is by definition somebody else's.
NATIVE_PROVIDER = "native"


def is_shared(metadata: Any) -> bool:
    """Whether *metadata* declares ``sharing_policy: shared``.

    Delegates to :func:`~personalclaw.knowledge.project_scope.item_scope`, so the closed
    enum's fail-closed coercion (an unrecognised value reads as ``private``) decides here
    too. A second policy reader with its own parsing is how a privacy filter becomes
    decorative.
    """
    _project_id, _run_id, policy = item_scope(metadata)
    return policy is SharingPolicy.SHARED


def contributor_of(metadata: Any) -> str:
    """The ``contributor`` handle on an item's metadata, or ``""`` (the owner's own)."""
    return str(as_metadata_dict(metadata).get(CONTRIBUTOR_KEY, "") or "").strip()


def outbound_metadata(metadata: Any, *, contributor: str) -> dict[str, str]:
    """The metadata a push carries: the scope trio plus who contributed it.

    Narrow on purpose (see the module docstring): this is what crosses to a store the
    harness does not own, so it is an allowlist of the four attribution keys rather than the
    item's whole ``file_metadata``. An allowlist cannot acquire a leak by a later key being
    added to an item; a passthrough acquires one by default.
    """
    meta = as_metadata_dict(metadata)
    out: dict[str, str] = {}
    for key in (PROJECT_ID_KEY, RUN_ID_KEY, SHARING_POLICY_KEY):
        value = str(meta.get(key, "") or "").strip()
        if value:
            out[key] = value
    who = str(contributor or "").strip()
    if who:
        # Empty stays ABSENT rather than becoming `""`. An unattributed record reads as the
        # local owner's (the shipped `belongs_to` bargain), and writing a blank contributor
        # onto the wire would make "no username configured" look like a deliberate claim.
        out[CONTRIBUTOR_KEY] = who
    return out


def inbound_attribution(metadata: Any) -> dict[str, str]:
    """The attribution keys that survive a polled item's trip back into the library.

    The engine writes these onto the row it creates, which is what lets a foreign
    contribution be LABELLED afterwards: the ``provider`` column already says which
    federated store a row came from, but a shared store has many contributors, so without
    this the row could only ever be attributed to the store, not to the teammate.

    Same allowlist as :func:`outbound_metadata`, read in the other direction — a provider
    cannot smuggle arbitrary keys into an item's metadata by putting them on a poll result.
    """
    meta = as_metadata_dict(metadata)
    out: dict[str, str] = {}
    for key in ATTRIBUTION_KEYS:
        value = str(meta.get(key, "") or "").strip()
        if value:
            out[key] = value
    return out


def fence_source(base: str, *, contributor: str, owner: str | None = None) -> str:
    """The federated-source label with the contributor riding on it.

    ``"knowledge:decision"`` stays exactly that for the owner's own items and becomes
    ``"knowledge:decision (from teammate)"`` for a foreign contribution — one label, the
    shipped :func:`~personalclaw.identity.contributor_label` form, appended to the source
    label the fence already carried. Not a second label beside it: the fence has one
    ``source=`` and a reader comparing two provenance strings is a reader that will believe
    the wrong one.

    *owner* is resolved once by the caller when it is labelling a list, so a ten-item brief
    does not read the config ten times.
    """
    who = str(contributor or "").strip()
    if not who:
        return base
    resolved = identity.current_username() if owner is None else owner
    return f"{base}{identity.contributor_label(who, resolved)}"


async def push_shared_item(item: Any, *, contributor: str | None = None) -> list[str]:
    """Offer a ``shared`` knowledge item to every registered provider's outbound half.

    *item* is a :class:`~personalclaw.knowledge_providers.base.KnowledgeItem` whose
    ``metadata`` carries the §1.6 scope. Returns the sorted names of the providers that
    ACCEPTED it (a provider returning its own record from
    :meth:`~personalclaw.knowledge_providers.base.KnowledgeProvider.push`), which is what
    lets the run that wrote the item see the push happened instead of assuming it.

    Returns ``[]`` — pushing nothing — when the item is not ``shared``. That is the gate,
    and it is here rather than in a provider so that no provider can widen it.

    Never raises: the local write has already committed, and a team store that is down must
    not fail the knowledge item that was written next to it.
    """
    metadata = getattr(item, "metadata", None) or {}
    if not is_shared(metadata):
        return []
    who = identity.current_username() if contributor is None else str(contributor or "")
    payload = _outbound_item(item, contributor=who)
    accepted: list[str] = []
    for provider in _push_targets():
        name = str(getattr(provider, "name", "") or "")
        try:
            record = await provider.push(payload)
        except Exception:  # noqa: BLE001 — a provider fault must not fail the local write
            logger.info("knowledge push to provider %r failed", name, exc_info=True)
            continue
        if record is None:
            # Declined, not failed. A provider with no outbound half returns the ABC's
            # default, and reporting that as a success would tell the owner their item
            # reached a team store that never saw it.
            logger.debug("provider %r declined the shared knowledge push", name)
            continue
        accepted.append(name)
    return sorted(accepted)


# ── internals ───────────────────────────────────────────────────────────────


def _push_targets() -> list[Any]:
    """Registered providers a shared item may be offered to — never the native library."""
    try:
        from personalclaw.knowledge_providers.registry import list_providers

        return [p for p in list_providers() if str(getattr(p, "name", "")) != NATIVE_PROVIDER]
    except Exception:  # noqa: BLE001 — no registry means no team store, which is the default
        logger.debug("knowledge provider registry unavailable for push", exc_info=True)
        return []


def _outbound_item(item: Any, *, contributor: str) -> Any:
    """A copy of *item* carrying only the outbound attribution metadata."""
    from personalclaw.knowledge_providers.base import KnowledgeItem

    return KnowledgeItem(
        id=str(getattr(item, "id", "") or ""),
        title=str(getattr(item, "title", "") or ""),
        content=str(getattr(item, "content", "") or ""),
        source_id=str(getattr(item, "source_id", "") or ""),
        metadata=outbound_metadata(getattr(item, "metadata", None), contributor=contributor),
    )
