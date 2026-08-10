# WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS — atomic plans

**Source plan:** [`WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS`](../plans/WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS.md)  
**Code:** `WF2KNO`  
**Source status:** done

Decomposed WF2KNO into 10 atoms: 6 done (sessions 34-39, all CODE DONE on main), 1 deferred (render_report), and 3 residual/blocked seams (gap-healing→flywheel enum, 4 net.fetch-blocked templates, model-tier conflict-pass wiring). Cross-plan edges: WORKFLOWS-V2 engine (action/wait/loop), LEARNING-FLYWHEEL proposal-queue Kind, and a dispatchable net.fetch action provider.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2KNO-1` | ✅ | Store semantics groundwork: taxonomy, logical identity, claims/relations, migrations, KnowledgeConfig | — | knowledge/semantics.py (10-kind taxonomy, {kind}:{normalized_title} logical identity, content+chunk hashing, 1-∏(1-cᵢ) sub-1.0 confidence, claims/mentions with invalid_at supersession, 5-verb item relations) + store.py additive migrations (kind/logical_key/last_verified/expires_at cols + item_relations table + logical-key index in _migrate) + KnowledgeConfig wired through all 4 config points + schema.md scaffold; tests green |
| `WF2KNO-2` | ✅ | The provider pair: knowledge_persist + knowledge_retrieve app, allowlist, native-provider retrieval seam | `WF2KNO-1`, `EXT:WORKFLOWS-V2:action-node dispatcher + wait node (Slice 1) and ALLOWED_HOOK_PROVIDERS seam` | apps/native/knowledge-actions/ ships both providers (idempotent upsert/append_evidence/ops, budgets, citation enforcement, degradation-ladder retrieve with create_safety/freshness/detail caps); both in ALLOWED_HOOK_PROVIDERS + action registry; NativeKnowledgeProvider.search() routes retrieval via knowledge_providers registry (search_all first caller; manifest factory repointed); persist→retrieve→confirm template validated live with a proven idempotent no-op on re-run |
| `WF2KNO-3` | ✅ | Long-run engine additions: until_cancelled + reaper, siblings/previous bindings, buffer-seal, adaptive clamp | `EXT:WORKFLOWS-V2:loop/parallel/wait engine + run journal (Slices 0-2)` | workflows/longrun.py adds until_cancelled loop mode + reap_watchers, {{siblings.*}} (window/unseen/significant/full/hygiene) + {{previous.output}} + run-continuity state, buffer-seal wait condition, adaptive-delay clamp, journal ledger kinds, validators; a watcher run completes on accompanied-work-complete instead of hanging (validated live); 3 pre-existing loop-path defects fixed |
| `WF2KNO-4` | ✅ | Consolidation + maintenance lifecycle: consolidation.py, health/consolidate/gaps providers + 3 templates | `WF2KNO-1`, `WF2KNO-2` | knowledge/consolidation.py (gate stack, deterministic pre-dedup, injectable-metric clustering, lineage caps/reflection_count<3, differential refresh, phantom hubs, mutation-cadenced lint) + knowledge-health/knowledge-consolidate/knowledge-gaps providers + 3 bundled maintenance templates + config knobs; runs over a 100+-item store with health-before-lint ordering |
| `WF2KNO-5` | ✅ | Contradiction pass + retrieval polish: contradiction.py, session_brief.py, fenced_sources, conflict UI | `WF2KNO-1`, `WF2KNO-2` | knowledge/contradiction.py + session_brief.py: persist-time deterministic conflict pass with typed-edge writes, fenced_sources binding filter, read-only conflict/relations routes, ConflictPanel Knowledge view, config knobs; a seeded same-subject+predicate conflict is flagged at persist with both claims retained under the precedence ladder; Session Brief injected into runs; split-brain knowledge_db_path fix; validated live |
| `WF2KNO-6` | ✅ | Bundled template slate + long-run validation (4 provider-buildable templates) | `WF2KNO-2`, `WF2KNO-3`, `WF2KNO-5` | knowledge-synthesis, rich-ingest, thesis-tracker, publish-article shipped in the Store; test_knowledge_longrun_validation.py mutation-tested (sibling window, seen-set marking, idempotency at 50 calls); rich-ingest provider set asserted exactly {knowledge-persist, create-task}; publish-article draft→dual-review→gate→persist appending a kind:decision validated live against the real store |
| `WF2KNO-7` | ⬜ | render_report action provider (deferred/optional last slice) | `WF2KNO-2` | render_report ships in apps/native/knowledge-actions/ providers[] and ALLOWED_HOOK_PROVIDERS; declarative spec (markdown/table-ops/Mermaid xychart) → sanitized self-contained HTML/SVG; spec text stored as the versioned record in artifacts/registry with rendered output as a derived export; periodic synthesizer regenerates visuals from updated data |
| `WF2KNO-8` | ⬜ | Route gap-healing drafts to the LEARNING-FLYWHEEL proposal queue | `WF2KNO-4`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:extend proposal-queue Kind enum with a knowledge-draft kind` | gap-healing / schema-edit drafts enqueue via learning.proposals.enqueue under a knowledge-draft Kind; the session-37 workaround (persisting a TTL'd probe tagged 'proposal') is removed — blocked because learning.proposals.Kind is a closed 6-value enum with no knowledge-draft kind |
| `WF2KNO-9` | ⬜ | Provider-blocked template slate: market-monitor, trending-repo-digest, dual-sink watcher, paper-ingest | `WF2KNO-6`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:dispatchable net.fetch action provider (HTTP egress chokepoint)` | the four monitor/ingest templates ship and dispatch a real HTTP-egress action node at run time without ALLOWED_HOOK_PROVIDERS/run-time failure — blocked because net.fetch is a library egress function, not a dispatchable action provider |
| `WF2KNO-10` | ✅ | Wire the model-tier (fast-model) contradiction pass to a live model via a stage node | `WF2KNO-5` | the built-and-tested fast-model conflict pass runs against a live model through a stage node (not inside the action provider, per the action/stage split), and background typed-edge inference beyond `contradicts` is wired; observed end-to-end |

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

**Status:** todo

§6.2 render_report Action Provider (KNOW-R15 — deliberately last, deferrable); Implementation Effort Session 7 (optional)

**Done when:** render_report ships in apps/native/knowledge-actions/ providers[] and ALLOWED_HOOK_PROVIDERS; declarative spec (markdown/table-ops/Mermaid xychart) → sanitized self-contained HTML/SVG; spec text stored as the versioned record in artifacts/registry with rendered output as a derived export; periodic synthesizer regenerates visuals from updated data

### `WF2KNO-8` — Route gap-healing drafts to the LEARNING-FLYWHEEL proposal queue

**Status:** todo

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

