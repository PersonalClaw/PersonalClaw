"""Another home's rows arrived in a store that holds vectors: a signal the re-index path takes.

A merge brings another home's rows in with their vectors — a sync pulling a peer's databases, a
restore's merge, an archive import — and a vector another model wrote is not one this home's model
can search by: until it is embedded again, the memory it belongs to is read by keyword only. So
each of those merges says here that rows came (:func:`arrived`), and the gateway's watch on the
embedding binding (``dashboard.handlers.embedding_reindex.watch_embedding_binding``) takes the one
re-index path on its next pass (:func:`take`): the path counts what the model bound here has not
embedded, and re-embeds it.

In this process only. A merge run by another process — ``personalclaw restore`` at a terminal —
is taken when the gateway next starts, whose first pass takes the same path, which the command's
closing line says to do.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_pending = False


def arrived() -> None:
    """A merge brought another home's rows into a store that holds vectors. Safe from any thread:
    a sync merges on the durability service's worker thread."""
    global _pending
    with _lock:
        _pending = True


def take() -> bool:
    """Whether rows arrived since this was last asked, and forget that they did."""
    global _pending
    with _lock:
        came, _pending = _pending, False
    return came
