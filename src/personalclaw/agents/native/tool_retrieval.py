"""Per-turn tool retrieval (TR1) — a thin sibling of skills surfacing.

Stop riding the **entire** tool-schema set on every model turn. Surface only a
per-turn relevant projection: a small always-include CORE ∪ top-K by
``max(cosine(query, tool_embedding), keyword_overlap)`` ∪ structural hints
(a URL in the turn → web/fetch tools; "remind me", "every Monday" → the schedule tools and
``automation_create``; "when a new file lands in …" → ``automation_create``) ∪ the **sticky
set** (tools already CALLED this session stay available). Does for tools what
:mod:`skills.surfacing` does for skills.

**The tools' vectors come from the process's index** (:mod:`tool_vectors`), never from the turn:
kept across restarts, filled in the background in batches, and read without a network call. A
tool whose vector is not there yet ranks by its words. :meth:`ToolRetriever.select` still embeds
the query (one request), so its caller runs it off the event loop.

**A hint names tools by their words, never by part of a word** (:func:`_names_fragment`). A
hint is admitted before anything the scores rank, so a loose one takes the room the ranked
tools needed: a path in the request used to force every tool whose name merely CONTAINED
"read", "file", "dir" or "edit" — twenty-seven of them on one catalog, ``task_ready`` among
them for "ready" — and on a 32,768-token window they filled the schema budget before the one
tool the request named ranked first. So hints match whole words, and there is no path hint:
the file tools a path calls for are the core ones, which ride every turn.

**Fails OPEN, the inverse of the egress layer:** a hidden tool is a capability
regression, not a safety risk, so every uncertainty (no embed model, error, low
scores, prior use) resolves toward *including* the tool. Selection ≠ dispatch: this only
changes the *schema the model sees*; the runtime ``_tool_index`` callable map is untouched —
every tool stays callable, and every tool whose schema is deferred is still listed by name
and description in the turn's catalog.

**Two bounds, and whichever binds first wins:** :data:`DEFAULT_K` caps how many full schemas
ride a turn, and the turn's window caps how much of it they may take
(:data:`SCHEMA_WINDOW_FRACTION`, :func:`schema_budget_chars`). The count alone was calibrated
for a catalog of about thirty tools, where it was a no-op; at about 120 it let 48 schemas —
43,000 characters, more than the whole rest of the prompt — ride every turn of a local model
serving a 32,768-token window, which then took two minutes to read the prompt before its first
word.
"""

from __future__ import annotations

import json
import logging
import math
import re

from personalclaw.agents.native.tool_vectors import (
    bound_embedder,
    default_path,
    tool_text,
    tool_vectors,
)
from personalclaw.task_modes import SHELL_TOOL_NAMES
from personalclaw.token_estimate import NOMINAL_CHARS_PER_TOKEN

logger = logging.getLogger(__name__)

#: The most tools whose full schemas ride one turn; every other tool is listed in the catalog.
DEFAULT_K = 48

#: The most of a model's window the full tool schemas may take on one turn — the bound the memory
#: sections of the same prompt are held to (``context._MEMORY_WINDOW_FRACTION``). Inert on the
#: windows :data:`DEFAULT_K` was sized on: at 128k tokens it affords 64,000 characters, over the
#: 43,000 that 48 of this catalog's schemas measure. It binds on a local model's 32,768-token
#: window, where it affords 16,384. The core tools ride whatever this says (see ``_CORE_NAMES``).
SCHEMA_WINDOW_FRACTION = 0.125


def schema_budget_chars(window_tokens: int | None) -> int | None:
    """How many characters of full tool schemas a turn served with ``window_tokens`` affords.

    ``None`` when the window is not known, which leaves the count as the only bound: a guessed
    window would hide schemas on a guess.
    """
    if not window_tokens or window_tokens <= 0:
        return None
    return int(window_tokens * SCHEMA_WINDOW_FRACTION * NOMINAL_CHARS_PER_TOKEN)


# Cosine gate for a semantic tool match (short name+description text → 0.55, the
# same calibration skills surfacing uses for short descriptions).
DEFAULT_SEMANTIC_THRESHOLD = 0.55
_KEYWORD_GATE = 0.5  # word-overlap fraction to count a keyword hit

#: A cadence or a reminder: "every Monday", "every 15 minutes", "daily", "remind me",
#: "message me on Telegram".
_CADENCE = (
    r"\bschedul|\bremind|\bcron\b|\bdaily\b|\bweekly\b|\bmonthly\b|\bhourly\b"
    r"|\bevery\s+(other\s+)?(day|week|weekday|weekend|hour|minute|month|morning|evening|night"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|\d+\s*(min|minute|hour|day|week)s?)\b"
    r"|\b(message|text|ping|notify|remind)\s+me\b"
)

#: Something that should happen when something else does: "when a new PDF lands in …",
#: "whenever the build fails", "set that up as an automation".
_EVENT = (
    r"\bwhen(ever)?\b[^.?!\n]{0,80}\b(lands?|arrives?|appears?|comes?\s+in|changes?"
    r"|shows?\s+up|finish(es)?|fails?|completes?"
    r"|(is|are|gets?)\s+(added|created|modified|updated|saved|uploaded))\b"
    r"|\bautomat(e|es|ed|ion|ions|ically)\b|\b(each|every)\s+time\b"
)

# Structural hints: a regex over the user's request → the tools it plainly needs, named by the
# words of their names (`_names_fragment`) or by a whole name. Cheap detectors for the obvious
# "this turn clearly needs X".
_STRUCTURAL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # A URL is fetched or browsed. Not "search": that word names every search tool of every
    # server (issues, repositories, docs, tasks), none of which reads a URL.
    (r"https?://|www\.|\.com\b|\.org\b", ("web", "fetch", "url", "browse")),
    # "Remind me", "every Monday at 15:10": the one-off and the recurring task schedule the agent
    # itself (`set_onetime_task`, `set_recurring_task`); an automation is what sends the owner
    # words on a cadence or runs something for them, and it is the one the reminder asks for.
    (_CADENCE, ("schedule", "cron", "trigger", "onetime", "recurring", "automation_create")),
    (_EVENT, ("automation_create",)),
    # shell/exec → bash (the single env interface). Covers "run the command", a
    # CLI verb, AND git/test/lint language — those are bash commands now, not their
    # own tools, so all of it should surface bash. Matched by the shell tools' own names
    # (`task_modes.SHELL_TOOL_NAMES`) and shell words, never by a bare "run": that named every
    # `*_run*` tool — project runs, automation runs, subagent and workflow runs — none a shell.
    (
        r"\bshell\b|\bbash\b|\bcommand\b|\bterminal\b|\bexecute\b|\brun\b|\$\s"
        r"|\b(npm|pip|make|cargo|go|node|python|pytest|ls|cat|echo|chmod|mkdir|curl)\b"
        r"|\bgit\b|commit|diff|branch|stage|\btest|\bpytest|\bspec\b|assert|lint|build",
        ("shell", "exec", "command", "terminal", *sorted(SHELL_TOOL_NAMES)),
    ),
    (r"\bremember|\brecall|\bmemor|\blesson", ("memory", "recall", "lesson")),
    (r"\btask\b|\btodo\b|\bbacklog", ("task",)),
)


def _name_words(name: str) -> set[str]:
    """A tool name's words: split at every character that is not a letter or digit, and where a
    lower-case letter meets an upper-case one (``openaiDeveloperDocs``)."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w}


def _names_fragment(name: str, fragment: str) -> bool:
    """Whether a hint's ``fragment`` names the tool ``name``: the whole name, the name a server
    serves it under (``mcp/box/run_script`` for ``run_script``), or one of its words, plural
    included. Never part of a word — "read" does not name ``task_ready``."""
    low = name.lower()
    if fragment in (low, re.split(r"/|__", low)[-1]):
        return True
    words = _name_words(name)
    return fragment in words or f"{fragment}s" in words or f"{fragment}es" in words


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _schema_chars(d) -> int:
    """About how many characters ``d``'s full schema adds to a request: the function object
    ``tools.tool_definitions_to_openai_schema`` writes for it."""
    fn = {
        "name": getattr(d, "name", ""),
        "description": getattr(d, "description", "") or "",
        "parameters": getattr(d, "parameters", None) or {"type": "object", "properties": {}},
    }
    try:
        return len(json.dumps({"type": "function", "function": fn}, default=str))
    except (TypeError, ValueError):
        return len(str(fn))


class ToolRetriever:
    """Per-turn tool selector over a fixed catalog (built once at startup).

    Ranks each tool's ``name: description`` with the vectors of the process's index
    (:mod:`tool_vectors`), which holds one per text and model: a stable catalog is embedded once
    for the whole install, and a changed tool server's catalog only for the tools that changed.
    ``select`` returns the union (core ∪ sticky ∪ structural ∪ top-K), within the turn's schema
    budget when it has one. Always fail-open: any error or no-embed-model returns the FULL catalog.
    """

    def __init__(
        self,
        defs: list,
        *,
        k: int = DEFAULT_K,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ) -> None:
        self._defs = list(defs)
        self._k = max(1, int(k))
        self._threshold = semantic_threshold
        self._by_name = {getattr(d, "name", ""): d for d in self._defs}
        # core = tools that must never be filtered out (control/orientation).
        self._core = {n for n, d in self._by_name.items() if _is_core(n, d)}
        self._sticky: set[str] = set()
        # The tools the last request's hints named, carried into ONE next turn when that turn's
        # request names none: "It's ~/Notes/…" answers the question the agent asked about the
        # request before it, and the automation that request plainly needed is still the task.
        self._carried: set[str] = set()
        self._last_surfaced = len(self._defs)  # tools surfaced last select() (for hidden_count)
        self._chars = {n: _schema_chars(d) for n, d in self._by_name.items()}
        # What the index embeds for each tool.
        self._texts = {
            n: tool_text(n, getattr(d, "description", "") or "") for n, d in self._by_name.items()
        }

    def warm(self) -> None:
        """Have the index embed this catalog's tools in the background, before a turn asks.

        Resolves the bound embedding model, and reads the index's file on its first use, so the
        caller runs it off the event loop. Never raises: a catalog without vectors ranks by words.
        """
        try:
            embedder = bound_embedder()
            if embedder is not None:
                tool_vectors().want(default_path(), embedder, self._texts.values())
        except Exception:  # noqa: BLE001 — see the docstring
            logger.debug("tool retrieval: warming the tool vectors failed", exc_info=True)

    # ── sticky set (tools the agent has actually called this session) ──
    def mark_used(self, tool_name: str) -> None:
        if tool_name in self._by_name:
            self._sticky.add(tool_name)

    def _structural(self, query: str) -> set[str]:
        q = query.lower()
        hinted: set[str] = set()
        for pattern, frags in _STRUCTURAL_HINTS:
            if re.search(pattern, q):
                for name in self._by_name:
                    if any(_names_fragment(name, f) for f in frags):
                        hinted.add(name)
        return hinted

    def _semantic(self, query: str, names: set[str]) -> dict[str, float]:
        """Each of ``names`` whose vector the index holds, and its cosine against ``query``.

        Reads the index, and asks it to embed the tools it lacks in the background; embeds only
        the query, and only when there is a vector to compare it with. Empty when no embedding
        model is bound, when nothing is embedded yet, or when the query cannot be embedded.
        """
        if not query or not names:
            return {}
        embedder = bound_embedder()
        if embedder is None:
            return {}
        path = default_path()
        index = tool_vectors()
        texts = {n: self._texts[n] for n in names if n in self._texts}
        known = index.vectors(path, embedder.model, texts.values())
        if len(known) < len(texts):
            index.want(path, embedder, (t for t in texts.values() if t not in known))
        if not known:
            return {}
        try:
            query_vec = embedder.one(query)
        except Exception:
            query_vec = None
        if not query_vec:
            return {}
        return {n: _cosine(query_vec, known[t]) for n, t in texts.items() if t in known}

    def select(
        self,
        query: str,
        *,
        restrict: set[str] | None = None,
        budget_chars: int | None = None,
    ) -> list:
        """Return the tool defs to surface this turn (a subset of the catalog).

        ``query`` is what the user asked (``context.user_request``), not the assembled prompt:
        ranked against the whole prompt, every hint its own boilerplate trips (a path, "run",
        "task", "remember") fired on every turn whatever was asked.

        ``restrict`` limits selection to those tool names — the tool-GROUP seam
        (CONTEXT-ECONOMY §5.3): retrieval selects *within* the active groups, so
        the K budget is spent on tools whose schemas can actually ride this turn,
        while :meth:`search` still ranks the FULL catalog across inactive groups.

        ``budget_chars`` (:func:`schema_budget_chars`) bounds the characters the surfaced
        schemas may add. The core tools always ride; then the tools this session already
        called, then the structurally hinted ones, then the best-scoring, each while it fits.

        Fail-open: if the union would be the whole (restricted) catalog, or anything goes
        wrong, return it all.

        Embeds the query when the catalog must be ranked, which is a network call, so the runtime
        calls this off the event loop.
        """
        try:
            return self._select(query, restrict=restrict, budget_chars=budget_chars)
        except Exception:
            logger.debug("tool retrieval failed — surfacing full catalog", exc_info=True)
            return self._pool(restrict)

    def _pool(self, restrict: set[str] | None) -> list:
        """The candidate defs for selection (the full catalog, or the restriction)."""
        if restrict is None:
            return list(self._defs)
        return [d for d in self._defs if getattr(d, "name", "") in restrict]

    def _scores(self, query: str, names: set[str]) -> dict[str, float]:
        """Each of ``names`` that passes a gate, and its ``max(semantic, keyword)`` score."""
        query_words = set(re.findall(r"\w+", query.lower()))
        semantic = self._semantic(query, names)

        scores: dict[str, float] = {}
        for name in names:
            d = self._by_name[name]
            desc_words = set(
                re.findall(r"\w+", f"{name} {getattr(d, 'description', '') or ''}".lower())
            )
            kw = (len(query_words & desc_words) / len(query_words)) if query_words else 0.0
            sem = semantic.get(name, 0.0)
            score = max(kw if kw >= _KEYWORD_GATE else 0.0, sem if sem >= self._threshold else 0.0)
            if score > 0:
                scores[name] = score
        return scores

    def _select(
        self,
        query: str,
        *,
        restrict: set[str] | None = None,
        budget_chars: int | None = None,
    ) -> list:
        pool = self._pool(restrict)
        pool_names = {getattr(d, "name", "") for d in pool}
        total = len(pool)
        within_budget = budget_chars is None or (
            sum(self._chars.get(n, 0) for n in pool_names) <= budget_chars
        )
        if total <= self._k and within_budget:
            self._carried = self._structural((query or "").strip())
            return pool  # no-op: everything fits

        q = (query or "").strip()
        # never surface a tool outside the candidate pool
        core = self._core & pool_names
        sticky = (self._sticky & pool_names) - core
        hinted = self._structural(q)
        carried, self._carried = self._carried, hinted
        structural = ((hinted or carried) & pool_names) - core - sticky
        scores = self._scores(q, pool_names - core)

        def ranked(names) -> list[str]:
            return sorted(names, key=lambda n: (-scores.get(n, 0.0), n))

        selected: set[str] = set(core)
        used = sum(self._chars.get(n, 0) for n in core)

        def admit(name: str) -> bool:
            nonlocal used
            size = self._chars.get(name, 0)
            if budget_chars is not None and used + size > budget_chars:
                return False
            selected.add(name)
            used += size
            return True

        for name in ranked(sticky) + ranked(structural):
            admit(name)
        room = max(0, self._k - len(selected))
        tried = core | sticky | structural
        for name in ranked(n for n in scores if n not in tried):
            if room <= 0:
                break
            if admit(name):
                room -= 1

        # If selection didn't actually reduce (rare), just return the pool (fail-open).
        if len(selected) >= total:
            self._last_surfaced = total
            return pool
        self._last_surfaced = len(selected)
        return [d for n, d in self._by_name.items() if n in selected]

    # ── search escape hatch (the agent can find a tool retrieval hid) ──
    def reduced(self) -> bool:
        """Whether the catalog is large enough that per-turn selection hides some
        tools — so the runtime should tell the agent it can ``tool_search``."""
        return len(self._defs) > self._k

    def hidden_count(self) -> int:
        return max(0, len(self._defs) - getattr(self, "_last_surfaced", len(self._defs)))

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Rank the ENTIRE catalog against ``query`` and return ``[{name,
        description}]`` — backs the ``tool_search`` meta-tool. Scores by
        ``max(semantic cosine, lexical overlap, substring)`` — the SAME semantic
        path :meth:`_select` uses, so discovery finds a tool by capability even
        with no keyword overlap ("resize an image" → an image tool). Lexical is
        the fail-open floor: no embed model / embed error / no vector yet → still
        works, just keyword-only. Discovery is generous (no score gate, unlike
        selection). Embeds the query, so the runtime calls it off the event loop."""
        q = (query or "").strip()
        qwords = set(re.findall(r"\w+", q.lower()))
        try:
            semantic = self._semantic(q, set(self._by_name))
        except Exception:
            logger.debug("tool search: semantic ranking failed — lexical only", exc_info=True)
            semantic = {}
        scored: list[tuple[float, str]] = []
        for name, d in self._by_name.items():
            hay = f"{name} {getattr(d, 'description', '') or ''}".lower()
            haywords = set(re.findall(r"\w+", hay))
            kw = (len(qwords & haywords) / len(qwords)) if qwords else 0.0
            substr = 0.5 if q and q.lower() in hay else 0.0
            sem = semantic.get(name, 0.0)
            score = max(kw, substr, sem)
            if score > 0 or not q:
                scored.append((score, name))
        scored.sort(key=lambda t: (-t[0], t[1]))
        out = []
        for _s, name in scored[:limit]:
            d = self._by_name[name]
            out.append({"name": name, "description": (getattr(d, "description", "") or "")[:200]})
        return out

    def catalog(self, *, exclude: set[str] | None = None, max_chars: int = 6000) -> str:
        """Render a compact ``name: one-line description`` catalog of the tools NOT
        in ``exclude`` (the Tier-1 surfaced set), grouped by provider for
        scannability. Backs progressive disclosure: the model always SEES every
        enabled tool's name+blurb even when its full schema was deferred this turn.

        Bounded by ``max_chars`` — if the long tail is huge, it summarizes the
        overflow as a per-provider count and points at ``tool_search`` (so a giant
        MCP fleet can't blow the prompt)."""
        exclude = exclude or set()
        by_prov: dict[str, list[tuple[str, str]]] = {}
        for name, d in self._by_name.items():
            if name in exclude:
                continue
            prov = getattr(d, "provider", "") or "other"
            desc = (getattr(d, "description", "") or "").strip().split("\n", 1)[0][:100]
            by_prov.setdefault(prov, []).append((name, desc))
        if not by_prov:
            return ""
        lines: list[str] = []
        overflow: list[str] = []
        used = 0
        for prov in sorted(by_prov):
            entries = sorted(by_prov[prov])
            header = f"[{prov}]"
            block = [header] + [f"- {n}: {d}" if d else f"- {n}" for n, d in entries]
            chunk = "\n".join(block)
            if used + len(chunk) <= max_chars:
                lines.append(chunk)
                used += len(chunk) + 1
            else:
                overflow.append(f"{prov} (+{len(entries)} tools)")
        if overflow:
            lines.append("[more — use tool_search to find these] " + ", ".join(overflow))
        return "\n".join(lines)


# EXACT native tool names that must always be surfaced. Two groups:
#   • universal primitives — an agentic coding OS turn almost always needs file +
#     shell + search access, so hiding these is the cardinal failure (a model that
#     can't see `bash` concludes "no shell tool exists" instead of using it);
#   • control/orientation — tools the model can't recover from losing.
# Exact match (not substring) so a huge MCP catalog can't accidentally inflate the
# core set (e.g. a substring "read" would pull in every `*Read*` MCP tool).
_CORE_NAMES: frozenset[str] = frozenset(
    {
        # universal coding primitives (git/tests/lint are done via bash, not own tools)
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "list_dir",
        # progressive discovery — tools AND skills (the model can't recover without these)
        "tool_search",
        "tool_schema",
        "skill_search",
        "skill_invoke",
        # control / orientation (always recoverable-from only if present)
        "tool_result_get",
        "ask_user",
        "finish",
    }
)

# Name FRAGMENTS for CROSS-PROVIDER control tools (ACP/MCP dialects name these
# differently, e.g. "ask_followup_question", "attempt_completion"). Kept tight to
# avoid substring false positives — notably NOT "ask" (collides with "task"),
# "run"/"read" (collide with MCP tool names).
_CORE_NAME_FRAGS: tuple[str, ...] = (
    "ask_user",
    "ask_followup",
    "attempt_completion",
    "memory_recall",
    "tool_search",
)


def _is_core(name: str, d) -> bool:
    """Whether a tool must always be surfaced. A def may declare ``core=True``;
    else an exact-name allowlist (primitives + control) or a tight fragment list
    for cross-provider control-tool variants."""
    if getattr(d, "core", False):
        return True
    low = (name or "").lower()
    if low in _CORE_NAMES:
        return True
    return any(frag in low for frag in _CORE_NAME_FRAGS)
