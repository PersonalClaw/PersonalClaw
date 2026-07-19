"""The bundled-prompt catalog — ONE source of truth for every shipped prompt.

Each runtime context that used to hardcode an LLM instruction string now declares
it here as a :class:`BundledPrompt` (a named template + its typed variables, with
content living in ``config/prompts/<file>``) or a :class:`BundledSnippet` (a
reusable fragment in ``config/prompt_snippets/<file>``). The catalog drives three
things that previously duplicated each other:

* **Seeding** — :mod:`native_provider` writes each entry to the user's prompt
  store on first run (idempotent, non-clobbering).
* **Use-case vocabulary** — :mod:`personalclaw.providers.prompt_use_cases` derives
  ``PROMPT_USE_CASES`` + the default binding for each from ``BUNDLED_PROMPTS``,
  so every shipped prompt is individually bindable in Settings → Prompts.
* **Runtime rendering** — call sites fetch the bound prompt by its use-case and
  render it through the engine (``render_prompt``), so a preview can never drift
  from what the model actually receives.

A prompt's ``category`` groups it for the Settings UI:

* ``agent``    — the default-agent system prompt for a runtime context
                 (chat / background / code / goal_loop).
* ``internal`` — a one-shot LLM task the system runs on the user's behalf
                 (title generation, history compression, consolidation, …).
* ``loop``     — autonomous loop/orchestration prompts (classifiers, judges,
                 planning briefs, phase directives).
* ``eval``     — evaluation-harness prompts.

To migrate a hardcoded prompt: add a :class:`BundledPrompt` row here, drop its
content in ``config/prompts/<file>``, and have the call site render it via the
use-case. No bespoke seeding or wiring per prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from personalclaw.prompt_providers.base import PromptVariable

# Category vocabulary (see module docstring).
PromptCategory = str  # "agent" | "internal" | "loop" | "eval"


@dataclass(frozen=True)
class BundledPrompt:
    """A shipped prompt template: its identity, its bindable use-case, the source
    file holding its content, and the typed variables it renders with."""

    name: str
    use_case: str
    filename: str
    kind: str = "system"
    category: PromptCategory = "internal"
    description: str = ""
    variables: tuple[PromptVariable, ...] = ()
    tags: tuple[str, ...] = ("system", "bundled")


@dataclass(frozen=True)
class BundledSnippet:
    """A shipped reusable fragment included by prompts via ``{{> name}}``."""

    name: str
    filename: str
    description: str = ""
    variables: tuple[PromptVariable, ...] = ()
    tags: tuple[str, ...] = ("system", "bundled")


# Runtime variables the four default-agent system prompts render with. Values are
# supplied at resolve time by ``context._apply_runtime_vars``.
_AGENT_SYSTEM_VARS: tuple[PromptVariable, ...] = (
    PromptVariable(name="bot_name", description="The configured assistant name.", default="PersonalClaw"),
    PromptVariable(name="widget_block", type="textarea", description="Inline-widget instructions (dashboard only; empty elsewhere)."),
)


# ── The catalog ───────────────────────────────────────────────────────────────
# Append rows as prompts are migrated. Order is display order within a category.

BUNDLED_PROMPTS: tuple[BundledPrompt, ...] = (
    # ── agent system prompts (the default-agent prompt per runtime context) ──
    BundledPrompt(
        name="system-chat", use_case="chat", filename="chat.md", category="agent",
        description="The bundled PersonalClaw system prompt for the chat context.",
        variables=_AGENT_SYSTEM_VARS,
    ),
    BundledPrompt(
        name="system-background", use_case="background", filename="background.md", category="agent",
        description="The bundled PersonalClaw system prompt for the background context.",
        variables=_AGENT_SYSTEM_VARS,
    ),
    BundledPrompt(
        name="system-code", use_case="code", filename="code.md", category="agent",
        description="The bundled PersonalClaw system prompt for the code context.",
        variables=_AGENT_SYSTEM_VARS,
    ),
    BundledPrompt(
        name="system-goal-loop", use_case="goal_loop", filename="goal_loop.md", category="agent",
        description="The bundled PersonalClaw system prompt for the goal_loop context.",
        variables=_AGENT_SYSTEM_VARS,
    ),

    # ── internal task prompts (one-shot LLM jobs the system runs) ──
    BundledPrompt(
        name="task-title", use_case="title", filename="task-title.md",
        kind="user", category="internal",
        description="Generate a short conversation title from the recent transcript.",
        variables=(
            PromptVariable(name="transcript", type="textarea", required=True,
                           description="Recent user/assistant turns (role: content, truncated)."),
        ),
    ),
    BundledPrompt(
        name="task-suggestions", use_case="suggestions", filename="task-suggestions.md",
        kind="user", category="internal",
        description="Generate contextual dashboard prompt suggestions from the user's recent context.",
        variables=(
            PromptVariable(name="context", type="textarea", required=True,
                           description="Assembled context: preferences, projects, recent activity, sessions, crons, time."),
        ),
    ),
    BundledPrompt(
        name="task-nl-to-cron", use_case="nl_to_cron", filename="task-nl_to_cron.md",
        kind="user", category="internal",
        description="Convert a natural-language scheduling request into a 5-field cron expression.",
        variables=(
            PromptVariable(name="request", required=True,
                           description="The natural-language scheduling request."),
        ),
    ),
    BundledPrompt(
        name="task-prompt-optimizer", use_case="prompt_optimizer", filename="task-prompt_optimizer.md",
        kind="system", category="internal",
        description="System prompt for the prompt optimizer — rewrites vague prompts into specific, scoped instructions.",
    ),
    BundledPrompt(
        name="task-side-chat", use_case="side_chat", filename="task-side_chat.md",
        kind="system", category="internal",
        description="System envelope for a side-chat turn — read-only Q&A against a frozen conversation snapshot.",
    ),
    BundledPrompt(
        name="task-history-compression", use_case="history_compression",
        filename="task-history_compression.md", kind="user", category="internal",
        description="Compress a long chat transcript into a dense summary, preserving facts/decisions/code/errors.",
        variables=(
            PromptVariable(name="cap", type="number", required=True,
                           description="Target maximum character count for the summary."),
            PromptVariable(name="query", type="textarea", default="",
                           description="The latest user query, for relevance weighting."),
            PromptVariable(name="transcript", type="textarea", required=True,
                           description="The full transcript to compress."),
        ),
    ),
    BundledPrompt(
        name="task-memory-consolidation", use_case="memory_consolidation",
        filename="task-memory_consolidation.md", kind="user", category="internal",
        description="Memory consolidation agent: extract history/semantic/episodic/lessons/persona/skills from a session. Envelope; the per-key instructions are composed at runtime.",
        variables=(
            PromptVariable(name="numbered_keys", type="textarea", required=True,
                           description="The numbered list of JSON keys to return (composed from the active extraction fragments)."),
            PromptVariable(name="semantic_block", type="textarea", default="",
                           description="The '## Current Semantic Memory' section (empty when no vector store)."),
            PromptVariable(name="markdown_blocks", type="textarea", default="",
                           description="The '## Current Preferences/Projects' sections (empty when migrated)."),
            PromptVariable(name="conversation", type="textarea", required=True,
                           description="The formatted conversation to process."),
        ),
    ),
    BundledPrompt(
        name="task-plan-consolidation", use_case="plan_consolidation",
        filename="task-plan_consolidation.md", kind="user", category="internal",
        description="Plan-memory consolidation: synthesize orchestration events into plan_lessons.md guidance.",
        variables=(
            PromptVariable(name="existing", type="textarea", default="",
                           description="Current plan_lessons.md content."),
            PromptVariable(name="event_lines", type="textarea", required=True,
                           description="Recent orchestration events, one per line."),
        ),
    ),
    BundledPrompt(
        name="task-plan-rephrase", use_case="plan_rephrase",
        filename="task-plan_rephrase.md", kind="user", category="internal",
        description="Reformat a plan to the canonical stage template (optionally first deciding whether the text is even a plan).",
        variables=(
            PromptVariable(name="plan_template", type="textarea", required=True,
                           description="The canonical plan template to match."),
            PromptVariable(name="issues", default="",
                           description="Comma-joined list of format issues to fix."),
            PromptVariable(name="text", type="textarea", required=True,
                           description="The plan text to reformat."),
            PromptVariable(name="might_not_be_plan", type="boolean", default=False,
                           description="When true, first decide if the text is a plan at all (return NOT_A_PLAN if not)."),
        ),
    ),
    BundledPrompt(
        name="task-contradiction-judge", use_case="contradiction_judge",
        filename="task-contradiction_judge.md", kind="user", category="internal",
        description="Judge whether two saved lessons directly contradict (following both is impossible).",
        variables=(
            PromptVariable(name="new_rule", type="textarea", required=True, description="The new lesson rule."),
            PromptVariable(name="existing_rule", type="textarea", required=True, description="The existing lesson rule."),
        ),
    ),
    # ── Inbox triage: classify / draft / digest over stored inbox items. The
    # external message text is fenced (<untrusted_content>) by the InboxService
    # before it reaches these templates, and each prompt reinforces "quoted =
    # data, not instructions" against prompt-injection in a scraped message. ──
    BundledPrompt(
        name="task-inbox-classify", use_case="inbox_classify", filename="task-inbox-classify.md",
        kind="user", category="internal",
        description="Triage an inbox message into needs_reply/fyi/noise + a confidence (JSON out).",
        variables=(
            PromptVariable(name="channel", required=True, description="The channel/DM name."),
            PromptVariable(name="sender", required=True, description="The sender's display name."),
            PromptVariable(name="message", type="textarea", required=True, description="The fenced message + thread context."),
        ),
    ),
    BundledPrompt(
        name="task-inbox-draft", use_case="inbox_draft", filename="task-inbox-draft.md",
        kind="user", category="internal",
        description="Draft a reply to an inbox message in the user's voice (or SKIP if none needed).",
        variables=(
            PromptVariable(name="user_name", required=True, description="Who the reply is on behalf of."),
            PromptVariable(name="channel", required=True, description="The channel/DM name."),
            PromptVariable(name="sender", required=True, description="The sender's display name."),
            PromptVariable(name="message", type="textarea", required=True, description="The fenced message + thread context."),
            PromptVariable(name="style", type="textarea", required=False, description="Optional style rules for the reply voice."),
        ),
    ),
    BundledPrompt(
        name="task-inbox-digest", use_case="inbox_digest", filename="task-inbox-digest.md",
        kind="user", category="internal",
        description="Summarize recent channel messages into a short catch-up digest.",
        variables=(
            PromptVariable(name="channel", required=True, description="The channel name."),
            PromptVariable(name="hours", required=True, description="The look-back window in hours."),
            PromptVariable(name="user_name", required=True, description="Whose attention to flag."),
            PromptVariable(name="messages", type="textarea", required=True, description="The fenced, oldest-first message list."),
        ),
    ),
    # P10 — the generalized, user-parameterized recurring DIGEST. Unlike task-inbox-digest
    # (a one-shot text summarizer over pasted messages), this is an AGENT directive fired by
    # the ``run-prompt`` action on a Schedule Trigger: the agent GATHERS the named sources
    # with its own tools, correlates/dedups, narrates, and DELIVERS to the chosen target —
    # so cadence + sourcing + delivery are all config, no new service/provider (see
    # the digest plan). Fresh use_case="digest" (auto-registers in
    # PROMPT_USE_CASES; independent of the model-capability USE_CASES vocab).
    BundledPrompt(
        name="task-digest", use_case="digest", filename="task-digest.md",
        kind="user", category="internal",
        description="Recurring multi-source digest: gather the named sources over a window, correlate/dedup, narrate, and deliver to a channel/inbox/knowledge target. Fire it on a schedule via a run-prompt trigger.",
        variables=(
            PromptVariable(name="sources", type="textarea", required=True,
                           description="Which sources to cover — channels/DMs, inbox, knowledge, tasks (name them)."),
            PromptVariable(name="window", required=True, default="24 hours",
                           description="The look-back window, e.g. '24 hours' or 'the last week'."),
            PromptVariable(name="target", required=True, default="inbox",
                           description="Where to deliver — a channel/DM name, 'inbox', or 'knowledge'."),
        ),
    ),
    # NOTE: knowledge_extraction / knowledge_insights (the native-knowledge app)
    # and web_extract (the web-tools app) are now APP-OWNED prompts — each app
    # ships its prompt YAML and seeds it on enable (see apps.prompt_seed). They are
    # intentionally NOT in this core catalog; their use-cases join the bindable
    # vocabulary via the app-prompt registry (providers.prompt_use_cases unions it).
    BundledPrompt(
        name="task-nav-links", use_case="nav_links", filename="task-nav_links.md",
        kind="user", category="internal",
        description="Label a batch of numbered URLs with short human-readable titles ('<index>: <title>' per line).",
        variables=(
            PromptVariable(name="numbered_links", type="textarea", required=True,
                           description="The numbered links, one '<i>: <url>[ — context: ...]' per line."),
        ),
    ),
    BundledPrompt(
        name="task-folder-icon", use_case="folder_icon", filename="task-folder_icon.md",
        kind="user", category="internal",
        description="Pick a single emoji that represents a project folder name.",
        variables=(
            PromptVariable(name="folder_name", required=True, description="The folder name."),
        ),
    ),
    BundledPrompt(
        name="task-channel-title", use_case="channel_title", filename="task-channel_title.md",
        kind="user", category="internal",
        description="Channel thread auto-title: name the topic in 3-6 words, or reply SKIP if too vague.",
        variables=(
            PromptVariable(name="user_msg", type="textarea", required=True, description="The user message."),
            PromptVariable(name="assistant_msg", type="textarea", required=True, description="The assistant reply."),
        ),
    ),

    # ── autonomous loop/orchestration prompts ──
    BundledPrompt(
        name="task-grill-flat", use_case="grill_flat", filename="task-grill_flat.md",
        kind="user", category="loop",
        description="Grill (flat shape): decompose a goal into distinct, first-principles sub-goals (JSON array out).",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True, description="The goal to decompose."),
            PromptVariable(name="prior", type="textarea", default="",
                           description="Relevant prior context to avoid re-asking what's settled (empty when none)."),
        ),
    ),
    BundledPrompt(
        name="task-grill-tree", use_case="grill_tree", filename="task-grill_tree.md",
        kind="user", category="loop",
        description="Grill (tree shape): produce phases of clarifying questions that scope the work (JSON object out).",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True, description="The goal to plan."),
            PromptVariable(name="prior", type="textarea", default="",
                           description="Relevant prior context to skip already-answered questions (empty when none)."),
        ),
    ),
    BundledPrompt(
        name="task-grill-assess", use_case="grill_assess", filename="task-grill_assess.md",
        kind="user", category="loop",
        description="Grill (assess): decide whether a goal is clear enough to decompose or needs clarifying questions (JSON object out).",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True, description="The goal to assess."),
        ),
    ),
    BundledPrompt(
        name="task-orchestrator-skill", use_case="orchestrator_skill", filename="task-orchestrator_skill.md",
        kind="system", category="loop",
        description="The always-loaded orchestrator SKILL.md: agent-delegation guidance + the installed specialist roster.",
        variables=(
            PromptVariable(name="roster", type="textarea", default="",
                           description="The assembled specialist-agent roster (### name + description blocks), or the no-specialists notice."),
        ),
    ),
    BundledPrompt(
        name="task-parallel-worker-nudge", use_case="parallel_worker_nudge",
        filename="task-parallel_worker_nudge.md", kind="user", category="loop",
        description="Per-cycle trigger for a parallel loop task-worker: work only its task in its own checkout, mark it done, write a finding.",
        variables=(
            PromptVariable(name="loop_id", required=True, description="The parent loop id."),
            PromptVariable(name="worktree_dir", required=True, description="The worker's isolated checkout path."),
            PromptVariable(name="task_title", required=True, description="The task's title."),
            PromptVariable(name="task_id", required=True, description="The task's id."),
            PromptVariable(name="loop_dir", default="", description="The loop's data directory (findings/guidance live here)."),
            PromptVariable(name="task_description", type="textarea", default="",
                           description="The task's description, if any."),
            PromptVariable(name="plan", type="textarea", default="",
                           description="The task's action-plan lines, if any."),
            PromptVariable(name="criteria", type="textarea", default="",
                           description="The task's exit-criteria lines, if any."),
            PromptVariable(name="guidance", type="textarea", default="",
                           description="Pending user steering for this task, if any."),
        ),
    ),

    # ── loop planning-walkthrough briefs (per-kind step/design briefs) ──
    BundledPrompt(
        name="task-goal-step-brief", use_case="goal_step_brief",
        filename="task-goal_step_brief.md", kind="user", category="loop",
        description="Goal-loop planning: the brief for ONE stepwise-walkthrough step (intent/sub_goals/quorum/execution_plan), carrying prior approved artifacts + re-draft comments.",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True,
                           description="The goal to plan (stripped)."),
            PromptVariable(name="step_title", required=True, description="This step's human title."),
            PromptVariable(name="step_kind", required=True, description="This step's kind slug."),
            PromptVariable(name="objective", type="textarea", default="",
                           description="This step's objective (empty when none)."),
            PromptVariable(name="approved_block", type="textarea", default="",
                           description="Pre-rendered approved-artifact lines (empty when none), one '  - [kind] title: summary' per line."),
            PromptVariable(name="comments_block", type="textarea", default="",
                           description="Pre-rendered user-comment lines (empty when none), one '  - text' per line."),
            PromptVariable(name="artifact_sentinel", required=True,
                           description="The artifact JSON filename to write (step_artifact.json)."),
            PromptVariable(name="artifact_contract", type="textarea", required=True,
                           description="The expected artifact JSON contract for this step kind."),
        ),
    ),
    BundledPrompt(
        name="task-code-design-brief", use_case="code_design_brief",
        filename="task-code_design_brief.md", kind="user", category="loop",
        description="Code (SDLC) planning pass 1: design the ordered step list for the target after investigating real context.",
        variables=(
            PromptVariable(name="task", type="textarea", required=True,
                           description="The task to plan (stripped)."),
            PromptVariable(name="workspace_dir", default="",
                           description="The bound workspace path (empty when none — switches the investigate-context guidance)."),
            PromptVariable(name="guide", type="textarea", required=True,
                           description="The pre-rendered standard step-kind guide lines."),
            PromptVariable(name="steps_sentinel", required=True,
                           description="The step-list JSON filename to write (plan_steps.json)."),
        ),
    ),
    BundledPrompt(
        name="task-code-step-brief", use_case="code_step_brief",
        filename="task-code_step_brief.md", kind="user", category="loop",
        description="Code (SDLC) planning pass 2: the brief for ONE step's artifact, carrying prior approved artifacts + re-draft comments + workspace.",
        variables=(
            PromptVariable(name="task", type="textarea", required=True,
                           description="The overall task (stripped)."),
            PromptVariable(name="step_title", required=True, description="This step's human title."),
            PromptVariable(name="step_kind", required=True, description="This step's kind slug."),
            PromptVariable(name="objective", type="textarea", default="",
                           description="This step's objective (empty when none)."),
            PromptVariable(name="approved_block", type="textarea", default="",
                           description="Pre-rendered approved-artifact lines (empty when none)."),
            PromptVariable(name="comments_block", type="textarea", default="",
                           description="Pre-rendered user-comment lines (empty when none)."),
            PromptVariable(name="workspace_dir", default="",
                           description="The bound workspace path (empty when none)."),
            PromptVariable(name="artifact_sentinel", required=True,
                           description="The artifact JSON filename to write (step_artifact.json)."),
            PromptVariable(name="artifact_contract", type="textarea", required=True,
                           description="The expected artifact JSON contract for this step kind."),
        ),
    ),
    BundledPrompt(
        name="task-design-design-brief", use_case="design_design_brief",
        filename="task-design_design_brief.md", kind="user", category="loop",
        description="Design-loop planning pass 1: design the ordered design-phase step list, working through every reference input (URL/image/video/HTML/React/DESIGN.md).",
        variables=(
            PromptVariable(name="task", type="textarea", required=True,
                           description="The design task to plan (stripped)."),
            PromptVariable(name="design_inputs_block", type="textarea", default="",
                           description="Pre-rendered reference-input lines (empty when none); carries its own leading blank line when present."),
            PromptVariable(name="workspace_dir", default="",
                           description="The bound workspace path (empty when none)."),
            PromptVariable(name="guide", type="textarea", required=True,
                           description="The pre-rendered standard design-phase guide lines."),
            PromptVariable(name="steps_sentinel", required=True,
                           description="The step-list JSON filename to write (plan_steps.json)."),
        ),
    ),
    BundledPrompt(
        name="task-design-step-brief", use_case="design_step_brief",
        filename="task-design_step_brief.md", kind="user", category="loop",
        description="Design-loop planning pass 2: the brief for ONE design-planning step's artifact, carrying prior approved artifacts + re-draft comments + workspace.",
        variables=(
            PromptVariable(name="task", type="textarea", required=True,
                           description="The overall design task (stripped)."),
            PromptVariable(name="step_title", required=True, description="This step's human title."),
            PromptVariable(name="step_kind", required=True, description="This step's kind slug."),
            PromptVariable(name="objective", type="textarea", default="",
                           description="This step's objective (empty when none)."),
            PromptVariable(name="approved_block", type="textarea", default="",
                           description="Pre-rendered approved-artifact lines (empty when none)."),
            PromptVariable(name="comments_block", type="textarea", default="",
                           description="Pre-rendered user-comment lines (empty when none)."),
            PromptVariable(name="workspace_dir", default="",
                           description="The bound workspace path (empty when none)."),
            PromptVariable(name="artifact_sentinel", required=True,
                           description="The artifact JSON filename to write (step_artifact.json)."),
            PromptVariable(name="artifact_contract", type="textarea", required=True,
                           description="The expected artifact JSON contract for this step kind."),
        ),
    ),
    BundledPrompt(
        name="task-research-step-brief", use_case="research_step_brief",
        filename="task-research_step_brief.md", kind="user", category="loop",
        description="Research-loop planning: the research-specific artifact contract appended to the (goal-derived) step brief for the subtopics/output step kinds (empty for intent/execution_plan).",
        variables=(
            PromptVariable(name="step_kind", required=True,
                           description="This step's kind slug (subtopics/output carry a contract; others render empty)."),
        ),
    ),

    # ── loop classifiers + cycle judges (intake analyze + done-ness assessors) ──
    BundledPrompt(
        name="task-goal-classify", use_case="goal_classify", filename="task-goal_classify.md",
        kind="user", category="loop",
        description="Goal-loop intake classifier: read a goal + installed-capability catalog, return goal_type/rigor/execution/roster/sub_goals/deliverables/execution_plan (JSON out).",
        variables=(
            PromptVariable(name="catalog", type="textarea", default="",
                           description="The installed skills/workflows/agents catalog block (empty when none installed)."),
            PromptVariable(name="goal", type="textarea", required=True, description="The goal to classify."),
        ),
    ),
    BundledPrompt(
        name="task-code-classify", use_case="code_classify", filename="task-code_classify.md",
        kind="user", category="loop",
        description="Code (SDLC) intake classifier: read a software task + capability catalog, return entry_stage/project_kind/rigor/execution/stage_plan with per-stage tasks (JSON out).",
        variables=(
            PromptVariable(name="catalog", type="textarea", default="",
                           description="The installed skills/workflows/agents catalog block (empty when none installed)."),
            PromptVariable(name="task", type="textarea", required=True, description="The software task to classify."),
        ),
    ),
    BundledPrompt(
        name="task-cycle-judge", use_case="cycle_judge", filename="task-cycle_judge.md",
        kind="user", category="loop",
        description="Open-ended cycle judge: a third-party assessor scores one loop cycle's done-ness/marginal-value/quality/regression (JSON out).",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True, description="The goal under assessment."),
            PromptVariable(name="dod", type="textarea", default="",
                           description="The definition-of-done line, pre-assembled with its leading newline (empty when no success criteria)."),
            PromptVariable(name="digest", type="textarea", required=True,
                           description="The compact digest of prior cycles' summaries."),
            PromptVariable(name="cycle", description="This cycle's number (or '?')."),
            PromptVariable(name="evidence", type="textarea", required=True,
                           description="The evidence the worker reported this cycle."),
            PromptVariable(name="metric_line", type="textarea", default="",
                           description="The reported-metric line, pre-assembled with its leading newline (empty when no metric)."),
        ),
    ),
    BundledPrompt(
        name="task-cycle-judge-skeptic", use_case="cycle_judge_skeptic", filename="task-cycle_judge_skeptic.md",
        kind="user", category="loop",
        description="Adversarial cycle judge (P4): a second, skeptical assessor that tries to REFUTE a claimed completion/regression before the supervisor trusts it (JSON out, same shape as cycle_judge).",
        variables=(
            PromptVariable(name="goal", type="textarea", required=True, description="The goal under assessment."),
            PromptVariable(name="dod", type="textarea", default="",
                           description="The definition-of-done line, pre-assembled with its leading newline (empty when no success criteria)."),
            PromptVariable(name="digest", type="textarea", required=True,
                           description="The compact digest of prior cycles' summaries."),
            PromptVariable(name="cycle", description="This cycle's number (or '?')."),
            PromptVariable(name="evidence", type="textarea", required=True,
                           description="The evidence the worker reported this cycle."),
            PromptVariable(name="metric_line", type="textarea", default="",
                           description="The reported-metric line, pre-assembled with its leading newline (empty when no metric)."),
        ),
    ),
    BundledPrompt(
        name="task-subgoal-judge", use_case="subgoal_judge", filename="task-subgoal_judge.md",
        kind="user", category="loop",
        description="Verifiable-goal sub-goal gate: after the automated check passes, confirm EVERY sub-goal is met from cycle evidence (PASS/FAIL out).",
        variables=(
            PromptVariable(name="task", type="textarea", required=True, description="The overall goal."),
            PromptVariable(name="criteria", type="textarea", required=True,
                           description="The sub-goals, one '- <sub-goal>' per line."),
            PromptVariable(name="evidence", type="textarea", required=True,
                           description="Evidence from recent cycles, one '- cycle N: summary' per line."),
        ),
    ),
    BundledPrompt(
        name="task-design-phases", use_case="design_phases", filename="task-design_phases.md",
        kind="user", category="loop",
        description="Design-loop phase planner: tailor the canonical design-system phases (foundations/palette/typography/components/export) to a task (JSON out).",
        variables=(
            PromptVariable(name="task", type="textarea", required=True, description="The design task to phase."),
        ),
    ),
    BundledPrompt(
        name="task-sdlc-stage-gate", use_case="sdlc_stage_gate", filename="task-sdlc_stage_gate.md",
        kind="user", category="loop",
        description="Strict SDLC stage gate: decide PASS/FAIL on whether a stage's exit criteria are fully met from cycle evidence.",
        variables=(
            PromptVariable(name="stage_title", required=True, description="The stage title."),
            PromptVariable(name="objective", type="textarea", default="", description="The stage objective."),
            PromptVariable(name="criteria", type="textarea", required=True,
                           description="Exit criteria, one '- <criterion>' per line."),
            PromptVariable(name="evidence", type="textarea", required=True,
                           description="Evidence from recent cycles + any automated check results."),
        ),
    ),

    # ── evaluation-harness prompts ──
    BundledPrompt(
        name="eval-judge", use_case="eval_judge", filename="task-eval_judge.md",
        kind="user", category="eval",
        description="LLM judge: score an assistant response 1-5 on a scenario's memory/context criteria (JSON out).",
        variables=(
            PromptVariable(name="scenario_description", type="textarea", required=True,
                           description="The evaluation scenario description."),
            PromptVariable(name="criteria", type="textarea", required=True, description="Scoring criteria."),
            PromptVariable(name="user_message", type="textarea", required=True, description="What the user said."),
            PromptVariable(name="assistant_response", type="textarea", required=True,
                           description="The assistant response under evaluation."),
        ),
    ),
)


BUNDLED_SNIPPETS: tuple[BundledSnippet, ...] = (
    BundledSnippet(
        name="safety-rules", filename="safety-rules.md",
        description="Core safety guardrails — no git push / destructive commands / credential reads; read-only AWS; bind HTTP to 127.0.0.1.",
    ),
    BundledSnippet(
        name="diff-output", filename="diff-output.md",
        description="The mandatory unified-diff output rule shown after any file change.",
    ),
    BundledSnippet(
        name="skills-syntax", filename="skills-syntax.md",
        description="Read the relevant skill for a tool's exact syntax before first use.",
    ),
    BundledSnippet(
        name="memory-discipline", filename="memory-discipline.md",
        description="Save corrections with memory_remember; search memory before claiming you don't know.",
    ),
    BundledSnippet(
        name="parallel-subagents", filename="parallel-subagents.md",
        description="Use subagent_run for parallel work — never a built-in mechanism.",
    ),
    BundledSnippet(
        name="subagent-orchestration", filename="subagent-orchestration.md",
        description="The spawn-and-synthesize pattern: tasks array + wait=false, results inject back.",
    ),
    BundledSnippet(
        name="mcp-reconnect", filename="mcp-reconnect.md",
        description="A same-turn MCP disconnect→reconnect is transient — retry, report only after 2+ failures.",
    ),

    # ── injected session-mode instruction blocks (prepended to a turn) ──
    BundledSnippet(
        name="autonomous-turn-preamble", filename="autonomous-turn-preamble.md",
        description="Autonomous-run preamble prepended to unattended turns (cron, goal loops, spaces): no questions, no option menus, report at the end.",
    ),
    BundledSnippet(
        name="session-incognito", filename="session-incognito.md",
        description="Incognito-session instruction: no memory writes; memory_forget/cron allowed.",
    ),
    BundledSnippet(
        name="session-temporary", filename="session-temporary.md",
        description="Temporary-session instruction: blank slate, no memory reads or writes at all.",
    ),
    BundledSnippet(
        name="project-context", filename="project-context.md",
        description="First-turn project-scope framing for a project-bound chat; wraps the assembled project details.",
        variables=(
            PromptVariable(name="project_name", required=True,
                           description="The project's display name."),
            PromptVariable(name="project_details", type="textarea", default="",
                           description="Assembled detail lines: brief, workspace, context dir + files, loop history."),
        ),
    ),
    BundledSnippet(
        name="prompt-expansion", filename="prompt-expansion.md",
        description="Wrapper for an @-mentioned prompt expanded in chat: the rendered instructions + optional user context.",
        variables=(
            PromptVariable(name="content", type="textarea", required=True,
                           description="The rendered prompt-template content to execute."),
            PromptVariable(name="user_text", type="textarea", default="",
                           description="Optional additional context the user typed alongside the @-mention."),
        ),
    ),

    # ── session-context instruction blocks (composed into build_session_context) ──
    BundledSnippet(
        name="critical-rules", filename="critical-rules.md",
        description="The always-on critical rules injected every session: diff-after-change, absolute paths in backticks, [OPTIONS:] buttons.",
    ),
    BundledSnippet(
        name="workspace-identity", filename="workspace-identity.md",
        description="Workspace-identity block: explains the working directory is the workspace, with cwd-scoped memory and global/workspace lesson scopes.",
        variables=(
            PromptVariable(name="ws_path", default="(none)",
                           description="The session's working directory path."),
        ),
    ),
    BundledSnippet(
        name="thread-history-header", filename="thread-history-header.md",
        description="Header framing for injected thread conversation history — tells the agent it's the PRIMARY context, not a task to re-run.",
    ),
    BundledSnippet(
        name="session-context-wrapper", filename="session-context-wrapper.md",
        description="Wrapper framing for the assembled session context (memory/lessons/history) — background reference only, respond to current request.",
        variables=(
            PromptVariable(name="session_context", type="textarea", required=True,
                           description="The assembled session-context body to wrap."),
        ),
    ),
    BundledSnippet(
        name="channel-thread-context", filename="channel-thread-context.md",
        description="Channel thread metadata block (channel_id/thread_ts) with optional parent text and MCP fetch guidance.",
        variables=(
            PromptVariable(name="channel_id", required=True, description="Channel id."),
            PromptVariable(name="thread_ts", required=True, description="Thread timestamp."),
            PromptVariable(name="thread_parent_text", type="textarea", default="",
                           description="The original thread-parent post text, if available."),
        ),
    ),
    BundledSnippet(
        name="cancelled-turn-preamble", filename="cancelled-turn-preamble.md",
        description="Context-restore preamble describing the user-cancelled previous turn.",
        variables=(
            PromptVariable(name="user_text", type="textarea", required=True,
                           description="The cancelled user request (truncated)."),
            PromptVariable(name="assistant_text", type="textarea", default="",
                           description="Partial assistant response before cancel, if any (truncated)."),
        ),
    ),
    BundledSnippet(
        name="widget-instructions", filename="widget-instructions.md",
        description="Inline-widget rendering instructions for dashboard sessions, by density ('more' shows interactive widgets, 'less' is markdown-first).",
        variables=(
            PromptVariable(name="density", type="select", default="more", options=["more", "less"],
                           description="Dashboard widget density ('more' or 'less')."),
        ),
    ),
    BundledSnippet(
        name="agent-runtime-identity", filename="agent-runtime-identity.md",
        description="Tells the agent which agent it is and which runtime it runs in (dashboard/channel/CLI/cron), so it answers natively.",
        variables=(
            PromptVariable(name="agent_label", required=True, description="The running agent's name."),
            PromptVariable(name="runtime", required=True, description="Human-readable runtime name."),
        ),
    ),
    BundledSnippet(
        name="agent-system-prompt-wrapper", filename="agent-system-prompt-wrapper.md",
        description="Wrapper framing around the resolved agent system prompt at session start.",
        variables=(
            PromptVariable(name="agent_prompt", type="textarea", required=True,
                           description="The resolved + runtime-var-rendered agent system prompt."),
        ),
    ),
    BundledSnippet(
        name="cross-tab-context", filename="cross-tab-context.md",
        description="Framing for recent activity in sibling dashboard tabs — awareness only, not tasks to act on.",
        variables=(
            PromptVariable(name="cross_lines", type="textarea", required=True,
                           description="The assembled recent sibling-tab message lines."),
        ),
    ),
    BundledSnippet(
        name="learned-corrections-header", filename="learned-corrections-header.md",
        description="Header framing for the injected learned-corrections (lessons) block — always-follow, overrides defaults.",
    ),
    BundledSnippet(
        name="subagent-system-prefix", filename="subagent-system-prefix.md",
        description="System prefix prepended to a spawned sub-agent's task: focused, concise, no self-narration, no [OPTIONS:].",
    ),
    BundledSnippet(
        name="agent-voice-layer", filename="agent-voice-layer.md",
        description="The VOICE-layer framing that prepends an agent's persona (voice) above its operating rules (system prompt).",
        variables=(
            PromptVariable(name="voice", type="textarea", required=True,
                           description="WHO the agent is — tone/opinions/persona."),
            PromptVariable(name="system_prompt", type="textarea", default="",
                           description="The agent's operating rules (system prompt)."),
        ),
    ),
    BundledSnippet(
        name="persona-lumon", filename="persona-lumon.md",
        description="The Lumon persona, appended on first turn for the 'lumon' dashboard theme.",
    ),

    # ── loop per-cycle directives (prepended to a worker's cycle nudge) ──
    BundledSnippet(
        name="loop-code-stage-directive", filename="loop-code-stage-directive.md",
        description="SDLC stage directive: names the active stage + objective + exit criteria (+ optional deliverable/delegation), prepended to the cycle nudge.",
        variables=(
            PromptVariable(name="label", required=True,
                           description="The stage label, e.g. 'stage 2/4 — Design'."),
            PromptVariable(name="objective", type="textarea", default="",
                           description="The stage objective, if any."),
            PromptVariable(name="criteria_joined", type="textarea", default="",
                           description="The stage's exit criteria, joined with '; ' (empty when none)."),
            PromptVariable(name="deliverable", default="",
                           description="The stage's named deliverable, if any."),
            PromptVariable(name="agent_name", default="",
                           description="A specialist agent to delegate this stage to, if any."),
        ),
    ),
    BundledSnippet(
        name="loop-orchestrator-directive", filename="loop-orchestrator-directive.md",
        description="The per-cycle ORCHESTRATOR directive: delegate the step to roster personas as subagents, then write the finding from their results.",
    ),
    BundledSnippet(
        name="loop-goal-phase-directive", filename="loop-goal-phase-directive.md",
        description="Goal-loop phase directive: names the active execution_plan phase + objective (+ optional delegation/next-phase signal), prepended to the cycle nudge.",
        variables=(
            PromptVariable(name="label", required=True,
                           description="The phase label, e.g. 'phase 1/3 — researcher'."),
            PromptVariable(name="target", type="textarea", default="",
                           description="This phase's objective, if any."),
            PromptVariable(name="agent_name", default="",
                           description="A specialist agent to delegate this phase to, if any."),
            PromptVariable(name="next_exit", type="textarea", default="",
                           description="The next-phase exit signal — set only when there IS a next phase and it declares one (empty otherwise)."),
        ),
    ),
    BundledSnippet(
        name="loop-goal-cycle-nudge", filename="loop-goal-cycle-nudge.md",
        description="Goal-loop per-cycle trigger: the cycle steps + mandatory finding write, plus the goal-type-shaped deliverable/verifiable/orchestrator blocks, prepended (with any phase directive) to the worker's turn.",
        variables=(
            PromptVariable(name="loop_id", required=True, description="The loop id."),
            PromptVariable(name="loop_dir", required=True,
                           description="The working dir for loop files (findings/brief/guidance)."),
            PromptVariable(name="has_deliverables", type="boolean", default=False,
                           description="True when the goal declares MULTIPLE separate deliverables."),
            PromptVariable(name="deliverables_count", type="number", default=0,
                           description="The number of separate deliverables (when has_deliverables)."),
            PromptVariable(name="deliverables_names", default="",
                           description="The separate deliverable names, joined with ', ' (when has_deliverables)."),
            PromptVariable(name="show_single_deliverable", type="boolean", default=False,
                           description="True when there is ONE document deliverable and no multi-deliverable list (the if/elif precedence)."),
            PromptVariable(name="deliverable", default="",
                           description="The single document deliverable filename (when show_single_deliverable)."),
            PromptVariable(name="is_verifiable", type="boolean", default=False,
                           description="True for a verifiable goal (no document deliverable; the supervisor runs the check)."),
            PromptVariable(name="is_multi_agent_with_roster", type="boolean", default=False,
                           description="True when this loop runs multi-agent with a roster (the worker is the orchestrator)."),
        ),
    ),

    # ── memory-consolidation per-key extraction fragments ──
    # Each describes one JSON key the consolidation agent may return. The runtime
    # selects which apply (by vector/migration/skill flags) and numbers them into
    # the ``task-memory-consolidation`` envelope.
    BundledSnippet(name="consolidation-key-history", filename="consolidation-history.md",
                   description="Consolidation key: history_entry (session summary paragraph)."),
    BundledSnippet(name="consolidation-key-semantic", filename="consolidation-semantic.md",
                   description="Consolidation key: semantic (structured long-term facts array)."),
    BundledSnippet(name="consolidation-key-episodic", filename="consolidation-episodic.md",
                   description="Consolidation key: episodic (conversation fragments array)."),
    BundledSnippet(name="consolidation-key-preferences", filename="consolidation-preferences.md",
                   description="Consolidation key: preferences_update (full markdown prefs file)."),
    BundledSnippet(name="consolidation-key-projects", filename="consolidation-projects.md",
                   description="Consolidation key: projects_update (full markdown projects file)."),
    BundledSnippet(name="consolidation-key-lessons", filename="consolidation-lessons.md",
                   description="Consolidation key: lessons (implicit user corrections array)."),
    BundledSnippet(name="consolidation-key-self-persona", filename="consolidation-self_persona.md",
                   description="Consolidation key: self_persona (first-person positive growth notes)."),
    BundledSnippet(name="consolidation-key-commitments", filename="consolidation-commitments.md",
                   description="Consolidation key: commitments (guardrailed proactive future check-ins).",
                   variables=(PromptVariable(name="max_commitments", type="number", default=3,
                                             description="Max commitments to extract."),)),
    BundledSnippet(name="consolidation-key-new-skill", filename="consolidation-new_skill.md",
                   description="Consolidation key: new_skill (reusable multi-step procedure or null)."),
    BundledSnippet(name="consolidation-key-refined-skill", filename="consolidation-refined_skill.md",
                   description="Consolidation key: refined_skill (improved existing auto/ skill or null)."),
)


# ── Derived lookups (built once at import) ──────────────────────────────────────

_PROMPT_BY_USE_CASE: dict[str, BundledPrompt] = {p.use_case: p for p in BUNDLED_PROMPTS}
_PROMPT_BY_NAME: dict[str, BundledPrompt] = {p.name: p for p in BUNDLED_PROMPTS}


def prompt_for_use_case(use_case: str) -> BundledPrompt | None:
    """The bundled prompt that serves ``use_case`` by default, or None."""
    return _PROMPT_BY_USE_CASE.get(use_case)


def prompt_by_name(name: str) -> BundledPrompt | None:
    return _PROMPT_BY_NAME.get(name)


def use_cases() -> tuple[str, ...]:
    """Every bindable prompt use-case, in catalog (display) order."""
    return tuple(p.use_case for p in BUNDLED_PROMPTS)


def use_case_category(use_case: str) -> str:
    p = _PROMPT_BY_USE_CASE.get(use_case)
    return p.category if p else "internal"
