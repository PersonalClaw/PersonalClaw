"""Runtime-agnostic tool-loop breaker (gap 5).

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
  refuses further identical calls, and :attr:`LoopBreaker.circuit_threshold` total
  failures in one run abort it — the one rung an operator can retune, through
  ``guardrails.loop_breaker.circuit_threshold`` (default :data:`CIRCUIT_THRESHOLD`).
* **structural path** — :meth:`LoopBreaker.record_structural` catches
  stuck-but-*successful* repetition that the failure path cannot see because nothing
  failed: the same call answering exactly what it answered last time. Any call repeating
  back to back is warned at :data:`STRUCT_REPEAT`. A READ (:func:`only_reads`) is followed
  wherever else in the turn its calls fell: warned at :data:`STRUCT_REPEAT`, refused before
  it runs again past :data:`REPEAT_BLOCK_THRESHOLD`, and a turn with more than
  :data:`REPEAT_CIRCUIT_THRESHOLD` such repeats is stopped. Only reads
  are refused and counted: a read answered the same a fourth time cannot tell the model
  anything, however the answer came about, while a call that acts — re-running the tests
  after an edit — is re-checking, and its unchanged answer is news. Measured: a local model
  ran 65 shell calls in one turn, the same few reads answered identically over and over but
  almost never twice in a row, and the old rule — N identical calls IN A ROW, warn-only —
  said nothing for 1.2M tokens. A rotation of two or three distinct calls (an A↔B
  ping-pong) is still reported, warn-only.

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

from personalclaw.task_modes import (
    SHELL_TOOL_NAMES,
    is_read_only_bash,
    reads_only,
    shell_command,
)

# ── Thresholds (graduated verdicts over one run) ─────────────────────────────
#: ≥ this many identical failures → warn the model, still allow the call.
WARN_THRESHOLD = 3
#: ≥ this many → refuse further identical calls this run (native: pre-execution;
#: ACP: a user-visible notice, since the host cannot un-run the CLI's call).
BLOCK_THRESHOLD = 5
#: > this many total failures in one run → abort the whole run. The DEFAULT only:
#: :func:`configured_circuit_threshold` reads ``guardrails.loop_breaker.circuit_threshold``
#: and falls back here. A bare constant made this rung unreachable in practice — proving it
#: took >30 genuine tool failures in one run and there was no way to ask for a lower bar —
#: so the ceiling is the one rung of the three that is operator-tunable. WARN and BLOCK stay
#: constants: they are advisory rungs whose wording quotes the number, and neither ends a run.
CIRCUIT_THRESHOLD = 30

# ── Structural (no-progress) detection ──────────────────────────────────────
#: Recent call signatures kept for pattern matching.
STRUCT_WINDOW = 16
#: ≥ this many identical triples in a row → no-progress.
STRUCT_REPEAT = 3
#: ≥ this many repetitions of a cycle → ping-pong. The cycle PERIODS checked are below;
#: `STRUCT_WINDOW` must hold `max(period) * cycles` entries (3 × 3 = 9 ≤ 16 ✓).
STRUCT_PINGPONG_CYCLES = 3
#: Cycle lengths the structural detector recognises. Period 2 is A↔B; period 3 is
#: A→B→C→A→B→C, which is the shape of the most common real agent loop — read → edit → test,
#: repeat — and was undetectable while the span was hardcoded to `cycles * 2`.
STRUCT_CYCLE_PERIODS = (2, 3)
#: The no-progress threshold for a tool that is POLLING by nature. An agent waiting on a
#: service correctly calls the same status probe with the same result until it changes; telling
#: it at the third identical answer that it is "looping without making progress" is advice to
#: stop doing the right thing. The multiplier keeps the detector alive for a poll that never
#: terminates while leaving normal waiting alone.
STRUCT_POLL_REPEAT = STRUCT_REPEAT * 3
#: Tool names whose repetition is a wait, not a loop. Matched case-insensitively against the
#: tool name at the head of the signature.
POLL_TOOLS = frozenset(
    {
        "workflow_status",
        "workflow_observe",
        "subagent_status",
        "subagent_list",
        "automation_history",
        "wait",
        "wait_for",
    }
)
#: Shell-family tools whose repetition is a wait only when the COMMAND is a status probe —
#: `bash` itself is not pollable, `bash("systemctl is-active x")` is.
#:
#: Imported rather than restated: "which tools run a shell" is one question, and it is
#: :mod:`personalclaw.task_modes`' to answer because the approval gate turns on it. This
#: module held a second copy that had already drifted (it was missing ``run_script``), and
#: a second copy of a security-relevant set is a set that gets tightened in one place —
#: which is how #443 happened.
SHELL_TOOLS = SHELL_TOOL_NAMES
#: Substrings that make a shell command a status probe rather than an action.
_POLL_COMMAND_HINTS = (
    "is-active",
    "is-enabled",
    "systemctl status",
    "--status",
    "pgrep",
    "ps -",
    "curl -sf",
    "wait",
    "tail -f",
    "docker ps",
    "git status",
)
#: ``status`` as a command's own word (``service web status``, ``kubectl rollout status x``),
#: matched against the signature, where the command sits inside JSON quotes. Not a name that
#: starts with it: ``find / -name status.json`` searches for a file, and is no probe.
_STATUS_WORD_RE = re.compile(r"\sstatus(?=[\s\"]|$)")
#: Read-only file tools exempted from the no-progress rule entirely. Re-reading a file is how
#: an agent CONFIRMS an edit landed, and scanning a tree is ordinary work; the warning told it
#: that reading was looping. A read cannot make progress by itself, so its repetition is not
#: evidence of a loop — the tools that act are where a loop shows.
READ_ONLY_TOOLS = frozenset({"read", "fs_read", "glob", "grep", "code", "view", "cat"})

#: A READ that has answered the same this many times in a row is refused before it runs again:
#: a fourth identical answer to a call that only reads tells the model nothing the first three
#: did not. The count the no-progress warning fires at, so the warning's "calling it this way
#: again will be refused" is what happens next.
REPEAT_BLOCK_THRESHOLD = STRUCT_REPEAT
#: More repeats than this in one run stops it: reads answered as they were last time, and reads
#: refused for being one (see :meth:`LoopBreaker.record_structural`). Measured, the loop this
#: exists for reached it after about half of its 65 calls; a working turn asks something new.
REPEAT_CIRCUIT_THRESHOLD = 8

#: Tools whose whole job is to let time pass: after one, a read may rightly answer anew.
WAIT_TOOLS = frozenset({"wait", "wait_for"})

#: An output redirect to nowhere (``2>/dev/null``, ``>/dev/null``, ``&>/dev/null``) or stderr
#: joined to stdout (``2>&1``): neither writes anything, so the command still only reads.
_DISCARDED_OUTPUT_RE = re.compile(r"\s*(?:[12&]?>>?\s*/dev/null\b|2>&1)")


def only_reads(title: str, tool_kind: str, tool_input: object, declared: object = "") -> bool:
    """Whether a call only reads — the one kind the loop breaker refuses for repeating.

    A call established as a read (:func:`personalclaw.task_modes.reads_only`: a tool declaring it
    only reads, or a shell command screened read-only), or a shell pipeline whose FIRST command
    is such a read: ``ls -R | grep -E "a|b" | sort -u`` only reads, though the screen — built to
    let a call through unasked, where a wrong "yes" is the costly error — passes no pipe it
    cannot prove. Here a wrong "yes" costs one refused re-run of a command that already answered
    three times, so the first command is enough, and output it throws away
    (:data:`_DISCARDED_OUTPUT_RE`) does not make it act: ``find / -name status.json 2>/dev/null``
    is the search a loop worker ran six times in one cycle.
    """
    if reads_only(title, tool_kind, tool_input, declared):
        return True
    first = shell_command(title, tool_kind, tool_input, declared).split("|", 1)[0]
    first = _DISCARDED_OUTPUT_RE.sub("", first)
    return bool(first.strip()) and is_read_only_bash(first)


def _is_wait(sig: str) -> bool:
    """Whether this call let time pass: a wait tool, or a shell command that sleeps first."""
    tool = _tool_of(sig)
    if tool in WAIT_TOOLS:
        return True
    if tool not in SHELL_TOOLS:
        return False
    try:
        args = json.loads(sig.split("\x1f", 1)[0].split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return False
    command = args.get("command", "") if isinstance(args, dict) else ""
    return isinstance(command, str) and command.strip().startswith("sleep")


def _tool_of(sig: str) -> str:
    """The tool name at the head of a signature.

    A signature is ``params_key(tool, args)`` + ``\x1f`` + result digest, and `params_key` is
    ``f"{tool}:{json}"`` (or the bare tool name when args are not serializable — the ACP shape).
    So the tool is everything before the first ``:`` of the first field.
    """
    head = sig.split("\x1f", 1)[0]
    return head.split(":", 1)[0].strip().lower()


def _is_poll_signature(sig: str) -> bool:
    """Whether repeating this call is a WAIT rather than a loop.

    Two ways in: the tool is inherently a status read, or it is a shell-family tool whose
    command reads like a status probe. The second matters because the real-world case is
    ``bash("systemctl is-active x")`` — the tool name alone cannot tell a poll from an action,
    which is why a tool-name-only set would have missed exactly the reported example.
    """
    tool = _tool_of(sig)
    if tool in POLL_TOOLS:
        return True
    if tool in SHELL_TOOLS:
        lowered = sig.split("\x1f", 1)[0].lower()
        return any(hint in lowered for hint in _POLL_COMMAND_HINTS) or bool(
            _STATUS_WORD_RE.search(lowered)
        )
    return False


def _repeat_threshold(sig: str) -> int:
    """How many identical calls in a row count as no-progress for this signature."""
    return STRUCT_POLL_REPEAT if _is_poll_signature(sig) else STRUCT_REPEAT


def _cycle_at(recent: list[str], period: int, cycles: int) -> tuple[str, ...] | None:
    """The repeating cycle at the tail, or None.

    Generalized from the old ``span = cycles * 2`` so a period-3 rotation (read → edit → test,
    repeat) is visible. A cycle whose entries are not all distinct is rejected: ``A,A,B`` × 3
    is the no-progress rule's business, and reporting it here too would double-warn one loop.
    """
    span = period * cycles
    if len(recent) < span:
        return None
    tail = recent[-span:]
    head = tuple(tail[:period])
    if len(set(head)) != period:
        return None
    if all(tail[i] == head[i % period] for i in range(span)):
        return head
    return None


#: Argument keys an adapter INJECTS rather than the model passing them as arguments.
#: Dunder-prefixed by convention, and per-call by nature — kiro stamps
#: ``__tool_use_purpose`` ("Run the requested command for the first time." → "…second
#: time.") into every ``rawInput``. Excluded from the bucket identity because they say
#: nothing about what the tool was asked to do. See :func:`normalize_call_args`.
ADAPTER_ARG_PREFIX = "__"

#: Argument keys that ANNOTATE a call rather than determine what it does — free-text the
#: model writes for the human reading the transcript. `AAP-6`/`G154`: claude-code sends
#: ``description`` on every Bash call, and a model enumerating its own retries writes
#: "Run boom command (1 of 4)" … "(4 of 4)". Byte-identical commands therefore produced
#: four buckets of one and no rung fired, which is the same defect ``ADAPTER_ARG_PREFIX``
#: fixed for kiro arriving through a different door: a per-call nonce in the identity.
#:
#: Dropped ONLY when a behavioural key survives beside them (see
#: :func:`normalize_call_args`) — for a tool whose payload genuinely IS a description,
#: the description is the behaviour and merging on it would abort healthy turns.
ANNOTATION_ARG_KEYS = frozenset(
    {"description", "explanation", "reason", "rationale", "thought", "why", "purpose"}
)


def normalize_call_args(args: object) -> object:
    """Strip adapter-injected metadata from tool arguments before keying on them.

    `AAP-6`/`G152`. The breaker's whole premise is that the SAME call repeated is one
    bucket. An ACP adapter hands the host its arguments as an opaque JSON string, and
    kiro's includes a per-call narration key — so four byte-identical
    ``bash -c 'echo boom >&2; exit 3'`` calls produced four DIFFERENT keys, four streaks
    of one, and no warn at a threshold of three. Measured live: the failure bit arrived
    on all four and the breaker still said nothing, because a params-aware breaker
    keyed on a per-call nonce is a params-BLIND breaker that also never repeats.

    Parses the ACP string shape, drops :data:`ADAPTER_ARG_PREFIX` keys, and leaves
    everything else — including the native dict shape and any non-JSON string —
    untouched, so this can only ever MERGE buckets that differ by adapter narration.
    """
    raw = args
    if isinstance(args, str):
        try:
            raw = json.loads(args)
        except (TypeError, ValueError):
            return args
    if not isinstance(raw, dict):
        return args
    stripped = {k: v for k, v in raw.items() if not str(k).startswith(ADAPTER_ARG_PREFIX)}
    # Free-text annotation keys are per-call by nature, so they fragment the
    # bucket exactly like an adapter nonce. Dropped only when something behavioural
    # survives beside them — a tool whose ONLY argument is a description keeps keying on
    # it, because there the description IS the call and merging would blame identical
    # buckets for genuinely different work.
    behavioural = {k: v for k, v in stripped.items() if str(k) not in ANNOTATION_ARG_KEYS}
    if behavioural:
        stripped = behavioural
    # An input made ENTIRELY of adapter metadata would otherwise collapse to `{}` and
    # merge every such call into one bucket regardless of tool arguments. Keep the
    # original rather than invent an identity out of nothing.
    return stripped if stripped else args


def params_key(tool_name: str, args: object) -> str:
    """Stable ``(tool, params)`` identity for breaker bucketing.

    Same tool + same args = same bucket, so repeated *identical* failing calls
    accumulate while genuinely different calls stay independent. Adapter-injected
    per-call metadata is normalized out first (:func:`normalize_call_args`) — without
    that the ACP shape, an opaque JSON string, keys on the narration too. Falls back to
    the tool name alone if args aren't JSON-serializable.
    """
    try:
        return f"{tool_name}:{json.dumps(normalize_call_args(args), sort_keys=True, default=str)}"
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


def repeat_blocked_message(tool_name: str, count: int) -> str:
    """The answer a repeated READ gets instead of running (see :data:`REPEAT_BLOCK_THRESHOLD`)."""
    return (
        f"Error: tool `{tool_name}` was not run — this exact call only reads, and it returned "
        f"the same result the last {count} times it ran this turn, so running it again cannot "
        "tell you anything new. Use the result you have, try something different, or stop "
        "and tell the user what is blocking you."
    )


def repeat_circuit_message(total_repeats: int) -> str:
    """The REPEAT circuit: the run is stopped because it keeps getting the answers it already has.

    Worded as :func:`circuit_message`'s sibling — one breaker, one vocabulary — and ends with
    the step that helps: a model that keeps searching usually lacks something it was never told.
    """
    return (
        f"Run aborted by the loop breaker: {total_repeats} tool calls in this turn repeated "
        "an earlier call and got the same result back. The run was going in circles instead "
        "of making progress, so it was stopped rather than allowed to burn the rest of its "
        "budget. Give it what it was looking for, such as a path or a name, and ask again."
    )


def configured_circuit_threshold() -> int:
    """``guardrails.loop_breaker.circuit_threshold``, or :data:`CIRCUIT_THRESHOLD`.

    Best-effort and never raises: the circuit rung is an ABORT, so a config read that
    fails must leave the shipped ceiling standing rather than take the breaker out of
    service. Imported inside the function because this module is the one both runtimes
    depend on and it deliberately owns no import of the config tree at module scope.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return max(1, int(AppConfig.load().guardrails.loop_breaker.circuit_threshold))
    except Exception:
        return CIRCUIT_THRESHOLD


class LoopBreaker:
    """Per-run progress tracker with graduated verdicts.

    Two parallel paths over the same call stream:

    * **failure path** — :meth:`record` counts consecutive *failures* per
      ``(tool, params)`` key; :meth:`count` drives the BLOCK/WARN rungs and
      :attr:`total_failures` the run-wide circuit breaker. A success clears the key.
    * **structural path** — :meth:`record_structural` tracks, per ``(tool, params)``
      key, how many times in a row it has answered the same (wherever in the run the
      calls fell), and recent signatures for an A↔B↔A↔B alternation (ping-pong). Returns
      a reason string on detection, else "". :meth:`refusal` answers a read repeated past
      :data:`REPEAT_BLOCK_THRESHOLD`, and :attr:`total_repeats` drives the repeat circuit
      (:meth:`stop_sentence`).
    """

    def __init__(self, *, circuit_threshold: int | None = None) -> None:
        self._counts: dict[str, int] = {}
        self.total_failures = 0
        # Recent structural signatures (most-recent last), bounded to the window.
        self._recent: deque[str] = deque(maxlen=STRUCT_WINDOW)
        # Reasons already reported this run, so we warn once per distinct loop and
        # don't re-inject the same observation every subsequent identical call.
        self._struct_reported: set[str] = set()
        # The repeat path. Per (tool, params) key: the digest of its last result and how many
        # times in a row it came back that way.
        self._same: dict[str, tuple[str, int]] = {}
        #: Repeats this run — reads answered as they were last time, and reads refused for being
        #: one. Survives a compaction, as ``total_failures`` does.
        self.total_repeats = 0
        # An explicit ceiling PINS this breaker's circuit rung and survives `reset()`;
        # None means "ask the config". Kept apart from the resolved value below because
        # reset() re-resolves the config one but must not discard the caller's.
        self._circuit_pin = None if circuit_threshold is None else max(1, int(circuit_threshold))
        # Resolved lazily, on the first circuit check that could actually trip — see
        # `circuit_tripped`. `None` = not read yet, which is NOT the same as 30.
        self._circuit_resolved: int | None = self._circuit_pin

    def reset(self) -> None:
        self._counts.clear()
        self.total_failures = 0
        self._recent.clear()
        self._struct_reported.clear()
        self._same.clear()
        self.total_repeats = 0
        # Re-arm the config read so a ceiling edited mid-session binds on the next run
        # instead of on the next restart. A pinned ceiling is left alone.
        self._circuit_resolved = self._circuit_pin

    def reset_structural(self) -> None:
        """Re-arm structural detection (after a compaction) without touching the
        failure counts — a loop that resumes identically post-compaction should be
        caught fresh (post-compaction guard). The repeat streaks go too: the earlier
        answers are no longer in the model's context, so asking again is not a repeat.
        :attr:`total_repeats` stays, like the failure total."""
        self._recent.clear()
        self._struct_reported.clear()
        self._same.clear()

    def record(self, key: str, failed: bool) -> int:
        if failed:
            self.total_failures += 1
            self._counts[key] = self._counts.get(key, 0) + 1
        else:
            self._counts.pop(key, None)  # a success clears this key's streak
        return self._counts.get(key, 0)

    def repeat_count(self, key: str) -> int:
        """How many times in a row this call has answered the same. A status poll is never
        counted (waiting repeats by nature), nor a read-only file tool (never recorded)."""
        last = self._same.get(key)
        if last is None or _is_poll_signature(key):
            return 0
        return last[1]

    def refusal(self, tool_name: str, key: str, *, reads: bool) -> str:
        """The text a call gets INSTEAD of running, or ``""`` to run it. Pure.

        A call that failed the same way :data:`BLOCK_THRESHOLD` times, or a READ (``reads``:
        :func:`only_reads`) that answered the same :data:`REPEAT_BLOCK_THRESHOLD` times in a
        row. Only a read is refused for repeating: a call that acts may rightly be re-run to
        check what it did, and its unchanged answer is news.
        """
        failures = self._counts.get(key, 0)
        if failures >= BLOCK_THRESHOLD:
            return blocked_message(tool_name, failures)
        same = self.repeat_count(key)
        if reads and same >= REPEAT_BLOCK_THRESHOLD:
            return repeat_blocked_message(tool_name, same)
        return ""

    def refuse(self, tool_name: str, key: str, *, reads: bool) -> str:
        """:meth:`refusal`, at the one place a call is answered with it: a refused repeat counts
        toward the repeat circuit, as the call it stands for would have."""
        text = self.refusal(tool_name, key, reads=reads)
        if text and self._counts.get(key, 0) < BLOCK_THRESHOLD:
            self.total_repeats += 1
        return text

    def stop_sentence(self) -> str:
        """Why this run must stop now, or ``""`` to go on: too many failures or repeats."""
        if self.circuit_tripped():
            return circuit_message(self.total_failures)
        if self.total_repeats > REPEAT_CIRCUIT_THRESHOLD:
            return repeat_circuit_message(self.total_repeats)
        return ""

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)

    @property
    def circuit_threshold(self) -> int:
        """This breaker's abort ceiling — the pin, else the configured value.

        Resolved once and cached, because the native runtime asks on EVERY tool result
        and an uncached ``AppConfig.load()`` per tool call would be a JSON parse per
        call. `reset()` clears the cache, so the effective cadence is one read per run.
        """
        if self._circuit_resolved is None:
            self._circuit_resolved = configured_circuit_threshold()
        return self._circuit_resolved

    def circuit_tripped(self) -> bool:
        """True once this run's total failures exceed :attr:`circuit_threshold`."""
        # A clean run answers without touching the config at all: the ceiling is floored
        # at 1 and the comparison is strict, so zero failures can never trip whatever the
        # threshold is. That keeps the per-tool-result call on the happy path free.
        if not self.total_failures:
            return False
        return self.total_failures > self.circuit_threshold

    def record_structural(self, sig: str, *, reads: bool = False) -> str:
        """Record a successful call's ``(tool, params, result_digest)`` signature; return a
        reason string when a structural loop is newly detected this run, else ``""``.

        ``reads`` says the call only reads (:func:`only_reads`); only a read's repeats are
        counted toward the circuit, and only a read is refused for repeating.

        Detects (a) no-progress: the same call answering the same :data:`STRUCT_REPEAT`
        times — longer for a status poll, and never for a read-only tool. A READ is warned on
        its own run of identical answers wherever in the turn they fell, since that run is what
        gets it refused next; any other call only when the same signature repeats back to back.
        (b) a cycle: a rotation of 2 OR 3 distinct calls (:data:`STRUCT_CYCLE_PERIODS`)
        repeating :data:`STRUCT_PINGPONG_CYCLES` times. Period 3 matters because read → edit →
        test, repeat is the most common real loop and the old ``cycles * 2`` span could not see
        it. Each distinct loop is reported once (dedup via ``_struct_reported``) so the warning
        fires on the turn the loop becomes evident, not every call. Both only warn: what STOPS
        a repeating turn is a read refused past :data:`REPEAT_BLOCK_THRESHOLD`
        (:meth:`refusal`) and the repeat circuit it counts toward (:attr:`total_repeats`,
        :meth:`stop_sentence`).
        """
        self._recent.append(sig)
        recent = list(self._recent)

        # The repeat path. Time passing clears it: whatever a read answered before, after a
        # wait it may rightly answer anew. A read-only file tool is exempt (a re-read is how an
        # edit is confirmed), and a status poll repeats by nature, so it is never counted.
        if _is_wait(sig):
            self._same.clear()
        key, _, digest = sig.partition("\x1f")
        streak = 0
        if _tool_of(sig) not in READ_ONLY_TOOLS:
            last = self._same.get(key)
            streak = last[1] + 1 if last is not None and last[0] == digest else 1
            self._same[key] = (digest, streak)
            if reads and streak > 1 and not _is_poll_signature(sig):
                self.total_repeats += 1

        # (a) no-progress. A poll gets a longer rope — see READ_ONLY_TOOLS / STRUCT_POLL_REPEAT
        # for why each exemption is a decision rather than a tolerance dial.
        if streak:
            threshold = _repeat_threshold(sig)
            if reads:
                seen, where = streak >= threshold, "in this turn"
            else:
                tail = recent[-threshold:]
                seen, where = len(tail) == threshold and len(set(tail)) == 1, "in a row"
            reason = f"no-progress:{sig}"
            if seen and reason not in self._struct_reported:
                self._struct_reported.add(reason)
                if _is_poll_signature(sig):
                    after = " (and it looks like a status poll — if you are waiting, say so)"
                elif reads:
                    after = "; calling it this way again will be refused"
                else:
                    after = ""
                return (
                    f"the same tool call produced the same result {threshold} times "
                    f"{where}{after}"
                )

        # (b) cycle: a rotation of `period` distinct calls repeating `STRUCT_PINGPONG_CYCLES`
        # times. Periods are tried SHORTEST first so an A↔B loop is still reported as A↔B
        # rather than as a degenerate longer cycle.
        for period in sorted(STRUCT_CYCLE_PERIODS):
            cycle = _cycle_at(recent, period, STRUCT_PINGPONG_CYCLES)
            if cycle is None:
                continue
            # Dedup key is the CANONICAL rotation, not the tail order. An
            # A↔B loop warned on two consecutive calls, because at call 6 the tail read
            # (A,B) and at call 7 it read (B,A) — a different key for one loop. The note is
            # meant to fire once, on the turn the loop becomes evident.
            canonical = min(tuple(cycle[i:] + cycle[:i]) for i in range(period))
            reason = "cycle:" + "|".join(canonical)
            if reason in self._struct_reported:
                break
            self._struct_reported.add(reason)
            shape = "→".join("ABCDEFGH"[i] for i in range(period))
            return (
                f"{period} tool calls are cycling without making progress "
                f"({STRUCT_PINGPONG_CYCLES}× {shape} with no new state)"
            )
        return ""
