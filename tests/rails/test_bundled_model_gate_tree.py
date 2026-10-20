"""The source-tree checks split from
``tests/test_bundled_model_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

from bundled_model_gate_shared import _VERIFY_WHEEL


def _calls_in(function_name: str) -> list[tuple[int, str]]:
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name), None
    )
    assert fn is not None, f"scripts/verify_wheel.py has no {function_name}()"
    return sorted(
        (node.lineno, node.func.id)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )


def test_every_verification_asks_the_installed_package_after_installing_it() -> None:
    """A perfect assertion with no call site guards nothing — and before the install there is no
    artifact to ask, so the ORDER is part of the contract. The install-and-boot half lives in
    one function, and both ways in (``--wheel``, and ``make build``'s ``--build``) reach it."""
    names = [name for _line, name in _calls_in("_verify_wheel_runtime")]
    assert "_assert_installed_bundled_model" in names, f"assertion 7 never runs: {names}"
    assert names.index("_pip_install_wheel") < names.index("_assert_installed_bundled_model")
    for entry in ("main", "_canonical_distribution_build"):
        called = [name for _line, name in _calls_in(entry)]
        assert "_verify_wheel_runtime" in called, f"{entry}() skips the installed checks: {called}"


def test_the_boot_probe_asks_for_the_download_offer() -> None:
    names = [name for _line, name in _calls_in("_boot_and_probe")]
    assert "_assert_download_offer" in names, f"assertion 8 has no call site: {names}"
