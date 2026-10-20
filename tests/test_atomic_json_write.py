"""`atomic_json_write` is the one JSON writer, and it keeps every guarantee its old copy had.

`agent._atomic_json_write` was a second implementation of `atomic_write.py`'s temp-and-rename.
It counted as a durable-write duplicate in `structural-baseline.json`, and because it never
called the post-write hook, everything it wrote (`mcp.json`, the ACP agent configs, the `auth`
section of `config.json`, another tool's MCP file) landed on disk without reaching the seam
every other store's write reaches. It is now `atomic_write.atomic_json_write`, built on
`_atomic_write`, so those writes reach the seam and the home's 0600 rule applies through the
same code as every other home write.
"""

from __future__ import annotations

import errno
import json
import stat
from pathlib import Path

import pytest

from personalclaw import atomic_write as aw
from personalclaw.atomic_write import atomic_json_write, atomic_write
from personalclaw.config import loader as config_loader


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _refuse_rename(src, dst):
    raise OSError(errno.EBUSY, "Device or resource busy")


@pytest.fixture
def seen_writes():
    """A recording hook on the real seam, always unsubscribed."""
    paths: list[Path] = []

    def hook(path: Path) -> object:
        paths.append(Path(path))
        return None

    aw.register_post_write_hook(hook)
    try:
        yield paths
    finally:
        aw.unregister_post_write_hook(hook)


# ── outside the home the file is another program's, so it keeps its mode ─────────────────────


def test_a_rewrite_keeps_the_mode_the_file_already_has(tmp_path):
    target = tmp_path / "agent.json"
    target.write_text("{}")
    target.chmod(0o664)

    atomic_json_write(target, {"key": "value"})

    assert _mode(target) == 0o664
    assert json.loads(target.read_text()) == {"key": "value"}


def test_a_new_file_outside_the_home_is_0644(tmp_path):
    target = tmp_path / "new.json"
    atomic_json_write(target, {"new": True})
    assert _mode(target) == 0o644


def test_it_writes_indented_json_with_a_trailing_newline_and_no_temp_file(tmp_path):
    target = tmp_path / "doc.json"
    atomic_json_write(target, {"a": [1, 2]})
    assert target.read_text() == json.dumps({"a": [1, 2]}, indent=2) + "\n"
    assert [p.name for p in tmp_path.iterdir()] == ["doc.json"]


# ── a refused rename (a bind-mounted file) still lands the write ─────────────────────────────


def test_a_refused_rename_copies_the_new_content_over_the_file(tmp_path, monkeypatch):
    target = tmp_path / "mcp.json"
    target.write_text('{"old": true}\n')
    monkeypatch.setattr(aw.os, "replace", _refuse_rename)

    atomic_json_write(target, {"new": True})

    assert json.loads(target.read_text()) == {"new": True}
    assert [p.name for p in tmp_path.iterdir()] == ["mcp.json"], "the temp file was left behind"


def test_the_copy_fallback_is_the_json_writers_alone(tmp_path, monkeypatch):
    """A store written with `atomic_write` keeps rename-or-fail, as before."""
    monkeypatch.setattr(aw.os, "replace", _refuse_rename)
    with pytest.raises(OSError):
        atomic_write(tmp_path / "store.json", "{}")
    assert list(tmp_path.iterdir()) == []


def test_an_interrupted_write_leaves_no_temp_file(tmp_path, monkeypatch):
    def interrupt(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(aw.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_json_write(tmp_path / "doc.json", {"a": 1})
    assert list(tmp_path.iterdir()) == []


# ── the seam: what this writer writes now reaches the post-write hook ────────────────────────


def test_the_writer_reaches_the_post_write_seam(tmp_path, seen_writes):
    target = tmp_path / "doc.json"
    atomic_json_write(target, {"a": 1})
    assert seen_writes == [target]


def test_writing_mcp_json_reaches_the_seam(seen_writes):
    """`write_mcp_document` writes `mcp.json` and the agent config through the one writer."""
    from personalclaw.config.secret_refs import write_mcp_document

    path = config_loader.config_dir() / "mcp.json"
    write_mcp_document(path, {"mcpServers": {}})
    assert path in seen_writes


def test_setting_an_auth_field_reaches_the_seam(seen_writes):
    """`personalclaw auth enable` / `disable` edit `config.json` through the one writer."""
    from personalclaw.auth.cli import _set_auth_field

    _set_auth_field("login_enabled", False)

    config = config_loader.config_path()
    assert config in seen_writes
    assert json.loads(config.read_text(encoding="utf-8"))["auth"]["login_enabled"] is False
