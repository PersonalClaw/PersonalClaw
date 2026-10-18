"""The source-tree checks split from
``tests/test_verify_wheel_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

from verify_wheel_contract_shared import _VERIFY_WHEEL


def test_the_gateway_is_booted_verbose_before_the_subcommand() -> None:
    """``--verbose`` is what opens the evidence window, and its POSITION is load-bearing.

    It is a top-level flag: measured, ``gateway --test-mode --verbose`` exits 2 with
    "unrecognized arguments: --verbose", so a well-meaning tidy-up that moves it after the
    subcommand would break the boot outright. Asserted over the argv literal in the AST rather
    than by grepping for the string, so a mention in a comment cannot satisfy it.
    """
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    argvs = [
        [el.value for el in node.elts if isinstance(el, ast.Constant)]
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        and any(isinstance(el, ast.Constant) and el.value == "personalclaw" for el in node.elts)
    ]
    boots = [argv for argv in argvs if "gateway" in argv]
    assert len(boots) == 1, f"expected exactly one gateway argv literal, found {argvs!r}"
    argv = boots[0]
    assert "--verbose" in argv, (
        f"the gateway is booted without --verbose ({argv!r}), so the registry's enable lines "
        f"stay at INFO under a WARNING root level and assertion 6 judges an EMPTY transcript"
    )
    assert argv.index("--verbose") < argv.index("gateway"), (
        f"--verbose sits AFTER the subcommand in {argv!r}. It is a top-level flag; there it "
        f"makes the gateway exit 2 with 'unrecognized arguments'."
    )


def test_the_boot_probe_actually_calls_the_assertion() -> None:
    """A detector with no call site is the failure class this repo keeps finding.

    Asserted over the AST rather than by string search so a mention inside a docstring or a
    comment cannot satisfy it: the name has to appear as a CALL inside ``_boot_and_probe``,
    which is the function ``main`` uses to exercise the wheel.
    """
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    probes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_boot_and_probe"
    ]
    assert len(probes) == 1, "scripts/verify_wheel.py has no single `_boot_and_probe` function"
    called = {
        child.func.id
        for child in ast.walk(probes[0])
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_assert_every_bundled_app_enabled" in called, (
        "_boot_and_probe does not call _assert_every_bundled_app_enabled, so the extension "
        "check exists and never runs — assertion 6 would be declared and unexecuted."
    )


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(_VERIFY_WHEEL.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, f"scripts/verify_wheel.py has no single `{name}` function"
    return found[0]


def test_the_boot_probe_asks_the_installed_gateway_for_its_licence_notices() -> None:
    called = {
        child.func.id
        for child in ast.walk(_function("_boot_and_probe"))
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "_assert_notices_served" in called, (
        "_boot_and_probe does not call _assert_notices_served, so assertion 9 is declared and "
        "never runs"
    )


def test_the_canonical_build_checks_the_licences_of_what_the_spa_build_emitted() -> None:
    """``make build`` fails when a bundled npm package, font or binary has no licence notice:
    the check runs on the SPA it just built, before any artifact is made from it."""
    commands = [
        [elt.value for elt in node.args[0].elts if isinstance(elt, ast.Constant)]
        for node in ast.walk(_function("_canonical_distribution_build"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run"
        and isinstance(node.args[0], ast.List)
    ]
    spa = next(i for i, cmd in enumerate(commands) if cmd[-2:] == ["run", "build"])
    check = next(i for i, cmd in enumerate(commands) if "scripts/check_asset_licenses.py" in cmd)
    wheel = next(i for i, cmd in enumerate(commands) if "--wheel" in cmd)
    assert "--built-web" in commands[check]
    assert spa < check < wheel, f"the licence check runs out of order: {commands}"
