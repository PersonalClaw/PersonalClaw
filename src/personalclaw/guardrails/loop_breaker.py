"""Runtime-agnostic tool-loop breaker (ACP-AGENT-PARITY §2.3, gap 5).

The native in-process runtime has counted its own tool failures since the first
release: repeated identical failures warn the model, then refuse the call, and a
turn drowning in failures aborts. That logic lived as a private class inside
``agents/native/runtime.py``, so an ACP session — where the CLI runs the tools and
the host only *observes* the neutral event stream — got none of it. `G6` measured
the consequence: six consecutive failing tool calls in one ACP turn produced no
warn, no block and no circuit trip. An unattended ACP loop could burn its whole
budget re-running the same broken call.

This module is that logic with the runtime taken out of it. It counts over a
stream of ``(tool, params, ok, result)`` observations and returns verdicts; it
never touches a provider, a session or a message list. Both runtimes consume it,
so the thresholds and the *wording* of every notice are defined once:

* **failure path** — :meth:`LoopBreaker.record` counts consecutive failures per
  ``(tool, params)`` key. :data:`WARN_THRESHOLD` warns, :data:`BLOCK_THRESHOLD`
  refuses further identical calls, and :data:`CIRCUIT_THRESHOLD` total failures
  in one run abort it.
* **structural path** — :meth:`LoopBreaker.record_structural` catches
  stuck-but-*successful* repetition (the same ``(tool, params, result_digest)``
  triple N× in a row, or an A↔B ping-pong) that the failure path cannot see
  because nothing failed. Warn-only, deliberately: looping is higher-variance
  than failure counting.

**The honest boundary between the two consumers.** The native runtime owns
dispatch, so it can enforce the BLOCK rung *before* the tool runs. The ACP host
sees a tool call only as protocol frames the CLI has already acted on, so it can
warn and it can abort the turn between frames — it cannot un-run a call. That
asymmetry is real and is stated in the parity doc rather than papered over with a
pre-block the host has no seam to perform.
"""

from __future__ import annotations

import json
import re
from collections import deque

# ── Thresholds (graduated verdicts over one run) ─────────────────────────────
#: ≥ this many identical failures → warn the model, still allow the call.
WARN_THRESHOLD = 3
#: ≥ this many → refuse further identical calls this run (native: pre-execution;
#: ACP: a user-visible notice, since the host cannot un-run the CLI's call).
BLOCK_THRESHOLD = 5
#: > this many total failures in one run → abort the whole run.
CIRCUIT_THRESHOLD = 30

# ── Structural (no-progress) detection ──────────────────────────────────────
#: Recent call signatures kept for pattern matching.
STRUCT_WINDOW = 16
#: ≥ this many identical triples in a row → no-progress.
STRUCT_REPEAT = 3
#: ≥ this many A↔B cycles (2× entries) → ping-pong.
STRUCT_PINGPONG_CYCLES = 3


def params_key(tool_name: str, args: object) -> str:
    """Stable ``(tool, params)`` identity for breaker bucketing.

    Same tool + same args = same bucket, so repeated *identical* failing calls
    accumulate while genuinely different calls stay independent. Falls back to the
    tool name alone if args aren't JSON-serializable — which is also the ACP shape,
    where ``tool_input`` arrives as an opaque string rather than a dict.
    """
    try:
        return f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    except (TypeError, ValueError):
        return str(tool_name)


# Volatile substrings that make two otherwise-identical results look different —
# timestamps, pids, durations, hex/uuid ids, memory addresses. Normalized out of
# the result digest so a call producing the "same" result each time is recognized
# as no-progress (result normalization). Order-independent.
_VOLATILE_PATTERNS = [
    re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    ),  # ISO ts
    re.compile(r"\b\d{10,13}\b"),  # epoch (s / ms)
    re.compile(r"0x[0-9a-fA-F]+"),  # hex / memory address
    re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    ),  # uuid
    re.compile(r"\b(?:pid|PID)[=: ]\s*\d+"),  # pid=NNN
    re.compile(r"\bin \d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|m|min)\b"),  # "in 1.23s"
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|µs|us)\b"),  # bare durations
]


def result_digest(result_str: str) -> str:
    """A normalized fingerprint of a tool result for structural loop detection.

    Strips volatile fields (timestamps / pids / durations / ids / addresses) so two
    runs of the *same* call that differ only in those don't look like progress, and
    bounds length so a huge identical output is cheap to compare. NOT used for the
    failure path — only the ``(tool, params, result_digest)`` structural triple.
    """
    s = result_str or ""
    for pat in _VOLATILE_PATTERNS:
        s = pat.sub("·", s)
    s = " ".join(s.split())  # collapse whitespace
    if len(s) > 512:
        s = s[:256] + "…" + s[-256:]
    return s


# ── The standard notices (ONE wording for every runtime) ─────────────────────


def warn_note(tool_name: str, streak: int) -> str:
    """The WARN rung, appended to the tool's own result text."""
    return (
        f"\n[note: this is failure #{streak} of `{tool_name}` with these "
        "arguments this run — stop repeating it and change approach.]"
    )


def blocked_message(tool_name: str, count: int) -> str:
    """The BLOCK rung: the result text a refused call is given instead of running.

    The ACP host cannot substitute a result (the CLI already ran the tool), so it
    surfaces this same text as a steering notice — same words, different seam.
    """
    return (
        f"Error: tool `{tool_name}` was blocked — it has already failed "
        f"{count} times this run with these same "
        "arguments. Do NOT call it this way again; change your approach or "
        "stop and explain what's blocking you."
    )


def structural_note(reason: str) -> str:
    """The structural (no-progress / ping-pong) observation, warn-only."""
    return (
        f"\n[note: {reason}. You appear to be looping without "
        "making progress — stop repeating this and change approach, or "
        "stop and report what's blocking you.]"
    )


def circuit_message(total_failures: int) -> str:
    """The CIRCUIT rung: the run is aborted, and this says why.

    One string for both runtimes so "the standard breaker message" means the same
    sentence whether the tools ran in-process or inside an ACP CLI.
    """
    return (
        f"Run aborted by the loop breaker: {total_failures} tool failures in this "
        "turn. The run was repeating failures instead of making progress, so it was "
        "stopped rather than allowed to burn the rest of its budget."
    )


class LoopBreaker:
    """Per-run progress tracker with graduated verdicts.

    Two parallel paths over the same call stream:

    * **failure path** — :meth:`record` counts consecutive *failures* per
      ``(tool, params)`` key; :meth:`count` drives the BLOCK/WARN rungs and
      :attr:`total_failures` the run-wide circuit breaker. A success clears the key.
    * **structural path** — :meth:`record_structural` tracks recent
      ``(tool, params, result_digest)`` triples to catch stuck-but-*successful*
      repetition: the same triple N× in a row (no-progress), or an A↔B↔A↔B
      alternation (ping-pong). Returns a reason string on detection, else "".
      Warn-only: the consumer injects an observation; it does not block.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self.total_failures = 0
        # Recent structural signatures (most-recent last), bounded to the window.
        self._recent: deque[str] = deque(maxlen=STRUCT_WINDOW)
        # Reasons already reported this run, so we warn once per distinct loop and
        # don't re-inject the same observation every subsequent identical call.
        self._struct_reported: set[str] = set()

    def reset(self) -> None:
        self._counts.clear()
        self.total_failures = 0
        self._recent.clear()
        self._struct_reported.clear()

    def reset_structural(self) -> None:
        """Re-arm structural detection (after a compaction) without touching the
        failure counts — a loop that resumes identically post-compaction should be
        caught fresh (post-compaction guard)."""
        self._recent.clear()
        self._struct_reported.clear()

    def record(self, key: str, failed: bool) -> int:
        if failed:
            self.total_failures += 1
            self._counts[key] = self._counts.get(key, 0) + 1
        else:
            self._counts.pop(key, None)  # a success clears this key's streak
        return self._counts.get(key, 0)

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)

    def circuit_tripped(self) -> bool:
        """True once this run's total failures exceed :data:`CIRCUIT_THRESHOLD`."""
        return self.total_failures > CIRCUIT_THRESHOLD

    def record_structural(self, sig: str) -> str:
        """Record a ``(tool, params, result_digest)`` signature; return a reason
        string when a structural loop is newly detected this run, else ``""``.

        Detects (a) no-progress: the same signature :data:`STRUCT_REPEAT` times in a
        row; (b) ping-pong: an A↔B alternation spanning
        :data:`STRUCT_PINGPONG_CYCLES` cycles. Each distinct loop is reported once
        (dedup via ``_struct_reported``) so the warning fires on the turn the loop
        becomes evident, not every call.
        """
        self._recent.append(sig)
        recent = list(self._recent)

        # (a) no-progress: identical signature repeated at the tail.
        tail = recent[-STRUCT_REPEAT:]
        if len(tail) == STRUCT_REPEAT and len(set(tail)) == 1:
            reason = f"no-progress:{sig}"
            if reason not in self._struct_reported:
                self._struct_reported.add(reason)
                return (
                    f"the same tool call produced the same result "
                    f"{STRUCT_REPEAT} times in a row"
                )

        # (b) ping-pong: A,B,A,B,… alternation at the tail spanning the cycle count.
        span = STRUCT_PINGPONG_CYCLES * 2
        tailp = recent[-span:]
        if len(tailp) == span:
            a, b = tailp[0], tailp[1]
            if a != b and all(tailp[i] == (a if i % 2 == 0 else b) for i in range(span)):
                reason = f"ping-pong:{a}|{b}"
                if reason not in self._struct_reported:
                    self._struct_reported.add(reason)
                    return (
                        f"two tool calls are alternating without making progress "
                        f"({STRUCT_PINGPONG_CYCLES}× A↔B with no new state)"
                    )
        return ""
