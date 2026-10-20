"""A model-call path that skips the enforced helper must RED the gate.

Every model call is supposed to pass through :func:`personalclaw.guardrails.wrap_model_call_guard`,
which writes the ``model_calls.jsonl`` attempt audit, charges the spend meter and applies
the budget/breaker policy. A provider that reaches a caller UNWRAPPED still works perfectly —
it just spends money and takes actions with no audit row and no budget. That is the failure shape
this rail
exists for: silent, invisible, and indistinguishable from correct behaviour at runtime.

**Measured before it was written, on `origin/main`.** The invariant holds, so this gate ships
at ZERO because zero is the measured population — not as an aspiration:

* ``wrap_model_call_guard`` is called from exactly ONE place in ``src/`` —
  ``providers/provider_bridge.metered``, which ``_resolve_from_config_registry`` calls "at the
  single point where the entry name + model are known" (its own comment). No model is built
  outside that seam: ``one_shot_completion`` resolves every call through it.
* ``resolve_provider_for_use_case`` has five provider-returning paths. Four return a value obtained
  from ``_resolve_from_config_registry``. The fifth is the native-agent branch,
  ``_build_native_runtime``, whose docstring records that "its inference ModelProvider is resolved
  through the SAME active-model" path — so its inner model is wrapped too, and the resolver's own
  SCOPE comment says the same of the ACP CLI branch.

So the allowlist below has exactly two entries, both verified to funnel into the chokepoint rather
than taken on trust: :func:`test_the_allowlisted_builder_actually_wraps` asserts that
``_resolve_from_config_registry`` really calls ``metered`` and that ``metered`` really wraps,
because an allowlist keyed on a NAME would keep passing if that function stopped wrapping.

**Why AST and not a regex.** The property is structural — "does this return value come from an
allowlisted builder" — and a text scan cannot follow ``x = f(); … ; return x``. That indirection is
the normal shape here (four of the five paths look exactly like it), so a regex rail would either
miss every real bypass or flag every correct path.
"""

from __future__ import annotations

import ast

import pytest
from model_call_chokepoint_rail_shared import BRIDGE, RESOLVER, _find_func, _unguarded_returns


class TestTheRailItselfDetectsABypass:
    """The clause is that a DELIBERATELY raw call reds the gate. That is a property of the
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
