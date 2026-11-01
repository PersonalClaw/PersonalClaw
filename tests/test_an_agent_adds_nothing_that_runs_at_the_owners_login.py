"""An agent adds nothing to the places whose entries run at the owner's login.

macOS launch agents (``~/Library/LaunchAgents/``), XDG autostart entries (``autostart/`` in
``$XDG_CONFIG_HOME``), systemd's user units (``systemd/user/`` there and in ``$XDG_DATA_HOME``), its
user manager's settings (``systemd/user.conf``, ``user.conf.d/``), the session's environment
(``environment.d/``) and the X session scripts (``~/.xprofile``, ``~/.xsessionrc``,
``~/.xsession``, ``~/.xinitrc``) hold what the owner's system runs as her at every login, outside
any sandbox. They are one owner-only kind
(``owner_only.LOGIN``), so every path an agent writes by refuses a change to one before anyone is
asked, in the words the other owner-only places use: the native file tools, the shell's screen, an
agent CLI's own write, an automation's files to change, and the macOS profile, which denies writes
under each. Reading them still works, ``$XDG_CONFIG_HOME`` and ``$XDG_DATA_HOME`` decide where
theirs are, and a file in another folder of ``~/.config`` still writes. Each case names its place
by path.

Same harness as ``test_an_agent_changes_nothing_git_or_her_shell_runs_as_her.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_an_agent_changes_nothing_git_or_her_shell_runs_as_her import (  # noqa: F401 — fixture
    OWNERS,
    _bash,
    _refusal,
    _screened,
    owner,
)

from personalclaw import owner_only

pytestmark = pytest.mark.security

#: What a refusal of a change to one says.
LOGINS = "is where the owner's system keeps what it runs as the owner at every login"
#: An entry already there, which the owner put in.
ENTRY = "<plist version='1.0'><dict/></plist>\n"

#: Each place, from the owner's home folder, and the entry a write would add in it (none for a
#: place that is a file, which a write would change).
PLACES = {
    "launch agents": ("Library/LaunchAgents", "com.example.helper.plist"),
    "autostart": (".config/autostart", "helper.desktop"),
    "systemd user units": (".config/systemd/user", "helper.service"),
    "systemd user units in the data folder": (".local/share/systemd/user", "helper.service"),
    "the user manager's settings folder": (".config/systemd/user.conf.d", "50-helper.conf"),
    "the session's environment": (".config/environment.d", "50-helper.conf"),
    "the user manager's settings": (".config/systemd/user.conf", ""),
    "the X session profile": (".xprofile", ""),
    "the X session rc": (".xsessionrc", ""),
    "the X session": (".xsession", ""),
    "xinit's script": (".xinitrc", ""),
}


def _written(place: str) -> str:
    """What a write to *place* names, from the owner's home folder."""
    where, entry = PLACES[place]
    return f"{where}/{entry}" if entry else where


@pytest.fixture
def login(owner, monkeypatch):  # noqa: F811 — the imported fixture, used by name
    """Each folder place in the owner's home folder with the owner's own entry in it, the folder of
    each file place, another app's folder in ``~/.config``, and her home folder an allowed working
    directory."""
    for where, entry in PLACES.values():
        if entry:
            (owner.user / where).mkdir(parents=True, exist_ok=True)
            (owner.user / where / "owners-own.entry").write_text(ENTRY)
        else:
            (owner.user / where).parent.mkdir(parents=True, exist_ok=True)
    (owner.user / ".config" / "other-app").mkdir(parents=True)
    allowed = {"agent": {"subagent_cwd_allowed_roots": [str(owner.user)]}}
    (owner.home / "config.json").write_text(json.dumps(allowed))
    return SimpleNamespace(owner=owner, user=owner.user)


@pytest.mark.parametrize("place", PLACES)
def test_each_is_a_login_place(login, place) -> None:
    where, _entry = PLACES[place]
    assert owner_only.kind_of(login.user / _written(place)) == "login"
    assert owner_only.kind_of(login.user / where) == "login"
    assert owner_only.kind_of(login.user / ".config" / "other-app" / "a.json") == ""


@pytest.mark.parametrize("place", PLACES)
def test_the_file_tools_refuse_a_write_to_each(login, place) -> None:
    refused = _refusal(login.owner, f"~/{_written(place)}")
    assert refused is not None and refused.control == "owner_only", refused
    assert LOGINS in str(refused) and OWNERS in str(refused), str(refused)
    assert _refusal(login.owner, "~/.config/other-app/a.json") is None


@pytest.mark.parametrize("place", PLACES)
def test_the_shell_does_not_copy_a_file_to_each(login, place) -> None:
    target = login.user / _written(place)
    result = _bash(login.owner, f"cp README.md ~/{_written(place)}")
    assert not result.success
    assert LOGINS in result.error and OWNERS in result.error, result.error
    assert not target.exists()


@pytest.mark.parametrize("place", PLACES)
def test_an_agent_clis_write_to_each_is_refused_where_it_asks(login, place) -> None:
    from personalclaw.hooks import TOOL_DENY

    path = str(login.user / _written(place))
    verdict = _screened(
        f"Write {path}", {"file_path": path, "content": ENTRY}, cwd=login.owner.repo
    )
    assert verdict.action == TOOL_DENY, verdict
    assert verdict.control == "owner_only" and LOGINS in verdict.reason


def test_reading_them_and_writing_another_apps_folder_still_work(login) -> None:
    for command in (
        "ls ~/Library/LaunchAgents",
        "cat ~/.config/autostart/owners-own.entry",
        "echo '{}' > ~/.config/other-app/a.json",
    ):
        result = _bash(login.owner, command)
        assert result.success, (command, result.error)


def test_the_xdg_variables_say_where_the_config_and_data_places_are(login, monkeypatch) -> None:
    config, data = login.user / "xdg-config", login.user / "xdg-data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    for path in (
        config / "autostart" / "helper.desktop",
        config / "systemd" / "user" / "helper.service",
        config / "systemd" / "user.conf",
        config / "systemd" / "user.conf.d" / "50-helper.conf",
        config / "environment.d" / "50-helper.conf",
        data / "systemd" / "user" / "helper.service",
    ):
        assert owner_only.kind_of(path) == "login", path
    assert owner_only.kind_of(login.user / ".config" / "autostart" / "helper.desktop") == ""
    assert owner_only.kind_of(login.user / ".local/share/systemd/user/helper.service") == ""


def test_an_automation_may_not_name_one_as_a_file_it_changes(login) -> None:
    from personalclaw.write_scope import problem

    entry = f"{login.user}/Library/LaunchAgents/com.example.helper.plist"
    assert problem([entry]) == (
        f"{entry} is where your system keeps what it runs as you at every login, which no "
        "automation may change"
    )


def test_the_macos_profile_denies_writes_under_each(login) -> None:
    from personalclaw.sandbox import _build_seatbelt_profile

    profile = _build_seatbelt_profile("standard")
    for where, _entry in PLACES.values():
        assert f'(deny file-write* (subpath "{login.user / where}"))' in profile, where
    assert f'(deny file-write* (literal "{login.user / "Library"}"))' in profile
    assert "other-app" not in profile
