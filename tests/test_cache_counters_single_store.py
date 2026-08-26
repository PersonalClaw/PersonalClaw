"""PCS-7 — the prompt-cache tallies must stay ONE store, provably.

The atom's done_when says the per-turn aggregate reuses ``stats.py``'s counters "with
no second store". Three rails already ship, and none of them can see a second store
appear:

* ``test_stats_counters_still_carry_both_cache_keys`` checks the two counters are
  PRESENT on the singleton. Its docstring claims it reds "if a refactor adds a
  parallel per-turn store" — it does not. A new accumulator in another module leaves
  ``Stats().snapshot()`` and ``hasattr(Stats, "inc_cache_*")`` untouched.
* ``test_cache_hit_pct_is_module_level_and_stateless`` pins only that the derived
  helper did not become a ``Stats`` method.
* ``TestCallSiteRail`` pins that the call site CALLS the shared helpers. Calling them
  and also keeping a private accumulator are not mutually exclusive.

So "no second store" was, until this file, an invariant held by a hand-run census
recorded in the plan's execution log. Anything a human counted once, a later change
un-counts silently. This is the executable version: over
``src/personalclaw/**/*.py``, by AST rather than by grep (a docstring naming
``cache_read_tokens`` is not a store), (1) nothing ACCUMULATES a prompt-cache-named
quantity, (2) the only writers of the tally are ``Stats.inc_cache_*`` and they are
called from exactly one module, (3) the two per-turn locals that feed the telemetry
are ASSIGNED from the terminal event, never accumulated across events, and (4) the
singleton grew no cache-named attribute beside its one counter dict.

Half (1)'s expected result is the EMPTY set, which is the shape that passes when a
scanner matches nothing at all. It therefore carries a positive control: the same
scanner is re-run over a source string that does accumulate, and MUST flag it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import personalclaw
from personalclaw.dashboard import chat_runner
from personalclaw.stats import Stats

SRC = Path(personalclaw.__file__).parent

# Every spelling this repo uses for the prompt-cache quantity. ``cache_write`` is
# ``pricing.py``'s name for what the providers call cache CREATION.
_CACHE_NAMES = ("cache_read", "cache_creation", "cache_write")

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

# A module that keeps its own running total. If the scanner cannot flag this, it
# cannot flag the real thing either.
_POSITIVE_CONTROL = """
class Runner:
    def __init__(self) -> None:
        self._cache_read_tokens = 0

    def on_event(self, event) -> None:
        self._cache_read_tokens += event.cache_read_tokens
"""

# A module that merely PASSES the quantity around. The scanner must not flag this, or
# half (1) would be unsatisfiable and would get weakened rather than obeyed.
_NEGATIVE_CONTROL = """
def render(cache_read_tokens, cache_creation_tokens):
    total = 0
    total += cache_read_tokens + cache_creation_tokens
    return f"{cache_read_tokens} read / {cache_creation_tokens} written -> {total}"
"""


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "tests_fixtures" not in p.parts)


def _target_names(node: ast.AST) -> list[str]:
    """Every identifier or string-subscript an assignment target mentions."""
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            names.append(sub.value)
    return names


def _accumulators(source: str, label: str) -> list[str]:
    """``x += ...`` (or ``-=``, ``|=`` &c.) onto a prompt-cache-named quantity.

    The TARGET is what makes a store: ``total += cache_read_tokens`` accumulates a
    turn total the caller already owns, while ``self._cache_read_tokens += n``
    accumulates the cache tally itself. Only the latter is a second store.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AugAssign):
            continue
        if any(frag in name for name in _target_names(node.target) for frag in _CACHE_NAMES):
            hits.append(f"{label}:{node.lineno}")
    return hits


def _tally_writer_calls() -> dict[str, list[str]]:
    """``{module_relpath: ["inc_cache_read_tokens:3942", ...]}`` over the package."""
    found: dict[str, list[str]] = {}
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _TALLY_WRITERS
            ):
                found.setdefault(rel, []).append(f"{node.func.attr}:{node.lineno}")
    return found


def _turn_local_bindings() -> dict[str, list[ast.AST]]:
    """Every statement in ``chat_runner`` that binds one of the two turn locals."""
    tree = ast.parse(Path(chat_runner.__file__).read_text(encoding="utf-8"))
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


# --- half (1): nothing accumulates the cache quantity -------------------------------


def test_the_accumulator_scanner_can_actually_fail() -> None:
    """VACUITY FLOOR for the census below, whose expected answer is the empty set.

    An empty result is exactly what a scanner that matches nothing returns, so the
    census is worthless unless the scanner is shown to flag a real second store.
    """
    assert _accumulators(_POSITIVE_CONTROL, "control"), (
        "the scanner did not flag `self._cache_read_tokens += ...` — "
        "the census below proves nothing"
    )


def test_the_accumulator_scanner_does_not_flag_pass_through_arithmetic() -> None:
    """Counter-floor: a scanner that flags everything would force its own weakening."""
    assert _accumulators(_NEGATIVE_CONTROL, "control") == [], (
        "the scanner flagged `total += cache_read_tokens`, which is a caller's own "
        "sum, not a second tally"
    )


def test_no_module_on_the_live_path_accumulates_a_prompt_cache_quantity() -> None:
    """THE CENSUS: ``Stats._c`` is the only running total of cached tokens.

    ``Stats.inc`` moves it through a generic ``self._c[key] += n`` — keyed on a
    parameter, not on a cache name — so the sanctioned tally is deliberately invisible
    to this scan and every hit on the live path is a genuine second store.
    """
    hits: list[str] = []
    for path in _py_files():
        rel = path.relative_to(SRC).as_posix()
        if rel in _READ_SIDE_FOLDS:
            continue
        hits.extend(_accumulators(path.read_text(encoding="utf-8"), rel))
    assert hits == [], (
        "a second prompt-cache accumulator appeared: "
        + ", ".join(hits)
        + " — PCS-7's aggregate must derive from `Stats`' counters and the terminal "
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
        source = (SRC / rel).read_text(encoding="utf-8")
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


# --- half (2): one writer, one module ----------------------------------------------


def test_the_only_tally_writers_are_the_stats_mutators() -> None:
    """One module writes the tally, and it writes both halves of it."""
    calls = _tally_writer_calls()
    assert set(calls) == {
        "dashboard/chat_runner.py"
    }, f"expected exactly one module to write the cache tally, found: {sorted(calls)}"
    written = {entry.split(":")[0] for entry in calls["dashboard/chat_runner.py"]}
    # Vacuity: a census finding nothing would satisfy an "is a subset of" assertion.
    assert written == set(_TALLY_WRITERS), f"expected both mutators to be called, found {written}"


# --- half (3): the turn locals are a snapshot, not a total --------------------------


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


# --- half (4): the singleton grew no turn-scoped cache attribute --------------------


def test_the_singleton_holds_no_cache_named_attribute_beside_its_counter_dict() -> None:
    """The tally lives INSIDE ``_c``; a cache-named attribute would be store number two."""
    instance = Stats()
    # Vacuity: confirm we are looking at the object that actually holds the tally.
    assert "cache_read_tokens" in instance.snapshot()
    stray = sorted(attr for attr in vars(instance) if "cache" in attr.lower())
    assert stray == [], f"Stats grew cache-named attribute(s) outside `_c`: {stray}"
