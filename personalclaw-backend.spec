# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `personalclaw-backend` binary.

Produces a `dist/personalclaw-backend/` directory bundle (one-folder mode) that
the Electron app embeds via `extraResources` into the macOS .app. Run with:

    pyinstaller personalclaw-backend.spec --noconfirm

Output is `dist/personalclaw-backend/personalclaw-backend` (executable) plus a
sibling `_internal/` directory.

WHAT GOES IN IS NOT DECIDED HERE for first-party content. `scripts/backend_bundle_manifest.py`
derives the data payload from `[tool.setuptools.package-data]` — the same declaration the
wheel is built from — and the dynamically-imported module lists from the tree, every module
core loads by its name among them, and `tests/test_backend_bundle_manifest.py` and
`tests/test_by_name_load_census.py` assert that derivation is complete. This file used
to transcribe those globs by hand and had drifted eleven of thirty, which is how the shipped
`.app` came to have no `agents/runner_catalog.json` and no `tool_providers/rules_builtin.json`.
Third-party collection stays below, where PyInstaller's knowledge of site-packages belongs.
"""
import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None


def _load_manifest():
    """Import `scripts/backend_bundle_manifest.py` by path.

    A spec is `exec`'d, not imported, so it has no package context and cannot `import
    scripts.backend_bundle_manifest`. `SPECPATH` is the directory PyInstaller resolves the
    spec from (the repo root); fall back to CWD for a direct `exec` of this file, which is
    how a linter or a `python personalclaw-backend.spec` smoke read would reach it.
    """
    root = globals().get("SPECPATH") or os.getcwd()
    path = os.path.join(root, "scripts", "backend_bundle_manifest.py")
    spec = importlib.util.spec_from_file_location("backend_bundle_manifest", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable with the file present
        raise SystemExit(f"personalclaw-backend.spec: cannot load the bundle manifest at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest = _load_manifest()


def _assert_host_native_arch() -> None:
    """Fail the build if it would ship a non-native (Rosetta x86_64) macOS bundle.

    The EXE below pins ``target_arch=None`` *intentionally* — PyInstaller then
    builds for the architecture of the interpreter running this spec, i.e. the
    host arch. Native arm64 thus falls out of building on an Apple Silicon mac;
    no cross-arch tooling is involved.

    The one footgun is a Rosetta-translated x86_64 Python shell on Apple Silicon:
    ``platform.machine()`` reports ``x86_64`` even though the hardware is arm64,
    so a careless build would silently emit an x86_64 ``.dmg`` that runs under
    Rosetta. Detect that mismatch — real CPU arm64 but interpreter x86_64 — and
    stop, so nobody ships a translated build by accident. Set
    ``PERSONALCLAW_ALLOW_CROSS_ARCH=1`` to override (deliberate cross-arch build).
    """
    import os
    import platform

    if platform.system() != "Darwin" or os.environ.get("PERSONALCLAW_ALLOW_CROSS_ARCH"):
        return
    interp_arch = platform.machine()  # interpreter's arch (x86_64 under Rosetta)
    # The true hardware arch: sysctl reports arm64 even from a Rosetta shell.
    hw_arm64 = False
    try:
        import subprocess
        out = subprocess.run(
            ["sysctl", "-in", "hw.optional.arm64"],
            capture_output=True, text=True, timeout=5,
        )
        hw_arm64 = out.stdout.strip() == "1"
    except Exception:
        hw_arm64 = False
    if hw_arm64 and interp_arch != "arm64":
        raise SystemExit(
            "personalclaw-backend.spec: refusing to build a non-native "
            f"{interp_arch!r} bundle on Apple Silicon — this would ship a "
            "Rosetta x86_64 .dmg. Run the build with a native arm64 Python "
            "(check `python -c 'import platform; print(platform.machine())'` "
            "prints 'arm64'), or set PERSONALCLAW_ALLOW_CROSS_ARCH=1 to override."
        )


_assert_host_native_arch()


# The first-party modules the analysis cannot see, every one derived by the manifest and none named
# here: a hand list beside a growing tree is how this one came to name a module that does not
# exist, and to miss the ones core loads by name.
#
# Every module core loads by its NAME (`by_name_modules`): the agent's memory, automation, prompt
# and subagent tools, the document writers, the bundled skill and template packages read through
# `importlib.resources`, every native app's provider factory, the child modules the bundle's entry
# runs as `-m <module>`, the entry points. Without them the desktop app's agent had none of those
# tools and the tool server agent CLIs run exited at its first tool listing.
hidden = manifest.by_name_modules()
# What the native apps' own files import (`path_loaded_imports`): the loader reads those files by
# their path, so the analysis never reads them.
hidden += manifest.path_loaded_imports()
# Every `personalclaw.sdk.*` submodule. An app imports core ONLY through the SDK and
# `providers.registry` resolves that import at ENABLE time via importlib, so static analysis
# sees none of it: the 2026-09-23 bundle could not enable a single one of the four extensions
# it shipped (sdk.search / sdk.tts / sdk.channel / sdk.trigger_source all
# ModuleNotFoundError). Enumerated from the directory, never named one by one.
hidden += manifest.sdk_submodules()

# LLM provider SDKs are lazy-imported inside provider classes.
hidden += collect_submodules("openai")
hidden += collect_submodules("anthropic")
# openai requires tqdm and imports it only lazily, so the analysis left it out. Carried, so the
# bundle holds every package openai requires, and an app that requires openai installs here
# without asking for a pip the bundle does not have (`metadata_datas` below).
hidden += ["tqdm"]
# Snowball stemmer registers languages dynamically.
hidden += collect_submodules("snowballstemmer")
# slack_sdk has lots of conditional imports.
hidden += collect_submodules("slack_sdk")
# openpyxl lazy-imports its reader/writer submodules (knowledge .xlsx ingestion) —
# static analysis misses them, so collect the whole package for the frozen bundle.
hidden += collect_submodules("openpyxl")
# trafilatura (web/extract.py main-content extraction) lazy-imports its extractor +
# dependency submodules (justext, courlan, htmldate); collect the whole package so the
# frozen bundle can extract pages.
hidden += collect_submodules("trafilatura")

datas = manifest.bundle_datas()
# trafilatura bundles data files (language models / settings) referenced at runtime.
datas += collect_data_files("trafilatura")
# Slack SDK ships a `version.py` and `data/` files referenced at runtime.
datas += collect_data_files("slack_sdk")
# cron_descriptor includes locale data.
datas += collect_data_files("cron_descriptor")
# sqlite-vec ships its loadable SQLite extension as a shared library INSIDE the package
# (sqlite_vec/vec0.dylib|.so), found at runtime via sqlite_vec.loadable_path(). It is data, not
# an importable extension module, so PyInstaller's import analysis never sees it — collect it
# with the package path preserved so loadable_path() still resolves inside the bundle. If this
# ever fails to land, the knowledge ANN index degrades to the exact scan (correct, slower) and
# says so in the Doctor rather than breaking search.
datas += collect_data_files("sqlite_vec", include_py_files=False)

a = Analysis(
    ["src/personalclaw/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy optional deps — opt in via separate build if needed.
        "torch",
        "tensorflow",
        "faster_whisper",
        "faiss",
        "sentence_transformers",
        "transformers",
        # Optional JS-render path (web/render.py) — ships a headless browser; never
        # bundle it (web/render imports it lazily + degrades when absent).
        "playwright",
        # Test/dev tooling.
        "pytest",
        "hypothesis",
        "black",
        "isort",
        "flake8",
        "mypy",
    ],
    noarchive=False,
    optimize=0,
)
# What `importlib.metadata` reads in the bundle: the metadata of every distribution the analysis
# carries modules of. An app's declared packages are judged installed by it, so without it every
# model app's `openai` read as missing and its install asked for a pip the bundle does not have.
# Derived from what was analysed (`manifest.metadata_datas`), never listed.
a.datas += manifest.metadata_datas(
    [name for name, _src, _kind in a.pure]
    + [name for name, _src, kind in a.binaries if kind == "EXTENSION"]
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="personalclaw-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # Host-native arch (arm64 on Apple Silicon, x86_64 on Intel) — intentional;
    # see _assert_host_native_arch() above. Do NOT pin to "x86_64": it would
    # ship a Rosetta build on Apple Silicon. universal2 is a separate, larger
    # effort (needs universal2 wheels for every native dep) — out of scope.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="personalclaw-backend",
)
