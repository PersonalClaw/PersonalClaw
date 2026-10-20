"""The wire-error-envelope CENSUS: how many routes emit which of the two shapes.

`AGENTS.md` §"Shared conventions" → **Error envelope (HTTP)** declares ONE wire
shape — ``{"error": {"code": "<stable_snake_code>", "message": "<human>"}}``. The
codebase ships two. This module measures the population so the second shape is a
counted, shrinking number instead of an anecdote, and so a client's complaint ("I
cannot tell from a route which of the two I will get") has a size.

**Why a test and not a script.** A one-off count decays the day it is taken. As a
test the count is re-derived on every run and RATCHETED: the structured population
may only grow, the flat one may only shrink. That turns "we should unify these"
into a mechanism that cannot silently reverse.

**What the two shapes are.**

* *structured* — ``web.json_response({"error": {"code": ..., "message": ...}})``,
  plus every call to :func:`personalclaw.http_errors.json_error`, which emits that
  shape by construction. A client can branch on the code.
* *flat* — ``web.json_response({"error": <anything else>})``: a bare prose string,
  an f-string, a variable. Carries NO code, so a client has nothing to branch on
  and is reduced to matching on prose that is free to be reworded.

**Wrapper indirection, and why this scanner follows it.** The first version of this
census classified the payload at the ``json_response`` *call site* only. That made it
blind by construction to a module that routes its responses through a local helper —
``def _json(payload, status): return web.json_response(payload, status=status)`` —
because at the ``json_response`` line the payload is a *variable*, not a literal, and
a non-literal was silently skipped. `inbound/bridge.py` shipped **eleven** flat
envelopes that this census scored at **zero**, and the companion rail
(:func:`test_no_module_local_error_helper_came_back`) missed them too because it
matches helper NAMES — ``_err``/``_error``/``_bad_request`` — and the helper was
called ``_json``. A name-matched denylist is not a control; renaming the helper
defeats it. `inbound/mcp_http.py` shipped **seven** more behind the same blindness,
two hops deep (``_done``, a closure, forwarding into module-level ``_json``); both
modules are converted now and the list of wrapper-routed flat sites is empty.

So the scanner now does two things instead:

1. It **follows the value one hop through a wrapper.** Any module-local function that
   forwards one of its own parameters into ``json_response``'s payload slot (directly,
   or through another such wrapper) is recognised as a response wrapper, and calls to
   it are classified by the payload argument at *their* call site. That is what makes
   the bridge's eleven visible.
2. Where it **cannot** resolve the payload to a dict literal it *refuses to classify*
   and says so out loud: the site lands in :attr:`Census.unresolved`, and a list names
   each one with its reason. A scanner that silently declines to classify is the same
   defect one level up, so the refusals are a listed, shrinking set — and a brand-new
   flat envelope can no longer hide behind indirection, because it must either resolve
   (counted flat) or not (a site the list does not hold). Both directions red.

**Scope.** Only the wire envelope. ``AgentError``'s ``ERR_UPPER_SNAKE`` envelope
(:mod:`personalclaw.errors`) is a DIFFERENT surface and is deliberately left
distinct; success envelopes are explicitly out of scope (the same convention says
they imitate the neighboring handler and are not standardized retroactively).
"""

from __future__ import annotations

import ast
import re

import pytest
import site_baseline
import source_index
from rails_repo import REPO
from wire_error_envelopes import (
    EMITTER_SITE_BASELINE,
    SRC,
    Census,
    _is_json_response,
    scan,
    scan_source,
)

# ── The measured baseline (re-measured 2026-08-21 on this tree) ────────────────
#
# Re-measure with this suite, never by hand. The change that introduced this census
# quoted 98 structured / 134 flat "measured at authoring"; neither number
# reproduces at call-site, per-function, or per-file granularity on this tree —
# the real call-site population was an order of magnitude larger on the flat side.
# These rows are what THIS scanner measures, and they are the baseline from here.

#: Direct ``json_response({"error": {"code": ...}})`` sites. A FLOOR (may only grow):
#: every one of these is a route a client can branch on, and losing one is a
#: regression. Falls only when a site is converted to :func:`json_error`, which is
#: counted separately below — so the SUM is the number that must never fall.
STRUCTURED_DIRECT_BASELINE = 118


#: Direct ``json_response({"error": "<prose>"})`` sites — the shape that carries no
#: code. A CEILING (may only shrink). Every one of these is a route where a client
#: must match prose. The four helpers that manufactured them wholesale are gone;
#: the remaining population is per-site work for later changes.
#:
#: Deliberately still counts DIRECT sites only, so the number stays comparable with
#: every earlier measurement of it. The population the widened scanner newly sees is
#: listed separately below rather than folded in here — folding it in would have
#: required raising this ceiling, which is the one move a ratchet cannot survive.
#: 🔁 RATCHETED 1507 -> 1504 on 2026-08-28. `main` had drifted to 1508 — one over — and the four
#: offending sites were `handlers/apps.py`'s "JSON body must be an object" plus the three
#: `_reject_name` refusals in `handlers/files.py`. All four now emit `json_error`, so the
#: population fell to 1504 and the ceiling follows it down: shrink-only means a shrink is
#: LOCKED IN, not banked as slack a later flat envelope could spend.
#: 🔁 RATCHETED 1504 -> 1499 on 2026-08-28, applying that same rule to somebody else's shrink.
#: `b4bfdc5f6` ("give the hierarchy handler's malformed-body refusals a wire code") converted five
#: refusals in `tasks/hierarchy_handlers.py` and left this ceiling untouched, so the population sat
#: at 1499 against 1504. Five slack is not neutral: it is five future flat envelopes that can land
#: green, which is precisely the "banked, not locked in" state the line above argues against. That
#: file still carries 27 flat sites, so the next shrink there should ratchet this again.
#: 🔁 RATCHETED 1499 -> 1490 on 2026-10-04: the agent-turn webhook (`dashboard/handlers/hooks.py`)
#: answers each of its nine refusals through `json_error`, in a sentence, as the fire door does.
FLAT_BASELINE = 1490

#: The flat sites reached through a WRAPPER (see the module docstring), one line each with the
#: reason it is allowed, in the per-site format of ``tests/site_baseline.py``. The first
#: measurement, the moment the scanner could see them at all, was **18**: eleven in
#: ``inbound/bridge.py``, converted to :func:`~personalclaw.http_errors.json_error` in the same
#: change that widened the scanner, and seven in ``inbound/mcp_http.py``, behind its
#: ``_json``/``_done`` helpers, converted in the follow-up. So the list is empty, and a new site
#: fails by name. It replaced the count ``FLAT_VIA_WRAPPER_BASELINE = 0``.
#:
#: An empty list is satisfied by a working detector AND by a detector that has stopped
#: detecting, and those are the two states this whole census exists to tell apart. Three things
#: stop that from being indistinguishable from success:
#: :func:`test_the_wrapper_detector_still_fires_on_the_real_tree` (the tree still HAS
#: wrappers), :func:`test_a_renamed_helper_cannot_escape_the_wrapper_scan` (shape, not
#: name), and :func:`test_a_flat_envelope_planted_back_into_mcp_http_is_counted`, which
#: re-plants one of the converted envelopes into the real module's real source and asserts
#: the list fails on it by name. Do not delete that last one; it is the only thing that makes
#: the empty list mean anything.
FLAT_VIA_WRAPPER_SITES = "tests/rails/baselines/flat-envelopes-via-wrapper.sites.jsonl"

#: The ``json_response``/wrapper sites whose payload the scanner CANNOT resolve to a dict
#: literal (a variable, a call, a comprehension, an await), one line each with the reason it is
#: allowed. This is precisely the hole the bridge's eleven hid in: a site the scanner declines
#: to classify must not be a site it declines to *name*. A wrapper's OWN forwarding line is not
#: a site: it is accounted for at the wrapper's call sites, so listing it would seed the list
#: with lines nobody can ever fix.
#:
#: A new unresolvable site fails by name, which is the point: a new flat envelope must now
#: either resolve (and hit the flat ceiling) or fail to resolve (and need a line of its own
#: here, with ``added`` and a reason, which the rails job writes to its summary). There is no
#: third option any more. Most lines are 200 success bodies whose shape a composer or a view
#: function owns, where spelling the keys out at the call site would put a second author on
#: that schema. A site that comes to build its body at the call site, or to answer through
#: :func:`json_error`, has its line deleted. It replaced the count
#: ``UNRESOLVED_PAYLOAD_CEILING = 217``, whose notes are now its lines' reasons.
UNRESOLVED_PAYLOAD_SITES = "tests/rails/baselines/unresolved-payloads.sites.jsonl"


#: Vacuity floor for the scan itself. The tree has ~950 modules; a scan that walks
#: a handful has lost its root, and every count below would read as an improvement.
FILES_SCANNED_FLOOR = 800


# ── vacuity: a scan that measures nothing must not read as clean ──────────────


def test_the_scan_is_not_vacuous():
    """Every count below is only as good as the walk that produced it."""
    census = scan()
    assert census.files_scanned >= FILES_SCANNED_FLOOR, (
        f"the census walked only {census.files_scanned} modules (floor "
        f"{FILES_SCANNED_FLOOR}) — the scan lost its root, so every count below "
        f"would read as an improvement. Check SRC={SRC}."
    )
    assert census.structured_direct, "no structured site found at all — the matcher is broken"
    assert census.flat, "no flat site found at all — the matcher is broken"
    assert census.emitter_sites, "no json_error call found at all — the matcher is broken"


# ── the wrapper mechanism, proved against synthetic source ────────────────────
#
# These do not measure the tree. They measure the SCANNER, on source written here, so
# that "the wrapper buckets are empty" can be told apart from "the wrapper detector
# stopped working" — which is the failure mode that let eleven envelopes hide in the
# first place, one level up.


_WRAPPED = '''
from aiohttp import web


def _json(payload, status=200):
    return web.json_response(payload, status=status)


def _done(status, payload):
    """A wrapper of a wrapper — `mcp_http`'s real shape, two hops from json_response."""
    return _json(payload, status=status)


def refuse():
    return _json({"error": "no code here at all"}, status=400)


def refuse_two_hops():
    return _done(500, {"error": "also flat, one hop further away"})


def refuse_properly():
    return _json({"error": {"code": "bad_request", "message": "explained"}}, status=400)


def refuse_from_a_local():
    """Unresolvable: the payload is a local, so its shape is not readable here."""
    body = {"error": "built above the call"}
    return _json(body, status=400)
'''


def test_the_scanner_follows_a_wrapper():
    """The positive: a flat envelope handed to a local wrapper is COUNTED.

    This is the exact shape `inbound/bridge.py` shipped eleven of while both rails
    reported zero. Two sites here: one a single hop from ``json_response``, one two hops
    (a wrapper calling a wrapper — `mcp_http`'s ``_done`` → ``_json``, which is why the
    resolver iterates to a fixpoint rather than looking one level down).
    """
    census = Census()
    scan_source(_WRAPPED, "synthetic.py", census)
    assert ("synthetic.py", "_json") in census.wrapper_defs, census.wrapper_defs
    assert ("synthetic.py", "_done") in census.wrapper_defs, census.wrapper_defs
    assert len(census.flat_via_wrapper) == 2, census.flat_via_wrapper
    assert not census.flat, "a wrapper-routed site was miscounted as a DIRECT one"
    # Each is named for the list by its function and the wrapper call's own source.
    assert census.flat_via_wrapper_sites == [
        "synthetic.py::refuse::_json({'error': 'no code here at all'}, status=400)",
        "synthetic.py::refuse_two_hops::_done(500, {'error': 'also flat, one hop further away'})",
    ], census.flat_via_wrapper_sites


def test_the_scanner_does_not_call_a_coded_envelope_flat():
    """The negative half of the same mechanism — otherwise "follows wrappers" could be
    satisfied by a scanner that simply calls everything flat."""
    census = Census()
    scan_source(_WRAPPED, "synthetic.py", census)
    assert len(census.structured_via_wrapper) == 1, census.structured_via_wrapper


def test_the_scanner_refuses_loudly_when_it_cannot_resolve_the_payload():
    """``_json(body, ...)`` where ``body`` is a local is unclassifiable — so it is COUNTED.

    The old scanner's bare ``continue`` here is the whole defect: a site it declined to
    classify was a site it declined to mention, so indirection was free.
    """
    census = Census()
    scan_source(_WRAPPED, "synthetic.py", census)
    unresolved = [(ln, kind, via) for _f, ln, kind, via in census.unresolved]
    # Exactly one: `refuse_from_a_local`. The two wrappers' OWN forwarding lines are
    # accounted for at their call sites and must not be counted here as well — otherwise
    # the list fills with lines nobody can fix.
    assert len(unresolved) == 1, unresolved
    _line, kind, via = unresolved[0]
    assert kind == "Name" and via is True, unresolved
    assert census.unresolved_sites == [
        "synthetic.py::refuse_from_a_local::_json(body, status=400)"
    ], census.unresolved_sites


def test_a_renamed_helper_cannot_escape_the_wrapper_scan():
    """The failure of the NAME-matched rail, stated as a test.

    `test_no_module_local_error_helper_came_back` looks for ``_err``/``_error``/
    ``_bad_request``. The bridge's helper was ``_json``, so renaming was all it took.
    The wrapper scan keys on SHAPE — forwards a parameter into ``json_response`` — so a
    name nobody predicted is still caught.
    """
    renamed = _WRAPPED.replace("_json", "_totally_innocuous_name")
    census = Census()
    scan_source(renamed, "synthetic.py", census)
    assert len(census.flat_via_wrapper) == 2, (
        "renaming the wrapper hid the flat envelopes again — the scan is matching names, "
        "not shape"
    )


#: The line the plant below swaps out. A real, converted refusal in `mcp_http.handle_mcp`.
_MCP_HTTP = SRC / "inbound" / "mcp_http.py"
_MCP_HTTP_REL = "src/personalclaw/inbound/mcp_http.py"
_CONVERTED_LINE = (
    '        return _refuse(json_error("not_found", status=404, headers=_NO_STORE), '
    "refused=problem)"
)
_PLANTED_LINE = '        return _done(404, {"error": "planted flat envelope"})'
_PLANTED_WRAPPER_SITE = (
    f"{_MCP_HTTP_REL}::handle_mcp::_done(404, {{'error': 'planted flat envelope'}})"
)


def test_a_flat_envelope_planted_back_into_mcp_http_is_counted():
    """The positive control for :data:`FLAT_VIA_WRAPPER_SITES`, an empty list.

    An empty list is satisfied by a working detector AND by a detector that has
    stopped detecting, and those are the two states this whole census exists to tell
    apart. :func:`test_the_scanner_follows_a_wrapper` proves the mechanism against source
    written in this file; this proves it against the source that actually ships, through
    the wrapper shape that actually hid the sites — ``_done``, a CLOSURE nested inside
    ``handle_mcp``, forwarding into module-level ``_json``, two hops from
    ``json_response``. A synthetic module cannot vouch for that, because the reason the
    original scanner missed these was a property of the real nesting.

    Nothing is written to disk: the plant is a string swap on source read into memory, and
    the list check below runs with no base and no prune.
    """
    real = _MCP_HTTP.read_text(encoding="utf-8")

    # 1) The module as it ships scores zero — so a red below is the plant, not the tree.
    baseline = Census()
    scan_source(real, _MCP_HTTP_REL, baseline)
    assert not baseline.flat_via_wrapper, (
        "mcp_http.py already ships a wrapper-routed flat envelope, so this test cannot "
        f"attribute the plant: {baseline.flat_via_wrapper}"
    )

    # 2) The plant APPLIED. A swap that silently matched nothing would make step 3 a
    #    tautology about unmodified source — the exact shape of a vacuous guard.
    planted = real.replace(_CONVERTED_LINE, _PLANTED_LINE, 1)
    assert planted != real, (
        f"the planted-regression swap matched nothing in {_MCP_HTTP.name}; _CONVERTED_LINE "
        f"has drifted from the shipped source, so this floor is measuring nothing. "
        f"Re-anchor it on a live `_refuse(json_error(...))` line."
    )
    assert _PLANTED_LINE in planted, "the swap applied but the plant is not in the source"

    # 3) The detector sees it — through two wrapper hops, in the real module.
    census = Census()
    scan_source(planted, _MCP_HTTP_REL, census)
    assert len(census.flat_via_wrapper) == 1, (
        "a flat envelope routed through mcp_http's own `_done` wrapper was NOT counted. "
        f"The empty {FLAT_VIA_WRAPPER_SITES} is therefore measuring the detector, not the "
        f"code. Buckets: flat_via_wrapper={census.flat_via_wrapper}, "
        f"flat={len(census.flat)}, unresolved={len(census.unresolved)}"
    )
    assert not census.flat, "the planted site was miscounted as a DIRECT flat envelope"

    # 4) The list fails on it by name: the tree's own sites, the planted module in place of
    #    the real one.
    listed = (REPO / FLAT_VIA_WRAPPER_SITES).read_text(encoding="utf-8")
    found = [s for s in scan().flat_via_wrapper_sites if not s.startswith(f"{_MCP_HTTP_REL}::")]
    with pytest.raises(AssertionError) as failed:
        site_baseline.check(
            found + census.flat_via_wrapper_sites, FLAT_VIA_WRAPPER_SITES, None, prune=False
        )
    message = str(failed.value)
    assert f"Site {_PLANTED_WRAPPER_SITE} is not listed" in message, message
    assert message.count("is not listed") == 1, message
    assert (REPO / FLAT_VIA_WRAPPER_SITES).read_text(encoding="utf-8") == listed


def test_the_wrapper_detector_still_fires_on_the_real_tree():
    """Vacuity anchor for the wrapper list below.

    If this tree genuinely has no response wrappers left, the wrapper buckets are
    trivially green and this floor is the only thing that says so out loud. Delete the
    floor deliberately in that change — do not let it pass by accident.
    """
    census = scan()
    assert census.wrapper_defs, (
        "no response wrapper detected in any module. Either every local wrapper was "
        "removed (in which case delete this floor and FLAT_VIA_WRAPPER_SITES's list in the "
        "same change) or _response_wrappers stopped resolving — and then "
        "census.flat_via_wrapper reads clean for the wrong reason."
    )


# ── the ratchets ──────────────────────────────────────────────────────────────


def test_the_structured_population_never_shrinks():
    """A route that could be branched on must not go back to prose."""
    census = scan()
    floor = STRUCTURED_DIRECT_BASELINE + EMITTER_SITE_BASELINE
    assert census.structured_total >= floor, (
        f"structured wire-error sites fell to {census.structured_total} "
        f"({len(census.structured_direct)} direct + {len(census.emitter_sites)} via "
        f"json_error), below the {floor} baseline. A route stopped emitting a code a "
        f"client branches on."
    )


def test_the_flat_population_never_grows():
    """The shape that carries no code is a shrinking budget, not a style choice."""
    census = scan()
    assert len(census.flat) <= FLAT_BASELINE, (
        f"flat wire-error sites rose to {len(census.flat)} (ceiling {FLAT_BASELINE}). A "
        f'new route returned {{"error": "<prose>"}}; the convention requires '
        f'{{"error": {{"code", "message"}}}} — emit it with '
        f"personalclaw.http_errors.json_error. New sites:\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in census.flat[-12:])
    )


def test_every_wrapper_routed_flat_site_is_listed():
    """A local wrapper must not become the place flat envelopes go to hide.

    Eighteen sites were here the moment the scanner could see them: eleven in
    `inbound/bridge.py`, converted in that same change, and seven in
    `inbound/mcp_http.py`, converted in the follow-up. :data:`FLAT_VIA_WRAPPER_SITES` is
    empty, so this is no longer a shrinking allowance but a closed door: routing
    ``{"error": "<prose>"}`` through a local helper is still a flat envelope, and it fails by
    name until it is emitted with ``personalclaw.http_errors.json_error``. Its meaning rests
    entirely on :func:`test_a_flat_envelope_planted_back_into_mcp_http_is_counted`.

    No total of direct and wrapper-routed flat sites is held beside the two: a site moved from
    a direct call into a wrapper is a site this list does not hold, so it cannot read as
    progress on the direct ceiling.
    """
    census = scan()
    assert len(census.flat_via_wrapper_sites) == len(
        census.flat_via_wrapper
    ), "a wrapper-routed flat envelope was found and not named, so no list holds it"
    site_baseline.check(census.flat_via_wrapper_sites, FLAT_VIA_WRAPPER_SITES)


def test_every_unclassifiable_payload_is_listed_with_its_reason():
    """The scanner's own blind spot, held to a list instead of a shrug.

    Every site here is one where the payload is a variable, a call or a comprehension,
    so the shape cannot be read statically. That is exactly where a new flat envelope
    would go to avoid the flat ceiling, which is why a site :data:`UNRESOLVED_PAYLOAD_SITES`
    does not hold fails by name: a new emit site must resolve to a literal (and be counted)
    or have a line of its own, with its reason. Build the response body at the call site —
    or emit the error with ``personalclaw.http_errors.json_error``, which needs no payload
    dict at all.
    """
    census = scan()
    assert census.files_scanned >= FILES_SCANNED_FLOOR, (
        f"walked only {census.files_scanned} modules (floor {FILES_SCANNED_FLOOR}) — "
        f"a shrunken walk makes this list meaningless."
    )
    assert len(census.unresolved_sites) == len(
        census.unresolved
    ), "an unclassifiable payload was found and not named, so no list holds it"
    site_baseline.check(census.unresolved_sites, UNRESOLVED_PAYLOAD_SITES)


#: A module that sends a body built in a local twice in one handler, beside a literal one.
_PLANTED_MODULE = """\
from aiohttp import web


async def api_planted(request):
    body = {"error": "built above the call"}
    if request.query.get("literal"):
        return web.json_response({"ok": True})
    if request.query.get("again"):
        return web.json_response(body, status=400)
    return web.json_response(body, status=400)
"""
_PLANTED_MODULE_REL = "src/personalclaw/dashboard/handlers/planted.py"
_PLANTED_PAYLOAD_SITE = f"{_PLANTED_MODULE_REL}::api_planted::web.json_response(body, status=400)"


def test_a_planted_unclassifiable_payload_fails_by_name_and_a_fixed_one_by_its_line():
    """The positive control, on the real list: the tree's sites with a planted module's two
    bodies built in a local fail, naming each (the second in the handler as ``#2``), and its
    literal body is no site; the tree's sites less one, as if that site built its body at the
    call site, fail on its stale line. Neither run writes the list."""
    listed = (REPO / UNRESOLVED_PAYLOAD_SITES).read_text(encoding="utf-8")
    census = scan()
    scan_source(_PLANTED_MODULE, _PLANTED_MODULE_REL, census)
    with pytest.raises(AssertionError) as failed:
        site_baseline.check(census.unresolved_sites, UNRESOLVED_PAYLOAD_SITES, None, prune=False)
    message = str(failed.value)
    assert f"Site {_PLANTED_PAYLOAD_SITE} is not listed" in message, message
    assert f"Site {_PLANTED_PAYLOAD_SITE} #2 is not listed" in message, message
    assert message.count("is not listed") == 2, message
    fixed, *rest = scan().unresolved_sites
    with pytest.raises(AssertionError, match=f"Site {re.escape(fixed)} is listed in"):
        site_baseline.check(rest, UNRESOLVED_PAYLOAD_SITES, None, prune=False)
    assert (REPO / UNRESOLVED_PAYLOAD_SITES).read_text(encoding="utf-8") == listed


def test_the_one_emitter_is_actually_used():
    """The point: thirteen module-local helpers became one shared emitter."""
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_BASELINE, (
        f"only {len(census.emitter_sites)} json_error call sites (floor "
        f"{EMITTER_SITE_BASELINE}) — a module grew its own error helper again."
    )
    files = {f for f, _ in census.emitter_sites}
    assert len(files) >= 12, (
        f"json_error is used in only {len(files)} modules; it replaced helpers in "
        f"thirteen. A module reverted to a local helper."
    )


def test_no_module_local_error_helper_came_back():
    """The thirteen deleted helpers are deleted — no wrappers, no revivals.

    Any ``def _err``/``_error``/``_bad_request`` that returns a ``web.Response`` is
    the exact hazard their deletion removed: a second emitter whose argument order is free to
    disagree with the shared one.

    **This rail is a NAME match, and that is its known limit.** It scored
    `inbound/bridge.py`'s eleven flat envelopes at zero because the helper was called
    ``_json``. Kept as-is — the three names it lists are the ones actually deleted,
    and a rail that says "no revival of THESE" is honest about its scope. The general
    case is not fixable by adding names, so it is not fixed here: it is fixed by
    `test_every_wrapper_routed_flat_site_is_listed`, which keys on SHAPE and
    therefore catches a helper whose name nobody thought to list.
    """
    offenders: list[str] = []
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in ("_err", "_error", "_bad_request"):
                continue
            emits = any(
                isinstance(inner, ast.Call) and _is_json_response(inner) for inner in ast.walk(node)
            )
            if emits:
                offenders.append(f"{path.relative_to(SRC.parent.parent)}:{node.lineno} {node.name}")
    assert not offenders, (
        "a module-local wire-error helper came back — use "
        "personalclaw.http_errors.json_error instead:\n  " + "\n  ".join(offenders)
    )


def test_the_evals_surface_emits_no_flat_envelope():
    """The evals surface's five reads answer bodies its view functions build, each a line of
    :data:`UNRESOLVED_PAYLOAD_SITES`, admitted because this module emits NO flat envelope at
    all: every refusal on it goes through :func:`~personalclaw.http_errors.json_error`. Pinned
    so a flat refusal written here reds beside the code, not only in the tree-wide flat ceiling;
    one built into a local is a new site the list does not hold.
    """
    census = scan()
    module = "src/personalclaw/dashboard/handlers/evals.py"
    assert [row for row in census.unresolved if row[0] == module], "the evals reads are gone"
    assert [row for row in census.flat if row[0] == module] == []
    assert [row for row in census.flat_via_wrapper if row[0] == module] == []


#: The learning surface's flat-envelope rows at the time the learning summary was admitted to
#: the unresolved list. Unlike the evals surface this module is NOT flat-free, so the pin's
#: shape does not transfer.
#: 14 → **10**: the four "learning is disabled" 404s on the reads the Learning page loads to
#: render (the proposal list, the staging week, health, the summary) now answer the decided
#: ``_off()`` 200, which carries no error envelope at all. A shrink, so the pin moves down with it.
_LEARNING_FLAT_BASELINE = 10


def test_the_learning_surface_adds_no_flat_envelope():
    """The learning surface's composer bodies are lines of :data:`UNRESOLVED_PAYLOAD_SITES`,
    the learning summary and the identity report's preview and delivery among them, and the
    flat refusals beside them may not grow.

    **This pin deliberately differs from the evals one.** That test asserts the evals module
    emits NO flat envelope at all. Measured here, that claim is simply false:
    ``handlers/learning.py`` carried **14** flat rows when the learning summary was admitted (its
    proposal-inbox and staging refusals predate it and are counted against the flat ceiling;
    four of them have since become decided 200s, see the baseline). Copying the evals assertion
    would have produced a test that reds for a reason unrelated to the composer bodies — so the
    pin instead fixes the flat COUNT, which reds on a NEW flat envelope while tolerating the
    existing ones.

    Each composer body is listed by its call (``web.json_response(<the composer>(...))``), so a
    dict built in the handler and sent as a local is a site the list does not hold: the
    indirection the census exists to expose.
    """
    census = scan()
    module = "src/personalclaw/dashboard/handlers/learning.py"
    assert [row for row in census.unresolved if row[0] == module], "the learning reads are gone"
    flat = [row for row in census.flat if row[0] == module]
    assert len(flat) == _LEARNING_FLAT_BASELINE, (
        f"flat envelopes on the learning surface moved {_LEARNING_FLAT_BASELINE} -> "
        f"{len(flat)}; a new refusal here goes through json_error"
    )
    assert [row for row in census.flat_via_wrapper if row[0] == module] == []


def test_the_a2a_surface_emits_no_flat_envelope():
    """The A2A gateway's three protocol documents, the agent card and the A2A Task document the
    task-start and task-poll routes return, are lines of :data:`UNRESOLVED_PAYLOAD_SITES`.

    This pin takes the **stronger** evals-surface shape rather than the learning one, because
    the stronger claim is measurably true here: ``inbound/a2a.py`` emits NO flat envelope at
    all — every refusal on the surface goes through :func:`~personalclaw.http_errors.json_error`.
    """
    census = scan()
    module = "src/personalclaw/inbound/a2a.py"
    assert [row for row in census.unresolved if row[0] == module], "the A2A documents are gone"
    assert [row for row in census.flat if row[0] == module] == []
    assert [row for row in census.flat_via_wrapper if row[0] == module] == []
