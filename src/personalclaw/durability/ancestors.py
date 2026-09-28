"""What this home last agreed on with each peer, record by record: the base of a three-way merge.

A sync reads a record's divergence against the version this home and the peer last both held
(:func:`conflicts.detect_conflicts`, :func:`reconcile.reconcile_entry`): this home's copy as agreed
and the peer's not means the peer edited it, and its edit is taken in; the peer's as agreed and
this home's not means this home edited it, and it stays; both moved is a conflict for review.

    {"peers": {"<peer machine id>": {"<entry id>": {"<entity id>": "<content sha>"}}},
     "published": {"<entry id>": {"<entity id>": ["<content sha>", …]}}}

**Per peer**, because an agreement is between two homes. One map every home wrote held whichever
agreement was published last: with a third home, one that had not pulled a record's edit yet read
the map moved past the copy it held, published that copy, and the others read it as an edit made
there and took it over the newer one.

**And what this home published**, the last few versions of each record, oldest first. A peer that
took this home's edit hands it back in its next export, and this home may have edited the record
again before it pulls that: the peer's copy is then this home's own earlier version, newer than
what the two last agreed on, and without this it read as an edit made there — every second edit in
a row a conflict to review.

**Machine-local**, under the sync root beside the pull cursor and the conflict queue, which the
home audit ignores, so it is never exported into the shards a pull rewrites. Nothing in it is any
other machine's to read: the copy in the shared registry was also the one object an encrypted sync
leaves readable, and put every record's id and a hash of its content there.

A sha is taken over what two homes compare of a row (:func:`conflicts.compared`). A home with no
file here, or a peer it has never agreed with, has no base: a record then merges by its store's
rule, and a divergence is never called a conflict.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

from personalclaw.atomic_write import atomic_json_write

logger = logging.getLogger(__name__)

_ANCESTORS_FILE = "ancestors.json"

#: How many versions of one record this home keeps as published, newest last. A peer hands back
#: a copy at most a few cycles old — it pulls every seq it has not seen before it exports — so this
#: covers every one it can, and a copy older than all of them reads as a conflict, never as an
#: edit taken over a newer one.
PUBLISHED_VERSIONS = 16


def _families(raw: Any) -> dict[str, dict[str, Any]]:
    """``entry id → {entity id → value}`` from *raw*, a malformed family dropped."""
    out: dict[str, dict[str, Any]] = {}
    for entry_id, family in (raw if isinstance(raw, dict) else {}).items():
        if isinstance(family, dict):
            out[str(entry_id)] = {str(k): v for k, v in family.items() if k and v}
    return out


class Ancestors:
    """This home's per-peer agreements and published versions, read once and written back when
    they change."""

    def __init__(self, sync_root: Path) -> None:
        self._path = Path(sync_root) / _ANCESTORS_FILE
        self._peers: dict[str, dict[str, dict[str, str]]] = {}
        self._published: dict[str, dict[str, list[str]]] = {}
        self._load()
        self._changed = False

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        """The file on disk. An absent, unreadable or malformed file, or one malformed family,
        degrades to nothing known there, never to a failed pull: without a base a record merges by
        its store's rule."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        peers = raw.get("peers")
        for peer, families in (peers if isinstance(peers, dict) else {}).items():
            self._peers[str(peer)] = {
                entry_id: {rid: str(sha) for rid, sha in family.items()}
                for entry_id, family in _families(families).items()
            }
        for entry_id, family in _families(raw.get("published")).items():
            self._published[entry_id] = {
                rid: [str(sha) for sha in shas if sha]
                for rid, shas in family.items()
                if isinstance(shas, list)
            }

    def of(self, peer_id: str, entry_id: str) -> dict[str, str]:
        """``entity id → content sha`` this home and *peer_id* last agreed on in *entry_id*. A copy,
        and empty where they have agreed on nothing yet."""
        return dict(self._peers.get(peer_id, {}).get(entry_id, {}))

    def record(self, peer_id: str, entry_id: str, shas: Mapping[str, str]) -> None:
        """Record *shas* — the records a reconcile of *peer_id*'s rows landed on the peer's version
        of (``ReconcileResult.new_ancestors``) — as what the two homes now agree on. A record the
        reconcile held under a conflict, or kept as this home edited it, is not among them, so its
        older agreement stays and the next divergence is still measured from it."""
        if not peer_id or not shas:
            return
        family = self._peers.setdefault(peer_id, {}).setdefault(entry_id, {})
        for rid, sha in shas.items():
            if rid and sha and family.get(rid) != sha:
                family[str(rid)] = str(sha)
                self._changed = True

    def published(self, entry_id: str) -> dict[str, list[str]]:
        """``entity id → the versions this home published of it, oldest first`` in *entry_id*."""
        return {rid: list(shas) for rid, shas in self._published.get(entry_id, {}).items()}

    def publish(self, entry_id: str, shas: Mapping[str, str]) -> None:
        """Record *shas* — every record of *entry_id* as this home is about to publish it — as the
        newest version of each, keeping the last :data:`PUBLISHED_VERSIONS`. A record no longer in
        *shas* is gone from this home, and its versions with it."""
        before = self._published.get(entry_id, {})
        after: dict[str, list[str]] = {}
        for rid, sha in shas.items():
            if not rid or not sha:
                continue
            versions = list(before.get(rid, []))
            if not versions or versions[-1] != sha:
                versions = [*versions, str(sha)][-PUBLISHED_VERSIONS:]
            after[str(rid)] = versions
        if after != before:
            self._published[entry_id] = after
            self._changed = True

    def save(self) -> None:
        """Write the file back, when a :meth:`record` or a :meth:`publish` changed it. Called once
        per pulled seq, before the cursor moves past it, and once per export."""
        if not self._changed:
            return
        atomic_json_write(self._path, {"peers": self._peers, "published": self._published})
        self._changed = False
