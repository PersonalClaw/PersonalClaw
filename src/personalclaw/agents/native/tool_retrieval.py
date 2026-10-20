"""Per-turn tool retrieval — a thin sibling of skills surfacing.

Stop riding the **entire** tool-schema set on every model turn. Surface only a
per-turn relevant projection: a small always-include CORE ∪ top-K by
``max(cosine(query, tool_embedding), keyword_overlap)`` ∪ structural hints
(a URL in the turn → web/fetch tools; "remind me", "every Monday" → the schedule tools and
``automation_create``; "when a new file lands in …" → ``automation_create``; "my automations" →
``automation_list``; a handoff, a standup, "this week" → ``chat_search``) ∪ the **sticky
set** (tools already CALLED this session stay available). Does for tools what
:mod:`skills.surfacing` does for skills.

**The tools' vectors come from the process's index** (:mod:`tool_vectors`), never from the turn:
kept across restarts, filled in the background in batches, and read without a network call. A
tool whose vector is not there yet ranks by its words. :meth:`ToolRetriever.select` still embeds
the query (one request), so its caller runs it off the event loop.

**An Incognito or Temporary chat's request is never embedded.** Its turn, a side question asked
beside it and every subagent it starts rank their tools by their words alone, as a gateway with no
embedding model bound does: the embedding functions hand such work's text to no model but the one
the chat runs on (``memory_writes.model_may_read``), so the query gets no vector. The ranking
otherwise holds: the core tools, the ones already called, the hinted ones and the best keyword
matches ride in full within the turn's budget, and every other tool is in the catalog, which
``tool_search`` ranks by the same words.

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

**The catalog of deferred tools names every one of PersonalClaw's own first** (:meth:`ToolRetriever.
catalog`). It filled its room provider by provider in name order, and every external server's tools
sit under ``mcp``, which sorts before ``personalclaw-*``: with six servers set up, every platform
group (tasks, knowledge, workflows, automations, …) was cut to a count, and a question about the
owner's automations was answered without ``automation_list``, which the turn never named. Now each
of the platform's groups (:func:`catalog_groups`) says what it is for and names every tool it has;
the servers, one group each, and the installed apps share the room that leaves, the tools the
request is about first and then a line each in turn; and a last line says what was left out. Where
the turn's ranking breaks a tie, the platform's own tool goes first, in the schemas a turn carries
and in ``tool_search``'s answer alike.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from personalclaw.agents.native.tool_vectors import (
    bound_embedder,
    default_path,
    tool_text,
    tool_vectors,
)
from personalclaw.task_modes import SHELL_TOOL_NAMES
from personalclaw.token_estimate import NOMINAL_CHARS_PER_TOKEN
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition
from personalclaw.tool_providers.registry import MCP_NAMESPACE

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

#: Work over a stretch of recent time, or something said before: a handoff, a standup, a weekly
#: review, "catch me up", "what did we decide". What only the user's earlier chats may hold, so
#: such a request names ``chat_search``.
_RECENT_WORK = (
    r"\bhand[- ]?offs?\b|\bstand[- ]?ups?\b|\brecaps?\b|\bretro(spective)?s?\b"
    r"|\bcatch\s+(me\s+)?up\b|\bstatus\s+(update|report)s?\b"
    r"|\b(weekly|monthly|daily)\s+(review|report|summary|update)s?\b"
    r"|\b(this|last|past|previous)\s+(week|month|sprint|quarter|shift)\b"
    r"|\b(last|past|previous)\s+\d+\s+(days?|weeks?|months?)\b|\byesterday\b"
    r"|\b(earlier|previous|past|other|last)\s+(chats?|conversations?|sessions?|threads?)\b"
    r"|\bwe\s+(discuss|talk|decid|agree|settl)\w*|\bi\s+(told|asked|showed)\s+you\b"
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
    # "How are my automations doing?", "did my automations run?": the one that says how each
    # stands and how its last run went, beside the one that makes them.
    (r"\bautomations?\b", ("automation_list",)),
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
    (r"\bremember|\brecall|\bmemor|\blesson", ("memory", "recall", "lesson", "chat_search")),
    (_RECENT_WORK, ("chat_search",)),
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


#: What a turn that defers schemas says above its catalog (:meth:`ToolRetriever.catalog`), whose
#: layout it describes.
CATALOG_NOTE = (
    "[tool catalog] To save context, only the most relevant tools above carry their full input "
    "schema this turn. The others are listed below by group. PersonalClaw's own come first: each "
    "group says what it is for and names every tool it has, with a description where there is "
    "room. Your tool servers and apps follow as room allows, and a last line names what is not "
    'listed. To use a tool, call tool_schema("name") to see its inputs, then call it, or call '
    'tool_search("capability") to rank every tool, listed or not. Every tool is fully '
    "available; nothing is disabled."
)

#: How much of a deferred tool's description its catalog line keeps (its first line, to here).
_DESC_CHARS = 100

#: How much of what one of the platform's groups is for its catalog header keeps.
_PURPOSE_CHARS = 100

#: How the catalog's last line starts, when there was not room to list everything.
_LEFT_OUT = '[not listed, for room: tool_search("what you need") finds any of them] '


@dataclass(frozen=True)
class CatalogGroup:
    """Where a turn's catalog lists a tool whose schema it defers: under ``key``, in one of
    PersonalClaw's own groups (``own``) or in a tool server's or an app's, and what the group is
    for (``purpose``, said for the platform's own)."""

    key: str
    own: bool = False
    purpose: str = ""


def _server_group(name: str) -> str:
    """The group an external MCP server's tool is listed under: its server (``mcp/<server>``)."""
    return "/".join(name.split("/", 2)[:2])


def _default_group(name: str, d: Any) -> CatalogGroup:
    """Where a tool is listed when its session said nothing of it: an MCP server's under the
    server, any other under the provider it names; none of them as one of the platform's own."""
    if name.startswith(MCP_NAMESPACE):
        return CatalogGroup(_server_group(name))
    return CatalogGroup(getattr(d, "provider", "") or "other")


def _purpose_line(text: str) -> str:
    """What a group is for, from its app's description: the first sentence, before any dash that
    starts its list of examples, cut at a word to :data:`_PURPOSE_CHARS`."""
    line = " ".join((text or "").split())
    line = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
    line = line.split(" — ", 1)[0].rstrip(" .")
    if len(line) <= _PURPOSE_CHARS:
        return line
    cut = line[: _PURPOSE_CHARS - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def _purpose_of(provider: Any) -> str:
    """What the tools *provider* serves are for, in the words of the app that ships it (the ones
    the Tools page and the Store show), else its display name."""
    from personalclaw.tool_providers.registry import app_of

    name = str(getattr(provider, "name", "") or "")
    app = app_of(name)
    if app:
        try:
            from personalclaw.providers.registry import get_provider_registry

            ext = get_provider_registry().get(app)
        except Exception:  # noqa: BLE001 - a group with no purpose is still listed by its name
            logger.debug("tool catalog: could not read the app %r", app, exc_info=True)
            ext = None
        said = str(getattr(getattr(ext, "manifest", None), "description", "") or "")
        if said.strip():
            return _purpose_line(said)
    display = str(getattr(provider, "display_name", "") or "")
    return _purpose_line(display) if display and display != name else ""


def catalog_groups(served: Iterable[tuple[Any, Iterable[Any]]]) -> dict[str, CatalogGroup]:
    """Where a session's catalog lists each tool its providers serve (``registry.serve``'s pairs),
    when the turn defers its schema.

    PersonalClaw's own tools, the platform's and those of every provider it ships
    (``registry.ships_with_core``), are listed under their provider, with what it is for. An MCP
    server's are listed under the server and an installed app's under its provider. The MCP Tool
    Servers app and ``app-routes`` ship with PersonalClaw, but what they serve is the servers' and
    the apps' own.
    """
    from personalclaw.tool_providers.app_routes import PROVIDER_NAME as APP_ROUTES
    from personalclaw.tool_providers.registry import ships_with_core

    out: dict[str, CatalogGroup] = {}
    for provider, tools in served:
        key = str(getattr(provider, "name", "") or "") or "other"
        own = key != APP_ROUTES and ships_with_core(provider)
        group = CatalogGroup(key, own=True, purpose=_purpose_of(provider)) if own else None
        for tool in tools:
            name = str(getattr(tool, "name", "") or "")
            if name.startswith(MCP_NAMESPACE):
                out[name] = CatalogGroup(_server_group(name))
            else:
                out[name] = group or CatalogGroup(key)
    return out


@dataclass
class _Block:
    """One group's part of a catalog: its deferred tools (most relevant first, then by name),
    whether it is listed under a header of its own yet, and which tools carry a description. The
    sizes are what its text would take, kept as it changes so a turn never renders to measure."""

    group: CatalogGroup
    tools: list[str]
    listed: bool = False
    described: set[str] = field(default_factory=set)
    #: The header's characters, the described lines' (each with the newline before it), and the
    #: names of the tools not described: how many, and their characters.
    head: int = 0
    said: int = 0
    rest: int = 0
    rest_chars: int = 0


class _Listing:
    """A catalog as it is fitted to its room: each change is kept only while the whole text fits.

    A listed block is its header, a line for each tool it describes and, for one of the
    platform's own groups, a line naming the rest. What is not listed is counted on the last line.
    The text is rendered once, at the end, from what was kept.
    """

    def __init__(self, blocks: list[_Block], lines: Mapping[str, str], max_chars: int) -> None:
        self._blocks = blocks
        self._lines = lines
        self._max = max_chars
        for b in blocks:
            b.head = len(self._header(b))
            b.rest, b.rest_chars = len(b.tools), sum(len(n) for n in b.tools)
        # The listed blocks' characters and count, and the last line's entries' characters and
        # count: what the whole text takes, without the text.
        self._listed = (0, 0)
        entries = [self._entry(b) for b in blocks]
        self._entries = (sum(len(e) for e in entries), sum(1 for e in entries if e))

    @staticmethod
    def _header(b: _Block) -> str:
        return f"[{b.group.key}]" + (f" {b.group.purpose}" if b.group.purpose else "")

    def _line(self, name: str) -> str:
        said = self._lines.get(name, "")
        return f"- {name}: {said}" if said else f"- {name}"

    @staticmethod
    def _entry(b: _Block) -> str:
        """What the last line says of *b*: all of it when it is not listed, how much of it is not
        when one of the servers' or apps' groups is listed in part, else nothing."""
        if not b.listed:
            n = len(b.tools)
            return f"{b.group.key} ({n} tool{'' if n == 1 else 's'})"
        if not b.group.own and b.rest:
            return f"{b.group.key} (+{b.rest} more)"
        return ""

    @staticmethod
    def _size_of(b: _Block) -> int:
        named = 3 + b.rest_chars + 2 * (b.rest - 1) if b.group.own and b.rest else 0
        return b.head + b.said + named

    def _total(self, listed: tuple[int, int], entries: tuple[int, int]) -> int:
        last = len(_LEFT_OUT) + entries[0] + 2 * (entries[1] - 1) if entries[1] else 0
        parts = listed[1] + (1 if entries[1] else 0)
        return listed[0] + last + max(0, parts - 1)

    def keep(self, b: _Block, describe: str = "") -> bool:
        """List *b* (describing *describe* too, when given) if the whole catalog still fits."""
        if describe and describe in b.described:
            return True
        size, entry, listed = (self._size_of(b) if b.listed else 0), self._entry(b), b.listed
        saved = (b.said, b.rest, b.rest_chars)
        b.listed = True
        if describe:
            b.described.add(describe)
            b.said += 1 + len(self._line(describe))
            b.rest, b.rest_chars = b.rest - 1, b.rest_chars - len(describe)
        now = self._entry(b)
        kept = (self._listed[0] - size + self._size_of(b), self._listed[1] + (not listed))
        entries = (
            self._entries[0] - len(entry) + len(now),
            self._entries[1] - bool(entry) + bool(now),
        )
        if self._total(kept, entries) <= self._max:
            self._listed, self._entries = kept, entries
            return True
        b.listed = listed
        b.described.discard(describe)
        b.said, b.rest, b.rest_chars = saved
        return False

    def in_turn(self, blocks: list[_Block]) -> None:
        """Describe *blocks*' tools one a block a round, so none of them crowds out the others."""
        queues = [[n for n in b.tools if n not in b.described] for b in blocks]
        while any(queues):
            for b, queue in zip(blocks, queues, strict=True):
                if queue:
                    self.keep(b, queue.pop(0))

    def _render(self, b: _Block) -> str:
        out = [self._header(b), *(self._line(n) for n in b.tools if n in b.described)]
        rest = sorted(n for n in b.tools if n not in b.described)
        if b.group.own and rest:
            out.append("- " + ", ".join(rest))
        return "\n".join(out)

    def text(self) -> str:
        """The catalog. Only a room too small for even the last line cuts that line short."""
        entries = [e for e in (self._entry(b) for b in self._blocks) if e]
        parts = [self._render(b) for b in self._blocks if b.listed]
        out = "\n".join([*parts, _LEFT_OUT + ", ".join(entries)] if entries else parts)
        if len(out) <= self._max:
            return out
        return "" if self._max <= len(_LEFT_OUT) else out[: self._max - 1].rsplit(", ", 1)[0] + "…"


class ToolRetriever:
    """Per-turn tool selector over the session's catalog (built again when the tools change).

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
        carry_from: ToolRetriever | None = None,
        groups: Mapping[str, CatalogGroup] | None = None,
    ) -> None:
        """*carry_from* is this session's retriever over its last catalog, when the catalog was
        rebuilt because the tools changed: what it learned (the tools the agent called, and the
        hints carried into the next turn) carries over as far as this catalog still has them, so
        a rebuild never loses a tool the agent is in the middle of using.

        *groups* says where the catalog lists each tool and which are PersonalClaw's own
        (:func:`catalog_groups`); a tool it does not name is listed by its provider, as no one's
        own."""
        self._defs = list(defs)
        self._k = max(1, int(k))
        self._threshold = semantic_threshold
        self._by_name = {getattr(d, "name", ""): d for d in self._defs}
        given = groups or {}
        self._group = {n: given.get(n) or _default_group(n, d) for n, d in self._by_name.items()}
        # What the last request made of each tool its turn ranked (`_select`): the catalog lists
        # the deferred ones it is about first.
        self._relevance: dict[str, float] = {}
        self._lines = {
            n: (getattr(d, "description", "") or "").strip().split("\n", 1)[0][:_DESC_CHARS]
            for n, d in self._by_name.items()
        }
        # core = tools that must never be filtered out (control/orientation).
        self._core = {n for n, d in self._by_name.items() if _is_core(n, d)}
        self._sticky: set[str] = (
            {n for n in carry_from._sticky if n in self._by_name} if carry_from else set()
        )
        # The tools the last request's hints named, carried into ONE next turn when that turn's
        # request names none: "It's ~/Notes/…" answers the question the agent asked about the
        # request before it, and the automation that request plainly needed is still the task.
        self._carried: set[str] = (
            {n for n in carry_from._carried if n in self._by_name} if carry_from else set()
        )
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

    def _rank(self, score: float, name: str) -> tuple[float, bool, str]:
        """The order tools are taken in: the higher score first and, between equals, one of
        PersonalClaw's own before a server's or an app's, then by name."""
        return (-score, not self._group[name].own, name)

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

        ``restrict`` limits selection to those tool names — the tool-GROUP seam:
        retrieval selects *within* the active groups, so
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
            self._relevance = {}
            return pool  # no-op: everything fits

        q = (query or "").strip()
        # never surface a tool outside the candidate pool
        core = self._core & pool_names
        sticky = (self._sticky & pool_names) - core
        hinted = self._structural(q)
        carried, self._carried = self._carried, hinted
        structural = ((hinted or carried) & pool_names) - core - sticky
        scores = self._scores(q, pool_names - core)
        # A called or hinted tool the schema budget leaves out is the first the catalog lists.
        self._relevance = {**scores, **{n: 1.0 + scores.get(n, 0.0) for n in sticky | structural}}

        def ranked(names) -> list[str]:
            return sorted(names, key=lambda n: self._rank(scores.get(n, 0.0), n))

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

    def search(
        self, query: str, limit: int = 20, shown: Callable[[str], bool] | None = None
    ) -> list[dict]:
        """Rank the ENTIRE catalog against ``query`` and return ``[{name,
        description}]`` — backs the ``tool_search`` meta-tool. Scores by
        ``max(semantic cosine, lexical overlap, substring)`` — the SAME semantic
        path :meth:`_select` uses, so discovery finds a tool by capability even
        with no keyword overlap ("resize an image" → an image tool). Lexical is
        the fail-open floor: no embed model / embed error / no vector yet → still
        works, just keyword-only. Discovery is generous (no score gate, unlike
        selection). Embeds the query, so the runtime calls it off the event loop.

        *shown* is the run's own check of which tools its model is shown: a tool it hides is left
        out before the answer is cut at *limit*, so the matches the run can use all count."""
        q = (query or "").strip()
        qwords = set(re.findall(r"\w+", q.lower()))
        try:
            semantic = self._semantic(q, set(self._by_name))
        except Exception:
            logger.debug("tool search: semantic ranking failed — lexical only", exc_info=True)
            semantic = {}
        scored: list[tuple[float, str]] = []
        for name, d in self._by_name.items():
            if shown is not None and not shown(name):
                continue
            hay = f"{name} {getattr(d, 'description', '') or ''}".lower()
            haywords = set(re.findall(r"\w+", hay))
            kw = (len(qwords & haywords) / len(qwords)) if qwords else 0.0
            substr = 0.5 if q and q.lower() in hay else 0.0
            sem = semantic.get(name, 0.0)
            score = max(kw, substr, sem)
            if score > 0 or not q:
                scored.append((score, name))
        scored.sort(key=lambda t: self._rank(*t))
        out = []
        for _s, name in scored[:limit]:
            d = self._by_name[name]
            out.append({"name": name, "description": (getattr(d, "description", "") or "")[:200]})
        return out

    def catalog(self, *, exclude: set[str] | None = None, max_chars: int = 6000) -> str:
        """The tools NOT in ``exclude`` (the turn's surfaced set), by group, in at most
        ``max_chars``: progressive disclosure, so the model knows a tool is there when its schema
        was deferred (:data:`CATALOG_NOTE` says how it reads).

        The room goes, in this order, to whatever still fits:

        1. each of PersonalClaw's own groups: a header saying what it is for, and every tool it has
           by name;
        2. a description for each tool the request is about (:meth:`select`'s ranking), the most
           relevant first, whichever group it is in;
        3. the tool servers and apps, one described tool a group per round, so that no server
           crowds out the rest the way the first in name order used to;
        4. descriptions for the rest of the platform's own tools, one a group per round.

        A last line names each group, or the part of one, left out, and points at ``tool_search``.
        """
        exclude = exclude or set()
        rel = self._relevance
        blocks: dict[str, _Block] = {}
        for name in self._by_name:
            if name not in exclude:
                group = self._group[name]
                blocks.setdefault(group.key, _Block(group, [])).tools.append(name)
        if not blocks:
            return ""
        for b in blocks.values():
            b.tools.sort(key=lambda n: (-rel.get(n, 0.0), n))
        order = sorted(blocks.values(), key=lambda b: (not b.group.own, b.group.key))
        listing = _Listing(order, self._lines, max_chars)
        own = [b for b in order if b.group.own]
        for b in sorted(own, key=lambda b: -max(rel.get(n, 0.0) for n in b.tools)):
            listing.keep(b)
        block_of = {n: b for b in order for n in b.tools}
        relevant = [n for n in block_of if rel.get(n, 0.0) > 0]
        for name in sorted(relevant, key=lambda n: self._rank(rel[n], n)):
            listing.keep(block_of[name], name)
        listing.in_turn([b for b in order if not b.group.own])
        listing.in_turn(own)
        return listing.text()


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


def tool_search_definition() -> ToolDefinition:
    """Synthetic schema for the tool_search escape hatch, answered by the runtime from this
    module's retriever (:meth:`ToolRetriever.search`), not by a provider."""
    return ToolDefinition(
        name="tool_search",
        provider="native",
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
        description=(
            "Find tools by capability. Searches the FULL catalog (incl. tools shown "
            "this turn only as a name in the catalog). Args: query (str), optional "
            "limit (int). Returns ranked name+description; then call tool_schema(name) "
            "to see a tool's inputs, or just call it by name."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    )


def tool_schema_definition() -> ToolDefinition:
    """Progressive disclosure: tools not in the per-turn full-schema set still appear in a
    name+description CATALOG (:meth:`ToolRetriever.catalog`). tool_schema expands ONE of them to
    its full input schema on demand, so the model can call any catalog tool correctly without
    ever carrying every schema."""
    return ToolDefinition(
        name="tool_schema",
        provider="native",
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
        description=(
            "Get the full input schema for a tool by name — use when the catalog lists "
            "a tool you want but you need its exact arguments. Args: tool_name (str). "
            "Returns the tool's parameters/description; then call the tool by name."
        ),
        parameters={
            "type": "object",
            "properties": {"tool_name": {"type": "string"}},
            "required": ["tool_name"],
        },
    )
