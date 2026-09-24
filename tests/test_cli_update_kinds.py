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
    """No release probe (network) and no frontend build in any of these tests.

    The pip/git updaters resolve their target through ``self_update.resolve_target``
    → ``fetch_releases`` (RUM-2/6). Stub the network seam (`fetch_releases`) to an
    empty list so the REAL resolver runs offline-degraded (→ ``""``); a test that
    wants a concrete target either patches `fetch_releases` with a list (real
    resolver) or overrides `resolve_target` directly via `_fake_resolve`. Also pin a
    default `stable`/no-pin config so `AppConfig.load()` never touches disk.
    """

    async def _no_releases() -> list:
        return []

    monkeypatch.setattr(su, "fetch_releases", _no_releases)
    monkeypatch.setattr(cli_server, "build_frontend_sync", lambda path: None)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    _channel(monkeypatch)


def _channel(monkeypatch: pytest.MonkeyPatch, channel: str = "stable", pin: str = "") -> None:
    """Pin the `updates` channel/pin (RUM-4) without writing a config file.

    The git updater's cadence is now the `updates.channel` block, not the retired
    `dashboard.update_dev_mode` bool: `nightly` tracks the branch (fast-forward),
    every other channel rides the resolved release tag.
    """
    cfg = types.SimpleNamespace(updates=types.SimpleNamespace(channel=channel, pin=pin))
    monkeypatch.setattr(cli_server.AppConfig, "load", classmethod(lambda cls: cfg))


#: The REAL ``AppConfig.load``, captured at IMPORT — before the autouse ``_channel``
#: fixture replaces it with a no-disk fake. The RUM-9 rollback tests are the one group
#: here that needs the loader to actually READ the ``config.json`` that ``--to`` just
#: wrote: the whole claim under test is that the pin round-trips through the file, so a
#: fake that returns a hard-coded pin would prove nothing.
_REAL_APPCONFIG_LOAD = cli_server.AppConfig.load


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
    _fake_resolve(monkeypatch, "v9.9.9")
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
    _fake_resolve(monkeypatch, "v0.0.1")
    monkeypatch.setattr(cli_server, "__version__", "9.9.9")

    cli_server._update()

    assert "Already on the latest release" in capsys.readouterr().out
    assert not spawns


def test_pip_kind_unknown_latest_upgrades_unpinned(monkeypatch: pytest.MonkeyPatch, spawns) -> None:
    """Offline (no release resolves) still tries: `-U personalclaw`, not a refusal.

    The autouse fixture stubs `fetch_releases` to an empty list, so the real
    `resolve_wheel_target` degrades to `""` on the default `stable`/no-pin config —
    exactly the offline case. With no pin, an empty target upgrades unpinned rather
    than refusing (a pin, by contrast, refuses; see the pin-miss test)."""
    _fake_installer(monkeypatch)

    cli_server._update()

    assert ["FAKE-INSTALLER", "install", "-U", "personalclaw", "--quiet"] in spawns


def test_pip_kind_install_failure_reports_one_clean_line(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """uv's stderr is ANSI-colored and leads with the headline — say that, not raw bytes."""
    _fake_resolve(monkeypatch, "v9.9.9")
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

    _fake_resolve(monkeypatch, "v9.9.9")

    def _none(args):  # type: ignore[no-untyped-def]
        raise NoInstallerError("no pip, no uv")

    monkeypatch.setattr("personalclaw._installer.install_argv", _none)

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    assert exc.value.code == 1
    assert "no pip, no uv" in capsys.readouterr().out
    assert not spawns


# ── pip honors the `updates` channel/pin (RUM-6) ────────────────────────────

# A releases list ADVERSARIAL to a "blind latest" install: the newest release is a
# PRERELEASE, so stable and beta resolve to DIFFERENT tags, and the pin points at an
# even older one. A `-U personalclaw==<releases/latest>` implementation would install
# 0.2.1 for all three rows and fail beta + pin — which is exactly the bug RUM-6 kills.
_RUM6_RELEASES = [
    {"tag": "v0.3.0-rc.1", "prerelease": True},
    {"tag": "v0.2.1", "prerelease": False},
    {"tag": "v0.2.0", "prerelease": False},
]

#: The running version the RUM-6 rows execute against, FIXED here instead of inherited
#: from the project's own ``__version__``.
#:
#: ``_update_pip`` only installs a resolved target that DIFFERS from what is running (it
#: short-circuits on "Already on the pinned/latest release"), so "the running version is
#: not one of these tags" is a precondition of every row below. Inheriting it from the
#: project turned each row into a landmine that fires on one specific future release —
#: the pin row on v0.2.0 (equality), the stable row on v0.2.1 and the beta row on v0.3.0
#: (``_is_current``) — i.e. a fixture that self-destructs exactly when the project ships
#: the version it hard-codes. Bumping the literals would only move those landmines one
#: release along; pinning the running version removes the project's version from the
#: fixture altogether, so these rows are version-INDEPENDENT rather than
#: correct-until-the-next-bump. ``0.0.1`` is below every published release, and versions
#: only go up, so it is a value this project can never take again.
_RUM6_RUNNING_VERSION = "0.0.1"

#: ``(channel, pin, expected wheel spec)`` over ``_RUM6_RELEASES``, extracted so the
#: fixture's own non-vacuity floor can assert over the same rows the parametrisation drives.
_RUM6_ROWS = [
    ("stable", "", "personalclaw==0.2.1"),  # newest non-prerelease
    ("beta", "", "personalclaw==0.3.0-rc.1"),  # newest INCLUDING prereleases
    ("stable", "0.2.0", "personalclaw==0.2.0"),  # pin overrides the channel exactly
]


def _fake_release_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feed the REAL resolver a fixed releases list (no network, no resolve_target stub)."""

    async def _list() -> list:
        return [dict(r) for r in _RUM6_RELEASES]

    monkeypatch.setattr(su, "fetch_releases", _list)


def test_the_rum6_rows_resolve_to_distinct_versions() -> None:
    """The fixture's own floor: the three rows must name THREE DIFFERENT versions.

    This is the non-vacuity the parametrised test's docstring claims, asserted instead of
    assumed: a `releases/latest` implementation can only fail the beta and pin rows while
    they differ from stable's latest, so a future edit that collapses two rows onto one
    version would quietly make this a single-row test that any blind-latest build passes.
    """
    versions = [spec.split("==", 1)[1] for _, _, spec in _RUM6_ROWS]
    assert len(set(versions)) == len(versions), f"rows collapsed onto one version: {versions}"
    tags = {su.normalize_version(str(r["tag"])) for r in _RUM6_RELEASES}
    assert set(versions) <= tags, f"a row names a version no release publishes: {versions}"


@pytest.mark.parametrize("channel, pin, expected", _RUM6_ROWS)
def test_pip_installs_the_channel_pin_resolved_spec(
    monkeypatch: pytest.MonkeyPatch, spawns, channel: str, pin: str, expected: str
) -> None:
    """RUM-6 core: the wheel installed is the channel/pin-resolved tag, never a blind latest.

    Non-vacuous by construction — beta (0.3.0-rc.1) and the pin (0.2.0) resolve to
    versions DIFFERENT from stable's latest (0.2.1) over the same releases list, so a
    `releases/latest` implementation would fail the beta and pin rows (asserted by
    `test_the_rum6_rows_resolve_to_distinct_versions`). Drives the REAL
    `resolve_wheel_target`/`select_target` over `_RUM6_RELEASES` (only `fetch_releases`
    is stubbed), so the resolver policy itself is exercised, not mocked away.

    The running version is pinned to `_RUM6_RUNNING_VERSION` so the resolved target is
    always something to move TO; see that constant for why inheriting the project's
    version made this row fail on exactly one release.
    """
    monkeypatch.setattr(cli_server, "__version__", _RUM6_RUNNING_VERSION)
    _channel(monkeypatch, channel, pin)
    _fake_release_list(monkeypatch)
    _fake_installer(monkeypatch)

    # The precondition for this row's assertion being REACHABLE, proven not assumed. Both
    # comparisons are load-bearing: the pin branch short-circuits on normalized-string
    # equality, and the channel branch on `_is_current`'s tuple ordering — which drops the
    # prerelease suffix, so `0.3.0-rc.1` and `0.3.0` are equal tuples but distinct strings.
    want = expected.split("==", 1)[1]
    running = cli_server.__version__
    assert su.normalize_version(want) != su.normalize_version(running)
    assert su.version_tuple(want) > su.version_tuple(running)

    cli_server._update()

    assert any(
        a[:5] == ["FAKE-INSTALLER", "install", "-U", expected, "--quiet"] for a in spawns
    ), f"expected to install {expected}; spawns={spawns}"


def test_pip_pin_miss_refuses_and_never_installs_latest(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """A pin naming no release must REFUSE, not silently install the latest wheel.

    The whole point of a pin is "stay exactly here"; falling back to `releases/latest`
    on a miss would violate it. Distinguishes RUM-6 from the offline/no-pin case, which
    does upgrade unpinned."""
    _channel(monkeypatch, "stable", "0.9.9")  # no such release in the list
    _fake_release_list(monkeypatch)
    _fake_installer(monkeypatch)

    cli_server._update()

    out = capsys.readouterr().out
    assert "No release matches the pinned version" in out
    assert not spawns  # nothing was installed


# ── container / desktop: instructions, not pretending ───────────────────────


def test_container_kind_prints_the_two_commands_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    # Default config (stable/no-pin) + the autouse offline `fetch_releases`->[] means
    # the resolver degrades to the `latest` fallback (RUM-7): the commands still print
    # and carry `PERSONALCLAW_IMAGE_TAG=latest`, exit 0.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()  # returns, i.e. exit status 0 — see _update's docstring

    out = capsys.readouterr().out
    for cmd in su.container_instructions("latest"):
        assert cmd in out
    assert "PERSONALCLAW_IMAGE_TAG=latest " in out
    assert not git.calls and not spawns


@pytest.mark.parametrize(
    "channel, pin, tag",
    [
        ("stable", "", "0.2"),  # moving minor of the newest stable (v0.2.1)
        ("beta", "", "beta"),  # the moving prerelease tag
        ("stable", "0.2.0", "0.2.0"),  # pin overrides the channel, exact immutable tag
    ],
)
def test_container_prints_the_channel_pin_resolved_tag(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns, channel: str, pin: str, tag: str
) -> None:
    """RUM-7 core: the container commands carry the channel/pin-resolved image tag,
    never a bare `latest`. Non-vacuous — over `_RUM6_RELEASES` stable/beta/pin resolve
    to DIFFERENT tags (0.2 / beta / 0.2.0), so a constant-`latest` implementation fails
    the beta and pin rows. Drives the REAL `resolve_image_tag`/`select_image_tag` (only
    `fetch_releases` is stubbed)."""
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    _channel(monkeypatch, channel, pin)
    _fake_release_list(monkeypatch)
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    for cmd in su.container_instructions(tag):
        assert cmd in out, f"expected {cmd!r} in output; out={out!r}"
    assert f"PERSONALCLAW_IMAGE_TAG={tag} " in out
    assert not git.calls and not spawns


def test_container_pin_miss_refuses_and_prints_no_pull(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """A container pin naming no release must REFUSE — never print a bare `latest` pull
    (mirrors the wheel pin-miss refusal)."""
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    _channel(monkeypatch, "stable", "0.9.9")  # no such release in _RUM6_RELEASES
    _fake_release_list(monkeypatch)
    git = _Git()
    monkeypatch.setattr(su, "_run_git", git)

    cli_server._update()

    out = capsys.readouterr().out
    assert "No release matches the pinned version" in out
    assert "docker compose" not in out  # nothing to pull — no commands offered
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


# ── rollback: `personalclaw update --to <version>` (RUM-9) ───────────────────

# A releases list ADVERSARIAL to every implementation that ignores `--to`: the channel's
# newest release (0.2.0) is NEWER than the running version, so a build that resolved the
# channel — or that treated `--to` as a no-op — installs `personalclaw==0.2.0`, an
# UPGRADE, for a user who asked to roll back. The pinned 0.1.2 is older than both.
_ROLLBACK_RELEASES = [
    {"tag": "v0.2.0", "prerelease": False},
    {"tag": "v0.1.3", "prerelease": False},
    {"tag": "v0.1.2", "prerelease": False},
]


def _real_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Give these tests a REAL, writable config in a tmp home.

    Restores the genuine ``AppConfig.load`` (the autouse fixture's fake never touches
    disk) and points the home at *tmp_path*, so `--to`'s write and `_update_pip`'s read
    are the same file. Nothing here can reach the developer's real ``~/.personalclaw``:
    ``PERSONALCLAW_HOME`` is set explicitly, on top of conftest's autouse isolation.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(cli_server.AppConfig, "load", _REAL_APPCONFIG_LOAD)

    async def _list() -> list:
        return [dict(r) for r in _ROLLBACK_RELEASES]

    monkeypatch.setattr(su, "fetch_releases", _list)


def test_pip_rollback_installs_the_older_pinned_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """RUM-9 core: `update --to <older>` installs `personalclaw==<older>`, a DOWNGRADE.

    Drives the whole path for real — `_pin_before_update` writes `updates.pin` to a
    config file, `AppConfig.load()` reads it back, and the REAL
    `resolve_wheel_target`/`select_target` resolve it over `_ROLLBACK_RELEASES`. Only
    two seams are faked: the releases list (network) and the installer argv, so nothing
    downloads or mutates this environment — the assertion is on the argv that WOULD have
    run, which is also the exact thing the atom names ("the older `==` spec").

    Non-vacuous: the channel's newest is 0.2.0, NEWER than the running 0.1.3, so an
    implementation that ignored `--to` would install `personalclaw==0.2.0` and fail here.
    """
    _real_config_home(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_server, "__version__", "0.1.3")
    _fake_installer(monkeypatch)

    cli_server._update(to="0.1.2")

    assert any(
        a[:5] == ["FAKE-INSTALLER", "install", "-U", "personalclaw==0.1.2", "--quiet"]
        for a in spawns
    ), f"expected the older pinned wheel; spawns={spawns}"
    # and NOT the channel's newest — the upgrade a `--to`-blind build would have done
    assert not any("personalclaw==0.2.0" in arg for a in spawns for arg in a)
    out = capsys.readouterr().out
    assert "Pinned updates.pin = 0.1.2" in out
    assert "personalclaw snapshot" in out  # docs advise a snapshot before a rollback


def test_rollback_pin_persists_in_config(monkeypatch: pytest.MonkeyPatch, tmp_path, spawns) -> None:
    """The pin is PERSISTED, so the next scheduled check stays on the rolled-back release.

    This is the difference between a rollback and a one-shot install: without the stored
    pin, the next check resolves the channel's newest (0.2.0 here) and offers to jump
    straight back to the version the user just left.
    """
    import json as _json

    _real_config_home(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_server, "__version__", "0.1.3")
    _fake_installer(monkeypatch)

    cli_server._update(to="v0.1.2")  # a leading `v` is normalized away

    stored = _json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert stored["updates"]["pin"] == "0.1.2"
    assert cli_server.AppConfig.load().updates.pin == "0.1.2"


def test_rollback_to_an_unusable_version_refuses_before_installing(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns
) -> None:
    """An unusable `--to` exits 1 and installs NOTHING.

    Falling through would be the worst outcome available: the channel's apply would run
    instead, i.e. it would UPGRADE the user who asked to pin.
    """
    _real_config_home(monkeypatch, tmp_path)
    _fake_installer(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        cli_server._update(to="   ")

    assert exc.value.code == 1
    assert "Not a usable version to pin" in capsys.readouterr().out
    assert not spawns
    assert not (tmp_path / "config.json").exists()  # nothing was written either
