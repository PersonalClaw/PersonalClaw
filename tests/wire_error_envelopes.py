"""What ``test_wire_error_envelope_census.py`` shares with the suite's other tests.

``Census, EMITTER_SITE_FLOOR, scan, scan_source``.

Kept here, beside the suite, so a test that is not a rail can import them without importing
a rail (rails live in ``tests/rails/``, which only the rails job collects)."""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

import source_index
from site_baseline import code_site, numbered, scoped

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"


#: Calls to the one emitter. A FLOOR. This is also the population the append-only
#: rail must inspect (see ``EMITTER_SITE_FLOOR``).
EMITTER_SITE_BASELINE = 132


#: What the append-only rail must inspect. Derived from the census so a matcher that
#: stops matching cannot read as clean: if the rail's scan finds fewer emitter sites
#: than the census counted, the rail is measuring itself, not the code.
EMITTER_SITE_FLOOR = EMITTER_SITE_BASELINE


@dataclass
class Census:
    files_scanned: int = 0
    structured_direct: list[tuple[str, int]] = field(default_factory=list)
    flat: list[tuple[str, int]] = field(default_factory=list)
    emitter_sites: list[tuple[str, int]] = field(default_factory=list)
    #: ``(file, line, code)`` for every emitter call whose code is a string literal.
    emitter_literal_codes: list[tuple[str, int, str]] = field(default_factory=list)
    #: Emitter calls whose code is computed (``exc.reason``, an f-string, a constant
    #: reference). Not statically checkable against the registry — listed so they
    #: cannot become a hiding place for unregistered codes.
    emitter_dynamic_sites: list[tuple[str, int]] = field(default_factory=list)
    #: The same calls, each named as a site (``site_baseline.code_site``: the path, the
    #: function it is in and the call's source), repeats in one function numbered ``#2``.
    #: ``tests/rails/baselines/dynamic-error-codes.sites.jsonl`` lists them.
    dynamic_code_sites: list[str] = field(default_factory=list)
    #: Flat/structured sites reached through a module-local response WRAPPER rather
    #: than at a ``json_response`` call directly. Kept in their own lists so the
    #: long-standing direct ceilings stay comparable across measurements.
    flat_via_wrapper: list[tuple[str, int]] = field(default_factory=list)
    structured_via_wrapper: list[tuple[str, int]] = field(default_factory=list)
    #: The ``flat_via_wrapper`` calls, each named as a site the same way;
    #: ``tests/rails/baselines/flat-envelopes-via-wrapper.sites.jsonl`` lists them.
    flat_via_wrapper_sites: list[str] = field(default_factory=list)
    #: ``(file, name)`` for every response wrapper the scanner recognised. The vacuity
    #: anchor for the whole wrapper mechanism: if this empties, the two buckets above
    #: read clean for the wrong reason.
    wrapper_defs: list[tuple[str, str]] = field(default_factory=list)
    #: ``(file, line, node_type, via_wrapper)`` for every emit site whose payload the
    #: scanner refused to classify. LOUD by construction — see the docstring.
    unresolved: list[tuple[str, int, str, bool]] = field(default_factory=list)
    #: The same calls, each named as a site the same way;
    #: ``tests/rails/baselines/unresolved-payloads.sites.jsonl`` lists them.
    unresolved_sites: list[str] = field(default_factory=list)

    @property
    def structured_total(self) -> int:
        return len(self.structured_direct) + len(self.emitter_sites)


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


def scan_source(source: str | ast.Module, rel: str, census: Census) -> None:
    """Classify one module's error sites into ``census``, given as source text or its tree.

    Split out of :func:`scan` so the wrapper-following logic can be exercised against a
    SYNTHETIC module (see :func:`test_the_scanner_follows_a_wrapper`). A mechanism whose
    only test is "the tree's current count is N" cannot distinguish "no sites" from
    "detector broken", and that distinction is the whole reason this function exists.
    """
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    census.files_scanned += 1
    constants = _module_string_constants(tree)
    wrappers, accounted = _response_wrappers(tree)
    for name in sorted(wrappers):
        census.wrapper_defs.append((rel, name))
    dynamic: set[int] = set()
    refused: set[int] = set()
    wrapped_flat: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_emitter(node):
            census.emitter_sites.append((rel, node.lineno))
            code = _static_code(node, constants)
            if code is None:
                census.emitter_dynamic_sites.append((rel, node.lineno))
                dynamic.add(id(node))
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
            # REFUSED, not skipped: named below, and the rail's list holds each one.
            census.unresolved.append((rel, node.lineno, type(payload).__name__, via_wrapper))
            refused.add(id(node))
            continue
        for key, value in zip(payload.keys, payload.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            has_code = isinstance(value, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "code" for k in value.keys
            )
            if via_wrapper:
                bucket = census.structured_via_wrapper if has_code else census.flat_via_wrapper
                if not has_code:
                    wrapped_flat.add(id(node))
            else:
                bucket = census.structured_direct if has_code else census.flat
            bucket.append((rel, node.lineno))

    # Each site named for its list in source order, so a repeat in one function is ``#2``.
    if dynamic or refused or wrapped_flat:
        ordered = list(scoped(tree))
        for ids, sites in (
            (dynamic, census.dynamic_code_sites),
            (refused, census.unresolved_sites),
            (wrapped_flat, census.flat_via_wrapper_sites),
        ):
            sites += numbered(code_site(rel, s, node) for node, s in ordered if id(node) in ids)


def scan() -> Census:
    """Walk every module under ``src/personalclaw`` and classify each error site.

    AST, not grep: a multi-line dict literal and a single-line one must count the
    same, and a mention of ``"error"`` in a docstring or a comment must not count
    at all (a text scanner reads comments — see the ``json_error`` docstring, which
    quotes both shapes verbatim and would otherwise inflate every number here).
    """
    census = Census()
    for path, tree in source_index.package_trees():
        scan_source(tree, str(path.relative_to(SRC.parent.parent)), census)
    return census
