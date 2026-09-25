"""An app may not re-pin a dependency core owns (EI-12 D3).

App ``pythonDependencies`` are pip-installed into the **shared** venv the gateway is
running out of, so a pin pip can only satisfy by moving a core dependency changes the
gateway's own dependency set — under a live process that already imported those
modules. ``app_manager._reject_core_dependency_conflicts`` refuses that class of pin
before anything is installed.

Why this shape rather than the plan's original "app-scoped target" isolation: measured
over the 44 first-party manifests, **zero** apps have both a backend and declared
``pythonDependencies`` — the two apps with a backend (``growth``, ``minutes``) declare
none, and all 20 dep-declaring apps are in-process provider apps. So a backend-scoped
``PYTHONPATH`` would isolate an empty population while the real shadowing risk (the
in-process providers) stayed open. Isolating those needs out-of-process providers, an
owner-scope seam change recorded BLOCKED in the plan. Refusing the conflict is the part
that makes the property true without redefining the provider seam.

Every assertion here runs through the real installer entry point, and the pip
subprocess is replaced with one that FAILS the test if it is ever reached — a refusal
that still spawned pip would not be a refusal.
"""

from __future__ import annotations

import json
from importlib.metadata import version as _dist_version

import pytest

from personalclaw.apps import app_manager
from personalclaw.apps.manifest import AppManifest

# A core-declared dependency, measured from the installed distribution's metadata
# (not pyproject.toml, which a wheel does not ship). numpy is core: `numpy>=1.21,<3`.
_CORE_NAME = "numpy"


def _manifest(deps: list[str], name: str = "dep-app") -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": name,
            "version": "1.0.0",
            "dependencies": {"pythonDependencies": deps},
            "provider": {"type": "tool", "implementation": "provider:make"},
        }
    )


@pytest.fixture
def no_pip(monkeypatch: pytest.MonkeyPatch):
    """Any pip spawn fails the test: a refusal must happen BEFORE the installer runs."""

    def unreachable(cmd, **kw):  # pragma: no cover — reaching this IS the failure
        raise AssertionError(f"pip was spawned despite a refused pin: {cmd}")

    monkeypatch.setattr(app_manager.subprocess, "run", unreachable)


# ── The refusals ──────────────────────────────────────────────────────────────


def test_a_conflicting_core_pin_is_refused_and_leaves_the_gateway_untouched(no_pip) -> None:
    """The atom's clause, with a real conflicting pin: core runs numpy>=1.21, the app
    demands <1.21, so pip could only satisfy it by downgrading the gateway's numpy."""
    before = _dist_version(_CORE_NAME)

    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest([f"{_CORE_NAME}<1.21"]))

    msg = str(ei.value)
    assert _CORE_NAME in msg and "refused" in msg, msg
    # The gateway's own dependency is byte-for-byte the version it was running.
    assert _dist_version(_CORE_NAME) == before


def test_an_upgrade_pip_would_have_to_perform_is_also_refused(no_pip) -> None:
    """Stricter than "stay inside core's range", and deliberately so: a pin ABOVE the
    installed version still sits inside core's `<3` ceiling, but satisfying it moves
    numpy under a running gateway. "Does not affect the gateway" means pip moves
    nothing, not "pip moves it somewhere core would also have accepted"."""
    installed = _dist_version(_CORE_NAME)
    with pytest.raises(app_manager.AppLifecycleError):
        app_manager._install_python_deps(_manifest([f"{_CORE_NAME}>{installed}"]))


def test_an_unparseable_pin_is_refused(no_pip) -> None:
    """Fail-closed: `AppManifest.validate()` does not vet requirement specifiers, so a
    garbage spec reaches the installer. It must not be handed to pip to "decide"."""
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest(["=not a requirement="]))
    assert "unparseable" in str(ei.value)


def test_a_core_name_whose_version_cannot_be_read_is_refused(no_pip) -> None:
    """Fail-closed on the third case. `pysqlite3-binary` is core-declared but carries a
    linux/x86_64 marker, so it is absent on other platforms: we cannot prove the install
    would leave core's dependency alone, so it denies rather than resolving it."""
    core = app_manager._core_requirement_pins()
    absent = [name for name in core if not _installed(name)]
    if not absent:  # pragma: no cover — every core dep present on this platform
        pytest.skip("no core dependency is absent on this platform")
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest([f"{absent[0]}>=0.1"]))
    assert "cannot be read" in str(ei.value)


def _installed(name: str) -> str | None:
    try:
        return _dist_version(name)
    except Exception:
        return None


# ── What must stay installable (the guard's blast radius) ─────────────────────


def test_the_real_compatible_core_pin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The vacuity floor: this is `diarization-onnx`'s ACTUAL pin (`numpy>=1.24`), the
    one real first-party collision with a core name. The guard evaluates a core-owned
    name here and ALLOWS it — so the rail is matching real input, not nothing.
    """
    installed = _dist_version(_CORE_NAME)
    assert app_manager._core_requirement_pins().get(_CORE_NAME) is not None, (
        "numpy is no longer core-declared — this test's premise, and the guard's only "
        "real first-party collision, is gone"
    )
    # No pip spawn expected either: the pin is already satisfied, so it is a no-op.
    monkeypatch.setattr(
        app_manager.subprocess,
        "run",
        lambda cmd, **kw: (_ for _ in ()).throw(AssertionError(f"unexpected pip: {cmd}")),
    )
    assert app_manager._install_python_deps(_manifest([f"{_CORE_NAME}>=1.24"])) is False
    assert _dist_version(_CORE_NAME) == installed


def test_extras_are_not_core_so_provider_apps_stay_installable() -> None:
    """The invariant that keeps the Store working. Nearly every first-party app that
    declares pythonDependencies pins one of these; every one is an `extra ==` entry in
    core's metadata, NOT a core dependency. If a future change promotes one to core,
    this goes red — which is the warning that those apps just became uninstallable.

    Deliberately no count here: the number of apps is a fact about another repository,
    and this test cannot see it. `docs/security/limitations.md` carries the measured
    ratio with the ref it was measured against.
    """
    core = app_manager._core_requirement_pins()
    for name in (
        "openai",  # 12 provider apps
        "anthropic",  # anthropic-models, anthropic-compatible
        "boto3",  # bedrock-models
        "slack-sdk",  # slack-channel
        "faster-whisper",
        "sentence-transformers",
        "piper-tts",
        "huggingface-hub",
        "faiss-cpu",
    ):
        assert name not in core, f"{name} became a CORE dep — dep-declaring apps now refuse"


def test_a_non_core_pin_is_untouched_by_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A library core does not own passes straight through to pip, unchanged."""
    calls: list[list[str]] = []

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        app_manager.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _OK())[1]
    )
    assert (
        app_manager._install_python_deps(_manifest(["totally-not-a-real-pkg-xyz==9.9.9"])) is True
    )
    assert calls, "the guard swallowed a perfectly legal non-core requirement"


def test_every_real_first_party_dep_declaration_passes_the_guard(no_pip) -> None:
    """ "Existing installed apps continue to work". These are the exact
    `pythonDependencies` of all 20 dep-declaring first-party apps, read from the apps
    repo at authoring time; they are inlined rather than globbed so the assertion cannot
    silently pass by finding no manifests (CI has no apps checkout).
    """
    real_declarations = {
        "alibaba-models": ["openai>=1.0"],
        "anthropic-compatible": ["anthropic>=0.20"],
        "anthropic-models": ["anthropic>=0.20"],
        "bedrock-models": ["boto3>=1.34"],
        "deepseek-models": ["openai>=1.0"],
        "diarization-onnx": [
            "onnxruntime>=1.16",
            "sherpa-onnx>=1.10",
            "soundfile>=0.12",
            "numpy>=1.24",
        ],
        "diarization-pyannote": ["pyannote.audio>=3.1", "torch>=2.0"],
        "faster-whisper": ["faster-whisper>=1.0"],
        "google-models": ["openai>=1.0"],
        "groq-models": ["openai>=1.0"],
        "meta-muse-spark": ["openai>=1.0"],
        "mistral-models": ["openai>=1.0"],
        "openai-compatible": ["openai>=1.0"],
        "openai-models": ["openai>=1.0"],
        "openrouter-models": ["openai>=1.0"],
        "piper-tts": ["piper-tts>=1.2", "huggingface-hub>=0.23"],
        "sentence-transformers": ["sentence-transformers>=3.0", "faiss-cpu>=1.7"],
        "slack-channel": ["slack-sdk>=3.27,<4"],
        "together-models": ["openai>=1.0"],
        "vllm-models": ["openai>=1.0"],
    }
    assert len(real_declarations) == 20  # the measured population
    for app, deps in real_declarations.items():
        # The guard alone — not the installer — so an absent heavy wheel cannot
        # masquerade as a refusal.
        app_manager._reject_core_dependency_conflicts(_manifest(deps, name=app), deps)


def test_the_guard_runs_on_update_too_not_only_install() -> None:
    """Both lifecycle call sites funnel through `_install_python_deps`, so one guard
    covers install AND update. Asserted structurally: if a future change gives update
    its own dep path, this catches it."""
    import inspect

    src = inspect.getsource(app_manager)
    assert (
        src.count("_install_python_deps(manifest)") == 2
    ), "install and update no longer share the single guarded dependency path"
    assert src.count("_reject_core_dependency_conflicts(manifest, reqs)") == 1


def test_core_pins_exclude_extras_by_marker_not_by_name_list() -> None:
    """The exclusion must be derived from the `extra ==` marker. A hardcoded name list
    would rot the moment core gained an extra."""
    core = app_manager._core_requirement_pins()
    assert "numpy" in core and "httpx" in core, sorted(core)
    # `personalclaw` self-references appear only under extras (dev/all bundles).
    assert "personalclaw" not in core
    assert json.dumps(sorted(core))  # names are plain strings, safely serializable


# ── The DISCLOSURE half: what the consent surface is told ──────────────────────
#
# The guard above refuses a pin that would move a core dependency. It says nothing about
# the pins it ADMITS, and neither did the install-consent dialog: it enumerated gateway
# permissions, app messaging, desktop capabilities, network reach and dashboard code, and
# never that installing an app pip-installs a third-party package into the interpreter the
# gateway runs in — holding the owner's credentials, filesystem and network reach. Measured
# on a fresh `python:3.13-slim` container: four of nine Store installs did exactly that.
#
# `describe_python_dependencies` is that disclosure, and it reads `coreOwned` from
# `_core_requirement_pins` — the SAME set the guard gates on — so the two cannot drift.
# These tests pin the properties the UI copy depends on being TRUE.


def test_the_disclosure_reads_core_ownership_from_the_guards_own_pin_set() -> None:
    """A core-owned name and a non-core one must classify differently, and the split must
    come from the guard's authority rather than a second list."""
    deps = app_manager.describe_python_dependencies(
        _manifest([f"{_CORE_NAME}>=1.24", "openai>=1.0", "anthropic>=0.20"])
    )
    assert deps == [
        {"spec": f"{_CORE_NAME}>=1.24", "coreOwned": True},
        {"spec": "openai>=1.0", "coreOwned": False},
        {"spec": "anthropic>=0.20", "coreOwned": False},
    ]
    # The provider SDKs are core EXTRAS, which `_core_requirement_pins` excludes on
    # purpose — so they correctly read as new code entering the interpreter. That is the
    # same exclusion `test_extras_are_not_core_so_provider_apps_stay_installable` relies
    # on, read from the other side.
    core = app_manager._core_requirement_pins()
    assert "openai" not in core and "anthropic" not in core


def test_the_spec_is_returned_VERBATIM_because_the_specifier_is_the_disclosure() -> None:
    """A user deciding about `anthropic>=0.20` has to see that string. Normalising it
    (canonicalised name, re-rendered specifier) would silently change what the screen
    claims the manifest says."""
    raw = ["Pillow>=10,<13", "slack_sdk>=3.27,<4", "  numpy>=1.24  "]
    out = app_manager.describe_python_dependencies(_manifest(raw))
    assert [d["spec"] for d in out] == raw
    # …while the CLASSIFICATION still canonicalises, so `slack_sdk` resolves against
    # `slack-sdk` and `Pillow` against `pillow`. Verbatim display, canonical matching.
    by_spec = {d["spec"]: d["coreOwned"] for d in out}
    assert by_spec["Pillow>=10,<13"] is True  # core-declared
    assert by_spec["slack_sdk>=3.27,<4"] is False  # an extra, not core
    assert by_spec["  numpy>=1.24  "] is True  # whitespace must not defeat the match


def test_an_app_declaring_nothing_discloses_nothing() -> None:
    """`[]`, not a placeholder row. An empty "Python packages: none" box on the consent
    screen would alarm without informing — five of the nine measured installs declared no
    dependency at all."""
    assert app_manager.describe_python_dependencies(_manifest([])) == []


def test_an_unreadable_pin_set_degrades_to_the_LOUDER_disclosure() -> None:
    """The one fail direction that is safe. `packaging` is genuinely absent on a fresh
    container (issue #3539 — measured: `import packaging` raises there), which is also the
    condition that makes the guard REFUSE the install. The disclosure must not vanish and
    must not quietly claim a package is core-owned: every spec degrades to `coreOwned:
    False`, i.e. to "new code enters your interpreter", which over-discloses rather than
    under-discloses. Over-disclosing a package is safe; under-disclosing one is the defect
    being fixed."""

    def no_pins():
        raise ModuleNotFoundError("No module named 'packaging'")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_manager, "_core_requirement_pins", no_pins)
        out = app_manager.describe_python_dependencies(_manifest([f"{_CORE_NAME}>=1.24"]))
    # The spec survives — losing it is the only outcome worse than mis-grouping it.
    assert out == [{"spec": f"{_CORE_NAME}>=1.24", "coreOwned": False}]


def test_an_unparseable_spec_still_discloses_and_does_not_claim_core_ownership() -> None:
    """`AppManifest.validate()` does not vet specifiers, so an unparseable one reaches
    here. The install will be REFUSED for it (see `test_an_unparseable_pin_is_refused`), and
    until then the string is shown as-is rather than dropped: a disclosure that silently
    omits the thing it cannot parse is how a surface understates what it is consenting to."""
    out = app_manager.describe_python_dependencies(_manifest(["=not a requirement="]))
    assert out == [{"spec": "=not a requirement=", "coreOwned": False}]


def test_the_catalog_carries_the_disclosure_to_every_scanned_card() -> None:
    """The three scan paths (git, local, native) build a `CatalogEntry` from a manifest, and
    all three read the consent facts from ONE helper — which is what stops a fourth scan
    site from surfacing permissions and crons while forgetting the packages. Asserted
    structurally, because the defect being fixed was precisely an omission."""
    import inspect

    from personalclaw.apps import catalog

    src = inspect.getsource(catalog)
    assert src.count("_perms, _crons, _deps = _manifest_consent(m)") == 3
    assert src.count("pythonDependencies=_deps,") == 3
    # A registry POINTER must NOT get one: its manifest is unread, and `consentKnown=False`
    # is what the frontend reads to say "unknown" rather than "none".
    pointer = inspect.getsource(catalog._pointer_to_entry)
    assert "pythonDependencies" not in pointer
    assert (
        catalog._pointer_to_entry(
            "https://example.invalid/r.git",
            catalog.RegistryPointer(name="p", repo="https://example.invalid/r.git"),
            is_git=True,
        ).to_dict()["pythonDependencies"]
        == []
    )


def test_a_scanned_manifest_with_deps_reaches_the_wire_classified() -> None:
    """End to end through the real helper: manifest → `_manifest_consent` → the wire shape
    the consent UI reads."""
    from personalclaw.apps import catalog

    _perms, _crons, deps = catalog._manifest_consent(
        _manifest([f"{_CORE_NAME}>=1.24", "openai>=1.0"])
    )
    assert deps == [
        {"spec": f"{_CORE_NAME}>=1.24", "coreOwned": True},
        {"spec": "openai>=1.0", "coreOwned": False},
    ]
    # And the no-dep case stays empty rather than becoming a placeholder.
    assert catalog._manifest_consent(_manifest([]))[2] == []
