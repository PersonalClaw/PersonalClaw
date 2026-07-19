# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `personalclaw-backend` binary.

Produces a `dist/personalclaw-backend/` directory bundle (one-folder mode) that
the Electron app embeds via `extraResources` into the macOS .app. Run with:

    pyinstaller personalclaw-backend.spec --noconfirm

Output is `dist/personalclaw-backend/personalclaw-backend` (executable) plus a
sibling `_internal/` directory.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None


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


def _backend_data():
    """Replicate the package-data globs from pyproject.toml.

    Sources live under ``src/personalclaw/``; bundle destinations mirror the
    import package (``personalclaw/...``) so the frozen binary resolves data
    via ``importlib.resources`` the same way the installed wheel does.
    """
    patterns = [
        ("src/personalclaw/py.typed", "personalclaw"),
        ("src/personalclaw/slack-manifest.yaml", "personalclaw"),
        ("src/personalclaw/model_tokens.json", "personalclaw"),
        ("src/personalclaw/model_pricing.json", "personalclaw"),
        ("src/personalclaw/config", "personalclaw/config"),
        ("src/personalclaw/eval/scenarios", "personalclaw/eval/scenarios"),
        ("src/personalclaw/scripts", "personalclaw/scripts"),
        ("src/personalclaw/static", "personalclaw/static"),
        ("src/personalclaw/tests_fixtures", "personalclaw/tests_fixtures"),
        ("src/personalclaw/skills/bundled", "personalclaw/skills/bundled"),
        ("src/personalclaw/workflows/bundled", "personalclaw/workflows/bundled"),
        ("src/personalclaw/apps/native", "personalclaw/apps/native"),
        # Built React SPA — served by personalclaw.dashboard.handlers.core.index.
        # Must be built first: `cd web && npm install && npm run build`.
        ("web/dist", "personalclaw/static/dist"),
    ]
    out = []
    import os
    for src, dst in patterns:
        if os.path.exists(src):
            out.append((src, dst))
    return out


def _bundled_provider_modules():
    """Every provider entry-point module declared by a bundled ``app.json``.

    Bundled providers are loaded at runtime from each manifest's
    ``provider.implementation`` (``module.path:factory``) via importlib, so
    PyInstaller's static analysis can't see them. Deriving the hidden-import
    list straight from the manifests (rather than hand-maintaining it) keeps the
    frozen app in lockstep with the bundles: a new bundle ships automatically,
    and none can silently drop out of the frozen binary.
    """
    import glob
    import json
    import os

    mods: set[str] = set()
    for manifest in glob.glob("src/personalclaw/apps/native/*/app.json"):
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        impl = (data.get("provider") or {}).get("implementation", "")
        module_path = impl.split(":", 1)[0].strip()
        if module_path:
            mods.add(module_path)
            # Also pull the whole owning package so sibling helpers a factory
            # imports (e.g. acp_bundles._register) come along.
            pkg = module_path.rsplit(".", 1)[0]
            if pkg:
                mods.add(pkg)
    return sorted(mods)


# Provider entry-points are loaded dynamically (importlib), so force-include
# them as hidden imports. Manifest-derived so the list never drifts; plus the
# core-native model machinery, referenced by code rather than a bundled manifest:
# the acp_agent runtime + the two inference PROTOCOL clients (OpenAI-/Anthropic-
# compatible) that live in core and back every model app via sdk.model. (The model
# PROVIDERS themselves — openai/anthropic/vllm/bedrock/… — are installed apps now, so
# they are NOT listed here; they ship + load from apps/.)
hidden = [
    "personalclaw.llm.acp_agent",
    "personalclaw.llm.anthropic",
    "personalclaw.llm.openai",
    "personalclaw.inbox_providers.slack_source",
]
hidden += _bundled_provider_modules()
# acp:<cli> bundles import sibling helpers dynamically too — collect the whole
# package so ``personalclaw.acp_bundles._register`` etc. always ship.
hidden += collect_submodules("personalclaw.acp_bundles")

# LLM provider SDKs are lazy-imported inside provider classes.
hidden += collect_submodules("openai")
hidden += collect_submodules("anthropic")
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

datas = _backend_data()
# trafilatura bundles data files (language models / settings) referenced at runtime.
datas += collect_data_files("trafilatura")
# Slack SDK ships a `version.py` and `data/` files referenced at runtime.
datas += collect_data_files("slack_sdk")
# cron_descriptor includes locale data.
datas += collect_data_files("cron_descriptor")

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
