"""A chat's cost is read from what the ledger gained since, not from the whole ledger.

The chat asks ``/api/usage/totals?session=<chat>`` on every load and after every reply, for its
cost chip, and the CLI, the OpenAI-dialect server and a loop's planner ask the same way. Each
ask parsed every row of ``usage/turns.jsonl`` (up to 50,000, 18.7 MiB at its cap) to keep one
chat's: 0.2-0.3 s of a worker thread after every turn.

Now each session's total is folded once, and then only from the bytes appended since. A trim
(an atomic rewrite), a file put in its place and a line still being written are each noticed. A
windowed total, a prefix and a key filter read the ledger as before.
"""

from __future__ import annotations

import json
import os

import pytest

from personalclaw import usage_ledger as ul
from personalclaw.atomic_write import atomic_write


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "usage" / "turns.jsonl"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(ul, "_path", lambda: path)
    return path


def _row(session: str, n: int, **extra) -> dict:
    return {
        "ts": f"2026-11-20T10:{n // 60:02d}:{n % 60:02d}+00:00",
        "session_key": session,
        "model": "m",
        "input_tokens": 10 + n,
        "output_tokens": 5,
        "cost_usd": 0.001 * (n + 1),
        "priced": True,
        **extra,
    }


def _write(path, rows, mode="a"):
    with open(path, mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _scanned(path, session: str) -> dict:
    """What a full read of the file says for *session*, the way the ledger folds a row."""
    agg = ul._blank_agg()
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            row = json.loads(line)
            if row.get("session_key") == session:
                ul._fold(agg, row)
    return agg


def test_a_session_total_is_what_reading_every_row_says(ledger):
    rows = [_row(f"dashboard:chat-{n % 3}", n) for n in range(30)]
    rows.append(_row("dashboard:chat-1", 99, unit="image", quantity=2, priced=False))
    _write(ledger, rows)

    for session in ("dashboard:chat-0", "dashboard:chat-1", "dashboard:chat-2", "nobody"):
        assert ul.totals(session_key=session) == _scanned(ledger, session), session


def test_a_second_ask_reads_only_the_rows_added_since(ledger, monkeypatch):
    """🔴 Red before: every ask parsed the whole file."""
    _write(ledger, [_row("dashboard:chat-0", n) for n in range(200)])
    ul.totals(session_key="dashboard:chat-0")
    added = [_row("dashboard:chat-0", 500), _row("dashboard:chat-1", 501)]
    _write(ledger, added)
    appended = sum(len((json.dumps(r, ensure_ascii=False) + "\n").encode()) for r in added)
    read: list[int] = []
    real = ul._appended

    def _counting(path, offset, size):
        data = real(path, offset, size)
        read.append(len(data))
        return data

    def _no_scan():
        raise AssertionError("a session's total must not read every row")

    monkeypatch.setattr(ul, "_appended", _counting)
    monkeypatch.setattr(ul, "_iter_rows", _no_scan)

    total = ul.totals(session_key="dashboard:chat-0")

    assert read == [appended]
    assert total == _scanned(ledger, "dashboard:chat-0")
    assert total["turns"] == 201


def test_a_trim_is_read_again_from_its_start(ledger):
    _write(ledger, [_row("dashboard:chat-0", n) for n in range(10)])
    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 10

    kept = [_row("dashboard:chat-0", n) for n in range(7, 10)]
    atomic_write(ledger, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))

    assert ul.totals(session_key="dashboard:chat-0") == _scanned(ledger, "dashboard:chat-0")
    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 3


def test_a_file_put_in_the_ledgers_place_is_read_as_itself(ledger):
    """The same size and, after a delete, possibly the same inode: its rows are still its own."""
    _write(ledger, [_row("dashboard:chat-0", n) for n in range(5)])
    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 5

    os.unlink(ledger)
    _write(ledger, [_row("dashboard:chat-9", n) for n in range(5)], mode="w")

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 0
    assert ul.totals(session_key="dashboard:chat-9")["turns"] == 5


def test_a_line_still_being_written_is_counted_once_it_ends(ledger):
    _write(ledger, [_row("dashboard:chat-0", 0)])
    half = json.dumps(_row("dashboard:chat-0", 1), ensure_ascii=False)
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(half[:20])

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 1

    with open(ledger, "a", encoding="utf-8") as f:
        f.write(half[20:] + "\n")

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 2


def test_a_corrupt_line_is_skipped_as_before(ledger):
    _write(ledger, [_row("dashboard:chat-0", 0)])
    with open(ledger, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    _write(ledger, [_row("dashboard:chat-0", 1)])

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 2


def test_a_row_holding_a_line_separator_is_one_row(ledger):
    """🔴 Red before: the full read split lines on U+2028 as well, so such a row was dropped."""
    _write(ledger, [_row("dashboard:chat-0", 0, agent="a\u2028b"), _row("dashboard:chat-0", 1)])

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 2
    assert (
        ul.totals(since="2026-11-20T00:00:00+00:00", session_key="dashboard:chat-0")["turns"] == 2
    )


def test_a_window_a_prefix_and_a_filter_read_the_ledger_as_before(ledger):
    _write(
        ledger,
        [_row("loop-a", 0), _row("loop-a-t1", 1), _row("loop-ab", 2), _row("dashboard:chat-0", 3)],
    )

    assert ul.totals(session_prefix="loop-a")["turns"] == 2
    assert ul.totals(key_filter=lambda k: k.startswith("loop"))["turns"] == 3
    assert ul.totals(since="2026-11-20T10:00:02+00:00")["turns"] == 2
    assert ul.totals()["turns"] == 4


def test_a_copy_over_the_ledger_keeping_its_start_is_read_as_itself(ledger):
    """A restore copies over the file in place: the same inode, the same first rows, more bytes."""
    first = [_row("dashboard:chat-0", n) for n in range(3)]
    _write(ledger, first + [_row("dashboard:chat-0", 3)])
    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 4
    inode = os.stat(ledger).st_ino

    _write(ledger, first + [_row("dashboard:chat-5", n) for n in range(10, 15)], mode="w")

    assert os.stat(ledger).st_ino == inode
    assert ul.totals(session_key="dashboard:chat-0") == _scanned(ledger, "dashboard:chat-0")
    assert ul.totals(session_key="dashboard:chat-5")["turns"] == 5


def test_a_reused_inode_at_the_same_size_is_told_apart_by_the_files_start(ledger):
    """A delete then a new file can take the old inode (ext4 reuses one at once) and the same
    size, and rows that end alike leave the same bytes where the fold stopped: only the start
    differs. The index is made to see the new file's inode as the one it folded."""
    _write(ledger, [_row("dashboard:chat-0", n) for n in range(5)])
    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 5
    size = os.stat(ledger).st_size

    os.unlink(ledger)
    _write(ledger, [_row("dashboard:chat-9", n) for n in range(5)], mode="w")
    st = os.stat(ledger)
    assert st.st_size == size
    with open(ledger, "rb") as f:
        assert f.read()[-ul._SEAM_BYTES :] == ul._INDEX.seam
    ul._INDEX.ident = (st.st_dev, st.st_ino)

    assert ul.totals(session_key="dashboard:chat-0")["turns"] == 0
    assert ul.totals(session_key="dashboard:chat-9")["turns"] == 5


def test_asks_from_many_threads_while_rows_are_added_agree_with_a_full_read(ledger):
    """Eight threads ask for every session while another appends 3,000 rows: no ask fails, no
    total goes backwards, and each ends as a full read says (an unlocked fold counts rows twice)."""
    import threading

    sessions = [f"dashboard:chat-{n}" for n in range(5)]
    stop = threading.Event()
    errors: list[BaseException] = []
    went_back: list[tuple[str, int, int]] = []

    def _append() -> None:
        try:
            for n in range(3000):
                _write(ledger, [_row(sessions[n % 5], n)])
        except BaseException as exc:  # noqa: BLE001 — reported below
            errors.append(exc)
        finally:
            stop.set()

    def _ask() -> None:
        last = {s: -1 for s in sessions}
        try:
            while not stop.is_set():
                for s in sessions:
                    turns = ul.totals(session_key=s)["turns"]
                    if turns < last[s]:
                        went_back.append((s, last[s], turns))
                    last[s] = turns
        except BaseException as exc:  # noqa: BLE001 — reported below
            errors.append(exc)

    threads = [threading.Thread(target=_append)] + [threading.Thread(target=_ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [] and went_back == []
    for s in sessions:
        assert ul.totals(session_key=s) == _scanned(ledger, s), s
        assert ul.totals(session_key=s)["turns"] == 600


def test_a_trim_keeps_a_row_holding_a_line_separator():
    """🔴 Red before: the trim split lines on U+2028 too, and kept neither half of that row."""
    import tempfile
    from pathlib import Path

    from personalclaw import bounded_log

    with tempfile.TemporaryDirectory() as home:
        path = Path(home) / "turns.jsonl"
        rows = [_row("dashboard:chat-0", n) for n in range(6)]
        rows.append(_row("dashboard:chat-0", 7, agent="a\u2028b"))
        _write(path, rows)

        assert bounded_log.trim_jsonl(path, 3, at="ts") is True
        kept = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]

    assert len(kept) == 3
    assert kept[-1]["agent"] == "a\u2028b"


def test_the_usage_pages_read_keeps_a_row_holding_a_line_separator(ledger):
    """🔴 Red before: the Usage page's read split it in two, so it left out what the chip counted."""
    from personalclaw.routing.usage import _iter_json_lines

    _write(ledger, [_row("dashboard:chat-0", 0, agent="a\u2028b"), _row("dashboard:chat-0", 1)])

    assert [r["agent"] for r in _iter_json_lines(ledger) if "agent" in r] == ["a\u2028b"]
    assert len(_iter_json_lines(ledger)) == 2
