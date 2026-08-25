"""Detection for the model-root rail (LOCAL-MODEL-MANAGER-V2 Success Criterion 10).

The bound-model-deletion incident was a test that ran a real delete against a real cache
root and removed a model the developer had actually downloaded. The fix cannot be "every
test remembers to pass ``tmp_path``" — that is a convention, and the incident happened
under that convention. So the rail is structural: every entry point in
``local_models/layouts.py`` (the ONE seam all download-probing and all deletion goes
through) is wrapped for the whole suite and refuses a real, user-owned model root.

Detection lives here rather than inline in ``conftest.py`` for the reason
``real_home_guard`` gives: a guard that only ever runs against the tree it guards cannot
be distinguished from a guard that never fires. ``tests/test_local_model_root_guard.py``
drives this function against both a real root and a ``tmp_path`` one.

The rail names the roots it forbids instead of forbidding all of ``$HOME``: a developer's
checkout legitimately lives under ``$HOME`` (``~/Projects/...``), so a blanket
home-rejection would fail on a relative path in a normal clone and get switched off. What
it forbids is the set of places a real model actually lives.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The user's real home, captured at import time — BEFORE any fixture repoints
#: ``HOME``/``PERSONALCLAW_HOME``, so a test cannot move the rail out from under itself.
REAL_HOME = Path(os.path.expanduser("~"))

#: Roots that hold REAL downloaded weights / real PersonalClaw state. Relative to the
#: real home, so the rail is meaningful on a machine that has none of them yet.
FORBIDDEN_SUBPATHS: tuple[str, ...] = (
    ".personalclaw",  # the real PersonalClaw home (models/, entity_settings/, …)
    ".cache/huggingface",  # HF hub — faster-whisper, sentence-transformers, pyannote
    ".cache/torch",  # torch.hub checkpoints
    ".cache/whisper",  # openai-whisper's own cache
    ".cache/piper",  # piper voices
    ".ollama",  # ollama's pulled models
    "Library/Caches/huggingface",  # macOS HF cache spelling
    "Library/Application Support/personalclaw",
    ".local/share/personalclaw",
)


def forbidden_roots() -> tuple[Path, ...]:
    """The real model roots, plus the bare home itself (a sweep of ``~`` is never a test)."""
    return (REAL_HOME, *(REAL_HOME / sub for sub in FORBIDDEN_SUBPATHS))


def offending_root(cache_root: object) -> Path | None:
    """The forbidden root ``cache_root`` falls inside, or ``None`` when it is safe.

    ``expanduser`` first (a literal ``"~/.cache/huggingface"`` is the exact shape the
    incident used) and then compare WITHOUT ``resolve()`` against the real roots, plus a
    resolved comparison so a symlinked detour is caught too. Anything unparseable is
    treated as safe — the rail exists to catch real roots, not to police argument types.
    """
    try:
        raw = Path(os.path.expanduser(str(cache_root)))
    except (TypeError, ValueError):
        return None

    # A RELATIVE path is unparseable for this rail's purpose and must not be resolved:
    # `resolve()` would anchor it to the CWD, and on a machine whose CWD sits under a
    # forbidden root (CI runs from `/home/runner/work/...` while REAL_HOME is
    # `/home/runner`) every junk argument would report that root as offending. Measured:
    # `offending_root(None)` builds `Path("None")` and returned `/home/runner` on CI while
    # returning None locally, i.e. the rail's verdict depended on where it was run.
    if not raw.is_absolute():
        return None

    candidates = {raw}
    try:
        candidates.add(raw.resolve())
    except (OSError, RuntimeError):
        pass

    for root in forbidden_roots():
        for candidate in candidates:
            if candidate == root or root in candidate.parents:
                return root
    return None


def assert_safe(function_name: str, cache_root: object) -> None:
    """Raise if ``cache_root`` names a real model root. The message says what to do."""
    root = offending_root(cache_root)
    if root is None:
        return
    raise AssertionError(
        f"layouts.{function_name}() was called with a REAL model root: {cache_root!r} "
        f"(inside {root}). Tests must pass tmp_path — this is the bound-model-deletion "
        f"rail (LMMV Success Criterion 10), not a style preference. If a test genuinely "
        f"needs to exercise root resolution, assert on the returned PATHS instead of "
        f"calling a layouts function against the real root."
    )


#: The UNWRAPPED ``layouts`` functions, recorded by ``conftest._forbid_real_model_roots``
#: as it installs each wrapper. This is exactly the object a module-level
#: ``from personalclaw.local_models.layouts import delete_all_layouts`` captures — the
#: alias is bound at IMPORT time, before any fixture runs, so it never sees the wrapper.
#: Kept here so the rail can be driven against the shape that escapes it instead of only
#: against the shape it catches.
ORIGINALS: dict[str, object] = {}

#: The layouts entry points the rail wraps. Every one takes ``cache_root`` first, and
#: between them they cover every probe and the single deletion sweep.
GUARDED_FUNCTIONS: tuple[str, ...] = (
    "candidate_paths",
    "is_downloaded",
    "downloaded_layouts",
    "on_disk_bytes",
    "delete_all_layouts",
    "cleanup_candidates",
    "reclaimable_bytes",
)

#: The module a guarded name would be imported FROM.
_LAYOUTS_MODULE = "personalclaw.local_models.layouts"


def import_bound_guarded_names(source: str) -> set[str]:
    """Guarded ``layouts`` names ``source`` captures at IMPORT time, if any.

    The rail is installed with ``monkeypatch.setattr(layouts, name, wrapper)``, so it
    intercepts an ATTRIBUTE LOOKUP (``layouts.delete_all_layouts(...)``) and nothing else.
    A module-level ``from personalclaw.local_models.layouts import delete_all_layouts``
    binds the unwrapped function object at collection time — before the autouse fixture has
    run even once — and every later call through that alias bypasses the rail.

    Only import-TIME bindings count. A ``from ... import`` inside a function body executes
    when the function is called, i.e. after the fixture has installed the wrapper, so it
    resolves the guarded attribute and is the safe shape (it is what the production callers
    in ``dashboard/`` and ``local_models/`` already use). Imports nested inside a function
    are therefore skipped, and importing the MODULE (``from ... import layouts``) is fine at
    any depth because the lookup still happens per call.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — the lint gate owns syntax
        return set()

    guarded = set(GUARDED_FUNCTIONS)
    found: set[str] = set()

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # deferred to call time — resolves the wrapper, not the original
            if isinstance(child, ast.ImportFrom) and child.module == _LAYOUTS_MODULE:
                found.update(a.name for a in child.names if a.name in guarded)
            _walk(child)

    _walk(tree)
    return found


def trailing_args(function_name: str) -> tuple[str, ...]:
    """The dummy arguments AFTER ``cache_root``, derived from the live signature.

    The coverage cell used to pass ``(real_root, "some/model")`` to every entry point with
    a single hardcoded name exception, so the two one-argument functions were not both
    covered: ``reclaimable_bytes(cache_root)`` was being called with TWO arguments and the
    cell passed only because the wrapper raises BEFORE forwarding. Measured by neutralizing
    the detection: that cell then failed with ``TypeError: reclaimable_bytes() takes 1
    positional argument but 2 were given`` rather than the ``DID NOT RAISE`` every other
    cell reported — i.e. it was asserting the guard fires, but through a call the real
    function could never accept.

    Deriving the arity from :func:`inspect.signature` of the recorded ORIGINAL means a
    signature change re-shapes the call instead of stranding the cell, and the call the
    rail is proven against is one the real function would actually accept.
    """
    import inspect

    original = ORIGINALS.get(function_name)
    if original is None:  # pragma: no cover — the autouse fixture records every name
        raise AssertionError(
            f"{function_name} has no recorded original; the model-root fixture must run "
            f"before the arity can be derived from the live signature."
        )

    positional = [
        p
        for p in inspect.signature(original).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
    ]
    # ``cache_root`` is supplied by the caller; everything else required gets a dummy.
    return tuple("some/model" for _ in positional[1:])
