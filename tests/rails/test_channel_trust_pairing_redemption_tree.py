"""The source-tree checks split from
``tests/test_channel_trust_pairing_redemption.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import channel_trust_pairing_redemption_shared
import source_index

from personalclaw import channel_trust as ct

isolated = channel_trust_pairing_redemption_shared.isolated


def _src_root() -> Path:
    import personalclaw

    return Path(personalclaw.__file__).parent


def test_the_gate_reaches_redemption():
    """``guard_inbound`` itself calls ``redeem_pairing_code`` — asserted on its AST.

    A grep would match the docstring; walking the function body cannot.
    """
    tree = ast.parse(inspect.getsource(ct.guard_inbound))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "redeem_pairing_code" in called, (
        "guard_inbound MUST reach redeem_pairing_code — that call IS the fix for #950. "
        f"Calls found: {sorted(called)}"
    )


def test_redemption_has_a_non_test_production_caller():
    """The gate that redeems is reached from shipped, non-test code.

    This project's rule is that a validator with no production call site is a dead fix, and
    #950 is exactly that failure mode: ``redeem_pairing_code`` existed, was correct, and was
    reachable only from a reference transport. So the call site is asserted, not assumed:
    some module under ``src/personalclaw/`` that is neither ``channel_trust`` itself, nor a
    test-support module, nor a bare re-export must call ``guard_inbound``.
    """
    root = _src_root()
    callers: list[str] = []
    for path, tree in source_index.package_trees():
        rel = path.relative_to(root).as_posix()
        if rel == "channel_trust.py" or rel.startswith("testing/") or rel.startswith("sdk/"):
            continue  # the definition site, test-support, and pure re-export surfaces
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "guard_inbound"
            ):
                callers.append(rel)
                break

    assert callers, (
        "no production module calls guard_inbound, so the pairing redemption inside it is "
        "unreachable — the #950 failure mode reintroduced one layer up"
    )
    assert "channel_inbound.py" in callers, (
        "the platform's inbound door must still route through the gate; production "
        f"callers found: {callers}"
    )
