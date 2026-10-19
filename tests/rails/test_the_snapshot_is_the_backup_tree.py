"""The CLI reference says the snapshot is the backup a restore reads, and the shards are not.

The tree half of ``tests/test_the_snapshot_is_the_backup.py``, which says why every backup
surface must name the copy a restore reads and tests the others. It reads the CLI reference, so it
is a rail: it runs once per CI run, in the rails job.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

_CLI_REFERENCE = REPO / "docs" / "reference" / "cli.md"


def test_the_cli_reference_says_the_snapshot_is_the_backup():
    doc = source_index.text(_CLI_REFERENCE)
    export_row = next(line for line in doc.splitlines() if "personalclaw backup export" in line)
    snapshot_row = next(line for line in doc.splitlines() if "`personalclaw snapshot [" in line)
    assert "It is not a backup" in export_row
    assert "The backup is `personalclaw snapshot`" in export_row
    assert "the backup a restore reads" in snapshot_row and "skills" in snapshot_row
    assert "A backup nobody has verified" not in doc, "validate no longer calls the shards a backup"
