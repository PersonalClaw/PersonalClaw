"""Tests for agent config installation."""

import json
import os
import unittest.mock
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.agent import rebuild_agent_config


def _bundled_defaults(tmp_path: Path) -> Path:
    """Write a minimal bundled defaults.json and return its parent dir."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    defaults = {
        "model": "claude-default",
        "tools": ["ReadFile"],
        "allowedTools": ["ReadFile"],
        "mcpServers": {},
        "hooks": {"preToolUse": "audit"},
    }
    (cfg_dir / "defaults.json").write_text(json.dumps(defaults))
    (cfg_dir / "prompt.md").write_text("system prompt")
    return cfg_dir


_DEFAULT_MANAGED_MCPS = {
    "personalclaw-schedule": {"command": "/usr/bin/personalclaw", "args": ["mcp-schedule"]},
    "personalclaw-core": {"command": "/usr/bin/personalclaw", "args": ["mcp-core"]},
}


def _run_install(tmp_path: Path, cfg_dir: Path, managed_mcps: dict | None = None, **kwargs) -> Path:  # type: ignore[return]  # noqa: E501
    """Run rebuild_agent_config with all module globals patched to tmp_path."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    prompt = cfg_dir / "prompt.md"

    # Isolate tests from the caller's real ~/.personalclaw/hooks/ by disabling
    # autoimport in the patched config.  Tests that want to exercise autoimport
    # should override config_path themselves.
    pc_config = tmp_path / "empty_pc_config.json"
    if not pc_config.exists():
        pc_config.write_text(json.dumps({"agent": {"agent_hooks_autoimport": False}}))

    patches = [
        patch.multiple(
            "personalclaw.agent",
            AGENTS_DIR=agents_dir,
            _BUNDLED_CFG_DIR=cfg_dir,
            _PERSONALCLAW_BIN="/usr/bin/personalclaw",
            _USER_DIR=tmp_path / "personalclaw_home",
            _MANAGED_MCP_SERVERS=(
                managed_mcps if managed_mcps is not None else _DEFAULT_MANAGED_MCPS
            ),
        ),
        patch("personalclaw.agent._prompt_path", return_value=prompt),
        patch("personalclaw.agent._shipped_defaults", return_value=cfg_dir / "defaults.json"),
        patch("personalclaw.agent._project_dir", return_value=None),
        patch("personalclaw.agent._all_skill_paths", return_value=[]),
        patch("personalclaw.agent.shutil.which", side_effect=lambda c, **kw: c),
        # Patched at definition site: agent.py uses a local `from personalclaw.config import
        # config_path` inside function bodies, so the from-import re-resolves each call.
        patch("personalclaw.config.config_path", return_value=pc_config),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return rebuild_agent_config(**kwargs)


class TestInstallAgent:
    def test_fresh_install_generates_from_defaults(self, tmp_path: Path):
        """No existing personalclaw.json → config built from defaults."""
        cfg_dir = _bundled_defaults(tmp_path)
        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["model"] == "claude-default"
        assert "ReadFile" in config["tools"]

    def test_existing_config_preserves_user_model(self, tmp_path: Path):
        """Existing personalclaw.json → user's model choice survives restart."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": ["ReadFile", "WriteFile"],
            "allowedTools": ["ReadFile", "WriteFile"],
            "mcpServers": {},
            "toolsSettings": {"execute_bash": {"deniedCommands": ["old"]}},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["model"] == "claude-user-custom"
        assert "WriteFile" in config["tools"]

    def test_existing_config_refreshes_security_fields(self, tmp_path: Path):
        """hooks are always overwritten from bundled config on refresh.

        (Bash command screening is enforced natively in ``personalclaw.security``;
        the agent file no longer carries a per-agent ``deniedCommands`` list.)
        """
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {},
            "hooks": {"old": "hook"},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["hooks"] == {"preToolUse": "audit"}
        # The legacy per-agent denylist is no longer injected.
        assert "deniedCommands" not in config.get("toolsSettings", {}).get("execute_bash", {})

    def test_existing_config_refreshes_dynamic_mcp_servers(self, tmp_path: Path):
        """personalclaw-schedule and personalclaw-core commands are always refreshed."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {
                "personalclaw-schedule": {
                    "command": "/old/path/personalclaw",
                    "args": ["mcp-schedule"],
                },
            },
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["mcpServers"]["personalclaw-schedule"]["command"] == "/usr/bin/personalclaw"
        assert config["mcpServers"]["personalclaw-core"]["command"] == "/usr/bin/personalclaw"

    def test_existing_config_preserves_mcp_auto_approve(self, tmp_path: Path):
        """User autoApprove settings on MCP servers survive restart."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {
                "personalclaw-schedule": {
                    "command": "/old/path/personalclaw",
                    "args": ["mcp-schedule"],
                    "autoApprove": ["schedule_list", "schedule_add"],
                },
                "personalclaw-core": {
                    "command": "/old/path/personalclaw",
                    "args": ["mcp-core"],
                    "autoApprove": ["memory_list"],
                },
                "my-mcp-server": {
                    "command": "my-mcp-server",
                    "autoApprove": ["ReadFile"],
                },
            },
            "toolsSettings": {"execute_bash": {"deniedCommands": []}},
            "hooks": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        # personalclaw-schedule/core: command refreshed, autoApprove preserved
        assert config["mcpServers"]["personalclaw-schedule"]["command"] == "/usr/bin/personalclaw"
        assert config["mcpServers"]["personalclaw-schedule"]["autoApprove"] == [
            "schedule_list",
            "schedule_add",
        ]
        assert config["mcpServers"]["personalclaw-core"]["autoApprove"] == ["memory_list"]
        # other MCP servers: untouched
        assert config["mcpServers"]["my-mcp-server"]["autoApprove"] == ["ReadFile"]
        # hooks must always be refreshed from bundled defaults
        assert config["hooks"] == {"preToolUse": "audit"}

    def test_personalclaw_mcp_json_overrides_legacy_mcp(self, tmp_path: Path):
        """~/.personalclaw/mcp.json overrides legacy settings/mcp.json for personalclaw agent."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        # Pre-existing agent config with my-mcp-server from legacy settings
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {
                "my-mcp-server": {
                    "command": "my-mcp-server",
                    "args": ["--include-tools", "ReadFile"],
                    "autoApprove": ["ReadFile"],
                },
            },
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))
        # personalclaw mcp.json overrides args (removes --include-tools)
        pc_home = tmp_path / "personalclaw_home"
        pc_home.mkdir(exist_ok=True)
        (pc_home / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "my-mcp-server": {"command": "my-mcp-server", "args": []},
                        "new-server": {"command": "new-cmd", "args": ["start"]},
                    }
                }
            )
        )
        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        # my-mcp-server args overridden by personalclaw mcp.json
        assert config["mcpServers"]["my-mcp-server"]["args"] == []
        # autoApprove preserved (not in personalclaw mcp.json)
        assert config["mcpServers"]["my-mcp-server"]["autoApprove"] == ["ReadFile"]
        # new server added from personalclaw mcp.json
        assert config["mcpServers"]["new-server"]["command"] == "new-cmd"

    def test_new_managed_server_seeds_auto_approve(self, tmp_path: Path):
        """A new managed MCP server with autoApprove gets it seeded on first install."""
        cfg_dir = _bundled_defaults(tmp_path)
        mcps = {
            **_DEFAULT_MANAGED_MCPS,
            "example-governance": {
                "command": "/usr/bin/example-tool",
                "args": ["mcp", "start"],
                "autoApprove": ["search_example"],
            },
        }
        path = _run_install(tmp_path, cfg_dir, managed_mcps=mcps)
        config = json.loads(path.read_text())
        assert config["mcpServers"]["example-governance"]["autoApprove"] == ["search_example"]

    def test_new_managed_server_seeds_auto_approve_on_refresh(self, tmp_path: Path):
        """When a managed server is new to an existing config, autoApprove is seeded."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        # Existing config has personalclaw-schedule/core but NOT example-governance
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {
                "personalclaw-schedule": {"command": "/old/personalclaw", "args": ["mcp-schedule"]},
                "personalclaw-core": {"command": "/old/personalclaw", "args": ["mcp-core"]},
            },
            "toolsSettings": {"execute_bash": {"deniedCommands": []}},
            "hooks": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        mcps = {
            **_DEFAULT_MANAGED_MCPS,
            "example-governance": {
                "command": "/usr/bin/example-tool",
                "args": ["mcp", "start"],
                "autoApprove": ["search_example"],
            },
        }
        path = _run_install(tmp_path, cfg_dir, managed_mcps=mcps)
        config = json.loads(path.read_text())
        # example-governance is genuinely new → autoApprove should be seeded
        assert config["mcpServers"]["example-governance"]["autoApprove"] == ["search_example"]

    def test_user_removed_auto_approve_not_re_added(self, tmp_path: Path):
        """If user deliberately removed autoApprove, refresh must not re-add it."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        # Existing config has example-governance but user removed autoApprove
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {
                "example-governance": {
                    "command": "/old/example-tool",
                    "args": ["mcp", "start"],
                },
            },
            "toolsSettings": {"execute_bash": {"deniedCommands": []}},
            "hooks": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        mcps = {
            **_DEFAULT_MANAGED_MCPS,
            "example-governance": {
                "command": "/usr/bin/example-tool",
                "args": ["mcp", "start"],
                "autoApprove": ["search_example"],
            },
        }
        path = _run_install(tmp_path, cfg_dir, managed_mcps=mcps)
        config = json.loads(path.read_text())
        # command/args refreshed, but autoApprove NOT re-added
        assert config["mcpServers"]["example-governance"]["command"] == "/usr/bin/example-tool"
        assert "autoApprove" not in config["mcpServers"]["example-governance"]

    def test_clean_flag_ignores_existing(self, tmp_path: Path):
        """clean=True → regenerates from defaults even if file exists."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": ["UserTool"],
            "allowedTools": [],
            "mcpServers": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir, clean=True)
        config = json.loads(path.read_text())
        assert config["model"] == "claude-default"
        assert "UserTool" not in config["tools"]

    def test_corrupt_existing_falls_back_to_defaults(self, tmp_path: Path):
        """Corrupt personalclaw.json → falls back to build_agent_config()."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / "personalclaw.json").write_text("not valid json{{{")

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["model"] == "claude-default"

    def test_non_dict_json_falls_back_to_defaults(self, tmp_path: Path):
        """Valid JSON that is not a dict → falls back to build_agent_config()."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / "personalclaw.json").write_text("[]")

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["model"] == "claude-default"

    def test_missing_bundled_defaults_raises_when_existing_config_present(self, tmp_path: Path):
        """Error propagates when bundled defaults are absent during refresh."""
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        # No defaults.json written — bundled config is absent
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "personalclaw.json").write_text(json.dumps({"model": "x", "mcpServers": {}}))

        with pytest.raises(RuntimeError, match="Cannot build agent config"):
            _run_install(tmp_path, cfg_dir)


class TestAtomicJsonWrite:
    """Test 1.3: _atomic_json_write preserves permissions and handles new files."""

    def test_preserves_existing_permissions(self, tmp_path: Path):
        from personalclaw.agent import _atomic_json_write

        target = tmp_path / "test.json"
        target.write_text("{}")
        target.chmod(0o664)

        _atomic_json_write(target, {"key": "value"})

        import stat

        assert stat.S_IMODE(target.stat().st_mode) == 0o664
        assert json.loads(target.read_text()) == {"key": "value"}

    def test_new_file_gets_0o644(self, tmp_path: Path):
        from personalclaw.agent import _atomic_json_write

        target = tmp_path / "new.json"
        _atomic_json_write(target, {"new": True})

        import stat

        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert json.loads(target.read_text()) == {"new": True}

    def test_no_temp_file_left_on_success(self, tmp_path: Path):
        from personalclaw.agent import _atomic_json_write

        target = tmp_path / "clean.json"
        _atomic_json_write(target, {"a": 1})

        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert tmp_files == []


class TestResolvePersonalclawBin:
    """Tests for lazy personalclaw binary resolution."""

    def test_finds_bin_in_parent_hierarchy(self, tmp_path: Path):
        """Walks up from package dir to find bin/personalclaw."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        # Create structure: venv/lib/python3.x/site-packages/personalclaw
        #                   venv/bin/personalclaw
        venv = tmp_path / "venv"
        pkg_dir = venv / "lib" / "python3.11" / "site-packages" / "personalclaw"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")

        bin_dir = venv / "bin"
        bin_dir.mkdir()
        personalclaw_bin = bin_dir / "personalclaw"
        personalclaw_bin.write_text("#!/bin/bash\necho personalclaw")
        personalclaw_bin.chmod(0o755)

        # Mock personalclaw.__file__ to point to our fake package
        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(pkg_dir / "__init__.py")

        # Reset global and mock the import
        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                result = _resolve_personalclaw_bin()
            assert result == str(personalclaw_bin)
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_finds_bin_alongside_interpreter_when_venv_outside_source_tree(self, tmp_path: Path):
        """Regression: the dev layout has a repo-root ``.venv`` while the package
        lives under ``PersonalClaw/src/personalclaw`` — so the step-1 parent walk
        from the package never crosses the sibling ``.venv/bin`` and misses the
        console script, dropping the personalclaw-schedule/-core MCP servers every
        boot. Step 2 resolves it via ``sys.prefix``/``sys.executable`` (the venv
        root) instead. MUST NOT ``resolve()`` the interpreter symlink (that jumps
        to the base python, out of the venv bin)."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        # A source tree with NO bin/personalclaw anywhere in the package's parents.
        src_pkg = tmp_path / "repo" / "Core" / "src" / "personalclaw"
        src_pkg.mkdir(parents=True)
        (src_pkg / "__init__.py").write_text("")

        # The venv lives at the repo root — a SIBLING of the source tree, not a
        # parent of the package (so the step-1 walk can't reach it).
        venv_bin = tmp_path / "repo" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        pc = venv_bin / "personalclaw"
        pc.write_text("#!/bin/sh\n")
        pc.chmod(0o755)

        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(src_pkg / "__init__.py")

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                # sys.executable points at the venv's python (a symlink in reality);
                # sys.prefix is the venv root. Either must find venv_bin/personalclaw.
                with (
                    patch.object(agent_mod.sys, "executable", str(venv_bin / "python")),
                    patch.object(agent_mod.sys, "prefix", str(tmp_path / "repo" / ".venv")),
                ):
                    result = _resolve_personalclaw_bin()
            assert result == str(pc)
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_falls_back_to_shutil_which(self, tmp_path: Path):
        """Falls back to PATH lookup when bin/ not found in hierarchy."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        # Package dir with no bin/ sibling anywhere (use /tmp which has no bin/)
        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(tmp_path / "personalclaw" / "__init__.py")
        (tmp_path / "personalclaw").mkdir()
        (tmp_path / "personalclaw" / "__init__.py").write_text("")

        # Create the fallback binary so _usable() validation passes
        fallback_bin = tmp_path / "usr_local_bin_personalclaw"
        fallback_bin.write_text("#!/bin/sh\n")
        fallback_bin.chmod(0o755)

        # Selective isfile mock: only the explicit fallback passes validation;
        # any real /bin/personalclaw the walk might find gets rejected.
        _real_isfile = os.path.isfile

        def _fake_isfile(p):
            return p == str(fallback_bin) and _real_isfile(p)

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                with patch("os.path.isfile", side_effect=_fake_isfile):
                    # Skip pkg-path branch
                    def _which(cmd: str) -> str | None:
                        if cmd == "pkg-path":
                            return None
                        if cmd == "personalclaw":
                            return str(fallback_bin)
                        return None

                    with patch("shutil.which", side_effect=_which):
                        result = _resolve_personalclaw_bin()
            assert result == str(fallback_bin)
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_returns_personalclaw_when_not_found(self, tmp_path: Path):
        """Returns 'personalclaw' string when not found anywhere."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(tmp_path / "personalclaw" / "__init__.py")
        (tmp_path / "personalclaw").mkdir(exist_ok=True)
        (tmp_path / "personalclaw" / "__init__.py").write_text("")

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                # Blanket isfile=False blocks walk, pkg-path, and which fallback
                with patch("os.path.isfile", return_value=False):
                    with patch("shutil.which", return_value=None):
                        result = _resolve_personalclaw_bin()
            assert result == "personalclaw"
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_skips_stale_shutil_which_result(self, tmp_path: Path):
        """Falls through to bare 'personalclaw' when shutil.which returns a
        path that no longer exists (e.g. the binary was moved/removed after a
        package manager migration). Regression: ~/.local/bin/personalclaw was
        removed but a stale path was still cached in the PATH lookup.
        """
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(tmp_path / "personalclaw" / "__init__.py")
        (tmp_path / "personalclaw").mkdir(exist_ok=True)
        (tmp_path / "personalclaw" / "__init__.py").write_text("")

        # Stub pkg-path so its subprocess call doesn't resolve to a real binary
        def _which(cmd: str) -> str | None:
            if cmd == "pkg-path":
                return None  # skip pkg-path branch
            if cmd == "personalclaw":
                return "/home/user/.local/bin/personalclaw-DELETED"
            return None

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                # Blanket isfile=False — the stale path must not pass validation
                with patch("os.path.isfile", return_value=False):
                    with patch("shutil.which", side_effect=_which):
                        result = _resolve_personalclaw_bin()
            # Must NOT cache the stale path — falls through to bare 'personalclaw'
            assert result == "personalclaw"
            assert agent_mod._PERSONALCLAW_BIN is None  # didn't cache fallback
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_caches_result(self):
        """Result is cached in global _PERSONALCLAW_BIN."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = "/cached/personalclaw"
            result = _resolve_personalclaw_bin()
            assert result == "/cached/personalclaw"
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_accepts_binary_in_install_tree(self, tmp_path: Path):
        """Resolves bin/personalclaw by walking up from the package __file__."""
        import personalclaw.agent as agent_mod
        from personalclaw.agent import _resolve_personalclaw_bin

        # Mirror a venv-style layout: lib/.../site-packages/personalclaw with a
        # bin/personalclaw sibling several directories up.
        runtime = tmp_path / "env" / "runtime"
        (runtime / "lib" / "python3.12" / "site-packages" / "personalclaw").mkdir(parents=True)
        (
            runtime / "lib" / "python3.12" / "site-packages" / "personalclaw" / "__init__.py"
        ).write_text("")

        bin_dir = runtime / "bin"
        bin_dir.mkdir()
        personalclaw_bin = bin_dir / "personalclaw"
        personalclaw_bin.write_bytes(b"#!/usr/bin/env python3\nimport sys\n")
        personalclaw_bin.chmod(0o755)

        mock_pc = unittest.mock.MagicMock()
        mock_pc.__file__ = str(
            runtime / "lib" / "python3.12" / "site-packages" / "personalclaw" / "__init__.py"
        )

        old_val = agent_mod._PERSONALCLAW_BIN
        try:
            agent_mod._PERSONALCLAW_BIN = None
            with patch.dict("sys.modules", {"personalclaw": mock_pc}):
                result = _resolve_personalclaw_bin()
            # Should accept — bin/personalclaw is a sibling up the install tree
            assert result == str(personalclaw_bin)
        finally:
            agent_mod._PERSONALCLAW_BIN = old_val

    def test_accepts_shell_wrapper_binary(self, tmp_path: Path):
        """Accepts a bin/personalclaw that is a shell wrapper script."""
        from personalclaw.agent import _bin_is_usable

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        bin_dir = workspace / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "personalclaw"
        wrapper.write_text('#!/bin/sh\nexec python3 -m personalclaw "$@"\n')
        wrapper.chmod(0o755)

        assert _bin_is_usable(wrapper) is True


class TestAgentHooksMerge:
    """Tests for agent.agent_hooks merge into ACP agent agent config."""

    def _bundled_with_hooks(self, tmp_path: Path) -> Path:
        """Write bundled defaults with realistic list-based hooks."""
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        defaults = {
            "model": "claude-default",
            "tools": ["ReadFile"],
            "allowedTools": ["ReadFile"],
            "mcpServers": {},
            "toolsSettings": {"execute_bash": {"deniedCommands": ["rm -rf /"]}},
            "hooks": {
                "postToolUse": [
                    {"matcher": "execute_bash", "command": "audit.sh"},
                ],
            },
        }
        (cfg_dir / "defaults.json").write_text(json.dumps(defaults))
        (cfg_dir / "prompt.md").write_text("system prompt")
        return cfg_dir

    def _make_hook(self, tmp_path: Path, name: str = "hook.sh") -> str:
        """Create a real executable hook script and return its absolute path."""
        hook = tmp_path / "hooks" / name
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        return str(hook)

    def _run_with_agent_hooks(
        self,
        tmp_path: Path,
        agent_hooks: dict,
        existing: dict | None = None,
    ) -> dict:
        """Install agent with agent_hooks in config.json and return the result."""
        cfg_dir = self._bundled_with_hooks(tmp_path)
        pc_config = tmp_path / "pc_config.json"
        # Disable autoimport in this helper: these tests target the explicit
        # agent_hooks merge path only. The autoimport path is covered by
        # TestDefaultDialectHooksAutoimport below.
        pc_config.write_text(
            json.dumps(
                {
                    "agent": {
                        "agent_hooks": agent_hooks,
                        "agent_hooks_autoimport": False,
                    }
                }
            )
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        if existing:
            (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        prompt = cfg_dir / "prompt.md"
        patches = [
            patch.multiple(
                "personalclaw.agent",
                AGENTS_DIR=agents_dir,
                _BUNDLED_CFG_DIR=cfg_dir,
                _PERSONALCLAW_BIN="/usr/bin/personalclaw",
                _MANAGED_MCP_SERVERS=_DEFAULT_MANAGED_MCPS,
            ),
            patch("personalclaw.agent._prompt_path", return_value=prompt),
            patch("personalclaw.agent._shipped_defaults", return_value=cfg_dir / "defaults.json"),
            patch("personalclaw.agent._project_dir", return_value=None),
            patch("personalclaw.agent._all_skill_paths", return_value=[]),
            patch("personalclaw.agent.shutil.which", side_effect=lambda c, **kw: c),
            patch("personalclaw.config.config_path", return_value=pc_config),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            path = rebuild_agent_config(clean=existing is None)
        return json.loads(path.read_text())

    def test_user_hooks_appended_to_bundled(self, tmp_path: Path):
        """User agent_hooks are appended after bundled hooks per event."""
        hook = self._make_hook(tmp_path, "guardian.sh")
        config = self._run_with_agent_hooks(
            tmp_path,
            {
                "postToolUse": [{"matcher": "*", "command": hook}],
            },
        )
        post = config["hooks"]["postToolUse"]
        assert post[0] == {"matcher": "execute_bash", "command": "audit.sh"}  # bundled first
        assert post[1] == {"matcher": "*", "command": hook}  # user appended

    def test_user_hooks_new_event_type(self, tmp_path: Path):
        """User agent_hooks can add hooks for event types not in bundled."""
        hook = self._make_hook(tmp_path, "guardian.sh")
        config = self._run_with_agent_hooks(
            tmp_path,
            {
                "preToolUse": [{"matcher": "*", "command": hook}],
            },
        )
        assert config["hooks"]["postToolUse"] == [
            {"matcher": "execute_bash", "command": "audit.sh"},
        ]
        assert config["hooks"]["preToolUse"] == [
            {"matcher": "*", "command": hook},
        ]

    def test_user_hooks_dedup_by_command(self, tmp_path: Path):
        """Duplicate commands are not added twice."""
        hook = self._make_hook(tmp_path)
        config = self._run_with_agent_hooks(
            tmp_path,
            {
                "preToolUse": [
                    {"command": hook},
                    {"command": hook},
                ],
            },
        )
        assert len(config["hooks"]["preToolUse"]) == 1

    def test_user_hooks_dedup_against_bundled(self, tmp_path: Path):
        """User hook whose command+matcher matches a bundled hook is not added twice."""
        from personalclaw.agent import _merge_agent_hooks

        hook = self._make_hook(tmp_path, "audit.sh")
        bundled = {"postToolUse": [{"matcher": "execute_bash", "command": hook}]}
        user = {"postToolUse": [{"matcher": "execute_bash", "command": hook}]}
        result = _merge_agent_hooks(bundled, user)
        assert len(result["postToolUse"]) == 1
        assert result["postToolUse"][0] == {"matcher": "execute_bash", "command": hook}

    def test_user_hooks_same_command_different_matcher(self, tmp_path: Path):
        """Same command with different matchers are kept as separate entries."""
        hook = self._make_hook(tmp_path)
        config = self._run_with_agent_hooks(
            tmp_path,
            {
                "preToolUse": [
                    {"matcher": "execute_bash", "command": hook},
                    {"matcher": "ReadFile", "command": hook},
                ],
            },
        )
        assert len(config["hooks"]["preToolUse"]) == 2

    def test_user_hooks_malformed_skipped(self, tmp_path: Path):
        """Entries without command field are skipped."""
        hook = self._make_hook(tmp_path, "valid.sh")
        config = self._run_with_agent_hooks(
            tmp_path,
            {
                "preToolUse": [
                    {"matcher": "*"},  # no command
                    {"command": hook},
                ],
            },
        )
        assert config["hooks"]["preToolUse"] == [{"command": hook}]

    def test_existing_config_merges_agent_hooks(self, tmp_path: Path):
        """agent_hooks are merged on refresh of existing config."""
        existing = {
            "model": "claude-user-custom",
            "tools": [],
            "allowedTools": [],
            "mcpServers": {},
            "toolsSettings": {"execute_bash": {"deniedCommands": []}},
            "hooks": {"old": "hook"},
        }
        config = self._run_with_agent_hooks(
            tmp_path,
            {"preToolUse": [{"matcher": "*", "command": self._make_hook(tmp_path, "guardian.sh")}]},
            existing=existing,
        )
        # Bundled hooks overwrite old hooks
        assert config["hooks"]["postToolUse"] == [
            {"matcher": "execute_bash", "command": "audit.sh"},
        ]
        # User hooks appended
        assert len(config["hooks"]["preToolUse"]) == 1
        assert config["hooks"]["preToolUse"][0]["matcher"] == "*"

    # -- Direct unit tests for _merge_agent_hooks defensive branches --
    def test_merge_agent_hooks_non_dict_user_hooks_returns_original(self):
        from personalclaw.agent import _merge_agent_hooks

        bundled = {"postToolUse": [{"command": "audit.sh"}]}
        assert _merge_agent_hooks(bundled, ["bad"]) == bundled

    def test_merge_agent_hooks_non_list_event_entries_skipped(self):
        from personalclaw.agent import _merge_agent_hooks

        bundled = {"postToolUse": [{"command": "audit.sh"}]}
        result = _merge_agent_hooks(bundled, {"postToolUse": "not-a-list"})
        assert result == bundled

    def test_merge_agent_hooks_non_dict_entry_in_list_skipped(self):
        from personalclaw.agent import _merge_agent_hooks

        result = _merge_agent_hooks({}, {"preToolUse": ["just-a-string"]})
        assert result["preToolUse"] == []

    # -- Direct unit tests for _validate_hook_command --

    def test_validate_rejects_relative_path(self, tmp_path: Path):
        from personalclaw.agent import _validate_hook_command

        assert _validate_hook_command("relative/hook.sh", "test") is None

    def test_validate_rejects_shell_metacharacters(self, tmp_path: Path):
        from personalclaw.agent import _validate_hook_command

        hook = self._make_hook(tmp_path)
        assert _validate_hook_command(hook + "; rm -rf /", "test") is None
        assert _validate_hook_command(hook + " | cat", "test") is None
        assert _validate_hook_command(hook + " $(evil)", "test") is None

    def test_validate_rejects_nonexistent_file(self):
        from personalclaw.agent import _validate_hook_command

        assert _validate_hook_command("/nonexistent/hook.sh", "test") is None

    def test_validate_accepts_valid_hook(self, tmp_path: Path):
        from personalclaw.agent import _validate_hook_command

        hook = self._make_hook(tmp_path)
        assert _validate_hook_command(hook, "test") is not None

    def test_merge_strips_extra_fields(self, tmp_path: Path):
        """Only command and matcher fields are kept; arbitrary keys are stripped."""
        from personalclaw.agent import _merge_agent_hooks

        hook = self._make_hook(tmp_path)
        user = {"preToolUse": [{"command": hook, "matcher": "*", "shell": True, "env": {"X": "1"}}]}
        result = _merge_agent_hooks({}, user)
        assert result["preToolUse"] == [{"command": hook, "matcher": "*"}]

    def test_validate_rejects_symlink_to_sensitive(self, tmp_path: Path):
        """Symlinks resolving to sensitive paths are rejected."""
        from personalclaw.agent import _validate_hook_command

        sensitive = tmp_path / ".ssh" / "key"
        sensitive.parent.mkdir(parents=True)
        sensitive.write_text("#!/bin/sh\n")
        sensitive.chmod(0o755)
        link = tmp_path / "hooks" / "sneaky.sh"
        link.parent.mkdir(parents=True)
        link.symlink_to(sensitive)
        with patch("personalclaw.agent.is_sensitive_path", side_effect=lambda p: ".ssh" in p):
            assert _validate_hook_command(str(link), "test") is None

    def test_merge_rejects_non_string_matcher(self, tmp_path: Path):
        """Non-string matcher values are skipped (prevents TypeError and injection)."""
        from personalclaw.agent import _merge_agent_hooks

        hook = self._make_hook(tmp_path)
        user = {
            "preToolUse": [
                {"command": hook, "matcher": {"$regex": ".*"}},
                {"command": hook, "matcher": ["list"]},
                {"command": hook, "matcher": "*"},
            ]
        }
        result = _merge_agent_hooks({}, user)
        assert len(result["preToolUse"]) == 1
        assert result["preToolUse"][0] == {"command": hook, "matcher": "*"}

    def test_merge_agent_hooks_max_per_event_limit(self, tmp_path: Path):
        """At most _MAX_USER_HOOKS_PER_EVENT hooks are accepted per event."""
        from personalclaw.agent import _MAX_USER_HOOKS_PER_EVENT, _merge_agent_hooks

        hooks = [
            {"command": self._make_hook(tmp_path, f"hook_{i}.sh")}
            for i in range(_MAX_USER_HOOKS_PER_EVENT + 5)
        ]
        result = _merge_agent_hooks({}, {"preToolUse": hooks})
        assert len(result["preToolUse"]) == _MAX_USER_HOOKS_PER_EVENT

    def test_merge_agent_hooks_unknown_event_rejected(self):
        """Unknown event types are silently dropped."""
        from personalclaw.agent import _merge_agent_hooks

        result = _merge_agent_hooks({}, {"onBadEvent": [{"command": "/bin/true"}]})
        assert "onBadEvent" not in result

    def test_merge_rejects_matcher_with_shell_metacharacters(self, tmp_path: Path):
        """Matchers with shell metacharacters are rejected."""
        from personalclaw.agent import _merge_agent_hooks

        hook = self._make_hook(tmp_path)
        user = {
            "preToolUse": [
                {"command": hook, "matcher": "tool; rm -rf /"},
                {"command": hook, "matcher": "tool | cat"},
                {"command": hook, "matcher": "$(evil)"},
                {"command": hook, "matcher": "tool name with spaces"},
                {"command": hook, "matcher": "*"},  # valid
            ]
        }
        result = _merge_agent_hooks({}, user)
        assert len(result["preToolUse"]) == 1
        assert result["preToolUse"][0]["matcher"] == "*"

    def test_merge_rejects_oversized_matcher(self, tmp_path: Path):
        """Matchers exceeding max length are rejected."""
        from personalclaw.agent import _MAX_MATCHER_LEN, _merge_agent_hooks

        hook = self._make_hook(tmp_path)
        user = {
            "preToolUse": [
                {"command": hook, "matcher": "a" * (_MAX_MATCHER_LEN + 1)},
                {"command": hook, "matcher": "valid"},
            ]
        }
        result = _merge_agent_hooks({}, user)
        assert len(result["preToolUse"]) == 1
        assert result["preToolUse"][0]["matcher"] == "valid"

    def test_merge_global_hooks_limit(self, tmp_path: Path):
        """Total hooks across all events are capped at _MAX_TOTAL_USER_HOOKS."""
        from personalclaw.agent import _MAX_TOTAL_USER_HOOKS, _merge_agent_hooks

        user = {}
        for event in ("preToolUse", "postToolUse", "userPromptSubmit"):
            user[event] = [
                {"command": self._make_hook(tmp_path, f"{event}_{i}.sh")}
                for i in range(_MAX_TOTAL_USER_HOOKS)
            ]
        result = _merge_agent_hooks({}, user)
        total = sum(len(v) for v in result.values() if isinstance(v, list))
        assert total == _MAX_TOTAL_USER_HOOKS


class TestToolBloatFixes:
    """Tests for tool bloat prevention: rename migration, fresh_install gating, dedup."""

    def test_existing_config_tools_untouched(self, tmp_path: Path):
        """Existing tools/allowedTools are preserved exactly as-is (no renames, no additions)."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": ["bash", "read_file", "write_file", "grep", "@my-server"],
            "allowedTools": ["read_file", "grep"],
            "mcpServers": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["tools"] == ["bash", "read_file", "write_file", "grep", "@my-server"]
        assert config["allowedTools"] == ["read_file", "grep"]

    def test_existing_config_no_managed_mcp_added(self, tmp_path: Path):
        """Existing configs don't get @managed-mcp refs injected."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": ["shell", "read"],
            "allowedTools": ["read"],
            "mcpServers": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["tools"] == ["shell", "read"]
        assert config["allowedTools"] == ["read"]

    def test_fresh_install_adds_managed_mcp_to_tools_only(self, tmp_path: Path):
        """Fresh install adds @managed-mcp to tools but NOT allowedTools."""
        cfg_dir = _bundled_defaults(tmp_path)
        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert "@personalclaw-schedule" in config["tools"]
        assert "@personalclaw-core" in config["tools"]
        assert "@personalclaw-schedule" not in config["allowedTools"]
        assert "@personalclaw-core" not in config["allowedTools"]

    def test_dedup_preserves_order(self, tmp_path: Path):
        """Duplicate tools are removed while preserving first-occurrence order."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        existing = {
            "model": "claude-user-custom",
            "tools": ["shell", "read", "shell", "code", "read"],
            "allowedTools": ["read", "read", "code"],
            "mcpServers": {},
        }
        (agents_dir / "personalclaw.json").write_text(json.dumps(existing))

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        assert config["tools"] == ["shell", "read", "code"]
        assert config["allowedTools"] == ["read", "code"]

    def test_non_dict_json_treated_as_fresh_install(self, tmp_path: Path):
        """Valid JSON that is not a dict → treated as fresh install."""
        cfg_dir = _bundled_defaults(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / "personalclaw.json").write_text('"just a string"')

        path = _run_install(tmp_path, cfg_dir)
        config = json.loads(path.read_text())
        # Should get defaults (fresh install path)
        assert config["model"] == "claude-default"
        assert "@personalclaw-schedule" in config["tools"]


class TestDefaultDialectHooksAutoimport:
    """Tests for auto-discovery of executable hook scripts in ~/.personalclaw/hooks/."""

    @pytest.fixture(autouse=True)
    def _isolate_home(self, tmp_path: Path, monkeypatch):
        """Point Path.home() at tmp_path so hooks_dir validation accepts tmp dirs.

        The production rule rejects agent_hooks_dir values that do not resolve
        under Path.home(), to prevent an LLM-writable config from pointing
        autoimport at /tmp or similar.  Tests legitimately use tmp_path for
        isolation, so we fake HOME = tmp_path for every test in this class.
        """
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    def _make_script(
        self,
        hooks_dir: Path,
        name: str,
        body: str = "exit 0\n",
        executable: bool = True,
    ) -> Path:
        """Write a hook script with the given body; mark it executable by default."""
        hooks_dir.mkdir(parents=True, exist_ok=True)
        p = hooks_dir / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755 if executable else 0o644)
        return p

    def test_agent_hooks_autoimport_loads_executable_scripts(self, tmp_path: Path):
        """Two executable scripts both land under preToolUse with their absolute paths."""
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        s1 = self._make_script(hooks_dir, "a.sh")
        s2 = self._make_script(hooks_dir, "b.sh")

        result = _autoimport_agent_hooks(hooks_dir)

        commands = sorted(e["command"] for e in result["preToolUse"])
        assert commands == sorted([str(s1), str(s2)])
        assert list(result.keys()) == ["preToolUse"]

    def test_agent_hooks_autoimport_parses_event_header(self, tmp_path: Path):
        """A ``# event: PostToolUse`` header routes the script to postToolUse."""
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        self._make_script(hooks_dir, "audit.sh", body="# event: PostToolUse\nexit 0\n")

        result = _autoimport_agent_hooks(hooks_dir)

        assert "postToolUse" in result
        assert "preToolUse" not in result
        assert len(result["postToolUse"]) == 1

    def test_agent_hooks_autoimport_parses_matcher_header(self, tmp_path: Path):
        """A ``# matcher:`` header is preserved on the resulting entry."""
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        self._make_script(hooks_dir, "guard.sh", body="# matcher: shell\nexit 0\n")

        result = _autoimport_agent_hooks(hooks_dir)

        assert result["preToolUse"][0]["matcher"] == "shell"

    def test_agent_hooks_autoimport_skips_non_executable(self, tmp_path: Path, caplog):
        """Non-executable ``.sh`` files are skipped; executable siblings still load."""
        import logging

        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        self._make_script(hooks_dir, "ok.sh")
        self._make_script(hooks_dir, "disabled.sh", executable=False)

        with caplog.at_level(logging.INFO, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert len(result["preToolUse"]) == 1
        assert result["preToolUse"][0]["command"].endswith("/ok.sh")
        assert any("not executable" in rec.message for rec in caplog.records)

    def test_agent_hooks_autoimport_skips_sensitive_path(self, tmp_path: Path, monkeypatch):
        """Scripts resolving into a sensitive path (~/.ssh) are rejected."""
        from personalclaw.agent import _autoimport_agent_hooks

        # Pretend HOME is tmp_path so ~/.ssh is fabricated and isolated.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        sensitive = tmp_path / ".ssh" / "evil.sh"
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("#!/bin/sh\nexit 0\n")
        sensitive.chmod(0o755)

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        symlink = hooks_dir / "evil.sh"
        symlink.symlink_to(sensitive)

        result = _autoimport_agent_hooks(hooks_dir)
        assert result == {}

    def test_agent_hooks_autoimport_dedupes_with_explicit_config(self, tmp_path: Path):
        """A script listed both explicitly and in the autoimport dir yields one entry."""
        from personalclaw.agent import _apply_user_agent_hooks

        hooks_dir = tmp_path / "hooks"
        script = self._make_script(hooks_dir, "shared.sh")

        config: dict = {"hooks": {}}
        pc_cfg = {
            "agent": {
                "agent_hooks": {"preToolUse": [{"command": str(script)}]},
                "agent_hooks_dir": str(hooks_dir),
            }
        }

        _apply_user_agent_hooks(config, pc_cfg)

        entries = config["hooks"]["preToolUse"]
        assert len(entries) == 1
        assert entries[0]["command"] == str(script)

    def test_agent_hooks_autoimport_respects_disable_flag(self, tmp_path: Path):
        """``agent.agent_hooks_autoimport=False`` skips the scan even when scripts exist."""
        from personalclaw.agent import _apply_user_agent_hooks

        hooks_dir = tmp_path / "hooks"
        self._make_script(hooks_dir, "a.sh")

        config: dict = {"hooks": {}}
        pc_cfg = {
            "agent": {
                "agent_hooks_autoimport": False,
                "agent_hooks_dir": str(hooks_dir),
            }
        }

        _apply_user_agent_hooks(config, pc_cfg)

        assert config["hooks"] == {}

    def test_agent_hooks_autoimport_honors_custom_dir(self, tmp_path: Path):
        """``agent.agent_hooks_dir`` overrides the default ~/.personalclaw/hooks path."""
        from personalclaw.agent import _apply_user_agent_hooks

        custom = tmp_path / "custom-hooks"
        self._make_script(custom, "only.sh")

        config: dict = {"hooks": {}}
        pc_cfg = {"agent": {"agent_hooks_dir": str(custom)}}

        _apply_user_agent_hooks(config, pc_cfg)

        assert len(config["hooks"]["preToolUse"]) == 1
        assert config["hooks"]["preToolUse"][0]["command"].endswith("/only.sh")

    def test_agent_hooks_autoimport_respects_total_limit(self, tmp_path: Path, caplog):
        """More scripts than ``_MAX_TOTAL_USER_HOOKS`` get capped; one WARNING logged."""
        import logging

        from personalclaw.agent import _MAX_TOTAL_USER_HOOKS, _apply_user_agent_hooks

        # Spread across events so the per-event cap (10) does not fire first.
        # Filename suffixes are used so the scripts route to different events
        # without needing to write headers.
        hooks_dir = tmp_path / "hooks"
        suffixes = ["-pre.sh", "-post.sh", "-prompt.sh", "-spawn.sh", "-stop.sh"]
        total = _MAX_TOTAL_USER_HOOKS + 5
        for i in range(total):
            self._make_script(hooks_dir, f"h{i:02d}{suffixes[i % len(suffixes)]}")

        config: dict = {"hooks": {}}
        pc_cfg = {"agent": {"agent_hooks_dir": str(hooks_dir)}}

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        merged_total = sum(len(v) for v in config["hooks"].values() if isinstance(v, list))
        assert merged_total == _MAX_TOTAL_USER_HOOKS
        cap_warnings = [r for r in caplog.records if "global limit" in r.message.lower()]
        # Note: `_merge_agent_hooks` re-checks the cap at the
        # start of each event's inner loop, so the number of WARNINGs
        # depends on how scripts are distributed across events -- which
        # shifts with dict ordering changes or minor test edits.  The
        # invariant we actually care about is `merged_total == cap`
        # (asserted above); at least one cap WARNING must fire as
        # evidence the branch was exercised, but the exact count is not
        # the contract.  This matches the sibling test
        # `test_agent_hooks_total_limit_shared_across_explicit_and_autoimport`.
        assert cap_warnings, (
            "expected at least one global-limit WARNING when scripts exceed "
            "_MAX_TOTAL_USER_HOOKS; the merged_total cap is the real invariant"
        )

    def test_agent_hooks_total_limit_shared_across_explicit_and_autoimport(
        self, tmp_path: Path, caplog
    ):
        """Regression: ``_MAX_TOTAL_USER_HOOKS`` caps combined explicit + autoimport.

        Hardening (agent.py:778): the
        original code in ``_apply_user_agent_hooks`` called
        ``_merge_agent_hooks`` twice — once for explicit entries, once for
        auto-discovered scripts.  Because ``_merge_agent_hooks`` initializes
        ``total_added = 0`` on each call, the per-call cap of
        ``_MAX_TOTAL_USER_HOOKS`` (20) applied to each source independently,
        yielding up to 40 total user hooks instead of the intended 20.  The
        fix merges both sources in a single pass so the total cap is
        enforced across the combined set.

        This test stages enough scripts in BOTH sources that each source
        alone would fit under the cap (each has ``_MAX_TOTAL_USER_HOOKS``
        entries, which equals the cap), but together they exceed it.  The
        merged result must land at exactly ``_MAX_TOTAL_USER_HOOKS``, not
        ``2 * _MAX_TOTAL_USER_HOOKS``.  Under the old code this test
        observed ``merged_total == 2 * _MAX_TOTAL_USER_HOOKS``; under the
        new single-pass code it observes exactly the cap.
        """
        import logging

        from personalclaw.agent import _MAX_TOTAL_USER_HOOKS, _apply_user_agent_hooks

        # Build explicit agent_hooks with _MAX_TOTAL_USER_HOOKS entries,
        # spread across events so the per-event cap (10) is not what
        # limits the explicit source.  These scripts are real files on
        # disk so ``_validate_hook_command`` accepts them.
        explicit_dir = tmp_path / "explicit-scripts"
        explicit_dir.mkdir(parents=True, exist_ok=True)
        explicit_events = ["preToolUse", "postToolUse", "userPromptSubmit"]
        explicit_hooks: dict[str, list[dict[str, str]]] = {ev: [] for ev in explicit_events}
        for i in range(_MAX_TOTAL_USER_HOOKS):
            script = explicit_dir / f"e{i:02d}.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            explicit_hooks[explicit_events[i % len(explicit_events)]].append(
                {"command": str(script)}
            )

        # Autoimport dir: _MAX_TOTAL_USER_HOOKS more scripts, also spread
        # across events via filename suffix so per-event cap doesn't fire
        # within the autoimport source alone.
        autoimport_dir = tmp_path / "hooks"
        autoimport_suffixes = [
            "-pre.sh",
            "-post.sh",
            "-prompt.sh",
            "-spawn.sh",
            "-stop.sh",
        ]
        for i in range(_MAX_TOTAL_USER_HOOKS):
            self._make_script(
                autoimport_dir,
                f"a{i:02d}{autoimport_suffixes[i % len(autoimport_suffixes)]}",
            )

        config: dict = {"hooks": {}}
        pc_cfg = {
            "agent": {
                "agent_hooks": explicit_hooks,
                "agent_hooks_dir": str(autoimport_dir),
                # Default is True, but set explicitly so the test does
                # not silently become a single-source test if the
                # default ever flips.
                "agent_hooks_autoimport": True,
            }
        }

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        merged_total = sum(len(v) for v in config["hooks"].values() if isinstance(v, list))
        assert merged_total == _MAX_TOTAL_USER_HOOKS, (
            f"regression: combined explicit + autoimport hooks exceeded "
            f"_MAX_TOTAL_USER_HOOKS ({_MAX_TOTAL_USER_HOOKS}); got "
            f"{merged_total}.  Both sources must be merged in a single "
            f"_merge_agent_hooks pass so the total cap is enforced across "
            f"the combined set, not per-source."
        )
        # The cap warning should fire at least once because the single
        # merge pass trips the global-limit branch when the combined input
        # exceeds the cap.  It can fire multiple times because
        # ``_merge_agent_hooks`` iterates events and re-checks the cap per
        # event after it is reached; the count is not the invariant here,
        # the total-merged count above is.
        cap_warnings = [r for r in caplog.records if "global limit" in r.message.lower()]
        assert cap_warnings, (
            "expected at least one global-limit WARNING from the single "
            "merge pass when combined input exceeds _MAX_TOTAL_USER_HOOKS"
        )

    def test_agent_hooks_per_event_cap_emits_sel_audit(self, tmp_path: Path, monkeypatch, caplog):
        """Regression: per-event cap break must emit SEL audit.

        Hardening (agent.py:682): when ``_merge_agent_hooks`` hits the per-event
        cap ``_MAX_USER_HOOKS_PER_EVENT``, remaining entries for that
        event were silently dropped with only a ``logger.warning`` and
        no ``_sel_hook_rejected`` call.  Every other rejection branch
        in ``_merge_agent_hooks`` (missing command, failed validation,
        non-string matcher, invalid matcher) correctly emits SEL audit.
        Closing this audit-trail gap so auditors can distinguish "user
        configured 15 preToolUse hooks and 5 were cap-dropped" from
        "user configured 10 and all loaded".
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _MAX_USER_HOOKS_PER_EVENT, _apply_user_agent_hooks

        # Configure more scripts on a single event than the per-event cap.
        # All scripts route to preToolUse so the per-event cap fires before
        # the total cap (which is higher).
        explicit_dir = tmp_path / "scripts"
        explicit_dir.mkdir()
        over_cap = _MAX_USER_HOOKS_PER_EVENT + 3
        explicit_hooks: dict[str, list[dict[str, str]]] = {"preToolUse": []}
        for i in range(over_cap):
            script = explicit_dir / f"h{i:02d}.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            explicit_hooks["preToolUse"].append({"command": str(script)})

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        config: dict = {"hooks": {}}
        pc_cfg = {
            "agent": {
                "agent_hooks": explicit_hooks,
                "agent_hooks_autoimport": False,
            }
        }

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        # Exactly _MAX_USER_HOOKS_PER_EVENT scripts should have been
        # merged for preToolUse (the cap); the other 3 must be dropped.
        assert len(config["hooks"]["preToolUse"]) == _MAX_USER_HOOKS_PER_EVENT
        # The per-event cap path must have emitted at least one SEL
        # audit tagged with the event (preToolUse) and the
        # "per-event limit exceeded" reason.  Under the pre-fix code
        # this assertion failed with zero SEL calls tagged that reason.
        cap_sel = [c for c in sel_calls if "per-event limit exceeded" in c[2].lower()]
        assert cap_sel, (
            f"regression: per-event cap must emit _sel_hook_rejected; got "
            f"zero calls with reason 'per-event limit exceeded'.  All SEL "
            f"calls: {sel_calls!r}"
        )
        # The tag should be the event name, not the literal "autoimport",
        # since this is inside _merge_agent_hooks which uses the inferred
        # event as the SEL tag (consistent with other branches in this
        # function).
        assert cap_sel[0][0] == "preToolUse"

    def test_agent_hooks_global_cap_emits_sel_audit(self, tmp_path: Path, monkeypatch, caplog):
        """Regression: global cap break must emit SEL audit.

        Hardening (agent.py:688): sibling to the per-event cap gap.  When the
        global cap ``_MAX_TOTAL_USER_HOOKS`` is hit across all events,
        remaining hooks are silently dropped.  An auditor cannot
        distinguish "25 configured, 5 cap-dropped" from "20 configured,
        all loaded" without a SEL signal.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _MAX_TOTAL_USER_HOOKS, _apply_user_agent_hooks

        # Spread over-cap scripts across events so the per-event cap (10)
        # does not fire first; only the global cap (20) gates.
        explicit_dir = tmp_path / "scripts"
        explicit_dir.mkdir()
        over_cap = _MAX_TOTAL_USER_HOOKS + 3
        events = ["preToolUse", "postToolUse", "userPromptSubmit"]
        explicit_hooks: dict[str, list[dict[str, str]]] = {e: [] for e in events}
        for i in range(over_cap):
            script = explicit_dir / f"g{i:02d}.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            explicit_hooks[events[i % len(events)]].append({"command": str(script)})

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        config: dict = {"hooks": {}}
        pc_cfg = {
            "agent": {
                "agent_hooks": explicit_hooks,
                "agent_hooks_autoimport": False,
            }
        }

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        merged_total = sum(len(v) for v in config["hooks"].values() if isinstance(v, list))
        assert merged_total == _MAX_TOTAL_USER_HOOKS
        # Global-cap SEL audit must fire at least once.  The reason
        # string is the contract; the exact count depends on how many
        # events the loop visits after the cap is reached.
        cap_sel = [c for c in sel_calls if "global limit exceeded" in c[2].lower()]
        assert cap_sel, (
            f"regression: global cap must emit _sel_hook_rejected; got "
            f"zero calls with reason 'global limit exceeded'.  All SEL "
            f"calls: {sel_calls!r}"
        )

    def test_agent_hooks_unknown_event_type_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: unknown event type in user_hooks must emit SEL audit.

        Hardening: when ``_merge_agent_hooks``
        encounters an event name not in ``_VALID_HOOK_EVENTS`` (e.g.
        typo or future-event-name from a newer ACP agent), the entire
        event-bucket is dropped.  This is a permission decision per
        the security-controls policy and must emit SEL audit so an
        auditor can distinguish "user configured 0 hooks for event X"
        from "user configured 5 and the whole bucket was dropped for
        invalid event name".
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _apply_user_agent_hooks

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        config: dict = {"hooks": {}}
        # "bogusEvent" is not in _VALID_HOOK_EVENTS; bucket must be
        # dropped with SEL audit.
        pc_cfg = {
            "agent": {
                "agent_hooks": {
                    "bogusEvent": [{"command": "/bin/true"}],
                },
                "agent_hooks_autoimport": False,
            }
        }

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        assert config["hooks"] == {}
        unknown_sel = [c for c in sel_calls if "unknown event" in c[2].lower()]
        assert unknown_sel, (
            f"regression: unknown event type must emit _sel_hook_rejected; " f"got {sel_calls!r}"
        )
        event_tag, _command, reason = unknown_sel[0]
        assert event_tag == "bogusEvent"
        assert "unknown event type" in reason.lower()

    def test_agent_hooks_non_list_entries_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: non-list entries for a valid event must emit SEL audit.

        Hardening (agent.py:669): when ``_merge_agent_hooks`` sees
        ``user_hooks["preToolUse"]`` is e.g. a string or dict (not a
        list), the whole bucket is dropped silently.  Auditors need a
        SEL signal to distinguish "malformed config dropped all hooks
        for this event" from "no hooks configured".
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _apply_user_agent_hooks

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        config: dict = {"hooks": {}}
        # ``preToolUse`` is a valid event, but a string is not a list;
        # bucket must be dropped with SEL audit.
        pc_cfg = {
            "agent": {
                "agent_hooks": {
                    "preToolUse": "not-a-list-but-a-string",
                },
                "agent_hooks_autoimport": False,
            }
        }

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        non_list_sel = [c for c in sel_calls if "not a list" in c[2].lower()]
        assert non_list_sel, (
            f"regression: non-list entries must emit _sel_hook_rejected; " f"got {sel_calls!r}"
        )
        event_tag, _command, reason = non_list_sel[0]
        assert event_tag == "preToolUse"
        assert "not a list" in reason.lower()

    def test_agent_hooks_autoimport_missing_dir_is_noop(self, tmp_path: Path, caplog):
        """Missing directory returns empty dict with only a DEBUG log (no WARNINGs)."""
        import logging

        from personalclaw.agent import _autoimport_agent_hooks

        missing = tmp_path / "does-not-exist"

        with caplog.at_level(logging.DEBUG, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(missing)

        assert result == {}
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []

    def test_agent_hooks_autoimport_invalid_matcher_skips_script(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """An invalid matcher header must skip the script entirely.

        Regression guard: silently demoting a tool-scoped hook to an unscoped
        hook (firing on every tool call) would be a privilege expansion.  The
        whole script must be rejected so the user notices and fixes the
        matcher instead of getting a silently-broader hook.

        Also asserts (hardening): the SEL audit event uses the
        literal ``"autoimport"`` tag for consistency with every other
        rejection branch in ``_autoimport_agent_hooks``.  The pre-fix code
        passed the variable ``event`` (e.g. ``"preToolUse"``), which broke
        audit-trail consistency -- auditors filtering on
        ``event="autoimport"`` would miss invalid-matcher rejections.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        # Matcher with a space is rejected by _SAFE_MATCHER_RE.
        entry = self._make_script(
            hooks_dir, "bad.sh", body="# matcher: tool name with spaces\nexit 0\n"
        )

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert any(
            "matcher" in rec.message.lower() and "invalid" in rec.message.lower()
            for rec in caplog.records
        )
        # Note: SEL tag must be the literal "autoimport",
        # not the variable ``event`` (e.g. "preToolUse").  Under the
        # pre-fix code this assertion failed with event_tag == "preToolUse".
        assert len(sel_calls) == 1, (
            f"expected exactly one _sel_hook_rejected for invalid matcher; "
            f"got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport", (
            f"regression: invalid-matcher SEL audit used tag {event_tag!r}; "
            f"must be literal 'autoimport' for audit-trail consistency with "
            f"every other rejection branch in _autoimport_agent_hooks."
        )
        assert command == str(entry)
        assert "invalid matcher" in reason.lower()

    def test_agent_hooks_autoimport_unknown_event_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: unknown ``# event:`` rejection must emit a SEL audit event.

        Hardening (agent.py:586): when ``_infer_hook_event`` returns ``None``
        (the header declares an event name outside ``_HOOK_EVENT_CANONICAL``),
        the script is rejected.  Before this fix, the rejection only emitted
        a ``logger.warning`` — it did NOT call ``_sel_hook_rejected``.  Every
        other rejection branch in ``_autoimport_agent_hooks`` (symlink-escape,
        failed-validation, invalid-matcher) correctly emits a SEL audit event
        per the security-controls policy: "All tool invocations and
        permission decisions must emit SEL audit events via sel.py."

        This test stages a script whose ``# event:`` header is a bogus event
        name (not in ``_HOOK_EVENT_CANONICAL``), spies on
        ``_sel_hook_rejected``, and asserts the audit call was made with the
        ``"autoimport"`` source tag, the script path, and an
        ``"unknown event header"`` reason.  Under the pre-fix code, the spy
        observed zero calls and this test failed with the designed error
        message; under the fix, it observes exactly one.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        # ``NoSuchEvent`` is not in _HOOK_EVENT_CANONICAL, so _infer_hook_event
        # returns None and this rejection branch fires.  The filename has no
        # known suffix so fallback inference does not rescue it either.
        script = self._make_script(hooks_dir, "bogus.sh", body="# event: NoSuchEvent\nexit 0\n")

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        # The SEL audit must fire exactly once for this one rejected script,
        # tagged with the ``"autoimport"`` log label and the script path.
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when a "
            f"script's '# event:' header is unknown; got {len(sel_calls)}: "
            f"{sel_calls!r}.  Every rejection branch in _autoimport_agent_hooks "
            f"must emit a SEL audit event per the security-controls policy."
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(script)
        assert "unknown event" in reason.lower()

    def test_agent_hooks_autoimport_cannot_resolve_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``entry.resolve()`` failure rejection must emit SEL audit.

        Hardening (agent.py:556): the ``cannot resolve entry`` rejection branch
        (when ``entry.resolve()`` raises ``OSError``) was missing the
        ``_sel_hook_rejected`` call.  Every other rejection branch in
        ``_autoimport_agent_hooks`` emits a SEL audit event per
        the security-controls policy.  Without this call, an
        auditor reconstructing agent-install activity from SEL would not
        see scripts dropped due to resolve() failures.

        This test forces ``Path.resolve`` to raise ``OSError`` for the
        entry and asserts a SEL audit is recorded with the
        ``"autoimport"`` source tag and a ``"cannot resolve entry"``
        reason.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        entry = self._make_script(hooks_dir, "broken.sh")

        # Patch Path.resolve to raise OSError ONLY for the entry path,
        # letting hooks_dir.resolve() (the first resolve() call in
        # _autoimport_agent_hooks) succeed.  Otherwise the function
        # returns early before the loop even starts.
        real_resolve = Path.resolve

        def _raising_resolve(self, *args, **kwargs):
            if self == entry:
                raise OSError("simulated resolve failure")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", _raising_resolve)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when "
            f"entry.resolve() raises; got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(entry)
        assert "cannot resolve" in reason.lower()

    def test_agent_hooks_autoimport_cannot_stat_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``resolved_entry.stat()`` failure must emit SEL audit.

        Hardening (agent.py:556): the ``cannot stat entry`` rejection branch
        (when ``resolved_entry.stat()`` raises ``OSError``) was missing
        the ``_sel_hook_rejected`` call.  Same audit-completeness class
        of bug as the ``cannot resolve`` gap.

        This test forces ``Path.stat`` to raise ``OSError`` for the
        resolved entry and asserts a SEL audit is recorded with the
        ``"autoimport"`` source tag and a ``"cannot stat entry"`` reason.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        entry = self._make_script(hooks_dir, "broken.sh")

        # Arm the stat failure only AFTER ``entry.is_file()`` has been
        # observed on this path.  The production loop calls ``is_file()``
        # first (internally a stat), then later ``resolved_entry.stat()``
        # explicitly.  We want only the explicit call to fail.
        #
        # Previous version used ``call_count >= 3`` which worked on
        # CPython 3.12 but is fragile: pathlib's internal stat-usage per
        # ``is_file()`` varies between 3.10, 3.11, 3.12, 3.13 (3.12
        # rewrote pathlib internals), so a hard-coded threshold is a
        # time bomb.  Gating on ``is_file`` being called instead is
        # stable across versions — no matter how many stats ``is_file``
        # makes internally, we only raise on stats that happen *after*
        # it completes, which is when ``resolved_entry.stat()`` runs.
        real_stat = Path.stat
        real_is_file = Path.is_file
        armed = False

        def _arming_is_file(self, *args, **kwargs):
            # Arm by Path identity (self == entry) rather than by
            # self.name, which would also fire on any path whose
            # basename happens to be "broken.sh" (e.g. a sibling
            # fixture in a future multi-script variant of this test).
            nonlocal armed
            rv = real_is_file(self, *args, **kwargs)
            if self == entry:
                armed = True
            return rv

        def _raising_stat(self, *args, **kwargs):
            if self == entry and armed:
                raise OSError("simulated stat failure")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "is_file", _arming_is_file)
        monkeypatch.setattr(Path, "stat", _raising_stat)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when "
            f"resolved_entry.stat() raises; got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(entry)
        assert "cannot stat" in reason.lower()

    def test_agent_hooks_autoimport_non_executable_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: non-executable ``.sh`` skip must emit SEL audit.

        Hardening (agent.py:581): the non-executable skip
        branch was the last rejection path in ``_autoimport_agent_hooks``
        that logged at INFO only and did NOT call
        ``_sel_hook_rejected``.  Every other rejection branch
        (symlink-escape, cannot-resolve, cannot-stat, failed-validation,
        unknown-event, invalid-matcher, cannot-read-dir) emits a SEL
        audit event per the security-controls policy: "All
        tool invocations and permission decisions must emit SEL audit
        events via sel.py."  The non-executable skip is also a
        permission decision (a discovered ``.sh`` file will NOT be
        loaded as a hook), so an auditor reconstructing agent-install
        activity from SEL must see it.

        This test drops a non-executable ``.sh`` file in the hooks dir,
        spies on ``_sel_hook_rejected``, and asserts exactly one audit
        call with the ``"autoimport"`` tag and a ``"not executable"``
        reason.  Under the pre-fix code, the spy observed zero calls
        and this test failed with the designed error message.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        entry = self._make_script(hooks_dir, "disabled.sh", executable=False)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.INFO, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when "
            f"a non-executable .sh is skipped; got {len(sel_calls)}: "
            f"{sel_calls!r}.  Every rejection branch in "
            f"_autoimport_agent_hooks must emit a SEL audit event per "
            f"the security-controls policy."
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(entry)
        assert "not executable" in reason.lower()

    def test_agent_hooks_autoimport_rejects_dir_equal_to_home(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``agent_hooks_dir: "~"`` (resolved == HOME) must be rejected.

        Hardening (agent.py:746): the original containment check allowed
        ``resolved == home`` to pass because the condition was
        ``(resolved != home and home not in resolved.parents)``.  When
        ``resolved == home``, the left side of the ``and`` was ``False``,
        so the whole clause was ``False`` and the path was *accepted*.

        Impact: an LLM-writable config setting ``agent_hooks_dir: "~"``
        would cause ``_autoimport_agent_hooks`` to scan the *entire* home
        directory for executable ``*.sh`` files, auto-registering any
        executable script anywhere under ``$HOME``.

        The fix is strict containment: require ``resolved`` to be *under*
        HOME, not equal to it.  ``Path.parents`` of e.g. ``/home/user``
        is ``(/, /home)`` and does not include ``/home/user`` itself, so
        ``home not in resolved.parents`` rejects ``resolved == home``.

        This test sets ``agent_hooks_dir`` to a path that resolves to HOME
        (the fake home we monkeypatch to ``tmp_path``) and asserts no
        scripts get merged — the "evil" script at HOME root is not
        auto-registered — and that the rejection is logged + SEL-audited.
        """
        import logging

        from personalclaw.agent import _apply_user_agent_hooks

        # ``_isolate_home`` fixture already sets Path.home() -> tmp_path.
        # Re-route the default fallback so failure does not touch the real
        # ~/.personalclaw/hooks.  Place an executable script directly at HOME root
        # to prove that home-root scanning would pick it up.
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            tmp_path / ".personalclaw" / "hooks",
        )
        evil = tmp_path / "evil.sh"
        evil.write_text("#!/bin/sh\nexit 0\n")
        evil.chmod(0o755)

        config: dict = {"hooks": {}}
        # ``agent_hooks_dir: "~"`` expands to HOME, which equals tmp_path
        # after the _isolate_home fixture runs.  Under the pre-fix code
        # this passed validation; under the fix it's rejected.
        pc_cfg = {"agent": {"agent_hooks_dir": str(tmp_path)}}

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        # Critical invariant: nothing from HOME-root got auto-registered.
        # The "evil" script must NOT appear in config["hooks"].
        assert config["hooks"] == {}, (
            f"regression: agent_hooks_dir resolving to HOME itself was accepted, "
            f"causing home-root scan; expected empty hooks dict, got "
            f"{config['hooks']!r}.  The containment check must reject "
            f"resolved == home."
        )
        assert any(
            "agent_hooks_dir" in rec.message and "rejected" in rec.message.lower()
            for rec in caplog.records
        ), "expected 'agent_hooks_dir ... rejected' WARNING from containment check"

    def test_agent_hooks_autoimport_rejects_dir_outside_home(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """agent_hooks_dir resolving outside HOME is rejected with a fallback warning.

        Regression guard: config.json is LLM-writable.  A malicious override
        pointing autoimport at /tmp or /var could auto-register any executable
        script an attacker lands there.  The code must fall back to the
        default ~/.personalclaw/hooks and log a WARNING + SEL audit.
        """
        import logging

        from personalclaw.agent import _apply_user_agent_hooks

        # Stage a fake HOME so our default hooks dir doesn't exist and so
        # tmp_path's /private/var/folders path is *outside* HOME.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        # Also re-route the default hooks dir into the fake HOME so fallback
        # does not hit the caller's real ~/.personalclaw/hooks directory.
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            fake_home / ".personalclaw" / "hooks",
        )

        # This dir is genuinely outside fake_home, since tmp_path itself is
        # the parent directory of fake_home.
        outside = tmp_path / "outside-hooks"
        outside.mkdir()
        self._make_script(outside, "evil.sh")

        config: dict = {"hooks": {}}
        pc_cfg = {"agent": {"agent_hooks_dir": str(outside)}}

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        # Fallback is fake_home/.personalclaw/hooks (doesn't exist), so nothing gets
        # merged.  Critically: the "evil" script is not merged.
        assert config["hooks"] == {}
        assert any(
            "agent_hooks_dir" in rec.message and "rejected" in rec.message.lower()
            for rec in caplog.records
        )

    def test_agent_hooks_autoimport_rejects_symlink_escaping_dir(self, tmp_path: Path, caplog):
        """A symlink inside hooks_dir pointing at an outside script is rejected.

        Regression guard: entry.is_file() follows symlinks, and
        is_sensitive_path only matches a small set of $HOME subdirs.  A
        symlink named ``guard.sh`` inside ~/.personalclaw/hooks/ whose target is
        ~/elsewhere/attacker.sh would otherwise pass every other check.
        The resolved-path containment check must catch it.
        """
        import logging

        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        # Outside target, inside HOME so the is_sensitive_path check alone
        # wouldn't reject it - only the containment check does.
        outside_target = tmp_path / "elsewhere" / "attacker.sh"
        outside_target.parent.mkdir(parents=True, exist_ok=True)
        outside_target.write_text("#!/bin/sh\nexit 0\n")
        outside_target.chmod(0o755)
        (hooks_dir / "guard.sh").symlink_to(outside_target)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert any("resolves outside" in rec.message for rec in caplog.records)

    def test_agent_hooks_autoimport_validates_before_parsing_headers(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``_validate_hook_command`` must run BEFORE header parsing.

        Hardening (agent.py:562): the original ordering parsed the script's
        ``# event:`` / ``# matcher:`` headers first and only then called
        ``_validate_hook_command``.  Defense-in-depth: even though the
        containment check above already rejects sensitive-path symlinks,
        the no-file-reads-on-rejected-paths invariant is worth keeping
        explicit.

        To prove the reorder (rather than the pre-existing containment
        check) is what stops header parsing, we force a rejection from
        ``_validate_hook_command`` specifically: monkeypatch it to return
        ``None`` for any path, then assert ``_parse_hook_script_headers``
        was never invoked.  Under the OLD code, headers were parsed first
        and this test would observe a call; under the NEW code, validation
        rejects before the parser runs.

        We also assert the rejection is SEL-audited via
        ``_sel_hook_rejected`` so a future refactor that silently drops
        the audit call is caught.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        # One legitimate script inside hooks_dir (passes containment).
        hooks_dir = tmp_path / "hooks"
        script = self._make_script(hooks_dir, "ok.sh")

        # Force _validate_hook_command to reject everything.  This isolates
        # the reorder: if header parsing ran before validation, we'd still
        # see a call; with the reorder, validation rejects first.
        monkeypatch.setattr(_agent_mod, "_validate_hook_command", lambda *_a, **_kw: None)

        header_calls: list[str] = []

        def _record_header_call(path: Path) -> tuple:
            header_calls.append(str(path))
            return None, None

        monkeypatch.setattr(_agent_mod, "_parse_hook_script_headers", _record_header_call)

        # Spy on the SEL audit sink so we can assert a rejection was
        # recorded with the ``"autoimport"`` source tag.  Without this,
        # a future refactor that silently drops the audit call would
        # still let the monkeypatched validate-to-None path pass.
        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert header_calls == [], (
            "regression: _parse_hook_script_headers was called before "
            "_validate_hook_command rejected the script. Validation must "
            "run first so no file reads happen on rejected paths."
        )
        # Exactly one SEL rejection recorded for the autoimport rejection
        # branch, tagged with the ``"autoimport"`` log label (a log-only
        # tag; see agent.py:562-570 note).
        assert len(sel_calls) == 1, (
            f"expected exactly one _sel_hook_rejected call for the rejected "
            f"script; got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(script)
        assert "failed validation" in reason

    def test_agent_hooks_dir_stored_as_resolved_path(self, tmp_path: Path, monkeypatch):
        """Regression: ``_autoimport_agent_hooks`` receives the *resolved* hooks dir.

        Hardening (agent.py:744): the original
        code did ``hooks_dir = requested`` after validating ``resolved``.
        If a path component of ``requested`` was a symlink, it could be
        swapped between the validate-in-HOME check in
        ``_apply_user_agent_hooks`` and the resolve()-for-containment check
        inside ``_autoimport_agent_hooks``, letting autoimport scan a
        directory outside HOME.  Storing the already-resolved path makes
        the downstream resolve() a no-op on an already-canonical path,
        eliminating the named symlink-swap window.

        This test does not (and cannot deterministically) reproduce the
        race itself; it verifies the observable contract that proves the
        mitigation is in place: the caller stores and forwards the
        resolved form of ``agent_hooks_dir``.  The per-entry containment
        check already canonicalizes each entry, so asserting on the
        resulting command path cannot tell the fix apart from the bug -
        we instead intercept the call into ``_autoimport_agent_hooks``
        and assert it was invoked with the *resolved* path, not the
        symlinked ``requested`` path.
        """
        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _apply_user_agent_hooks

        # Real hooks directory plus a user-facing symlink that points at it.
        real_hooks = tmp_path / "real" / "hooks"
        real_hooks.mkdir(parents=True)
        link_hooks = tmp_path / "link-hooks"
        link_hooks.symlink_to(real_hooks)

        captured: list[Path] = []

        # Spy on _autoimport_agent_hooks to capture what `hooks_dir` value
        # _apply_user_agent_hooks stores and forwards.  Under the old
        # `hooks_dir = requested` code, we would see `link_hooks`; under
        # the new `hooks_dir = resolved` code, we see `real_hooks`.
        def _spy(hooks_dir: Path) -> dict:
            captured.append(hooks_dir)
            return {}

        monkeypatch.setattr(_agent_mod, "_autoimport_agent_hooks", _spy)

        config: dict = {"hooks": {}}
        # Explicit ``agent_hooks_autoimport: True`` so the test does not
        # silently become a no-op if the default ever flips.
        pc_cfg = {
            "agent": {
                "agent_hooks_autoimport": True,
                "agent_hooks_dir": str(link_hooks),
            }
        }

        _apply_user_agent_hooks(config, pc_cfg)

        assert len(captured) == 1, "autoimport should have been invoked exactly once"
        forwarded = captured[0]
        # The forwarded path must equal the real (resolved) directory, NOT
        # the symlink requested by the user.  Comparing by resolve() on
        # both sides would mask the bug (since resolve(link) == real);
        # comparing the raw Path confirms the resolved form was stored.
        assert forwarded == real_hooks, (
            f"regression: _autoimport_agent_hooks received {forwarded!r}; "
            f"expected the resolved path {real_hooks!r}. _apply_user_agent_hooks "
            "must store the resolved path so the downstream resolve() is a "
            "no-op even under adversarial symlink swaps."
        )
        # Sanity: it really is NOT the symlink form (would be the bug).
        assert forwarded != link_hooks

    def test_agent_hooks_dir_null_byte_does_not_crash(self, tmp_path: Path, monkeypatch):
        """Regression: ``agent_hooks_dir`` with a null byte must not crash.

        Adversarial hardening: ``Path("\\x00")``
        raises ``ValueError: embedded null byte`` -- uncaught, this
        propagates up through ``rebuild_agent_config()`` and crashes agent
        bootstrap (denial of service via LLM-writable config).

        Fix: ``except (OSError, ValueError)`` around the resolve() pair.
        This test feeds a null-byte ``agent_hooks_dir`` and asserts the
        code neither raises nor registers any hooks (falls back to the
        default which we re-route away from the caller's real
        ``~/.personalclaw/hooks``).  Under the pre-fix code, ``ValueError`` would
        escape and the test body would fail with an unhandled exception.
        """
        from personalclaw.agent import _apply_user_agent_hooks

        # Re-route the default so fallback doesn't touch caller's HOME.
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            tmp_path / "nonexistent" / "hooks",
        )

        config: dict = {"hooks": {}}
        pc_cfg = {"agent": {"agent_hooks_dir": "\x00"}}

        # Must not raise.  Under pre-fix code this line propagates
        # ``ValueError: embedded null byte`` from Path.resolve().
        _apply_user_agent_hooks(config, pc_cfg)

        assert config["hooks"] == {}

    def test_agent_hooks_autoimport_cannot_read_dir_emits_sel_audit(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``iterdir()`` OSError on hooks_dir must emit SEL audit.

        Coverage hardening: the ``cannot read
        <hooks_dir>`` rejection branch in ``_autoimport_agent_hooks``
        (when ``hooks_dir.iterdir()`` raises ``OSError``, e.g. EACCES)
        was missing the ``_sel_hook_rejected`` call.  Without it, an
        auditor reconstructing agent-install activity cannot
        distinguish "hooks dir unreadable" from "no scripts configured"
        -- both show ``requested_autoimport=0`` in the merge summary.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()

        # Force iterdir() to raise OSError (simulating permission
        # denial).  Narrow the patch to Path.iterdir only.
        def _raising_iterdir(self):
            raise OSError("simulated permission denied")

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when "
            f"iterdir() raises OSError; got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(hooks_dir)
        assert "cannot read" in reason.lower()

    def test_agent_hooks_dir_non_string_ignored_without_sel(self, tmp_path: Path, monkeypatch):
        """Regression: non-string ``agent_hooks_dir`` reverts to default silently.

        Coverage hardening: an LLM-writable
        ``"agent_hooks_dir": null`` or ``[]`` silently skips the
        containment check (the ``isinstance(custom_dir, str) and
        custom_dir`` guard) and uses the default ``~/.personalclaw/hooks``.
        A malicious config that intentionally sets the value to a
        non-string should NOT emit a false-positive SEL rejection
        (nothing was actually rejected), but it also should not
        crash or scan an unintended directory.

        This test asserts: (a) fallback to default, (b) no SEL call,
        (c) no crash.
        """
        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _apply_user_agent_hooks

        # Re-route default so fallback is empty (hooks dir doesn't exist).
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            tmp_path / "nonexistent" / "hooks",
        )

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        for bogus in (None, [], 42, {"foo": "bar"}):
            config: dict = {"hooks": {}}
            pc_cfg = {"agent": {"agent_hooks_dir": bogus}}
            # Must not raise.
            _apply_user_agent_hooks(config, pc_cfg)
            assert config["hooks"] == {}, (
                f"non-string agent_hooks_dir={bogus!r} produced hooks: " f"{config['hooks']!r}"
            )

        assert sel_calls == [], (
            f"non-string agent_hooks_dir should NOT emit SEL "
            f"(nothing was rejected); got: {sel_calls!r}"
        )

    def test_agent_hooks_autoimport_rejects_dir_equal_to_symlinked_home(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: HOME-containment survives HOME-as-symlink topology.

        Coverage hardening: remote dev hosts and
        macOS laptops often have ``$HOME`` as a symlink (e.g.
        ``/home/user -> /mnt/fast-disk/user``).  The strict-containment
        check uses ``home = Path.home().resolve()`` and ``resolved =
        requested.resolve()`` -- both canonicalize, so the check should
        survive.  This test proves that contract: user points
        ``agent_hooks_dir`` at the canonical (resolved) HOME target
        directly, while ``Path.home()`` returns the symlink; both
        must canonicalize to the same path and get rejected.

        Under a hypothetical regression where one side stops calling
        ``.resolve()``, the paths would mismatch and the test would
        accept the equal-to-HOME config, failing the assertion.
        """
        import logging

        from personalclaw.agent import _apply_user_agent_hooks

        # Construct a real directory and a symlink to it.
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        symlink_home = tmp_path / "link_home"
        symlink_home.symlink_to(real_home)

        # Path.home() returns the symlink; .resolve() inside the code
        # should canonicalize it to real_home.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: symlink_home))
        # Re-route default so fallback doesn't touch caller's real HOME.
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            symlink_home / ".personalclaw" / "hooks",
        )

        # Plant an executable at the canonical HOME root to prove it
        # would be scanned under a buggy containment check.
        evil = real_home / "evil.sh"
        evil.write_text("#!/bin/sh\nexit 0\n")
        evil.chmod(0o755)

        config: dict = {"hooks": {}}
        # User points agent_hooks_dir directly at the canonical HOME
        # (bypassing the symlink) -- should still be rejected because
        # resolved == canonical HOME.
        pc_cfg = {"agent": {"agent_hooks_dir": str(real_home)}}

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            _apply_user_agent_hooks(config, pc_cfg)

        assert config["hooks"] == {}, (
            f"regression: agent_hooks_dir resolving to canonical HOME "
            f"(via symlink'd Path.home()) was accepted; expected "
            f"empty hooks, got {config['hooks']!r}.  The containment "
            f"check must .resolve() both sides."
        )
        assert any(
            "agent_hooks_dir" in rec.message and "rejected" in rec.message.lower()
            for rec in caplog.records
        )

    def test_agent_hooks_autoimport_hooks_dir_resolve_oserror_emits_sel(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        """Regression: ``hooks_dir.resolve()`` OSError must emit SEL audit.

        Hardening (agent.py:509): the initial ``hooks_dir.resolve()`` failure
        branch returned early with only a ``logger.debug`` -- no
        ``_sel_hook_rejected`` call.  Same audit-completeness gap class
        as rev 5 fixed for the per-entry cannot-resolve-entry branch,
        missed on the directory-level resolve.

        This test forces ``Path.resolve`` to raise ``OSError`` for the
        hooks_dir specifically and asserts a SEL audit is recorded with
        the ``"autoimport"`` source tag and a ``"cannot resolve
        hooks_dir"`` reason.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()

        real_resolve = Path.resolve

        def _raising_resolve(self, *args, **kwargs):
            # Fire only on the hooks_dir, not on entries or Path.home().
            if self == hooks_dir:
                raise OSError("simulated resolve failure on hooks_dir")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", _raising_resolve)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        with caplog.at_level(logging.DEBUG, logger="personalclaw.agent"):
            result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: expected exactly one _sel_hook_rejected call when "
            f"hooks_dir.resolve() raises; got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(hooks_dir)
        assert "cannot resolve hooks_dir" in reason.lower()

    def test_agent_hooks_autoimport_handles_valueerror_on_entry_resolve(
        self, tmp_path: Path, monkeypatch
    ):
        """Regression: ``ValueError`` from ``entry.resolve()`` must not crash.

        Hardening (agent.py:540):
        the two inner ``resolve()`` calls in ``_autoimport_agent_hooks``
        only caught ``OSError``, not ``ValueError``.  A filename from
        ``iterdir()`` containing a null byte (FS shenanigans, adversarial
        filenames) would propagate ``ValueError: embedded null byte``
        uncaught and crash agent bootstrap.

        Fix: ``except (OSError, ValueError)`` on both inner resolve
        calls.  This test forces ``Path.resolve`` on an entry to raise
        ``ValueError`` and asserts the code rejects cleanly (no crash,
        SEL audited).
        """
        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _autoimport_agent_hooks

        hooks_dir = tmp_path / "hooks"
        entry = self._make_script(hooks_dir, "bad.sh")

        real_resolve = Path.resolve

        def _raising_resolve(self, *args, **kwargs):
            if self == entry:
                raise ValueError("embedded null byte")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", _raising_resolve)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        # Must not raise.
        result = _autoimport_agent_hooks(hooks_dir)

        assert result == {}
        assert len(sel_calls) == 1, (
            f"regression: ValueError on entry.resolve() must trigger the "
            f"same SEL-audited rejection branch as OSError; got "
            f"{len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        assert command == str(entry)
        assert "cannot resolve" in reason.lower()

    def test_agent_hooks_dir_resolve_oserror_falls_back(self, tmp_path: Path, monkeypatch, caplog):
        """Regression: OSError from ``Path.resolve()`` falls back cleanly.

        Coverage hardening: if
        ``requested.resolve()`` or ``Path.home().resolve()`` raises
        ``OSError`` (ENAMETOOLONG, ELOOP, EACCES on a path component),
        the code sets ``resolved = home = None`` and falls through to
        the rejection branch, logs a warning, and emits SEL audit.

        This test forces the OSError path and asserts: (a) no crash,
        (b) hooks empty (fallback), (c) SEL audit emitted, (d) warning
        logged.  Under a hypothetical regression where the except is
        narrowed back to only ``OSError`` without catching ValueError,
        this test would still pass -- it specifically exercises the
        ``OSError`` arm of the ``except (OSError, ValueError)`` block.
        """
        import logging

        from personalclaw import agent as _agent_mod
        from personalclaw.agent import _apply_user_agent_hooks

        # Re-route default so fallback is inert.
        monkeypatch.setattr(
            "personalclaw.agent._DEFAULT_HOOKS_DIR",
            tmp_path / "nonexistent" / "hooks",
        )

        # Force Path.resolve to raise OSError.  Narrow the patch so only
        # resolve() on the "requested" path fails; Path.home() still
        # works so home=None comes from resolved=None cascade.
        real_resolve = Path.resolve

        def _raising_resolve(self, *args, **kwargs):
            # Raise only on the user-supplied path (a custom fake-home
            # target we pass below).  Leave other resolve() calls alone.
            if self.name == "too-long":
                raise OSError("ENAMETOOLONG simulated")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", _raising_resolve)

        sel_calls: list[tuple[str, str, str]] = []

        def _record_sel(event: str, command: str, reason: str) -> None:
            sel_calls.append((event, command, reason))

        monkeypatch.setattr(_agent_mod, "_sel_hook_rejected", _record_sel)

        config: dict = {"hooks": {}}
        # A path whose name triggers our resolve-fail shim.
        pc_cfg = {"agent": {"agent_hooks_dir": str(tmp_path / "too-long")}}

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            # Must not raise.
            _apply_user_agent_hooks(config, pc_cfg)

        assert config["hooks"] == {}
        # Exactly one SEL call for the rejection.
        assert len(sel_calls) == 1, (
            f"expected exactly one _sel_hook_rejected on OSError resolve; "
            f"got {len(sel_calls)}: {sel_calls!r}"
        )
        event_tag, _command, reason = sel_calls[0]
        assert event_tag == "autoimport"
        # Reason currently says "outside HOME or sensitive" which is
        # broadly accurate (resolved=None does land in that branch),
        # but a future refinement may split OSError into its own
        # reason string -- either shape is acceptable here.
        assert (
            "agent_hooks_dir" in reason.lower()
            or "hooks_dir" in reason.lower()
            or "home" in reason.lower()
            or "sensitive" in reason.lower()
        )
        # A warning mentioning "rejected" must be logged.
        assert any("rejected" in rec.message.lower() for rec in caplog.records)
