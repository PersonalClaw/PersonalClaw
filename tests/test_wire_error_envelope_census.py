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
FLAT_BASELINE = 1507

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

    @property
    def structured_total(self) -> int:
        return len(self.structured_direct) + len(self.emitter_sites)


def _is_json_response(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "json_response") or (
        isinstance(f, ast.Name) and f.id == "json_response"
    )


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
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        census.files_scanned += 1
        rel = str(path.relative_to(SRC.parent.parent))
        constants = _module_string_constants(tree)
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
            if not _is_json_response(node):
                continue
            payload = node.args[0] if node.args else None
            if payload is None:
                for kw in node.keywords:
                    if kw.arg == "data":
                        payload = kw.value
            if not isinstance(payload, ast.Dict):
                continue
            for key, value in zip(payload.keys, payload.values):
                if not (isinstance(key, ast.Constant) and key.value == "error"):
                    continue
                has_code = isinstance(value, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "code" for k in value.keys
                )
                (census.structured_direct if has_code else census.flat).append((rel, node.lineno))
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
