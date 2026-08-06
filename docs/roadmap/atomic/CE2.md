# CONTEXT-ECONOMY — atomic plans

**Source plan:** [`CONTEXT-ECONOMY`](../plans/CONTEXT-ECONOMY.md)  
**Code:** `CE2`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CE2-1` | ✅ | Retrieval hardening + per-model savings accounting + subagent projection | — | SC#1 (content-hash r_<sha> ids dedupe identical raw to one file; fetch_slice line_start/line_end) + SC#2 (buried subagent finding recovered via typed digest + raw_ref through project_and_retain) + SC#4 (Settings→Tools savings card from tokenjuice_savings.json, GET /api/tools/savings) met; log [2026-07-26][S1] DONE, gate green (7986 passed) |
| `CE2-2` | ✅ | Type-routed compressors (JSON crusher, AST code) + three-layer rule overlay + prose compressor | `CE2-1` | SC#3 met: .personalclaw/projection_rules.json reroutes to the log projector beating user layer + sniff, bad regex skipped+logged never raising; 829K JSON crushes to ~379 chars with raw_ref; code content type + builtin rule pack (rules_builtin.json) ship; prose compressor locked out of sync dispatch by test; log [2026-07-26][S2] DONE, gate green (8083 passed) |
| `CE2-3` | ✅ | Continuous background compression service + shared topic segmenter + prefix-stability locks | `CE2-2` | SC#5 (7-day-idle session topic-compressed at consolidation tick, incognito untouched, disable stops within a tick) + SC#6 (byte-stability: same raw → identical previews, at-rest only) met; ToolsConfig.bg_compress_enabled/bg_compress_idle_days wired; log [2026-07-27][S3] DONE, gate green (full suite) |
| `CE2-4` | ✅ | Dynamic tool-group activation core: ToolGroup derivation, reset_tools meta-tool, assembly-seam group filter, per-surface defaults | — | SC#7 (background session carries only core+memory; stub + tool_search names inactive group; one reset_tools activates it with instructions) + SC#8 (reset_tools(groups={}) leaves always_on core present, all tools still dispatchable) + SC#10 (groups-off byte-identical schema, snapshot-locked) met; measured 56% smaller background surface; groups_enabled default False; log [2026-07-27][S4] DONE, gate green (8235 passed) |
| `CE2-5` | ✅ | Declaration surfaces: per-capability gating, groups API endpoints, config-wiring completion + Tools-page FE | `CE2-4` | SC#9 met: app-contributed tool provider appears as its own group with zero app-side code, unbound capability group (subagents with no model) never renders + reset_tools refuses it; flag PATCH round-trips + persists; perf regression from unconditional offerability probe found & fixed (probe only when grouping active); log [2026-07-28][S5] DONE, gate green (8243 passed) |
| `CE2-6` | ✅ | Codebase graph: tree-sitter indexer + SQLite store + code_map tools + SDLC planning + @-mention centrality | — | As-a-user validation: both tools in GET /api/tools (group=workflows), code_map_overview returns real repo shape, @-mention search centrality-ranked; measured 3.9s full index / 37ms incremental on 1,467-file repo; fail-soft verified with tree_sitter imports forced to fail; centrality distinct-file + name-rarity fix locked by test; log [2026-07-28][S6] DONE 'completes CONTEXT-ECONOMY', gate green (8575 passed) |

## Atom scopes

### `CE2-1` — Retrieval hardening + per-model savings accounting + subagent projection

**Status:** done

§1 (§1.1 content-hash result-id markers, §1.2 tool_result_get line addressing, §1.3 per-model savings accounting) + §2.5a subagent transcripts/results; Session 1

**Done when:** SC#1 (content-hash r_<sha> ids dedupe identical raw to one file; fetch_slice line_start/line_end) + SC#2 (buried subagent finding recovered via typed digest + raw_ref through project_and_retain) + SC#4 (Settings→Tools savings card from tokenjuice_savings.json, GET /api/tools/savings) met; log [2026-07-26][S1] DONE, gate green (7986 passed)

### `CE2-2` — Type-routed compressors (JSON crusher, AST code) + three-layer rule overlay + prose compressor

**Status:** done

§2.1 JSON crusher, §2.2 AST-aware code content type, §2.3 three-layer rule overlay (builtin/user/project + rule ops v2), §2.4 background-only prose-model compressor; Session 2

**Done when:** SC#3 met: .personalclaw/projection_rules.json reroutes to the log projector beating user layer + sniff, bad regex skipped+logged never raising; 829K JSON crushes to ~379 chars with raw_ref; code content type + builtin rule pack (rules_builtin.json) ship; prose compressor locked out of sync dispatch by test; log [2026-07-26][S2] DONE, gate green (8083 passed)

### `CE2-3` — Continuous background compression service + shared topic segmenter + prefix-stability locks

**Status:** done

§3 prefix-stability KV-cache contract (invariants 1-2 unit-locked), §4 continuous background compression service (context_segmentation.py shared with LOOP-R13, attention-weighted per-topic compression, consolidation-cadence wiring, incognito/temporary skip, archive-before-rewrite, kill switch + idle-days knob); Session 3

**Done when:** SC#5 (7-day-idle session topic-compressed at consolidation tick, incognito untouched, disable stops within a tick) + SC#6 (byte-stability: same raw → identical previews, at-rest only) met; ToolsConfig.bg_compress_enabled/bg_compress_idle_days wired; log [2026-07-27][S3] DONE, gate green (full suite)

### `CE2-4` — Dynamic tool-group activation core: ToolGroup derivation, reset_tools meta-tool, assembly-seam group filter, per-surface defaults

**Status:** done

§5.1 group model (one group per provider, always-on core), §5.2 activation lifecycle + reset_tools final-state meta-tool, §5.3 assembly seam (group filter + refresh_toolset, fail-open triad, tool_search cross-group discovery, stable-sort serialization), §5.4 per-surface defaults + tool_groups kwarg; Session 4

**Done when:** SC#7 (background session carries only core+memory; stub + tool_search names inactive group; one reset_tools activates it with instructions) + SC#8 (reset_tools(groups={}) leaves always_on core present, all tools still dispatchable) + SC#10 (groups-off byte-identical schema, snapshot-locked) met; measured 56% smaller background surface; groups_enabled default False; log [2026-07-27][S4] DONE, gate green (8235 passed)

### `CE2-5` — Declaration surfaces: per-capability gating, groups API endpoints, config-wiring completion + Tools-page FE

**Status:** done

§5.4 declaration surfaces, §5.5 per-capability gating (can_resolve_use_case probe, offerable()), §6 four-point config wiring completion, GET /api/tools/groups, FE ToolGroupsTile + flag switch + per-surface panel; Session 5

**Done when:** SC#9 met: app-contributed tool provider appears as its own group with zero app-side code, unbound capability group (subagents with no model) never renders + reset_tools refuses it; flag PATCH round-trips + persists; perf regression from unconditional offerability probe found & fixed (probe only when grouping active); log [2026-07-28][S5] DONE, gate green (8243 passed)

### `CE2-6` — Codebase graph: tree-sitter indexer + SQLite store + code_map tools + SDLC planning + @-mention centrality

**Status:** done

§5.5 Codebase Graph — codegraph/ package (parse.py tree-sitter extraction, index.py per-workspace SQLite with mtime+size invalidation), code_map + code_map_overview tools registered under the workflows group, SDLC planning-brief module summary (budget-capped ~2K tokens), @-mention centrality ranking; tree-sitter + tree-sitter-language-pack as core deps; Session 6

**Done when:** As-a-user validation: both tools in GET /api/tools (group=workflows), code_map_overview returns real repo shape, @-mention search centrality-ranked; measured 3.9s full index / 37ms incremental on 1,467-file repo; fail-soft verified with tree_sitter imports forced to fail; centrality distinct-file + name-rarity fix locked by test; log [2026-07-28][S6] DONE 'completes CONTEXT-ECONOMY', gate green (8575 passed)

