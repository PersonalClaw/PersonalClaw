"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ledger_priced.py``

Its checks:
``tests/rails/test_ledger_priced_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: The ledger money PRODUCERS — the functions whose result pairs a dollar figure with its
#: `priced` disclosure. `guardrails.budgets.SpendMeter.run_totals` is deliberately NOT here: it is a
#: different symbol returning a `_ScopeTotal`, and folding it in would make this rail police an
#: unrelated meter. Resolved by receiver below so the two never get confused.
MONEY_PRODUCERS = frozenset({"run_totals", "run_stats", "template_card"})


#: Receivers that mean the SpendMeter method rather than the ledger function. A call whose receiver
#: is one of these is skipped.
_METER_RECEIVERS = frozenset({"self", "meter", "m", "self._meter", "_meter"})


#: The money-carrying dataclasses. Their own methods are consumers of their own float.
MONEY_CLASSES = frozenset({"RunStats", "TemplateCard"})


#: Every key/attribute that IS a dollar figure from one of those producers. `cost_p50`/`cost_p95`
#: ride here too: a percentile over an unpriced sample is exactly as much a floor as the sum is.
MONEY_KEYS = frozenset({"cost_usd", "cost_p50", "cost_p95"})


#: The disclosure that must accompany any of them.
DISCLOSURE = "priced"


def _consumer_reason(fn: ast.AST, cls: str) -> str | None:
    if cls in MONEY_CLASSES:
        return "method of a money dataclass"
    # A MODULE-LEVEL function with a producer's name IS that producer. A METHOD with the same name
    # is not: `SpendMeter.run_totals` returns a `_ScopeTotal` from an in-memory meter and answers to
    # a different contract, so policing it here would be this rail reaching into a subsystem it does
    # not own — the exact over-reach that makes a structural rail get deleted.
    if not cls and getattr(fn, "name", "") in MONEY_PRODUCERS:
        return "is a money producer"
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in MONEY_PRODUCERS:
            return "calls a money producer"
        if isinstance(func, ast.Attribute) and func.attr in MONEY_PRODUCERS:
            if ast.unparse(func.value) in _METER_RECEIVERS:
                continue  # the SpendMeter method — a different symbol entirely
            return "calls a money producer"
    return None


def _reads_money(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in MONEY_KEYS:
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in MONEY_KEYS
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in MONEY_KEYS
        ):
            return True
    return False
