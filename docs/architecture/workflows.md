# Workflows — the Deterministic Orchestration Engine

A **workflow** is a declarative graph the engine executes: a spec tree of typed
nodes, driven by one conductor per run, with every outcome journaled. Where a
loop is an agent iterating toward a goal it judges for itself, a workflow is a
shape the author decided in advance — so the engine can schedule it, resume it,
edit it mid-flight, and rewind part of it without asking a model anything.

The engine lives in `PersonalClaw/src/personalclaw/workflows/`.

## The rule everything follows

**A run has exactly one writer**. The `RunController` tick loop under
its own lock is it. Nothing else — not a dispatcher, not the watchdog, not an
HTTP handler — writes a run's terminal status. Handlers *request*; the loop
decides and writes.

That single rule is what makes the hard features tractable:

- **mid-flight mutation** is only safe if there is a well-defined moment when
  nothing is being scheduled. Here that moment is "between scheduling steps,
  holding the lock", which is why every mutation is *queued* and applied at the
  controller's drain point rather than written directly;
- **crash recovery** is only correct if terminal writes are serialized, or a
  resumed run and a still-dying task race to disagree about the outcome.

The tick loop is deliberately boring:

```
while not terminal:
    drain cancel intent
    compute frontier (pure)
    launch admitted work
    await *something* finishing
    apply results, persist state
```

## Module layout

| Module | Job |
|---|---|
| `models.py` | the spec algebra — node kinds, states, run/def records |
| `tick.py` | `frontier()` — a PURE function from (spec, states) to what may run |
| `admission.py` | the ordered `AdmissionPolicy` list `frontier()` composes tightest-wins, plus the ready projection (`rank_key` comparator + `ready`/`next_ready`) the task pool used to keep privately |
| `container_env.py` | container workspace backends — the workspace-manifest model, docker/nerdctl/Apple-container CLI drivers, `detect_backend()` |
| `controller.py` | the conductor: one per run, the only writer of run state — the tick loop, launch, the settle (`_apply`) and the terminal write (`_finish`). Its other responsibilities are the modules below, each a set of functions over the controller reached only through it (its tick loop and `resume`), so they are that one writer split by what it decides, never a second writer |
| `run_admission.py` | PP-12 admission for each tick's ready set: the lease, bake-floor and metric-gate policies that need a clock and the disk, which the pure `frontier()` cannot apply |
| `incident_hold.py` | incident mode's hold on a run: the stage in flight withdrawn as a pause withdraws it, nothing started while the switch is on, the status left `running` with the sentence its views show (`held`), and the run carrying on by itself once the switch is off |
| `stage_settlement.py` | settling `stage` nodes, whose work runs in a spawned subagent the controller polls rather than awaits: the one out-of-band predicate, the settle, re-queueing after a restart, stopping on cancel and pause. A stage whose answer ignored its declared `schema` settles `failed` (`protocol`), naming what was asked for and what came (`engine.apply_declared_schema`, the same gate every node kind meets at the dispatch seam; a judge is held to its contract instead of every key); one whose time limit ended a wait for the owner's answer settles `timeout` with the typed reason the run's ending reads (`approval_timeout`); one whose subagent's model can't use tools (`subagent_tier.without_tools_ending`) settles `user`, with a fix about its model rather than its tools. The owner's Allow of an attempt's start is kept on its instance (`approved_request`, `approved_at`), so the attempt a restart re-queues starts on it when it asks the same thing (`engine.stage_request_key`) within the step's time limit; a settle or a rewind drops it |
| `step_dispatch.py` | running one node's dispatcher under its knobs: the retry correction and carried context on a copy of the node, the write-scope snapshot, `timeout_total` as a real kill, `success_when` |
| `node_bindings.py` | the `BindingContext` a node's `{{…}}` resolve against, built per dispatch from durable run state: outputs, artifacts, `last`, `previous`, siblings, the Session Brief, the secret resolver, the run's own document path |
| `input_secrets.py` | the `{{secret:…}}` references a run is handed in its inputs: which inputs its record says carry one, the resolver a step that starts a run keeps them with, the refusal of text from elsewhere that names a secret the step hands on, and the start-up pass that writes the reference over a stored secret's value in a record written before |
| `iteration_context.py` | the handoff / carryover / decisions lifecycle across a loop's iterations: captured from an iteration's own output, journaled, rehydrated on resume, rendered into a fresh iteration's prompt; and the steering queued for the next iteration, taken at the boundary (`consume_steering`) |
| `loop_iteration.py` | a loop's iteration boundary: the counter, the `until_dry` streak, the breaker fed and asked, steering, the long-run seen-set, and the continue/stop decision |
| `declines.py` | a Deny of a call inside a `stage`: kept on the step (`NodeInstance.declined`, from `SubagentInfo.declined_calls`), the rest of its loop cycle not run, the cycle journaled `declined` and the loop waiting for its owner (a paused run, its sentence, one Inbox item) instead of running the next cycle into the same ask; her Resume carries her steering into that cycle. What the run page lists under "Declined by you" |
| `loop_convergence.py` | what a tripped loop does next: one decision through `loop.tick.evaluate` on the node's `SupervisorPolicy`, the ladder position persisted on the run row, a replan as a real mutation, or a hand-off to a human |
| `gate_answers.py` | a step waiting on a human — a gate, or an action that parked: its durable continuation, the typed confirmation, the escalation's outcome question, the `revise` verb, judge/human divergence, what answering a parked step does, a decline and ending the run at an approval it did not get, closing an ended run's waits, and withdrawing an ask nobody answered |
| `ending_sentence.py` | the sentence a run ends with when a step's failure is the reason: how a step is named (a fan-out item by its item), a failure's cause as one clause, the steps after a step in each sequence that holds it, and the ending of a run that went on past a failed step (`for_failures`). Also WHY a step or a loop handed the run to a person, decided once: the closed cause vocabulary (`CAUSES`: budget, judge, approval_timeout, approval, refusal, step), each stop's sentence and its remedy (`loop_stop`, `step_stop`), read in the order of what is most true — a failed cycle's first failed step, then the judge's own ruling on the cycle the loop ended on, then the budget |
| `mid_flight.py` | the mid-flight mutation queue — held beside the run so a restart keeps it — and applying it at the tick's safe point: rewind and `run_from`, skip, set-input, fork, and the stale-input flags. An edit that would let a step do more than it does is refused before it is queued, unless the owner's yes covers it (`posture_refusal`, the rule every save of a definition follows) |
| `effect_boundary.py` | the effect ledger at execution time: ATTEMPTED before an effect-committing dispatch, its verdict after, and the committed-effect refusal or teardown before a redo |
| `task_projection.py` | projecting settled nodes into Tasks and running their done-criteria, scheduled off the tick and never failing a node whose work succeeded. A step's task is born with its step's state (`materialize.project_status`), names no person as its author, and is nobody's work to pick up: not the owner's (`Task.belongs_to`), and not ready work for anyone (`tasks.registry.ready_tasks`), since the run keeps its status. The Tasks page says it is from a workflow run and links to the run, and offers the run instead of an edit that would be refused. A step whose own output is what the owner sees of it (the morning triage's one step, which delivers its digest) declares `materialize_task: false` beside its `provider` |
| `run_start.py` | what a run binds before its first node: the declared workspace (the folder its steps work in when it is isolated, and the tree its project is bound to when it works in place), the project's context dir as the folder its steps work in otherwise (its memory is the project's, the folder the project binds: `memory_locality.work_folder`), a restricted origin's memory posture; and the context its tick loop runs in (`run_context`): a restricted run's work runs as its own from its record, under its mode and on its origin's model, however it is started or resumed; and, when a run resumes, the steps a controller now gone left running, back in the queue at their epoch (`requeue_lost_steps`) |
| `run_finish.py` | the guarded consequences of the terminal write: run-end learning capture, the project overview line, the `on_overlap: queue` drain, the report to the trigger or the chat that started the run |
| `engine.py` | one dispatcher per node kind; the only place real work happens |
| `engine_support.py` | the preparation every dispatcher in `engine.py` shares, lifted out so that file stays inside its size band (#3253): config resolution that SKIPS the keys holding a condition (`conditions` parses those; interpolating `{{a}} && {{b}}` would report a broken binding for a well-formed expression), the three-field journalled-prompt envelope that never persists a BLOCKED call's body, the `model_tier` → use-case → concrete `Provider:model_id` chain read from the live active selection (so a `cross_model` judge is validated against the model it will actually run on, and an unbound axis fails closed as an empty family), and the `judge_samples` count clamped at `MAX_JUDGE_SAMPLES` because each sample is a full reasoning-tier completion. Holds no dispatch logic of its own |
| `bindings.py` | the `{{…}}` expression language and its closed pipe set |
| `conditions.py` | the ONE boolean-condition dialect: gate `expr`, loop `until`, `success_when` |
| `execution_hints.py` | the `runtime_hints.execution` half — today, WIP=1 (`single_active_feature`) |
| `journal.py` | the resume cache and the Run Ledger (one append-only file, read two ways) |
| `replay.py` | `workflow replay <run_id>` — re-drives the PURE `frontier()` against a run's OWN recorded responses (keyed by `output_ref`) and its recorded clock (the `clock_read` envelope), and diffs the resulting trajectory against the one the run took, reporting the first divergent node. Divergence is a first-class outcome, not a failure |
| `store.py` | persistence — runs, specs, state, outputs, and the sticky intents a request leaves in a run's folder for its controller (pause, cancel, steering) |
| `versions.py` | the monotonic template version store: append-only per-version snapshots + a pinned pointer, re-pin/rollback, the typed-op diff, and the L0–L3 maturity computation |
| `mutations.py` | the typed edit grammar and its structural rules |
| `checkpoints.py` | fork, revert, prune |
| `human_input.py` | typed asks and durable resume tokens |
| `gate_policy.py` | risk-scoped auto-approval |
| `attention.py` | a step waiting on a human → a durable inbox row + one notification; a run's ending that needs one (`announce_run_end`) → the same |
| `context.py` | handoffs, carryover buckets, decision records |
| `compaction.py` | the two-layer prompt-compaction ladder for LLM-backed nodes: proactive at ~80% of the bound model window, then aggressive re-compaction + one retry on a length rejection, degrading to drop-with-placeholder if a summarizer raises. Wraps `personalclaw.context_compaction` — it does not reimplement it |
| `macros.py` | template macros, expanded at definition time |
| `blocks.py` | shared prompt blocks, resolved at definition time |
| `coalescer.py` | per-observer event batching in front of the SSE write |
| `projection.py` | the schema-validated run snapshot |
| `resilience.py` | retries, circuit breaker, budgets |
| `step_usage.py` | what one attempt at a step used, as every row that ends it records it: `measured()` reads the node's `guardrails.calls` log once for `step_completed`, `step_failed` and `step_cancelled` (tokens and cost as the providers reported them, the model and provider, a floor beside `model_calls_open` when calls were cut off, and `model_substituted` when a fallback served in place of the model asked for) and says what the run row's token budget is charged. `NOTHING_SENT` for an attempt refused before it dispatched |
| `liveness.py` | the stall clock: what keeps a working node's clock running — a nested run's heartbeat (`wait_with_progress`) and the latest event the node's model calls received (`last_heard`) — and the per-node window past which a silent node is stopped (`enforce_stall_timeouts`) |
| `failure_taxonomy.py` | the ONE place that decides whether a failed step's retry can help (only `TRANSIENT`/`NETWORK` are retryable), which is also whether the run page offers Retry. Classified at the cause, typed errors first: `classify_exception()` reads an HTTP status, a transport error's type, the guard's `CircuitOpenError`/`ModelCallTimeout`/`BudgetExceededError` and the provider bridge's WHAT/WHY/FIX before any substring rule; `classify_action_result()` takes a failed action's own `failure_class`, `retry_after` and `agent_error.fix`, and never assumes a silent failure is retryable; `binding_failure()` files a binding by who can fix it; `with_breaker_window()` records the providers a retryable failure called and, while one's breaker is open, when a retry can run (`Failure.retry_at`). A permanent failure's remediation says what to change and where. Lifted out of `engine.py` because three modules consult it — the engine, the controller's terminal-failure path and the gateway's channel injection — and two of them reached it through a function-local import of a private name |
| `error_codes.py` | `WF_ERROR_CODES` — the registry for the `WF_UPPER_SNAKE` service-result vocabulary (#3499), and the place to look a code up. One derived one-line meaning per code, grouped by the module that raises it so the derivation can be re-checked. Every meaning is read off the raise site — the guard that fires plus the message it emits — never off the name: a plausible-sounding guess reads as authoritative, and an author would act on a contract the engine never implemented. A row is the *stable contract* a caller may branch on, while the per-instance message stays the concrete detail (which node, which key, which run) — which is why, unlike `http_errors.HTTP_ERROR_CODES`, this registry is not also a default message. Carries no severity, because `validator.py`'s `_add` takes one per call and the emitters decide it. Its rail runs BOTH directions — every raised code has a row, and every row is still raised, the half that stops a registry rotting into codes that no longer exist — and EXCLUDES this module from the scan, since its own keys are string literals in core and counting them would make the second direction true by construction |
| `preflight.py` | run-start checks — credentials, binaries, models, providers — and each `stage` step that would run on a model that can't use tools (`tool_less_steps`): a step works with tools, so its run is refused (`WF_RUN_PREFLIGHT_FAILED`, a `WF_PRE_MODEL_NO_TOOLS` finding naming the step and the model, and Orchestration in Settings → Models as the fix). Its model is the one its agent would run on, built with no call made (`provider_bridge.model_without_tools`); a step whose agent or model is a binding resolves only at dispatch, where its subagent refuses to start on such a model |
| `audit.py` | the maintenance ops: `workflow_audit` diagnoses, `workflow_repair` heals |
| `judge_contract.py` | the ONE closed verdict enum (`verify.Verdict` was merged into it and deleted), the judge's wire shape (`judge_instruction` renders it, `parse_judge_json` reads it), the rubric ratchet with tolerant score lookup, the engine-computed overall, and the forbidden-mode denylist. Enforced on the live path: the judge gate validates every answer here, and `engine.apply_judge_contract` validates a judge STAGE's output at the dispatch seam |
| `judge_pretier.py` | the free rule tier that runs BEFORE any judge model call, plus the deterministic `fallback_check` |
| `judge_actors.py` | the actor-transition invariant (a worker may never reach `done`; a `self_judge` gate's PASS is redirected to review), judge isolation, and the blinded role-filtered evidence a judge is allowed to read |
| `loop_middleware.py` | the breaker's next tier: call fingerprinting, failure-class routing, the Continue→Nudge→Escalate→Halt ladder, the interrupt queue |
| `supervisor_policy.py` | the ONE `SupervisorPolicy` a loop node declares (rubric, escalation ladder, failure mutations, dwell/metric gates, marginal-value band, judge model tier, reproduce-before-ship, write scope, budget, HITL posture), its tolerant parser and its authoring-time `WF_SUPERVISOR_*` validation. Reuses the scattered types rather than re-minting them. **Live, not inert:** `loop_convergence._supervisor_policy` (`workflows/loop_convergence.py:31`, called at `:215`) parses a loop node's `supervisor:` block and `tick_config` turns it into the `TickConfig` that `loop.tick.evaluate` (`loop/tick.py:306`) reads, so the thresholds a template declares here are the thresholds the engine applies. `HAS_ZERO_PRODUCTION_CALLERS` is `False` (`supervisor_policy.py:73`) and a rail asserts that marker against reality in both directions, so it cannot quietly disagree with the code |
| `judge_calibration.py` | the nodding-loop detector, divergence records, stuck detection, and the verdict ledger they read |
| `review_service.py` | the run-scoped binding for `personalclaw.review_triage`: the live `git diff` a run's findings are anchored against, the `review_finding` ledger read, the re-anchor-on-submit TOCTOU check, dispatch of the ACCEPTED subset through `service.steer_run`, and rejections written as `judge_divergence` calibration rows |
| `loop_aliases.py` | read-time aliases for legacy loop-kind references, and cockpit stream-key equivalence |
| `longrun.py` | long-run watcher mechanics: item identity, the persistent seen-set, bounded sibling views, buffer-seal, the adaptive-delay clamp, lineage caps |
| `intent.py` | the no-LLM intent classifier: the (complexity, uncertainty, stakes, time_pressure) tuple, irreversibility, and rigor routing |
| `matcher.py` | tiered template matching T1-T5: keyword index, metadata scoring, shape filter, cached embedding tie-break gated by `workflows.match_threshold`, LLM summarize-then-rematch |
| `preamble.py` | the grounding preamble (UP-R14): the deterministic entity-resolution first node with its identity guard and degraded fallback, and the topic extraction that feeds the grill's lookup channels |
| `brownfield.py` | the brownfield context pass (UP-R17): the depth-filtered tree + README head + project-metadata synthesis, tree-hashed and cached per project with a 7-day TTL, rendered as the prompt's `CODEBASE_CONTEXT` |
| `grounding.py` | the grounding bundle: node taxonomy, provider signatures (three discovery tiers), MCP servers, binding roots, model capability |
| `patterns.py` | the seven proven graph shapes, their slots, when each is WRONG, and the deterministic shape pick |
| `generation.py` | the generated planning prompt, the mechanical self-check, repair-not-regenerate, and the decline path |
| `contracts.py` | derived parameter schemas, per-stage done-means contracts and their lint, and blocking-vs-open decision typing |
| `revision.py` | typed merge-by-id patches, the NO_UPDATE sentinel, TTL'd draft sketches, and the announce-block review surface |
| `autonomy.py` | the risk-signal registry, autonomy floors and offers, HITL/AFK typing compiled to `require_hitl`, the confirmation matrix (an action step is asked about by the effect its provider declares, `ActionProvider.effect`, and a declared deletion is never auto-approved), the two interrupts, earned trust |
| `grill_protocol.py` | the structured `rigor: deep` protocol: recommendation-bearing questions, the facts-vs-decisions channel split, adaptive pacing, stress probes, the Step-0 schema, frozen prohibitions |
| `rigor.py` | the cheap end of the axis: `rigor: fast` + its auto-scheduled refinement gate, Specify's one-stage rewrite, the append-only acceptance ratchet, revise-spec-from-artifact |
| `template_pipeline.py` | chat-session mining, discover-then-freeze candidates on the scope ladder, the `suggest_template` nudge with its anti-nag rules, entity scrubbing |
| `template_store.py` | the state writer behind that pipeline (which stays pure): file-backed per-shape `NudgeState` so a cooldown or a permanent DECLINE outlives the process, and frozen `Candidate` templates so the same intent resolves to one graph across runs |
| `eval_specs.py` | per-template eval specs derived from the template artifact — fixtures, structural and parameterization checks, and the named-not-graded judge surface |
| `containers.py` | the Work board projection (state grouping, claim leases, per-section `/work` isolation), the substrate-checked boot sweep, and the project context block + wayfinder ledger contract |
| `leases.py` | the flock-backed claim files behind `containers.claim`/`release`: `single_flight`-guarded read-modify-write over a per-target lease file whose `expires_at` outlives the process, so a claim stays truthful across a gateway kill |
| `publish.py` | the `publish:` declaration, material-change version gating, typed lineage (flattened to scalar event metadata), evidence bundles, the terminal handoff report and the append-only results ledger |
| `publish_seam.py` | where `publish.py`'s decision is carried out, called from `engine.dispatch` beside the artifact gate: the artifact registry write, the media copies a published body references (read only from under the run's own cwd, sensitive paths refused), the run's `publishes.jsonl` the outbox lists, and the consumption outcome question the dormancy sweep grades. A malformed declaration fails the node; a registry failure is reported on the result instead |
| `filedrop.py` | the per-run file drop (spec-declared, approval-gated multipart ingestion into the run's `immutable` `dropped/` zone, fenced on read) and the outbox — the run's published-artifact listing projected from the publish journal rather than a second registry |
| `pinned.py` | the pinned-artifact set a user curates for the composable home (`entity_settings/pinned_artifacts.json`), owning its own entity file the way `channel_trust` does. Stores only slugs — name, kind and version are re-read from the artifact on every load, so a rename or a new version cannot leave a stale pin |
| `project_archive.py` | the archive I/O around `project_export`'s planner: an allowlist walk into `plan_export`, a manifest ZIP, and extraction into a per-call temp dir reaped in `finally`. Writes only plan-ACCEPTED entries so the archive and its manifest cannot disagree, reuses `project_export.safe_member` rather than adding a second path check, and offers optional AES-GCM through the `cryptography` dependency |
| `batch_compile.py` | batch `subagent_run` compiled to a `parallel[stage...]` run: the N≥2 threshold, capability classes, the single-writer lint, static depth rejection, typed leaf outputs compiled to `output_contract`, and the safety-filtered recall view. Each `tasks[]` item is read by the type its contract declares (`leaf_from_item`; the tool shows the same table as its schema, `leaf_item_schema`): one path sent as text is a one-item `writes`, and a value of another type, an unknown `capability` or a key the contract does not have refuses the compile. The boundary is prose for the worker plus `off_limits`, the paths it must not write; only those are compared with `writes` (`boundary_contradicts_writes`), never paths found in the prose. Each step is named by its item's `title` (the node's `label`, which every surface shows, and what its id is cut from), else by the start of its objective (`LeafTask.label`) — never by the task's text, which is written for the worker and often opens with a path. Called from `mcp_subagents._run_compiled_batch`, which hands the compiled spec to the gateway (`POST /api/workflows/batches`, `batch_start`) to persist as a def and start a run against, so the widget rebuilds from disk after a restart. Emits the top-level `workspace:` block the run-start applier reads (`provisioning.declares_workspace`), so the fan-out is provisioned into one isolated `scratch` substrate — RUN-scoped, because there is no per-node provisioning in the engine. A crash-surviving batch is therefore SUSPENDED with a Resume affordance rather than auto-adopted (§5.2), since `stamp_run` records a recoverable `worktree_path` for every isolated mode |
| `batch_start.py` | the start of a compiled batch (`POST /api/workflows/batches`, from the `subagent_run` tool, whose call asks nobody itself: what it starts asks, `tool_providers.base.WORK_ASKS_META_KEY`). A batch's start is decided once, for all its tasks, and nothing is saved or run before. A batch whose tasks only read starts as a subagent its chat started would: on the grant that starts that chat's subagents without asking (its Trust, YOLO, the hook setting; `SubagentManager._start_grant`, under the operator ceiling), or on the answer to one ask, asked through the start's own relay (`SubagentManager._ask_to_start`: the card in that chat, the Inbox, the phone, a channel; a Trust or YOLO switch answers it). A task that may change things carries a posture key only the owner's consent puts on a step (`automation_posture.POSTURE_SPECS`), and the tool cannot give that consent, so such a batch asks for her own Allow, through the approval registry itself (`owner_allow.ask`: no standing grant and no Trust or YOLO switch answers it). The ask names each task and what it may change (its tools, and the paths it says it writes). What allowed it is on the run's record (`CONSENT_KEY`), and each task's start reads it (`approval_grants.BATCH_ALLOWED`); a Deny ends it declined, an unanswered ask ends it unstarted, a turn's Stop or a loop's ending cancels a batch's ask like any other (`owner_allow.end_asks`), and a session that acts on its own (an Unattended loop's) or a gateway with nowhere to ask is refused where nothing else lets it start (`409 nobody_to_ask`). A batch an app's agent asks for (its scheduled job's, its agent run's, a conversation it started) is the app's work (`apps.app_work`): no grant of the owner's starts it (one that only reads starts on the app's install consent, `approval_grants.APP`, as one subagent its agent starts does; one that may change things asks for her own Allow), it starts only within the tier the app holds then (`403 agent_tier_exceeded` at `text` or with no tier, and at `read` for a task that may change things), its ask names the app and its scheduled job, and its run records whose work it is (`app_work.RUN_KEY`), so each task carries the app's name and is held to the tier when it starts (`engine.dispatch_stage`). A batch waiting for its answer is recorded under `workflows/batches/<name>.json` until it ends: a gateway that comes back asks it again (`resume`), or ends it unstarted once its approval window has passed, and the record only ever asks. Its card in the chat follows it from the ask to its run (`GET /api/workflows/batches/{name}`, `state_of`), and the Inbox names it as the batch it is (`asker`). The run carries the origin the batch was started with (`subagent-tool`, the chat's session key), which is how its ending reaches that chat (`ending_of_run`, `run_finish.report_to_its_chat`) and how its tasks' live events are shown in that chat's Activity panel (`task_of_chat`) |
| `owner_allow.py` | the owner's own Allow for what an agent cannot give itself, asked once: the start of a batch with a task that may change things (`batch_start`) and an agent's save of a workflow whose steps would do more (`definition_ask`). The ask goes to the approval registry itself (`request_approval(answered_alone=True)`), so no standing grant and no Trust or YOLO switch answers it; a session that acts on its own, or a gateway with nowhere to ask, is refused; and a turn's Stop or a loop's ending cancels what still waits (`end_asks`) |
| `agent_routes.py` | the workflow operations an agent's tools make with the gateway's internal credential, which `dashboard/server.py` opens to it: the owner's own routes an agent CLI's tool server makes its workflow tools' calls by, and those only an agent's tools call (a batch `subagent_run` compiled, an agent CLI's save and dry run, plan and bounded watch of a run). Kept apart from `handlers.py`, which imports the dashboard, so the server never imports the routes. One workflow's read names none of the routes beside it, so the credential opens that read alone |
| `restricted_calls.py` | what a call made for an Incognito or Temporary chat may change in your workflows, the one rule both doors ask before anything changes (the routes' `handlers._guard` and a native agent's workflow tools in `mcp_workflows`): it starts a run or a batch, which keeps the chat's mode, and controls only a run it started that keeps nothing; anything else is refused, saying why in the words of the one reader of a session's mode, and audited. The call's mode is the strictest of the session it names and the work it runs inside |
| `definition_ask.py` | an agent's save of a workflow (`POST /api/workflows/agent-saves`, from `workflow_author` when its save needs the owner's yes, and for every save and dry run (`save: false`) an agent CLI's tool server makes, which keeps no workflows): saved at once when no step does more, else asked once, naming the workflow, each step it is for and what that step would then do. Her Allow saves exactly what she was shown (a step the ask did not name is not covered, and her Inbox says so); a Deny, no answer or a stopped turn leaves nothing saved; a newer save of the same workflow replaces the ask; a session that acts on its own is refused (`409 save_nobody_to_ask`) |
| `roster.py` | the slug-keyed agent catalog PROJECTION over `config.json agents{}` plus the reserved system names (WORK-R16) — owns no state and reads the SAME `AppConfig.agents` dict `subagent._validate_agent` checks, so there is one answer to "which agents exist". Consumed in production by `batch_compile.agent_lint`, which slug-resolves each leaf's declared agent and persists the resolved CONFIG KEY (never the slug — `_validate_agent` checks config keys, so a persisted slug would fail every multi-word agent name); also supplies the `unresolved_slugs` drift check the test gate runs over every bundled template |
| `workspace.py` | the `workspace` provisioning block (mode/preserve/setup/teardown/env), reserved-var rejection, the secret-filtered spawn env with presence-only flags, and tolerant `.folder.yaml` contracts |
| `ownership.py` | run-owned session keys (`workflow:<run>:<node>`), the SEL + prompt-use-case registrations, incognito/temporary inheritance read by the one reader of a session's mode (`memory_writes.session_mode`: the live chat first, then the registry and the transcript) together with the mode of the work that starts the run, a chat whose mode nothing can say inherited as `unreadable` (a Temporary chat's rules, saying why), recorded on the run with the model its origin runs on (and carried into a subworkflow and a fork), and the engine-level learning-node skip |
| `needs_input.py` | the NeedsInputItem card (block kind, blocker, attempted, evidence, recommendation, one decision), owner binding, once-only staleness re-notify, and the refs round trip |
| `worktrees.py` | code-kind run worktrees on the proven `loop/worktree.py` machinery: preserve-in, marker-guarded setup, resume safety, teardown-before-deletion, the per-run branch, the machinery-free review diff, and the two reintegration verbs |
| `provisioning.py` | the PERFORMER for `workspace.py`'s plan and `worktrees.py`'s decisions: create → preserve → setup at run start (`controller._prepare`), setup/teardown steps as no-shell ceiling-wrapped subprocesses that carry the run's marker (`run_processes`: a service its setup starts runs until its teardown, which ends what still carries it), the PID-liveness lock OUTSIDE the workspace, the run-record stamp (`worktree_path`, `preserved_workspace_path`), teardown-before-deletion for both deletion paths, the cockpit's diff + reintegration offer, and `run_workdir`: the one folder the spawn working-directory allowlist admits for a run's own steps, derived from the run's record (and, for a run in place, its project's) |
| `introspection.py` | the nine-question checklist, RunStats as a pure journal projection, verification debt, said-no gate statistics with a sample-gated fake-check badge, per-template p50/p95 cards, and the Proof section |
| `run_cockpit.py` | the run cockpit's reads, each a projection over what the run already wrote: `introspect` (the nine questions, its template card read across the template's recent runs), `ledger_rails` (findings and verdicts), `run_deliverable`, `template_trajectory` and `touched_items`. `introspection.py` holds the arithmetic and this module holds the reads; each returns the same service-result dict as `service.py` |
| `project_export.py` | project export/import: the allowlisted portable set, per-entity sha256 in a versioned manifest, secrets as presence flags only, import refusals (unsafe member, digest/size mismatch, unknown schema) and `imported-N` collision slots |
| `materialize.py` | tasks as a projection of run state: the exhaustive state→status table, `blocked_kind` derivation, fingerprint dedup, fan-out caps with a parent counter, the managed/produced/standalone split and the engine-owned-field write rejection |
| `verified_done.py` | engine-owned criterion execution over the `loop/gates` tristate, pass-state gating, the three-actor transition matrix, the weighted acceptance schema, cascade-fail over the binding graph, the stuck-work sweep and idempotent timing |
| `confirmation.py` | the one durable ConfirmationRequest record: construction-time redacted previews, per-type expiry policy, the four-verb resolution vocabulary, `require_hitl`, per-stage mute, tool profiles and the DagView approve/deny card |
| `surfacing.py` | SOP surfacing discipline: the `surface_mode` ladder (off/passive/suggest), trigger-phrase lint + collision check, the shared `!`-negative veto plus planning/paste/named-workflow vetoes, one-source-two-wrappers rendering with a verbatim digest fence, per-def graduation, SOP migration and the reachability doctor |
| `surfacing_channels.py` | The two non-semantic surfacing channels plus the contracts that gate a suggestion: cadence/recency (freshness gradient, overdue-first sort, once-daily escalation throttle, last-completed derived from real run history), workspace fingerprint packs (weighted globs, bounded scan, propose-don't-enable with per-project dismissal), layered scope resolution with visible shadowing and per-stage overlays, the three-state requirements preflight, schema-driven parameter pre-fill, and the reachability doctor + trigger-accuracy fixtures |
| `pool.py` | Task-pool concurrency: TTL'd compare-and-swap lease decisions with takeover/renew/release rules, the flocked claim write path and an expiry sweep, evented unblock with `dependency_failed` cascade and burst coalescing, delegated write-time acyclicity, hand-off edges with an allowlisted context carry, and blueprint sessions (numbered guided conversations, replace-not-merge hydration) plus the passive/blueprint/run router. Its private `frontier`/`next` projection was RETIRED by `PP-13` onto `admission.py`; the lease decisions stayed, because they are what the `Lease` policy calls |
| `settings.py` | The config knobs the runtime actually reads: one resolver per live-editable `WorkflowsConfig` field (`surface_mode_default`, `max_materialized_per_foreach`, `confirmation_ttl_secs`, `lease_ttl_secs`), each with the module constant as a fail-safe fallback, clamped to the bounds the records enforce, and deliberately uncached so a PATCH takes effect without a restart |
| `scope.py` | filesystem write-scope enforcement by post-hoc diff |
| `watchdog.py` | the supervisor: adoption, reaping, per-run publishing. Its first-poll boot sweep runs through `concurrency.boot_sweep` — the ONE boot-adoption path shared with `loop/watchdog.py`; only the §5.2 substrate rule (`_sweep_one`) is run-specific. A run it suspends is reattached by the next poll's adoption; what must wait for its owner carries the sticky pause intent (a pause asked for, a run a restore held). A controller whose loop died under it (`RunController.dead`) is no controller to anyone who asks, and the next poll takes its run over as a restart would; a caller on another thread reaches the runs through `run_threadsafe`, on the loop they are driven on |
| `overlap.py` | `on_overlap`: the exhaustive policy decision with a raising tail, the queued-vs-hand-made-draft marker on `run.extra`, the coalesce-to-one cap, and the single-flight drain called from the terminal writer and the watchdog poll |
| `web_preview.py` | a run's localhost dev-server preview: fixed-argv `lsof`/`ss`/`ps` host-fact probes, port→pid→cwd attribution scoped to the run's own workspace, and the honest empty reason when no scanner exists |
| `loop_run_map.py` | the `Loop`→`WorkflowRun` field map: every `Loop` field either maps to a run field, maps to a template input, or is listed as homeless — the checked starting point for retiring the second work-unit noun |
| `loop_view.py` | the READ half of that map: a run started as a loop (`WorkflowRun.loop_kind`) projected back into the loop wire shape, so `GET /api/loops` lists every loop whatever backs it. The projected row carries `run_id` (the discriminator every surface routes by); a run's status, stop reason and error come from one mapping (`loop_ending`): a budget stop is `complete` with the budget it reached ("Ended early"), any other escalation `failed` + `worker_failed`, and the error is the run's ending; the cycle count is the root loop's distinct finished iterations and the budget is the one the engine stops at; and `RUN_ACTION_SOURCE_STATES` is the run's narrower action table (no resume from `failed`), railed equal to the frontend mirror |
| `deliverable.py` | a run's DOCUMENT deliverable + working log, the run-side answer to `GET /api/loops/{id}/report`: the kind→filename resolution DERIVED by walking `loop_aliases` forward and asking each kind's own `deliverable_name` (never a constant here) unless the run was started to produce a named document (`run_document`, kept in the run's `extra` by a loop started with `document`) or its own spec states one (a top-level `"document"`: a filename, or `""` for none — `goal-pursuit-monitor` keeps none, since each wake is a fresh step with no directory of its own to keep a log in), the workspace-then-run-dir root order that mirrors `loop/watchdog._deliverable_file` (a run whose steps keep the document in its own documents folder, `{{run.document}}`, is read there first), the one path such a run hands its steps, its judge's check and its panel, and the copy a run that continues another starts from, a confined + redacted read with a blob ceiling that bounds the redactor's quadratic unbroken-token cost, and a five-member NAMED absence vocabulary — unknown template, kind declares none, not written, no root, unreadable — because a blank panel cannot tell a finished verifiable goal from a slow worker. Carries no money field by design (issue #2566) |

## Containers do not execute

A `sequence` has no work of its own — it is a scheduling policy over its
children. So `frontier()` recurses into containers and only ever returns leaf
work, and a container's state is **derived** from its children rather than
stored. That is why `container_outcome()` exists and why nothing writes a
container's state directly: after a rewind resets children, the container's
verdict is recomputed instead of patched.

One consequence worth knowing: an *untouched* container derives as `RUNNING`,
not `PENDING` — "has unfinished children" is running by that definition. Code
that asks "has this subtree started?" must look for recorded state, not derive.

**What a step can read of a container** is fixed by its kind, in one definition the
engine records by and validation checks against (`models.NO_OUTPUT_KINDS`). A `loop`
that ends done records its last cycle's output, layered the way `{{last.output}}` and
its own `condition` read a cycle (`loop_convergence.finish_loop`), stored with its
instance so a resumed run reads it back, and a step after the loop reads it as
`{{nodes.<loop>.output}}`. A loop handed to a person (its budget spent without its
exit met, a judge that would not decide, a cycle that failed, an exit condition it
could not read) records nothing, and a step that reads it is skipped. A `branch`
records its routing, `{"case": label}`. A `sequence`, `parallel` or `foreach`
records nothing of its own, so validation refuses a read of one when the spec is saved
(`WF_UNSATISFIABLE_OUTPUT_REF`): read the step inside it whose output you need. A loop
that should carry a running account across its cycles has each cycle return it, built
on `{{last.output.…}}`, since its output is its last cycle's. Until loops recorded
theirs, a step after a loop failed "unresolved reference" on every run while
validation accepted the read, which is where optimize-harness, design-project and
goal-pursuit-open-ended stopped.

## Scheduling: three rules that carry the weight

**Active-edge join gating.** A join waits only on predecessors whose
edge is on an actually-taken path. A `branch` picking `cases[bug]` leaves
`cases[feat]` unreachable, and a join waiting on "all predecessors" would
deadlock forever; a join firing on "any completed predecessor" fires early on a
fan-out whose other legs are still waiting. Both directions are bugs. The rule
that satisfies both: **a `needs` edge is satisfied by any TERMINAL predecessor,
and unreachable paths are MADE terminal by marking them skipped.** Declines are
recorded explicitly, never inferred from "the source routed elsewhere" —
inferring would starve a sibling whose `needs` merely names the branch.

A **dataflow** edge — a reader that binds the producer's output — is stricter.
Only a succeeded node's output enters the binding namespace, so a producer that
ended without one (skipped, failed, cancelled: any terminal state outside
`SUCCESS_STATES`) makes that reader unreachable, and it is skipped too. Running
it instead is a guaranteed binding failure whose escalation would replace the
producer's on `run.attention`, blaming a step that did nothing wrong for the
one that failed.

The wait-entry subtlety: a `wait`/`gate` enters `WAITING` rather than
completing, and `WAITING` is not terminal, so a join behind it keeps waiting
instead of firing on the fast leg alone.

**Typed lanes.** Ready work is admitted per-lane by node kind, so a
`foreach` over minute-long IO actions saturates the `io` lane while `llm` stages
keep flowing. Excess stays `ready` — the next tick admits it.

**Per-container concurrency.** A `foreach`'s `max_concurrency` caps items in
flight independently of the lane caps. They answer different questions: a lane
cap protects the *engine*, this protects the *run's shape* (this fan-out takes
two at a time because each item holds a lock).

`frontier()` reports `blocked` when nothing can run and nothing is running.
That state is a deadlock, and naming it is the whole reason it is computed
rather than assumed.

## Node kinds

Thirteen, and no more. Every orchestration pattern is a *composition*:

| Kind | Notes |
|---|---|
| `sequence` `parallel` `foreach` `loop` | containers; DAG shapes come from per-child `needs` |
| `stage` | one subagent execution — tools, session, can spawn |
| `infer` | exactly ONE bounded model call — no tools, no session |
| `visualize` | ONE bounded reasoning-axis call → a genui widget spec — no tools (agency-free) |
| `branch` | conditional dispatch on a binding |
| `transform` | zero-token pure data reshaping — `expr`, or `skeleton` (see below) |
| `action` | zero-token action-provider dispatch |
| `wait` `gate` | deadline / human-input |
| `subworkflow` | a real nested CHILD run, depth ≤ 3 |

`stage` and `infer` are separate kinds for a real reason: a template that needs
a classification should not pay for a subagent, and the lane accounting depends
on distinguishing them. A judge panel of five `infer` nodes is five bounded
calls; the same panel as `stage` nodes is five concurrent sessions.

**A `transform` renders either an inline `expr` or a stored `skeleton`.**
`config.skeleton: "<artifact-slug>"` reads that artifact's body and interpolates the
same `{{…}}` bindings into it — the layout/data split a live dashboard tile needs
(AMBIENT-SURFACES §2.1). The skeleton is authored ONCE by a model and every refresh
after that is pure substitution, so the spec stays readable and a steady-state
refresh costs zero tokens. Either field satisfies the validator; neither is
`WF_MISSING_EXPR`.

**Action arguments go under `config.with`.** A flat argument beside `provider`
reaches the provider as an empty config — it then reports its own required field
missing for a value visibly present in the spec, and every downstream binding
fails. Validation refuses the shape at authoring time.

**A `bash` step runs PersonalClaw as `personalclaw`.** Its command runs in `/bin/sh`
on the gateway's `PATH`, and in an isolated install nothing there is this install: a
`uv tool` install keeps PersonalClaw in an environment of its own, so the `python3`
on `PATH` has no PersonalClaw, and the desktop app is a frozen bundle with no
interpreter at all. So the bash action hands the command's shell a function named
`personalclaw` that runs this install's own CLI, the command every child of the
gateway that runs the CLI uses (`bash_provider.own_cli_function`, through
`self_update.cli_argv`), and a step that says `personalclaw <command>` runs the same
install as the gateway that started it, alone or in a pipeline, a subshell or a
substitution. A program the command starts (`env`, `nohup`, `xargs`, another
`sh -c`) looks the name up on `PATH` like any other, and `command personalclaw` asks
`PATH` on purpose. The bundled `optimize-harness` template's bash steps run
`personalclaw optimize-harness <step>`, a CLI command its help does not list. Its
`experience` step hands the template's proposer the search's own ledger (every
candidate so far with its raw diff, and the winner with its ops), since the template
refiner's tools read evidence and file proposals and cannot open a file. Its scoring
step is the one that calls models, so it is an action that runs in the gateway
(`optimize-score`, `evals.optimize.score_step`) rather than a command: a model call a
`bash` step's command makes is booked to neither the step nor the run, and the search
holds its `budget_usd` to what the run's own calls cost
(`workflows.ownership.run_spend`), checked before every cycle and every scoring
(`evals.optimize.budget_stop`). Its `file` step files the winner from the ledger, with
the runs it was scored on as the proposal's evidence, and asks no model.
Nothing the package ships runs PersonalClaw through an interpreter found on `PATH`
(`tests/test_bare_interpreter_census.py`).

**An `action` step asks the action denylist before its provider runs, as a trigger's
fire does** (`guardrails.denylist.enforce_action`, from `engine.dispatch_action`). A run
is unattended work whoever started it: each step is dispatched with nobody approving
it, and the run lives in the gateway it would stop. So a step is judged as unattended
(`unattended_dispatch_key("workflow:<run id>")`), and one that would stop, restart,
update or reinstall the gateway running it (`personalclaw stop`, `personalclaw update`,
`personalclaw service uninstall`, its service manager or a kill aimed at it,
`guardrails/self_destruct.py`) is refused, with what the denylist refuses at every
other unattended dispatch: a credential path, a path the operator's
`security.autonomy_denylist` names or the ceiling's `paths` leave out, and a command the
shell denylist refuses. The check is in the one dispatch every action step takes (a
sequence's, a fan-out's or a loop's body, a sub-run's steps), not in the provider lookup
the gateway hands the engine (`EngineServices.get_provider`), which a caller can replace.
A refused step never reaches its provider and fails `permission` with the words a
trigger's fire records for the same rule, its code and its sentence
(`DenyDecision.refusal`: "blocked by the guardrails denylist: self_destruct:stop —
unattended action refused: it would stop the PersonalClaw gateway that is executing it
…"); the run stops there and its ending names it (a control's refusal stops the run
whatever the step declares), the audit log has its `guardrails.denylist` row and the
gateway log a warning. Each quotes the step as it was written: a path or a command a
`{{secret:NAME}}` filled in is judged on the value and named by the reference, never the
value. You can still stop or update PersonalClaw yourself, from your own
shell or Settings → Updates. `tests/test_action_provider_chokepoints.py` fails an
execution site that reaches a provider, under any name, without asking. What the step then
reaches is held to the egress tier of that same identity (`net.policy.egress_held_to`), so
an operator ceiling that gives no run any network refuses a step's fetch or webhook as it
refuses a trigger's ([limitations §18](../security/limitations.md#18-a-runs-egress-tier-holds-where-its-requests-ask-the-guard)).
A command the run runs (a setup or teardown step, a verify check, an effect's teardown) is held
to that tier where it is launched (`sandbox.egress_bound_argv`): under a tier that is not `all`
it runs in the OS sandbox with no network, or is refused where the sandbox cannot take it away.

**The run's other commands are held to the same rules** (`guardrails.denylist.check_command`):
a verify gate's command (`loop.gates.run_verify_command`, which a loop's check runs through
too), a setup or teardown step (`provisioning.run_step`) and an effect's teardown
(`effects.run_teardown`) each ask it before they run anything, as unattended work, and
before the shell denylist, so a command both catch (`personalclaw update`) is refused for
its effect. A refused gate fails with the sentence and ends the run saying it, a refused
setup step is a failed one, and a refused teardown leaves the redo it guards blocked; each
is a `command_refused` row in the audit log naming the control (`action_denylist`) and the
rule. `tests/test_every_command_path_asks_the_denylist.py` fails a command runner nobody
answers that does not ask.

**Every `WF_*` code has a registry row: `workflows/error_codes.py`
(`WF_ERROR_CODES`).** That is where to look one up. `WF_*` is the third of this repo's
three code vocabularies — `lowercase_snake` is the HTTP wire envelope
(`http_errors.HTTP_ERROR_CODES`), `ERR_UPPER_SNAKE` is the carrier into an LLM session
(`errors.ERROR_CODES`), and `WF_UPPER_SNAKE` is the transport-independent workflows
service result, which `workflows/handlers.py`'s `_STATUS_MAP` translates into the first.
Until #3499 it was the only one of the three with no registry and no rail: 159 codes were
raised across ten core modules and exactly one — `WF_MISSING_EXPR`, above — was documented
anywhere. Each row's meaning is the *stable contract*; the per-instance message stays the
concrete detail (which node, which key, which run). The rail
(`tests/test_wf_error_codes_registry.py`) runs **both** directions — every raised code has
a row, and every row is still raised — because the second is what stops the registry
rotting into a list of codes that no longer exist.

## Bindings

`{{inputs.x}}`, `{{nodes.<id>.output}}`, `{{item}}`, `{{secret:KEY}}`, with a
**closed** pipe set. No eval, no filesystem, no arbitrary expressions — a spec
is user- and model-authored text, and an expression language would make it a
code-execution surface. An unknown pipe is *refused*, never ignored: a silently
dropped sanitization pipe leaves a spec that looks sanitized and is not.

`{{run.document}}` is the absolute path of the document the run keeps in its own
documents folder (`deliverable.document_path`), for a template whose steps carry
state in a file from step to step. A template that binds it keeps its document
THERE: its steps are handed that one path and their file tools reach that folder
and no other run's (`provisioning.step_documents`), the judge's declared
`artifact_exists` check reads it, the Document panel serves it, and a file of
the same name in a shared folder is never copied over it. A run reads another
run's document only when its `continue_from` input names that run: it starts
from a copy (`run_start.carry_over_document`, recorded as `continued_from` on
the run), a fork says it continues its parent there on its own, and a start that
names a run it cannot continue is refused (`WF_RUN_CONTINUATION_INVALID`; a
subworkflow child is refused the same way, as its node's failure). The carry asks
the store for that run again rather than trusting the start check, and reads only
an earlier run of the same workflow. `continue_from` is read when the run starts,
so an edit that would change it on a run that exists is refused
(`WF_MUT_START_ONLY_INPUT`). A run that keeps no document has no `run` root, and a
read of it says so.

A pipe's arguments are **literals** — a quoted string, a number, `true`/`false` or
`null` — so an argument can never name a variable. That makes `| default([])` not an
expression at all (`[]` is not a literal); "an empty list when the value is null" is
`| filter`, whose null case is `[]`. Authoring validation parses every pipe call with
the function resolution itself uses (`bindings.parse_pipe`), so a spec that validates
is one whose pipes evaluate: `WF_UNKNOWN_PIPE` for a name outside the set, `WF_BAD_PIPE`
for anything else resolution would refuse — not `name(...)` syntax, a non-literal
argument, more arguments than the pipe takes. Each refusal carries its own fix
(`BindingError.remediation`), which is also what a failed step shows for a spec saved
before the rule existed, since run start does not re-validate. The validator used to check only the
name, and `rich-ingest` shipped `| default([])` on seven reads: its judge gate failed its
prompt, a fan-out reading it could never resolve its items and deadlocked the run, and
no run of the template could persist what its lenses extracted.

A binding that fails is filed by who can fix it (`failure_taxonomy.binding_failure`,
keyed on `BindingError.caller_supplied`, which only the raise site can set). `user` is
for what the caller supplied: a run input the run was started without, or a
`{{secret:KEY}}` that is not set. Everything else a binding reads belongs to the
definition (a `{{nodes.…}}` id, a field of another step's output, a loop root, a pipe)
and is `internal`, since whoever pressed Run did not write it. Neither is retryable. A
secret the credential store does not hold is refused, never substituted: the store
answers "" for a missing key, and a request carrying it fails at its receiver with
nothing naming the key. The store is the one Settings → Secrets writes, read through one
resolver (`llm/credentials.py` `resolve_secret`, reading `config/credentials.py`). **A run
that belongs to a project reads that project's secret first** (Settings → Secrets keeps it in
the same store under the project's own key) **and the global secret of the same name
second; a run with no project reads only the global one** (`secrets_vault.reading_order`).
Nothing reads a project's secret by its stored key: an owned `PCSECRET_…` key, which belongs
to a provider's or an app's own setting, and a project's `PCPROJ_…` key are both refused by
name with the reason, so a step cannot read another record's key or another project's
secret. A project's secret is never copied into the gateway's environment, which every child
the gateway starts inherits.

A `{{secret:KEY}}` is filled in only where the step runs its config itself. A step whose
config is text for a model (a `stage`, `infer` or `visualize` step, and an `action` whose
provider's action is a model turn, `ActionProvider.hands_config_to_a_model`) keeps the
reference as the name (`node_bindings._secrets_for`): filled in there, the value would be
in the model's context. A stage's agent uses the name in a command, and PersonalClaw's `bash`
tool fills it in as the command runs, with the run's project: the stage's lineage carries it
(`engine.leaf_spawn_env`, from the run's record). The agent an Invoke Agent or Run Prompt step
starts works for the run's project the same way: the engine sets `ActionContext.project_id`
from the run's record, never from the step's config, and the provider starts its agent with it
(`SubagentManager.spawn(project_id=…)`), so that agent's session is the project's.

**A run is handed a secret's reference, never its value** (`workflows/input_secrets.py`). An
automation's Run workflow action (a fire or Run now), a step that starts a run with that action
(`ActionProvider.hands_config_to_a_run`) and a `subworkflow` step hand the run they start its
inputs, and a `{{secret:KEY}}` in them stays the reference: filled in there, the value would be in
the run's inputs, the opening row of its ledger, the run list and the prompt of a model step that
reads the input. The run's record names the inputs it was handed a reference in, and the secrets
each refers to (`secret_inputs` on the run, carried over by a fork); a step that reads one of those
inputs has those references filled as one written in the step itself is
(`bindings.resolve_expr`): the run's project's secret first, then the global one, recorded as a
`secret_read` row, and kept as the name where the step's text goes to a model. A run fills only
those. Text that reads as a reference in any other input — typed at Run, given by an agent's call or
a caller from outside, or produced by a step — stays text. A step that hands a run its inputs keeps
its references as references (`input_secrets.Handed`) and checks each against the secrets the run
it starts reads, failing before anything starts when one is missing; and when text it takes from
another step or an input refers to a secret it hands on, the run could not tell that text from the
reference its author wrote, so the step is refused (`input_secrets.handed_on`). A record written
before this rule that holds a stored secret's value is rewritten when the gateway starts, before any
run is driven (`input_secrets.redact_home`): the run's inputs, ledger, state and prompts, and the
automations' run history, hold the reference instead, and the run database is rebuilt so no old
copy of a row is left in its file. A snapshot taken before keeps what it was taken with.

Each secret a step uses goes on the run's record as a `secret_read` ledger row: the secret's
name and where it came from (this project's secrets, the global ones, or the gateway's
environment) — for a stage, an Invoke Agent or a Run Prompt step, which hands the reference
on, where PersonalClaw's `bash` tool reads it in that agent — and a one-line `rationale` the
run's node inspector shows. Never the value. A reference handed on to any other model (an
`infer` step, a best-of-n sample, a second opinion) gets no row: nothing of the run's reads it
there.

Two asymmetries that are easy to get backwards:

- **a null output is a value; an unresolvable reference is an error**. A
  node that legitimately produced nothing hands `None` downstream and `default`
  absorbs it. A binding naming something that does not exist raises — because
  the silent alternative is a prompt with a hole in it, which produces confident
  nonsense that reads like a real answer;
- **declared input defaults are applied at run start**, not lazily at each
  binding, so the run record shows the values the run actually used.

A value that is exactly one reference keeps that value's type; any other value,
one holding a second reference included, interpolates. Which it is, is decided by the
scan that splits a value into its references (`bindings._whole_ref`), the one
validation reads a spec with. A pattern of its own used to read any value that began
with `{{` and ended with `}}` as a single reference, so `{{inputs.a}} changed:
{{nodes.b.output}}` failed as a path named `a}} changed: {{nodes` while validation
saw two good references; market-monitor's alert title was such a value.

## The journal: two jobs, one file

**Resume cache.** Keyed by `(instance_path, epoch, inputs_hash,
spec_region_hash)` — all four earn their place. `epoch` because a rewind bumps
it, so a replayed region from a superseded epoch can never be mistaken for a hit
on the current one. `inputs_hash` because an upstream change makes a cached
output stale even when the node itself was untouched. `spec_region_hash` because
editing a prompt and resuming would otherwise serve the pre-edit answer.

A consequence: **a rewind produces no cache hit.** It bumps the epoch, and the
epoch is part of the key, so the region correctly *misses*. The cache serves a
resume; invalidation is what serves a rewind.

**Run Ledger.** The event subset a downstream refiner reads. These are emission
*requirements*: an evaluator that wants to know which model a step used, what it
cost, and why it failed is starved if the engine only journals free text. The
acceptance bar is that prompt → tool calls → output is reconstructable from
ledger events alone.

Every row that ends an attempt carries what its model calls used, read once
from the guard's call log (`step_usage.py`): `step_completed`, `step_failed`
(a retried attempt, a final one, a stall kill) and `step_cancelled` all write
`tokens`, `cost_usd`, `model`, `provider` and `model_calls_open`. The numbers
are what the providers reported: `null` where none reported anything, and a
floor when calls were cut off before they finished, with `model_calls_open`
counting them. `run_totals`, Introspect and the run row's token charge fold all
three the same way, so a failed attempt's spend reaches the run's budget.

A step keeps the model it asked for. A binding that cannot be built (a model
app whose update failed) fails the step, and the failure names the model and
the fix: nothing builds whichever provider happens to be registered in its
place (`llm_helpers.one_shot_completion` keeps that last resort for a use case
with nothing bound at all), and a model named directly is refused by name. A
fallback the user configured, a later entry of the use case's chain, still
serves, and every row that ends the attempt then carries `model_substituted`:
the distinct "ran on X instead of Y: why" sentences of its calls, written only
when there is one. The run view shows them on the step's row
(`NodeInstance.model_substituted`) and Introspect beside Models
(`RunStats.substitutions`). An agent's pin follows the same rule
(`provider_bridge.named_model_problem`); see [chat-sessions.md](chat-sessions.md).

Everything written passes through `redact()` first. A journal is read by the
flywheel, shipped in bug reports, and rendered in a UI — a credential reaching
it is leaked to all three. Outputs past ~64KB, or matching a binary magic
prefix, spill to a file and leave a typed `result_omitted` stub.

The run row and a step's stored output are kept as written, so every read that
shows them masks them with the same redactor: the run list, the run page's status
and its live snapshot (`handlers.shown_status`), every live event
(`RunController._publish`) and a step's output, as the inspect drawer does. The
Loops page masks the same run (`loop_view`), and a run's text reads the same on
both.

**A run stays on the machine that ran it.** The watchdog adopts every active
run it finds and resumes it from its journal (`watchdog._poll_once`), so the run
ledger (`workflows/runs.db`) and each run's folder (`workflows/runs/`) are
`machine_local` and not `merged_in` (`durability/inventory.py`): no sync carries
them, and a merge restore or an archive import leaves the archive's out.
Another machine's running run used to arrive and run a second time here, on
this machine's files. A backup carries them, and a replace restore brings them
back with the whole home, holding each run that was running, waiting on a gate
or queued to start (`store.hold_restored`): it is paused, with a pause intent
the watchdog honours across a restart and the reason as its error, because the
snapshot is a moment the run has gone on from since. Resume takes it on from
there.

**A run is driven on the supervisor's loop, whoever starts it.** A run's
controller drives it from a task on the event loop it was launched on, so a run
is launched where its supervisor runs: the routes are on that loop already, and
the agent's workflow tools, which the native runtime runs in a worker thread,
hand each call that reaches a run to it and wait for the answer
(`mcp_workflows._on_engine`, `WorkflowWatchdog.run_threadsafe`). A start names
the chat whose turn made it, as the routes name their caller, so the run keeps
that chat's memory posture and its turn's Stop ends it. A controller whose loop
died under it can move its run no more: the supervisor answers no live
controller for it, and its next poll takes the run over as a restart would, the
steps the dead controller left running back in the queue.

**An agent CLI's workflow tools are the gateway's.** An agent on an agent CLI
reaches the workflow tools through the CLI's tool server (`personalclaw
mcp-core`), a process with no engine and no definitions in it, so there every
workflow tool call is made by the gateway (`mcp_workflows_gateway`): through the
owner's own route for it, with that process's internal credential and the
session of the chat it serves, and answered as the native runtime's call is
answered. The routes take such a call as an agent's
(`approval_answer.of_request`): a run it starts is its chat's (origin `chat`,
named by `X-Session-Key`), a resume lifts a pause and answers no gate, and an
edit carries no yes of the owner's. Three calls have no route of the owner's and
are served to it alone: its dry run of a save (`POST /api/workflows/agent-saves`
with `save: false`), its plan (`POST /api/workflows/agent-plans`) and its
bounded watch of a run (`GET /api/workflows/runs/{run_id}/observe`). A blocking
start waits there for 25 s at most, then answers where the run stands. A
Temporary or Incognito chat's call starts a run or a batch, which keeps the
chat's mode and stays on its model as one its agent starts in the gateway does,
and it controls a run it started that keeps nothing as it does. Any other change
it asks (a definition saved or deleted, a run that you or another chat started)
is refused, saying why, and so is every call of a chat whose mode cannot be
read. A subagent's or a step's call is judged as its chat's, by the strictest
mode up the chain it works for (`memory_reads.reach_of`), whatever its own key
is marked. That is one rule (`restricted_calls`), asked by the routes
(`handlers._guard`) and by the workflow tools a native agent calls in the
gateway, which reach the engine with no route between: each tool that changes
something asks it under the operation the route for the same call names
(`mcp_workflows._CHANGES`), so a Temporary chat's native agent changes only the
runs it started too. A preview of an edit changes nothing, so it is answered for
any run the chat can read, on either door.

A run started for an app's work (its batch, or a run its agent starts with
`workflow_start`) is the app's work, and its record says so from its create
(`apps.app_work.RUN_KEY`, written by `service.start_run`; a sub-run and a fork
keep it, `ownership.inherited_extra`), so it stays the app's after a restart,
when the agent that started it is gone. Each of its steps starts only within
the agent tier the app holds then, carries the app's name, and approves none of
its calls whatever the run's overlay or the step says (`engine.dispatch_stage`):
a step the tier does not cover fails saying why and starts nothing. An action
step that would start an agent of its own (`invoke-agent`, `run-prompt`,
`run-workflow`) is refused in such a run (`engine.dispatch_action`): its agents
start only as the run's own steps.

## Mid-flight mutation

A typed op grammar (`update_node`, `insert`, `delete`, `move`, `skip`, `rewind`,
`run_from`, `fork`, …), and four things guard it:

1. **A live controller is required.** Editing a run nobody drives would write
   state with no one to apply it. A finished run is refused
   (`WF_RUN_ALREADY_TERMINAL`): it is one attempt and is never re-entered, so a
   retry is a fork.
2. **Batches are queued, applied at the drain point.** `edit_run` returns
   `queued: true`; nothing has changed yet. A run parked on a question has no
   tick loop (it ended `needs_input`, and only an answer wakes it), so queueing a
   batch wakes the loop and the batch applies at the next tick — a rewind
   confirmed at a gate re-runs its closure while the gate waits, and the run parks
   on the same question again. A rewind whose closure includes the waiting gate
   withdraws that question: its confirmation resolves `withdrawn`, its token is
   dropped and its Inbox row closes, and the gate asks afresh. A PAUSED run is not
   woken; its edit applies when it resumes. The queue is also written beside the
   run (`pending_mutations.json`, `mid_flight.queue_mutation`) and read back by the
   controller that next drives it, so an edit queued on a paused run survives a
   restart before the resume. It applies at most once: the file goes before the
   batches apply. Edits queued before the drain all apply, in the order they
   were made: each is checked at submit against the spec the queue will leave
   (`mid_flight.projected_spec`), so an edit may build on one still queued, and
   prepared again at the drain against the spec the edit before it left. A batch
   that no longer fits the spec the run resumes with is journaled
   `mutation_rejected`, never dropped silently.
3. **The frozen-region invariant.** A COMPLETED node cannot be edited — its
   output is already downstream, and changing the spec that produced it would
   make the run's own history a lie. The user's order is *rewind, then edit*.
4. **TOCTOU re-verify + `expect_version`.** Two edits computed against the same
   version cannot both apply; the second was reasoned about against state that
   has moved. The version lives on `run.spec_version`.

A rewind whose cascade would re-run completed work reports
`needs_confirmation` and applies nothing until confirmed. The cascade is
computed over the **binding-dependency graph**, not the container tree, so
editing a node invalidates what actually reads it. Resetting a node whose
attempt has settled gives that attempt's no-double-execution claim back
(`mid_flight._apply_reentry`): a stage that settled DONE keeps its claim, and
its confirmed re-run is the same instance, so it used to meet its own lease, read
DEGRADED ("not executing twice") and run nothing for the claim's 900s TTL. A
node whose subagent is still RUNNING keeps its claim, so the re-dispatch is
refused as the second execution it would be.

A boolean a definition or an edit carries is read as the word it spells
(`safety_flags.yes_or_no`): an agent writes one as text as often as not, and `bool("false")`
is True. A value that spells neither reads as the safe value of what it guards. A step's
`redo_effects` (the committed-effect boundary holds), `allow_failure` (a failed check holds
what follows it), `self_judge` (the judge runs in a session of its own), `judge_contract` and
an edit op's `force` and `redo_effects` are a yes only on a yes; `require_hitl` and
`persists_memory` are a no only on a no or when left out (the step asks someone; a run that may
write no memory skips the step); and a verify criterion is `hard` unless it says no.

A finished run is one attempt and cannot be re-entered, so a retry is a
**fork**, not a rewind: the child draft inherits only the steps that SUCCEEDED
(their state, outputs and step records), and every other step starts `PENDING`
at the same epoch, so starting the child re-runs exactly what did not finish.
For a template that keeps its document in the run's own folder, the child's
inputs say it continues its parent (`continue_from`), so the step it re-runs
starts from a copy of the document the finished steps left.
Effect records carry over whole, because the committed-effect boundary reads
them and a fork cannot un-fire anything, and they are also what keeps a retried
effect recognisable: `effects.effect_key` reuses the key of the newest same-epoch
record, so the child re-sends a failed effect under its parent attempt's key, and an
action receives it as `payload.idempotency_key` to dedupe on.

The run page's Retry is that fork followed by a start, offered only when EVERY step
that gave up has a retryable failure: a Retry re-runs all of them, so one refused
key fails the new run the same way. A run's status carries all of its escalations
(`escalations`, read from its `step_escalated` ledger rows, oldest first, leaving
out a step that has since succeeded); `run.attention` is one slot and each
escalation overwrote the last. While the circuit breaker of a provider the failed
step called is open, a retry is refused without a call, so the failure carries
`retry_at` and the Retry waits with a countdown. `status` asks the breakers again on
every read (`Failure.providers` names them), and the page reads the run again before
it forks, because a breaker can open after the step failed: every call to that
provider counts toward it.

A stopped run (`failed` or `escalated`) waits on nobody, so its escalation panel is
headed "This run stopped" and offers the ways forward that work on a finished run:
Retry where every failure is retryable, and **Change the workflow**, the definition's
editor (`#/workflows/defs/<name>/edit`), for a failure a new run repeats until the step
changes. The record's five `options` are still not offered: nothing accepts one back.
The page names a step the way the run's ending does, by its label: each node row of
`GET /api/workflows/runs/{id}` carries the `label` its node declares (absent on a step
without one), and the rows, the graph, the dialogs, the inspector and the escalation
panel read it, falling back to the node's id.

A stopped run also says so where you will see it (`attention.announce_run_end`, from
`_finish`): one Inbox row, "Workflow run failed" or "Workflow run stopped before it
finished" over the run's name and its ending sentence, that opens the run
(`refs.workflow` → `#/workflows/runs/<id>`), and its one notification through the rule for
"a run needs you" (`loop/needs_input`, the pair a gate rides), deduped per run and ending.
A run started as a loop announces its end the way a loops-table loop does instead; a run a
trigger started is reported on that trigger's route (`run_finish.report_to_its_trigger`);
a batch a chat's agent started tells that chat instead, in one turn, how each of its tasks
ended: its result, or why it has none (`run_finish.report_to_its_chat`, delivered the way a
subagent's completion is), unless someone stopped it or its loop has ended
(`batch_start.tells_its_chat`), as a stopped subagent tells nobody; a sub-run's ending is its
parent's step. A run that completed, was
cancelled or was declined raises nothing.

## Waiting on a person

Two kinds of step wait on a person, and both are asked the same way. A `gate`
asks (`approval` or `event`). An `action` stops for one: its provider returns
`outcome: "needs_input"` (browse at a sign-in page, a spent step or model
budget), or its output carries a question under `needs_input`
(`gate_policy.clarification_from_output`). Either way the step goes `WAITING`,
and once nothing else can run, `gate_answers.ensure_continuation` mints the
resume token, the confirmation's pending half and one Inbox row (deduped per
run, path and epoch), and the run parks `needs_input`. A parked action also keeps
its output on the step and states why it stopped. When its provider composed a
needs-input card (browse's sign-in handoff: the question and what it tried),
that card's wording is what the run page and the Inbox show.

Each ask is its own record. Its question is the one its step kept when it began
to wait (`NodeInstance.ask`), not the run's one `attention` slot, which two steps
waiting at once would share. Its confirmation id is minted with it from `(run,
step, epoch)` and which ask of that step it is (`confirmation.request_id`), and is
carried on its continuation, so the answer cites the id the question was asked
with. The step is its instance path, which names a loop's cycle
(`…body@2.children[0]`): each cycle's ask of a gate inside a loop is its own
question, where a node id — which every cycle repeats — gave them all one id. A
step can ask twice in one epoch (approved, run again, stopped again; a
rewind that does not force), and the second ask is a new question that no earlier
answer can answer. An ask that closes with nobody answering it — the run ended, a
rewind withdrew it, its deadline passed — resolves `withdrawn` with the reason and
`answered: false`, so its pending half is never left open and no escalation bet
is graded by it (`gate_answers.withdraw_asks`). A revise (`revise{step_ref,
comment}`: change that step, then ask me again) is a third outcome: its ask
resolves `revised` with `answered: true`, and once the revised step has run the
gate asks again under a new id. Scoring counts it apart from a yes and a no: the
gate's said-no table lists it as `revised` (`introspection.gate_stats`), and the
escalation bet on the revised ask is graded as the answer `revised`, with no
number on its yes/no scale.

Answering a gate records the answer as its output. Answering a parked action does
not: approving runs the step again (`Ask.rerun`), with the answer on the one
dispatch it starts (`ActionContext.answer`), because what the person did was lift
what stopped it. That answer lives in memory: a restart between the answer and the
dispatch loses it, and the step then parks on the same check and asks again. The
gate policy's auto-approve and a remembered "always allow" apply to gates only — no
policy can sign in for a user. Both are grants (`approval_grants`): the operator
ceiling bounds them, so under `{"approval": {"value": "ask"}}` neither stands and the
gate asks, and a gate the policy did approve is written to the audit log
(`workflow_gate.approved_without_asking`) as well as the run's journal.

**Only you answer.** A gate's question is put to you, and `controller.resume`, the one entry point
every answer goes through, refuses anyone else before the token is touched (`approval_answer`,
[security.md](security.md#who-answers-an-approval-approval_answerpy)). That covers an app's token,
an agent's tool (the gateway's internal secret) and the run itself. An agent's `workflow_resume`
lifts a pause and answers nothing: an answer it sends is refused with the sentence saying who
answers, and audited (`approval.answer_refused`). The one exception is an `event` gate, which
parks a run until something happens and asks nobody's permission. The trigger it waits for answers
it, as a monitor's self-scheduled wake does, and you still can. A trigger answers no other gate, so
a trigger an agent armed against its own run (`resume_run_id: "self"`) cannot approve that run's
approval gate. Its fire is refused, and the gate waits for you.

**An `event` gate is woken, not answered.** Its ask is `event` (`human_input.AskKind.EVENT`, from
the gate's own kind, whatever `ask_kind` says), and its answer is the wake's payload: whatever the
trigger was armed with (`set_onetime_task` arms it with its `message`), or `true` when you press
**Wake it now** on the run page, the Inbox row or Mission Control's card. Any payload moves the
run on, and the gate's output records it as `answer`. It is never read as a verdict or a verb: a
`false` does not decline the run, a `{"revise": …}` does not amend a step, an "always allow" is
not remembered (`Ask.rememberable`), and the gate policy does not auto-approve it in an unattended
run, since approving it would skip the wait it is for.

A trigger's action that stops the same way asks through the trigger instead
([tasks-triggers.md](tasks-triggers.md#an-action-that-stops-for-you-asks-you)):
its run is recorded `waiting`, one Inbox row carries the same card, and Approve
runs the action again with the answer. Where a person signs in is the browser
the step drives (its `cdp_url` target): core opens no window for them, so no
card, row or banner points them at one.

**A gate is a gate: what follows one runs only once it passed.** `needs` means
after, not after-it-passed, so the frontier alone would run the next step behind a
gate that said no — and `on_error: null_continue`, the default, walked past one.
The controller reads `gate_answers.stopping_gate` on every step, after deadlines
resolve and before the frontier, and `end_at_gate` ends the run there:

* **Deny** — on a gate, or on a parked step — makes the step `declined`: not a
  failure, so no `on_error` can continue past it, and not a pass. The run ends
  `declined`, and its error names the gate and who said no ("“approve” was declined
  by Keyur, so nothing after it ran"): the owner's name, and the channel for a remote
  reply. Deny on the approval a `stage`'s subagent asks before it starts is the same
  decision (`stage_settlement`, from `SubagentInfo.declined`): the step is `declined`
  "denied by you", with no failure class and no remedy, and the run ends `declined`
  ("“Audit the notes” was denied by you."), which raises no failure in the Inbox. A
  start nobody approved — no answer in time, an approval that ended first — never ran a
  turn, so it fails as `permission` with "run this step again, and answer its approval
  when it asks", not a pointer at a transcript that does not exist.
* **No answer** — the gate's deadline passed — keeps the gate `failed`, because
  nobody chose it and an unattended run must surface it. The run ends
  `failed`, saying how long it waited. A gate waits as long as every other approval:
  the owner's approval window, `agent.approval_timeout_minutes`, read when it parks
  (`human_input.gate_timeout_secs`), in a background run or a blocking one. A run
  started unattended (the explicit `attended: false` grant,
  `supervisor_policy.unattended_grant`) has nobody to answer, so its gate gives up
  after `UNATTENDED_GATE_TIMEOUT_SECS` (45 s) instead of parking on a question
  nobody will see. The author's `timeout_secs` wins over both, and `0` waits
  indefinitely.
* **A check that did not pass** — a `judge`, `expression`, `verify_command`,
  `verify_script` or `ladder` gate that fails or escalates — ends what follows it the
  same way. The run ends `failed` with the check's reason ("“quality-check” failed:
  the draft cites no source, so nothing after it ran"), or `escalated` for a judge
  that escalated. A check continues past a failure only when the gate itself
  declares it: `on_error: null_continue` runs what follows and the run still ends
  `failed`; `allow_failure: true` records the failure as degraded and the run can
  complete. An `expression` gate asks nobody, so the words its author gives it are
  its `message`: what the step's failure and the run's ending say when the condition
  is false ("“Frozen region untouched” failed: the candidate wrote into the frozen
  region …, so nothing after it ran"); without one, the condition itself is the
  cause. Of the bundled templates, `knowledge-lint` declares `allow_failure` on its
  per-item judge (it records each item's verdict, and nothing after it writes),
  `audit-sweep`'s `fix_enabled`, a mode switch that was written as a gate, is a
  branch, and `produce-and-audit`'s quality gate, whose words asked a person to
  accept an artifact the audit had not passed, is a real approval that a branch asks
  only then. No bundled gate carries words the engine never shows
  (`test_workflows_no_gate_text_goes_unshown`), and validation warns an author whose gate
  does (`WF_GATE_TEXT_UNSHOWN`, from `validator.GATE_TEXT_READ`): any words on a verifier
  or a ladder, which read none, a `prompt` on an expression gate, a `message` on a judge,
  or a `message` beside the `prompt` of an approval or event gate, which the ask shows
  only when there is no `prompt`. The spec still saves and runs.

Every way, each step after the gate, in each sequence that holds it, is marked
skipped with the reason ("not run: “approve” was declined", "not run: “verify”
failed"), and anything still in flight elsewhere is stopped. A decline is the one terminal state that ends every
container holding it (`tick.container_outcome`) and makes even a plain `needs`
onto it unreachable. The engine has no construct for an author to declare a path
taken on a decline — a denied gate's answer never entered the binding namespace,
and `on_error` is a failure policy — so a decline always stops the run.

**A Deny inside a step is the step's to say, not a gate.** A `stage`'s subagent asks before a
call it may not make on its own, and the owner's Deny of one is kept as hers
(`SubagentInfo.declined_calls`; an ask nobody answered, or one with nowhere to put to her, is a
refusal as before). The step settles as its subagent ended, its row saying "You declined
write_file (notes/plan.md).", and the run page lists it under "Declined by you". Outside a loop
the run goes on: nothing runs the step again to ask her the same thing. Inside a loop's cycle the
next cycle would, with the judge's critique of work she declined, so the cycle ends at her Deny
(`declines.end_cycle`): the rest of it is not run, the cycle is journaled `declined` ("Cycle 2
ended at “work”: you declined write_file (notes/plan.md)."), and the loop waits for her — the run
pauses with that sentence and one Inbox item until she steers, resumes or cancels it, and her
Resume runs the next cycle with what she steered it with. A loop with no cycle left after it
ends instead, at its budget or, counted, done.

**A failure the run went on past says so.** Outside a gate, `on_error:
null_continue` — the default — lets the steps after a failed step run, and the run
ends `failed` (or `escalated`, for a judge or a loop that would not decide) once
they have. Its error says the run continued past a failed step when a step after it
ran, then names each failed step and its cause, a fan-out item by its item
(`ending_sentence.for_failures`): "The run continued past “summarize”, which failed:
model output was not valid JSON." The continuation comes first because the line is
read cut short — the Workflows list shows it on one line, and a cause can be a
model's whole reply. A failed last step, or a failed `parallel` leg whose siblings
ran beside it, is named without that claim. The failures are followed down
from the run's own outcome through the scheduler's derivation, so a failure the run
tolerated (`allow_failure`, a `skip` fan-out, a leg of an `any` join) is never named
as its reason. That ending used to be empty — the completion path's terminal write
took no `error` — so the run page read "Failed" over nothing.

**A step's failure is said in its own words.** A command a step runs says why it
failed by printing one JSON object whose `error` is the reason, as the bundled steps'
refusals do (`evals.optimize.main`), and that reason is the step's failure, ahead of its
stderr; a command that says nothing is named by its exit status
(`failure_taxonomy.classify_action_result`). It used to read "action failed" with the
reason left in the step's output. The run's way forward follows the reason: what it names
is what to change, the step or what the run was started with (`ending_sentence.step_stop`),
where it used to say "change the step that gave up" under a refusal of the run's own budget.

**A step can declare that its failure ends the run.** `on_error: fail_run` says
nothing after the step may run once it failed, which is what a preflight that refuses
its inputs means. The run stops there the way it stops at a check gate that failed
(`stopping_gate`, through `tick.fails_the_run`): it ends `failed` with the step's
reason ("“Refuse or report, before the first model call” failed: optimize-harness
refuses to search without a positive `budget_usd` …, so nothing after it ran"), and
each step after it is marked skipped, saying why. The frontier already stopped
scheduling after such a step, and nothing ended the run, which ended "run
deadlocked". A step a control refused before it did anything stops the run the same
way, whatever the step declares (`models.REFUSED_TO_START`, stamped where the engine
refuses one: the action denylist, an app's limits on a run that is its work, the
nesting cap): what the step was for never happened, the steps after it were written
expecting it to have, and a failure policy does not walk past a control's refusal.

**A loop cycle that ends in skipped steps is read at its boundary.** A step whose
producer failed is skipped, and a skip is not a settle, so a cycle whose last open
steps were skipped finished with nothing to read its boundary: the loop waited on it
and the run ended "run deadlocked". The frontier's skips now read each such cycle's
boundary as if its first failed step had just settled (`loop_iteration.advance_skipped`),
so the loop goes on to its next cycle, its breaker counts the failure, and it ends by
its own rules. A loop that cannot read its own exit condition stops and is handed to a
person, naming the step that failed when one did, since DONE would hand the steps
after it its output as a result.

A run that ends closes whatever it was still asking, whichever way it ended:
`gate_answers.close_waits` cancels each waiting step and withdraws its ask, and
`_finish` closes the run's Inbox rows and cancels its approvals (#3620), saying what
ended the run when its cancel carried a reason ("the workflow run that asked for it was
cancelled because its chat turn was stopped"). It also ends what its steps started
(`started_work.end_run`): a batch one of its steps' subagents handed to `subagent_run`
from its own session (`subagent:<id>`) is cancelled, saying how the run ended ("Stopped
because the workflow run “…” that started it failed.") or the reason its own cancel
carried, and deleting a run does the same for one its ending left running. A cancel
always reaches that writer. The supervisor wakes a parked controller to apply a
cancel written without waking it (`on_overlap: cancel_then_start` does that to
the prior run), and after a restart it launches a controller to apply one rather
than writing the row itself.

## Timeouts: two knobs that mean different things

`timeout_total` bounds wall time. `timeout_stall` bounds *silence*, and it is
fed by progress — a long-but-progressing node survives, a wedged one does not.
If nothing feeds the stall clock the two collapse into one and a node that is
visibly working gets killed as wedged. Two things feed it (`liveness.py`): the
`on_progress` callback threaded through `dispatch`, which a nested run's
heartbeat calls while its child works, and the node's own model calls. The
guard stamps every event a provider sends on the call, so a best-of-n whose
samples are streaming is working, while a call that sends nothing for the whole
window is still a stall.

`0` means unbounded, for both. A cap the user did not ask for that silently
halts work is worse than no cap.

## Context lifecycle for long horizons

Compaction keeps the *what* and drops the *why*, so a compacted loop
re-litigates settled decisions and re-reads verified files. Three mechanisms:

- **handoffs** — `session: fresh` (the default for iterated bodies). An
  iteration writes verified state / changes / what is NOT verified / next
  action, and the next iteration starts from that. `session: continuous`
  injects nothing, because it already holds the previous iteration;
- **carryover buckets** — typed, bounded, deduped facts (files touched with line
  spans, claims verified, children spawned). Structure survives summarization;
  prose does not;
- **decision records** — `{choice, reason, rejected, constraints}`. The rejected
  alternatives are load-bearing: without them a resumed run re-proposes the
  option already dismissed.

All three are journaled, so rewind and resume *replay* them rather than
reconstructing a summary, and all three are bounded — an unbounded bucket is a
transcript with extra steps.

## Events

Per-run SSE on `DashboardState.workflow_sse()` (key `workflow:<run_id>`), plus
WebSocket refetch signals. Deliberately NOT routed through `notify()`: that is
the user-notification gate behind mute/severity/quiet-hours, and a quiet-hours
setting would silently eat a run's entire event stream.

Every event carries `event_id` (deterministic), `seq` (monotonic) and `epoch`
(the run's), stamped at ONE publish seam — a call site that forgot one would
emit an event the FE cannot dedup or supersede, and that is invisible until a
rewind duplicates a row. High-frequency node chatter is batched per observer
into a `workflow_batch` frame; members keep publish order and their own
envelopes, so batching is a transport optimization and nothing more.

The FE folds events through one pure function (`workflowFold.ts`) with four
guards: dedup by event id, epoch supersede-drop, node-keyed patches, and a
per-node `seq` floor.

## Genealogy and nesting

`subworkflow` runs a named workflow as a real **child run** — its own journal,
state map and terminal writer. That costs a row and a directory and buys
everything that matters: the child can be rewound, resumed, forked and inspected
on its own, and a crash mid-child leaves a child run to adopt rather than a
half-written parent.

`parent_run_id` answers "who spawned this?"; `root_run_id` answers "show me
everything this request did", which at depth 3 the parent chain cannot. A
`child_run_attach` ledger event records *which node* spawned it — the run row
does not, and a rewind of that node needs it.

Depth is capped at 3 and checked **before** anything is created: a workflow
referencing itself would otherwise spawn runs until the process died.

## Write scope

Stage and action nodes declare `allowed_write_paths`; the engine snapshots the
filesystem before the node and diffs after. Out-of-scope writes flag the node
`scope_violation` in the ledger with the violating paths named.

The load-bearing asymmetry: the **watched** set is wider than the **allowed**
set. An escape lands outside what is allowed, so snapshotting only the allowed
paths would make a violation undetectable by construction. Both sides are
resolved (symlinks, `..`) at comparison time.

Opt-in, deliberately: the tree walk is real work, and a fan-out of fast
transforms must not each pay for one.

## Sandbox tiers

`allowed_write_paths` above is the *watched* side of confinement — snapshot-and-diff, after the
fact. A **sandbox provider** is the *enforced* side: it runs a node's (or an app backend's, or a
picked terminal's) process INSIDE a boundary, so an out-of-scope write fails because the path was
never reachable, not because a diff noticed it afterward. The seam
(`personalclaw/sandbox_providers/`) is a two-phase `wrap → exec` contract; a consumer's
`allowed_write_paths`, `egress_tier`, and `safety_profile` thread into the `SandboxSpec` the
selected tier translates to its native knobs.

| Tier | Kind | Registration | Enforces |
|---|---|---|---|
| `none` | host | core builtin (always available) | OS path sandbox + resource ceilings only — no boundary |
| `docker` | bind-mount container | core builtin (self-gates on the daemon probe) | UID-aligned bind-mount over the workspace; `allowed_write_paths`/`grant_paths` mounted rw, everything else unreachable; `egress_tier: off` → `--network none`; ceilings → `--pids-limit`/`--memory` |
| `lima` | VM (`limactl shell`) | **app** (`apps/lima-sandbox`, enabled via `SandboxTypeHandler`) | full-VM isolation; host↔guest path translation for the guest workdir; availability = `limactl` present + instance `Running`, else a typed reasoned refusal (greyed-with-reason, never a silent host downgrade). Per-exec network / pids / memory are instance-creation config, so at this layer they are advisory rather than fabricated |

A tier that is requested but unavailable, or not installed at all, **refuses** rather than
downgrading to the host: an unattended run parks `needs-input`, an app backend declines to launch,
an agent session or second opinion does not start (`resolve_provider` raises for a named tier
nothing registered, and resolves to `none` only when no tier is named), and a terminal is not
opened, with a sentence in its tab that names the tier and why. App backends select a tier
through the `backend.sandbox` manifest field, with the app's `permissions.network` →
`egress_tier` and `permissions.storage` → `allowed_write_paths`; a terminal session selects one
per-session through the picker (`GET /api/sandbox/providers`). The tier is part of the terminal's
session id (`<id>@<tier>`, `<id>@none` for the host shell), so every open of the session, a
reconnect after a gateway restart included, is in that tier or refused, and a tier shell is never
a tmux client. An id that names no tier was made before ids carried one; nothing says where its
shell ran, so opening it is refused (*This terminal was opened before an update; open a new
one.*) while closing it still works.

## Templates

The shipped templates live in `workflows/bundled/`, served read-only from the package — an
upgrade ships new templates with no "did the user edit it?" reconciliation. A
user who wants to change one saves a copy under their own name and edits that:
the definition page's **Edit a copy** opens the dashboard editor on it.

### Editing a definition

The editor (`web/src/pages/workflows/WorkflowDefEditor.tsx`) is a client of the
two routes every other author uses: it reads `GET /api/workflows/{name}` and
writes `POST /api/workflows`, whose `save: false` is its **Check**. It sends the
WHOLE editable definition back — `runtime_hints`, `defaults`, `on_overlap` and
`workspace` included, which `author_def` and the native store now carry rather
than dropping — and places every returned issue by its `path`, the same `walk()`
path the engine keys instances by.

Because the save replaces the whole definition, a save over one of yours names
the `revision` its read reported, in `If-Match` (the contract every
whole-document write has, `personalclaw/stale_write.py`). When another tab, the
agent's `workflow_author`, the A2A publish toggle or an accepted refiner
proposal saved the definition after the editor read it — or another tab deleted
it — the save is refused with `409 stale_write` before anything is written or
any consent is asked, and the editor keeps the edit: **Reload and reapply**
merges it into what is stored field by field. A restore is not merged into a
newer version — it is saved over one only from **Review the difference**. A
copy under a new name and a Check replace nothing and name no revision; a copy
whose name was taken after the editor's check is refused (`428`) rather than
saved over the workflow that took it.

What a step may do is the owner's yes. A step that approves its own tool calls
(`approval_mode: "auto"`) or may change things (`capability: "mutating"`) does
so each time the workflow runs, so a save that lets a step do more than the same
step does now is written only with her yes: one writer screens every save
(`service._write_definition`; `tests/test_workflow_definition_writer_census.py`
holds every door to it). A step is the one with the same node id, and it does
more when it loosens a posture key or carries one she allowed but no longer runs
as it ran. The screen reads the definition as it will be stored, macros
expanded and hidden values restored. The editor's save asks it as the consent
question (`400 confirmation_required`, answered by resending with
`confirm: true`), and a Check names the steps a save would ask for
(`needs_owner_allow`). An agent's `workflow_author` hands such a save to the
gateway (`definition_ask`), which asks her once and saves on her Allow; an
accepted refiner diff that would let a step do more is not applied; a prompt
card's template is saved through the same save; and a pack's template arrives
with every step asking before it acts and only reading
(`automation_posture.workflow_what_it_is`).

The read is stripped, so the definition the editor holds has
`_has_<key>` flags where values were. `author_def` re-injects them before it
validates anything (`secrets.reinject_secrets`): a node's values are found
through the node's id and their place inside it, so a moved step keeps them,
and everything else by its path in the document. The source is the definition
named by `based_on` (a copy's template) or one recorded version
(`based_on_version`, a restore), else the definition being saved. A flag with
nothing to restore it from is refused as `WF_HIDDEN_VALUE_UNMATCHED` at its step,
never written to disk as a field. Every definition's read carries at least one
flag — `defaults.budget.max_tokens` matches the `token` hint — so this is the
common path, not an edge case.

A restore is an edit of a recorded version (`GET
/api/workflows/{name}/versions/{version}`), and its save is a new version. The
pinned pointer `versions/repin` moves is read by nothing that starts a run.

Authoring conventions, the lint that enforces them, and the macro/block
libraries are documented in
[`docs/guides/workflow-templates.md`](../guides/workflow-templates.md).

### The `self-qa` template's evidence node

The bundled `self-qa` template's `evidence` node is an **`action`** node backed by
`selfqa-evidence`, not an LLM `stage`: sealing a proof bundle is deterministic work a model
must not be trusted to fake ("compute the digests; do not estimate them"). Two new modules
under `src/personalclaw/selfqa/` carry that work, consumed by the
`action_providers/selfqa_evidence_provider.py` provider:

| Module | Job |
|---|---|
| `selfqa/evidence.py` | the evidence bundle: a cached ffmpeg availability probe (modelled on the docker sandbox probe — `None` "never yet" sentinel, short TTL); contact-sheet + GIF derived via ffmpeg as a local subprocess with typed graceful degradation (a `Derivation` whose `degraded_reason` the manifest records, never a crash); a schema-versioned SHA256 `Manifest` (per-file `{kind,name,size,sha256}` computed from the bytes on disk); `check_required_kinds`, the bundle-level completion gate that names the missing kinds; and `register_bundle`, which composes the bundle into a single Artifact (manifest as content, files stored content-addressed under the artifact dir) |
| `selfqa/fix_branch.py` | `create_fix_branch` opens `pclaw/selfqa-<sha8>` off the failing commit only when `fix_branch_enabled`, with no checkout and no push — the git runner mirrors `loop/worktree.py`'s build-ceiling discipline |

The completion gate is a kind-level, deterministic counterpart to the engine's file-glob
`required_artifacts` gate (`verify.check_required_artifacts`): where that one refuses a
node that did not write its declared *files*, `check_required_kinds` — run by the `selfqa-evidence`
provider, not declared on the node — refuses a run whose bundle is missing a required *kind* and
names it. The default required kinds are the ffmpeg-independent proof (screenshot, recording,
manifest), so a degraded (ffmpeg-less) bundle is still complete and only a genuinely missing proof
blocks; the provider returns a failed action (naming the missing kinds) to mark the run incomplete.
The provider reads its bundle from the run workspace, threaded into the action payload as
`workspace` at the dispatch seam (the same path the artifact gate already receives).

## The judge contract: self-approval is impossible, not discouraged

A loop that judges its own work converges on whatever the worker finds easiest to
claim. Prompt doctrine ("be skeptical") is advice, and advice loses to a worker being
scored on completion. So four mechanisms are structural rather than instructional:

**The worker actor may never reach `done`.** It can reach `waiting` or `review`; the
terminal transition belongs to a judge or gate actor (`judge_actors.check_transition`).
A worker claiming completion produces a `review` — a *request* for adjudication, not
the adjudication. A `done` from a worker is REDIRECTED rather than refused, because the
work may genuinely be finished and the right answer is to route it to a checker.

**A PASS without cited proof is invalid.** Rejected by the contract, not frowned upon.
A completion record without proof is a claim, and the point of a checker is to stop
accepting claims.

**The rubric ratchets; the overall is engine-computed.** Under `ratchet: strict` any
criterion below target fails the stage — no averaging, because averaging is how a
broken deliverable passes on the strength of its documentation. The overall score is
recomputed from dimension scores server-side; the model's own overall survives only as
metadata, so drift between them is visible instead of authoritative.

**The judge never sees worker narration.** `assemble_judge_evidence` keeps
user/spec/tool-call/tool-output roles and drops assistant prose entirely. A worker
cannot argue its way to a PASS if its arguments never reach the judge — stronger than
any instruction to discount them. Provenance ("attempt 4 of 5") is stripped too: it
tells a judge how much patience is left, which is exactly the pressure that produces a
lenient pass.

Beneath all of it, `judge_pretier` runs the free rules first — empty output, admitted
give-ups, tool errors, stub markers, missing referenced files, zero artifacts. Loop
judges run every cycle, so a rule-solvable failure reaching the model costs tokens on
every iteration forever. The pre-tier can prove work is *unfinished*; it can never
issue a PASS, since a cheap approval would recreate self-approval with extra steps.

`fallback_check` is a standing cross-check, not only a degradation path: when the
deterministic check FAILED and the judge passed anyway, the verdict auto-escalates. A
judge that passes what `exit 1` failed is either wrong or being gamed, and both need a
human. The check is tristate — `None` means "could not run", and collapsing that into
failure would turn an uninstalled linter into a broken deliverable.

## Where things are NOT

- **no compaction ladder yet** — the two-layer proactive/error-triggered
  summarizer from WF2-R6 needs a summarizer seam that does not exist;
- **no `{{nodes.x.artifact}}` binding** — oversize outputs spill to a stub with
  an `output_ref`, but the artifact-pointer binding form and `artifact_inspect`
  action are unbuilt;
- **no lifecycle gates** — per the owner's deferral, breaking changes are clean
  breaks under the pre-1.0 banner until the migration-backed lifecycle regime
  lands.
