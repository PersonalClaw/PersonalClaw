"""Tests for the config-tree FS watcher."""

from __future__ import annotations

from personalclaw.fs_watch import FS_WATCH_FEED, ConfigFsWatcher, _signature


# ── ConfigFsWatcher.scan_once ──
def test_first_scan_seeds_baseline_no_storm(tmp_path):
    """The first pass only records signatures — never reports a change."""
    (tmp_path / "config.json").write_text('{"a": 1}')
    (tmp_path / "agent.md").write_text("hello")
    events: list = []
    w = ConfigFsWatcher([tmp_path], publish=lambda *a: events.append(a))
    assert w.scan_once() == []  # seed pass
    assert events == []


def test_detects_modified_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"a": 1}')
    events: list = []
    w = ConfigFsWatcher([tmp_path], publish=lambda *a: events.append(a))
    w.scan_once()  # seed
    cfg.write_text('{"a": 2}')  # mtime+size change
    changed = w.scan_once()
    assert str(cfg) in changed
    assert events and events[0][0] == FS_WATCH_FEED
    assert events[0][1] == "changed"
    assert events[0][2] == {"path": str(cfg)}


def test_detects_new_file(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()  # seed
    new = tmp_path / "agents" / "x.md"
    new.parent.mkdir()
    new.write_text("new agent")
    changed = w.scan_once()
    assert str(new) in changed


def test_detects_deletion(tmp_path):
    f = tmp_path / "skill.md"
    f.write_text("body")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()  # seed
    f.unlink()
    changed = w.scan_once()
    assert str(f) in changed


def test_suffix_filter_ignores_unwatched(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    w = ConfigFsWatcher([tmp_path], publish=None)
    w.scan_once()  # seed
    (tmp_path / "scratch.bin").write_bytes(b"\x00\x01")  # not in suffixes
    assert w.scan_once() == []


def test_signature_missing_file_is_sentinel(tmp_path):
    assert _signature(tmp_path / "nope.json") == (0.0, -1)


def test_publish_failure_never_raises(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")

    def _boom(*_a):
        raise RuntimeError("sse down")

    w = ConfigFsWatcher([tmp_path], publish=_boom)
    w.scan_once()  # seed
    cfg.write_text('{"x": 1}')
    # Should swallow the publish error and still return the change.
    assert str(cfg) in w.scan_once()
