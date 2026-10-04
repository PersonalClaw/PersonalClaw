"""Every check that compares versions reads them one way: the packaging standard's.

The app gate, the Store, the manifest and parts of the updater each read versions with a rule of
their own, and the rules disagreed on the versions a release line ships: a candidate's two
spellings (its tag ``v0.3.0-rc.1``, its installed ``0.3.0rc1``), a dev build, a post release.
The Store dropped every suffix, so a release never replaced its own candidate; the stable channel
took a candidate whose tag had no dash for a release; a pin spelled as ``personalclaw --version``
prints it was refused; and an update's change list started at a tag spelled from the installed
version, which a candidate does not have.

``personalclaw.versions`` is the one reading now, and each of those checks asks it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from personalclaw import self_update
from personalclaw.apps import catalog, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import loader

# ── the reading ─────────────────────────────────────────────────────────────────────────────
# Imported in each test, so every other test here reads its own failure where the module is absent.


def test_one_version_spelled_two_ways_is_one_version():
    from personalclaw.versions import parse_version, same_version

    assert same_version("v0.3.0-rc.1", "0.3.0rc1")
    assert same_version(" v0.3.0 ", "0.3.0")
    assert parse_version("0.3.0-rc.1") == parse_version("0.3.0rc1")
    assert not same_version("0.3.0rc1", "0.3.0")


def test_a_release_line_orders_the_way_it_ships():
    from personalclaw.versions import is_newer, order_key

    line = [
        "0.2.9",
        "0.3.0.dev1",
        "0.3.0a1",
        "0.3.0b1",
        "v0.3.0-rc.1",
        "0.3.0rc2",
        "0.3.0",
        "0.3.0.post1",
        "0.3.1",
        "0.10.0",
    ]
    for earlier, later in zip(line, line[1:]):
        assert is_newer(later, earlier), (later, earlier)
        assert not is_newer(earlier, later), (earlier, later)
    shuffled = list(line)
    random.Random(7).shuffle(shuffled)
    assert sorted(shuffled, key=order_key) == line


@pytest.mark.parametrize("text", ["latest", "", "1.2.x", ">=1.0", "main", "1.0.0-foo", "v"])
def test_a_string_that_is_not_a_version_is_never_newer_older_or_the_same(text):
    from personalclaw.versions import is_newer, parse_version, same_version

    assert parse_version(text) is None
    assert not is_newer(text, "0.1.0") and not is_newer("0.1.0", text)
    assert not same_version(text, text)


def test_every_unreadable_version_orders_below_every_readable_one():
    from personalclaw.versions import order_key

    assert sorted(["0.0.1", "latest", "0.0.0", ""], key=order_key)[2:] == ["0.0.0", "0.0.1"]
    assert order_key("latest") == order_key("")


@pytest.mark.parametrize(
    "text", ["1" * 5000, "1." * 5000, "0.1.0+" + "a" * 10_000, "0.1.0-rc." + "9" * 5000]
)
def test_reading_a_hostile_version_never_raises(text):
    from personalclaw.versions import parse_version

    assert parse_version(text) is None


# ── the app manifest ────────────────────────────────────────────────────────────────────────


def _version_errors(version: str) -> list[str]:
    m = AppManifest(name="demo", version=version, displayName="Demo", description="d")
    return [e for e in m.validate() if "version" in e]


@pytest.mark.parametrize(
    "version",
    [
        "1.0.0",
        "1.0.0-rc.1",
        "1.0.0rc1",
        "1.0.0-beta.2",
        "1.0.0+build.5",
        "1.0.0.post1",
        "2.0.0.dev3",
    ],
)
def test_an_app_version_the_comparison_reads_is_accepted(version):
    assert _version_errors(version) == []


@pytest.mark.parametrize(
    "version",
    ["1.0.0-foo", "1.0.0-x.7.z.92", "1.0.0-", "v1.0.0", " 1.0.0", "1.0", "1.0.0.0", "latest"],
)
def test_an_app_version_the_comparison_cannot_order_is_refused(version):
    assert _version_errors(version) == [
        f"version must be MAJOR.MINOR.PATCH, optionally with a pre-release such as 1.0.0-rc.1, "
        f"got: {version!r}"
    ]


# ── the Store ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    for module in (cfg, manager, catalog, _er, _inbox):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    return tmp_path


def _write_app(folder: Path, name: str, version: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "displayName": name.title(), "description": "d"}
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")


def _installed(home: Path, name: str, version: str) -> None:
    _write_app(home / "apps" / name, name, version)
    installed = {"name": name, "version": version, "enabled": True, "origin": "local"}
    (home / "apps" / name / "installed.json").write_text(json.dumps(installed), encoding="utf-8")


def _offered(home: Path, source: str, name: str, version: str) -> None:
    _write_app(home / source / name, name, version)
    if str(home / source) not in catalog.list_local_sources():
        catalog.add_local_source(str(home / source))


def _updates() -> dict[str, str]:
    return {u["name"]: u["latestVersion"] for u in catalog.updates_available()}


def test_the_store_offers_a_release_over_its_own_candidate(store):
    _installed(store, "notes", "1.0.0-rc.1")
    _offered(store, "myapps", "notes", "1.0.0")
    assert _updates() == {"notes": "1.0.0"}


def test_the_store_never_offers_a_candidate_over_its_release(store):
    _installed(store, "notes", "1.0.0")
    _offered(store, "myapps", "notes", "1.0.0-rc.2")
    assert _updates() == {}


def test_the_newest_copy_across_sources_is_the_release_not_its_candidate(store):
    _installed(store, "notes", "1.0.0")
    _offered(store, "a", "notes", "1.1.0-rc.1")
    _offered(store, "b", "notes", "1.1.0")
    assert _updates() == {"notes": "1.1.0"}


class _State:
    def __init__(self) -> None:
        self.notifications: list[str] = []
        self._inbox_svc = None

    def notify(self, kind, title, body, *, meta=None):  # noqa: ANN001
        self.notifications.append(title)


def test_a_release_after_its_announced_candidate_is_announced_too(store):
    _installed(store, "notes", "1.0.0")
    _offered(store, "myapps", "notes", "1.2.0-rc.1")
    state = _State()
    catalog.surface_app_updates(state)
    _offered(store, "myapps", "notes", "1.2.0")
    catalog.surface_app_updates(state)
    assert len(state.notifications) == 2
    assert catalog._load_notified()["notes"] == "1.2.0"


# ── the updater ─────────────────────────────────────────────────────────────────────────────


def _release(tag: str, prerelease: bool = False) -> dict[str, object]:
    return {"tag": tag, "name": tag, "body": "", "prerelease": prerelease}


@pytest.mark.parametrize("tag", ["v0.3.0rc1", "v0.3.0.dev1", "v0.3.0b2"])
def test_an_unflagged_pre_release_stays_off_the_stable_channel(tag):
    releases = [_release(tag), _release("v0.2.1")]
    assert self_update.select_target(releases, "stable") == "v0.2.1"
    assert self_update.select_target(releases, "beta") == tag


def test_a_pin_spelled_as_the_installed_version_names_its_release():
    releases = [_release("v0.3.0-rc.1", prerelease=True), _release("v0.2.1")]
    assert self_update.normalize_pin("0.3.0rc1") == "0.3.0rc1"
    assert self_update.select_target(releases, "stable", "0.3.0rc1") == "v0.3.0-rc.1"
    assert self_update.select_target(releases, "stable", "0.3.0-rc.1") == "v0.3.0-rc.1"


@pytest.mark.parametrize(
    "pin", ["0.2", "0.2.x", ">=0.2,<0.3", "latest", "v", "1.2.3.4", "0.3.0+local", "1!0.3.0"]
)
def test_a_pin_that_names_no_release_is_still_refused(pin):
    with pytest.raises(ValueError, match="not a release version"):
        self_update.normalize_pin(pin)


def test_a_rollback_point_is_not_recorded_for_the_same_version_spelled_again(monkeypatch):
    recorded: list[dict[str, str]] = []
    monkeypatch.setattr(self_update, "read_run_state", lambda: {"version": "0.3.0-rc.1"})
    monkeypatch.setattr(self_update, "write_updates_fields", lambda f: recorded.append(f) or True)
    monkeypatch.setattr(self_update, "write_run_state", lambda v: None)
    assert self_update.record_running_version("0.3.0rc1") == ""
    assert recorded == []


# ── the change list an update shows ─────────────────────────────────────────────────────────


class _Git:
    """The git a checkout's update check runs, answered from a fixture: already at its upstream
    (the code was pulled, the gateway not restarted), whose newest version is 0.3.0."""

    def __init__(self, tags: list[str]) -> None:
        self.tags = tags
        self.diff_ranges: list[str] = []

    async def __call__(self, *argv, **kwargs):  # noqa: ANN002, ANN003
        args = list(argv)
        sub = next(a for a in args[1:] if a in {"fetch", "rev-parse", "show", "tag", "diff"})
        out = b""
        if sub == "rev-parse":
            out = b"abc123\n"
        elif sub == "show":
            out = b'[project]\nname = "personalclaw"\nversion = "0.3.0"\n'
        elif sub == "tag":
            out = "\n".join(self.tags).encode()
        elif sub == "diff":
            self.diff_ranges.append(next(a for a in args if ".." in a))
            out = b"+++ b/CHANGELOG.md\n+## [0.3.0]\n+- **A change since the candidate**\n"
        return _Proc(out)


class _Proc:
    returncode = 0

    def __init__(self, out: bytes) -> None:
        self._out = out

    async def communicate(self):
        return self._out, b""


@pytest.mark.asyncio
async def test_an_updates_change_list_starts_at_the_running_candidates_tag(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import updates

    git = _Git(["v0.2.0", "v0.3.0-rc.1", "v0.3.0"])
    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", git)
    monkeypatch.setattr(updates, "_local_version", "0.3.0rc1")
    monkeypatch.setattr(updates.self_update, "may_check_for_updates", lambda asked: True)
    monkeypatch.setattr(updates.self_update, "detect_install_kind", lambda: "git")
    monkeypatch.setattr(updates.self_update, "source_checkout", lambda: str(tmp_path))
    monkeypatch.setattr(updates, "_update_info", {})

    assert await updates._do_update_check(asked=True) is True
    assert git.diff_ranges == ["v0.3.0-rc.1..abc123"]
    assert updates._update_info["available"] is True
    assert "- **A change since the candidate**" in updates._update_info["changes"]


@pytest.mark.asyncio
async def test_a_running_version_with_no_tag_shows_no_change_list(tmp_path, monkeypatch):
    from personalclaw.dashboard.handlers import updates

    git = _Git(["v0.2.0", "v0.3.0"])
    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", git)
    monkeypatch.setattr(updates, "_local_version", "0.3.0.dev4")
    monkeypatch.setattr(updates.self_update, "may_check_for_updates", lambda asked: True)
    monkeypatch.setattr(updates.self_update, "detect_install_kind", lambda: "git")
    monkeypatch.setattr(updates.self_update, "source_checkout", lambda: str(tmp_path))
    monkeypatch.setattr(updates, "_update_info", {})

    assert await updates._do_update_check(asked=True) is True
    assert git.diff_ranges == []
    assert updates._update_info["available"] is True and updates._update_info["changes"] == ""
