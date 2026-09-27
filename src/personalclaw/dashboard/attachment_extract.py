"""In-process registry for chat-attachment content extraction.

When a file is attached in chat it's uploaded to ``~/.personalclaw/uploads`` and
its extraction (knowledge EXTRACTION graph only — see ``knowledge.extract``)
starts IMMEDIATELY, while the user is still typing. The result is cached by the
saved file path. When the chat turn runs, the runner awaits any pending
extraction for the turn's attached files (so the query blocks on extraction iff
it isn't done yet) and prepends the extracted text to the prompt context.

Singleton, keyed by absolute upload path. Bounded so a long session can't grow
it without bound.
"""

from __future__ import annotations

import asyncio
import logging
import os

from personalclaw.knowledge.extract import Extracted

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 200
_MAX_TEXT_CHARS = 200_000  # cap injected text per attachment (~50k tokens)


class AttachmentExtractor:
    """Fires-and-tracks extraction tasks keyed by upload path."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Extracted]] = {}

    def start(self, path: str, mime: str | None = None) -> None:
        """Begin extracting *path* now (idempotent). Returns immediately."""
        if not path or path in self._tasks:
            return
        if len(self._tasks) >= _MAX_ENTRIES:
            # drop the oldest finished entries to stay bounded
            for k in [k for k, t in list(self._tasks.items()) if t.done()][:50]:
                self._tasks.pop(k, None)
        try:
            self._tasks[path] = asyncio.create_task(self._run(path, mime))
        except RuntimeError:
            # no running loop (shouldn't happen on the gateway) — skip; the
            # await-path will fall back to a synchronous extract.
            logger.debug("attachment extract: no loop to start task for %s", path)

    async def _run(self, path: str, mime: str | None) -> Extracted:
        from personalclaw.knowledge.extract import extract_file

        try:
            got = await extract_file(path, mime)
        except Exception:
            logger.warning("attachment extract failed for %s", path, exc_info=True)
            return Extracted("", False)
        return Extracted(got.text[:_MAX_TEXT_CHARS], got.read)

    async def get(self, path: str, mime: str | None = None) -> Extracted:
        """Await + return what extraction got from *path*. Starts extraction if it
        wasn't already kicked off at upload (so a late/missed start still works).
        Blocks until extraction completes — this is the turn-gating point."""
        if path not in self._tasks:
            self.start(path, mime)
        task = self._tasks.get(path)
        if task is None:
            # couldn't schedule a task (no loop) → extract inline
            return await self._run(path, mime)
        try:
            return await task
        except Exception:
            return Extracted("", False)


_INSTANCE: AttachmentExtractor | None = None


def get_extractor() -> AttachmentExtractor:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AttachmentExtractor()
    return _INSTANCE


def display_name(path: str) -> str:
    """Clean filename for prompt labelling — strips the uuid upload prefix."""
    base = os.path.basename(path)
    # uploads are saved as "<32-hex>_<original>"
    if len(base) > 33 and base[32] == "_" and all(c in "0123456789abcdef" for c in base[:32]):
        return base[33:]
    return base
