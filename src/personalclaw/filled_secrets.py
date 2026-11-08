"""What work a ``{{secret:NAME}}`` was filled into keeps of what it did: masked of the value.

A dispatch that fills a stored secret into what it runs hands the value to work that can print it,
return it or fail with it: an ``echo``, a ``curl -v`` trace, an error that quotes its arguments, a
script's answer. What comes back is kept and shown: an automation's run history and last error, the
note that reports the run and the answer Run now gives (``triggers.secrets.resolve_for``), and a
workflow step's output, failure and ledger (``workflows.bindings.BindingContext.filled``). A value
has no shape a mask could find, so the dispatch that filled it masks it out by value before any of
those keep or show what came back, by the rule the agent's ``bash`` tool applies to the command it
runs (``security.redact_known_values``): every value it filled in, wherever it appears, however
short, as written, as a JSON string writes it, percent-encoded or HTML-escaped; not a value the
work changed otherwise (cut short, split, or encoded another way).

What the work writes itself while it runs is masked too: the dispatch holds the values it filled in
for as long as the work runs (:func:`handed`), and the writers of such a record mask them out of
what they are given (:func:`masked_here`). An address, and what is said of one, is masked of each
value in the letters an address carries it in too (:func:`in_address_forms`, which
``browse.credentials.masked_in_addresses`` masks).
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import ipaddress
import re
import unicodedata
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import unquote

import idna

from personalclaw.security import _KNOWN_VALUE_MASK, redact_known_values

#: What stands in for each value it masks.
MASK = _KNOWN_VALUE_MASK

#: The values each dispatch the work running in this context runs inside filled into it, one
#: collection per dispatch (:func:`handed`).
_HANDED: ContextVar[tuple[Collection[str], ...]] = ContextVar("filled_secrets_handed", default=())

#: What an action's answer says (``ActionResult``): what it printed, its error output, its reason
#: and its line for a person. Its handles (``reversal``, ``work_id``) name what it did, and its
#: outcome and exit code are no text, so they are kept as they are.
_ANSWER_TEXT = ("stdout", "stderr", "error", "summary")

#: What a failure envelope (``errors.AgentError``) says.
_ENVELOPE_TEXT = ("what", "why", "fix", "suggestions")


def masked(value: Any, filled: Collection[str]) -> Any:
    """*value* with every one of *filled* masked in each string it holds: the text itself, a
    mapping's values, a list's and a tuple's items. A mapping's keys, a number and anything else
    are kept: what a step returns is keyed by its author or by the engine, and the text a command
    printed is masked as text before anything reads it as JSON (:func:`masked_answer`)."""
    if not filled:
        return value
    if isinstance(value, str):
        return redact_known_values(value, filled)
    if isinstance(value, dict):
        return {key: masked(item, filled) for key, item in value.items()}
    if isinstance(value, list):
        return [masked(item, filled) for item in value]
    if isinstance(value, tuple):
        return tuple(masked(item, filled) for item in value)
    return value


def masked_fields(obj: Any, names: Iterable[str], filled: Collection[str]) -> Any:
    """*obj* with each of its attributes in *names* masked (:func:`masked`): a copy when that
    changes any, *obj* itself when it changes none."""
    changes: dict[str, Any] = {}
    for name in names:
        said = getattr(obj, name, None)
        if said is not None and (shown := masked(said, filled)) != said:
            changes[name] = shown
    return _copied(obj, changes)


def masked_answer(result: Any, filled: Collection[str]) -> Any:
    """*result*, what an action answered (``ActionResult``), with every one of *filled* masked in
    what it says (:data:`_ANSWER_TEXT`) and in its failure envelope. *result* itself when nothing
    was filled in or there is no answer (``None``)."""
    if not filled or result is None:
        return result
    envelope = getattr(result, "agent_error", None)
    if envelope is not None:
        told = masked_fields(envelope, _ENVELOPE_TEXT, filled)
        result = _copied(result, {"agent_error": told} if told is not envelope else {})
    return masked_fields(result, _ANSWER_TEXT, filled)


@contextmanager
def handed(filled: Collection[str]) -> Iterator[None]:
    """Hold *filled*, the values a dispatch filled into the work it runs, while that work runs.

    The work writes records of its own as it runs: a command it refuses is audited with the
    command, a check that cannot run is logged with the command and what it printed, a request is
    audited with its URL. Each of those writers masks by shape what it is given (the floors
    ``security.mask_child_output`` and ``security.MaskingFormatter``, and ``net.client.audit``),
    and by :func:`masked_here` these values, which no shape finds. Held as given, never copied: a
    step's bindings fill its values in as its dispatcher resolves them, after the hold begins. And
    held beside what an enclosing dispatch holds, never in its place: work dispatched inside other
    work can be handed what that work was."""
    token = _HANDED.set((*_HANDED.get(), filled))
    try:
        yield
    finally:
        _HANDED.reset(token)


def held_here() -> list[str]:
    """Every value the work running here was handed (:func:`handed`); none where no work holds
    any."""
    return [value for filled in _HANDED.get() for value in filled]


def masked_here(text: str, mask: str | None = None, also: Iterable[str] = ()) -> str:
    """*text* masked of every value the work running here was handed (:func:`handed`), and of
    *also*, the forms a converter wrote them in, each as *mask* when it is given (a word a later
    reading keeps whole, then masks); *text* as it is where no work holds any."""
    held = held_here()
    if not held:
        return text
    return redact_known_values(text, [*held, *also], MASK if mask is None else mask)


def _as_a_host_maps(text: str) -> str:
    """*text* as a browser maps a host's letters before it writes them: UTS46's mapping as the
    WHATWG URL standard applies it (nontransitional, without the STD3 rules;
    ``idna.uts46_remap``), each code point it refuses kept as it is."""
    try:
        return idna.uts46_remap(text, std3_rules=False, transitional=False)
    except (idna.IDNAError, UnicodeError, ValueError):
        return unicodedata.normalize("NFC", "".join(_as_a_host_maps_one(ch) for ch in text))


def _as_a_host_maps_one(ch: str) -> str:
    try:
        return idna.uts46_remap(ch, std3_rules=False, transitional=False)
    except (idna.IDNAError, UnicodeError, ValueError):
        return ch


def as_an_ipv4(text: str) -> str | None:
    """*text* as the dotted IPv4 address a browser reads a host of numbers as (the WHATWG URL
    standard: one to four parts, each decimal, octal on a leading 0 or hex on 0x, the last filling
    the bytes left), or None when it reads no address."""
    parts = text.split(".")
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        if part[:2].lower() == "0x":
            base, digits = 16, part[2:] or "0"
        elif len(part) > 1 and part.startswith("0"):
            base, digits = 8, part[1:]
        else:
            base, digits = 10, part
        if not digits or not digits.isascii() or not digits.isalnum():
            return None
        try:
            numbers.append(int(digits, base))
        except ValueError:
            return None
    if any(n > 255 for n in numbers[:-1]) or numbers[-1] >= 256 ** (5 - len(numbers)):
        return None
    address = numbers[-1] + sum(n << (8 * (3 - i)) for i, n in enumerate(numbers[:-1]))
    return str(ipaddress.IPv4Address(address))


def as_an_ipv6(text: str) -> str | None:
    """*text*, bracketed or not, as the IPv6 address a browser writes (the WHATWG URL standard):
    each piece in lower-case hex without its leading zeros, an IPv4 tail as two pieces, and the
    first longest run of two or more zero pieces as ``::``; None when it reads no address."""
    try:
        packed = ipaddress.IPv6Address(text.removeprefix("[").removesuffix("]")).packed
    except ValueError:
        return None
    pieces = [int.from_bytes(packed[i : i + 2], "big") for i in range(0, 16, 2)]
    start, run = -1, 1
    i = 0
    while i < 8:
        j = i
        while j < 8 and pieces[j] == 0:
            j += 1
        if j - i > run:
            start, run = i, j - i
        i = max(j, i + 1)
    if start < 0:
        return ":".join(f"{p:x}" for p in pieces)
    head = ":".join(f"{p:x}" for p in pieces[:start])
    tail = ":".join(f"{p:x}" for p in pieces[start + run :])
    return f"{head}::{tail}"


def _as_numbers_read(text: str) -> set[str]:
    """*text* as the address a browser reads a host of numbers as, whole and the parts of it a
    shorter number fills (:func:`as_an_ipv4`), and as an IPv6 address, bracketed and not
    (:func:`as_an_ipv6`); none when it reads no address."""
    forms: set[str] = set()
    dotted = as_an_ipv4(text)
    if dotted and dotted != text:
        octets = dotted.split(".")
        number = int(ipaddress.IPv4Address(dotted))
        forms.add(dotted)
        for kept in (3, 2):
            if number < 256**kept:
                forms.add(".".join(octets[4 - kept :]))
    v6 = as_an_ipv6(text) if ":" in text else None
    if v6:
        forms |= {v6, f"[{v6}]"}
    return forms


@functools.lru_cache(maxsize=256)
def as_a_host_reads(value: str) -> frozenset[str]:
    """*value* in each form a browser can write into a host: as written, NFKC-folded, as it maps a
    host's letters (:func:`_as_a_host_maps`), percent-decoded first, and a number or an IPv6
    address as the address it reads one as (:func:`_as_numbers_read`)."""
    forms = {value, unicodedata.normalize("NFKC", value)}
    for each in (value, unquote(value)):
        forms |= {each, _as_a_host_maps(each)}
    for each in list(forms):
        forms |= _as_numbers_read(each)
    return frozenset(form for form in forms if form)


def in_address_forms(also: Iterable[str]) -> set[str]:
    """Each value the work running here holds, and *also*, in the letters an address or a page's
    words can carry it in: as written; each way a browser writes it into a host
    (:func:`as_a_host_reads`), the value read as written and with its tabs and line breaks dropped;
    as a browser reads an address, tabs and line breaks dropped and a backslash a slash, and as it
    writes one back, a slash before a query or a fragment that follows the host; and with each run
    of spaces or tabs, or of any white space, as one space, as a page's words fold them."""
    forms = set(also)
    for value in held_here():
        read_forms = as_a_host_reads(value) | as_a_host_reads(re.sub(r"[\t\r\n]", "", value))
        for each in read_forms:
            read = re.sub(r"[\t\r\n]", "", each).strip().replace("\\", "/")
            forms |= {
                each,
                read,
                re.sub(r"^([^/?#]*)([?#])", r"\1/\2", read),
                re.sub(r"[ \t]+", " ", each).strip(),
                re.sub(r"\s+", " ", each).strip(),
            }
    return {form for form in forms if form}


def _copied(obj: Any, changes: dict[str, Any]) -> Any:
    """*obj* with *changes*, as a copy (a dataclass's own copy, any other object's shallow one);
    *obj* itself when there are none."""
    if not changes:
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.replace(obj, **changes)
    copied = copy.copy(obj)
    for name, value in changes.items():
        setattr(copied, name, value)
    return copied
