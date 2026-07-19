# Plan: Proactive Assistant — Triage Flagship + Personal Decision Journal

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-12  
**Depends on:** WORKFLOWS-V2.md Slices 0-2 (run engine + ledger); WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md steps 1-5 (event bus, `triggers.json`, typed-outcome run records, delivery contract) incl. the approved AUTO-R19 `pulse` kind (referenced, not duplicated); AUTONOMY-GUARDRAILS NEW-1 (budget floor) + NEW-2 (typed structured-output substrate); WORKFLOWS-V2-LEARNING-FLYWHEEL.md §3.3 (LEARN-R18 pending→resolved outcome lessons)  
**Scope:** The capstone *composed experience* over the substrate plans — the first automations a user feels every morning, not more plumbing

---

## Research Integration (2026-07-12)

Two approved recommendations folded in (mechanism-level, not appendix):

- **NEW-26** — Proactive Triage flagship: scheduled digest-collect → classifier gate → tiered strict-JSON proposals → persistent approval memory → trivial-tier auto-execution → run-outcome ranking → §1 (all sub-sections), §4 tools, §5 FE, sessions 1-3
- **NEW-23** — Personal Decision Journal: horizon-triggered review, outcome capture, calibration record, LEARN-R18 pairing → §2, §4 tools, §5 FE, sessions 4-5

---

## Overview

The substrate plans build the machine; this plan builds the two experiences that justify it. Both are **compositions** — nearly every ingredient exists or is planned elsewhere, and this plan's job is to name the composed product, specify the few genuinely new pieces, and keep them plugged into the provider architecture the way everything else is.

1. **Proactive Triage (NEW-26).** Every morning (and on demand) PersonalClaw collects what accumulated across inbox items, channel sessions, and background-run outcomes; a lightweight classifier gate filters what matters; a strict-JSON stage emits *tiered action proposals* (trivial / low / medium / high); trivial-tier items and items matching a stored always-approve rule execute automatically under the NEW-1 budget floor; everything else lands as one digest whose one-word replies ("3 yes", "always no 3", "yes all") both act *and teach* — "always" answers persist as pattern-keyed approval rules the next digest consults. The digest leads with what mattered because ranking consumes the substrate's run-outcome classification (AUTO-R2's materiality predicate: action-vs-response weight + extracted artifact permalinks), not raw recency.

2. **Personal Decision Journal (NEW-23).** Log any real-life decision — career, financial, technical, personal — with expectations and a review horizon. The decision is a **user item** (knowledge side). At horizon, a one-shot trigger surfaces a review prompt; an outcome-capture dialogue records what actually happened; the distilled 2-4 sentence evidence-citing lesson is **harness memory** (memory side), written through the LEARN-R18 pending→resolved lifecycle the LEARNING-FLYWHEEL plan already owns. Over time the journal becomes a calibration record: predicted confidence vs realized outcome, per decision domain.

**Soul guardrail:** personal-scale, single user, local files. Proactive behaviors **propose; they never silently write** — the only auto-execution is the trivial tier plus explicitly-taught always-approve rules, both bounded by NEW-1 budgets and revocable in one click. The autonomy shape is Leon's bounded Pulse Engine and Memoh's heartbeat-vs-schedule split: the *scheduled* digest (an explicit clock trigger the user owns) stays orthogonal to the *self-directed* `pulse` kind (AUTO-R19, substrate §1.2/§7 step 10) — this plan ships the scheduled half and consumes pulse output as one more digest source when Phase 2 lands. No enterprise machinery: the approval store is memory rows, the journal is knowledge items, the rules manager is one settings card.

### Starting points (verified against code, 2026-07-12 recon)

- **Inbox alert evaluation happens ONCE at ingestion** — `evaluate_alert` (inbox.py L270: case-insensitive `alert_keywords` substring + `alert_on_name_mention` whole-word match) runs inside `InboxService._ingest` (inbox_service.py L194) and never re-evaluates stored items. The triage classifier gate (§1.2) deliberately runs at *digest time over stored items*, so it is not subject to that gotcha — the two mechanisms coexist: alerts are the real-time tripwire, triage is the batch review.
- **Inbox AI affordances already exist as one-shots**: classify / draft_reply / generate_digest via `one_shot_completion(use_case="background")` over stored items, external text wrapped in `fence_untrusted` (`_fence_message`), prompts via `render_use_case_prompt("inbox_classify|inbox_draft|inbox_digest")`. The triage pipeline *extends* this pattern (new use-case prompts, same resolution and fencing path) rather than building a second background-LLM stack.
- **Only two inbox sources exist and source wiring is hard-coded**: `filesystem_source` (the poll default) and `native_source` (always-on push sink); gateway `_init_inbox` asks `get_default_provider("filesystem")` only, and Slack is *deliberately not* an inbox source (gateway L1626). "Digest-collect over inbox sources" therefore means: stored inbox items + channel-session surfaces + run-ledger outcomes — NOT a fan-out over N pollable providers that don't exist. New sources arrive later via the `MessageSourceProvider` ABC (inbox_providers/base.py: `poll/send_reply/add_reaction/get_channel_history`) and the WATCHED-SOURCES plan; the collect stage is written against the ABC so they slot in without touching triage.
- **The notification gate is singular**: `DashboardState.notify` (dashboard/state.py:1023) → `notification_allowed(kind)` (providers/entity_routes.py:171 — `mute_all` → `min_severity` rank → quiet-hours suppress <error). Digest delivery routes through it; this plan builds no second path.
- **Commitments are a MemoryKind, not a file**: `record_commitment` lives in memory_service.py L558-651 with hard-coded guardrails (opt-in `memory.proactive_commitments`, confidence ≥0.8, ≤3 active/day/agent, key `user.commitment.<md5-12>`), delivered by the heartbeat's `_deliver_due_commitments` scan — which the substrate converts to one-shot `clock/at` triggers with `delete_after_run` (substrate disposition table). The decision journal's horizon reminders (§2.3) reuse *that* conversion pattern, and the approval memory (§1.4) reuses the *key-prefix precedent* (`user.approval.<md5-12>` beside `user.commitment.<md5-12>`).
- **Memory semantic keys are constrained**: regex `^[a-z][a-z0-9_.]*[a-z0-9]$`, ≤100 chars, value ≤4096 B (vector_memory.py); kind is inferred from key prefix (`_kind_from_key`, memory_record.py L310); non-fact prefixes are excluded from ambient fact injection via `_NON_FACT_KEY_CLAUSE` (vector_memory.py L383). OpenJARVIS-style colon-delimited rule keys (`email_delete:domain:noreply.github.com`) are **invalid as memory keys** — the pattern lives in `value_json`, the key is the hash (§1.4).
- **Knowledge is the user-item side**: one global library, `knowledge.db`, 12 `NATIVE_TYPES`, one ingest queue, `create_typed_item(provider="native")` + `ingest_queue.enqueue` as THE creation path; Passthrough pipeline graph covers note/gist/journal/fleeting. The `decision` type (§2.1) is the 13th native type riding Passthrough. There is **no cross-linking between memory records and knowledge items** today — the journal keeps it that way structurally (soft references by id in `value_json`/metadata, no FK).
- **The run-outcome classifier is NOT new**: substrate §1.3 already specifies the materiality predicate (AUTO-R2, tryfriday's action|response|error derivation from the journaled tool calls, with extracted external permalinks). Triage ranking (§1.5) *consumes* those typed ledger rows; this plan adds zero run instrumentation.

---

## 1. Proactive Triage Flagship (NEW-26)

Shipped as a **bundled workflow-template pack** ("Morning triage") — a WorkflowDef with a clock trigger pre-attached, installable in one click and fully editable, exactly like the substrate's §5.3 templates. The pipeline is five stages; stages 1-2 are deterministic/cheap, 3 is the one strict-JSON LLM call, 4-5 are rule-driven.

### 1.1 Stage 1 — Collect

A digest-collect node gathers, per configured window (default: since last successful digest run):

- **Inbox items**: unread/pending rows from `InboxStore` (inbox.json), already fenced at rest; muted threads and dismissed items excluded (existing `InboxState` filters).
- **Channel activity**: unresolved threads across channel sessions (`channel:` session keys) via the channel transports' history surface.
- **Background-run outcomes**: typed rows from the substrate Run Ledger since the window start — the materiality-classified fires (§1.5 consumes these for ranking; the digest's "what your machine did" section renders the productive rows' written/learned diffs + artifact permalinks directly from substrate §1.3 records).
- **Pulse proposals** *(Phase 2, when AUTO-R19 lands)*: pending matters from the pulse queue fold in as one more digest section — the digest becomes the pulse's delivery surface, keeping Memoh's split intact (pulse *generates* self-directed matters; the scheduled digest *presents* them).

Each collected item gets a stable per-digest ordinal id (`1`, `2`, …) and a one-line rendering. **Anti-hallucination contract (OpenJARVIS):** downstream stages must copy item ids *exactly* from these digest lines; any proposal referencing an id not in the collect manifest is dropped with a `refused` ledger outcome.

### 1.2 Stage 2 — Classifier gate (optional, cheap)

Per-source natural-language filter rules ("from GitHub notifications only surface review requests; skip dependabot"), evaluated by a small background-tier model — this is the substrate's fire→spawn triage stage (§3.6, LocalAGI ClassifierFilter convergence) applied at digest scope: verdicts `{drop | surface | propose}`, decisions cached on item fingerprint, `skipped_triage` ledger rows carry the rationale. Resolution via `one_shot_completion(use_case="background")` per the plug-in map; zero-item windows short-circuit before any LLM spend (the gcp-always-on precondition-guard pattern: one cheap store query decides whether the LLM stage runs at all).

### 1.3 Stage 3 — Tiered strict-JSON proposals

ONE LLM call over the fenced, surviving items emits an action-proposal array — schema-enforced through the NEW-2 typed structured-output substrate (`additionalProperties: false`, length caps, max 8 proposals per run):

```json
{
  "proposals": [{
    "item_id": "3",                  // MUST match a collect-manifest ordinal exactly
    "action_type": "archive|reply_draft|create_task|mute_thread|dismiss|remind|none",
    "action_config": { },            // args for the bound action provider, schema per action_type
    "tier": "trivial|low|medium|high",
    "pattern_key": "reply_draft:sender:github.com",  // the generalization this proposal instantiates
    "reasoning": "one sentence"
  }]
}
```

- **Tier assignment is prompt-guided but policy-clamped**: a deterministic post-pass caps the tier by action class (anything that sends/posts externally is never below `medium`; destructive ops never below `high`), so a jailbroken prompt cannot self-assign `trivial`. Fail-closed: unparseable output → the run degrades to a plain digest with zero proposals, `refused` outcome, never a retry loop against the schema.
- All item content crosses this stage **fenced** (`fence_untrusted` with provenance attrs per substrate decision 4) — an inbox item containing injection text can at worst produce a proposal, and proposals only *bind arguments to the pre-declared action set* (frozen action-set invariant, substrate decision 7); they can never introduce actions.

### 1.4 Stage 4 — Approval memory (the learning half)

**Routing per proposal:**

| Condition | Route |
|---|---|
| stored `always_deny` rule matches `pattern_key` | silently skip → `skipped_gate` ledger row naming the rule |
| tier `trivial` OR stored `always_approve` rule matches | auto-execute (§1.6) |
| else | queue pending in the digest |

**Reply grammar (deterministic, no LLM):** the digest delivery thread accepts one-word replies parsed by a small grammar — `3 yes` / `3 no` (act once), `always yes 3` / `always no 3` (act + persist a rule from that proposal's `pattern_key`), `yes all` / `no all`. Replies arrive over whatever surface delivered the digest: a channel reply (the `ChannelTransportProvider` inbound path — the substrate's stable event-id makes acks idempotent) or the inbox reply path (`MessageSourceProvider.send_reply` is the confirmation affordance). Unparseable replies get a help line back, never an LLM interpretation — the grammar IS the safety boundary.

**Rule persistence — memory side, explicitly.** Learned approve/deny patterns are the *harness's model of how the user wants it to behave* — memory, not knowledge. Storage follows the commitments precedent exactly:

- Semantic rows keyed `user.approval.<md5-12(pattern)>` (valid under the key regex; the raw pattern string like `archive:sender:noreply.github.com` lives in `value_json` alongside `{verdict: approve|deny, action_type, scope, created_from_digest, hit_count, last_hit_at, expires_at?}`).
- `user.approval.` joins the prefix set in `_kind_from_key` (memory_record.py) as an `approval` MemoryKind, and joins `_NON_FACT_KEY_CLAUSE` so rules never leak into ambient fact-injection blocks — they are policy lookups, not conversation context.
- Lookup at triage time is a **deterministic prefix query** (`MemoryProvider.query(kinds=[approval])` + exact/most-specific pattern match), never vector search — approval decisions must be exact.
- Like commitments, approval rules **never promote by heat** and are scope-bounded; unlike commitments they have no daily cap (they are user-taught, not agent-inferred) but DO carry optional `expires_at` and a hit-count so stale rules are visible.
- Writes route through the existing guarded path (`MemoryService` + S5 write-injection scan) — the rule text is derived from a proposal the user explicitly ratified, but it still passes the scanner.

**Suppression learning (Leon):** declining the *same* `pattern_key` repeatedly without saying "always no" applies escalating suppression cooldowns (24h → 7d → 30d) recorded on a shadow `user.approval.` row with `verdict: suppressed` — the digest stops re-proposing what the user keeps ignoring, without requiring them to formalize a rule. Accepting during a cooldown clears it.

### 1.5 Stage 5 — Rank + deliver

- Ranking: substrate §1.3 typed outcomes drive section order — externally-material items (runs that touched the world, items with pending external effects) lead; `response`-weight and no-op noise sinks or is folded into a one-line count. Artifact permalinks from the ledger render as deep links.
- Delivery: through the substrate's outbound delivery contract (decision 13) — `DashboardState.notify` → `notification_allowed()` gate (quiet hours/severity respected — a digest is `info`-ranked, so quiet hours defer it, which is correct for a *morning* digest), destination-aware formatting (rich block inbox/dashboard, flattened for `channel:slack`), stable event-id, statusUrl into the run journal.
- The whole digest run is a normal WorkflowRun: journaled, resumable, visible in the Runs inbox, autopause-on-true-failures per substrate §3.7.

### 1.6 Trivial-tier auto-execution — guardrails

Auto-execution is the sharpest edge; it is quadruple-bounded:

1. **NEW-1 budget floor** (AUTONOMY-GUARDRAILS): per-run/per-day token+dollar+action ceilings consulted before every auto-executed action; breach → remaining proposals demote to pending + needs-input, `skipped_budget` rows.
2. **Frozen capability set** (substrate decision 7): the triage template's trigger declares exactly the action providers proposals may bind (`inbox-op`, `create-task`, `send-message` draft-only, `notify`); the engine enforces at execution. External-send actions are NOT in the trivial-capable set by default — even an always-approve rule for `reply_draft` produces a *draft*, and graduating a pattern to actually-send is an explicit per-rule toggle rendered with a warning badge.
3. **Cap per run**: `max_auto_actions_per_run` (default 5) — the rest queue pending regardless of tier.
4. **Every auto-execution is a ledger row** with the matched rule named, and the digest's first section lists what was auto-done with one-click undo where the action provider supports it (archive/mute are reversible; that's why they're the trivial class).

**New action provider — `inbox-op`.** Archive / dismiss / mute-thread / mark-read / reply against `InboxStore` + the source provider's `send_reply`/`add_reaction`. Implements `ActionProvider` (action_providers/base.py), registered via `register_action_provider`, **added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)**, settings schema via an `inbox-op-action` extension manifest — the full provider-fidelity checklist (see Plug-in Map). Once registered it is usable by ALL trigger kinds, not just triage.

---

## 2. Personal Decision Journal (NEW-23)

A product surface, not engine plumbing. The engine pieces (one-shot triggers, pending→resolved lesson lifecycle) belong to the substrate and LEARNING-FLYWHEEL respectively; this section specifies the data model, tools, and UX that make decision-tracking *ambient*.

**Boundary note up front (user directive):** a decision entry is a *user item* — a document about the user's life — and lives in **knowledge.db**. The distilled lesson the harness learns from its resolution is **memory** (a `lesson.*` semantic row via `write_lesson`). The calibration record is derived from knowledge items at read time. Nothing in this section writes memory except the final R18 lesson step, and nothing writes knowledge except the user's own entries and their resolution updates.

### 2.1 Data model — knowledge side

The research source (TradingAgents) proved the minimal shape with one markdown file + HTML-comment delimiters + status-in-tag lifecycle. PersonalClaw has a better home for user items than a loose file: **`decision` becomes the 13th `NATIVE_TYPES` entry** (knowledge_providers/native), created through the one true path — `store.create_typed_item(item_type="decision", provider="native")` + `ingest_queue.enqueue` — riding the **Passthrough pipeline graph** (same class as note/journal: no scraping/extraction, straight to consolidate → entities → embed). What survives from the research shape is the *lifecycle*, not the file: append-only entries, `pending → resolved` status, pending entries never evicted.

Decision-specific structured fields ride the item's metadata JSON (no new columns needed; `_migrate` untouched):

```json
{
  "decision": {
    "status": "pending|resolved|abandoned",
    "domain": "career|financial|technical|personal|health|other",
    "expectation": "what I predict will happen",
    "confidence": 0.7,
    "options_considered": ["…"],
    "review_horizon": "2026-10-12",
    "reminder_trigger_id": "clock:…",
    "outcome": null,
    "outcome_captured_at": null,
    "lesson_memory_key": null
  }
}
```

`content` is the free-prose decision record (context, reasoning, stakes) — it embeds and retrieves like any knowledge item, shows up in the knowledge graph via entity extraction, and is @-pickable into chat. `lesson_memory_key` is a **soft string reference** to the eventual `lesson.*` row — deliberately not a FK; the two stores stay structurally uncoupled (recon: no cross-linking exists today, and we keep it that way).

### 2.2 `log_decision` chat tool

The ambient-capture affordance: mid-conversation, "log this as a decision" (or the agent offers when it detects one — offer-only, never auto-log). The tool:

1. Elicits/extracts `expectation`, `confidence`, `domain`, `review_horizon` (defaults: config `proactive.decision_default_horizon_days`, 90).
2. Creates the knowledge item via the native provider path.
3. Mints ONE one-shot review trigger: `Trigger{kind: clock, spec: {kind:'at', at: horizon, delete_after_run: true}, workflow: {ref: 'decision-review'}, created_by: 'system:decision-journal', delivery: inbox}` — deterministic id (`system:decision-journal:<item_id>`) so re-logging is idempotent, exactly the substrate's commitment-conversion pattern. Editing the horizon reschedules the trigger; abandoning the decision retires it.

Registered beside the existing knowledge tools (`agents/native/builtin_tools.py` L459-513, app `personalclaw-knowledge-tools`): `log_decision`, `decision_list(status?, domain?)`, `decision_resolve(id, outcome)`.

### 2.3 Horizon-triggered review + outcome capture

At horizon the trigger fires the bundled `decision-review` WorkflowDef:

1. Loads the decision item; delivers a review card to the **inbox** (through the notify gate — quiet hours respected) with the original expectation + confidence quoted back, and a statusUrl into the journal view.
2. The card opens an **outcome-capture dialogue** in chat (a linked session, the `to-chat` pattern): "You decided X expecting Y at 70% — what actually happened?" Structured capture: `outcome` prose + a simple resolution grade (`better|as_expected|worse|mixed|too_early`). `too_early` re-arms a new one-shot trigger (+50% horizon, capped at 2 deferrals — then the item surfaces as stale-pending in the journal view rather than nagging forever).
3. Resolution updates the knowledge item (`status: resolved`, outcome fields) and re-enqueues it for ingestion so the outcome text embeds too.

Non-response is fine: the trigger already fired and deleted itself; the pending card sits in the inbox under normal retention; the journal view shows overdue-pending items. No re-nag loop by default (personal-scale: one reminder per horizon, the surface does the rest).

### 2.4 Lesson distillation — memory side, via LEARN-R18

The decision journal is the *product face* of the pending-outcome lesson lifecycle LEARNING-FLYWHEEL §3.3 already specifies — this plan does **not** duplicate the resolver. Wiring: at resolution, the `decision-review` workflow files the R18 lesson-writer invocation with the decision item as evidence — strict format (2-4 sentences, plain prose, cite the stated expectation vs the captured outcome, one concrete lesson, every word earns its place — the TradingAgents write-time contract), written through `MemoryService.write_lesson` → a `lesson.<md5-12>` semantic row, deduped/superseded like any lesson. `lesson_memory_key` is stamped back on the knowledge item. Where a decision declared a *measurable* outcome (a metric + baseline), it instead journals a proper R18 `pending_outcome` contract and lets the flywheel resolver measure ground truth — the journal's subjective capture is the fallback for unmeasurable life decisions, which is most of them.

### 2.5 Calibration record

Computed, not stored: the journal view aggregates resolved decisions into a per-domain calibration strip — stated confidence buckets vs realized `better/as_expected/worse` rates, count-honest ("7 decisions — too few to mean much" below n=10). One optional ambient hook: when `log_decision` captures a confidence in a domain where the user's calibration is demonstrably skewed (n≥10), the tool echoes it back ("your 80%+ financial calls resolved 'as expected' 40% of the time") — information at the moment of prediction, never a nag. No LLM, no new store; one SQL aggregate over `knowledge.db` decision items.

---

## 3. Composition Map (what is consumed vs built)

| Ingredient | Source | This plan builds |
|---|---|---|
| Clock trigger + one-shot `at`/`delete_after_run` | substrate §1.2 | nothing — consumes |
| Run-outcome materiality classification + permalinks | substrate §1.3 (AUTO-R2) | ranking consumer only |
| Fencing + injection screen + frozen capability sets | substrate decisions 4/7 | triage-specific capability declaration |
| Delivery contract + `notification_allowed` gate | substrate decision 13; entity_routes.py | digest formatting, reply-grammar inbound |
| Fire→spawn triage/classifier machinery | substrate §3.6 | per-source NL rules UI + digest-scope application |
| `pulse` self-directed matters | substrate AUTO-R19 (Phase 2) | digest renders them as a section — nothing else |
| Budget floor, kill switch | AUTONOMY-GUARDRAILS NEW-1 | consultation points in auto-execute path |
| Strict-JSON structured output | AUTONOMY-GUARDRAILS NEW-2 | the proposal schema |
| Pending→resolved lesson lifecycle | LEARNING-FLYWHEEL §3.3 (LEARN-R18) | the invocation at decision resolution |
| Inbox stores, fencing, background one-shots | inbox.py / inbox_service.py | collect stage, `inbox-op` action provider |
| Commitments key-prefix + one-shot delivery precedent | memory_service.py L558-651 | `user.approval.` prefix + approval MemoryKind |
| Knowledge native types + ingest queue | knowledge_providers/native, ingest_queue | `decision` type + Passthrough wiring + 3 tools |
| Lessons write path | memory_service `write_lesson` | strict-format invocation only |

---

## 4. Chat Tools

| Tool | Description |
|---|---|
| `log_decision` | `(summary, expectation, confidence, domain?, review_horizon?)` → knowledge item + one-shot review trigger |
| `decision_list` | `(status?, domain?)` — pending/resolved/overdue |
| `decision_resolve` | `(id, outcome, grade)` — manual resolution outside the review card |
| `triage_rules` | `(list \| revoke <id> \| add <pattern> <verdict>)` — the approval-memory management surface; every rule shows hit_count + created_from provenance |
| `triage_run` | manual digest fire (bypasses min-interval per substrate manual semantics; never budget floors) |

All tools route over HTTP with session-key checks like `mcp_memory.py` does — restricted sessions (temporary) get memory-side reads/writes blocked per the existing gates; incognito blocks the rule writes.

---

## 5. FE Surfaces

1. **Digest card** (inbox + dashboard notification): auto-done section with undo, pending proposals with tier badges + one-tap yes/no/always, "what your machine did" ledger section with permalinks, statusUrl into the run journal. Strictly read-only on view; acting is explicit (the substrate's runs-inbox rule).
2. **Triage rules manager** (one settings card under Inbox settings, per the dual-writer rule: rows are `created_by: system:triage`, edit-locked on the Automations page, managed here): pattern, verdict, scope, hit count, expiry, revoke; the send-capable graduation toggle with warning badge.
3. **Decision Journal view** (a filtered knowledge view, not a new nav section — decisions ARE knowledge items): pending (with horizon countdown + overdue flag), resolved (expectation vs outcome side-by-side, linked lesson chip), the per-domain calibration strip.
4. **Template pack card** ("Morning triage") on the substrate's templates surface — install → editable trigger + per-source classifier rules form.

---

## 6. What We Deliberately Do NOT Build

- **No second pulse engine** — AUTO-R19 owns self-directed proactivity; this plan's digest is the scheduled, user-owned half of the Memoh split and later *presents* pulse matters.
- **No LLM interpretation of approval replies** — the grammar is deterministic; ambiguity gets a help line, not a guess.
- **No auto-send tier by default** — external sends are drafts until a rule is explicitly graduated.
- **No new inbox source providers** — that's WATCHED-SOURCES; the collect stage codes against the existing `MessageSourceProvider` ABC.
- **No decision-journal markdown file store** — the research shape's *lifecycle* survives; the storage is the knowledge store where user items belong.
- **No FK between knowledge items and memory rows** — soft string references only; the two subsystems stay structurally separate (recon-verified invariant, kept).
- **No calibration gamification** — one honest strip, count-caveated; this is a mirror, not a score.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Injected inbox content steering proposals | fenced input + strict schema (NEW-2) + tier clamp by action class + frozen action set + exact-ordinal-id contract; adversarial test in success criteria |
| Auto-execution doing harm | quadruple bound (§1.6): NEW-1 budgets, reversible-only trivial class, per-run cap, named-rule ledger rows + undo; kill switch inherited |
| Approval rules over-generalizing | pattern keys are narrow (action_type + one qualifier); most-specific-match wins; deny beats approve; hit-count + expiry surface stale rules; one-click revoke |
| Reply grammar acting on the wrong digest | stable event-id per digest + ordinals scoped to one digest run; replies against an expired digest get "that digest expired" not best-effort execution |
| Digest fatigue | classifier gate + suppression cooldowns + materiality ranking + quiet-hours gate; empty windows short-circuit to no delivery |
| Horizon reminders nagging | one fire per horizon (trigger self-deletes), 2-deferral cap, stale-pending is a view state not a notification |
| Memory/knowledge conflation creeping in | boundary stated per artifact (§1.4, §2 preamble); review checklist item: no code path writes the other store |
| Coupling to unshipped substrate | plan is staged behind substrate steps 1-5; session 1 (approval memory + config) has no substrate dependency and can land early |

---

## Provider & Config Plug-in Map

Where each new piece plugs into the pluggable-provider architecture — nothing invents a parallel path:

- **`inbox-op` action provider**: implements `ActionProvider` (action_providers/base.py), registered via `register_action_provider` in `_ensure_default_providers_registered` (or app-delivered later via `provider: {type: "action"}` per the webhook-action precedent), **name added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)**, settings schema via an `inbox-op-action` extension manifest. Reaches `InboxStore`/source providers through `ActionServices` (action_providers/services.py) like other native providers.
- **`decision` knowledge type**: extends `NATIVE_TYPES` in `knowledge_providers/native/__init__.py` + a Passthrough mapping in `knowledge/pipeline/graphs.py`; creation only via `store.create_typed_item(provider="native")` + `ingest_queue.enqueue` — the uber-pool rule. Future external knowledge providers (Drive, Photos) are orthogonal; decisions are native items.
- **`approval` MemoryKind**: `user.approval.` prefix added to `_kind_from_key` (memory_record.py L310) + `_NON_FACT_KEY_CLAUSE` (vector_memory.py L383); writes through `MemoryService` guarded paths; the prefix is inside the builtin `user.*` allowlist so no `memory.semantic_keys` config change is needed.
- **Templates**: "Morning triage" + "decision-review" ship as bundled WorkflowDefs with pre-attached triggers on the substrate's template surface; triggers minted with deterministic `system:` ids for idempotent re-registration.
- **Background LLM**: classifier gate + proposal stage resolve via `one_shot_completion(use_case="background")` / the reasoning axis in `active_models.json` — never the chat axis; new prompts registered as use-case prompts beside `inbox_digest` (`render_use_case_prompt("triage_classify" | "triage_propose")`).
- **New config = a `ProactiveConfig` section**, wired through the FOUR points (recon persistence-security gotcha #1): (a) dataclass fields with `_meta(label, help)` — `triage_enabled` (default false), `digest_schedule`, `auto_execute_enabled` (default false), `max_auto_actions_per_run` (5), `classifier_gate_enabled`, `decision_default_horizon_days` (90); (b) `AppConfig.load()` explicit field-by-field mapping; (c) `to_dict()` new top-level section; (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for the runtime-editable ones.
- **Delivery**: exclusively `DashboardState.notify` → `notification_allowed()`; channel formatting via registered `ChannelTransportProvider`s; redaction before every surface, as heartbeat delivery does today.
- **SEL**: auto-executions, rule creations/revocations, and send-capability graduations audit to `sel.py` like skill installs and egress do.

---

## Implementation Effort

**~5 sessions** (after substrate steps 1-5 are available; session 1 is independently landable):

- **Session 1 — Approval memory + config**: `user.approval.` prefix + `approval` kind + non-fact exclusion; deterministic rule matcher (most-specific, deny-wins); reply grammar parser (pure function, exhaustively unit-tested); suppression cooldowns; `ProactiveConfig` four-point wiring; `triage_rules` tool.
- **Session 2 — Triage pipeline**: collect node (inbox + channels + ledger, ordinal manifest); classifier gate over the substrate triage machinery + per-source NL rules; strict-JSON proposal stage (NEW-2 schema, tier clamp, exact-id contract); ranking off ledger outcomes; digest formatting + delivery contract; "Morning triage" template.
- **Session 3 — Auto-execution + `inbox-op`**: the action provider (registry + ALLOWED_HOOK_PROVIDERS + manifest); NEW-1 budget consultation + per-run cap + undo; auto-done ledger rows; adversarial injection tests (success criteria 2/3).
- **Session 4 — Decision journal core**: `decision` native type + Passthrough graph + metadata schema; `log_decision`/`decision_list`/`decision_resolve` tools; horizon one-shot triggers (deterministic ids, reschedule/retire); `decision-review` WorkflowDef + outcome-capture dialogue; R18 lesson invocation + soft back-reference.
- **Session 5 — FE + validation**: digest card (undo, one-tap replies, tier badges); rules manager card; journal view + calibration strip; template pack cards; end-to-end as-a-user validation of both flows incl. quiet-hours, restart, and revocation paths.

## Success Criteria

1. Installing "Morning triage" and sleeping through one cycle produces exactly one digest (quiet-hours deferred to morning), ranked with material items first, with a statusUrl that opens the run journal.
2. An inbox item containing prompt-injection text cannot cause any action outside the trigger's frozen capability set, cannot self-assign `trivial`, and cannot reference an item id absent from the collect manifest — verified adversarially.
3. Replying `always no 4` executes nothing, persists a `user.approval.` deny rule visible in the rules manager with provenance, and the matching item class is silently skipped (with a `skipped_gate` ledger row naming the rule) in the next digest; revoking the rule restores proposals.
4. A trivial-tier archive auto-executes under budget, appears in the auto-done section, and undo restores the item; when the NEW-1 daily budget is exhausted mid-run, remaining trivial proposals demote to pending with `skipped_budget` rows — zero silent drops.
5. `log_decision` in chat creates a knowledge item (`decision` type, searchable, @-pickable) and exactly one one-shot trigger; at horizon a review card lands in the inbox once, the outcome dialogue resolves the item, and a `lesson.*` memory row exists citing the stated expectation vs the captured outcome — with the knowledge item and the memory row linked only by soft references.
6. `too_early` defers at most twice, then the item shows as stale-pending in the journal view with no further notifications.
7. The calibration strip renders per-domain confidence-vs-outcome honestly (count caveat under n=10) from knowledge.db alone — no new store, no LLM call.
8. Nothing in the triage or journal code paths writes to the other store: grep-level audit shows knowledge writes only in §2 paths, memory writes only in §1.4 rules + §2.4 lessons.
9. Kill the gateway between digest delivery and a reply: the reply still acts (stable event-id, idempotent ack) or refuses with "digest expired" — never a wrong-target execution.
10. Disabling `proactive.triage_enabled` retires the system triggers and the rules manager renders rules as dormant-but-kept; re-enabling is lossless.
