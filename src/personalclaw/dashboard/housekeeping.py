"""The dashboard's periodic housekeeping: the abandoned-upload sweep and the SEL retention prune.

`start_dashboard` starts each loop once as a background task. Each runs once at startup, then on
its own cadence, off the event loop, until the gateway's shutdown event is set.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# How often to trim the security-event log (append-only + high-rate). Runs once
# at startup, then on this cadence, off the event loop (prune rewrites the file).
_SEL_PRUNE_INTERVAL_SECS = 6 * 60 * 60  # 6 hours


_UPLOAD_SWEEP_INTERVAL_SECS = 60 * 60  # hourly


async def upload_sweep_loop() -> None:
    """Periodically delete abandoned resumable-upload session dirs (partial parts).

    A partial 2 GB upload the client never finishes would otherwise pin disk
    forever. Sweeps sessions idle past the store TTL, at startup then hourly."""
    from personalclaw import shutdown_event
    from personalclaw.dashboard.handlers.files import _upload_dir
    from personalclaw.uploads.store import UploadStore

    store = UploadStore(Path(_upload_dir()) / ".parts")
    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=_UPLOAD_SWEEP_INTERVAL_SECS)
                return
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            swept = await asyncio.get_running_loop().run_in_executor(None, store.sweep)
            if swept:
                logger.info("Upload sweep removed %d abandoned session(s)", swept)
        except Exception:
            logger.debug("upload sweep skipped", exc_info=True)


async def sel_prune_loop() -> None:
    """Periodically apply the SEL audit log's retention.

    The live file's SIZE is bounded by the log's own rotation (`sel._ROTATE_BYTES`), which moves
    it into `sel_archive/`; this is the AGE half — rows past retention leave the live file and
    expired rotated files leave the archive. Once at startup, then every few hours, on an
    executor thread (the prune rewrites the live file)."""
    from personalclaw import shutdown_event

    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=_SEL_PRUNE_INTERVAL_SECS)
                return  # shutdown signalled
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            from personalclaw.sel import SecurityEventLog

            removed = await asyncio.get_running_loop().run_in_executor(
                None, SecurityEventLog().prune
            )
            if removed:
                logger.info("SEL prune removed %d entries", removed)
        except Exception:
            logger.debug("SEL prune skipped", exc_info=True)
