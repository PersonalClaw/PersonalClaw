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


@pytest.mark.parametrize("fn_name", real_model_root_guard.GUARDED_FUNCTIONS)
def test_every_guarded_entry_point_is_wrapped(fn_name):
    """Coverage assertion: each listed entry point really is behind the rail.

    Without this, adding a new cache-root function to ``layouts`` and forgetting to list
    it would leave a hole the rail reports as clean.
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
