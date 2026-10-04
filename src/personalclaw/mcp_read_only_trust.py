"""The owner's trust in an MCP server's read-only labels, sealed to each tool's definition.

A server labels its own tools: ``readOnlyHint: true`` says a tool only reads, and a server can say
that of anything. Believed, the tool runs without a card and in Ask and Plan mode, so a label counts
only from a server whose labels the owner trusts (`tool_providers.base.risk_from_annotations`), and
that trust covers exactly the tools the owner saw when they gave it. Kept with it is a digest of
each of those tools' definitions. A tool the server adds later, or one whose definition changed, is
one the owner never saw: its label is not believed, so it asks like any untrusted server's tool,
until the owner reviews it on the Tools page, which seals the server's tools again as the page
showed them.

**The digest** of a tool is the SHA-256, written as lowercase hex, of its definition serialized
canonically: the JSON object with exactly the keys ``name``, ``description``, ``inputSchema`` and
``annotations``, every object's keys in sorted order at every depth, no whitespace between tokens,
and text other than ASCII written as itself in UTF-8 (``json.dumps(value, sort_keys=True,
separators=(",", ":"), ensure_ascii=False).encode("utf-8")``). The values are as the server listed
them, with nothing normalized: ``name`` and ``description`` are strings (no description is ``""``),
and ``inputSchema`` and ``annotations`` are objects (a tool listed with no input schema has the
client's own ``{"type": "object", "properties": {}}``, and one with no labels has ``{}``). Arrays
keep their order. Nothing else in a listing is in it. The description, the input schema and the
labels are each digested alone as well (their JSON value, serialized the same way), so the page can
say what changed in a tool, not only that it did.

**Where it is kept.** ``<home>/grants/mcp_read_only.json``: ``{"version": 1, "servers": {server:
{"at": <when it was trusted or last reviewed, UTC>, "tools": {tool: {"digest", "description",
"inputSchema", "annotations"}}}}}``. ``grants/`` is owner-only (`owner_only`): the agent's sandbox
cannot write it and the tool-call screen refuses a call that names it. Only the owner's surface
writes it, after its question: the Tools page's Trust, Review and Stop trusting
(`dashboard.handlers.mcp_trust`). A record that cannot be read trusts no server.

**The one read.** The approval gate takes a tool's label through :func:`believes`, which compares
the tool's digest with the sealed one, by way of `mcp_client.declared_risk`. Nothing reads the
record except this module, and nothing but `declared_risk` turns a label into a read
(`tests/test_mcp_read_only_trust_has_one_read.py`). :func:`holds_trust` says whether the owner
trusts a server at all, for the words that say why a tool is not believed and for the page's
switch; it is never a tool's answer.

**Trust given before the record existed** (a server named in ``security.mcp_read_only_servers``)
is not carried over. Nothing recorded which tools the owner saw then, so no tool could be shown to
be one of them, and taking the next listing as what they trusted would extend the trust to tools
they were never shown. The key is retired (`config.validation._normalize_retired`): the server's
tools ask, its switch reads off, and one Trust on the Tools page seals what the page shows.

**What a listing tells the owner** (:func:`observe`, at every start of a server). A listing that
differs from the last one this process saw of the server makes every session judge its tools again
at its next turn (:func:`stamp`, read by `tool_providers.registry.surface_stamp`). And a tool's
description is text the model reads, so one that changed on a server whose labels the owner does not
trust raises a quiet notice (`dashboard.lifecycle_hooks`), once per change: the descriptions as last
seen are kept beside the trust, in ``grants/mcp_tool_descriptions.json``, where an agent cannot
rewrite them to hide one.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The record of the owner's trust, by name under ``grants/``.
TRUST_RECORD = "mcp_read_only"
#: The descriptions of each server's tools as last seen, by name under ``grants/``.
SEEN_RECORD = "mcp_tool_descriptions"

#: The parts of a definition digested alone, so a review can say what changed.
PARTS: tuple[str, ...] = ("description", "inputSchema", "annotations")

#: The input schema the client gives a tool listed with none (`mcp_client._refresh_tools`).
_NO_INPUTS: dict[str, Any] = {"type": "object", "properties": {}}

_HEX = frozenset("0123456789abcdef")


# ── a tool's definition, and its digest ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Definition:
    """One tool as its server listed it: the four things the digest is taken of."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    annotations: Mapping[str, Any]

    def as_listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "annotations": dict(self.annotations),
        }


def definition(tool: Any) -> Definition:
    """*tool* as a :class:`Definition`: a listed spec (``name``, ``description``, ``input_schema``,
    ``annotations``), or a probe's row (``inputSchema`` for the schema)."""
    if isinstance(tool, Mapping):
        name, description = tool.get("name"), tool.get("description")
        schema, labels = tool.get("inputSchema"), tool.get("annotations")
    else:
        name, description = getattr(tool, "name", ""), getattr(tool, "description", "")
        schema, labels = getattr(tool, "input_schema", None), getattr(tool, "annotations", None)
    return Definition(
        name=name if isinstance(name, str) else "",
        description=description if isinstance(description, str) else "",
        input_schema=schema if isinstance(schema, Mapping) else _NO_INPUTS,
        annotations=labels if isinstance(labels, Mapping) else {},
    )


def _canonical(value: Any) -> bytes:
    """*value* serialized the one way a digest is taken of it (see the module docstring)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(tool: Any) -> str:
    """The digest of *tool*'s definition: what the owner's trust in it is sealed to."""
    return _sha256(_canonical(definition(tool).as_listed()))


def part_digests(tool: Any) -> dict[str, str]:
    """The digest of each of *tool*'s description, input schema and labels, alone."""
    listed = definition(tool).as_listed()
    return {part: _sha256(_canonical(listed[part])) for part in PARTS}


def is_digest(value: object) -> bool:
    """Whether *value* reads as a digest: 64 lowercase hex characters."""
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


# ── the record ──────────────────────────────────────────────────────────────────────────────────


def _path(record: str) -> Path:
    from personalclaw.owner_grants import grants_dir
    from personalclaw.record_ids import record_path

    return record_path(grants_dir(), record, kind="grant book")


#: The last read of each record: where it was, the file's identity when read, and what it held.
_reads: dict[str, tuple[str, tuple[int, int, int], dict[str, Any]]] = {}


def _identity(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _servers(record: str) -> dict[str, Any]:
    """The ``servers`` a record holds, read again only when the file changed. A record that is
    absent holds nothing; one that cannot be read is read as holding nothing too, and said: for the
    trust that fails closed, since a record nobody can read trusts nobody."""
    path = _path(record)
    try:
        seen = _identity(path)
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("MCP tool record %s is unreadable; read as holding nothing", path)
        return {}
    cached = _reads.get(record)
    if cached is not None and cached[0] == str(path) and cached[1] == seen:
        return cached[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("MCP tool record %s is unreadable; read as holding nothing", path)
        return {}
    servers = data.get("servers") if isinstance(data, dict) else None
    held = dict(servers) if isinstance(servers, dict) else {}
    _reads[record] = (str(path), seen, held)
    return held


@contextmanager
def _locked(record: str) -> Iterator[None]:
    from personalclaw.atomic_write import ensure_private_dir

    path = _path(record)
    ensure_private_dir(path.parent)
    with open(path.parent / f".{record}.lock", "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(record: str, servers: dict[str, Any]) -> None:
    from personalclaw.atomic_write import atomic_json_write

    atomic_json_write(_path(record), {"version": 1, "servers": servers})


def _sealed_tools(server: str) -> dict[str, dict[str, str]] | None:
    """The tools the owner's trust in *server* is sealed to, by name, or ``None`` when the owner
    does not trust its labels. An entry that is not one the Tools page writes seals nothing."""
    entry = _servers(TRUST_RECORD).get(server)
    tools = entry.get("tools") if isinstance(entry, dict) else None
    if not isinstance(tools, dict):
        return None
    return {
        str(name): seal
        for name, seal in tools.items()
        if isinstance(seal, dict) and is_digest(seal.get("digest"))
    }


# ── the reads ───────────────────────────────────────────────────────────────────────────────────


def believes(server: str, tool: Any) -> bool:
    """Whether *tool*'s read-only label is believed: the owner trusts *server*'s labels, and the
    trust is sealed to *tool* exactly as it is defined now. THE read the approval gate takes a label
    through (`mcp_client.declared_risk`). A tool added or changed since the owner trusted the
    server, or last reviewed it, is not believed, and asks like any untrusted server's tool."""
    sealed = _sealed_tools(server)
    if not sealed:
        return False
    found = sealed.get(definition(tool).name)
    return found is not None and found.get("digest") == digest(tool)


def holds_trust(server: str) -> bool:
    """Whether the owner trusts *server*'s read-only labels at all. For the words that say why a
    tool is not believed, and for the Tools page's switch: never a tool's answer, which is
    :func:`believes`."""
    return _sealed_tools(server) is not None


@dataclass(frozen=True)
class Change:
    """A tool whose definition changed since the trust, and which of its parts did."""

    name: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    """What a server's listing is, against the owner's trust in its labels."""

    trusted: bool
    #: When the owner trusted the labels, or last reviewed them ("" without trust).
    at: str = ""
    #: Listed now, and not when the trust was given.
    added: tuple[str, ...] = ()
    #: Listed both times, defined differently now.
    changed: tuple[Change, ...] = ()
    #: Gone from the listing since.
    removed: tuple[str, ...] = ()
    #: Every tool listed now, with its digest: what a Trust or a Review seals. ``None`` while no
    #: listing of the server as it is defined now is known, which says nothing of what changed.
    listed: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "trusted": self.trusted,
            "listed": None if self.listed is None else dict(self.listed),
        }
        if self.trusted:
            out["at"] = self.at
        if self.trusted and self.listed is not None:
            out.update(
                added=list(self.added),
                changed=[{"name": c.name, "parts": list(c.parts)} for c in self.changed],
                removed=list(self.removed),
            )
        return out


def review(server: str, tools: Iterable[Any] | None) -> Review:
    """*tools* (the server's listing now, or ``None`` while none is known) against the owner's
    trust in *server*'s labels: which were added, changed or removed since it was given, and the
    digest of each listed now."""
    sealed = _sealed_tools(server)
    entry = _servers(TRUST_RECORD).get(server)
    at = str(entry.get("at") or "") if sealed is not None and isinstance(entry, dict) else ""
    if tools is None:
        return Review(trusted=sealed is not None, at=at)
    now = {d.name: d for d in map(definition, tools) if d.name}
    listed = {name: digest(d) for name, d in sorted(now.items())}
    if sealed is None:
        return Review(trusted=False, listed=listed)
    changed = []
    for name, d in sorted(now.items()):
        seal = sealed.get(name)
        if seal is None or seal.get("digest") == listed[name]:
            continue
        parts = part_digests(d)
        changed.append(Change(name, tuple(p for p in PARTS if seal.get(p) != parts[p])))
    return Review(
        trusted=True,
        at=at,
        added=tuple(sorted(set(now) - set(sealed))),
        changed=tuple(changed),
        removed=tuple(sorted(set(sealed) - set(now))),
        listed=listed,
    )


# ── the owner's writes ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Sealed:
    """What a Trust or a Review recorded."""

    #: The tools the trust now covers.
    sealed: tuple[str, ...]
    #: Reviewed, and defined differently by the time the review landed: they still ask.
    changed_since: tuple[str, ...]


def seal(server: str, tools: Iterable[Any], seen: Mapping[str, str]) -> Sealed:
    """Record the owner's trust in *server*'s labels, sealed to the tools they reviewed. *seen* is
    each tool the page showed them, with the digest it showed; *tools* is the server's listing now.
    A tool is sealed when the listing still defines it as shown. One defined differently by now, or
    no longer listed, is left out: it asks until the owner reviews it again. Replaces the record
    the server had, so a tool reviewed before and not shown this time is no longer covered.

    Only an owner surface calls this, after its question."""
    now = {d.name: d for d in map(definition, tools) if d.name}
    kept: dict[str, dict[str, str]] = {}
    changed_since: list[str] = []
    for name, shown in sorted(seen.items()):
        d = now.get(name)
        if d is None:
            continue
        if digest(d) != shown:
            changed_since.append(name)
            continue
        kept[name] = {"digest": shown, **part_digests(d)}
    from personalclaw.instants import utc_now_iso

    with _locked(TRUST_RECORD):
        servers = dict(_servers(TRUST_RECORD))
        servers[server] = {"at": utc_now_iso(), "tools": kept}
        _write(TRUST_RECORD, servers)
    return Sealed(sealed=tuple(sorted(kept)), changed_since=tuple(changed_since))


def _drop(record: str, server: str) -> bool:
    """Take *server* out of *record*. True when it was there."""
    if server not in _servers(record):
        return False
    with _locked(record):
        servers = dict(_servers(record))
        if servers.pop(server, None) is None:
            return False
        _write(record, servers)
    return True


def revoke(server: str) -> bool:
    """Stop trusting *server*'s labels. True when the owner had trusted them."""
    return _drop(TRUST_RECORD, server)


def forget(server: str) -> None:
    """*server* is gone: the trust in its labels and what was seen of its tools go with it, so a
    server added again under its name starts from neither."""
    _drop(TRUST_RECORD, server)
    _drop(SEEN_RECORD, server)
    _last_listing.pop(server, None)


# ── what a listing tells the owner ──────────────────────────────────────────────────────────────

#: Each server's last listing in this process: every tool's name and digest.
_last_listing: dict[str, tuple[tuple[str, str], ...]] = {}
#: Counts the listings that differed from the one before them (:func:`stamp`).
_listings_changed = 0
#: ``(server, tools whose description changed) -> None``, told of a description that changed on a
#: server whose labels the owner does not trust (:func:`observe`).
_listeners: list[Callable[[str, tuple[str, ...]], None]] = []


def subscribe(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    """Be told, with the server and the tools, of each description that changed on a server whose
    labels the owner does not trust (idempotent). The dashboard subscribes at start and raises the
    quiet notice."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def stamp() -> tuple[object, ...]:
    """A value that changes whenever a tool's verdict can: the owner's trust was written, or a
    server listed its tools differently. A session's catalog holds each tool's verdict, so the
    runtime reads this at each turn (`tool_providers.registry.surface_stamp`) and judges its tools
    again when it differs."""
    try:
        written: object = _identity(_path(TRUST_RECORD))
    except FileNotFoundError:
        written = "absent"
    except OSError:
        written = "unreadable"
    return (_listings_changed, written)


def description_notice(server: str, tools: tuple[str, ...]) -> tuple[str, str]:
    """The quiet notice's title and body: *server* changed the description of *tools*. Product
    copy."""
    if len(tools) == 1:
        title = f"{server} changed the description of its tool {tools[0]}"
    else:
        title = f"{server} changed the descriptions of {len(tools)} of its tools"
        title += f": {', '.join(tools)}" if len(tools) <= 5 else ""
    body = (
        "A tool's description is text the model reads when it decides what to call, so look at "
        f"what {'it says' if len(tools) == 1 else 'they say'} now on the Tools page."
    )
    return title, body


def observe(server: str, tools: Iterable[Any]) -> None:
    """A start of *server* listed *tools* (`mcp_discovery.note_start`, for the server as it is
    defined now). Never raises: what it found has already happened."""
    global _listings_changed

    defs = [d for d in map(definition, tools) if d.name]
    listing = tuple(sorted((d.name, digest(d)) for d in defs))
    before = _last_listing.get(server)
    _last_listing[server] = listing
    if before is not None and before != listing:
        _listings_changed += 1
    try:
        changed = _note_descriptions(server, defs)
    except Exception:  # noqa: BLE001 - a record that cannot be written must not fail the start
        logger.warning("MCP tool descriptions for %s could not be recorded", server, exc_info=True)
        return
    if not changed or holds_trust(server):
        return
    for listener in list(_listeners):
        try:
            listener(server, changed)
        except Exception:  # noqa: BLE001 - the owner is told elsewhere if this one fails
            logger.warning("MCP description notice for %s failed", server, exc_info=True)


def _note_descriptions(server: str, defs: list[Definition]) -> tuple[str, ...]:
    """Keep each listed tool's description digest as the one last seen of *server*, and return the
    tools whose description differs from the one seen before. A tool seen for the first time has
    nothing to differ from; one no longer listed keeps what was seen of it."""
    current = {d.name: _sha256(_canonical(d.description)) for d in defs}
    held = _servers(SEEN_RECORD).get(server)
    before = held if isinstance(held, dict) else {}
    changed = tuple(sorted(n for n, h in current.items() if n in before and before[n] != h))
    if all(before.get(n) == h for n, h in current.items()):
        return changed
    with _locked(SEEN_RECORD):
        seen = dict(_servers(SEEN_RECORD))
        entry = seen.get(server)
        merged = dict(entry) if isinstance(entry, dict) else {}
        merged.update(current)
        seen[server] = merged
        _write(SEEN_RECORD, seen)
    return changed
