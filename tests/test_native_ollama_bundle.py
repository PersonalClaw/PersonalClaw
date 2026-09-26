"""``ollama-models`` is BUNDLED, and bundling it re-coupled nothing to core.

an owner ruling (2026-09-21): an app essential to the platform offering ships in core
and auto-installs at gateway install, **through the app mechanism** — `apps/native/` plus
the app loader. What was deliberately removed when Ollama was de-cored was *hardcoded
integration inside core*: an `llm/*.py` self-register, a special case in the registry.
That must not come back. This file is the machine-checkable form of that ruling for the
first bundled model provider.

Why `ollama-models` and not another: of the 16 chat-capable model apps it is the only one
that needs neither a credential nor a new Python wheel. It runs on `httpx` + `aiohttp`,
both already core runtime dependencies, and it declares `embedding` as well as `chat`, so
one bundle closes both lanes a fresh install has open. That matters because
``seed_builtin_apps()`` **never installs a bundled app's declared dependencies** — see
:func:`test_no_bundled_app_declares_python_dependencies`, which is the rail that keeps the
next bundle honest rather than a note someone has to remember.

The generic rails live next door and pick this bundle up for free:
``tests/test_native_capability_contract.py`` sweeps every bundled module for the SDK import
boundary, resolves every bundle-relative ``implementation``, and its parametrized
``test_bundle_owned_capability_has_no_core_implementation`` fails if any core module reaches
for this bundle's code. What is HERE is what those cannot say: that the manifest declares
the right shape, that the LLM-registry type is registered by the APP's own file, and that
core contains no ollama factory of its own.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
    native_bundle_dirs,
)

APP_NAME = "ollama-models"
PROVIDER_TYPE = "ollama"

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
_BUNDLE = NATIVE_DIR / APP_NAME


def _manifest() -> AppManifest:
    return AppManifest.from_json_file(_BUNDLE / "app.json")


# ── the manifest: shipped, native, zero-dependency ────────────────────────────


def test_the_bundle_ships_in_the_native_tree():
    """The directory exists with the four files the app owns."""
    assert _BUNDLE.is_dir(), f"{APP_NAME} is not bundled under {NATIVE_DIR}"
    for filename in ("app.json", "provider.py", "README.md", "LICENSE"):
        assert (_BUNDLE / filename).is_file(), f"{APP_NAME} is missing {filename}"


def test_the_bundle_is_declared_native():
    """``native: true`` is mandatory, and omitting it FAILS OPEN INTO A DOUBLE REGISTRATION.

    ``seed_builtin_apps`` does ``if not manifest.native: continue`` — so without the flag
    the app is never seeded through the installed-app path, and
    ``discover_bundled_extensions()`` then picks it up on the OTHER path instead. The
    failure mode is two registrations of one provider, not a clean "not installed".
    """
    assert _manifest().native is True


def test_the_manifest_declares_a_bundle_relative_model_provider():
    """type ``model``, both capabilities, and an implementation that is the BUNDLE's own."""
    provider = _manifest().provider
    assert provider is not None
    assert provider.type == "model"
    assert {"chat", "embedding"} <= set(provider.capabilities)
    assert provider.implementation == "provider:create_provider", (
        "the implementation must be bundle-relative (no dot in the module path). A dotted "
        "path names a CORE module, which is exactly the coupling R1 forbids."
    )


def test_no_bundled_app_declares_python_dependencies():
    """A bundled app must run on what core already ships, because nothing installs its deps.

    MEASURED: ``_install_python_deps()`` has exactly two callers, ``install()`` and
    ``update()``. ``seed_builtin_apps()`` — the only auto-install path, and the only one a
    bundled app ever takes — calls neither. ``apps/native_contract.py`` documents this as
    deliberate ("it cannot declare its own dependencies; the manifest ``dependencies`` block
    is installed by the Store's install/update path, and seeding a bundled app never takes
    it"), so a declared
    dependency on a bundled app is not a slow install — it is a tile that is dead on arrival
    with no error anywhere.

    This is also the measured answer to owner question Q1: ``openai-compatible`` and
    ``anthropic-compatible`` declare ``openai>=1.0`` / ``anthropic>=0.20`` and therefore
    CANNOT be bundled as-is. Bundling them needs either those wheels promoted to core
    dependencies or a seed path that shells out to pip — both owner-level decisions.
    """
    offenders: dict[str, list[str]] = {}
    for bundle in native_bundle_dirs():
        raw = json.loads((bundle / "app.json").read_text(encoding="utf-8"))
        declared = [
            key
            for key in ("pythonDependencies", "dependencies")
            if raw.get(key)  # absent, [] and {} all read as "declares none"
        ]
        if declared:
            offenders[bundle.name] = declared
    assert not offenders, (
        "bundled apps declare dependencies nothing installs: "
        + json.dumps(offenders, indent=2)
        + "\nseed_builtin_apps() never calls _install_python_deps() (only install() and "
        "update() do). Either the app must run on core's own dependencies, or the "
        "dependency belongs in pyproject.toml's `dependencies` — that is an owner call."
    )


def test_the_bundles_runtime_imports_are_core_dependencies():
    """Its two third-party imports are wheels core already carries, so the bundle is free.

    Read off the source rather than asserted from memory: a future edit that reaches for a
    fourth library would make the app unimportable on a bare ``pip install personalclaw``
    and nothing else would notice, because the imports are LAZY (inside ``__init__`` and
    inside the catalog methods) and so an import-time smoke test cannot see them.
    """
    text = (_BUNDLE / "provider.py").read_text(encoding="utf-8")
    third_party = {"httpx", "aiohttp"}
    found = {name for name in third_party if f"import {name}" in text}
    assert found == third_party, f"expected lazy imports of {third_party}, found {found}"

    import tomllib

    with (_SRC_DIR.parent / "pyproject.toml").open("rb") as fh:
        declared = tomllib.load(fh)["project"]["dependencies"]
    # A requirement line is "name>=x,<y" — compare on the distribution name alone.
    core_dep_names = {re.split(r"[^A-Za-z0-9._-]", req, maxsplit=1)[0].lower() for req in declared}
    for name in sorted(third_party):
        assert name in core_dep_names, (
            f"{name} is imported by the bundled {APP_NAME} app but is not a core runtime "
            "dependency — a bundled app's dependencies are never installed."
        )


# ── R1: the registration lives in the APP, and core has no copy ───────────────


def _reset_registration_state() -> None:
    """Clear the THREE process globals one enable-the-bundle cycle writes.

    The two registries are obvious. The third — the app module's entry in ``sys.modules``
    — is the one that used to be missing from the setup half, and it is not optional:

    🪤 ``load_bundle_module`` caches the app's module under its namespaced name, on purpose
    (a second resolution must not re-execute app code — see ``test_bundle_module_is_loaded_
    once`` next door, and the ``isinstance`` identity argument in its docstring). But the
    provider TYPE is registered as an *import-time side effect* of that module. So clearing
    the LLM registry while leaving the module cached leaves nothing able to re-register the
    type: the loader answers from cache, ``register_type`` never re-fires, and
    ``capability_of("ollama")`` raises ``unknown provider type 'ollama'; known types: []``.
    The three are one state and must be reset together, which is why this is a helper the
    fixture calls on BOTH sides rather than two hand-written halves that drifted.

    The app's own registration is already written to survive the re-exec this causes —
    ``provider.py`` wraps ``register_type`` in ``except ProviderResolutionError`` for exactly
    this reason — so eviction is safe whether or not the type is currently registered.
    """
    from personalclaw.llm.registry import reset_default_registry
    from personalclaw.providers import registry as prov_reg

    prov_reg._registry = None
    reset_default_registry()
    sys.modules.pop(namespaced_module_name(APP_NAME, "provider"), None)


@pytest.fixture()
def registered_bundle():
    """The bundle as the gateway builds it: manifest → ProviderRegistry → ModelTypeHandler.

    No shortcut construction and no monkeypatched loader — the point is that the REAL
    dispatch path reaches the bundle's own file. Every process-global one enable writes is
    reset around the test (see :func:`_reset_registration_state`) so it neither inherits nor
    leaks a registration.
    """
    from personalclaw.providers import registry as prov_reg

    _reset_registration_state()
    try:
        reg = prov_reg.get_provider_registry()
        reg.register(_manifest(), enabled=True)
        yield reg
    finally:
        _reset_registration_state()


def test_enabling_the_bundle_registers_the_ollama_type(registered_bundle):
    """The provider TYPE reaches the LLM registry, with both capabilities, offline.

    ``create_provider()`` only constructs an ``httpx.AsyncClient`` — every network call in
    the app is ``async`` and lazily reached (``is_available()``, ``OllamaCatalog.*``), so
    this runs with no daemon listening and no credential anywhere.
    """
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import get_default_registry

    capability = get_default_registry().capability_of(PROVIDER_TYPE)
    assert Capability.CHAT in capability.capabilities
    assert Capability.EMBEDDING in capability.capabilities


def test_a_gateway_already_loaded_the_module_and_the_type_still_registers():
    """The order-dependent form of the test above, made deterministic in ONE file.

    The test above only registers the type if nothing has already loaded the bundle, and in
    a multi-file run something has: booting a gateway enrols every ``type: model`` app, so
    ``tests/test_gateway_boot_provider_sync.py``'s first test leaves this bundle's module
    cached for the rest of the worker. Measured on pristine ``origin/main``::

        pytest tests/test_gateway_boot_provider_sync.py tests/test_native_ollama_bundle.py
        → FAILED test_enabling_the_bundle_registers_the_ollama_type
          ProviderResolutionError: unknown provider type 'ollama'; known types: []

    This is that precondition stated rather than inherited: the module is pre-cached exactly
    as a booted gateway leaves it, and the same manifest → ProviderRegistry →
    ModelTypeHandler path must still put the type in the registry. It is also the vacuity
    floor for :func:`_reset_registration_state` — delete its ``sys.modules.pop`` and this
    reds on its own, in isolation, instead of waiting for a file ordering to expose it.
    """
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers import registry as prov_reg

    module_name = namespaced_module_name(APP_NAME, "provider")
    _reset_registration_state()
    try:
        # What a booted gateway leaves behind: this module executed, its import-time
        # register_type already spent, and the module CACHED. Loaded inside the reset
        # boundary so the type lands on a throwaway registry rather than the process-wide
        # singleton other files share.
        load_bundle_module(_BUNDLE, APP_NAME, "provider")
        assert module_name in sys.modules, (
            "the precondition did not take — with no cached module this test would pass "
            "for the same reason the one above already does, and prove nothing"
        )

        # Now the fixture's own setup, run from that precondition.
        _reset_registration_state()
        prov_reg.get_provider_registry().register(_manifest(), enabled=True)

        capability = get_default_registry().capability_of(PROVIDER_TYPE)
        assert Capability.CHAT in capability.capabilities
        assert Capability.EMBEDDING in capability.capabilities
    finally:
        _reset_registration_state()


def test_the_registration_came_from_the_bundles_own_file(registered_bundle):
    """The module that called ``register_type`` is the bundle's ``provider.py``.

    This is the R1 assertion stated positively. The app reaches the registry through the
    published re-export in ``personalclaw.sdk.model``; the call site is in the app's file,
    under the app's own directory, loaded under a namespaced module name.
    """
    module_name = namespaced_module_name(APP_NAME, "provider")
    module = sys.modules.get(module_name)
    assert module is not None, (
        "enabling the app did not load the bundle module — the implementation is not "
        "resolving bundle-relative"
    )
    assert Path(module.__file__ or "") == _BUNDLE / "provider.py"
    assert "provider" not in sys.modules, "a bare 'provider' module leaked into sys.modules"


def test_core_declares_no_ollama_provider_type():
    """R1's negative half: no core module registers or implements an ollama provider.

    Ollama's de-coring removed a hardcoded integration from core, and bundling must not put
    one back. The two spellings that would count are a ``register_type`` call naming the
    type and a factory core owns, so both are checked against the source of every core
    module outside the bundled tree.

    Two Ollama-shaped things in core are deliberately NOT flagged, because neither is a
    provider registration and both predate this change: ``local_model_detect.py`` /
    ``seed_local_model.py`` (the OU-13 credential-free on-ramp, which probes an endpoint and
    writes a ``providers[]`` entry) and ``guardrails/model_call.py``'s local-endpoint
    classification. They are coupling to the *name*, which is a separate debt; they do not
    make core a provider.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if NATIVE_DIR in path.parents or "egg-info" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        hits = []
        if "register_type(OLLAMA" in text or 'register_type("ollama' in text:
            hits.append("registers the ollama provider type")
        if "def create_ollama_provider" in text or "class OllamaProvider" in text:
            hits.append("implements an ollama provider")
        if 'register_catalog("ollama' in text:
            hits.append("registers the ollama catalog")
        if hits:
            offenders[str(path.relative_to(_SRC_DIR))] = hits
    assert not offenders, (
        "core re-acquired a hardcoded Ollama provider registration — the exact thing R1 "
        "forbids. The registration belongs in the app's own provider.py, reached through "
        "personalclaw.sdk.model:\n" + json.dumps(offenders, indent=2)
    )


# ── the wheel: the bundle's code must actually ship ───────────────────────────


def test_a_bundled_apps_provider_module_is_declared_package_data():
    """``apps/native/*/*.py`` is an explicit package-data glob, not an implicit freebie.

    MEASURED 2026-09-21 on a clean ``rm -rf build dist && python -m build --wheel``:
    setuptools 84 already carries ``apps/native/*/*.py`` into the wheel with NO glob
    declared, while a ``.txt`` or ``.yaml`` placed beside it is dropped. So the inclusion is
    real today and rests entirely on setuptools' implicit handling of Python sources under
    the package tree — and ``build-system.requires`` is an unbounded ``setuptools>=64``.

    Without the glob, a future setuptools could stop shipping this bundle's provider and the
    symptom would be a manifest naming a module that isn't there: an error row in the Store
    on every install, invisible from a source checkout and visible only from a real
    ``pip install``. A source-level rail catches it in CI without paying for a build; the
    built-wheel proof is ``scripts/verify_wheel.py``'s job on release.
    """
    pyproject = (_SRC_DIR.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"apps/native/*/*.py",' in pyproject, (
        "pyproject.toml's [tool.setuptools.package-data] lost the apps/native/*/*.py glob. "
        "Two bundled apps own their provider code (personalclaw-ui-docs, ollama-models); "
        "without the glob their inclusion in the wheel is setuptools' choice, not ours."
    )


def test_a_bundled_apps_licence_is_declared_package_data():
    """The bundle's own ``LICENSE`` reaches the wheel, not just the source tree.

    MEASURED on the same clean build: setuptools ships `.py` beside `app.json` implicitly but
    DROPS everything else, so before this glob ``ollama-models`` shipped in the wheel with no
    licence statement at all — its ``app.json`` declares no licence field, so the file is the
    app's only terms, and ``seed_builtin_apps`` copies the whole bundle dir into the user's
    home. An app the platform installs on every first boot should carry its grant.
    """
    pyproject = (_SRC_DIR.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"apps/native/*/LICENSE",' in pyproject, (
        "pyproject.toml's [tool.setuptools.package-data] lost the apps/native/*/LICENSE glob — "
        "a bundled app would ship with no licence file."
    )
    for bundle in native_bundle_dirs():
        licence = bundle / "LICENSE"
        if not licence.is_file():
            continue  # a manifest-only bundle states its terms through the root LICENSE
        assert "MIT" in licence.read_text(encoding="utf-8"), (
            f"{bundle.name}/LICENSE is not MIT — every licence identifier this project states "
            "about itself must be MIT (docs/architecture/licence-identity.txt)."
        )
