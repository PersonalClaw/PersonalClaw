"""Every CPU budget in the suite reads its bound through ``cpu_budget.limit``, or is exempt with its
reason (``tests/cpu_budget.py``, #5629).

A budget put back to its written bound, or a new one, would pass every untraced leg and go red on
breadth's coverage leg alone, where a tracer another thread carries slows the timed call. So each
budget is read here: a test function that times a call with ``cpu_budget.seconds`` reads its bound
through ``cpu_budget.limit``, or is listed in :data:`EXEMPT` with why it must not.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

#: The budgets that do not read through ``cpu_budget.limit``, each with why: its regression is
#: under ``cpu_budget.TRACED`` times its bound, so an allowance for a tracer would stop it failing.
EXEMPT = {
    (
        "test_the_credential_screen_checks_a_long_command_in_bounded_time.py",
        "test_a_32_kb_heredoc_is_checked_by_the_credential_screen_in_under_a_second",
    ): "its regression took 4.2 s, under the traced bound; it reads at most 0.22 s traced",
    (
        "test_the_tool_name_rule_refuses_only_a_real_publish.py",
        "test_a_one_megabyte_line_is_judged_within_the_bar",
    ): "its bar is set by its regression, not by a runner; it reads at most 1.7 s traced",
}

#: The files that test the helper itself, and time calls that are not budgets.
_HELPER_TESTS = {"test_a_cpu_budget_is_its_own_threads_time.py"}


def _budgets(path: Path) -> list[tuple[str, bool]]:
    """Each test function in *path* that times a call with ``cpu_budget.seconds``, and whether it
    reads its bound through ``cpu_budget.limit``."""
    source = path.read_text(encoding="utf-8")
    if "cpu_budget.seconds(" not in source:
        return []
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = {
            f"{call.func.value.id}.{call.func.attr}"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
        }
        if "cpu_budget.seconds" in called:
            found.append((node.name, "cpu_budget.limit" in called))
    return found


def test_every_budget_reads_through_the_limit_or_is_exempt_with_its_reason():
    """A budget put back to its written bound, or a new one, would pass every untraced leg and go
    red on the coverage leg alone. So each is read here: through the limit, or listed above."""
    seen: dict[tuple[str, str], bool] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        if path.name in _HELPER_TESTS:
            continue
        for name, limited in _budgets(path):
            seen[(path.name, name)] = limited
    unlimited = sorted(key for key, limited in seen.items() if not limited and key not in EXEMPT)
    assert not unlimited, f"neither read through cpu_budget.limit nor exempt: {unlimited}"
    assert set(EXEMPT) <= set(seen), f"exemptions with no budget left: {set(EXEMPT) - set(seen)}"
    assert sum(seen.values()) >= 4, "the census found fewer limited budgets than there are"


def test_the_census_reader_tells_a_limited_budget_from_one_that_is_not(tmp_path):
    """The vacuity proof for the census above: a reader that saw nothing would pass it."""
    path = tmp_path / "test_budgets.py"
    path.write_text(
        "import cpu_budget\n\n"
        "def test_limited():\n"
        "    took = cpu_budget.seconds(lambda: None)\n"
        "    assert took < cpu_budget.limit(1.0)\n\n"
        "def test_written():\n"
        "    took = cpu_budget.seconds(lambda: None)\n"
        "    assert took < 1.0\n",
        encoding="utf-8",
    )
    assert _budgets(path) == [("test_limited", True), ("test_written", False)]
