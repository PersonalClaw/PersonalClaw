"""Two memory stores this process opens on one database bring its schema up to date once.

Once a store opening a fresh ``memory.db`` waited out another's lock (#6172), both reached the
migrations together: each read an empty ``schema_version`` and applied every migration side by
side, so one waited out the other's write lock (up to the 5 s busy timeout, which made the racing
test time out on a loaded runner) and a guarded ``ADD COLUMN`` could be checked by both before
either added it. The stores now take turns through opening it: the first applies each migration,
and the second finds them applied.
"""

from __future__ import annotations

import threading

from personalclaw import vector_memory
from personalclaw.vector_memory import VectorMemoryStore


def test_a_second_store_opening_while_the_first_migrates_applies_no_migration_again(
    tmp_path, monkeypatch
):
    calls: list[int] = []
    first_inside = threading.Event()
    let_first_on = threading.Event()
    first_with_a_function = min(ver for ver, _sql, fn in vector_memory._MIGRATIONS if fn)

    def counted(ver, fn):
        def run(db):
            calls.append(ver)
            if ver == first_with_a_function and not first_inside.is_set():
                first_inside.set()
                let_first_on.wait(10)
            return fn(db)

        return run

    monkeypatch.setattr(
        vector_memory,
        "_MIGRATIONS",
        [
            (ver, sql, counted(ver, fn) if fn else None)
            for ver, sql, fn in vector_memory._MIGRATIONS
        ],
    )
    path = tmp_path / "memory.db"
    first, second = VectorMemoryStore(db_path=path), VectorMemoryStore(db_path=path)
    opening_first = threading.Thread(target=first.init)
    opening_first.start()
    assert first_inside.wait(10), "the first store never reached its migrations"
    opening_second = threading.Thread(target=second.init)
    opening_second.start()
    # Unguarded, the second store runs every migration now, while the first waits inside one.
    opening_second.join(1.0)
    let_first_on.set()
    opening_first.join(30)
    opening_second.join(30)

    with_a_function = [ver for ver, _sql, fn in vector_memory._MIGRATIONS if fn]
    assert sorted(calls) == with_a_function, f"applied more than once: {sorted(calls)}"
    for store in (first, second):
        assert store._db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(
            vector_memory._MIGRATIONS
        )
