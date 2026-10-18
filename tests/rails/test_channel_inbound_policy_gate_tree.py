"""The source-tree checks split from
``tests/test_channel_inbound_policy_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

import channel_inbound_policy_gate_shared

from personalclaw import channel_inbound as ci

isolated = channel_inbound_policy_gate_shared.isolated


def test_admit_returns_only_what_the_gate_returned():
    """``admit`` must hold no verdict of its own — the pin on the fix's shape.

    The defect was not a wrong policy check, it was a *second* decision site above the one
    decision function. A behavioural test can only catch the cases it enumerates; this
    catches any re-added early return, whatever policy it does or does not consult.
    """
    tree = ast.parse(inspect.getsource(ci.admit))
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]

    assert (
        len(returns) == 1
    ), f"admit has {len(returns)} return statements; the gate is the only one"
    value = returns[0].value
    assert isinstance(value, ast.Call) and getattr(value.func, "id", "") == "guard_inbound", (
        "admit returns something other than guard_inbound's verdict — a second trust "
        "decision site above the gate is exactly the defect this file pins"
    )
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and "redeem" in ast.dump(n.func)
    ], "admit redeems a pairing code again; redemption belongs inside guard_inbound"


def test_channel_inbound_imports_no_trust_primitive_but_the_gate():
    """The module-level pin: importing the primitives is what let the door use them.

    ``redeem_pairing_code`` / ``is_allowed_sender`` are the two the pre-gate block used.
    The door needs neither — it needs one gate — and not importing them is what keeps the
    next contributor from assembling a policy here by hand. Beside the gate and its verdict
    type it reaches only the gate's one reporter, which decides nothing: the door refuses a
    message with no id through it, so that refusal is said in the gate's words.
    """
    src = ast.parse(inspect.getsource(ci))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(src)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("channel_trust")
        for alias in node.names
    }

    assert "guard_inbound" in imported, "floor: the door must still import the gate"
    assert imported <= {"TrustVerdict", "guard_inbound", "report_inbound_verdict"}, (
        "channel_inbound imports trust primitives beyond the gate and its verdict type "
        f"({sorted(imported)}) — those are the ingredients of a second policy"
    )
