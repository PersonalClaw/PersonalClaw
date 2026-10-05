"""Structured context compaction for the native agent loop (the compact hook).

The native loop owns its history (``runtime._messages``), so PClaw owns its
compaction — a far richer pass than head/tail truncation, implemented as a
``ContextCompressor``. Order matters: the cheap **no-LLM tool-output pruning
pre-pass** runs FIRST (it often reclaims enough on its own from verbose shell /
file / search output), THEN a 4-region structure protects the head + recent tail
and folds the middle into a structured summary that preserves *intent + which
files matter* — not a vague blob.

Message shapes (native loop):
- ``{"role": "user"|"assistant", "content": str, "tool_calls"?: [...]}}``
- ``{"role": "tool", "tool_call_id": str, "content": str}``

Design properties:
- **Anti-thrashing:** skip if the last 2 compactions each saved <10% (no infinite
  re-compaction).
- **Tool-pair integrity:** the protected head and tail hold WHOLE exchanges — a call and
  every result it got (:func:`_whole_exchanges`). Cut between them, the tail's results were
  dropped as orphans, a call kept in the head lost its results (a provider then answers it
  as interrupted), and the account of the folded part said the call had no result: an
  agent that had cited a release time read that its release lookup never returned, and
  disowned the time. An orphan already in the history is still dropped — it breaks the
  provider.
- **The request survives:** the user message the running turn answers (``request``, else the
  latest message the user wrote) and every message the user wrote after it, a steer included, are
  kept word for word wherever they sit, and so is the runtime's note for the turn (a volatile
  message: its tool catalog). The tail reaches back only eight messages, so a turn's own rounds
  pushed its request out of it after four tool rounds, and the summary then kept the request's
  first line, or nothing. Kept messages sit right after the summary, in their order, and the most
  recent messages are in the tail.
- **Prefix guard:** the summary is fenced "[CONTEXT COMPACTION — REFERENCE ONLY]" so only the live
  conversation wins over stale state, and its opening says where the user's request is.
- **The summary keeps what the user asked:** each request it folds is listed in the user's own
  words, the newest word for word while :data:`_REQUESTS_WORD_FOR_WORD_CHARS` lasts and older ones
  by their first line while :data:`_REQUESTS_FIRST_LINE_CHARS` lasts, and the ones past both are
  counted: bounded by size, never by a count, and never dropped without a word. A pass that folds
  an earlier summary carries its requests and tools forward rather than reading the summary as
  one more request, so a second pass keeps what the first one kept.
- **No-LLM safe:** ``summarize_fn=None`` produces a structured deterministic
  digest, so compaction always works (and is testable) without a model call.
- **The resume account survives:** a compacted session carries an explicit
  structured account of what already happened, DERIVED from the folded region's own
  tool_calls/tool-result pairs rather than left to the summary. A block already in the
  history is carried through VERBATIM (its facts came from messages that are already
  gone, so re-deriving it is impossible), and a fresh one is derived for the region this
  pass is about to fold. Both sit right after the summary, ahead of the kept messages and the
  verbatim tail — beside the live task, never in place of it: nothing kept is shortened, so the
  account cannot evict the messages the turn is actually acting on. Two blocks is the ceiling,
  so the carried weight is bounded at ``2 × MAX_ACCOUNT_CHARS``.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tool results older than the most-recent few, longer than this, are pruned to a
# one-line digest in the pre-pass.
_TOOL_RESULT_PRUNE_OVER = 600
_KEEP_RECENT_TOOL_RESULTS = 4
# 4-region protection.
_PROTECT_HEAD = 3  # system + first messages stay verbatim
_PROTECT_TAIL = 8  # floor of recent messages kept verbatim
# Anti-thrashing: a compaction that saved less than this fraction "didn't help".
_MIN_SAVE_FRACTION = 0.10
# Paths in tool-call args worth surfacing as "Relevant Files".
_FILE_ARG_KEYS = ("path", "file_path", "workdir", "output_path", "cwd")
_FILE_RE = re.compile(r"(?:[\w./~-]+/)?[\w.-]+\.[A-Za-z0-9]{1,8}")
# What the summary keeps of the requests it folds, in characters of the user's own words: the
# newest word for word while the first budget lasts, each older one by its first line (cut at
# `_FIRST_LINE_CHARS`) while the second lasts, and a count of the rest. Sized beside the resume
# account's bound (`resume_account.MAX_ACCOUNT_CHARS`): a record that could grow to the window would
# evict the turn it exists to serve.
_REQUESTS_WORD_FOR_WORD_CHARS = 4000
_REQUESTS_FIRST_LINE_CHARS = 1200
_FIRST_LINE_CHARS = 120
# The record's fence and its sections. A later pass reads its own record back by these, so the
# writer and the reader are one set of constants.
_FENCE_OPEN = "[CONTEXT COMPACTION — REFERENCE ONLY."
_FENCE_CLOSE = "[END CONTEXT COMPACTION]"
_DIGEST_HEADING = "## Earlier conversation (compacted)"
_REQUESTS_HEADING = "### Requests made, oldest first"
# A prompt split at its paragraphs (``sections=True``) lists what it folds under this one instead.
_SECTIONS_HEADING = "### Sections folded, oldest first"
_TOOLS_HEADING = "### Tools used"
_FILES_HEADING = "### Relevant Files"
_UNLISTED_RE = re.compile(r"^(\d+) earlier requests? (?:is|are) not listed here\.$")
_TOOL_COUNT_RE = re.compile(r"([^\s,×]+)×(\d+)")


def _msg_len(m: dict) -> int:
    n = len(str(m.get("content", "")))
    for tc in m.get("tool_calls", []) or []:
        n += len(str(tc.get("function", {}).get("arguments", "")))
    return n


def total_chars(messages: list[dict]) -> int:
    return sum(_msg_len(m) for m in messages)


# A projected tool result names its retrieval affordance in-content:
# ``tool_result_get(result_id="r_…")`` (see builtin_tools projection wiring). Compaction
# must carry that id into the digest so a projected result stays RECOVERABLE after it's
# pruned — otherwise the raw_ref is lost and tool_result_get can never reach it (the
# no-double-loss rule).
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')


def prune_tool_outputs(messages: list[dict]) -> list[dict]:
    """No-LLM pre-pass: shrink large, non-recent tool results to one-liners.

    Keeps the most recent ``_KEEP_RECENT_TOOL_RESULTS`` tool results full (the
    agent is likely still acting on them); older verbose ones collapse to a
    digest that preserves the shape ("… → 47 lines, 612 chars"). Identical
    consecutive results dedupe. Returns a new list; never drops a message (that
    would break tool-pairing) — only shrinks content.

    A digested result that carried a projection's ``result_id`` keeps that id in the
    digest, so ``tool_result_get`` still recovers the raw AFTER compaction (no
    double-loss: projection defers at dispatch, compaction must not turn that into a
    permanent loss by dropping the recovery handle).
    """
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    keep_full = set(tool_idxs[-_KEEP_RECENT_TOOL_RESULTS:])
    out: list[dict] = []
    last_digest: str | None = None
    for i, m in enumerate(messages):
        if m.get("role") != "tool" or i in keep_full:
            out.append(m)
            last_digest = None
            continue
        content = str(m.get("content", ""))
        if len(content) <= _TOOL_RESULT_PRUNE_OVER:
            out.append(m)
            last_digest = None
            continue
        lines = content.count("\n") + 1
        digest = f"[pruned tool result — {lines} lines, {len(content)} chars]"
        if digest == last_digest:
            digest = "[pruned tool result — identical to previous]"
        # Preserve the retrieval handle if this result was a projection with a raw_ref,
        # so the dropped raw stays reachable via tool_result_get after pruning.
        rid = _RESULT_ID_RE.search(content)
        if rid:
            digest += f' full result: tool_result_get(result_id="{rid.group(1)}")'
        out.append({**m, "content": digest})
        last_digest = digest
    return out


def extract_file_refs(messages: list[dict], limit: int = 25) -> list[str]:
    """Files in play across the conversation — from tool-call args + content.

    Compaction must never lose *which files matter*; this feeds the summary's
    Relevant Files section. Deduped, capped, insertion-ordered.
    """
    seen: dict[str, None] = {}
    for m in messages:
        for tc in m.get("tool_calls", []) or []:
            args = tc.get("function", {}).get("arguments", "")
            if isinstance(args, str):
                import json

                try:
                    parsed = json.loads(args)
                except (ValueError, TypeError):
                    parsed = {}
            else:
                parsed = args if isinstance(args, dict) else {}
            for k in _FILE_ARG_KEYS:
                v = parsed.get(k)
                if isinstance(v, str) and v.strip():
                    seen.setdefault(v.strip(), None)
        for match in _FILE_RE.findall(str(m.get("content", ""))):
            seen.setdefault(match, None)
            if len(seen) >= limit * 2:
                break
    return list(seen)[:limit]


@dataclass(frozen=True)
class _Asked:
    """One request as the summary lists it: whole, or already cut to its first line by an earlier
    pass (a cut line is never listed again as if it were whole)."""

    text: str
    whole: bool = True


def _is_record(message: dict) -> bool:
    """A summary this module wrote: in the user's role, fenced from its first line to its last."""
    content = str(message.get("content", ""))
    return (
        message.get("role") == "user"
        and content.startswith(_FENCE_OPEN)
        and content.endswith(_FENCE_CLOSE)
    )


def _is_runtime_note(message: dict) -> bool:
    """The runtime's note for the turn (its tool catalog among it): the loop marks it volatile and
    replaces it every turn, so no pass folds it while it stands."""
    from personalclaw.llm.prompt_cache import VOLATILE_KEY

    return bool(message.get(VOLATILE_KEY))


def _is_the_users(message: dict) -> bool:
    """A message the user wrote: a turn's request, a steer, a room's human line. Not a record this
    module or the resume account wrote in the user's role, and not a runtime note."""
    return (
        message.get("role") == "user"
        and not _is_record(message)
        and not is_resume_account(message)
        and not _is_runtime_note(message)
    )


def _users_words(message: dict) -> str:
    """What the user asked in *message*: an assembled turn's request (``context.user_request``),
    else the whole text. Deferred import: the assembly module is heavy, and only a fold reads it."""
    from personalclaw.context import user_request

    return user_request(str(message.get("content", "")))


def _first_line(text: str) -> str:
    """*text*'s first non-blank line, cut at :data:`_FIRST_LINE_CHARS`, and marked when anything
    of *text* is left out."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    first = lines[0] if lines else ""
    if len(first) > _FIRST_LINE_CHARS or len(lines) > 1:
        return first[:_FIRST_LINE_CHARS].rstrip() + " [...]"
    return first


def _quoted(text: str) -> str:
    """*text* word for word, each line behind ``>``: by that the next pass reads it back whole."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _requests_section(asked: list[_Asked], unlisted: int, *, sections: bool = False) -> str:
    """The requests a summary folds, oldest first, and ``""`` when there are none.

    Walked from the newest: each is kept word for word while that budget lasts, then each older one
    by its first line while that one lasts, and the rest are counted beside the *unlisted* an
    earlier record counted. The walk only moves on to the next way of listing, never back, so what
    is kept whole is always the newest. *sections* lists a split prompt's folded paragraphs instead,
    each by its first line only: they are the bulk the fold exists to shed, not requests.
    """
    whole_left = 0 if sections else _REQUESTS_WORD_FOR_WORD_CHARS
    line_left = _REQUESTS_FIRST_LINE_CHARS
    whole: list[str] = []
    lines: list[str] = []
    stage = 0  # 0 word for word, 1 by first line, 2 counted
    for item in reversed(asked):
        if stage == 0 and item.whole and len(item.text) <= whole_left:
            whole.append(item.text)
            whole_left -= len(item.text)
            continue
        stage = max(stage, 1)
        line = _first_line(item.text) if item.whole else item.text
        if stage == 1 and len(line) <= line_left:
            lines.append(line)
            line_left -= len(line)
            continue
        stage = 2
        unlisted += 1
    if not (whole or lines or unlisted):
        return ""
    heading, noun = (_SECTIONS_HEADING, "section") if sections else (_REQUESTS_HEADING, "request")
    parts = [heading]
    if unlisted:
        parts.append(
            f"{unlisted} earlier {noun}{' is' if unlisted == 1 else 's are'} not listed here."
        )
    if lines:
        parts.append("By their first line:\n" + "\n".join(f"- {ln}" for ln in reversed(lines)))
    if whole:
        parts.append("Word for word:\n" + "\n\n".join(_quoted(text) for text in reversed(whole)))
    return "\n\n".join(parts)


def _read_record(content: str) -> tuple[list[_Asked], int, Counter[str]]:
    """What an earlier record listed: its requests oldest first, how many it counted without
    listing them, and its tool counts — the inverse of :func:`_requests_section` and the tools line.
    Read by line prefix: every line of a word-for-word request starts with ``>``, so nothing the
    user wrote can be taken for a heading, a first line or a count."""
    asked: list[_Asked] = []
    unlisted = 0
    tools: Counter[str] = Counter()
    section = ""
    quote: list[str] | None = None
    for line in content.split("\n"):
        if section == _REQUESTS_HEADING and line.startswith(">"):
            quote = [] if quote is None else quote
            quote.append(line[2:] if line.startswith("> ") else line[1:])
            continue
        if quote is not None:
            asked.append(_Asked("\n".join(quote)))
            quote = None
        if line.startswith("### "):
            section = line
        elif section == _REQUESTS_HEADING and line.startswith("- "):
            asked.append(_Asked(line[2:], whole=False))
        elif section == _REQUESTS_HEADING and (gone := _UNLISTED_RE.match(line)):
            unlisted += int(gone.group(1))
        elif section == _TOOLS_HEADING:
            tools.update({name: int(count) for name, count in _TOOL_COUNT_RE.findall(line)})
    if quote is not None:
        asked.append(_Asked("\n".join(quote)))
    return asked, unlisted, tools


def _structured_digest(middle: list[dict], files: list[str], *, sections: bool = False) -> str:
    """Deterministic (no-LLM) fallback summary of the compacted middle.

    Not a replacement for an LLM summary, but a structured digest that keeps what the user asked
    (:func:`_requests_section`), which tools ran and which files matter, so compaction always works
    without a model. An earlier record in *middle* is read back, not listed: its requests and tools
    carry forward ahead of what was asked after it. A ``summarize_fn`` (LLM) overrides this when
    available. *sections*: *middle* is paragraphs of one prompt (see :func:`compact`).
    """
    asked: list[_Asked] = []
    unlisted = 0
    tools: Counter[str] = Counter()
    for m in middle:
        if _is_record(m):
            carried, counted, used = _read_record(str(m.get("content", "")))
            asked += carried
            unlisted += counted
            tools += used
        elif _is_the_users(m) and (words := _users_words(m)).strip():
            asked.append(_Asked(words))
        for tc in m.get("tool_calls", []) or []:
            n = tc.get("function", {}).get("name", "")
            if n:
                tools[n] += 1
    parts = ["## Earlier part of this prompt (compacted)" if sections else _DIGEST_HEADING]
    if requests := _requests_section(asked, unlisted, sections=sections):
        parts.append(requests)
    if tools:
        parts.append(
            _TOOLS_HEADING + "\n" + ", ".join(f"{n}×{c}" for n, c in tools.most_common(12))
        )
    if files:
        parts.append(_FILES_HEADING + "\n" + "\n".join(f"- {f}" for f in files))
    return "\n\n".join(parts)


def _fence(request_kept: bool, *, sections: bool = False) -> str:
    """The record's opening: what it is, and where the conversation it stands for goes on.

    Worded to stay true for as long as the record stays in the history, so it names no position:
    "the user's most recent request" is whichever is newest when the record is read, and that one
    is never folded. Without any message the user wrote (a room fed only other members' replies) it
    claims no request at all. A split prompt (*sections*) keeps its opening and its closing
    paragraphs, where the ladder that splits it puts the instruction.
    """
    if sections:
        return (
            f"{_FENCE_OPEN} Part of this prompt was compacted to save context: this is a record "
            "of what it held, not a task. The prompt's opening and its instruction at the end are "
            "kept in full outside it: act on that instruction.]"
        )
    where = (
        "The user's most recent request is kept in full outside it, with everything the user "
        "added after it: act on that request."
        if request_kept
        else "Continue from the most recent messages below."
    )
    return (
        f"{_FENCE_OPEN} Earlier parts of this conversation were compacted to save context: this "
        f"is a record of what happened, not a task to resume. {where}]"
    )


def should_compact(saves: list[float]) -> bool:
    """Anti-thrashing: False if the last 2 compactions each saved <10%."""
    if len(saves) >= 2 and all(s < _MIN_SAVE_FRACTION for s in saves[-2:]):
        return False
    return True


def autocompact_pct() -> float:
    """The Settings "Auto-compact threshold", ``session.autocompact_pct``, as config.json reads now.

    The ONE compaction threshold. The native loop compacts its own history when the context
    crosses it, and the session manager restarts, at the same value, a runtime that cannot
    compact itself. The loop used to carry its own fixed 70% and the manager read the value it
    was started with, so the setting governed neither the way Settings said it did.

    Read on every call, so a change in Settings applies to an open chat at its next turn.
    Deferred import: the native loop calls this, and its package keeps a config-free import
    surface. A config that cannot be read reads as the field's default.
    """
    from personalclaw.config.loader import AppConfig, SessionConfig

    try:
        return float(AppConfig.load().session.autocompact_pct)
    except Exception:
        logger.debug("auto-compact threshold unreadable, using the default", exc_info=True)
        return SessionConfig().autocompact_pct


def _drop_orphan_tool_results(messages: list[dict]) -> list[dict]:
    """Remove tool messages whose matching assistant tool_call isn't present.

    A tool_result with no preceding tool_call breaks the provider; after slicing
    out a middle region we may orphan some, so prune them.
    """
    live_call_ids: set[str] = set()
    for m in messages:
        for tc in m.get("tool_calls", []) or []:
            if tc.get("id"):
                live_call_ids.add(str(tc["id"]))
    out = []
    for m in messages:
        if m.get("role") == "tool" and str(m.get("tool_call_id", "")) not in live_call_ids:
            continue  # orphaned result — drop
        out.append(m)
    return out


def is_resume_account(msg: dict) -> bool:
    """True for a message carrying a resume account.

    Keyed on the fence constant the account module owns, not a literal copied here: two copies of
    the fence would let the writer and the carrier drift, and the failure mode is silent — the
    account would be folded into the summary exactly as if this rule did not exist.
    """
    from personalclaw.resume_account import FENCE_START

    return FENCE_START in str(msg.get("content", ""))


def _account_message(body: str) -> dict:
    """The account as a history message. ``role: user`` matches the summary message's role — the
    provider must accept it in any position, and an assistant-authored record would read as
    something the model itself claimed rather than something the log recorded."""
    return {"role": "user", "content": body}


def _derive_account_for(folded: list[dict]) -> str:
    """The account of what the region about to be folded actually did, or ``""``.

    Derived from the ORIGINAL messages, never the pruned copy: ``prune_tool_outputs`` rewrites a
    long tool result to ``[pruned tool result — …]``, and that digest does not start with
    ``Error:``, so a FAILED call read from the pruned list would classify as done — a false
    completion manufactured by the compressor itself.

    ``ledger_events``/``checkpoint_entries`` are ``NOT_CONSULTED``: compaction runs on a message
    list and has no session key, so it cannot reach a run ledger or a checkpoint store. Saying so
    is the honest answer; passing ``[]`` would assert those stores recorded nothing.
    """
    try:
        from personalclaw.resume_account import NOT_CONSULTED, derive_account, render_account

        return render_account(
            derive_account(
                ledger_events=NOT_CONSULTED,
                tool_messages=folded,
                checkpoint_entries=NOT_CONSULTED,
            )
        )
    except Exception:
        # Compaction must never break a turn. No account is a forgotten completion; a raised
        # exception here is a dead session.
        logger.warning("resume account derivation skipped during compaction", exc_info=True)
        return ""


def _whole_exchanges(messages: list[dict], head_end: int, tail_start: int) -> tuple[int, int]:
    """Where the head ends and the tail starts so neither cuts a call from its results.

    An exchange is an assistant message's tool calls and every result answering them. One the
    head's edge crosses is kept in the head (its end moves past the exchange's last result), and
    one the tail's edge crosses is kept in the tail (its start moves back to the call). Both only
    grow, so the head and the tail keep at least what they protect, and moved edges are checked
    again until no exchange crosses either one.
    """
    call_at: dict[str, int] = {}
    last_result: dict[int, int] = {}
    for i, m in enumerate(messages):
        for call in m.get("tool_calls", []) or []:
            if call.get("id"):
                call_at[str(call["id"])] = i
        at = call_at.get(str(m.get("tool_call_id", ""))) if m.get("role") == "tool" else None
        if at is not None:
            last_result[at] = i
    moved = True
    while moved:
        moved = False
        for at, last in last_result.items():
            if at < head_end <= last:
                head_end, moved = last + 1, True
            if at < tail_start <= last:
                tail_start, moved = at, True
    return head_end, tail_start


def _live_from(messages: list[dict], request: dict | None) -> int:
    """Where the conversation the turn acts on starts: *request*'s index, found by identity, else
    the latest message the user wrote; ``len(messages)`` when the user wrote none."""
    if request is not None:
        for i, m in enumerate(messages):
            if m is request:
                return i
    return next(
        (i for i in range(len(messages) - 1, -1, -1) if _is_the_users(messages[i])),
        len(messages),
    )


def compact(
    messages: list[dict],
    *,
    request: dict | None = None,
    summarize_fn: Callable[[list[dict]], str] | None = None,
    protect_head: int = _PROTECT_HEAD,
    protect_tail: int = _PROTECT_TAIL,
    sections: bool = False,
) -> list[dict]:
    """Compact ``messages`` to head + structured-summary + what the turn needs + recent tail.

    Always runs the tool-output pruning pre-pass first. If after pruning there's
    no meaningful middle to summarize (short conversation), returns the pruned
    list unchanged. Otherwise: keep the head verbatim, fold the middle into one
    fenced summary message (LLM via ``summarize_fn`` or the deterministic
    digest), keep the tail verbatim, and drop any orphaned tool results. The head
    and the tail are at least ``protect_head`` and ``protect_tail`` messages, and
    longer where that keeps a call with its results (:func:`_whole_exchanges`).

    *request* is the user message the running turn answers, found by identity; with none, the
    latest message the user wrote stands for it. It, every message the user wrote after it and the
    runtime's note for the turn are never folded or shortened: one the tail does not reach stays,
    as the same object, right after the summary, in its order. ``summarize_fn`` is handed the
    middle without them, since they are kept word for word.

    *sections* says *messages* is one prompt split at its paragraphs (the workflow ladder), not a
    conversation: its last paragraph is the instruction the tail keeps, and the record lists the
    paragraphs it folds as sections, each by its first line, since they are not requests.
    """
    pruned = prune_tool_outputs(messages)
    n = len(pruned)
    if n <= protect_head + protect_tail:
        return pruned  # nothing to summarize — the pre-pass is the whole win

    head_end, tail_start = _whole_exchanges(pruned, protect_head, n - protect_tail)
    live = _live_from(messages, request)
    keep = {
        i
        for i in range(head_end, tail_start)
        if i == live or (i > live and _is_the_users(pruned[i])) or _is_runtime_note(pruned[i])
    }
    fold = [i for i in range(head_end, tail_start) if i not in keep]
    if not fold:
        return pruned  # whole exchanges and what the turn needs leave no middle to fold
    head = pruned[:head_end]
    tail = pruned[tail_start:]
    middle = [pruned[i] for i in fold]
    kept = [pruned[i] for i in sorted(keep)]

    files = extract_file_refs(pruned)
    body = (
        summarize_fn(middle)
        if summarize_fn
        else _structured_digest(middle, files, sections=sections)
    )
    opening = _fence(live < n, sections=sections)
    summary_msg = {"role": "user", "content": f"{opening}\n\n{body}\n{_FENCE_CLOSE}"}
    # Two distinct jobs, in this order:
    #  1. CARRY an account already in the history verbatim. Its facts came from messages this
    #     pass (or an earlier one) has already folded away, so it cannot be re-derived — the most
    #     recent one wins, which bounds the carried weight at one block.
    #  2. DERIVE one for the region being folded NOW, from the ORIGINAL slice rather than the
    #     pruned one (see `_derive_account_for`).
    # Both land between the summary and what is kept: adjacent to the live task, never displacing
    # it. Read from what folds only: a block already inside head or tail survives verbatim on its
    # own, and re-inserting it would put the same account in the history twice.
    folding = [messages[i] for i in fold]
    carried = [m for m in folding if is_resume_account(m)]
    accounts: list[dict] = [carried[-1]] if carried else []
    fresh = _derive_account_for([m for m in folding if not is_resume_account(m)])
    if fresh:
        accounts.append(_account_message(fresh))
    result = head + [summary_msg] + accounts + kept + tail
    return _drop_orphan_tool_results(result)
