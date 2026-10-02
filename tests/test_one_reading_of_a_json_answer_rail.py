"""No check of a model's JSON answer, and no parser of one, reads the answer with ``json`` itself.

A model's JSON answer is read one way, ``llm_helpers._parse_llm`` behind ``parse_llm_json`` /
``parse_llm_json_list`` / ``parse_llm_json_value``: by the one-shot call that asked for it (its
``output_type`` check), by the caller's check of its shape, and by every parser of it. Measured
before this rail: the triage's proposal check read the answer with ``json.loads`` while the call
had accepted it in a markdown fence, so a usable answer moved the chain on and the digest degraded;
the triage gate, the prompt-card import, a workflow's infer node, the knowledge extractor, the
judges and others each kept a reading of their own, and they disagreed.

What it reads, by AST, over ``src/personalclaw``:

* **Checks.** Every check handed to a model call: ``expecting(check)`` and a ``validate=check``
  keyword. Each is resolved to its function (module-level, nested, a method through
  ``self``/``cls``, an import, a lambda). A parameter or a local value passed on is plumbing; any
  other expression is a failure, because the rail cannot read it.
* **Answers.** In each function, the names a model's answer is bound to: an assignment from a call
  made under ``expecting``, or from a call that names a check or an ``output_type``; and the answer
  an ``OutputContractError`` carries.
* **Readers no call marks:** the judges (``parse_judge_json``, the eval judge, the loop's
  stage-gate judge), the parsers of the step and artifact files a planner writes, and a workflow
  planner's emissions.

From each, the answer is followed by name through every call it is passed to, across modules, and
decoding it with ``json.loads``, ``raw_decode`` or a ``JSONDecoder`` anywhere but the one reading
is a failure.

A zero needs a positive control: the same scan runs over fixture modules that read an answer a
second way in each of those shapes (the measured one first) and must find each one, and over a
fixture that reads only through the one reading and must find none; and the scan of the tree must
reach the checks and parsers named below.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The one reading: the only functions a model's JSON answer may be decoded in.
READING_MODULE = "personalclaw.llm_helpers"
READING = frozenset(
    {
        "_parse_llm",
        "_document",
        "_first_value",
        "parse_llm_json",
        "parse_llm_json_list",
        "parse_llm_json_value",
    }
)

#: The calls that hand a check to a model call, and which argument the check is. A ``validate=``
#: keyword is a check on any call.
CHECK_ARGUMENT = {"expecting": 0}

#: Readers of a model's JSON that no call marks (it names no check and no output type), so nothing
#: above finds them: (module, function, how the answer reaches it). The judges; the planner's step
#: and artifact files, which a model writes; and a workflow planner's emissions.
UNMARKED_READERS = (
    ("personalclaw.workflows.judge_contract", "parse_judge_json", "argument"),
    ("personalclaw.eval.judge", "LLMJudge.judge_turn", "stream"),
    ("personalclaw.loop.judge", "_parse_verdict", "argument"),
    ("personalclaw.loop.goal_plan_briefs", "parse_artifact_sentinel", "argument"),
    ("personalclaw.loop.code_plan_briefs", "parse_steps_sentinel", "argument"),
    ("personalclaw.loop.code_plan_briefs", "parse_artifact_sentinel", "argument"),
    ("personalclaw.loop.design_plan_briefs", "parse_steps_sentinel", "argument"),
    ("personalclaw.loop.design_plan_briefs", "parse_artifact_sentinel", "argument"),
    ("personalclaw.workflows.generation", "parse_emission", "argument"),
    ("personalclaw.workflows.revision", "parse_revision", "argument"),
)

#: A module can hold a check or an answer only when its text names one of these.
_MARKERS = ("expecting(", "validate=", "output_type=")
_MOST_HOPS = 8

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
FuncNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


# ── the source tree ─────────────────────────────────────────────────────────


class Module:
    def __init__(self, name: str, text: str, *, package: bool) -> None:
        self.name = name
        self.package = package
        self.tree = ast.parse(text)
        self._scopes: dict[int, tuple[set[str], set[str], dict[str, FuncNode]]] = {}
        self.parent: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        self.top = {n.name: n for n in self.tree.body if isinstance(n, _FUNCTIONS)}
        self.classes = {
            n.name: {m.name: m for m in n.body if isinstance(m, _FUNCTIONS)}
            for n in self.tree.body
            if isinstance(n, ast.ClassDef)
        }
        #: alias → (module, name) of every ``from personalclaw… import …`` in the module.
        self.imports: dict[str, tuple[str, str]] = {}
        #: Names the json module is bound to, and names ``json.loads`` is bound to.
        self.json_modules: set[str] = set()
        self.json_loads: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self.json_modules.update(a.asname or a.name for a in node.names if a.name == "json")
            elif isinstance(node, ast.ImportFrom):
                base = self._absolute(node)
                for alias in node.names:
                    if base == "json" and alias.name == "loads":
                        self.json_loads.add(alias.asname or alias.name)
                    elif base.startswith("personalclaw"):
                        self.imports[alias.asname or alias.name] = (base, alias.name)

    def _absolute(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        parts = self.name.split(".")
        package = parts if self.package else parts[:-1]
        base = package[: len(package) - (node.level - 1)]
        return ".".join([*base, *([node.module] if node.module else [])])

    def scope(self, fn: FuncNode) -> tuple[set[str], set[str], dict[str, FuncNode]]:
        """*fn*'s parameters, the names its own body binds, and the functions defined in it."""
        key = id(fn)
        if key not in self._scopes:
            nodes = list(own_nodes(fn))
            self._scopes[key] = (
                set(params(fn)),
                {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)},
                {n.name: n for n in nodes if isinstance(n, _FUNCTIONS)},
            )
        return self._scopes[key]

    def around(self, node: ast.AST) -> Iterator[ast.AST]:
        current = self.parent.get(node)
        while current is not None:
            yield current
            current = self.parent.get(current)

    def functions_around(self, node: ast.AST) -> list[FuncNode]:
        return [n for n in self.around(node) if isinstance(n, (*_FUNCTIONS, ast.Lambda))]

    def class_around(self, node: ast.AST) -> ast.ClassDef | None:
        return next((n for n in self.around(node) if isinstance(n, ast.ClassDef)), None)

    def qualname(self, fn: FuncNode) -> str:
        if isinstance(fn, ast.Lambda):
            return f"<lambda>:{fn.lineno}"
        names = [fn.name] + [
            n.name for n in self.around(fn) if isinstance(n, (*_FUNCTIONS, ast.ClassDef))
        ]
        return ".".join(reversed(names))


class Sources:
    """Module sources by dotted name: the tree under ``src/personalclaw``, with fixture modules
    laid over it. Only the fixtures are scanned for checks and answers when there are any."""

    def __init__(self, fixtures: dict[str, str] | None = None) -> None:
        self.fixtures = dict(fixtures or {})
        self._modules: dict[str, Module | None] = {}

    def module(self, name: str) -> Module | None:
        if name not in self._modules:
            self._modules[name] = self._load(name)
        return self._modules[name]

    def _load(self, name: str) -> Module | None:
        if name in self.fixtures:
            return Module(name, self.fixtures[name], package=False)
        parts = name.split(".")
        if parts[0] != "personalclaw":
            return None
        here = SRC.joinpath(*parts[1:])
        for path, package in ((here.with_suffix(".py"), False), (here / "__init__.py", True)):
            if path.is_file():
                return Module(name, path.read_text("utf-8"), package=package)
        return None

    def scanned(self) -> list[str]:
        if self.fixtures:
            return sorted(self.fixtures)
        names = []
        for path in sorted(SRC.rglob("*.py")):
            if any(marker in path.read_text("utf-8") for marker in _MARKERS):
                rel = path.relative_to(SRC.parent).with_suffix("")
                parts = list(rel.parts[:-1]) if rel.name == "__init__" else list(rel.parts)
                names.append(".".join(parts))
        return names


@dataclass(frozen=True)
class Fn:
    module: str
    node: FuncNode
    name: str

    def __hash__(self) -> int:
        return hash((self.module, id(self.node)))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Fn) and (self.module, self.node) == (other.module, other.node)


PLUMBING = "plumbing"


def own_nodes(fn: FuncNode) -> Iterator[ast.AST]:
    """Every node of *fn*'s own scope: a function, lambda or class defined in it is yielded but
    not entered."""
    stack: list[ast.AST] = [fn.body] if isinstance(fn, ast.Lambda) else list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def params(fn: FuncNode) -> list[str]:
    a = fn.args
    named = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return named + [x.arg for x in (a.vararg, a.kwarg) if x is not None]


def stored(target: ast.AST) -> set[str]:
    """The names an assignment target binds (an attribute or item it sets binds none)."""
    return {
        n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }


def call_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def resolve(
    sources: Sources,
    mod: Module,
    at: ast.AST,
    expr: ast.AST,
    within: frozenset[str] | None = None,
) -> Fn | str | None:
    """The function *expr* names where it appears, :data:`PLUMBING` for a parameter or local value
    passed on, or ``None`` when it cannot be read (or, with *within*, lives outside those
    modules)."""
    if isinstance(expr, ast.Lambda):
        return Fn(mod.name, expr, mod.qualname(expr))
    if isinstance(expr, ast.Name):
        for fn in mod.functions_around(at):
            names, local, nested = mod.scope(fn)
            if expr.id in names:
                return PLUMBING
            if expr.id in nested:
                return Fn(mod.name, nested[expr.id], mod.qualname(nested[expr.id]))
            if expr.id in local:
                return PLUMBING
        return resolve_global(sources, mod, expr.id, within=within)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        owner = expr.value.id
        if owner in ("self", "cls"):
            cls = mod.class_around(at)
            method = mod.classes.get(cls.name, {}).get(expr.attr) if cls is not None else None
            return Fn(mod.name, method, mod.qualname(method)) if method is not None else None
        if owner in mod.classes:
            method = mod.classes[owner].get(expr.attr)
            return Fn(mod.name, method, mod.qualname(method)) if method is not None else None
        if owner in mod.imports:
            base, name = mod.imports[owner]
            if within is None or f"{base}.{name}" in within:
                target = sources.module(f"{base}.{name}")
                if target is not None:
                    return resolve_global(sources, target, expr.attr, within=within)
    return None


def resolve_global(
    sources: Sources,
    mod: Module,
    name: str,
    hops: int = 0,
    within: frozenset[str] | None = None,
) -> Fn | None:
    if name in mod.top:
        return Fn(mod.name, mod.top[name], name)
    if name in mod.imports and hops < 4:
        base, imported = mod.imports[name]
        if within is None or base in within:
            target = sources.module(base)
            if target is not None:
                return resolve_global(sources, target, imported, hops + 1, within)
    return None


def is_decode(mod: Module, call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in mod.json_loads
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "raw_decode":
        return True
    if func.attr == "loads":
        return isinstance(func.value, ast.Name) and func.value.id in mod.json_modules
    return (
        func.attr == "decode"
        and isinstance(func.value, ast.Call)
        and call_name(func.value) == "JSONDecoder"
    )


def mentions(expr: ast.AST | None, names: set[str]) -> bool:
    return expr is not None and any(
        isinstance(n, ast.Name) and n.id in names for n in ast.walk(expr)
    )


#: Methods whose result is still (a piece of) the text they were called on or handed: a string's
#: own, ``"".join`` of the pieces, and a regular expression's search of it and its groups.
_TEXT_METHODS = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "casefold",
        "replace",
        "removeprefix",
        "removesuffix",
        "split",
        "rsplit",
        "splitlines",
        "partition",
        "rpartition",
        "join",
        "search",
        "match",
        "fullmatch",
        "findall",
        "finditer",
        "sub",
        "group",
        "groups",
    }
)
#: Builtins whose result is the text, or the texts, they were handed.
_TEXT_BUILTINS = frozenset({"str", "zip", "enumerate", "reversed", "sorted", "list", "tuple"})


def carries(expr: ast.AST | None, names: set[str]) -> bool:
    """Whether *expr* is the answer's text, or a piece of it, while *names* hold it. A value
    parsed from the text is not: the scan follows the text, not what was read from it."""
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        return expr.id in names
    if isinstance(expr, ast.Attribute):
        return expr.attr == "raw" and carries(expr.value, names)
    if isinstance(expr, (ast.Subscript, ast.Starred, ast.NamedExpr)):
        return carries(expr.value, names)
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return any(carries(e, names) for e in expr.elts)
    if isinstance(expr, ast.BoolOp):
        return any(carries(v, names) for v in expr.values)
    if isinstance(expr, ast.IfExp):
        return carries(expr.body, names) or carries(expr.orelse, names)
    if isinstance(expr, ast.Call):
        func = expr.func
        handed = any(carries(a, names) for a in expr.args)
        if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
            return carries(func.value, names) or handed
        if isinstance(func, ast.Name) and func.id in _TEXT_BUILTINS:
            return handed
        if isinstance(func, ast.Name) and func.id == "getattr" and len(expr.args) >= 2:
            name = expr.args[1]
            return carries(expr.args[0], names) and getattr(name, "value", None) == "raw"
    return False


# ── the scan ────────────────────────────────────────────────────────────────


@dataclass
class Report:
    findings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    checks: set[tuple[str, str]] = field(default_factory=set)
    readers: set[tuple[str, str]] = field(default_factory=set)
    #: Calls that hand an answer to the one reading.
    read_once: int = 0


class Scan:
    def __init__(self, sources: Sources) -> None:
        self.sources = sources
        self.report = Report()
        self._seen: set[tuple[Fn, frozenset[str]]] = set()
        self._scanned = frozenset(sources.scanned())
        self._returning: dict[Fn, bool] = {}

    def run(self) -> Report:
        for name in sorted(self._scanned):
            mod = self.sources.module(name)
            if mod is not None:
                self._checks(mod)
                self._answers(mod)
        if not self.sources.fixtures:
            self._unmarked()
        return self.report

    # checks ──────────────────────────────────────────────────────────────

    def _checks(self, mod: Module) -> None:
        for call in (n for n in ast.walk(mod.tree) if isinstance(n, ast.Call)):
            for expr in self._check_exprs(call):
                target = resolve(self.sources, mod, call, expr)
                if target == PLUMBING:
                    continue
                if not isinstance(target, Fn):
                    self.report.unresolved.append(
                        f"{mod.name}:{call.lineno} hands a model call a check the rail cannot "
                        f"read ({ast.unparse(expr)[:60]}); pass a function"
                    )
                    continue
                self.report.checks.add((target.module, target.name))
                self._follow(target, set(self._leading(target)[:1]))

    @staticmethod
    def _check_exprs(call: ast.Call) -> list[ast.AST]:
        out = [k.value for k in call.keywords if k.arg == "validate"]
        out = [e for e in out if not isinstance(e, ast.Constant)]
        index = CHECK_ARGUMENT.get(call_name(call))
        if index is not None and len(call.args) > index:
            out.append(call.args[index])
        return out

    # answers ─────────────────────────────────────────────────────────────

    def _answers(self, mod: Module) -> None:
        for fn in (n for n in ast.walk(mod.tree) if isinstance(n, _FUNCTIONS)):
            seeds = self._bound_answers(mod, fn)
            handlers = self._contract_handlers(fn)
            if seeds or handlers:
                self._follow(Fn(mod.name, fn, mod.qualname(fn)), seeds, handlers)

    def _bound_answers(self, mod: Module, fn: FuncNode) -> set[str]:
        """Names *fn* binds a model's answer to: from a call under ``expecting``, or from a call
        that names a check or an output type."""
        names: set[str] = set()
        for node in own_nodes(fn):
            if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                isinstance(i.context_expr, ast.Call) and call_name(i.context_expr) == "expecting"
                for i in node.items
            ):
                for statement in node.body:
                    for inner in self._statement_nodes(statement):
                        if isinstance(inner, (ast.Assign, ast.AnnAssign)) and any(
                            isinstance(n, (ast.Await, ast.Call)) for n in ast.walk(inner.value)
                        ):
                            names |= self._targets(inner)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                if any(
                    isinstance(n, ast.Call)
                    and (self._asks_for_structure(mod, n) or self._returns_an_answer(mod, n))
                    for n in ast.walk(node.value)
                ):
                    names |= self._targets(node)
        return names

    def _returns_an_answer(self, mod: Module, call: ast.Call) -> bool:
        """Whether *call* (or a call it is handed, as ``asyncio.run(_call())``) is to a function
        that returns the answer of a call asking for structure. Such a function can only be in a
        module the scan reads, so no other module is loaded to find out."""
        for inner in (call, *(a for a in call.args if isinstance(a, ast.Call))):
            target = resolve(self.sources, mod, inner, inner.func, within=self._scanned)
            if not isinstance(target, Fn) or isinstance(target.node, ast.Lambda):
                continue
            if target not in self._returning:
                target_mod = self.sources.module(target.module)
                self._returning[target] = target_mod is not None and any(
                    isinstance(node, ast.Return)
                    and node.value is not None
                    and any(
                        isinstance(n, ast.Call) and self._asks_for_structure(target_mod, n)
                        for n in ast.walk(node.value)
                    )
                    for node in own_nodes(target.node)
                )
            if self._returning[target]:
                return True
        return False

    def _asks_for_structure(self, mod: Module, call: ast.Call) -> bool:
        for keyword in call.keywords:
            if keyword.arg == "validate" and not isinstance(keyword.value, ast.Constant):
                if resolve(self.sources, mod, call, keyword.value) != PLUMBING:
                    return True
            if keyword.arg == "output_type":
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value is None:
                    continue
                if isinstance(value, ast.Name) and self._is_local(mod, call, value.id):
                    continue
                return True
        return False

    @staticmethod
    def _is_local(mod: Module, at: ast.AST, name: str) -> bool:
        return any(
            name in mod.scope(fn)[0] or name in mod.scope(fn)[1] for fn in mod.functions_around(at)
        )

    @staticmethod
    def _statement_nodes(statement: ast.AST) -> Iterator[ast.AST]:
        stack = [statement]
        while stack:
            node = stack.pop()
            yield node
            if not isinstance(node, _SCOPES):
                stack.extend(ast.iter_child_nodes(node))

    @staticmethod
    def _targets(node: ast.Assign | ast.AnnAssign) -> set[str]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return set().union(*(stored(t) for t in targets))

    @staticmethod
    def _contract_handlers(fn: FuncNode) -> list[tuple[ast.ExceptHandler, str]]:
        return [
            (node, node.name)
            for node in own_nodes(fn)
            if isinstance(node, ast.ExceptHandler)
            and node.name
            and node.type is not None
            and any(
                getattr(n, "id", getattr(n, "attr", "")) == "OutputContractError"
                for n in ast.walk(node.type)
            )
        ]

    @staticmethod
    def _accumulated(fn: FuncNode) -> set[str]:
        def texty(expr: ast.AST) -> bool:
            return any(isinstance(n, ast.Attribute) and n.attr == "text" for n in ast.walk(expr))

        names: set[str] = set()
        for node in own_nodes(fn):
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                if isinstance(node.op, ast.Add) and texty(node.value):
                    names.add(node.target.id)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and any(texty(a) for a in node.args)
            ):
                names.add(node.func.value.id)
        return names

    def _unmarked(self) -> None:
        for module, qualname, how in UNMARKED_READERS:
            mod = self.sources.module(module)
            assert mod is not None, f"{module} is gone; re-derive the judges' entry in this rail"
            *owner, name = qualname.split(".")
            node = (mod.classes.get(owner[0], {}) if owner else mod.top).get(name)
            assert node is not None, f"{module}:{qualname} is gone; update UNMARKED_READERS"
            target = Fn(module, node, qualname)
            if how == "argument":
                seeds = set(self._leading(target)[:1])
            else:
                seeds = self._accumulated(node)
            assert seeds, f"{module}:{qualname} reads no answer the rail can find"
            self._follow(target, seeds)

    # following an answer ─────────────────────────────────────────────────

    @staticmethod
    def _leading(target: Fn) -> list[str]:
        """*target*'s positional parameters an argument binds to, ``self``/``cls`` dropped."""
        node = target.node
        names = [x.arg for x in (*node.args.posonlyargs, *node.args.args)]
        method = not isinstance(node, ast.Lambda) and names[:1] in (["self"], ["cls"])
        static = not isinstance(node, ast.Lambda) and any(
            getattr(d, "id", "") == "staticmethod" for d in node.decorator_list
        )
        return names[1:] if method and not static else names

    @staticmethod
    def _binds(node: ast.AST, names: set[str], holds) -> set[str]:
        """The names *node* binds to a value *holds* says holds the answer, given *names* do."""
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            if holds(node.value, names):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                return set().union(*(stored(t) for t in targets))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if holds(node.iter, names):
                return stored(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            if holds(node.context_expr, names):
                return stored(node.optional_vars)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend", "insert")
            and isinstance(node.func.value, ast.Name)
            and any(holds(a, names) for a in node.args)
        ):
            return {node.func.value.id}
        return set()

    def _follow(
        self,
        fn: Fn,
        seeds: set[str],
        handlers: list[tuple[ast.ExceptHandler, str]] | None = None,
        hops: int = 0,
    ) -> None:
        key = (fn, frozenset(seeds) | frozenset(name for _h, name in handlers or ()))
        if key in self._seen or hops > _MOST_HOPS:
            return
        self._seen.add(key)
        mod = self.sources.module(fn.module)
        assert mod is not None
        self.report.readers.add((fn.module, fn.name))
        nodes = list(own_nodes(fn.node))
        scoped: dict[int, set[str]] = {}
        for handler, name in handlers or ():
            for statement in handler.body:
                for inner in self._statement_nodes(statement):
                    scoped.setdefault(id(inner), set()).add(name)
        # Two readings of "holds the answer": `tainted`, the answer's text (or a piece of it),
        # which is what is followed into another function; and `derived`, anything worked out
        # from it in this function, which no decode may take either.
        tainted = set(seeds)
        derived = set(seeds)
        changed = True
        while changed:
            changed = False
            for node in nodes:
                extra = scoped.get(id(node), set())
                for names, holds in ((tainted, carries), (derived, mentions)):
                    found = self._binds(node, names | extra, holds)
                    if found - names:
                        names |= found
                        changed = True
            derived |= tainted
        for call in (n for n in nodes if isinstance(n, ast.Call)):
            live = tainted | scoped.get(id(call), set())
            if is_decode(mod, call):
                seen = derived | live
                if any(mentions(a, seen) for a in [*call.args, *(k.value for k in call.keywords)]):
                    self.report.findings.append(
                        f"{fn.module}:{call.lineno} {fn.name} decodes a model's answer with json "
                        "itself; read it with llm_helpers.parse_llm_json / parse_llm_json_list / "
                        "parse_llm_json_value, the reading its call used"
                    )
                continue
            positional = [carries(a, live) for a in call.args]
            keywords = {k.arg: carries(k.value, live) for k in call.keywords}
            if not (any(positional) or any(keywords.values())):
                continue
            target = resolve(self.sources, mod, call, call.func)
            if not isinstance(target, Fn):
                continue
            if target.module == READING_MODULE and target.name in READING:
                self.report.read_once += 1
                continue
            names = self._leading(target)
            reached: set[str] = set()
            for index, holds in enumerate(positional):
                if not holds:
                    continue
                if isinstance(call.args[index], ast.Starred) or index >= len(names):
                    reached |= set(params(target.node))
                else:
                    reached.add(names[index])
            for name, holds in keywords.items():
                if holds:
                    reached |= {name} if name in params(target.node) else set(params(target.node))
            if reached:
                self._follow(target, reached, hops=hops + 1)


def scan(fixtures: dict[str, str] | None = None) -> Report:
    return Scan(Sources(fixtures)).run()


@pytest.fixture(scope="module")
def tree() -> Report:
    return scan()


# ── the tree ────────────────────────────────────────────────────────────────


def test_no_check_or_parser_reads_a_json_answer_a_second_way(tree):
    assert tree.findings == []


def test_every_check_handed_to_a_model_call_can_be_read(tree):
    assert tree.unresolved == []


#: Checks and readers the scan must reach, so a zero above is the tree's and not a blind scan's.
MUST_CHECK = {
    ("personalclaw.proactive.proposals", "proposals_problem"),
    ("personalclaw.proactive.gate", "dispositions_problem"),
    ("personalclaw.dashboard.chat_followups", "_followups_problem"),
    ("personalclaw.suggestions", "_suggestions_problem"),
    ("personalclaw.knowledge.extractor", "EntityExtractor.answer_problem"),
    ("personalclaw.durability.conflict_merge", "_merge_problem"),
    ("personalclaw.nl_to_cron", "nl_to_cron._problem"),
    ("personalclaw.dashboard.chat_title", "_title_problem"),
    ("personalclaw.llm_helpers", "json_object_problem"),
}
MUST_READ = {
    ("personalclaw.proactive.proposals", "parse_proposals"),
    ("personalclaw.proactive.gate", "parse_gate_output"),
    ("personalclaw.inbox_sorting", "parse_verdicts"),
    ("personalclaw.dashboard.chat_followups", "_parse_followups"),
    ("personalclaw.suggestions", "_parse_suggestions"),
    ("personalclaw.knowledge.extractor", "EntityExtractor._parse_response"),
    ("personalclaw.packs.prompt_cards", "convert_card"),
    ("personalclaw.workflows.engine", "dispatch_infer"),
    ("personalclaw.workflows.judge_contract", "parse_judge_json"),
    ("personalclaw.eval.judge", "LLMJudge.judge_turn"),
    ("personalclaw.loop.judge", "_parse_verdict"),
    ("personalclaw.loop.kinds.design", "DesignKind._plan_phases"),
    ("personalclaw.knowledge.retrieval", "_run_rerank_prompt"),
}


def test_the_scan_reaches_the_checks_and_parsers_it_guards(tree):
    assert MUST_CHECK - tree.checks == set()
    assert MUST_READ - tree.readers == set()
    assert tree.read_once >= 25, "answers reach the one reading from most readers"


def test_the_one_reading_is_where_an_answer_is_decoded():
    mod = Sources().module(READING_MODULE)
    assert mod is not None
    decoding = {
        mod.qualname(fn)
        for fn in ast.walk(mod.tree)
        if isinstance(fn, _FUNCTIONS)
        for call in own_nodes(fn)
        if isinstance(call, ast.Call) and is_decode(mod, call)
    }
    assert decoding, "the one reading decodes the answer"
    assert decoding <= READING, f"{READING_MODULE} decodes JSON outside the one reading"


# ── the positive control ────────────────────────────────────────────────────

_SECOND_READINGS = {
    "the measured one: a check that reads the answer with json.loads": (
        """
import json
from personalclaw.llm_helpers import expecting


def shape_problem(raw):
    try:
        json.loads(raw)
    except ValueError:
        return "not JSON"
    return ""


async def run(ask):
    with expecting(shape_problem):
        raw = await ask("p")
    return raw
""",
        "shape_problem",
    ),
    "a parser that reads the answer a second way": (
        """
import json
from personalclaw.llm_helpers import expecting, parse_llm_json


def shape_problem(raw):
    return "" if parse_llm_json(raw) is not None else "no JSON object"


def parse_shape(raw):
    return json.loads(raw)


async def run(ask):
    with expecting(shape_problem):
        raw = await ask("p")
    return parse_shape(raw)
""",
        "parse_shape",
    ),
    "an inline decode of a slice of the answer": (
        """
import json as _json
from personalclaw.llm_helpers import expecting, parse_llm_json_list


def rows_problem(raw):
    return "" if parse_llm_json_list(raw) is not None else "no JSON array"


async def plan(ask):
    with expecting(rows_problem):
        raw = await ask("p")
    start, end = raw.find("["), raw.rfind("]")
    return _json.loads(raw[start : end + 1])
""",
        "plan",
    ),
    "a lambda check on a direct call": (
        """
import json
from personalclaw.llm_helpers import one_shot_completion


def count_problem(raw, count):
    return "" if len(json.loads(raw)) == count else "short"


async def sort(count):
    return await one_shot_completion("p", validate=lambda answer: count_problem(answer, count))
""",
        "count_problem",
    ),
    "a method check": (
        """
import json
import re
from personalclaw.llm_helpers import expecting


class Reader:
    @classmethod
    def answer_problem(cls, response):
        found = re.search(r"[{].*[}]", response)
        return "" if found and json.loads(found.group()) else "no object"

    async def read(self, pool):
        with expecting(self.answer_problem):
            return await pool.send("p")
""",
        "Reader.answer_problem",
    ),
    "the answer an OutputContractError carries, read a second way": (
        """
import json
from personalclaw.guardrails.failure import OutputContractError
from personalclaw.llm_helpers import expecting, parse_llm_json


def shape_problem(raw):
    return "" if parse_llm_json(raw) is not None else "no JSON object"


class Reader:
    def _parse(self, response):
        return json.JSONDecoder().raw_decode(response)[0]

    async def read(self, pool):
        try:
            with expecting(shape_problem):
                response = await pool.send("p")
        except OutputContractError as exc:
            return self._parse(exc.raw)
        return parse_llm_json(response)
""",
        "Reader._parse",
    ),
    "a piece cut from the answer by a helper, then decoded": (
        """
import json
import re
from personalclaw.llm_helpers import expecting


def _first_object(raw):
    start = raw.find("{")
    return raw[start:] if start >= 0 else None


def answer_problem(raw):
    candidates = [_first_object(raw)]
    greedy = re.search(r"[{].*[}]", raw)
    if greedy:
        candidates.append(greedy.group())
    for candidate in candidates:
        try:
            return "" if json.loads(candidate) else "empty"
        except ValueError:
            continue
    return "no JSON object"


async def classify(ask):
    with expecting(answer_problem):
        return await ask("p")
""",
        "answer_problem",
    ),
    "an output_type call read a second way (the prompt card's shape)": (
        """
import json
from personalclaw.llm_helpers import one_shot_completion


async def convert(prompt):
    raw = await one_shot_completion(prompt, output_type=dict)
    return json.loads(raw)
""",
        "convert",
    ),
}


@pytest.mark.parametrize("shape", sorted(_SECOND_READINGS))
def test_the_scan_finds_an_answer_read_a_second_way(shape):
    source, function = _SECOND_READINGS[shape]
    report = scan({"personalclaw.rail_fixture": source})
    assert any(f" {function} decodes" in finding for finding in report.findings), report


def test_the_scan_follows_a_check_imported_from_another_module():
    report = scan(
        {
            "personalclaw.rail_fixture_checks": (
                "import json\n\n\ndef shape_problem(raw):\n    json.loads(raw)\n    return ''\n"
            ),
            "personalclaw.rail_fixture": (
                "from personalclaw.llm_helpers import expecting\n"
                "from personalclaw.rail_fixture_checks import shape_problem\n\n\n"
                "async def run(ask):\n"
                "    with expecting(shape_problem):\n"
                "        return await ask('p')\n"
            ),
        }
    )
    assert any(
        f.startswith("personalclaw.rail_fixture_checks:") and " shape_problem decodes" in f
        for f in report.findings
    ), report


def test_the_scan_reads_no_second_reading_into_answers_read_once():
    """The negative control: each shape above, reading only through the one reading."""
    report = scan({"personalclaw.rail_fixture": """
from personalclaw.guardrails.failure import OutputContractError
from personalclaw.llm_helpers import expecting, one_shot_completion, parse_llm_json


def shape_problem(raw):
    return "" if parse_llm_json(raw) is not None else "no JSON object"


def parse_shape(raw):
    return parse_llm_json(raw)


async def run(ask):
    try:
        with expecting(shape_problem):
            raw = await ask("p")
    except OutputContractError as exc:
        raw = exc.raw
    return parse_shape(raw)


async def convert(prompt):
    raw = await one_shot_completion(prompt, output_type=dict, validate=lambda a: shape_problem(a))
    return parse_llm_json(raw)
"""})
    assert report.findings == []
    assert {"shape_problem"} <= {name for _m, name in report.checks}
    assert {"run", "convert", "parse_shape"} <= {name for _m, name in report.readers}
    assert report.read_once >= 3


def test_a_check_the_scan_cannot_read_is_a_failure():
    report = scan(
        {
            "personalclaw.rail_fixture": (
                "from personalclaw.llm_helpers import expecting\n\n\n"
                "async def run(ask, checks):\n"
                "    with expecting(checks['shape']):\n"
                "        return await ask('p')\n"
            )
        }
    )
    assert [u for u in report.unresolved if "personalclaw.rail_fixture:5" in u], report
