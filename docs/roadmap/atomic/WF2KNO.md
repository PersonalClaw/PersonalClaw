# WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS — atomic plans

**Source plan:** [`WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS`](../plans/WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS.md)  
**Code:** `WF2KNO`  
**Source status:** done

Decomposed WF2KNO into 12 atoms: 9 done, 3 todo. (This breakdown sentence predates several landings — the per-atom `**Status:**` blocks below and `dag.json` are authoritative; sessions 34-39 are CODE DONE on main, `render_report` was deferred, and the residual seams are the gap-healing→flywheel enum, the 4 net.fetch-blocked templates and the model-tier conflict-pass wiring.) Cross-plan edges: WORKFLOWS-V2 engine (action/wait/loop), LEARNING-FLYWHEEL proposal-queue Kind, and a dispatchable net.fetch action provider. **Capability-gap amendment (2026-08-19)** adds two atoms: `WF2KNO-11` (synthesis legibility — a staleness banner, propose-then-accept updates, and citation markers parsed out of the model's OUTPUT and stored per marker; today the whole retrieved set is stored, so the require-citations control is satisfiable by an answer that cites nothing and "which source supports this sentence" has no answer) and `WF2KNO-12` (scheduled research reports, whose source-scope x context-scope x citation-policy triple is what makes a contradiction scan and an open-question tracker configuration rather than code, with four named schedule failure modes closed).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2KNO-1` | ✅ | Store semantics groundwork: taxonomy, logical identity, claims/relations, migrations, KnowledgeConfig | — | knowledge/semantics.py (10-kind taxonomy, {kind}:{normalized_title} logical identity, content+chunk hashing, 1-∏(1-cᵢ) sub-1.0 confidence, claims/mentions with invalid_at supersession, 5-verb item relations) + store.py additive migrations (kind/logical_key/last_verified/expires_at cols + item_relations table + logical-key index in _migrate) + KnowledgeConfig wired through all 4 config points + schema.md scaffold; tests green |
| `WF2KNO-2` | ✅ | The provider pair: knowledge_persist + knowledge_retrieve app, allowlist, native-provider retrieval seam | `WF2KNO-1`, `EXT:WORKFLOWS-V2:action-node dispatcher + wait node (Slice 1) and ALLOWED_HOOK_PROVIDERS seam` | apps/native/knowledge-actions/ ships both providers (idempotent upsert/append_evidence/ops, budgets, citation enforcement, degradation-ladder retrieve with create_safety/freshness/detail caps); both in ALLOWED_HOOK_PROVIDERS + action registry; NativeKnowledgeProvider.search() routes retrieval via knowledge_providers registry (search_all first caller; manifest factory repointed); persist→retrieve→confirm template validated live with a proven idempotent no-op on re-run |
| `WF2KNO-3` | ✅ | Long-run engine additions: until_cancelled + reaper, siblings/previous bindings, buffer-seal, adaptive clamp | `EXT:WORKFLOWS-V2:loop/parallel/wait engine + run journal (Slices 0-2)` | workflows/longrun.py adds until_cancelled loop mode + reap_watchers, {{siblings.*}} (window/unseen/significant/full/hygiene) + {{previous.output}} + run-continuity state, buffer-seal wait condition, adaptive-delay clamp, journal ledger kinds, validators; a watcher run completes on accompanied-work-complete instead of hanging (validated live); 3 pre-existing loop-path defects fixed |
| `WF2KNO-4` | ✅ | Consolidation + maintenance lifecycle: consolidation.py, health/consolidate/gaps providers + 3 templates | `WF2KNO-1`, `WF2KNO-2` | knowledge/consolidation.py (gate stack, deterministic pre-dedup, injectable-metric clustering, lineage caps/reflection_count<3, differential refresh, phantom hubs, mutation-cadenced lint) + knowledge-health/knowledge-consolidate/knowledge-gaps providers + 3 bundled maintenance templates + config knobs; runs over a 100+-item store with health-before-lint ordering |
| `WF2KNO-5` | ✅ | Contradiction pass + retrieval polish: contradiction.py, session_brief.py, fenced_sources, conflict UI | `WF2KNO-1`, `WF2KNO-2` | knowledge/contradiction.py + session_brief.py: persist-time deterministic conflict pass with typed-edge writes, fenced_sources binding filter, read-only conflict/relations routes, ConflictPanel Knowledge view, config knobs; a seeded same-subject+predicate conflict is flagged at persist with both claims retained under the precedence ladder; Session Brief injected into runs; split-brain knowledge_db_path fix; validated live |
| `WF2KNO-6` | ✅ | Bundled template slate + long-run validation (4 provider-buildable templates) | `WF2KNO-2`, `WF2KNO-3`, `WF2KNO-5` | knowledge-synthesis, rich-ingest, thesis-tracker, publish-article shipped in the Store; test_knowledge_longrun_validation.py mutation-tested (sibling window, seen-set marking, idempotency at 50 calls); rich-ingest provider set asserted exactly {knowledge-persist, create-task}; publish-article draft→dual-review→gate→persist appending a kind:decision validated live against the real store |
| `WF2KNO-7` | ✅ | render_report action provider (deferred/optional last slice) | `WF2KNO-2` | render_report ships in apps/native/knowledge-actions/ providers[] and ALLOWED_HOOK_PROVIDERS; declarative spec (markdown/table-ops/Mermaid xychart) → sanitized self-contained HTML/SVG; spec text stored as the versioned record in artifacts/registry with rendered output as a derived export; periodic synthesizer regenerates visuals from updated data |
| `WF2KNO-8` | ✅ | Route gap-healing drafts to the LEARNING-FLYWHEEL proposal queue | `WF2KNO-4`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:extend proposal-queue Kind enum with a knowledge-draft kind` | gap-healing / schema-edit drafts enqueue via learning.proposals.enqueue under a knowledge-draft Kind; the session-37 workaround (persisting a TTL'd probe tagged 'proposal') is removed — blocked because learning.proposals.Kind is a closed 6-value enum with no knowledge-draft kind |
| `WF2KNO-9` | ⬜ | Provider-blocked template slate: market-monitor, trending-repo-digest, dual-sink watcher, paper-ingest | `WF2KNO-6`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:dispatchable net.fetch action provider (HTTP egress chokepoint)` | the four monitor/ingest templates ship and dispatch a real HTTP-egress action node at run time without ALLOWED_HOOK_PROVIDERS/run-time failure — blocked because net.fetch is a library egress function, not a dispatchable action provider |
| `WF2KNO-10` | ✅ | Wire the model-tier (fast-model) contradiction pass to a live model via a stage node | `WF2KNO-5` | the built-and-tested fast-model conflict pass runs against a live model through a stage node (not inside the action provider, per the action/stage split), and background typed-edge inference beyond `contradicts` is wired; observed end-to-end |
| `WF2KNO-11` | ⬜ | Synthesis legibility: staleness banner, propose-then-accept update, parsed per-marker citations | `WF2KNO-2` | a synthesized artifact whose sources have changed renders a banner naming the COUNT of new source items and offering one regenerate action, rather than silently serving a stale document; an update runs as a proposal the owner inspects and accepts or dismisses so generated prose never overwrites human writing, and the plain update path is expressed as propose-plus-auto-accept rather than a second code path (clean break, one updater); citation markers are PARSED OUT OF THE MODEL'S OUTPUT and stored relationally per marker (marker index, source item, chunk index, excerpt), keyed on the marker NUMBER rather than order of appearance so a marker emitted out of order does not renumber; a marker with no registered source is dropped with a warning rather than reaching the reader as a dangling reference; markers already present inside a quoted source are stripped before that text re-enters a prompt so every number resolves against the current turn's sources; the require-citations control is no longer satisfiable by storing the whole retrieved set, and a test proves an output that cites nothing fails it — today 'which source supports this sentence' is unanswerable |
| `WF2KNO-12` | ⬜ | Scheduled research reports: source-scope x context-scope x citation-policy, with hardened schedule semantics | `WF2KNO-6` | a report definition carries a research prompt, a schedule with a timezone, a SOURCE scope (which items count as new material, by tag subtree and time window), a SEPARATE CONTEXT scope (what may be searched while writing) and a CITATION POLICY (cite-source-only vs allow-citing-context) — that triple is what makes a contradiction scan and an open-question tracker expressible as configuration instead of code; each run writes one finding as an ordinary knowledge item so it inherits search, graph and synthesis for free, excluded from the default item list by kind; the agent loop is bounded by an iteration cap and told not to invent citation markers; schedule semantics are hardened against four named failures — an unparseable expression fails CLOSED so a malformed report cannot wedge the runner, a never-run report anchors its first fire on its creation time rather than the epoch, a missed window fires ONCE rather than once per window skipped, and a failed run records its error WITHOUT advancing the last-run timestamp; the due watermark is taken at scope-resolution time, not completion time, so an item captured mid-run is not skipped forever; a manual run is idempotent against an in-flight scheduled fire; an empty scope is a terminal success with no model call and no item written; delivery rides the existing digest and notification path rather than adding a second one |

## Atom scopes

### `WF2KNO-1` — Store semantics groundwork: taxonomy, logical identity, claims/relations, migrations, KnowledgeConfig

**Status:** done

§2 (KnowledgeConfig four-point wiring), §3.1 Compiled Truth + Timeline, §3.2 Typed Relations, §3.3 schema.md, §9 Store & Config Changes; Implementation Effort Session 1

**Done when:** knowledge/semantics.py (10-kind taxonomy, {kind}:{normalized_title} logical identity, content+chunk hashing, 1-∏(1-cᵢ) sub-1.0 confidence, claims/mentions with invalid_at supersession, 5-verb item relations) + store.py additive migrations (kind/logical_key/last_verified/expires_at cols + item_relations table + logical-key index in _migrate) + KnowledgeConfig wired through all 4 config points + schema.md scaffold; tests green

### `WF2KNO-2` — The provider pair: knowledge_persist + knowledge_retrieve app, allowlist, native-provider retrieval seam

**Status:** done

§2.1 knowledge_persist, §2.2 knowledge_retrieve, §2.3 Zero-Model Heuristic Extraction Floor, §5.1 Retrieval Stage at Workflow Start; Session 2

**Done when:** apps/native/knowledge-actions/ ships both providers (idempotent upsert/append_evidence/ops, budgets, citation enforcement, degradation-ladder retrieve with create_safety/freshness/detail caps); both in ALLOWED_HOOK_PROVIDERS + action registry; NativeKnowledgeProvider.search() routes retrieval via knowledge_providers registry (search_all first caller; manifest factory repointed); persist→retrieve→confirm template validated live with a proven idempotent no-op on re-run

### `WF2KNO-3` — Long-run engine additions: until_cancelled + reaper, siblings/previous bindings, buffer-seal, adaptive clamp

**Status:** done

§4.1 Item Identity + Persistent Seen-Set, §4.2 Bounded-Context Continuation, §4.3 Buffer-Seal, §8 Changes to WORKFLOWS-V2.md; Session 3

**Done when:** workflows/longrun.py adds until_cancelled loop mode + reap_watchers, {{siblings.*}} (window/unseen/significant/full/hygiene) + {{previous.output}} + run-continuity state, buffer-seal wait condition, adaptive-delay clamp, journal ledger kinds, validators; a watcher run completes on accompanied-work-complete instead of hanging (validated live); 3 pre-existing loop-path defects fixed

### `WF2KNO-4` — Consolidation + maintenance lifecycle: consolidation.py, health/consolidate/gaps providers + 3 templates

**Status:** done

§3.4 Maintenance Lifecycle (three cost-tiered templates), §4.4 Consolidation Mechanics + Lineage Caps; Session 4

**Done when:** knowledge/consolidation.py (gate stack, deterministic pre-dedup, injectable-metric clustering, lineage caps/reflection_count<3, differential refresh, phantom hubs, mutation-cadenced lint) + knowledge-health/knowledge-consolidate/knowledge-gaps providers + 3 bundled maintenance templates + config knobs; runs over a 100+-item store with health-before-lint ordering

### `WF2KNO-5` — Contradiction pass + retrieval polish: contradiction.py, session_brief.py, fenced_sources, conflict UI

**Status:** done

§3.2 Contradiction Flagging at Persist Time, §5.2 Provenance/Citation/Fencing, §5.3 Push-Based Session Brief; Session 5

**Done when:** knowledge/contradiction.py + session_brief.py: persist-time deterministic conflict pass with typed-edge writes, fenced_sources binding filter, read-only conflict/relations routes, ConflictPanel Knowledge view, config knobs; a seeded same-subject+predicate conflict is flagged at persist with both claims retained under the precedence ladder; Session Brief injected into runs; split-brain knowledge_db_path fix; validated live

### `WF2KNO-6` — Bundled template slate + long-run validation (4 provider-buildable templates)

**Status:** done

§7 Bundled Template Slate (knowledge-synthesis, rich-ingest, thesis-tracker, publish-article), §8 Success Criteria validation; Session 6

**Done when:** knowledge-synthesis, rich-ingest, thesis-tracker, publish-article shipped in the Store; test_knowledge_longrun_validation.py mutation-tested (sibling window, seen-set marking, idempotency at 50 calls); rich-ingest provider set asserted exactly {knowledge-persist, create-task}; publish-article draft→dual-review→gate→persist appending a kind:decision validated live against the real store

### `WF2KNO-7` — render_report action provider (deferred/optional last slice)

**Status:** done

§6.2 render_report Action Provider (KNOW-R15 — deliberately last, deferrable); Implementation Effort Session 7 (optional)

**Done when:** render_report ships in apps/native/knowledge-actions/ providers[] and ALLOWED_HOOK_PROVIDERS; declarative spec (markdown/table-ops/Mermaid xychart) → sanitized self-contained HTML/SVG; spec text stored as the versioned record in artifacts/registry with rendered output as a derived export; periodic synthesizer regenerates visuals from updated data

### `WF2KNO-8` — Route gap-healing drafts to the LEARNING-FLYWHEEL proposal queue

**Status:** done

§3.3 schema.md proposals / §3.4 gap-healing proposal routing (propose-don't-write to the flywheel queue)

**Done when:** gap-healing / schema-edit drafts enqueue via learning.proposals.enqueue under a knowledge-draft Kind; the session-37 workaround (persisting a TTL'd probe tagged 'proposal') is removed — blocked because learning.proposals.Kind is a closed 6-value enum with no knowledge-draft kind

### `WF2KNO-9` — Provider-blocked template slate: market-monitor, trending-repo-digest, dual-sink watcher, paper-ingest

**Status:** todo

§6.1 Monitoring Template, §7.1 slate items 2 (market-monitor), 3 (trending-repo-digest), 4 (dual-sink variant), 9 (paper-ingest)

**Done when:** the four monitor/ingest templates ship and dispatch a real HTTP-egress action node at run time without ALLOWED_HOOK_PROVIDERS/run-time failure — blocked because net.fetch is a library egress function, not a dispatchable action provider

### `WF2KNO-10` — Wire the model-tier (fast-model) contradiction pass to a live model via a stage node

**Status:** done

§3.2 Contradiction check on persist (fast-model conflict pass + background typed-edge inference); Session 5 residual (the plan header's sole named residual)

**Done when:** the built-and-tested fast-model conflict pass runs against a live model through a stage node (not inside the action provider, per the action/stage split), and background typed-edge inference beyond `contradicts` is wired; observed end-to-end

### `WF2KNO-11` — Synthesis legibility: staleness banner, propose-then-accept update, parsed per-marker citations

**Status:** todo

Capability-gap amendment (2026-08-19)

**Done when:** a synthesized artifact whose sources have changed renders a banner naming the COUNT of new source items and offering one regenerate action, rather than silently serving a stale document; an update runs as a proposal the owner inspects and accepts or dismisses so generated prose never overwrites human writing, and the plain update path is expressed as propose-plus-auto-accept rather than a second code path (clean break, one updater); citation markers are PARSED OUT OF THE MODEL'S OUTPUT and stored relationally per marker (marker index, source item, chunk index, excerpt), keyed on the marker NUMBER rather than order of appearance so a marker emitted out of order does not renumber; a marker with no registered source is dropped with a warning rather than reaching the reader as a dangling reference; markers already present inside a quoted source are stripped before that text re-enters a prompt so every number resolves against the current turn's sources; the require-citations control is no longer satisfiable by storing the whole retrieved set, and a test proves an output that cites nothing fails it — today 'which source supports this sentence' is unanswerable

### `WF2KNO-12` — Scheduled research reports: source-scope x context-scope x citation-policy, with hardened schedule semantics

**Status:** todo

Capability-gap amendment (2026-08-19)

**Done when:** a report definition carries a research prompt, a schedule with a timezone, a SOURCE scope (which items count as new material, by tag subtree and time window), a SEPARATE CONTEXT scope (what may be searched while writing) and a CITATION POLICY (cite-source-only vs allow-citing-context) — that triple is what makes a contradiction scan and an open-question tracker expressible as configuration instead of code; each run writes one finding as an ordinary knowledge item so it inherits search, graph and synthesis for free, excluded from the default item list by kind; the agent loop is bounded by an iteration cap and told not to invent citation markers; schedule semantics are hardened against four named failures — an unparseable expression fails CLOSED so a malformed report cannot wedge the runner, a never-run report anchors its first fire on its creation time rather than the epoch, a missed window fires ONCE rather than once per window skipped, and a failed run records its error WITHOUT advancing the last-run timestamp; the due watermark is taken at scope-resolution time, not completion time, so an item captured mid-run is not skipped forever; a manual run is idempotent against an in-flight scheduled fire; an empty scope is a terminal success with no model call and no item written; delivery rides the existing digest and notification path rather than adding a second one
