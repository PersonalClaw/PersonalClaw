"""A merge restore brings an archive's older rows into a log that already holds newer ones, and
the next trim of that log keeps the newest rows by their own time and lets the oldest go.

🔴 The merge appended the archive's rows after the home's own, so the file stopped being in the
order its rows happened, and every bounded log trimmed by position: it kept the file's last lines.
Measured on a real home: a merge put 201 older notifications at the end of a 234-row log, and the
next notification trimmed it to the last 200 lines. The archive's old rows stayed, thirteen hours of
the newest went, and nothing said so. The same shape sat under the feedback log, the model-call
audit, the run history and the budget samples in ``learning.db``; the security log loses no row
(it rotates whole), but its readers took the merged-in rows for its newest events.

Each test here merges an archive older than the home into a home whose log is at its bound, adds
one row the way the product does, and reads back what a user is shown.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

#: A few minutes ago: a row the product stamps itself (a verdict, an event) is newer than every
#: row a test writes.
NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    return h


def _archive(tmp_path: Path, files: dict[str, str]) -> Path:
    """A snapshot archive holding *files*, as ``personalclaw snapshot`` lays one out."""
    stage = tmp_path / "stage" / "personalclaw-snapshot-20261001T215700Z"
    for rel, body in files.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    archive = tmp_path / "personalclaw-snapshot-20261001T215700Z.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname=stage.name)
    return archive


def _merge(archive: Path, components: str) -> None:
    from personalclaw.snapshot import restore_main

    assert restore_main([str(archive), "--mode", "merge", "--components", components]) == 0


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── notifications ────────────────────────────────────────────────────────────────────────────


def _note(when: datetime, title: str, *, acked: bool = False) -> dict:
    return {"kind": "info", "title": title, "body": "", "ts": when.isoformat(), "acked": acked}


def _state():
    from personalclaw.dashboard.state import DashboardState

    return DashboardState(sessions=MagicMock(count=0), start_time=0.0)


def test_the_next_notification_after_a_merge_keeps_the_newest_and_trims_the_oldest(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("personalclaw.dashboard.state._MAX_PERSISTED_NOTIFICATIONS", 10)
    # The home's own log: 12 notifications over the last 12 hours, newest last.
    mine = [_note(_ago(12 - i), f"mine-{i}") for i in range(12)]
    (home / "notifications.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    # The archive's: 10 from the day before, which the home no longer holds.
    theirs = [_note(_ago(48 - i), f"archived-{i}") for i in range(10)]
    _merge(_archive(tmp_path, {"notifications.jsonl": _jsonl(theirs)}), "notifications")

    state = _state()  # the gateway's next start reads the merged log
    state._append_notification(_note(NOW, "the next one"))

    on_disk = _read(home / "notifications.jsonl")
    newest = [f"mine-{i}" for i in range(3, 12)] + ["the next one"]
    assert [n["title"] for n in on_disk] == newest, "the newest 10 by time, in time order"
    assert [n["title"] for n in state._notification_log] == newest, "the bell shows what is kept"


def test_a_merge_into_a_running_gateways_home_survives_its_next_read_state_change(
    home: Path, tmp_path: Path
) -> None:
    """The dashboard's merge runs inside the gateway, and the gateway writes its whole log back
    on every ack: a copy read before the merge wrote the archive's rows away again."""
    mine = [_note(_ago(3 - i), f"mine-{i}") for i in range(3)]
    (home / "notifications.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    state = _state()  # running, its log read before the merge
    theirs = [_note(_ago(30 - i), f"archived-{i}") for i in range(2)]

    _merge(_archive(tmp_path, {"notifications.jsonl": _jsonl(theirs)}), "notifications")
    listed = [n["title"] for n in state._notification_log]
    assert state.ack_notification(mine[0]["ts"]) is True

    expected = ["archived-0", "archived-1", "mine-0", "mine-1", "mine-2"]
    assert listed == expected, "the bell lists what the merge brought in, without a restart"
    on_disk = _read(home / "notifications.jsonl")
    assert [n["title"] for n in on_disk] == expected
    assert [n["acked"] for n in on_disk] == [False, False, True, False, False]


# ── feedback ─────────────────────────────────────────────────────────────────────────────────


def _verdict(fid: str, when: datetime, target: str, verdict: str) -> dict:
    return {
        "id": fid,
        "created_at": when.timestamp(),
        "target_kind": "inbox_classification",
        "target_id": target,
        "verdict": verdict,
    }


def test_the_feedback_log_keeps_its_newest_verdicts_after_a_merge(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw import feedback

    monkeypatch.setattr(feedback, "_CAP", 3)
    feedback._invalidate()
    # The home's: four verdicts this morning; the last says the item-1 classification is right.
    mine = [_verdict(f"fb-mine-{i}", _ago(4 - i), f"item-{i}", "down") for i in range(3)]
    mine.append(_verdict("fb-mine-flip", _ago(0.5), "item-1", "up"))
    (home / "feedback.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    # The archive's: older verdicts, one of them the 👎 on item-1 she later changed her mind about.
    theirs = [_verdict(f"fb-old-{i}", _ago(72 - i), f"old-{i}", "down") for i in range(2)]
    theirs.append(_verdict("fb-old-item-1", _ago(70), "item-1", "down"))
    _merge(_archive(tmp_path, {"feedback.jsonl": _jsonl(theirs)}), "notifications")
    feedback._invalidate()

    assert feedback.current_verdict("inbox_classification", "item-1").verdict == "up"

    feedback.record_feedback(target_kind="inbox_classification", target_id="item-9", verdict="up")

    kept = [r["id"] for r in _read(home / "feedback.jsonl")]
    assert kept[:2] == ["fb-mine-2", "fb-mine-flip"], kept
    assert len(kept) == 3 and kept[2].startswith("fb_"), "the newest three by time"
    feedback._invalidate()
    assert feedback.current_verdict("inbox_classification", "item-1").verdict == "up"


# ── the model-call audit ─────────────────────────────────────────────────────────────────────


def _attempt(aid: str, when: datetime) -> dict:
    return {"audit_id": aid, "ts": when.timestamp(), "use_case": "chat", "provider": "p"}


def test_the_model_call_audit_keeps_its_newest_attempts_after_a_merge(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.guardrails import audit

    monkeypatch.setattr(audit, "_LINE_CAP", 3)
    mine = [_attempt(f"mine-{i}", _ago(6 - i)) for i in range(6)]
    (home / "model_calls.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    theirs = [_attempt(f"archived-{i}", _ago(90 - i)) for i in range(4)]
    _merge(_archive(tmp_path, {"model_calls.jsonl": _jsonl(theirs)}), "notifications")

    assert [r["audit_id"] for r in audit.read_recent(2)] == ["mine-4", "mine-5"]

    audit.record_attempt(
        audit.AttemptRecord(
            audit_id="the-next-one",
            ts=NOW.timestamp(),
            use_case="chat",
            provider="p",
            model="m",
            attempt=1,
        )
    )

    kept = [r["audit_id"] for r in _read(home / "model_calls.jsonl")]
    assert kept == ["mine-4", "mine-5", "the-next-one"]


# ── the run history ──────────────────────────────────────────────────────────────────────────


def _run(run_id: str, when: datetime) -> dict:
    t = when.timestamp()
    return {
        "run_id": run_id,
        "job_id": "morning-brief",
        "trigger": "scheduled",
        "started_at": t,
        "finished_at": t + 5,
        "status": "success",
    }


def test_the_run_history_keeps_its_newest_runs_after_a_merge(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw import schedule_history
    from personalclaw.schedule_history import ScheduleRunStore

    monkeypatch.setattr(schedule_history, "_MAX_RECORDS_PER_JOB", 5)
    monkeypatch.setattr(schedule_history, "_MAX_SUPPRESSED_PER_JOB", 1)
    monkeypatch.setattr(schedule_history, "_MAX_INDEX_PER_JOB", 5)
    monkeypatch.setattr(schedule_history, "_MAX_INDEX_RECORDS", 5)
    mine = [_run(f"mine-{i}", _ago(5 - i)) for i in range(5)]
    history = home / "cron-history"
    history.mkdir()
    (history / "morning-brief.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    (history / "_index.jsonl").write_text(_jsonl(mine), encoding="utf-8")
    theirs = [_run(f"archived-{i}", _ago(100 - i)) for i in range(4)]
    archive = _archive(
        tmp_path,
        {
            "cron-history/morning-brief.jsonl": _jsonl(theirs),
            "cron-history/_index.jsonl": _jsonl(theirs),
        },
    )
    _merge(archive, "crons")

    store = ScheduleRunStore(home)
    first, _ = store._list_for_job_sync("morning-brief", 0, 2)
    assert [r["run_id"] for r in first] == ["mine-4", "mine-3"], "newest first, as the page shows"

    store._rotate_all_sync()  # the gateway's next boot

    newest = [f"mine-{i}" for i in range(5)]
    assert [r["run_id"] for r in _read(history / "morning-brief.jsonl")] == newest
    assert [r["run_id"] for r in _read(history / "_index.jsonl")] == newest


# ── the security log ─────────────────────────────────────────────────────────────────────────


def test_the_security_log_reads_its_newest_events_first_after_a_merge(
    home: Path, tmp_path: Path
) -> None:
    """The log loses no row — it rotates whole — but the audit page and the recent-events read
    take the end of the file for its newest events."""
    from personalclaw import sel as sel_mod
    from personalclaw.snapshot import _merge_security_events

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    log = sel_mod.SecurityEventLog()
    for tool in ("before-1", "before-2"):
        log.log_tool_invocation(tool_name=tool, outcome="completed", session_key="s")
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "security_events.jsonl").write_bytes((home / "security_events.jsonl").read_bytes())
    (snap / "sel_hmac.key").write_bytes((home / "sel_hmac.key").read_bytes())
    log.rotate()  # the live log rotated since the snapshot was taken
    for tool in ("after-1", "after-2"):
        log.log_tool_invocation(tool_name=tool, outcome="completed", session_key="s")

    _merge_security_events(snap, home)

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    recent = sel_mod.SecurityEventLog().recent(4)
    assert [e.get("operation") for e in recent] == ["after-2", "after-1", "before-2", "before-1"]
    assert sel_mod.SecurityEventLog().verify_integrity(max_entries=None) == (4, 4)


# ── learning.db's budget samples ─────────────────────────────────────────────────────────────


def test_the_budget_samples_keep_the_newest_after_a_merge(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``INSERT OR IGNORE`` keeps the archive's row ids, so the merged-in samples took ids above
    the home's own, and the prune that kept the highest ids deleted the home's newest."""
    from personalclaw.learning.staging import StagingStore
    from personalclaw.snapshot import _merge_sqlite_attach

    monkeypatch.setattr(StagingStore, "ALLOCATION_KEEP", 5)
    elsewhere = tmp_path / "archived-home"
    elsewhere.mkdir()
    archived = StagingStore(elsewhere)
    for i in range(8):
        archived.record_allocation(used_tokens=1, budget_tokens=10, now=_ago(200 - i).timestamp())
    archived.close()
    mine = StagingStore(home)
    for i in range(3):
        mine.record_allocation(used_tokens=9, budget_tokens=10, now=_ago(3 - i).timestamp())
    mine.close()

    _merge_sqlite_attach(elsewhere / "learning.db", home / "learning.db", "learning.db")

    mine = StagingStore(home)
    mine.record_allocation(used_tokens=9, budget_tokens=10, now=NOW.timestamp())
    mine.close()
    with sqlite3.connect(home / "learning.db") as conn:
        kept = [
            row[0]
            for row in conn.execute("SELECT created_ts FROM allocation_samples ORDER BY created_ts")
        ]
    expected = [_ago(194).timestamp(), _ago(193).timestamp()]
    expected += [_ago(3 - i).timestamp() for i in range(3)] + [NOW.timestamp()]
    assert kept == expected[-5:], "the newest five by time"
