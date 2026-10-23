"""The process table holds no row for the ``ps`` that read it.

Where there is no ``/proc`` (macOS), ``process_table()`` runs ``ps``, and ``ps`` lists itself: a
child of this process, still running as it printed. So every caller asking for this process's
descendants found one, the probe it had just run. An app's unload (``app_runtime._running_from``)
then asked ``lsof`` for that probe's working directory on every unload, after the probe had
exited, and on a hosted Mac that ``lsof`` is what carried ``test_app_scaffold``'s 21 unloads past
the per-test timeout (#3905). A row is the probe's own only when it is this process's child and its
command line is the probe's exact argv, so a real child is never dropped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw import process_facts

PROBE = "ps -Awwo pid=,ppid=,command="


def test_the_probes_own_row_is_left_out_and_a_real_child_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = os.getpid()
    printed = "\n".join(
        [
            f"  {me} 1 python -m pytest",
            f"  4242 {me} {PROBE}",
            f"  4243 {me} sleep 30",
            f"  4244 1 {PROBE}",
        ]
    )
    monkeypatch.setattr(process_facts, "PROC", tmp_path / "no-proc")
    monkeypatch.setattr(process_facts, "run_probe", lambda argv: printed)
    table = process_facts.process_table()
    assert 4242 not in table, "this process's own probe"
    assert table[4243] == (me, "sleep 30"), "a real child of this process"
    assert table[4244] == (1, PROBE), "another process's ps is a process like any other"
    assert set(process_facts.descendants(me, table)) == {4243}


@pytest.mark.skipif(
    process_facts.PROC.is_dir(), reason="/proc is read here, and reading it runs no program"
)
def test_this_process_has_no_descendant_but_its_own_children() -> None:
    table = process_facts.process_table()
    assert table, "ps answered"
    below = process_facts.descendants(os.getpid(), table)
    assert not [pid for pid in below if table[pid][1] == PROBE], below
