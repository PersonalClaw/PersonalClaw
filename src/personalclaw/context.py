"""Context builder — assembles memory, skills, and hooks into prompt context."""

import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from personalclaw.agent import _shipped_prompt
from personalclaw.config.loader import AppConfig, memory_dir_for_cwd
from personalclaw.hooks import (
    HOOK_INJECT_CONTEXT,
    HOOK_MODIFY,
    HookManager,
    HookResult,
    safe_read_file,
)
from personalclaw.learn import LessonStore
from personalclaw.memory import MemoryStore
from personalclaw.schedule import get_local_tz
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.skills import SkillsLoader

if TYPE_CHECKING:
    from personalclaw.channel_history import ChannelHistory
    from personalclaw.history import ConversationLog
    from personalclaw.session import SessionManager


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".personalclaw"


logger = logging.getLogger(__name__)

# Lazy cache of MemoryStore instances keyed by cwd-partition path.
_memory_stores: dict[str, MemoryStore] = {}
# Lazy cache of LessonStore instances keyed by cwd-partition path.
_lesson_stores: dict[str, LessonStore] = {}


def _attach_vector_store(store: MemoryStore, ws_path) -> None:
    """Give a cwd-scoped MemoryStore its own semantic/episodic vector index.

    The index lives inside the partition dir so each working directory has
    isolated semantic memory. Wired to the active embedding model (Settings >
    Models); if no embedding model is active, the store stays text-only.
    """
    try:
        from personalclaw.embedding_providers.registry import (
            get_active_embed_fn,
            get_active_embedding_dim,
        )
        from personalclaw.vector_memory import VectorMemoryStore

        embed_fn = get_active_embed_fn()
        if embed_fn is None:
            return  # no active embedding model — semantic memory stays off
        vs = VectorMemoryStore(
            db_path=ws_path / "memory_index.db",
            embedding_dim=get_active_embedding_dim() or 384,
        )
        vs.init()
        vs.embed_fn = embed_fn
        vs.contradiction_judge = _make_contradiction_judge()
        store.vector_store = vs
    except Exception:
        logger.debug("Could not attach vector store for %s", ws_path, exc_info=True)


def _make_contradiction_judge():
    """Build the lesson contradiction judge (or None).

    Returns a sync ``(new_rule, existing_rule) -> bool`` that asks the background
    LLM whether the new lesson contradicts the existing one. Safe-by-construction:
    if called from within a running event loop (where a nested ``asyncio.run``
    would fail) it returns False — keep both — so it never crashes a turn; the
    judge runs on the sync write paths (consolidation thread, CLI) where it can.
    """

    def _judge(new_rule: str, existing_rule: str) -> bool:
        import asyncio

        try:
            asyncio.get_running_loop()
            return False  # in an async context → fail-safe: keep both
        except RuntimeError:
            pass  # no running loop → safe to run the completion
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        prompt = render_use_case_prompt(
            "contradiction_judge",
            {"new_rule": new_rule, "existing_rule": existing_rule},
        )
        if not prompt:
            return False
        try:
            import asyncio as _aio

            from personalclaw.llm_helpers import one_shot_completion

            resp = _aio.run(one_shot_completion(prompt, use_case="background"))
            return "CONTRADICT" in resp.upper()
        except Exception:
            logger.debug("contradiction judge LLM call failed — keeping both", exc_info=True)
            return False

    return _judge


# Cap injected context to avoid blowing the context window on first turn
_MAX_CONTEXT_CHARS = 165_000  # ~55k tokens

# ACP agent slices strings at fixed byte offsets (e.g. 4096).
# Multi-byte UTF-8 chars straddling the boundary cause a Rust panic:
#   "byte index 4096 is not a char boundary; it is inside '—'"
# Workaround: replace common multi-byte punctuation with ASCII equivalents.
# NOTE: truncate_safe workaround for ACP agent string slicing.
_MULTIBYTE_TABLE = str.maketrans(
    {
        "\u2014": "--",  # em dash
        "\u2013": "-",  # en dash
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2026": "...",  # ellipsis
        "\u00a0": " ",  # non-breaking space
        "\u2022": "-",  # bullet
        "\u2192": "->",  # rightwards arrow (→) — ACP agent string slicing workaround
        "\u2190": "<-",  # leftwards arrow (←)
        "\u2194": "<->",  # left right arrow (↔)
        "\u21d2": "=>",  # rightwards double arrow (⇒)
        "\u2713": "[x]",  # check mark (✓)
        "\u2717": "[ ]",  # ballot x (✗)
        "\u00d7": "x",  # multiplication sign (×)
        # Known gap: accented chars (e.g. \u00e9) and emoji are not replaced here.
        # They are legitimate content; stripping them would be lossy. The real fix
        # is the ACP agent truncate_safe behavior.
    }
)

# Soft per-component caps: each component is individually truncated to its
# cap, then the assembled context is hard-truncated at _MAX_CONTEXT_CHARS.
# No single component may exceed 30% of the hard cap to prevent any one
# category from dominating. The sum of soft caps (~145k) is under the hard
# cap (165k), so all components can coexist without silent truncation.
_HISTORY_BUDGET_CHARS = 35_000  # thread history (fallback/truncated)
_CROSS_TAB_BUDGET_CHARS = 6_000  # sibling dashboard sessions
# Memory-injection per-section caps. These are the BASELINE (calibrated for a 200k-
# token window); mem-adaptive-budget scales them proportionally to the resolved
# model's context window (via _memory_caps) so a 1M-window model recalls more and a
# 128k one less — clamped floor (the baseline) / ceiling (×5).
_MEMORY_PREFS_CAP = 4_000  # user preferences
_MEMORY_PROJECTS_CAP = 6_000  # active projects
_MEMORY_HISTORY_CAP = 25_000  # daily history (multi-tier decay)
_LESSONS_CAP = 35_000  # learned corrections (high priority)
_SEMANTIC_MEMORY_CAP = 12_000  # structured key-value facts (vector memory)
_EPISODIC_MEMORY_CAP = 12_000  # relevant past conversation fragments (vector memory)
_PER_MESSAGE_CAP = 8_000  # truncate individual messages on fallback path

# The window the baseline caps were calibrated for + the max multiple we scale to.
_BASELINE_WINDOW = 200_000
_MAX_BUDGET_MULTIPLE = 5.0


class _MemoryCaps(TypedDict):
    prefs_cap: int
    projects_cap: int
    history_cap: int
    semantic_cap: int
    episodic_cap: int


def _memory_caps(context_window: int | None) -> _MemoryCaps:
    """Per-section memory caps scaled to the resolved model window (mem-adaptive-budget).

    Baseline caps are calibrated for a 200k window; scale linearly by
    ``window / 200k``, clamped to [1.0, 5.0]× so a 1M-window model (e.g. Opus)
    recalls ~5× more while a small model stays at the safe baseline. ``None``/unknown
    → the baseline (no regression). History stays the dominant section (its cap is
    largest), preserving the current section balance across the scale."""
    win = context_window or _BASELINE_WINDOW
    mult = max(1.0, min(_MAX_BUDGET_MULTIPLE, win / _BASELINE_WINDOW))
    return {
        "prefs_cap": int(_MEMORY_PREFS_CAP * mult),
        "projects_cap": int(_MEMORY_PROJECTS_CAP * mult),
        "history_cap": int(_MEMORY_HISTORY_CAP * mult),
        "semantic_cap": int(_SEMANTIC_MEMORY_CAP * mult),
        "episodic_cap": int(_EPISODIC_MEMORY_CAP * mult),
    }


# Strip Mode Identity blocks from injected context so cross-tab or history
# content from a different mode doesn't override the current prompt's identity.
_MODE_IDENTITY_RE = re.compile(r"## 🔒 Mode Identity.*?(?=\n## |\Z)", re.DOTALL)
_COMPRESSED_HISTORY_CAP = 45_000  # budget for LLM-compressed thread summary


_STOP_EVENT_CAP = 3  # max recent stop events to inject into LLM context
_STOP_EVENT_RESOLVED_STATES = frozenset({"stopped", "stop_failed_reset"})


def _build_stop_event_notes(conversation_log: "ConversationLog", session_key: str) -> str:
    """Render recent resolved stop_events as short system notes for LLM context."""
    # Bound the scan: only the last _STOP_EVENT_CAP stop events matter,
    # and stop events from hundreds of turns ago are not actionable context.
    # Matches the pattern used by ``build_cancelled_turn_preamble`` below.
    messages = conversation_log.recent(session_key, max_messages=20)
    notes: list[str] = []
    for m in reversed(messages):
        if len(notes) >= _STOP_EVENT_CAP:
            break
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            continue
        if (
            isinstance(data, dict)
            and data.get("kind") == "stop_event"
            and data.get("state") in _STOP_EVENT_RESOLVED_STATES
        ):
            notes.append("[User stopped the previous turn mid-execution.]")
    if not notes:
        return ""
    notes.reverse()
    return "\n".join(notes) + "\n\n"


# Budget tradeoff: 100 user+assistant msgs covers P90 of sessions.
# Role filtering excludes tool display titles, so the budget is spent
# on actual conversation content.
_COMPRESSION_MAX_MESSAGES = 100
_HEAD_TAIL_MESSAGES = 2  # verbatim head/tail kept around compressed middle

# Display names for runtime environments, keyed by the source tag from
# sel.py _infer_source().  Kept here so the mapping is close to the
# injection site and easy to extend.
_RUNTIME_DISPLAY = {
    "dashboard": "PersonalClaw dashboard",
    "cron": "PersonalClaw cron job",
    "subagent": "PersonalClaw subagent",
    "background": "PersonalClaw background",
    "cli": "CLI terminal",
    "channel": "messaging channel",
}


def _runtime_display_name(session_key: str) -> str:
    """Map a session_key to a human-readable runtime name.

    Uses the same prefix heuristic as ``sel.py:_infer_source()`` so both
    SEL audit logs and LLM context agree on the runtime.
    """
    if session_key.startswith("dashboard:") or session_key.startswith("dashboard_"):
        source = "dashboard"
    elif session_key.startswith("cron:") or session_key.startswith("cron_"):
        source = "cron"
    elif session_key.startswith("subagent:"):
        source = "subagent"
    elif session_key == "_bg":
        source = "background"
    elif session_key == "cli_chat":
        source = "cli"
    else:
        source = "channel"
    return _RUNTIME_DISPLAY.get(source, source)


def _prompt_use_case_for(session_key: str | None, explicit: str = "") -> str:
    """The prompt use-case for a session. An explicit non-default value wins;
    otherwise derive from the session_key prefix (background/subagent/cron → the
    ``background`` prompt; code/loop workers → ``code``/``goal_loop``). Defaults
    to ``chat``."""
    if explicit and explicit != "chat":
        return explicit
    sk = session_key or ""
    if sk.startswith("code:") or sk.startswith("code_"):
        return "code"
    if sk.startswith("loop:") or sk.startswith("loop_") or sk.startswith("campaign"):
        return "goal_loop"
    if (
        sk == "_bg"
        or sk.startswith("cron:")
        or sk.startswith("cron_")
        or sk.startswith("subagent:")
    ):
        return "background"
    return explicit or "chat"


def _resolve_use_case_prompt(use_case: str) -> str:
    """Resolve the default-agent system prompt bound to ``use_case`` via the
    prompt provider. Returns "" if it can't resolve (caller falls back to file)."""
    try:
        from personalclaw.providers.prompt_use_cases import resolve_prompt_content

        return resolve_prompt_content(use_case) or ""
    except Exception:
        logger.debug("use-case prompt resolution failed for %r", use_case, exc_info=True)
        return ""


def _snippet_resolver():
    """A name → PromptSnippet resolver for the render engine's ``{{> name}}``
    includes, backed by the default prompt provider. Returns a callable that
    yields None for any name when no provider is available (the engine then
    renders an explicit ``[missing snippet: name]`` marker)."""
    try:
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
    except Exception:
        provider = None
    return lambda n: provider.get_snippet(n) if provider is not None else None


# Critical rules reinforced every session (supplements the system prompt)
# The always-on critical rules live in the prompt system (bundled ``critical-rules``
# snippet, bindable/editable in Settings → Prompts). Rendered fresh each session by
# ``build_session_context`` via the prompt engine.


# Regex patterns for noise compression in assistant messages
_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_JSON_BLOB_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _compress_assistant_message(text: str) -> str:
    """Reduce low-signal noise from assistant messages on the fallback path.

    Code blocks over 2K chars are replaced with a head/tail excerpt that
    preserves function signatures, imports, and structure.  JSON blobs
    over 1K chars are replaced with a truncation marker.
    """

    def _replace_code_block(m: re.Match[str]) -> str:
        body = m.group(1)
        if len(body) <= 2000:
            return m.group(0)
        lines = body.strip().splitlines()
        if len(lines) > 15:
            kept = lines[:10] + [f"  ... ({len(lines) - 15} lines omitted)"] + lines[-5:]
        else:
            # Few lines but still over 2K — apply character-level truncation
            truncated_body = body[:2000]
            kept = truncated_body.splitlines()
            kept.append(f"  ... ({len(body) - 2000} chars truncated)")
        lang_line = m.group(0).split("\n", 1)[0]  # ```lang
        return lang_line + "\n" + "\n".join(kept) + "\n```"

    result = _CODE_BLOCK_RE.sub(_replace_code_block, text)

    def _replace_json(m: re.Match[str]) -> str:
        if len(m.group(0)) <= 1000:
            return m.group(0)
        return "[tool output truncated]"

    result = _JSON_BLOB_RE.sub(_replace_json, result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def build_cancelled_turn_preamble(
    conversation_log: "ConversationLog",
    session_key: str,
    *,
    user_cap: int = 2000,
    assist_cap: int = 2000,
) -> str:
    """Build a preamble describing the most recent cancelled turn, if any.

    ACP agent does not persist cancelled turns to its conversation log,
    so after a soft-stop the LLM has no memory of what the user asked or
    what it had started saying. Scan the persisted ``conversation_log``
    backwards for a ``stop_event`` marker, then find the user message
    immediately before it plus any assistant text in between. Return a
    short bracketed preamble. Returns "" if nothing to inject.

    Called by both dashboard and channel callers after ``prev_turn_cancelled``
    is observed on the session.
    """
    try:
        recent = conversation_log.recent(session_key, max_messages=20)
    except Exception:
        return ""
    if not recent:
        return ""
    # Look for a stop_event marker (dashboard writes these; channels do not).
    # If present, it bounds the cancelled turn. Otherwise fall back to "last
    # user turn" — safe because (a) ``prev_turn_cancelled`` is a one-shot
    # flag consumed right before this function runs, and (b) callers persist
    # the NEW user message to ``conversation_log`` only AFTER the preamble
    # is built (see handler.py save_conversation_turn / chat.py _flush_segment),
    # so ``recent()`` at this moment contains only prior turns and the most
    # recent user entry is the cancelled one.
    stop_idx = -1
    for i in range(len(recent) - 1, -1, -1):
        if recent[i].get("role") != "system":
            continue
        content = recent[i].get("content", "")
        if not isinstance(content, str) or not content:
            continue
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("kind") == "stop_event":
                stop_idx = i
                break
        except (ValueError, TypeError):
            continue
    # Find the most recent user message. If a stop_event was found, the user
    # message must precede it; otherwise just take the latest user entry.
    search_end = stop_idx if stop_idx >= 0 else len(recent)
    user_idx = -1
    for i in range(search_end - 1, -1, -1):
        if recent[i].get("role") == "user":
            user_idx = i
            break
    if user_idx < 0:
        return ""
    # Collect any assistant text between user_idx and the boundary.
    boundary = stop_idx if stop_idx >= 0 else len(recent)
    user_text = (recent[user_idx].get("content") or "").strip()
    assistant_parts: list[str] = []
    for i in range(user_idx + 1, boundary):
        if recent[i].get("role") == "assistant":
            t = (recent[i].get("content") or "").strip()
            if t:
                assistant_parts.append(t)
    assistant_text = "\n".join(assistant_parts)
    if len(user_text) > user_cap:
        user_text = user_text[:user_cap] + "… [truncated]"
    if len(assistant_text) > assist_cap:
        assistant_text = assistant_text[:assist_cap] + "… [truncated]"
    # The preamble lives in the prompt system (bundled ``cancelled-turn-preamble``
    # snippet); its conditional includes the partial assistant response when present.
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block(
        "cancelled-turn-preamble",
        {"user_text": user_text, "assistant_text": assistant_text},
    )


async def compress_thread_history(
    conversation_log: "ConversationLog",
    session_key: str,
    query: str,
    sessions: "SessionManager",
) -> str | None:
    """Compress full thread history via background LLM call.

    ``is_new`` in callers means a new ACP agent process (or dashboard tab)
    attached to an *existing* channel thread — not a brand-new conversation.
    The thread already has history from prior processes, so we compress it
    to fit within the context window of the fresh session.

    Returns the compressed summary string, or None on failure (callers
    fall back to raw truncation).  This is the ONLY async function in
    this module — callers await it and pass the result into the sync
    ``build_session_context`` / ``build_message`` methods.

    The output uses a head/tail pattern: the first and last
    ``_HEAD_TAIL_MESSAGES`` are kept verbatim while the middle is
    LLM-compressed, preserving both conversation opening context and
    the most recent exchanges.
    """
    from personalclaw.llm_helpers import stream_and_collect  # circular import
    from personalclaw.session import BACKGROUND_KEY  # circular import

    recent = conversation_log.recent(
        session_key,
        max_messages=_COMPRESSION_MAX_MESSAGES,
        roles={"user", "assistant"},
    )
    if not recent:
        return None

    lines: list[str] = []
    for m in recent:
        # Compression path: no per-message cap, no code stripping.
        # The LLM compressor sees full content and decides what to keep.
        lines.append(f"{m['role'].title()}: {m['content']}")
    transcript = "\n".join(lines)

    if len(transcript) <= _COMPRESSED_HISTORY_CAP:

        transcript, _ = redact_exfiltration_urls(transcript)
        transcript, _ = redact_credentials(transcript)
        return transcript.translate(_MULTIBYTE_TABLE)

    head_lines = lines[:_HEAD_TAIL_MESSAGES]
    tail_lines = lines[-_HEAD_TAIL_MESSAGES:] if len(lines) > _HEAD_TAIL_MESSAGES else []

    # The compression instruction lives in the prompt system (bundled
    # ``task-history-compression``, bindable in Settings → Prompts), rendered with
    # the target cap, latest query, and transcript.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt(
        "history_compression",
        {"cap": _COMPRESSED_HISTORY_CAP, "query": query, "transcript": transcript},
    )
    if not prompt:
        logger.debug("history compression prompt unresolved — skipping compression")
        return None

    acquired = False
    try:
        client, _is_new, _resumed = await sessions.get_or_create(
            BACKGROUND_KEY, agent="personalclaw-lite"
        )
        acquired = True
        result = await stream_and_collect(client, prompt)
        if not result:
            return None

        parts: list[str] = []
        if head_lines:
            parts.append("## Thread start (verbatim)\n" + "\n".join(head_lines))
        parts.append("## Compressed history\n" + result[:_COMPRESSED_HISTORY_CAP])
        if tail_lines:
            parts.append("## Recent exchanges (verbatim)\n" + "\n".join(tail_lines))
        final = "\n\n".join(parts)
        final, _ = redact_exfiltration_urls(final)
        final, _ = redact_credentials(final)
        return final.translate(_MULTIBYTE_TABLE)
    except Exception:
        logger.warning("Thread history compression failed", exc_info=True)
        return None
    finally:
        if acquired:
            sessions.release(BACKGROUND_KEY)
            await sessions.recycle_background()


class ContextBuilder:
    """Builds context for injection into ACP prompts.

    Assembles memory, skills, and hook-injected context into a single
    string that gets prepended to the user's message on the first turn
    of a session (or after a context reset).
    """

    @staticmethod
    def get_memory_for(cwd: str | None = None, memory_store: str | None = None):
        """Return the memory provider for a session.

        When *memory_store* names a registered memory provider (an agent's
        explicit ``memory_store`` binding), that provider is returned. Otherwise
        the filesystem-fallback ``MemoryStore`` is used, partitioned by the
        session's working directory (see ``memory_dir_for_cwd``) and cached per
        partition.

        ``memory_store`` is TWO namespaces that share the string: a registered
        *provider* name (e.g. ``"native"``) OR a ``config.memory_stores`` *tuning-
        profile* key (e.g. the seeded ``"default"``, which only carries TTL/cap
        overrides for the fallback store — NOT a provider binding). A profile key
        must resolve to the fallback SILENTLY; only a name that is neither a
        provider nor a known profile is genuinely dangling and worth a warning.
        (Fixes the false "memory_store 'default' not registered" logged on every
        default-agent turn — the seeded agent binds the ``default`` profile key.)
        """
        if memory_store:
            from personalclaw.memory_providers.registry import get_provider

            provider = get_provider(memory_store)
            if provider is not None:
                return provider
            if memory_store not in AppConfig.load().memory_stores:
                logger.warning(
                    "memory_store %r not registered; using filesystem fallback", memory_store
                )

        ws_path = memory_dir_for_cwd(cwd)
        key = str(ws_path)
        if key not in _memory_stores:
            store = MemoryStore(workspace=ws_path)
            store.init()
            _attach_vector_store(store, ws_path)
            _memory_stores[key] = store
        return _memory_stores[key]

    @staticmethod
    def get_lessons_for(cwd: str | None = None) -> LessonStore:
        """Return a LessonStore scoped to a working dir, creating lazily."""
        ws_path = memory_dir_for_cwd(cwd)
        key = str(ws_path)
        if key not in _lesson_stores:
            _lesson_stores[key] = LessonStore(base_dir=ws_path)
        return _lesson_stores[key]

    def __init__(
        self,
        memory: MemoryStore | None = None,
        skills: SkillsLoader | None = None,
        hooks: HookManager | None = None,
        lessons: LessonStore | None = None,
        conversation_log: "ConversationLog | None" = None,
        channel_history: "ChannelHistory | None" = None,
        bot_name: str = "",
    ):
        # Default (no-cwd) memory lives in the shared "_default" partition so it
        # matches what get_memory_for(None) resolves to.
        self.memory = memory or MemoryStore(workspace=memory_dir_for_cwd(None))
        self.skills = skills or SkillsLoader()
        self.hooks = hooks or HookManager()
        self.lessons = lessons or LessonStore(base_dir=memory_dir_for_cwd(None))
        self.conversation_log = conversation_log
        self.channel_history = channel_history
        # Explicit override (tests / embedders). Empty = resolve from config
        # LIVE per turn (property below) so a Settings → Account save takes
        # effect on the next message, not the next restart — same live-read
        # semantic as widget_density in _widget_block.
        self._bot_name_override = bot_name
        # Register this builder's memory in the cwd-partition cache under BOTH
        # the no-cwd "_default" key AND the running workspace key. The gateway
        # builds ContextBuilder with the MAIN vector store (~/.personalclaw/
        # memory.db — the one the Memory UI + consolidator read/write); without
        # the workspace-key registration, a dashboard chat (whose workspace_dir is
        # PERSONALCLAW_WORKSPACE) resolved a DIFFERENT, near-empty cwd partition,
        # so user-saved + consolidated memory was invisible to the agent in chat.
        # Genuinely different cwds (other projects, remote subagents) still get
        # their own partition via get_memory_for — only the gateway's own
        # workspace is unified onto the main store here.
        _memory_stores[str(memory_dir_for_cwd(None))] = self.memory
        try:
            from personalclaw.config.loader import default_workspace_dir

            _ws_key = str(memory_dir_for_cwd(default_workspace_dir()))
            _memory_stores.setdefault(_ws_key, self.memory)
        except Exception:
            logger.debug("Could not register memory under the workspace key", exc_info=True)

    @property
    def _bot_name(self) -> str:
        """The assistant display name — explicit override, else live config."""
        if self._bot_name_override:
            return self._bot_name_override
        try:
            return AppConfig.load().agent.bot_name or "PersonalClaw"
        except Exception:
            return "PersonalClaw"

    def _apply_runtime_vars(self, prompt: str, session_key: str) -> str:
        """Substitute the runtime prompt variables on the unified ``{{name}}``
        format used by the prompts entity everywhere:

        * ``{{bot_name}}`` — the configured assistant name.
        * ``{{widget_block}}`` — the inline-widget instructions (dashboard only;
          empty elsewhere), honoring ``dashboard.widget_density``.

        Snippet includes (``{{> name}}``) are resolved through the prompt provider
        so a system prompt can compose shared fragments. Rendered via the prompt
        engine so the substitution mechanism is identical to provider-rendered
        prompts; undeclared ``{{…}}`` pass through untouched.
        """
        from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable
        from personalclaw.prompt_providers.engine import render_template

        values = {
            "bot_name": self._bot_name,
            "widget_block": self._widget_block(session_key),
        }
        tpl = PromptTemplate(
            name="_runtime",
            content=prompt,
            variables=[PromptVariable(name=k) for k in values],
        )
        return render_template(tpl, values, resolver=_snippet_resolver())

    @staticmethod
    def _widget_block(session_key: str) -> str:
        """The inline-widget instruction block for ``{{widget_block}}``.

        Dashboard sessions get widget instructions; channel/CLI get an empty string.
        Respects dashboard.widget_density config ('more' or 'less').
        """
        is_dashboard = session_key and (
            session_key.startswith("dashboard:") or session_key.startswith("dashboard_")
        )
        if not is_dashboard:
            return ""

        cfg = AppConfig.load()
        density = getattr(cfg.dashboard, "widget_density", "more")

        # The widget instructions (both density variants) live in the prompt system
        # as the ``widget-instructions`` snippet; the conditional selects the variant.
        from personalclaw.prompt_providers.runtime import render_snippet_block

        return render_snippet_block("widget-instructions", {"density": density})

    @staticmethod
    def _load_agent_prompt(agent: str) -> str:
        """Read the prompt from a custom agent's config file."""
        agents_dir = _path_home_pclaw() / "agents"
        for f in agents_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("name") == agent or f.stem == agent:
                    prompt = data.get("prompt") or ""
                    if prompt.startswith("file://"):
                        try:
                            return safe_read_file(prompt[7:])
                        except (OSError, PermissionError):
                            return ""
                    return prompt
            except (json.JSONDecodeError, OSError):
                continue
        return ""

    def build_session_context(
        self,
        session_key: str | None = None,
        agent: str | None = None,
        resumed: bool = False,
        cwd: str | None = None,
        memory_store: str | None = None,
        compressed_history: str | None = None,
        mode: str = "",
        blocks_reads: bool = False,
    ) -> str:
        """Build context for a new session (memory + skills + history).

        Injected once at session start, not on every message.

        When *compressed_history* is provided, it replaces the naive
        truncation of thread history.  Callers obtain it by awaiting
        ``compress_thread_history()`` before calling this method.

        For custom agents (non-personalclaw), skills and workspace identity
        are skipped — the agent loads its own. Memory,
        lessons, critical rules, and hooks are injected for all agents.
        """
        is_custom = agent and agent != "personalclaw"
        parts: list[str] = []

        if is_custom:
            logger.info(
                "Custom agent %r: injecting memory/lessons/rules, skipping skills",
                agent,
            )
        else:
            logger.debug("Building session context for personalclaw agent")

        from personalclaw.prompt_providers.runtime import render_snippet_block

        parts.append(render_snippet_block("critical-rules") + "\n\n")

        # Current date/time — inject for ALL agents so the LLM knows "today".
        # Honour AppConfig.timezone (e.g. "Asia/Tokyo") so the LLM sees
        # the user's local time instead of the gateway host's system TZ, which
        # is often UTC on remote/server hosts and makes "today" ambiguous.

        _, tz = get_local_tz()
        now = datetime.now(tz)
        parts.append(f"[CURRENT DATE] {now.strftime('%A, %Y-%m-%d %H:%M %Z')}\n\n")

        # Agent identity and runtime — inject for ALL agents so the LLM
        # knows which agent it is and where it's running.  Without this,
        # the LLM cannot distinguish dashboard from the ACP agent and may
        # incorrectly tell the user to "go to the dashboard" when it IS
        # the dashboard.
        #
        # Runtime detection reuses the same heuristic as sel.py
        # _infer_source() to keep a single source of truth.
        agent_label = agent or "personalclaw"
        if session_key:
            runtime = _runtime_display_name(session_key)
            parts.append(
                render_snippet_block(
                    "agent-runtime-identity",
                    {"agent_label": agent_label, "runtime": runtime},
                )
                + "\n\n"
            )

        # Workspace identity — personalclaw-only (custom agents don't use workspaces).
        # The workspace IS the working directory; memory is scoped to it.
        if not is_custom:
            ws_path = cwd or "(none)"
            parts.append(render_snippet_block("workspace-identity", {"ws_path": ws_path}) + "\n\n")

        # Thread conversation history — highest priority context.
        # Use pre-computed LLM compression when available; fall back to truncation.
        if session_key and self.conversation_log and not resumed:
            _history_header = render_snippet_block("thread-history-header") + "\n"
            if compressed_history:

                compressed_history, _ = redact_exfiltration_urls(compressed_history)
                compressed_history, _ = redact_credentials(compressed_history)
                compressed_history = _MODE_IDENTITY_RE.sub("", compressed_history)
                logger.info(
                    "🔍 build_session_context: session_key=%s LLM-compressed " "history (%d chars)",
                    session_key,
                    len(compressed_history),
                )
                parts.append(_history_header + compressed_history + "\n[End of thread history]\n\n")
            else:
                recent = self.conversation_log.recent(session_key, roles={"user", "assistant"})
                logger.info(
                    "🔍 build_session_context: session_key=%s resumed=%s "
                    "conv_log_entries=%d (fallback truncation)",
                    session_key,
                    resumed,
                    len(recent),
                )
                if recent:
                    budget = _HISTORY_BUDGET_CHARS
                    history_lines: list[str] = []
                    for m in reversed(recent):
                        content = _MODE_IDENTITY_RE.sub("", m["content"])
                        if m["role"] == "assistant":
                            content = _compress_assistant_message(content)
                        if len(content) > _PER_MESSAGE_CAP:
                            content = content[:_PER_MESSAGE_CAP] + "…[truncated]"
                        line = f"{m['role'].title()}: {content}"
                        if budget - len(line) < 0:
                            break
                        history_lines.append(line)
                        budget -= len(line)
                    if history_lines:
                        history_lines.reverse()
                        history_block = "\n".join(history_lines)
                        history_block, _ = redact_exfiltration_urls(history_block)
                        history_block, _ = redact_credentials(history_block)
                        parts.append(
                            _history_header + history_block + "\n[End of thread history]\n\n"
                        )
        elif session_key and resumed:
            logger.info(
                "🔍 build_session_context: session_key=%s RESUMED — "
                "skipping thread history (ACP agent has native history)",
                session_key,
            )

        # Stop event context — inject notes for recent stop events so the
        # LLM knows prior turns were cancelled by the user.
        if session_key and self.conversation_log:
            _stop_notes = _build_stop_event_notes(self.conversation_log, session_key)
            if _stop_notes:
                parts.append(_stop_notes)

        # Memory and lessons: inject for ALL agents (including custom).
        # The user's preferences, project context, and learned corrections
        # are valuable regardless of which agent is running.
        # Temporary sessions skip all memory reads.
        # Memory: an agent's named memory_store provider if set, else the
        # filesystem-fallback store scoped by the working directory.
        memory = self.get_memory_for(cwd, memory_store)
        if not blocks_reads:
            from personalclaw.memory_service import service_for

            _svc = service_for(memory)
            # Adaptive budget (mem-adaptive-budget): scale the per-section caps to the
            # window of the model actually bound to chat (1M for Opus → ~5× recall;
            # 200k baseline for smaller models). Resolved from the active-model binding
            # so no window param has to thread through every build_message call site.
            from personalclaw.model_windows import active_chat_model_window

            memory_ctx = _svc.get_context(**_memory_caps(active_chat_model_window()))
            if memory_ctx:
                parts.append(memory_ctx)
            # Session working memory (M5c): a rolling distilled summary of THIS
            # session, always injected (not relevance-gated) — a consequence of
            # its scope=session, not a separate code path.
            if session_key:
                wm = _svc.working_memory(session_key)
                if wm:
                    parts.append(wm)
            # Agent self-persona (M5e): the agent's positive self-model, injected
            # always-on when its scope=agent matches the running agent. The agent
            # name is normalized to the canonical default when unset, so the most
            # common case (a default-agent chat, where ``agent`` is None) still
            # gets — and reads back — its persona. Capture (consolidation) uses the
            # SAME normalization so write/read agree on the scope key.
            from personalclaw.agents.defaults import normalize_agent_name

            persona = _svc.persona_block(agent=normalize_agent_name(agent))
            if persona:
                parts.append(persona)

            # User preference profile (C15): the always-on ambient half of the
            # preference split — Active, decaying, typed facets (style/identity/
            # tooling/veto/goal/channel) rendered as stable DEFAULTS, distinct from
            # on-demand memory recall. Capped + Active-only inside render_profile_block.
            try:
                from personalclaw.preference_facets import render_profile_block

                _vs = getattr(_svc, "_vs", None)
                if _vs is not None:
                    profile = render_profile_block(_vs)
                    if profile:
                        parts.append(profile)
            except Exception:
                logger.debug("preference profile block render failed", exc_info=True)

        # Skills: personalclaw-only (custom agents load their own). Pass the agent
        # so its agent-local skill tier (skill-agent-local-tier) overrides global
        # for this turn when present.
        if not is_custom:
            skills_ctx = self.skills.get_context(agent=agent)
            if skills_ctx:
                parts.append(skills_ctx)
        # Ephemeral session skills (skill-ephemeral-promotion): drafts the user
        # taught THIS session are live immediately, for every agent, until the
        # user promotes or forgets them at session end.
        if session_key:
            try:
                from personalclaw.skills.ephemeral import context_block

                eph = context_block(session_key)
                if eph:
                    parts.append(eph)
            except Exception:
                logger.debug("ephemeral skills context failed", exc_info=True)

        # Lessons — inject for ALL agents (skipped for temporary sessions). The
        # vector store is the ONE injection source of truth: cwd-scoped lessons
        # live in it as namespaced keys, not a parallel JSONL file. (The JSONL
        # LessonStore remains only the no-embedder WRITE fallback + dashboard
        # backing — it no longer feeds the prompt, which removes the dual-source
        # read that let a global + a workspace lesson disagree.)
        lessons_ctx = ""
        if not blocks_reads:
            from personalclaw.memory_service import service_for

            lessons_ctx = service_for(memory).lessons_context()
            if lessons_ctx:
                if len(lessons_ctx) > _LESSONS_CAP:
                    lessons_ctx = lessons_ctx[:_LESSONS_CAP] + "\n…[lessons truncated]\n"
                parts.append(lessons_ctx)

        # Cross-tab context (dashboard only, skipped for temporary sessions)
        if (
            session_key
            and self.conversation_log
            and session_key.startswith("dashboard:")
            and not blocks_reads
        ):
            cross = self.conversation_log.recent_from_source(
                "dashboard:", exclude_key=session_key, max_messages=10
            )
            if cross:
                cross_lines: list[str] = []
                cross_len = 0
                for m in cross:
                    content = _MODE_IDENTITY_RE.sub("", m["content"])
                    if len(content) > _PER_MESSAGE_CAP:
                        content = content[:_PER_MESSAGE_CAP] + "…[truncated]"
                    line = f"{m['role'].title()}: {content}"
                    if cross_len + len(line) > _CROSS_TAB_BUDGET_CHARS:
                        break
                    cross_lines.append(line)
                    cross_len += len(line)
                if cross_lines:
                    parts.append(
                        render_snippet_block(
                            "cross-tab-context", {"cross_lines": "\n".join(cross_lines)}
                        )
                        + "\n\n"
                    )

        # Provenance-tagged entries from recent sessions (skipped for temporary)
        if session_key and self.conversation_log and not blocks_reads:
            provenance = self.conversation_log.recent_with_provenance(session_key)
            if provenance:
                prov_lines: list[str] = []
                for p in provenance:
                    prov_lines.append(
                        f"- [thread {p['source_thread']}, {p['ts'][:16]}] {p['snippet']}"
                    )
                parts.append("## Recent Session Context\n" + "\n".join(prov_lines) + "\n\n")

        context = "".join(parts)
        if len(context) > _MAX_CONTEXT_CHARS:
            logger.warning(
                "Session context too large (%d chars), truncating to %d",
                len(context),
                _MAX_CONTEXT_CHARS,
            )
            context = context[:_MAX_CONTEXT_CHARS]
            # Avoid cutting mid-line
            last_nl = context.rfind("\n")
            if last_nl > 0:
                context = context[: last_nl + 1]

        logger.debug(
            "Session context: agent=%s, custom=%s, %d chars",
            agent or "personalclaw",
            is_custom,
            len(context),
        )
        return context

    def build_message(
        self,
        text: str,
        is_new_session: bool,
        session_key: str | None = None,
        channel_id: str | None = None,
        interactive: bool = True,
        agent: str | None = None,
        resumed: bool = False,
        thread_ts: str | None = None,
        cwd: str | None = None,
        memory_store: str | None = None,
        user_display_name: str | None = None,
        compressed_history: str | None = None,
        mode: str = "",
        prompt_use_case: str = "chat",
        blocks_reads: bool = False,
        action_context: str | None = None,
        thread_parent_text: str | None = None,
        system_prompt_override: str = "",
        system_prompt_suffix: str = "",
        resolved_agent_id: str = "",
        force_skill_ids: list[str] | None = None,
        force_workflow_ids: list[str] | None = None,
    ) -> tuple[str, HookResult]:
        """Build the full message with context and hook processing.

        On new sessions: prepends memory + always-on skills + lessons + history
        + episodic memory.
        On follow-up messages: only channel history (group channels), triggered
        skills, and hook context. ACP native history is trusted — no parallel
        transcript is injected.

        Pass *compressed_history* (from ``compress_thread_history()``) to
        inject LLM-compressed thread context instead of naive truncation.

        Returns:
            (full_message, hook_result) — hook_result may be a reply/modify/inject.
        """
        is_custom = agent and agent != "personalclaw"
        hook_result = self.hooks.on_message(text)

        parts: list[str] = []

        # Session context on first message only
        if is_new_session:
            # Agent prompt goes BEFORE session context wrapper
            # so the LLM treats it as its identity, not background info.
            # An Agent Definition's system_prompt (edited in the Agents UI) wins
            # over the file-based prompt — so what the user edits actually runs.
            if system_prompt_override.strip():
                agent_prompt = system_prompt_override
            elif is_custom:
                agent_prompt = self._load_agent_prompt(agent or "")
            else:
                # Default-agent system prompt resolves from the prompt provider,
                # via the use-case binding (chat / background / code / goal_loop;
                # derived from the session_key when not set explicitly). Falls back
                # to the shipped prompt file when the provider can't resolve it.
                _uc = _prompt_use_case_for(session_key, prompt_use_case)
                agent_prompt = _resolve_use_case_prompt(_uc)
                if not agent_prompt:
                    try:
                        agent_prompt = _shipped_prompt().read_text(encoding="utf-8")
                    except OSError:
                        agent_prompt = ""
            # A suffix LAYERS on the resolved prompt (task-mode framing) — it
            # must never REPLACE it the way the override does, or the posture
            # block becomes the entire system prompt (dropping identity/safety).
            if system_prompt_suffix.strip():
                agent_prompt = (
                    f"{agent_prompt}\n\n{system_prompt_suffix}"
                    if agent_prompt
                    else system_prompt_suffix
                )
            if agent_prompt:
                agent_prompt = self._apply_runtime_vars(agent_prompt, session_key or "")
                from personalclaw.prompt_providers.runtime import render_snippet_block

                parts.append(
                    render_snippet_block(
                        "agent-system-prompt-wrapper", {"agent_prompt": agent_prompt}
                    )
                    + "\n\n"
                )
            session_ctx = self.build_session_context(
                session_key,
                agent=agent,
                resumed=resumed,
                cwd=cwd,
                memory_store=memory_store,
                compressed_history=compressed_history,
                mode=mode,
                blocks_reads=blocks_reads,
            )
            if session_ctx:
                from personalclaw.prompt_providers.runtime import render_snippet_block

                parts.append(
                    render_snippet_block(
                        "session-context-wrapper", {"session_context": session_ctx}
                    )
                    + "\n\n"
                )

        # Channel history — inject on every message for group channel context
        ch_ctx: str | None = None
        if channel_id and self.channel_history:
            ch_ctx = self.channel_history.context_for(channel_id, thread_ts=thread_ts) or None
            if ch_ctx:
                parts.append(ch_ctx)

        # Thread parent text — inject whenever available, even alongside
        # channel history (they serve different purposes: ch_ctx has recent
        # messages, parent text has the original post that started the thread).
        if channel_id and thread_ts:
            # The thread-context block (both the with-parent-text and bare-metadata
            # variants) lives in the prompt system as the ``channel-thread-context``
            # snippet; its conditional selects the variant by thread_parent_text.
            from personalclaw.prompt_providers.runtime import render_snippet_block

            parts.append(
                render_snippet_block(
                    "channel-thread-context",
                    {
                        "channel_id": channel_id,
                        "thread_ts": thread_ts,
                        "thread_parent_text": thread_parent_text or "",
                    },
                )
                + "\n\n"
            )

        # Trust ACP native history for follow-up messages — do NOT inject
        # a parallel transcript reminder. Only inject
        # transcript on new sessions (via build_session_context), never
        # on follow-ups. Dual sources of truth cause contradictions.
        logger.info(
            "🔍 build_message: session_key=%s is_new=%s resumed=%s "
            "has_channel_history=%s injected_parts=%d",
            session_key,
            is_new_session,
            resumed,
            bool(channel_id and self.channel_history),
            len(parts),
        )

        # Episodic memory — only on new sessions to avoid cross-thread contamination;
        # ACP native history already provides in-thread context for follow-ups.
        # Skipped for temporary sessions.
        if blocks_reads:
            logger.info("🔍 Temporary session — episodic memory skipped")
        elif is_new_session:
            from personalclaw.memory_service import service_for

            memory = self.get_memory_for(cwd, memory_store)
            episodic_ctx = service_for(memory).episodic_context(query_text=text, cap=3000)
            if episodic_ctx:
                parts.append(episodic_ctx + "\n")
                logger.info("🔍 Injected episodic memory (%d chars)", len(episodic_ctx))
            else:
                logger.info("🔍 No episodic memory to inject")
        else:
            logger.info("🔍 Follow-up message — episodic memory skipped (trust ACP)")

        # Force-loaded skills (goal-loop planner/quorum): a loop's confirmed
        # skill_ids load ACTIVELY every cycle, bypassing both the is_custom skip
        # (the loop worker is a custom agent) and passive trigger-matching. The
        # user picked these in Plan Review precisely so they're always present.
        forced_skills: list[str] = []
        if force_skill_ids:
            for name in force_skill_ids:
                content = self.skills.load_skill(name)
                if content:
                    stripped = self.skills.strip_frontmatter(content)
                    parts.append(f"[Skill: {name}]\n{stripped}\n[End of skill]\n\n")
                    forced_skills.append(name)
            if forced_skills:
                logger.info("Force-loaded loop skills: %s", ", ".join(forced_skills))
                try:
                    from personalclaw.skills.usage import SkillUsageStore

                    SkillUsageStore().record_uses(forced_skills)
                except Exception:
                    logger.debug("skill usage record skipped (error)", exc_info=True)

        # Surfaced skills (on-demand, any message) — semantic ∪ keyword (#26),
        # skip for custom agents
        if not is_custom:
            triggered = [s for s in self.skills.get_surfaced_skills(text) if s not in forced_skills]
            if triggered:
                logger.info("Surfaced skills: %s", ", ".join(triggered))
            # Progressive disclosure (#29): above the threshold, inject only a
            # compact INDEX of the matched skills (name + one-line description) and
            # let the agent pull full bodies on demand via the skill_invoke tool —
            # instead of inlining every matched body (token efficiency at scale).
            # At/below the threshold (the common case) inline as before — no extra
            # round-trip. 0 disables (always inline).
            try:
                _disclosure_threshold = AppConfig.load().skills.progressive_disclosure_threshold
            except Exception:
                _disclosure_threshold = 8
            injected: list[str] = []
            if _disclosure_threshold and len(triggered) > _disclosure_threshold:
                index_lines = [
                    "[Relevant skills — INDEX only. Call skill_invoke{name} to load a "
                    "skill's full steps before using it. These are the matches for this "
                    "turn; call skill_search(query) to find others in the full library.]"
                ]
                by_key = {s["key"]: s for s in self.skills.list_skills()}
                for name in triggered:
                    desc = by_key.get(name, {}).get("description") or name
                    index_lines.append(f"- {name}: {desc}")
                index_lines.append("[End of skill index]")
                parts.append("\n".join(index_lines) + "\n\n")
                # Index-only: nothing loaded yet → no use recorded here (skill_invoke
                # records the use when the agent actually pulls a body).
            else:
                for name in triggered:
                    content = self.skills.load_skill(name)
                    if content:
                        stripped = self.skills.strip_frontmatter(content)
                        parts.append(f"[Skill: {name}]\n{stripped}\n[End of skill]\n\n")
                        injected.append(name)
            # Turn-time use counter (skill-use-counter): record the skills actually
            # inlined into this turn — the shared signal for semantic-surfacing
            # ranking (#26) and library GC (#27). Advisory, never breaks a turn.
            if injected:
                try:
                    from personalclaw.skills.usage import SkillUsageStore

                    SkillUsageStore().record_uses(injected)
                except Exception:
                    logger.debug("skill usage record skipped (error)", exc_info=True)

        # Workflow surfacing (scoped + semantic) — runs for ALL agents (unlike
        # the default skills/context above, which custom agents skip). Scope
        # gating already controls relevance precisely, and agent-scoped SOPs are
        # specifically meant to surface on a particular (often custom/ACP) agent's
        # turns — so this must not be gated on is_custom. Runs every turn (like
        # triggered skills, NOT gated on is_new_session). Gated by
        # config.workflows.enabled (kill-switch); never breaks a turn
        # (surface_for_turn_sync swallows all errors → None).
        try:
            cfg = AppConfig.load()
            if getattr(cfg.workflows, "enabled", True):
                from personalclaw.workflows.surfacing import (
                    TurnScope,
                    render_injection,
                    surface_for_turn_sync,
                )

                turn = TurnScope(
                    session_key=session_key,
                    agent=agent,
                    cwd=cwd,  # == workspace scope (session.workspace_dir)
                    # Agent-scoped SOPs match on the resolved binding id; fall
                    # back to the bare agent name for native turns.
                    agent_id=resolved_agent_id or (agent or ""),
                )
                emitted_wf: set[str] = set()
                match = surface_for_turn_sync(text, turn)
                if match:
                    parts.append(render_injection(match) + "\n\n")
                    emitted_wf.add(match.workflow.id)
                    logger.info(
                        "Surfaced workflow %s (scope=%s score=%.2f method=%s)",
                        match.workflow.name,
                        match.scope.value,
                        match.score,
                        match.method,
                    )
                # Force-included workflows (goal-loop planner/quorum): the loop's
                # confirmed workflow_ids inject ACTIVELY every cycle, by id, on top
                # of (and deduped against) the passive scope/text match above.
                if force_workflow_ids:
                    import asyncio as _asyncio

                    from personalclaw.workflows.registry import list_all_workflows
                    from personalclaw.workflows.surfacing import WorkflowMatch

                    try:
                        all_wf, _ = _asyncio.run(list_all_workflows(limit=1000))
                    except RuntimeError:
                        all_wf = []  # already in an event loop — skip (rare in this sync path)
                    by_id = {w.id: w for w in all_wf}
                    forced_wf: list[str] = []
                    for wid in force_workflow_ids:
                        wf = by_id.get(wid)
                        if wf and wf.id not in emitted_wf:
                            m = WorkflowMatch(
                                workflow=wf, score=1.0, scope=wf.scope, method="forced"
                            )
                            parts.append(render_injection(m, all_wf) + "\n\n")
                            emitted_wf.add(wf.id)
                            forced_wf.append(wf.name)
                    if forced_wf:
                        logger.info("Force-included loop workflows: %s", ", ".join(forced_wf))
        except Exception:
            logger.debug("Workflow surfacing skipped (error)", exc_info=True)

        # Hook-injected context — apply to all agents
        if hook_result.action == HOOK_INJECT_CONTEXT:
            parts.append(f"[Hook context:]\n{hook_result.text}\n[End of hook context]\n\n")

        # Action button context — structured payload from inline button click
        if action_context:
            parts.append(action_context + "\n\n")

        # The actual message (possibly modified by transform hook)
        if parts:
            if user_display_name:
                parts.append(f"[CURRENT USER] {user_display_name}\n")
            parts.append("[CURRENT USER REQUEST — respond to this]\n")
        if hook_result.action == HOOK_MODIFY:
            parts.append(hook_result.text)
        else:
            parts.append(text)

        # Lightweight reminder for OPTIONS syntax (rendered as buttons in dashboard)
        if interactive:
            parts.append(
                "\n\n(If presenting choices, end with [OPTIONS: choice1 | choice2 | choice3]. "
                "Users can select multiple options before submitting.)"
            )

        # Widget instructions — dashboard only (channel/CLI can't render iframes)
        _is_dashboard = session_key and (
            session_key.startswith("dashboard:") or session_key.startswith("dashboard_")
        )
        if _is_dashboard:
            parts.append(
                "\n\n[WIDGETS] You can render rich HTML inline using "
                '<widget title="Title">HTML</widget> tags. Tailwind CSS is available. '
                "The widget iframe inherits the dashboard's active theme: use "
                "var(--bg), var(--text), var(--card), var(--border), var(--accent), "
                "var(--muted), var(--ok), var(--warn), var(--danger) (or Tailwind "
                "arbitrary values like bg-[var(--card)]) instead of hardcoded colors "
                "so widgets look right on every theme. "
                "Use when the response benefits from styled visual content that markdown "
                "cannot express well (e.g. charts with Chart.js, styled cards, color-coded "
                "tables, or visual previews). "
                "Keep widgets concise. For larger HTML, save to a file and return "
                "the path -- you can iterate on it in future turns."
            )

        return "".join(parts).translate(_MULTIBYTE_TABLE), hook_result
