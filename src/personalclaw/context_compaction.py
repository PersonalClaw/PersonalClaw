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
- **Tool-pair integrity:** never leave an orphaned tool-result (a tool message
  whose matching assistant tool_call was dropped) — they break the provider.
- **Anchoring:** the latest user + assistant messages always survive in the tail.
- **Prefix guard:** the summary is fenced "[CONTEXT COMPACTION — REFERENCE ONLY]"
  so only the live tail wins over stale state.
- **No-LLM safe:** ``summarize_fn=None`` produces a structured deterministic
  digest, so compaction always works (and is testable) without a model call.
- **The resume account survives (CE2-10):** a compacted session carries an explicit
  structured account of what already happened, DERIVED from the folded region's own
  tool_calls/tool-result pairs rather than left to the summary. A block already in the
  history is carried through VERBATIM (its facts came from messages that are already
  gone, so re-deriving it is impossible), and a fresh one is derived for the region this
  pass is about to fold. Both sit immediately before the verbatim tail — beside the live
  task, never in place of it: ``protect_tail`` is untouched, so the account cannot evict
  the messages the turn is actually acting on. Two blocks is the ceiling, so the carried
  weight is bounded at ``2 × MAX_ACCOUNT_CHARS``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

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
# no-double-loss rule, plan OP4).
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')


def prune_tool_outputs(messages: list[dict]) -> list[dict]:
    """No-LLM pre-pass: shrink large, non-recent tool results to one-liners.

    Keeps the most recent ``_KEEP_RECENT_TOOL_RESULTS`` tool results full (the
    agent is likely still acting on them); older verbose ones collapse to a
    digest that preserves the shape ("… → 47 lines, 612 chars"). Identical
    consecutive results dedupe. Returns a new list; never drops a message (that
    would break tool-pairing) — only shrinks content.

    A digested result that carried a projection's ``result_id`` keeps that id in the
    digest, so ``tool_result_get`` still recovers the raw AFTER compaction (OP4 — no
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


def _structured_digest(middle: list[dict], files: list[str]) -> str:
    """Deterministic (no-LLM) fallback summary of the compacted middle.

    Not a replacement for an LLM summary, but a structured, lossless-on-intent
    digest so compaction always works without a model. A ``summarize_fn`` (LLM)
    overrides this when available.
    """
    user_msgs = [str(m.get("content", "")) for m in middle if m.get("role") == "user"]
    tool_names: list[str] = []
    for m in middle:
        for tc in m.get("tool_calls", []) or []:
            n = tc.get("function", {}).get("name", "")
            if n:
                tool_names.append(n)
    parts = ["## Earlier conversation (compacted)"]
    if user_msgs:
        parts.append("### Requests made\n" + "\n".join(f"- {u[:200]}" for u in user_msgs[:10]))
    if tool_names:
        from collections import Counter

        counts = Counter(tool_names)
        parts.append("### Tools used\n" + ", ".join(f"{n}×{c}" for n, c in counts.most_common(12)))
    if files:
        parts.append("### Relevant Files\n" + "\n".join(f"- {f}" for f in files))
    return "\n\n".join(parts)


def should_compact(saves: list[float]) -> bool:
    """Anti-thrashing: False if the last 2 compactions each saved <10%."""
    if len(saves) >= 2 and all(s < _MIN_SAVE_FRACTION for s in saves[-2:]):
        return False
    return True


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
    """True for a message carrying a CE2-10 resume account.

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


def compact(
    messages: list[dict],
    *,
    summarize_fn: Callable[[list[dict]], str] | None = None,
    protect_head: int = _PROTECT_HEAD,
    protect_tail: int = _PROTECT_TAIL,
) -> list[dict]:
    """Compact ``messages`` to head + structured-summary + recent tail.

    Always runs the tool-output pruning pre-pass first. If after pruning there's
    no meaningful middle to summarize (short conversation), returns the pruned
    list unchanged. Otherwise: keep the head verbatim, fold the middle into one
    fenced summary message (LLM via ``summarize_fn`` or the deterministic
    digest), keep the tail verbatim, and drop any orphaned tool results.
    """
    pruned = prune_tool_outputs(messages)
    n = len(pruned)
    if n <= protect_head + protect_tail:
        return pruned  # nothing to summarize — the pre-pass is the whole win

    head = pruned[:protect_head]
    tail = pruned[-protect_tail:]
    middle = pruned[protect_head : n - protect_tail]
    if not middle:
        return pruned

    files = extract_file_refs(pruned)
    body = summarize_fn(middle) if summarize_fn else _structured_digest(middle, files)
    summary_msg = {
        "role": "user",
        "content": (
            "[CONTEXT COMPACTION — REFERENCE ONLY. The earlier conversation was "
            "compacted to save context. This is a record of what happened, NOT a "
            "task to resume verbatim — act on the latest message below.]\n\n"
            + body
            + "\n[END CONTEXT COMPACTION]"
        ),
    }
    # CE2-10. Two distinct jobs, in this order:
    #  1. CARRY an account already in the history verbatim. Its facts came from messages this
    #     pass (or an earlier one) has already folded away, so it cannot be re-derived — the most
    #     recent one wins, which bounds the carried weight at one block.
    #  2. DERIVE one for the region being folded NOW, from the ORIGINAL slice rather than the
    #     pruned one (see `_derive_account_for`).
    # Both land between the summary and the tail: adjacent to the live task, never displacing it.
    # Sliced to the MIDDLE only: a block already inside head or tail survives verbatim on its own,
    # and re-inserting it would put the same account in the history twice.
    folding = messages[protect_head : n - protect_tail]
    carried = [m for m in folding if is_resume_account(m)]
    accounts: list[dict] = [carried[-1]] if carried else []
    fresh = _derive_account_for([m for m in folding if not is_resume_account(m)])
    if fresh:
        accounts.append(_account_message(fresh))
    result = head + [summary_msg] + accounts + tail
    return _drop_orphan_tool_results(result)
