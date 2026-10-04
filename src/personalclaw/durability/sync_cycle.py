"""One full sync cycle: pull → merge → export → push (DAS-6c-ii-i).

The orchestrator that assembles every piece built in 6c-i … 6c-ii-h into the loop §4.1 names —
``pull → merge-import remote rows → export local union → push`` — against a resolved transport:

    registry = read the shared registry.json from the remote     (transport.pull of REGISTRY_KEY)
    pull_from_peers(transport, home, registry, cursor,            # 6c-ii-e + the 6c-ii-h db_merger
                    db_merger=make_db_merger(home), ancestors=…)  # merged against each peer's base
    ancestors.publish(…) per record store                         # what it publishes, what is gone
    export_shards(home, out, for_sync=True, agreements=…,         # 6b + 6c-ii-g (DB copies),
                  deletions=…)                                    # and this home's deletes
    unless the export holds what the store's newest copy does     # durability.published
        publish_export(transport, out, registry, outbox, …)       # 6c-ii-f (+ CAS registry bump)
    retire_superseded(transport, …)                               # the copies a newer one replaced

Everything below the orchestration was already unit-tested in isolation; this module owns only
the wiring and the read-the-registry step. A publish that did not land — its push not delivered,
or its registry write lost — fails the cycle as a push: nothing of this machine's reached the
other machines, and a run that said otherwise read "Last sync just now" while it sent nothing.
A removal of old copies that fails does not fail one: it is said in the report, and the next
cycle tries again — but a key the transport refused to remove is named in the report's
``refused``, as any refused key is, since only a link someone put in this machine's own folder of
the store is refused there. It is clock-free (``now`` is passed in) and does not
own scheduling — the ``stale_after_secs`` staleness window and the "is sync enabled / which
transport" resolution live in the service layer (6c-ii-j) that calls this. A transport error at
any step is caught and reported in the :class:`SyncCycleReport`, never raised, so one bad cycle
never kills the durability service loop. A key the transport refused (``KeysRefused``) is named in
the report's ``refused`` too, whichever step it stopped.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.ancestors import Ancestors
from personalclaw.durability.conflicts import ID_KEYED_MERGES, ConflictQueue
from personalclaw.durability.cursor import Cursor
from personalclaw.durability.db_merge import make_db_merger
from personalclaw.durability.home_paths import LinkInTheWay, home_path
from personalclaw.durability.outbox import Outbox
from personalclaw.durability.published import Published, export_digest
from personalclaw.durability.pull_engine import PullReport, pull_from_peers
from personalclaw.durability.push_engine import (
    PushReport,
    holds_copy,
    publish_export,
    retire_superseded,
)
from personalclaw.durability.registry import REGISTRY_KEY, Registry, machine_prefix, seq_of_key
from personalclaw.durability.shards import export_shards, left_out_sentence, refused_sentence
from personalclaw.sync_transports.base import KeysRefused, RemoteRef, SyncTransportProvider

logger = logging.getLogger(__name__)


def _refused_by(exc: Exception) -> dict[str, str]:
    """The keys the transport refused, with why, when *exc* is its refusal."""
    return exc.refused if isinstance(exc, KeysRefused) else {}


@dataclass
class SyncCycleReport:
    """What one cycle did, honestly — including the step that failed, if any."""

    ok: bool = True
    pulled: PullReport | None = None
    pushed: PushReport | None = None
    error: str = ""
    #: Which step stopped a failed cycle, as a code the service puts into words
    #: (``service.PROBLEMS``): ``passphrase`` (encryption is on and none is stored),
    #: ``salt``, ``pull`` or ``push``. Empty when the cycle did not fail.
    failure: str = ""
    skipped: str = ""
    # roll-ups for the service log / doctor
    rows_added: int = 0
    #: Rows this home had that took in another machine's edit.
    rows_updated: int = 0
    rows_removed: int = 0
    seq_published: int = 0
    #: The seq that stands as this machine's newest copy when the export held nothing new, so
    #: none was sent (``published.Published.stands``); 0 when one was.
    unchanged_since: int = 0
    #: This machine's copies removed from the store because a newer one replaced them, by seq.
    copies_removed: list[int] = field(default_factory=list)
    #: Why removing them failed, when it did; the next cycle tries again.
    removal_failed: str = ""
    conflicts: int = 0  # both-sides-edited divergences queued for review
    #: This machine's files the export for the other machines could not carry, by path, with why
    #: (``shards.Read``): said in the report, never dropped in silence.
    left_out: dict[str, str] = field(default_factory=dict)
    #: The paths another machine named outside what a sync may write, and the links this home
    #: holds that a sync never writes through, with why (``pull_engine.PullReport.refused``): none
    #: was written, and the report says so. With them,
    #: a key the transport refused (``KeysRefused``) where it stopped the cycle: its registry,
    #: its salt, or this machine's push, which the cycle's error says.
    refused: dict[str, str] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        if not self.ok:
            return f"error: {self.error}"
        sent = (
            f"nothing new to send, seq {self.unchanged_since} stands"
            if self.unchanged_since
            else f"published seq {self.seq_published}"
        )
        base = f"+{self.rows_added} ~{self.rows_updated} -{self.rows_removed} rows; {sent}"
        if self.copies_removed:
            n = len(self.copies_removed)
            base += f"; removed {n} older {'copy' if n == 1 else 'copies'}"
        if self.removal_failed:
            base += f"; older copies not removed ({self.removal_failed})"
        if self.conflicts:
            base += f"; {self.conflicts} conflict(s) queued"
        if self.refused:
            base += f"; {refused_sentence(self.refused)}"
        if self.left_out:
            base += f"; {left_out_sentence(self.left_out, what='synced')}"
        return base


def _record_published(ancestors: Ancestors, home: Path, now: str) -> None:
    """Record each record of every store a sync merges by id, as this home is about to export it
    (:meth:`ancestors.Ancestors.publish`): a peer that takes one of these versions and hands it
    back is then read as behind, not as having edited it. And what is gone: a record this home
    held and no longer holds is deleted here, and the export that follows carries the delete. A
    store that is not there, or could not be read, is left as it was known."""
    for entry in inv.INVENTORY:
        if (
            reconcile.handles_kind(entry.kind)
            and entry.merge in ID_KEYED_MERGES
            and not entry.machine_local
        ):
            _forget_the_retired_side_log(home, entry)
            held = reconcile.held_shas(home, entry)
            if held is not None:
                shas, unknown = held
                known = ancestors.held(entry.id).keys() | ancestors.deleted(entry.id).keys()
                # What could not be read, and what stays on each machine, is never a delete.
                unread = {rid for rid in known if unknown(rid) or inv.stays_here(entry, rid)}
                ancestors.publish(entry.id, shas, now=now, unread=unread)
    ancestors.save()


#: The delete side-log the task stores kept before a sync noticed deletes for itself
#: (``ancestors.Ancestors.publish``). A home that kept one still holds it, nothing reads it now,
#: and as a file of the store a copy would carry it: removed before the export.
_RETIRED_SIDE_LOG = "_tombstones.jsonl"


def _forget_the_retired_side_log(home: Path, entry: inv.StateEntry) -> None:
    if entry.kind != inv.KIND_JSON_ENTITY_DIR:
        return
    try:
        home_path(home, f"{entry.path}/{_RETIRED_SIDE_LOG}").unlink(missing_ok=True)
    except LinkInTheWay:
        # Nothing is removed through a link the home holds (`durability.home_paths`); the pull
        # names it.
        return
    except OSError:
        logger.debug("sync cycle: could not remove %s/%s", entry.path, _RETIRED_SIDE_LOG)


def read_registry(transport: SyncTransportProvider) -> Registry:
    """Read and parse the shared ``registry.json`` from the remote.

    Absent (a brand-new sync root) → :meth:`Registry.absent`, an empty registry marked not
    there, so the first machine publishes from scratch and a swap creates it. A listing/pull
    error propagates to the caller, which records it as a failed cycle.
    """
    refs = transport.list_remote(REGISTRY_KEY)
    if not refs:
        return Registry.absent()
    # list_remote(prefix) is a prefix match; take the exact key if present.
    exact = [r for r in refs if r.key == REGISTRY_KEY] or [RemoteRef(key=REGISTRY_KEY)]
    objs = transport.pull(exact)
    for obj in objs:
        if obj.key == REGISTRY_KEY:
            return Registry.loads(obj.data)
    return Registry.absent()


def _retire(
    transport: SyncTransportProvider,
    listed: list[RemoteRef],
    *,
    sync_root: Path,
    outbox: Outbox,
    published: Published,
    registry: Registry,
    self_id: str,
    newest: int,
    now: str,
    report: SyncCycleReport,
) -> None:
    """Remove this machine's copies a newer one replaced (``push_engine.retire_superseded``), and
    the bookkeeping kept for them. A failure is said in *report*, never raised: the copies are
    removed next cycle, and nothing of the sync depends on it. A key the transport refused is
    named in ``refused`` too, which the run reports."""
    landed = dict(published.landed)
    if newest not in landed:
        # Sent before this machine kept the times: the registry says when it landed.
        entry = registry.machines.get(self_id)
        if entry is not None and entry.seq == newest and entry.last_export_at:
            landed[newest] = entry.last_export_at
    try:
        report.copies_removed, _objects = retire_superseded(
            transport, listed, self_id=self_id, newest=newest, landed=landed, now=now
        )
    except Exception as exc:  # noqa: BLE001 — a removal that fails must not fail the sync
        logger.warning("sync cycle: removing older copies failed (%s)", exc, exc_info=True)
        report.removal_failed = str(exc) or type(exc).__name__
        report.refused.update(_refused_by(exc))
        return
    removed = set(report.copies_removed)
    kept = {newest} | {
        seq
        for ref in listed
        if (seq := seq_of_key(self_id, ref.key)) is not None and seq < newest and seq not in removed
    }
    published.keep_only(kept)
    published.save(sync_root)
    outbox.forget_below(transport.name, min(kept))


def run_sync_cycle(
    transport: SyncTransportProvider,
    home: Path,
    *,
    self_id: str,
    now: str = "",
    encrypt: str = "auto",
) -> SyncCycleReport:
    """Run one full pull→merge→export→push cycle against ``transport``.

    ``self_id`` is this machine's id (``shards.machine_id(home)``); ``now`` is the cycle's time,
    ISO-8601 — when a copy it sends landed, and what the store's older copies are measured
    against (``durability.published``). ``encrypt`` is the ``durability.sync_encrypt``
    tri-state (``auto``/``on``/``off``) resolved against the transport's own default (§4.4).
    Never raises: any transport failure lands in the report so the service loop survives.
    """
    report = SyncCycleReport()
    sync_root = Path(home) / "sync"
    cursor = Cursor(sync_root)
    outbox = Outbox(sync_root)
    conflict_queue = ConflictQueue(home)
    ancestors = Ancestors(sync_root)

    # ── ENCRYPTION ──────────────────────────────────────────────────────────
    # Resolved ONCE per cycle, before anything moves: the salt round-trip and the Argon2id
    # stretch happen here rather than per object. A setup failure (no passphrase, no salt)
    # is FAIL-CLOSED — the cycle is skipped entirely rather than falling back to plaintext,
    # which would upload the user's whole state in the clear to storage they chose to encrypt.
    codec = None
    try:
        from personalclaw.durability.crypto import (
            MissingPassphrase,
            SyncEncryptionError,
            codec_for,
        )

        codec = codec_for(transport, setting=encrypt)
    except SyncEncryptionError as exc:
        logger.warning("sync cycle: encryption unavailable (%s)", exc)
        report.ok = False
        report.error = f"encryption: {exc}"
        report.failure = "passphrase" if isinstance(exc, MissingPassphrase) else "salt"
        return report
    except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the service loop
        # The salt is read from the remote through the transport's listing and read, and a
        # transport says a listing or read that fails by raising. That is the failed read the
        # pull step below reports, so it is said the same way; nothing moves without the codec.
        logger.warning("sync cycle: reading the encryption salt failed (%s)", exc, exc_info=True)
        report.ok = False
        report.error = f"pull: {exc}"
        report.failure = "pull"
        report.refused.update(_refused_by(exc))
        return report

    # ── PULL + MERGE ────────────────────────────────────────────────────────
    try:
        registry = read_registry(transport)
        report.pulled = pull_from_peers(
            transport,
            home,
            registry,
            cursor,
            self_id=self_id,
            db_merger=make_db_merger(home),
            queue=conflict_queue,
            ancestors=ancestors,
            now=now,
            codec=codec,
        )
        report.rows_added = report.pulled.added
        report.rows_updated = report.pulled.updated
        report.rows_removed = report.pulled.removed
        report.conflicts = report.pulled.conflicts
        report.refused = report.pulled.refused
    except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the service loop
        logger.warning("sync cycle: pull failed (%s)", exc, exc_info=True)
        report.ok = False
        report.error = f"pull: {exc}"
        report.failure = "pull"
        report.refused.update(_refused_by(exc))
        return report

    # ── EXPORT (with DB copies) + PUSH, unless the store's newest copy holds it ─
    published = Published.load(sync_root)
    newest = 0
    try:
        listed = transport.list_remote(machine_prefix(self_id))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _record_published(ancestors, home, now)
            report.left_out = export_shards(
                home,
                out,
                for_sync=True,
                agreements=ancestors.agreements(),
                deletions=ancestors.deletions(),
            ).left_out
            digest = export_digest(out)
            standing = registry.seq_of(self_id)
            if published.stands(
                digest,
                remote_seq=standing,
                remote_has_it=holds_copy(listed, self_id, standing),
            ):
                report.unchanged_since = newest = standing
            else:
                report.pushed = publish_export(
                    transport,
                    out,
                    registry,
                    outbox,
                    self_id=self_id,
                    now=now,
                    reload_registry=lambda: read_registry(transport),
                    codec=codec,
                )
                if report.pushed.registry_committed:
                    newest = report.seq_published = report.pushed.seq
                    published.record(newest, digest, now=now)
                    published.save(sync_root)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync cycle: push failed (%s)", exc, exc_info=True)
        report.ok = False
        report.error = f"push: {exc}"
        report.failure = "push"
        report.refused.update(_refused_by(exc))
        return report
    if report.pushed is not None and not report.pushed.registry_committed:
        # Nothing of it reached the other machines: a push the store did not take, or a seq the
        # registry does not name. Its objects wait under a seq no peer reads, for the next cycle.
        report.ok = False
        report.error = f"push: {report.pushed.detail or report.pushed.push_outcome}"
        report.failure = "push"
        return report
    # The store holds this machine's deletes now: one past the horizon has ridden a copy, and goes.
    ancestors.forget_old_deletes(now)
    ancestors.save()
    if newest and transport.removes_old_copies:
        _retire(
            transport,
            listed,
            sync_root=sync_root,
            outbox=outbox,
            published=published,
            registry=registry,
            self_id=self_id,
            newest=newest,
            now=now,
            report=report,
        )
    return report
