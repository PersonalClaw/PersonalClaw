"""`PHF-7` clause 4 — a model-call path that skips the enforced helper must RED the gate.

Every model call is supposed to pass through :func:`personalclaw.guardrails.wrap_model_call_guard`,
which writes the ``model_calls.jsonl`` attempt audit, charges the spend meter and applies
the budget/breaker policy. A provider that reaches a caller UNWRAPPED still works perfectly —
it just spends money and takes actions with no audit row and no budget. That is the failure shape
this rail
exists for: silent, invisible, and indistinguishable from correct behaviour at runtime.

**Measured before it was written, on `origin/main`.** The invariant holds, so this gate ships
at ZERO because zero is the measured population — not as an aspiration:

* ``wrap_model_call_guard`` is called from exactly ONE place in ``src/`` —
  ``providers/provider_bridge._resolve_from_config_registry``, which wraps "at the single
  point where the entry name + model are known" (its own comment).
* ``resolve_provider_for_use_case`` has five provider-returning paths. Four return a value obtained
  from ``_resolve_from_config_registry``. The fifth is the native-agent branch,
  ``_build_native_runtime``, whose docstring records that "its inference ModelProvider is resolved
  through the SAME active-model" path — so its inner model is wrapped too, and the resolver's own
  SCOPE comment says the same of the ACP CLI branch.

So the allowlist below has exactly two entries, both verified to funnel into the chokepoint rather
than taken on trust: :func:`test_the_allowlisted_builder_actually_wraps` asserts the wrap call is
really inside ``_resolve_from_config_registry``, because an allowlist keyed on a NAME would keep
passing if that function stopped wrapping.

**Why AST and not a regex.** The property is structural — "does this return value come from an
allowlisted builder" — and a text scan cannot follow ``x = f(); … ; return x``. That indirection is
the normal shape here (four of the five paths look exactly like it), so a regex rail would either
miss every real bypass or flag every correct path.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
BRIDGE = SRC / "providers" / "provider_bridge.py"

#: The enforced helper. Renaming it must break this rail loudly rather than silently widen it.
CHOKEPOINT = "wrap_model_call_guard"

#: The public resolver every caller uses to obtain a model provider.
RESOLVER = "resolve_provider_for_use_case"

#: Functions allowed to produce the resolver's return value, each verified to funnel into the
#: chokepoint. Adding an entry here is the explicit, reviewable act of declaring a new path
#: guarded — which is the point: it cannot happen by accident.
ALLOWED_BUILDERS = {
    # wraps directly, at the single point where the entry name + model are known
    "_resolve_from_config_registry",
    # native-agent runtime: its INNER inference provider resolves through the same path
    "_build_native_runtime",
    # ACP agent runtime (``acp:<cli>``): an EXTERNAL CLI owns the model call, out of
    # process and on its own vendor account. There is no host-side inference to meter, so
    # there is nothing for ModelCallGuard to wrap — the same reason the pooled ACP claim
    # in SessionManager has never gone through the chokepoint either. The spend it does
    # incur is the CLI's, and it is unobservable to us by construction (documented in
    # docs/agents/acp-parity.md as a protocol boundary, not an audit hole we opened).
    "_build_acp_runtime",
}


def _module() -> ast.Module:
    return ast.parse(BRIDGE.read_text(), filename=str(BRIDGE))


def _find_func(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(
        f"{name}() is gone from {BRIDGE.name}. This rail is pinned to it by name; re-derive the "
        f"rail against whatever replaced it rather than deleting the rail."
    )


def _call_name(node: ast.AST) -> str | None:
    """The called function's bare name, for `f(...)` and `mod.f(...)` alike."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _names_bound_from_allowed(func: ast.AST) -> set[str]:
    """Local names assigned (anywhere in the function) from an allowlisted builder call.

    Deliberately not flow-sensitive: this rail asks "was this name EVER produced by a guarded
    builder", which is the question that makes a bypass visible. A name assigned from both a
    guarded and an unguarded source would pass — but that shape does not exist here, and the
    `return <raw call>` form (the one an accident actually takes) is caught directly below.
    """
    bound: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if _call_name(node.value) in ALLOWED_BUILDERS:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        bound.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None and _call_name(node.value) in ALLOWED_BUILDERS:
                bound.add(node.target.id)
    return bound


def _unguarded_returns(func: ast.AST) -> list[tuple[int, str]]:
    """`(lineno, source-ish)` for each return whose value is not traceable to a guarded builder."""
    guarded_names = _names_bound_from_allowed(func)
    bad: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        v = node.value
        # `return None` / `return ""` — not a provider.
        if isinstance(v, ast.Constant):
            continue
        # `return _resolve_from_config_registry(...)`
        if _call_name(v) in ALLOWED_BUILDERS:
            continue
        # `x = _resolve_from_config_registry(...); … ; return x`
        if isinstance(v, ast.Name) and v.id in guarded_names:
            continue
        bad.append((node.lineno, ast.dump(v)[:120]))
    return bad


class TestTheChokepointStaysSingle:
    def test_the_enforced_helper_is_called_from_exactly_one_place(self):
        """One wrapping site is what makes the invariant checkable at all.

        If a second call site appears, this rail's whole argument — "everything funnels through
        `_resolve_from_config_registry`" — stops holding, and the reviewer needs to look.
        Deliberately counts CALLS, not mentions: the `guardrails/__init__` re-export and the
        `__all__` entry are not call sites, and a rail that counted them would have to be loosened
        to a number nobody
        could interpret.
        """
        sites: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except SyntaxError:  # pragma: no cover - a syntax error is the lint job's failure
                continue
            for node in ast.walk(tree):
                if _call_name(node) == CHOKEPOINT:
                    sites.append(f"{path.relative_to(SRC)}:{node.lineno}")
        assert sites, (
            f"{CHOKEPOINT}() is never CALLED anywhere in src/ — the model-call guard is not being "
            f"applied at all, or the helper was renamed. Either way this rail is now measuring "
            f"nothing, which is the one outcome worse than a red."
        )
        assert len(sites) == 1, (
            f"{CHOKEPOINT}() now has {len(sites)} call sites: {sites}. The guard is meant to be "
            f"applied at ONE chokepoint so every provider is wrapped identically. A second site is "
            f"either a duplicate wrap (double-charging the spend meter) or a divergent one. If the "
            f"fan-out is deliberate, this rail has to be re-derived — do not just raise the number."
        )

    def test_the_allowlisted_builder_actually_wraps(self):
        """The allowlist must not be trusted by NAME.

        `_resolve_from_config_registry` earns its place in ALLOWED_BUILDERS by calling the
        chokepoint. If it stopped, every path this rail credits would be unguarded while the rail
        stayed green — the allowlist would have become a blindfold.
        """
        tree = _module()
        fn = _find_func(tree, "_resolve_from_config_registry")
        calls = {_call_name(n) for n in ast.walk(fn)}
        assert CHOKEPOINT in calls, (
            f"_resolve_from_config_registry() no longer calls {CHOKEPOINT}(), but this rail "
            f"credits every return that flows from it. Either restore the wrap or remove it from "
            f"ALLOWED_BUILDERS — leaving both as-is makes the gate vacuous."
        )


class TestEveryResolverPathIsGuarded:
    def test_the_resolver_has_provider_returning_paths_at_all(self):
        """Vacuity floor. A resolver with no returns to inspect would make the assertion below
        pass over an empty list, which reads exactly like a clean result."""
        fn = _find_func(_module(), RESOLVER)
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
        assert len(returns) >= 3, (
            f"{RESOLVER}() has only {len(returns)} value-returning path(s). Measured on main: 5. "
            f"The function was restructured — re-derive this rail rather than trusting it."
        )

    def test_no_return_path_skips_the_enforced_helper(self):
        fn = _find_func(_module(), RESOLVER)
        bad = _unguarded_returns(fn)
        assert bad == [], (
            f"{RESOLVER}() returns a provider that never passed through {CHOKEPOINT}():\n"
            + "\n".join(f"  line {ln}: {src}" for ln, src in bad)
            + f"\n\nAn unwrapped provider works perfectly and spends money with no "
            f"model_calls.jsonl audit row, no spend-meter charge and no budget/breaker policy. "
            f"Route it through one of {sorted(ALLOWED_BUILDERS)}, or — if you are adding a "
            f"genuinely new guarded builder — add it to ALLOWED_BUILDERS in the same commit."
        )


class TestTheRailItselfDetectsABypass:
    """`PHF-7`'s clause is that a DELIBERATELY raw call reds the gate. That is a property of the
    detector, so it is asserted here rather than only demonstrated by hand: the same analysis is
    run over synthetic modules whose shape is known."""

    @staticmethod
    def _analyze(src: str) -> list[tuple[int, str]]:
        tree = ast.parse(src)
        return _unguarded_returns(_find_func(tree, RESOLVER))

    def test_a_raw_provider_construction_is_caught(self):
        bad = self._analyze(
            "def resolve_provider_for_use_case(u):\n" "    return SomeVendorProvider(model='x')\n"
        )
        assert len(bad) == 1, f"a raw `return SomeVendorProvider(...)` slipped past: {bad}"

    def test_a_raw_provider_via_a_local_name_is_caught(self):
        bad = self._analyze(
            "def resolve_provider_for_use_case(u):\n"
            "    p = SomeVendorProvider(model='x')\n"
            "    return p\n"
        )
        assert len(bad) == 1, f"a raw provider bound to a local name slipped past: {bad}"

    def test_the_two_guarded_shapes_are_NOT_caught(self):
        """The other direction. A detector that flagged the correct forms would be reverted within
        a day, and then nothing would guard the chokepoint at all."""
        direct = self._analyze(
            "def resolve_provider_for_use_case(u):\n"
            "    return _resolve_from_config_registry(u)\n"
        )
        indirect = self._analyze(
            "def resolve_provider_for_use_case(u):\n"
            "    got = _resolve_from_config_registry(u)\n"
            "    if got:\n"
            "        return got\n"
            "    return None\n"
        )
        assert direct == [], f"the direct guarded form was flagged: {direct}"
        assert indirect == [], f"the indirect guarded form was flagged: {indirect}"


def test_the_bridge_module_is_where_this_rail_thinks_it_is():
    """A rail pinned to a path silently measures nothing once the file moves."""
    if not BRIDGE.exists():  # pragma: no cover - the assert below is the message
        pytest.fail(
            f"{BRIDGE} does not exist. The provider bridge moved; re-point this rail rather than "
            f"letting it disappear."
        )
