"""An unreadable config file must never be REPLACED by a write that only knows part of it.

Two defects, one shape: code read a config with a loader that returns ``{}`` (or swallows the
error), mutated the result, and wrote it back. When the file was genuinely absent that creates.
When the file EXISTED but could not be read — a permission blip, a concurrent write caught
mid-flush and therefore momentarily invalid JSON — the same code path DESTROYED it.

  1. ``dashboard/handlers/mcp.py`` — ``_set_scope_entry`` is called with ``~/.claude.json``, a file
     PersonalClaw does not own. Claude Code keeps projects, history and auth state there, not just
     ``mcpServers``, so "enable one MCP server" could replace the user's entire Claude Code config
     with a one-server dict, taking every other server's API keys with it.
  2. ``config/loader.py`` — ``AppConfig.save()`` preserved ``providers``/``use_cases``/``slack``
     from the existing file inside ``except Exception: pass``. A failed read fell through to the
     write with those keys absent, deleting every configured model provider and every stored
     provider API key — on the path every settings toggle uses.

Both writes are ATOMIC, which is why neither left partial-file evidence behind.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

# ── 1. ~/.claude.json and PersonalClaw's own mcp.json ────────────────────────────────────────────


def _mcp():
    from personalclaw.dashboard.handlers import mcp

    return mcp


def test_absent_file_still_loads_as_empty() -> None:
    """The safe case must keep working: absent → {} → the write CREATES."""
    m = _mcp()
    assert m._load_json_for_update(Path("/nonexistent/definitely/not/here.json")) == {}


def test_unparseable_file_raises_rather_than_loading_empty(tmp_path: Path) -> None:
    m = _mcp()
    p = tmp_path / "claude.json"
    p.write_text('{"mcpServers": {"a": {"command": "x"}}, TRUNCATED', encoding="utf-8")
    with pytest.raises(m.ConfigUnreadable):
        m._load_json_for_update(p)


def test_non_object_json_raises(tmp_path: Path) -> None:
    """A JSON array parses fine and is still not a config; `{}` would have been written over it."""
    m = _mcp()
    p = tmp_path / "claude.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(m.ConfigUnreadable):
        m._load_json_for_update(p)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a chmod-000 file")
def test_unreadable_file_raises(tmp_path: Path) -> None:
    m = _mcp()
    p = tmp_path / "claude.json"
    p.write_text('{"mcpServers": {}}', encoding="utf-8")
    p.chmod(0o000)
    try:
        with pytest.raises(m.ConfigUnreadable):
            m._load_json_for_update(p)
    finally:
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_set_scope_entry_refuses_and_leaves_the_file_byte_identical(tmp_path: Path) -> None:
    """🔴 THE DEFECT ITSELF. A malformed ~/.claude.json holding other servers' secrets must come
    out of an enable attempt UNCHANGED, not replaced by a one-server dict."""
    m = _mcp()
    p = tmp_path / "claude.json"
    original = (
        '{"projects": {"/work": {"history": ["secret-prompt"]}},\n'
        ' "mcpServers": {"other": {"command": "srv", "env": {"API_KEY": "sk-live-DO-NOT-LOSE"}}},\n'
        " TRUNCATED-BY-A-CONCURRENT-WRITE"
    )
    p.write_text(original, encoding="utf-8")
    before = p.read_bytes()

    outcome = m._set_scope_entry(p, "newsrv", enabled=True, spec={"command": "new"})

    assert outcome == "unreadable", "the refusal must be reported, not silently a no-op"
    assert p.read_bytes() == before, "the file was modified — this is the data loss"
    assert b"sk-live-DO-NOT-LOSE" in p.read_bytes()


def test_set_scope_entry_still_works_on_a_readable_file(tmp_path: Path) -> None:
    """The guard must not break the normal path — and must preserve the untouched keys."""
    m = _mcp()
    p = tmp_path / "claude.json"
    p.write_text(
        json.dumps({"projects": {"/work": {"history": ["keep me"]}}, "mcpServers": {}}),
        encoding="utf-8",
    )
    outcome = m._set_scope_entry(p, "newsrv", enabled=True, spec={"command": "new"})
    assert outcome == "added"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["mcpServers"]["newsrv"] == {"command": "new"}
    assert data["projects"]["/work"]["history"] == ["keep me"], "unrelated keys must survive"


def test_set_scope_entry_creates_an_absent_file(tmp_path: Path) -> None:
    m = _mcp()
    p = tmp_path / "nested" / "claude.json"
    assert m._set_scope_entry(p, "srv", enabled=True, spec={"command": "x"}) == "added"
    assert json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["srv"] == {"command": "x"}


# ── 2. AppConfig.save() ──────────────────────────────────────────────────────────────────────────


def test_save_refuses_when_the_existing_config_is_unreadable(tmp_path, monkeypatch) -> None:
    """🔴 THE SECOND DEFECT. `providers` holds the user's model-provider API keys; a failed read
    must abort the save rather than write a config without them."""
    from personalclaw.config import loader as L

    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(
        '{"providers": {"anthropic": {"api_key": "sk-ant-DO-NOT-LOSE"}}, BROKEN',
        encoding="utf-8",
    )
    before = cfg.read_bytes()
    monkeypatch.setattr(L, "config_path", lambda: cfg)

    c = L.AppConfig()
    with pytest.raises(L.ConfigPreserveError):
        c.save()

    assert cfg.read_bytes() == before, "the config was rewritten without its providers block"
    assert b"sk-ant-DO-NOT-LOSE" in cfg.read_bytes()


def test_save_still_preserves_the_blocks_on_a_readable_config(tmp_path, monkeypatch) -> None:
    from personalclaw.config import loader as L

    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "providers": {"anthropic": {"api_key": "sk-keep"}},
                "use_cases": {"chat": "anthropic"},
                "slack": {"token": "xoxb-keep"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "config_path", lambda: cfg)

    L.AppConfig().save()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["providers"] == {"anthropic": {"api_key": "sk-keep"}}
    assert data["use_cases"] == {"chat": "anthropic"}
    assert data["slack"] == {"token": "xoxb-keep"}
    assert "meta" in data, "the normal save still stamps meta"


def test_save_creates_a_config_that_does_not_exist_yet(tmp_path, monkeypatch) -> None:
    """Absent is the one case where writing without the blocks is correct."""
    from personalclaw.config import loader as L

    cfg = tmp_path / "home" / "config.json"
    monkeypatch.setattr(L, "config_path", lambda: cfg)
    L.AppConfig().save()
    assert cfg.is_file()
    assert "meta" in json.loads(cfg.read_text(encoding="utf-8"))


@pytest.mark.parametrize("blank", ["", "   ", "\n\n", "\t \n"])
def test_save_treats_an_EMPTY_file_as_absent_not_unreadable(tmp_path, monkeypatch, blank) -> None:
    """Zero bytes hold no block to preserve, so refusing would be a dead end, not a guard.

    A config truncated to nothing — a crashed write, a full disk, a bare `touch` — has no
    `providers`/`use_cases`/`slack` to lose. Refusing there would leave the user unable to save
    config ever again, with no recovery path anywhere in the product, which is strictly worse
    than the data loss this guard exists to prevent. The refusal is for a file whose CONTENT
    cannot be known; a file that HAS no content is the `absent` case one line up.
    """
    from personalclaw.config import loader as L

    cfg = tmp_path / "config.json"
    cfg.write_text(blank, encoding="utf-8")
    monkeypatch.setattr(L, "config_path", lambda: cfg)
    L.AppConfig().save()
    written = json.loads(cfg.read_text(encoding="utf-8"))
    assert "meta" in written
    # And it must not invent the blocks it had nothing to preserve.
    assert "providers" not in written and "use_cases" not in written


def test_a_file_holding_only_whitespace_and_junk_is_still_REFUSED(tmp_path, monkeypatch) -> None:
    """The empty carve-out must not widen into "unparseable is fine if it is short"."""
    from personalclaw.config import loader as L

    cfg = tmp_path / "config.json"
    cfg.write_text("   {oops   ", encoding="utf-8")
    monkeypatch.setattr(L, "config_path", lambda: cfg)
    with pytest.raises(L.ConfigPreserveError):
        L.AppConfig().save()
    assert cfg.read_text(encoding="utf-8") == "   {oops   "
