# Plan: Durability & Sync — Deterministic Shards, Scheduled Snapshots, User-Owned Transport

**Status:** IN PROGRESS — Session 1 (inventory + audit) and Session 2 (2a shards / 2b service / 2c
settings surface) shipped 2026-07-27/29: deterministic shards + `backup export|validate`, the
boot-started snapshot service, tiered retention, restore drills, and the Backups settings panel.
**Remaining in S2:** the restore endpoint (`POST /api/durability/restore` does not exist) and the
T2-M1/M2/M3 merge path — `--mode merge` exists on the CLI but **NOTHING reads `StateEntry.merge`**, so
the inventory-driven dispatch is unbuilt. 🔴 Also: **`audit_home()` has zero runtime callers**
(test-only), so the claims-everything guard is inert and `model_calls.jsonl`/`spend.json` remain
unclaimed by any snapshot or export. Sessions 3-5 (sync core, transports, time travel) not started.
Audited 2026-08-04. (rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Approved recommendation set folded in (mechanism-level, not appendix):

- **NEW-4** — durable state backup + multi-machine sync core (JSONL shards + SHA manifest + validate; pull→merge-import→export-union→push; boot-started scheduled self-backup with rolling backups + restore endpoints; git-versioned memory snapshots; encrypted-intents crypto for machine exchange) → §1, §2, §3, §4, §7
- **NEW-4.a** — workspace-level adaptive-debounce git auto-commits on config/skills/memory/projects + rollback/revert/preview UI panel → §5
- **NEW-4.b** — user-facing export/import/archive-restore endpoints (DSAR-shaped portability safety net) → §6
- **NEW-4.c** — multi-machine sync over plain shared storage (S3/local-FS) via manifest + versioned registry + mtime-fingerprint refresh + sha-conflict detection with propose-only LLM merge on divergent edits → §4

---

## Overview

PersonalClaw's soul is the state it accumulates: **memory** (the harness's own internals — facts, facets, episodic, procedural, lessons in `memory.db`), **knowledge** (the user's personal items — documents, files, photos, notes in `workspace/knowledge/knowledge.db`), tasks, projects, skills, prompts, workflows, agents, apps, config, and the run ledger. That distinction is load-bearing throughout this plan (user directive): *knowledge* names the knowledge store; *memory* names the harness subsystem — the shard inventory, sync rules, and UI never conflate them.

Today that state has a partial, manual, single-shot durability story — and this very project has already lost a memory directory once (2026-07-02). This plan makes durability boring: every byte of state is (1) enumerated in one inventory, (2) exportable as deterministic, human-diffable, validatable shards, (3) snapshotted on a schedule with rolling retention and periodic restore drills, (4) syncable across machines through transports the *user* owns (a git repo, a synced folder, an rsync target, an S3 bucket), and (5) recoverable through first-class restore endpoints — not archaeology.

**Soul guardrails:** single user, plain local files, no server component of ours anywhere in the sync path (the shared store is dumb storage with insert-only semantics — zero server logic). Human-diffable Git history of what the assistant knows is itself a trust feature. Anything intelligent in the pipeline (conflict merge) *proposes* — it never silently writes.

### Starting points (verified against code, 2026-07-12 recon)

The design builds on what actually exists — not the idealized versions the approved recommendation assumed:

- **Snapshot/portability coverage is PARTIAL — closing that gap is work item #1.** `snapshot.py` covers only `VALID_COMPONENTS = (memory, crons, config, skills, workspace, notifications, security)` over `CORE_FILES` (memory.db, memory_index.db, crons.json, config.json, session_map.json, hooks.json, project_dir, workspace_dir, notifications.jsonl, sel_hmac.key, telemetry_salt) plus the workspace/plan_memory/skills trees. **Neither snapshot nor export covers `tasks/`, `projects/`, `entity_settings/`, `loop/`, `artifacts/`, `prompts/`, `workflows/`, `apps/`, `agents/`** (recon gotcha #10). There is no "everything" component. A plan claiming "full-state backup" via today's tools is wrong.
- **`knowledge.db` is snapshotted UNSAFELY today.** It lives at `workspace/knowledge/knowledge.db` (verified: `knowledge/__init__.py:knowledge_db_path`), inside the `workspace` tree that `snapshot_main` copies via `_copytree_safe` — a raw file copy of a live WAL sqlite database (`-wal`/`-shm` sidecars present on disk). Only the CORE_FILES `*.db` entries go through the sqlite backup API. Fixing this (backup-API copy for *every* sqlite file, discovered by extension, not by allowlist) ships in Slice 1.
- **Good machinery to reuse, not rebuild:** `snapshot_main` already does WAL checkpoint, sqlite `backup()` API for core DBs, `_data_filter` (rejects traversal/symlinks/hardlinks), atomic tmp-tar rename, 0600 chmod (the tar contains `sel_hmac.key`), `--keep` pruning, and merge helpers (`_merge_memory` — ATTACH + INSERT OR IGNORE over a 4-table allowlist; `_merge_crons` by job name; `_merge_notifications`). `portability.py` has zip export/import with `EXPORT_EXCLUDE` (.env, .local_secret, sel_hmac.key, telemetry_salt, session_map.json, pid files), traversal-safe `validate_import_zip` (MANIFEST v1|2), and merge/replace apply. Both are **manual and single-shot** — no scheduler calls either.
- **Restore refuses while the gateway runs** (`_is_gateway_running`, snapshot.py:554). Replace-restore keeps that invariant; merge-import can run live (it already reuses the merge helpers the dashboard import path uses).
- **Store conventions shape the shard design:** `atomic_write` (mkstemp + `os.replace`) is THE write convention for every JSON store; fcntl flock via `single_flight(job_key)` (concurrency.py) is the overlap guard; sqlite is used ONLY for memory (`memory.db`, `memory_index.db`) and knowledge (`knowledge.db`) — everything else is per-entity JSON files with no cross-file transaction. So most of the store is *already* entity-sharded; the exporter folds files into JSONL rows rather than inventing a new schema.
- **There is no sync provider type.** `PROVIDER_TYPES` (apps/manifest.py:453) = {model, agent, task, channel, inbox, skills, knowledge, memory, notification, tool, workflow, search, action, prompt}, and it MUST equal the runtime `_TypeHandler` set (`test_manifest_types_match_handlers` guards the #47 bug class). Adding `sync` means adding **both sides together** (§ Plug-in Map).
- **Egress:** `net.fetch` policies cap bodies at 5-10MB (STRICT/CONNECTOR). Backup shards can exceed that; the S3 transport derives a named `SYNC` `EgressPolicy` profile via `egress_policy_for()` rather than hand-rolling aiohttp (recon rule: never hand-roll for attacker-influenceable hosts).
- **The "memory dir lost" incident** the recommendation cites was a markdown memory workspace, not a sqlite store. The hourly git-versioned snapshot in this plan therefore covers the *memory markdown tree* (`workspace/memory` + `workspace/_ext/<cwd-slug>` partitions) alongside memory.db shard exports — not a nonexistent standalone "memory dir".
- **`triggers.json` does not exist yet** — background stores today are `crons.json` / `hooks.json` / `event_triggers.json` / `autonudge.json`. The inventory (§1) covers what exists; when AUTOMATION-SUBSTRATE unifies them, the inventory line item swaps (that plan's step 9 already owns updating snapshot coverage for `triggers.json`).

---

## 1. The State Inventory — one manifest of everything that matters

One module, `durability/inventory.py`, is the single source of truth for "what is PersonalClaw's state." Every other piece of this plan (snapshot, shard export, sync, portability, drills) iterates the inventory instead of maintaining its own allowlist — the CORE_FILES-drift bug class dies here.

Each inventory entry declares:

```python
@dataclass(frozen=True)
class StateEntry:
    id: str            # "tasks", "projects", "memory_db", "knowledge_db", "skills", ...
    kind: str          # json_entity_dir | json_file | jsonl_append | sqlite | tree
    path: str          # relative to config_dir()
    domain: str        # memory | knowledge | work | automation | platform | config
    secret: bool       # NEVER leaves the machine (sel_hmac.key, .env, .local_secret, telemetry_salt)
    derived: bool      # index/cache — excluded from shards, rebuilt on import
                       # (memory_index.db, memory.faiss, memory.ids.json, knowledge FTS,
                       #  skills/.skill_embeddings.json, __pycache__, WAL/SHM sidecars)
    merge: str         # union_by_id | lww_by_updated_at | append_dedup | sqlite_attach_ignore | replace_only
    tombstones: bool   # entity kinds that need delete markers to survive sync (tasks, projects)
```

**Inventory contents (the gap closure).** Everything `CORE_FILES` has today PLUS the uncovered stores: `tasks/` (incl. `task_lists/`, `_comments_*`), `projects/` (project.json + `context/`; `worktrees/` excluded as derived/git-owned), `entity_settings/`, `loop/`, `prompts/`, `prompt_snippets/`, `workflows/`, `agents/`, `apps/` (installed copies — `data/` config is state; code trees are re-installable and marked `derived` with a recorded source ref), `subagents/`, `folders.json`, `active_models.json`, `active_search_providers.json`, `active_prompts.json`, `extensions/` (instances + use_case_settings), `artifacts/`, `sessions/` (JSONL, year-sharded), `crons.json`/`hooks.json`/`event_triggers.json`/`autonudge.json`, `workspace/` (memory markdown, knowledge/ incl. knowledge.db + files/, lexicon, plan_memory). The `domain` field keeps **memory** entries (memory.db, workspace/memory, plan_memory) and **knowledge** entries (knowledge.db, knowledge files, lexicon) distinct end-to-end — filters, UI grouping, and sync scopes all key on it.

`snapshot.py`'s `VALID_COMPONENTS`/`CORE_FILES` become projections of the inventory (component = domain), gaining an `everything` component for free. `portability.py`'s `EXPORT_EXCLUDE` becomes `secret=True ∪ derived=True`. A unit test asserts every file/dir found under `config_dir()` is claimed by exactly one inventory entry or an explicit ignore list — new stores can't silently dodge backup again.

---

## 2. Deterministic Shard Format — JSONL + SHA manifest + `validate`

The authoritative, syncable, human-diffable representation of state (birdclaw's proven design: "SQLite is just a fast local index built from the shards"):

- **Layout:** `shards/<entry-id>/…` — per-entity-family JSONL files. Entity dirs fold to one row per entity file (`{"id": "t-3fa2b1c9", "data": {…}}`), rows **sorted by id**, canonical JSON (sorted keys, LF, UTF-8) so identical state always produces byte-identical shards. Append-only stores (sessions, notifications) shard by year (`sessions/2026.jsonl`); rows with unparseable timestamps land in `unknown.jsonl`, never backdated. Files >48MiB deterministically split into `part-NNNN` (no LFS needed when the transport is git).
- **sqlite export:** `memory.db` rows dump per-table to JSONL through the sqlite backup API snapshot (starting from `_merge_memory`'s existing 4-table allowlist: semantic_memory, episodic_memories, memory_events, knowledge_facts/edges — extended to the full schema), stable-ordered by rowid/id. `knowledge.db` likewise (items, sources, edges; the `files/` originals ride as a content-addressed blob dir keyed by sha256, deduplicated). Indexes are `derived` — **excluded and rebuilt on import** (FTS, faiss + ids.json, embedding caches).
- **Manifest:** `manifest.json` pins schema version, `generated_at`, `machine_id` (a new stable per-machine id file — NOT `telemetry_salt`, which is a secret), and per-shard `{bytes, rows, sha256}`.
- **Tombstones:** entity kinds with `tombstones=True` write `{"id": …, "deleted_at": …}` rows on delete (a small write-path hook in the native task/project stores) so sync deletion survives the union merge instead of resurrecting (lifeGLANCE's tombstone-before-delete).
- **`personalclaw backup validate`:** manifest well-formed → every shard exists → bytes/rows/sha match → every row parses → sqlite dumps re-import cleanly into a scratch DB. Non-zero exit for CI/cron use. This is also the restore-drill core (§3).
- **Writer discipline:** shard writes use `atomic_write`/`atomic_write_bytes`; the whole export runs under `single_flight("shard-export")`; deterministic id backfills never bump `updated_at` (the fleet-churn lesson).

Secrets (`secret=True`) are **never sharded**. Local snapshot tars keep including `sel_hmac.key` (with the existing 0600 discipline) because a same-machine restore needs it; shards are the *leaves-the-machine* representation and follow export rules.

---

## 3. Snapshot Service — scheduled, rolling, drilled

A boot-started background service (`durability/service.py`, started from `dashboard/server.py` startup alongside the extension loader — same pattern as the inbox retention loop), replacing "manual and single-shot":

- **Schedule:** default nightly full snapshot (the existing tar.gz path, now inventory-driven so it actually covers everything) + hourly incremental shard export of dirty entries (mtime-fingerprint per inventory entry — export only what changed). The **memory markdown tree and memory.db shards additionally commit hourly into a local git repo** (`~/.personalclaw/backups/state-history/`, plain `git` via subprocess) — the direct mitigation for the 2026-07-02 loss: an hour is the maximum blast radius, and `git log` over shards is the human-diffable "what did the assistant learn this week" view. Overlap guarded by `single_flight("snapshot")`; job timing rides the existing schedule machinery until AUTOMATION-SUBSTRATE lands, then converts to `created_by: system:durability:*` triggers with deterministic ids (that plan's idempotent-re-registration rule).
- **Rolling retention:** `keep` generalized — N nightly + M weekly + Y monthly, pruned oldest-first (extends the existing `--keep` logic).
- **Restore endpoints:** `POST /api/durability/restore {snapshot_id, mode: merge|replace, components?, confirm: true}` + `GET /api/durability/snapshots`. Merge runs live (reusing `_do_merge`/`_merge_*`); replace stages the snapshot and keeps the refuse-while-gateway-runs invariant — the endpoint schedules the swap for next boot (staged dir + marker file the startup path applies before opening stores), mirroring the app-update `.rollback` staging pattern. Every restore writes the existing `pre-restore-<ts>` escape hatch first.
- **Restore drills (trust is a tested property):** a monthly drill job restores the latest snapshot into a temp dir, runs `backup validate`, opens each sqlite copy with `PRAGMA integrity_check`, diffs manifest row counts against the live store, and reports PASS/FAIL through `DashboardState.notify` (kind `warning` on failure — ranks above quiet-hours info suppression). A backup that has never been restored is a hope, not a backup. Drills never touch the live store.
- All snapshot/restore/drill/prune operations write SEL audit events (`sel.py`), as snapshot already does today.

---

## 4. Sync — user-owned transports as pluggable providers

Multi-machine sync = the shard representation (§2) + a dumb shared store + a deterministic merge. **No PersonalClaw server anywhere**: the shared store only ever needs "write these objects" and "list/read objects" — insert-only semantics (lifeGLANCE's zero-server-logic doctrine, re-grounded onto plain storage per amendment NEW-4.c; the "batch/list endpoints" of the original research become append-object + list-prefix operations on the store).

### 4.1 The sync cycle (birdclaw's proven loop)

`pull → merge-import remote rows → export local union → push`, per the inventory's `merge` strategy:

- `union_by_id` for entity dirs (rows only one side has are preserved), `lww_by_updated_at` per entity for divergent same-id rows *below* the conflict threshold, `append_dedup` for JSONL streams (stable event ids make re-import a no-op), `sqlite_attach_ignore` for memory/knowledge DBs (the existing `_merge_memory` mechanism, generalized), tombstone-aware everywhere `tombstones=True`.
- **Versioned registry:** `registry.json` at the sync root records per-machine `{machine_id, seq, last_export_at, manifest_sha}`. Push = write shards under `machines/<machine_id>/seq-NNNN/` + CAS-update the registry (compare-and-swap on the prior sha; on registry race, re-pull and retry — insert-only object writes are idempotent, so retries are free).
- **mtime-fingerprint refresh + staleness window:** read paths pull+merge only when the last remote check exceeds `stale_after_secs` (default 900, per-process memo — no store roundtrip per operation); every local export triggers a push afterward. Remote listing cached by mtime/etag fingerprint so polls are cheap.
- **Durable outbox:** pending pushes queue in `~/.personalclaw/sync/outbox/` with per-target status `pending|delivered|given-up` and typed deliverer outcomes `delivered|transient|permanent` (unexpected throw = transient, never drop); the pull cursor advances **only on consumed rows** — prerequisite-absent holds the drain, payload-bad advances+logs. One target giving up never blocks others.
- **Indexes rebuilt on import**, never synced (FTS/faiss/embedding caches are `derived`).

### 4.2 Conflict handling — deterministic first, propose-don't-write second

sha-divergence on the same entity id with **both sides edited since the common ancestor** (ancestor sha tracked in the registry per entity family) is a *conflict*, not an LWW coin-flip:

1. Disjoint-field merges and insert-only unions resolve deterministically.
2. Genuinely divergent edits produce a **conflict record** in a review queue. A background LLM pass (`one_shot_completion(use_case="background")` — the reasoning axis, never the chat/native-runtime axis) drafts a proposed merged version with a rationale, **surfaced as a needs-review item; never auto-applied** (soul rule: propose, don't write). Until resolved, the local version stays authoritative locally and both versions persist in the shared store (no data loss while the user decides).
3. Conflicts on **memory-domain** entries route to the memory review surface; **knowledge-domain** conflicts to the knowledge UI — the boundary holds even in failure paths.

### 4.3 Transports — first-party provider apps

Each transport is an app-delivered provider of a NEW extension type `sync` (see Plug-in Map for the exact wiring). Contract:

```python
class SyncTransportProvider(ABC):
    name: str; display_name: str
    def push(objects: list[SyncObject]) -> PushResult      # insert-only; idempotent on object key
    def list_remote(prefix: str) -> list[RemoteRef]        # key + size + fingerprint (mtime/etag/sha)
    def pull(refs: list[RemoteRef]) -> list[SyncObject]
    def cas_registry(expected_sha: str | None, data: bytes) -> bool
    def test() -> ConnectionResult                          # the ModelCatalog.test_connection precedent
```

First targets, each a first-party app under `apps/` (installed copies at `~/.personalclaw/apps/{name}/`, settings via `ProviderSettings` → `data/config.json`, credentials via the credential store / `save_credential` `.env` 0600 path):

| App | Mechanism | Notes |
|---|---|---|
| `git-sync` | subprocess `git` against a user repo | The trust-feature default: human-diffable history of what the assistant knows. Commit = push; `git log -p` over shards is the audit UI for free |
| `dir-sync` | copy into any local/auto-synced folder (iCloud Drive, Dropbox, Syncthing) | Zero credentials; the folder's own sync does transport; registry CAS degrades to rename-based locking |
| `rsync-sync` | `rsync` over ssh | subprocess; host/path from provider settings |
| `s3-sync` | S3-compatible HTTP (signed PUT/GET/LIST) | Routes through `net.fetch` with a derived `SYNC` `EgressPolicy` (host-pinned to the configured endpoint, raised `max_bytes`, via `egress_policy_for()`) — never hand-rolled aiohttp |

### 4.4 Encryption for untrusted stores

Optional end-to-end encryption (lifeGLANCE's codec, adapted): AES-256-GCM per shard, per-shard key via HKDF from a user passphrase + a first-write-wins salt object in the sync root; **routing/metadata fields (manifest entry names, machine_id, seq) stay plaintext** so sync logic works without the key; key derivation is machine-agnostic so every machine with the passphrase can decrypt every other's shards. Default **ON** for `s3-sync` and `dir-sync` (third-party storage), default **OFF** for `git-sync` to a private repo — encryption destroys diffability, and the human-readable git history is the point; the toggle states that tradeoff explicitly. Plaintext-over-encrypted-store is rejected on both send AND receive as a permanent skip (contract violation, never an error loop). Never fabricate a salt; missing salt with encryption enabled is a hard setup error.

**Secrets never sync, ever** — `secret=True` inventory entries are excluded before any transport sees bytes, independent of encryption.

---

## 5. Workspace Time-Travel (NEW-4.a) — adaptive-debounce git + rollback/revert/preview

Continuous, zero-thought history for the state the user and the agent edit most, distinct from scheduled snapshots (which are for disasters; this is for "undo what just happened"):

- **Roots:** `config.json` (+ entity_settings), `skills/`, the memory markdown workspace (`workspace/memory`, `workspace/_ext/`, `plan_memory/`), `prompts/` + `prompt_snippets/`, and `projects/<id>/context/`. Each root is (or shares) a local git repo under the `state-history` umbrella (§3).
- **Adaptive debounce:** after any write (hooked at the `atomic_write` seam via a lightweight post-write notifier — one callsite, since every JSON store already funnels through it; tree writes hook the same notifier at their save paths), a commit is scheduled starting at 10s and tightening toward 0 under sustained write activity; work serialized per repo root through one queue (space-agent's proven design). Near-zero cost, full history.
- **Rollback vs revert as distinct operations:** *rollback* = hard reset to a commit, with prior HEAD preserved in service-owned refs so later commits stay listable (forward travel possible); *revert* = inverse commit via reverse-merge so non-overlapping later edits survive — overlap fails loudly naming the blocking file. Secrets (`.env` etc.) are gitignored in every root yet preserved across rollbacks.
- **Preview before destruction:** every rollback/revert first returns an operation preview (affected files + per-file diffs; diffs >1MB listed not rendered) that the UI confirms.
- **UI panel:** Settings → Durability → Time Travel: per-root commit timeline, diff preview, rollback/revert buttons, and a "what changed while I slept" filter (commits authored by background sessions vs interactive ones — commit messages carry the writing surface). Config/skills restores that require a process reload surface the existing restart affordance.

Time-travel is *local-only* history; it never syncs (the shard/sync layer is the cross-machine story — one writer per mechanism).

---

## 6. User-Facing Portability Endpoints (NEW-4.b)

`portability.py` grows from a zip pair into a DSAR-shaped surface — the safety net that works even if the user never configures sync:

- `POST /api/durability/export` — full or per-domain (memory / knowledge / work / automation / platform / config) shard export as a zip (inventory-driven; `secret ∪ derived` excluded exactly as `EXPORT_EXCLUDE` does today), with the §2 manifest inside. "Give me everything PersonalClaw knows about me" is one click.
- `POST /api/durability/import {mode: merge|replace}` — extends `validate_import_zip`/`apply_import_zip` to the full inventory (traversal-safe, MANIFEST version 3 with 1|2 back-compat; replace strips sensitive + `skills/auto` first, as today).
- `GET /api/durability/archive` + `POST /api/durability/archive/{id}/restore` — the snapshot list/restore pair (§3) exposed as the archive browser: date, size, per-domain row counts (from the manifest), validate status from the last drill.
- Knowledge exports include the `files/` originals (they are the user's documents/photos — the whole point); memory exports are the harness-internals dump, labeled as such in the UI. The two are separate export buttons, not one blob.

---

## 7. What We Deliberately Do NOT Build

- **No PersonalClaw sync server / no server-to-server protocol** (amendment NEW-4.c is explicit) — dumb user-owned storage + insert-only objects + a registry file. The lifeGLANCE endpoint design survives only as *semantics* (insert-only, batch, list-since, first-write-wins salt), mapped onto object storage.
- **No CRDTs.** Per-id union + LWW + tombstones + a review queue for real conflicts. Single-user-few-machines does not need convergence theory; it needs "never lose a row and never silently pick a loser."
- **No auto-applied LLM merges** — proposals only (soul).
- **No continuous real-time sync.** Staleness-window pull + after-write push. This is a personal tool; seconds-level convergence is enterprise machinery.
- **No secret syncing, no credential export** — credentials re-enter per machine via the existing onboarding/`save_credential` path.
- **No second scheduler** — snapshot timing rides the existing schedule service now, system triggers later (AUTOMATION-SUBSTRATE owns that migration).
- **No new notification path** — drills/failures route through `DashboardState.notify` → `notification_allowed()`.

---

## 8. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `snapshot.py` | **ABSORBED** (kept as the tar engine) | `VALID_COMPONENTS`/`CORE_FILES` re-derived from the inventory (§1); gains `everything`; sqlite-backup-API applied to ALL `*.db` discovered (fixes the live `knowledge.db` raw-copy hazard); WAL checkpoint, `_data_filter`, atomic tar rename, 0600, `--keep` (→ rolling tiers), merge helpers all kept verbatim. CLI stays; the service (§3) becomes its caller |
| `portability.py` | **ABSORBED** | `EXPORT_EXCLUDE`/`EXCLUDE_DIRS` re-derived from `secret ∪ derived`; zip format gains the shard manifest (v3); endpoints grow into §6's surface; existing merge/replace + traversal validation kept |
| `_merge_memory` / `_merge_crons` / `_merge_notifications` | **KEPT + generalized** | Become the `sqlite_attach_ignore` / `union_by_id` / `append_dedup` merge strategies of the inventory; the sync import path (§4.1) and dashboard import reuse the same functions |
| `atomic_write` / `single_flight` | **KEPT — load-bearing** | Shard writer + service overlap guard; time-travel's post-write notifier hooks the `atomic_write` seam |
| `sessions/*.jsonl`, `notifications.jsonl` | **COVERED as append streams** | Year-sharded JSONL shards, `append_dedup` merge; incognito/temporary sessions: the `memory_mode` metadata head-line rides along so a restored store re-derives suppression exactly as history consolidation does |
| `workspace/outbox` | **UNTOUCHED** | Pre-existing dir, unrelated concern; the sync outbox lives at `~/.personalclaw/sync/outbox/` |
| AUTOMATION-SUBSTRATE `triggers.json` | **FORWARD-COMPATIBLE** | Inventory line item swaps from crons/hooks/event_triggers/autonudge to `triggers.json` + ledger when that plan's step 9 lands (it already owns snapshot-coverage updates) |

---

## Provider & Config Plug-in Map

Where each new piece plugs into the pluggable-provider architecture (recon: providers.md) — nothing invents a parallel extension path:

- **New provider type `sync`:** added to `PROVIDER_TYPES` (apps/manifest.py) **AND** a `SyncTypeHandler` in `providers/registry.py:get_provider_registry()` **in the same commit** — `test_manifest_types_match_handlers` guards the #47 bug class (never add one side only). The handler `create()`s via the standard `providers/loader.py:load_factory` (manifest `provider: {type: "sync", implementation: "provider:create_provider"}`) with `ProviderSettings` config, and registers into a new domain registry `sync_transports/registry.py` (flat dict + register/get, the `action_providers/registry.py` shape). Transports are ordinary apps: install → enable → factory → registry; disable unregisters.
- **SDK:** `sdk/sync.py` re-exports `SyncTransportProvider` + `SyncObject`/`PushResult` so third-party transports (a future WebDAV or Google Drive transport) are buildable against `SDK_VERSION 1.0` conventions; `sdk.net` + `sdk.credentials` cover their egress and secrets exactly as model apps do today.
- **No new action provider is required** (so no `ALLOWED_HOOK_PROVIDERS` change in the core slices). IF a later slice adds a hook-invokable `run-backup` action (e.g. "snapshot before every risky workflow"), it follows the rule to the letter: implement `ActionProvider`, register via `register_action_provider` or as an app, **and add its name to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)** or hook create/update rejects it.
- **Config = a new `DurabilityConfig` section**, wired through the FOUR points (recon persistence-security gotcha #1): (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce, per-element `_meta` if any `list[dataclass]` field appears); (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped); (c) `to_dict()` — a NEW top-level section must be added; (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for the runtime-editable knobs. Fields: `snapshot_enabled`, `snapshot_interval_hours`, `keep_daily/weekly/monthly`, `drill_enabled`, `timetravel_enabled`, `timetravel_debounce_secs`, `sync_enabled`, `sync_transport` (provider name), `sync_stale_after_secs`, `sync_encrypt`.
- **Egress:** only `s3-sync` talks to non-user-controlled infrastructure over HTTP → derived `SYNC` `EgressPolicy` profile through `egress_policy_for()` + `net.fetch` (operator `security.egress` layering applies for free). `git`/`rsync` shell out to user-configured hosts via subprocess — `denied_command_patterns()` and the security policy layer are unaffected and unbypassed (no new command surfaces added to agents; the service, not the agent, invokes them).
- **Secrets:** transport credentials via the credential store (`save_credential` → `.env` 0600, names-not-values in any API response); the sync passphrase-derived key cached non-extractably in the service, never persisted; every journal/SEL record stores key *names* only.
- **Audit + notify:** SEL events for export/push/pull/merge/conflict/restore/drill; user-facing outcomes through `DashboardState.notify` → `notification_allowed()` — the entity-settings gate stays THE gate.
- **Memory vs knowledge routing:** the inventory `domain` field is the boundary's mechanical enforcement — memory-domain shards/conflicts/exports route to memory surfaces and are LEARNING-FLYWHEEL's concern to evolve; knowledge-domain ones to the knowledge UI; future knowledge providers (Google Drive, Google Photos) plug into the existing `knowledge_providers` seam and their *items* enter durability through `knowledge.db` like everything else — this plan never writes to either subsystem's contents, only copies them.

---

## Implementation Order & Effort

**5 sessions** (the NEW-4 core is ~4; amendments a-c add ~1). Each slice ships independently and is useful alone:

- **Session 1 — Inventory + gap closure + safe snapshots.** `durability/inventory.py` + the claims-everything test; re-derive snapshot/portability allowlists; sqlite-backup-API for all DBs (knowledge.db hazard fixed); `everything` component; rolling retention tiers. *Backup is now complete and correct, still manual.*
- **Session 2 — Shard exporter + manifest + validate + snapshot service.** §2 format (canonical JSONL, year shards, part-split, tombstone write-path hook, blob dir for knowledge files); `backup validate`; boot-started service with nightly tar + hourly dirty-entry shard export + hourly git commit of memory tree/shards; restore endpoints (merge live / replace staged-for-boot); monthly restore drill + notify. *Scheduled, drilled durability.*
- **Session 3 — Sync core + `git-sync` + `dir-sync`.** `sync` type (manifest + handler together), domain registry, `sdk/sync.py`; pull→merge-import→export-union→push with the registry/CAS, staleness window, outbox + cursor rules; deterministic merges + tombstones; conflict records + propose-only LLM merge queue; the two zero-credential transports. *Two machines converge over a git repo or a synced folder.*
- **Session 4 — `rsync-sync` + `s3-sync` + encryption.** `SYNC` egress profile; HKDF/AES-GCM codec with first-write-wins salt + plaintext routing fields + both-direction plaintext rejection; per-transport encryption defaults. *Untrusted storage becomes a valid transport.*
- **Session 5 — Time-travel + portability endpoints + FE.** §5 adaptive-debounce repos + rollback/revert/preview; §6 export/import/archive endpoints; Settings → Durability panel (status card, archive browser, sync config via the standard `/api/providers/...` routes, time-travel timeline, conflict review queue). *The whole story has a face.*

---

## Risks

| Risk | Mitigation |
|---|---|
| Live sqlite copied mid-write (today's knowledge.db reality) | sqlite backup API for every DB, discovered by extension; WAL checkpoint first; drill's `integrity_check` catches regressions |
| A new store dodges backup (the CORE_FILES drift class) | Inventory claims-everything test fails CI the moment an unclaimed path appears under `config_dir()` |
| Sync resurrects deleted entities | Tombstone rows for `tombstones=True` kinds; union merge honors `deleted_at` |
| Silent conflict data loss | sha-divergence vs common ancestor → conflict record; both versions retained; LLM merge is propose-only |
| Secrets leak into a remote store | `secret=True` excluded before any transport; names-not-values in APIs; encryption default-on for third-party storage; SEL audit on every push |
| Registry CAS races between machines | Insert-only object writes are idempotent; CAS failure → re-pull, re-merge, retry — never overwrite |
| Backup that can't restore | Monthly automated drill (validate + integrity_check + count diff) with loud notify on failure |
| Time-travel commit storms under heavy writes | Adaptive debounce (10s→0), serialized per root; derived/cache paths gitignored |
| Restore under a running gateway corrupts open stores | Replace-restore stays staged-for-boot (existing `_is_gateway_running` refusal honored); merge path uses the store-aware merge helpers |
| Shard export blocking the event loop / GIL pressure | Export + git + encryption run in a worker subprocess/thread pool; `single_flight` prevents overlap |

---

## Success Criteria

1. `personalclaw backup snapshot --components everything` followed by wiping `~/.personalclaw` and restoring reproduces a byte-equivalent state for every non-derived inventory entry — **including tasks, projects, entity_settings, loop, prompts, workflows, agents, and app data**, which today's tools drop.
2. The inventory claims-everything test fails when a new store directory is added without an inventory entry.
3. `backup validate` on a fresh shard export passes; corrupting one byte of any shard makes it fail naming the shard; the drill notification arrives on schedule with real numbers.
4. Two machines syncing through a plain git repo converge: a task created on A and a knowledge item added on B both exist on both after one sync cycle each way; a task deleted on A stays deleted on B (tombstone), and indexes (FTS/faiss) rebuild locally rather than syncing.
5. Editing the same task's description on both machines while offline produces a conflict-review item with an LLM-proposed merge — and applies **nothing** until the user accepts.
6. `git log -p` on the state-history repo shows an hour-granular, human-readable diff of memory changes; restoring the memory tree to any commit works via the panel with a preview first (the 2026-07-02 incident is now a 1-hour rollback).
7. No shard, sync object, or export zip ever contains `.env`, `.local_secret`, `sel_hmac.key`, or `telemetry_salt` (adversarially verified against every transport).
8. An encrypted S3 sync store is useless without the passphrase, yet `list_remote`/registry operations work without the key; a plaintext object appearing in an encrypted store is skipped permanently and logged, never looped on.
9. Memory-domain and knowledge-domain state are separately exportable, separately drill-counted, and their conflicts land on separate review surfaces — the boundary survives every path in this plan.
10. Installing a third-party sync transport app (manifest `type: "sync"`) registers, configures via the standard provider settings routes, and syncs — with zero core changes.

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**Merge-mode restore as a first-class, plan-printing citizen.** Sibling evidence: the restore people actually perform is "onto a machine that already has state" (new laptop half-set-up, partial loss, second machine) — and replace-mode restores there destroy the newer half. Recon: `snapshot.py::restore_main` (:604) ALREADY has `--mode merge|replace`, `--dry-run`, and auto-defaults to merge when `memory.db` exists (:650) — with real merge helpers (`_merge_memory` ATTACH + INSERT OR IGNORE over the 4-table allowlist; `_merge_crons` by job name; `_merge_notifications` by ts; `_copy_tree_no_overwrite` for trees) and `portability.py` reusing them. Credentials are already never in exports (`EXPORT_EXCLUDE`: `.env`, `.local_secret`, `sel_hmac.key`, `telemetry_salt` — verified) and snapshot-tar restore of security files is copy-if-missing only. So the amendment does NOT build merge restore — it closes the three honest gaps: (1) merge coverage is only as wide as `CORE_FILES` (tasks/projects/inbox/entity_settings etc. have NO merge semantics — the §1 inventory's `merge` field is specified but merge-restore isn't listed as a consumer), (2) `--dry-run` prints a raw file list, not a merge plan (no counts, no per-entry strategy, no conflict preview), (3) config merge is copy-if-missing per file — an existing `config.json` is safe, but this contract must be stated and tested, not incidental.

### Contract-level design

- **Inventory-driven merge (extends §1/§3, no new mechanism):** `restore --mode merge` iterates `durability/inventory.py` entries and dispatches on the already-specified `merge` field — `union_by_id` (entity dirs: copy rows/files whose id is absent), `sqlite_attach_ignore` (memory/knowledge DBs — content-hash-keyed INSERT OR IGNORE where the schema has stable ids; `episodic_memories.id` is the PK, verified `vector_memory.py:269`), `append_dedup` (JSONL by stable ts/event id — inbox items dedup by their `{channel}_{ts}` id), `replace_only` entries are **SKIPPED in merge mode with an explicit printed line** (never half-merged). **`config.json` is NEVER overwritten in merge mode** — stated in code and tested: existing file untouched (today's copy-if-missing behavior becomes the documented contract); missing file restored.
- **`--dry-run` prints the merge plan** (and becomes the default prompt before a live merge): per inventory entry `{entry, strategy, incoming_rows, would_add, would_skip_existing, skipped_replace_only}` computed by running every merge helper in plan-mode (a `dry_run: bool` threaded through `_merge_*`; sqlite plan via the same SELECTs without the INSERT). Exit 0; nothing written.
- **Auto-detect default (sharpens :650):** restoring onto a non-empty home (any non-derived inventory entry present — not just `memory.db`) → propose merge, print the plan, require confirmation; `--replace` stays the explicit wipe path and keeps the `_is_gateway_running` refusal + `pre-restore-<ts>` escape hatch verbatim. `POST /api/durability/restore` (§3) mirrors: `{mode}` omitted → server picks merge on non-empty and returns the plan for the UI to confirm (§2.2 envelope on errors).
- Credentials-never-in-snapshots: already true — merge mode additionally never writes any `secret=True` entry even if a hand-built archive contains one (belt-and-suspenders skip + warning).

### Session placement

Folded into **Session 2** (which already owns the restore endpoints + `backup validate`); the merge-plan UI confirm lands with Session 5's archive browser. Session count stays 5.

| ID | Task | Files | Done when |
|---|---|---|---|
| T2-M1 | Inventory-driven merge dispatch: `restore --mode merge` walks `StateEntry.merge` strategies; replace_only entries skipped loudly; config-never-overwritten contract in code + test | `snapshot.py`, `durability/inventory.py`, tests | fixture home with tasks+inbox+memory merges a snapshot: absent rows appear, existing rows untouched, `config.json` byte-identical, replace_only lines printed |
| T2-M2 | Merge-plan dry-run: `dry_run` threaded through every `_merge_*`; plan table printed; `--dry-run` and the pre-merge confirmation share one code path | `snapshot.py` merge helpers | plan counts match the subsequent real merge exactly on the same fixture; nothing written in plan mode (dir hash unchanged) |
| T2-M3 | Auto-detect + API mirror: non-empty-home detection over the inventory; CLI proposes merge+plan+confirm; `--replace` explicit; `POST /api/durability/restore` returns the plan when mode omitted | `snapshot.py`, `durability/` restore endpoint | empty home → replace path; non-empty → merge proposed with plan; explicit `--replace` still refuses while gateway runs; secret-entry planted in archive is skipped + warned |

---

## Execution log

- [2026-07-28][S1] DONE: inventory + gap closure + safe snapshots. (a) **`durability/inventory.py`** — the single manifest of what PersonalClaw's state IS: 56 `StateEntry` rows (id/kind/path/domain/merge + `secret`/`derived`/`tombstones`/`derived_within`), grounded by enumerating BOTH a fresh dev home and a long-lived real home rather than trusting the plan's or the code's claims. Projections replace the hand-maintained lists: `backup_entries()`, `export_entries()`, `secret_paths()`, `sqlite_entries()`, `domains()`/`entries_for_domain()`. `merge`/`tombstones` are declared now but consumed by S2/S3 — they're part of an entry's identity, not a later bolt-on. (b) **`audit_home()` — the claims-everything guard**: every top-level path must be claimed by an entry or matched by `IGNORED`, AND (my addition beyond the plan) **every `*.db` found anywhere must be declared `kind=sqlite`** — a database nested inside a `tree` entry is the dangerous case, since it gets filesystem-copied. Both real homes audit clean (claimed 23/25, 0 unclaimed, 0 undeclared DBs). (c) **THE DATA-LOSS FIX, measured not argued**: only `CORE_FILES["memory"]` got the sqlite backup API; `workspace/knowledge/knowledge.db`, `workspace/lexicon/lexicon.db` and `loop/loops.db` were inside `shutil.copytree`/`copy2` paths. I reproduced the hazard: a live WAL DB with an open handle and 576KB of un-checkpointed WAL, raw-copied, **lost 2000 of 4000 rows**. Now `_declared_db_paths()` drives (i) a WAL checkpoint per declared DB, (ii) a `_safe_copy_db()` backup-API pass that runs BEFORE the tree copies, and (iii) `_tree_ignore_dbs()` so tree copies skip `*.db`/`-wal`/`-shm` entirely — verified: all 4 DBs staged, `integrity_check=ok`, **2000/2000 rows captured from the live open DB**, zero sidecars in the archive. (d) **Gap closure**: `_everything_paths()` stages every remaining inventory entry, so `tasks/`, `projects/`, `loop/`, `artifacts/`, `prompts/`, `workflows/`, `agents/`, `apps/`, `entity_settings/`, `sessions/`, `uploads/` are now in the archive — verified end-to-end on a seeded home (the seeded task survived a real snapshot round-trip). Before this, a "full backup" silently dropped the user's entire task board. (e) `portability.EXPORT_EXCLUDE` is now a PROJECTION of `secret=True` (it gained `credentials`, which it was missing) with a literal fallback so an import error can never widen an export. 25 new tests (`test_durability_inventory.py` 15 + `test_snapshot.py` +10, incl. a parametrized live-open-DB capture test per DB and the sidecar-exclusion test).
- [2026-07-28][S1] DISCOVERY + FIX (pre-existing bug, in scope because it corrupts backup semantics): `_default_snapshot_dir()`'s fallback hardcoded `Path.home()/".personalclaw"/"snapshots"` instead of the ACTIVE home, so snapshotting an isolated dev home wrote its archive into the REAL home — mixing two installs' backups in the directory retention pruning then walks. I hit this live (an archive appeared in `~/.personalclaw/snapshots`; removed it — it was the only file there and seconds old). Now resolves `_pc_dir()`, pinned by `test_default_output_dir_honors_the_active_home`.
- [2026-07-28][S1] Gate: `make lint` green (517 files) · `make test` **8286 passed** · new tests stable over 3 repeat runs. NOTE on a scary-looking intermediate: one full-suite run showed 275s + 2 `test_subagent.py::TestOnDoneTimeout` timeouts, the exact signature of the CONTEXT-ECONOMY-S5 perf regression. It was NOT a regression — it was machine contention from my own concurrently-running baseline suite. Confirmed by re-running (51s, then 58s, both green) and by timing the new tests in isolation (4.6s total). The lesson from S5 still holds (compare duration against a baseline), but the corollary is: never run two full suites at once and then read the timing.
- [2026-07-28][S1] Remaining: S2 (shard exporter + manifest + `backup validate` + boot-started snapshot service + restore drills) and S3 (sync core + `git-sync`/`dir-sync`). §3's rolling retention tiers were NOT built this session — the existing `--keep N` remains; the tiered N-nightly/M-weekly/Y-monthly generalization belongs with S2's scheduled service, which is what makes tiers meaningful (a manual CLI has no cadence to tier).
- [2026-07-28][S2a] DONE (§2 — the shard format half of Session 2): `durability/shards.py`. (a) **Deterministic export** — `export_shards()` writes canonical JSONL (sorted keys, compact separators, LF, UTF-8) one directory per inventory entry, rows sorted by id; entity dirs → `entities.jsonl`, single JSON docs → `value.jsonl`, append-only stores → YEAR shards with unparseable-timestamp rows in `unknown.jsonl` (never back-dated), databases → one JSONL per table, tree entries → a content-addressed `blobs/` dir deduped by sha256. Files past `PART_SPLIT_BYTES` (48MiB) split into `part-NNNN` at boundaries that are a pure function of the content. (b) **Manifest + `validate()`** — `manifest.json` pins `schema_version`, `generated_at`, a NEW non-secret `machine_id` file (deliberately not `telemetry_salt`, which is `secret=True`), and per-shard `{bytes, rows, sha256}`; `validate()` re-derives all three, re-parses every row, and flags shards present-but-undeclared. (c) **CLI** `personalclaw backup export [--incremental]` / `backup validate`, non-zero exit on any problem so it works from cron/CI; export runs under `single_flight("shard-export")`; `dirty_entries()` gives the mtime-fingerprint incremental path. (d) **KEY DESIGN CALL — table discovery, not an allowlist:** `_sqlite_tables()` reads the schema. DISCOVERY CONFIRMED: `snapshot._merge_memory`'s 4-table allowlist names `knowledge_facts` + `knowledge_edges`, which **do not exist in memory.db** (verified against the real home: tables are `schema_version, semantic_memory, episodic_memories, memory_events, sqlite_sequence`) — two of four merge targets are dead code, which the exporter deliberately does NOT inherit. (e) Databases are read through the sqlite backup API into a scratch copy, so a live WAL store exports consistently (the S1 hazard, same fix); blob columns are recorded as `{"__bytes__": n}` rather than base64-bloating a human-diffable shard; secrets are excluded unconditionally (`export_entries()`).
  **BUG FOUND AND FIXED DURING VALIDATION:** an `--incremental` export rewrote `manifest.json` with only the changed entries' shards, orphaning the rest — so a perfectly good export failed `validate` with "present on disk but not declared". `_merged_shard_records()` now carries forward untouched entries' records (dropping any whose file has since vanished). Regression-tested.
  **MEASURED on the real home:** 22 entries / 24 shards / **50,205 rows** exported and validated clean. **Determinism proven twice** — two independent exports of live state produced byte-identical shards (every sha256 equal), and a re-export of an unchanged dev home left **zero** changed shard files. **Reviewability proven:** adding one task yields exactly ONE added line in the shard diff (`+{"data":{...},"id":"t-readable"}`). Corruption detection proven for all four modes (tampered content → sha mismatch, deleted shard, undeclared stray file, unparseable row). 28 tests in `test_durability_shards.py`. Gate: `make lint` green (518 files), `make test` **8314 passed** (51.5s — no regression). Docs: `docs/reference/cli.md` gained both subcommands.
  **DEFERRED to S2b (deliberate scope split, not an omission):** the §3 boot-started snapshot SERVICE (nightly tar + hourly incremental + hourly git commit), rolling retention tiers, the `POST /api/durability/restore` + `GET /api/durability/snapshots` endpoints, the monthly restore drill, and the T2-M1/M2/M3 inventory-driven merge amendment (merge dispatch on `StateEntry.merge`, `dry_run` merge plan, non-empty-home auto-detect). Each is independently valuable and independently completable; shipping the format + verification first means the service has something verified to schedule.

---

## Execution log — Session 2b (scheduled snapshot service)

### 2026-07-28 — §3 service + tiered retention + drills: DONE. Restore endpoint + T2-M1..M3 NOT in this session (see below).

New `durability/retention.py` (pure tier math) and `durability/service.py` (the
boot-started loop), plus `dashboard/handlers/durability.py` and three routes.
Durability stops depending on remembering to run a command.

**What landed**

- **Tiered retention** replacing `--keep N`. `keep N` on a nightly schedule means a
  week of history and nothing older, which is the wrong shape: corruption you notice
  immediately needs yesterday, corruption you notice in April needs January. N daily
  + M weekly + Y monthly, one promoted snapshot per period, tiers as UNIONS not
  slices. Measured: 400 nightly snapshots thin to **30 files spanning 12 months**.
  Idempotent, and it only ever deletes files whose names it positively recognizes.
- **The loop**: hourly incremental shard export (bounds a loss to one hour), nightly
  full snapshot + retention, monthly restore drill. Elapsed-time scheduling rather
  than wall-clock, so a laptop asleep at 03:00 gets its snapshot on the next wake
  instead of silently skipping the night. Every job is `single_flight`-guarded across
  processes, runs on an executor thread, and swallows its own failure into an audited
  report — a backup service that can kill a gateway is worse than none.
- **Restore drills**: extract the newest snapshot to a temp dir, `PRAGMA
  integrity_check` every SQLite copy, and report PASS/FAIL through
  `DashboardState.notify` — a FAILURE is `warning`, not `info`, so minimum-severity
  and quiet-hours filters can't hide it. Never touches live state. A failed drill is
  still time-stamped so it warns once instead of every tick.
- **Config** `durability.{auto_backup,keep_daily,keep_weekly,keep_monthly,restore_drills}`
  wired dataclass + `_meta` → `load()` (via `_guard_flag`, so ambiguity keeps backups
  RUNNING) → `to_dict()`. Fail-safe direction throughout: losing scheduled backups to
  an unreadable config value is the exact failure this plan exists to prevent.
- **Endpoints**: `GET /api/durability/status`, `GET /api/durability/snapshots` (the
  archive list WITH the retention plan, so the policy is inspectable before it
  deletes anything), `POST /api/durability/run {job}` for "back up before I do
  something risky".

**Two real bugs found by running it, not by reading it**

1. **Retention matched nothing.** The filename regex assumed
   `personalclaw-snapshot-YYYYmmdd-HHMMSS`; `snapshot.py` actually writes
   `%Y%m%dT%H%M%SZ`. Consequence was silent and total — every file was unrecognized,
   so retention kept all of them while reporting success. Now verified against the
   real output and test-locked.
2. **The incremental export never noticed a memory write.** Every store runs in WAL
   mode, so a committed write lands in the `-wal` sidecar and `memory.db`'s own mtime
   does not move — `_fingerprint` reported "unchanged" through an entire session of
   writes, meaning the hourly job backed up nothing. `_fingerprint` now folds in the
   `-wal`/`-shm` sidecars. **This bug predates this session** (S2a shipped
   `_fingerprint`), and it defeated the single most valuable property of the whole
   plan, so it is worth remembering as a class: fingerprinting a SQLite file means
   fingerprinting its sidecars.

Also corrected: the job reported `len(result.shards)` — the MERGED manifest, which
carries untouched shards forward — making an idle hour look like a full backup. It
now reports entries actually re-exported. And `export_shards(entries=[])` reads the
empty list as falsy and exports EVERYTHING, so the nothing-changed case must return
before that call.

**NOT in this session, deliberately:**

- **`POST /api/durability/restore`.** Replace-restore refuses to run while the
  gateway is up (`_is_gateway_running`), so a useful endpoint needs the
  staged-swap-on-next-boot machinery §3 describes (staged dir + marker file the
  startup path applies before opening stores). Half of that is worse than none — an
  endpoint that appears to restore and doesn't is a trap — so restore stays
  `personalclaw restore` until the staging lands. The handler docstring says so.
- **T2-M1/M2/M3** (inventory-driven merge dispatch, merge-plan dry-run, non-empty-home
  auto-detect). These are `snapshot.py` merge-path work, independent of the schedule,
  and folding them in would have made one unreviewable change. Next Durability
  session.
- The hourly **git commit** of the memory markdown tree (§3's `state-history/` repo).
  Independent of the schedule loop; belongs with the §5 time-travel work that owns the
  git surface.

Tests: `tests/test_durability_service.py`, 49 cases. Validated as a user on an
isolated dev home: the service ran unattended at boot (snapshot + shards + drill
verifying 4 databases), the snapshots endpoint showed the tier plan without deleting
anything, on-demand jobs and both input validations behaved, the drill notification
was delivered, and the gateway log was clean. Full suite 8679 passed; lint clean.

## Execution log — Session 2c (the backups settings surface)

- [2026-07-29][S2c] **DONE.** Closed the frontend leg of §3. S2a/S2b shipped the shard
  format, the scheduled service, tiered retention, restore drills and three endpoints —
  with **no frontend at all**, which left a data-protection surface entirely invisible.

  **The gap was wider than "no control for 5 fields."** `durability.*` had its dataclass,
  `_meta`, `load()` and `to_dict()` legs wired but **no PATCH allowlist entry**, so the
  five shipped fields were file-editable only. And `grep -rn "api/durability" web/src`
  returned **zero** — the `status`, `snapshots` and `run` endpoints had no reader either,
  so a user could not see what the schedule had produced, let alone change it. Both halves
  are now closed: the five dot-paths joined `_EDITABLE_CONFIG` and a new Settings →
  **Backups** panel consumes all three endpoints.

  **`snapshot_dir` is deliberately NOT patchable.** Repointing where backups are written
  is a filesystem decision (permissions, mount points, free space) and a bad value silently
  redirects every future backup. It stays a config-file edit; a test pins that the
  allowlist matches the dataclass *minus* that one field, so neither a new field nor a
  stray entry drifts unnoticed.

  **BUG FOUND BY DRIVING THE REAL ENDPOINT** (unit tests could not have caught it): I
  typed `JobResult.skipped` as `boolean` in `api.ts`, but it is a **reason string**
  (`service.py:55`, e.g. `"another export is already running"`, `"no snapshot to drill
  yet"`). Empty-string is falsy so the truthiness check happened to work, but the type was
  wrong and the panel was throwing away the server's own explanation in favour of a
  generic "skipped". Now typed `string` and the reason is surfaced verbatim. A skip is
  reported as success, not error — a healthy single-flight race is not a failure.

  **Restore is deliberately absent from the panel**, matching the handler module's own
  stated reasoning: replace-restore refuses to run while the gateway is up, so a useful
  restore endpoint needs staged-swap-on-next-boot machinery that does not exist, and a
  button that appears to restore and doesn't is a trap. The panel says restore is
  `personalclaw restore` rather than leaving a user hunting for it.

  Retention caps are bounded rather than open-ended (0 disables a tier; ceilings of
  365/260/120) so a typo cannot budget a decade of archives.

  **Validated as a user** on an isolated dev home (port 10732, never the owner's :10000),
  in a real browser: all five fields PATCHed and persisted to `config.json`; an
  out-of-range value was rejected (`must be between 0 and 365`); `snapshot_dir` was
  refused (`field not editable`); a real snapshot ran from the panel's button
  (`kept 1, pruned 1`) and appeared in the list with its size and the isolated-home path;
  the snapshot list reflected the tiers I had just PATCHed (3/0/1); **clicking the
  "Automatic backups" switch flipped `auto_backup` to `true` on disk** — the full
  five-point round trip proven through the UI, not just the API; the settings-home bento
  card surfaced under a search for "retention" showing live data ("1 snapshot kept",
  "Nightly + hourly, automatic"); zero console errors or warnings.

  Tests: 6 new cases in `test_durability_service.py::TestConfigContract` (54 in that
  file), including an allowlist-matches-dataclass assertion and a save+load round trip
  that pins **`False`** surviving — the interesting direction, since `_guard_flag` keeps
  backups ON for an unreadable value, so a deliberate opt-out must not be read back as
  True. Gate: `make lint` green · `make test` **8888 passed, 0 failed** · web typecheck +
  283 vitest + build green.

  **Pre-existing gap NOT closed here** (recorded, not fixed): `model_calls.jsonl` and
  `spend.json` are both unclaimed by `durability/inventory.py`, so neither is in a
  snapshot or an export. `audit_home()` does not catch them because it has **zero runtime
  callers** and its test builds a synthetic 5-path fixture home. That is a guardrails-store
  question, out of scope for a settings surface.

## Execution log — Session 2d (the declared merge with no executor)

- [2026-08-05][S176] **DONE.** Closed gap (1), which this plan names against itself:
  *"the §1 inventory's `merge` field is specified but merge-restore isn't listed as a
  consumer."* It was worse than an unlisted consumer — the field had **no executor at all**.

  **Swept the declared strategies against their readers.** All five `MERGE_*` constants in
  `durability/inventory.py` have zero readers outside their own declaration, and **14 of the
  38 entries declaring a merge strategy are never named by `snapshot._do_merge`**.
  `_do_merge` covers exactly seven components — `memory`, `crons`, `config`,
  `notifications`, `security`, `workspace`, `skills` — with **no glob or `iterdir`
  catch-all**, so an unlisted component is not degraded, it is absent.

  **Driven, not read.** A snapshot holding a `FROM-SNAPSHOT` run merged into a home holding
  `LIVE-run` came back holding only `LIVE-run`, while the CLI printed **"✅ Merge
  complete"**. That is a durability layer reporting success for a partial recovery — the one
  failure mode a backup system cannot have, since the user's evidence that it worked is the
  message it just printed.

  **Scoped to run history, deliberately.** Of the 14, this is the entry whose loss is
  **unrecoverable**: `config` and `models` can be re-entered by hand, `screenshots` are
  reproducible, but a run's history has no other source. The remaining 13 are recorded in
  the queue as follow-on work rather than swept in here, because each needs its own dedup
  key argued from its own format — a generic line-dedup would be the kind of
  one-size mechanism that later has to be unpicked per component.

  **Dedup key: `run_id`, not the whole line.** The same run round-trips through `to_dict()`
  on both sides, so a reordered key or a re-serialised float would make an identical run
  look new and double it. `_merge_notifications` dedupes on `ts` for the same reason.
  Per-shard, since the store is one file per job; a shard present only in the snapshot is
  copied whole, because an automation the live home has never run must still come back.

  🔴 **My own load-bearing check found that seven passing tests could not distinguish the
  fix from an inert one.** Disabling the call site in `_do_merge` left all 61 tests green —
  every one of them called `_merge_run_history` **directly**. A helper that works perfectly
  and is never invoked is this program's signature defect, and the suite I had just written
  would have shipped it. Added a test that drives `_do_merge` itself; with the wiring
  disabled it now fails, which is what makes the other seven trustworthy.

  **Does NOT rotate, on purpose.** `ScheduleRunStore.rotate_all()` owns retention and runs
  at gateway boot (S175). A second trim here would be another copy of that policy — exactly
  the duplication S175 deleted after finding it had silently reverted S173's per-class
  quota. Pinned by a source assertion, since the defect class is duplication rather than a
  wrong value.

  Also verified: idempotence (a re-run of the same merge imports 0 rows, so a repeated
  restore drill cannot double the history), a malformed line skipped without aborting the
  rest of the shard (a partial recovery beats an aborted one — the same call `count_since`
  makes), and an absent source directory as a clean no-op that creates no empty dir.

  Tests: 8 new cases in `tests/test_snapshot.py` (62 in that file). Gate: `make lint` green
  · full `pytest -n 4 --dist worksteal` green.
