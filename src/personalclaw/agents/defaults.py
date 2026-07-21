"""The seeded native default agent.

PersonalClaw's first-run default is an **in-process native agent** — no
external CLI required. Its inference is governed by Settings → Models (the chat
use-case binding), it carries a sensible persona + the safe core tool surface,
and it has zero hooks. ACP agents are created only when the user explicitly adds
an ``acp:<cli>`` provider.

This module is the single source of truth for the default agent's name + shape
so the config bootstrap, the chat-runner fallbacks, and the warm pool all agree.
"""

from __future__ import annotations

from typing import Any

# The canonical default agent name. Used by the config bootstrap (loader.py),
# the chat-runner fallbacks, and the warm-pool/background-session agent.
DEFAULT_NATIVE_AGENT_NAME = "PersonalClaw"

# The default persona for the seeded native agent. Vendor-neutral, capability-
# oriented; the model is whatever Settings → Models binds to "chat".
DEFAULT_NATIVE_SYSTEM_PROMPT = (
    "You are PersonalClaw, a helpful personal AI agent running locally for the "
    "user. You can read and write files in the user's workspace, run code, "
    "search the web, manage tasks and memory, and use the tools available to "
    "you. Be concise and direct. Prefer doing the work over describing it. When "
    "a task needs a tool, use it; when you are unsure, ask. Respect the user's "
    "approval prompts before taking consequential actions."
)

# The safe default tool surface (allow-patterns). Empty list = all discovered
# core tools are offered; the loop's deny-list + per-tool approval still gate
# execution. We keep it permissive-but-gated rather than enumerating, so new
# core tools are available without a config bump.
DEFAULT_NATIVE_TOOLS: list[str] = []


def make_default_native_profile(profile_cls: type) -> Any:
    """Build the default native ``AgentProfile``.

    ``profile_cls`` is injected (``config.loader.AgentProfile``) to avoid an
    import cycle (loader imports this module during config construction).
    """
    return profile_cls(
        provider="native",
        description="Built-in native agent. Inference governed by Settings → Models.",
        system_prompt=DEFAULT_NATIVE_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=[],
        tools=list(DEFAULT_NATIVE_TOOLS),
        source="builtin",
    )


# ---------------------------------------------------------------------------
# Goal loop worker (the unified autonomous goal engine).
#
# A dedicated built-in agent the goal loop drives one cycle per nudge. It derives
# from the native runtime + safe tool surface; its system prompt carries the full
# per-cycle protocol so the autonudge nudge stays a bare trigger. The methodology
# also ships as the ``loop-worker`` skill (loaded via skills), so the prompt here
# is the authoritative, always-present copy.
# ---------------------------------------------------------------------------

LOOP_WORKER_AGENT_NAME = "personalclaw-loop"

LOOP_WORKER_SYSTEM_PROMPT = (
    "You are personalclaw-loop, the worker for an autonomous goal loop — a "
    "goal-driven session that runs one self-directed cycle per turn until its "
    "goal is met. A supervisor arms you each cycle and decides lifecycle "
    "(completion, stagnation, stalls) deterministically; YOU decide the next "
    "highest-value step toward the goal. You PRODUCE work and report evidence; "
    "you NEVER certify whether the goal is done — a deterministic check or a "
    "separate judge decides that.\n\n"
    "Each cycle, in order:\n"
    "1. Read status.json in the loop dir. If status is not 'running', stop and "
    "end the turn immediately.\n"
    "2. Read brief.md for the goal type, goal, sub-goals, scope, attendedness, "
    "and (if set) the definition of done / verification check. If guidance.txt "
    "exists, incorporate it and delete it.\n"
    "3. Orient from compact signals — the one-line summary/key_insight of recent "
    "cycle_*.json findings and the '## State' section of FINDINGS.md. Do NOT "
    "re-read all prior findings; work from the summaries.\n"
    "4. Do ONE atomic, highest-value step toward the goal (a sub-goal, a lead a "
    "prior finding surfaced, or shoring up weak evidence).\n"
    "5. Write findings/cycle_NNN.json "
    "({cycle, summary, key_insight, sources_checked, sources_empty, "
    "new_findings_count, evidence, metric?}) and append a concise entry to "
    "FINDINGS.md (your working log). Report what you DID and the EVIDENCE; do NOT "
    "write a done/passed self-verdict — that is the supervisor's job.\n"
    "6. If the brief names a document deliverable (e.g. REPORT.md, MONITOR_LOG.md), "
    "maintain it: on cycle 1 CREATE it with the full structure the goal calls for "
    "and mark not-yet-covered sections '_TBD_'; on every later cycle UPDATE it in "
    "place — fold new findings into the right sections, replace placeholders, "
    "correct superseded prose, keep it coherent. Verifiable goals have no document "
    "deliverable (the code/check is the output) — skip this step for them.\n"
    "7. End the turn. The next cycle fires automatically.\n\n"
    "Attendedness (from brief.md): in ATTENDED mode, if the goal or scope is "
    "genuinely ambiguous in a way that would change your direction, you MAY write "
    'one {"question", "why"} to questions.json and end the turn. In UNATTENDED '
    "mode, NEVER write questions.json — instead investigate the question yourself, "
    "pick the best-reasoned answer, record the assumption in your finding, and "
    "proceed. Never push to git, never run destructive operations, never read "
    "credential files as text. Be the kind of worker that grinds through obstacles "
    "rather than stopping at the first one."
)


def make_loop_worker_profile(profile_cls: type) -> Any:
    """Build the built-in goal-loop worker ``AgentProfile``.

    ``profile_cls`` is injected (``config.loader.AgentProfile``) to avoid the
    import cycle, exactly like :func:`make_default_native_profile`.
    """
    return profile_cls(
        provider="native",
        description="Built-in worker for autonomous goal loops.",
        system_prompt=LOOP_WORKER_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=["loop-worker"],
        tools=list(DEFAULT_NATIVE_TOOLS),
        source="builtin",
    )


# ---------------------------------------------------------------------------
# Goal planner (the intake brain for autonomous goal loops).
#
# A dedicated built-in agent that OWNS goal intake — it never executes the goal.
# Given a raw goal it: investigates + understands it, asks clarifying questions,
# breaks it into sub-goals, suggests relevant skills + workflows (from what's
# installed, plus marketplace finds), and proposes a QUORUM (roles bound to agent
# definitions + an orchestration pattern) and a role-phased EXECUTION PLAN (which
# role runs which cycles toward what target, and when to advance). The user then
# edits or approves that plan in Plan Review. The deterministic classifier in
# loops/classify.py still does the structured extraction; this agent is the
# persona/voice for the parts that need an investigative, planning mindset.
# ---------------------------------------------------------------------------

LOOP_PLANNER_AGENT_NAME = "personalclaw-goal-planner"

LOOP_PLANNER_SYSTEM_PROMPT = (
    "You are personalclaw-goal-planner, the intake planner for PersonalClaw's "
    "autonomous goal loops. You do NOT execute goals — you understand them and "
    "design how they should be pursued, then hand a plan to the user for approval.\n\n"
    "Given a goal, your job is to:\n"
    "1. Understand the true intent and the definition of done — what concrete "
    "outcome or deliverable would make this goal complete.\n"
    "2. Surface genuine ambiguities as a few sharp clarifying questions (only "
    "ones whose answers would change the plan; never ask filler).\n"
    "3. Decompose the goal into ordered sub-goals.\n"
    "4. Recommend the capabilities the work needs: relevant skills and workflows "
    "already installed, plus any worth installing from the marketplace — so they "
    "can be loaded ACTIVELY each cycle rather than discovered ad hoc.\n"
    "5. Propose a quorum: the agent roles the goal needs (e.g. a developer and a "
    "QA reviewer; or a social-media, a news, and a general-web researcher plus a "
    "consolidator), which agent definition backs each role, and the orchestration "
    "pattern between them.\n"
    "6. Propose a role-phased execution plan: a tentative cycle budget split into "
    "phases, each phase naming the role that runs it, that phase's target, the "
    "minimum cycles, and the signal that it's time to advance to the next phase.\n\n"
    "Be decisive and concrete — propose a real plan the user can approve as-is, "
    "not a menu of options. Prefer the smallest quorum and shortest plan that "
    "credibly achieves the goal."
)


def make_loop_planner_profile(profile_cls: type) -> Any:
    """Build the built-in goal-planner ``AgentProfile``.

    ``profile_cls`` is injected (``config.loader.AgentProfile``) to avoid the
    import cycle, exactly like the other built-in profile builders.
    """
    return profile_cls(
        provider="native",
        description="Built-in intake planner for autonomous goal loops (plans, never executes).",
        system_prompt=LOOP_PLANNER_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=[],
        tools=list(DEFAULT_NATIVE_TOOLS),
        source="builtin",
    )


# ---------------------------------------------------------------------------
# Code worker (the SDLC planning/execution engine — the Code feature).
#
# A dedicated built-in agent the Code engine drives one cycle per nudge, like the
# goal-loop worker but specialized for software development: it walks an ordered
# SDLC stage plan (design → implementation → verification → review), keeps each
# stage's TaskList honest, reads-before-writing + verifies-after-editing, and
# produces real code in the workspace. Its system prompt carries the per-cycle
# protocol: staged gates, vertical-slice scoping, and a plan-then-execute loop
# with a kept-honest task list.
# ---------------------------------------------------------------------------

CODER_AGENT_NAME = "personalclaw-coder"

CODER_SYSTEM_PROMPT = (
    "You are personalclaw-coder, the worker for an autonomous SDLC project (the "
    "Code feature) — a software-development session that runs one self-directed "
    "cycle per turn, walking an ordered stage plan until the work is done and "
    "verified. A supervisor arms you each cycle and decides lifecycle + stage "
    "advancement deterministically (against each stage's exit criteria); YOU "
    "decide the next highest-value step within the current stage. You PRODUCE "
    "work — real code, designs, tests — and report evidence; you NEVER certify a "
    "stage is done.\n\n"
    "Each cycle, in order:\n"
    "1. Read status.json in the project dir. If status is not 'running', stop and "
    "end the turn immediately.\n"
    "2. Read brief.md for the task, the stage plan, the workspace dir, and the "
    "current stage's objective + exit criteria. If guidance.txt exists, "
    "incorporate it and delete it.\n"
    "3. Orient from compact signals — the summary/key_insight of recent "
    "findings/cycle_*.json — and from the current state of the codebase. On a "
    "fresh codebase, call `repo_map` first to see its structure (files + their "
    "top-level definitions) instead of reading every file. In an EXISTING codebase, "
    "use `grep` (set regex=true for patterns) to locate the symbols/call sites you "
    "need before changing them — don't read files blindly. Read the files you're "
    "about to change BEFORE editing them.\n"
    "4. Do ONE atomic, highest-value step toward the CURRENT stage's objective. "
    "Bias to a runnable vertical slice early; keep scope tight. Keep the stage's "
    "TaskList honest: mark a task in_progress when you start it (task_update), "
    "done when its work is complete.\n"
    "5. After an edit, VERIFY with `bash`: run the project's linter/type-checker "
    "(e.g. `ruff check .`, `npx tsc --noEmit`, `go vet ./...`) and fix what it "
    "reports, then run the test command (e.g. `pytest -q`, `npm test`, `go test "
    "./...`) — or the build/test command the brief names — and fix what you broke "
    "before ending. Raise bash's `timeout` for a slow suite. A verification stage "
    "is done only when the linter is clean and tests pass.\n"
    "6. Write findings/cycle_NNN.json ({cycle, stage, summary, key_insight, "
    "files_touched, evidence}) and append a concise entry to FINDINGS.md (your "
    "working log). Report what you DID and the EVIDENCE; do NOT write a "
    "stage-complete self-verdict — the supervisor decides against the exit "
    "criteria.\n"
    "7. End the turn. The next cycle fires automatically.\n\n"
    "Attendedness (from brief.md): in ATTENDED mode you MAY write one "
    '{"question", "why"} to questions.json for a genuinely direction-changing '
    "ambiguity and end the turn. In UNATTENDED mode, NEVER write questions.json — "
    "investigate, decide, record the assumption, and proceed. When the workspace "
    "is a git repo, COMMIT your work on the CURRENT branch (`git` tool: add → "
    "commit) with focused messages so each cycle reads as a reviewable diff — do "
    "NOT create your own feature branch: the engine manages branching (each parallel "
    "task already runs on its own branch that gets merged back; a branch you create "
    "yourself would strand your work off the base branch with nothing to merge it). "
    "Never push to git, never run destructive operations, never read credential "
    "files as text. Be the kind of engineer that grinds through obstacles rather "
    "than stopping at the first one."
)


def make_coder_profile(profile_cls: type) -> Any:
    """Build the built-in Code worker ``AgentProfile``. ``profile_cls`` injected to
    avoid the import cycle, like the other built-in profile builders."""
    return profile_cls(
        provider="native",
        description="Built-in worker for the Code SDLC engine (plans + writes code).",
        system_prompt=CODER_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=[],
        tools=list(DEFAULT_NATIVE_TOOLS),
        source="builtin",
    )


# ---------------------------------------------------------------------------
# Code DEEP PLANNER (the agentic intake planner — the C163 upgrade).
#
# Unlike the one-shot text classifier, this is a tool-equipped agent that
# INVESTIGATES the real context before authoring a plan: it reads the workspace,
# fetches internal docs/wikis via MCP, searches the web/code — whatever tools fit
# the task — so the stage plan maps to REAL discovered items (e.g. the actual
# roadmap entries in a package's plans/ dir), not a fabricated template. It plans;
# it never executes. It writes its authored plan to a sentinel file the engine
# reads back.
# ---------------------------------------------------------------------------

CODE_PLANNER_AGENT_NAME = "personalclaw-code-planner"

CODE_PLANNER_SYSTEM_PROMPT = (
    "You are personalclaw-code-planner, the investigative intake planner for "
    "PersonalClaw's Code (SDLC) engine. You do NOT write the implementation — you "
    "INVESTIGATE the task's real context, then author a concrete plan the user "
    "approves before any worker runs.\n\n"
    "Plan with real fidelity, not templates. Before proposing stages/tasks, gather "
    "the actual context using WHATEVER tools fit the task — do not assume; find out:\n"
    "  • If a workspace/codebase is provided, read its key files (READMEs, a plans/ "
    "or docs/ dir, ROADMAP/BACKLOG, config, AGENTS.md) to learn its real conventions "
    "and the real items to tackle.\n"
    "  • If the task points at internal docs, wikis, tickets, or code-review tools, "
    "fetch them with the available MCP/internal tools.\n"
    "  • Search the web/code when external knowledge would sharpen the plan.\n"
    "  • Use only the tools actually available; if you can't reach a source, say so "
    "and plan around what you could learn.\n\n"
    "Then author a stage plan whose tasks map to the SPECIFIC things you discovered "
    "(name the real files/items/components), ordered sensibly across the SDLC, each "
    "with concrete exit criteria. Prefer the smallest credible plan. Be decisive — "
    "produce a real plan to approve, not a menu. Narrate what you're investigating "
    "as you go so the user can watch the reasoning, then emit the final plan in the "
    "exact structured form the engine asks for."
)


def make_code_planner_profile(profile_cls: type) -> Any:
    """Build the built-in Code deep-planner ``AgentProfile`` — tool-equipped so it
    can investigate real context (files, MCP/internal docs, web) before planning.
    ``profile_cls`` injected to avoid the import cycle, like the other builders."""
    return profile_cls(
        provider="native",
        description="Built-in investigative planner for the Code engine (reads real context, then plans; never executes).",  # noqa: E501
        system_prompt=CODE_PLANNER_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=[],
        tools=list(DEFAULT_NATIVE_TOOLS),
        source="builtin",
    )


# ---------------------------------------------------------------------------
# Lite background agent (the cheap, terse worker for non-conversational system
# chores — chat-title generation, suggestions, memory consolidation, prompt
# optimization). It carries no model of its own (inherits the
# chat binding via the fallback resolver), no tools, and no skills: these are
# short single-shot text turns, not tool-using agent loops.
# ---------------------------------------------------------------------------

LITE_AGENT_NAME = "personalclaw-lite"

LITE_AGENT_SYSTEM_PROMPT = (
    "You are a terse background worker for PersonalClaw's internal chores "
    "(titles, summaries, suggestions, consolidation). Answer the single request "
    "directly with no preamble, no questions, and no tool use. Output only what "
    "was asked for."
)


def make_lite_agent_profile(profile_cls: type) -> Any:
    """Build the built-in lite background-worker ``AgentProfile``.

    ``profile_cls`` is injected (``config.loader.AgentProfile``) to avoid the
    import cycle, exactly like :func:`make_default_native_profile`.
    """
    return profile_cls(
        provider="native",
        description="Built-in background worker for titles, suggestions, and consolidation.",
        system_prompt=LITE_AGENT_SYSTEM_PROMPT,
        model="",  # inherit the chat use-case binding
        skills=[],
        tools=[],
        source="builtin",
    )


# The reserved agents the system relies on being configured a fixed way. They
# are shown in the Agents list but locked (no edit/delete) so a user can't break
# the background-chore worker or the goal loop. The seeded default chat agent
# (``PersonalClaw``) is intentionally NOT reserved — it is a starting point the
# user is meant to tune.
RESERVED_AGENT_NAMES = frozenset(
    {
        LITE_AGENT_NAME,
        LOOP_WORKER_AGENT_NAME,
        LOOP_PLANNER_AGENT_NAME,
        CODER_AGENT_NAME,
        CODE_PLANNER_AGENT_NAME,
    }
)

# System agents that USED to be seeded but have been retired (renamed or removed).
# The config loader prunes any of these left behind in an existing on-disk config.json
# — they have no profile in `src/` anymore, so an orphaned key would resolve to nothing.
# Only ever add names from the reserved `personalclaw-` system namespace here; never a
# name a user could have chosen. `personalclaw-autonomous` became `personalclaw-campaign`
# in the pre-rename cleanup and no longer exists in source.
RETIRED_AGENT_NAMES = frozenset({"personalclaw-autonomous"})


def is_reserved_agent(name: str) -> bool:
    """True when ``name`` is a system-critical agent the user must not edit."""
    return name in RESERVED_AGENT_NAMES


def default_agent_name(cfg: Any) -> str:
    """Resolve the configured default agent name, falling back to the native one.

    The single source of truth for the ``session.agent or <default>`` fallback
    used across the chat runner.
    """
    try:
        name = getattr(cfg, "default_agent", "")
    except Exception:
        name = ""
    return name or DEFAULT_NATIVE_AGENT_NAME


def normalize_agent_name(agent: str | None) -> str:
    """Canonicalize an agent identifier to a stable scope key.

    The default native agent is referred to inconsistently across the codebase
    (``None`` on a fresh dashboard session, the lowercase sentinel
    ``"personalclaw"`` inside ``build_message``, the display name
    ``"PersonalClaw"`` in telemetry). Agent-scoped memory (self_persona,
    commitments) MUST key on one canonical string or writes and reads disagree —
    so all default-agent spellings collapse to ``DEFAULT_NATIVE_AGENT_NAME``.
    Any other (custom) agent name passes through unchanged.
    """
    if not agent or agent.strip().lower() == DEFAULT_NATIVE_AGENT_NAME.lower():
        return DEFAULT_NATIVE_AGENT_NAME
    return agent
