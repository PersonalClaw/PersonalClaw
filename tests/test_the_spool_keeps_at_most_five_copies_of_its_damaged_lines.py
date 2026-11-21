"""The trigger spool keeps at most five copies of its damaged lines, says when it keeps no more,
and lets none go.

🔴 The defect (Backlog: "The trigger spool keeps every copy of its damaged lines, with no bound on
how many", from CORE's review of the spool fix). When a tick takes fires off the spool and finds
lines it cannot read, it keeps what they held beside it as `trigger-spool.jsonl.broken-<UTC
instant>` (`dispatch.clear_spool` through `record_files.keep_copy`). Each clear that dropped
different bytes kept a new copy and nothing ever let one go, so a spool damaged again and again (a
foreign writer, a crash loop) grew copies without bound.

Now at most `dispatch.SPOOL_COPIES_KEPT` (5) are kept. Past it a damaged line still leaves the
spool, so the fires around it still run, but what it held is not written: none is deleted to make
room, a copy being the owner's to remove, and one they remove makes room again. The warning says
why it kept nothing, and the Doctor's spool row says the most is kept. Driven through the tick's
real drain over a scratch home.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from test_the_spool_reads_every_good_line_and_keeps_every_append import _drain, _line, _spool

from personalclaw import record_files
from personalclaw.triggers import dispatch

MOST = dispatch.SPOOL_COPIES_KEPT


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _damaged(n: int) -> bytes:
    """A line a write cut short, different each time, as a spool damaged again and again holds."""
    return b'{"seq":%d,"source":"memo\n' % n


def _damage_and_drain(home: Path, monkeypatch: pytest.MonkeyPatch, n: int) -> list[str]:
    """One tick over a spool holding one fire and one damaged line: the fires it ran."""
    _spool(home, _line(n), _damaged(n))
    return _drain(home, monkeypatch)


def _copies(home: Path) -> list[Path]:
    return record_files.kept_copies(home / dispatch.SPOOL_FILENAME)


def _fill(home: Path, monkeypatch: pytest.MonkeyPatch) -> dict[Path, bytes]:
    """The spool damaged until the most copies are kept; each copy and what it holds."""
    for n in range(1, MOST + 1):
        assert _damage_and_drain(home, monkeypatch, n) == [f"notes.{n}"]
    kept = {copy: copy.read_bytes() for copy in _copies(home)}
    assert len(kept) == MOST
    return kept


def test_a_spool_damaged_again_and_again_keeps_at_most_five_copies(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: a seventh damage kept a seventh copy, and so on without end."""
    kept = _fill(home, monkeypatch)
    for n in (MOST + 1, MOST + 2):
        assert _damage_and_drain(home, monkeypatch, n) == [f"notes.{n}"], "its fires still run"
        assert (home / dispatch.SPOOL_FILENAME).read_bytes() == b"", "the damaged line still goes"
    assert {
        copy: copy.read_bytes() for copy in _copies(home)
    } == kept, "none let go, none rewritten"


def test_past_the_most_the_warning_says_why_nothing_was_kept(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _fill(home, monkeypatch)
    caplog.clear()  # only what the drain past the most says
    with caplog.at_level(logging.WARNING, logger=dispatch.logger.name):
        _damage_and_drain(home, monkeypatch, MOST + 1)
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "is not kept" in said and f"{MOST} copies are kept" in said, said
    assert "the most it keeps" in said and "no copy could be kept" not in said, said


def test_the_same_bytes_at_the_most_are_named_as_the_copy_kept_of_them(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A damage a copy already holds is kept: in that copy, which the warning names."""
    kept = _fill(home, monkeypatch)
    caplog.clear()  # only what this drain says, not the fills that kept the copy it names
    with caplog.at_level(logging.WARNING, logger=dispatch.logger.name):
        _damage_and_drain(home, monkeypatch, 1)
    said = " ".join(r.getMessage() for r in caplog.records)
    first = next(copy for copy, held in kept.items() if held == _damaged(1))
    assert f"kept as {first.name}" in said, said
    assert len(_copies(home)) == MOST


def test_a_copy_the_owner_removes_makes_room(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kept = _fill(home, monkeypatch)
    removed = next(iter(kept))
    removed.unlink()  # the owner's own removal, in this test's scratch home
    _damage_and_drain(home, monkeypatch, MOST + 1)
    now = _copies(home)
    assert len(now) == MOST and any(c.read_bytes() == _damaged(MOST + 1) for c in now), now


def test_the_doctor_says_when_the_most_is_kept(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Short of the most, the row names the copies as before; at it, it says no more are kept."""
    from personalclaw.resilience.doctor import DoctorContext
    from personalclaw.resilience.store_files import probe_trigger_spool

    for n in range(1, MOST):
        _damage_and_drain(home, monkeypatch, n)
    short = asyncio.run(probe_trigger_spool(DoctorContext(home=home)))
    assert short.ok and short.evidence["full"] is False and "the most" not in short.detail
    _damage_and_drain(home, monkeypatch, MOST)
    full = asyncio.run(probe_trigger_spool(DoctorContext(home=home)))
    assert full.ok, "a record, not a fault"
    assert full.evidence["full"] is True and full.evidence["most"] == MOST
    assert f"That is the most it keeps ({MOST})" in full.detail, full.detail


def test_a_copy_with_no_most_is_kept_whatever_was_kept_before(tmp_path: Path) -> None:
    """The control: `keep_copy` with no *most*, as a store that could not be read keeps its
    copies, keeps each distinct one, as before."""
    path = tmp_path / "a-store.json"
    for n in range(MOST + 2):
        assert record_files.keep_copy(path, b"damaged %d" % n) is not None
    assert len(record_files.kept_copies(path)) == MOST + 2
