"""A security-log row a process that holds no key reports is signed by the gateway, as reported.

The tool server an agent CLI runs (``personalclaw mcp-core``) runs in the CLI's sandbox, which now
keeps the security log's key from it and the log unchangeable by it. Measured on main before this:
the server read the key, signed each row and appended it itself, so any program in that sandbox
could do the same, and a row it wrote passed the log's check. Now the server hands each row to the
gateway (``sel.report_rows_to_the_gateway``), or leaves it in the spool when the gateway does not
answer, and the gateway takes only the event's own fields, stamps who reported it, and signs it
(``sel_intake``). The end-to-end run through a real gateway is
``test_an_agent_clis_tools_save_in_a_home_its_sandbox_fences``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from personalclaw import sel as sel_module
from personalclaw import sel_intake
from personalclaw.config.loader import config_dir
from personalclaw.sel import SecurityEvent, SecurityEventLog, sel


@pytest.fixture(autouse=True)
def _nothing_taken_yet(monkeypatch):
    """Each test starts with no row taken lately (``sel_intake._TAKEN_IDS``)."""
    monkeypatch.setattr(sel_intake, "_TAKEN_IDS", type(sel_intake._TAKEN_IDS)())


def _row(**overrides) -> dict:
    row = {
        "event_id": "a1b2c3",
        "timestamp": "2026-11-13T12:00:00+00:00",
        "event_type": "tool_invocation",
        "caller_identity": "dashboard:abc",
        "agent": "personalclaw",
        "source": "mcp",
        "operation": "skill_search",
        "outcome": "completed",
    }
    row.update(overrides)
    return row


def _logged() -> list[dict]:
    log = config_dir() / "security_events.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_a_reported_row_keeps_only_its_own_fields_and_says_who_reported_it():
    """The sender's hashes, its caller scope and a stamp of its own are not taken as the gateway's:
    the gateway's log computes the chain, stamps its own scope, and keeps the sender's as its
    claim, in the one stamp the gateway writes."""
    sent = _row(
        prev_hash="forged",
        entry_hash="forged",
        caller_scope="skill_ladder",
        not_a_field="dropped",
        metadata={"reported_by": {"process": "the gateway"}, "kept": 1},
    )
    event = sel_intake.event_of(
        sent, process="mcp-core", pid=42, session="dashboard:abc", spooled=False
    )
    assert event is not None
    assert sel_intake.take(event)

    (row,) = _logged()
    assert row["prev_hash"] != "forged" and row["entry_hash"] != "forged"
    assert row["caller_scope"] == ""
    assert "not_a_field" not in row
    assert row["metadata"] == {
        "kept": 1,
        "reported_by": {
            "process": "mcp-core",
            "pid": 42,
            "session": "dashboard:abc",
            "spooled": False,
            "caller_scope": "skill_ladder",
        },
    }
    assert sel()._record_is_authentic(row)


@pytest.mark.parametrize(
    "row",
    [
        "not an object",
        _row(outcome=3),
        _row(event_id=""),
        _row(event_id="../../etc"),
        _row(operation=""),
        _row(metadata=["a", "list"]),
    ],
    ids=[
        "not an object",
        "a field not text",
        "no id",
        "an id the log never makes",
        "no operation",
        "metadata not an object",
    ],
)
def test_what_is_not_a_row_is_not_taken(row):
    assert sel_intake.event_of(row, process="mcp-core", pid=1, session="", spooled=False) is None


def test_a_row_reported_twice_is_written_once():
    """A row whose answer was lost on its way back is spooled as well: it is kept once."""
    event = sel_intake.event_of(_row(), process="mcp-core", pid=1, session="", spooled=False)
    again = sel_intake.event_of(_row(), process="mcp-core", pid=1, session="", spooled=True)
    assert event is not None and again is not None

    assert sel_intake.take(event) and not sel_intake.take(again)
    assert [r["event_id"] for r in _logged()] == ["a1b2c3"]


def test_the_spool_is_taken_in_once_signed_as_spooled():
    """Each spooled row is signed in once, stamped as spooled; a file that is no row is said and
    removed; a file being written is left; and a file a gateway that stopped had claimed is taken
    in once its process is gone."""
    home = config_dir()
    sel_module.spool_row(home, _row(event_id="s1"), process="mcp-core")
    sel_module.spool_row(home, _row(event_id="s2"), process="mcp-core")
    spool = home / "sel_spool"
    (spool / "junk.json").write_text("{not json")
    (spool / "tmpabc.tmp").write_text("being written")
    gone = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    stale = spool / f".taken-{gone.stdout.strip()}-99-1-x.json"
    stale.write_text(json.dumps({"row": _row(event_id="s3"), "process": "mcp-core", "pid": 99}))

    assert sel_intake.take_in_spool() == 3
    assert sel_intake.take_in_spool() == 0

    rows = {r["event_id"]: r for r in _logged()}
    assert sorted(rows) == ["s1", "s2", "s3"]
    for row in rows.values():
        assert row["metadata"]["reported_by"]["spooled"] is True, row
        assert sel()._record_is_authentic(row), row
    assert sorted(p.name for p in spool.iterdir()) == ["tmpabc.tmp"]


def test_a_process_that_reports_reads_no_key_and_writes_no_row(tmp_path, monkeypatch):
    """A process that hands its rows to the gateway never reads the key, never makes one, and
    never appends to the log: each row goes to the gateway without a hash, and one the gateway
    did not take goes to the spool."""
    sent: list[dict] = []
    answers = iter([True, False])
    monkeypatch.setattr(sel_module, "_REPORTER", None)
    monkeypatch.setattr(sel_module, "_REPORTED_BY_PROCESS", "")
    sel_module.report_rows_to_the_gateway(
        lambda row: sent.append(row) or next(answers), process="mcp-core"
    )
    home = tmp_path / "home"
    home.mkdir()
    log = SecurityEventLog(base_dir=home)
    for event_id in ("r1", "r2"):
        log.log(SecurityEvent(**_row(event_id=event_id)))

    assert [row["event_id"] for row in sent] == ["r1", "r2"]
    assert all("entry_hash" not in row and "prev_hash" not in row for row in sent)
    assert sorted(p.name for p in home.iterdir()) == ["sel_spool"], "a key or a log was made"
    (spooled,) = (home / "sel_spool").iterdir()
    payload = json.loads(spooled.read_text())
    assert payload["row"]["event_id"] == "r2" and payload["process"] == "mcp-core"
    assert payload["pid"] == os.getpid()


def test_a_row_neither_takes_is_said_in_the_processs_own_log(tmp_path, monkeypatch, caplog):
    """No gateway and no spool: the row is said, masked, and what wrote it goes on."""
    monkeypatch.setattr(sel_module, "_REPORTER", None)
    monkeypatch.setattr(sel_module, "_REPORTED_BY_PROCESS", "")
    sel_module.report_rows_to_the_gateway(lambda row: False, process="mcp-core")
    home = tmp_path / "home"
    home.mkdir()
    (home / "sel_spool").write_text("a file where the spool would be")
    log = SecurityEventLog(base_dir=home)

    secret = "fake-row-secret-0123456789"
    log.log(SecurityEvent(**_row(event_id="lost", resources=f"password={secret} next")))

    said = " ".join(r.getMessage() for r in caplog.records)
    assert "did not take this row and its spool could not" in said and '"lost"' in said
    assert secret not in said and "[REDACTED: credential]" in said


def test_a_spooled_row_the_log_cannot_take_stays_for_the_next(monkeypatch):
    home = config_dir()
    sel_module.spool_row(home, _row(event_id="later"), process="mcp-core")

    def full(_self, _event):
        raise OSError(28, "No space left on device")

    real = SecurityEventLog.log
    monkeypatch.setattr(SecurityEventLog, "log", full)
    assert sel_intake.take_in_spool() == 0
    monkeypatch.setattr(SecurityEventLog, "log", real)
    assert len(list((home / "sel_spool").iterdir())) == 1
    assert sel_intake.take_in_spool() == 1
    assert [r["event_id"] for r in _logged()] == ["later"]


def _spooled_payload(event_id: str) -> str:
    return json.dumps({"row": _row(event_id=event_id), "process": "mcp-core", "pid": 7})


def test_a_spool_that_is_a_link_is_not_taken_from(tmp_path, caplog):
    """🔴 Red before this. The spool is opened as it is: a link in its place is said, and nothing
    where it points is read or removed."""
    home = config_dir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "kept.json").write_text('{"not": "a row"}')
    (elsewhere / "row.json").write_text(_spooled_payload("through-a-link"))
    shutil.rmtree(home / "sel_spool", ignore_errors=True)
    (home / "sel_spool").symlink_to(elsewhere, target_is_directory=True)

    assert sel_intake.take_in_spool() == 0
    assert sorted(p.name for p in elsewhere.iterdir()) == ["kept.json", "row.json"]
    assert (elsewhere / "kept.json").read_text() == '{"not": "a row"}'
    assert _logged() == []
    assert "did not take in the security log's spool" in caplog.text


def test_a_spool_others_can_write_is_not_taken_from(caplog):
    """🔴 Red before this. A spool others can write is said, and nothing in it is read or
    removed."""
    home = config_dir()
    sel_module.spool_row(home, _row(event_id="shared"), process="mcp-core")
    spool = home / "sel_spool"
    spool.chmod(0o777)
    try:
        assert sel_intake.take_in_spool() == 0
        assert len(list(spool.iterdir())) == 1 and _logged() == []
        assert "writable by others" in caplog.text
    finally:
        spool.chmod(0o700)


def test_a_link_in_the_spool_is_neither_read_nor_followed(tmp_path):
    """🔴 Red before this. Only a regular file in the spool is read: a link in it is dropped, and
    the file it names is left as it is."""
    home = config_dir()
    (home / "sel_spool").mkdir(exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text(_spooled_payload("followed"))
    (home / "sel_spool" / "linked.json").symlink_to(outside)

    assert sel_intake.take_in_spool() == 0
    assert outside.read_text() == _spooled_payload("followed")
    assert _logged() == []
    assert list((home / "sel_spool").iterdir()) == []


def test_a_pipe_in_the_spool_does_not_hold_the_gateway():
    """🔴 Red before this. A pipe in the spool is dropped at once, and the rows beside it are
    taken."""
    home = config_dir()
    sel_module.spool_row(home, _row(event_id="beside"), process="mcp-core")
    os.mkfifo(home / "sel_spool" / "a-pipe.json")

    assert sel_intake.take_in_spool() == 1
    assert [r["event_id"] for r in _logged()] == ["beside"]
    assert list((home / "sel_spool").iterdir()) == []


def test_a_row_from_a_server_that_names_no_chat_goes_to_the_spool_unsent(monkeypatch):
    """🔴 Red before this: the tool server posted each row whether or not it named a chat, and the
    gateway refuses every internal call that names no work, so a row from a server not yet given
    a chat was a refused call in the gateway's own log. Such a row now goes to the spool with no
    call; a server that names its chat still hands each row to the gateway."""
    from personalclaw import mcp_core

    posted: list[str] = []
    monkeypatch.setattr(
        mcp_core, "_post", lambda route, *_a, **_k: posted.append(route) or {"ok": True}
    )

    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: "")
    assert mcp_core._report_row(_row()) is False
    assert posted == []

    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: "dashboard:abc")
    assert mcp_core._report_row(_row()) is True
    assert posted == [mcp_core.SECURITY_ROW_ROUTE]
