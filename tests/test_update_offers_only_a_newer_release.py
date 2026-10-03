"""An update is offered, pulled or installed only when the release is a move from what runs.

A container's ``personalclaw update`` printed its pull-and-recreate commands whatever the
releases list said: on a 0.2.0 build whose newest published release was 0.1.x, it told the
user to pull ``:0.1``, a minor line back. The Updates panel compared the two and offered
nothing; the CLI never compared at all.

The comparison was the rest of it. It dropped every pre-release suffix, so ``0.3.0-rc.1``,
``0.3.0-rc.2`` and ``0.3.0`` were one version, and it could not read the spelling an installed
candidate reports (``0.3.0rc1``, where its tag says ``v0.3.0-rc.1``) at all: it scored that
lower than every release, so a running candidate was offered every release as an update,
older ones included, and a pinned candidate was reinstalled on every apply.

Everything here drives the real resolvers and the real update paths. Only the network (the
releases list), the installer, git, the config and the running version are faked, so nothing
is pulled, installed or checked out.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from personalclaw import cli_server, container_host
from personalclaw import self_update as su


def _view(tag: str, prerelease: bool = False) -> dict[str, object]:
    return {"tag": tag, "name": tag, "body": "", "prerelease": prerelease}


#: The defect as it was found: a 0.2.0 build, and only 0.1.x published.
_ONLY_OLDER = [_view("v0.1.3"), _view("v0.1.2")]
#: A candidate is the newest release. Its SECOND candidate is listed after the first, so an
#: answer that depends on list order picks the older one.
_CANDIDATES = [
    _view("v0.3.0-rc.1", prerelease=True),
    _view("v0.3.0-rc.2", prerelease=True),
    _view("v0.2.1"),
]
#: The release is out, newer than every candidate — `:beta` still carries the last candidate.
_RELEASED = [
    _view("v0.3.0"),
    _view("v0.3.0-rc.2", prerelease=True),
    _view("v0.3.0-rc.1", prerelease=True),
]
#: No candidate was ever cut, so no release ever published `:beta`.
_NO_CANDIDATE = [_view("v0.2.1"), _view("v0.2.0")]


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every argv the CLI would have run (the installer, `setup --agent-only`); none runs."""
    seen: list[list[str]] = []

    def _fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli_server.subprocess, "run", _fake_run)
    monkeypatch.setattr("personalclaw._installer.require_own_installer", lambda: "fake")
    monkeypatch.setattr(
        "personalclaw._installer.install_argv", lambda args: ["FAKE-INSTALLER", "install", *args]
    )
    monkeypatch.setattr(
        "personalclaw._installer.checkout_install_argv",
        lambda package_root: ["FAKE-INSTALLER", "install", "-e", "."],
    )
    return seen


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every git invocation (they all funnel through `_run_git`); each one succeeds."""
    seen: list[list[str]] = []

    def _fake_git(args, *, cwd, timeout):  # type: ignore[no-untyped-def]
        seen.append(list(args))
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(su, "_run_git", _fake_git)
    return seen


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str,
    running: str,
    releases: list[dict[str, object]],
    channel: str = "stable",
    pin: str = "",
) -> None:
    """Run `personalclaw update` as *kind*, on version *running*, against *releases*."""
    if kind == "git":
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr(su, "detect_install_kind", lambda: "git")
    else:
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", kind)
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)
    monkeypatch.setattr(cli_server, "__version__", running)
    cfg = types.SimpleNamespace(updates=types.SimpleNamespace(channel=channel, pin=pin))
    monkeypatch.setattr(cli_server.AppConfig, "load", classmethod(lambda cls: cfg))

    async def _list() -> list[dict[str, object]]:
        return [dict(r) for r in releases]

    monkeypatch.setattr(su, "fetch_releases", _list)
    monkeypatch.setattr(cli_server, "build_frontend_sync", lambda path: None)


def _pulled_tag(out: str) -> str:
    """The image tag the printed `docker pull` names, or "" when nothing is pulled."""
    for line in out.splitlines():
        words = line.split()
        if words[:2] == ["docker", "pull"]:
            return words[2].rsplit(":", 1)[1]
    return ""


# ── the container: say it is current, or print the commands for a newer release ─────────


def test_a_container_ahead_of_every_release_is_offered_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """The defect as found: a 0.2.0 image with only 0.1.x published was told to pull `:0.1`."""
    _install(monkeypatch, kind="container", running="0.2.0", releases=_ONLY_OLDER)

    cli_server._update()  # returns: exit 0, the command did its job

    out = capsys.readouterr().out
    assert "You're on v0.2.0, newer than the newest release (v0.1.3)." in out
    assert "docker " not in out, out
    assert not spawns


def test_a_container_on_the_newest_release_is_told_so(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    _install(monkeypatch, kind="container", running="0.2.1", releases=_CANDIDATES)

    cli_server._update()

    out = capsys.readouterr().out
    assert "You're on the newest release (v0.2.1)." in out
    assert "docker " not in out, out


def test_a_container_on_its_pinned_release_prints_no_pull(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    _install(monkeypatch, kind="container", running="0.2.1", releases=_CANDIDATES, pin="0.2.1")

    cli_server._update()

    out = capsys.readouterr().out
    assert "Already on the pinned release (v0.2.1)." in out
    assert "docker " not in out, out


def test_a_container_pinned_to_an_older_release_is_shown_the_rollback(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """A pin is a move to exactly that release, and an older one is the rollback."""
    _install(monkeypatch, kind="container", running="0.2.1", releases=_NO_CANDIDATE, pin="0.2.0")

    cli_server._update()

    out = capsys.readouterr().out
    assert _pulled_tag(out) == "0.2.0", out
    assert "⏪ v0.2.1 → v0.2.0" in out


def test_a_container_with_no_release_to_compare_with_pulls_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """Nothing fetched and nothing cached: nothing can be said to be newer, so exit 1.

    It printed a `:latest` pull and exited 0 — possibly a move BACK, for a build ahead of the
    newest release, offered where nothing had been compared."""
    _install(monkeypatch, kind="container", running="0.2.0", releases=[])

    with pytest.raises(SystemExit) as exc:
        cli_server._update()

    printed = capsys.readouterr()
    assert exc.value.code == 1
    assert "No matching release found" in printed.err
    assert "docker " not in printed.out + printed.err


def test_a_running_candidate_is_its_own_release_however_it_is_spelled(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """The image reports `0.3.0rc1`; its release is tagged `v0.3.0-rc.1`. One version."""
    releases = [_view("v0.3.0-rc.1", prerelease=True), _view("v0.2.1")]
    _install(monkeypatch, kind="container", running="0.3.0rc1", releases=releases, channel="beta")

    cli_server._update()

    out = capsys.readouterr().out
    assert "You're on the newest release (v0.3.0-rc.1)." in out
    assert "docker " not in out, out


def test_the_newer_candidate_is_offered_whatever_order_the_list_is_in(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    _install(
        monkeypatch, kind="container", running="0.3.0rc1", releases=_CANDIDATES, channel="beta"
    )

    cli_server._update()

    out = capsys.readouterr().out
    assert "v0.3.0rc1 → v0.3.0-rc.2" in out, out
    assert _pulled_tag(out) == "beta"


def test_beta_follows_a_release_that_is_newer_than_every_candidate(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """`:beta` still carries the last candidate once the release ships: pulling it moves
    nothing. The commands pull the release's own line instead."""
    _install(monkeypatch, kind="container", running="0.3.0rc2", releases=_RELEASED, channel="beta")

    cli_server._update()

    out = capsys.readouterr().out
    assert "v0.3.0rc2 → v0.3.0" in out, out
    assert _pulled_tag(out) == "0.3"


def test_beta_before_the_first_candidate_pulls_a_tag_that_exists(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """With no candidate ever cut, no release published `:beta`: pulling it fails."""
    _install(monkeypatch, kind="container", running="0.2.0", releases=_NO_CANDIDATE, channel="beta")

    cli_server._update()

    assert _pulled_tag(capsys.readouterr().out) == "0.2"


# ── the wheel and the checkout answer the same way ────────────────────────────────────


def test_a_running_candidate_is_not_downgraded_by_the_stable_channel(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    """A wheel running 0.3.0rc1 on stable: `-U personalclaw==0.2.1` would take it BACK."""
    _install(monkeypatch, kind="pip", running="0.3.0rc1", releases=_CANDIDATES)

    cli_server._update()

    assert "You're on v0.3.0rc1, newer than the newest release (v0.2.1)." in (
        capsys.readouterr().out
    )
    assert not spawns, spawns


def test_a_pinned_candidate_that_is_running_is_not_reinstalled(
    monkeypatch: pytest.MonkeyPatch, capsys, spawns
) -> None:
    _install(monkeypatch, kind="pip", running="0.3.0rc1", releases=_CANDIDATES, pin="0.3.0-rc.1")

    cli_server._update()

    assert "Already on the pinned release (v0.3.0-rc.1)." in capsys.readouterr().out
    assert not spawns, spawns


def test_a_pinned_candidate_that_is_checked_out_is_not_checked_out_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys, spawns, git_calls
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(su, "source_checkout", lambda: str(tmp_path))
    _install(monkeypatch, kind="git", running="0.3.0rc1", releases=_CANDIDATES, pin="0.3.0-rc.1")

    cli_server._update()

    assert "Already on the pinned release (v0.3.0-rc.1)." in capsys.readouterr().out
    assert not any(c[:1] == ["checkout"] for c in git_calls), git_calls
    assert not spawns


def test_pinning_back_to_a_candidate_of_the_running_release_is_a_rollback(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """`--to 0.3.0-rc.1` on 0.3.0 goes BACK, and says so with the snapshot advice."""
    monkeypatch.setattr(cli_server, "__version__", "0.3.0")
    monkeypatch.setattr(su, "set_version_pin", lambda version: True)

    cli_server._pin_before_update("0.3.0-rc.1")

    out = capsys.readouterr().out
    assert "Rolling BACK: v0.3.0 → v0.3.0-rc.1" in out
    assert "personalclaw snapshot" in out


# ── the check, the dashboard and the unattended apply ─────────────────────────────────


def _status_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kind: str,
    channel: str,
    releases: list[dict[str, object]],
    check_enabled: bool = True,
    pin: str = "",
) -> None:
    from personalclaw.config import loader as _loader

    if kind == "pip":
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr(su, "detect_install_kind", lambda: "pip")
    else:
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", kind)
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)
    cfg = types.SimpleNamespace(
        updates=types.SimpleNamespace(
            channel=channel,
            pin=pin,
            check_enabled=check_enabled,
            check_interval_hours=12,
            auto="off",
            last_version="",
        )
    )
    monkeypatch.setattr(_loader.AppConfig, "load", classmethod(lambda cls: cfg))

    async def _list() -> list[dict[str, object]]:
        return [dict(r) for r in releases]

    monkeypatch.setattr(su, "fetch_releases", _list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel, releases, available",
    [
        # stable's newest release is 0.2.1: OLDER than the candidate running, not an update.
        ("stable", _CANDIDATES, False),
        # beta's newest is the candidate running — the same version, spelled another way.
        ("beta", [_view("v0.3.0-rc.1", prerelease=True), _view("v0.2.1")], False),
        # ...and a later candidate IS one.
        ("beta", _CANDIDATES, True),
    ],
)
async def test_the_check_offers_a_running_candidate_only_what_is_newer(
    monkeypatch: pytest.MonkeyPatch, channel, releases, available
) -> None:
    """The Updates panel reads `update_available`, and the first two rows read True: an update
    to an older release, and to the one running."""
    _status_config(monkeypatch, kind="pip", channel=channel, releases=releases)

    status = await su.build_update_status("0.3.0rc1", fetch=True)

    assert status["update_available"] is available, status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "running, pin, commands",
    [
        ("0.2.0", "", False),  # ahead of every release: the defect as found, one layer down
        ("0.1.3", "", False),  # on the newest release
        ("0.2.0", "0.1.3", True),  # a pin back is the rollback, and its commands stand
        ("0.1.3", "0.1.3", False),  # already on the pinned release
    ],
)
async def test_a_container_check_carries_commands_only_for_a_move(
    monkeypatch: pytest.MonkeyPatch, running: str, pin: str, commands: bool
) -> None:
    """`instructions` are what `POST /api/update` hands a container install, and they carried
    the pull of `:0.1` to a 0.2.0 image — the CLI's defect, in the payload the panel reads."""
    _status_config(
        monkeypatch,
        kind="container",
        channel="stable",
        releases=_ONLY_OLDER,
        pin=pin,
    )

    status = await su.build_update_status(running, fetch=True)

    assert bool(status["instructions"]) is commands, status["instructions"]
    assert status["image_tag"] == ("0.1.3" if pin else "0.1")


@pytest.mark.asyncio
async def test_a_container_check_pulls_the_release_it_compared(monkeypatch) -> None:
    """On beta, once the release is out, the commands pull the release's line — not the
    `:beta` an older candidate still holds."""
    _status_config(monkeypatch, kind="container", channel="beta", releases=_RELEASED)

    status = await su.build_update_status("0.3.0rc2", fetch=True)

    assert status["latest"] == "0.3.0"
    assert status["update_available"] is True
    assert status["image_tag"] == "0.3"


@pytest.mark.asyncio
async def test_a_container_check_with_checking_off_makes_no_call(monkeypatch, tmp_path) -> None:
    """`check_enabled=false` promises zero outbound calls. The container's image tag came
    from a second probe of the releases list that nothing guarded. Driven through the check
    every path runs, with an eternity since the last one, so only the switch can stop it."""
    from personalclaw.dashboard.handlers import updates as dash_updates

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    su.write_releases_cache({"releases": [dict(r) for r in _NO_CANDIDATE], "etag": ""})
    _status_config(
        monkeypatch, kind="container", channel="stable", releases=[], check_enabled=False
    )
    monkeypatch.setattr(dash_updates, "_last_update_check", 0.0)
    monkeypatch.setattr(dash_updates, "_local_version", "0.2.0")

    async def _no_call() -> list[dict[str, object]]:
        raise AssertionError("check_enabled=false must not fetch the releases list")

    monkeypatch.setattr(su, "fetch_releases", _no_call)

    status = await dash_updates.update_status(asked=False)

    # The answer still comes from what the last check cached.
    assert status["latest"] == "0.2.1"
    assert status["image_tag"] == "0.2"


@pytest.mark.asyncio
async def test_the_staged_apply_leaves_a_pinned_candidate_where_it_is(
    monkeypatch, tmp_path
) -> None:
    """The unattended apply checked the pinned candidate out again on every check."""
    from personalclaw import gateway as gw
    from personalclaw.dashboard.handlers import updates as dash_updates
    from personalclaw.gateway import GatewayOrchestrator

    # Automatic checks on, so the apply reaches its comparison rather than stopping at the gate.
    cfg = types.SimpleNamespace(
        updates=types.SimpleNamespace(channel="stable", pin="0.3.0-rc.1", check_enabled=True)
    )
    monkeypatch.setattr("personalclaw.config.AppConfig.load", lambda *_a, **_k: cfg)
    monkeypatch.setattr("personalclaw.__version__", "0.3.0rc1")
    monkeypatch.setattr(dash_updates, "_local_version", "0.3.0rc1")
    compared: list[str] = []
    real_moves_to = su.moves_to

    def _moves_to(target: str, current: str, pin: str = "") -> bool:
        compared.append(f"{target} from {current}")
        return real_moves_to(target, current, pin)

    monkeypatch.setattr(su, "moves_to", _moves_to)
    moved: list[str] = []

    async def _resolve(channel: str, pin: str = "") -> str:
        return "v0.3.0-rc.1"

    def _fetch_tags(_proj: str) -> subprocess.CompletedProcess[str]:
        moved.append("fetch --tags")
        return subprocess.CompletedProcess([], 1, "", "refused by the test")

    def _checkout(_proj: str, tag: str) -> subprocess.CompletedProcess[str]:
        moved.append(f"checkout {tag}")
        return subprocess.CompletedProcess([], 1, "", "refused by the test")

    async def _no_spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"the staged apply spawned {args}")

    monkeypatch.setattr(su, "resolve_target", _resolve)
    monkeypatch.setattr(su, "source_checkout", lambda: str(tmp_path))
    monkeypatch.setattr(su, "git_tracked_changes", lambda _p: [])
    monkeypatch.setattr(su, "git_fetch_tags", _fetch_tags)
    monkeypatch.setattr(su, "git_checkout", _checkout)
    monkeypatch.setattr(gw.asyncio, "create_subprocess_exec", _no_spawn)

    stub = types.SimpleNamespace(dashboard_state=None)
    await GatewayOrchestrator._auto_apply_update(stub)  # type: ignore[arg-type]

    assert compared == ["v0.3.0-rc.1 from 0.3.0rc1"], "the apply never compared the release"
    assert moved == [], f"the pinned candidate was fetched/checked out again: {moved}"


# ── the rule itself ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "older, newer",
    [
        ("0.3.0-rc.1", "0.3.0-rc.2"),
        ("0.3.0-rc.2", "0.3.0"),
        ("0.3.0-beta.2", "0.3.0-rc.1"),
        ("0.3.0-alpha.1", "0.3.0-beta.1"),
        ("0.3.0rc1", "v0.3.0-rc.2"),  # the installed spelling against a tag
        ("0.2.9", "0.3.0-rc.1"),
        ("0.1.9", "0.1.10"),
    ],
)
def test_pre_releases_order_before_their_release(older: str, newer: str) -> None:
    assert su.is_newer(newer, older)
    assert not su.is_newer(older, newer)
    assert not su.same_version(older, newer)


def test_a_version_is_one_version_however_it_is_spelled() -> None:
    assert su.same_version("v0.3.0-rc.1", "0.3.0rc1")
    assert not su.is_newer("v0.3.0-rc.1", "0.3.0rc1")
    assert not su.is_newer("0.3.0rc1", "v0.3.0-rc.1")
    # Not a version: never the same as anything, never newer than anything.
    assert not su.same_version("nightly", "nightly")
    assert not su.is_newer("nightly", "0.1.0")


@pytest.mark.parametrize(
    "target, running, pin, moves",
    [
        ("v0.2.1", "0.2.0", "", True),  # a channel moves forward...
        ("v0.1.3", "0.2.0", "", False),  # ...never back
        ("v0.2.0", "0.2.0", "", False),  # and not onto itself
        ("v0.1.3", "0.2.0", "0.1.3", True),  # a pin moves back: the rollback
        ("v0.3.0-rc.1", "0.3.0rc1", "0.3.0-rc.1", False),  # a pin onto itself does not
        ("", "0.2.0", "", False),  # no release, no move
        ("", "0.2.0", "0.1.3", False),
    ],
)
def test_a_move_is_forward_on_a_channel_and_exact_on_a_pin(
    target: str, running: str, pin: str, moves: bool
) -> None:
    assert su.moves_to(target, running, pin) is moves


def test_the_newest_candidate_wins_whatever_order_the_list_is_in() -> None:
    assert su.select_target(_CANDIDATES, "beta") == "v0.3.0-rc.2"
    assert su.select_target(list(reversed(_CANDIDATES)), "beta") == "v0.3.0-rc.2"
    # A tag that is not a version cannot be ordered, so it is never the answer.
    assert su.select_target([_view("nightly-build"), _view("v0.1.0")], "beta") == "v0.1.0"
