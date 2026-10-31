"""What ``test_apps_import_boundary.py`` shares with the suite's other tests.

``_app_roots``, and :func:`first_party_app` for a test that needs one of the first-party apps.

Kept here, beside the suite, so a test that is not a rail can import them without importing
a rail (rails live in ``tests/rails/``, which only the rails job collects)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


#: Apps that ship INSIDE this repo. Always present in any clone, which is what keeps this rail
#: from being a no-op — see the skip note below.
_BUNDLED_APPS = _REPO_ROOT / "src" / "personalclaw" / "apps" / "native"


#: How far up to look for the workspace-level apps clone. Four is the measured depth from a
#: linked worktree (``<name>`` → ``.worktrees`` → checkout → workspace); five leaves one
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
    live in, and this project's parallel branches each run from a linked worktree at
    ``<checkout>/.worktrees/<name>``; there ``_REPO_ROOT.parent`` is ``.worktrees``, so
    neither sibling spelling exists and the scan silently fell back to the bundled apps
    alone. **Measured: 2 files scanned, against 177 in the sibling clone** — and
    ``test_the_lint_has_something_to_lint`` stayed green, because a non-empty assertion is
    satisfied by 1.1% of the population just as well as by all of it.

    So the rail that had "never run, anywhere" was, after its fix, running at 1% for every
    linked worktree in the project. Hence the search is by ancestor walk with a positive proof
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


#: Where the first-party apps are, as the gateway reads it too: a checkout of PersonalClawApps.
FIRST_PARTY_APPS_DIR = "PERSONALCLAW_FIRST_PARTY_APPS_DIR"
#: Set where a run has the apps checked out (the merge tier's ``apps-contract`` job): a test that
#: needs a first-party app then fails when it cannot find one, instead of skipping.
REQUIRE_FIRST_PARTY_APPS = "PERSONALCLAW_REQUIRE_FIRST_PARTY_APPS"


def first_party_apps_dir() -> Path | None:
    """The first-party apps checkout: :data:`FIRST_PARTY_APPS_DIR` when it names one, else the
    nearest workspace-level clone (:func:`_sibling_apps_root`), else ``None``."""
    env = os.environ.get(FIRST_PARTY_APPS_DIR, "").strip()
    if env and _holds_app_bundles(Path(env).expanduser()):
        return Path(env).expanduser().resolve()
    return _sibling_apps_root()


def first_party_app(name: str, *, module_level: bool = False) -> Path:
    """The directory of the first-party app *name*.

    A core clone has no apps checkout, so a test that reads an app skips there. Read that way on
    every CI run, such a test never ran anywhere (#3918), so the job that checks the apps out sets
    :data:`REQUIRE_FIRST_PARTY_APPS`, and there a missing app fails the test instead."""
    root = first_party_apps_dir()
    if root is not None and (root / name / "app.json").is_file():
        return root / name
    why = (
        f"the first-party app {name!r} is not here: no apps checkout was found "
        f"({FIRST_PARTY_APPS_DIR}, or a PersonalClawApps clone beside this one)"
    )
    skip_unless_required(why, module_level=module_level)
    raise AssertionError("unreachable")  # pragma: no cover


def skip_unless_required(why: str, *, module_level: bool = False) -> None:
    """Skip a test that needs the first-party apps with *why*; fail it with *why* where
    :data:`REQUIRE_FIRST_PARTY_APPS` says the apps are checked out."""
    if os.environ.get(REQUIRE_FIRST_PARTY_APPS) == "1":
        pytest.fail(f"{why}, and {REQUIRE_FIRST_PARTY_APPS}=1 requires it", pytrace=False)
    pytest.skip(why, allow_module_level=module_level)
