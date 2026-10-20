"""Writing an upload's bytes to disk as they arrive, without the event loop waiting on the disk.

A request's body arrives on the event loop, and the disk it is written to may not keep up with
it: on a busy disk a single write of an upload's next chunk took 0.1 s, and every other request
waited for it. :class:`Spool` gathers the bytes and writes them a megabyte at a time in a worker
thread, where the wait on the disk holds nothing up (a file write leaves the interpreter lock while
it waits). Every route that streams an upload to a file writes through it.
"""

from __future__ import annotations

import asyncio
from typing import IO

#: How much is gathered before it is written: few enough writes that handing each to a thread
#: costs nothing next to the upload, and little enough held in memory.
SPOOL_BYTES = 1024 * 1024


class Spool:
    """The bytes of one upload, written to the open file *fh* in a worker thread.

    :meth:`write` each chunk as it arrives, then :meth:`flush` once the last has. The file is the
    caller's to close; a caller that stops early (a cap passed, the connection lost) discards the
    file, and with it what the spool still holds."""

    def __init__(self, fh: IO[bytes]) -> None:
        self._fh = fh
        self._held = bytearray()

    async def write(self, chunk: bytes) -> None:
        self._held += chunk
        if len(self._held) >= SPOOL_BYTES:
            await self.flush()

    async def flush(self) -> None:
        """Write what is gathered, and wait until it is written."""
        if self._held:
            data = bytes(self._held)
            self._held.clear()
            await asyncio.to_thread(self._fh.write, data)
