"""``bounded_log``: a bounded log keeps its newest rows by each row's own time, and a merge writes
it in time order — then every log in core that trims through it, read back with its rows out of
order, keeps the newest.

A log's rows go out of order whenever something other than its own appender writes it: a merge
restore puts an archive's older rows after the home's, a sync puts another machine's there. So
each trim below is handed a file whose newest rows sit at the FRONT, and must keep them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw import bounded_log

NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def _ids(path: Path, key: str = "id") -> list:
    return [json.loads(line)[key] for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── a row's time ─────────────────────────────────────────────────────────────────────────────


def test_a_rows_time_is_read_from_each_way_core_writes_one() -> None:
    utc = datetime(2026, 10, 1, 5, 17, 9, tzinfo=timezone.utc)
    assert bounded_log.instant(utc.isoformat()) == utc.timestamp()
    assert bounded_log.instant("2026-10-01T05:17:09Z") == utc.timestamp()
    assert bounded_log.instant(utc.timestamp()) == utc.timestamp()
    assert bounded_log.instant(42) == 42.0
    zone_less = "2026-10-01T05:17:09"
    assert bounded_log.instant(zone_less) == datetime.fromisoformat(zone_less).timestamp()


@pytest.mark.parametrize("value", [None, "", "  ", "yesterday", True, float("nan"), [], {}])
def test_a_value_that_names_no_moment_is_older_than_every_row_with_one(value) -> None:
    assert bounded_log.instant(value) == bounded_log.UNKNOWN
    assert bounded_log.instant(value) < bounded_log.instant(0)


def test_newest_keeps_the_newest_by_time_whatever_their_order() -> None:
    rows = [{"n": 3, "ts": _ago(1).isoformat()}, {"n": 1, "ts": _ago(3).isoformat()}]
    rows += [{"n": 4, "ts": _ago(0).isoformat()}, {"n": 2, "ts": _ago(2).isoformat()}]
    assert [r["n"] for r in bounded_log.newest(rows, 2, at="ts")] == [3, 4]
    assert [r["n"] for r in bounded_log.newest(rows, 9, at="ts")] == [1, 2, 3, 4]
    assert bounded_log.newest(rows, 0, at="ts") == []


def test_rows_at_one_time_keep_their_order_and_a_row_with_none_goes_first() -> None:
    same = _ago(1).isoformat()
    rows = [{"n": "a", "ts": same}, {"n": "x"}, {"n": "b", "ts": same}, {"n": "c", "ts": same}]
    assert [r["n"] for r in bounded_log.newest(rows, 3, at="ts")] == ["a", "b", "c"]


def test_newest_first_lists_by_time_whatever_their_order() -> None:
    rows = [{"n": 3, "ts": _ago(1).isoformat()}, {"n": 1, "ts": _ago(3).isoformat()}]
    rows += [{"n": 4, "ts": _ago(0).isoformat()}, {"n": 2, "ts": _ago(2).isoformat()}]
    assert [r["n"] for r in bounded_log.newest_first(rows, at="ts")] == [4, 3, 2, 1]


def test_newest_first_lists_the_last_written_of_one_time_first_and_a_row_with_none_last() -> None:
    same = _ago(1).isoformat()
    rows = [{"n": "a", "ts": same}, {"n": "x"}, {"n": "b", "ts": same}, {"n": "c", "ts": same}]
    assert [r["n"] for r in bounded_log.newest_first(rows, at="ts")] == ["c", "b", "a", "x"]


def test_a_rows_time_can_be_read_off_an_object() -> None:
    class Take:
        def __init__(self, n: int, at: str) -> None:
            self.n, self.at = n, at

    takes = [Take(2, _ago(1).isoformat()), Take(1, _ago(2).isoformat())]
    assert [t.n for t in bounded_log.in_time_order(takes, at=lambda t: t.at)] == [1, 2]


# ── trim_jsonl ───────────────────────────────────────────────────────────────────────────────


def test_a_log_within_twice_its_bound_is_not_written(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    log.write_text(_jsonl([{"id": i, "ts": i} for i in range(4)]), encoding="utf-8")
    before = log.stat().st_mtime_ns
    assert bounded_log.trim_jsonl(log, 2, at="ts") is False
    assert log.stat().st_mtime_ns == before


def test_a_trim_keeps_the_newest_lines_byte_for_byte_and_lets_a_line_with_no_row_go_first(
    tmp_path: Path,
) -> None:
    log = tmp_path / "log.jsonl"
    lines = ['{"id": 9, "ts": 9,   "pad": "kept as written"}', "not a row"]
    lines += [json.dumps({"id": i, "ts": i}) for i in (1, 2, 8, 3)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert bounded_log.trim_jsonl(log, 2, at="ts") is True
    assert log.read_text(encoding="utf-8").splitlines() == ['{"id": 8, "ts": 8}', lines[0]]


# ── merge_jsonl ──────────────────────────────────────────────────────────────────────────────


def test_a_merge_brings_in_what_the_log_lacks_in_time_order(tmp_path: Path) -> None:
    src, dst = tmp_path / "archive.jsonl", tmp_path / "home.jsonl"
    src.write_text(_jsonl([{"id": "a1", "ts": 1}, {"id": "h3", "ts": 3}, {"id": "a5", "ts": 5}]))
    dst.write_text(_jsonl([{"id": "h3", "ts": 3}, {"id": "h4", "ts": 4}, {"id": "h6", "ts": 6}]))
    assert bounded_log.merge_jsonl(src, dst, key="id", at="ts") == 2
    assert _ids(dst) == ["a1", "h3", "h4", "a5", "h6"]
    before = dst.stat().st_mtime_ns
    assert bounded_log.merge_jsonl(src, dst, key="id", at="ts") == 0, "a repeat brings nothing"
    assert dst.stat().st_mtime_ns == before, "and writes nothing"


def test_a_merge_keeps_every_line_the_home_holds_and_takes_no_line_that_holds_no_row(
    tmp_path: Path,
) -> None:
    src, dst = tmp_path / "archive.jsonl", tmp_path / "home.jsonl"
    src.write_text('not a row\n[1, 2]\n{"id": "a", "ts": 1}\n', encoding="utf-8")
    dst.write_text('{"id": "h",   "ts": 2}\nthe home\'s own odd line\n', encoding="utf-8")
    assert bounded_log.merge_jsonl(src, dst, key="id", at="ts") == 1
    assert dst.read_text(encoding="utf-8").splitlines() == [
        "the home's own odd line",
        '{"id": "a", "ts": 1}',
        '{"id": "h",   "ts": 2}',
    ]


def test_a_row_without_its_key_is_matched_on_its_whole_line(tmp_path: Path) -> None:
    src, dst = tmp_path / "archive.jsonl", tmp_path / "home.jsonl"
    src.write_text('{"ts": 1, "msg": "same"}\n{"ts": 2, "msg": "other"}\n', encoding="utf-8")
    dst.write_text('{"ts": 1, "msg": "same"}\n', encoding="utf-8")
    assert bounded_log.merge_jsonl(src, dst, key="id", at="ts") == 1


def test_a_line_appended_while_the_merge_ran_is_not_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = tmp_path / "archive.jsonl", tmp_path / "home.jsonl"
    src.write_text(_jsonl([{"id": "a", "ts": 1}]), encoding="utf-8")
    dst.write_text(_jsonl([{"id": "h", "ts": 2}]), encoding="utf-8")
    real = bounded_log.signature
    calls = {"n": 0}

    def appended_once_mid_merge(path: Path):
        calls["n"] += 1
        if calls["n"] == 2:  # after the first read, before the first write
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": "live", "ts": 3}) + "\n")
        return real(path)

    monkeypatch.setattr(bounded_log, "signature", appended_once_mid_merge)
    assert bounded_log.merge_jsonl(src, dst, key="id", at="ts") == 1
    assert _ids(dst) == ["a", "h", "live"]


def test_a_log_that_never_stops_changing_is_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = tmp_path / "archive.jsonl", tmp_path / "home.jsonl"
    src.write_text(_jsonl([{"id": "a", "ts": 1}]), encoding="utf-8")
    dst.write_text(_jsonl([{"id": "h", "ts": 2}]), encoding="utf-8")
    ticks = iter(range(10_000))
    monkeypatch.setattr(bounded_log, "signature", lambda path: next(ticks))
    with pytest.raises(bounded_log.LogKeptChanging):
        bounded_log.merge_jsonl(src, dst, key="id", at="ts")
    assert _ids(dst) == ["h"]


def test_a_merge_restore_names_the_log_it_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its last line then says the merge left that part unchanged, rather than "Merge complete"."""
    from personalclaw import snapshot

    src, dst = tmp_path / "archive.jsonl", tmp_path / "feedback.jsonl"
    src.write_text(_jsonl([{"id": "a", "created_at": 1}]), encoding="utf-8")
    dst.write_text(_jsonl([{"id": "h", "created_at": 2}]), encoding="utf-8")
    ticks = iter(range(10_000))
    monkeypatch.setattr(bounded_log, "signature", lambda path: next(ticks))
    left: list[str] = []
    imported = snapshot._merge_keyed_jsonl(
        src, dst, "id", "Feedback", at="created_at", left_unchanged=left
    )
    assert (imported, left) == (0, ["feedback.jsonl"])
    assert "left as it was" in capsys.readouterr().out
    assert _ids(dst) == ["h"]


# ── prune_table ──────────────────────────────────────────────────────────────────────────────


def test_a_table_keeps_its_newest_rows_by_time_not_its_highest_ids() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, created_ts REAL)")
    conn.executemany(
        "INSERT INTO samples VALUES (?, ?)", [(1, 50.0), (2, 60.0), (7, 10.0), (8, 20.0)]
    )
    assert bounded_log.prune_table(conn.cursor(), "samples", 2, at="created_ts") == 2
    assert [r[0] for r in conn.execute("SELECT id FROM samples ORDER BY id")] == [1, 2]


@pytest.mark.parametrize("table,at", [("samples; DROP TABLE x", "ts"), ("samples", "ts DESC")])
def test_a_name_that_is_not_a_plain_identifier_is_refused(table: str, at: str) -> None:
    with pytest.raises(ValueError):
        bounded_log.prune_table(sqlite3.connect(":memory:").cursor(), table, 1, at=at)


# ── every trim in core, handed a log whose newest rows come first ────────────────────────────


def _out_of_order(n: int, field: str, *, iso: bool, pad: int = 0) -> tuple[list[dict], list[str]]:
    """*n* rows whose file order is the reverse of their time order, and the ids of the newest
    two, oldest first."""
    rows = []
    for i in range(n):
        when = _ago(i + 1)
        rows.append({"id": f"r{i}", field: when.isoformat() if iso else when.timestamp()})
        if pad:
            rows[-1]["pad"] = "x" * pad
    return rows, ["r1", "r0"]


def _feedback(path: Path) -> None:
    from personalclaw import feedback

    feedback._maybe_trim(path)


def _model_calls(path: Path) -> None:
    from personalclaw.guardrails import audit

    audit._maybe_trim(path)


def _usage(path: Path) -> None:
    from personalclaw import usage_ledger

    usage_ledger._maybe_trim(path)


def _sampling(path: Path) -> None:
    from personalclaw import sampling

    sampling._trim_outcomes(path)


def _inbound(path: Path) -> None:
    from personalclaw.inbound import audit

    audit._maybe_trim(path)


def _digest(path: Path) -> None:
    from personalclaw import notification_rules

    notification_rules._trim_digest_queue(path)


TRIMS = [
    pytest.param(_feedback, "personalclaw.feedback._CAP", "created_at", False, 0, id="feedback"),
    pytest.param(
        _model_calls, "personalclaw.guardrails.audit._LINE_CAP", "ts", False, 0, id="model-calls"
    ),
    pytest.param(_usage, "personalclaw.usage_ledger._CAP", "ts", True, 0, id="usage-ledger"),
    pytest.param(
        _sampling, "personalclaw.sampling._MAX_OUTCOME_LINES", "ts", True, 0, id="sampling"
    ),
    pytest.param(
        _inbound, "personalclaw.inbound.audit._MAX_LINES", "ts", True, 120_000, id="inbound-audit"
    ),
    pytest.param(
        _digest, "personalclaw.notification_rules.DIGEST_QUEUE_CAP", "ts", True, 0, id="digest"
    ),
]


@pytest.mark.parametrize("trim,cap,field,iso,pad", TRIMS)
def test_each_jsonl_trim_keeps_the_newest_by_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trim, cap: str, field: str, iso: bool, pad: int
) -> None:
    monkeypatch.setattr(cap, 2)
    rows, newest = _out_of_order(5, field, iso=iso, pad=pad)
    log = tmp_path / "log.jsonl"
    log.write_text(_jsonl(rows), encoding="utf-8")
    trim(log)
    assert _ids(log) == newest


def test_the_source_event_spool_keeps_its_highest_sequence_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.knowledge import source_streams

    monkeypatch.setattr(source_streams, "MAX_SPOOL_RECORDS", 4)
    monkeypatch.setattr(source_streams, "TRIM_KEEP_RECORDS", 2)
    spool = source_streams.SourceEventSpool(tmp_path / "events.jsonl")
    spool.path.write_text(_jsonl([{"seq": s, "event": "e"} for s in (5, 4, 3, 2, 1)]))
    spool._maybe_trim()
    assert _ids(spool.path, "seq") == [4, 5]


def test_the_remediation_ledger_keeps_its_newest_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.resilience import remediation

    monkeypatch.setattr(remediation, "_doctor_dir", lambda: tmp_path)
    monkeypatch.setattr(remediation, "_LEDGER_CAP", 2)
    ledger = tmp_path / remediation._LEDGER_FILE
    ledger.write_text(_jsonl([{"ts": _ago(h).timestamp(), "jobs": [h]} for h in (2, 9, 8, 7)]))
    remediation._write_ledger(remediation.RunResult(1.0, 2.0, jobs=[0]), now=NOW.timestamp())
    assert [json.loads(line)["jobs"] for line in ledger.read_text().splitlines()] == [[2], [0]]


def test_an_apps_message_queue_keeps_its_newest_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.apps import messaging

    monkeypatch.setattr(messaging, "_MAX_QUEUE", 2)
    queue = tmp_path / "queue.json"
    monkeypatch.setattr(messaging, "_queue_path", lambda target: queue)
    older = [{"id": f"m{h}", "ts": _ago(h).isoformat()} for h in (1, 5, 4)]
    queue.write_text(json.dumps(older), encoding="utf-8")
    newest = messaging.AppMessage(
        id="m0", sender="one-app", target="other-app", type="t", payload="p", ts=NOW.isoformat()
    )
    messaging._append_to_queue("other-app", newest)
    assert [m["id"] for m in json.loads(queue.read_text())] == ["m1", "m0"]


def test_the_reversal_records_keep_the_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.guardrails import ladder

    monkeypatch.setattr(ladder, "_MAX_RECORDS", 2)
    monkeypatch.setattr(ladder, "_store_path", lambda: tmp_path / "reversals.json")
    records = [
        ladder.ReversalRecord(
            id=f"rv{h}", action_type="a", rung="r", handle="h", created_at=_ago(h).isoformat()
        )
        for h in (1, 3, 2, 0)
    ]
    ladder._save_records(records)
    saved = json.loads((tmp_path / "reversals.json").read_text())["records"]
    assert [r["id"] for r in saved] == ["rv1", "rv0"]


def test_a_files_drop_manifest_keeps_the_newest_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.workflows import filedrop

    monkeypatch.setattr(filedrop, "MAX_DROPPED_FILES", 2)
    monkeypatch.setattr(filedrop, "drop_dir", lambda run_id: tmp_path / run_id)
    (tmp_path / "run-1").mkdir()
    earlier = [{"filename": f"f{h}", "accepted_at": _ago(h).isoformat()} for h in (1, 4, 3)]
    (tmp_path / "run-1" / filedrop.DROP_MANIFEST).write_text(json.dumps(earlier))
    filedrop.record_drop("run-1", {"filename": "f0", "accepted_at": NOW.isoformat()})
    assert [r["filename"] for r in filedrop.read_manifest("run-1")] == ["f1", "f0"]
