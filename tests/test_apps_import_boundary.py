"""Import-lint: an installed APP may only reach into core through ``personalclaw.sdk.*``.

The core/app boundary (workspace-core-app-split §3) is a PUBLISHED SDK: apps import the
stable ``personalclaw.sdk`` facade, never deep core internals (``personalclaw.dashboard``,
``personalclaw.agents.native``, ``personalclaw.tool_providers.projection``, …). This test
statically scans every ``apps/<name>/*.py`` and fails on any ``import personalclaw.X`` /
``from personalclaw.X import`` where ``X`` is not ``sdk`` (or ``sdk.*``).

Rationale: if an app reaches past the SDK, core can't evolve its internals without
breaking installed apps — the whole point of the separation. When a genuinely-needed
symbol isn't on the SDK yet, the fix is to PROMOTE it to a ``personalclaw.sdk`` submodule
(as the model/media/tool/acp waves did), not to reach around the boundary.

Test files (``test_*.py``) are exempt: they legitimately import core test helpers +
patch core module paths (they run in the dev tree, not as an installed app).
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Apps that ship INSIDE this repo. Always present in any clone, which is what keeps this rail
#: from being a no-op — see the skip note below.
_BUNDLED_APPS = _REPO_ROOT / "src" / "personalclaw" / "apps" / "native"

#: Path segments that are never app source. ``.worktrees`` and ``.git`` are the two that
#: matter and the two that were missing: the apps clone hosts other lanes' linked worktrees
#: under ``PersonalClawApps/.worktrees/<lane>/``, and ``rglob`` walks straight into them.
#: Measured on the main checkout: **532** files matched, **355 of them (67%) inside two other
#: lanes' worktrees** — so this rail's verdict was a function of other people's uncommitted
#: work, and a violation in scratch state reded a lane that had never opened the file.
_NOT_APP_SOURCE = frozenset({"__pycache__", ".venv", "node_modules", ".worktrees", ".git"})

#: How far up to look for the workspace-level apps clone. Four is the measured depth from a
#: linked worktree (``<lane>`` → ``.worktrees`` → checkout → workspace); five leaves one
#: step of slack without wandering far enough to meet an unrelated ``apps/`` directory.
_ANCESTOR_SEARCH_DEPTH = 5


def _holds_app_bundles(path: Path) -> bool:
    """Is *path* an apps ROOT — i.e. does a child directory carry an ``app.json``?

    The walk below searches by directory NAME, and ``apps`` is a name almost anything can
    have. Requiring the manifest that defines an app bundle is what makes a hit proof rather
    than a guess, so the search can look upward without risking an unrelated directory.
    """
    if not path.is_dir():
        return False
    try:
        children = list(path.iterdir())
    except OSError:  # pragma: no cover - unreadable dir is "not an apps root"
        return False
    return any((child / "app.json").is_file() for child in children if child.is_dir())


def _sibling_apps_root() -> Path | None:
    """The nearest workspace-level apps clone, searched UP the ancestor chain.

    🔴 The previous fix for issue 1777 was still wrong from a git worktree, in the same way
    for a subtler reason. It resolved the workspace as ``_REPO_ROOT.parent`` — correct from
    the main checkout, where that IS the workspace. But ``_REPO_ROOT`` is the tree the tests
    live in, and this project's lanes each run from a linked worktree at
    ``<checkout>/.worktrees/<lane>``; there ``_REPO_ROOT.parent`` is ``.worktrees``, so
    neither sibling spelling exists and the scan silently fell back to the bundled apps
    alone. **Measured: 2 files scanned, against 177 in the sibling clone** — and
    ``test_the_lint_has_something_to_lint`` stayed green, because a non-empty assertion is
    satisfied by 1.1% of the population just as well as by all of it.

    So the rail that had "never run, anywhere" was, after its fix, running at 1% for every
    lane in the project. Hence the search is by ancestor walk with a positive proof
    (``_holds_app_bundles``) instead of one hardcoded level: the depth from the tests to the
    workspace is a property of how the suite was invoked, which no fixed offset can encode.

    A CI clone still resolves nothing here — it has no sibling apps checkout, by design —
    and falls back to the bundled apps, which is why those remain an unconditional root.
    """
    for ancestor in [_REPO_ROOT, *_REPO_ROOT.parents][:_ANCESTOR_SEARCH_DEPTH]:
        for name in ("PersonalClawApps", "apps"):
            candidate = ancestor / name
            if _holds_app_bundles(candidate):
                return candidate
    return None


def _app_roots() -> list[Path]:
    """Every directory holding app source this lint should scan.

    The one lint enforcing the provider-agnostic-core tenet ("apps import core only via
    ``personalclaw.sdk.*``"). There is no second copy of it in the apps repo — checked — so
    this is the whole enforcement, which is why both resolution bugs above mattered.

    Roots, in order: the env var the gateway already honours, the nearest workspace-level
    apps clone, and the bundled apps. Duplicates collapse, so a workspace where two of these
    point at one tree is scanned once.
    """
    roots: list[Path] = []
    env = os.environ.get("PERSONALCLAW_FIRST_PARTY_APPS_DIR", "").strip()
    if env:
        roots.append(Path(env).expanduser())
    sibling = _sibling_apps_root()
    if sibling is not None:
        roots.append(sibling)
    roots.append(_BUNDLED_APPS)

    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        try:
            key = r.resolve()
        except OSError:
            key = r
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _app_source_files() -> list[tuple[Path, Path]]:
    """``(root, file)`` for every app source file under every resolved root.

    The exclusion is tested against the path RELATIVE TO ITS ROOT, never the absolute path.
    Matching absolute parts looks equivalent and silently drops the bundled apps whenever the
    suite runs from a linked worktree, because ``.worktrees`` is then a segment of their own
    location — measured: the bundled root resolved and contributed **0** files, which in a
    clone with no sibling apps checkout would empty the scan and red the vacuity floor for a
    reason having nothing to do with apps.
    """
    out: list[tuple[Path, Path]] = []
    for root in _app_roots():
        for p in sorted(root.rglob("*.py")):
            if _NOT_APP_SOURCE & set(p.relative_to(root).parts):
                continue
            if p.name.startswith("test_"):  # test files may import core helpers
                continue
            out.append((root, p))
    return out


def _offending_imports(path: Path) -> list[str]:
    """Return ``personalclaw.<non-sdk>`` module paths imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []

    def _check(mod: str | None) -> None:
        if not mod or not mod.startswith("personalclaw"):
            return
        parts = mod.split(".")
        # allow `personalclaw.sdk` and `personalclaw.sdk.<anything>`
        if len(parts) >= 2 and parts[1] == "sdk":
            return
        bad.append(mod)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # ignore relative imports (node.level > 0 → app-local siblings)
            if node.level == 0:
                _check(node.module)
    return bad


def test_the_lint_has_something_to_lint():
    """The vacuity floor, and the reason this rail was worthless for so long.

    A scan of zero files passes every assertion below. The bundled apps ship in-repo, so a
    correctly-resolved root list can never be empty — if this fails, the resolution is broken
    again rather than the codebase being clean.
    """
    roots = _app_roots()
    assert roots, "no app root resolved — even the in-repo bundled apps were not found"
    assert _app_source_files(), f"roots resolved but hold no app source: {roots}"


def test_no_scanned_file_comes_from_another_lanes_worktree():
    """The over-scan half: the apps clone hosts linked worktrees, and ``rglob`` entered them.

    Not folded into the floor above because it is the opposite failure. That one catches a
    scan that sees too little; this one catches a scan that sees other people's uncommitted
    work — 355 of 532 matched files on the measured run — which makes this rail's verdict
    non-deterministic across lanes and reds a branch for a file it never opened.
    """
    intruders = [
        str(f)
        for root, f in _app_source_files()
        if ".worktrees" in f.relative_to(root).parts  # root-relative: see _app_source_files
    ]
    assert not intruders, (
        f"{len(intruders)} scanned files live inside a linked worktree, so this rail is "
        f"reading another checkout's working tree: {intruders[:5]}"
    )


def test_the_apps_root_resolves_from_a_linked_worktree_layout(tmp_path, monkeypatch):
    """The resolution regression, pinned on a layout instead of on this machine.

    `_REPO_ROOT.parent` is the workspace from the main checkout and `.worktrees` from a lane,
    which is why the bug was invisible to whoever ran the suite from the checkout. Building
    both layouts makes the difference the assertion rather than the environment.

    `_holds_app_bundles` is the unit under test — it is what lets the walk go upward safely —
    so the fake clone carries a real `app.json`, and a same-named decoy directory without one
    must NOT match.
    """
    workspace = tmp_path / "ws"
    lane = workspace / "PersonalClaw" / ".worktrees" / "lane-x"
    lane.mkdir(parents=True)
    apps = workspace / "PersonalClawApps"
    (apps / "demo-app").mkdir(parents=True)
    (apps / "demo-app" / "app.json").write_text("{}", encoding="utf-8")

    assert _holds_app_bundles(apps)
    decoy = workspace / "apps"
    decoy.mkdir()
    (decoy / "not-an-app").mkdir()
    assert not _holds_app_bundles(decoy), "a directory with no app.json child is not an apps root"

    for tree_root in (lane, workspace / "PersonalClaw"):
        monkeypatch.setattr(sys.modules[__name__], "_REPO_ROOT", tree_root)
        assert _sibling_apps_root() == apps, (
            f"from {tree_root.relative_to(workspace)} the walk did not find the apps clone "
            f"(this is the 2-files-instead-of-179 bug)"
        )


def test_apps_only_import_sdk():
    files = _app_source_files()
    assert files, "no app source files found — see test_the_lint_has_something_to_lint"
    violations: dict[str, list[str]] = {}
    for root, f in files:
        bad = _offending_imports(f)
        if bad:
            try:
                label = str(f.relative_to(root.parent))
            except ValueError:
                label = str(f)
            violations[label] = sorted(set(bad))
    assert not violations, (
        "Apps must import core only via personalclaw.sdk.* — found deep-core imports:\n"
        + "\n".join(f"  {f}: {mods}" for f, mods in sorted(violations.items()))
        + "\nPromote the needed symbol to a personalclaw.sdk submodule instead of reaching around the boundary."  # noqa: E501
    )


@pytest.mark.parametrize("app_file", [str(f) for _root, f in _app_source_files()])
def test_each_app_file_sdk_clean(app_file):
    """Per-file view (so a failure names the exact app file)."""
    bad = _offending_imports(Path(app_file))
    assert not bad, f"{app_file} imports non-SDK core: {sorted(set(bad))}"
