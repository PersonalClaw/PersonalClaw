"""Where a credential sits in an MCP server's arguments and in its address.

A server's arguments and URL are free text, and a token goes there when it is not in an
environment variable or a header: ``--api-token=…``, ``--api-key …``, ``--header "Authorization:
Bearer …"``, ``https://user:pw@…``, ``?token=…``. Three readers need to know where:

* the credential store (``config.secret_refs``) keeps each such value in the store and leaves a
  ``{{secret:…}}`` reference in its place, so ``mcp.json``, the agent config, a snapshot, an export
  and a sync carry the reference and never the value, and a start of the server puts it back;
* the owner's yes to a server (``mcp_grants``) is sealed to what it runs, not to the values it is
  handed, as it is for its environment: each spot reads as the mask in the seal;
* every page that shows a server masks it (``mcp_discovery.masked_args``), by a rule deliberately
  wider than this one.

A spot is found by its PLACE where the place says what it holds: the value of a flag named for a
credential (``is_credential_field_name``, the repository's one rule), a header's value, the login
an address carries, a query value whose name is a credential's. So the stored definition (a
reference there) and the started one (the value there) read alike. Where the place says nothing,
the VALUE must: an argument in a format only credentials have (``security.redact_credentials``),
or a part of an address shaped like a token (:func:`looks_secret`). A long word standing alone is
masked on every page but is not moved: a package or a project name can look like one, and a home
restored elsewhere would then ask for a value nobody was ever shown, and a change to what the
server runs would no longer be asked about.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from urllib.parse import unquote

from personalclaw.apps.secret_fields import SECRET_MASK, is_credential_field_name
from personalclaw.workflows.secrets import SECRET_BINDING_RE

#: Flags whose NEXT argument is an HTTP header (``mcp-remote --header "Authorization: Bearer …"``,
#: curl's ``-H``): its name stays, its value is the credential.
HEADER_FLAGS = frozenset({"--header", "--headers", "-H"})
#: A scheme, as ``urlsplit`` reads one: so ``--url=https://…`` is not mistaken for a URL.
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
#: A run of token characters. Long enough, and mixing letters and digits, it is a key in a format
#: no named rule knows (a hex or base64url secret). ``/`` and ``.`` are not in it: a path, a host
#: name and a version number are not tokens, and a JWT's parts are tested one by one.
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9_\-+=~]{20,}")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"\S+")


def looks_secret(text: str) -> bool:
    """Shaped like a credential: a reference to one in the credential store, a format the
    credential redactor knows (a provider key, a bearer token, ``api_key=…``), or a long run of
    token characters mixing letters and digits."""
    from personalclaw.security import redact_credentials

    if SECRET_BINDING_RE.search(text) or redact_credentials(text)[1]:
        return True
    return any(
        _TOKEN_RUN_RE.fullmatch(piece) and _LETTER_RE.search(piece) and _DIGIT_RE.search(piece)
        for piece in text.split(".")
    )


def flag_carries(arg: str) -> str | None:
    """What the argument AFTER ``arg`` holds: ``"header"``, a credential ``"value"``, or neither."""
    if arg in HEADER_FLAGS:
        return "header"
    if arg.startswith("-") and "=" not in arg and is_credential_field_name(arg.lstrip("-")):
        return "value"
    return None


@dataclass(frozen=True)
class Spot:
    """Where one credential sits in a text: ``text[start:end]``. ``kind`` and ``name`` say what it
    is in words that hold no value: ``flag`` (``name`` is the flag, ``--api-token``), ``header``
    (``name`` is the header), ``login`` (an address's), ``query`` (``name`` is the query key),
    ``path`` (a part of an address's path) or ``key`` (an argument in a key's format)."""

    start: int
    end: int
    kind: str
    name: str = ""

    def shifted(self, by: int) -> Spot:
        return replace(self, start=self.start + by, end=self.end + by)


def _is_reference(text: str) -> bool:
    return SECRET_BINDING_RE.fullmatch(text) is not None


def _is_key_format(word: str) -> bool:
    from personalclaw.security import redact_credentials

    return bool(redact_credentials(word)[1])


def _header_spots(text: str) -> list[Spot]:
    """A ``Name: value`` header's value, without the whitespace around it."""
    name, sep, value = text.partition(":")
    held = value.strip()
    if not sep or not name.strip() or not held:
        return []
    start = len(name) + 1 + (len(value) - len(value.lstrip()))
    return [Spot(start, start + len(held), "header", name.strip())]


def _spots_after(word: str, carried: tuple[str, str] | None) -> list[Spot]:
    """*word*'s spots, *carried* being what the word before it said this one holds."""
    if carried is None:
        return _word_spots(word)
    kind, flag = carried
    if kind == "header":
        return _header_spots(word)
    return [Spot(0, len(word), "flag", flag)] if word else []


def _carried(word: str) -> tuple[str, str] | None:
    carries = flag_carries(word)
    return (carries, word) if carries else None


def _word_spots(word: str) -> list[Spot]:
    scheme, sep, _rest = word.partition("://")
    if sep and SCHEME_RE.fullmatch(scheme):
        return list(address_spots(word))
    name, sep, value = word.partition("=")
    if sep and name and not any(ch.isspace() for ch in name):
        base = len(name) + 1
        if name in HEADER_FLAGS:
            return [s.shifted(base) for s in _header_spots(value)]
        if is_credential_field_name(name.lstrip("-")):
            return [Spot(base, len(word), "flag", name)] if value else []
        return [s.shifted(base) for s in _word_spots(value)]
    if any(ch.isspace() for ch in word):
        # A shell string (``sh -c "…"``), read word by word by the same rules.
        spots: list[Spot] = []
        carried: tuple[str, str] | None = None
        for match in _WORD_RE.finditer(word):
            part = match.group()
            spots.extend(s.shifted(match.start()) for s in _spots_after(part, carried))
            carried = _carried(part)
        return spots
    if word and (_is_reference(word) or _is_key_format(word)):
        return [Spot(0, len(word), "key")]
    return []


def argument_spots(args: Sequence[str]) -> list[tuple[Spot, ...]]:
    """The spots of each of a server's arguments, in order."""
    out: list[tuple[Spot, ...]] = []
    carried: tuple[str, str] | None = None
    for arg in args:
        out.append(tuple(_spots_after(arg, carried)))
        carried = _carried(arg)
    return out


def address_spots(url: str) -> tuple[Spot, ...]:
    """The spots of an address: its login, the value of each query key named for a credential or
    shaped like one, and each part of its path shaped like a token. Nothing of a text that is not
    an address with a scheme."""
    from personalclaw.address_logins import url_userinfo_spans

    scheme, sep, _rest = url.partition("://")
    if not sep or not SCHEME_RE.fullmatch(scheme):
        return ()
    spots: list[Spot] = []
    host = len(scheme) + 3
    for start, end, found in url_userinfo_spans(url):
        # Only the address's own login: one quoted later in it (a query value) is read there.
        if start == 0:
            spots.append(Spot(len(found) + 3, end - 1, "login"))
            host = end
        break
    authority_end = min([i for i in (url.find(c, host) for c in "/?#") if i != -1] or [len(url)])
    at = url.rfind("@", host, authority_end)
    if not spots and at != -1 and _is_reference(url[host:at]):
        # A login kept in the credential store: the reference is the whole of it.
        spots.append(Spot(host, at, "login"))
        host = at + 1
    path_end = min([i for i in (url.find(c, authority_end) for c in "?#") if i != -1] or [len(url)])
    pos = authority_end
    for segment in url[authority_end:path_end].split("/"):
        if segment and looks_secret(unquote(segment)):
            spots.append(Spot(pos, pos + len(segment), "path"))
        pos += len(segment) + 1
    if path_end < len(url) and url[path_end] == "?":
        fragment = url.find("#", path_end)
        query_end = len(url) if fragment == -1 else fragment
        pos = path_end + 1
        for pair in url[pos:query_end].split("&"):
            key, eq, value = pair.partition("=")
            if (
                eq
                and value
                and (is_credential_field_name(unquote(key)) or looks_secret(unquote(value)))
            ):
                spots.append(Spot(pos + len(key) + 1, pos + len(pair), "query", unquote(key)))
            pos += len(pair) + 1
    return tuple(spots)


def replaced(text: str, spots: Iterable[Spot], value_for: Callable[[Spot, str], str]) -> str:
    """*text* with each spot's content replaced by ``value_for(spot, content)``."""
    out: list[str] = []
    pos = 0
    for spot in sorted(spots, key=lambda s: s.start):
        out.append(text[pos : spot.start])
        out.append(value_for(spot, text[spot.start : spot.end]))
        pos = spot.end
    out.append(text[pos:])
    return "".join(out)


def sealed_arguments(args: Sequence[str]) -> list[str]:
    """*args* with every spot read as the mask: what a seal of what a server runs is taken of."""
    return [
        replaced(arg, spots, lambda _spot, _value: SECRET_MASK)
        for arg, spots in zip(args, argument_spots(args), strict=True)
    ]


def sealed_address(url: str) -> str:
    """*url* with every spot read as the mask (:func:`sealed_arguments`)."""
    return replaced(url, address_spots(url), lambda _spot, _value: SECRET_MASK)


def described(spot: Spot) -> str:
    """What *spot* holds, as the sentence that asks for it says it: where the value goes, never
    the value."""
    if spot.kind == "flag":
        return f"the value of {spot.name}"
    if spot.kind == "header":
        return f"the value of its {spot.name} header"
    if spot.kind == "login":
        return "the login in its address"
    if spot.kind == "query":
        return f"the {spot.name} value in its address"
    if spot.kind == "path":
        return "the token in its address"
    return "a key in its arguments"
