"""A resolved credential never reaches the run ledger ON DISK (criterion 11 — S138).

Criterion 11: *"`{{secret:KEY}}` never appears resolved in triggers.json, journals, **ledger**, or
`automation_history` output."*

**What already held, verified by driving rather than assumed** — the dispatch half is sound. A
trigger
whose bash command is `echo tok={{secret:MY_KEY}}` keeps the PLACEHOLDER in `triggers.json`, the
provider receives the RESOLVED value (it must, to authenticate), and after the fire the resolved
value
appears in no file under the home. S115 built that correctly.

🔴 THE GAP. The API's `_redact_run` cleans the RESPONSE, and nothing cleaned the WRITE. Measured:
a run
record whose `summary` carried a resolved credential — exactly what a bash action that echoes one
produces — was written in plaintext to **both** `cron-history/<job>.jsonl` and
`cron-history/_index.jsonl`:

    PLAINTEXT on disk in: ['cron-history/_index.jsonl', 'cron-history/clock:a.jsonl']

Both are 0600, but both are on disk, both are carried by `personalclaw snapshot` (S113), and
both are
readable by anything that reads the home. **Redacting only on read is a read-path control over a
storage-path leak** — and the criterion says "ledger", not "ledger responses".

Fixed at `_append_sync`, the single funnel every run record passes through, so a future caller
cannot
forget it. The per-call-site alternative is exactly how the injection-screen and
capability-fence gaps
happened.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore, _redact_stored

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
    run entirely, which is the silent drop criterion 8 bans."""
    monkeypatch.setattr(
        "personalclaw.security.redact_credentials",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    store = _write(tmp_path, summary=f"tok={SECRET}")
    _runs, total = asyncio.run(store.list_for_job("clock:a", 0, 5))
    assert total == 1


def test_the_redaction_is_at_the_SINGLE_write_point():
    """One funnel, so a future caller cannot forget it — the per-call-site alternative is how the
    injection-screen (S134) and capability-fence (S116) gaps happened."""
    import inspect

    from personalclaw.schedule_history import ScheduleRunStore as Store

    src = inspect.getsource(Store._append_sync)
    assert "_redact_stored(run.summary)" in src
    assert "_redact_stored(run.trace)" in src
    assert "_redact_stored(run.error)" in src


def test_it_reuses_the_SHARED_matcher():
    """Not a second regex set. A credential pattern added to `security` must cover this
    automatically
    — a private copy would drift, which is the lesson S115 recorded for the workflow lint."""
    import inspect

    src = inspect.getsource(_redact_stored)
    assert "redact_credentials" in src
    assert "redact_exfiltration_urls" in src


# ── the dispatch half, verified rather than assumed ──


def test_the_STORED_TRIGGER_keeps_the_placeholder(tmp_path):
    """S115's contract, re-asserted here because criterion 11 covers `triggers.json` too: resolution
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
