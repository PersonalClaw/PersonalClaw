"""The ONE request-body reader and the ONE string-field check on the write path.

`AGENTS.md` §"Shared conventions" → **Error envelope (HTTP)** owns the shape a
refusal travels in; :mod:`personalclaw.http_errors` owns the emitter. This module
owns the layer above both: *deciding* that a request is malformed in the first
place, which until now every handler decided for itself.

**Why this module exists.** Measured on the tree that introduced it, twelve
modules under ``dashboard/`` defined their own private body reader —
``chat_plan._body``, ``handlers/autonomy._body``, ``browse_connector._body``,
``desktop._json_body``, ``devices._body``, ``loop_routes._json_body``,
``packs._json_body``, ``proactive._body``, ``push._body``,
``research_reports._body``, ``views._json_body``, ``voice_profiles._body`` — and
they disagreed on the one question that matters. **EIGHT of the twelve were annotated
to return a bare ``dict``** (five ``-> dict``, three ``-> dict[str, Any]``), and
**SEVEN could not refuse a malformed body at all**: the reader had no way to say
"no" and the handler carried on with ``{}``, creating a defaulted record at 200
(#2923 is that defect at an inline copy of the same code). The eighth,
``voice_profiles._body``, is the exception that shows why the annotation is the wrong
thing to read on its own — it is annotated ``-> dict`` yet refuses by RAISING, so it
is the one bare-``dict`` reader that works.

The five that could refuse spelled the refusal FOUR ways:
``json_error("invalid_json", ...)`` (``chat_plan``, ``research_reports``), a flat
``{"error": "invalid JSON"}`` (``loop_routes``), a raised ``VoiceProfileError``
(``voice_profiles``), and a bare ``None`` for the caller to interpret (``packs``).
They also disagreed on whether an absent body is ``{}`` or an error — ``packs``
short-circuits ``can_read_body`` to ``{}``, the other four refuse it.

The same split ran one level down, on the FIELDS. ``"must be a string"`` was
hand-written at 50 sites across 31 files with no helper behind any of them, so
every door re-decided three questions independently — *is it a string · is it
non-blank after strip · does the update door re-ask what the create door asked* —
and a door that skipped one fell through to ``str(...)``, which cannot fail. That
is why ``{"name": {"a": "b"}}`` created a dashboard view called ``{'a': 'b'}``
(#3001: Python's ``repr``, single quotes and all, as a user-visible name), why
three create/update pairs disagreed about a blank name (#2992), and why
``{"name": null}`` became a project literally called ``None`` (#456).

**The behaviour extracted here is not new.** Seven doors already answered a
wrong-typed name with a proper type error (``/api/themes``, ``/api/prompts``,
``/api/prompt-snippets``, ``/api/agents``, ``/api/skills``, ``/api/tasks``,
``/api/chat/tag-columns``). This is that behaviour lifted into one place, not a
new design.

**How a refusal reaches the client.** These functions RAISE
:class:`RequestValidationError`; :mod:`personalclaw.dashboard.request_boundary`
translates it into the wire envelope once, for every route. That is deliberate and
it is the reason adoption is a one-line change at a call site: a handler that had
``body = await _body(request)`` becomes ``body = await json_object_body(request)``
and gains a real refusal without growing a ``try``/``except``. It is the same
argument that middleware already makes for the *unguarded* shape fault — a
per-handler answer is how a systemic gap stays open, because the next route
forgets. The two precisions sit side by side there: the boundary answers a generic
``bad_request`` for a fault nobody guarded, and this module lets a handler answer a
specific 400 that names the offending field.

**Not a superset of the stores' own validation.** A store that raises
``ValueError("collection name is required")`` keeps doing so; this module only
guarantees the value reaching it is a genuine, non-blank ``str``, which is
precisely the guarantee those ``not name`` checks were written assuming and never
had (``"{'a': 'b'}"`` is a perfectly non-empty string by the time it arrives).
"""

from __future__ import annotations

from typing import Any, Final

from aiohttp import web

from personalclaw.http_errors import json_error

__all__ = [
    "MISSING",
    "RequestValidationError",
    "json_object_body",
    "optional_string",
    "require_string",
    "string_field",
]


class RequestValidationError(Exception):
    """A request the handler refuses on shape, carrying the envelope it answers with.

    Deliberately NOT a subclass of ``ValueError``/``TypeError``/``AttributeError``:
    :mod:`~personalclaw.dashboard.request_boundary` catches those three as the
    *unguarded* fault family and answers a generic ``bad_request`` that names no
    field. Inheriting from one of them would route every refusal below through that
    branch and throw away the field name — the whole point of validating.
    """

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    @property
    def response(self) -> web.Response:
        """The wire envelope for this refusal, from the one emitter."""
        return json_error(self.code, message=self.message, status=self.status)


class _Missing:
    """The absent-field sentinel. A singleton so ``is MISSING`` is the only test."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        # Falsy would invite `if value:` at a call site, which is exactly the
        # missing-vs-blank confusion `optional_string` exists to remove. Raise
        # instead, so the mistake is loud at the first call rather than silent.
        raise TypeError("MISSING has no truth value — compare with `is MISSING`")


#: Returned by :func:`optional_string` when the field is ABSENT from the body, which
#: is a different fact from "present and blank". A PATCH that omits a field is not a
#: PATCH that clears it (#2970), and a sentinel is the only way to say so — ``None``
#: cannot, because ``None`` is also a value a client can send.
MISSING: Final[_Missing] = _Missing()


async def json_object_body(request: web.Request, *, empty_ok: bool = True) -> dict[str, Any]:
    """The request's JSON object.

    :param empty_ok: When true (the default) a request that carries NO body at all is
        ``{}`` — the shape every route whose fields are all optional wants. This is
        about *absent bytes*, never about bytes that failed to parse: a parse failure
        is the strongest available signal that the caller is broken, so it is always
        a refusal. Conflating the two is #2923 — ``body = {}`` in the ``except``
        arm, and a truncated request minting a real persisted session at 200.

    The emptiness question is asked AFTER the parse attempt, on the bytes themselves,
    rather than up front on ``request.can_read_body``. That ordering is deliberate and was
    measured: ``can_read_body`` is an aiohttp transport flag, not a statement about
    content, so anything that carries a body without satisfying it — a chunked upload, or
    the ``make_mocked_request`` doubles a hundred-odd handler tests drive these routes
    with — took the short-circuit and received ``{}`` while holding a perfectly good
    payload. A reader whose emptiness test can disagree with its own bytes is the same
    class of defect as the nineteen it replaced, one layer down. ``await request.read()``
    returns the payload aiohttp already cached during the failed ``json()``, so this costs
    no second read of the socket.

    :raises RequestValidationError: ``invalid_json`` if the body is not parseable
        JSON, ``invalid_body`` if it parses to something other than an object.
    """
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — any parse failure is one refusal
        if empty_ok and not await _has_bytes(request):
            return {}
        raise RequestValidationError("invalid_json", "The request body is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise RequestValidationError(
            "invalid_body",
            f"The request body must be a JSON object, not {_type_name(raw)}.",
        )
    return raw


async def _has_bytes(request: web.Request) -> bool:
    """Did the caller send any non-whitespace body bytes at all?

    Only consulted on the failure path, to tell "sent nothing" from "sent something
    broken" — the one distinction ``empty_ok`` is about. A read that itself fails is
    reported as NOT empty, so a transport error becomes the ``invalid_json`` refusal the
    caller is already getting rather than a silent ``{}``: failing closed here is the whole
    point, since a silent ``{}`` is #2923.
    """
    try:
        return bool((await request.read()).strip())
    except Exception:  # noqa: BLE001 — an unreadable body is not an empty one
        return True


def require_string(body: dict[str, Any], field: str, *, strip: bool = True) -> str:
    """A REQUIRED string field: present, a real ``str``, and non-blank after strip.

    Returns the stripped value, so a caller never re-strips (and never forgets to).

    The ``bool`` case is spelled out below rather than left to ``isinstance(x, str)``
    for the reason ``session_templates.py`` shipped a bug over: an allowlist of
    ``(str, int, float)`` admits ``True``, because in Python a ``bool`` IS an ``int``.
    Here the check is positive — it must BE a ``str`` — so no scalar slips through at
    all, which is the property the eight ``str()``-coercing doors lacked.

    :raises RequestValidationError: ``field_not_a_string`` for a non-string (including
        ``null``, a number, a bool, an object or a list), ``field_required`` for a
        missing field or one that is blank once stripped.
    """
    if field not in body:
        raise RequestValidationError("field_required", f"{field} is required.")
    value = body[field]
    if not isinstance(value, str):
        raise RequestValidationError(
            "field_not_a_string",
            f"{field} must be a string, not {_type_name(value)}.",
        )
    cleaned = value.strip() if strip else value
    if not cleaned.strip():
        raise RequestValidationError("field_required", f"{field} must not be blank.")
    return cleaned


def string_field(body: dict[str, Any], field: str, *, default: str = "", strip: bool = True) -> str:
    """A field that may be ABSENT or BLANK, but must be a ``str`` when it has a value.

    The third and last shape, and the one that covers most of a body: ``content``,
    ``icon``, ``query``, ``url``, ``description`` — a value the resource does not
    require but must not INVENT. ``null`` and absence both mean ``default``, because a
    client that omits an optional field and one that nulls it are asking for the same
    thing; anything else non-string is a refusal.

    It exists because the two functions above cannot express
    ``POST /api/knowledge/items``'s ``title``, and getting that wrong is how this
    vocabulary would have grown a knob instead of a third name: a blank title there is
    LEGAL and gets derived downstream (a journal's date, the bookmark's url, a content
    slug), while ``{"title": {"a": "b"}}`` must still be refused rather than stored as
    ``"{'a': 'b'}"``. :func:`require_string` would break the derivation and
    :func:`optional_string` would refuse a blank the route accepts on purpose — so the
    honest answer is a third question, not a flag on one of the other two.

    :raises RequestValidationError: ``field_not_a_string`` for a present non-string
        value other than ``null``. Never ``field_required`` — this field never is.
    """
    value = body.get(field)
    if value is None:
        return default
    if not isinstance(value, str):
        raise RequestValidationError(
            "field_not_a_string",
            f"{field} must be a string, not {_type_name(value)}.",
        )
    return value.strip() if strip else value


def optional_string(body: dict[str, Any], field: str, *, strip: bool = True) -> str | _Missing:
    """An OPTIONAL string field: :data:`MISSING` when absent, else validated as required.

    This is the update-door half of :func:`require_string`, and the asymmetry it
    removes is the family's most common shape: a create door that validates and an
    update door that does not, so the same value the POST refused the PUT persists
    (#2992, #456). An update door calls this and re-applies the create door's rule to
    every field the caller actually sent — no more, which is what keeps a PATCH a
    partial write.

    ABSENT and PRESENT-BUT-BLANK are answered differently on purpose. Omitting a field
    means "leave it alone"; sending ``""`` or ``null`` for a required field is a
    refusal, not an instruction to clear it (#2970). A field that a caller genuinely
    may clear is not a string field with a blank value — it is a nullable field, and
    its handler reads ``body[field] is None`` deliberately rather than routing through
    here.

    :raises RequestValidationError: as :func:`require_string`, but never
        ``field_required`` for absence — absence is :data:`MISSING`, not an error.
    """
    if field not in body:
        return MISSING
    return require_string(body, field, strip=strip)


def _type_name(value: Any) -> str:
    """The JSON name for a value's type, because the client speaks JSON, not Python.

    A refusal that says ``dict`` when the caller wrote ``{...}`` makes them translate;
    worse, telling them ``NoneType`` when they sent ``null`` reads as an internal
    error. The mapping is the same direction as the coercion bug this whole module
    exists to remove: the client's vocabulary wins over Python's.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__
