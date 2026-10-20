"""A resolved credential never reaches the run ledger ON DISK.

The promise: `{{secret:KEY}}` never appears resolved in triggers.json, journals, **ledger**, or
`automation_history` output.

**What already held, verified by driving rather than assumed** — the dispatch half is sound. A
trigger
whose bash command is `echo tok={{secret:MY_KEY}}` keeps the PLACEHOLDER in `triggers.json`, the
provider receives the RESOLVED value (it must, to authenticate), and after the fire the resolved
value
appears in no file under the home. That half was built correctly.

🔴 THE GAP. The API's `_redact_run` cleans the RESPONSE, and nothing cleaned the WRITE. Measured:
a run
record whose `summary` carried a resolved credential — exactly what a bash action that echoes one
produces — was written in plaintext to **both** `cron-history/<job>.jsonl` and
`cron-history/_index.jsonl`:

    PLAINTEXT on disk in: ['cron-history/_index.jsonl', 'cron-history/clock:a.jsonl']

Both are 0600, but both are on disk, both are carried by `personalclaw snapshot`, and
both are
readable by anything that reads the home. **Redacting only on read is a read-path control over a
storage-path leak** — and the promise says "ledger", not "ledger responses".

Fixed at `append_sync`, the single funnel every run record passes through, so a future caller
cannot
forget it. The per-call-site alternative is exactly how the injection-screen and
capability-fence gaps
happened.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from table_rows import row

from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore, _redact_stored

pytestmark = pytest.mark.security

SECRET = "sk-ant-api03-REALTOKEN123456789"


def _write(tmp_path, **kw) -> ScheduleRunStore:
    store = ScheduleRunStore(tmp_path)
    asyncio.run(store.append(ScheduleRun(run_id="r1", job_id="clock:a", status="success", **kw)))
    return store


def _disk_hits(tmp_path, needle: str) -> list[str]:
    return [
        str(f.relative_to(tmp_path))
        for f in tmp_path.rglob("*")
        if f.is_file() and needle in f.read_text(errors="replace")
    ]


# ── the defect ──


def test_a_credential_in_SUMMARY_is_not_written_to_disk(tmp_path):
    """🔴 THE DEFECT, pinned. This wrote plaintext to two files before the fix."""
    _write(tmp_path, summary=f"printed tok={SECRET}")
    assert _disk_hits(tmp_path, SECRET) == []


def test_a_credential_in_TRACE_is_not_written_to_disk(tmp_path):
    """The trace is the fuller field — a command's raw stdout lands here."""
    _write(tmp_path, trace=f"stdout: tok={SECRET}")
    assert _disk_hits(tmp_path, SECRET) == []


def test_a_credential_in_ERROR_is_not_written_to_disk(tmp_path):
    """🔴 The likeliest field of the three in practice: a failed authenticated request echoes the
    token back in its error. It was also the one with no cap and no redaction."""
    _write(tmp_path, error=f"401 unauthorized for token {SECRET}")
    assert _disk_hits(tmp_path, SECRET) == []


def test_BOTH_ledger_files_are_clean(tmp_path):
    """The store writes twice — the per-job file (with trace) and the cross-job index (without). A
    fix that cleaned one would leave the leak in the other."""
    _write(tmp_path, summary=f"tok={SECRET}", trace=f"tok={SECRET}")
    assert not any("cron-history" in p for p in _disk_hits(tmp_path, SECRET))


# ── the row stays USEFUL ──


def test_the_row_is_still_READABLE(tmp_path):
    """A redaction that ate the whole summary would trade a leak for an unusable history."""
    store = _write(tmp_path, summary=f"indexed 42 notes with tok={SECRET}")
    runs, _total = asyncio.run(store.list_for_job("clock:a", 0, 5))
    assert "indexed 42 notes" in runs[0]["summary"]
    assert "REDACTED" in runs[0]["summary"]


def test_the_row_still_round_trips(tmp_path):
    store = _write(tmp_path, summary="clean run")
    runs, total = asyncio.run(store.list_for_job("clock:a", 0, 5))
    assert total == 1
    assert runs[0]["summary"] == "clean run"
    assert runs[0]["status"] == "success"


# ── no false positives ──


@pytest.mark.parametrize(
    "summary",
    [
        "Indexed 42 notes in 1.2s",
        "3 new posts: Release 2.1, Docs update",
        "exit 0 — nothing to do",
        "wrote /Users/me/notes/summary.md",
        "HTTP 200 from https://api.example.com/v1/items",
        "commit a1b2c3d pushed to main",
    ],
)
def test_an_ORDINARY_summary_is_BYTE_IDENTICAL(summary):
    """This runs on EVERY run record. A rule that mangled real summaries would be worse than
    the leak
    — a user reads these to find out what their machine did."""
    assert _redact_stored(summary) == summary


def test_empty_fields_are_survived():
    assert _redact_stored("") == ""
    assert _redact_stored(None) == ""


# ── the discipline ──


def test_a_REDACTION_FAILURE_withholds_rather_than_stores_raw(monkeypatch):
    """🔴 The safe direction. Losing a summary is recoverable; writing a credential to disk
    is not."""
    monkeypatch.setattr(
        "personalclaw.security.redact_credentials",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = _redact_stored(f"tok={SECRET}")
    assert SECRET not in out
    assert "withheld" in out


def test_a_redaction_failure_does_NOT_lose_the_run_record(tmp_path, monkeypatch):
    """The record must still be written — a bookkeeping failure that dropped the row would hide the
    run entirely: a silent drop."""
    monkeypatch.setattr(
        "personalclaw.security.redact_credentials",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    store = _write(tmp_path, summary=f"tok={SECRET}")
    _runs, total = asyncio.run(store.list_for_job("clock:a", 0, 5))
    assert total == 1


def _sync_row(**fields):
    """A writer that hands one run straight to `append_sync`, the way a caller with no event loop
    does, never through the async `append`."""

    def write(home):
        ScheduleRunStore(home).append_sync(
            ScheduleRun(run_id="r1", job_id="clock:a", status="failure", **fields)
        )

    return write


def _reaper_row(reason):
    """The reaper's own row for a run it closed: a real caller of `append_sync` that redacts
    nothing itself."""
    from personalclaw.triggers import reaper

    def write(home):
        assert reaper._write_row(
            "clock:a",
            status=reaper.RESTART_INTERRUPTED_STATUS,
            reason=reason,
            name="clock a",
            started_at=1.0,
            now=2.0,
            source="clock",
            base_dir=home,
        )

    return write


def test_the_redaction_is_at_the_SINGLE_write_point(tmp_path, subtests):
    """One funnel, so a future caller cannot forget it — the per-call-site alternative is how the
    injection-screen and capability-fence gaps happened. The funnel is `append_sync`: the async
    `append` only hands a run to it, and the reaper's pass calls it directly. So a row written
    straight through it, each field holding the credential, reaches neither file in plaintext, and
    the reaper's row, whose writer leaves the redaction to the funnel, is clean too."""
    leak = f"tok={SECRET} and the rest"
    table = [
        ("summary", "summary", _sync_row(summary=leak)),
        ("trace", "trace", _sync_row(trace=leak)),
        ("error", "error", _sync_row(error=leak)),
        ("job_name", "job_name", _sync_row(job_name=leak)),
        ("the reaper's row", "error", _reaper_row(leak)),
    ]
    assert len(table) == 5
    for n, (door, field, write) in enumerate(table):
        with row(subtests, door=door):
            home = tmp_path / f"home-{n}"
            write(home)
            ledger = home / "cron-history"
            files = sorted(p.name for p in ledger.glob("*.jsonl"))
            assert files == ["_index.jsonl", "clock:a.jsonl"], f"{door}: wrote {files}"
            assert _disk_hits(home, SECRET) == [], f"{door}: the credential is on disk"
            for name in files:
                (stored,) = [json.loads(line) for line in (ledger / name).read_text().splitlines()]
                if field == "trace" and name == "_index.jsonl":
                    assert "trace" not in stored, f"{door}: the index row carries a trace"
                    continue
                assert (
                    stored[field] == "tok=[REDACTED: credential] and the rest"
                ), f"{door}: {name}'s {field} is {stored[field]!r}"


def test_it_reuses_the_SHARED_matcher(tmp_path, subtests, monkeypatch):
    """Not a second regex set. A credential pattern added to `security` must cover this
    automatically
    — a private copy would drift, the lesson already learned with the workflow lint.

    Each row is a token shape no matcher knows yet, added to one of `security`'s two shared sets:
    its credential patterns, and the patterns that mark a URL's query as carrying one. Before the
    addition the ledger keeps the token; once it is added there, and nowhere else, a run whose
    summary holds it is written with the token gone, as the shared masker writes it."""
    from personalclaw import security

    token = "ledgerzz-0f1e2d3c4b5a69788796a5b4"
    table = [
        ("_CREDENTIAL_PATTERNS", f"rotated {token} for the sync"),
        ("_EXFIL_PATTERNS", f"posted to https://collector.example.net/c?k={token} done"),
    ]
    assert len(table) == 2
    for matcher, text in table:
        with row(subtests, matcher=matcher):
            assert token in _redact_stored(text), f"{matcher}: a matcher already knows the token"
            with monkeypatch.context() as patch:
                shared = getattr(security, matcher)
                added = re.compile(f"(?:{shared.pattern})|ledgerzz-[0-9a-f]{{24}}", shared.flags)
                patch.setattr(security, matcher, added)
                home = tmp_path / matcher
                store = _write(home, summary=text)
                masked = security.redact_or_withhold(text)
                assert token not in masked, f"{matcher}: the added pattern masks nothing"
                assert _disk_hits(home, "ledgerzz") == [], f"{matcher}: the token is on disk"
                runs, _total = asyncio.run(store.list_for_job("clock:a", 0, 5))
                assert runs[0]["summary"] == masked, f"{matcher}: {runs[0]['summary']!r}"


# ── the dispatch half, verified rather than assumed ──


def test_the_STORED_TRIGGER_keeps_the_placeholder(tmp_path):
    """The contract, re-asserted here because the promise covers `triggers.json` too: resolution
    happens at DISPATCH, so the row on disk never holds a value."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:auth",
            name="auth",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"inline": {"provider": "bash", "config": {"command": "tok={{secret:K}}"}}},
        )
    )
    disk = (tmp_path / "triggers.json").read_text()
    assert "{{secret:K}}" in disk
