"""The credential screen — the *credentials-never-transit-the-agent* invariant, structurally.

The invariant can be stated as a promise: "the LLM never sees a password field's
value, never receives a 2FA code, never handles an OAuth token". A promise is enforced by whoever
remembers it. This module is the mechanism that makes it hold whether anyone remembers or not, and
the shape of that mechanism is the whole point of the file, so it is worth stating plainly.

**Why redaction alone cannot be the answer.** :func:`personalclaw.security.redact_credentials` is
SHAPE- and NAME-based: it knows `sk-ant-…`, `ghp_…` and `name = value` assignments. A human password
(`hunter2`) matches none of them, and a 2FA code (`418290`) matches nothing that is not also a
line number. So a design where the credential travels and a redactor tries to catch it fails for
exactly the inputs this invariant is about. It also **garbles a composed `field: value` line whose
value is another screen's marker** — `password: [withheld]` loses its field name into a
`[REDACTED: …]` mask — which forbids the tempting "redact everything on the way out" posture:
composition happens many times, so the screen must happen ONCE, at the boundary, BEFORE
composition.

**So the invariant is three refusals, not a filter.**

1. **A credential field's value is never READ.** :func:`is_credential_input` decides at the single
   point where ``extraction._handle_input`` turns an ``<input>`` into a record. For a credential
   input the record carries :data:`WITHHELD` unconditionally — not the value, and not "the value if
   non-empty", because whether a password box is prefilled is itself a fact about the user's
   credential store. The value therefore does not exist anywhere in the browse process's state, so
   there is no downstream consumer to audit, no ordering to get right, and no second pass to
   garble. This is the difference between "we remembered not to log it" and "there is nothing to
   log".

2. **The agent cannot WRITE one either.** The loop refuses ``TYPE <ref>(…)`` into a credential
   field and escalates to the ``request_login`` handoff instead (see
   :mod:`personalclaw.browse.handoff`). That refusal is what makes the human handoff the ONLY
   authentication path rather than the polite one: a model that hallucinated a password could not
   spend it, and a caller that supplied one on the action config could not either. It also closes
   the echo: :func:`screen_action_render` replaces the value in the rendered action line, which is
   the string that reaches the stuck-detector, the next prompt's WARNINGS block, the step ledger,
   the SEL row and the parked run's user-facing sentence. Screening it at the render site is the
   "once, before composition" rule applied to the model's own output.

3. **A credential in a URL is never composed in.** The post-login redirect is where an OAuth
   ``code``/``access_token``/``id_token`` actually shows up, and a URL reaches the model through
   three separate paths (the outline's ``# <url>`` header, the fence's ``source``/``source_id``, and
   a link's rendered target) plus three user/audit paths (the park sentence, the run payload, the
   SEL ``resources`` field). :func:`screen_url` replaces the VALUE and keeps the KEY, so
   ``?code=[withheld]&country=US`` stays diagnosable — a URL redacted to nothing turns "the login
   redirect landed somewhere unexpected" into an unanswerable question.

**Deliberately value-replacing, never whole-string.** Every function here removes a value and
leaves its surroundings intact, for the reason `address_logins.redact_url_userinfo` records:
taking out the secret must not take out the ability to read the line. And every replacement is a
literal containing characters the thing it replaces cannot contain, so re-running any of these over
its own output is a no-op — idempotence by construction, not by a guard a later change could
delete.
"""

from __future__ import annotations

import functools
import ipaddress
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import idna

from personalclaw import filled_secrets
from personalclaw.security import redact_known_values

#: What stands in for a credential value, everywhere. Contains characters an HTML attribute value,
#: a URL query value and a sentinel ``TYPE`` argument can all carry, so it is deliberately NOT
#: chosen for un-typeability — it is chosen so a human reading a prompt, a log line or an inbox card
#: sees the same word every time and learns it means "the agent never had this".
WITHHELD = "[withheld]"

#: ``<input type=…>`` values whose content is a credential by definition. Type-based first because
#: it is the one signal a site cannot get wrong without breaking its own login: a password box is
#: ``type=password`` or browsers will not mask it.
CREDENTIAL_INPUT_TYPES: frozenset[str] = frozenset({"password"})

#: ``autocomplete`` tokens that declare a credential on a field whose ``type`` is plain text. Real
#: login forms use these: a one-time-code box is almost always ``type=text
#: autocomplete=one-time-code`` (so the keyboard shows digits and the value is not masked), which
#: means a type-only rule would miss every 2FA field — the exact input the invariant names.
CREDENTIAL_AUTOCOMPLETE: frozenset[str] = frozenset(
    {"current-password", "new-password", "one-time-code"}
)

#: Substrings in a field's ``name``/``id`` that mean "a secret goes here". The weakest of the three
#: signals and deliberately last: it is a heuristic over author-chosen identifiers, so it is scoped
#: to tokens that have no innocent reading on a form field. ``code`` alone is NOT here — a postal
#: code, a country code and a discount code are all ``code``, and screening those would blind the
#: agent to fields it legitimately fills while protecting nothing.
CREDENTIAL_NAME_TOKENS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "passcode",
    "otp",
    "totp",
    "mfa",
    "2fa",
    "twofactor",
    "two_factor",
    "onetimecode",
    "one_time_code",
    "verification_code",
    "verificationcode",
    "security_code",
    "securitycode",
    "authcode",
    "auth_code",
    "secret",
    "api_key",
    "apikey",
    "token",
)

#: URL query/fragment parameter names whose value is a credential or a bearer of one. ``code`` IS
#: here, unlike in :data:`CREDENTIAL_NAME_TOKENS`, and the asymmetry is deliberate: on a form field
#: ``code`` is usually a postal code, but as a URL parameter on a redirect it is the OAuth
#: authorization code — a single-use credential — and the cost of screening a country code in a
#: query string is that the agent reads ``?code=[withheld]`` and can still follow the link by ref.
#: ``state`` is deliberately ABSENT: it is a CSRF nonce, not a credential, and screening it would
#: hide the one parameter that makes a broken OAuth round trip diagnosable.
CREDENTIAL_URL_PARAMS: frozenset[str] = frozenset(
    {
        "code",
        "access_token",
        "id_token",
        "refresh_token",
        "token",
        "auth_token",
        "authtoken",
        "id_assertion",
        "assertion",
        "session",
        "sessionid",
        "session_id",
        "sid",
        "sso",
        "samlresponse",
        "saml_response",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "key",
        "otp",
        "ticket",
        "signature",
        "sig",
    }
)


def is_credential_input(
    input_type: str, *, name: str = "", autocomplete: str = "", placeholder: str = ""
) -> bool:
    """Whether this ``<input>`` holds a credential, so its value must never be read.

    Three signals, strongest first, ORed — a field is a credential if ANY of them says so, because
    the failure directions are not symmetric. A false positive costs one field the agent cannot
    fill (and the handoff exists precisely to fill it); a false negative puts a password in a
    prompt.

    ``placeholder`` is screened alongside ``name``/``id`` because a placeholder is the label a
    site shows on a field it did not bother to name, and ``extraction`` already falls back to it
    for the accessible name.
    """
    itype = (input_type or "").strip().lower()
    if itype in CREDENTIAL_INPUT_TYPES:
        return True
    tokens = {t.strip().lower() for t in (autocomplete or "").replace(",", " ").split()}
    if tokens & CREDENTIAL_AUTOCOMPLETE:
        return True
    haystack = f"{name or ''} {placeholder or ''}".lower()
    # Normalise the separators authors use so `user-password` and `user password` read the same as
    # `user_password`; the token list is written in snake_case and would otherwise miss both.
    haystack = haystack.replace("-", "_").replace(".", "_")
    return any(token in haystack for token in CREDENTIAL_NAME_TOKENS)


_SECRET_REFERENCE = re.compile(r"\{\{\s*secret\s*:([^}]*)\}\}", re.IGNORECASE)

#: What a start page's references are blanked to before it is parsed: a host's letters, so a parse
#: reads it where the browser would read the value.
_BLANKED = "pclawblankedreference"

#: A scheme and the slashes after it, however many, as a browser reads ``https:/h`` and
#: ``https:///h``: the scheme, then an authority.
_SCHEME_SLASHES = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):/*")


def read_as_a_browser_does(address: str) -> str:
    """*address* as a browser reads it before it parses it: tabs and line breaks dropped and a
    backslash read as a slash."""
    return re.sub(r"[\t\r\n]", "", address).strip().replace("\\", "/")


def _as_a_browser_reads(page: str) -> tuple[str, str, str]:
    """*page*'s scheme, host and port as a browser reads them (:func:`read_as_a_browser_does`),
    a scheme followed by any slashes read as ``scheme://``."""
    text = _SCHEME_SLASHES.sub(lambda m: f"{m.group(1)}://", read_as_a_browser_does(page), count=1)
    try:
        parts = urlsplit(text)
    except ValueError:
        return "", "", ""
    authority = parts.netloc.rpartition("@")[2]
    host, _, port = authority.rpartition(":") if authority.count(":") == 1 else (authority, "", "")
    return parts.scheme.lower(), host.lower(), port


def start_page_refusal(written: object) -> tuple[str, str] | None:
    """Why a browse start page, as written (or with each reference kept as its name), is refused,
    as (what, fix); None when it isn't. A secret in its scheme, host or port stays in the origin
    browse shows of every address (:func:`origin_of`); a page that names one and has no scheme or
    host is read by the browser where the value lands; and what a pipe makes of a secret no mask
    follows. A start page that isn't text is refused, and so is one that names a secret and isn't
    an http or https address."""
    if written is None or written == "":
        return None
    if not isinstance(written, str):
        return (
            "a browse start page has to be an address written as text",
            "write the start page as one address",
        )
    found = list(_SECRET_REFERENCE.finditer(written))
    if any("|" in m.group(1) for m in found):
        return (
            "a browse start page can't pipe a secret, since no mask follows what the pipe makes of "
            "it",
            "take the {{secret:…}} out of the pipe",
        )
    if not found:
        return None
    scheme, host, port = _as_a_browser_reads(_SECRET_REFERENCE.sub(_BLANKED, written))
    if not scheme or not host or any(_BLANKED in part for part in (scheme, host, port)):
        return (
            "a browse start page can't name a secret in its scheme, host or port, since the "
            "address browse shows keeps them",
            "take the {{secret:…}} out of the host",
        )
    if scheme not in ("http", "https"):
        return (
            "a browse start page that names a secret has to be an http or https address",
            "take the {{secret:…}} out of it, or open an http or https address",
        )
    return None


def start_page_moved(written: object, filled: object) -> tuple[str, str] | None:
    """Why the start page *filled*, as its references filled it in, is refused for opening
    somewhere *written* (with each reference blanked) does not: a value that ends the login, its
    ``/`` or ``@``, moves the rest of the address into the host. None when both name one origin."""
    if not isinstance(written, str) or not isinstance(filled, str) or written == filled:
        return None
    if not _SECRET_REFERENCE.search(written):
        return None
    if _as_a_browser_reads(_SECRET_REFERENCE.sub(_BLANKED, written)) == _as_a_browser_reads(filled):
        return None
    return (
        "a browse start page can't hold a secret whose value changes the site it opens, since the "
        "address browse shows keeps its host",
        "take the {{secret:…}} out of the address",
    )


#: The schemes of an address that names a host browse shows, each with the port it opens when the
#: address names none.
_WEB_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443}
#: The schemes of an address with no host that browse shows by its scheme alone.
_HOSTLESS_SCHEMES = frozenset({"about", "blob", "data", "file", "javascript"})

#: What a site is known by (:func:`_host_key`): ``("ip", its bytes)`` or ``("name", its ACE form)``.
_HostKey = tuple[str, "bytes | str"]


@dataclass(frozen=True)
class _Site:
    """A host the work running here knew of before it touched the browser (:func:`known_sites`)."""

    #: As a browser writes it into an origin: its ACE name, or its address, an IPv6 one bracketed.
    shown: str
    #: Its letters as written and as shown, and its name decoded, which :func:`_coincides` reads.
    texts: frozenset[str]
    #: The ports an address the work was set to open wrote for it.
    ports: frozenset[int]


#: The sites the work running here knows (:func:`known_sites`), by :func:`_host_key`.
_KNOWN: ContextVar[Mapping[_HostKey, _Site]] = ContextVar("browse_known_sites", default={})


def _host_key(host: str) -> _HostKey | None:
    """*host* as a site is known by, in whatever form it is written: ``("ip", its bytes)`` for an
    address (a host of numbers read as a browser reads one), else ``("name", its ACE form)`` (IDNA
    with UTS46's mapping, nontransitional, without the STD3 rules). None when it reads as neither,
    so no site is known by it."""
    name = (host or "").strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if not name:
        return None
    try:
        return ("ip", ipaddress.ip_address(name).packed)
    except ValueError:
        pass
    dotted = filled_secrets.as_an_ipv4(name)
    if dotted is not None:
        return ("ip", ipaddress.IPv4Address(dotted).packed)
    try:
        ace = idna.encode(name, uts46=True, std3_rules=False, transitional=False)
    except Exception:  # IDNAError, UnicodeError, OverflowError, ValueError: no name it opens
        return None
    return ("name", ace.decode("ascii"))


def _site_of(address: str) -> tuple[str, str, int | None]:
    """*address*'s scheme, host and port as a browser reads them (:func:`read_as_a_browser_does`,
    a scheme followed by any slashes read as ``scheme://``); empty when it reads none."""
    text = _SCHEME_SLASHES.sub(
        lambda m: f"{m.group(1)}://", read_as_a_browser_does(address or ""), count=1
    )
    try:
        parts = urlsplit(text)
        return parts.scheme.lower(), parts.hostname or "", parts.port
    except ValueError:
        return "", "", None


def _with_site(sites: dict[_HostKey, _Site], host: str, port: int | None) -> None:
    """*sites* with *host* known, and *port* among its ports when one is written."""
    key = _host_key(host)
    if key is None:
        return
    if isinstance(key[1], bytes):
        address = ipaddress.ip_address(key[1])
        shown = (
            f"[{filled_secrets.as_an_ipv6(str(address))}]" if address.version == 6 else str(address)
        )
        texts = {shown.strip("[]")}
    else:
        shown = key[1]
        texts = {shown}
        try:
            texts.add(idna.decode(shown))
        except Exception:  # an ACE label idna can't decode: its letters as written still count
            pass
    texts.add(host.strip().lower().strip("[]"))
    before = sites.get(key)
    sites[key] = _Site(
        shown=shown,
        texts=frozenset(texts | (before.texts if before else frozenset())),
        ports=frozenset({*(before.ports if before else ()), *(() if port is None else (port,))}),
    )


@contextmanager
def known_sites(start_url: str, allow_hosts: Iterable[str] = ()) -> Iterator[None]:
    """Know, for the work running here, the sites it was set to reach before it touches the
    browser: *start_url*'s host, and the port it writes, and each of *allow_hosts* (an egress
    allow-list's entries) as the one host it names, not its subdomains. While the work holds a
    stored secret's value, browse names only a host it knows (:func:`origin_of`,
    :func:`shown_host`), since a host a page chose can carry that value in a form no mask follows.
    Known beside what an enclosing run knows, never in its place. Entered before the browser's
    transport starts the tasks it reads and judges events in, so they know it too."""
    sites = dict(_KNOWN.get())
    scheme, host, port = _site_of(start_url)
    if scheme in _WEB_SCHEMES and host:
        _with_site(sites, host, port)
    for listed in allow_hosts:
        if isinstance(listed, str) and listed.strip(" .") and "*" not in listed:
            _with_site(sites, listed, None)
    token = _KNOWN.set(sites)
    try:
        yield
    finally:
        _KNOWN.reset(token)


def _known(host: str) -> _Site | None:
    """The site the work running here knows *host* as (:func:`known_sites`), or None."""
    key = _host_key(host)
    return None if key is None else _KNOWN.get().get(key)


#: How many characters in a row of a known host a held value shares with it to coincide with it.
_WINDOW = 4
#: Where a held value splits into the pieces an address can carry apart.
_SIGNS = re.compile(r"[/\\?#@:.\s]")


@functools.lru_cache(maxsize=256)
def _renderings(held: tuple[str, ...]) -> frozenset[str]:
    """Each of *held* in every form :func:`_coincides` compares, lower-case: each form a browser
    can write it into a host in (``filled_secrets.as_a_host_reads``), as written and with its
    tabs and line breaks dropped, case-folded too; and each piece of those between the signs an
    address splits at, as written, as IDNA writes it and as the address a number reads as."""
    forms: set[str] = set()
    for value in held:
        for each in {value, re.sub(r"[\t\r\n]", "", value)}:
            for form in filled_secrets.as_a_host_reads(each):
                forms |= {form.lower(), unicodedata.normalize("NFKC", form).casefold()}
    out = set(forms)
    for form in forms:
        for piece in _SIGNS.split(form):
            if not piece:
                continue
            out |= {each.lower() for each in filled_secrets.as_a_host_reads(piece)}
            try:
                out.add(
                    idna.encode(piece, uts46=True, std3_rules=False, transitional=False).decode()
                )
            except Exception:  # a piece no host can hold as one label
                pass
    return frozenset(out - {""})


def _windows(text: str) -> list[str]:
    """Every run of :data:`_WINDOW` characters in *text*, or *text* when it is shorter."""
    return [text[i : i + _WINDOW] for i in range(max(1, len(text) - _WINDOW + 1))]


@functools.lru_cache(maxsize=256)
def _coincides(site: _Site, held: tuple[str, ...]) -> tuple[bool, bool]:
    """Whether a value in *held* coincides with *site*'s host, and with one of its ports: read
    only to decide what is withheld, never shown, so each doubt withholds. A value coincides with
    the host when one of its forms (:func:`_renderings`) holds :data:`_WINDOW` characters in a row
    of one of the host's (:attr:`_Site.texts`), or one of its pieces is one of the host's labels;
    and with a port when a run of its digits, or that run without its leading zeros, is in it."""
    renderings = _renderings(held)
    windows = {window for text in site.texts for window in _windows(text)}
    labels = {label for text in site.texts for label in re.split(r"[.:]", text) if label}
    host = bool((labels | site.texts) & renderings) or any(
        window in each for window in windows for each in renderings
    )
    runs = {run for each in renderings for run in re.findall(r"[0-9]+", each)}
    digits = {each for run in runs for each in (run, run.lstrip("0") or "0")}
    port = any(run in str(written) for written in site.ports for run in digits)
    return host, port


#: The values the sites are judged by while a page's own host is held to mask its words
#: (:func:`its_own_host_held`): the ones held before it, so that host withholds no site.
_JUDGED_BY: ContextVar[tuple[str, ...] | None] = ContextVar("browse_sites_judged_by", default=None)


def _held_values() -> tuple[str, ...]:
    """The held values a site, a scheme and a port are judged by (:func:`_coincides`): the work's
    own, without a page's host held to mask its words (:data:`_JUDGED_BY`)."""
    judged_by = _JUDGED_BY.get()
    return tuple(filled_secrets.held_here()) if judged_by is None else judged_by


def _shown_site(host: str) -> _Site | None:
    """The site *host* is shown as while the work running here holds a stored secret's value: one
    it knows (:func:`known_sites`) that no held value coincides with (:func:`_coincides`), or
    None."""
    site = _known(host)
    if site is None or _coincides(site, _held_values())[0]:
        return None
    return site


def _scheme_shown(scheme: str) -> bool:
    """Whether *scheme* is shown while the work running here holds a stored secret's value: one of
    the schemes browse names (:data:`_WEB_SCHEMES`, :data:`_HOSTLESS_SCHEMES`), and no piece of a
    held value (:func:`_renderings`)."""
    known = scheme in _WEB_SCHEMES or scheme in _HOSTLESS_SCHEMES
    return known and scheme not in _renderings(_held_values())


#: A run of text with no white space in it: a word, or an address written in a sentence.
_A_RUN = re.compile(r"\S+")
#: What can sit around a host or an address written in a sentence: a quote, a bracket, and the
#: signs and punctuation after it.
_EDGES = "'\"()[]{}<>`,.;:!?&*=~"


def masked_in_addresses(text: str, mask: str | None = None, also: Iterable[str] = ()) -> str:
    """*text*, an address or what is said of one, masked of each value the work running here was
    handed (``filled_secrets.handed``), and of *also*, in any case and in each form an address or a
    page's words carry it in (``filled_secrets.in_address_forms``), each as *mask* when it is given
    (a word a later reading keeps whole, then masks). Each run of it with no white space that holds
    an ``xn--`` label, in any case, is :data:`WITHHELD` whole first, unless what it names, without
    the quotes, brackets and punctuation around it, names a host browse shows (:func:`_shown_site`)
    and no other part of it holds one: an IDNA host is never decoded to be masked, since a value in
    it can be written in more forms than any mask follows. *text* as it is where no work holds
    any."""
    if not text or not filled_secrets.held_here():
        return text

    def _run(found: re.Match[str]) -> str:
        run = found.group(0)
        if "xn--" not in run.lower():
            return run
        named = run.strip(_EDGES)
        _, host, _ = _site_of(named if "//" in named else f"//{named}")
        rest = named.lower().replace(host.lower(), "", 1) if host else "xn--"
        return run if _shown_site(host) is not None and "xn--" not in rest else WITHHELD

    word = filled_secrets.MASK if mask is None else mask
    forms = filled_secrets.in_address_forms(also)
    return redact_known_values(_A_RUN.sub(_run, text), forms, word, ignore_case=True)


@contextmanager
def its_own_host_held(address: str) -> Iterator[None]:
    """While the work running here holds a stored secret's value and *address*, a page's own as the
    browser reports it, names a host browse doesn't show (:func:`shown_host`), that host held too
    (``filled_secrets.handed``), and each label of a name of four or more characters, so what is
    read of the page is masked of them: a page can write its own host into its words, and a value
    with it. The sites browse shows are still judged by the values held before
    (:data:`_JUDGED_BY`), so the page's host withholds none of them. Nothing more otherwise."""
    _, host, _ = _site_of(address) if filled_secrets.held_here() else ("", "", None)
    if not host or _shown_site(host) is not None:
        yield
        return
    key = _host_key(host)
    labels = [] if key is None or isinstance(key[1], bytes) else host.split(".")
    token = _JUDGED_BY.set(_held_values())
    try:
        with filled_secrets.handed([host, *(label for label in labels if len(label) >= 4)]):
            yield
    finally:
        _JUDGED_BY.reset(token)


def origin_of(url: str) -> str:
    """*url* as its origin alone, ``scheme://host[:port]/``: no login, path, query or fragment.
    What browse shows of every address while its work holds a stored secret's value
    (``filled_secrets.held_here``), since a site can carry that value on in a form no mask follows
    (a redirect's encoded return address).

    While a value is held, an origin names only a site the work was set to reach
    (:func:`known_sites`) that no held value coincides with, over http, https, ws or wss, on the
    scheme's own port or one the work's own address wrote for it; and an address with no host shows
    its scheme alone when it is ``about:``, ``blob:``, ``data:``, ``file:`` or ``javascript:``.
    Every other address, and one whose scheme is a piece of a held value, is :data:`WITHHELD`
    whole: no host, port or scheme a page chose is shown. Otherwise its host is shown as a browser
    opens it, NFKC-folded and lower-cased, an address with no host as its scheme alone, and
    anything else :data:`WITHHELD`."""
    try:
        parts = urlsplit(read_as_a_browser_does(url))
        port = parts.port
    except ValueError:
        return WITHHELD
    if filled_secrets.held_here():
        return _known_origin(parts.scheme.lower(), parts.netloc, parts.hostname or "", port)
    name = unicodedata.normalize("NFKC", parts.hostname).lower() if parts.hostname else ""
    if name and not _A_HOST.fullmatch(name):
        return WITHHELD
    if parts.scheme and name:
        host = f"[{name}]" if ":" in name else name
        return f"{parts.scheme}://{host}{f':{port}' if port else ''}/"
    if parts.scheme and not parts.netloc:
        return f"{parts.scheme}:"
    return WITHHELD


def _known_origin(scheme: str, netloc: str, host: str, port: int | None) -> str:
    """An address's origin while a value is held (:func:`origin_of`): known, or :data:`WITHHELD`."""
    if scheme and not netloc:
        return f"{scheme}:" if scheme in _HOSTLESS_SCHEMES and _scheme_shown(scheme) else WITHHELD
    if scheme not in _WEB_SCHEMES or not _scheme_shown(scheme):
        return WITHHELD
    site = _shown_site(host)
    if site is None:
        return WITHHELD
    if port is None or port == _WEB_SCHEMES[scheme]:
        return f"{scheme}://{site.shown}/"
    if port in site.ports and not _coincides(site, _held_values())[1]:
        return f"{scheme}://{site.shown}:{port}/"
    return WITHHELD


#: A host as an origin shows one: a name, or a bracketed address literal.
_A_HOST = re.compile(r"[\w.-]+|\[[0-9A-Fa-f:.]+\]|[0-9A-Fa-f:.]+")

#: An address written in a sentence, up to a space: a scheme of up to 32 characters after no word
#: (so a ``word:value`` that starts a word reads as one); one glued to a word when a slash or a
#: backslash follows its colon, or when it is a scheme a browser opens or runs; or a path, up to a
#: quote or a bracket, from a slash after no word or slash, from a backslash after no word, slash or
#: backslash (a browser reads ``\\host\path`` as ``//host/path``), or from two of them after a word
#: (a scheme-relative address glued to it). A glued ``name:value`` is left as it is unless its name
#: ends in one of those schemes or a path starts in it, and so is a glued scheme that isn't one with
#: no slash or backslash after its colon. Each ends before the punctuation that ends a sentence. The
#: bound keeps it linear.
_ADDRESS_IN_TEXT = re.compile(
    r"(?:(?<![\w/])[A-Za-z][A-Za-z0-9+.-]{0,31}:\S*"
    r"|[A-Za-z][A-Za-z0-9+.-]{0,31}:[/\\]\S*"
    r"|(?i:https?|wss?|ftp|file|mailto|javascript|vbscript|data|blob|tel|sms|about):\S*"
    r"|(?<![\w/])/[^\s\"'<>()\[\]]*"
    r"|(?<![\w/\\])\\[^\s\"'<>()\[\]]*"
    r"|(?<=\w)[/\\]{2}[^\s\"'<>()\[\]]*)[^\s\"'<>()\[\].,;:!?]"
)


def screen_text(text: str) -> str:
    """*text*, a sentence or a note browse records, as browse shows it: while its work holds a
    stored secret's value, each address in it as its origin (:func:`origin_of`) and the rest masked
    of that value (``filled_secrets.masked_here``); and always with each credential parameter
    :data:`WITHHELD` (:func:`screen_url`'s screen)."""
    raw = held_as_origins(text or "")
    return _screened_parameters(raw)


#: A held value while its text is read for addresses (:func:`held_as_origins`): one word, so an
#: address it sits in stays one address and none starts inside it.
_HELD_WORD = "pclawheldvalue"


def held_as_origins(text: str, also: Iterable[str] = ()) -> str:
    """*text*, while its work holds a stored secret's value, with each address in it as its origin
    (:func:`origin_of`, the address grammar :func:`addresses_as_origins` reads) and that value
    masked, the value kept whole through the reading: each form of it, in any case and *also* among
    them (the forms a converter wrote it in), and the mask a value is already shown as, is one word
    while the addresses are found and while each is read as a browser reads it (:func:`_held_kept`),
    so no address is cut out of it, and an address already shown is shown as it was. *text* as it
    is otherwise."""
    if not text or not filled_secrets.held_here():
        return text

    def _origin(found: re.Match[str]) -> str:
        return origin_of(_held_kept(found.group(0), also))

    kept = masked_in_addresses(text.replace(filled_secrets.MASK, _HELD_WORD), _HELD_WORD, also)
    shown = _ADDRESS_IN_TEXT.sub(_origin, kept)
    return filled_secrets.masked_here(shown.replace(_HELD_WORD, filled_secrets.MASK), also=also)


def _held_kept(address: str, also: Iterable[str] = ()) -> str:
    """*address* as a browser reads it (:func:`read_as_a_browser_does`), each held value in it, in
    any case, as :data:`_HELD_WORD` (:func:`masked_in_addresses`): one word where the
    address is read, so no part of the value is read as its host."""
    return masked_in_addresses(read_as_a_browser_does(address), _HELD_WORD, also)


def shown_host(url: str, host: str) -> str:
    """*host*, the host a guard read from *url*, as browse shows it: while its work holds a stored
    secret's value, as a browser writes it when it is a site the work knows that no held value
    coincides with (:func:`_shown_site`), and :data:`WITHHELD` otherwise. *url* isn't read: what a
    page wrote around a host is never shown. *host* as it is otherwise."""
    if not host or not filled_secrets.held_here():
        return host
    site = _shown_site(host)
    return WITHHELD if site is None else site.shown


def addresses_as_origins(text: str) -> str:
    """*text* with each address in it as its origin (:func:`origin_of`)."""
    return _ADDRESS_IN_TEXT.sub(lambda m: origin_of(m.group(0)), text)


def screen_url(url: str) -> str:
    """Replace credential-bearing query/fragment VALUES with :data:`WITHHELD`, keeping the keys.

    While the work holds a stored secret's value, every URL is its origin alone (:func:`origin_of`).

    Applied where a URL is READ from the browser rather than where it is displayed, so one call
    covers every downstream consumer at once — the outline header, the fence's ``source_id``, the
    Links DSL, the run payload, the park sentence and the SEL row. That is the "screen at the
    boundary, once, before composition" rule: the alternative is six display sites each remembering
    to screen, which is five chances to forget.

    Both the query AND the fragment are screened. The fragment matters more, not less: the OAuth
    *implicit* flow returns ``#access_token=…``, and a fragment never reaches a server, so it is the
    one place a token is guaranteed to be sitting in the URL bar of the page the agent just read.

    Non-URL input, an unparseable URL, or a URL with no credential parameter is returned unchanged
    — this is a screen, not a validator, and refusing to hand back a string the agent needs would
    turn a privacy control into an outage.
    """
    raw = url or ""
    if raw and filled_secrets.held_here():
        return origin_of(raw)
    return _screened_parameters(raw)


def _screened_parameters(raw: str) -> str:
    """*raw* with each credential parameter's value :data:`WITHHELD` (:func:`screen_url`)."""
    if not raw or ("?" not in raw and "#" not in raw):
        return raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        # Unparseable: return it untouched rather than guessing. A malformed URL cannot have been
        # produced by an OAuth redirect, and mangling it would lose the evidence of what went wrong.
        return raw
    query = _screen_query(parts.query)
    fragment = _screen_query(parts.fragment)
    if query == parts.query and fragment == parts.fragment:
        return raw
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def _screen_query(blob: str) -> str:
    """Screen one ``a=1&b=2`` blob. Returns it unchanged when it holds no credential parameter.

    ``keep_blank_values=True`` so ``?code=`` survives as ``?code=`` rather than vanishing: a
    parameter disappearing changes what the URL means, and this function's whole contract is that
    only VALUES move.
    """
    if not blob or "=" not in blob:
        # A bare fragment (`#section`) or an empty query is not a key/value blob. Returned as-is:
        # round-tripping it through parse_qsl would silently delete it.
        return blob
    try:
        pairs = parse_qsl(blob, keep_blank_values=True)
    except ValueError:
        return blob
    if not pairs:
        return blob
    hit = False
    screened: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.strip().lower().replace("-", "_") in CREDENTIAL_URL_PARAMS and value != WITHHELD:
            screened.append((key, WITHHELD))
            hit = True
        else:
            screened.append((key, value))
    if not hit:
        return blob
    # `safe="[]"` keeps WITHHELD readable rather than percent-encoding it into `%5Bwithheld%5D`,
    # which a user reading the park sentence would have to decode by eye.
    return urlencode(screened, safe="[]")


def screen_action_render(rendered: str, *, credential: bool) -> str:
    """Screen one rendered sentinel line before it is recorded or re-composed.

    ``credential`` is the caller's answer to "does this action address a credential field", which
    only the caller can know (it holds the ref→element index). Passing the verdict in rather than
    re-deriving it here keeps ONE place deciding what a credential field is.

    Only the ``TYPE`` argument moves, and the ref does not: the ref is what makes the refusal
    legible ("browse refused to type into field 4f2a1c"), and a line screened to
    ``TYPE [withheld]`` would name neither the field nor the action.
    """
    if not credential:
        return rendered
    if not rendered.startswith("TYPE "):
        return rendered
    head, sep, _ = rendered.partition("(")
    if not sep:
        return rendered
    return f"{head}({WITHHELD})"
