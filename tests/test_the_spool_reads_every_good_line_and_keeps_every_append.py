"""The trigger spool reads every good line and takes each fire once, and no append to it, to the
digest queue or to a bounded log is lost to a trim, a clear or a drain of the same file.

🔴 Two defects (Backlog: "The trigger spool stalls on a non-UTF-8 byte and can fire twice" and
"Appends made during a trim of the spool, the digest queue or a log can be lost").

The spool (`trigger-spool.jsonl`) parks fires raised where no event loop runs (a memory written
from the CLI, an agent CLI's tool server) for the next tick:
- `drain_spool()` decoded the whole file at once and read nothing on any error, so one byte that
  was not UTF-8 stalled every parked fire, and every later one, for good, and said nothing;
- `clear_spool(handled=N)` dropped the first N LINES for N envelopes, so a damaged or blank line
  among them left the last fire handled on disk, and the next tick ran it again;
- `clear_spool()` swallowed its own failure, so the loop's "may re-run" warning never fired.

And the spool, the digest queue (`digest_queue.jsonl`) and six bounded logs (the model-call audit,
the inbound clients' audit, feedback, the self-remediation record, the sampling outcomes, the
per-turn usage record) were cut back by reading the file and replacing it, with nothing stopping
another writer appending in between: an append made in that window was lost.

Now the spool is read line by line, a damaged line set aside beside it (its copy named by the
Doctor) and the rest read; the ack drops exactly the lines the drain consumed, damaged ones among
them; an ack that fails says so. And every append, trim, clear and drain of these files takes the
file's own lock (`record_files.locked`, across processes), so an append lands either before the
rewrite reads the file or after it is replaced.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw import record_files
from personalclaw.triggers import dispatch
from personalclaw.triggers.dispatch import Envelope, Handling


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _env(n: int) -> Envelope:
    return Envelope(
        seq=n, source="memory", kind="update", payload={"key": f"notes.{n}"}, emitted_at=1000.0 + n
    )


def _line(n: int) -> bytes:
    return (json.dumps(_env(n).to_dict(), separators=(",", ":")) + "\n").encode("utf-8")


#: A line a write cut short at power loss, and a line with a byte that is not UTF-8.
TORN = b'{"seq":9,"source":"memo\n'
NOT_UTF8 = b'{"seq":8,"source":"memory","kind":"update","payload":{"key":"caf\xe9"}}\n'


def _spool(home: Path, *lines: bytes) -> Path:
    path = home / "trigger-spool.jsonl"
    path.write_bytes(b"".join(lines))
    return path


def _keys(envelopes: list[Envelope]) -> list[str]:
    return [str(e.payload["key"]) for e in envelopes]


# ── the spool reads every good line ──


def test_a_byte_that_is_not_utf8_does_not_stall_the_spool(home: Path) -> None:
    """🔴 Before: the whole file failed to decode, and the drain read nothing, said nothing."""
    _spool(home, _line(1), NOT_UTF8, _line(2))
    envelopes, skipped = dispatch.drain_spool()
    assert _keys(envelopes) == ["notes.1", "notes.2"]
    assert skipped == 1


def _drain(home: Path, monkeypatch: pytest.MonkeyPatch, hold: str = "") -> list[str]:
    """One tick's drain of the spool through the loop's real cursor rule, each envelope re-entered
    as delivered, or held (a transient failure before delivery) when its key is *hold*. The keys
    it re-entered, in order."""
    from personalclaw.triggers import loop

    seen: list[str] = []

    def reenter(envelope: Any, *, now: float) -> tuple[str, str]:
        key = str(envelope.payload["key"])
        if key == hold:
            return Handling.TRANSIENT.value, "the event engine is not reachable yet"
        seen.append(key)
        return Handling.DELIVERED.value, ""

    monkeypatch.setattr(loop, "_reenter_spooled", reenter)
    loop._drain_spool(now=2000.0)
    return seen


def test_each_fire_is_taken_once_with_a_damaged_line_among_them(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: three envelopes handled dropped three LINES, the damaged one among them, so the
    last fire stayed on disk and the next tick ran it again."""
    path = _spool(home, _line(1), TORN, _line(2), b"\n", _line(3))
    assert _drain(home, monkeypatch) == ["notes.1", "notes.2", "notes.3"]
    assert path.read_bytes() == b""
    assert _drain(home, monkeypatch) == [], "nothing runs twice"


def test_a_held_fire_keeps_its_place_and_the_damaged_line_before_it_goes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ack drops exactly what the drain consumed: the fire before the held one, and the
    damaged line between them. The held fire and what follows it wait for the next tick."""
    path = _spool(home, _line(1), NOT_UTF8, _line(2), _line(3))
    assert _drain(home, monkeypatch, hold="notes.2") == ["notes.1"]
    assert path.read_bytes() == _line(2) + _line(3)
    assert _drain(home, monkeypatch) == ["notes.2", "notes.3"]


def test_a_damaged_line_is_set_aside_beside_the_spool(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: a damaged line was skipped and left in place, and nothing kept what it held."""
    path = _spool(home, TORN, _line(1), NOT_UTF8)
    _drain(home, monkeypatch)
    (kept,) = record_files.kept_copies(path)
    assert kept.read_bytes() == TORN + NOT_UTF8


def test_a_damaged_line_ahead_of_a_held_fire_is_set_aside_too(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drain consumed nothing, but the line it could not read is not left at the head to be
    skipped, and said, on every tick."""
    path = _spool(home, TORN, _line(1))
    assert _drain(home, monkeypatch, hold="notes.1") == []
    assert path.read_bytes() == _line(1)
    assert len(record_files.kept_copies(path)) == 1


def test_an_ack_that_fails_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 Before: `clear_spool` swallowed its own failure, so the loop's warning that the fires may
    run again never fired."""
    _spool(home, _line(1))

    def refuse(*_a: Any, **_kw: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(dispatch, "atomic_write_bytes", refuse)
    with pytest.raises(OSError):
        dispatch.clear_spool(handled=1)


def test_the_doctor_names_a_set_aside_copy(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What a damaged line held is kept, and the Doctor's automations check says where."""
    from personalclaw.resilience.doctor import DoctorContext
    from personalclaw.resilience.store_files import probe_trigger_spool

    path = _spool(home, TORN, _line(1))
    clean = asyncio.run(probe_trigger_spool(DoctorContext(home=home)))
    assert clean.ok and clean.evidence["kept"] == []
    _drain(home, monkeypatch)
    (kept,) = record_files.kept_copies(path)
    said = asyncio.run(probe_trigger_spool(DoctorContext(home=home)))
    assert said.ok, "a copy kept is a record, not a fault to fix again and again"
    assert kept.name in said.detail and said.evidence["kept"] == [kept.name]


# ── no append is lost to a rewrite of the same file ──


@contextlib.contextmanager
def _held(path: Path) -> Iterator[None]:
    """*path*'s lock, held as another process holds it (its own open of the lock file)."""
    with record_files.locked(path):
        yield


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _waits_for_the_lock(path: Path, act: Callable[[], Any]) -> None:
    """*act*, run while another writer holds *path*'s lock, neither finishes nor changes the file
    until the lock is let go: an append that wrote first and then waited for its trim's lock would
    already have written, in the window a rewrite under way would lose it."""
    done = threading.Event()

    def run() -> None:
        act()
        done.set()

    with _held(path):
        before = _bytes(path)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        assert not done.wait(0.4), "it went ahead while another writer held the file's lock"
        assert _bytes(path) == before, "it wrote to the file while another writer held its lock"
    assert done.wait(10), "it never went ahead once the lock was let go"
    worker.join(10)


def test_a_fire_spooled_during_an_ack_is_kept(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 Before: `spool_fire`'s append landed in the file `clear_spool` was about to replace, and
    the fire never ran."""
    path = _spool(home, _line(1))
    real = dispatch.atomic_write_bytes
    late = threading.Thread(target=lambda: dispatch.spool_fire(_env(2)), daemon=True)

    def replace(target: Any, data: bytes, **kw: Any) -> None:
        late.start()
        time.sleep(0.3)  # the append, if it does not wait, lands now, in the file being replaced
        real(target, data, **kw)

    monkeypatch.setattr(dispatch, "atomic_write_bytes", replace)
    dispatch.clear_spool(handled=1)
    late.join(10)
    assert path.read_bytes() == _line(2)


def test_the_spool_s_append_waits_for_its_lock(home: Path) -> None:
    path = home / "trigger-spool.jsonl"
    _waits_for_the_lock(path, lambda: dispatch.spool_fire(_env(1)))
    assert path.read_bytes() == _line(1)


def test_a_trim_keeps_an_append_made_while_it_ran(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: a model-call row appended between the trim's read and its replace was lost."""
    from personalclaw import bounded_log
    from personalclaw.guardrails import audit

    path = audit._audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps({"ts": f"2026-11-0{i}T00:00:00+00:00"}) + "\n" for i in (1, 2, 3))
    )
    real = bounded_log.atomic_write
    late = threading.Thread(
        target=lambda: audit.record_attempt(_attempt("2026-11-21T00:00:00+00:00")),
        daemon=True,
    )

    def replace(target: Any, data: str, **kw: Any) -> None:
        late.start()
        time.sleep(0.3)
        real(target, data, **kw)

    monkeypatch.setattr(bounded_log, "atomic_write", replace)
    assert bounded_log.trim_jsonl(path, 1, at="ts", past=2)
    late.join(10)
    kept = [json.loads(line)["ts"] for line in path.read_text().splitlines()]
    assert kept == ["2026-11-03T00:00:00+00:00", "2026-11-21T00:00:00+00:00"], kept


def _attempt(ts: str) -> Any:
    from personalclaw.guardrails import audit

    return audit.AttemptRecord(
        audit_id="a1", ts=ts, use_case="chat", provider="p", model="m", attempt=1
    )


def _appends() -> list[tuple[str, Callable[[], Path], Callable[[], Any]]]:
    """Each bounded log's own append, beside the file it appends to."""
    from personalclaw import feedback, notification_rules, sampling, usage_ledger
    from personalclaw.guardrails import audit as model_calls
    from personalclaw.inbound import audit as inbound
    from personalclaw.resilience import remediation

    return [
        (
            "model calls",
            model_calls._audit_path,
            lambda: model_calls.record_attempt(_attempt("2026-11-21T00:00:00+00:00")),
        ),
        (
            "inbound clients",
            inbound._audit_path,
            lambda: inbound.audit("mcp", client_id="c", route="/mcp", status=200),
        ),
        (
            "feedback",
            feedback._path,
            lambda: feedback._append(
                feedback.FeedbackRecord(
                    id="f1",
                    target_kind="message",
                    target_id="m1",
                    verdict="up",
                    created_at="2026-11-21T00:00:00+00:00",
                )
            ),
        ),
        (
            "usage",
            usage_ledger._path,
            lambda: usage_ledger.record_turn(
                usage_ledger.TurnUsage(
                    ts="2026-11-21T00:00:00+00:00",
                    session_key="s",
                    source="chat",
                    agent="",
                    provider="p",
                    model="m",
                )
            ),
        ),
        (
            "sampling outcomes",
            sampling._outcomes_path,
            lambda: sampling._record_outcome(
                n=2, criteria="", winner_idx=0, candidates=[], judgments=[], prompt=""
            ),
        ),
        (
            "self-remediation",
            lambda: remediation._doctor_dir() / remediation._LEDGER_FILE,
            lambda: remediation._write_ledger(
                remediation.RunResult(score_before=50.0, score_after=60.0), now=1.0
            ),
        ),
        (
            "digest queue",
            notification_rules.digest_queue_path,
            lambda: notification_rules.queue_for_digest({"ts": "2026-11-21", "title": "t"}),
        ),
    ]


@pytest.mark.parametrize("case", range(7), ids=lambda i: f"append-{i}")
def test_each_bounded_log_s_append_waits_for_its_lock(home: Path, case: int) -> None:
    """🔴 Before: none of them took the file's lock, so none could be kept out of a trim's window.
    Another process holding it stands for a trim, clear or drain under way."""
    _name, where, append = _appends()[case]
    path = where()
    _waits_for_the_lock(path, append)
    assert path.read_text().strip(), "the append landed once it went ahead"


@pytest.mark.parametrize(
    "rewrite",
    ["trim", "digest drain", "digest retext", "spool drain", "spool clear"],
)
def test_each_rewrite_waits_for_its_lock(home: Path, rewrite: str) -> None:
    """A trim, the digest queue's drain and its rewording, and the spool's drain and clear all
    take the same lock as the appends."""
    from personalclaw import bounded_log, notification_rules

    if rewrite.startswith("digest"):
        path = notification_rules.digest_queue_path()
        path.write_text(json.dumps({"ts": "2026-11-21", "inbox_item": "i", "title": "t"}) + "\n")
        act: Callable[[], Any] = (
            notification_rules.drain_digest_queue
            if rewrite == "digest drain"
            else lambda: notification_rules.retext_digest_queue({"i": ("new", "words")})
        )
    elif rewrite.startswith("spool"):
        path = _spool(home, _line(1))
        act = (
            dispatch.drain_spool
            if rewrite == "spool drain"
            else lambda: dispatch.clear_spool(handled=1)
        )
    else:
        path = home / "a-log.jsonl"
        path.write_text("".join(json.dumps({"ts": i}) + "\n" for i in range(5)))
        act = lambda: bounded_log.trim_jsonl(path, 1, at="ts", past=2)  # noqa: E731
    _waits_for_the_lock(path, act)
