"""Every status a writer can produce is a key of the table that translates it (WV-15).

🔴 WHY THIS RAIL EXISTS. `triggers/history.py` keeps three status→outcome tables, and each one is
read with a `.get(status, <fallback>)`. The fallbacks are the honest ones — unclassifiable must not
read as a success — but a fallback is SILENT, so a status nobody mapped looks exactly like a status
somebody decided about. Measured on the parent commit: FOUR live statuses were unmapped.

* `hooks.py` → `HOOK_STATUS_TO_OUTCOME`: `skipped_incident` (the incident kill switch held the
  action BEFORE dispatch) and `launched` (a fire-and-forget background turn) both fell to
  `RAN if last_run` — "it ran and did something durable" for a hook that ran nothing.
* `gateway._record_blocked_fire` → `SCHEDULE_STATUS_TO_OUTCOME`: `blocked_injection` fell to
  `FAILED`, so a defended injection attempt read as a broken automation.
* `service._record_suppression_row` → the same table: all six `INERT_OUTCOMES` members fell to
  `FAILED`, so a quiet-hours skip read as a genuine failure.

**A REGEX SCAN FINDS ONLY THE EASY HALF.** The owner's own `grep` over `hooks.py` found
`skipped_incident` and missed `launched`, because that write is
`hook.last_status = result.outcome if result.outcome in ("launched", "queued") else "ok"` — the
status is a NAME whose values live in the `in` tuple of the condition. Two of the four real writers
have that shape and a third writes a variable pinned by an early-return guard in a different module:

    status = str(getattr(result, "outcome", "") or "")   # dashboard handler: pinned by its `elif`
    status=outcome                                       # service.py: pinned by `not in` + return

So the rail reads ASTs and infers each write's POSSIBLE VALUES from the guards that dominate it.
Deliberately a small, total inference (constant / conditional-expression / `str()` and `or`
unwrapping / local-name resolution / `in` and `not in` guards): anything it cannot resolve is a
FAILURE, not a shrug, because "I could not tell" is how the four got in.

**Two directions of drift are pinned, not one.** The value check catches a new status; the
file-census check (`test_the_writer_file_census_is_pinned`) catches a new WRITER, which no
per-file scan could see. And every writer carries vacuity floors — a scan that suddenly finds two
literals where it found nine has stopped measuring the thing it was written to measure, which is
the failure mode that makes a green rail worse than no rail.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from personalclaw.triggers import executor as E
from personalclaw.triggers import history as H

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


# ── the writer census ──


@dataclass(frozen=True)
class Writer:
    """One place a status is written, and the table that must translate what it writes.

    `min_sites` / `min_values` are VACUITY FLOORS measured against the real source, not guesses.
    Lowering one is a claim that the writer genuinely shrank; make that claim in a commit message,
    never to make a red rail green.
    """

    label: str
    path: str
    table: dict[str, str]
    table_name: str
    #: How a status is written here. `attr` = `<x>.<name> = ...`; `kwarg` = `Name(<name>=...)`;
    #: `dictkey` = `{"<name>": ...}`.
    kind: str
    name: str
    #: For `kwarg`: the callable's name. For `dictkey`: unused.
    call: str = ""
    #: Restrict the scan to one function (`dictkey` would otherwise match any dict in the file).
    #: Asserted to EXIST, so a rename reds the rail instead of quietly scanning nothing.
    in_function: str = ""
    min_sites: int = 1
    min_values: int = 1
    #: Statuses the table deliberately does not carry, each with a reason in the comment below.
    exempt: frozenset[str] = field(default_factory=frozenset)


WRITERS: tuple[Writer, ...] = (
    Writer(
        label="hooks.py sets ScriptHook.last_status",
        path="hooks.py",
        table=H.HOOK_STATUS_TO_OUTCOME,
        table_name="HOOK_STATUS_TO_OUTCOME",
        kind="attr",
        name="last_status",
        min_sites=8,
        min_values=7,
    ),
    Writer(
        label="gateway.py records a fire's ScheduleRun",
        path="gateway.py",
        table=H.SCHEDULE_STATUS_TO_OUTCOME,
        table_name="SCHEDULE_STATUS_TO_OUTCOME",
        kind="kwarg",
        name="status",
        call="ScheduleRun",
        min_sites=2,
        min_values=3,
    ),
    Writer(
        label="triggers/service.py records a suppressed fire's ScheduleRun",
        path="triggers/service.py",
        table=H.SCHEDULE_STATUS_TO_OUTCOME,
        table_name="SCHEDULE_STATUS_TO_OUTCOME",
        kind="kwarg",
        name="status",
        call="ScheduleRun",
        min_sites=1,
        min_values=6,
    ),
    Writer(
        label="dashboard/handlers/triggers.py records a manual run's ScheduleRun",
        path="dashboard/handlers/triggers.py",
        table=H.SCHEDULE_STATUS_TO_OUTCOME,
        table_name="SCHEDULE_STATUS_TO_OUTCOME",
        kind="kwarg",
        name="status",
        call="ScheduleRun",
        min_sites=1,
        min_values=4,
    ),
    Writer(
        # The executor's runner is INJECTED, so its status source is open by design (an app's
        # provider, or `mcp_automation._http_runner`'s HTTP body). `classify`'s unrecognized→FAILED
        # rule plus `action_providers/base.py`'s "adding a member means updating these maps" note
        # are what cover the open half; this covers the one in-repo runner, which is the one that
        # fires every clock trigger.
        label="gateway._clock_loop._runner reports a status to the executor",
        path="gateway.py",
        table=E.STATUS_TO_OUTCOME,
        table_name="executor.STATUS_TO_OUTCOME",
        kind="dictkey",
        name="status",
        in_function="_runner",
        min_sites=2,
        min_values=2,
    ),
)


# ── the inference ──


class Unresolved(Exception):
    """A write whose possible values this rail cannot infer.

    Raised rather than returned so a caller cannot forget to check: an unresolved write silently
    treated as "no values" is a vacuous pass, which is the shape of the bug being ratcheted.
    """


def _literals(node: ast.expr, module: str) -> set[str]:
    """The string members of a container used as an `in`/`not in` comparator.

    A `Name` comparator is resolved by importing the module under scan and reading the constant —
    the same namespace the writer itself reads it from, so the rail cannot disagree with the code
    about what `INERT_OUTCOMES` contains.
    """
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out = {
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
        if len(out) != len(node.elts):
            raise Unresolved(f"non-string member in {ast.unparse(node)}")
        return out
    if isinstance(node, ast.Name):
        try:
            value = getattr(importlib.import_module(module), node.id)
        except Exception as exc:  # noqa: BLE001 - an unreadable constant is unresolved, not clean
            raise Unresolved(f"could not resolve {node.id} in {module}: {exc}") from exc
        if not isinstance(value, (set, frozenset, tuple, list)) or not all(
            isinstance(v, str) for v in value
        ):
            raise Unresolved(f"{node.id} is not a container of strings")
        return set(value)
    raise Unresolved(f"unsupported comparator {ast.unparse(node)}")


def _pins(test: ast.expr, module: str, *, holds: bool) -> dict[str, set[str]]:
    """What a guard proves about an expression's value, as `{unparsed expr: possible values}`.

    `holds=True` for the branch a test guards; `holds=False` for the path that survives it (an
    early return). The asymmetry in the boolean handling is the whole correctness argument:

    * `A and B` HOLDING implies B, so an `and`-chain contributes each `in` it contains.
    * `not (A or B)` implies `not B`, so an `or`-chain contributes each `not in` it contains.

    Any other combination proves nothing about an individual operand and contributes nothing, which
    is why an `or` of `in`s (or an `and` of `not in`s) is deliberately ignored rather than guessed.
    """
    out: dict[str, set[str]] = {}
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _pins(test.operand, module, holds=not holds)
    if isinstance(test, ast.BoolOp):
        chain_ok = (isinstance(test.op, ast.And) and holds) or (
            isinstance(test.op, ast.Or) and not holds
        )
        if chain_ok:
            for value in test.values:
                out.update(_pins(value, module, holds=holds))
        return out
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        op = test.ops[0]
        wanted = isinstance(op, ast.In) if holds else isinstance(op, ast.NotIn)
        if wanted:
            try:
                out[ast.unparse(test.left)] = _literals(test.comparators[0], module)
            except Unresolved:
                # An unreadable container (`in (None, "")`, a dict of non-strings) pins NOTHING.
                # Swallowed only here, and only in the safe direction: a missing pin can make the
                # write site unresolvable, which fails loudly, while a guessed pin would invent
                # coverage.
                pass
    return out


def _values(
    expr: ast.expr,
    pins: dict[str, set[str]],
    locals_: dict[str, list[tuple[ast.expr, dict[str, set[str]]]]],
    module: str,
    depth: int = 0,
) -> set[str]:
    """Every string this expression can evaluate to, or `Unresolved`.

    GUARDS ARE CHECKED FIRST, and that order is load-bearing. `service._record_suppression_row`
    writes `status=outcome` where `outcome = str(row.get("outcome") or "")` — resolving the
    assignment would yield `{""}` and miss all six real values, while the dominating
    `if ... outcome not in INERT_OUTCOMES: return` names them exactly.
    """
    if depth > 6:
        raise Unresolved(f"gave up unwrapping {ast.unparse(expr)}")
    key = ast.unparse(expr)
    if key in pins:
        return set(pins[key])
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, str):
            return {expr.value}
        raise Unresolved(f"non-string constant {key}")
    if isinstance(expr, ast.IfExp):
        return _values(
            expr.body, {**pins, **_pins(expr.test, module, holds=True)}, locals_, module, depth + 1
        ) | _values(
            expr.orelse,
            {**pins, **_pins(expr.test, module, holds=False)},
            locals_,
            module,
            depth + 1,
        )
    # `str(x)` / `x or ""` are transparent wrappers around the value that matters.
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "str":
        if len(expr.args) == 1:
            return _values(expr.args[0], pins, locals_, module, depth + 1)
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
        out: set[str] = set()
        for value in expr.values:
            out |= _values(value, pins, locals_, module, depth + 1)
        return out
    if isinstance(expr, ast.Name) and expr.id in locals_:
        out = set()
        for value, at in locals_[expr.id]:
            out |= _values(value, at, locals_, module, depth + 1)
        return out
    raise Unresolved(f"cannot infer {key}")


def _terminates(body: list[ast.stmt]) -> bool:
    """Whether a block always leaves — so the code after its `if` runs only when the test failed."""
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def _matches(node: ast.AST, writer: Writer) -> list[ast.expr]:
    """The status expressions this node writes, per the writer's shape."""
    out: list[ast.expr] = []
    if writer.kind == "attr" and isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == writer.name:
                out.append(node.value)
    elif writer.kind == "kwarg" and isinstance(node, ast.Call):
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called == writer.call:
            for kw in node.keywords:
                if kw.arg == writer.name:
                    out.append(kw.value)
    elif writer.kind == "dictkey" and isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == writer.name:
                out.append(v)
    return out


def _scan_body(
    stmts: list[ast.stmt],
    pins: dict[str, set[str]],
    writer: Writer,
    sites: list[tuple[ast.expr, dict[str, set[str]]]],
    locals_: dict[str, list[tuple[ast.expr, dict[str, set[str]]]]],
    module: str,
) -> None:
    """Collect write sites (with the guards holding at each) and local assignments, in order.

    Nested `def`s are skipped here and scanned separately with their own empty guard set: a
    closure's body does not run under its parent's `if`.
    """
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.If):
            inside = {**pins, **_pins(stmt.test, module, holds=True)}
            _scan_body(stmt.body, inside, writer, sites, locals_, module)
            after = {**pins, **_pins(stmt.test, module, holds=False)}
            _scan_body(stmt.orelse, after, writer, sites, locals_, module)
            if _terminates(stmt.body) and not stmt.orelse:
                # An early-return guard: everything below it runs only on the surviving path.
                pins = after
            continue
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
            for field_name in ("body", "orelse", "finalbody"):
                _scan_body(
                    getattr(stmt, field_name, []) or [], pins, writer, sites, locals_, module
                )
            for handler in getattr(stmt, "handlers", []) or []:
                _scan_body(handler.body, pins, writer, sites, locals_, module)
            continue
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name):
                locals_.setdefault(target.id, []).append((stmt.value, pins))
        for node in ast.walk(stmt):
            for expr in _matches(node, writer):
                sites.append((expr, pins))


def _module_name(path: str) -> str:
    return "personalclaw." + path.removesuffix(".py").replace("/", ".")


def scan(writer: Writer) -> tuple[set[str], int, list[str]]:
    """`(possible statuses, site count, unresolved descriptions)` for one writer."""
    source = (SRC / writer.path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module = _module_name(writer.path)
    scopes: list[list[ast.stmt]] = [tree.body]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if writer.in_function and node.name != writer.in_function:
                continue
            scopes.append(node.body)
    if writer.in_function and len(scopes) == 1:
        pytest.fail(
            f"{writer.label}: no function named {writer.in_function!r} in {writer.path}. "
            "It was renamed or moved — repoint this writer rather than dropping the scan."
        )

    values: set[str] = set()
    unresolved: list[str] = []
    count = 0
    for body in scopes:
        sites: list[tuple[ast.expr, dict[str, set[str]]]] = []
        locals_: dict[str, list[tuple[ast.expr, dict[str, set[str]]]]] = {}
        _scan_body(body, {}, writer, sites, locals_, module)
        for expr, pins in sites:
            count += 1
            try:
                values |= _values(expr, pins, locals_, module)
            except Unresolved as exc:
                unresolved.append(f"line {expr.lineno}: {exc}")
    return values, count, unresolved


# ── the rails ──


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.label)
def test_every_status_a_writer_produces_is_in_its_table(writer: Writer) -> None:
    """🔴 THE RAIL. A status the table lacks is projected by a fallback nobody chose."""
    values, _count, unresolved = scan(writer)
    assert not unresolved, (
        f"{writer.label}: this rail could not infer {len(unresolved)} write(s):\n  "
        + "\n  ".join(unresolved)
        + "\nAn uninferable write is a status nobody checked. Either write it in a shape the "
        "inference covers (a literal, a conditional expression, or a name pinned by an `in` / "
        "`not in` guard) or extend `_values`."
    )
    missing = sorted(values - set(writer.table) - writer.exempt)
    assert not missing, (
        f"{writer.label} can write {missing}, which {writer.table_name} does not map. "
        "Those fires will be projected by the table's silent fallback — `failed` for a schedule "
        "row, and a hook's unmapped status is now a warning plus `failed`. Map each one with a "
        "comment saying why that outcome, or exempt it here with a reason."
    )


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.label)
def test_the_scan_is_not_vacuous(writer: Writer) -> None:
    """A scan that matches nothing is indistinguishable from a scan that found nothing wrong.

    Both floors matter: `min_sites` catches a shape change that stops matching (the writer moved to
    a helper, or `ScheduleRun(**kwargs)` replaced the keyword), and `min_values` catches an
    inference that silently stopped resolving guards.
    """
    values, count, _unresolved = scan(writer)
    assert count >= writer.min_sites, (
        f"{writer.label}: found {count} write site(s), expected at least {writer.min_sites}. "
        "The shape this rail matches changed; repoint it before lowering the floor."
    )
    assert len(values) >= writer.min_values, (
        f"{writer.label}: inferred {len(values)} status value(s) {sorted(values)}, expected at "
        f"least {writer.min_values}."
    )


def test_the_writer_file_census_is_pinned() -> None:
    """A NEW writer file is the drift no per-file scan can see.

    Every module that assigns `.last_status` or constructs a `ScheduleRun` is enumerated here, so a
    fifth writer reds this test and forces the question "which table translates what it writes?"
    instead of inheriting a silent fallback.
    """
    attr_files: set[str] = set()
    call_files: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "last_status":
                        attr_files.add(rel)
            if isinstance(node, ast.Call):
                called = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                if called == "ScheduleRun":
                    call_files.add(rel)

    assert attr_files == {
        "hooks.py",
        # `web_poll.py` writes an HTTP status CODE (an int) to a different entity's field. Named
        # here so its absence from the tables is a decision on the record, not an oversight.
        "triggers/web_poll.py",
    }, f"the set of modules assigning .last_status changed: {sorted(attr_files)}"
    assert call_files == {
        "gateway.py",
        "triggers/service.py",
        "dashboard/handlers/triggers.py",
    }, f"the set of modules constructing a ScheduleRun changed: {sorted(call_files)}"


def test_every_table_value_is_a_typed_outcome() -> None:
    """A table may only translate INTO the closed vocabulary, or the feed becomes unfilterable."""
    from personalclaw.triggers.models import FIRE_OUTCOMES

    for name, table in (
        ("HOOK_STATUS_TO_OUTCOME", H.HOOK_STATUS_TO_OUTCOME),
        ("SCHEDULE_STATUS_TO_OUTCOME", H.SCHEDULE_STATUS_TO_OUTCOME),
        ("executor.STATUS_TO_OUTCOME", E.STATUS_TO_OUTCOME),
    ):
        for status, outcome in table.items():
            assert outcome in FIRE_OUTCOMES, f"{name}[{status!r}] = {outcome!r} is not typed"


def test_the_inference_handles_the_shape_that_hid_a_status() -> None:
    """The conditional-expression-with-an-`in`-guard shape, driven directly.

    Kept as a unit test rather than trusted implicitly: this is the exact shape the owner's regex
    scan missed, so the rail's ability to read it is itself worth pinning. `x = v if v in (...)
    else "c"` must yield all three values, and an `and`-guarded write must yield its pinned set
    while an `or`-guarded one must NOT (an `or` proves nothing about one operand).
    """
    src = "\n".join(
        [
            "def f(v, w):",
            "    a.last_status = v if v in ('launched', 'queued') else 'ok'",
            "    if w == 1 and v in ('parked',):",
            "        a.last_status = v",
            "    if w == 2 or v in ('never',):",
            "        a.last_status = v",
        ]
    )
    writer = Writer(
        label="probe",
        path="probe",
        table={},
        table_name="probe",
        kind="attr",
        name="last_status",
    )
    tree = ast.parse(src)
    sites: list[tuple[ast.expr, dict[str, set[str]]]] = []
    locals_: dict[str, list[tuple[ast.expr, dict[str, set[str]]]]] = {}
    _scan_body(
        tree.body[0].body,  # type: ignore[attr-defined]
        {},
        writer,
        sites,
        locals_,
        "personalclaw.triggers.history",
    )
    assert len(sites) == 3

    resolved = _values(sites[0][0], sites[0][1], locals_, "personalclaw.triggers.history")
    assert resolved == {"launched", "queued", "ok"}
    assert _values(sites[1][0], sites[1][1], locals_, "personalclaw.triggers.history") == {"parked"}
    with pytest.raises(Unresolved):
        _values(sites[2][0], sites[2][1], locals_, "personalclaw.triggers.history")


def test_a_name_resolves_through_the_module_it_is_read_in() -> None:
    """`INERT_OUTCOMES` in a guard resolves to the SAME six values the writer sees."""
    from personalclaw.triggers.models import INERT_OUTCOMES

    test = ast.parse("outcome not in INERT_OUTCOMES", mode="eval").body
    pins = _pins(test, "personalclaw.triggers.service", holds=False)
    assert pins == {"outcome": set(INERT_OUTCOMES)}
    assert len(pins["outcome"]) == 6


def test_the_two_status_families_do_not_disagree() -> None:
    """A status both tables carry must mean the same thing in both.

    Not a style rule: one feed merges both projections, so `launched` reading as `deferred` from a
    cron and `ran` from a hook would make one word mean two things in one list — which is how the
    hook table's missing `launched` key stayed invisible for so long.
    """
    shared = set(H.HOOK_STATUS_TO_OUTCOME) & set(H.SCHEDULE_STATUS_TO_OUTCOME)
    for status in sorted(shared):
        assert H.HOOK_STATUS_TO_OUTCOME[status] == H.SCHEDULE_STATUS_TO_OUTCOME[status], status
    both = (set(H.HOOK_STATUS_TO_OUTCOME) | set(H.SCHEDULE_STATUS_TO_OUTCOME)) & set(
        E.STATUS_TO_OUTCOME
    )
    for status in sorted(both):
        table: Any = (
            H.HOOK_STATUS_TO_OUTCOME
            if status in H.HOOK_STATUS_TO_OUTCOME
            else H.SCHEDULE_STATUS_TO_OUTCOME
        )
        assert table[status] == E.STATUS_TO_OUTCOME[status], status
