"""The credential backend selector: keychain vs `.env` 0600 (SECURITY-HARDENING SH-1 / C1).

Two stores sit behind one API. The properties that matter are not "does keyring work" —
they are the ones a credential store cannot get wrong:

* **Reads are backend-transparent.** `get_credential(key)` takes a key and nothing else;
  the same call answers from the keychain or from `.env` and the caller cannot tell.
* **The fallback is fail-CLOSED.** No keyring → `.env` at mode 0600 and a doctor warning.
  Never a new plaintext file in a new location, never looser permissions, never a silent
  discard. `keyring`'s own `null` backend DISCARDS what it is handed and its `fail`
  backend raises; both are refused rather than adopted.
* **Doctor reports the OUTCOME.** An install that asks for a keychain it does not have
  must read as `.env`, not as "keychain". A line that echoed the *request* is the exact
  defect the done_when clause exists to prevent, so it is asserted directly.

⚠️  `keyring` is an OPTIONAL extra and is NOT in `[dev]`/`[test]`: CI does not install it.
Every test here therefore either BLOCKS the import through `sys.meta_path` or installs a
stub module in `sys.modules`. Nothing in this file touches a real OS keychain, and nothing
passes only because the developer's machine happens to have `keyring` installed — the
no-keyring path is proven by blocking the import, not by the absence of a package.
"""

from __future__ import annotations

import ast
import inspect
import json
import stat
import sys
import types
from pathlib import Path

import pytest

from personalclaw.config import loader
from personalclaw.config.loader import (
    CREDENTIAL_BACKEND_ENV,
    AppConfig,
    credential_backend,
    credential_backend_warning,
    get_credential,
    keychain_available,
    requested_credential_backend,
    save_credential,
)

_KEY = "SH1_TEST_TOKEN"
_OTHER = "SH1_TEST_OTHER"


# ── keyring doubles ──────────────────────────────────────────────────────────


class _ImportBlocker:
    """A `sys.meta_path` finder that makes `import keyring` fail, whatever is installed.

    This is how the headless path is proven. Uninstalling a package (or trusting that CI
    lacks it) proves nothing about the code — a finder that refuses the name reproduces a
    keyring-less box deterministically on any machine.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def find_module(self, fullname, path=None):  # pragma: no cover - legacy hook
        return None

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name or fullname.startswith(self.name + "."):
            raise ImportError(f"blocked by the test: {fullname}")
        return None


@pytest.fixture
def no_keyring(monkeypatch: pytest.MonkeyPatch):
    """Make `import keyring` raise ImportError for the duration of the test."""
    blocker = _ImportBlocker("keyring")
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    assert not keychain_available(), "the blocker must make keyring unimportable"
    return blocker


def _backend_class(module_name: str, *, on_set=None, on_get=None):
    """Build a keyring backend class whose `__module__` decides how loader classifies it."""

    class _Backend:
        pass

    _Backend.__module__ = module_name
    _Backend.__qualname__ = "Keyring"
    _Backend.__name__ = "Keyring"
    return _Backend


def _install_stub_keyring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend_module: str = "keyring.backends.macOS",
    store: dict[str, str] | None = None,
    set_raises: bool = False,
) -> dict[str, str]:
    """Install a fake `keyring` module in `sys.modules`; return its backing store.

    `backend_module` drives the usability classification (`keyring.backends.fail` /
    `keyring.backends.null` must be refused). `set_raises` simulates a locked or broken
    secret service so the fail-closed write path can be exercised.
    """
    values: dict[str, str] = {} if store is None else store
    module = types.ModuleType("keyring")

    def get_password(service: str, key: str):
        return values.get(f"{service}\x00{key}")

    def set_password(service: str, key: str, value: str) -> None:
        if set_raises:
            raise RuntimeError("secret service is locked")
        values[f"{service}\x00{key}"] = value

    module.get_keyring = lambda: _backend_class(backend_module)()  # type: ignore[attr-defined]
    module.get_password = get_password  # type: ignore[attr-defined]
    module.set_password = set_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    return values


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated credential home. Never the real one — these tests write secrets."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    # save_credential mirrors into os.environ; registering the keys with monkeypatch
    # first means teardown restores the process environment.
    for key in (_KEY, _OTHER):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(CREDENTIAL_BACKEND_ENV, raising=False)
    return cfg


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── the selector ─────────────────────────────────────────────────────────────


def test_the_default_backend_is_dotenv_and_warns_about_nothing(home: Path) -> None:
    assert requested_credential_backend() == "dotenv"
    assert credential_backend() == "dotenv"
    assert credential_backend_warning() == ""


@pytest.mark.parametrize("request_value", ["vault", "KEYCHAIN ", "1", "true"])
def test_an_unreadable_backend_request_resolves_to_dotenv(
    home: Path, monkeypatch: pytest.MonkeyPatch, request_value: str
) -> None:
    """Fail-closed on the REQUEST too: only a clean `keychain` turns the keychain on.

    `"KEYCHAIN "` is included on purpose — it is accepted (case/whitespace normalised),
    unlike `"vault"`/`"1"`/`"true"`, which are refused. An unparsable request must never
    be read as "use the fancier store".
    """
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, request_value)
    expected = "keychain" if request_value.strip().lower() == "keychain" else "dotenv"
    assert requested_credential_backend() == expected


def test_keychain_requested_without_keyring_resolves_to_dotenv_with_a_warning(
    home: Path, no_keyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert requested_credential_backend() == "keychain", "the request stands as intent"
    assert credential_backend() == "dotenv", "the OUTCOME is the .env fallback"
    warning = credential_backend_warning()
    assert warning, "a silent fallback is the defect — doctor must be told"
    assert "0600" in warning and "keychain" in warning


@pytest.mark.parametrize("bad", ["keyring.backends.fail", "keyring.backends.null"])
def test_a_fail_or_null_keyring_backend_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """`fail` raises on every call; `null` SILENTLY DISCARDS. Adopting either is fail-open.

    The `null` half is the dangerous one: `set_password` returns cleanly and the secret is
    gone. So this asserts the credential actually lands in `.env`, not merely that the
    selector said "dotenv".
    """
    values = _install_stub_keyring(monkeypatch, backend_module=bad)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    assert keychain_available() is False
    assert credential_backend() == "dotenv"
    assert credential_backend_warning()

    save_credential(_KEY, "landed-in-env")
    assert values == {}, "nothing may be handed to a fail/null backend"
    # Mode BEFORE any read: `_dotenv_credentials()` repairs loose permissions, so a
    # read placed first would make this assertion measure the repair, not the write.
    assert _mode(loader.env_path()) == 0o600
    assert get_credential(_KEY) == "landed-in-env"


def test_the_keychain_becomes_active_when_it_is_requested_and_present(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert keychain_available() is True
    assert credential_backend() == "keychain"
    assert credential_backend_warning() == "", "nothing fell back, so nothing to warn about"


def test_a_present_keychain_is_not_used_until_it_is_requested(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Availability alone must not move where secrets are written.

    The lifecycle is opt-in first (SH-2 owns the gate + the consent-triggered migration).
    A machine that merely HAS a keychain keeps writing `.env` until asked, so no install
    silently ends up with half its secrets in each store.
    """
    values = _install_stub_keyring(monkeypatch)
    assert keychain_available() is True
    assert credential_backend() == "dotenv"

    save_credential(_KEY, "still-dotenv")
    assert values == {}
    assert loader.env_path().exists()


# ── fail-closed write path ───────────────────────────────────────────────────


def test_the_headless_fallback_writes_env_at_0600_and_creates_nothing_else(
    home: Path, no_keyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole "never plaintext-elsewhere" clause, asserted as a directory listing.

    A fallback that invented `credentials.txt`, or wrote `.env` at 0644, would satisfy a
    "the value round-trips" test and still be the bug. So: exact mode, and `.env` is the
    ONLY thing the write created.
    """
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    save_credential(_KEY, "s3cret-value")

    env = loader.env_path()
    assert env.exists()
    assert _mode(env) == 0o600, f".env must be 0600, found {oct(_mode(env))}"
    assert {p.name for p in home.iterdir()} == {".env"}
    assert f"{_KEY}=s3cret-value" in env.read_text()
    assert get_credential(_KEY) == "s3cret-value"


def test_a_keychain_write_failure_falls_back_to_env_0600(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked secret service must not lose the credential — or downgrade where it lands."""
    values = _install_stub_keyring(monkeypatch, set_raises=True)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert credential_backend() == "keychain"

    save_credential(_KEY, "rescued")

    assert values == {}
    assert _mode(loader.env_path()) == 0o600
    assert get_credential(_KEY) == "rescued"


def test_the_keychain_backend_stores_secrets_in_the_keychain_and_not_in_env(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    save_credential(_KEY, "keychain-value")

    assert values[f"personalclaw\x00{_KEY}"] == "keychain-value"
    assert not loader.env_path().exists(), "a keychain write must not also spill to .env"
    index = json.loads(values["personalclaw\x00__personalclaw_key_index__"])
    assert index == [_KEY], "the keychain must stay enumerable for load_credentials()"


def test_the_key_index_accumulates_and_stays_sorted(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    save_credential(_OTHER, "b")
    save_credential(_KEY, "a")
    save_credential(_KEY, "a2")  # a re-save must not duplicate the index entry

    index = json.loads(values["personalclaw\x00__personalclaw_key_index__"])
    assert index == sorted([_KEY, _OTHER])
    assert get_credential(_KEY) == "a2"


# ── backend transparency ─────────────────────────────────────────────────────


def test_the_read_api_gives_the_caller_no_way_to_name_a_backend() -> None:
    """The contract is structural: one key in, one value out.

    A `backend=` parameter would make every call site a place to get the choice wrong,
    which is what "reads are backend-transparent" forbids.
    """
    params = list(inspect.signature(get_credential).parameters)
    assert params == ["key"]
    assert list(inspect.signature(save_credential).parameters) == ["key", "value"]


def test_reads_are_transparent_across_both_stores_in_one_process(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One key in the keychain, one in `.env`, one identical call shape for both."""
    _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    save_credential(_KEY, "from-keychain")

    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "dotenv")
    save_credential(_OTHER, "from-dotenv")

    assert get_credential(_KEY) == "from-keychain"
    assert get_credential(_OTHER) == "from-dotenv"
    assert get_credential("SH1_NEVER_STORED") == ""

    # …and the union survives the backend being switched back, which is why reads
    # consult both stores regardless of which one is active: flipping the env var
    # must not make an already-stored secret disappear.
    assert credential_backend() == "dotenv"
    assert get_credential(_KEY) == "from-keychain"

    # …and in the direction that actually happens in the field: the keychain is
    # ACTIVE and the key still lives only in `.env` (an install that opted in before
    # SH-2's migration moved anything). A read that consulted only the active backend
    # would return "" here and read as a lost credential.
    #
    # 🔴 This assertion exists because falsification found the gap: a mutation making
    # `get_credential` active-backend-EXCLUSIVE passed the rest of this file untouched.
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    assert credential_backend() == "keychain"
    assert get_credential(_OTHER) == "from-dotenv"


def test_load_credentials_unions_both_backends_with_the_keychain_winning(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bulk read must see keychain-held keys too, or a migrated install loses them."""
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "dotenv")
    save_credential(_OTHER, "dotenv-only")
    save_credential(_KEY, "stale-dotenv-copy")

    _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    save_credential(_KEY, "fresh-keychain-copy")

    creds = AppConfig.load().load_credentials()
    assert creds[_OTHER] == "dotenv-only"
    assert creds[_KEY] == "fresh-keychain-copy", "a partly-migrated key resolves to keychain"


def test_the_app_setup_context_reads_through_the_shared_chokepoint() -> None:
    """`app_cli` used to parse `.env` itself — a second read path that keychain-stored
    secrets would have been invisible to. It must now hold the loader's function."""
    import personalclaw.app_cli as app_cli

    src = Path(inspect.getsourcefile(app_cli) or "").read_text()
    assert "get_credential=get_credential" in src
    assert "from personalclaw.config.loader import get_credential" in src
    assert "def _get_credential" not in src, "the ad-hoc .env parser must be gone, not shadowed"


# ── doctor reports the ACTIVE backend ────────────────────────────────────────


def test_doctor_reports_the_env_fallback_and_not_the_request(
    home: Path, no_keyring, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from personalclaw.cli_doctor import _doctor_credentials

    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    issues = _doctor_credentials()
    out = capsys.readouterr().out

    assert ".env 0600" in out
    assert "OS keychain" not in out, "reporting the request instead of the outcome is the defect"
    assert "no usable OS keyring backend" in out
    assert issues == ["credential backend: keychain requested but unavailable"]


def test_doctor_reports_the_keychain_when_the_keychain_is_the_one_answering(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from personalclaw.cli_doctor import _doctor_credentials

    _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    issues = _doctor_credentials()
    out = capsys.readouterr().out

    assert "OS keychain" in out
    assert ".env" not in out
    assert issues == []


def test_the_doctor_actually_calls_the_credential_line() -> None:
    """A reported backend nobody prints is an inert control. Assert the CALL SITE."""
    import personalclaw.cli_doctor as cd

    tree = ast.parse(Path(inspect.getsourcefile(cd) or "").read_text())
    doctor = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_doctor"
    )
    called = {
        n.func.id
        for n in ast.walk(doctor)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_doctor_credentials" in called


# ── the doctor probe (the dashboard half) ────────────────────────────────────


def _credential_probe():
    from personalclaw.resilience import doctor as rd

    return {p.id: p for p in rd.all_probes()}["security.credential_backend"]


def test_the_credential_probe_is_registered_as_a_capability_probe() -> None:
    from personalclaw.resilience.doctor import Tier

    probe = _credential_probe()
    assert (
        probe.tier is Tier.CAPABILITY
    ), "a credential-store report must never gate the core ladder"
    assert probe.capability == "security"


@pytest.mark.asyncio
async def test_the_probe_reports_the_resolved_backend_not_the_request(
    home: Path, no_keyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.resilience.doctor import DoctorContext

    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")
    save_credential(_KEY, "in-the-env-file")

    result = await _credential_probe().run(DoctorContext(home=home))

    assert result.ok is False, "asking for a keychain and not getting one is actionable"
    assert result.evidence["backend"] == "dotenv"
    assert result.evidence["requested"] == "keychain"
    assert result.evidence["keychain_available"] is False
    assert result.evidence["env_mode"] == "0600"


@pytest.mark.asyncio
async def test_the_probe_is_ok_and_names_the_keychain_when_it_is_active(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.resilience.doctor import DoctorContext

    _install_stub_keyring(monkeypatch)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "keychain")

    result = await _credential_probe().run(DoctorContext(home=home))

    assert result.ok is True
    assert result.evidence["backend"] == "keychain"
    assert "keychain" in result.detail


@pytest.mark.asyncio
async def test_the_probe_flags_a_group_readable_credential_file(home: Path, no_keyring) -> None:
    from personalclaw.resilience.doctor import DoctorContext

    save_credential(_KEY, "v")
    loader.env_path().chmod(0o644)

    result = await _credential_probe().run(DoctorContext(home=home))

    assert result.ok is False
    assert "0644" in result.detail


@pytest.mark.asyncio
async def test_no_secret_value_reaches_the_probe_result(home: Path, no_keyring) -> None:
    """A health probe reports names, modes and states — never a value."""
    from personalclaw.resilience.doctor import DoctorContext

    save_credential(_KEY, "unmistakable-secret-9c3f")
    result = await _credential_probe().run(DoctorContext(home=home))

    assert "unmistakable-secret-9c3f" not in json.dumps(result.to_dict())
