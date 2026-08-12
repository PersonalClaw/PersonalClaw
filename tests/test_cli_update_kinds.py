"""`personalclaw update` behaves per install kind (DIST-13).

Before this, the CLI *was* the git pipeline: it demanded `$PERSONALCLAW_PROJECT_DIR`
and a `.git` dir, so every pip / pipx / uv-tool user — the install the README lists
first — hit "❌ PERSONALCLAW_PROJECT_DIR not set" and exit 1, while the install-kind
machinery the dashboard already used sat one module away. One test per branch drives
the real dispatch; nothing here runs git, pip, or a frontend build.

The fake layer is deliberately narrow and at the two real seams:
`self_update._run_git` (every sync git spawn funnels through it) and
`cli_server.subprocess.run` (the installer and the post-update `setup --agent-only`).
A test that actually ran `git reset --hard` or `pip -U` would be a wrecking ball.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from personalclaw import cli_server
from personalclaw import self_update as su


class _Git:
    """Records every git invocation and answers from a per-subcommand script."""

    def __init__(self, **replies: tuple[int, str, str]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, args: list[str], *, cwd: str, timeout: float):
        self.calls.append(list(args))
        rc, out, err = self._replies.get(args[0], (0, "", ""))
        return subprocess.CompletedProcess(["git", *args], rc, out, err)

    def ran(self, *prefix: str) -> bool:
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


def _fake_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for uv/pip resolution, keeping the real ``install`` verb position."""
    monkeypatch.setattr(
        "personalclaw._installer.install_argv",
        lambda args: ["FAKE-INSTALLER", "install", *args],
    )
    monkeypatch.setattr("personalclaw._installer.installer_name", lambda: "fake")


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture cli_server's subprocess.run argvs; nothing is executed."""
    seen: list[list[str]] = []

    def _fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli_server.subprocess, "run", _fake_run)
    return seen


@pytest.fixture(autouse=True)
def _no_network_and_no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """No release probe (network) and no frontend build in any of these tests."""
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "")
    monkeypatch.setattr(cli_server, "build_frontend_sync", lambda path: None)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)


def _dev_mode(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """Pin dashboard.update_dev_mode without writing a config file."""
    cfg = types.SimpleNamespace(dashboard=types.SimpleNamespace(update_dev_mode=on))
    monkeypatch.setattr(cli_server.AppConfig, "load", classmethod(lambda cls: cfg))


def _as_git_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    return str(tmp_path)


# ── the dispatch itself ─────────────────────────────────────────────────────


def test_every_install_kind_has_a_cli_branch() -> None:
    """The dispatch is exhaustive over the taxonomy — adding a kind reds here.

    This is the ratchet that makes the "no default arm" rule enforceable: a new
    InstallKind member cannot quietly land in someone's else-branch.
    """
    assert set(su.INSTALL_KINDS) == set(cli_server._UPDATE_HANDLED_KINDS)


def test_unmapped_kind_refuses_and_names_what_it_detected(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setattr(cli_server.self_update, "detect_install_kind", lambda: "flatpak")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "flatpak" in out  # says what it saw
    assert "refusing to guess" in out
    assert not git.calls and not spawns  # never fell through to the git pipeline


# ── git ─────────────────────────────────────────────────────────────────────


def test_git_kind_fetches_and_resets_the_resolved_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    proj = _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _Git(
        **{
            "rev-parse": (0, "main\n", ""),
            "diff": (1, "", ""),  # HEAD != origin/main → there is something to apply
            "status": (0, "", ""),  # clean tree → no confirmation needed
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("fetch", "origin", "main")
    assert git.ran("reset", "--hard", "origin/main")
    # The install runs, and the agent config is refreshed afterwards.
    assert any("install" in " ".join(a) for a in spawns)
    assert any(a[-2:] == ["setup", "--agent-only"] for a in spawns)
    assert proj in capsys.readouterr().out


def test_git_kind_already_up_to_date_does_not_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _Git(**{"rev-parse": (0, "main\n", ""), "diff": (0, "", "")})
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "Already up to date" in capsys.readouterr().out
    assert not git.ran("reset")
    assert not spawns


def test_git_kind_dev_mode_off_on_latest_tag_rides_tags_not_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """dev_mode OFF + already on the latest release ⇒ don't pull arbitrary commits.

    Same rule the dashboard's apply enforces, so the two surfaces cannot disagree
    about what "up to date" means for a checkout.
    """
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, False)
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "0.0.1")
    monkeypatch.setattr(cli_server, "__version__", "9.9.9")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    assert "Already on the latest release" in out
    assert "Developer update mode" in out
    assert not git.calls and not spawns


def test_git_kind_fetch_failure_exits_nonzero_before_touching_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _Git(**{"rev-parse": (0, "main\n", ""), "fetch": (128, "", "fatal: no such ref")})
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "git fetch origin main failed" in capsys.readouterr().out
    assert not git.ran("reset")


# ── git: the destructive-change confirmation ────────────────────────────────


def _dirty_git(monkeypatch: pytest.MonkeyPatch) -> _Git:
    git = _Git(
        **{
            "rev-parse": (0, "main\n", ""),
            "diff": (1, "", ""),
            "status": (0, " M src/personalclaw/cli.py\n?? scratch.txt\n", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)
    return git


def test_tracked_changes_confirmed_at_a_tty_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _dirty_git(monkeypatch)
    monkeypatch.setattr(cli_server.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    cli_server._update()

    assert git.ran("reset", "--hard", "origin/main")
    out = capsys.readouterr().out
    assert "src/personalclaw/cli.py" in out  # names the tracked file at risk
    assert "scratch.txt" not in out  # untracked files survive a reset — don't cry wolf


def test_tracked_changes_declined_at_a_tty_aborts_with_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _dirty_git(monkeypatch)
    monkeypatch.setattr(cli_server.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    # Declining is a deliberate choice, not a failure.
    assert exc.value.code == 0
    assert "Aborted." in capsys.readouterr().out
    assert not git.ran("reset")
    assert not spawns


def test_eof_at_the_prompt_is_not_a_yes(monkeypatch: pytest.MonkeyPatch, tmp_path, spawns) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _dirty_git(monkeypatch)
    monkeypatch.setattr(cli_server.sys.stdin, "isatty", lambda: True, raising=False)

    def _eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    with pytest.raises(SystemExit):
        cli_server._update()

    assert not git.ran("reset")


def test_non_interactive_stdin_refuses_the_reset_without_prompting(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """No TTY ⇒ never prompt, never guess "yes", and exit non-zero.

    A piped "y" (or an EOFError traceback) would let cron discard uncommitted work
    nobody agreed to lose. Refusing is recoverable; a wrong yes is not.
    """
    _as_git_checkout(monkeypatch, tmp_path)
    _dev_mode(monkeypatch, True)
    git = _dirty_git(monkeypatch)
    monkeypatch.setattr(cli_server.sys.stdin, "isatty", lambda: False, raising=False)

    def _boom(prompt: str = "") -> str:
        raise AssertionError("input() must not be called without a TTY")

    monkeypatch.setattr("builtins.input", _boom)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "stdin is not a terminal" in out
    assert "git stash" in out  # names the remedy
    assert not git.ran("reset")
    assert not spawns


# ── pip / pipx / uv tool ────────────────────────────────────────────────────


def test_pip_kind_upgrades_without_a_source_tree(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """The regression this atom exists for: no PROJECT_DIR, and it still updates."""
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "9.9.9")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)
    _fake_installer(monkeypatch)

    cli_server._update()

    out = capsys.readouterr().out
    assert "PERSONALCLAW_PROJECT_DIR" not in out  # the old dead end is gone
    assert ["FAKE-INSTALLER", "install", "-U", "personalclaw==9.9.9", "--quiet"] in [
        a[:5] for a in spawns
    ]
    assert not git.calls  # a wheel install never touches git
    assert "personalclaw restart" in out  # tells you how to run the new code


def test_pip_kind_already_current_skips_the_installer(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "0.0.1")
    monkeypatch.setattr(cli_server, "__version__", "9.9.9")

    cli_server._update()

    assert "Already on the latest release" in capsys.readouterr().out
    assert not spawns


def test_pip_kind_unknown_latest_upgrades_unpinned(monkeypatch: pytest.MonkeyPatch, spawns) -> None:
    """Offline (no latest tag) still tries: `-U personalclaw`, not a refusal."""
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "")
    _fake_installer(monkeypatch)

    cli_server._update()

    assert ["FAKE-INSTALLER", "install", "-U", "personalclaw", "--quiet"] in spawns


def test_pip_kind_install_failure_reports_one_clean_line(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """uv's stderr is ANSI-colored and leads with the headline — say that, not raw bytes."""
    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "9.9.9")
    _fake_installer(monkeypatch)
    raw = "\x1b[31m×\x1b[0m No solution found when resolving dependencies:\n  ╰─▶ unsatisfiable."

    def _fail(argv, *a, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, 1, "", raw)

    monkeypatch.setattr(cli_server.subprocess, "run", _fail)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "No solution found when resolving dependencies" in out
    assert "\x1b[" not in out  # no escape sequences leaked to the terminal


def test_pip_kind_no_installer_available_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    from personalclaw._installer import NoInstallerError

    monkeypatch.setattr(cli_server, "_latest_release_version", lambda: "9.9.9")

    def _none(args):  # type: ignore[no-untyped-def]
        raise NoInstallerError("no pip, no uv")

    monkeypatch.setattr("personalclaw._installer.install_argv", _none)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "no pip, no uv" in capsys.readouterr().out
    assert not spawns


# ── container / desktop: instructions, not pretending ───────────────────────


def test_container_kind_prints_the_two_commands_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()  # returns, i.e. exit status 0 — see _update's docstring

    out = capsys.readouterr().out
    for cmd in su.container_instructions():
        assert cmd in out
    assert not git.calls and not spawns


def test_desktop_kind_delegates_to_the_app_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "updates itself" in capsys.readouterr().out
    assert not git.calls and not spawns


def test_container_env_beats_a_git_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """A container built from a checkout must not run the git pipeline."""
    _as_git_checkout(monkeypatch, tmp_path)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "container install" in capsys.readouterr().out
    assert not git.calls and not spawns
