"""One full sync cycle: pull → merge → export → push (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-i).

The orchestrator that assembles every piece built in 6c-i … 6c-ii-h into the loop §4.1 names —
``pull → merge-import remote rows → export local union → push`` — against a resolved transport:

    registry = read the shared registry.json from the remote     (transport.pull of REGISTRY_KEY)
    pull_from_peers(transport, home, registry, cursor,            # 6c-ii-e + the 6c-ii-h db_merger
                    db_merger=make_db_merger(home))
    export_shards(home, out, include_databases=True)              # 6b + 6c-ii-g (DB copies)
    publish_export(transport, out, registry, outbox, …)           # 6c-ii-f (+ CAS registry bump)

Everything below the orchestration was already unit-tested in isolation; this module owns only
the wiring and the read-the-registry step. It is clock-free (``now`` is passed in) and does not
own scheduling — the ``stale_after_secs`` staleness window and the "is sync enabled / which
transport" resolution live in the service layer (6c-ii-j) that calls this. A transport error at
any step is caught and reported in the :class:`SyncCycleReport`, never raised, so one bad cycle
never kills the durability service loop.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from personalclaw.durability.cursor import Cursor
from personalclaw.durability.db_merge import make_db_merger
from personalclaw.durability.outbox import Outbox
from personalclaw.durability.pull_engine import PullReport, pull_from_peers
from personalclaw.durability.push_engine import PushReport, publish_export
from personalclaw.durability.registry import REGISTRY_KEY, Registry
from personalclaw.durability.shards import export_shards
from personalclaw.sync_transports.base import RemoteRef, SyncTransportProvider

logger = logging.getLogger(__name__)


@dataclass
class SyncCycleReport:
    """What one cycle did, honestly — including the step that failed, if any."""

    ok: bool = True
    pulled: PullReport | None = None
    pushed: PushReport | None = None
    error: str = ""
    skipped: str = ""
    # roll-ups for the service log / doctor
    rows_added: int = 0
    rows_removed: int = 0
    seq_published: int = 0

    @property
    def detail(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        if not self.ok:
            return f"error: {self.error}"
        return f"+{self.rows_added} -{self.rows_removed} rows; published seq {self.seq_published}"


def read_registry(transport: SyncTransportProvider) -> Registry:
    """Read and parse the shared ``registry.json`` from the remote.

    Absent (a brand-new sync root) → an empty registry, so the first machine publishes from
    scratch. A listing/pull error propagates to the caller, which records it as a failed cycle.
    """
    refs = transport.list_remote(REGISTRY_KEY)
    if not refs:
        return Registry.empty()
    # list_remote(prefix) is a prefix match; take the exact key if present.
    exact = [r for r in refs if r.key == REGISTRY_KEY] or [RemoteRef(key=REGISTRY_KEY)]
    objs = transport.pull(exact)
    for obj in objs:
        if obj.key == REGISTRY_KEY:
            return Registry.loads(obj.data)
    return Registry.empty()


def run_sync_cycle(
    transport: SyncTransportProvider,
    home: Path,
    *,
    self_id: str,
    manifest_sha: str = "",
    now: str = "",
) -> SyncCycleReport:
    """Run one full pull→merge→export→push cycle against ``transport``.

    ``self_id`` is this machine's id (``shards.machine_id(home)``); ``manifest_sha`` is the sha of
    the export we're about to publish (the caller computes it after export, or passes "" — it is
    a cheap change-probe field, not load-bearing). Never raises: any transport failure lands in
    the report so the service loop survives.
    """
    report = SyncCycleReport()
    sync_root = Path(home) / "sync"
    cursor = Cursor(sync_root)
    outbox = Outbox(sync_root)

    # ── PULL + MERGE ────────────────────────────────────────────────────────
    try:
        registry = read_registry(transport)
        report.pulled = pull_from_peers(
            transport, home, registry, cursor, self_id=self_id, db_merger=make_db_merger(home)
        )
        report.rows_added = report.pulled.added
        report.rows_removed = report.pulled.removed
    except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the service loop
        logger.warning("sync cycle: pull failed (%s)", exc, exc_info=True)
        report.ok = False
        report.error = f"pull: {exc}"
        return report

    # ── EXPORT (with DB copies) + PUSH ────────────────────────────────────────
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_shards(home, out, include_databases=True)
            report.pushed = publish_export(
                transport,
                out,
                registry,
                outbox,
                self_id=self_id,
                manifest_sha=manifest_sha,
                now=now,
                reload_registry=lambda: read_registry(transport),
            )
            report.seq_published = report.pushed.seq if report.pushed.registry_committed else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync cycle: push failed (%s)", exc, exc_info=True)
        report.ok = False
        report.error = f"push: {exc}"
        return report
    return report
