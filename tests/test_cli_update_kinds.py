"""`personalclaw update` behaves per install kind (DIST-13).

Before this, the CLI *was* the git pipeline: it demanded `$PERSONALCLAW_PROJECT_DIR`
and a `.git` dir, so every pip / pipx / uv-tool user — the install the README lists
first — hit "❌ PERSONALCLAW_PROJECT_DIR not set" and exit 1, while the install-kind
machinery the dashboard already used sat one module away. One test per branch drives
the real dispatch; nothing here runs git, pip, or a frontend build.

The fake layer is deliberately narrow and at the two real seams:
`self_update._run_git` (every sync git spawn funnels through it) and
`cli_server.subprocess.run` (the installer and the post-update `setup --agent-only`).
A test that actually ran `git checkout` / a fast-forward or `pip -U` would be a wrecking ball.
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


def _channel(monkeypatch: pytest.MonkeyPatch, channel: str = "stable", pin: str = "") -> None:
    """Pin the `updates` channel/pin (RUM-4) without writing a config file.

    The git updater's cadence is now the `updates.channel` block, not the retired
    `dashboard.update_dev_mode` bool: `nightly` tracks the branch (fast-forward),
    every other channel rides the resolved release tag.
    """
    cfg = types.SimpleNamespace(updates=types.SimpleNamespace(channel=channel, pin=pin))
    monkeypatch.setattr(cli_server.AppConfig, "load", classmethod(lambda cls: cfg))


def _fake_resolve(monkeypatch: pytest.MonkeyPatch, tag: str) -> None:
    """Make `self_update.resolve_target` return *tag* without any network."""

    async def _resolve(channel: str, pin: str = "") -> str:
        return tag

    monkeypatch.setattr(su, "resolve_target", _resolve)


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


def test_git_release_channel_fetches_tags_and_checks_out_the_resolved_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """stable channel, a newer release resolved ⇒ fetch --tags + checkout <tag>.

    The core of RUM-4: the git kind rides release TAGS, never `git pull` /
    `reset --hard origin/main`."""
    proj = _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "stable")
    _fake_resolve(monkeypatch, "v9.9.9")  # newer than the running version
    monkeypatch.setattr(cli_server, "__version__", "0.1.0")
    git = _Git(**{"status": (0, "", "")})  # clean tree
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("fetch", "--tags", "origin")
    assert git.ran("checkout", "v9.9.9")
    # It never reset --hard nor pulled.
    assert not git.ran("reset")
    assert not git.ran("pull")
    # The install runs, and the agent config is refreshed afterwards.
    assert any("install" in " ".join(a) for a in spawns)
    assert any(a[-2:] == ["setup", "--agent-only"] for a in spawns)
    assert proj in capsys.readouterr().out


def test_git_release_channel_on_latest_tag_rides_tags_not_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """stable channel + already on the resolved tag ⇒ don't touch the tree.

    Being on the latest release TAG is "up to date" even when `main` has newer
    commits — the whole point of retiring pull-from-main."""
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "stable")
    _fake_resolve(monkeypatch, "v0.0.1")  # older than / equal to the running version
    monkeypatch.setattr(cli_server, "__version__", "9.9.9")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    assert "Already on the latest release" in out
    assert not git.calls and not spawns


def test_git_release_channel_offline_makes_no_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """Offline (resolve_target → "") ⇒ nothing to update to, tree untouched."""
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "stable")
    _fake_resolve(monkeypatch, "")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "No matching release found" in capsys.readouterr().out
    assert not git.ran("checkout") and not spawns


def test_git_pin_checks_out_the_pinned_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """A pin overrides the channel and is checked out even if it differs (rollback)."""
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "stable", pin="0.1.0")
    _fake_resolve(monkeypatch, "v0.1.0")
    monkeypatch.setattr(cli_server, "__version__", "0.2.0")  # running a NEWER version
    git = _Git(**{"status": (0, "", "")})
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("checkout", "v0.1.0")
    assert not git.ran("reset")


def test_git_nightly_channel_fast_forwards_the_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """nightly channel ⇒ fetch + fast-forward the branch, NEVER reset --hard."""
    proj = _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "nightly")
    git = _Git(
        **{
            "rev-parse": (0, "main\n", ""),
            "diff": (1, "", ""),  # HEAD != origin/main → something to advance
            "status": (0, "", ""),  # clean tree
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert git.ran("fetch", "origin", "main")
    assert git.ran("merge", "--ff-only", "origin/main")
    assert not git.ran("reset")  # the destructive path is gone
    assert any("install" in " ".join(a) for a in spawns)
    assert proj in capsys.readouterr().out


def test_git_nightly_already_up_to_date_does_not_advance(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "nightly")
    git = _Git(**{"rev-parse": (0, "main\n", ""), "diff": (0, "", "")})
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    assert "Already up to date" in capsys.readouterr().out
    assert not git.ran("merge") and not git.ran("reset")
    assert not spawns


def test_git_nightly_fetch_failure_exits_nonzero_before_touching_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "nightly")
    git = _Git(**{"rev-parse": (0, "main\n", ""), "fetch": (128, "", "fatal: no such ref")})
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "git fetch origin main failed" in capsys.readouterr().out
    assert not git.ran("merge") and not git.ran("reset")


# ── git: a dirty tree is REFUSED, never discarded (RUM-4 has no reset) ────────


def test_release_checkout_refuses_a_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """A tracked edit blocks the checkout and is NEVER discarded — exit 1.

    RUM-4 advances by `git checkout`, which is non-destructive; there is no
    "discard my work?" prompt any more, so the safe answer is always to keep the
    edits and tell the user to commit or stash."""
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "stable")
    _fake_resolve(monkeypatch, "v9.9.9")
    monkeypatch.setattr(cli_server, "__version__", "0.1.0")
    git = _Git(**{"status": (0, " M src/personalclaw/cli.py\n?? scratch.txt\n", "")})
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "src/personalclaw/cli.py" in out  # names the tracked file at risk
    assert "scratch.txt" not in out  # untracked files are safe — don't cry wolf
    assert "git stash" in out  # names the remedy
    assert not git.ran("checkout") and not spawns  # nothing was advanced


def test_nightly_fast_forward_refuses_a_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    _as_git_checkout(monkeypatch, tmp_path)
    _channel(monkeypatch, "nightly")
    git = _Git(
        **{
            "rev-parse": (0, "main\n", ""),
            "diff": (1, "", ""),
            "status": (0, " M src/personalclaw/gateway.py\n", ""),
        }
    )
    monkeypatch.setattr(su, "_run_git", git)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "git stash" in out
    assert not git.ran("merge") and not git.ran("reset") and not spawns


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

    # The delegation must name WHERE the new version comes from. "the app updates itself"
    # (the wording this asserted before #2673) described the unbuilt electron-updater half
    # of DC-1, so a reader followed an instruction with nothing behind it.
    out = capsys.readouterr().out
    assert "desktop install" in out
    assert "https://github.com/PersonalClaw/PersonalClaw/releases" in out
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
