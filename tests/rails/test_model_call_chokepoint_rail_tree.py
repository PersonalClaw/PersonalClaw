"""The source-tree checks split from
``tests/test_model_call_chokepoint_rail.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from model_call_chokepoint_rail_shared import (
    ALLOWED_BUILDERS,
    BRIDGE,
    RESOLVER,
    SRC,
    _call_name,
    _find_func,
    _unguarded_returns,
)

#: The enforced helper. Renaming it must break this rail loudly rather than silently widen it.
CHOKEPOINT = "wrap_model_call_guard"


def _module() -> ast.Module:
    return ast.parse(BRIDGE.read_text(), filename=str(BRIDGE))


class TestTheChokepointStaysSingle:
    def test_the_enforced_helper_is_called_from_exactly_one_place(self):
        """One wrapping site is what makes the invariant checkable at all.

        If a second call site appears, this rail's whole argument — "everything funnels through
        `metered`" — stops holding, and the reviewer needs to look.
        Deliberately counts CALLS, not mentions: the `guardrails/__init__` re-export and the
        `__all__` entry are not call sites, and a rail that counted them would have to be loosened
        to a number nobody
        could interpret.
        """
        sites: list[str] = []
        for path, tree in source_index.package_trees():
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

        `_resolve_from_config_registry` earns its place in ALLOWED_BUILDERS by calling `metered`,
        and `metered` by calling the chokepoint. If either stopped, every path this rail credits
        would be unguarded while the rail stayed green — the allowlist would have become a
        blindfold.
        """
        tree = _module()
        for fn_name, wraps_with in (
            ("_resolve_from_config_registry", "metered"),
            ("metered", CHOKEPOINT),
        ):
            calls = {_call_name(n) for n in ast.walk(_find_func(tree, fn_name))}
            assert wraps_with in calls, (
                f"{fn_name}() no longer calls {wraps_with}(), but this rail credits every return "
                f"that flows from _resolve_from_config_registry(). Either restore the wrap or "
                f"remove it from ALLOWED_BUILDERS — leaving both as-is makes the gate vacuous."
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
