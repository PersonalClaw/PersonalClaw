"""`DCU-2`'s missing half: the three screens exist, and NOTHING in ``src/`` calls them yet.

`DCU-2` ships the decisions (``policy.check_app``, ``policy.check_input_target``) and the
audit (``gate.require_computer_use``). Every one of them provably refuses/records when
called — ``test_computer_use_policy.py`` and ``test_computer_use_gate.py`` drive them
directly. But a screen is only a screen where something consults it, and on ``main`` the
dispatch chain those three sit in **does not exist**: `DCU-3` owns the macOS driver and
`DCU-4` owns ``service.py``'s in-gateway chain and the ``computer_*`` tool surface. So the
atom's third clause — *"every attempt, allowed or refused, produces a SEL record"* — is
today a property of a function nobody calls, which is the exact defect shape this codebase
keeps rediscovering: a control that is present, correct, tested, and inert.

**Why this file exists rather than a chain rail.** `DCU-1` armed the analogous rail for
step 1 (``test_every_computer_use_entry_point_guards_first``) by requiring every
``computer_*`` function in the package to call the keystone FIRST. The equivalent for steps
2/4/5 cannot be written yet without dictating a shape: `DCU-4`'s scope puts the chain in a
central ``service.py`` dispatch, not in each tool, so a per-tool "must call
``check_input_target``" rail would prescribe the wrong composition and a central-dispatch
rail has no dispatch to bind to. Choosing between those is `DCU-4`'s call, not this file's.

**What this file does instead**, which needs no such choice: it says the inertness OUT LOUD
and makes it self-announcing. The census below reds the moment the first production caller
appears — at which point the author must come back and assert the CALL SITE (that the
refusal really fires from the driving path, and that a SEL row is written on the allowed
path as well as the refused one), not merely that the mechanism works in isolation.

**This is invisible to every other gate on main, which is why it is written down here.**
``inert-surface-baseline.json`` censuses five declared-surface kinds — config keys, enum
members, trigger kinds, ``_EDITABLE_CONFIG`` entries and SDK exports — so a *module-level*
function with no importer is outside its vocabulary entirely (measured: zero occurrences of
``computer_use`` in that baseline). ``test_the_packages_public_surface_is_pinned`` pins the
three names as public API but says nothing about anyone calling them.

**AST, not a text scan.** ``policy.py``'s own module docstring names all three functions,
and ``gate.py``'s names two of them, so a ``grep``-shaped rail would count prose as call
sites and read as "already wired" today — the false-clean this repo has recorded before.
Only ``ast.Call`` nodes count here, and
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


def test_the_dcu2_screens_have_no_production_caller_yet():
    """⚠️ THE INERTNESS MARKER for `DCU-2` — read it before trusting the three clauses.

    Measured on ``origin/main``: ZERO. ``check_app``, ``check_input_target`` and
    ``require_computer_use`` are called from ``tests/`` only. That is correct *for now* —
    the chain that would call them is `DCU-4`'s deliverable and the driver is `DCU-3`'s —
    but it means no clause of `DCU-2`'s ``done_when`` is enforced against a real driving
    path today, and in particular nothing guarantees a refused attempt is ever accompanied
    by a SEL row: ``policy`` raises without recording, by design, because ``gate`` is a
    separate step a caller must remember.

    **When this reds, do not just bump the number.** Add, in the same change, a test that
    asserts the CALL SITE for whichever screen gained a caller:

    * a non-allowlisted app refused *through the dispatch path*, not by calling
      ``check_app`` directly;
    * a type/set-value into a secure field refused the same way;
    * a SEL row written on the ALLOWED path **and** on the refused path — asserted
      separately, because a single "a row exists" assertion passes when only the refusal
      records.
    """
    sites = _production_call_sites()
    total = sum(len(names) for names in sites.values())
    assert total == 0, (
        f"`DCU-2`'s screens now have production caller(s): {sites}. They are defined in "
        f"{sorted(set(_DEFINED_IN.values()))} and were inert when this census was written. "
        "Update this marker AND add a call-site assertion for each newly-called screen — "
        "see this test's docstring for the three that `DCU-2`'s done_when requires."
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
