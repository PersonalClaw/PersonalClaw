"""The request-body parity invariants: ONE function reads a request body, and NO door
swallows a malformed one.

`personalclaw.request_validation.json_object_body` is that function. Every other route and
helper must go through it, because the thing a private reader gets wrong is not a detail of
one route — it is whether the route can refuse a malformed request AT ALL.

**What this replaces, re-measured on ``origin/main`` at ``dd5c279db`` — and unchanged in kind
from ``63ec012ad``, the branch point of the work that began this file, so the shape is a
property of the tree rather than of one moment in it.** Nineteen functions across nineteen
modules read the body themselves, with ELEVEN distinct return contracts::

    dict                                     autonomy._body · desktop._json_body
                                             proactive._body · views._json_body
                                             voice_profiles._body
    dict[str, Any]                           browse_connector._body · devices._body
                                             push._body
    dict | None                              packs._json_body
    dict | web.Response                      chat_plan._body · loop_routes._json_body
    dict[str, Any] | web.Response            workflows.handlers._json_body
    tuple[dict, web.Response | None]         durability._history_body
                                             knowledge._restructure_body
    tuple[dict[str, Any] | None, Response]   research_reports._body
    tuple[str | None, web.Response | None]   routing._agent_from_body
    tuple[str, str] | web.Response           hierarchy_handlers._claim_body
    tuple[object, OrganizeProposal|None, …]  session_organize._proposal_from_body
    bool                                     security_credentials._confirmed

**EIGHT of those nineteen returned a bare ``dict``**, and that is the defect this invariant
exists against: a function whose only return type is ``dict`` has no way to SAY "no". Every
one of them ended ``except Exception: return {}``, so a truncated or malformed body arrived
at the handler as an empty dict and the handler carried on and wrote a defaulted record at
200 (#2923 is exactly that, at an inline copy of the same five lines). Refusing malformed
input was not a behaviour those routes got wrong — it was structurally unavailable to them.

**Why a STRUCTURAL rail and not a per-route test.** The lesson of #2983: nine doors had the
defect, eight were fixed, and the per-route suite stayed green because it only knew about the
eight. A per-route test cannot fail for the twentieth module, which is the only regression
that matters here — someone adds a route, writes the same five lines again, and every
existing test passes. So this rail asks a question about the SHAPE of the tree instead, and
it enumerates the modules from the filesystem (``src/personalclaw/**/*.py``) rather than from
a list in this file. There is no allowlist to forget to update, and nothing here needs
editing when a module is added, renamed or split.

That property is load-bearing and was worth proving rather than asserting: run this rail
against ``dd5c279db`` and it fails naming all nineteen with their return contracts; run it
against the tree that ships it and it passes with one reader plus one stated exemption. A rail
that cannot fail protects nothing.

**A name-grep gets this wrong in BOTH directions, which is why the classifier reads
behaviour instead.** ``git grep -l -E "^\\s*(async )?def (_json_body|_body)\\("`` over
``src/personalclaw`` returns FIFTEEN files on ``dd5c279db``. Two are false positives —
``learning/consumer_liveness.py`` and ``packs/external_formats.py``, both ``_body``, a
proposal's and a persona's prose — because "body" there means the content of a document, not
an HTTP request. (Widen the pattern to a substring and it also picks up
``artifacts/native.py``'s ``_body_filename``, an on-disk filename, and ``cli_app_new.py``'s
``_body_for``, a generated stub's source text.) And it MISSES six real readers that carry
neither name: ``_history_body``, ``_restructure_body``, ``_agent_from_body``,
``_proposal_from_body``, ``_claim_body`` and ``_confirmed``. Thirteen and fifteen are both
wrong; nineteen is the count you get by asking which functions actually await
``request.json()``.

**The classifier, and why the discriminator is the RETURN ANNOTATION.** A function that
awaits ``request.json()`` is doing one of two different jobs:

* it is a ROUTE HANDLER, which must return a ``web.Response`` — it reads its own body inline
  and answers the request itself;
* it is a READER, which returns the body (or a value parsed out of it) for a caller to use —
  and therefore has to have decided, privately, what a malformed body means.

Only the second kind is a *reader*, and the annotation separates them exactly: a handler's is
``web.Response`` (or a union of response types), a reader's is anything else. Every one of the
nineteen is visible in the table above by its annotation alone. An UNANNOTATED function that
reads the body counts as a reader too, deliberately — otherwise deleting the annotation would
be a way to slip past this rail, and "it is not annotated" is not a reason to trust it.

**Four spellings reach the body, and recognising only one hid a real reader.** The original
classifier matched ``request.json()`` alone. ``inbound/openai_dialect._read_json`` reads
``request.content.read(cap)`` and calls ``json.loads`` itself, so it read as "does not touch
the body" — a twentieth private reader, invisible. ``_request_read_kind`` now also matches
``request.read()``, ``request.text()`` and ``request.content.read()``, qualified by a
``.loads(...)`` call in the same function so that upload and webhook-signature handlers (which
read raw bytes and never parse JSON) do not become false positives. Measured, that qualifier is
the difference between two hits and a list long enough to need an allowlist.

That twentieth reader is the file's ONE exemption, in ``_EXEMPTIONS`` with its reason, because
it cannot be folded in rather than merely has not been: it enforces a BYTE CAP that
``json_object_body`` has no concept of (dropping it would delete a size control on a
third-party-reachable surface), and it must refuse in OpenAI's error dialect, which
``RequestValidationError`` cannot produce. ``test_every_exemption_is_still_a_real_reader``
fails if that entry ever goes stale, so the exemption cannot quietly become a hole.

**The SECOND invariant, and why one reader was not enough on its own.** Deleting the nineteen
readers leaves the same defect wherever a route handler reads its body INLINE and swallows the
failure. Measured on ``dd5c279db``: 53 ``try``/``except`` blocks around a body read had an
except-handler that FALLS THROUGH — no ``return``, no ``raise`` — and so continued the handler
with ``body = {}`` (or ``pass``, or ``query = ""``). #2923 is one of those 53 and there is
nothing structurally special about it; it is simply the one someone drove a ``curl`` at. A rail
that asserted only "one reader" would have gone green with 53 doors still discarding malformed
input, which is exactly the #2983 failure mode one level down — so
``test_no_door_swallows_a_malformed_body`` asserts the fall-through count is ZERO, enumerated
from the filesystem the same way.

An inline read that does NOT swallow needs no change: an unguarded
``json.JSONDecodeError`` is a ``ValueError``, and
:mod:`personalclaw.dashboard.request_boundary` already turns that into
``400 bad_request`` for every ``/api/*`` route. Swallowing is what defeats that middleware,
which is why the assertion is about the except-handler's control flow rather than about the
read.

🔴 **Deliberately NOT asserted: the SHAPE of the refusal at the 157 inline doors that already
refuse.** They answer ``400`` — which is what these invariants are about — but with the flat
``{"error": "invalid JSON"}`` body rather than the structured
``{"error": {"code", "message"}}`` envelope that ``AGENTS.md`` §"Shared conventions" mandates
for new routes. That split is a separately-owned defect (roadmap atom ``PL-8``, plus #1854 for
its two rawest children) covering all ~134 flat-envelope sites across the API, not just the
body-read ones. Converging them HERE would fork PL-8's work and mint the envelope decision in
two places, so this file measures the split and leaves it: 157 flat, 17 ``json_error``, 3
behind module-local ``_deny`` helpers.

🔴 **Also NOT asserted: that inline reads go through the one reader at all.** 182 route
handlers still call ``await request.json()`` in their own bodies. Each answers its own request,
none swallows any more, and each is individually visible to a route test — so the property that
MATTERS is already pinned by the two invariants above. Converging the call syntax as well is a
larger mechanical change; ``test_request_json_is_called_only_by_the_one_reader`` below is
written and skipped so the next author inherits a measurement rather than a memory.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "personalclaw"

#: The ONE reader. Spelled as (module, qualname) so a same-named helper appearing in some
#: other module is still a failure — the invariant is about identity, not about the name.
THE_READER = ("personalclaw.request_validation", "json_object_body")

#: aiohttp's response types. A function annotated with only these is answering the request
#: itself (a route handler), not handing a body back to a caller.
_RESPONSE_NAMES = frozenset({"Response", "StreamResponse", "WebSocketResponse", "FileResponse"})

#: The names a request argument goes by in this codebase. Verified exhaustive by scanning
#: every `*.json()` call under `src/`: `request` everywhere, `req` in a handful of helpers.
_REQUEST_NAMES = frozenset({"request", "req"})

#: Readers that are NOT `json_object_body` and are allowed to exist, each with the reason.
#: An exemption is a liability, so it is spelled with its justification inline and guarded by
#: `test_every_exemption_is_still_a_real_reader` below — a stale entry fails rather than
#: silently widening the invariant. There is exactly one, and it is not a `dashboard/` route.
_EXEMPTIONS: dict[tuple[str, str], str] = {
    ("personalclaw.inbound.openai_dialect", "_read_json"): (
        "The `/v1/*` OpenAI-compatibility surface. Two properties make it unmergeable with "
        "`json_object_body` rather than merely unmerged. (1) It enforces a BYTE CAP — "
        "`request.content.read(DEFAULT_CAPS.body_bytes + 1)` — on an inbound surface reached "
        "by third-party clients; `json_object_body` has no cap, so folding this in would "
        "DELETE a size control, not refactor one. (2) It must refuse in OpenAI's error "
        "dialect, and `RequestValidationError` is served by `request_boundary` in "
        "PersonalClaw's envelope for ANY path (that branch is deliberately not /api-scoped), "
        "so raising here would answer an OpenAI client a shape it cannot parse. Giving the "
        "shared reader a `max_bytes` knob AND a second error dialect would move both problems "
        "into it. Revisit only together with the inbound caps owner."
    ),
}


def _is_response_annotation(node: ast.expr | None) -> bool:
    """Does this annotation denote ONLY aiohttp response types?

    ``web.Response``, ``Response``, ``web.Response | None`` and ``Optional[web.Response]``
    are all response-only. ``tuple[str, str] | web.Response`` is NOT — it can hand a value
    back, which makes its function a reader. ``None`` (no annotation) is not either, which
    is the point: an unannotated body-reading function is treated as a reader rather than
    given the benefit of the doubt.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in _RESPONSE_NAMES
    if isinstance(node, ast.Attribute):  # `web.Response`
        return node.attr in _RESPONSE_NAMES
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # A stringized annotation. Parsed rather than pattern-matched so that
        # `"web.Response | None"` is read the same way the unquoted form is.
        try:
            return _is_response_annotation(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_response_annotation(node.left) and _is_response_annotation(node.right)
    if isinstance(node, ast.Subscript):
        value = node.value
        name = (
            value.id
            if isinstance(value, ast.Name)
            else (value.attr if isinstance(value, ast.Attribute) else "")
        )
        if name == "Optional":
            return _is_response_annotation(node.slice)
        if name == "Union":
            parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return all(_is_response_annotation(p) for p in parts)
        # `tuple[...]`, `dict[...]`, `list[...]` — a container hands values back.
        return False
    return False


def _request_read_kind(call: ast.Call) -> str | None:
    """Which request-body read this call is, if any.

    Four spellings reach the same bytes: ``request.json()`` (aiohttp parses),
    ``request.read()``/``request.text()`` and ``request.content.read(n)`` (raw, parsed by the
    caller). Only the first was recognised originally, and that blind spot hid a real reader:
    ``inbound/openai_dialect._read_json`` reads ``request.content.read(cap)`` and calls
    ``json.loads`` itself, so a ``request.json()``-only classifier reported it as not reading
    a body at all. A rail that cannot see a whole spelling of the thing it forbids is the
    ``_UNTRUSTED_ROOTS`` failure mode — enumerate the ways, not the ones you remember.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in ("json", "read", "text"):
        return None
    base = func.value
    if isinstance(base, ast.Name) and base.id in _REQUEST_NAMES:
        if func.attr == "json" and (call.args or call.keywords):
            # `json.dumps(...)`-style calls take arguments; `request.json()` never does.
            return None
        return func.attr
    if (
        func.attr == "read"
        and isinstance(base, ast.Attribute)
        and base.attr == "content"
        and isinstance(base.value, ast.Name)
        and base.value.id in _REQUEST_NAMES
    ):
        return "content.read"
    return None


def _reads_the_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """Line at which THIS function reads the request body AS JSON, or None.

    Nested functions are skipped so a closure's read is attributed to the closure and not
    to its enclosing route — otherwise a handler defining an inner helper would be
    misreported, and the inner helper would be missed.

    ``request.json()`` counts on its own. The raw spellings count only when the same function
    also calls ``.loads(...)``, and that qualifier is what keeps the rail precise rather than
    merely wide: upload and webhook-signature handlers legitimately read raw request bytes and
    never parse them as a JSON body. Measured, the qualifier is the difference between 2 hits
    and a noisy list needing an allowlist — which is the thing this file refuses to have.
    """
    read_line: int | None = None
    raw_line: int | None = None
    parses_json = False
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
            continue
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "loads":
            parses_json = True
            continue
        kind = _request_read_kind(node)
        if kind == "json":
            read_line = read_line or node.lineno
        elif kind is not None:
            raw_line = raw_line or node.lineno
    if read_line is not None:
        return read_line
    return raw_line if (raw_line is not None and parses_json) else None


def _walk_functions(node: ast.AST, prefix: tuple[str, ...] = ()):
    """Every function in the tree, with its dotted qualname. Nested defs included."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = prefix + (child.name,)
            yield ".".join(qual), child
            yield from _walk_functions(child, qual)
        else:
            yield from _walk_functions(child, prefix)


def _module_name(path: pathlib.Path) -> str:
    return str(path.relative_to(SRC).with_suffix("")).replace("/", ".")


def _body_readers() -> list[tuple[str, str, int, str]]:
    """(module, qualname, line, annotation) for every body reader in the package."""
    found: list[tuple[str, str, int, str]] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path)
        for qualname, fn in _walk_functions(tree):
            line = _reads_the_body(fn)
            if line is None or _is_response_annotation(fn.returns):
                continue
            annotation = ast.unparse(fn.returns) if fn.returns else "<unannotated>"
            found.append((module, qualname, line, annotation))
    return found


def _swallowing_body_reads() -> list[tuple[str, int, str]]:
    """(module, line, except-handler source) for every body read whose failure FALLS THROUGH.

    "Falls through" means the ``except`` handler contains no ``return`` and no ``raise``, so
    control resumes after the ``try`` with whatever default the handler assigned. That is the
    #2923 shape and it is the ONE thing that defeats the request-boundary middleware: an
    unguarded ``json.JSONDecodeError`` is a ``ValueError`` and already becomes a 400, but a
    swallowed one never reaches the middleware at all.

    Only the FIRST handler of each ``try`` is classified. A body read has one failure mode
    worth distinguishing, so a multi-handler ``try`` that refuses in its first arm is refusing;
    the count exists to find the arm that silently continues, not to audit exception ladders.
    """
    found: list[tuple[str, int, str]] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not node.handlers:
                continue
            line = next((ln for ln in (_reads_the_body(s) for s in node.body) if ln), None)
            if line is None:
                continue
            handler = node.handlers[0]
            if any(isinstance(n, (ast.Return, ast.Raise)) for n in ast.walk(handler)):
                continue
            found.append((module, line, "; ".join(ast.unparse(s) for s in handler.body)[:70]))
    return found


def test_no_door_swallows_a_malformed_body() -> None:
    """No ``except`` around a body read falls through to a default. The #2923 invariant.

    Separate from the one-reader assertion on purpose: deleting the nineteen private readers
    left 53 inline doors on ``dd5c279db`` still discarding malformed input, so a rail that
    checked only reader parity would have gone green over the defect it was built to remove.
    """
    swallowers = _swallowing_body_reads()
    assert not swallowers, (
        "these doors read the request body and SWALLOW a parse failure, continuing with a "
        "default instead of refusing:\n"
        + "\n".join(f"  {m} (line {ln}) -> except: {src}" for m, ln, src in swallowers)
        + "\n\nA swallowed parse failure never reaches `request_boundary`, so the route "
        "answers 200 with a defaulted record (#2923). Call "
        "`request_validation.json_object_body(request)` — it returns {} for a genuinely "
        "EMPTY body and raises for a malformed one, which is the distinction the `except` "
        "arm above collapses."
    )


def test_the_swallow_classifier_still_recognises_guarded_reads() -> None:
    """The vacuity floor for the assertion above, which is also of the form "no module does X".

    ``_swallowing_body_reads`` returning nothing is the goal — and is also exactly what a
    classifier that has stopped recognising ``try``/``except`` around a body read returns. So
    assert the population it is SELECTING FROM is still large: 178 body reads on the tree that
    ships this are wrapped in a ``try`` whose handler returns or raises, i.e. correctly refuses.
    If that collapses, this rail is green for the wrong reason.
    """
    guarded = 0
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not node.handlers:
                continue
            if not any(_reads_the_body(s) for s in node.body):
                continue
            if any(isinstance(n, (ast.Return, ast.Raise)) for n in ast.walk(node.handlers[0])):
                guarded += 1
    assert guarded > 100, (
        f"only {guarded} body reads are wrapped in a refusing try/except — the swallow "
        "classifier has stopped recognising guarded reads, so "
        "`test_no_door_swallows_a_malformed_body` is vacuous"
    )


def test_the_package_has_python_sources_to_scan() -> None:
    """The vacuity floor: a rail over an empty file list passes and proves nothing.

    Named explicitly because every assertion below is of the form "no module does X", and
    that is trivially true of no modules. The real numbers on the tree that ships this are
    1128 source files and 186 body-reading functions (1127 and 265 on ``dd5c279db`` before
    it), so a collapse to a handful means the scan root moved, not that the defect was fixed.
    """
    sources = list(PKG.rglob("*.py"))
    assert len(sources) > 400, f"expected the whole package, scanned only {len(sources)} files"
    readers_and_handlers = sum(
        1
        for path in sources
        for _, fn in _walk_functions(ast.parse(path.read_text(encoding="utf-8")))
        if _reads_the_body(fn) is not None
    )
    assert readers_and_handlers > 100, (
        f"only {readers_and_handlers} functions read a request body — the classifier has "
        "stopped recognising body reads, so the parity assertion below is vacuous"
    )


def test_exactly_one_function_reads_the_request_body() -> None:
    """No module defines its own body reader. There is one, and it is the shared one.

    This is the whole invariant. It fails for a NEW module that writes its own reader, which
    is the regression a per-route test cannot see, and it names the offender and its return
    contract so the fix is obvious from the failure alone.
    """
    readers = _body_readers()
    allowed = {THE_READER} | set(_EXEMPTIONS)
    offenders = [r for r in readers if (r[0], r[1]) not in allowed]
    assert not offenders, (
        "these functions read the request body privately instead of calling "
        "`personalclaw.request_validation.json_object_body`:\n"
        + "\n".join(f"  {m}.{q} (line {ln}) -> {ann}" for m, q, ln, ann in offenders)
        + "\n\nA reader that does not return `web.Response` has decided, privately, what a "
        "malformed body means — and a reader annotated `-> dict` cannot refuse one at all. "
        "Call `json_object_body(request)` and let `RequestValidationError` reach the "
        "request-boundary middleware. If it genuinely cannot (a byte cap, or a non-PersonalClaw "
        "error dialect), add it to `_EXEMPTIONS` WITH the reason — do not widen this list "
        "silently."
    )
    assert len(readers) == 1 + len(_EXEMPTIONS), (
        f"expected {1 + len(_EXEMPTIONS)} body readers (the shared one plus "
        f"{len(_EXEMPTIONS)} exemption(s)), found {len(readers)}"
    )


def test_every_exemption_is_still_a_real_reader() -> None:
    """A stale exemption silently widens the invariant, so each one must still resolve.

    Two ways an exemption rots: the function is deleted or renamed (the entry then excuses
    nothing but still inflates the expected count above), or it stops reading a body (it no
    longer needs excusing, and leaving it invites the next author to copy a stale precedent).
    Both fail here. Every entry must also carry a non-empty reason — an exemption whose
    justification was never written down is indistinguishable from an oversight.
    """
    readers = {(m, q) for m, q, _, _ in _body_readers()}
    for key, reason in _EXEMPTIONS.items():
        module, qualname = key
        path = SRC / (module.replace(".", "/") + ".py")
        assert path.is_file(), f"exemption {module}.{qualname}: {module} no longer exists"
        names = {q for q, _ in _walk_functions(ast.parse(path.read_text(encoding="utf-8")))}
        assert qualname in names, f"exemption {module}.{qualname}: no such function any more"
        assert key in readers, (
            f"exemption {module}.{qualname} no longer reads a request body — delete the "
            "exemption rather than leaving it to excuse a future reader"
        )
        assert len(reason.strip()) > 80, (
            f"exemption {module}.{qualname} carries no real justification; an exemption "
            "without a stated reason is an oversight with extra steps"
        )


def test_the_one_reader_is_where_it_is_declared_to_be() -> None:
    """`THE_READER` resolves. Otherwise the assertion above could pass by naming nothing."""
    module, qualname = THE_READER
    path = SRC / (module.replace(".", "/") + ".py")
    assert path.is_file(), f"{module} does not exist — the exemption names no real function"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {q for q, _ in _walk_functions(tree)}
    assert qualname in names, f"{module} defines no {qualname}"
    assert (module, qualname) in {(m, q) for m, q, _, _ in _body_readers()}, (
        f"{module}.{qualname} no longer reads the request body — the exemption is stale and "
        "every other reader is now unguarded by this rail"
    )


def test_the_one_reader_can_refuse_a_malformed_body() -> None:
    """The reader's contract is that it CAN say no — the property the eight `-> dict`s lacked.

    Asserted on the signature rather than by driving a request: `-> dict[str, Any]` is what
    `autonomy._body` was annotated too, so the annotation alone does not distinguish them.
    What does is that this one declares a raise. The behavioural half lives in
    `tests/test_request_validation.py`.
    """
    path = SRC / (THE_READER[0].replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(f for q, f in _walk_functions(tree) if q == THE_READER[1])
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert raises, (
        "the one reader has no `raise` — it cannot refuse a malformed body, which is the "
        "entire reason it replaced nineteen private readers"
    )
    raised = {ast.unparse(n.exc).split("(")[0] for n in raises if n.exc is not None}
    assert "RequestValidationError" in raised, (
        f"the reader raises {raised or '{}'} — the request-boundary middleware only "
        "translates RequestValidationError into the wire envelope"
    )


@pytest.mark.skip(
    reason="The stricter form of the invariants above, recorded rather than enforced. "
    "182 route handlers still read `request.json()` inline; each answers its own request, "
    "none is a private reader, and none swallows a parse failure any more, so the property "
    "that matters is already pinned. Converging the call syntax too is a separate, much "
    "larger change. Unskip it when they are migrated — do not weaken the number to pass."
)
def test_request_json_is_called_only_by_the_one_reader() -> None:
    """Eventually: nothing outside the one reader touches `request.json()` at all."""
    callers: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for qualname, fn in _walk_functions(tree):
            line = _reads_the_body(fn)
            if line is not None and (module, qualname) != THE_READER:
                callers.append(f"{module}.{qualname} (line {line})")
    assert not callers, "\n".join(callers)
