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

    The trailing arguments are DERIVED from the live signature
    (``real_model_root_guard.trailing_args``), not hardcoded: two of the seven entry points
    take ``cache_root`` alone, and naming only one of them as the exception meant the other
    was called with an arity the real function cannot accept, passing purely because the
    wrapper raises before it forwards.
    """
    fn = getattr(layouts, fn_name)
    assert fn.__name__ == "_guarded", f"layouts.{fn_name} is not behind the model-root rail"
    real_root = real_model_root_guard.REAL_HOME / ".cache" / "huggingface"
    with pytest.raises(AssertionError, match="REAL model root"):
        fn(real_root, *real_model_root_guard.trailing_args(fn_name))


# ── The rail's one soft edge: it is an attribute lookup deep, not alias deep ───


def test_the_installed_rail_is_only_an_attribute_lookup_deep():
    """State the rail's reach, because the ban below rests on it.

    ``monkeypatch.setattr(layouts, name, wrapper)`` replaces a module ATTRIBUTE. Anything
    that resolves the name per call (``layouts.delete_all_layouts(...)``, and every
    function-local ``from ... import`` in ``dashboard/`` and ``local_models/``) therefore
    goes through the wrapper. Anything that captured the name at import time holds the
    object asserted here — a different object, with no guard in front of it.
    """
    recorded = real_model_root_guard.ORIGINALS
    # Vacuity: an empty ORIGINALS would make the loop below assert nothing at all.
    assert set(recorded) == set(real_model_root_guard.GUARDED_FUNCTIONS), sorted(recorded)
    for name, original in recorded.items():
        installed = getattr(layouts, name)
        assert installed is not original, f"layouts.{name} is not behind the rail at all"
        assert installed.__name__ == "_guarded"
        assert getattr(original, "__name__", None) == name


def test_an_import_bound_alias_of_the_deletion_sweep_is_still_caught(monkeypatch, tmp_path):
    """The sweep from the incident is caught even through an import-bound alias.

    Not because the fixture reaches aliases — it cannot — but because
    ``delete_all_layouts`` (``layouts.py:184``) resolves ``candidate_paths`` as a MODULE
    GLOBAL at ``:195``, which is a guarded attribute, so the refusal happens before
    ``shutil.rmtree`` at ``:200``. Worth pinning: it means a refactor that inlined that
    lookup would quietly shorten the rail.

    ``REAL_HOME`` is repointed at ``tmp_path`` so the forbidden root is a real directory the
    rail rejects while being physically disposable. Driving the true ``~/.cache/huggingface``
    here would mean that a regression in the rail makes THIS test perform the incident.
    """
    monkeypatch.setattr(real_model_root_guard, "REAL_HOME", tmp_path)
    root = tmp_path / ".cache" / "huggingface"
    doomed = root / "models--org--m" / "snapshots" / "abc"
    doomed.mkdir(parents=True)
    (doomed / "model.bin").write_bytes(b"x" * 128)

    alias = real_model_root_guard.ORIGINALS["delete_all_layouts"]
    with pytest.raises(AssertionError, match="REAL model root"):
        alias(root, "org/m")
    assert doomed.exists(), "the refusal must happen before anything is removed"


def _test_sources() -> list[tuple[Path, str]]:
    root = Path(__file__).resolve().parent
    return [(p, p.read_text(encoding="utf-8", errors="ignore")) for p in root.rglob("*.py")]


def test_no_test_module_import_binds_a_guarded_layouts_name():
    """The ban that closes the soft edge above, stated over the whole suite.

    LOAD-BEARING, not belt-and-braces. Most guarded entry points delegate to another
    guarded module global before touching disk (``is_downloaded`` → ``candidate_paths``,
    ``on_disk_bytes`` → ``downloaded_layouts``, ``reclaimable_bytes`` →
    ``cleanup_candidates``), so an alias of those is still intercepted one hop in. But
    ``cleanup_candidates`` (``layouts.py:211``) delegates to nothing — it walks
    ``root.rglob("*")`` itself at ``:223`` — so an import-bound alias of THAT name reaches a
    real cache root with nothing in front of it. One name is enough for the incident class
    this rail exists to close, and which name is self-contained is a refactor away from
    changing, so the suite bans the shape rather than tracking the delegation graph.

    The safe shape stays available and is what every module here already uses: import the
    MODULE (``from personalclaw.local_models import layouts``) and call through it, or
    import the function inside the test body.
    """
    sources = _test_sources()
    # Vacuity: a scan that found no files, or one that had stopped recognizing the shape,
    # would report clean. Assert it really read the suite; the detector's own ability to
    # flag is asserted in test_the_import_binding_detector_flags_the_offending_shape_only.
    assert len(sources) >= 900, f"the import-binding scan only read {len(sources)} files"

    offenders = {
        str(path.relative_to(Path(__file__).resolve().parent)): sorted(names)
        for path, text in sources
        if (names := real_model_root_guard.import_bound_guarded_names(text))
    }
    assert not offenders, (
        f"{offenders} bind a guarded layouts function at IMPORT time, which captures the "
        f"UNWRAPPED object and bypasses the model-root rail (LMMV Success Criterion 10). "
        f"Import the module instead (`from personalclaw.local_models import layouts`) and "
        f"call `layouts.<fn>(...)`, or move the import inside the test body."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # The offending shape: bound at import time, before the fixture exists.
        (
            "from personalclaw.local_models.layouts import cleanup_candidates",
            {"cleanup_candidates"},
        ),
        # Aliased, and more than one name — still the same capture.
        (
            "from personalclaw.local_models.layouts import (\n"
            "    delete_all_layouts as nuke,\n"
            "    is_downloaded,\n"
            ")",
            {"delete_all_layouts", "is_downloaded"},
        ),
        # The safe shapes, none of which may be flagged or the rail becomes a tax nobody keeps.
        ("from personalclaw.local_models import layouts", set()),
        ("import personalclaw.local_models.layouts", set()),
        ("def t():\n    from personalclaw.local_models.layouts import delete_all_layouts", set()),
        (
            "async def t():\n    from personalclaw.local_models.layouts import cleanup_candidates",
            set(),
        ),
        # A non-guarded helper from the same module is not this rail's business.
        ("from personalclaw.local_models.layouts import hf_repo_dirname", set()),
    ],
)
def test_the_import_binding_detector_flags_the_offending_shape_only(source, expected):
    """The scan's vacuity floor: prove it fires, and prove it does not over-fire."""
    assert real_model_root_guard.import_bound_guarded_names(source) == expected


@pytest.mark.parametrize("fn_name", real_model_root_guard.GUARDED_FUNCTIONS)
def test_the_coverage_cells_call_shape_is_one_the_real_function_accepts(fn_name, tmp_path):
    """The arity the coverage cell asserts through must be a REAL call, not a lucky raise.

    ``test_every_guarded_entry_point_is_wrapped`` proves the wrapper fires on a real root,
    but the wrapper raises before forwarding — so a wrong arity there is invisible. Drive
    the same derived argument shape against the UNWRAPPED original on a tmp root: a
    ``TypeError`` here means the coverage cell was asserting through a call the real
    function cannot accept, which is how ``reclaimable_bytes`` hid behind a two-argument
    call for one argument.

    Vacuity: the derived tuple is asserted to disagree across the population, so a
    ``trailing_args`` that always returned ``()`` (or always one dummy) would fail below
    rather than make every cell trivially callable.
    """
    original = real_model_root_guard.ORIGINALS[fn_name]
    extra = real_model_root_guard.trailing_args(fn_name)
    original(tmp_path, *extra)  # must not raise TypeError


def test_the_derived_arity_is_not_uniform_across_the_guarded_population():
    """Vacuity floor for ``trailing_args``: the population really has two shapes.

    Two of the seven entry points take ``cache_root`` alone. If that ever stopped being
    true the derivation would be pointless, and a constant would do — so state it, because
    the whole reason the previous hardcoded exception was wrong is that it named ONE of the
    two one-argument functions.
    """
    shapes = {
        fn: real_model_root_guard.trailing_args(fn)
        for fn in real_model_root_guard.GUARDED_FUNCTIONS
    }
    zero_arg = {fn for fn, extra in shapes.items() if extra == ()}
    assert zero_arg == {"cleanup_candidates", "reclaimable_bytes"}, sorted(zero_arg)
    assert set(shapes) - zero_arg, "every entry point took cache_root alone — derivation is moot"


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
