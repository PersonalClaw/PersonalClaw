"""Every home keeps its own secrets in the OS keychain, under a name that belongs to it.

The keychain is the machine's: every PersonalClaw home on the machine reaches the same one. So the
keychain half of a home's credential store is filed under the home's own service name
(``credentials.keychain_service``). The default home keeps ``personalclaw``, the name every earlier
release filed every home's items under, so an existing install's secrets stay where they are. Any
other home is named ``personalclaw-<id>``, the id minted on its first keychain write and kept in the
home, never worked out from its path, which can move. A home never lists, reads, uses, hands to a
child process, overwrites or deletes another home's items.

Every test drives the real credential calls against a fake keyring backend that keeps its items in a
dict keyed by ``(service, name)``, stood in through ``keychain_stub``: nothing here reaches the
machine's keychain. Both homes live under ``tmp_path``. The default home is ``$HOME/.personalclaw``
for a scratch ``$HOME``, and the other is named by ``PERSONALCLAW_HOME``, as a dev gateway's is.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import types
from pathlib import Path

import keychain_stub
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import credential_migration as mig
from personalclaw.config import credentials, loader
from personalclaw.config.credentials import (
    credential_names,
    delete_credential,
    find_credential,
    get_credential,
    save_credential,
)
from personalclaw.config.loader import AppConfig

#: The name every release before per-home namespaces filed every home's items under.
TODAYS_NAME = "personalclaw"
#: The entry that lists a namespace's credential names.
INDEX = "__personalclaw_key_index__"
#: The file a home other than the default one keeps its namespace id in, as the docs name it.
NAMESPACE_FILE = "keychain_namespace"

#: One credential name both homes use, as two installs set up for the same service would.
SHARED = "WEATHER_SERVICE_TOKEN"


class _Keychain:
    """A fake OS keychain: items by ``(service, name)``, behind a backend that reads as a real
    store, with a record of every call that reached it."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []

    def module(self) -> types.ModuleType:
        module = types.ModuleType("keyring")

        class _Backend:
            pass

        _Backend.__module__ = "keyring.backends.macOS"

        def get_password(service: str, name: str) -> str | None:
            self.calls.append(("get", service, name))
            return self.items.get((service, name))

        def set_password(service: str, name: str, value: str) -> None:
            self.calls.append(("set", service, name))
            self.items[(service, name)] = value

        def delete_password(service: str, name: str) -> None:
            self.calls.append(("delete", service, name))
            if (service, name) not in self.items:
                raise KeyError(name)  # the real backends raise their "nothing there" error
            del self.items[(service, name)]

        module.get_keyring = lambda: _Backend()  # type: ignore[attr-defined]
        module.get_password = get_password  # type: ignore[attr-defined]
        module.set_password = set_password  # type: ignore[attr-defined]
        module.delete_password = delete_password  # type: ignore[attr-defined]
        return module

    def services(self) -> set[str]:
        return {service for service, _ in self.items}


class _Homes:
    """The default home and other homes on one machine, and which one this process runs on."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        user = root / "user"
        user.mkdir()
        monkeypatch.setenv("HOME", str(user))
        self.default = user / ".personalclaw"
        self.scratch = root / "scratch-home"
        self.other = root / "another-home"

    def use_default(self) -> None:
        self._monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
        assert loader.uses_default_home(), "the premise: this process runs on the default home"

    def use(self, home: Path) -> None:
        self._monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        assert not loader.uses_default_home(), "the premise: this home is not the default one"
        assert loader.resolve_config_dir() == home.resolve()


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> _Keychain:
    """The fake keychain, answering, and asked for: a write goes to the keychain."""
    fake = _Keychain()
    keychain_stub.stand_in(monkeypatch, fake.module())
    monkeypatch.setenv(credentials.CREDENTIAL_BACKEND_ENV, "keychain")
    return fake


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset_env) -> _Homes:
    # A stored credential is mirrored into this process's environment; each name is given back.
    unset_env(SHARED, "PROVIDER_TOKEN", "MAPS_TOKEN")
    return _Homes(tmp_path, monkeypatch)


def _a_process_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget what an earlier save mirrored into this process's environment: a gateway on another
    home is a process of its own, and starts without it."""
    monkeypatch.delenv(SHARED, raising=False)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── two homes ────────────────────────────────────────────────────────────────


def test_a_secret_saved_in_one_home_is_invisible_in_another_and_a_delete_there_leaves_it(
    homes: _Homes, keychain: _Keychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Red before each home had its own name: the scratch home listed the default home's secret,
    read it, mirrored it into the environment its children inherit, and its delete removed it."""
    homes.use_default()
    save_credential(SHARED, "the-default-homes-value")

    homes.use(homes.scratch)
    _a_process_of_its_own(monkeypatch)
    assert get_credential(SHARED) == "", "the scratch home read the default home's secret"
    assert find_credential(SHARED) == ("", "")
    assert SHARED not in credential_names(), "the scratch home listed the default home's secret"
    assert SHARED not in AppConfig.load().load_credentials()
    assert (
        SHARED not in os.environ
    ), "the default home's secret was handed to the scratch's children"
    assert delete_credential(SHARED) is False, "there was nothing of the scratch home's to delete"

    save_credential(SHARED, "the-scratch-homes-value")
    assert get_credential(SHARED) == "the-scratch-homes-value"
    assert delete_credential(SHARED) is True
    assert get_credential(SHARED) == ""

    homes.use_default()
    _a_process_of_its_own(monkeypatch)
    assert get_credential(SHARED) == "the-default-homes-value", "a delete elsewhere removed it"
    assert SHARED in credential_names()
    assert keychain.items[(TODAYS_NAME, SHARED)] == "the-default-homes-value"


def test_the_default_home_still_reads_what_it_saved_under_todays_name(
    homes: _Homes, keychain: _Keychain
) -> None:
    """The control: an install's items, filed under ``personalclaw`` by an earlier release, are
    read where they are, and the default home is given no namespace file of its own."""
    keychain.items[(TODAYS_NAME, "PROVIDER_TOKEN")] = "saved-by-an-earlier-release"
    keychain.items[(TODAYS_NAME, INDEX)] = json.dumps(["PROVIDER_TOKEN"])

    homes.use_default()
    assert get_credential("PROVIDER_TOKEN") == "saved-by-an-earlier-release"
    assert "PROVIDER_TOKEN" in credential_names()
    assert AppConfig.load().load_credentials()["PROVIDER_TOKEN"] == "saved-by-an-earlier-release"

    save_credential("MAPS_TOKEN", "a-new-one")
    assert keychain.items[(TODAYS_NAME, "MAPS_TOKEN")] == "a-new-one"
    assert json.loads(keychain.items[(TODAYS_NAME, INDEX)]) == ["MAPS_TOKEN", "PROVIDER_TOKEN"]
    assert keychain.services() == {TODAYS_NAME}
    assert not (homes.default / NAMESPACE_FILE).exists()


def test_the_default_home_is_named_as_the_default_homes(homes: _Homes, keychain: _Keychain) -> None:
    homes.use_default()
    assert credentials.keychain_service() == TODAYS_NAME
    assert credentials.keychain_namespace() == credentials.KeychainNamespace(TODAYS_NAME, "default")


def test_another_home_is_named_from_the_id_it_keeps_not_from_its_path(
    homes: _Homes, keychain: _Keychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes.use(homes.scratch)
    save_credential(SHARED, "kept-across-a-move")
    service = credentials.keychain_service()
    recorded = (homes.scratch / NAMESPACE_FILE).read_text(encoding="ascii").strip()
    assert service == f"{TODAYS_NAME}-{recorded}", "named from the id the home keeps"
    assert _mode(homes.scratch / NAMESPACE_FILE) == 0o600
    assert credentials.keychain_namespace() == credentials.KeychainNamespace(service, "own")
    assert keychain.items[(service, SHARED)] == "kept-across-a-move"

    moved = homes.scratch.parent / "the-same-home-moved"
    shutil.move(str(homes.scratch), str(moved))
    homes.use(moved)
    _a_process_of_its_own(monkeypatch)
    assert credentials.keychain_service() == service, "a home that moves keeps its name"
    assert get_credential(SHARED) == "kept-across-a-move"

    homes.use(homes.other)
    _a_process_of_its_own(monkeypatch)
    assert get_credential(SHARED) == ""
    save_credential(SHARED, "the-other-homes-own")
    assert credentials.keychain_service() not in (service, TODAYS_NAME, "")
    assert keychain.items[(service, SHARED)] == "kept-across-a-move", "overwritten from elsewhere"


def test_a_home_that_never_stored_a_secret_there_asks_the_keychain_nothing(
    homes: _Homes, keychain: _Keychain
) -> None:
    """Its namespace is named by its first keychain WRITE: until then it holds nothing there, so
    every read answers empty without asking, and nothing is written into the home by a read."""
    homes.use(homes.scratch)
    assert credentials.keychain_namespace() == credentials.KeychainNamespace("", "unnamed")
    assert get_credential(SHARED) == ""
    assert credential_names() == []
    assert AppConfig.load().load_credentials() == {}
    assert delete_credential(SHARED) is False
    assert credentials.credential_store_state().keychain == credentials.KeychainNamespace(
        "", "unnamed"
    )
    assert keychain.calls == []
    assert not (homes.scratch / NAMESPACE_FILE).exists()


def test_a_home_whose_id_file_holds_no_id_uses_no_keychain_and_keeps_the_file(
    homes: _Homes,
    keychain: _Keychain,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail closed: never the default home's name in its place, and never a new id over it, which
    would put whatever was filed under the old one out of the home's reach for good."""
    homes.use_default()
    save_credential(SHARED, "the-default-homes-value")
    homes.scratch.mkdir()
    (homes.scratch / NAMESPACE_FILE).write_text("not a namespace id\n", encoding="ascii")

    homes.use(homes.scratch)
    _a_process_of_its_own(monkeypatch)
    assert credentials.keychain_namespace() == credentials.KeychainNamespace("", "unreadable")
    assert credentials.credential_backend() == "dotenv", "it does not claim a keychain it skips"
    assert NAMESPACE_FILE in credentials.credential_backend_warning()
    # What to do says what is true: no snapshot carries the file, and the items keep their names.
    fix = credentials.credential_store_state().keychain_fix
    assert "write the id back" in fix and f"{TODAYS_NAME}-<id>" in fix and "snapshot" not in fix
    from personalclaw.cli_doctor import _doctor_credentials

    assert _doctor_credentials() == ["credential backend: keychain requested but unavailable"]
    assert "⚠️  keychain namespace: unreadable" in capsys.readouterr().out
    assert get_credential(SHARED) == ""

    save_credential(SHARED, "kept-in-the-file")
    assert get_credential(SHARED) == "kept-in-the-file"
    assert f"{SHARED}=kept-in-the-file" in loader.env_path().read_text(encoding="utf-8")
    assert _mode(loader.env_path()) == 0o600
    assert keychain.services() == {TODAYS_NAME}
    assert keychain.items[(TODAYS_NAME, SHARED)] == "the-default-homes-value"
    assert (homes.scratch / NAMESPACE_FILE).read_text(encoding="ascii") == ("not a namespace id\n")


def test_reading_another_homes_store_reads_that_homes_own_namespace(
    homes: _Homes, keychain: _Keychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``find_credential(home=…)`` names a whole store: that home's ``.env`` and its keychain
    namespace, never one home's file beside another's keychain items."""
    homes.use(homes.scratch)
    save_credential(SHARED, "the-scratch-homes-value")

    homes.use_default()
    _a_process_of_its_own(monkeypatch)
    assert find_credential(SHARED) == ("", "")
    assert find_credential(SHARED, home=homes.scratch) == ("the-scratch-homes-value", "keychain")


def test_two_processes_naming_one_home_at_once_file_under_one_id(
    homes: _Homes, keychain: _Keychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The id is linked into place, so when another process names the home between this one's
    look and its write, this one files under the id that landed, not its own."""
    homes.use(homes.scratch)
    landed = "0123456789abcdef0123456789abcdef"
    real_link = os.link

    def another_process_got_there_first(src, dst, *args, **kwargs):
        Path(dst).write_text(landed + "\n", encoding="ascii")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(credentials.os, "link", another_process_got_there_first)
    save_credential(SHARED, "filed-under-the-id-that-landed")

    assert keychain.items[(f"{TODAYS_NAME}-{landed}", SHARED)] == "filed-under-the-id-that-landed"
    assert [p.name for p in homes.scratch.iterdir() if p.name.endswith(".tmp")] == []


def test_the_move_into_the_keychain_and_its_rollback_touch_only_this_homes_items(
    homes: _Homes, keychain: _Keychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes.use_default()
    save_credential(SHARED, "the-default-homes-value")

    homes.scratch.mkdir()
    (homes.scratch / ".env").write_text(f"{SHARED}=the-scratch-homes-value\n", encoding="utf-8")
    (homes.scratch / ".env").chmod(0o600)
    homes.use(homes.scratch)
    _a_process_of_its_own(monkeypatch)

    moved = mig.migrate_credentials_to_keychain(confirm=True)
    assert moved.ok and moved.moved == [SHARED], moved
    assert keychain.items[(credentials.keychain_service(), SHARED)] == "the-scratch-homes-value"
    assert keychain.items[(TODAYS_NAME, SHARED)] == "the-default-homes-value"

    rolled = mig.rollback_credentials_to_keychain(confirm=True)
    assert rolled.ok, rolled
    assert (credentials.keychain_service(), SHARED) not in keychain.items
    assert keychain.items[(TODAYS_NAME, SHARED)] == "the-default-homes-value", "deleted elsewhere"


# ── the namespace is named where a person looks ──────────────────────────────


def test_doctor_names_the_namespace_in_use(
    homes: _Homes, keychain: _Keychain, capsys: pytest.CaptureFixture[str]
) -> None:
    from personalclaw.cli_doctor import _doctor_credentials

    homes.use_default()
    _doctor_credentials()
    assert f"keychain namespace: {TODAYS_NAME} — the default home's" in capsys.readouterr().out

    homes.use(homes.scratch)
    _doctor_credentials()
    assert "keychain namespace: none yet" in capsys.readouterr().out
    save_credential(SHARED, "v")
    _doctor_credentials()
    assert (
        f"keychain namespace: {credentials.keychain_service()} — this home's own"
        in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_the_doctor_probe_names_the_namespace_in_use(
    homes: _Homes, keychain: _Keychain
) -> None:
    from personalclaw.resilience.doctor import DoctorContext, all_probes

    homes.use(homes.scratch)
    save_credential(SHARED, "v")
    probe = {p.id: p for p in all_probes()}["security.credential_backend"]
    result = await probe.run(DoctorContext(home=homes.scratch))

    assert result.ok is True
    assert result.evidence["keychain_namespace"] == credentials.keychain_service()
    assert result.evidence["keychain_scope"] == "own"
    assert credentials.keychain_service() in result.detail


@pytest.mark.asyncio
async def test_the_secrets_page_reads_the_namespace_in_use(
    homes: _Homes, keychain: _Keychain
) -> None:
    from personalclaw.dashboard.handlers.secrets import register_secrets_routes

    app = web.Application()
    register_secrets_routes(app)
    homes.use(homes.scratch)
    save_credential(SHARED, "the-scratch-homes-value")
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/secrets")
        assert response.status == 200, await response.text()
        raw = await response.text()

    assert json.loads(raw)["store"] == {
        "backend": "keychain",
        "keychain_namespace": credentials.keychain_service(),
        "keychain_scope": "own",
    }
    assert "the-scratch-homes-value" not in raw


# ── the id is the home's, and stays the home's ───────────────────────────────


def test_the_id_file_never_travels_in_a_snapshot_an_export_or_a_sync() -> None:
    """Accounted for, or `audit_home` reports it on every home with a namespace, and as
    machine-local identity rather than state: a copy a restore or an import planted would make two
    homes share one namespace, each reading and deleting the other's secrets."""
    from personalclaw.durability import inventory as inv

    assert credentials.KEYCHAIN_NAMESPACE_FILE == NAMESPACE_FILE
    assert inv.is_ignored(NAMESPACE_FILE)
    assert inv.claim_for(NAMESPACE_FILE) is None, "no snapshot, export or sync carries it"


def test_a_home_with_a_namespace_audits_clean(homes: _Homes, keychain: _Keychain) -> None:
    from personalclaw.durability.inventory import audit_home

    homes.use(homes.scratch)
    save_credential(SHARED, "v")
    assert (homes.scratch / NAMESPACE_FILE).exists()
    assert audit_home(homes.scratch).unclaimed == []
