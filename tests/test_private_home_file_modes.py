"""The PersonalClaw home is private: its files are 0600, its directories 0700.

Measured on the real image: ``/data/config.json`` — which held the provider key typed in
Settings — was 0644, world-readable. The settings stores are written through ``atomic_write``,
and ``atomic_write`` defaulted to the umask mode, so every writer of every file that can hold a
secret (``config.json``, an app's ``data/config.json``, a provider instance record) produced a
0644 file unless its author remembered ``mode=0o600``. ``mcp.json`` went through a second raw
writer with a hardcoded 0644 for a new file (now ``atomic_json_write``, on ``_atomic_write``), and
three writers of ``config.json`` itself bypassed ``atomic_write`` altogether with a bare
``write_text``.

The rail is the writer itself: under the home it writes 0600 in a 0700 directory, and an explicit
wider mode is REFUSED rather than honoured, so no write path can produce a readable secret file.
The static legs below catch a wide literal mode handed to the writer anywhere in ``src/``, and a
``config.json`` write that goes around it.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import private_home_file_modes_shared
import pytest

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config import loader as config_loader

_umask = private_home_file_modes_shared._umask


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_file_written_under_the_home_is_0600_in_a_0700_directory():
    home = config_loader.config_dir()
    target = home / "some-store" / "record.json"

    atomic_write(target, "{}")
    atomic_write_bytes(home / "blob.bin", b"\x00")

    assert _mode(target) == 0o600, oct(_mode(target))
    assert _mode(target.parent) == 0o700, oct(_mode(target.parent))
    assert _mode(home / "blob.bin") == 0o600


def test_an_existing_loose_directory_is_tightened_when_a_file_is_written_into_it():
    home = config_loader.config_dir()
    loose = home / "apps" / "some-app" / "data"
    loose.mkdir(parents=True)
    loose.chmod(0o755)

    atomic_write(loose / "config.json", "{}")

    assert _mode(loose) == 0o700


def test_a_wider_explicit_mode_under_the_home_is_refused():
    home = config_loader.config_dir()

    with pytest.raises(ValueError, match="0o644"):
        atomic_write(home / "config.json", "{}", mode=0o644)
    assert not (home / "config.json").exists(), "the refused write still landed"


def test_a_file_outside_the_home_keeps_the_umask_default(tmp_path):
    """Scope guard: the rule is the HOME's, not every file PersonalClaw ever writes (an export
    the user saves into their Downloads folder is theirs to share)."""
    outside = tmp_path / "exported.json"

    atomic_write(outside, "{}")

    assert _mode(outside) == 0o644


def test_a_new_home_is_created_0700(tmp_path, monkeypatch):
    home = tmp_path / "new-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    config_loader.config_dir()

    assert _mode(home) == 0o700, oct(_mode(home))


def test_resolving_a_loose_home_does_not_change_it_but_writing_into_it_does(tmp_path, monkeypatch):
    """Permissions change on the WRITE path only. `config_dir()` runs on every resolution —
    including from module constants at import time — so a chmod there changed whatever home an
    import happened to resolve (the developer's real one, during test collection)."""
    home = tmp_path / "loose-home"
    home.mkdir()
    home.chmod(0o755)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    resolved = config_loader.config_dir()
    assert _mode(home) == 0o755, "resolving the home changed its mode"

    atomic_write(resolved / "config.json", "{}")
    assert _mode(home) == 0o700, oct(_mode(home))
    assert _mode(home / "config.json") == 0o600


def test_a_config_json_created_on_a_fresh_install_is_private():
    """``personalclaw chat`` on a fresh install creates ``config.json`` itself, and did so with a
    bare ``write_text`` at the umask mode — the file the provider references land in next."""
    from personalclaw.cli_chat import _ensure_default_agent_in_config

    cfg = config_loader.config_path()
    cfg.unlink(missing_ok=True)

    _ensure_default_agent_in_config()

    assert _mode(cfg) == 0o600, oct(_mode(cfg))
    assert json.loads(cfg.read_text(encoding="utf-8"))["default_agent"] == "default"


def test_mcp_json_is_private_whether_new_or_rewritten():
    """`mcp.json` carries MCP server env blocks, which hold tokens."""
    from personalclaw.atomic_write import atomic_json_write

    mcp = config_loader.config_dir() / "mcp.json"
    atomic_json_write(mcp, {"mcpServers": {}})
    assert _mode(mcp) == 0o600, oct(_mode(mcp))

    mcp.chmod(0o644)  # an upgraded home: the file already exists, world-readable
    atomic_json_write(mcp, {"mcpServers": {"x": {"command": "true"}}})
    assert _mode(mcp) == 0o600, "a rewrite preserved the loose mode"
    assert json.loads(mcp.read_text())["mcpServers"]["x"]["command"] == "true"
