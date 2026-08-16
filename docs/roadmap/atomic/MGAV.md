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
| `MGAV-5` | ✅ | Memory formation: Extract→Gather→Decide consolidation + holder attribution + Louvain topology | `MGAV-1`, `MGAV-2` | _consolidate_locked restructured to Extract→Gather→Decide with one added structured call producing ADD/UPDATE/SUPERSEDE/NOOP mapped to the v4 supersession chain + WAL (no physical deletes, keep-both conflict flag surfaced in lint); optional holder column + weight caps + claim.* prefix with attributed fact-block rendering and holder precedence in Decide; deterministic seeded Louvain post-step writes community into mem_link_stats and materializes a <=400-char topology block gated by graph_topology_in_context (default off) |
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

**Status:** done

§4.1 two-phase Extract→Decide consolidation, §4.2 takes/claims holder attribution, §2.4 community topology (Louvain) + topology block; Session 3

**Done when:** _consolidate_locked restructured to Extract→Gather→Decide with one added structured call producing ADD/UPDATE/SUPERSEDE/NOOP mapped to the v4 supersession chain + WAL (no physical deletes, keep-both conflict flag surfaced in lint); optional holder column + weight caps + claim.* prefix with attributed fact-block rendering and holder precedence in Decide; deterministic seeded Louvain post-step writes community into mem_link_stats and materializes a <=400-char topology block gated by graph_topology_in_context (default off)

**DONE (2026-08-15):** three modules carry the three clauses, and each one is enforced in
code rather than trusted to the model.

`memory_formation.py` owns Extract→Gather→Decide. Extract is the existing consolidation
prompt, untouched. **Gather is fully deterministic** — same key, shared dotted key
namespace, keyword overlap, then the §2.1 graph arm — and costs zero model calls.
**Decide is ONE structured call for the whole batch**, and only when a candidate actually
has an overlap: with nothing to collide with every verdict is `ADD` by construction, so
the common consolidation still costs exactly one call and the "one extra cheap call" claim
stays true instead of becoming "one extra call, always". Verdicts map to the v4 chain —
`UPDATE` = same-key put, `SUPERSEDE` = write new **then** `supersede_semantic` (that order
matters: superseding first leaves a window where neither value is live) — and **nothing
physically deletes**. The superseded row keeps `superseded_by` + `invalidated_at`, stays
readable, and lands in the `memory_events` WAL. Every failure path (no snippet, no model,
garbled verdicts, a Gather exception) degrades to "ADD everything", i.e. exactly the
pre-MGAV-5 behavior: **adjudication is optional, the user's facts are not.**

**Unsure means keep BOTH, visibly.** An unsure `SUPERSEDE`/cross-key `UPDATE` writes the
new row, retires nothing, and records the contradiction twice — a `references` edge with
`provenance='conflict'` (what the lint reads, what the graph UI can draw) and a
`conflict_keep_both` WAL event (what survives on a store whose graph is off). The lint's
new `keep_both` check is deliberately **NOT** gated on `graph_enabled`: the flag is a
data-safety notice about semantic rows, and hiding it behind an unrelated toggle would
recreate the exact failure — an invisible contradiction — that keeping both exists to
prevent. A conflict the user resolves stops being flagged, so the flag never outlives its
cause.

`memory_holder.py` + migration **v10** own the attribution axis: `holder` (`''` = plain
fact / `user` / `assistant` / `person:<entity_id>` / `external`) and `weight`, quantized
to 0.05 and **clamped** to its class ceiling (self-report ≤0.75, secondhand ≤0.55).
Clamped, not rejected — dropping a memory the user gave us because a model over-claimed
its strength would be the worse bug. A plain fact is never re-weighted (`holder=''` keeps
weight 1.0 and renders byte-identically), and `holder=None` on a write means "don't
touch", so a plain rewrite cannot silently convert a recorded claim into asserted fact.
`claim.*` joined `_BUILTIN_PREFIXES` because kind inference here is key-prefix based (no
kind column — the recon invariant), so the prefix IS the discriminator between "Alex says
the deploy slips" and "the deploy slips". Both fact-block paths (`get_l1_manifest` and
`get_semantic_context`) render `[<who>, weight <n>]` plus a fence clause explaining it —
an attribution with no strength and no fence reads as endorsement. **Precedence is
enforced at the DECISION point, not in ranking:** a lower-authority claim cannot supersede
a higher-authority one at all (an `external` rumour vs something the user said becomes a
flagged keep-both), and the reverse direction still supersedes, which is the vacuity guard
on that rule.

`memory_topology.py` owns the seeded Louvain post-step on the existing consolidation
maintenance cadence (no new loop), writing `community` into `mem_link_stats` and
materializing a ≤400-char block gated by `memory.graph_topology_in_context` (default off).
Injection lands in `get_context`'s L1-manifest region, which gives "new sessions only" for
free — that method is only reached from `build_session_context`. Determinism rests on three
things: sorted iteration everywhere (never set/dict order), a per-call `random.Random(42)`
for the one randomized step, and **canonical community numbering** by (size desc, smallest
member) so two runs that find the same partition also label it the same. Louvain's
aggregation carries intra-community weight as a **self-loop** — dropping it is the classic
bug that merges every level into one community and then looks like a graph with no
structure rather than like a defect.

**Falsified, not assumed** (each mutation reverted): making `SUPERSEDE` hard-delete reds
`test_supersede_keeps_the_old_row_readable` ("the superseded row was PHYSICALLY DELETED")
and `test_row_count_never_drops_across_a_formation_pass`; varying the Louvain seed per run
reds both determinism tests, including the cross-**process** one ("communities depend on
the interpreter's hash seed"); deleting the precedence check reds "an outside rumour
retired what the user said"; making `_lint_conflicts` a no-op reds "an undecided
contradiction was invisible in the lint". The determinism tests run on a fixture graph
found by sweeping random graphs for one whose partition genuinely depends on visit order —
a clean two-cluster fixture would have passed with the seed removed entirely, i.e. proved
nothing.

**Known tension, left alone deliberately:** `memory_lint`'s pre-existing
`_SUPERSEDED_RETENTION_DAYS` auto-fix physically DELETEs rows superseded more than 90 days
ago. That is a shipped, bounded retention policy with its own test, not part of the
supersede verdict path, so this atom did not touch it — but "the superseded row stays
readable" is true for 90 days, not forever. Worth an owner call in `MGAV-6`/lifecycle work.

FE controls for the two new toggles are `MGAV-9`'s declared scope ("settings tab exposes …
topology toggle via `_EDITABLE_CONFIG` PATCH"); both are already in the PATCH allowlist and
the `/api/memory/settings` payload, so that atom only has to render them.

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

