"""What the frozen `personalclaw-backend` bundle must carry, DERIVED rather than listed.

`personalclaw-backend.spec` used to hand-transcribe the wheel's package-data globs into its
own `datas` list, with a comment saying so ("Replicate the package-data globs from
pyproject.toml"). A transcription of a list that grows is a list that rots, and on
2026-09-23 the rot was measured from the owner's own macOS install: the shipped
`.app` had no `personalclaw/agents/runner_catalog.json` and no
`personalclaw/tool_providers/rules_builtin.json`, so the agent-runner catalogue and the
builtin tool-projection rule pack were both unavailable in the installed product while
being present in every checkout. Eleven of pyproject's thirty globs had never been copied
across, and three of the spec's own patterns named paths that do not exist
(`eval/scenarios`, `slack-manifest.yaml`, `scripts`) — silently swallowed by an
`os.path.exists` filter, so a dead pattern and a live one looked identical.

So the spec no longer keeps its own copy. This module derives the bundle's data payload
from `[tool.setuptools.package-data]` — the SAME declaration the wheel is built from — and
derives the dynamically-imported module lists from the tree. Adding a data file or an SDK
submodule now reaches the frozen bundle by the act of declaring it for the wheel; nothing
has to be remembered twice. The same holds for every module core loads by its name
(:func:`by_name_modules`): naming one is what carries it.

**Why a plain stdlib module and not PyInstaller hooks.** `tests/test_backend_bundle_manifest.py`
asserts this derivation is COMPLETE, and that assertion has to run in the ordinary test
environment, which has no PyInstaller (CI installs it only in the two `release.yml` desktop
jobs, into a throwaway venv). A `collect_data_files`/`collect_submodules` formulation would
push the whole contract behind a dependency the suite cannot import, which is how this class
of defect stayed invisible in the first place: every existing test runs against the source
tree, where the missing files are on disk. Third-party collection (`openai`, `trafilatura`,
`sqlite_vec`, …) stays in the spec, where PyInstaller's knowledge of site-packages belongs.

Imported by the spec via `importlib.util.spec_from_file_location`, the same way
`tests/test_verify_wheel_contract.py` loads `scripts/verify_wheel.py`.
"""

from __future__ import annotations

import ast
import glob
import json
import os
import sys
import tomllib
from pathlib import Path

#: The import package the bundle mirrors.
PACKAGE = "personalclaw"

#: Where `PACKAGE` lives in the repository.
SRC_PREFIX = f"src/{PACKAGE}"

#: Data the bundle needs that is NOT package data, as `(source, bundle destination)`.
#:
#: `web/dist` is the built React SPA. It is not declared in `[tool.setuptools.package-data]`
#: because the wheel gets it a different way — `make web-build` symlinks
#: `src/personalclaw/static/dist` at it before the sdist/wheel is cut — so it is the one
#: entry that genuinely has to be named here. Destination mirrors that symlink so
#: `dashboard.handlers.core.index` resolves the SPA identically frozen or installed.
EXTRA_DATAS: tuple[tuple[str, str], ...] = (("web/dist", f"{PACKAGE}/static/dist"),)

#: The one package-data glob allowed to match nothing, and why.
#:
#: `trusted_keys/*.pub` forward-declares the first-party signing trust store. No
#: `.pub` is committed — the public half of the release key is not in the tree — so the
#: glob is vacuous today and must stay declared, because a wheel without it would ship a
#: verifier that refuses every signed bundle as "unknown key". Every OTHER glob matching
#: nothing is a typo of the `eval/scenarios` kind and reds
#: `tests/test_backend_bundle_manifest.py`.
FORWARD_DECLARED_GLOBS: frozenset[str] = frozenset({"trusted_keys/*.pub"})

#: Files under the package tree that are deliberately NOT shipped, each with the reason. Kept as
#: narrow as the exemption above: every entry is read by a contributor or by dev tooling and never
#: by the product, and `tests/test_backend_bundle_manifest.py` pins the set by value, so adding one
#: is a visible edit there.
UNSHIPPED_FILES: dict[str, str] = {
    "trusted_keys/README.md": (
        "authoring prose for the empty trust store, read by a contributor adding a key"
    ),
    "sdk/signatures.json": (
        "the reviewed snapshot of the published SDK surface: scripts/sdk_signature_snapshot.py "
        "writes it, and tests/test_sdk_signature_snapshot.py and scripts/apps_sdk_contract.py "
        "compare against it. It sits beside the SDK because scripts/ci_touches_sdk.py runs the "
        "apps' contract check on any change under sdk/, and nothing in the product reads it"
    ),
}


def repo_root() -> Path:
    """The repository root — the directory holding `pyproject.toml`."""
    return Path(__file__).resolve().parents[1]


def package_data_globs(root: Path | None = None) -> list[str]:
    """`[tool.setuptools.package-data].personalclaw`, verbatim and in declaration order."""
    root = root or repo_root()
    doc = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    globs = doc["tool"]["setuptools"]["package-data"][PACKAGE]
    return [str(g) for g in globs]


def _matches(root: Path, pattern: str) -> list[str]:
    """Every FILE under the package tree matching one package-data *pattern*.

    Paths come back repo-relative with forward slashes, sorted, so the derivation is
    deterministic and a spec diff is readable. `recursive=True` is what makes `**`
    (`tests_fixtures/**/*`, `packs/bundled/**/*.json`) reach past one level; directory
    matches are dropped because PyInstaller wants files.
    """
    hits = glob.glob(str(root / SRC_PREFIX / pattern), recursive=True)
    rel = []
    for hit in hits:
        path = Path(hit)
        if not path.is_file():
            continue
        rel.append(path.relative_to(root).as_posix())
    return sorted(rel)


def package_data_datas(root: Path | None = None) -> list[tuple[str, str]]:
    """One `(source, destination directory)` pair per package-data file, deduplicated.

    Destinations mirror the import package (`personalclaw/agents`, not
    `src/personalclaw/agents`) so a frozen read resolves data exactly where the installed
    wheel puts it. Emitting one entry per FILE rather than per directory is deliberate:
    the previous per-directory copies also swept `.py` sources into `datas` — fifteen
    `personalclaw.config.*` / `personalclaw.*.bundled` modules were shipped twice, once as
    modules and once as data — and, worse, a directory copy cannot be checked against the
    wheel's declaration, which is the whole point of deriving it.
    """
    root = root or repo_root()
    pairs: dict[str, str] = {}
    for pattern in package_data_globs(root):
        for rel in _matches(root, pattern):
            dest = Path(rel).relative_to(SRC_PREFIX).parent
            pairs[rel] = (Path(PACKAGE) / dest).as_posix()
    return sorted(pairs.items())


def bundle_datas(root: Path | None = None) -> list[tuple[str, str]]:
    """The spec's complete `datas` for FIRST-PARTY content.

    Package data plus :data:`EXTRA_DATAS`. Entries whose source is absent are dropped, so
    a build that has not run `make web-build` yet still produces a bundle (without the SPA)
    instead of failing inside PyInstaller — but note that nothing here is allowed to be
    *permanently* absent: `tests/test_backend_bundle_manifest.py` asserts every declared
    glob matches something, which is the check the old `os.path.exists` filter replaced
    with silence.
    """
    root = root or repo_root()
    out = package_data_datas(root)
    for src, dest in EXTRA_DATAS:
        if (root / src).exists():
            out.append((src, dest))
    return out


def sdk_submodules(root: Path | None = None) -> list[str]:
    """Every `personalclaw.sdk.*` module name, plus the package itself.

    An app imports core ONLY through `personalclaw.sdk.*` (the provider-boundary contract),
    and `providers.registry` resolves that import at ENABLE time via importlib — so
    PyInstaller's static analysis never sees any of it. The packaged app therefore failed to
    enable every extension it shipped: `brave-search` wanted `sdk.search`,
    `voice-clone-tts` wanted `sdk.tts`, `telegram-channel` wanted `sdk.channel`,
    `discord-channel` wanted `sdk.trigger_source`. Enumerating the directory rather than
    naming those four is the point: the fifth extension to ship must not hit the same wall.
    """
    root = root or repo_root()
    sdk_dir = root / SRC_PREFIX / "sdk"
    names = [f"{PACKAGE}.sdk"]
    for path in sorted(sdk_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        names.append(f"{PACKAGE}.sdk.{path.stem}")
    return names


def package_modules(root: Path | None = None) -> dict[str, bool]:
    """Every module of the package by its dotted name, and whether it is a package.

    Read from the files under the package tree, never imported: the spec runs this outside the
    package, and importing it would read a home. A folder whose name is not an identifier holds
    data, not modules: a native app's own folder (``apps/native/personalclaw-ui-docs``), whose
    Python files ship as package data and are loaded by their path (:func:`path_loaded_imports`).
    """
    root = root or repo_root()
    src = root / "src"
    out: dict[str, bool] = {}
    for path in (root / SRC_PREFIX).rglob("*.py"):
        parts = list(path.relative_to(src).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        if all(part.isidentifier() for part in parts):
            out[".".join(parts)] = is_package
    return out


def referenced_module(text: str, modules: dict[str, bool]) -> str | None:
    """The module of the package *text* names, as its dotted name or as a ``module:attr``
    reference to it (``personalclaw.tool_providers.registry:create_memory_provider``), or None."""
    name, sep, attr = text.partition(":")
    if sep and not attr.isidentifier():
        return None
    return name if name in modules else None


def formatted_package(node: ast.JoinedStr, modules: dict[str, bool]) -> str | None:
    """The package a name formatted from its prefix loads a module of
    (``f"personalclaw.documents.writers.{module}"``), or None."""
    head = node.values[0] if node.values else None
    if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
        return None
    prefix = head.value
    if prefix.endswith(".") and modules.get(prefix[:-1]):
        return prefix[:-1]
    return None


def package_family(package: str, modules: dict[str, bool]) -> set[str]:
    """*package* and every module under it."""
    return {name for name in modules if name == package or name.startswith(f"{package}.")}


def _strings(value: object) -> list[str]:
    """Every string in a parsed JSON document."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def native_manifests(root: Path | None = None) -> list[Path]:
    """Every native app's ``app.json``."""
    root = root or repo_root()
    return sorted((root / SRC_PREFIX / "apps" / "native").glob("*/app.json"))


def entry_point_modules(root: Path | None = None) -> set[str]:
    """The module of every entry point and console script ``pyproject.toml`` declares, which
    ``importlib.metadata`` loads by name (``provider_registry.discover_providers``)."""
    root = root or repo_root()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    groups = [project.get("scripts", {}), *project.get("entry-points", {}).values()]
    return {str(target).partition(":")[0].strip() for group in groups for target in group.values()}


def by_name_modules(root: Path | None = None) -> list[str]:
    """Every module of the package core loads by its NAME, which the bundle therefore carries.

    The analysis follows import statements, and core reaches some of its own modules by name:
    ``importlib.import_module`` and ``__import__`` (the agent's memory, automation, prompt and
    subagent tools, the document writers), ``importlib.resources.files`` (the bundled skills, the
    workflow template library and its shared blocks, the route reference), a ``module:factory``
    reference (every native app's provider), an entry point, and the child modules the bundle's
    entry runs as ``-m <module>``. None of them reached the analysis, so the desktop app's agent
    had no memory, automation, prompt or subagent tools, the tool server agent CLIs run exited at
    its first tool listing, no document could be written, and the template library was empty.

    Derived from the tree, never listed, so naming a module is what carries it:

    * every string in the package's source that is a module's dotted name or a ``module:attr``
      reference to one, wherever a load takes it from (a constant, a table, a keyword argument);
    * every name formatted from a package's prefix: that package and every module under it;
    * every string in a native app's ``app.json`` that references a module of the package (a
      factory in the app's own folder is package data instead, ``apps/native/*/*.py``);
    * the entry points ``pyproject.toml`` declares.

    It is a superset of the loads, deliberately: a string that names a module costs the bundle
    nothing it does not carry already, and telling a load from a mention would take the data flow
    a scan cannot see. Every name is a module of the package, so the build never looks for one
    that does not exist. ``tests/test_by_name_load_census.py`` holds every load site to it.
    """
    root = root or repo_root()
    modules = package_modules(root)
    named: set[str] = set()
    for path in sorted((root / SRC_PREFIX).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                module = referenced_module(node.value, modules)
                if module:
                    named.add(module)
            elif isinstance(node, ast.JoinedStr):
                package = formatted_package(node, modules)
                if package:
                    named |= package_family(package, modules)
    for manifest in native_manifests(root):
        for text in _strings(json.loads(manifest.read_text(encoding="utf-8"))):
            module = referenced_module(text, modules)
            if module:
                named.add(module)
    named |= {module for module in entry_point_modules(root) if module in modules}
    return sorted(named)


def native_app_files(root: Path | None = None) -> list[Path]:
    """The Python files in the native apps' own folders that the loader reads by their path: each
    manifest's factory module in the app's folder, and the app's own modules those import."""
    root = root or repo_root()
    todo: list[Path] = []
    for manifest in native_manifests(root):
        for text in _strings(json.loads(manifest.read_text(encoding="utf-8"))):
            module, sep, attr = text.partition(":")
            if sep and attr.isidentifier() and module:
                file = manifest.parent / (module.replace(".", "/") + ".py")
                if file.is_file():
                    todo.append(file)
    seen: list[Path] = []
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.append(path)
        for name in _absolute_imports(path):
            own = _app_module_file(path.parent, name)
            if own is not None:
                todo.append(own)
    return sorted(seen)


def _absolute_imports(path: Path) -> list[str]:
    """The module every absolute import in *path* names (``from a.b import c`` names ``a.b``)."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def _app_module_file(app_dir: Path, name: str) -> Path | None:
    """The file in *app_dir* the import of *name* reads, when *name* is the app's own module."""
    base = app_dir / name.replace(".", "/")
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def path_loaded_imports(root: Path | None = None) -> list[str]:
    """What the native apps' own Python files import, which the bundle carries for them.

    A native app may ship its provider in its own folder (``provider:create_provider``): the file
    ships as package data and the loader reads it by its path, so the analysis never reads it, and
    whatever it imports (the SDK, core's own third-party dependencies) reached the bundle only when
    something else imported it too. Every absolute import of each such file is named here, except
    the app's own modules, which ship beside it as data, and the standard library's: some of its
    names are aliases no file holds (``collections.abc``), which the analysis cannot find by name,
    and ``tests/test_by_name_load_census.py`` holds each one to a module the package imports, which
    the analysis carries.
    """
    root = root or repo_root()
    names: set[str] = set()
    for path in native_app_files(root):
        names |= {
            name
            for name in _absolute_imports(path)
            if _app_module_file(path.parent, name) is None
            and name.split(".", 1)[0] not in sys.stdlib_module_names
        }
    return sorted(names)


def _top_level(name: str) -> str:
    """The import package a bundled module or extension belongs to: ``openai`` for
    ``openai.types.chat``, ``numpy`` for ``numpy/_core/_multiarray_umath.cpython-313-darwin.so``."""
    return name.replace("\\", "/").split("/", 1)[0].split(".", 1)[0]


def metadata_datas(modules: list[str]) -> list[tuple[str, str, str]]:
    """The installed metadata of every distribution whose packages the bundle carries, as the
    ``(destination, source, "DATA")`` entries the spec adds to the analysis.

    *modules* are what the analysis put in the bundle: its pure modules' names and its extension
    modules' paths. Each import package among them is mapped to the distribution that installed it
    (``importlib.metadata.packages_distributions``), and that distribution's ``.dist-info`` files
    are carried, so ``importlib.metadata`` in the bundle describes exactly what the bundle can
    import. Without them it described a dozen distributions, and an app's packages were judged
    missing when the bundle carried them (every model app's ``openai``), so its install asked for
    a pip the desktop app does not have. Derived from the analysis, never listed: a distribution
    named but not bundled would read as installed while its import failed.
    """
    import importlib.metadata

    tops = {_top_level(name) for name in modules}
    distributions: set[str] = set()
    for top, names in importlib.metadata.packages_distributions().items():
        if top in tops:
            distributions.update(names)
    out: dict[str, tuple[str, str, str]] = {}
    for name in sorted(distributions):
        dist = importlib.metadata.distribution(name)
        for file in dist.files or []:
            rel = Path(file)
            source = Path(str(dist.locate_file(file)))
            if rel.parts and rel.parts[0].endswith(".dist-info") and source.is_file():
                out[rel.as_posix()] = (rel.as_posix(), str(source), "DATA")
    return sorted(out.values())


def undeclared_package_files(root: Path | None = None) -> list[str]:
    """Non-Python files under the package tree that NO package-data glob carries.

    The rail that makes the next omission loud. A `.json`/`.md`/`.yaml` beside a module is
    invisible to both packaging surfaces unless declared, and this is the shape every
    finding in the 2026-09-23 set took: the file was in the checkout, the suite read it
    from the checkout, and the artefact did not have it. Python sources are excluded because
    PyInstaller carries them as MODULES and setuptools carries them implicitly;
    :data:`UNSHIPPED_FILES` is excluded by name.
    """
    root = root or repo_root()
    declared = {src for src, _ in package_data_datas(root)}
    tree = root / SRC_PREFIX
    out = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel in declared or name.endswith((".py", ".pyc", ".pyi")):
                continue
            if path.relative_to(tree).as_posix() in UNSHIPPED_FILES:
                continue
            out.append(rel)
    return sorted(out)


def vacuous_globs(root: Path | None = None) -> list[str]:
    """Declared package-data globs that match no file, minus the forward-declared one."""
    root = root or repo_root()
    return [
        pattern
        for pattern in package_data_globs(root)
        if pattern not in FORWARD_DECLARED_GLOBS and not _matches(root, pattern)
    ]
