"""In-process registry for chat-attachment content extraction.

When a file is attached in chat it's uploaded to ``~/.personalclaw/uploads`` and
its extraction (knowledge EXTRACTION graph only — see ``knowledge.extract``)
starts IMMEDIATELY, while the user is still typing. The result is cached by the
saved file path. When the chat turn runs, the runner awaits any pending
extraction for the turn's attached files (so the query blocks on extraction iff
it isn't done yet) and prepends the extracted text to the prompt context.

An image is the exception: it is read only when something asks for its text (see
:meth:`AttachmentExtractor.start`).

Reading an attachment is reading what the person gave the chat, so it runs as such
(``memory_writes.reading_their_input``): the models set up for reading images, recordings and
scans read it in an Incognito or Temporary chat as in any other, and the chat's notice says so.

Singleton, keyed by absolute upload path. Bounded so a long session can't grow
it without bound.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from personalclaw import memory_writes
from personalclaw.knowledge.extract import Extracted

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 200
_MAX_TEXT_CHARS = 200_000  # cap injected text per attachment (~50k tokens)


class AttachmentExtractor:
    """Fires-and-tracks extraction tasks keyed by upload path."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Extracted]] = {}
        #: The paths whose running extraction already says when it finishes (:meth:`peek`).
        self._announcing: set[str] = set()

    def start(self, path: str, mime: str | None = None) -> None:
        """Begin reading *path* ahead of its turn (idempotent). Returns immediately.

        Not an image. Reading one is image-model calls (the graph's OCR and description), and
        whether it reaches the model as pixels or as text is decided by the model serving its
        turn (``chat_runner._prepare_image_attachments``), which an upload cannot know. A turn
        that sends it as pixels never uses its text, so an image is read only when something
        asks for it (:meth:`get`): the composer's note when the chat's model takes no images
        (it says what the model will get instead), the turn that sends it as text, and the
        sent turn's preview of it.
        """
        from personalclaw.dashboard.attachment_images import is_image_attachment

        if is_image_attachment(path):
            return
        self._begin(path, mime)

    def _begin(self, path: str, mime: str | None) -> None:
        if not path or path in self._tasks:
            return
        if len(self._tasks) >= _MAX_ENTRIES:
            # drop the oldest finished entries to stay bounded
            for k in [k for k, t in list(self._tasks.items()) if t.done()][:50]:
                self._tasks.pop(k, None)
        try:
            # Reading a file someone attached is reading what they gave the chat, so the models
            # set up for reading it do, in an Incognito or Temporary chat too. The task keeps
            # that from the context it is made in.
            with memory_writes.reading_their_input():
                self._tasks[path] = asyncio.create_task(self._run(path, mime))
        except RuntimeError:
            # no running loop (shouldn't happen on the gateway) — skip; the
            # await-path will fall back to a synchronous extract.
            logger.debug("attachment extract: no loop to start task for %s", path)

    async def _run(self, path: str, mime: str | None) -> Extracted:
        from personalclaw.knowledge.extract import extract_file

        try:
            got = await extract_file(path, mime, name=display_name(path))
        except Exception:
            logger.warning("attachment extract failed for %s", path, exc_info=True)
            return Extracted("", False)
        return Extracted(got.text[:_MAX_TEXT_CHARS], got.read, got.unread)

    def peek(self, path: str, mime: str | None, on_done: Callable[[], None]) -> Extracted | None:
        """What extraction got from *path* when it has finished, else None. Never waits.

        Starts the extraction when nothing has, as :meth:`get` does, and calls *on_done* once
        when it finishes, however many reads asked while it ran. A read is a page's question
        (the composer's note, a sent turn's preview), and a read that waited held one of the
        browser's six connections to the gateway while an image model read the file.
        """
        if path not in self._tasks:
            self._begin(path, mime)
        task = self._tasks.get(path)
        if task is None:
            return None
        if task.done():
            if task.cancelled() or task.exception() is not None:
                return Extracted("", False)
            return task.result()
        if path not in self._announcing:
            self._announcing.add(path)

            def _finished(_task: asyncio.Task[Extracted]) -> None:
                self._announcing.discard(path)
                on_done()

            task.add_done_callback(_finished)
        return None

    async def get(self, path: str, mime: str | None = None) -> Extracted:
        """Await + return what extraction got from *path*. Starts extraction if it
        wasn't already kicked off at upload (an image never is; a late/missed start
        works the same way). Blocks until extraction completes — this is the
        turn-gating point."""
        if path not in self._tasks:
            self._begin(path, mime)
        task = self._tasks.get(path)
        if task is None:
            # couldn't schedule a task (no loop) → extract inline
            with memory_writes.reading_their_input():
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
    """The name an upload was attached with: its stored name without the uuid prefix.

    What the prompt labels the file, and what its extracted text calls it when it can only
    describe it. The stored name is the upload route's alone and never reaches a user."""
    base = os.path.basename(path)
    # uploads are saved as "<32-hex>_<original>"
    if len(base) > 33 and base[32] == "_" and all(c in "0123456789abcdef" for c in base[:32]):
        return base[33:]
    return base
