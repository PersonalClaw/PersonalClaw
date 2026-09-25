"""What the code must do when it IS the packaged artefact rather than a checkout.

Before 2026-09-23 nothing under `src/personalclaw` asked whether it was running frozen — a
`grep` for `sys.frozen` / `_MEIPASS` over the whole package returned zero. That is the root of
two of the high-severity findings from the owner's macOS install, because both are decisions
that are only wrong inside a bundle:

* **the core MCP server was dropped.** `_resolve_personalclaw_bin` looks for a `bin/personalclaw`
  console script. An `.app` has none and has no interpreter to put one beside, so every probe
  missed, the bare-name fallback resolved to nothing, and the agent ran with no core tools —
  `Could not resolve personalclaw binary to an existing file` followed by
  `Dropping MCP server 'personalclaw-core'`, one WARNING as the only evidence.
* **the update check shelled out to git in a directory that is not a repository**, twelve times
  in one session, because the Electron shell sets `PERSONALCLAW_PROJECT_DIR` to `…/Resources`
  inside the bundle and the check treated a set project dir as proof of a checkout.

Frozen-ness is simulated by setting the attributes PyInstaller's bootloader sets, which is the
only way to exercise these paths from a test process — and is exactly why they were never
exercised. Each arm is driven from BOTH sides (frozen and not), so a clause that has stopped
distinguishing the two reads as a failure rather than as a pass.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from personalclaw import self_update


@pytest.fixture
def frozen(monkeypatch):
    """Make this process look like a PyInstaller one-folder bundle."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "sys._MEIPASS", "/Applications/PersonalClaw.app/Contents/Resources", raising=False
    )
    return True


class TestIsFrozen:
    def test_a_plain_interpreter_is_not_frozen(self):
        assert self_update.is_frozen() is False

    def test_the_bootloader_attributes_are_recognized(self, frozen):
        assert self_update.is_frozen() is True

    def test_either_signal_alone_is_enough(self, monkeypatch):
        """Two independent signals so a bootloader that drops one still reads correctly."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert self_update.is_frozen() is True
        monkeypatch.undo()
        monkeypatch.setattr("sys._MEIPASS", "/tmp/_MEI", raising=False)
        assert self_update.is_frozen() is True


class TestInstallKindFromTheArtefact:
    def test_a_frozen_bundle_is_desktop_without_the_env(self, frozen, monkeypatch):
        """🔴 THE REGRESSION. With the env unset a frozen bundle used to answer `pip`, whose
        apply path pip-installs into `sys.executable` — the PyInstaller launcher."""
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
        assert self_update.detect_install_kind() == "desktop"

    def test_an_unfrozen_process_with_no_env_is_still_pip(self, monkeypatch):
        """The control. Without this the clause above could be a constant."""
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
        assert self_update.detect_install_kind() == "pip"

    def test_the_env_still_wins_over_frozen(self, frozen, monkeypatch):
        """Contract C1 order is unchanged: the shell's declaration is still first, so a
        container image that happens to freeze its backend keeps saying `container`."""
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
        assert self_update.detect_install_kind() == "container"

    def test_frozen_wins_over_a_project_dir_that_is_a_real_checkout(
        self, frozen, monkeypatch, tmp_path
    ):
        """A bundle cannot be a git checkout, so `desktop` must beat the `.git` probe even when
        a stray `PERSONALCLAW_PROJECT_DIR` points at one."""
        (tmp_path / ".git").mkdir()
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        assert self_update.detect_install_kind() == "desktop"

    def test_the_same_project_dir_is_git_when_not_frozen(self, monkeypatch, tmp_path):
        """The control for the arm above."""
        (tmp_path / ".git").mkdir()
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
        assert self_update.detect_install_kind() == "git"


class TestTheCoreMcpServerSurvivesFreezing:
    def test_the_frozen_launcher_is_the_resolved_binary(self, frozen, monkeypatch, tmp_path):
        """`sys.executable` in a bundle IS the CLI: its entry script is `personalclaw/__main__.py`,
        so `<launcher> mcp-core` is the command a console script would have run."""
        from personalclaw import agent

        launcher = tmp_path / "personalclaw-backend"
        launcher.write_bytes(b"#!/bin/sh\n")
        launcher.chmod(0o755)
        monkeypatch.setattr(agent, "_PERSONALCLAW_BIN", None)
        monkeypatch.setattr("sys.executable", str(launcher))
        assert agent._resolve_personalclaw_bin() == str(launcher)

    def test_the_managed_core_server_gets_a_real_command_when_frozen(
        self, frozen, monkeypatch, tmp_path, caplog
    ):
        """The consequence, not just the helper: `personalclaw-core` must resolve to an existing
        file, because `agent.py` DROPS a managed server whose command does not exist."""
        from personalclaw import agent

        launcher = tmp_path / "personalclaw-backend"
        launcher.write_bytes(b"#!/bin/sh\n")
        launcher.chmod(0o755)
        monkeypatch.setattr(agent, "_PERSONALCLAW_BIN", None)
        monkeypatch.setattr("sys.executable", str(launcher))

        with caplog.at_level(logging.WARNING, logger="personalclaw.agent"):
            command = agent._MANAGED_MCP_SERVERS["personalclaw-core"]["command_fn"]()
        assert command == str(launcher)
        assert command != "personalclaw", "the bare-name fallback is what dropped the server"
        assert not [
            r for r in caplog.records if "Could not resolve personalclaw binary" in r.getMessage()
        ]

    def test_every_managed_server_resolves_from_a_bundle(self, frozen, monkeypatch, tmp_path):
        """Enumerated, so adding a second managed server cannot skip this rail."""
        from personalclaw import agent

        launcher = tmp_path / "personalclaw-backend"
        launcher.write_bytes(b"#!/bin/sh\n")
        launcher.chmod(0o755)
        monkeypatch.setattr(agent, "_PERSONALCLAW_BIN", None)
        monkeypatch.setattr("sys.executable", str(launcher))
        assert agent._MANAGED_MCP_SERVERS, "no managed servers — the loop would pass vacuously"
        for name, entry in agent._MANAGED_MCP_SERVERS.items():
            resolved = entry["command_fn"]()
            assert resolved == str(launcher), f"{name} resolved to {resolved!r}"


class TestTheUpdateCheckDoesNotShellOutToGit:
    def _run_check(self, monkeypatch, tmp_path, kind: str) -> list[tuple[str, ...]]:
        """Drive `_do_update_check` with a *kind*, recording any subprocess it tries to spawn.

        The recorder raises so the call stops at the first spawn, and `_do_update_check`'s own
        `except Exception` swallows it — which is why the assertion is on the RECORD rather than
        on a propagated error. `check_enabled` defaults to True, so neither arm can pass by
        hitting the RUM-3 egress kill switch instead of the clause under test.
        """
        from personalclaw.config.loader import AppConfig
        from personalclaw.dashboard.handlers import updates as updates_mod

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        assert (
            AppConfig.load().updates.check_enabled
        ), "the kill switch would short-circuit both arms"

        spawned: list[tuple[str, ...]] = []

        async def _record(*argv, **kwargs):
            spawned.append(tuple(str(a) for a in argv))
            raise RuntimeError("test recorder: no real subprocess")

        monkeypatch.setattr(updates_mod.self_update, "detect_install_kind", lambda: kind)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _record)
        monkeypatch.setenv(
            "PERSONALCLAW_PROJECT_DIR", "/Applications/PersonalClaw.app/Contents/Resources"
        )
        asyncio.run(updates_mod._do_update_check())
        return spawned

    @pytest.mark.parametrize("kind", ["desktop", "pip", "container"])
    def test_a_non_git_install_spawns_nothing(self, monkeypatch, tmp_path, kind):
        """🔴 THE REGRESSION. 12 x `git fetch failed (rc=128): fatal: not a git repository`.

        Asserted on the SPAWN, not on the log line: a fix that kept running git and swallowed
        the warning would leave the wasted subprocess in place, and is the fix this rail refuses.
        """
        assert self._run_check(monkeypatch, tmp_path, kind) == []

    def test_a_git_install_still_runs_the_probe(self, monkeypatch, tmp_path):
        """The positive control. Without it the gate above could be an unconditional return —
        which would silently retire the changelog-diff check for the one kind that has one."""
        spawned = self._run_check(monkeypatch, tmp_path, "git")
        assert spawned, "the git kind must still fetch"
        assert spawned[0][:2] == ("git", "fetch"), spawned[0]
