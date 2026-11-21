"""The Doctor's spool row names what it checks apart from what it found, so it does not read its
one sentence twice.

🔴 The defect (Backlog: "The Doctor's spool row says its one sentence twice"). Seen in the health
surface's baselines (43606493f): the `automations.spool` row read "Every fire parked for the next
tick could be read Every fire parked for the next tick could be read." Its title was the same
sentence as its healthy detail, and the automations file's row beside it read "The automations
file can be read" twice the same way. The page draws a row as its title and its detail side by
side, as "Gateway port listening" beside "listening port".

Now the title names what is checked ("Fires parked for the next tick") and the detail says what
was found. Driven through the real probes over an empty scratch home, where every one is healthy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.resilience.doctor import DoctorContext
from personalclaw.resilience.store_files import store_file_probes


def _said(probe, home: Path) -> str:
    result = asyncio.run(probe.run(DoctorContext(home=home)))
    assert result.ok, (probe.id, result.detail)
    return result.detail


def _same(title: str, detail: str) -> bool:
    return title.strip().rstrip(".").casefold() == detail.strip().rstrip(".").casefold()


@pytest.mark.parametrize("probe", store_file_probes(), ids=lambda p: p.id)
def test_a_healthy_row_says_more_than_its_name(tmp_path: Path, probe) -> None:
    """🔴 Before: the spool row's title was its healthy detail, word for word."""
    assert probe.title, f"{probe.id} has no name for its row"
    assert not _same(
        probe.title, _said(probe, tmp_path)
    ), f"{probe.id}'s row reads its one sentence twice: name it for what it checks"


def test_the_automations_rows_name_what_they_check(tmp_path: Path) -> None:
    """The spool row, and the automations file's row beside it, which read its own sentence twice
    too."""
    rows = {p.id: p for p in store_file_probes()}
    spool, store = rows["automations.spool"], rows["automations.store"]
    assert spool.title == "Fires parked for the next tick"
    assert _said(spool, tmp_path) == "Every fire parked for the next tick could be read."
    assert store.title == "The automations file"
    assert _said(store, tmp_path) == "The automations file can be read."
