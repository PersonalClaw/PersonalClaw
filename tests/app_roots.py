"""What ``test_apps_import_boundary.py`` shares with the suite's other tests.

``_app_roots``.

Kept here, beside the suite, so a test that is not a rail can import them without importing
a rail (rails live in ``tests/rails/``, which only the rails job collects)."""

from __future__ import annotations

import os
from pathlib import Path

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
