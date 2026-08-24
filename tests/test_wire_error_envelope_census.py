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
modules are converted now and the wrapper ceiling is 0.

So the scanner now does two things instead:

1. It **follows the value one hop through a wrapper.** Any module-local function that
   forwards one of its own parameters into ``json_response``'s payload slot (directly,
   or through another such wrapper) is recognised as a response wrapper, and calls to
   it are classified by the payload argument at *their* call site. That is what makes
   the bridge's eleven visible.
2. Where it **cannot** resolve the payload to a dict literal it *refuses to classify*
   and says so out loud: the site lands in :attr:`Census.unresolved`, which carries its
   own ceiling. A scanner that silently declines to classify is the same defect one
   level up, so the refusal is a counted, shrinking number — and a brand-new flat
   envelope can no longer hide behind indirection, because it must either resolve
   (counted flat) or not (counted unresolved). Both directions red.

**Scope.** Only the wire envelope. ``AgentError``'s ``ERR_UPPER_SNAKE`` envelope
(:mod:`personalclaw.errors`) is a DIFFERENT surface and is deliberately left
distinct; success envelopes are explicitly out of scope (the same convention says
they imitate the neighboring handler and are not standardized retroactively).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"

# ── The measured baseline (PL-8, re-measured 2026-08-21 on this tree) ──────────
#
# Re-measure with this suite, never by hand. The atom that introduced this census
# quoted 98 structured / 134 flat "measured at authoring"; neither number
# reproduces at call-site, per-function, or per-file granularity on this tree —
# the real call-site population was an order of magnitude larger on the flat side.
# These rows are what THIS scanner measures, and they are the baseline from here.

#: Direct ``json_response({"error": {"code": ...}})`` sites. A FLOOR (may only grow):
#: every one of these is a route a client can branch on, and losing one is a
#: regression. Falls only when a site is converted to :func:`json_error`, which is
#: counted separately below — so the SUM is the number that must never fall.
STRUCTURED_DIRECT_BASELINE = 118

#: Calls to the one emitter. A FLOOR. This is also the population the append-only
#: rail must inspect (see ``EMITTER_SITE_FLOOR``).
EMITTER_SITE_BASELINE = 132

#: Direct ``json_response({"error": "<prose>"})`` sites — the shape that carries no
#: code. A CEILING (may only shrink). Every one of these is a route where a client
#: must match prose. PL-8 removed the four helpers that manufactured them wholesale;
#: the remaining population is per-site work for later atoms.
#:
#: Deliberately still counts DIRECT sites only, so the number stays comparable with
#: every earlier measurement of it. The population the widened scanner newly sees is
#: counted separately below rather than folded in here — folding it in would have
#: required raising this ceiling, which is the one move a ratchet cannot survive.
FLAT_BASELINE = 1507

#: Flat sites reached through a WRAPPER (see the module docstring). A CEILING, measured
#: on this tree the moment the scanner could see them at all. The first measurement was
#: **18** — eleven in ``inbound/bridge.py`` and seven in ``inbound/mcp_http.py``. The
#: bridge's eleven were converted to :func:`~personalclaw.http_errors.json_error` in the
#: same change that widened the scanner (baseline 18 → 7); `mcp_http`'s seven, routed
#: through its ``_json``/``_done`` helpers, were converted in the follow-up atom the
#: 7 was left visible for. Hence **0**.
#:
#: Zero is the strongest form of this ceiling and the most fragile to read: ``0 <= 0``
#: also passes when the detector has stopped working. Three things stop that from being
#: indistinguishable from success —
#: :func:`test_the_wrapper_detector_still_fires_on_the_real_tree` (the tree still HAS
#: wrappers), :func:`test_a_renamed_helper_cannot_escape_the_wrapper_scan` (shape, not
#: name), and :func:`test_a_flat_envelope_planted_back_into_mcp_http_is_counted`, which
#: re-plants one of the converted envelopes into the real module's real source and
#: asserts this bucket counts it. Do not delete that last one to make this ceiling
#: cheaper; it is the only thing that makes the zero mean anything.
FLAT_VIA_WRAPPER_BASELINE = 0

#: The honest single number going forward: no client cares which spelling of
#: indirection carried the envelope that gave it no code to branch on.
FLAT_TOTAL_BASELINE = FLAT_BASELINE + FLAT_VIA_WRAPPER_BASELINE

#: ``json_response``/wrapper sites whose payload the scanner CANNOT resolve to a dict
#: literal — a variable, a call, a comprehension, an await. A CEILING, because this is
#: precisely the hole the bridge's eleven hid in: a site the scanner declines to
#: classify must not be a site it declines to *count*. Measured on this tree at 204
#: (mostly plain names and calls, with a tail of comprehensions/lists/awaits), 14 of
#: them reached through a wrapper. A wrapper's OWN forwarding line is excluded — it is
#: accounted for at the wrapper's call sites, so counting it would seed this ceiling
#: with rows nobody can ever fix.
#:
#: A new unresolvable site reds this, which is the point: a new flat envelope must now
#: either resolve (and hit a flat ceiling) or fail to resolve (and hit this one). There
#: is no third option any more.
#:
#: 204 → **202**: converting `mcp_http`'s refusals also retired two rows that were hiding
#: HERE rather than in the wrapper bucket — an admission refusal built by a ternary into a
#: local (``refusal``) and the rate-limit body built into a local (``payload``). Both were
#: flat envelopes; neither was visible as one, because the scanner correctly refuses to
#: read a variable's shape. A ceiling that falls when work lands is the ratchet working,
#: so it is lowered rather than left slack.
UNRESOLVED_PAYLOAD_CEILING = 202

#: What the append-only rail must inspect. Derived from the census so a matcher that
#: stops matching cannot read as clean: if the rail's scan finds fewer emitter sites
#: than the census counted, the rail is measuring itself, not the code.
EMITTER_SITE_FLOOR = EMITTER_SITE_BASELINE

#: Vacuity floor for the scan itself. The tree has ~950 modules; a scan that walks
#: a handful has lost its root, and every count below would read as an improvement.
FILES_SCANNED_FLOOR = 800


@dataclass
class Census:
    files_scanned: int = 0
    structured_direct: list[tuple[str, int]] = field(default_factory=list)
    flat: list[tuple[str, int]] = field(default_factory=list)
    emitter_sites: list[tuple[str, int]] = field(default_factory=list)
    #: ``(file, line, code)`` for every emitter call whose code is a string literal.
    emitter_literal_codes: list[tuple[str, int, str]] = field(default_factory=list)
    #: Emitter calls whose code is computed (``exc.reason``, an f-string, a constant
    #: reference). Not statically checkable against the registry — counted so they
    #: cannot become a hiding place for unregistered codes.
    emitter_dynamic_sites: list[tuple[str, int]] = field(default_factory=list)
    #: Flat/structured sites reached through a module-local response WRAPPER rather
    #: than at a ``json_response`` call directly. Kept in their own lists so the
    #: long-standing direct ceilings stay comparable across measurements.
    flat_via_wrapper: list[tuple[str, int]] = field(default_factory=list)
    structured_via_wrapper: list[tuple[str, int]] = field(default_factory=list)
    #: ``(file, name)`` for every response wrapper the scanner recognised. The vacuity
    #: anchor for the whole wrapper mechanism: if this empties, the two buckets above
    #: read clean for the wrong reason.
    wrapper_defs: list[tuple[str, str]] = field(default_factory=list)
    #: ``(file, line, node_type, via_wrapper)`` for every emit site whose payload the
    #: scanner refused to classify. LOUD by construction — see the docstring.
    unresolved: list[tuple[str, int, str, bool]] = field(default_factory=list)

    @property
    def structured_total(self) -> int:
        return len(self.structured_direct) + len(self.emitter_sites)

    @property
    def flat_total(self) -> int:
        """Every flat site, however the payload reached the response."""
        return len(self.flat) + len(self.flat_via_wrapper)


def _is_json_response(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "json_response") or (
        isinstance(f, ast.Name) and f.id == "json_response"
    )


def _payload_arg(call: ast.Call) -> ast.expr | None:
    """``json_response``'s payload argument: first positional, else ``data=``."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "data":
            return kw.value
    return None


def _arg_at(call: ast.Call, index: int, name: str) -> ast.expr | None:
    """The argument in slot ``index``/``name``, whichever the caller used."""
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _positional_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [p.arg for p in list(fn.args.posonlyargs) + list(fn.args.args)]


def _response_wrappers(tree: ast.Module) -> tuple[dict[str, tuple[int, str]], set[int]]:
    """The module's response wrappers, and the forwarding calls INSIDE them.

    Returns ``({name: (positional index, param name)}, {id(call), ...})``.

    A *response wrapper* is a function that forwards one of its OWN parameters into
    ``json_response``'s payload slot. Nested functions count — `mcp_http`'s ``_done`` is
    a closure inside its handler, and it is one of the wrappers that was hiding sites.

    Wrapper-of-wrapper resolves by iterating to a fixpoint (``_done`` forwards into
    ``_json``, which forwards into ``json_response``), bounded because each pass can only
    add names and the module has finitely many functions.

    The second element is what keeps the unresolved bucket meaningful. A wrapper's own
    ``return web.json_response(payload, ...)`` has a non-literal payload by definition,
    but it is fully ACCOUNTED FOR — every value that reaches it is classified at the
    wrapper's call sites. Counting it as unaccounted-for would put three permanent,
    unfixable rows into a ceiling whose job is to make new blindness visible.
    """
    found: dict[str, tuple[int, str]] = {}
    functions = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def _forwarded_param(call: ast.Call) -> ast.expr | None:
        if _is_json_response(call):
            return _payload_arg(call)
        if isinstance(call.func, ast.Name) and call.func.id in found:
            index, name = found[call.func.id]
            return _arg_at(call, index, name)
        return None

    while True:
        grew = False
        for fn in functions:
            if fn.name in found:
                continue
            params = _positional_params(fn)
            if not params:
                continue
            for inner in ast.walk(fn):
                if not isinstance(inner, ast.Call):
                    continue
                forwarded = _forwarded_param(inner)
                if isinstance(forwarded, ast.Name) and forwarded.id in params:
                    found[fn.name] = (params.index(forwarded.id), forwarded.id)
                    grew = True
                    break
        if not grew:
            break

    # Second pass, once `found` is complete: EVERY forwarding call in EVERY recognised
    # wrapper, not just the first one the fixpoint happened to stop on.
    accounted: set[int] = set()
    for fn in functions:
        if fn.name not in found:
            continue
        param = found[fn.name][1]
        for inner in ast.walk(fn):
            if not isinstance(inner, ast.Call):
                continue
            forwarded = _forwarded_param(inner)
            if isinstance(forwarded, ast.Name) and forwarded.id == param:
                accounted.add(id(inner))
    return found, accounted


def _is_emitter(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Name) and f.id == "json_error") or (
        isinstance(f, ast.Attribute) and f.attr == "json_error"
    )


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """``NAME = "literal"`` at module level, so a named code is still checkable.

    The auth and device-pairing routes pass their codes as module constants
    (``ERR_LOCKED_OUT = "auth_locked_out"``) precisely because the code IS the whole
    response body there. Resolving one level of indirection keeps those ten codes
    inside the registry check instead of writing them off as dynamic.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def _static_code(call: ast.Call, constants: dict[str, str]) -> str | None:
    """The emitter's ``code`` when it is statically knowable, else ``None``."""
    arg = call.args[0] if call.args else None
    if arg is None:
        for kw in call.keywords:
            if kw.arg == "code":
                arg = kw.value
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name):
        return constants.get(arg.id)
    return None


def scan_source(source: str, rel: str, census: Census) -> None:
    """Classify one module's error sites into ``census``.

    Split out of :func:`scan` so the wrapper-following logic can be exercised against a
    SYNTHETIC module (see :func:`test_the_scanner_follows_a_wrapper`). A mechanism whose
    only test is "the tree's current count is N" cannot distinguish "no sites" from
    "detector broken", and that distinction is the whole reason this function exists.
    """
    tree = ast.parse(source)
    census.files_scanned += 1
    constants = _module_string_constants(tree)
    wrappers, accounted = _response_wrappers(tree)
    for name in sorted(wrappers):
        census.wrapper_defs.append((rel, name))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_emitter(node):
            census.emitter_sites.append((rel, node.lineno))
            code = _static_code(node, constants)
            if code is None:
                census.emitter_dynamic_sites.append((rel, node.lineno))
            else:
                census.emitter_literal_codes.append((rel, node.lineno, code))
            continue
        via_wrapper = False
        if _is_json_response(node):
            payload = _payload_arg(node)
        elif isinstance(node.func, ast.Name) and node.func.id in wrappers:
            index, param = wrappers[node.func.id]
            payload = _arg_at(node, index, param)
            via_wrapper = True
        else:
            continue
        if payload is None:
            # A genuinely payload-less call (``json_response()``); nothing to classify
            # and nothing hidden.
            continue
        if not isinstance(payload, ast.Dict):
            if id(node) in accounted:
                # A wrapper forwarding its own parameter. Not unaccounted-for: the values
                # that flow through it are classified at the wrapper's call sites.
                continue
            # REFUSED, not skipped. See UNRESOLVED_PAYLOAD_CEILING.
            census.unresolved.append((rel, node.lineno, type(payload).__name__, via_wrapper))
            continue
        for key, value in zip(payload.keys, payload.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            has_code = isinstance(value, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "code" for k in value.keys
            )
            if via_wrapper:
                bucket = census.structured_via_wrapper if has_code else census.flat_via_wrapper
            else:
                bucket = census.structured_direct if has_code else census.flat
            bucket.append((rel, node.lineno))


def scan() -> Census:
    """Walk every module under ``src/personalclaw`` and classify each error site.

    AST, not grep: a multi-line dict literal and a single-line one must count the
    same, and a mention of ``"error"`` in a docstring or a comment must not count
    at all (a text scanner reads comments — see the ``json_error`` docstring, which
    quotes both shapes verbatim and would otherwise inflate every number here).
    """
    census = Census()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        try:
            scan_source(source, str(path.relative_to(SRC.parent.parent)), census)
        except SyntaxError:  # pragma: no cover - defensive
            continue
    return census


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
    # the ceiling fills with rows nobody can fix.
    assert len(unresolved) == 1, unresolved
    _line, kind, via = unresolved[0]
    assert kind == "Name" and via is True, unresolved


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
_CONVERTED_LINE = (
    '        return _refuse(json_error("not_found", status=404, headers=_NO_STORE), '
    "refused=problem)"
)
_PLANTED_LINE = '        return _done(404, {"error": "planted flat envelope"})'


def test_a_flat_envelope_planted_back_into_mcp_http_is_counted():
    """The vacuity floor for ``FLAT_VIA_WRAPPER_BASELINE = 0``.

    A ceiling of zero is satisfied by a working detector AND by a detector that has
    stopped detecting, and those are the two states this whole census exists to tell
    apart. :func:`test_the_scanner_follows_a_wrapper` proves the mechanism against source
    written in this file; this proves it against the source that actually ships, through
    the wrapper shape that actually hid the sites — ``_done``, a CLOSURE nested inside
    ``handle_mcp``, forwarding into module-level ``_json``, two hops from
    ``json_response``. A synthetic module cannot vouch for that, because the reason the
    original scanner missed these was a property of the real nesting.

    Nothing is written to disk: the plant is a string swap on source read into memory.
    """
    real = _MCP_HTTP.read_text(encoding="utf-8")

    # 1) The module as it ships scores zero — so a red below is the plant, not the tree.
    baseline = Census()
    scan_source(real, "inbound/mcp_http.py", baseline)
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
    scan_source(planted, "inbound/mcp_http.py", census)
    assert len(census.flat_via_wrapper) == 1, (
        "a flat envelope routed through mcp_http's own `_done` wrapper was NOT counted. "
        f"FLAT_VIA_WRAPPER_BASELINE = {FLAT_VIA_WRAPPER_BASELINE} is therefore measuring "
        f"the detector, not the code. Buckets: flat_via_wrapper="
        f"{census.flat_via_wrapper}, flat={len(census.flat)}, "
        f"unresolved={len(census.unresolved)}"
    )
    assert not census.flat, "the planted site was miscounted as a DIRECT flat envelope"


def test_the_wrapper_detector_still_fires_on_the_real_tree():
    """Vacuity anchor for the two wrapper ceilings below.

    If this tree genuinely has no response wrappers left, both wrapper buckets are
    trivially green and this floor is the only thing that says so out loud. Delete the
    floor deliberately in that change — do not let it pass by accident.
    """
    census = scan()
    assert census.wrapper_defs, (
        "no response wrapper detected in any module. Either every local wrapper was "
        "removed (in which case delete this floor and FLAT_VIA_WRAPPER_BASELINE in the "
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


def test_the_wrapper_routed_flat_population_never_grows():
    """A local wrapper must not become the place flat envelopes go to hide.

    Eighteen sites were here the moment the scanner could see them: eleven in
    `inbound/bridge.py`, converted in that same change, and seven in
    `inbound/mcp_http.py`, converted in the follow-up. The budget is now ZERO, so this is
    no longer a shrinking allowance but a closed door — and its meaning rests entirely on
    :func:`test_a_flat_envelope_planted_back_into_mcp_http_is_counted`.
    """
    census = scan()
    assert len(census.flat_via_wrapper) <= FLAT_VIA_WRAPPER_BASELINE, (
        f"flat wire-error sites reached through a response wrapper rose to "
        f"{len(census.flat_via_wrapper)} (ceiling {FLAT_VIA_WRAPPER_BASELINE}). Routing "
        f'{{"error": "<prose>"}} through a local helper is still a flat envelope — emit '
        f"it with personalclaw.http_errors.json_error. Sites:\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in census.flat_via_wrapper)
    )


def test_the_total_flat_population_never_grows():
    """The number a client would recognise: routes that hand it no code to branch on.

    Held alongside the two component ceilings rather than instead of them, so moving a
    site from a direct call into a wrapper cannot read as progress on either.
    """
    census = scan()
    assert census.flat_total <= FLAT_TOTAL_BASELINE, (
        f"total flat wire-error sites rose to {census.flat_total} (ceiling "
        f"{FLAT_TOTAL_BASELINE} = {len(census.flat)} direct + "
        f"{len(census.flat_via_wrapper)} via wrapper)."
    )


def test_unclassifiable_payloads_do_not_grow():
    """The scanner's own blind spot, held to a ceiling instead of a shrug.

    Every site here is one where the payload is a variable, a call or a comprehension,
    so the shape cannot be read statically. That is exactly where a new flat envelope
    would go to avoid the ceilings above, which is why growth reds: a new emit site must
    resolve to a literal (and be counted) or be refused here (and be counted).
    """
    census = scan()
    assert census.files_scanned >= FILES_SCANNED_FLOOR, (
        f"walked only {census.files_scanned} modules (floor {FILES_SCANNED_FLOOR}) — "
        f"a shrunken walk makes this ceiling meaningless."
    )
    assert len(census.unresolved) <= UNRESOLVED_PAYLOAD_CEILING, (
        f"{len(census.unresolved)} json_response/wrapper sites have a payload the census "
        f"cannot resolve to a literal (ceiling {UNRESOLVED_PAYLOAD_CEILING}). Build the "
        f"response body at the call site — or emit the error with "
        f"personalclaw.http_errors.json_error, which needs no payload dict at all. New "
        f"sites:\n"
        + "\n".join(f"  {f}:{ln} ({kind})" for f, ln, kind, _via in census.unresolved[-12:])
    )


def test_the_one_emitter_is_actually_used():
    """The point of PL-8: thirteen module-local helpers became one shared emitter."""
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_BASELINE, (
        f"only {len(census.emitter_sites)} json_error call sites (floor "
        f"{EMITTER_SITE_BASELINE}) — a module grew its own error helper again."
    )
    files = {f for f, _ in census.emitter_sites}
    assert len(files) >= 12, (
        f"json_error is used in only {len(files)} modules; PL-8 replaced helpers in "
        f"thirteen. A module reverted to a local helper."
    )


def test_no_module_local_error_helper_came_back():
    """The thirteen deleted helpers are deleted — no wrappers, no revivals.

    Any ``def _err``/``_error``/``_bad_request`` that returns a ``web.Response`` is
    the exact hazard PL-8 removed: a second emitter whose argument order is free to
    disagree with the shared one.

    **This rail is a NAME match, and that is its known limit.** It scored
    `inbound/bridge.py`'s eleven flat envelopes at zero because the helper was called
    ``_json``. Kept as-is — the three names it lists are the ones PL-8 actually deleted,
    and a rail that says "no revival of THESE" is honest about its scope. The general
    case is not fixable by adding names, so it is not fixed here: it is fixed by
    `test_the_wrapper_routed_flat_population_never_grows`, which keys on SHAPE and
    therefore catches a helper whose name nobody thought to list.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
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
