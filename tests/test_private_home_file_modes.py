"""The PersonalClaw home is private: its files are 0600, its directories 0700.

Measured on the real image: ``/data/config.json`` — which held the provider key typed in
Settings — was 0644, world-readable. The settings stores are written through ``atomic_write``,
and ``atomic_write`` defaulted to the umask mode, so every writer of every file that can hold a
secret (``config.json``, an app's ``data/config.json``, a provider instance record) produced a
0644 file unless its author remembered ``mode=0o600``. ``mcp.json`` went through a second raw
writer (``agent._atomic_json_write``) with a hardcoded 0644 for a new file, and three writers of
``config.json`` itself bypassed ``atomic_write`` altogether with a bare ``write_text``.

The rail is the writer itself: under the home it writes 0600 in a 0700 directory, and an explicit
wider mode is REFUSED rather than honoured, so no write path can produce a readable secret file.
The static legs below catch a wide literal mode handed to the writer anywhere in ``src/``, and a
``config.json`` write that goes around it.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import stat
from pathlib import Path

import pytest

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config import loader as config_loader

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


@pytest.fixture(autouse=True)
def _umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


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
    from personalclaw.agent import _atomic_json_write

    mcp = config_loader.config_dir() / "mcp.json"
    _atomic_json_write(mcp, {"mcpServers": {}})
    assert _mode(mcp) == 0o600, oct(_mode(mcp))

    mcp.chmod(0o644)  # an upgraded home: the file already exists, world-readable
    _atomic_json_write(mcp, {"mcpServers": {"x": {"command": "true"}}})
    assert _mode(mcp) == 0o600, "a rewrite preserved the loose mode"
    assert json.loads(mcp.read_text())["mcpServers"]["x"]["command"] == "true"


@functools.lru_cache(maxsize=1)
def _sources() -> tuple[tuple[Path, ast.Module], ...]:
    """Every module under ``src/``, parsed once for both static legs."""
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(SRC.rglob("*.py"))
    )


def _called_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _literal_mode(call: ast.Call) -> int | None:
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, int):
                return kw.value.value
    return None


def _opens_for_writing(call: ast.Call, *, mode_index: int) -> bool:
    mode = call.args[mode_index] if len(call.args) > mode_index else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and any(ch in str(mode.value) for ch in "wax+")


#: How source spells the settings files that carry secrets or their references: ``config.json``
#: (``config_path()``), ``mcp.json`` and the agent config it is copied into, whose MCP ``env``
#: blocks hold tokens (``agents_dir() / AGENT_FILENAME``, ``_installed_agent_config()``).
_SECRET_SETTINGS_CALLS = {"config_path", "_installed_agent_config"}
_SECRET_SETTINGS_LEAVES = {"config.json", "mcp.json", "personalclaw.json", "AGENT_FILENAME"}


def _names_a_secret_settings_file(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Call):
        return _called_name(expr) in _SECRET_SETTINGS_CALLS
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        leaf = expr.right
        if isinstance(leaf, ast.Constant):
            return leaf.value in _SECRET_SETTINGS_LEAVES
        if isinstance(leaf, ast.Name):
            return leaf.id in _SECRET_SETTINGS_LEAVES
    return False


def test_secret_bearing_settings_files_are_written_only_through_atomic_write():
    """``config.json``, ``mcp.json`` and the agent config hold secrets or their references, so they
    are written the one way that makes a file private and whole. Six writes went around that
    with a bare ``write_text``: ``cli_chat`` CREATED ``config.json`` that way on a fresh install
    (0644); the agent-settings handlers rewrote ``config.json`` and the agent config in place (a
    crash mid-write truncating it); ``doctor``'s auto-fix rewrote the agent config in place,
    keeping whatever mode an older release had left it at; and the eval overlay wrote a cell
    home's ``config.json`` the same way.

    A name a function binds to one of those paths is the file it is about to touch; any
    ``write_text``/``write_bytes``/write-mode ``open`` on that name is an offender."""
    offenders: set[str] = set()
    for path, tree in _sources():
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {
                target.id
                for node in ast.walk(func)
                if isinstance(node, ast.Assign) and _names_a_secret_settings_file(node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if not names:
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                func_ = node.func
                if isinstance(func_, ast.Attribute) and isinstance(func_.value, ast.Name):
                    if func_.value.id in names and (
                        func_.attr in {"write_text", "write_bytes"}
                        or (func_.attr == "open" and _opens_for_writing(node, mode_index=0))
                    ):
                        offenders.add(f"{path.relative_to(SRC)}:{node.lineno}")
                elif isinstance(func_, ast.Name) and func_.id == "open" and node.args:
                    target = node.args[0]
                    if (
                        isinstance(target, ast.Name)
                        and target.id in names
                        and _opens_for_writing(node, mode_index=1)
                    ):
                        offenders.add(f"{path.relative_to(SRC)}:{node.lineno}")
    assert sorted(offenders) == []


def test_no_source_hands_the_atomic_writers_a_group_or_world_mode():
    """Static leg of the rail: a literal wide mode is a bug even on a path no test drives."""
    offenders = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"atomic_write", "atomic_write_bytes", "_atomic_write"}:
                continue
            mode = _literal_mode(node)
            if mode is not None and mode & 0o077:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} mode={oct(mode)}")
    assert offenders == []
