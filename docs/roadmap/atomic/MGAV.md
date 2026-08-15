# MEMORY-GRAPH-AND-VAULT — atomic plans

**Source plan:** [`MEMORY-GRAPH-AND-VAULT`](../plans/MEMORY-GRAPH-AND-VAULT.md)  
**Code:** `MGAV`  
**Source status:** in_progress

9 atoms: 4 shipped (S1 graph model+linker, S2a recall arm, S2b push reflex, §1.3 knowledge pre-pass PR #118), 5 remaining (S3 formation+Louvain, S4 vault two-way, memory citations, S5 slots, S5 FE+config). All deps intra-plan; no cross-plan gate.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MGAV-1` | ✅ | Graph data model + zero-LLM write-time linker (migration v7, alias index, backfill, lint) | — | Migration v7 creates mem_entities/mem_links/mem_link_stats (+mem_entity_proposals notability tally); token-trie linker runs on semantic/episodic/lesson write paths emitting typed mem_links with WAL undo; alias index seeded from facts+knowledge+user edits; idempotent backfill; orphans/proposed-entity lint in Health tab; graph_enabled config four-point wired; entity + rebuild API routes live |
| `MGAV-2` | ✅ | Graph recall arm + backlink boost + evidence tags in hybrid retrieval | `MGAV-1` | Third retrieval arm resolves query entities via the same matcher, admits+boosts backlinked records with a floor above stopword noise, graph hits increment recall_count/visit_count into the existing promote_by_heat math, recall_evidence tags each hit, and graph-off/empty degrades to today's vector+keyword behavior |
| `MGAV-3` | ✅ | Ambient push-context reflex + volunteer-events table + per-arm stats (migration v8) | `MGAV-2` | Per-turn deterministic reflex volunteers <=3 (hard 5) confidence-gated semantic records via context_engine on every turn (not is_new_session-gated), never injects knowledge (chip only), migration v8 mem_volunteer_events logs entity/arm/confidence/ref with recall_at_volunteer baseline, temporary sessions get nothing and incognito injects-without-logging, push_context + push_min_confidence wired four-point + PUT allowlist, Health tab per-arm precision (min-N gated) |
| `MGAV-4` | ✅ (##118) | Knowledge-side deterministic alias pre-pass in the ingestion entities stage | `MGAV-1` | link_known_entities runs before the LLM EntityExtractor and unconditionally (incl. pool is None), using the same AliasIndex as memory, adding INSERT-OR-IGNORE mentions for already-known entities; snapshots (name,type,context) across clear_item_entities so a model-present ingest keeps deterministic links; bounded by MAX_INDEXED_ENTITIES/MAX_MENTIONS_PER_ITEM; validated no-model ingest links a declared-alias mention |
| `MGAV-5` | ⬜ | Memory formation: Extract→Gather→Decide consolidation + holder attribution + Louvain topology | `MGAV-1`, `MGAV-2` | _consolidate_locked restructured to Extract→Gather→Decide with one added structured call producing ADD/UPDATE/SUPERSEDE/NOOP mapped to the v4 supersession chain + WAL (no physical deletes, keep-both conflict flag surfaced in lint); optional holder column + weight caps + claim.* prefix with attributed fact-block rendering and holder precedence in Decide; deterministic seeded Louvain post-step writes community into mem_link_stats and materializes a <=400-char topology block gated by graph_topology_in_context (default off) |
| `MGAV-6` | ⬜ | Two-way readable vault: mode config, wikilink projection, sync pass, vault lints, raw capture, seeding, snapshot | `MGAV-1` | memory.vault_mode off\|mirror\|two_way (back-reads vault_enabled); pages carry frontmatter+source_hash and [[wikilinks]] generated from mem_links with compiled-truth+append-only-timeline shape; on-cadence/on-demand POST /api/memory/vault/sync parses hash-changed pages back through the MemoryService write path (S5 scan, WAL, edit-wins, sync-conflict flag, no data loss); vault lints (backlink symmetry, stale hash, broken links, orphans); raw/ sweep routes files to the knowledge ingest queue only; starter seeding writes only missing/pristine files; snapshot.py adds the vault dir |
| `MGAV-7` | ⬜ | Memory citations in chat + admit-ignorance clause | `MGAV-2` | Memory-backed answers render inline [Memory N] chips in ui/Markdown.tsx deep-linking to the inspect tab (and vault path when present) using recall_with_provenance evidence arms; the system prompt instructs citing injected memories by index and adds the admit-ignorance clause so empty recall yields an explicit 'not in memory' rather than confabulation; no new tool |
| `MGAV-8` | ✅ | Memory slots: bounded always-injected registers + reflection append hook + self-model | — | slot.<name> prefix added to _BUILTIN_PREFIXES with per-slot size cap enforced at put (over-cap append fails loudly as a trim proposal); lazy built-ins (persona/preferences/pending_items/self_notes/glossary(workspace)/self_model); one bounded Slots block injected in build_session_context adjacent to persona/USER PROFILE; after_turn_review append-only hook (WAL, undo, human-tombstone respected); self-model promotes a line to a behavioral principle only after >=3 reinforcements |
| `MGAV-9` | ⬜ | FE surfaces: MemoryPanel tabs, MemoryGraph viz + HTML export, full config wiring + as-a-user validation | `MGAV-5`, `MGAV-6`, `MGAV-8` | studio tab gains Slots editor + entity browser + proposed-entity accept queue; inspect tab shows per-record backlinks + evidence tags (citation deep-link target); settings tab exposes vault mode/path, push toggle+min-confidence, slot caps, topology toggle via _EDITABLE_CONFIG PATCH; MemoryGraph.tsx renders entities colored by Louvain community with typed/provenance/min-confidence filtering + side drawer, plus GET /api/memory/graph/export self-contained HTML; all remaining MemoryConfig fields survive the four-point round trip (schema tests) and toggle live; end-to-end write→link→recall→volunteer→edit-vault→undo validation sweep passes with graph_enabled:false / foreign provider degrading cleanly |

## Atom scopes

### `MGAV-1` — Graph data model + zero-LLM write-time linker (migration v7, alias index, backfill, lint)

**Status:** done

§1 Typed Entity Graph, §1.1 Zero-LLM write-time linking, §1.2 Edge vocabulary+provenance, §1.3 alias index, §2.3 Orphans lint; Session 1

**Done when:** Migration v7 creates mem_entities/mem_links/mem_link_stats (+mem_entity_proposals notability tally); token-trie linker runs on semantic/episodic/lesson write paths emitting typed mem_links with WAL undo; alias index seeded from facts+knowledge+user edits; idempotent backfill; orphans/proposed-entity lint in Health tab; graph_enabled config four-point wired; entity + rebuild API routes live

### `MGAV-2` — Graph recall arm + backlink boost + evidence tags in hybrid retrieval

**Status:** done

§2.1 graph arm in memory retrieval, §2.2 retrieval provenance tags; Session 2 (partial)

**Done when:** Third retrieval arm resolves query entities via the same matcher, admits+boosts backlinked records with a floor above stopword noise, graph hits increment recall_count/visit_count into the existing promote_by_heat math, recall_evidence tags each hit, and graph-off/empty degrades to today's vector+keyword behavior

### `MGAV-3` — Ambient push-context reflex + volunteer-events table + per-arm stats (migration v8)

**Status:** done

§3 Ambient Push-Context Reflex; Session 2 (remainder)

**Done when:** Per-turn deterministic reflex volunteers <=3 (hard 5) confidence-gated semantic records via context_engine on every turn (not is_new_session-gated), never injects knowledge (chip only), migration v8 mem_volunteer_events logs entity/arm/confidence/ref with recall_at_volunteer baseline, temporary sessions get nothing and incognito injects-without-logging, push_context + push_min_confidence wired four-point + PUT allowlist, Health tab per-arm precision (min-N gated)

### `MGAV-4` — Knowledge-side deterministic alias pre-pass in the ingestion entities stage

**Status:** done (PR ##118)

§1.3 the alias index — knowledge-side pre-pass (knowledge/alias_prepass.py, knowledge/pipeline/runner.py)

**Done when:** link_known_entities runs before the LLM EntityExtractor and unconditionally (incl. pool is None), using the same AliasIndex as memory, adding INSERT-OR-IGNORE mentions for already-known entities; snapshots (name,type,context) across clear_item_entities so a model-present ingest keeps deterministic links; bounded by MAX_INDEXED_ENTITIES/MAX_MENTIONS_PER_ITEM; validated no-model ingest links a declared-alias mention

### `MGAV-5` — Memory formation: Extract→Gather→Decide consolidation + holder attribution + Louvain topology

**Status:** todo

§4.1 two-phase Extract→Decide consolidation, §4.2 takes/claims holder attribution, §2.4 community topology (Louvain) + topology block; Session 3

**Done when:** _consolidate_locked restructured to Extract→Gather→Decide with one added structured call producing ADD/UPDATE/SUPERSEDE/NOOP mapped to the v4 supersession chain + WAL (no physical deletes, keep-both conflict flag surfaced in lint); optional holder column + weight caps + claim.* prefix with attributed fact-block rendering and holder precedence in Decide; deterministic seeded Louvain post-step writes community into mem_link_stats and materializes a <=400-char topology block gated by graph_topology_in_context (default off)

### `MGAV-6` — Two-way readable vault: mode config, wikilink projection, sync pass, vault lints, raw capture, seeding, snapshot

**Status:** todo

§5.1 mirror→two-way projection, §5.2 edits flow back, §5.3 structure conventions+lints, §5.5 interop (symlink, raw/ capture, starter seeding, static export, git/snapshot); Session 4

**Done when:** memory.vault_mode off|mirror|two_way (back-reads vault_enabled); pages carry frontmatter+source_hash and [[wikilinks]] generated from mem_links with compiled-truth+append-only-timeline shape; on-cadence/on-demand POST /api/memory/vault/sync parses hash-changed pages back through the MemoryService write path (S5 scan, WAL, edit-wins, sync-conflict flag, no data loss); vault lints (backlink symmetry, stale hash, broken links, orphans); raw/ sweep routes files to the knowledge ingest queue only; starter seeding writes only missing/pristine files; snapshot.py adds the vault dir

### `MGAV-7` — Memory citations in chat + admit-ignorance clause

**Status:** todo

§5.4 memory citations in chat + admit-ignorance; Session 4

**Done when:** Memory-backed answers render inline [Memory N] chips in ui/Markdown.tsx deep-linking to the inspect tab (and vault path when present) using recall_with_provenance evidence arms; the system prompt instructs citing injected memories by index and adds the admit-ignorance clause so empty recall yields an explicit 'not in memory' rather than confabulation; no new tool

### `MGAV-8` — Memory slots: bounded always-injected registers + reflection append hook + self-model

**Status:** done

§6 Memory Slots, §6.1 the self-model slot; Session 5 (backend + slot primitive)

**Done when:** slot.<name> prefix added to _BUILTIN_PREFIXES with per-slot size cap enforced at put (over-cap append fails loudly as a trim proposal); lazy built-ins (persona/preferences/pending_items/self_notes/glossary(workspace)/self_model); one bounded Slots block injected in build_session_context adjacent to persona/USER PROFILE; after_turn_review append-only hook (WAL, undo, human-tombstone respected); self-model promotes a line to a behavioral principle only after >=3 reinforcements

**DONE (2026-08-15):** `memory_slots.py` owns the primitive: `slot.*` joined `_BUILTIN_PREFIXES`
(built-in, NOT via `memory.semantic_keys` — a default install must be able to hold a persona), and
the per-slot cap is enforced in `validate_semantic` under a new `SemanticRejectCode.SLOT_CAP`, so a
direct `set_semantic("slot.x", <huge>)` from a route or tool cannot route around the ceiling the
always-injected block depends on. Over-cap **raises** `SlotCapExceeded` carrying a `TrimProposal`
(cap, current/incoming chars, `over_by`, and only as many oldest-live `drop_candidates` as actually
free room) — never a truncation, never a dropped write; the code is auditable, because a refused
memory the user tried to keep is how "it forgot what I told it" becomes unexplainable. The six
built-ins are **descriptors, not rows**: a fresh store writes zero `slot.*` rows and
`render_slots_block` returns `""`, asserted by proving row ABSENCE (an eager six-empty-row
implementation would still make `load()` return `[]`). `ContextBuilder._slots_block` injects ONE
block beside persona/USER PROFILE, hard-sliced at `SLOTS_BLOCK_MAX_CHARS` unconditionally so even
hand-edited over-cap rows cannot widen it (`over_cap()` logs those at WARNING — that is the only
way the state arises, since every write path refuses it). `after_turn_review.capture_slot_lines`
is the append-only hook: existing lines are never rewritten or reordered, writes ride
`set_semantic` so the memory event log + `undo_event` cover them, a repeated observation bumps
`reinforcements` instead of duplicating, and a **human** tombstone is final (an *agent* tombstone
may be re-derived — the guard is scoped to the human, deliberately). `self_model` gained
`MIN_SEEN_BY_FACET = {"principle": 3}` + `min_seen_for()`/`promotable_for()`, and
`plan_promotion` now checks the facet-aware bar; the provisional facets keep the floor of 2 on
purpose. **Closed-enum sweep** (a `.get(kind, default)` would have silently mis-aged slots):
`MemoryKind.SLOT`, `_kind_from_key`, `_DECAY_PROFILES`, `decay.KIND_MULTIPLIERS` (`"slot": 0.3` —
the slowest class, but not exempt), `iter_records`'s `sem_kinds`, and `_NON_FACT_KEY_CLAUSE` (a
slot injects via its OWN block; left in the fact block it would double-charge the budget and read
`slot.self_notes` back as a claim about the user). `test_learning_decay_heat`'s per-kind heat table
gained the measured `MemoryKind.SLOT: 0.441351`. 21 tests in `tests/test_memory_slots.py`.
Falsified four ways: silent truncation → `test_over_cap_append_refuses_and_proposes_a_trim` +
`test_hook_surfaces_a_trim_proposal_instead_of_dropping`; no block ceiling →
`test_slots_block_is_hard_bounded_with_oversized_input`; tombstone resurrection →
`test_hook_never_resurrects_a_human_tombstone`; threshold 3→2 →
`test_principle_needs_three_reinforcements_two_is_not_enough`. **Deferred to `MGAV-9`** (its
`done_when`, not this one): the Slots editor UI and the `slot caps` `_EDITABLE_CONFIG` exposure —
caps are code constants here, which is why no `MemoryConfig` field or config round-trip changed.

### `MGAV-9` — FE surfaces: MemoryPanel tabs, MemoryGraph viz + HTML export, full config wiring + as-a-user validation

**Status:** todo

§7.1 MemoryPanel extensions, §7.2 graph visualization + export, Provider & Config Plug-in Map (FOUR-point wiring); Session 5 (FE + wiring)

**Done when:** studio tab gains Slots editor + entity browser + proposed-entity accept queue; inspect tab shows per-record backlinks + evidence tags (citation deep-link target); settings tab exposes vault mode/path, push toggle+min-confidence, slot caps, topology toggle via _EDITABLE_CONFIG PATCH; MemoryGraph.tsx renders entities colored by Louvain community with typed/provenance/min-confidence filtering + side drawer, plus GET /api/memory/graph/export self-contained HTML; all remaining MemoryConfig fields survive the four-point round trip (schema tests) and toggle live; end-to-end write→link→recall→volunteer→edit-vault→undo validation sweep passes with graph_enabled:false / foreign provider degrading cleanly

