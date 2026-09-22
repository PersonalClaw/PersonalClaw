"""#1783 clause 5 — `set_engine` reachable, railed on the REAL gateway boot.

The defect: `context_engine.set_engine` had exactly one non-test caller — its own
quarantine path, `set_engine(None)` — so `DefaultContextEngine` was the only engine that
could ever be active. The whole swappable seam (a 4-hook Protocol, a runtime-checkable
contract, a fail-closed validator, a quarantine on raise) was a complete read side with
no writer: nothing could install an engine, so nothing could be swapped.

These rails therefore do NOT call `install_engine` to prove the seam works. They set a
name in `config.json`, boot the real gateway, and read which engine came out active —
the `_context_engine_startup` hook is a closure inside `start_dashboard` and cannot be
imported, so a boot is the only honest way to observe that it fires. A test that called
`install_engine` directly would pass with the `on_startup.append` line deleted, which is
precisely the shape that let this ship inert. The boot harness is
`tests/test_gateway_boot_app_source_seed.py`'s, for the same reason.

The three fail-closed paths are railed at the boot too: a typo in `config.json` must
degrade to today's behaviour, never to a dark chat.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw import context_engine as ce
from personalclaw.context_engine import DEFAULT_ENGINE_NAME, AssembledContext

TEST_ENGINE = "test-engine-1783"


class _FakeEngine:
    """A minimal engine that satisfies the contract and is distinguishable by name."""

    name = TEST_ENGINE
    owns_compaction = False

    def ingest(self, session_key: str, role: str, content: str) -> None:
        return None

    def assemble(self, builder, text, *, is_new_session: bool, **kwargs) -> AssembledContext:
        return AssembledContext(message=text)

    def after_turn(self, session_key: str) -> None:
        return None


#: Every `_build_fake()` call, so "registered but not selected was never constructed" is
#: an OBSERVATION. Registering the class itself would make that assertion unfalsifiable.
_builds: list[str] = []


def _build_fake() -> _FakeEngine:
    _builds.append(TEST_ENGINE)
    return _FakeEngine()


@pytest.fixture(autouse=True)
def clean_registry():
    """Restore the PROCESS-GLOBAL active engine and registry around every test.

    Both are module state, and one of these rails deliberately installs a non-default
    engine: leaking that into the rest of the suite would change how every later test's
    context is assembled. Restored rather than cleared so the default registration made
    at import time survives.
    """
    saved_active, saved_registry = ce.get_engine(), dict(ce._REGISTRY)
    _builds.clear()
    try:
        yield
    finally:
        ce._REGISTRY.clear()
        ce._REGISTRY.update(saved_registry)
        ce.set_engine(None if saved_active is ce._DEFAULT else saved_active)


@pytest.fixture
def boot_home(tmp_path, monkeypatch):
    """An isolated `PERSONALCLAW_HOME`, asserted through BOTH `config_dir` bindings.

    `config/__init__.py` binds its own copy at import, so patching only the loader
    attribute leaves one binding pointed at the user's real home — which a boot would
    then write. Copied from the app-source-seed rails, where that was measured.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")

    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader

    assert config_loader.config_dir().resolve() == tmp_path.resolve()
    assert config_pkg.config_dir().resolve() == tmp_path.resolve()
    return tmp_path


def _write_engine_name(home, name) -> None:
    """Put a context-engine name in `config.json` the way an operator's edit leaves it."""
    (home / "config.json").write_text(
        json.dumps({"session": {"context_engine": name}}), encoding="utf-8"
    )


async def _boot_and_read_engine() -> str:
    """Boot the real gateway on an ephemeral port; return the ACTIVE engine's name."""
    from personalclaw.dashboard.server import start_dashboard

    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        return getattr(ce.get_engine(), "name", "?")
    finally:
        await runner.cleanup()


# ── the boot wire: a name in config selects an engine ────────────────────────


@pytest.mark.asyncio
async def test_a_registered_engine_named_in_config_is_active_after_boot(boot_home):
    """🔴 THE defect. Before #1783 no name in `config.json` could change the active
    engine, because nothing resolved one — this booted on the default whatever it said."""
    ce.register_engine(TEST_ENGINE, _build_fake)
    _write_engine_name(boot_home, TEST_ENGINE)

    assert await _boot_and_read_engine() == TEST_ENGINE
    assert ce.get_engine().name == TEST_ENGINE, "the engine outlives the boot it was set in"


@pytest.mark.asyncio
async def test_a_boot_with_no_configured_engine_stays_on_the_default(boot_home):
    """Vacuity control for the rail above: the boot leg can produce the other outcome,
    so its green is an observation of the boot and not of a constant. Also the promise
    that this atom changed nothing for an operator who never set the field."""
    ce.register_engine(TEST_ENGINE, _build_fake)  # registered but NOT selected

    assert await _boot_and_read_engine() == DEFAULT_ENGINE_NAME
    assert _builds == [], "an unselected engine must not be constructed"


@pytest.mark.asyncio
async def test_an_unknown_engine_name_boots_on_the_default(boot_home):
    """Fail closed #1 — a typo. The gateway must come up on the built-in assembly, not
    refuse to boot and not come up with no engine at all: every chat turn goes through
    `assemble_context`, so "no engine" is a dark chat."""
    _write_engine_name(boot_home, "engine-that-does-not-exist")

    assert await _boot_and_read_engine() == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_an_engine_whose_factory_raises_boots_on_the_default(boot_home):
    """Fail closed #2 — a registered engine that cannot be built (a missing model, an
    unreadable store). The failure belongs to that engine, not to the gateway."""

    def _explode():
        raise RuntimeError("no model bound")

    ce.register_engine(TEST_ENGINE, _explode)
    _write_engine_name(boot_home, TEST_ENGINE)

    assert await _boot_and_read_engine() == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_an_engine_missing_a_hook_boots_on_the_default(boot_home):
    """Fail closed #3 — an instance that does not satisfy the 4-hook contract. Checked at
    INSTALL time, because the alternative is an AttributeError mid-turn: `set_engine`'s
    existing validator is what makes this safe, and this rail is what proves the
    installer routes through it rather than assigning `_active` itself."""

    class _MissingAfterTurn:
        name = TEST_ENGINE
        owns_compaction = False

        def ingest(self, session_key, role, content):
            return None

        def assemble(self, builder, text, *, is_new_session, **kwargs):
            return AssembledContext(message=text)

    ce.register_engine(TEST_ENGINE, _MissingAfterTurn)
    _write_engine_name(boot_home, TEST_ENGINE)

    assert await _boot_and_read_engine() == DEFAULT_ENGINE_NAME


@pytest.mark.asyncio
async def test_the_installed_engine_assembles_the_turn(boot_home):
    """The seam is only swapped if the swap reaches the assembly. `assemble_context` is
    the single call site every chat turn uses, so it is asserted against the installed
    engine's output — the name being right would otherwise prove only bookkeeping."""
    ce.register_engine(TEST_ENGINE, _build_fake)
    _write_engine_name(boot_home, TEST_ENGINE)
    assert await _boot_and_read_engine() == TEST_ENGINE

    # `_FakeEngine.assemble` returns the text unchanged with no injection; the default
    # engine would call `build_message` on this MagicMock builder and inject a prompt.
    assembled = ce.assemble_context(MagicMock(), "hello", is_new_session=True)
    assert (assembled.message, assembled.injected_chars) == ("hello", 0)


# ── the config round trip + the Settings write path ─────────────────────────


def test_the_field_round_trips_through_load_and_to_dict(boot_home):
    """The config contract: a written name is read back and re-serialized unchanged. A
    field that `to_dict()` drops disappears on the next Settings save."""
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().session.context_engine == DEFAULT_ENGINE_NAME  # the default
    _write_engine_name(boot_home, TEST_ENGINE)

    cfg = AppConfig.load()
    assert cfg.session.context_engine == TEST_ENGINE
    assert cfg.to_dict()["session"]["context_engine"] == TEST_ENGINE


@pytest.mark.parametrize("registered", [True, False])
def test_the_edit_boundary_accepts_only_a_registered_name(registered):
    """The Settings write path is gated on the LIVE registry, not a static enum: an app
    bundle's engine has to become settable when it registers, and a name nothing
    registered has to be refused rather than written and silently ignored at the next
    boot."""
    from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG["session.context_engine"]
    if registered:
        ce.register_engine(TEST_ENGINE, _build_fake)
        assert coerce_edit_value("session.context_engine", TEST_ENGINE, spec) == TEST_ENGINE
    else:
        with pytest.raises(ConfigValueError):
            coerce_edit_value("session.context_engine", TEST_ENGINE, spec)

    # The built-in is always settable — it is the name every failure path falls back to,
    # so an operator must be able to type it back.
    assert (
        coerce_edit_value("session.context_engine", DEFAULT_ENGINE_NAME, spec)
        == DEFAULT_ENGINE_NAME
    )


def test_the_default_engine_is_registered_under_its_config_name():
    """The registry and the config default must agree, or a fresh install's own value
    would be the unknown-name path. One constant names both."""
    assert DEFAULT_ENGINE_NAME in ce.available_engines()
    assert ce.install_engine(DEFAULT_ENGINE_NAME) == DEFAULT_ENGINE_NAME
    assert ce.get_engine() is ce._DEFAULT
