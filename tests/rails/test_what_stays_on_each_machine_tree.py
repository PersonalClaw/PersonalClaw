"""The sync docs name everything that stays on each machine.

The tree half of ``tests/test_what_stays_on_each_machine.py``, which says why a machine's own
stores stay on it and tests that a backup carries them and a sync does not. It reads
``docs/architecture/tasks-triggers.md``, so it is a rail: it runs once per CI run, in the rails
job.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

from personalclaw.durability import inventory as inv

_SYNC_DOC = REPO / "docs" / "architecture" / "tasks-triggers.md"
_LEADS = (
    "**What is one machine's own stays on it.**",
    "**What ran on a machine stays on it too.**",
)


def _paragraph(doc: str, lead: str) -> str:
    """The paragraph of *doc* that starts with *lead*: up to the next blank line."""
    start = doc.find(lead)
    assert start >= 0, f"{_SYNC_DOC.name} has no paragraph starting {lead!r}"
    end = doc.find("\n\n", start)
    return doc[start : end if end >= 0 else len(doc)]


def test_the_sync_docs_name_everything_that_stays_on_each_machine():
    doc = source_index.text(_SYNC_DOC)
    named = {name for lead in _LEADS for name in re.findall(r"`([^`]+)`", _paragraph(doc, lead))}
    staying = {e.path for e in inv.INVENTORY if e.machine_local} | {
        f"{e.path}/{glob}" for e in inv.INVENTORY for glob in e.machine_local_within
    }
    assert staying, "nothing is declared machine-local — the check is vacuous"
    assert staying <= named, sorted(staying - named)
