"""Every CPU budget in the suite reads its bound through ``cpu_budget.limit``, or is exempt with its
reason (``tests/cpu_budget.py``, #5629).

A budget put back to its written bound, or a new one, would pass every untraced leg and go red on
breadth's coverage leg alone, where a tracer another thread carries slows the timed call. So each
budget is read here: a test function that times a call with ``cpu_budget.seconds`` reads its bound
through ``cpu_budget.limit``, or is listed in :data:`EXEMPT` with why it must not. The upload
probe times a real gateway's loop its own way and holds each test's reading through ``_holds``:
each test that calls it is listed in :data:`HOLDS_LIMITED`, if it takes the tracer's allowance,
in :data:`HOLDS_KEPT`, if it keeps the 0.1 s bound, or in :data:`HOLDS_PINS`, if it chooses case
by case, each with its reason; a helper may not call it for its callers.
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

_PROBE = "test_a_large_upload_leaves_the_gateway_answering.py"

#: The upload probe's tests that take the tracer's allowance (``_holds(..., limited=True)``),
#: each with its regression's size under a tracer: over ``cpu_budget.TRACED`` times the probe's
#: 0.1 s bound with #5959's 1.25 margin, 0.375 s, so the coverage leg still fails it.
HOLDS_LIMITED = {
    (_PROBE, "test_scanning_a_large_text_upload_leaves_the_gateway_answering"): (
        "its regression, the content scan run on the loop, reads 0.32 s untraced (3.2 times"
        " 0.1 s) and 0.68 to 1.0 s traced (2.3 to 3.3 times 0.3 s); the coverage leg's step a"
        " tracer's threads held, 0.136 s (breadth run 42388523314), passes. Its other"
        " regression, the scan in a worker thread, held the loop 0.12 s, one parse that holds"
        " the interpreter's lock and that a tracer does not lengthen: under the traced bound, so"
        " only the untraced legs fail it, at 0.1 s"
    ),
    (_PROBE, "test_under_a_tracer_a_scan_on_the_loop_still_fails"): (
        "the scanning test's plant: that regression, read under a tracer, fails the limited bound"
    ),
}

#: The upload probe's tests that keep its 0.1 s bound under a tracer, each with why: for the
#: three real-gateway tests, a regression under 0.375 s would pass the traced bound; a by-hand
#: pin keeps it by what it pins.
HOLDS_KEPT = {
    (_PROBE, "test_completing_a_large_upload_leaves_the_gateway_answering"): (
        "its regression held the loop 0.19 s; it passed all 11 of its coverage-leg runs, from"
        " 2026-10-31 to 11-08, 10 of them from 11-01, at 0.1 s"
    ),
    (_PROBE, "test_filing_a_large_single_request_upload_leaves_the_gateway_answering"): (
        "its regression held the loop 0.18 s; it passed all 11 of its coverage-leg runs, from"
        " 2026-10-31 to 11-08, 10 of them from 11-01, at 0.1 s"
    ),
    (_PROBE, "test_a_slow_security_log_write_does_not_hold_the_gateway"): (
        "its regression held the loop 0.2 s as planted, 0.343 s on a runner's disk; it passed"
        " all 11 of its coverage-leg runs, from 2026-10-31 to 11-08, 10 of them from 11-01, at"
        " 0.1 s"
    ),
    (_PROBE, "test_a_reading_under_the_bound_holds_and_an_answer_counts_when_judged"): (
        "pins _holds itself, on readings made by hand"
    ),
}

#: The upload probe's tests whose ``limited`` is an expression, not True or False: each chooses the
#: allowance case by case, so it is listed apart, never read as kept.
HOLDS_PINS = {
    (_PROBE, "test_a_real_gateway_is_held_to_its_tests_bound_under_a_tracer"): (
        "pins _holds itself, on readings made by hand, limited and kept alike"
    ),
}

#: The files that test the helper itself, and time calls that are not budgets.
_HELPER_TESTS = {"test_a_cpu_budget_is_its_own_threads_time.py"}


def _called(node: ast.AST) -> set[str]:
    """Each ``name.attribute`` called anywhere in *node*."""
    return {
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
    }


def _functions(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _budgets(path: Path) -> list[tuple[str, bool]]:
    """Each test function in *path* that times a call with ``cpu_budget.seconds``, and whether it
    reads its bound through ``cpu_budget.limit``."""
    source = path.read_text(encoding="utf-8")
    if "cpu_budget.seconds(" not in source:
        return []
    found = []
    for node in _functions(source):
        called = _called(node)
        if "cpu_budget.seconds" in called:
            found.append((node.name, "cpu_budget.limit" in called))
    return found


def _choice(call: ast.Call) -> str:
    """How one ``_holds`` call chooses the tracer's allowance: "limited" (``limited=True``),
    "kept" (no ``limited``, or ``limited=False``) or "chosen" (any other expression, or keywords
    spread from a mapping)."""
    for keyword in call.keywords:
        if keyword.arg is None:
            return "chosen"
        if keyword.arg == "limited":
            if isinstance(keyword.value, ast.Constant) and keyword.value.value in (True, False):
                return "limited" if keyword.value.value else "kept"
            return "chosen"
    return "kept"


def _holds_calls(path: Path) -> dict[str, str]:
    """Each test function in *path*, a class's methods included, and each other top-level
    function but ``_holds``, that calls ``_holds``, with how it chooses the tracer's allowance
    (:func:`_choice`): "limited" if any call takes it, else "chosen" if any call chooses it, else
    "kept". A function that is not a test is listed too: a helper that calls ``_holds`` passes
    its choice to every caller, unseen."""
    source = path.read_text(encoding="utf-8")
    if "_holds(" not in source:
        return {}
    found = {}
    tree = ast.parse(source)
    top = set(tree.body)
    for node in ast.walk(tree):
        if (
            not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            or node.name == "_holds"
            or not (node.name.startswith("test_") or node in top)
        ):
            continue
        choices = {
            _choice(call)
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_holds"
        }
        if choices:
            found[node.name] = next(c for c in ("limited", "chosen", "kept") if c in choices)
    return found


def _held_through_the_limit(path: Path, function: str) -> bool | None:
    """Whether *function* in *path* reads its bound through ``cpu_budget.limit``; ``None`` when
    *path* has no such function."""
    nodes = [node for node in _functions(path.read_text(encoding="utf-8")) if node.name == function]
    if len(nodes) != 1:
        return None
    return "cpu_budget.limit" in _called(nodes[0])


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


def test_each_upload_probe_test_takes_the_tracers_allowance_only_with_its_reason():
    """🔴 Red with a ``_holds`` that reads the traced bound for all four real-gateway tests and a
    rail keyed by the helper alone: the three whose regressions are under 0.375 s passed the
    coverage leg, and the rail saw none of it. Each test that calls ``_holds`` is listed by its
    choice, so dropping ``limited=True`` from the scan, adding it to a kept test, making a kept
    test's ``limited`` an expression, a new caller with no entry, or a helper that calls
    ``_holds`` for its callers fails here."""
    seen: dict[str, set] = {"limited": set(), "kept": set(), "chosen": set()}
    helpers = set()
    for path in sorted(TESTS.rglob("test_*.py")):
        # A ``_holds`` of the probe's kind reads cpu_budget.limit; another file's is its own.
        if path.name != _PROBE and not _held_through_the_limit(path, "_holds"):
            continue
        for name, choice in _holds_calls(path).items():
            (seen[choice] if name.startswith("test_") else helpers).add((path.name, name))
    assert not helpers, f"_holds called outside a test: {helpers}"
    for choice, listed in (
        ("limited", HOLDS_LIMITED),
        ("kept", HOLDS_KEPT),
        ("chosen", HOLDS_PINS),
    ):
        assert seen[choice] == set(listed), f"{choice}, listed or not: {seen[choice] ^ set(listed)}"
    (probe,) = TESTS.rglob(_PROBE)
    assert _held_through_the_limit(probe, "_holds"), "_holds no longer reads cpu_budget.limit"


def test_the_held_reader_tells_a_limited_bound_from_one_that_is_not(tmp_path):
    """The vacuity proof for the check above."""
    path = tmp_path / "test_held.py"
    path.write_text(
        "import cpu_budget\n\n"
        "def limited(took):\n"
        "    assert took < cpu_budget.limit(0.1)\n\n"
        "def written(took):\n"
        "    assert took < 0.1\n",
        encoding="utf-8",
    )
    assert _held_through_the_limit(path, "limited") is True
    assert _held_through_the_limit(path, "written") is False
    assert _held_through_the_limit(path, "gone") is None


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
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import cpu_budget\n\n"
        "def _holds(probe, *, answers=True, limited=False):\n"
        "    assert probe < (cpu_budget.limit(0.1) if limited else 0.1)\n\n"
        "def test_scan():\n"
        "    _holds(0.2, limited=True)\n\n"
        "def test_kept():\n"
        "    _holds(0.05, answers=False)\n\n"
        "def test_pin(limited):\n"
        "    _holds(0.05, limited=limited)\n\n"
        "def test_written_off(limited):\n"
        "    _holds(0.05, limited=False)\n\n"
        "def helper(probe):\n"
        "    _holds(probe, limited=True)\n\n"
        "class TestK:\n"
        "    def test_method(self):\n"
        "        _holds(0.2, limited=True)\n\n"
        "def test_spread(opts):\n"
        "    _holds(0.05, **opts)\n\n"
        "def test_nothing():\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert _holds_calls(probe) == {
        "test_scan": "limited",
        "test_kept": "kept",
        "test_pin": "chosen",
        "test_written_off": "kept",
        "helper": "limited",
        "test_method": "limited",
        "test_spread": "chosen",
    }
