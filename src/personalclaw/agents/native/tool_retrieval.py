"""Per-turn tool retrieval (TR1) — a thin sibling of skills surfacing.

Stop riding the **entire** tool-schema set on every model turn. Surface only a
per-turn relevant projection: a small always-include CORE ∪ top-K by
``max(cosine(query, tool_embedding), keyword_overlap)`` ∪ structural hints
(a URL in the turn → web/fetch tools; "schedule"/"remind" → schedule tools; a
path → file tools) ∪ the **sticky set** (tools already CALLED this session stay
available). Mirrors :mod:`skills.surfacing` (shared embedder, fingerprint-keyed
cache, never-raise) — does for tools what surfacing does for skills.

**Fails OPEN, the inverse of the egress layer:** a hidden tool is a capability
regression, not a safety risk, so every uncertainty (no embed model, error, low
scores, prior use) resolves toward *including* the tool, and the default K is set
**above** today's tool count so it's a literal no-op until external-MCP catalogs
grow the set. Selection ≠ dispatch: this only changes the *schema the model sees*;
the runtime ``_tool_index`` callable map is untouched — every tool stays callable.
"""

from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)

# Default cap on surfaced tools. Set comfortably above the ~30 builtins so the
# union almost always returns everything → behavioral no-op until MCP catalogs
# push the total well past this. Tunable; the reduction only bites at large K.
DEFAULT_K = 48

# Cosine gate for a semantic tool match (short name+description text → 0.55, the
# same calibration skills surfacing uses for short descriptions).
DEFAULT_SEMANTIC_THRESHOLD = 0.55
_KEYWORD_GATE = 0.5  # word-overlap fraction to count a keyword hit

# Structural hints: a regex over the turn → tool-name substrings to force-include.
# Cheap detectors (Odysseus-style) for the obvious "this turn clearly needs X".
_STRUCTURAL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"https?://|www\.|\.com\b|\.org\b", ("web", "fetch", "url", "search", "browse")),
    (
        r"\bschedul|\bremind|\bcron\b|every (day|week|hour)|daily|weekly",
        ("schedule", "cron", "trigger"),
    ),
    (
        r"/|\.py\b|\.ts\b|\.md\b|\bfile\b|\bdirectory\b|\bfolder\b",
        ("read", "write", "edit", "file", "dir", "glob", "grep"),
    ),
    # shell/exec → bash (the single env interface). Covers "run the command", a
    # CLI verb, AND git/test/lint language — those are bash commands now, not their
    # own tools, so all of it should surface bash.
    (
        r"\bshell\b|\bbash\b|\bcommand\b|\bterminal\b|\bexecute\b|\brun\b|\$\s"
        r"|\b(npm|pip|make|cargo|go|node|python|pytest|ls|cat|echo|chmod|mkdir|curl)\b"
        r"|\bgit\b|commit|diff|branch|stage|\btest|\bpytest|\bspec\b|assert|lint|build",
        ("bash", "shell", "exec", "run", "command", "terminal"),
    ),
    (r"\bremember|\brecall|\bmemor|\blesson", ("memory", "recall", "lesson")),
    (r"\btask\b|\btodo\b|\bbacklog", ("task",)),
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _active_embedder():
    """``(embed_fn, model_label)`` or ``(None, "")``. Reuses surfacing's resolver."""
    try:
        from personalclaw.skills.surfacing import _active_embedder as resolve

        return resolve()
    except Exception:
        return None, ""


class ToolRetriever:
    """Per-turn tool selector over a fixed catalog (built once at startup).

    Embeds each tool's ``name + description`` via the shared embedder, cached
    in-process keyed by ``(name, fingerprint, model)`` so a stable catalog embeds
    once and a changed MCP catalog re-embeds only the changed tools. ``select``
    returns the union (core ∪ top-K ∪ structural ∪ sticky), capped at K. Always
    fail-open: any error or no-embed-model returns the FULL catalog.
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
        self._embed_cache: dict[str, list[float] | None] = {}  # name → vec (None = tried, failed)
        self._embed_model = ""
        self._last_surfaced = len(self._defs)  # tools surfaced last select() (for hidden_count)

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
                    low = name.lower()
                    if any(f in low for f in frags):
                        hinted.add(name)
        return hinted

    def _ensure_embeddings(self, embed_fn, model: str) -> None:
        if model != self._embed_model:
            self._embed_cache.clear()  # model switch → re-embed
            self._embed_model = model
        for name, d in self._by_name.items():
            if name in self._embed_cache:
                continue
            text = f"{name}: {getattr(d, 'description', '') or ''}".strip()
            try:
                self._embed_cache[name] = embed_fn(text)
            except Exception:
                self._embed_cache[name] = None

    def select(self, query: str) -> list:
        """Return the tool defs to surface this turn (a subset of the catalog).

        Fail-open: if the union would be ≥ the whole catalog (the common case
        until catalogs grow), or anything goes wrong, return the FULL catalog.
        """
        try:
            return self._select(query)
        except Exception:
            logger.debug("tool retrieval failed — surfacing full catalog", exc_info=True)
            return list(self._defs)

    def _select(self, query: str) -> list:
        total = len(self._defs)
        if total <= self._k:
            return list(self._defs)  # no-op: everything fits

        q = (query or "").strip()
        selected: set[str] = set(self._core) | set(self._sticky) | self._structural(q)

        query_words = set(re.findall(r"\w+", q.lower()))
        scored: list[tuple[float, str]] = []
        embed_fn, model = _active_embedder()
        query_vec = None
        if embed_fn is not None and q:
            try:
                query_vec = embed_fn(q)
            except Exception:
                query_vec = None
            if query_vec is not None:
                self._ensure_embeddings(embed_fn, model)

        for name, d in self._by_name.items():
            if name in selected:
                continue
            desc_words = set(
                re.findall(r"\w+", f"{name} {getattr(d, 'description', '') or ''}".lower())
            )
            kw = (len(query_words & desc_words) / len(query_words)) if query_words else 0.0
            sem = 0.0
            if query_vec is not None:
                vec = self._embed_cache.get(name)
                if vec:
                    sem = _cosine(query_vec, vec)
            score = max(kw if kw >= _KEYWORD_GATE else 0.0, sem if sem >= self._threshold else 0.0)
            if score > 0:
                scored.append((score, name))

        scored.sort(key=lambda t: (-t[0], t[1]))
        room = max(0, self._k - len(selected))
        for _score, name in scored[:room]:
            selected.add(name)

        # If selection didn't actually reduce (rare), just return full (fail-open).
        if len(selected) >= total:
            self._last_surfaced = total
            return list(self._defs)
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
        the fail-open floor: no embed model / embed error → still works, just
        keyword-only. Discovery is generous (no score gate, unlike selection)."""
        q = (query or "").strip()
        qwords = set(re.findall(r"\w+", q.lower()))
        query_vec = None
        embed_fn, model = _active_embedder()
        if embed_fn is not None and q:
            try:
                query_vec = embed_fn(q)
            except Exception:
                query_vec = None
            if query_vec is not None:
                try:
                    self._ensure_embeddings(embed_fn, model)
                except Exception:
                    query_vec = None
        scored: list[tuple[float, str]] = []
        for name, d in self._by_name.items():
            hay = f"{name} {getattr(d, 'description', '') or ''}".lower()
            haywords = set(re.findall(r"\w+", hay))
            kw = (len(qwords & haywords) / len(qwords)) if qwords else 0.0
            substr = 0.5 if q and q.lower() in hay else 0.0
            sem = 0.0
            if query_vec is not None:
                vec = self._embed_cache.get(name)
                if vec:
                    sem = _cosine(query_vec, vec)
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
