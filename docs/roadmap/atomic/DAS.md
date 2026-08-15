# DURABILITY-AND-SYNC — atomic plans

**Source plan:** [`DURABILITY-AND-SYNC`](../plans/DURABILITY-AND-SYNC.md)  
**Code:** `DAS`  
**Source status:** in_progress

Sessions 1-2 (inventory, safe snapshots, shard format+validate, scheduled service+retention+drills, backups FE, and the full inventory-driven merge/restore/export sweep incl. T2-M1..M3) are DONE. Remaining: Session 3 sync core + git/dir transports, its conflict-handling half, Session 4 rsync/s3 transports + encryption, Session 5 time-travel and the §6 portability endpoints + remaining FE.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DAS-1` | ✅ | State inventory + gap closure + safe sqlite snapshots (Session 1) | — | durability/inventory.py is the single StateEntry manifest with audit_home() claims-everything guard; snapshot uses the sqlite backup API for every discovered *.db (live knowledge.db raw-copy hazard fixed, WAL rows preserved); everything/gap stores (tasks, projects, loop, prompts, workflows, agents, apps, entity_settings, sessions, uploads) captured; portability.EXPORT_EXCLUDE is a secret-projection; ~25 tests green |
| `DAS-2` | ✅ | Deterministic shard format + manifest + `backup validate` (Session 2a) | `DAS-1` | durability/shards.py export_shards() writes canonical byte-identical JSONL (sorted keys/rows, year-sharded append streams, part-split, content-addressed blob dir) + manifest.json (schema version, machine_id, per-shard bytes/rows/sha256); `personalclaw backup export [--incremental]` / `backup validate` CLI with non-zero exit; determinism + corruption detection proven; ~28 tests green |
| `DAS-3` | ✅ | Boot-started snapshot service + tiered retention + restore drills + endpoints (Session 2b) | `DAS-2` | durability/service.py boot loop (hourly incremental shard export, nightly tar + durability/retention.py N/M/Y tiers, monthly restore drill with PRAGMA integrity_check + DashboardState.notify warning-on-fail) single_flight-guarded on executor threads; durability.{auto_backup,keep_daily,keep_weekly,keep_monthly,restore_drills} config wired dataclass/_meta/load/to_dict; GET /api/durability/status, GET /api/durability/snapshots, POST /api/durability/run routes; ~49 tests green |
| `DAS-4` | ✅ | Backups settings frontend panel (Session 2c) | `DAS-3` | five durability.* dot-paths added to _EDITABLE_CONFIG PATCH allowlist (snapshot_dir deliberately excluded, pinned by test); Settings → Backups panel consumes status/snapshots/run endpoints; five-point config round trip (incl. False surviving _guard_flag) validated in a real browser on an isolated dev home; retention caps bounded; web typecheck+vitest+build green |
| `DAS-5` | ✅ | Inventory-driven merge/restore/export sweep + restore endpoint (T2-M1..M3, Sessions 2d-2l) | `DAS-1`, `DAS-3` | restore --mode merge dispatches all five StateEntry merge strategies (append_dedup incl. SEL key-gated fail-closed, sqlite_attach_ignore with FTS rebuild, union_by_id, lww_by_updated_at, replace_only skipped loudly); --components everything superset restores 19/19 files 0 lost; --dry-run prints per-entry merge plan matching the real merge; POST /api/durability/restore mirrors CLI with non-empty-home (home_is_populated) auto-detect + gateway-running refusal; create_export_zip/apply_import_zip made inventory-driven (no-secret asserted on bytes); triggers.json added to inventory; success criteria 1-2 and amendment gaps 2-3 met; full pytest -n4 green |
| `DAS-6` | ⬜ | Sync core + git-sync + dir-sync transports (Session 3) | `DAS-2`, `DAS-5` | `sync` added to PROVIDER_TYPES AND a SyncTypeHandler registered in the same commit (test_manifest_types_match_handlers green); sdk/sync.py re-exports SyncTransportProvider/SyncObject/PushResult; sync_transports/registry.py flat register/get; a shard import/restore function added (S2k noted shards were write-only); pull→merge-import→export-union→push cycle with registry.json machine seqs + CAS retry, stale_after_secs staleness window, durable outbox with typed deliverer outcomes + consumed-only cursor, deterministic per-strategy merges + tombstones, derived indexes rebuilt-not-synced; git-sync + dir-sync first-party apps; criterion 4 met (two machines converge over a git repo/synced folder incl. tombstoned delete) |
| `DAS-7` | ✅ | Conflict handling — records + review queue + propose-only LLM merge (Session 3) | `DAS-6` | sha-divergence on the same entity id with both sides edited since the common ancestor (ancestor sha in registry) produces a conflict record in a review queue; a background one_shot_completion(use_case="background") pass drafts a proposed merge + rationale surfaced as needs-review, never auto-applied; local version stays authoritative and both versions persist in the shared store until resolved; memory-domain conflicts route to the memory review surface and knowledge-domain to the knowledge UI; criterion 5 met (offline same-task edit yields a conflict-review item, applies nothing until accepted) |
| `DAS-8` | ⬜ | rsync-sync + s3-sync transports + end-to-end encryption (Session 4) | `DAS-6` | rsync-sync (subprocess over ssh) and s3-sync (signed PUT/GET/LIST via net.fetch with a derived SYNC EgressPolicy through egress_policy_for(), host-pinned + raised max_bytes, never hand-rolled aiohttp) shipped as first-party apps; AES-256-GCM per shard with per-shard HKDF key from passphrase + first-write-wins salt object, routing/metadata fields kept plaintext, plaintext-in-encrypted-store rejected on send AND receive as permanent skip, missing salt = hard setup error; per-transport encryption defaults (ON for s3/dir, OFF for git); secret=True entries excluded before any transport; criteria 7-8 met |
| `DAS-9` | ⬜ | Workspace time-travel — adaptive-debounce git + rollback/revert/preview + panel (Session 5, §5) | — | state-history git repos cover config.json/entity_settings, skills/, the memory markdown tree (workspace/memory, _ext, plan_memory), prompts/+prompt_snippets, projects/<id>/context; adaptive-debounce commits (10s→0, serialized per root) hooked at the atomic_write post-write seam; hourly git commit of the memory tree/shards lands (the deferred §3 piece); rollback (hard reset, prior HEAD preserved in service refs) vs revert (reverse-merge, loud overlap failure) with a mandatory preview; secrets gitignored yet preserved across rollbacks; Settings → Durability → Time Travel panel (timeline, diff preview, rollback/revert, what-changed-while-I-slept filter); criterion 6 met; time-travel never syncs |
| `DAS-10` | ⬜ | §6 DSAR portability endpoints + remaining Durability FE (Session 5, §6) | `DAS-5`, `DAS-6`, `DAS-7` | POST /api/durability/export (full or per-domain shard zip, secret∪derived excluded, §2 manifest inside), POST /api/durability/import {mode:merge\|replace} extending validate_import_zip/apply_import_zip to the full inventory with MANIFEST v3 (v1\|2 back-compat), GET /api/durability/archive + POST /api/durability/archive/{id}/restore with per-domain row counts and last-drill validate status; separate memory vs knowledge export buttons (knowledge includes files/ originals); Settings → Durability panel gains archive browser, sync config via standard /api/providers routes, and the conflict review queue; criteria 9-10 met (memory/knowledge separately exportable + separate conflict surfaces; a third-party type:"sync" app registers/configures/syncs with zero core changes) |

## Atom scopes

### `DAS-1` — State inventory + gap closure + safe sqlite snapshots (Session 1)

**Status:** done

§1 The State Inventory; §8 Disposition (snapshot.py/portability.py ABSORBED); Implementation Order Session 1

**Done when:** durability/inventory.py is the single StateEntry manifest with audit_home() claims-everything guard; snapshot uses the sqlite backup API for every discovered *.db (live knowledge.db raw-copy hazard fixed, WAL rows preserved); everything/gap stores (tasks, projects, loop, prompts, workflows, agents, apps, entity_settings, sessions, uploads) captured; portability.EXPORT_EXCLUDE is a secret-projection; ~25 tests green

### `DAS-2` — Deterministic shard format + manifest + `backup validate` (Session 2a)

**Status:** done

§2 Deterministic Shard Format; Implementation Order Session 2 (format half)

**Done when:** durability/shards.py export_shards() writes canonical byte-identical JSONL (sorted keys/rows, year-sharded append streams, part-split, content-addressed blob dir) + manifest.json (schema version, machine_id, per-shard bytes/rows/sha256); `personalclaw backup export [--incremental]` / `backup validate` CLI with non-zero exit; determinism + corruption detection proven; ~28 tests green

### `DAS-3` — Boot-started snapshot service + tiered retention + restore drills + endpoints (Session 2b)

**Status:** done

§3 Snapshot Service; Provider & Config Plug-in Map (DurabilityConfig); Implementation Order Session 2 (service half)

**Done when:** durability/service.py boot loop (hourly incremental shard export, nightly tar + durability/retention.py N/M/Y tiers, monthly restore drill with PRAGMA integrity_check + DashboardState.notify warning-on-fail) single_flight-guarded on executor threads; durability.{auto_backup,keep_daily,keep_weekly,keep_monthly,restore_drills} config wired dataclass/_meta/load/to_dict; GET /api/durability/status, GET /api/durability/snapshots, POST /api/durability/run routes; ~49 tests green

### `DAS-4` — Backups settings frontend panel (Session 2c)

**Status:** done

§3 + Provider & Config Plug-in Map (write-path leg / _EDITABLE_CONFIG + FE); Implementation Order Session 2 (surface)

**Done when:** five durability.* dot-paths added to _EDITABLE_CONFIG PATCH allowlist (snapshot_dir deliberately excluded, pinned by test); Settings → Backups panel consumes status/snapshots/run endpoints; five-point config round trip (incl. False surviving _guard_flag) validated in a real browser on an isolated dev home; retention caps bounded; web typecheck+vitest+build green

### `DAS-5` — Inventory-driven merge/restore/export sweep + restore endpoint (T2-M1..M3, Sessions 2d-2l)

**Status:** done

Amendment 2026-07-26 (T2-M1/M2/M3); §1 merge field consumers; §6 export/import functions; §8 portability.py ABSORBED

**Done when:** restore --mode merge dispatches all five StateEntry merge strategies (append_dedup incl. SEL key-gated fail-closed, sqlite_attach_ignore with FTS rebuild, union_by_id, lww_by_updated_at, replace_only skipped loudly); --components everything superset restores 19/19 files 0 lost; --dry-run prints per-entry merge plan matching the real merge; POST /api/durability/restore mirrors CLI with non-empty-home (home_is_populated) auto-detect + gateway-running refusal; create_export_zip/apply_import_zip made inventory-driven (no-secret asserted on bytes); triggers.json added to inventory; success criteria 1-2 and amendment gaps 2-3 met; full pytest -n4 green

### `DAS-6` — Sync core + git-sync + dir-sync transports (Session 3)

**Status:** todo

§4.1 sync cycle; §4.3 transports (git-sync, dir-sync); Provider & Config Plug-in Map (sync type + SyncTypeHandler + sdk/sync.py + sync_transports/registry.py); Implementation Order Session 3

**Done when:** `sync` added to PROVIDER_TYPES AND a SyncTypeHandler registered in the same commit (test_manifest_types_match_handlers green); sdk/sync.py re-exports SyncTransportProvider/SyncObject/PushResult; sync_transports/registry.py flat register/get; a shard import/restore function added (S2k noted shards were write-only); pull→merge-import→export-union→push cycle with registry.json machine seqs + CAS retry, stale_after_secs staleness window, durable outbox with typed deliverer outcomes + consumed-only cursor, deterministic per-strategy merges + tombstones, derived indexes rebuilt-not-synced; git-sync + dir-sync first-party apps; criterion 4 met (two machines converge over a git repo/synced folder incl. tombstoned delete)

### `DAS-7` — Conflict handling — records + review queue + propose-only LLM merge (Session 3)

**Status:** done

**DONE note.** `durability/conflicts.py` (record model + both-sides-edited detection + the
`sync/conflicts.jsonl` review queue) and `durability/conflict_merge.py` (the propose-only
`one_shot_completion(use_case="background")` pass) landed; the ancestor shas live in the SHARED
`registry.json` (`Registry.ancestors`, per entity family, published by the same CAS bump that
announces a seq), and `reconcile_entry` HOLDS a conflicted id — the remote row is dropped before the
merge, so the local version stays byte-identical and authoritative until a human resolves. Fail-open
on the model: no model / breaker / timeout / unparseable answer leaves the record `needs-review` with
no proposal and a recorded `proposal_error`; the conflict is never lost and never silently resolved.
Domain routing is the record's `surface` field (memory → memory review, knowledge → knowledge UI,
else the Durability queue) plus `ConflictQueue.items(surface=…)`; the review *screens* are DAS-10's
row ("Settings → Durability panel gains … the conflict review queue"), so no HTTP route is added
here. Criterion 5 is covered end to end over the shared-store fake (offline same-task edit on both
machines → one review item, nothing applied, still nothing applied on the next cycle).
Finding: an ancestor may only advance to a sha the PEER provably holds (converged, or the remote
won). The first implementation recorded the merged-local sha, which — with two peer seqs in one
sweep — advanced the ancestor to an unpublished local edit and masked the real conflict as a
one-sided fast-forward; only the end-to-end criterion-5 test caught it.

§4.2 Conflict handling; §7 (no auto-applied LLM merges); Implementation Order Session 3

**Done when:** sha-divergence on the same entity id with both sides edited since the common ancestor (ancestor sha in registry) produces a conflict record in a review queue; a background one_shot_completion(use_case="background") pass drafts a proposed merge + rationale surfaced as needs-review, never auto-applied; local version stays authoritative and both versions persist in the shared store until resolved; memory-domain conflicts route to the memory review surface and knowledge-domain to the knowledge UI; criterion 5 met (offline same-task edit yields a conflict-review item, applies nothing until accepted)

### `DAS-8` — rsync-sync + s3-sync transports + end-to-end encryption (Session 4)

**Status:** todo

§4.3 (rsync-sync, s3-sync); §4.4 Encryption for untrusted stores; Provider & Config Plug-in Map (SYNC EgressPolicy); Implementation Order Session 4

**Done when:** rsync-sync (subprocess over ssh) and s3-sync (signed PUT/GET/LIST via net.fetch with a derived SYNC EgressPolicy through egress_policy_for(), host-pinned + raised max_bytes, never hand-rolled aiohttp) shipped as first-party apps; AES-256-GCM per shard with per-shard HKDF key from passphrase + first-write-wins salt object, routing/metadata fields kept plaintext, plaintext-in-encrypted-store rejected on send AND receive as permanent skip, missing salt = hard setup error; per-transport encryption defaults (ON for s3/dir, OFF for git); secret=True entries excluded before any transport; criteria 7-8 met

### `DAS-9` — Workspace time-travel — adaptive-debounce git + rollback/revert/preview + panel (Session 5, §5)

**Status:** todo

§5 Workspace Time-Travel (NEW-4.a); §3 deferred hourly git commit of the memory tree; Implementation Order Session 5 (time-travel half)

**Done when:** state-history git repos cover config.json/entity_settings, skills/, the memory markdown tree (workspace/memory, _ext, plan_memory), prompts/+prompt_snippets, projects/<id>/context; adaptive-debounce commits (10s→0, serialized per root) hooked at the atomic_write post-write seam; hourly git commit of the memory tree/shards lands (the deferred §3 piece); rollback (hard reset, prior HEAD preserved in service refs) vs revert (reverse-merge, loud overlap failure) with a mandatory preview; secrets gitignored yet preserved across rollbacks; Settings → Durability → Time Travel panel (timeline, diff preview, rollback/revert, what-changed-while-I-slept filter); criterion 6 met; time-travel never syncs

### `DAS-10` — §6 DSAR portability endpoints + remaining Durability FE (Session 5, §6)

**Status:** todo

§6 User-Facing Portability Endpoints (NEW-4.b); §4.3 sync config via /api/providers routes; §4.2 conflict review surface FE; Implementation Order Session 5 (endpoints + FE half)

**Done when:** POST /api/durability/export (full or per-domain shard zip, secret∪derived excluded, §2 manifest inside), POST /api/durability/import {mode:merge|replace} extending validate_import_zip/apply_import_zip to the full inventory with MANIFEST v3 (v1|2 back-compat), GET /api/durability/archive + POST /api/durability/archive/{id}/restore with per-domain row counts and last-drill validate status; separate memory vs knowledge export buttons (knowledge includes files/ originals); Settings → Durability panel gains archive browser, sync config via standard /api/providers routes, and the conflict review queue; criteria 9-10 met (memory/knowledge separately exportable + separate conflict surfaces; a third-party type:"sync" app registers/configures/syncs with zero core changes)

