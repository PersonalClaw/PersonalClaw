"""WHERE `DCU-2`'s three screens are consulted — one dispatch, and nothing else.

This file shipped with `DCU-2` as an **inertness marker**: the three screens
(``policy.check_app``, ``policy.check_input_target``, ``gate.require_computer_use``) existed,
were correct, were tested by being driven directly — and had ZERO production callers, because
the chain they sit in was `DCU-4`'s deliverable. The census asserted the population was zero
and said so out loud, so the inertness could not be silent.

**`DCU-4` landed, so the census flipped from "nobody calls these" to "exactly this calls
these".** It is the same scanner and the same three vacuity floors; only the expected
population changed, from empty to the one file that owns the composition. What it now defends
is the property that replaced the old one:

* a **new** caller of any screen reds, naming the file — because a second place that decides
  whether an app may be driven is a second policy that can drift from this one. `DCU-4`'s
  chain lives in ONE central dispatch precisely so there is one order to get right.
* a **lost** caller reds too, because the map is asserted by equality. Deleting the
  ``check_input_target`` call from the dispatch would otherwise turn this file green again by
  returning it to the state it was written to complain about.

**The complementary half — a new UNSCREENED path — is not this file's job**, and saying so
matters, because a census of screen call sites structurally cannot see code that calls no
screen. Two rails cover that direction:
``test_computer_use_enable_state.py::test_every_computer_use_entry_point_guards_first`` (every
``computer_*`` entry point in the package must call the keystone first, and the population
census beside it pins how many entry points exist — currently one), and
``test_computer_use_dispatch.py``'s declaration rails, which pin exactly which tools are
exempt from which screen so an added tool cannot opt out silently.

**AST, not a text scan.** ``policy.py``'s own module docstring names all three functions, and
``gate.py``'s names two of them, so a ``grep``-shaped rail would count prose as call sites and
would have read as "already wired" on a tree where nothing called anything — the false-clean
this repo has recorded before. Only ``ast.Call`` nodes count here, and
:func:`test_the_scanner_does_not_count_prose_as_a_call_site` is the proof of that.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The three functions `DCU-2` ships as the chain's steps 2, 4 and 5.
_SCREENS = frozenset({"check_app", "check_input_target", "require_computer_use"})

#: Where each screen is DEFINED. A definition is not a call site, and ``ast.Call`` already
#: excludes it — these are named only so the failure message can point at the owner.
_DEFINED_IN = {
    "check_app": "computer_use/policy.py",
    "check_input_target": "computer_use/policy.py",
    "require_computer_use": "computer_use/gate.py",
}


def _called_name(call: ast.Call) -> str:
    """The bare name a call invokes: ``f()``, ``mod.f()`` and ``a.b.f()`` all give ``f``."""
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _call_sites(source: str) -> list[str]:
    """Every screen invoked by *source*, as ``name`` strings — CALLS only, never prose."""
    return [
        name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and (name := _called_name(node)) in _SCREENS
    ]


def _production_call_sites() -> dict[str, list[str]]:
    """Census of screen call sites across production ``src/``, keyed by relative path."""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        names = _call_sites(path.read_text(encoding="utf-8"))
        if names:
            found[str(path.relative_to(SRC))] = sorted(names)
    return found


# ── the census ───────────────────────────────────────────────────────────────


#: The ONLY production file permitted to consult the screens, and which ones it consults.
#:
#: One entry, deliberately. `DCU-4`'s chain is a single central dispatch — ``service.py``'s
#: ``computer_dispatch`` — so "which files call a screen" and "how many places decide" are the
#: same question, and the answer has to be one.
#:
#: **Each screen appears exactly ONCE, which is a measured fact and not a coincidence.** The
#: multiplicity is pinned (the value is a list, not a set) because it is the interesting number:
#: ``check_app`` is called once even though the chain screens both the "app named in the
#: arguments" and the "app the snapshot walked" shapes, and ``require_computer_use`` is called
#: once even though the audit fires on three different exits — both because the dispatch funnels
#: them through single call sites (``policy.check_app(app, …)`` after the app is resolved either
#: way, and ``_audit`` for every exit). A second call site appearing here means the funnel was
#: broken open, which is exactly when two paths start disagreeing. What this cannot see is an
#: exit that stops calling ``_audit`` at all — that is covered behaviourally by
#: ``test_computer_use_dispatch.py::test_every_attempt_writes_exactly_one_row`` plus the three
#: tests that assert the allowed, refused and keystone-refused rows separately.
_EXPECTED_CALL_SITES: dict[str, list[str]] = {
    "computer_use/service.py": [
        "check_app",
        "check_input_target",
        "require_computer_use",
    ],
}


def test_the_dcu2_screens_are_consulted_only_by_the_dispatch():
    """THE CALL-SITE CENSUS — the flipped form of `DCU-2`'s inertness marker.

    Asserted by **equality**, in both directions:

    * a new file calling any screen reds, naming itself. That is the drift this guards: two
      places deciding whether an app may be driven is two policies, and the second one is
      always the one that forgets a step.
    * a call REMOVED from the dispatch reds too. Without the equality this file would go green
      again the moment somebody deleted the ``check_input_target`` call — returning it to
      exactly the inert state it was written to complain about.

    The per-screen behavioural assertions the old marker demanded of whoever wired these live in
    ``tests/test_computer_use_dispatch.py``: a non-allowlisted app refused *through the
    dispatch*, a secure-field type refused the same way, and a SEL row on the ALLOWED path
    asserted separately from the refused one — against a real ``SecurityEventLog``, not a fake.
    """
    sites = _production_call_sites()
    assert sites == _EXPECTED_CALL_SITES, (
        f"the population of `DCU-2` screen call sites changed: {sites}. Expected exactly "
        f"{_EXPECTED_CALL_SITES} — the screens are defined in "
        f"{sorted(set(_DEFINED_IN.values()))} and `DCU-4` composes them in ONE dispatch. If you "
        "added a caller, assert the CALL SITE too (refusal through the dispatch path, and a SEL "
        "row on the allowed path as well as the refused one, separately). If you removed one, "
        "the chain just lost a screen."
    )


# ── the vacuity floor: prove the census can see a caller at all ──────────────


def test_the_scanner_detects_a_caller():
    """Efficacy proof. Without this, ``total == 0`` above is indistinguishable from a
    scanner that matches nothing — a rail whose matcher is broken looks exactly like a
    rail whose invariant holds.

    Covers all three call spellings a real chain would use: a module-qualified call
    (``policy.check_app(...)``, the convention both modules' docstrings mandate), a
    bare imported name, and a deeply-attributed one.
    """
    wired = (
        "def computer_type(index, text, target):\n"
        "    enable_state.require_enabled('computer_type')\n"
        "    policy.check_app(app, tool='computer_type')\n"
        "    check_input_target(target, tool='computer_type')\n"
        "    personalclaw.computer_use.gate.require_computer_use(\n"
        "        tool='computer_type', outcome='completed'\n"
        "    )\n"
    )
    assert _call_sites(wired) == [
        "check_app",
        "check_input_target",
        "require_computer_use",
    ]


def test_the_scanner_does_not_count_prose_as_a_call_site():
    """A docstring naming a screen is not a caller — and this is not hypothetical: both
    ``policy.py`` and ``gate.py`` name these functions in their own module docstrings, so a
    text-shaped scanner would report `DCU-2` as already wired on a tree where nothing calls
    anything. Proves the census reads code, not comments."""
    prose = (
        '"""Runs check_app then check_input_target, then require_computer_use."""\n'
        "# check_app(app, tool='computer_click') — how a caller would do it\n"
        "MENTIONS = ('check_app', 'check_input_target', 'require_computer_use')\n"
    )
    assert _call_sites(prose) == []


def test_the_census_actually_reads_the_shipped_modules():
    """Second vacuity floor, for the *corpus* rather than the matcher: an empty or
    mis-rooted ``SRC`` glob would also report zero call sites. Asserts the census really
    walked the two files `DCU-2` shipped."""
    scanned = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py")}
    assert {"computer_use/policy.py", "computer_use/gate.py"} <= scanned
