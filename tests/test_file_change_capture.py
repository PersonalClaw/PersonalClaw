"""File-change capture/flush for the diff-chip feature.

A write_file/edit_file call snapshots ``before`` (off disk) and ``after``
(computed in memory from the call args), accumulates the change on the session,
and at turn end flushes a deduped, redacted ``meta.file_changes`` onto the last
assistant message. These tests pin the capture rules and the flush
dedup/redaction.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.dashboard import chat_runner as cr
from personalclaw.dashboard.state import _ChatSession


def _session(tmp: Path) -> _ChatSession:
    s = _ChatSession(key="t", workspace_dir=str(tmp))
    s._file_changes = []
    return s


def test_write_file_captures_new_file(tmp_path: Path):
    s = _session(tmp_path)
    cr._capture_file_change(s, "write_file", {"path": "new.txt", "content": "hello world"})
    assert len(s._file_changes) == 1
    c = s._file_changes[0]
    assert c["path"] == "new.txt"
    assert c["before"] == ""  # file did not exist
    assert c["after"] == "hello world"


def test_write_file_captures_overwrite(tmp_path: Path):
    (tmp_path / "f.txt").write_text("old content", encoding="utf-8")
    s = _session(tmp_path)
    cr._capture_file_change(s, "write_file", {"path": "f.txt", "content": "new content"})
    c = s._file_changes[0]
    assert c["before"] == "old content"
    assert c["after"] == "new content"


def test_edit_file_computes_after_in_memory(tmp_path: Path):
    (tmp_path / "code.py").write_text("def foo(): return 1\n", encoding="utf-8")
    s = _session(tmp_path)
    cr._capture_file_change(
        s, "edit_file", {"path": "code.py", "old_str": "return 1", "new_str": "return 2"}
    )
    c = s._file_changes[0]
    assert c["before"] == "def foo(): return 1\n"
    assert c["after"] == "def foo(): return 2\n"


def test_noop_write_is_skipped(tmp_path: Path):
    (tmp_path / "same.txt").write_text("identical", encoding="utf-8")
    s = _session(tmp_path)
    cr._capture_file_change(s, "write_file", {"path": "same.txt", "content": "identical"})
    assert s._file_changes == []  # before == after → no chip


def test_non_write_tool_ignored(tmp_path: Path):
    s = _session(tmp_path)
    cr._capture_file_change(s, "read_file", {"path": "x.txt"})
    cr._capture_file_change(s, "bash", {"command": "ls"})
    assert s._file_changes == []


def test_path_escape_is_skipped(tmp_path: Path):
    s = _session(tmp_path)
    cr._capture_file_change(s, "write_file", {"path": "../escape.txt", "content": "x"})
    assert s._file_changes == []  # outside workspace → not captured


def test_flush_dedups_by_path_first_before_last_after(tmp_path: Path):
    s = _session(tmp_path)
    # Two edits to the same file in one turn.
    s._file_changes = [
        {"path": "a.txt", "before": "v0", "after": "v1"},
        {"path": "a.txt", "before": "v1", "after": "v2"},
        {"path": "b.txt", "before": "", "after": "new"},
    ]
    s.messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "done"},
    ]
    cr._flush_file_changes(s)
    fc = s.messages[-1]["meta"]["file_changes"]
    by_path = {c["path"]: c for c in fc}
    assert set(by_path) == {"a.txt", "b.txt"}
    # a.txt: earliest before (v0), latest after (v2)
    assert by_path["a.txt"]["before"] == "v0"
    assert by_path["a.txt"]["after"] == "v2"
    assert s._file_changes == []  # accumulator cleared


def test_flush_redacts_secrets(tmp_path: Path):
    s = _session(tmp_path)
    s._file_changes = [
        {
            "path": "cfg",
            "before": "",
            "after": "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE0000000000000000000X",
        }
    ]
    s.messages = [{"role": "assistant", "content": "wrote config"}]
    cr._flush_file_changes(s)
    after = s.messages[-1]["meta"]["file_changes"][0]["after"]
    assert "AKIAIOSFODNN7EXAMPLE0000000000000000000X" not in after


def test_flush_noop_when_no_changes(tmp_path: Path):
    s = _session(tmp_path)
    s.messages = [{"role": "assistant", "content": "no files touched"}]
    cr._flush_file_changes(s)
    assert "meta" not in s.messages[-1] or "file_changes" not in s.messages[-1].get("meta", {})
