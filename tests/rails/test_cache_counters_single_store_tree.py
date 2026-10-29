"""The source-tree checks split from
``tests/test_cache_counters_single_store.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import source_index
from cache_counters_single_store_shared import _CACHE_NAMES, _accumulators, _target_names

import personalclaw
from personalclaw.dashboard import chat_runner

SRC = Path(personalclaw.__file__).parent


# The sanctioned mutators — the only way the tally is allowed to move.
_TALLY_WRITERS = ("inc_cache_read_tokens", "inc_cache_creation_tokens")


# The two per-turn locals the turn-complete telemetry reads.
_TURN_LOCALS = ("_turn_cache_read_tokens", "_turn_cache_creation_tokens")


# READ-SIDE EXEMPTION, one module, justified and floored below.
#
# ``usage_ledger._fold`` accumulates the cache counts, and it is NOT a second store:
# it is a query-time group-by that reduces rows the ledger ALREADY persisted (from the
# same terminal event that feeds ``Stats``) into a transient per-group dict, the way
# SQL ``SUM()`` would. A second store is a long-lived tally fed from the live turn
# path; this is a fresh dict per call on the read path.
#
# The exemption is not a free pass: ``test_the_read_side_exemption_is_still_read_side``
# proves the accumulator is a distinct object on every call (so no group can share
# state with another, and nothing survives the query), and reds if the fold stops
# accumulating at all — an exemption must not outlive its reason.
_READ_SIDE_FOLDS = frozenset({"usage_ledger.py"})


# TURN-ASSEMBLY EXEMPTION, one module, justified and floored below (#3434, #4015).
#
# ``agents/native/loop_state.py`` sums the two cache counts across a turn's inferences
# (``UsageTotals.add``) and the turn's ending puts the totals on its terminal ``AgentEvent``.
# That is not a second store, by this file's own definition of one — "a long-lived tally fed
# from the live turn path". They are fields of the turn's own record, ``LoopState``, which
# ``NativeAgentRuntime.stream`` builds at each call and which dies with the call, beside the
# input and output sums, which this scan does not flag only because they are not cache-named.
# And the event they assemble is the very thing ``Stats`` is later fed FROM, via
# ``chat_runner`` — it is the tally's SOURCE, not a duplicate of it.
#
# It could not be avoided by assigning instead of accumulating. ``input_tokens`` on that
# same event is a turn total, and every consumer reconstructs the whole served prompt as
# ``input + cache_creation + cache_read`` (``stats.py:160``, ``pricing.py:169``,
# ``llm/openai.py:444``), so a last-inference-only cache count would divide a whole-turn
# numerator by a single-inference denominator. Wrong arithmetic, not a style choice.
#
# Renaming the fields to something this scanner does not match was the other way through,
# and is rejected: it would leave the invariant unchecked while looking checked, which is
# the exact failure mode this file was written to end. So is summing them some other way
# than ``+=``, for the same reason.
#
# The exemption is not a free pass. ``test_the_turn_assembly_exemption_is_still_turn_scoped``
# proves the accumulators are the record's own fields (``self.<field>`` in a method of
# ``UsageTotals``, never a module global, a subscript or another object's attribute), that the
# module still accumulates at all, and that it aggregates ``input_tokens`` in the same function,
# so the numerator and denominator cannot drift apart. And it proves the record cannot outlive
# the call (:func:`record_escapes`): ``stream`` is the only place one is built, into a bare
# local, and the code that holds one only reads and writes its fields and hands it to the
# runtime's own methods — it never stores it on an attribute, in a module global or a
# container, never returns or yields it, and never closes over it.
_TURN_ASSEMBLERS = frozenset({"agents/native/loop_state.py"})
#: The turn's record, and the totals it holds.
_TURN_RECORDS = frozenset({"LoopState", "UsageTotals"})
#: The record's fields that hold a record of their own.
_RECORD_FIELDS = frozenset({"totals"})
#: The one place a record is built.
_TURN_BUILDER = ("agents/native/runtime.py", "NativeAgentRuntime.stream")
#: The modules whose code holds a record: the runtime, its phases, and the record's own.
_RECORD_HOLDERS = (
    "agents/native/runtime.py",
    "agents/native/loop_phases.py",
    "agents/native/loop_state.py",
)


def _py_files() -> list[Path]:
    """Every package module outside test fixtures, from the suite's one source index."""
    return [p for p in source_index.package_files() if "tests_fixtures" not in p.parts]


def _tally_writer_calls() -> dict[str, list[str]]:
    """``{module_relpath: ["inc_cache_read_tokens:3942", ...]}`` over the package."""
    found: dict[str, list[str]] = {}
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(source_index.tree(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _TALLY_WRITERS
            ):
                found.setdefault(rel, []).append(f"{node.func.attr}:{node.lineno}")
    return found


def _turn_local_bindings() -> dict[str, list[ast.AST]]:
    """Every statement in ``chat_runner`` that binds one of the two turn locals."""
    tree = source_index.tree(Path(chat_runner.__file__).resolve())
    bindings: dict[str, list[ast.AST]] = {name: [] for name in _TURN_LOCALS}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in bindings:
                bindings[target.id].append(node)
    return bindings


def test_no_module_on_the_live_path_accumulates_a_prompt_cache_quantity() -> None:
    """THE CENSUS: ``Stats._c`` is the only running total of cached tokens.

    ``Stats.inc`` moves it through a generic ``self._c[key] += n`` — keyed on a
    parameter, not on a cache name — so the sanctioned tally is deliberately invisible
    to this scan and every hit on the live path is a genuine second store.
    """
    hits: list[str] = []
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in _READ_SIDE_FOLDS or rel in _TURN_ASSEMBLERS:
            continue
        hits.extend(_accumulators(source_index.tree(path), rel))
    assert hits == [], (
        "a second prompt-cache accumulator appeared: "
        + ", ".join(hits)
        + " — the per-turn aggregate must derive from `Stats`' counters and the terminal "
        "event, never from a parallel running total"
    )


def test_the_read_side_exemption_is_still_read_side() -> None:
    """FLOOR on the one exemption: prove it is a transient fold, not a tally.

    Two ways this reds. If ``_fold`` stops accumulating the cache counts, the exemption
    is stale and must be deleted rather than carried. If ``_blank_agg`` ever returns a
    shared object, the fold has become exactly the second store this file forbids —
    groups would bleed into each other and the total would survive the query.
    """
    from personalclaw import usage_ledger

    for rel in _READ_SIDE_FOLDS:
        source = source_index.tree(SRC / rel)
        assert _accumulators(source, rel), (
            f"{rel} no longer accumulates a cache quantity — drop it from "
            "_READ_SIDE_FOLDS instead of exempting a module that needs no exemption"
        )

    first, second = usage_ledger._blank_agg(), usage_ledger._blank_agg()
    assert first == second, "the fold's accumulator changed shape between calls"
    assert first is not second, "the fold accumulates into a SHARED dict — that is a second store"

    row = {"cache_read_tokens": 7, "cache_creation_tokens": 3}
    usage_ledger._fold(first, row)
    assert first["cache_read_tokens"] == 7
    # The decisive property: folding into one group left the other group untouched.
    assert second["cache_read_tokens"] == 0, "one group's fold leaked into another"


def _record_class_methods(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """The methods of each turn record class *tree* defines, by class."""
    return {
        cls.name: [fn for fn in cls.body if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name in _TURN_RECORDS
    }


def _is_a_record_field(aug: ast.AugAssign, tree: ast.Module) -> bool:
    """*aug* sums into ``self.<field>`` inside a method of a turn record class."""
    target = aug.target
    if not (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return False
    return any(
        aug is node
        for methods in _record_class_methods(tree).values()
        for fn in methods
        for node in ast.walk(fn)
    )


def _functions(tree: ast.AST, scope: str = "", cls: str = ""):
    """Every function under *tree*, with its qualified name and the class it is a method of."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{scope}{node.name}"
            yield name, node, cls
            yield from _functions(node, f"{name}.", "")
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{scope}{node.name}.", node.name)
        else:
            yield from _functions(node, scope, cls)


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _annotations(tree: ast.AST) -> set[int]:
    """The nodes of every annotation in *tree*: naming a type there stores nothing."""
    found: list[ast.AST | None] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            found.append(node.returns)
            found.extend(
                arg.annotation
                for arg in [
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                    args.vararg,
                    args.kwarg,
                ]
                if arg is not None
            )
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
    return {id(sub) for ann in found if ann is not None for sub in ast.walk(ann)}


def _own(fn: ast.AST):
    """The nodes of *fn*'s own body, not those of a function, lambda or class nested in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _names_a_record(node: ast.AST) -> str:
    if isinstance(node, ast.Name) and node.id in _TURN_RECORDS:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in _TURN_RECORDS:
        return node.attr
    return ""


def record_escapes(
    package: list[tuple[str, ast.Module]], holders: dict[str, ast.Module]
) -> list[str]:
    """Each place the turn's record could outlive the call that built it, as ``path:line why``.

    *package* is every module, ``(path, tree)``; *holders* the modules whose code holds a record
    (:data:`_RECORD_HOLDERS`). A record is built only in :data:`_TURN_BUILDER`, into a bare local.
    In a holder, a name holds a record when it is that local, a parameter annotated with a record
    type, ``self`` in a record's own method, or a bare local assigned one; ``<record>.totals``
    holds one too. Such a name may only be read through (``st.steps``, ``st.next = ...``), be
    aliased by a bare local, or be handed whole to a method a holder defines, called on ``self``
    (or a loop over such methods), whose result is awaited, iterated or read inside
    ``closing_stream``. Anything else — an attribute, a module global, a container, a return, a
    yield, a closure, a call of anything else — is an escape.
    """
    escapes: list[str] = []
    builds = 0
    for rel, tree in package:
        parents, skip = _parents(tree), _annotations(tree)
        record_methods = {
            id(node)
            for methods in _record_class_methods(tree).values()
            for fn in methods
            for node in ast.walk(fn)
        }
        scope_of = {id(node): qualname for qualname, fn, _ in _functions(tree) for node in _own(fn)}
        for node in ast.walk(tree):
            record = _names_a_record(node)
            if not record or id(node) in skip or not isinstance(node.ctx, ast.Load):
                continue
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call) and parent.func is node:
                assign = parents.get(id(parent))
                if (
                    (rel, scope_of.get(id(node))) == _TURN_BUILDER
                    and isinstance(assign, ast.Assign)
                    and assign.value is parent
                    and [type(t) for t in assign.targets] == [ast.Name]
                ):
                    builds += 1
                    continue
                escapes.append(f"{rel}:{node.lineno} builds a {record} outside {_TURN_BUILDER}")
            elif (
                isinstance(parent, ast.keyword)
                and parent.arg == "default_factory"
                and record == "UsageTotals"
                and rel in holders
                and id(node) not in record_methods
            ):
                continue  # LoopState's own ``totals`` field, built with the record
            else:
                escapes.append(f"{rel}:{node.lineno} names {record} where it can be kept")
    if builds != 1:
        escapes.append(f"{_TURN_BUILDER} builds {builds} records, not one")

    methods = {
        fn.name
        for tree in holders.values()
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        for fn in cls.body
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for rel, tree in holders.items():
        parents = _parents(tree)
        for qualname, fn, cls in _functions(tree):
            escapes += _held(rel, qualname, fn, cls, parents, methods)
    return escapes


def _record_types(annotation: ast.AST | None) -> bool:
    return annotation is not None and any(_names_a_record(n) for n in ast.walk(annotation))


def _held(
    rel: str, qualname: str, fn: ast.AST, cls: str, parents: dict[int, ast.AST], methods: set[str]
) -> list[str]:
    """The escapes of a record from one function of a holder module (:func:`record_escapes`)."""
    args = fn.args  # type: ignore[attr-defined]
    held = {
        a.arg
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if _record_types(a.annotation)
    }
    if cls in _TURN_RECORDS and args.args:
        held.add(args.args[0].arg)
    loops = {
        node.target.id
        for node in _own(fn)
        if isinstance(node, (ast.For, ast.AsyncFor))
        and isinstance(node.target, ast.Name)
        and isinstance(node.iter, ast.Tuple)
        and all(
            isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id == "self"
            for e in node.iter.elts
        )
    }
    own = list(_own(fn))
    for node in own:  # the record's builder, and bare locals that alias a record
        if isinstance(node, ast.Assign) and [type(t) for t in node.targets] == [ast.Name]:
            if (isinstance(node.value, ast.Call) and _names_a_record(node.value.func)) or (
                _holds(node.value, held)
            ):
                held.add(node.targets[0].id)  # type: ignore[attr-defined]
    escapes: list[str] = []
    for node in ast.walk(fn):
        if node in own or node is fn:
            continue
        if isinstance(node, ast.Name) and node.id in held:
            escapes.append(f"{rel}:{node.lineno} {qualname} closes over the turn's record")
    for node in own:
        if not (isinstance(node, ast.Name) and node.id in held and isinstance(node.ctx, ast.Load)):
            continue
        expr: ast.AST = node
        parent = parents.get(id(expr))
        # ``<record>.totals`` is a record too: follow it up to where it is used.
        while (
            isinstance(parent, ast.Attribute)
            and parent.value is expr
            and parent.attr in _RECORD_FIELDS
        ):
            expr, parent = parent, parents.get(id(parent))
        if isinstance(parent, ast.Attribute) and parent.value is expr:
            continue  # a field read through, or written
        if isinstance(parent, ast.Assign) and parent.value is expr and len(parent.targets) == 1:
            if isinstance(parent.targets[0], ast.Name) and parent.targets[0].id in held:
                continue  # aliased by a bare local, itself held to these rules
        if (
            isinstance(parent, ast.Call)
            and expr in parent.args
            and _a_call_that_keeps_nothing(parent, parents, methods, loops)
        ):
            continue
        where = ast.unparse(parent) if parent is not None else "?"
        escapes.append(f"{rel}:{node.lineno} {qualname} keeps the turn's record: {where}")
    return escapes


def _holds(value: ast.AST, held: set[str]) -> bool:
    while isinstance(value, ast.Attribute) and value.attr in _RECORD_FIELDS:
        value = value.value
    return isinstance(value, ast.Name) and value.id in held


def _a_call_that_keeps_nothing(
    call: ast.Call, parents: dict[int, ast.AST], methods: set[str], loops: set[str]
) -> bool:
    """*call* hands the record to a holder's own method (or a loop over them), and its result,
    a coroutine or a stream that holds the record, is awaited or read where it stands."""
    func = call.func
    own = (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
        and func.attr in methods
    ) or (isinstance(func, ast.Name) and func.id in loops)
    if not own:
        return False
    parent = parents.get(id(call))
    if isinstance(parent, (ast.Expr, ast.Await)):
        return True
    if isinstance(parent, (ast.For, ast.AsyncFor)) and parent.iter is call:
        return True
    closer = parents.get(id(parent)) if parent is not None else None
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Attribute)
        and parent.func.attr == "closing_stream"
        and isinstance(closer, ast.withitem)
    )


def _holder_trees() -> dict[str, ast.Module]:
    return {rel: source_index.tree(SRC / rel) for rel in _RECORD_HOLDERS}


def _package() -> list[tuple[str, ast.Module]]:
    """The package's modules that can name the turn's record. :func:`record_escapes` reports a
    module only where it names ``LoopState`` or ``UsageTotals``, so a module whose text names
    neither is passed over unwalked: walking every function of every module took the rails job
    past its 30 seconds a test."""
    files = _py_files()
    assert len(files) > 500, f"read {len(files)} modules — vacuous"
    return [
        (path.relative_to(SRC).as_posix(), source_index.tree(path))
        for path in files
        if _may_name_a_record(source_index.text(path))
    ]


def _may_name_a_record(text: str) -> bool:
    """Whether a module of *text* can name the turn's record: :func:`_names_a_record` matches only
    a name or an attribute spelled as one of :data:`_TURN_RECORDS`, which the text then holds."""
    return any(record in text for record in _TURN_RECORDS)


def test_the_turn_assembly_exemption_is_still_turn_scoped() -> None:
    """FLOOR on the #3434 exemption: prove it is the turn's own record, not a tally.

    Four ways this reds, each retiring the exemption's own justification:

    * the module stops accumulating → the exemption is stale and must be deleted;
    * an accumulator becomes anything but ``self.<field>`` in a method of the turn's record — a
      module global, a subscript, another object's attribute — → it can now outlive the turn,
      which is precisely the long-lived tally this file forbids;
    * it stops aggregating ``input_tokens`` in the same function → the served-prompt sum
      ``input + cache_creation + cache_read`` would mix a turn total with one inference's,
      which is the reason accumulating was necessary in the first place;
    * the record itself can outlive the call that built it (:func:`record_escapes`).
    """
    for rel in _TURN_ASSEMBLERS:
        tree = source_index.tree(SRC / rel)

        augs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AugAssign)
            and any(frag in name for name in _target_names(node.target) for frag in _CACHE_NAMES)
        ]
        assert augs, (
            f"{rel} no longer accumulates a cache quantity — drop it from "
            "_TURN_ASSEMBLERS instead of exempting a module that needs no exemption"
        )

        stores = [n.lineno for n in augs if not _is_a_record_field(n, tree)]
        assert stores == [], (
            f"{rel} accumulates a cache quantity into something other than a field of the "
            f"turn's record at line(s) {stores} — that can outlive the turn, which makes it "
            "the second store this file forbids, not turn assembly"
        )

        # The paired invariant: whatever function sums the cache counts must sum
        # input_tokens too, or the served-prompt denominator desynchronises.
        cache_fns = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(fn)
            if isinstance(node, ast.AugAssign)
            and any(frag in name for name in _target_names(node.target) for frag in _CACHE_NAMES)
        }
        input_fns = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(fn)
            if isinstance(node, ast.AugAssign)
            and isinstance(node.value, ast.BoolOp)
            and any(
                isinstance(sub, ast.Attribute) and sub.attr == "input_tokens"
                for sub in ast.walk(node.value)
            )
        }
        assert cache_fns, f"{rel}: no function accumulates a cache quantity"
        assert cache_fns <= input_fns, (
            f"{rel}: {sorted(cache_fns - input_fns)} sums the cache counts without also "
            "summing input_tokens — the whole-served-prompt denominator would mix a turn "
            "total with a single inference's"
        )

    escapes = record_escapes(_package(), _holder_trees())
    assert escapes == [], (
        "the turn's record, which holds its cache sums, can outlive the call that built it — "
        "the second store this file forbids:\n  " + "\n  ".join(escapes)
    )


# ── the turn-scope proof, on trees planted here ──

_PLANTED = {
    "agents/native/loop_state.py": """
class UsageTotals:
    cache_read_tokens: int = 0

    def add(self, usage) -> None:
        self.input_tokens += usage.input_tokens or 0
        self.cache_read_tokens += usage.cache_read_tokens or 0


class LoopState:
    totals: UsageTotals = field(default_factory=UsageTotals)

    def ending(self) -> AgentEvent:
        totals = self.totals
        return AgentEvent(cache_read_tokens=totals.cache_read_tokens)
""",
    "agents/native/loop_phases.py": """
class LoopPhases:
    async def _infer_phase(self, st: LoopState):
        async with turn_streams.closing_stream(self._answer(st)) as answer:
            async for ev in answer:
                yield ev
        self._record(st)
        st.next = Next.CONTINUE

    async def _answer(self, st: LoopState):
        st.totals.add(st.usage)
        yield st.ending()

    def _record(self, st: LoopState) -> None:
        st.steps += 1
""",
    "agents/native/runtime.py": """
class NativeAgentRuntime(LoopPhases):
    async def stream(self, message: str):
        st = LoopState()
        for phase in (self._infer_phase,):
            async with turn_streams.closing_stream(phase(st)) as events:
                async for ev in events:
                    yield ev
        if st.totals.charged:
            yield st.spent()
""",
}


def _planted(rel: str = "", old: str = "", new: str = "") -> list[str]:
    sources = dict(_PLANTED)
    if rel:
        assert old in sources[rel], old
        sources[rel] = sources[rel].replace(old, new)
    trees = {path: ast.parse(text) for path, text in sources.items()}
    return record_escapes(list(trees.items()), trees)


def test_a_module_passed_over_names_no_record() -> None:
    """The scan walks only a module whose text names ``LoopState`` or ``UsageTotals``. One that
    names neither holds nothing :func:`record_escapes` reports, so passing it over drops nothing,
    and every module that holds a record is still walked."""
    for record in sorted(_TURN_RECORDS):
        kept = f"from personalclaw.agents.native.loop_state import {record}\nKEPT = {record}()\n"
        renamed = kept.replace(record, "Elsewhere")
        assert _may_name_a_record(kept) and not _may_name_a_record(renamed), record
        found = record_escapes([("agents/elsewhere.py", ast.parse(kept))], {})
        assert any(e.startswith("agents/elsewhere.py:2 builds") for e in found), found
        passed_over = record_escapes([("agents/elsewhere.py", ast.parse(renamed))], {})
        assert not any(e.startswith("agents/elsewhere.py") for e in passed_over), passed_over
    walked = {rel for rel, _ in _package()}
    assert set(_RECORD_HOLDERS) <= walked, sorted(set(_RECORD_HOLDERS) - walked)


def test_a_planted_record_that_stays_in_its_call_has_no_escape() -> None:
    assert _planted() == []


@pytest.mark.parametrize(
    ("rel", "old", "new", "named"),
    [
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        self._loop = st",
            "self._loop = st",
        ),
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        _LAST.append(st)",
            "_LAST.append(st)",
        ),
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        return st",
            "return st",
        ),
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        self._totals = st.totals",
            "self._totals = st.totals",
        ),
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        self._later = lambda: st.steps",
            "closes over",
        ),
        (
            "agents/native/loop_phases.py",
            "        self._record(st)",
            "        self._remember(st)",
            "self._remember(st)",
        ),
        (
            "agents/native/loop_phases.py",
            "        self._record(st)",
            "        self._pending = self._answer(st)",
            "self._answer(st)",
        ),
        (
            "agents/native/loop_phases.py",
            "        st.steps += 1",
            "        st.steps += 1\n        kept = st\n        self._kept = kept",
            "self._kept = kept",
        ),
        (
            "agents/native/loop_phases.py",
            "    def _record(self, st: LoopState) -> None:",
            "    def _record(self, st: LoopState) -> None:\n        LoopState()",
            "builds a LoopState outside",
        ),
        (
            "agents/native/loop_state.py",
            "        totals = self.totals",
            "        totals = self.totals\n        _ALL.append(self)",
            "_ALL.append(self)",
        ),
        (
            "agents/native/runtime.py",
            "        st = LoopState()",
            "        st = self._st = LoopState()",
            "builds a LoopState outside",
        ),
    ],
)
def test_a_record_kept_past_its_call_is_named(rel: str, old: str, new: str, named: str) -> None:
    escapes = _planted(rel, old, new)
    assert escapes and any(named in escape for escape in escapes), escapes


def test_a_planted_sum_into_a_module_tally_is_not_a_record_field() -> None:
    tree = ast.parse(
        _PLANTED["agents/native/loop_state.py"].replace(
            "self.cache_read_tokens += usage.cache_read_tokens or 0",
            "_TALLY['cache_read_tokens'] += usage.cache_read_tokens or 0",
        )
    )
    augs = [node for node in ast.walk(tree) if isinstance(node, ast.AugAssign)]
    assert [_is_a_record_field(node, tree) for node in augs] == [True, False]


def test_the_only_tally_writers_are_the_stats_mutators() -> None:
    """One module writes the tally, and it writes both halves of it."""
    calls = _tally_writer_calls()
    assert set(calls) == {
        "dashboard/chat_runner.py"
    }, f"expected exactly one module to write the cache tally, found: {sorted(calls)}"
    written = {entry.split(":")[0] for entry in calls["dashboard/chat_runner.py"]}
    # Vacuity: a census finding nothing would satisfy an "is a subset of" assertion.
    assert written == set(_TALLY_WRITERS), f"expected both mutators to be called, found {written}"


def test_the_turn_locals_are_assigned_never_accumulated() -> None:
    """They snapshot the TERMINAL complete event, like ``_turn_input_tokens`` beside them.

    Accumulating them across events would make them a per-turn store in their own
    right AND would desynchronise the hit-rate denominator, since ``_turn_input_tokens``
    is assigned. Wrong arithmetic, not just a duplicated counter.
    """
    bindings = _turn_local_bindings()
    for name, nodes in bindings.items():
        # Vacuity: a renamed local would leave this loop with nothing to check.
        assert nodes, f"{name} is bound nowhere in chat_runner.py — has it been renamed?"
        offenders = [n.lineno for n in nodes if isinstance(n, ast.AugAssign)]
        assert offenders == [], (
            f"{name} is accumulated at line(s) {offenders}; it must be assigned from "
            "the terminal complete event, matching `_turn_input_tokens`"
        )
