"""Subscription-credential model providers (LMMV-6, LOCAL-MODEL-MANAGER-V2 §8).

A model-provider app whose vendor bills by SUBSCRIPTION has no API key to paste: the user
already signed the vendor's own agent CLI in, and PersonalClaw resolves the bearer token
from the store that CLI owns. Reading someone else's credential store raises the bar, so
this suite is organised around the four properties that make it safe rather than around
the functions:

  1. **Precedence** — the resolver sits at ONE fixed place in the documented credential
     order (``entry.credential`` → ``options.api_key`` → subscription source →
     ``spec.api_key_env`` → anon placeholder). A test per hop, because a resolver that
     silently outranked ``entry.credential`` would override an explicit user choice, and
     one that short-circuited would strand an app that has both a source and a key env.
  2. **Fail soft and typed** — not-signed-in, missing, malformed/half-written, tokenless
     and expired stores are all ``logged_in=False`` with a human reason. A parse error must
     never read as authenticated, and nothing raises.
  3. **Read-only** — proven twice: structurally (this module contains no write-shaped call
     at all) and behaviourally (bytes, mode and mtime are byte-identical after a resolve).
  4. **No leak** — a resolved secret appears in no repr, no reason, no availability line
     and no log record.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest

import personalclaw.sdk.model  # noqa: F401 — sdk.model must import before provider_helpers
from personalclaw.llm.capabilities import Capability
from personalclaw.llm.credentials import Credential
from personalclaw.llm.registry import ProviderEntry
from personalclaw.llm.subscription_credentials import (
    SubscriptionAuth,
    resolve_subscription_credential,
    subscription_source_status,
)

# ``SubscriptionSource`` + ``register_subscription_source`` are imported through the SDK
# facade on purpose: that is the ONLY path an app may use, so exercising it here is what
# proves the app-facing surface works (and keeps it out of the inert-surface census).
from personalclaw.sdk.provider_helpers import (
    BrandedProviderSpec,
    SubscriptionSource,
    register_branded_app,
    register_subscription_source,
    spec_credential_source,
)

SECRET = "sk-subscription-TOKEN-do-not-leak-9f3a"


class _FakeAsyncAnthropic:
    """Stand-in for ``anthropic.AsyncAnthropic``: records the resolved key, builds no
    HTTP/SSL client (whose eager trust-store setup fails on some runners)."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.messages = types.SimpleNamespace()

    async def close(self) -> None:  # pragma: no cover - lifecycle no-op
        pass


@pytest.fixture(autouse=True)
def _fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _FakeAsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)


@pytest.fixture(autouse=True)
def _isolated_source_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets an empty source registry — process-global state that would
    otherwise leak a fake vendor row into an unrelated test."""
    from personalclaw.llm import subscription_credentials as subcreds

    monkeypatch.setattr(subcreds, "_SOURCES", {})


def _store(tmp_path: Path, payload: object, *, name: str = ".credentials.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def _source(path: Path, **overrides: object) -> SubscriptionSource:
    kwargs: dict = {
        "id": "example-cli",
        "login_hint": "sign in with `example login` first",
        "credential_files": (str(path),),
        "token_path": ("oauth", "accessToken"),
    }
    kwargs.update(overrides)
    src = SubscriptionSource(**kwargs)  # type: ignore[arg-type]
    register_subscription_source(src)
    return src


def _signed_in(tmp_path: Path, **overrides: object) -> SubscriptionSource:
    return _source(_store(tmp_path, {"oauth": {"accessToken": SECRET}}), **overrides)


def _spec(**overrides: object) -> BrandedProviderSpec:
    kwargs: dict = {
        "type": f"subtest-{len(overrides)}-{overrides.get('type', 'x')}",
        "protocol": "anthropic",
        "default_base_url": "https://api.example.invalid",
        "default_model": "example-large",
        "capabilities": frozenset({Capability.CHAT}),
        "credential_source": "example-cli",
    }
    kwargs.update(overrides)
    return BrandedProviderSpec(**kwargs)  # type: ignore[arg-type]


# ── 1. Precedence: one test per hop of the documented order ───────────────────────────


def test_entry_credential_outranks_the_subscription_source(tmp_path: Path) -> None:
    """The FIRST hop. A user who wired an explicit credential-store descriptor chose it;
    a resolver that quietly outranked it would override that choice with a token from
    another tool's login."""
    _signed_in(tmp_path)
    spec = _spec(type="hop1")
    factory, _, _ = register_branded_app(spec)

    class _Store:
        def resolve(self, name: str) -> Credential:
            return Credential(
                name=name, kind="api_key", secret="EXPLICIT-ENTRY-CRED", source="file"
            )

    entry = ProviderEntry(name="H1", type=spec.type, model="m", credential="my-cred")
    prov = factory(entry=entry, credential_store=_Store())
    assert prov._client.api_key == "EXPLICIT-ENTRY-CRED"  # noqa: SLF001


def test_options_api_key_outranks_the_subscription_source(tmp_path: Path) -> None:
    """The SECOND hop. The Add-Provider flow persists ``options.api_key``; a per-instance
    key the user typed must beat an ambient CLI login."""
    _signed_in(tmp_path)
    spec = _spec(type="hop2")
    factory, _, _ = register_branded_app(spec)
    entry = ProviderEntry(
        name="H2", type=spec.type, model="m", options={"api_key": "PER-INSTANCE-KEY"}
    )
    prov = factory(entry=entry)
    assert prov._client.api_key == "PER-INSTANCE-KEY"  # noqa: SLF001


def test_subscription_source_outranks_the_spec_api_key_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The THIRD hop. A signed-in subscription is more specific than a global env key that
    was probably set for a different provider (the 'wrong key → 401' class of bug)."""
    _signed_in(tmp_path)
    spec = _spec(type="hop3", api_key_env="SOME_GLOBAL_KEY")
    factory, _, _ = register_branded_app(spec)
    monkeypatch.setenv("SOME_GLOBAL_KEY", "GLOBAL-WRONG-KEY")
    prov = factory(entry=ProviderEntry(name="H3", type=spec.type, model="m"))
    assert prov._client.api_key == SECRET  # noqa: SLF001


def test_a_source_that_is_not_signed_in_falls_through_to_the_api_key_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The FOURTH hop, and the reason the order is independent ``if``s rather than an
    ``elif`` chain: a failed subscription probe must not short-circuit the env key an app
    also declares."""
    _source(tmp_path / "absent.json")
    spec = _spec(type="hop4", api_key_env="MY_ENV_KEY")
    factory, _, _ = register_branded_app(spec)
    monkeypatch.setenv("MY_ENV_KEY", "ENV-KEY-USED")
    prov = factory(entry=ProviderEntry(name="H4", type=spec.type, model="m"))
    assert prov._client.api_key == "ENV-KEY-USED"  # noqa: SLF001


def test_a_source_that_is_not_signed_in_falls_all_the_way_to_the_anon_placeholder(
    tmp_path: Path,
) -> None:
    """The FIFTH hop. Construction still succeeds (the protocol client demands a populated
    secret) — the failure is reported by ``availability()``, not by a crash at build time."""
    _source(tmp_path / "absent.json")
    spec = _spec(type="hop5")
    factory, _, _ = register_branded_app(spec)
    prov = factory(entry=ProviderEntry(name="H5", type=spec.type, model="m"))
    assert prov._client.api_key == "unused"  # noqa: SLF001


def test_the_whole_five_hop_order_holds_as_one_descending_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The five per-hop tests above pin ADJACENT PAIRS, and that is measurably not enough.

    Each one supplies only the two sources it compares, so a hop it leaves unset cannot
    contradict it. Swapping hops 1 and 2 in ``_factory`` (letting ``options.api_key``
    overwrite an explicit ``entry.credential``) keeps this whole module green: the hop-1 test
    sets no ``options.api_key`` and the hop-2 test sets no ``entry.credential``, so neither
    observes the swap. That was verified by mutation, not assumed.

    So this test supplies ALL FIVE at once and knocks them out one at a time, asserting the
    winner changes in exactly the documented sequence. Every rung keeps the lower sources
    populated, which is what makes an adjacent swap anywhere in the chain visible here.
    """
    store = _store(tmp_path, {"oauth": {"accessToken": SECRET}})
    _source(store)
    spec = _spec(type="ladder", api_key_env="LADDER_ENV_KEY")
    factory, _, _ = register_branded_app(spec)
    monkeypatch.setenv("LADDER_ENV_KEY", "ENV-KEY")

    class _Store:
        def resolve(self, name: str) -> Credential:
            return Credential(name=name, kind="api_key", secret="ENTRY-CRED", source="file")

    def _winner(*, with_entry_cred: bool, with_opt_key: bool) -> str:
        entry = ProviderEntry(
            name="LADDER",
            type=spec.type,
            model="m",
            credential="my-cred" if with_entry_cred else None,
            options={"api_key": "OPT-KEY"} if with_opt_key else {},
        )
        return factory(entry=entry, credential_store=_Store())._client.api_key  # noqa: SLF001

    # Rung 1: every one of the five is available → the explicit entry credential wins.
    assert _winner(with_entry_cred=True, with_opt_key=True) == "ENTRY-CRED"
    # Rung 2: drop ONLY the entry credential; the per-instance key, the signed-in
    # subscription and the env key all remain → the per-instance key wins.
    assert _winner(with_entry_cred=False, with_opt_key=True) == "OPT-KEY"
    # Rung 3: drop the per-instance key too; source + env remain → the subscription wins.
    assert _winner(with_entry_cred=False, with_opt_key=False) == SECRET
    # Rung 4: sign the CLI out (the store this source declares disappears, exactly as it
    # looks before `example login`); the env key remains → the env key wins.
    store.unlink()
    assert _winner(with_entry_cred=False, with_opt_key=False) == "ENV-KEY"
    # Rung 5: remove the env key as well → the anon placeholder, and still no crash.
    monkeypatch.delenv("LADDER_ENV_KEY")
    assert _winner(with_entry_cred=False, with_opt_key=False) == "unused"


def test_the_five_hop_ladder_has_five_DISTINCT_rungs(tmp_path: Path) -> None:
    """Vacuity floor for the ladder above: it can only prove an ORDER if the five hops
    yield five different secrets. If two rungs shared a value, a swap between them would
    still read as a pass, which is the very failure mode the ladder exists to catch."""
    rungs = ["ENTRY-CRED", "OPT-KEY", SECRET, "ENV-KEY", "unused"]
    assert len(set(rungs)) == 5


def test_a_spec_with_no_credential_source_never_consults_the_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vacuity guard for the whole hop: the resolver is not merely outranked for an
    ordinary provider app, it is never called at all."""
    _signed_in(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "personalclaw.sdk.provider_helpers.resolve_subscription_credential",
        lambda source: calls.append(source) or SubscriptionAuth(source=source, logged_in=False),
    )
    spec = _spec(type="nosource", credential_source="")
    factory, _, _ = register_branded_app(spec)
    factory(entry=ProviderEntry(name="NS", type=spec.type, model="m"))
    assert calls == []


def test_both_api_key_spellings_are_stripped_from_extra_options(tmp_path: Path) -> None:
    """An entry carrying BOTH spellings must leak neither into the SDK call kwargs. The
    short-circuit ``or`` that used to pop them left ``apiKey`` behind → 'unexpected keyword
    argument' at the wire, the same class of bug the base_url/endpoint pop was fixed for."""
    _signed_in(tmp_path)
    spec = _spec(type="bothspellings")
    factory, _, _ = register_branded_app(spec)
    entry = ProviderEntry(
        name="BS",
        type=spec.type,
        model="m",
        options={"api_key": "snake", "apiKey": "camel", "temperature": 0.25},
    )
    prov = factory(entry=entry)
    extra = getattr(prov, "_extra_options", {})
    assert "api_key" not in extra and "apiKey" not in extra
    assert extra.get("temperature") == 0.25
    assert prov._client.api_key == "snake"  # noqa: SLF001 — snake_case wins, as before


# ── 2. Fail soft and typed: nothing raises, and a parse error is never "signed in" ─────


def test_a_signed_in_source_resolves_the_token(tmp_path: Path) -> None:
    _signed_in(tmp_path)
    auth = resolve_subscription_credential("example-cli")
    assert auth.logged_in is True and auth.secret == SECRET and auth.reason == ""
    assert subscription_source_status("example-cli") == (True, "")


def test_an_unregistered_source_is_not_signed_in_with_a_reason() -> None:
    available, reason = subscription_source_status("never-registered")
    assert available is False
    assert "never-registered" in reason and "not installed" in reason


def test_an_empty_source_id_is_not_signed_in() -> None:
    auth = resolve_subscription_credential("")
    assert auth.logged_in is False and auth.secret == "" and auth.reason


def test_an_absent_credential_file_is_not_signed_in_with_the_apps_login_hint(
    tmp_path: Path,
) -> None:
    _source(tmp_path / "nothing-here.json")
    available, reason = subscription_source_status("example-cli")
    assert available is False
    assert "example login" in reason  # the APP's own login verb, not core's invention


@pytest.mark.parametrize(
    "raw",
    [
        '{"oauth": {"accessToken": "sk-half',  # truncated mid-write
        "",  # zero-length (created but not yet written)
        "not json at all",
        "[]",  # valid JSON, wrong shape
        '{"oauth": null}',
    ],
    ids=["truncated", "empty", "garbage", "wrong-shape", "null-branch"],
)
def test_a_malformed_or_half_written_store_is_NOT_authenticated(tmp_path: Path, raw: str) -> None:
    """The single most dangerous failure mode: a parse error that read as 'signed in' would
    hand the wire an empty bearer token and report success. Every one of these is
    not-signed-in with a reason, and none of them raises."""
    _source(_store(tmp_path, raw))
    auth = resolve_subscription_credential("example-cli")
    assert auth.logged_in is False
    assert auth.secret == ""
    assert auth.reason
    assert subscription_source_status("example-cli")[0] is False


@pytest.mark.parametrize(
    "token", [None, "", "   ", 12345, {"nested": "object"}, ["list"], True], ids=str
)
def test_a_missing_blank_or_non_string_token_is_not_signed_in(
    tmp_path: Path, token: object
) -> None:
    _source(_store(tmp_path, {"oauth": {"accessToken": token}}))
    auth = resolve_subscription_credential("example-cli")
    assert auth.logged_in is False and auth.secret == ""


def test_an_expired_sign_in_is_not_signed_in(tmp_path: Path) -> None:
    path = _store(
        tmp_path, {"oauth": {"accessToken": SECRET, "expiresAt": 1_000_000_000_000}}  # year 2001
    )
    _source(path, expires_at_path=("oauth", "expiresAt"), expires_at_unit="ms")
    available, reason = subscription_source_status("example-cli")
    assert available is False and "expired" in reason


def test_a_future_expiry_is_still_signed_in(tmp_path: Path) -> None:
    path = _store(
        tmp_path, {"oauth": {"accessToken": SECRET, "expiresAt": 99_999_999_999}}  # year 5138, secs
    )
    _source(path, expires_at_path=("oauth", "expiresAt"), expires_at_unit="s")
    assert resolve_subscription_credential("example-cli").logged_in is True


def test_an_unparseable_expiry_stamp_does_not_grey_out_a_working_source(tmp_path: Path) -> None:
    """Inventing an expiry from a stamp we cannot read would disable a provider that works.
    That judgement belongs to the vendor's endpoint (its own 401)."""
    path = _store(tmp_path, {"oauth": {"accessToken": SECRET, "expiresAt": "whenever"}})
    _source(path, expires_at_path=("oauth", "expiresAt"))
    assert resolve_subscription_credential("example-cli").logged_in is True


def test_candidate_paths_are_tried_in_order_and_the_first_usable_one_wins(
    tmp_path: Path,
) -> None:
    """A source may declare several layouts. A malformed FIRST candidate must not mask a
    good second one — but it also must not be reported as success."""
    bad = _store(tmp_path, "{broken", name="bad.json")
    good = _store(tmp_path, {"oauth": {"accessToken": SECRET}}, name="good.json")
    _source(bad, credential_files=(str(bad), str(good)))
    assert resolve_subscription_credential("example-cli").secret == SECRET


def test_a_broken_source_declaration_raises_at_registration_time() -> None:
    """An app that mis-declares its store has a bug, not a runtime condition. Failing loudly
    at import beats degrading into a mystery 'not signed in' months later."""
    for bad in (
        SubscriptionSource(id="", login_hint="h", credential_files=("f",), token_path=("t",)),
        SubscriptionSource(id="x", login_hint="", credential_files=("f",), token_path=("t",)),
        SubscriptionSource(id="x", login_hint="h", credential_files=(), token_path=("t",)),
        SubscriptionSource(id="x", login_hint="h", credential_files=("f",), token_path=()),
        SubscriptionSource(
            id="x",
            login_hint="h",
            credential_files=("f",),
            token_path=("t",),
            expires_at_unit="fortnights",
        ),
    ):
        with pytest.raises(ValueError):
            register_subscription_source(bad)


def test_a_credential_file_that_is_a_DIRECTORY_is_not_signed_in(tmp_path: Path) -> None:
    """``read_text`` on a directory raises OSError, not JSONDecodeError — the OSError arm
    has to catch it or a mis-declared path becomes a traceback in the extensions list."""
    (tmp_path / "as-a-dir").mkdir()
    _source(tmp_path / "as-a-dir")
    assert resolve_subscription_credential("example-cli").logged_in is False


def test_a_tilde_relative_path_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sources declare ``~/.example/.credentials.json``; without expansion every real
    declaration would silently miss. Pointed at tmp_path — never a real home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    home_store = tmp_path / ".example"
    home_store.mkdir()
    _store(home_store, {"oauth": {"accessToken": SECRET}})
    _source(Path("unused"), credential_files=("~/.example/.credentials.json",))
    assert resolve_subscription_credential("example-cli").secret == SECRET


# ── 3. Read-only: proven structurally AND behaviourally ───────────────────────────────

#: Names that mutate a path. Any of these appearing in the resolver module means it can
#: write to a store it does not own.
_WRITE_SHAPED = (
    "write_text",
    "write_bytes",
    "unlink",
    "rmdir",
    "mkdir",
    "touch",
    "replace",
    "rename",
    "chmod",
    "remove",
    "renames",
    "symlink_to",
    "hardlink_to",
    "truncate",
)

_MODULE = Path(__file__).resolve().parents[1] / "src/personalclaw/llm/subscription_credentials.py"


def _write_shaped_calls(path: Path) -> set[str]:
    """Every write-shaped call name in ``path`` — plus any ``open()``/``Path.open()`` in a
    non-read mode, which is how a write hides without naming itself."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _WRITE_SHAPED:
            found.add(name)
        if name == "open":
            mode = ""
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            if any(ch in mode for ch in "wxa+"):
                found.add(f"open({mode!r})")
    return found


def test_the_resolver_module_contains_no_write_shaped_call() -> None:
    """The structural half of read-only. Behaviour tests prove today's paths don't write;
    this proves no path CAN, including one added tomorrow."""
    assert _write_shaped_calls(_MODULE) == set()


def test_the_write_detector_is_not_vacuous() -> None:
    """A rail that matches nothing looks clean. Point the same detector at a module that
    legitimately DOES write (the credential store's own ``.env`` writer) and require a hit —
    otherwise the assertion above proves only that the detector is broken."""
    writer = _MODULE.parents[1] / "config/loader.py"
    assert writer.is_file()
    assert _write_shaped_calls(writer), "detector found no writes in a module that writes"


def test_resolving_leaves_the_foreign_store_byte_for_byte_untouched(tmp_path: Path) -> None:
    """The behavioural half. Someone else's credential file must come out of a resolve with
    identical bytes, mode and mtime — no refresh, no rewrite, no normalising re-save."""
    path = _store(tmp_path, {"oauth": {"accessToken": SECRET, "extra": "keep me"}})
    os.chmod(path, 0o600)
    before_bytes = path.read_bytes()
    before = path.stat()

    _source(path)
    assert resolve_subscription_credential("example-cli").logged_in is True

    after = path.stat()
    assert path.read_bytes() == before_bytes
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_mode == before.st_mode
    assert after.st_size == before.st_size
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]  # no sidecar/backup either


def test_an_EXPIRED_store_is_not_refreshed_or_rewritten(tmp_path: Path) -> None:
    """The tempting write. An expired token is where a 'helpful' implementation would call
    the vendor CLI to refresh and re-save — that is a write to another tool's store, so the
    contract is to report it and let the user re-run their own login."""
    path = _store(tmp_path, {"oauth": {"accessToken": SECRET, "expiresAt": 1_000_000_000_000}})
    _source(path, expires_at_path=("oauth", "expiresAt"))
    before_bytes, before = path.read_bytes(), path.stat()

    assert subscription_source_status("example-cli")[0] is False

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_a_malformed_store_is_not_repaired(tmp_path: Path) -> None:
    """The other tempting write: 'fixing' a half-written file. Same answer."""
    path = _store(tmp_path, '{"oauth": {"accessToken": "sk-half')
    _source(path)
    before_bytes, before = path.read_bytes(), path.stat()

    assert resolve_subscription_credential("example-cli").logged_in is False

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before.st_mtime_ns


# ── 4. No leak: not in a repr, a reason, an availability line, or a log record ─────────


def test_the_resolved_secret_is_absent_from_every_emitted_string(tmp_path: Path) -> None:
    _signed_in(tmp_path)
    auth = resolve_subscription_credential("example-cli")
    assert auth.secret == SECRET  # it IS resolved …
    for rendered in (repr(auth), str(auth), f"{auth}", auth.reason, format(auth)):
        assert SECRET not in rendered  # … and it appears in none of these


def test_an_expired_or_malformed_reason_carries_no_fragment_of_the_file(tmp_path: Path) -> None:
    """A reason is built from the source id, the declared path and the app's hint — never
    from file content. An exception message quoting the parser's input would leak a
    fragment of a credential, which is why the malformed arm reports a fixed sentence."""
    path = _store(tmp_path, '{"oauth": {"accessToken": "' + SECRET)
    _source(path)
    reason = subscription_source_status("example-cli")[1]
    assert reason
    assert SECRET not in reason
    for fragment in (SECRET[:12], SECRET[-12:], "accessToken"):
        assert fragment not in reason

    expired = _store(
        tmp_path, {"oauth": {"accessToken": SECRET, "expiresAt": 1}}, name="expired.json"
    )
    _source(expired, expires_at_path=("oauth", "expiresAt"))
    assert SECRET not in subscription_source_status("example-cli")[1]


def test_resolving_logs_nothing_that_contains_the_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Today the module logs nothing at all; assert on the OUTCOME so a debug line added
    later that interpolates the store's contents reds this test instead of shipping."""
    _signed_in(tmp_path)
    with caplog.at_level(logging.DEBUG):
        assert resolve_subscription_credential("example-cli").secret == SECRET
    blob = "\n".join([r.getMessage() for r in caplog.records] + [caplog.text])
    assert SECRET not in blob


def test_the_secret_field_is_declared_repr_false() -> None:
    """Structural companion to the assertion above: the redaction is a property of the
    dataclass field, not of one lucky code path, so flipping it back reds here."""
    import dataclasses

    secret_field = {f.name: f for f in dataclasses.fields(SubscriptionAuth)}["secret"]
    assert secret_field.repr is False


# ── The availability seam: providers/loader.py derives the probe ───────────────────────


def _ext(provider_type: str, *, implementation: str = "provider:create_provider"):
    from personalclaw.apps.manifest import AppManifest, ProviderConfig
    from personalclaw.providers.registry import RegisteredProvider

    manifest = AppManifest(
        name="example-subscription-models",
        version="1.0.0",
        displayName="Example Subscription Models",
        description="rides the example CLI's login",
    )
    return RegisteredProvider(
        name=manifest.name,
        manifest=manifest,
        provider_config=ProviderConfig(
            type="model", implementation=implementation, providerType=provider_type
        ),
    )


def test_load_availability_derives_a_probe_from_the_declared_credential_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """done_when's soft-failure surface: a not-signed-in source greys the bundle out in the
    extensions list WITH the app's reason, and the app writes no hook to get it."""
    from personalclaw.providers import loader

    _source(tmp_path / "absent.json")
    spec = _spec(type="availtest")
    register_branded_app(spec)
    assert spec_credential_source(spec.type) == "example-cli"

    monkeypatch.setattr(loader, "_load_ext_module", lambda ext, path: types.ModuleType("m"))
    probe = loader.load_availability(_ext(spec.type))
    assert probe is not None
    available, reason = probe()
    assert available is False
    assert "example login" in reason


def test_a_derived_probe_reports_available_once_the_cli_is_signed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.providers import loader

    _signed_in(tmp_path)
    spec = _spec(type="availtest-ok")
    register_branded_app(spec)
    monkeypatch.setattr(loader, "_load_ext_module", lambda ext, path: types.ModuleType("m"))
    probe = loader.load_availability(_ext(spec.type))
    assert probe is not None
    assert probe() == (True, "")


def test_an_explicit_availability_hook_still_wins_over_the_derived_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app that wrote a hook knows something extra about its own machine."""
    from personalclaw.providers import loader

    _signed_in(tmp_path)
    spec = _spec(type="availtest-explicit")
    register_branded_app(spec)
    module = types.ModuleType("m")
    hook = "the example binary is missing"
    module.availability = lambda: (False, hook)  # type: ignore[attr-defined]
    monkeypatch.setattr(loader, "_load_ext_module", lambda ext, path: module)
    probe = loader.load_availability(_ext(spec.type))
    assert probe is not None and probe() == (False, hook)


def test_a_provider_declaring_no_credential_source_gets_no_derived_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vacuity guard on the seam: the derived probe must not appear for ordinary apps, or
    every keyed provider would start greying itself out."""
    from personalclaw.providers import loader

    spec = _spec(type="availtest-none", credential_source="")
    register_branded_app(spec)
    monkeypatch.setattr(loader, "_load_ext_module", lambda ext, path: types.ModuleType("m"))
    assert loader.load_availability(_ext(spec.type)) is None
    # …and neither does one whose manifest declares no concrete provider type at all.
    assert loader.load_availability(_ext("")) is None


# ── Spec round-trip: a new field must survive save/load ───────────────────────────────


def test_credential_source_round_trips_through_json_and_keeps_the_spec_hashable() -> None:
    """The round-trip discipline that catches a field added to the dataclass but not to the
    serializer — the exact miss ``pricing`` was added to guard against."""
    spec = BrandedProviderSpec(
        type="acme-subscription",
        protocol="anthropic",
        default_base_url="https://api.acme.test",
        default_model="acme-large",
        credential_source="acme-cli",
    )
    restored = BrandedProviderSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
    assert restored == spec
    assert restored.credential_source == "acme-cli"
    assert restored.to_dict() == spec.to_dict()
    assert hash(spec) == hash(
        BrandedProviderSpec(
            type="acme-subscription",
            protocol="anthropic",
            default_base_url="https://api.acme.test",
            default_model="acme-large",
            credential_source="acme-cli",
        )
    )


def test_credential_source_defaults_empty_so_an_older_serialized_spec_still_loads() -> None:
    spec = BrandedProviderSpec(type="plain")
    assert spec.credential_source == ""
    payload = spec.to_dict()
    del payload["credential_source"]
    assert BrandedProviderSpec.from_dict(payload) == spec


def test_spec_credential_source_answers_for_a_named_instance_and_never_guesses() -> None:
    """Mirrors ``spec_pricing``: resolves a user-named INSTANCE of a type, and returns ``""``
    (never a source id) for an unknown provider."""
    from personalclaw.sdk import provider_helpers

    spec = BrandedProviderSpec(type="acme", credential_source="acme-cli")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(provider_helpers, "_REGISTERED_SPECS", {"acme": spec})
        assert spec_credential_source("acme") == "acme-cli"
        assert spec_credential_source("acme-work") == "acme-cli"
        assert spec_credential_source("unknown") == ""
