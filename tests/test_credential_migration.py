"""``credentials_to_keychain`` — the consented, snapshot-backed credential move (SH-2).

The property under test is not "does keyring work". It is the one a credential move must
never get wrong: **no key leaves ``.env`` until its value has been read back out of the
keychain**, and if anything goes wrong the user gets their ``.env`` back byte-for-byte.

⚠️  ``keyring`` is an OPTIONAL extra and is NOT in ``[dev]``/``[test]``: CI does not install
it. Every test here installs a STUB ``keyring`` module in ``sys.modules`` (or blocks the
import outright), so nothing touches a real OS keychain and nothing passes only because the
developer's machine happens to have a secret service. The home is redirected twice — the
``PERSONALCLAW_HOME`` env var *and* ``loader.config_dir`` — and the ``home`` fixture ASSERTS
the redirect landed before any test writes a secret, because a credential test that leaks
into the real login keychain or the real ``~/.personalclaw/.env`` is an unacceptable defect,
not a flaky one.
"""

from __future__ import annotations

import json
import stat
import sys
import types
from pathlib import Path

import pytest

from personalclaw.config import credential_migration as mig
from personalclaw.config import credentials as cred
from personalclaw.config import loader
from personalclaw.config.credentials import CREDENTIAL_BACKEND_ENV, get_credential

_ENV_BODY = (
    "# provider credentials — do not edit by hand\n"
    "SH2_ALPHA=alpha-secret\n"
    "\n"
    "SH2_BETA=beta-secret\n"
    "# a trailing comment\n"
)
_KEYS = ("SH2_ALPHA", "SH2_BETA")


# ── doubles ──────────────────────────────────────────────────────────────────


class _ImportBlocker:
    """A ``sys.meta_path`` finder that makes ``import keyring`` fail, whatever is installed.

    This is how the headless path is proven: uninstalling a package proves nothing about the
    code, while a finder that refuses the name reproduces a keyring-less box on any machine.
    """

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy hook
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "keyring" or fullname.startswith("keyring."):
            raise ImportError(f"blocked by the test: {fullname}")
        return None


def _stub_keyring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend_module: str = "keyring.backends.macOS",
    set_raises: bool = False,
    lying_keys: frozenset[str] = frozenset(),
    undeletable: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Install a fake ``keyring`` in ``sys.modules``; return its backing store.

    ``lying_keys`` is the fixture that matters most: those keys ACCEPT a write and then read
    back something else. That is the failure mode a plain "did set_password raise?" check
    cannot see, and the one that would delete the user's only other copy.
    """
    values: dict[str, str] = {}

    class _Backend:
        pass

    _Backend.__module__ = backend_module
    _Backend.__name__ = _Backend.__qualname__ = "Keyring"

    def get_password(service: str, key: str):
        return values.get(f"{service}\x00{key}")

    def set_password(service: str, key: str, value: str) -> None:
        if set_raises:
            raise RuntimeError("secret service is locked")
        values[f"{service}\x00{key}"] = "corrupted-on-the-way-in" if key in lying_keys else value

    def delete_password(service: str, key: str) -> None:
        if key in undeletable:
            raise RuntimeError("entry is locked")
        values.pop(f"{service}\x00{key}", None)

    module = types.ModuleType("keyring")
    module.get_keyring = lambda: _Backend()  # type: ignore[attr-defined]
    module.get_password = get_password  # type: ignore[attr-defined]
    module.set_password = set_password  # type: ignore[attr-defined]
    module.delete_password = delete_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    return values


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated credential home. Never the real one — these tests write secrets."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    # BOTH bindings. `loader.config_dir` is what `loader.env_path` resolves at call time
    # (which is why `credentials` may bind `env_path` and still be redirected); the env var
    # covers anything that calls `config_dir` through a different binding.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.delenv(CREDENTIAL_BACKEND_ENV, raising=False)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    # 🪤 ASSERT THE REDIRECT, before a single secret is written. A fixture that silently
    # failed to redirect would run this whole file against the developer's real home and
    # every assertion would still pass.
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    assert mig.rollback_snapshot_path() == cfg / ".env.pre-keychain"
    assert tmp_path in mig.rollback_snapshot_path().parents
    return cfg


@pytest.fixture
def keychain_on(home: Path, monkeypatch: pytest.MonkeyPatch):
    """A populated ``.env`` plus an active stub keychain — the migration's happy premise."""
    _write_env(home)
    store = _stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert cred.credential_backend() == "keychain", "the premise: the keychain is ACTIVE"
    return store


def _write_env(home: Path) -> None:
    """Seed `.env` the way the product does: 0600 from the first byte.

    `write_text` alone creates 0644, and the refusal paths deliberately never read the file,
    so nothing would repair the mode — a mode assertion would then be measuring the fixture.
    """
    ep = home / ".env"
    ep.write_text(_ENV_BODY)
    ep.chmod(0o600)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _kc(store: dict[str, str], key: str) -> str | None:
    return store.get(f"personalclaw\x00{key}")


# ── the move ─────────────────────────────────────────────────────────────────


def test_the_migration_moves_env_secrets_into_the_keychain_and_removes_the_keys(
    keychain_on, home: Path
) -> None:
    res = mig.migrate_credentials_to_keychain(confirm=True)

    assert res.ok and res.moved == list(_KEYS), res.to_dict()
    assert res.failed == []
    for key, value in (("SH2_ALPHA", "alpha-secret"), ("SH2_BETA", "beta-secret")):
        assert _kc(keychain_on, key) == value, "the secret is IN the keychain"
        assert f"{key}=" not in (home / ".env").read_text(), "and GONE from .env"
        # The point of the whole exercise: the read is backend-transparent, so every
        # existing caller keeps working across the move without knowing it happened.
        assert get_credential(key) == value


def test_the_moved_env_keeps_its_comments_and_its_0600_mode(keychain_on, home: Path) -> None:
    mig.migrate_credentials_to_keychain(confirm=True)
    body = (home / ".env").read_text()
    assert "# provider credentials — do not edit by hand" in body
    assert "# a trailing comment" in body
    assert _mode(home / ".env") == 0o600, "the removal path shares the write contract"


def test_a_second_run_is_a_no_op_and_does_not_clobber_the_snapshot(keychain_on, home: Path) -> None:
    first = mig.migrate_credentials_to_keychain(confirm=True)
    snap_after_first = mig.rollback_snapshot_path().read_bytes()

    second = mig.migrate_credentials_to_keychain(confirm=True)

    assert second.ok and second.moved == [] and second.already == []
    assert second.rollback_available, "a completed migration stays reversible"
    # 🪤 THE REAL IDEMPOTENCY HAZARD. A second run that re-snapshotted would replace the
    # pre-migration .env with the post-migration one, and rollback would then restore an
    # EMPTY credential store while reporting success.
    assert mig.rollback_snapshot_path().read_bytes() == snap_after_first == _ENV_BODY.encode()
    assert first.moved == list(_KEYS)


def test_verify_passes_after_the_migration_and_reports_what_it_checked(keychain_on) -> None:
    mig.migrate_credentials_to_keychain(confirm=True)
    ok, evidence = mig.verify_credential_migration()
    assert ok
    # A vacuity floor: "verified" must be distinguishable from "verified nothing".
    assert evidence == {"checked": 2, "missing": [], "still_in_dotenv": []}


def test_verify_is_vacuously_true_with_no_snapshot_and_says_so(home: Path) -> None:
    ok, evidence = mig.verify_credential_migration()
    assert ok and evidence["checked"] == 0


def test_verify_fails_when_the_keychain_lost_a_key_it_was_handed(keychain_on) -> None:
    """The falsification rail for verify: it must be able to say NO."""
    mig.migrate_credentials_to_keychain(confirm=True)
    keychain_on.pop("personalclaw\x00SH2_ALPHA")
    ok, evidence = mig.verify_credential_migration()
    assert not ok and evidence["missing"] == ["SH2_ALPHA"]


# ── no key leaves .env unverified ────────────────────────────────────────────


def test_a_keychain_that_lies_about_a_write_keeps_that_key_in_env(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 THE CENTRAL GUARANTEE. A write that "succeeds" and reads back wrong is not a move.

    ``_keychain_save`` returns True here — the backend did not raise — so a migration that
    trusted its return value would have deleted ``SH2_ALPHA`` from the only other copy.
    """
    _write_env(home)
    _stub_keyring(monkeypatch, lying_keys=frozenset({"SH2_ALPHA"}))
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    res = mig.migrate_credentials_to_keychain(confirm=True)

    assert not res.ok
    assert res.failed == ["SH2_ALPHA"] and res.moved == ["SH2_BETA"]
    body = (home / ".env").read_text()
    assert "SH2_ALPHA=alpha-secret" in body, "the unverified key is STILL THERE"
    assert "SH2_BETA=" not in body, "and the verified one moved"
    assert get_credential("SH2_ALPHA") == "alpha-secret", "so the credential is not lost"


def test_a_locked_secret_service_moves_nothing_and_loses_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(home)
    _stub_keyring(monkeypatch, set_raises=True)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    res = mig.migrate_credentials_to_keychain(confirm=True)

    assert not res.ok and sorted(res.failed) == list(_KEYS) and res.moved == []
    assert (home / ".env").read_text() == _ENV_BODY, "byte-identical — nothing was rewritten"


# ── consent and the fail-closed premise ──────────────────────────────────────


def test_without_confirmation_the_migration_does_not_even_read_the_store(
    keychain_on, home: Path
) -> None:
    res = mig.migrate_credentials_to_keychain()
    assert not res.ok and "confirmation required" in res.reason
    assert (home / ".env").read_text() == _ENV_BODY
    assert keychain_on == {}, "nothing was written anywhere"
    assert not mig.rollback_snapshot_path().exists(), "not even the snapshot"


def test_the_gate_being_off_refuses_rather_than_emptying_env(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail CLOSED: with `.env` as the active backend there is nowhere for secrets to go."""
    _write_env(home)
    _stub_keyring(monkeypatch)
    assert cred.credential_backend() == "dotenv", "the gate defaults OFF"

    res = mig.migrate_credentials_to_keychain(confirm=True)

    assert not res.ok and "not the active credential backend" in res.reason
    assert (home / ".env").read_text() == _ENV_BODY


def test_a_headless_box_that_asked_for_a_keychain_refuses_with_the_honest_reason(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(home)
    monkeypatch.setattr(sys, "meta_path", [_ImportBlocker(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert cred.requested_credential_backend() == "keychain"
    assert cred.credential_backend() == "dotenv", "the premise: requested but unavailable"

    res = mig.migrate_credentials_to_keychain(confirm=True)

    assert not res.ok and "no usable OS keyring backend" in res.reason
    assert (home / ".env").read_text() == _ENV_BODY, "the .env fallback is untouched"
    assert _mode(home / ".env") == 0o600


# ── rollback ─────────────────────────────────────────────────────────────────


def test_rollback_restores_env_byte_for_byte_and_clears_the_keychain(
    keychain_on, home: Path
) -> None:
    original = (home / ".env").read_bytes()
    mig.migrate_credentials_to_keychain(confirm=True)
    assert (home / ".env").read_bytes() != original, "the premise: .env really changed"

    res = mig.rollback_credentials_to_keychain(confirm=True)

    assert res.ok and res.moved == list(_KEYS)
    # 🪤 A BYTE COMPARISON, not a key-by-key one. A "field-for-field" restore is exactly how
    # a comment, an ordering or an unparsed line goes missing without any assertion noticing.
    assert (home / ".env").read_bytes() == original
    assert _mode(home / ".env") == 0o600
    for key in _KEYS:
        assert _kc(keychain_on, key) is None, "the keychain copy is gone"
    assert not mig.rollback_snapshot_path().exists(), "and so is the plaintext snapshot"
    assert cred._keychain_index() == [], "the index no longer names a key it does not hold"


def test_rollback_leaves_a_credential_the_user_added_after_migrating(
    keychain_on, home: Path
) -> None:
    mig.migrate_credentials_to_keychain(confirm=True)
    cred.save_credential("SH2_LATER", "later-secret")
    assert _kc(keychain_on, "SH2_LATER") == "later-secret"

    mig.rollback_credentials_to_keychain(confirm=True)

    assert _kc(keychain_on, "SH2_LATER") == "later-secret", "not this operation's business"
    assert cred._keychain_index() == ["SH2_LATER"]


def test_rollback_without_a_snapshot_refuses_instead_of_writing_an_empty_env(
    keychain_on, home: Path
) -> None:
    res = mig.rollback_credentials_to_keychain(confirm=True)
    assert not res.ok and "no pre-migration snapshot" in res.reason
    assert (home / ".env").read_text() == _ENV_BODY


def test_rollback_needs_confirmation_too(keychain_on, home: Path) -> None:
    mig.migrate_credentials_to_keychain(confirm=True)
    res = mig.rollback_credentials_to_keychain()
    assert not res.ok and "confirmation required" in res.reason
    assert mig.rollback_snapshot_path().exists()


def test_a_partial_rollback_keeps_the_snapshot(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshot deleted after a half-cleared keychain would strand the user with both."""
    _write_env(home)
    _stub_keyring(monkeypatch, undeletable=frozenset({"SH2_BETA"}))
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    mig.migrate_credentials_to_keychain(confirm=True)

    res = mig.rollback_credentials_to_keychain(confirm=True)

    assert not res.ok and res.failed == ["SH2_BETA"]
    assert res.rollback_available and mig.rollback_snapshot_path().exists()
    assert (home / ".env").read_bytes() == _ENV_BODY.encode(), ".env came back regardless"


def test_the_snapshot_is_0600_and_holds_the_pre_migration_bytes(keychain_on, home: Path) -> None:
    mig.migrate_credentials_to_keychain(confirm=True)
    snap = mig.rollback_snapshot_path()
    assert snap.read_bytes() == _ENV_BODY.encode()
    assert _mode(snap) == 0o600, "a second plaintext copy of every secret — same floor"


# ── the config gate is the persisted request ─────────────────────────────────


def test_the_config_gate_turns_the_keychain_on_without_the_env_var(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_keyring(monkeypatch)
    assert cred.requested_credential_backend() == "dotenv", "off by default"
    (home / "config.json").write_text(json.dumps({"security": {"credential_keychain": True}}))
    assert cred.requested_credential_backend() == "keychain"
    assert cred.credential_backend() == "keychain"


def test_an_explicit_env_dotenv_overrides_the_config_gate(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery lever: a machine whose secret service broke can be forced back."""
    _stub_keyring(monkeypatch)
    (home / "config.json").write_text(json.dumps({"security": {"credential_keychain": True}}))
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "dotenv")
    assert cred.requested_credential_backend() == "dotenv"


@pytest.mark.parametrize("raw", ["yes", "false", "true", 1, 0, None, [], {}])
def test_only_a_real_json_true_opens_the_gate(
    home: Path, monkeypatch: pytest.MonkeyPatch, raw: object
) -> None:
    """🪤 `bool("false")` IS TRUE, so a truthiness read of this field would let a hand-edited
    `"credential_keychain": "false"` turn the keychain on. Only a JSON `true` opts in.

    **Two layers hold this, and falsification showed which one does the work.** Mutating the
    loader's `is True` back to `bool(...)` left every case here GREEN: `_validate_config_data`
    runs first, and `config/schema.py`'s `SCHEMA_REGISTRY` — GENERATED from the `AppConfig`
    dataclass, so this field got a `boolean` entry for free — strips a type mismatch and lets
    the field fall back to its default ("type mismatch at 'security.credential_keychain':
    expected boolean, got string … using default"). The `is True` read is therefore the SECOND
    line of defence, for the path where the schema is bypassed (a `SecurityConfig` built
    directly, or a future loader that skips validation). This test pins the BEHAVIOUR, which is
    what a user depends on; the docstring names the real enforcer so the next reader does not
    mistake the loader line for it."""
    _stub_keyring(monkeypatch)
    (home / "config.json").write_text(json.dumps({"security": {"credential_keychain": raw}}))
    assert loader.AppConfig.load().security.credential_keychain is False
    assert cred.requested_credential_backend() == "dotenv"


def test_the_gate_round_trips_through_load_and_to_dict(home: Path) -> None:
    cfg = loader.AppConfig.load()
    cfg.security.credential_keychain = True
    (home / "config.json").write_text(json.dumps(cfg.to_dict()))
    assert loader.AppConfig.load().security.credential_keychain is True


# ── the export exclusion still holds ─────────────────────────────────────────


def test_the_rollback_snapshot_is_claimed_and_excluded_from_every_export() -> None:
    """The snapshot is a second plaintext copy of every credential. It must never leave.

    Asserted through the INVENTORY, which is what ``portability.EXPORT_EXCLUDE`` projects —
    so this pins the mechanism that excludes it, not a hand-copied literal that could drift.
    """
    from personalclaw.durability import inventory as inv

    entry = next(e for e in inv.INVENTORY if e.path == mig.ROLLBACK_FILENAME)
    assert entry.secret, "secret=True is what puts it in EXPORT_EXCLUDE"
    assert mig.ROLLBACK_FILENAME in inv.secret_paths()
    assert entry.path not in [e.path for e in inv.export_entries()]


def test_the_export_exclude_set_covers_both_credential_files() -> None:
    from pathlib import PurePosixPath

    from personalclaw.portability import EXPORT_EXCLUDE, _is_excluded

    assert ".env" in EXPORT_EXCLUDE
    assert _is_excluded(PurePosixPath(mig.ROLLBACK_FILENAME)), "the snapshot too"
    # Vacuity floor: the predicate must be able to say NO.
    assert not _is_excluded(PurePosixPath("config.json"))


def test_a_migrated_export_carries_no_secret_at_all(
    keychain_on, home: Path, tmp_path: Path
) -> None:
    """The end-to-end clause: migrating must not open a new way for a secret to be exported."""
    import io
    import zipfile

    from personalclaw import portability

    mig.migrate_credentials_to_keychain(confirm=True)
    assert mig.rollback_snapshot_path().exists(), "the premise: a snapshot exists to leak"
    blob, _manifest = portability.create_export_zip()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
    assert names, "vacuity floor: an empty archive would pass every assertion below"
    assert not [n for n in names if n.endswith(".env")], names
    assert not [n for n in names if "pre-keychain" in n], names


# ── the surface a user actually touches ──────────────────────────────────────


def test_status_reports_the_resolved_backend_not_the_request(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(home)
    monkeypatch.setattr(sys, "meta_path", [_ImportBlocker(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    st = mig.credential_migration_status()
    assert st["requested"] == "keychain" and st["backend"] == "dotenv"
    assert st["blocked"] is True, "a box with no secret service must not read as ready"
    assert st["pending_keys"] == list(_KEYS)


def test_status_never_carries_a_secret_value(keychain_on, home: Path) -> None:
    blob = json.dumps(mig.credential_migration_status())
    assert "alpha-secret" not in blob and "beta-secret" not in blob
    assert "SH2_ALPHA" in blob, "names yes — the vacuity floor for the assertion above"
    mig.migrate_credentials_to_keychain(confirm=True)
    res = json.dumps(mig.migrate_credentials_to_keychain(confirm=True).to_dict())
    assert "alpha-secret" not in res


def test_the_gate_has_a_write_path_and_the_patch_allowlist_declares_it() -> None:
    """The config round-trip contract's WRITE point, pinned here because nothing else does.

    🪤 MEASURED: deleting `"security.credential_keychain"` from `_EDITABLE_CONFIG` left
    `tests/test_config_roundtrip.py` fully GREEN (17 passed). That file covers the dataclass,
    `load()` and `to_dict()`; the PATCH allowlist is the point it does not reach, and a field
    absent from it is silently dropped by the handler — the Settings toggle would report
    success and change nothing. So the write path gets its own rail.
    """
    from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    key = "security.credential_keychain"
    spec = _EDITABLE_CONFIG.get(key)
    assert spec is not None, "the Settings toggle PATCHes this path — it must be allowlisted"
    assert spec == {"type": "bool"}
    assert coerce_edit_value(key, True, spec) is True
    assert coerce_edit_value(key, False, spec) is False
    # Vacuity floor: the spec must be able to REFUSE. A string that coerced to True here
    # would put the fail-open the loader rejects back at the write boundary.
    with pytest.raises(ConfigValueError):
        coerce_edit_value(key, "keychain", spec)
