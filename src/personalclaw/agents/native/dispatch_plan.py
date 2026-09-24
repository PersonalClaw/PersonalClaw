"""Reader/writer resource reservations + the wave partition for one turn's tool calls (HC-6).

A model turn routinely asks for several independent lookups at once — read three files,
grep for two symbols, list a directory. The native loop ran them strictly one at a time,
so the turn cost the SUM of every lookup even though no two of them touched the same
thing. This module decides which of a turn's requested calls may overlap.

**The unit of the decision is a reservation, not a tool name.** Each call declares the
resources it touches as ``(mode, kind, key)`` triples — ``READ`` or ``WRITE`` over a
concrete path, a directory subtree, a glob PATTERN, or a non-path namespace. Two calls may
overlap iff no reservation of one conflicts with a reservation of the other, under the
reader/writer rule: two reads never conflict (same path or different), a write conflicts
with every reader and writer of anything it can overlap.

Three properties are load-bearing, in the order they matter:

1. **Unclassified means EVERYTHING.** A tool this module does not know is assumed to touch
   every resource there is, so it conflicts with every other call and lands alone in its
   own wave. An unknown tool therefore degrades to exactly the old serial behaviour rather
   than racing. That is why :data:`_RESERVATIONS` is an explicit allowlist and there is no
   heuristic fallback — a tool added elsewhere in the codebase is serial until someone
   states, here, what it touches.

2. **A PATTERN is never normalized.** This is our own recorded landmine, and it is the
   reason a pattern gets its own kind instead of being folded into a path key. Normalizing
   a glob turns it into a path-shaped STRING, and a path-shaped string is then compared by
   EQUALITY — so ``glob("**/*.py")`` would stop conflicting with a write to
   ``pkg/mod.py``: the reservation silently covers one nonexistent file named ``**/*.py``
   instead of every Python file in the tree, and the write overlaps a read it had to
   serialize against. Worse, ``abspath`` (which is what "normalize a path" means here)
   runs ``normpath``, and ``normpath`` treats ``*``/``**`` as ordinary segments and
   collapses an adjacent ``..`` against them — ``a/**/../b`` becomes ``a/b``, dropping the
   ``**`` entirely. So a pattern reservation keeps the pattern VERBATIM and conflict
   detection runs it through :func:`personalclaw.guardrails.registries.path_glob`, the
   matcher that already encodes this rule (PHF): normalize only the QUERIED ITEM, never
   the pattern. The one pattern shape that cannot be reasoned about as a pattern — a
   ``..`` segment, PHF's collapse case — is refused into ``EVERYTHING`` rather than
   normalized, so the unreasonable case serializes instead of guessing.

3. **Cannot prove disjoint ⇒ serialize.** Every uncertain comparison in
   :func:`_overlaps` answers "yes, these may overlap". Fail safe, not fast.

What this module deliberately does NOT decide: whether a call is *allowed* to run. A
reservation is about ORDERING and concurrency. Admission — the deny-list, the task-mode
gate, PreToolUse hooks, the approval gate, any pre-write read gate — stays where it is and
fires per call exactly as it does serially. The two compose: admission decides whether a
call runs at all, a reservation decides whether it may run beside its sibling.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from personalclaw.guardrails.registries import normalize_item, path_glob

# ── the shipped timing line (HC-1's measure-first contract, reused verbatim in shape) ──
#
# HC-1's rule for its own instrumentation applies unchanged: the benchmark reads the line
# PRODUCTION emits rather than keeping a second stopwatch, so the number in a report is the
# number that ships and the two can never disagree. Hence a fixed prefix and fixed
# ``key=value`` fields:
#
#   tool batch mode=concurrent calls=8 waves=2 widest=6 ms=812
#
# * ``mode`` first, because it decides whether the row is a concurrency sample at all:
#   ``serial`` when the partition produced no wave wider than one (every call conflicted,
#   or the runtime's bound is 1 — the BASELINE arm), ``concurrent`` otherwise.
# * ``calls`` and ``waves`` together are the compression the partition achieved; ``widest``
#   says how much of it landed in one wave, which is what the wall-clock follows.
# * ``ms`` is an integer of milliseconds, matching the worktree line — these get compared
#   by eye and by grep, and a float prints ``1e-05`` on a trivial batch.
TIMING_LOG_PREFIX = "tool batch"

MODE_SERIAL = "serial"
MODE_CONCURRENT = "concurrent"

#: Ceiling on how many calls one wave may dispatch at once. Not a config field: it is a
#: resource bound on this process's executor, not a user preference, and the runtime takes
#: it as a constructor argument so a caller can set it to 1 — which reproduces the exact
#: pre-HC-6 serial behaviour and is what the benchmark's baseline arm measures.
MAX_CONCURRENT_CALLS = 8

# ── reservations ──

READ = "read"
WRITE = "write"

#: One concrete file path. Key is NORMALIZED (``normalize_item``): expanded and absolutized
#: so a relative and an absolute spelling of the same file collide.
KIND_PATH = "path"
#: A directory subtree — everything at or under the key. Key is NORMALIZED. Used by the
#: tools that walk (``list_dir``, ``repo_map``, an unfiltered ``grep``), because a walk
#: cannot be described by any single path.
KIND_TREE = "tree"
#: A glob. Key is the pattern VERBATIM — see the module docstring, property 2.
KIND_PATTERN = "pattern"
#: A non-path resource namespace (``knowledge``, ``tasks``, ``inbox``). Compared by exact
#: equality: a namespace is not a path and absolutizing it would be nonsense.
KIND_NAMESPACE = "namespace"
#: Every resource there is. Conflicts with everything, including another EVERYTHING.
KIND_EVERYTHING = "everything"


@dataclass(frozen=True, slots=True)
class Reservation:
    """One resource a call touches, and how."""

    mode: str
    kind: str
    key: str

    @property
    def writes(self) -> bool:
        return self.mode == WRITE


#: The reservation an unclassifiable call gets. A WRITE so it conflicts with readers too.
EVERYTHING = Reservation(WRITE, KIND_EVERYTHING, "*")

#: A pure call touches nothing and may overlap anything — the runtime's own synthetic
#: discovery meta-tools, which read in-process state and reach no resource at all.
NOTHING: tuple[Reservation, ...] = ()


def _tree(mode: str, path: str, *, cwd: str | None) -> Reservation:
    return Reservation(mode, KIND_TREE, _resolve(path or ".", cwd=cwd))


def _resolve(path: str, *, cwd: str | None) -> str:
    """Normalize a QUERIED path, anchored at the turn's workspace rather than the process
    cwd. ``normalize_item`` absolutizes against ``os.getcwd()``, which for a gateway-hosted
    turn is the gateway's cwd and not the session's — two sessions' ``read_file("a.py")``
    would then collide on one key and serialize against each other for no reason."""
    raw = (path or "").strip()
    if cwd and not os.path.isabs(os.path.expanduser(os.path.expandvars(raw))):
        raw = os.path.join(cwd, raw)
    return normalize_item(raw)


def _pattern(mode: str, pattern: str) -> Reservation:
    """Reserve a glob AS A GLOB.

    A ``..`` segment is the one shape PHF's rule says cannot survive being reasoned about
    positionally (``normpath`` would collapse it against an adjacent ``**`` and change what
    the pattern covers), so it degrades to EVERYTHING instead of being normalized or
    matched. Refusing here rather than in the matcher keeps the refusal at the point where
    the resource set is DECLARED — the matcher's job is only to answer a question.
    """
    pat = (pattern or "").strip()
    if not pat:
        return EVERYTHING
    if ".." in pat.replace("\\", "/").split("/"):
        return EVERYTHING
    return Reservation(mode, KIND_PATTERN, pat)


# ── the per-tool resource declarations ──
#
# An allowlist on purpose (property 1). Each entry is a function of the call's arguments,
# because the resource set is in the ARGUMENTS — `read_file` is only cheap to overlap
# because we know WHICH file, and a table keyed on tool name alone could not say that.


def _r_path(mode: str, a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    """A single-file reservation. ``path`` is REQUIRED on all three of these tools, so an
    empty one is a malformed call and cannot be resolved to a resource — resolving it
    anyway would key the reservation on the workspace ROOT and claim a directory is the
    file. EVERYTHING instead: serialize, and let the tool report its own argument error."""
    raw = str(a.get("path", "") or "").strip()
    if not raw:
        return (EVERYTHING,)
    return (Reservation(mode, KIND_PATH, _resolve(raw, cwd=cwd)),)


def _r_read_file(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    return _r_path(READ, a, cwd)


def _r_write_path(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    return _r_path(WRITE, a, cwd)


def _r_walk(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    return (_tree(READ, str(a.get("path", "") or "."), cwd=cwd),)


def _r_glob(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    return (_pattern(READ, str(a.get("pattern", ""))),)


def _r_grep(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
    # A grep with no `glob` filter walks the whole workspace, so it reserves the workspace
    # tree — not "nothing in particular". With a filter it is a pattern read.
    filt = str(a.get("glob", "") or "")
    return (_pattern(READ, filt),) if filt else (_tree(READ, ".", cwd=cwd),)


def _ns(mode: str, name: str):
    def _f(a: dict, cwd: str | None) -> tuple[Reservation, ...]:
        return (Reservation(mode, KIND_NAMESPACE, name),)

    return _f


NS_KNOWLEDGE = "knowledge"
NS_TASKS = "tasks"
NS_INBOX = "inbox"
NS_TOOL_RESULTS = "tool_results"

#: tool name → what it touches. Every builtin whose resource set is KNOWN; anything absent
#: is EVERYTHING and therefore serial (property 1).
_RESERVATIONS = {
    # filesystem
    "read_file": _r_read_file,
    "write_file": _r_write_path,
    "edit_file": _r_write_path,
    "list_dir": _r_walk,
    "repo_map": _r_walk,
    "glob": _r_glob,
    "grep": _r_grep,
    # knowledge
    "knowledge_search": _ns(READ, NS_KNOWLEDGE),
    "knowledge_get": _ns(READ, NS_KNOWLEDGE),
    "knowledge_stats": _ns(READ, NS_KNOWLEDGE),
    "knowledge_create": _ns(WRITE, NS_KNOWLEDGE),
    "knowledge_update": _ns(WRITE, NS_KNOWLEDGE),
    # tasks / projects / runs — one namespace, because they share a store and a run touches
    # the tasks it schedules; splitting them would claim an independence they do not have.
    "task_list": _ns(READ, NS_TASKS),
    "task_get": _ns(READ, NS_TASKS),
    "task_search": _ns(READ, NS_TASKS),
    "task_ready": _ns(READ, NS_TASKS),
    "project_list": _ns(READ, NS_TASKS),
    "project_run_status": _ns(READ, NS_TASKS),
    "project_run_list": _ns(READ, NS_TASKS),
    "task_create": _ns(WRITE, NS_TASKS),
    "task_update": _ns(WRITE, NS_TASKS),
    "task_list_create": _ns(WRITE, NS_TASKS),
    "project_create": _ns(WRITE, NS_TASKS),
    "project_run_create": _ns(WRITE, NS_TASKS),
    "project_run_start": _ns(WRITE, NS_TASKS),
    # inbox + stored tool results
    "post_to_inbox": _ns(WRITE, NS_INBOX),
    "tool_result_get": _ns(READ, NS_TOOL_RESULTS),
}

#: The runtime's synthetic discovery tools: pure in-process reads of the tool catalog, no
#: resource at all. ``reset_tools`` is NOT here — it rewrites the turn's tool surface, so
#: it is unclassified and serial.
_PURE = frozenset({"tool_search", "tool_schema"})


def reservations_for(
    tool_name: str, args: dict, *, cwd: str | None = None
) -> tuple[Reservation, ...]:
    """What ``tool_name(args)`` touches. ``(EVERYTHING,)`` when that cannot be determined.

    Never raises: a malformed argument dict yields EVERYTHING (serialize) rather than an
    exception, because a planner that can fail is a planner that takes the whole turn down.
    """
    name = (tool_name or "").strip()
    if name in _PURE:
        return NOTHING
    fn = _RESERVATIONS.get(name)
    if fn is None:
        return (EVERYTHING,)
    try:
        return fn(args if isinstance(args, dict) else {}, cwd)
    except Exception:  # noqa: BLE001 — see the docstring: unknown shape ⇒ serialize
        return (EVERYTHING,)


# ── conflict detection ──


def _under(child: str, parent: str) -> bool:
    """Is normalized ``child`` at or under normalized ``parent``?"""
    if child == parent:
        return True
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _overlaps(a: Reservation, b: Reservation) -> bool:
    """Can these two reservations name the same resource? Uncertain ⇒ True."""
    if a.kind == KIND_EVERYTHING or b.kind == KIND_EVERYTHING:
        return True
    kinds = {a.kind, b.kind}
    if KIND_NAMESPACE in kinds:
        # A namespace is not a path and a path is not a namespace: they cannot name the
        # same resource, so this is the one "no" that is a fact rather than a guess.
        return a.kind == b.kind and a.key == b.key
    if KIND_PATTERN in kinds:
        if a.kind == b.kind:
            # Two globs. Deciding whether two glob languages intersect is not something
            # this module gets to be clever about, so: they may.
            return True
        pattern, other = (a, b) if a.kind == KIND_PATTERN else (b, a)
        if other.kind == KIND_TREE:
            # A glob can match somewhere inside any subtree unless we prove otherwise,
            # and proving otherwise is the same intersection problem as above.
            return True
        # The load-bearing line: the ITEM is already normalized, the PATTERN is verbatim,
        # and `path_glob` is the matcher that keeps it that way.
        return path_glob(other.key, pattern.key)
    # path/tree against path/tree — containment either way.
    return _under(a.key, b.key) or _under(b.key, a.key)


def conflicts(a: Sequence[Reservation], b: Sequence[Reservation]) -> bool:
    """Must these two calls be ordered against each other?

    The reader/writer rule, in one line: a pair conflicts iff at least one side WRITES a
    resource the other side touches. Two reads of different files overlap; two reads of the
    SAME file overlap; a write serializes against every reader and writer of that path.
    """
    for ra in a:
        for rb in b:
            if not (ra.writes or rb.writes):
                continue
            if _overlaps(ra, rb):
                return True
    return False


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    """One turn's calls grouped into ordered waves. Wave *k* runs after wave *k-1*."""

    waves: tuple[tuple[int, ...], ...]

    @property
    def call_count(self) -> int:
        return sum(len(w) for w in self.waves)

    @property
    def widest(self) -> int:
        return max((len(w) for w in self.waves), default=0)

    @property
    def mode(self) -> str:
        return MODE_CONCURRENT if self.widest > 1 else MODE_SERIAL


def plan(
    reservation_sets: Sequence[Sequence[Reservation]],
    *,
    max_width: int = MAX_CONCURRENT_CALLS,
) -> DispatchPlan:
    """Partition calls into waves of mutually non-conflicting calls, preserving order.

    Greedy in index order, which is what makes the ordering guarantee provable rather than
    asserted: a call joins the CURRENT wave only if it conflicts with nothing already in
    it, otherwise it opens a new wave. So for any conflicting pair ``i < j``, ``j`` was
    considered after ``i`` was already placed, and either the current wave was ``i``'s (in
    which case the conflict forces a new, later wave) or it was already later. Either way
    ``j``'s wave is strictly after ``i``'s, and the relative order of every non-disjoint
    pair is exactly the order the model asked for.

    A better packing exists (this is graph colouring, and greedy is not optimal), but a
    reordering one is not allowed: the cheapest schedule is worthless if it lets a write
    land before a read the model wrote first.
    """
    width = max(1, int(max_width or 1))
    waves: list[list[int]] = []
    current: list[int] = []
    for i, res in enumerate(reservation_sets):
        blocked = len(current) >= width or any(conflicts(res, reservation_sets[j]) for j in current)
        if blocked and current:
            waves.append(current)
            current = []
        current.append(i)
    if current:
        waves.append(current)
    return DispatchPlan(waves=tuple(tuple(w) for w in waves))
