"""The source-tree checks split from
``tests/test_browse_vision_grounding.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from browse_vision_grounding_shared import _REPO_ROOT

from personalclaw.browse.loop import run_browse_loop
from personalclaw.browse.page import CdpPageDriver


def test_there_is_no_coordinate_TYPE_anywhere_in_the_located_path() -> None:
    """credential-never-transits, structurally.

    The invariant rests on ``extraction`` classifying a field as a credential and the loop
    refusing to ``fill`` it. A coordinate the agent could TYPE into would sidestep that
    classification entirely — the field would never have been examined. So the located path is
    CLICK-only by construction, and this asserts that construction rather than trusting a
    docstring: the driver protocol grows exactly one coordinate verb, and it is a click.
    """
    from personalclaw.browse import loop as loop_module

    protocol = ast.parse(Path(loop_module.__file__).read_text(encoding="utf-8"))
    driver = next(
        node
        for node in ast.walk(protocol)
        if isinstance(node, ast.ClassDef) and node.name == "PageDriver"
    )
    methods = {n.name for n in driver.body if isinstance(n, ast.AsyncFunctionDef)}
    assert "click_at" in methods, methods
    coordinate_writers = {m for m in methods if m.endswith("_at")}
    assert coordinate_writers == {"click_at"}, (
        f"the driver grew a second coordinate verb: {sorted(coordinate_writers)}. A coordinate "
        "TYPE would put a value into a field the extraction never classified as a credential."
    )
    assert not hasattr(CdpPageDriver, "fill_at")
    assert not hasattr(CdpPageDriver, "type_at")


def test_adds_no_model_artifact_to_the_shipped_tree() -> None:
    """Rail (c) proper: the model is user-PULLED, so the fallback ships no weight.

    Measured with its own ``weight_shaped_members`` over the packaged source tree, so "what
    counts as a weight" is its definition rather than a second list that could drift. This cannot
    trip the size budget because it adds nothing to weigh — and it deliberately does NOT assert
    ``repo_declaration() is None``, which is its own state to change.
    """
    from personalclaw.bundled_model import weight_shaped_members

    names = [str(p.relative_to(_REPO_ROOT)) for p in source_index.package_any_files()]
    assert names, "no packaged files were found — the scan is vacuous"
    assert weight_shaped_members(names) == [], (
        "a model weight is present in the packaged source tree; the vision fallback's model is "
        "user-pulled and must add no artifact to the wheel or image"
    )


def test_the_action_provider_passes_the_flag_through_and_defaults_it_off() -> None:
    """The flag must reach ``run_browse_loop`` from ``action_config``, or the whole path is inert —
    the failure mode this repository keeps finding. Asserted on the AST of the call."""
    from personalclaw.action_providers import browse_provider

    tree = ast.parse(Path(browse_provider.__file__).read_text(encoding="utf-8"))
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_browse_loop"
    )
    kwargs = {kw.arg for kw in call.keywords}
    assert (
        "vision_grounding" in kwargs
    ), f"browse_provider never passes vision_grounding to run_browse_loop; it passes {kwargs}"
    # And the loop's own default is OFF, so a caller that says nothing gets nothing.
    import inspect

    assert inspect.signature(run_browse_loop).parameters["vision_grounding"].default is False
