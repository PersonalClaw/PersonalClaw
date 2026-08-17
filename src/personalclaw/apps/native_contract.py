"""The NATIVE CAPABILITY CONTRACT (APE-5) — how a bundled app owns its provider code.

A **bundled** (native) app lives at ``personalclaw/apps/native/<name>/`` and ships inside
the core distribution. Historically every one of them was ``app.json``-only: the manifest's
``provider.implementation`` named a *core* dotted path
(``personalclaw.tasks.native:create_provider``), so the bundle declared a capability core
implemented. Growing that capability meant editing core — the one thing the app platform
exists to avoid.

This module is the contract that removes that asymmetry:

* A bundled app MAY ship its own Python modules **in its own directory** and point
  ``provider.implementation`` at a bundle-relative module (``provider:create_provider``).
  A module path with no dot is bundle-relative; a dotted one is still a core/package path,
  so the 26 pre-existing bundles are unaffected.
* Those modules are loaded from the bundle dir under a **namespaced** module name
  (:func:`namespaced_module_name`), so two bundles that both ship ``provider.py`` cannot
  collide in ``sys.modules`` — the collision an installed app was already protected from
  and a bundled one was not.
* **Allowed imports = the published SDK, and nothing else.** A bundled app's module may
  import ``personalclaw.sdk.*``, the standard library, and core's own third-party
  dependencies. It may NOT import any other ``personalclaw.*`` module. This is the same
  rule ``tests/test_apps_import_boundary.py`` applies to installed apps —
  deliberately the SAME rule, not a second, narrower one: a bundled app is reached
  through the same ``providers/loader.py`` seam, is registered through the same typed
  handler, and is upgraded by the same release, so a separate "native-only" allowlist
  would be a second boundary to keep in step for no gain. What *is* native-specific is
  documented as caveats rather than import bans (see the module list in
  ``docs/architecture/app-platform.md``): a bundled module runs IN-PROCESS, so it never
  receives the per-app backend subprocess environment (``sdk.util.shared_app_data_dir``
  is always ``None`` for it), and it cannot declare its own dependencies (the manifest
  ``dependencies`` block installs into an app venv, which a bundled module does not get).

:func:`contract_violations` is the machine-checkable half; ``tests/
test_native_capability_contract.py`` runs it over every bundled module and carries the
vacuity floor (at least one bundle must actually ship a module, or the rail measures
nothing — the failure mode the installed-app rail has, since it silently skips whenever
the workspace ``apps/`` dir is absent).
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

# The bundled-app root. ``providers/loader.py`` exposes the same directory as
# ``BUNDLED_DIR``; this module is the lower layer (``apps/`` knows nothing about
# ``providers/``), so the constant lives here and the loader keeps its alias.
NATIVE_DIR = Path(__file__).resolve().parent / "native"

# The one import prefix a bundled app's module may reach for.
SDK_PACKAGE = "personalclaw.sdk"

_CORE_PREFIX = "personalclaw"


def native_bundle_dirs() -> list[Path]:
    """Every bundled-app directory (one per ``app.json``), sorted by name."""
    if not NATIVE_DIR.is_dir():
        return []
    return sorted(p for p in NATIVE_DIR.iterdir() if p.is_dir() and (p / "app.json").is_file())


def bundle_modules(bundle_dir: Path) -> list[Path]:
    """Every Python module a bundled app ships (``test_*.py`` excluded, like the
    installed-app rail: a bundle's tests run in the dev tree, not as loaded app code)."""
    if not bundle_dir.is_dir():
        return []
    return sorted(
        p
        for p in bundle_dir.rglob("*.py")
        if "__pycache__" not in p.parts and not p.name.startswith("test_")
    )


def is_bundle_relative(module_path: str) -> bool:
    """True when an ``implementation`` module path names a BUNDLE-LOCAL module.

    ``"provider"`` → bundle-local; ``"personalclaw.tasks.native"`` → a core package path.
    The dot is the discriminator, which is why a bundled app's own module must be
    top-level in its dir (``provider.py``, not ``pkg/provider.py``) — the same shape an
    installed app already uses.
    """
    return bool(module_path) and "." not in module_path


def bundle_module_file(ext_dir: Path | None, module_path: str) -> Path | None:
    """The file a bundle-relative ``module_path`` resolves to inside ``ext_dir``, or None.

    ``None`` means "not a bundle-local module" — the caller falls back to importing
    ``module_path`` as a real dotted package path.
    """
    if ext_dir is None or not module_path:
        return None
    candidate = ext_dir / (module_path.replace(".", "/") + ".py")
    return candidate if candidate.is_file() else None


def namespaced_module_name(app_name: str, module_path: str) -> str:
    """The ``sys.modules`` key a bundled/installed app's module is loaded under.

    Two apps commonly ship the same bare module name (``provider``, ``main``); importing
    it as-is means the first app's module wins and the second silently mis-loads. One
    namespacing scheme for both tiers, so there is a single answer to "under what name is
    an app's module registered".
    """
    return f"_pclaw_app_{app_name.replace('-', '_')}__{module_path.replace('.', '_')}"


def load_bundle_module(ext_dir: Path, app_name: str, module_path: str) -> Any:
    """Import an app's own module from its directory, under a namespaced name.

    Cached by that name: ``load_factory`` and ``load_availability`` both resolve the same
    module, and an app read (the extension-list API calls the availability probe) must not
    re-execute app code — re-exec would also give two distinct classes for one provider,
    so an ``isinstance`` across two reads would start failing. A changed module needs a
    gateway restart, which is what the install/update docs already promise.
    """
    unique_name = namespaced_module_name(app_name, module_path)
    cached = sys.modules.get(unique_name)
    if cached is not None:
        return cached
    file_path = bundle_module_file(ext_dir, module_path)
    if file_path is None:
        raise ImportError(f"no bundle-local module {module_path!r} in {ext_dir}")
    spec = importlib.util.spec_from_file_location(unique_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path!r} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    # The app dir goes on sys.path only while the module executes, so its own sibling
    # imports resolve; a permanent entry would let a later bare import pick up an app's
    # module by accident.
    added = str(ext_dir) not in sys.path
    if added:
        sys.path.insert(0, str(ext_dir))
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-executed module must not stay cached, or the next read gets a shell.
        sys.modules.pop(unique_name, None)
        raise
    finally:
        if added and str(ext_dir) in sys.path:
            sys.path.remove(str(ext_dir))
    return module


def contract_violations(path: Path) -> list[str]:
    """Core imports in ``path`` that the contract forbids (empty list = clean).

    Allowed: ``personalclaw.sdk`` / ``personalclaw.sdk.*``, relative imports (a bundle's
    own siblings), and anything outside the ``personalclaw`` namespace.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []

    def _check(mod: str | None) -> None:
        if not mod or not mod.startswith(_CORE_PREFIX):
            return
        parts = mod.split(".")
        if len(parts) >= 2 and parts[1] == "sdk":
            return
        bad.append(mod)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:  # relative import → a sibling inside the bundle
                _check(node.module)
    return sorted(set(bad))


def all_contract_violations() -> dict[str, list[str]]:
    """``{repo-relative module path: forbidden imports}`` across every bundled app."""
    out: dict[str, list[str]] = {}
    for bundle in native_bundle_dirs():
        for module in bundle_modules(bundle):
            bad = contract_violations(module)
            if bad:
                out[f"apps/native/{bundle.name}/{module.relative_to(bundle).as_posix()}"] = bad
    return out
