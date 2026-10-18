"""Every name the gateway writes for a piece of work comes from a writer the one parser reads.

A trigger's history row names what its fire started (``ScheduleRun.work_id``), and that name is read
back through ``personalclaw.workref`` as ``<kind>:<id>``. This rail holds the package to the
writers there are, each read back as the work it names, so a third way of writing a work's name
cannot ship without its reading.
"""

from __future__ import annotations

import ast

import source_index

from personalclaw import workref
from personalclaw.subagent import agent_work_id
from personalclaw.workflows.models import run_work_id

#: Each writer of a work's name, and the kind of work the parser reads its names as.
KNOWN = {"agent_work_id": (agent_work_id, "subagent"), "run_work_id": (run_work_id, "workflow")}


def _writers(trees) -> set[str]:
    """The function every ``work_id=<call>`` in *trees* names the work with. A name read back from
    a stored row (``str(row["work_id"])``) writes none."""
    named: set[str] = set()
    for _path, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "work_id":
                continue
            if isinstance(node.value, ast.Call):
                func = node.value.func
                named.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return named - {"str"}


def test_every_work_id_is_written_by_a_writer_the_parser_reads() -> None:
    writers = _writers(source_index.package_trees())
    assert writers, "found no `work_id=` writer in the package — vacuous"
    assert writers == set(KNOWN), sorted(writers ^ set(KNOWN))
    for write, kind in KNOWN.values():
        assert workref.parse(write("x-7")) == workref.WorkRef(kind, "x-7"), write


def test_a_writer_of_its_own_is_seen() -> None:
    """The positive control: a planted third writer is found beside a read-back that is not one."""
    planted = ast.parse(
        "run(work_id=agent_work_id(a))\nsave(work_id=str(row['work_id']))\nfire(work_id=mine(x))\n"
    )
    assert _writers([(None, planted)]) == {"agent_work_id", "mine"}
