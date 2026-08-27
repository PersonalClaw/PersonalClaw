"""Telemetry import for agents that cannot be proxied (EXTERNAL-ACCESS §8).

Some agents on this machine will never point their API base URL at the §7.1
capture proxy — they are closed, or they only ever wrote a log file. §8 says such
an agent is still mineable: export its log, normalise it into the §7.2 session
record shape with a *small per-format adapter*, and then run the identical
redact → fence → stage pipeline the proxy runs. This module is the adapter set
plus the idempotence ledger; it deliberately owns no hygiene of its own.

Three deliberate boundaries:

* **Hygiene belongs to the store.** ``capture_store.stage_records`` owns
  ``redact()`` and ``fence_untrusted()``. Re-implementing either here would mint
  a second hygiene path that can drift out of step with the proxy's — the exact
  failure §7.2 designs against. The store is resolved lazily (and injectable) so
  the adapters are testable without it.
* **Malformed input is data, not an exception.** A hand-exported agent log is
  usually part rubbish: a truncated last line, an interleaved stderr banner, a
  well-formed record of a kind that carries no turn. §8 requires those be
  *skipped and counted*, so every adapter returns a :class:`ParseResult` and no
  adapter raises on content. Only a caller error (unreadable path) short-circuits,
  and it short-circuits into the same report shape.
* **Idempotence is by file content.** Re-importing the same export is a no-op,
  keyed on a SHA-256 of the file's bytes recorded in a per-home ledger.
* **The two entry points do not share an admissible path set.** ``personalclaw
  capture import <path>`` takes any path — a human at a shell can already read
  that file as themselves. ``POST /capture/import`` (mounted beside the proxy in
  ``capture_proxy``) reads only :func:`imports_dir`, because its bearer is a
  capture-surface token and not the shell user. Both then run the *same*
  :func:`import_capture_file`, so there is one pipeline and one hygiene path.

Reporting shape (§8): ``{imported, skipped, reasons}``, plus the content hash and
a ``duplicate`` flag so a caller can tell "already imported" from "imported
nothing".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from personalclaw.atomic_write import atomic_write
from personalclaw.config import config_dir

logger = logging.getLogger(__name__)

#: The formats §8 names. Kept as a tuple so the CLI's ``choices`` and the
#: library's dispatch cannot drift apart.
FORMATS: tuple[str, ...] = ("jsonl", "json", "sse")

#: A §7.2 record carries *digests* beside a full-content sidecar, so the digest
#: fields are clipped prose rather than hashes: the flywheel mines this text, and
#: a hash would be both unmineable and pointless to redact.
DIGEST_MAX_CHARS = 2000
ARGS_CLIP_CHARS = 400

LEDGER_FILENAME = "import_ledger.json"
CAPTURE_DIRNAME = "capture"

#: The one directory ``POST /capture/import`` will read from. See
#: :func:`resolve_import_file` for why the HTTP half does not inherit the CLI's
#: any-path freedom.
IMPORTS_DIRNAME = "imports"

# Path attribution. §7.2's "injected/available ≠ used" property means only an
# actual read or write counts as evidence, so paths are harvested from tool-call
# arguments by tool name and never from anything merely mentioned in prose.
_READ_TOOLS = frozenset({"read", "grep", "glob", "ls", "notebookread", "readfile", "viewfile"})
_WRITE_TOOLS = frozenset(
    {"write", "edit", "multiedit", "notebookedit", "applypatch", "writefile", "editfile"}
)
_PATH_KEYS = ("file_path", "filePath", "notebook_path", "notebookPath", "path")

_SSE_FIELDS = ("data:", "event:", "id:", "retry:")


@dataclass
class ParseResult:
    """What one adapter made of one export.

    ``skipped`` is tracked beside ``reasons`` rather than derived from
    ``len(reasons)`` so a future adapter that collapses many identical skips into
    one reason cannot silently under-report the count.
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    reasons: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons.append(reason)


# ── §7.2 record construction ──


def _clip(text: str, limit: int) -> str:
    """Single-line, length-capped prose. Ellipsis marks a truncation."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _content_text(content: Any) -> str:
    """Flatten a message ``content`` into prose.

    Both dialects allow a bare string or a list of typed blocks, so this accepts
    either. Non-text blocks (``tool_use``, ``tool_result``, images) contribute
    nothing here — they are harvested separately into ``tool_calls`` so a tool
    argument cannot leak into the prose digest and be mined as if the model had
    said it.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def _tokens(usage: Any) -> dict[str, int] | None:
    """Normalise either dialect's usage block to ``{input, output}``.

    ``None`` when the export carried no usage at all: a record claiming zero
    tokens and a record that never learned its token count are different facts,
    and cost analysis downstream needs to tell them apart.
    """
    if not isinstance(usage, dict):
        return None
    got: dict[str, int] = {}
    for key, dest in (
        ("input_tokens", "input"),
        ("prompt_tokens", "input"),
        ("output_tokens", "output"),
        ("completion_tokens", "output"),
    ):
        raw = usage.get(key)
        if isinstance(raw, (int, float)) and dest not in got:
            got[dest] = int(raw)
    return got or None


def _paths_from_args(name: str, args: Any) -> tuple[list[str], list[str]]:
    """Split a tool call's path arguments into reads and writes by tool name."""
    if not isinstance(args, dict):
        return [], []
    found = [str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str) and args[k]]
    if not found:
        return [], []
    lowered = (name or "").lower().replace("_", "").replace("-", "")
    if lowered in _WRITE_TOOLS:
        return [], found
    if lowered in _READ_TOOLS:
        return found, []
    # An unrecognised tool that names a path is evidence of *something*, but
    # guessing its direction would fabricate a write. Attribute nothing.
    return [], []


def _tool_call(name: str, args: Any, *, ok: bool | None = None) -> dict[str, Any]:
    """One §7.2 ``tool_calls`` entry.

    ``ok`` is tri-state on purpose. ``None`` means the export contained no
    matching result, which is not the same claim as ``False``; an importer that
    defaulted to ``False`` would manufacture failures out of a truncated log.
    """
    if isinstance(args, str):
        clipped = _clip(args, ARGS_CLIP_CHARS)
    else:
        try:
            clipped = _clip(json.dumps(args, sort_keys=True, default=str), ARGS_CLIP_CHARS)
        except (TypeError, ValueError):
            clipped = _clip(str(args), ARGS_CLIP_CHARS)
    return {"name": str(name or ""), "args_clipped": clipped, "ok": ok}


def _record(
    *,
    ts: Any = None,
    dialect: str,
    model_requested: Any = None,
    prompt_digest: str | None = None,
    response_digest: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    read_paths: Iterable[str] = (),
    wrote_paths: Iterable[str] = (),
    tokens: dict[str, int] | None = None,
    latency_ms: Any = None,
) -> dict[str, Any]:
    """Build a §7.2 record with every key present.

    Absent facts are explicit ``None``/``[]`` rather than missing keys, so a
    consumer reads one shape whatever the source format was — a record whose
    fields appear and disappear per adapter is how a downstream ``.get()`` grows
    into a per-format branch.
    """
    return {
        "ts": ts,
        "dialect": dialect,
        "model_requested": str(model_requested) if model_requested else None,
        "prompt_digest": prompt_digest,
        "response_digest": response_digest,
        "tool_calls": tool_calls or [],
        "read_paths": sorted(set(read_paths)),
        "wrote_paths": sorted(set(wrote_paths)),
        "tokens": tokens,
        "latency_ms": int(latency_ms) if isinstance(latency_ms, (int, float)) else None,
    }


# ── Adapter: Claude Code session JSONL ──


def adapt_jsonl(text: str) -> ParseResult:
    """Claude Code session JSONL → §7.2 records, one per assistant turn.

    Fields read: ``type``, ``timestamp``, ``message.model``,
    ``message.content`` (``text`` / ``tool_use`` / ``tool_result`` blocks) and
    ``message.usage``. A ``user`` line is *not* a record — it is folded into the
    ``prompt_digest`` of the assistant line that answers it, which is what makes
    a §7.2 record a turn rather than a message.

    ``latency_ms`` is structurally unfillable here: a session transcript records
    what was said, not how long the round trip took. It stays ``None``.

    Tool outcomes arrive *after* the call, in a later line's ``tool_result``
    block. Records are emitted eagerly and their ``tool_calls`` entries patched
    by reference when the result shows up, so a call whose result never appears
    keeps ``ok=None`` instead of being dropped.
    """
    out = ParseResult()
    pending_prompt: str | None = None
    by_tool_id: dict[str, dict[str, Any]] = {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            out.skip(f"line {lineno}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(entry, dict):
            out.skip(
                f"line {lineno}: expected a JSON object, got {type(entry).__name__} — "
                "a session JSONL line is one object per turn"
            )
            continue

        kind = entry.get("type")
        if not isinstance(kind, str) or not kind:
            out.skip(f"line {lineno}: entry has no 'type' field, cannot tell user from assistant")
            continue

        message = entry.get("message")
        message = message if isinstance(message, dict) else {}

        if kind == "user":
            # Resolve any tool results this line carries, then keep its prose as
            # the prompt for the next assistant turn.
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                target = by_tool_id.get(str(block.get("tool_use_id") or ""))
                if target is not None:
                    target["ok"] = not bool(block.get("is_error"))
            prose = _content_text(message.get("content"))
            if prose:
                pending_prompt = _clip(prose, DIGEST_MAX_CHARS)
            continue

        if kind != "assistant":
            out.skip(
                f"line {lineno}: entry type {kind!r} carries no request/response turn "
                "(only 'user' and 'assistant' entries become records)"
            )
            continue

        if not message:
            out.skip(f"line {lineno}: assistant entry has no 'message' object to read a turn from")
            continue

        blocks = message.get("content")
        tool_calls: list[dict[str, Any]] = []
        reads: list[str] = []
        writes: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                args = block.get("input")
                call = _tool_call(name, args)
                tool_calls.append(call)
                tool_id = block.get("id")
                if isinstance(tool_id, str) and tool_id:
                    by_tool_id[tool_id] = call
                got_reads, got_writes = _paths_from_args(name, args)
                reads.extend(got_reads)
                writes.extend(got_writes)

        out.records.append(
            _record(
                ts=entry.get("timestamp"),
                dialect="anthropic",
                model_requested=message.get("model"),
                prompt_digest=pending_prompt,
                response_digest=_clip(_content_text(blocks), DIGEST_MAX_CHARS) or None,
                tool_calls=tool_calls,
                read_paths=reads,
                wrote_paths=writes,
                tokens=_tokens(message.get("usage")),
                latency_ms=None,
            )
        )
        pending_prompt = None

    return out


# ── Adapter: OpenAI-format request logs (JSON) ──


def _entries_from_json(doc: Any) -> tuple[list[Any], str | None]:
    """Find the list of logged exchanges in a whole-file JSON document."""
    if isinstance(doc, list):
        return doc, None
    if isinstance(doc, dict):
        for key in ("records", "data", "entries", "requests", "log"):
            value = doc.get(key)
            if isinstance(value, list):
                return value, None
        if "request" in doc or "messages" in doc:
            return [doc], None
        return [], (
            "file is JSON but holds no request log: expected a list of exchanges, or an "
            "object with a 'records'/'data'/'entries' list, or a single 'request' object"
        )
    return [], f"file is JSON but its top level is {type(doc).__name__}, not a list or object"


def adapt_json(text: str) -> ParseResult:
    """OpenAI-format request logs → §7.2 records, one per logged exchange.

    Fields read: ``request.model``/``model``, ``request.messages``,
    ``response.choices[0].message`` (content and ``tool_calls``),
    ``response.usage``, ``latency_ms`` and ``ts``. A bare request body with no
    ``request`` wrapper is accepted, because that is how most hand-rolled loggers
    dump one call.

    This is the only format that can fill ``latency_ms``: a request log is
    written by the caller, who is the one party that knows the round-trip time.
    ``tool_calls[].ok`` stays ``None`` — a *request* log records the call the
    model asked for, never whether running it worked.
    """
    out = ParseResult()
    stripped = text.strip()
    if not stripped:
        return out
    try:
        doc = json.loads(stripped)
    except json.JSONDecodeError as exc:
        out.skip(
            f"file is not valid JSON ({exc.msg} at line {exc.lineno}) — "
            "if this is one JSON object per line, import it with --format jsonl"
        )
        return out

    entries, problem = _entries_from_json(doc)
    if problem:
        out.skip(problem)
        return out

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            out.skip(f"entry {index}: expected an object, got {type(entry).__name__}")
            continue
        request = entry.get("request")
        request = request if isinstance(request, dict) else entry
        response = entry.get("response")
        response = response if isinstance(response, dict) else {}

        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            out.skip(
                f"entry {index}: no 'request.messages' list — this is not an OpenAI-format "
                "request log entry"
            )
            continue

        prompt_parts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            prose = _content_text(message.get("content"))
            if prose:
                prompt_parts.append(f"{message.get('role') or 'user'}: {prose}")

        choices = response.get("choices")
        reply: dict[str, Any] = {}
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            candidate = choices[0].get("message")
            reply = candidate if isinstance(candidate, dict) else {}
        response_text = _content_text(reply.get("content") or response.get("content"))

        tool_calls: list[dict[str, Any]] = []
        reads: list[str] = []
        writes: list[str] = []
        for call in reply.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            function = function if isinstance(function, dict) else call
            name = str(function.get("name") or "")
            raw_args = function.get("arguments")
            tool_calls.append(_tool_call(name, raw_args))
            # OpenAI ships arguments as a JSON *string*; decode best-effort so
            # path attribution works, and attribute nothing if it will not parse.
            parsed: Any = raw_args
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed = None
            got_reads, got_writes = _paths_from_args(name, parsed)
            reads.extend(got_reads)
            writes.extend(got_writes)

        out.records.append(
            _record(
                ts=entry.get("ts") or entry.get("timestamp") or response.get("created"),
                dialect="openai",
                model_requested=request.get("model") or response.get("model"),
                prompt_digest=_clip("\n".join(prompt_parts), DIGEST_MAX_CHARS) or None,
                response_digest=_clip(response_text, DIGEST_MAX_CHARS) or None,
                tool_calls=tool_calls,
                read_paths=reads,
                wrote_paths=writes,
                tokens=_tokens(response.get("usage") or entry.get("usage")),
                latency_ms=entry.get("latency_ms") or entry.get("latency"),
            )
        )

    return out


# ── Adapter: raw SSE event dumps ──


class _SSEStream:
    """Accumulator for one SSE response stream."""

    def __init__(self) -> None:
        self.dialect = "openai"
        self.model: Any = None
        self.text_parts: list[str] = []
        self.tool_names: dict[int, str] = {}
        self.tool_args: dict[int, list[str]] = {}
        self.usage: dict[str, Any] = {}
        self.ts: Any = None
        self.touched = False

    def tool_calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for index in sorted(set(self.tool_names) | set(self.tool_args)):
            args = "".join(self.tool_args.get(index, []))
            calls.append(_tool_call(self.tool_names.get(index, ""), args))
        return calls

    def to_record(self) -> dict[str, Any]:
        reads: list[str] = []
        writes: list[str] = []
        for index, name in self.tool_names.items():
            raw = "".join(self.tool_args.get(index, []))
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            got_reads, got_writes = _paths_from_args(name, parsed)
            reads.extend(got_reads)
            writes.extend(got_writes)
        return _record(
            ts=self.ts,
            dialect=self.dialect,
            model_requested=self.model,
            # An SSE dump is the *response* half of the wire. There is no request
            # in it, so this field is unfillable by construction, not by omission.
            prompt_digest=None,
            response_digest=_clip("".join(self.text_parts), DIGEST_MAX_CHARS) or None,
            tool_calls=self.tool_calls(),
            read_paths=reads,
            wrote_paths=writes,
            tokens=_tokens(self.usage),
            latency_ms=None,
        )


def _absorb_sse_payload(stream: _SSEStream, payload: dict[str, Any], event: str) -> None:
    """Fold one decoded SSE event into the running stream."""
    stream.touched = True
    kind = str(payload.get("type") or event or "")
    if kind.startswith(("message_", "content_block_", "ping")):
        stream.dialect = "anthropic"

    message = payload.get("message")
    if isinstance(message, dict):
        stream.model = stream.model or message.get("model")
        merged = _merge_usage(message.get("usage"))
        stream.usage.update(merged)
    stream.model = stream.model or payload.get("model")
    stream.ts = stream.ts or payload.get("created") or payload.get("ts")
    stream.usage.update(_merge_usage(payload.get("usage")))

    # Anthropic: text arrives on delta.text; a tool_use opens a content block.
    delta = payload.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("text"), str):
        stream.text_parts.append(delta["text"])
    if isinstance(delta, dict) and isinstance(delta.get("partial_json"), str):
        index = int(payload.get("index") or 0)
        stream.tool_args.setdefault(index, []).append(delta["partial_json"])
    block = payload.get("content_block")
    if isinstance(block, dict) and block.get("type") == "tool_use":
        stream.tool_names[int(payload.get("index") or 0)] = str(block.get("name") or "")

    # OpenAI: text and tool-call fragments arrive on choices[].delta.
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            chunk = choice.get("delta") or choice.get("message")
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("content"), str):
                stream.text_parts.append(chunk["content"])
            for call in chunk.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                index = int(call.get("index") or 0)
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                if function.get("name"):
                    stream.tool_names[index] = str(function["name"])
                if isinstance(function.get("arguments"), str):
                    stream.tool_args.setdefault(index, []).append(function["arguments"])


def _merge_usage(usage: Any) -> dict[str, Any]:
    return (
        {k: v for k, v in usage.items() if isinstance(v, (int, float))}
        if isinstance(usage, dict)
        else {}
    )


def adapt_sse(text: str) -> ParseResult:
    """Raw SSE event dumps → §7.2 records, one per response stream.

    Reads ``event:``/``data:`` field lines in both dialects: OpenAI's
    ``choices[].delta`` chunks terminated by ``data: [DONE]``, and Anthropic's
    ``message_start`` → ``content_block_delta`` → ``message_stop`` sequence.
    Deltas are concatenated into ``response_digest``; tool-call name and argument
    fragments are re-assembled by index.

    Two §7.2 fields are unfillable from this format and stay ``None``:
    ``prompt_digest`` (a response stream contains no request) and ``latency_ms``
    (the wire dump has no clock). A truncated dump — no terminator — still yields
    its partial record rather than being discarded, which is the whole point of
    importing a log somebody salvaged.
    """
    out = ParseResult()
    stream = _SSEStream()
    event = ""

    def flush() -> None:
        nonlocal stream, event
        if stream.touched:
            out.records.append(stream.to_record())
        stream = _SSEStream()
        event = ""

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
            continue
        if not line.startswith("data:"):
            if line.startswith(_SSE_FIELDS):
                continue  # id:/retry: are valid SSE fields carrying no payload
            out.skip(
                f"line {lineno}: not an SSE field line (expected 'event:' or 'data:') — "
                "if this is a JSON log, import it with --format json or --format jsonl"
            )
            continue

        payload_text = line[len("data:") :].strip()
        if payload_text == "[DONE]":
            flush()
            continue
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            out.skip(f"line {lineno}: invalid SSE data payload ({exc.msg})")
            continue
        if not isinstance(payload, dict):
            out.skip(
                f"line {lineno}: SSE data payload is {type(payload).__name__}, "
                "expected a JSON object"
            )
            continue
        _absorb_sse_payload(stream, payload, event)
        if str(payload.get("type") or event) == "message_stop":
            flush()

    flush()
    return out


ADAPTERS: dict[str, Callable[[str], ParseResult]] = {
    "jsonl": adapt_jsonl,
    "json": adapt_json,
    "sse": adapt_sse,
}


# ── Idempotence ledger ──


def file_content_hash(path: Path) -> str:
    """SHA-256 of the file's bytes — the import idempotence key.

    Deliberately *not* ``learning.staging.input_hash`` (staging.py:90), even
    though §8 names R19's input-hash idempotence as the mechanism to reuse. That
    helper composes ``learning.hygiene.fingerprint`` (hygiene.py:249), which
    lower-cases and collapses whitespace, and sorts its inputs. Both properties
    are right for recognising a reflowed *proposal* and wrong for identifying a
    *file*: two genuinely different exports differing only in case, or in the
    order of their turns, would collide and the second would be silently
    discarded as "already imported". What is reused is R19's shape — claim by
    hash, first writer wins, a repeat claim returns false
    (``StagingStore.claim_batch``, staging.py:381).

    Its ledger is also deliberately separate from ``batch_passes``: those rows
    drive ``should_run_batch``'s time-window gate (staging.py:378), so recording
    an import there would tell the flywheel an expensive pass had just run and
    silently defer its next consolidation.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _home(home: Path | str | None = None) -> Path:
    if home is not None:
        return Path(home)
    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))


def ledger_path(home: Path | str | None = None) -> Path:
    return _home(home) / CAPTURE_DIRNAME / LEDGER_FILENAME


def load_ledger(home: Path | str | None = None) -> dict[str, Any]:
    """Read the seen-hash ledger. A corrupt or absent ledger reads as empty.

    Failing open is the right call here: a lost ledger costs a duplicate import
    (visible, prunable), whereas raising would make one bad JSON byte block every
    future import of every file.
    """
    path = ledger_path(home)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _remember(content_hash: str, entry: dict[str, Any], home: Path | str | None = None) -> None:
    path = ledger_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = load_ledger(home)
    ledger[content_hash] = entry
    atomic_write(path, json.dumps(ledger, indent=2, sort_keys=True), mode=0o600)


# ── The drop directory (the HTTP half's only readable root) ──


def imports_dir(home: Path | str | None = None) -> Path:
    """``<home>/capture/imports`` — the ONE directory a network caller may import from.

    Created ``0700``, matching the recordings beside it (``capture_store._DIR_MODE``):
    an exported transcript is exactly as sensitive as a captured one.
    """
    path = _home(home) / CAPTURE_DIRNAME / IMPORTS_DIRNAME
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - an unwritable home is the caller's problem
        logger.debug("capture import: could not prepare %s", path, exc_info=True)
    return path


def resolve_import_file(name: str, home: Path | str | None = None) -> tuple[Path | None, str]:
    """Resolve ``name`` inside :func:`imports_dir`, or return ``(None, why)``.

    🔴 **This is a security boundary, not a convenience.** The CLI takes any path
    because a human at a shell can already read that file as themselves. The HTTP half
    cannot inherit that: its bearer is a *capture-surface token* held by an external
    agent, which is far less privileged than the shell user — so a caller-chosen path
    would turn the gateway into a file-read oracle that stages any readable file
    (``~/.ssh/id_rsa``, another project's secrets) into the learning tier. Confinement
    is the same ruling ``handlers/onboarding_import`` already made for the same shape:
    "read from the root under the request, never taken from the caller".

    Three refusals, in the order that leaks least:

    1. **Not a bare file name.** ``../``, ``a/b`` and ``/etc/passwd`` never become a
       candidate at all — checked textually first, so nothing is stat'ed on a path the
       caller had no business naming.
    2. **Resolves out of the directory.** A *symlink* placed in the drop directory is
       the escape a name check alone cannot see, so the resolved parent is compared
       against the resolved root. Both sides are resolved because the home itself may
       sit under a symlink (``/tmp`` → ``/private/tmp`` on macOS), where comparing a
       resolved path to an unresolved root would refuse every legitimate file.
    3. **Not a file.** Reported last, and by name: "nothing dropped here yet" is the
       operator's own mistake to fix, not a hint about the filesystem.
    """
    raw = str(name or "").strip()
    root = imports_dir(home)
    if not raw:
        return None, f"a file name is required — name a file you have placed in {root}"
    if "/" in raw or "\\" in raw or raw != Path(raw).name:
        return None, (
            f"{raw!r} is not a bare file name: this route reads only files placed "
            f"directly in {root}, never a path of the caller's choosing"
        )
    try:
        resolved = (root / raw).resolve()
        root_resolved = root.resolve()
    except OSError as exc:  # pragma: no cover - a resolve fault is not a valid name
        return None, f"cannot resolve {raw!r}: {exc.strerror or exc}"
    if resolved.parent != root_resolved:
        return None, (
            f"{raw!r} resolves outside {root} (it is a link out of the drop "
            "directory) and is refused"
        )
    if not resolved.is_file():
        return None, f"no file named {raw!r} in {root}"
    return resolved, ""


# ── The pipeline ──


def _resolve_stage(stage: Callable[..., dict[str, Any]] | None) -> Callable[..., dict[str, Any]]:
    """Resolve the sibling store's ``stage_records``, lazily.

    Imported inside the call rather than at module scope so the adapters are
    exercisable — and this module importable — without the store present, and so
    a test can inject a double instead of reaching into ``sys.modules``.
    """
    if stage is not None:
        return stage
    from personalclaw.inbound.capture_store import stage_records

    return stage_records


def import_capture_file(
    source_file: Path | str,
    *,
    fmt: str = "jsonl",
    source: str = "import",
    home: Path | str | None = None,
    stage: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalise an exported agent log and stage it (EXTERNAL-ACCESS §8).

    Returns ``{imported, skipped, reasons, duplicate, content_hash, format,
    source}``. Never raises on the *content* of the file: an unreadable path, an
    unknown format, an empty file and a file in the wrong format all come back as
    a report with a reason. The one thing that does propagate is a failure inside
    the store, because that is not a property of the import.

    Order of operations matters. The ledger is checked before parsing (a repeat
    import should cost one hash, not a full parse) and written only *after* the
    store accepted at least one record — recording the hash first would let a
    crashed or rejected stage poison the file forever.
    """
    path = Path(source_file)
    report: dict[str, Any] = {
        "imported": 0,
        "skipped": 0,
        "reasons": [],
        "duplicate": False,
        "content_hash": "",
        "format": fmt,
        "source": source,
    }

    if fmt not in ADAPTERS:
        report["reasons"].append(f"unknown format {fmt!r}: expected one of {', '.join(FORMATS)}")
        report["skipped"] = 1
        return report

    try:
        content_hash = file_content_hash(path)
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report["reasons"].append(f"cannot read {path}: {exc.strerror or exc}")
        report["skipped"] = 1
        return report

    report["content_hash"] = content_hash

    if content_hash in load_ledger(home):
        report["duplicate"] = True
        report["reasons"].append(
            f"already imported: {path.name} has content hash {content_hash[:12]} "
            "in this home's capture import ledger — nothing to do"
        )
        return report

    parsed = ADAPTERS[fmt](text)
    report["skipped"] = parsed.skipped
    report["reasons"] = list(parsed.reasons)

    if not parsed.records:
        if not parsed.reasons:
            report["reasons"].append(
                f"no records found in {path.name}: the file is empty or holds no " f"{fmt} turns"
            )
        return report

    staged = _resolve_stage(stage)(parsed.records, source=source)
    staged = staged if isinstance(staged, dict) else {}
    report["imported"] = int(staged.get("imported") or 0)
    report["skipped"] += int(staged.get("skipped") or 0)
    report["reasons"].extend(str(r) for r in staged.get("reasons") or [])

    if report["imported"]:
        _remember(
            content_hash,
            {
                "file": str(path),
                "format": fmt,
                "source": source,
                "imported": report["imported"],
                "skipped": report["skipped"],
            },
            home,
        )
    return report


# ── CLI ──


def render_report(report: dict[str, Any]) -> str:
    """The stdout the operator reads. Counts first, then every reason."""
    head = (
        f"imported {report.get('imported', 0)}, skipped {report.get('skipped', 0)}"
        f" ({report.get('format')} from {report.get('source')!r})"
    )
    if report.get("duplicate"):
        head = f"already imported — nothing staged ({head})"
    lines = [head]
    for reason in report.get("reasons") or []:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def capture_cmd(
    args: argparse.Namespace,
    *,
    stage: Callable[..., dict[str, Any]] | None = None,
) -> int:
    """``personalclaw capture import <file> --format … --source …``.

    Exits non-zero only when nothing was staged and the file was not a duplicate:
    a partial import is a success with visible losses (§8's skipped-and-counted),
    while "imported 0 from a file you named" is a result a script should be able
    to gate on. A duplicate exits 0 — a no-op re-import is the requested outcome.
    """
    action = getattr(args, "capture_action", None)
    if action != "import":
        print(f"Usage: personalclaw capture import <file> --format {'|'.join(FORMATS)}")
        return 2

    try:
        report = import_capture_file(
            args.file,
            fmt=getattr(args, "format", "jsonl"),
            source=getattr(args, "source", "") or "import",
            stage=stage,
        )
    except ImportError as exc:
        # The store is the sibling half of §8. Name it rather than showing a
        # traceback: "no module named …" is the actionable fact.
        print(f"capture import needs the capture store, which is not installed: {exc}")
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))

    if report["imported"] or report["duplicate"]:
        return 0
    return 1
