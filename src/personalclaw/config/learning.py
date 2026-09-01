"""Config sections for the learning loop: how the system improves from its own runs.

One domain, six sections. They sit together because they form a single pipeline — a loop
runs, a judge scores it, the feedback and evals sections decide what is recorded, and the
learning and planning sections decide what is carried forward. The three coercers here are
domain-specific (a judge axis, a stagnation window, an identity-report cadence) and belong
beside the fields that read them rather than in the shared coercion leaf.

Deliberately NO ``from __future__ import annotations``: ``config/schema.py`` resolves a
STRING annotation by ``eval``-ing it in ``config.loader``'s namespace with a silent
``except: return str`` fallback, so postponed annotations here would degrade this file's
schema types to ``string`` without any error. Real type objects cannot take that path.
"""

from dataclasses import dataclass, field

from personalclaw.config.coercion import _meta


def _identity_report_cadence(value: object) -> str:
    """Coerce ``learning.identity_report_cadence``, reading an unknown word as the default.

    Delegates to :func:`personalclaw.learning_report.normalize_cadence` instead of repeating the
    vocabulary, so the loader, the PATCH allowlist's enum spec, the reconciler's cron map and the
    frontend control cannot drift apart — the failure mode ``guardrails.scan_mode``'s three
    hand-copied ``warn/redact/block`` tuples are one edit away from. Imported lazily (like
    ``_slug_username`` above) so this module keeps no import-time dependency on
    ``learning_report``, which reads ``AppConfig`` back.
    """
    try:
        from personalclaw.learning_report import normalize_cadence

        return normalize_cadence(value)
    except Exception:
        return "monthly"


def _judge_axis(value: object) -> str:
    """Normalize ``loops.judge_use_case`` on load (WF2LOO-17).

    Fail-SAFE, not fail-open: an unknown/blank axis collapses to ``reasoning``
    rather than to the worker's ``loops`` axis, because the failure mode we are
    closing is exactly "the judge silently grades on the worker's binding". A
    typo in config.json must not re-create it. Imported lazily so the loader
    keeps no module-level dependency on the provider package.
    """
    axis = str(value or "").strip()
    if not axis:
        return "reasoning"
    try:
        from personalclaw.providers.use_cases import VALID_USE_CASES
    except Exception:
        return axis
    return axis if axis in VALID_USE_CASES else "reasoning"


def _stagnation_window(value: object) -> int:
    """Normalize ``loops.stagnation_window`` on load (WF2LOO-18).

    Clamped to [2, 50] and fail-safe to the default: a garbage or missing value keeps the
    stall detector at its shipped sensitivity rather than disabling it. The floor of 2 is
    structural — the two worker-independent signals compare findings BETWEEN cycles, so a
    window of 1 could only ever compare a cycle with itself and would detect nothing.
    """
    try:
        window = int(str(value).strip())
    except (TypeError, ValueError):
        return 5
    return max(2, min(50, window))


@dataclass
class LoopsConfig:
    """Settings for autonomous goal loops (the unified autonomous goal engine)."""

    max_cycles_hard_cap: int = field(
        default=100,
        metadata=_meta(
            "Max Cycles Hard Cap",
            "Absolute ceiling on a loop's cycle budget, regardless of the "
            "per-loop limit. Safety brake against runaway cost.",
        ),
    )
    default_idle_secs: int = field(
        default=120,
        metadata=_meta(
            "Default Idle Seconds",
            "Default seconds between worker cycles (the autonudge idle timer) "
            "when a loop does not specify its own.",
        ),
    )
    trust_ttl_secs: int = field(
        default=24 * 3600,
        metadata=_meta(
            "Trust TTL Seconds",
            "How long a loop's worker keeps auto-approved tool trust before "
            "the supervisor expires it and requires re-authorization.",
        ),
    )
    judge_use_case: str = field(
        default="reasoning",
        metadata=_meta(
            "Judge Model Axis",
            "Which model axis the loop JUDGE rides — deliberately not the "
            "`loops` axis the worker rides. Default `reasoning`: the judge that "
            "certifies done-ness gets its own (typically stronger) binding, so a "
            "reviewer mistake is not correlated with the mistake it is reviewing. "
            "Set to `loops` to put judge and worker back on one binding.",
        ),
    )
    stagnation_window: int = field(
        default=5,
        metadata=_meta(
            "Stagnation Window",
            "How many consecutive cycles of no progress stall a loop. The "
            "supervisor compares the last N findings: byte-identical cycle "
            "reports, an identical set of sources checked, or N cycles reporting "
            "no new findings all mean the loop is spinning. Lower is more "
            "trigger-happy; higher spends more cycles before asking for "
            "direction. Minimum 2 — a window of 1 can only compare a cycle "
            "with itself.",
        ),
    )
    check_work_stages: bool = field(
        default=False,
        metadata=_meta(
            "Check Work After Stage Gates",
            "After an SDLC stage's gate passes, re-derive 2-4 executable checks from "
            "what the stage CLAIMED and run them (the `check-work` skill's module). "
            "Catches the 'gate command passed but the claim was broader than the "
            "command' case — e.g. a deliverable file the stage said it wrote but "
            "didn't. Off by default: it adds a filesystem pass per stage advance.",
        ),
    )
    worktree_sparse: bool = field(
        default=True,
        metadata=_meta(
            "Sparse Task Worktrees",
            "When a parallel task's plan names the files it will touch, hydrate only "
            "those directories in its git worktree instead of the whole repo. On a "
            "large codebase this is most of a worktree's setup cost. Safe by "
            "construction: a task that writes outside its stated scope widens its own "
            "worktree automatically, a task with no usable scope gets a full checkout, "
            "and the merged result is identical either way. Turn off to always hydrate "
            "the full repo.",
        ),
    )


@dataclass
class LearningConfig:
    """Per-turn self-improvement review (learn-after-turn-review).

    After a learning-worthy turn (a correction signal, or ≥min_tool_calls), a
    bounded background review may persist a memory fact. Distinct from
    consolidation (batched, session-end) — this is continuous + correction-timely.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "After-Turn Learning",
            "Run a quick background review after a learning-worthy turn to capture "
            "user corrections/preferences as durable memory — continuous (vs the "
            "session-end consolidation). Skipped for incognito/temporary sessions.",
        ),
    )
    min_tool_calls: int = field(
        default=4,
        metadata=_meta(
            "Learning Min Tool Calls",
            "A turn with at least this many tool calls qualifies for review even "
            "without a correction signal (substantial work worth learning from).",
        ),
    )
    correction_heuristic: bool = field(
        default=True,
        metadata=_meta(
            "Correction Heuristic",
            "Treat a user message that negates/corrects the prior turn (no, don't, "
            "actually, instead, wrong…) as a first-class learning signal.",
        ),
    )
    surface_chip: bool = field(
        default=True,
        metadata=_meta(
            "Surface Learned Chip",
            "Show a quiet 'Learned: …' chip in chat when something is captured.",
        ),
    )
    skill_ladder: bool = field(
        default=True,
        metadata=_meta(
            "Skill-Ladder Review",
            "On a learning-worthy turn, run a bounded background LLM review that may "
            "PROPOSE a reusable skill (refine an existing one before minting a new "
            "one). Proposals land in the Skill-proposals inbox for your approval — "
            "never installed automatically. Off = memory-only learning.",
        ),
    )
    min_evidence: int = field(
        default=3,
        metadata=_meta(
            "Minimum Evidence",
            "How many separate occurrences a pattern needs before it can be proposed "
            "as durable learning. One is an anecdote and two a coincidence; this same "
            "floor is shared by the promotion ladder, pattern synthesis, and inferred "
            "proposals, so they cannot disagree about what counts as evidence.",
        ),
    )
    min_lesson_confidence: float = field(
        default=0.5,
        metadata=_meta(
            "Lesson Injection Confidence",
            "How well supported a learned lesson must be before it is injected into "
            "a prompt. Every lesson carries a confidence DERIVED from its evidence — "
            "how often it was observed, how recently, whether anything contradicted "
            "it, whether a correction reversed it. Below this floor a lesson is "
            "RETAINED: still stored, still accumulating evidence, but kept out of the "
            "prompt. The default 0.5 is not a picked number — it is exactly the "
            "confidence a lesson reaches at the third corroborating observation, "
            "which is the same evidence floor Minimum Evidence already sets for every "
            "other learning path (one is an anecdote and two a coincidence). Raise it "
            "to demand more corroboration; 0 injects anything that exists.",
        ),
    )
    staging_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Capture Staging Log",
            "Record every capture pass in an append-only log with an explicit outcome "
            "(produced / nothing-found / error). This is what makes a silently broken "
            "capture path visible — without it, a pass that crashes looks exactly like "
            "a quiet day. Off = capture still runs, but its failures are invisible.",
        ),
    )
    self_model_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learn From What Works",
            "Notice working patterns and OFFER them as behavioral principles. Every other "
            "learning path only sees corrections and failures, so this is the one that can "
            "learn from what quietly succeeds. It never installs anything: a pattern that "
            "recurs and keeps working becomes a proposal you review, capped at a handful of "
            "principles so it cannot grow without displacing one you already accepted.",
        ),
    )
    min_session_score: float = field(
        default=0.0,
        metadata=_meta(
            "Minimum Session Score",
            "Sessions scoring below this (0.0-1.0, weighted toward decisions rather "
            "than raw turn count) are skipped by the session-end consolidation pass. "
            "0 = score every session; raise it to stop paying to learn from thin ones.",
        ),
    )
    context_budget_tokens: int = field(
        default=4000,
        metadata=_meta(
            "Learning Context Budget",
            "Token budget for the ranked learning block (lessons, skills, memory, "
            "retrieved context) injected each turn. Only retrieved context is ever "
            "trimmed — lessons and instructions are never crowded out, and an item "
            "that does not fit is dropped whole rather than cut mid-sentence.",
        ),
    )
    curator_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learning Curator",
            "Age the learned library (skills, templates) on the consolidation cadence: "
            "unused items go stale, then archived. Never deletes, always reversible, and "
            "refuses any pass that would cut more than half the library. Off = the "
            "library grows without grooming.",
        ),
    )
    propose_quota_per_run: int = field(
        default=5,
        metadata=_meta(
            "Proposals Per Run",
            "How many proposals one learning pass may file. A pass that files twenty "
            "is not being thorough, it is being unreadable — and a queue nobody "
            "finishes reading is a queue that stops being read at all.",
        ),
    )
    replay_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Replay Evidence On Proposals",
            "Before you decide on a proposed skill or template, replay a few real turns from "
            "your captured coding sessions twice — once without the candidate, once with it — "
            "and show both scores on the proposal card. It is evidence, never a veto: a 'made "
            "things worse' verdict still lets you accept. Off by default because it spends "
            "money on the maintenance pass, and it does nothing until you also set a replay "
            "budget below.",
        ),
    )
    replay_max_dollars: float = field(
        default=0.0,
        metadata=_meta(
            "Replay Budget (USD)",
            "The spend ceiling for one replay pass. 0 means replay does not run at all — "
            "deliberately, because 0 reads as 'unlimited' to the spend meter and an unbounded "
            "LLM pass on a background tick is the one thing this must never be. Reaching the "
            "ceiling defers the remaining proposals to a later pass with a labelled card, "
            "never a silent skip.",
        ),
    )
    run_end_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Learn From Run Failures",
            "When a workflow run ends, mine its Run Ledger for failed steps and file "
            "lesson proposals (never auto-written) keyed by failure mode, plus a "
            "procedural-outcome record per failed step. Off = terminal runs leave no "
            "learning trace. Only fires when a memory service is available.",
        ),
    )
    attribution_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Grade Accepted Changes",
            "After you accept a proposal, watch its target's Run Ledger. Once enough runs "
            "have accumulated, grade whether the change delivered what it predicted — and if "
            "it only made things worse, file a revert proposal (never auto-applied) that names "
            "what broke. Off = accepted changes are never measured against their promise.",
        ),
    )
    identity_report_cadence: str = field(
        default="monthly",
        metadata=_meta(
            "Identity Report",
            "How often to write 'how I've adapted to you' — one readable document over the "
            "preferences, lessons and skills learned so far, saved as a versioned artifact and "
            "announced once in your inbox. Monthly, weekly, or off. Off means the scheduled "
            "job does not run at all; you can still write one by hand from the Learning page. "
            "This is the only switch there is — no separate on/off flag that could disagree "
            "with the cadence.",
        ),
    )


@dataclass
class EvalsConfig:
    """The offline eval substrate (EVALUATION-SUBSTRATE §10).

    Off by default: the substrate runs nothing until a study/benchmark/matrix is
    invoked. Everything it produces is a file under ``~/.personalclaw/evals/``;
    there is no daemon. ``bakeoff_capture_enabled`` is a privacy-sensitive capture
    flag kept OFF by default and out of the one-click PATCH allowlist (like
    ``inbound.mcp.allow_remote``) — flipping it is a deliberate config-file edit.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Evals enabled",
            "Turn on the offline eval substrate — pre-registered studies, ablation "
            "reports, the retrieval/judge benchmarks. Off by default; nothing runs "
            "until you invoke a study or benchmark. Results are files under "
            "~/.personalclaw/evals/, never a background service.",
        ),
    )
    study_default_k: int = field(
        default=5,
        metadata=_meta(
            "Study runs per arm (k)",
            "How many paired runs per arm a template A/B study takes by default. "
            "k≈5 is the smallest paired design that survives judge noise; higher k "
            "buys confidence at a linear cost in runs and judge calls.",
        ),
    )
    judge_agreement_floor: float = field(
        default=0.6,
        metadata=_meta(
            "Judge agreement floor",
            "Below this position-swap agreement rate a study's verdict is "
            "'judge_unreliable' — it files a judge-calibration item instead of a "
            "template verdict, so a noisy judge never produces a fake win.",
        ),
    )
    ablation_cadence_days: int = field(
        default=30,
        metadata=_meta(
            "Ablation cadence (days)",
            "How often the harness-ablation runner picks one component to measure "
            "keep/remove/lighten. Monthly by default — component payoff drifts on the "
            "timescale of model upgrades, not days.",
        ),
    )
    bakeoff_capture_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Bake-off input capture",
            "Let the model bake-off capture real per-use-case inputs to score "
            "candidate models on your actual traffic. OFF by default and privacy-"
            "sensitive: captured inputs are redacted and the capture auto-expires. "
            "Flipping it is a deliberate config-file edit, not a one-click toggle.",
        ),
    )
    default_budget_usd: float = field(
        default=0.0,
        metadata=_meta(
            "Default eval budget (USD)",
            "The default hard spend cap a matrix/study run refuses to exceed. 0 means "
            "no default cap — each study still declares its own budget at registration.",
        ),
    )


@dataclass
class ProactiveConfig:
    """Proactive triage + the decision journal (PROACTIVE-ASSISTANT §"Config Map").

    Two switches are OFF by default and stay that way: ``triage_enabled`` (nothing
    collects or spends until you ask for a digest) and ``auto_execute_enabled``
    (the digest proposes; it does not act). That pairing is the plan's soul
    guardrail — proactive behaviors propose, they never silently write — so the
    defaults are fail-closed on purpose. Do not "helpfully" flip them.
    """

    triage_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Proactive triage",
            "Let the scheduled triage digest run — collect what accumulated across "
            "inbox, channels and background runs, then propose what to do about it. "
            "Off by default: nothing is collected and no model is called until you "
            "turn this on and install a schedule.",
        ),
    )
    digest_schedule: str = field(
        default="0 8 * * *",
        metadata=_meta(
            "Digest schedule",
            "When the triage digest fires, as a cron expression in your configured "
            "timezone. 08:00 daily by default — a morning digest. Quiet hours still "
            "apply: an info-ranked digest defers rather than waking you.",
        ),
    )
    auto_execute_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Auto-execute trivial actions",
            "Let trivial-tier proposals and patterns you explicitly taught with "
            "'always yes' execute without another confirmation. Off by default. Even "
            "on, external sends stay drafts until that rule is individually "
            "graduated, and every auto-execution is a ledger row with one-click undo.",
        ),
    )
    max_auto_actions_per_run: int = field(
        default=5,
        metadata=_meta(
            "Max auto-actions per digest",
            "Hard cap on how many actions one digest may auto-execute; the rest queue "
            "as pending regardless of tier. This is a ceiling, not a target — it "
            "bounds the blast radius of one bad classification.",
        ),
    )
    classifier_gate_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Classifier gate",
            "Filter collected items through the lightweight relevance gate before the "
            "proposal stage. On by default: it is what keeps a quiet window from "
            "spending anything, and turning it off sends every item to the model.",
        ),
    )
    decision_default_horizon_days: int = field(
        default=90,
        metadata=_meta(
            "Decision review horizon (days)",
            "How far out a logged decision schedules its review when you do not name "
            "a horizon. 90 days by default — long enough for an outcome to exist, "
            "short enough that you still remember the reasoning.",
        ),
    )

    def __post_init__(self) -> None:
        # A cap of 0 means "auto-execute nothing", which is a coherent position; a
        # negative cap is not, and would read as "unbounded" to a `<` check.
        if self.max_auto_actions_per_run < 0:
            self.max_auto_actions_per_run = 0
        if self.decision_default_horizon_days < 1:
            self.decision_default_horizon_days = 1


@dataclass
class FeedbackConfig:
    """Feedback Signal (plan 58) — 👍/👎 capture on AI judgment outputs + the
    deterministic per-producer accuracy thresholds. No LLM anywhere; zero telemetry."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Feedback",
            "Show 👍/👎 on AI judgment outputs (inbox classifications, drafts, digests, "
            "loop findings) and track per-source accuracy. Off = thumbs never render.",
        ),
    )
    retire_threshold: float = field(
        default=0.4,
        metadata=_meta(
            "Retire Threshold",
            "A judgment source whose accuracy falls below this (with enough verdicts) "
            "stops surfacing and gets a 'retire this rule?' proposal.",
        ),
    )
    min_n: int = field(
        default=5,
        metadata=_meta(
            "Minimum Verdicts",
            "Verdicts required before a source's accuracy is shown or acted on.",
        ),
    )
    window_days: int = field(
        default=90,
        metadata=_meta(
            "Attribution Window (days)",
            "How far back verdicts count toward a source's rolling accuracy.",
        ),
    )


@dataclass
class PlanningConfig:
    """Planner entry surfaces (WORKFLOWS-V2-UNIVERSAL-PLANNING UP-R18) — the watched
    scratchpad. Empty by default: an unset path reads no files at all, so ambient capture
    is something the user opts into by naming one local file, never a default that starts
    scanning their notes."""

    scratchpad_path: str = field(
        default="",
        metadata=_meta(
            "Scratchpad path",
            "A local notes file to scan for jotted todos. Each actionable line becomes a "
            "PROPOSED plan in your inbox with a link back to the source line — never run "
            "automatically. Checked (- [x]) and struck-through lines are ignored. "
            "Empty = off.",
        ),
    )
