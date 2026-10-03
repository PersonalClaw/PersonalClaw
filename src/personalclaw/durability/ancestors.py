"""What this home last agreed on with each peer, record by record: the base of a three-way merge.

A sync reads a record's divergence against the version this home and the peer last both held
(:func:`conflicts.detect_conflicts`, :func:`reconcile.reconcile_entry`): this home's copy as agreed
and the peer's not means the peer edited it, and its edit is taken in; the peer's as agreed and
this home's not means this home edited it, and it stays; both moved is a conflict for review.

    {"peers": {"<peer machine id>": {"<entry id>": {"<entity id>": "<content sha>"}}},
     "published": {"<entry id>": {"<entity id>": ["<content sha>", …]}},
     "deleted": {"<entry id>": {"<entity id>": {"at": "<when>", "held": ["<content sha>", …],
                                               "by": "<machine id, empty for this home>"}}}}

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
home audit ignores, so it is never exported into the shards a pull rewrites. Never in the shared
registry either: that is the one object an encrypted sync leaves readable, and a copy there put
every record's id and a hash of its content in it. The agreements alone ride this home's sync
copies (:meth:`Ancestors.agreements`), inside the export and encrypted with it: a peer reads only
the newest copy, and needs them to tell this home's edit of its version from an edit made apart.

**And what this home deleted** (:class:`Deletion`): a record it held — published, or agreed on with
any machine — and no longer holds, noticed when it next publishes (:meth:`Ancestors.publish`), or
one another machine deleted that a sync applied here (:meth:`Ancestors.took_deletion`). Each keeps
every version of the record the deleting side held, which a copy of the record is weighed against
(:func:`conflicts.weigh_deletions`): one of those versions is older than the delete, and the record
stays deleted; any other is an edit the delete never saw, and a conflict for review. This home's
own deletes ride its sync copies (:meth:`Ancestors.deletions`), for :data:`DELETE_HORIZON_SECS`.
No store writes them: a record that stops being there, by whatever deleted it, is a delete. Only a
store that was read whole says so; a file that could not be read deletes nothing.

A sha is taken over what two homes compare of a row (:func:`conflicts.compared`). A home with no
file here, or a peer it has never agreed with, has no base: a record then merges by its store's
rule, and a divergence is never called a conflict.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from personalclaw.atomic_write import atomic_json_write

logger = logging.getLogger(__name__)

_ANCESTORS_FILE = "ancestors.json"

#: How many versions of one record this home keeps as published, newest last. A peer hands back
#: a copy at most a few cycles old — it pulls this home's newest copy before it exports — so this
#: covers every one it can, and a copy older than all of them reads as a conflict, never as an
#: edit taken over a newer one.
PUBLISHED_VERSIONS = 16

#: How long this home remembers a record as deleted, and how long its own delete rides its sync
#: copies: a machine away for longer than this never sees the delete, and its copy of the record
#: can come back. A peer reads only a machine's newest copy (``pull_engine``), and the copies before
#: it are removed (``durability.published``), so the delete has to be in the newest when that peer
#: comes back. A delete is an id, a time and a few hashes, so a long horizon costs nearly nothing.
DELETE_HORIZON_SECS = 90 * 24 * 60 * 60


@dataclass(frozen=True)
class Deletion:
    """A record a home no longer holds because it was deleted: here (``by`` empty), or on the
    machine ``by``, whose delete a sync applied here. ``held`` is every version of the record the
    deleting side held, which a copy of it is weighed against (:func:`conflicts.weigh_deletions`);
    ``at`` is when the delete was noticed, in the time this home's sync was given."""

    at: str
    held: tuple[str, ...] = ()
    by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "held": list(self.held), "by": self.by}

    @classmethod
    def from_dict(cls, raw: Any) -> Deletion | None:
        """The deletion *raw* records, or ``None`` for one that is not a record of one."""
        if not isinstance(raw, dict):
            return None
        held = raw.get("held")
        shas = [str(s) for s in held if isinstance(s, str) and s] if isinstance(held, list) else []
        return cls(
            at=str(raw.get("at", "") or ""),
            held=tuple(sorted(set(shas))),
            by=str(raw.get("by", "") or ""),
        )


def _moment(stamp: str) -> datetime | None:
    """*stamp*, an ISO-8601 time, as an aware datetime (UTC when it names no zone); ``None`` when
    it doesn't parse."""
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _past_the_horizon(at: str, now: str) -> bool:
    """Whether a delete noticed at *at* is older than :data:`DELETE_HORIZON_SECS` at *now*. Never,
    when either time doesn't parse: a delete is forgotten only by a time that says so."""
    start, end = _moment(at), _moment(now)
    if start is None or end is None:
        return False
    return (end - start).total_seconds() > DELETE_HORIZON_SECS


def _families(raw: Any) -> dict[str, dict[str, Any]]:
    """``entry id → {entity id → value}`` from *raw*, a malformed family dropped."""
    out: dict[str, dict[str, Any]] = {}
    for entry_id, family in (raw if isinstance(raw, dict) else {}).items():
        if isinstance(family, dict):
            out[str(entry_id)] = {str(k): v for k, v in family.items() if k and v}
    return out


class Ancestors:
    """This home's per-peer agreements, published versions and deletes, read once and written
    back when they change."""

    def __init__(self, sync_root: Path) -> None:
        self._path = Path(sync_root) / _ANCESTORS_FILE
        self._peers: dict[str, dict[str, dict[str, str]]] = {}
        self._published: dict[str, dict[str, list[str]]] = {}
        self._deleted: dict[str, dict[str, Deletion]] = {}
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
        for entry_id, family in _families(raw.get("deleted")).items():
            marks = {rid: Deletion.from_dict(mark) for rid, mark in family.items()}
            self._deleted[entry_id] = {rid: m for rid, m in marks.items() if m is not None}

    def of(self, peer_id: str, entry_id: str) -> dict[str, str]:
        """``entity id → content sha`` this home and *peer_id* last agreed on in *entry_id*. A copy,
        and empty where they have agreed on nothing yet."""
        return dict(self._peers.get(peer_id, {}).get(entry_id, {}))

    def agreements(self) -> dict[str, dict[str, dict[str, str]]]:
        """Everything this home last agreed on with each peer — ``peer → entry → entity → sha`` —
        as a sync's export carries it (``shards.export_shards(agreements=)``). A peer reads only
        this home's newest copy, so this is how it learns that this home took its version of a
        record before changing it again (:func:`reconcile._in_common`). A copy."""
        return {
            peer: {entry_id: dict(family) for entry_id, family in families.items()}
            for peer, families in self._peers.items()
        }

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

    def held(self, entry_id: str) -> dict[str, list[str]]:
        """``entity id → every version of it this home held`` in *entry_id*, sorted: the versions
        it published, the ones it agreed on with any machine, and a deleted record's. A record that
        is in none of these never was here."""
        out: dict[str, set[str]] = {}
        for rid, shas in self._published.get(entry_id, {}).items():
            out.setdefault(rid, set()).update(shas)
        for families in self._peers.values():
            for rid, sha in families.get(entry_id, {}).items():
                out.setdefault(rid, set()).add(sha)
        for rid, mark in self._deleted.get(entry_id, {}).items():
            out.setdefault(rid, set()).update(mark.held)
        return {rid: sorted(shas) for rid, shas in out.items()}

    def deleted(self, entry_id: str) -> dict[str, Deletion]:
        """``entity id → its delete`` for each record of *entry_id* deleted here or taken in as
        another machine's delete."""
        return dict(self._deleted.get(entry_id, {}))

    def deletions(self) -> dict[str, dict[str, Deletion]]:
        """This home's own deletes, ``entry id → entity id → delete``, as a sync's export carries
        them (``shards.export_shards(deletions=)``). A delete taken in from another machine is not
        among them: that machine's copies carry it, and sent on from here it came back to that
        machine as this home's delete, which kept a record it brought back deleted."""
        out: dict[str, dict[str, Deletion]] = {}
        for entry_id, marks in self._deleted.items():
            own = {rid: mark for rid, mark in marks.items() if not mark.by}
            if own:
                out[entry_id] = own
        return out

    def took_deletion(self, entry_id: str, marks: Mapping[str, Deletion]) -> None:
        """Record *marks* — the records of *entry_id* a reconcile deleted here because another
        machine deleted them (``ReconcileResult.deleted_there``) — as that machine's deletes."""
        if not marks:
            return
        self._deleted.setdefault(entry_id, {}).update(marks)
        self._changed = True

    def publish(
        self, entry_id: str, shas: Mapping[str, str], *, now: str = "", unread: Iterable[str] = ()
    ) -> None:
        """Record *shas* — every record of *entry_id* as this home is about to publish it — as the
        newest version of each, keeping the last :data:`PUBLISHED_VERSIONS`, and what is gone.

        A record this home held — published, or agreed on with any machine — and no longer holds
        is deleted here, at *now*, and keeps every version this home held of it (:class:`Deletion`):
        no store says when it deletes a record, so this is how a sync knows it did. One a sync took
        in as another machine's delete stays that machine's. A record this home holds again is not
        deleted any more. *unread* are records this home holds that could not be read this time
        (a file that does not parse, a folder that could not be listed): what is known of them
        stays as it was, and none of them reads as deleted."""
        unread = set(unread)
        before = self._published.get(entry_id, {})
        after: dict[str, list[str]] = {}
        for rid, sha in shas.items():
            if not rid or not sha:
                continue
            versions = list(before.get(rid, []))
            if not versions or versions[-1] != sha:
                versions = [*versions, str(sha)][-PUBLISHED_VERSIONS:]
            after[str(rid)] = versions
        for rid in unread & set(before):
            after.setdefault(rid, list(before[rid]))
        marks = self._deleted.get(entry_id, {})
        agreed: dict[str, set[str]] = {}
        for families in self._peers.values():
            for rid, sha in families.get(entry_id, {}).items():
                agreed.setdefault(rid, set()).add(sha)
        updated = {rid: mark for rid, mark in marks.items() if rid not in shas}
        for rid in (set(before) | set(agreed) | set(marks)) - set(shas) - unread:
            mark = marks.get(rid)
            held = set(before.get(rid, ())) | agreed.get(rid, set())
            held |= set(mark.held) if mark else set()
            updated[rid] = Deletion(
                at=mark.at if mark else now,
                held=tuple(sorted(held)),
                by=mark.by if mark else "",
            )
        # A deleted record's agreements are in its delete now, and forgotten with it.
        for families in self._peers.values():
            family = families.get(entry_id, {})
            for rid in set(family) & set(updated):
                del family[rid]
                self._changed = True
        if after != before:
            self._published[entry_id] = after
            self._changed = True
        if updated != marks:
            if updated:
                self._deleted[entry_id] = updated
            else:
                self._deleted.pop(entry_id, None)
            self._changed = True

    def forget_old_deletes(self, now: str) -> None:
        """Forget every delete older than :data:`DELETE_HORIZON_SECS` at *now*. Called after a
        sync's copy reached the store, so a delete rides at least one copy before it goes."""
        for entry_id in list(self._deleted):
            marks = self._deleted[entry_id]
            kept = {rid: m for rid, m in marks.items() if not _past_the_horizon(m.at, now)}
            if kept != marks:
                self._changed = True
                if kept:
                    self._deleted[entry_id] = kept
                else:
                    del self._deleted[entry_id]

    def save(self) -> None:
        """Write the file back, when a :meth:`record`, a :meth:`publish`, a :meth:`took_deletion`
        or a :meth:`forget_old_deletes` changed it. Called once per pulled seq, before the cursor
        moves past it, and once per export."""
        if not self._changed:
            return
        atomic_json_write(
            self._path,
            {
                "peers": self._peers,
                "published": self._published,
                "deleted": {
                    entry_id: {rid: mark.to_dict() for rid, mark in sorted(marks.items())}
                    for entry_id, marks in sorted(self._deleted.items())
                },
            },
        )
        self._changed = False
