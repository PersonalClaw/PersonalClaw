"""The model-root rail proves it fires (LOCAL-MODEL-MANAGER-V2 SC-10, LMMV-7).

``conftest._forbid_real_model_roots`` wraps every ``local_models/layouts.py`` entry point
for the whole suite so a test cannot reach a real model dir / cache root. A rail that only
ever runs against a tree where it should stay silent is indistinguishable from a rail that
never fires, so this file drives it both directions:

* a ``tmp_path`` root passes straight through, unchanged — the rail is not a tax
* every named real root is REFUSED, including via the fixture-installed wrapper, so the
  bound-model-deletion incident (a real delete against ``~/.cache/huggingface``) cannot be
  written again even by a test that means to

The forbidden-root list is asserted non-empty and asserted to contain the specific roots
the incident touched, so shrinking it to nothing is a visible change rather than a silent
one — a rail matching nothing looks clean.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import real_model_root_guard

from personalclaw.local_models import layouts


def test_the_rail_names_a_non_empty_set_of_real_roots():
    roots = real_model_root_guard.forbidden_roots()
    assert roots, "the model-root rail matches nothing — it would pass on any input"
    home = real_model_root_guard.REAL_HOME
    # The two the incident actually touched must be in the set.
    assert home / ".cache/huggingface" in roots
    assert home / ".personalclaw" in roots
    # And the bare home itself: a sweep of ``~`` is never a legitimate test root.
    assert home in roots


@pytest.mark.parametrize(
    "sub",
    [
        ".personalclaw",
        ".personalclaw/models/whisper",
        ".cache/huggingface",
        ".cache/huggingface/hub/models--openai--whisper-small",
        ".cache/torch/hub",
        ".ollama/models",
    ],
)
def test_a_real_root_is_detected(sub):
    root = real_model_root_guard.REAL_HOME / sub
    assert real_model_root_guard.offending_root(root) is not None
    with pytest.raises(AssertionError, match="REAL model root"):
        real_model_root_guard.assert_safe("delete_all_layouts", root)


def test_a_tilde_spelled_root_is_detected():
    """The exact shape the incident used: an un-expanded ``~/...`` string."""
    with pytest.raises(AssertionError, match="REAL model root"):
        real_model_root_guard.assert_safe("delete_all_layouts", "~/.cache/huggingface")


def test_a_tmp_root_is_allowed(tmp_path):
    assert real_model_root_guard.offending_root(tmp_path) is None
    real_model_root_guard.assert_safe("delete_all_layouts", tmp_path)  # does not raise


def test_an_unparseable_root_is_not_the_rails_business():
    assert real_model_root_guard.offending_root(None) is None


# ── The rail as INSTALLED by the autouse fixture ──────────────────────────────


def test_the_installed_wrapper_refuses_a_real_delete():
    """Driving the real function name through the patched module must raise.

    This is the assertion that makes the incident unreproducible: ``delete_all_layouts``
    is the sweep that removed the bound model, and here it refuses before touching disk.
    """
    real_root = real_model_root_guard.REAL_HOME / ".cache" / "huggingface"
    with pytest.raises(AssertionError, match="bound-model-deletion rail"):
        layouts.delete_all_layouts(real_root, "openai/whisper-small")


def _cache_root_functions() -> set[str]:
    """Public ``layouts`` functions whose FIRST parameter is ``cache_root``, read from SOURCE.

    Derived by parsing the file, never by introspecting the imported module: the autouse
    fixture has already replaced every guarded attribute with ``_guarded(cache_root, ...)``,
    so ``inspect.signature`` reports ``cache_root`` first for anything *already* wrapped and
    a signature-based derivation would confirm itself. Reading the source asks the question
    the rail actually cares about — what entry points EXIST — independently of what the rail
    did to them.
    """
    tree = ast.parse(Path(layouts.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):  # module-private helpers take a path, not a root
            continue
        args = node.args.args
        if args and args[0].arg == "cache_root":
            found.add(node.name)
    return found


def test_the_guarded_set_covers_every_cache_root_entry_point():
    """The rail's population is DERIVED, so a new entry point cannot escape it.

    ``test_every_guarded_entry_point_is_wrapped`` below iterates the *listed* set, which
    proves every listed name is wrapped but says nothing about a name nobody listed — a
    one-sided inventory. A cache-root function added to ``layouts.py`` and left out of
    ``GUARDED_FUNCTIONS`` is exactly the hole the bound-model-deletion rail must not have,
    and it is the hole a listed-set check reports as clean. So compare the list against the
    module's real surface in both directions.
    """
    derived = _cache_root_functions()
    # Vacuity: a failed/empty parse would make the equality below trivially true, including
    # against a GUARDED_FUNCTIONS someone had emptied. Assert the scan really found the
    # surface, and specifically found the sweep the incident ran.
    assert len(derived) >= 7, f"the cache-root scan found only {sorted(derived)}"
    assert "delete_all_layouts" in derived, derived

    listed = set(real_model_root_guard.GUARDED_FUNCTIONS)
    unguarded = derived - listed
    assert not unguarded, (
        f"layouts.{sorted(unguarded)} take a cache_root but are NOT in GUARDED_FUNCTIONS — "
        f"the model-root rail has a hole it reports as clean. Add them there so the autouse "
        f"fixture wraps them (LMMV Success Criterion 10)."
    )
    stale = listed - derived
    assert not stale, (
        f"GUARDED_FUNCTIONS lists {sorted(stale)}, which is no longer a public cache-root "
        f"entry point in layouts.py — a rail entry matching nothing looks clean."
    )


@pytest.mark.parametrize("fn_name", real_model_root_guard.GUARDED_FUNCTIONS)
def test_every_guarded_entry_point_is_wrapped(fn_name):
    """Coverage assertion: each listed entry point really is behind the rail.

    Completeness of the *list itself* is a separate question, answered by
    ``test_the_guarded_set_covers_every_cache_root_entry_point`` — parametrizing over
    ``GUARDED_FUNCTIONS`` here can only ever check the names already in it.
    """
    fn = getattr(layouts, fn_name)
    assert fn.__name__ == "_guarded", f"layouts.{fn_name} is not behind the model-root rail"
    real_root = real_model_root_guard.REAL_HOME / ".cache" / "huggingface"
    with pytest.raises(AssertionError, match="REAL model root"):
        fn(real_root, "some/model") if fn_name != "cleanup_candidates" else fn(real_root)


def test_a_tmp_root_still_reaches_the_real_implementation(tmp_path):
    """The rail must not swallow behaviour: a tmp root produces real answers."""
    model_dir = tmp_path / "models--org--m" / "snapshots" / "abc"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"x" * 128)

    assert layouts.is_downloaded(tmp_path, "org/m") is True
    assert layouts.on_disk_bytes(tmp_path, "org/m") == 128
    assert isinstance(layouts.candidate_paths(tmp_path, "org/m"), list)
    assert layouts.delete_all_layouts(tmp_path, "org/m")
    assert layouts.is_downloaded(tmp_path, "org/m") is False
    assert not any(p.exists() for p in [model_dir])
    # Nothing outside tmp_path was involved.
    assert Path(tmp_path).exists()
