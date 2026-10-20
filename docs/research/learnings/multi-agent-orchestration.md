# Multi-Agent Orchestration

> Part of the PersonalClaw research-learnings library. Source-agnostic; distilled 2026-07-13 from a 95-source competitive-research corpus.

## Principles (the durable truths)

**Structured state is durable; dialogue is ephemeral.** Pure natural-language multi-agent chat creates a "telephone effect": details are lost and shared state corrupts as conversations lengthen. The proven hybrid protocol is: agents communicate primarily through typed documents in a global state (reports, plans, verdicts); free-form NL is used *exclusively inside bounded scopes* (a debate, a worker session), and the scope's outcome is written back as a structured entry. Transcripts are node-local and dropped between stages.

**Handoff loss is the dominant multi-agent failure mode.** Consistent, typed handoffs — with acceptance criteria, evidence pointers, and an explicit "who consumes this next and in what format" field — are what prevent context loss at agent boundaries. Multiple independent systems converge on treating handoffs as schemas, not prose.

**Coordination is moving from declarative graphs to tool-driven orchestration.** One major production framework *deleted* its explicit pipeline/message-hub orchestration layer in a breaking 2.0 rewrite and replaced it with tools a leader LLM calls (create-team / create-agent / team-say) over a message bus, plus an agent-managed flat task list — betting that increasingly agentic LLMs need less structure. Meanwhile a widely-circulated architecture doctrine explicitly bets the opposite: single strong agent + explicit graph workflows + verifier layer, NOT a swarm. The synthesis: whichever side you build, the *agentic escape hatch* (cheap NL→workflow generation, mid-flight mutation) must be first-class, or agentic users route around the engine.

**Isolation of state, not shared mutable memory, is the coordination substrate.** Parallel agents converge on git-worktree-plus-branch per agent/task with no shared mutable state; the dashboard/board is the coordination surface. Where agents must share, it is through explicit queues, typed messages, or append-only files — never concurrent writes to one context.

**Reasoning is the escalation tier, not the default.** Monitoring, triage, and recovery layers should be cheap-code-first: a healthy-gate that bails, deterministic code fixes for routine cases, and LLM reasoning only for genuinely ambiguous situations.

**Workers must know their judge.** Pre-announcing the reviewing agent and the evidence it will demand ("your work will be reviewed by X, which requires screenshots and passing tests") in the worker's activation prompt raises first-pass rates mechanically, at zero cost.

**Roles are data, not prose.** Persona rosters, assignment matrices, and team runbooks work best as CI-guarded catalogs (JSON + slug-referenced markdown) consumed by both routers and planners — with drift checks that fail when catalog and on-disk agents disagree.

## Mechanisms (implementation-ready designs)

### Debate as a compilable macro
A multi-agent debate reduces to five primitives — no agent framework required:
- **State**: per-side history strings + one shared `history` + `current_response` + `judge_decision` + turn `count` (plain concatenated text, cheap to interpolate — no message objects).
- **Turn-taking**: pure function of counters + a speaker label. A 2-agent debate ends at `count >= 2 * max_rounds`, else routes to whichever side didn't speak last (detected by response prefix, e.g. `startswith("Bull")`). A 3-agent debate is round-robin ending at `count >= 3 * max_rounds`, routed off a `latest_speaker` prefix. Responses are prefixed with the role name — the prefix doubles as the router's signal. `max_rounds` (default 1) IS the depth/rigor knob.
- **Adversarial rebuttal prompts**: each debater sees all structured input reports + full shared debate history + the opponent's latest argument, instructed to "respond directly to each point... countering with data-driven rebuttals" and to persuade, not just present. If no opponent has spoken, present a standalone opening.
- **Judge**: a separate node consuming the accumulated history, producing a structured verdict written back into global state. Judges get graded verdict scales (e.g. 5-tier) with an explicit anti-fence-sitting instruction: "Reserve the middle verdict for situations where the evidence is genuinely balanced; otherwise commit to the side with the stronger arguments."
- **Router completeness invariant**: every conditional router's possible return values must be statically enumerated and fully mapped, so prompt/i18n/refactor drift in speaker labels can never hit a missing route and crash mid-run. Validate at spec-save time.

Parameterize by `roles: [{name, stance_prompt}]`, `rounds`, `judge: {prompt, verdict_schema}`. The same macro covers 2-role adversarial (bull/bear), 3-role round-robin (aggressive/conservative/neutral risk), and 6-role orthogonal-lens panels. See [verification-and-judging](verification-and-judging.md) for judge calibration priors.

### Orthogonal-lens role panels (perspective diversity without debate)
Fixed persona fan-outs work as parallel judges or ideators when each role carries an explicit orthogonality constraint ("stay strictly within this lens"): facts / risks / benefits / emotions / creative alternatives / process; or ideation rosters like Overly Positive, Overly Negative, Curious Child ("why/what if"), Skeptical Analyst, Visionary Futurist. A single prompt covering all lenses also works as a cheap single-call variant — expose `judge_mode: single-call-multi-lens | parallel` as a knob. See [planning-and-decomposition](planning-and-decomposition.md) for the ideation-method topologies these compose into.

### Two-tier model economics for role graphs
In an org-chart pipeline (analysts → debaters → trader/proposer → risk debate → final judge), cheap "quick-think" models power workers and debaters; the expensive "deep-think" model is reserved for exactly the judge/decision nodes. Encode as a portable hint (`judge_model_tier: deep`), and express model selection generally as portable intent tiers (`smol | regular | smart | ultra`) mapped through local provider bindings — never concrete model IDs in templates. Additionally, run judges on a **different model family than the generator**: same-family review tends to rubber-stamp.

### Typed handoff templates
A production handoff-template set covers 7 types: standard work transfer, QA-PASS, QA-FAIL, escalation, phase-gate, sprint, incident. Each carries:
- metadata (from / to / phase / task-ref / priority / timestamp);
- context (current state; relevant files *with what each contains*; dependencies; constraints);
- deliverable request (measurable acceptance-criteria checkboxes);
- quality expectations (must-pass items, evidence-required, and **who receives the output next + what format they need**).

QA-FAIL verdicts carry per-issue `{description, expected, actual, evidence, fix_instruction, files_to_modify, severity}` — this is what flows back into a retry branch, journaled per attempt.

### Dev↔QA loop with escalation-as-closed-menu
The core build cycle: assign task via an assignment matrix (task category → primary developer / backup / QA agent) → implement → QA tests with evidence → PASS advances and resets the retry counter; FAIL (attempt < 3) returns feedback with the constraint "Fix ONLY the issues listed. Do NOT introduce new features"; attempt = 3 escalates. The **escalation report is a typed artifact**: full 3-attempt failure history, root-cause analysis ("one-off or pattern? was the task properly scoped?"), and a *closed decision menu* — reassign to a different agent / decompose into sub-tasks / revise architecture / accept with documented limitations / defer — plus blocking/timeline/quality impact and a named decision-maker. Escalations surface as needs-input gates, never bare failures.

### Attempt-aware retries + completion protocol
Every execution attempt is a first-class record; retry contexts include prior outcomes "so they don't repeat a path that already failed." Workers MUST end with an explicit `complete(summary, metadata)` or `block(reason)` — exiting without one is a protocol violation that auto-blocks the task (never silently hung). Block kinds are typed — `needs_input | capability | transient` — enabling differentiated policy: transient retries, capability routes to setup, needs_input goes to a human inbox. A circuit breaker auto-blocks after `failure_limit` consecutive failures; respawn guards skip re-spawn on auth-blocked / recently-succeeded / pending-external-review states.

### Continue-vs-spawn policy (session reuse is a decision table, not a vibe)
Reuse an existing worker session when context overlap is high: its explored files match the edit target, or it is correcting its *own* failure (it has the full error context). Spawn fresh for: narrow implementation after broad research, independent verification, or wrong-approach retries. "High overlap → continue. Low overlap → spawn fresh." Corollary — the **synthesis mandate**: a coordinator must read worker findings and write the next worker's prompt containing file paths, line numbers, and exact changes; phrases like "based on your findings" are banned as lazy delegation. Verification workers must "prove the code works, not confirm it exists."

### Fork-don't-mutate agent invocation
When one agent invokes another existing session, the backend copies the target's history into a fork that processes the new message independently; only the text response (plus cost) crosses back. Caller lineage travels via environment (parent session id, board/container id). One system shipped this with **no depth limit** — the cautionary gap: always propagate and enforce a delegation-depth counter (a widely-cited doctrine default is max sub-delegation depth ~5, hard task timeout ~30 min, retry once then *change strategy*).

### Hierarchical delegation with profile specialization
A superior spawns a subordinate as `Agent(number+1)` with superior/subordinate links tracked as data keys; subordinates run their own loops and return results upward. Agent profiles (researcher / developer / tiny-local / custom) change prompts and behavior, merged from four origins with per-field overrides: default → plugin → user → project.

### Peer-team orchestration over a message bus (the tool-driven alternative)
The leader session gets `TeamCreate` / `AgentCreate` / `TeamSay` / `TeamDelete` / `AgentInvite`; workers get only `TeamSay`. Workers are **peer sessions, not nested coroutines under the leader** — concurrent and distributable. Key mechanics:
- **Typed subagent templates as tool-schema enums**: registered templates (`type`, `description`, `system_prompt_template` with `{team_name}/{member_name}/{leader_name}` placeholders, per-template permission context, seed task list) surface as a `subagent_type` enum on the spawn tool's schema — the LLM matches roles through the schema, not prose.
- **Ownership semantics**: workers *created by* a team are deleted with it; pre-existing agents *invited into* it merely lose the association on teardown.
- **Message-bus contract**: one abstract transport with six primitive modes — drain queue (single-consumer ack-on-read), replay log (multi-consumer, externally bounded), transient broadcast, distributed lock, registry map — with **all business key formats centralized in one auditable keys module**.
- **Inbox + wakeup**: team messages, background-tool completions, and scheduled fires all reduce to "push a typed hint block onto the target session's inbox + enqueue a wakeup; any dispatcher claims it and drives the run." Two wakeup kinds with different drop semantics: `wake` (idempotent nudge — skipped entirely if the session is already running) vs `resume` (carries a human-in-the-loop answer for a parked run — must re-queue until the parked run takes it). Without the split, gate answers get lost to the overlap guard.
- **Cross-session UI projection**: a per-target-session registry hash onto which another session projects UI cards (kind-prefixed keys like `{kind}:{source_run_id}`), so a worker's pending approval renders on its leader's surface. Resolution writes back through the normal resume path.
- Team messages arrive as XML-fenced typed units with source attribution (`<team-message from="…">`) — one typed unit for every out-of-band injection.

### Cross-process permission routing (headless child asks, human answers elsewhere)
A file-based mailbox — one JSON file per message at `teams/<team>/agents/<agent_id>/inbox/<timestamp>_<msgid>.json`, tmp-write + atomic rename so readers never see a partial message, filename = chronological order, corrupted files skipped — carries typed messages including `permission_request` / `permission_response` (with `permission_suggestions` and `updated_input`), `idle_notification` (worker self-reports idle with a summary), and `shutdown`. This is the minimal plumbing for routing a headless subagent's approval need to wherever the human is. See [security-and-guardrails](security-and-guardrails.md) for the approval-policy side.

### Coordinator-mode notification contract
The coordinator gets only spawn / send-message / stop tools; worker results come back as typed notifications (task_id, status ∈ completed|failed|killed, summary, result, usage: total_tokens / tool_uses / duration_ms) injected as user-role messages. Four-phase doctrine: Research (parallel) → Synthesis → Implementation → Verification, with write-heavy tasks serialized "one at a time per set of files." Workers may share a durable scratchpad directory for cross-worker knowledge.

### Parallel worktree isolation
Convergent across at least four independent systems (validated at 1M+ downloads in one): every parallel agent task gets its own **git worktree + branch**; the task is the container bundling "its branch, terminal, conversation, and review state." The adoption-critical detail is a declarative per-repo lifecycle config: `preservePatterns` (globs like `.env`, `.env.*.local`, `docker-compose.override.yml` copied into each fresh worktree so agents get working local config) plus `scripts.{setup, run, teardown}` hooks — fresh isolated copies are useless if local config doesn't follow. Repair-attempt nuance: first attempt hard-resets the worktree to base; later attempts preserve branch progress (measured as commits since base — ground truth, not self-report) and prompt for "the smallest patch." Teardown ordering matters: kill orphaned terminal panes FIRST, collect worktree paths BEFORE deleting directories, then remove.

### Agent monitoring boards
The proven board shape: parallel per-task cards showing a live tool-step feed (Read/Edit/Bash step names), test results, and status + elapsed chips ("running · 4m", "done · 8m ago"). Status derives from **lifecycle hooks** classified into three states — working / awaiting-input / done — which also drive OS notifications. Complementary details:
- Broadcast the step advance *before* dispatching the node so the UI flips immediately; a cheap 1.5s watcher extracts the latest tool-call name into a `last_tool_label` for the live view.
- Per-stage wall time can be inferred declaratively from which output keys are populated in stream chunks — no node instrumentation ("Market 12.4s | Sentiment 8.1s | News pending").
- Multi-worker readiness UX: gate the board's "ready" indicator on the *coordinator* being ready; individual stragglers fail visibly per-slot with stable pinned identity colors — never let one slow member block everything.
- Kanban variant: statuses `triage|todo|ready|running|blocked|done|archived` with a ~60s dispatcher tick that reclaims stale claims (4h), reclaims crashed workers, promotes ready tasks, atomically claims, and spawns assigned profiles. Idempotency keys dedupe automation-created tasks.

### Description-driven routing + auto-decomposition
Executor/profile descriptions are first-class routing metadata: an auxiliary LLM reads installed profiles + their descriptions and emits a JSON task graph; unknown assignees fall back to a default assignee — the decomposer "NEVER lands a child task with `assignee=None`." Auto-generated descriptions are flagged (`description_auto: true`) so review UX highlights unvetted LLM-authored fields. A one-click "Specify" pass (aux model rewrites a single rough task into a spec) is the light end when fan-out isn't warranted.

### Fail-closed capability routing + pin-don't-pool recovery
Any interrupted task returning to a pool carries a `{sourceAgentId, role, capabilities}` snapshot; one eligibility gate covers all pool consumers; eligible = same agent, OR exact role match AND capability superset; **missing role data on either side = ineligible** (never fail-open to "any idle worker"). Crash recovery pins the resume to the original agent (IDs stable across restarts); if unreclaimed after a grace window, a reaper escalates an explicit reroute decision to the lead — the work never returns to a role-blind pool. `MAX_RESUME_GENERATIONS` bounds a flapping task (fail instead of endless escalation). Each recovery behavior gets an env/config kill-switch documented as a reversible production change.

### Sibling awareness + context keys
Every ingress that creates work stamps a uniform grouping key (`task:<source>:<ids>` — chat thread, tracker issue, `task:schedule:{id}`, `task:workflow:{runId}`); children inherit it; indexed `(contextKey, status)`. ONE wrapper called at every ingress before task creation looks up in-progress siblings sharing the key, prepends a rendered sibling block to the new task's prompt, and auto-wires `parentTaskId` when a same-agent sibling exists (session resume); caller-set parentage wins. An additive debounce buffer keyed on contextKey coalesces rapid follow-ups (thread messages, comments) into one task — never applied to schedule/workflow ingress.

### Bounded parent-chain handoff context
Follow-up/child tasks get a preamble rebuilt from the task chain: the immediate parent contributes inline detail (task, output, artifacts, failure reason); **older ancestors are pointers only**; the whole block hard-capped (~2000 tokens default). Combine with a five-field structured summary as the inline content schema (`task_overview / current_state / important_discoveries / next_steps / context_to_preserve`) — see [memory-architectures](memory-architectures.md). Cascade rule: a failed/cancelled/superseded parent **cascade-fails dependents with a descriptive reason** instead of leaving them blocked forever.

### Roster-as-data catalogs
Three CI-guarded artifacts make a 200+ persona library operable:
- **divisions catalog**: `{label, icon, color}` per division; CI fails if the catalog disagrees with directories on disk or script allowlists.
- **runbook rosters**: `{slug, title, mode, duration, roster[]}` where roster groups carry a staged `activation` field (`"always"`, `"week 3+"`, `"as needed"`) so team deploys are staged, not all-at-once; agents referenced by **slug = filename stem** ("rename-proof and testable"); CI verifies every slug resolves to a real agent file.
- **assignment matrix**: task category → primary worker / backup / QA agent, as catalog data both the router and planner read.

Agent file anatomy is enforced by lint: frontmatter (`name, description, color` required; optional `services: [{name, url, tier}]` declaring paid dependencies) + body sections (Identity / Core Mission / Critical Rules / Deliverables / Workflow / Success Metrics). A header classifier mechanically splits persona (identity/rules/voice) from procedure (process) — keep them separate. An **entity-neutralized 8-word shingle-overlap originality gate** (proper nouns regex-neutralized first so find-replace re-skins still score as duplicates; warn ≥20%, fail ≥40%, calibrated: worst legitimate pair ~1.5%) blocks near-duplicate personas in CI.

### Team-sizing modes as a planning axis
Full (all agents, 12-24w) / Sprint (15-25 agents, 2-6w, skip the discovery phase — "market already validated") / Micro (5-10 agents, 1-5d, copy-paste one-liner playbooks per scenario). Severity-scaled variants for incidents: P0 → 6-agent team with incident commander + comms; P1 → 4; P2 → 2; P3 → one prioritizer (backlog it), each with response time + named decision authority. The planner should size the agent set and phase count to task scale — a third axis besides template-match and rigor.

### Distributed/clustered state mechanics
When orchestration spans processes: a primary owns versioned replicated state (entries partitioned by area+id) and a job runner; workers serve reads from local replicas. Every response advertises a state version; clients echo it; a lagging worker blocks until deltas arrive (delta window ~1000 versions, else full snapshot reset). Named locks (`acquireLock(area,id) → token`) guard jobs; the job runner takes a per-job-ID lock before each run so runs never overlap; jobs must mutate through tracked wrappers so replicas never drift. Sessions/PTYs survive orchestrator restarts via **deterministic session naming** derived from (project, task, session-id) — a restarted orchestrator recomputes the name and reattaches to still-running work instead of orphaning it. A version-negotiated wire protocol (`agreedMinor = min(client, server)` gates features; major mismatch throws with an upgrade action; strictly additive-minor field rules) keeps long-lived daemon boundaries evolvable.

## Patterns & compositions

- **Org-chart pipeline**: sequential specialist analysts (each with its own tool loop, transcript cleared after its structured report lands) → adversarial debate macro → judge → proposer → risk-debate macro → final judge. Generalizes beyond finance to vendor selection, architecture decisions, hiring. Debate rounds map directly onto a rigor knob.
- **Phase-gated delivery pipeline**: 7 phases (Discover→Strategize→Scaffold→Build→Harden→Launch→Operate), each with a named gate-keeper agent and a criteria table (criterion / threshold / evidence-required); strictest gate has a *sole-authority* judge whose **default verdict is "NEEDS WORK"**, with failure normalized in doctrine ("first implementations typically require 2-3 revision cycles; B/B+ is normal and healthy"); some gates require dual sign-off.
- **Drain loop**: parent goal → chain of small *stacked* PRs (each branched off the previous PR's branch) → a merge loop reviews/merges bottom-up and **halts on first request-changes or red CI** so a broken base can't poison the stack; iteration-capped. The shape that neither plain foreach nor plain loop captures for big code tasks.
- **Lead/worker inbox triage hub**: a central server ingests from many channels (chat/trackers/email/API), a lead agent decomposes and delegates to Docker-isolated workers, harness-agnostic via provider adapters; heartbeat triage keeps the fleet honest.
- **Board-as-coordination-surface**: no inter-agent messaging at all — isolated worktree agents + a spatial/monitoring board + human approval queue. The simplest topology that works, and the default when tasks are independent.
- **Debate/panel → judge → structured verdict**: any decision node can be widened into this composition; the judge's decision, not the transcript, is what persists (see [knowledge-pipelines](knowledge-pipelines.md)).
- **Escalation ladder**: retry-with-constrained-feedback (×3) → typed escalation artifact with closed decision menu → human/supervisor gate. Compose with circuit breakers and respawn guards from the mechanisms above.

## Anti-patterns & failure modes

- **Free-prose handoffs.** Context loss at agent boundaries is the #1 coordination failure; unstructured "here's what I found" handoffs compound it. Type the payload, name the consumer.
- **NL relay of state between agents.** The telephone effect corrupts shared state as chains lengthen — carry state as typed documents, use NL only inside bounded scopes.
- **Unbounded delegation depth.** A shipped fork-invocation tool with no depth limit is a real observed gap; runaway recursive spawning is cheap to prevent (propagated depth counter, cap ~5) and expensive to discover in production.
- **Fail-open work reassignment.** Handing an interrupted task to "any idle worker" loses role/capability fit; missing metadata must mean ineligible, and recovery must pin to the original agent or escalate an explicit decision.
- **Lazy delegation.** Coordinator prompts like "based on your findings, fix it" without paths/line numbers/exact changes; the synthesis step is the coordinator's job.
- **Same-family self-review.** LLM judges from the same model family as the generator tend to rubber-stamp; cross-family judging plus scope-anchored instructions ("don't add luxury requirements not in the spec") is the fix.
- **Retries that re-explore failed paths.** Retrying without injecting prior-attempt outcomes wastes budget repeating known-dead approaches; attempts must be first-class records fed into the next attempt.
- **Silent worker termination.** A worker exiting without an explicit complete/block signal must be a protocol violation that auto-blocks — otherwise the board lies.
- **Blocked-forever dependents.** Failing to cascade-fail children of a dead parent leaves the board showing misleadingly actionable work — a named, fixed bug class.
- **Schema-first, semantics-never.** Shipping genealogy/checkpoint/retry *schema* (parent_run_id, checkpoint_id, retry_count) without engine behavior yields dead fields everywhere; acceptance criteria must exercise resume/parallelism/retry behaviorally.
- **Fresh worktrees without config carryover.** Isolation without `preservePatterns`-style env/config copy-in produces agents that can't run anything.
- **All-at-once team activation.** Deploying a full roster when a staged activation (core always, growth later, support as-needed) or a Micro mode would do burns tokens and review bandwidth — review bandwidth, not compute, caps parallelism.
- **When NOT to multi-agent**: enterprise adoption guidance converges on shipping single-agent apps first ("one agent and one workflow at a time"), deferring multi-agent coordination until governance/observability/identity foundations exist. A complexity classifier should gate the planner: route LOW-complexity work to a single agent, escalating to multi-agent decomposition only on HIGH — with the inversion that *low classifier confidence escalates* (uncertainty → the heavier path), while classifier exceptions fall back to single-agent. Duplicate execution from missing atomic task locking "is one of the easiest ways to make multi-agent systems look capable while actually being broken."

## Quantitative findings

- Debate termination thresholds: `count >= k * rounds` for k debaters; default `rounds = 1` — one round is the shipped default depth for adversarial quality gains.
- An org-chart multi-agent trading pipeline backtested at cumulative return 26.62% on one instrument vs −5.23% buy-and-hold, Sharpe 5.6–8.2, max drawdown ≤2.11%, beating best baselines by ≥6.1% CR — with cheap models on all workers and expensive models on exactly 2 judge nodes.
- Pipeline meta-metrics targets from a production orchestration doctrine: first-pass QA rate ≥70%, average retries/task <1.5, phase-completion-on-first-attempt 95%, gate pass rate ≥80% — each with a named measuring agent; compute these per-template from run-ledger events.
- Retry/escalation constants that recur: 3 attempts before escalation; circuit-breaker on 3 consecutive failures; stuck detection = byte-identical evaluation output across 4 cycles; duration anomaly at 5× rolling average; stale worker claim reclaimed at 4h; dispatcher tick ~60s; heartbeat sweep ~90s.
- Doctrine defaults with stated reasons: pull-based task claiming ~30s; hard task timeout ~30 min; max sub-delegation depth ~5; retry once then change strategy; git worktree per parallel coding task.
- Persona-originality gate calibration: entity-neutralized 8-word shingle overlap, warn ≥20% / fail ≥40%, against a corpus whose worst legitimate pair scored ~1.5% (median 0%).
- Handoff-failure marketing claims ("73% of multi-agent projects fail at handoff boundaries", "40-60% timeline compression") are **unsourced** — treat the direction as corroborated by convergent design, not the numbers.
- Coordinator notification payloads carry usage (total_tokens, tool_uses, duration_ms) per worker task — per-child cost accounting is table stakes.
- Context caps that recur: parent-chain preamble ~2000 tokens; worker log-tail reads 12,000 bytes; escalation prior-summary 600 chars.
- Agent-to-agent synchronous invocation shipped with a 600s timeout, serialized.

## Open questions

- **Graphs vs tool-driven coordination**: the corpus contains both a high-star framework deleting its graph layer and an equally-circulated doctrine rejecting swarms for explicit graphs. No head-to-head measurement exists; the practical hedge (graph engine + first-class agentic escape hatch) is conviction, not evidence.
- **Optimal debate depth**: everyone defaults `rounds=1`; no source measures marginal quality per extra round vs cost. The knob exists; its response curve doesn't.
- **Peer-sessions vs nested-coroutines for workers**: peer sessions distribute and survive leader death but complicate ownership/teardown; no source quantifies the reliability difference.
- **Persona breadth vs quality**: 230+ persona catalogs exist, but nothing measures whether >10 distinct roles per project improves outcomes over a small assignment matrix with good judges.
- **Cross-family judging**: asserted from experience ("same-family review rubber-stamps") in multiple places, never benchmarked.
- **Agent performance history by task type** (per-(role × executor × task-type) outcome aggregates) was explicitly user-requested in one product and unshipped everywhere — the measurement layer that would answer most of the above.
