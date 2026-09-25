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
has to be remembered twice.

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

import glob
import os
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
#: `trusted_keys/*.pub` forward-declares the first-party signing trust store (SH-3). No
#: `.pub` is committed — the public half of the release key is not in the tree — so the
#: glob is vacuous today and must stay declared, because a wheel without it would ship a
#: verifier that refuses every signed bundle as "unknown key". Every OTHER glob matching
#: nothing is a typo of the `eval/scenarios` kind and reds
#: `tests/test_backend_bundle_manifest.py`.
FORWARD_DECLARED_GLOBS: frozenset[str] = frozenset({"trusted_keys/*.pub"})

#: Files under the package tree that are documentation for a forward-declared glob rather
#: than a runtime asset, so they are deliberately not shipped. Kept as narrow as the
#: exemption above: this is authoring prose for the empty trust store, read by a
#: contributor adding a key, never by the product.
UNSHIPPED_DOCS: frozenset[str] = frozenset({"trusted_keys/README.md"})


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


def undeclared_package_files(root: Path | None = None) -> list[str]:
    """Non-Python files under the package tree that NO package-data glob carries.

    The rail that makes the next omission loud. A `.json`/`.md`/`.yaml` beside a module is
    invisible to both packaging surfaces unless declared, and this is the shape every
    finding in the 2026-09-23 set took: the file was in the checkout, the suite read it
    from the checkout, and the artefact did not have it. Python sources are excluded because
    PyInstaller carries them as MODULES and setuptools carries them implicitly;
    :data:`UNSHIPPED_DOCS` is excluded by name.
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
            if path.relative_to(tree).as_posix() in UNSHIPPED_DOCS:
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
