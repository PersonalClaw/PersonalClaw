"""The function-size measure and its baseline, proved on functions planted here (#4015).

``tests/function_size.py`` holds the rule; ``tests/rails/test_function_size_tree.py`` holds the
real tree to it. Each case below plants a function at or over a limit, or a baseline line that has
gone wrong, and shows the check names it.
"""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import function_size
import pytest
import site_baseline


def _one(source: str) -> function_size.Measure:
    (measure,) = function_size.measure("m.py", ast.parse(textwrap.dedent(source)))
    return measure


def _long(body_lines: int) -> str:
    """A function whose span is its ``def`` line plus *body_lines* lines."""
    return "def f():\n" + "    x = 1\n" * body_lines


# ── the measure ────────────────────────────────────────────────────────────────


def test_a_function_of_300_lines_fits_and_one_of_301_does_not() -> None:
    fits, over = _one(_long(299)), _one(_long(300))
    assert (fits.lines, fits.over) == (300, False)
    assert (over.lines, over.over) == (301, True)


def _branches(n: int) -> str:
    return "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n))


def test_a_function_of_complexity_30_fits_and_one_of_31_does_not() -> None:
    fits, over = _one(_branches(29)), _one(_branches(30))
    assert (fits.complexity, fits.over) == (30, False)
    assert (over.complexity, over.over) == (31, True)


@pytest.mark.parametrize(
    ("body", "added"),
    [
        ("if a:\n    pass\nelif b:\n    pass\nelse:\n    pass", 2),
        ("for i in a:\n    pass\nelse:\n    pass", 1),
        ("while a:\n    pass", 1),
        ("try:\n    pass\nexcept A:\n    pass\nexcept B:\n    pass\nelse:\n    pass", 2),
        ("match a:\n    case 1:\n        pass\n    case _:\n        pass", 2),
        ("return a and b and c", 2),
        ("return a or (b and c)", 2),
        ("return [i for i in a if i if i > 1]", 2),
        ("return {i: j for i, j in a}", 0),
        ("return x if a else y", 1),
        ("with a:\n    pass", 0),
        ("assert a", 0),
        ("return lambda v: v if v else 0", 1),
    ],
)
def test_each_branch_adds_one_and_nothing_else_does(body: str, added: int) -> None:
    source = "def f(a, b, c, x, y):\n" + textwrap.indent(body, "    ") + "\n"
    assert _one(source).complexity == 1 + added


def test_an_async_for_adds_one() -> None:
    assert _one("async def f(a):\n    async for i in a:\n        pass\n").complexity == 2


def test_a_nested_function_counts_on_its_own_and_in_its_parent() -> None:
    source = textwrap.dedent("""
        def outer(a):
            def inner(b):
                if b:
                    return 1
                return 2

            if a:
                return inner(a)
            return 0
        """)
    measures = function_size.measure("m.py", ast.parse(source))
    by_site = {m.site: m for m in measures}
    assert by_site["m.py::outer"].complexity == 3
    assert by_site["m.py::outer.inner"].complexity == 2
    assert by_site["m.py::outer"].lines == 9
    assert by_site["m.py::outer.inner"].lines == 4


def test_a_long_body_cannot_pass_by_moving_into_a_nested_def() -> None:
    source = "def outer():\n    def inner():\n" + "        x = 1\n" * 300 + "    return inner\n"
    measures = {m.site: m for m in function_size.measure("m.py", ast.parse(source))}
    assert measures["m.py::outer"].over and measures["m.py::outer.inner"].over


def test_sites_name_methods_nested_functions_and_repeats() -> None:
    source = textwrap.dedent("""
        class C:
            def m(self):
                def helper():
                    pass

            async def n(self):
                pass

        def f(): ...
        def f(): ...
        """)
    sites = [m.site for m in function_size.measure("m.py", ast.parse(source))]
    assert sites == ["m.py::C.m", "m.py::C.m.helper", "m.py::C.n", "m.py::f", "m.py::f #2"]


def test_a_decorator_is_not_part_of_the_span() -> None:
    assert _one("@a\n@b\ndef f():\n    return 1\n").lines == 2


# ── the baseline ───────────────────────────────────────────────────────────────

_TREE = {
    "src/personalclaw/big.py": _long(300),
    "src/personalclaw/branchy.py": _branches(30).replace("def f", "def g"),
    "src/personalclaw/small.py": "def h():\n    return 1\n",
}


def _scaffold(root: Path, baseline: list[dict], tree: dict[str, str] | None = None) -> None:
    for rel, text in (tree or _TREE).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    path = root / function_size.BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in baseline), encoding="utf-8")
    registered = {
        "site": function_size.BASELINE,
        "rail": "tests/rails/test_function_size_tree.py",
        "replaces": "no limit",
        "ceiling": 2,
        "reason": "the scaffold's list",
    }
    (root / site_baseline.REGISTRY).write_text(json.dumps(registered) + "\n", encoding="utf-8")
    (root / "tests/rails/test_function_size_tree.py").write_text("", encoding="utf-8")


def _line(site: str, lines: int, complexity: int) -> dict:
    return {"site": site, "reason": "planted", "lines": lines, "complexity": complexity}


_LISTED = [
    _line("src/personalclaw/big.py::f", 301, 1),
    _line("src/personalclaw/branchy.py::g", 61, 31),
]


def _failures(root: Path) -> list[str]:
    """Every failure the rail's two checks report for the scaffold at *root*."""
    trees = [
        (path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted((root / "src").rglob("*.py"))
    ]
    over = function_size.over_the_limits(function_size.measure_all(trees))
    failures: list[str] = []
    try:
        site_baseline.check(
            [m.site for m in over], function_size.BASELINE, None, root=root, prune=False
        )
    except AssertionError as exc:
        failures += str(exc).splitlines()[1:]
    listed = site_baseline.load(root / function_size.BASELINE)
    return failures + function_size.grown(over, listed)


def test_a_tree_that_matches_its_baseline_passes(tmp_path: Path) -> None:
    _scaffold(tmp_path, _LISTED)
    assert _failures(tmp_path) == []


def test_a_function_over_a_limit_the_baseline_does_not_list_fails_by_name(tmp_path: Path) -> None:
    _scaffold(tmp_path, _LISTED[:1])
    (failure,) = _failures(tmp_path)
    assert "src/personalclaw/branchy.py::g is not listed" in failure


def test_a_listed_function_that_now_fits_fails_until_its_line_goes(tmp_path: Path) -> None:
    tree = {**_TREE, "src/personalclaw/big.py": _long(10)}
    _scaffold(tmp_path, _LISTED, tree)
    (failure,) = _failures(tmp_path)
    assert "src/personalclaw/big.py::f is listed" in failure and "no longer exists" in failure


def test_a_listed_function_that_is_gone_fails_until_its_line_goes(tmp_path: Path) -> None:
    _scaffold(tmp_path, [*_LISTED, _line("src/personalclaw/gone.py::old", 400, 50)])
    (failure,) = _failures(tmp_path)
    assert "src/personalclaw/gone.py::old is listed" in failure


@pytest.mark.parametrize(("lines", "complexity"), [(300, 1), (301, 0)])
def test_a_listed_function_that_grew_past_its_line_fails(
    tmp_path: Path, lines: int, complexity: int
) -> None:
    _scaffold(tmp_path, [_line("src/personalclaw/big.py::f", lines, complexity), _LISTED[1]])
    (failure,) = _failures(tmp_path)
    assert "src/personalclaw/big.py::f grew to 301 lines and complexity 1" in failure


def test_a_line_that_records_no_size_fails(tmp_path: Path) -> None:
    _scaffold(tmp_path, [{"site": "src/personalclaw/big.py::f", "reason": "r"}, _LISTED[1]])
    (failure,) = _failures(tmp_path)
    assert "records no `lines` and `complexity`" in failure


def test_a_listed_function_that_shrank_but_is_still_over_passes(tmp_path: Path) -> None:
    _scaffold(tmp_path, [_line("src/personalclaw/big.py::f", 900, 40), _LISTED[1]])
    assert _failures(tmp_path) == []


def test_a_function_listed_twice_fails(tmp_path: Path) -> None:
    _scaffold(tmp_path, [*_LISTED, _LISTED[0]])
    (failure,) = _failures(tmp_path)
    assert "listed 2 times" in failure
