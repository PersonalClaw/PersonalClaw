"""Capture-session store: the persistence half of the Dialect-5 capture proxy.

EXTERNAL-ACCESS §7.2. The capture proxy (``capture_proxy.py``) and the telemetry
importer (``capture_import.py``) both land here, because both owe the *identical*
guarantee and there must be exactly one implementation of it:

**Hygiene happens AT INGESTION, never at mining time.** Before a single byte of
external-agent content reaches disk it is (a) screened by
:func:`personalclaw.security.redact_credentials` and (b) wrapped by
:func:`personalclaw.security.fence_untrusted` with ``source="capture:<client_id>"``.
That ordering is the whole security argument for this atom: when a LEARNING-FLYWHEEL
pass later reads a capture session, the content is *already* inside fences, so
``learning/hygiene.py``'s existing rule ("content inside ``fence_untrusted`` is
invisible to direct capture cadences; it may only travel the proposal path") applies
with zero new policy. An injection planted in an external agent's transcript can
therefore never direct-write a lesson. Fencing at *read* time would have been a
policy that every future reader has to remember; fencing at *write* time is a
property of the bytes.

SCREEN EACH SOURCE STRING ONCE, AT ENTRY — never over an assembled record. This is
not a style preference, it is a measured defect. ``_CREDENTIAL_PATTERNS`` matches the
``api_key=`` prefix *as part of* the credential span, so screening the composed line
``api_key=sk-ant-api03-…`` collapses the whole thing to ``[REDACTED: credential]`` and
**destroys the field name**, while screening the value alone yields
``api_key=[REDACTED: credential]`` and keeps the record readable. A trailing
chokepoint over a finished record is therefore strictly worse than per-field
screening, which is why :func:`_screen` is called at every field boundary and never
once at the end.

The second half of the same hazard governs how callers may use the returned warning
list: a second pass over already-redacted text returns ``(unchanged_text, [])``.
``found`` is only trustworthy **on first contact**. Any vacuity floor ("prove the
redactor would have caught this") must assert against a screen of the *raw* source,
because a re-screen of scrubbed text reads as clean and would make the floor vacuous.

Latency honesty: recording is post-hoc. The proxy responds to its caller first, then
persists off the hot path via :func:`record_turn_async`, which is an
``asyncio.to_thread`` wrapper — sync file IO in the async proxy loop is the known
anti-pattern this avoids.

:func:`record_turn` NEVER raises. A capture store that can break the proxy it observes
is worse than no capture store: the user's actual agent traffic must not fail because
a record could not be serialized. Every failure degrades to "this turn went
unrecorded", logged at debug, and the caller is unharmed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.security import fence_untrusted, redact_credentials

logger = logging.getLogger(__name__)

# 0600/0700: capture sessions hold FULL prompts and responses from the user's coding
# agents — the highest-sensitivity content this project persists anywhere. Owner-only,
# enforced on every write rather than once at create, because an operator `umask` or a
# restored backup can loosen a directory that was created correctly.
_FILE_MODE = 0o600
_DIR_MODE = 0o700

#: Tool-call arguments are clipped, not stored whole, in the *record*. The record is
#: the mineable index; the sidecar carries full content for the rare deep read.
_ARGS_CLIP = 240

#: Retention semantics: 0 means NEVER prune. Chosen deliberately over "prune
#: everything immediately" — the two readings are indistinguishable in a config file
#: and only one of them is a silent data-loss footgun. Matches how
#: `auto_disable_after_breaches` already reads 0 as "never" in the same config block.
_RETENTION_NEVER = 0


def capture_dir() -> Path:
    """``<config_dir>/capture`` — created 0700 on demand.

    Resolved per call rather than cached at import, because ``config_dir()`` follows
    ``PERSONALCLAW_HOME`` and a module-level constant would freeze whichever home
    happened to be set when this module was first imported — the exact shape that
    makes a test suite write into the operator's real home.
    """
    from personalclaw.config.loader import config_dir

    path = Path(config_dir()) / "capture"
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, _DIR_MODE)
    except OSError:  # pragma: no cover - unwritable home is the caller's problem
        logger.debug("capture: could not prepare %s", path, exc_info=True)
    return path


def _screen(text: str) -> tuple[str, list[str]]:
    """Screen ONE source string. Call at every field boundary, never on a whole record.

    See the module docstring: screening a composed ``field=value`` line destroys the
    field name, and re-screening scrubbed text reports an empty ``found``.
    """
    if not text:
        return "", []
    try:
        return redact_credentials(text)
    except Exception:  # pragma: no cover - redactor must never break capture
        logger.debug("capture: redaction failed; dropping content", exc_info=True)
        # Fail CLOSED on content: if we cannot prove the text is scrubbed, we do not
        # persist it. An unscreened prompt on disk is the one outcome this module
        # exists to prevent.
        return "[REDACTED: unscreenable]", ["redaction failed"]


def _fence(text: str, client_id: str) -> str:
    """Wrap screened content as untrusted data attributed to its capture client."""
    if not text:
        return ""
    return fence_untrusted(
        text,
        source=f"capture:{client_id}",
        source_type="capture",
        source_id=client_id,
        transformation_path="proxy",
    )


def _dumps(value: Any) -> str:
    """Serialize for digesting. Raises on genuinely hostile input — by design.

    ``default=str`` is deliberately NOT used as a blanket escape: the caller
    (:func:`record_turn`) owns the never-raise guarantee, and swallowing the failure
    here would hide it from the one place that handles it.
    """
    return json.dumps(value, sort_keys=True, default=repr)


def _digest(text: str) -> str:
    """Content digest. The record stores digests; the sidecar stores content."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _messages(body: dict) -> list[dict]:
    raw = body.get("messages")
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


def _text_of(part: Any) -> str:
    """Flatten a message ``content`` across the dialects this proxy fronts.

    OpenAI sends a bare string or a list of ``{type, text}`` parts; Anthropic sends
    the list form plus ``tool_result`` blocks. Both reduce to text for digesting.
    """
    if isinstance(part, str):
        return part
    if isinstance(part, list):
        return "\n".join(_text_of(p) for p in part)
    if isinstance(part, dict):
        for key in ("text", "content", "thinking"):
            if key in part:
                return _text_of(part[key])
    return ""


def conversation_fingerprint(body: dict) -> str:
    """Stable id for the conversation a request belongs to.

    Keyed on the conversation's **opening** — the system prompt plus the first user
    message — precisely because that prefix is invariant as turns accumulate, while
    the tail grows every turn. Fingerprinting the whole message list would mint a
    fresh session per turn and defeat session assembly entirely.
    """
    opening: list[str] = []
    system = body.get("system")
    if system:
        opening.append(_text_of(system))
    for msg in _messages(body):
        if msg.get("role") == "system":
            opening.append(_text_of(msg.get("content")))
            continue
        if msg.get("role") == "user":
            opening.append(_text_of(msg.get("content")))
            break
    joined = "\n".join(p for p in opening if p)
    return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()[:16]


def session_id_for(client_id: str, body: dict) -> str:
    """``(client_id, conversation fingerprint)`` → session id. Requests sharing it fold."""
    safe_client = "".join(c if c.isalnum() or c in "-_" else "-" for c in client_id) or "unknown"
    return f"{safe_client}-{conversation_fingerprint(body)}"


# ---------------------------------------------------------------------------
# skill_path_map — "this session read my deploy-checklist skill" as a mechanical fact
# ---------------------------------------------------------------------------


def _skill_roots() -> list[Path]:
    """The skills trees whose files can be attributed, highest tier first."""
    roots: list[Path] = []
    try:
        from personalclaw.skills.loader import skills_dir

        roots.append(Path(skills_dir()))
    except Exception:  # pragma: no cover - skills package always present in-tree
        logger.debug("capture: user skills dir unavailable", exc_info=True)
    # Agent-tier skills (agentskills.io cross-client standard), per §7.2.
    roots.append(Path.home() / ".agents" / "skills")
    return roots


def skill_path_map() -> dict[str, str]:
    """Index every file under the skills trees → the skill id that owns it.

    The mapping is what makes **injected/available ≠ used** hold *by construction*:
    it is consulted only against paths a tool call actually read or wrote, so a skill
    that was merely present in the agent's context never appears as evidence. Nothing
    in this module can attribute a skill that was not touched, because the only input
    is a list of real file operations.
    """
    mapping: dict[str, str] = {}
    for root in _skill_roots():
        try:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    rel = path.resolve().relative_to(root.resolve())
                except (OSError, ValueError):
                    continue
                if not rel.parts:
                    continue
                # Skill id is the immediate child of the root — the directory that
                # holds SKILL.md. A file sitting loose at the root belongs to no skill.
                if len(rel.parts) < 2:
                    continue
                mapping.setdefault(str(path.resolve()), rel.parts[0])
        except OSError:  # pragma: no cover
            logger.debug("capture: could not index skills root %s", root, exc_info=True)
    return mapping


def attribute_skills(paths: list[str]) -> list[str]:
    """Skill ids for the subset of ``paths`` that live inside a skills tree.

    A path outside every skills tree maps to nothing — not to a guess, and not to a
    catch-all bucket. Unattributable file access is simply not skill evidence.
    """
    if not paths:
        return []
    mapping = skill_path_map()
    if not mapping:
        return []
    found: list[str] = []
    for raw in paths:
        try:
            resolved = str(Path(raw).resolve())
        except (OSError, ValueError, RuntimeError):
            continue
        skill = mapping.get(resolved)
        if skill and skill not in found:
            found.append(skill)
    return found


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------

#: Argument keys that carry a filesystem path, across the dialects' tool schemas.
_PATH_KEYS = ("path", "file_path", "filename", "file", "notebook_path", "target_file")
#: Tool names whose invocation counts as a WRITE rather than a read.
_WRITE_HINTS = ("write", "edit", "create", "patch", "apply", "insert", "replace", "delete")


@dataclass
class _ToolCall:
    name: str
    args_clipped: str
    ok: bool
    paths: list[str]
    writes: bool


def _extract_tool_calls(response_body: dict | None, request_body: dict) -> list[_ToolCall]:
    """Tool calls from either dialect's response, plus prior turns' results.

    Both dialects are read because the proxy fronts both and a session may mix them
    across turns; an unrecognised shape yields no tool calls rather than a guess.
    """
    calls: list[_ToolCall] = []
    blocks: list[dict] = []

    if isinstance(response_body, dict):
        # Anthropic: content blocks with type=tool_use
        content = response_body.get("content")
        if isinstance(content, list):
            blocks.extend(b for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
        # OpenAI: choices[].message.tool_calls[]
        for choice in response_body.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    blocks.append(call)

    # `ok` is only knowable from the FOLLOWING turn's tool_result, so the request's
    # message history is the authority for outcomes. Absent a result, ok stays True:
    # an unresolved call is not a failed one.
    failed: set[str] = set()
    for msg in _messages(request_body):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if block.get("is_error"):
                tool_id = block.get("tool_use_id")
                if isinstance(tool_id, str):
                    failed.add(tool_id)

    for block in blocks:
        name = str(block.get("name") or (block.get("function") or {}).get("name") or "")
        args: Any = block.get("input")
        if args is None:
            function = block.get("function")
            args = (function or {}).get("arguments") if isinstance(function, dict) else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}

        paths = [str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str)]
        lowered = name.lower()
        try:
            rendered = _dumps(args)
        except (TypeError, ValueError):
            rendered = "<unserializable>"
        calls.append(
            _ToolCall(
                name=name,
                args_clipped=rendered[:_ARGS_CLIP],
                ok=str(block.get("id") or "") not in failed,
                paths=paths,
                writes=any(hint in lowered for hint in _WRITE_HINTS),
            )
        )
    return calls


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _session_paths(session_id: str) -> tuple[Path, Path]:
    """``(record jsonl, full-content sidecar)`` for one session."""
    root = capture_dir()
    return root / f"{session_id}.jsonl", root / f"{session_id}.content.jsonl"


def _append(path: Path, payload: dict) -> None:
    """Append one JSON line, enforcing 0600 on every write.

    The mode is (re)applied per append rather than only at create: a file restored
    from a backup or created under a loose umask would otherwise stay world-readable
    for its whole life, and this content is the most sensitive on disk.
    """
    existed = path.exists()
    if not existed:
        # Create with the right mode from the first byte — never a window where a
        # capture file is readable by another local user.
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, _FILE_MODE)
        os.close(fd)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    os.chmod(path, _FILE_MODE)


def _record_hashes(path: Path) -> set[str]:
    """Content hashes already present in a session file (import idempotence)."""
    hashes: set[str] = set()
    if not path.exists():
        return hashes
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("record_hash"), str):
                    hashes.add(row["record_hash"])
    except OSError:  # pragma: no cover
        logger.debug("capture: could not read %s", path, exc_info=True)
    return hashes


def _hash_record(record: dict) -> str:
    """Content hash of a record, excluding ``ts`` (and its own previous value).

    Factored out of :func:`_build_record` because :func:`stage_records` overlays an
    importer's already-extracted tool facts *after* the record is built and must
    therefore re-hash. A hash that omitted the overlaid fields would make two imported
    turns that differ only in their tool calls read as duplicates of each other, and the
    import would silently drop the second one.
    """
    return hashlib.sha256(
        _dumps({k: v for k, v in record.items() if k not in ("ts", "record_hash")}).encode("utf-8")
    ).hexdigest()


def _build_record(
    *,
    client_id: str,
    dialect: str,
    model_requested: str,
    request_body: dict,
    response_body: dict | None,
    stream_text: str,
    tokens: dict | None,
    latency_ms: int,
) -> tuple[dict, dict]:
    """Assemble ``(record, sidecar)``. Every content field screened at its own boundary.

    Returns the §7.2 record shape plus the full-content sidecar. Raises on hostile
    input; :func:`record_turn` owns the never-raise contract.
    """
    prompt_parts = [_text_of(m.get("content")) for m in _messages(request_body)]
    system = request_body.get("system")
    if system:
        prompt_parts.insert(0, _text_of(system))
    # ONE screen per source string, at entry. Joining first and screening once would
    # destroy field names (module docstring) and would also lose per-part warnings.
    screened_prompts: list[str] = []
    warnings: list[str] = []
    for part in prompt_parts:
        clean, found = _screen(part)
        screened_prompts.append(clean)
        warnings.extend(found)
    prompt_text = "\n".join(p for p in screened_prompts if p)

    response_text = ""
    if isinstance(response_body, dict):
        chunks: list[str] = []
        content = response_body.get("content")
        if isinstance(content, list):
            chunks.append(_text_of(content))
        for choice in response_body.get("choices") or []:
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    chunks.append(_text_of(message.get("content")))
        for chunk in chunks:
            clean, found = _screen(chunk)
            if clean:
                chunks_clean = clean
                response_text = (
                    f"{response_text}\n{chunks_clean}" if response_text else chunks_clean
                )
            warnings.extend(found)
    if stream_text:
        clean, found = _screen(stream_text)
        response_text = f"{response_text}\n{clean}" if response_text else clean
        warnings.extend(found)

    calls = _extract_tool_calls(response_body, request_body)
    read_paths: list[str] = []
    wrote_paths: list[str] = []
    tool_rows: list[dict] = []
    for call in calls:
        name_clean, name_found = _screen(call.name)
        args_clean, args_found = _screen(call.args_clipped)
        warnings.extend(name_found)
        warnings.extend(args_found)
        tool_rows.append({"name": name_clean, "args_clipped": args_clean, "ok": call.ok})
        for raw in call.paths:
            path_clean, path_found = _screen(raw)
            warnings.extend(path_found)
            bucket = wrote_paths if call.writes else read_paths
            if path_clean not in bucket:
                bucket.append(path_clean)

    model_clean, model_found = _screen(model_requested)
    warnings.extend(model_found)

    record = {
        "ts": time.time(),
        "dialect": str(dialect),
        "model_requested": model_clean,
        "prompt_digest": _digest(prompt_text),
        "response_digest": _digest(response_text),
        "tool_calls": tool_rows,
        "read_paths": read_paths,
        "wrote_paths": wrote_paths,
        "read_skills": attribute_skills(read_paths),
        "wrote_skills": attribute_skills(wrote_paths),
        "tokens": dict(tokens) if isinstance(tokens, dict) else {},
        "latency_ms": int(latency_ms),
        "redactions": len(warnings),
    }
    record["record_hash"] = _hash_record(record)

    sidecar = {
        "ts": record["ts"],
        "record_hash": record["record_hash"],
        # Fenced, not raw. The flywheel reads this file; the fence is what makes an
        # injection in it un-actionable without any new policy at read time.
        "prompt": _fence(prompt_text, client_id),
        "response": _fence(response_text, client_id),
    }
    return record, sidecar


def record_turn(
    *,
    client_id: str,
    dialect: str,
    model_requested: str,
    request_body: dict,
    response_body: dict | None,
    stream_text: str = "",
    tokens: dict | None = None,
    latency_ms: int = 0,
) -> str:
    """Persist one captured turn. Returns the session id. **NEVER raises.**

    The proxy calls this after it has already answered its caller, so a failure here
    must cost at most one unrecorded turn. Anything that escaped would surface as the
    user's own coding agent failing on a request PClaw merely observed — a strictly
    worse outcome than a gap in the capture log.
    """
    session_id = ""
    try:
        body = request_body if isinstance(request_body, dict) else {}
        session_id = session_id_for(client_id, body)
        record, sidecar = _build_record(
            client_id=client_id,
            dialect=dialect,
            model_requested=model_requested,
            request_body=body,
            response_body=response_body,
            stream_text=stream_text,
            tokens=tokens,
            latency_ms=latency_ms,
        )
        record_path, sidecar_path = _session_paths(session_id)
        _append(record_path, record)
        _append(sidecar_path, sidecar)
    except Exception:
        # Broad by contract, not by laziness — see the docstring. Debug level because
        # a capture gap is not an operator-actionable event.
        logger.debug("capture: turn not recorded", exc_info=True)
    return session_id


async def record_turn_async(**kwargs: Any) -> str:
    """:func:`record_turn` off the hot path.

    Sync file IO inside the async proxy loop stalls the traffic it is observing —
    the §7.1 "latency honesty" requirement. ``to_thread`` is the whole mechanism;
    the never-raise guarantee is inherited from :func:`record_turn`.
    """
    return await asyncio.to_thread(lambda: record_turn(**kwargs))


def _overlay_imported_facts(record: dict, sidecar: dict, raw: dict) -> None:
    """Overlay an importer's already-extracted tool facts onto a synthesised record.

    :func:`_build_record` **derives** ``tool_calls``/``read_paths``/``wrote_paths`` from
    the request and response bodies. A §8 import has no bodies — only digests plus the
    facts its adapter already extracted — so the bodies synthesised in
    :func:`stage_records` contain no tool calls at all and re-deriving from them yields
    nothing. Without this overlay the derivation would therefore silently DROP every tool
    call and path the adapter found. It is a REPLACE, not a merge, precisely because the
    derived lists are provably empty for a synthesised body.

    Screened at each field boundary like every other content path, never over a joined
    line (module docstring). ``ok`` keeps the importer's tri-state ``None``: "the export
    contained no result" is not the claim "the call failed".
    """
    redactions = 0
    rows: list[dict] = []
    raw_calls = raw.get("tool_calls")
    for row in raw_calls if isinstance(raw_calls, list) else []:
        if not isinstance(row, dict):
            continue
        name_clean, name_found = _screen(str(row.get("name") or ""))
        args_clean, args_found = _screen(str(row.get("args_clipped") or ""))
        redactions += len(name_found) + len(args_found)
        ok = row.get("ok")
        rows.append(
            {
                "name": name_clean,
                "args_clipped": args_clean,
                "ok": ok if isinstance(ok, bool) else None,
            }
        )

    def _screened_paths(key: str) -> list[str]:
        nonlocal redactions
        out: list[str] = []
        entries = raw.get(key)
        for entry in entries if isinstance(entries, list) else []:
            clean, found = _screen(str(entry))
            redactions += len(found)
            if clean and clean not in out:
                out.append(clean)
        return out

    read_paths = _screened_paths("read_paths")
    wrote_paths = _screened_paths("wrote_paths")
    record["tool_calls"] = rows
    record["read_paths"] = read_paths
    record["wrote_paths"] = wrote_paths
    # Attribution is derived FROM the paths, so it must be re-derived from the overlaid
    # ones — otherwise an imported session reads a skill and nothing says so.
    record["read_skills"] = attribute_skills(read_paths)
    record["wrote_skills"] = attribute_skills(wrote_paths)
    record["redactions"] = int(record.get("redactions") or 0) + redactions
    # Re-hash over the overlaid content, and keep the sidecar's join key in step with it.
    record["record_hash"] = _hash_record(record)
    sidecar["record_hash"] = record["record_hash"]


def stage_records(records: list[dict], *, source: str) -> dict:
    """Normalise already-shaped records into capture sessions (§8 telemetry import).

    Runs the identical redact→fence→persist pipeline as the live proxy, so an imported
    transcript carries exactly the same guarantees as a proxied one — a second,
    laxer path for imported content would be the whole security argument undone.

    Idempotent by content hash: re-importing a file is a no-op. Malformed records are
    skipped and counted, never fatal, so one bad line cannot lose a good import.
    """
    imported = 0
    skipped = 0
    reasons: list[str] = []

    def _skip(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        if reason not in reasons:
            reasons.append(reason)

    if not isinstance(records, list):
        return {"imported": 0, "skipped": 0, "reasons": ["records was not a list"]}

    seen: dict[str, set[str]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            _skip("record was not an object")
            continue
        request_body: dict
        response_body: dict | None
        # `overlay` is set only for the bodiless §7.2 shape; see below.
        overlay: dict | None = None
        raw_request = raw.get("request_body")
        if isinstance(raw_request, dict):
            request_body = raw_request
            raw_response = raw.get("response_body")
            response_body = raw_response if isinstance(raw_response, dict) else None
        else:
            # The §8 adapters emit the §7.2 RECORD shape, not a transcript: digests, tool
            # calls and paths, with no bodies — an SSE dump structurally cannot supply a
            # request half at all. Synthesise the minimal bodies so `_build_record` stays
            # the ONE place that shapes and screens a record; a second, laxer path for
            # imported content would be the whole security argument undone.
            prompt = str(raw.get("prompt_digest") or "")
            response = str(raw.get("response_digest") or "")
            if not prompt and not response:
                _skip(
                    "record had neither a request_body object nor a "
                    "prompt_digest/response_digest to synthesise one from"
                )
                continue
            # No prompt ⇒ NO message, not an empty one: an SSE import legitimately has no
            # request half, and a fabricated empty user turn would be a lie in the record.
            request_body = {"messages": [{"role": "user", "content": prompt}]} if prompt else {}
            response_body = {"choices": [{"message": {"content": response}}]} if response else None
            overlay = raw
        client_id = str(raw.get("client_id") or f"import:{source}")
        try:
            session_id = session_id_for(client_id, request_body)
            record, sidecar = _build_record(
                client_id=client_id,
                dialect=str(raw.get("dialect") or "import"),
                model_requested=str(raw.get("model_requested") or ""),
                request_body=request_body,
                response_body=response_body,
                stream_text=str(raw.get("stream_text") or ""),
                tokens=raw.get("tokens") if isinstance(raw.get("tokens"), dict) else None,
                latency_ms=int(raw.get("latency_ms") or 0),
            )
            if overlay is not None:
                _overlay_imported_facts(record, sidecar, overlay)
            record["import_source"] = str(source)
            record_path, sidecar_path = _session_paths(session_id)
            if session_id not in seen:
                seen[session_id] = _record_hashes(record_path)
            if record["record_hash"] in seen[session_id]:
                _skip("duplicate record (already imported)")
                continue
            _append(record_path, record)
            _append(sidecar_path, sidecar)
            seen[session_id].add(str(record["record_hash"]))
            imported += 1
        except Exception as exc:
            _skip(f"record could not be normalised: {type(exc).__name__}")
            logger.debug("capture: import record skipped", exc_info=True)

    return {"imported": imported, "skipped": skipped, "reasons": reasons}


def prune(retention_days: int | None = None) -> int:
    """Delete capture files older than the retention window. Returns files removed.

    Called from the curator tick. ``retention_days=0`` means **never prune** (see
    ``_RETENTION_NEVER``) — the reading that cannot silently destroy data.
    """
    if retention_days is None:
        try:
            from personalclaw.config.loader import AppConfig

            retention_days = int(AppConfig.load().external_access.capture.retention_days)
        except Exception:
            logger.debug("capture: retention unreadable; skipping prune", exc_info=True)
            # Fail toward KEEPING data: an unreadable config must not trigger deletion.
            return 0
    if retention_days <= _RETENTION_NEVER:
        return 0

    cutoff = time.time() - (retention_days * 86400)
    removed = 0
    root = capture_dir()
    try:
        candidates = sorted(root.glob("*.jsonl"))
    except OSError:  # pragma: no cover
        return 0
    for path in candidates:
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover
            logger.debug("capture: could not prune %s", path, exc_info=True)
    return removed


__all__ = [
    "attribute_skills",
    "capture_dir",
    "conversation_fingerprint",
    "prune",
    "record_turn",
    "record_turn_async",
    "session_id_for",
    "skill_path_map",
    "stage_records",
]
