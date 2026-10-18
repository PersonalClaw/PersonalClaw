"""The source-tree checks split from
``tests/test_state_survival.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from state_survival_shared import _FIXTURE_DIR


def test_the_committed_fixture_ships_no_sqlite_sidecar_and_no_machine_path() -> None:
    """Read off the repo tree, not a seeded home — this is about what SHIPS.

    Every store here opens with ``PRAGMA journal_mode=WAL``, so writes sit in a ``-wal``
    sidecar until checkpointed. Committing the bare ``.db`` without a checkpoint ships a
    fixture that boots EMPTY, which would make the survival test pass against nothing;
    committing the sidecar ships a file that is not state. A leaked absolute path would
    resolve on exactly one machine.
    """
    fixture = source_index.under(source_index.package_any_files(), _FIXTURE_DIR)
    dbs = sorted(p.name for p in fixture if p.suffix == ".db")
    assert dbs == ["knowledge.db", "loops.db", "memory.db"], f"unexpected db set: {dbs}"

    strays = sorted(
        p.name for p in fixture if p.name.endswith(("-wal", "-shm", ".db-journal", ".lock"))
    )
    assert not strays, f"these must not ship: {strays}"

    for path in fixture:
        blob = path.read_bytes()
        for needle in (b"/Users/", b"/home/", b"/private/tmp", b"/var/folders"):
            assert needle not in blob, (
                f"{path.relative_to(_FIXTURE_DIR)} embeds the absolute path "
                f"{needle.decode()!r} from the machine that generated it"
            )
