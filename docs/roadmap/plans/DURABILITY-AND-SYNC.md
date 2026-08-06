# DURABILITY-AND-SYNC

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DAS.md`](../atomic/DAS.md) as 10 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
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

## Execution log — Session 2e (the restore side of the gap closure)

- [2026-08-05][S177] **DONE.** Closed the restore half of criterion 1. S176 found one declared
  merge strategy with no executor; the obvious next question was whether the *component lists*
  agreed across capture and restore. They did not.

  **The asymmetry.** S1 widened CAPTURE to the whole inventory (`_everything_paths`), and its own
  comment records why: before it, "a full backup silently dropped the user's entire task board".
  **Both restore modes stayed hand-written seven-component lists.** Measured: eight stores captured
  into the archive — `tasks`, `projects`, `agents`, `prompts`, `workflows`, `artifacts`, `uploads`,
  `entity_settings` — and **zero of eight recovered** by either `--mode merge` or `--mode replace`,
  with both printing a success line. Widening only the capture side made the archive *look*
  complete; a snapshot is worth exactly what its restore returns.

  **Criterion 1's own invocation was rejected.** The criterion says
  `--components everything followed by wiping ~/.personalclaw and restoring reproduces a
  byte-equivalent state`. The CLI answered **"❌ Unknown component: everything"** — so there was no
  way to ask for the task board at all, and the criterion could not have been demonstrated as
  written.

  🔴 **My own fix shipped half-inert, and eight passing tests did not notice.** Only driving the
  criterion's actual drill — snapshot, wipe the home, restore — exposed it: `--components
  everything` restored the task board and **dropped `config.json`, `memory.db`,
  `notifications.jsonl`, `workspace/` and `skills/`**. Cause: I added `everything` as just another
  member of the list, so naming it made `_want` answer False for all seven *named* components. A
  flag whose entire promise is completeness, silently narrowing the restore — on the exact
  invocation the plan tells a user to type. `everything` is now a superset marker read inside
  `_want`, so one definition serves both modes. The drill returns **19/19 files, 0 lost**; the only
  byte deltas are capture-side `config.json` normalisation (pre-existing, verified by hashing the
  archive copy) and a fresh `security_events.jsonl`.

  **Secrets are deliberately NOT restored through the generic path.** `backup_entries()` includes
  them on purpose — "losing the credential store is exactly what a backup should prevent" — but
  re-planting `.env` / `credentials/` / `.local_secret` into a home that may have rotated or
  removed them is the opposite call. Capture writes a local 0600 archive; restore writes into a
  live home, so the two directions do not warrant one default. The named `security` component
  remains the deliberate route (copy-if-missing, `chmod 0600`).

  **Bounded by the inventory allowlist — which matters because `portability.py:305` calls
  `_do_replace` on the IMPORT path**, and an import archive can be another user's export. The
  projection iterates declared entries and asks whether each exists in the archive; it never walks
  the archive copying what it finds. Verified against a tree carrying `evil/payload.sh`,
  `.ssh/authorized_keys` and credential files: only `tasks` was selected.

  Also verified: merge leaves an existing file alone (local state wins — these entries have no
  field-level merge executor yet, so copy-if-missing is the honest half rather than a silent
  overwrite), replace moves the live copy into `pre-restore-<ts>/` before overwriting so the new
  coverage is as recoverable as the old, a targeted `--components memory` stays targeted, and
  derived indexes stay out via the same `backup_entries()` call so the reasoning cannot drift
  between the two directions.

  Tests: 12 new cases in `tests/test_snapshot.py` (74 in that file), including a structural
  assertion that the capture and restore projections cannot diverge except by the stated secret
  exclusion. Each of the four halves — merge wiring, replace wiring, the secret exclusion, the
  `everything` superset — verified load-bearing by unwiring it independently and confirming the
  intended test failed. Gate: `make lint` green · full `pytest -n 4 --dist worksteal` green.

## Execution log — Session 2f (the audit log's merge, and a stale ratchet)

- [2026-08-05][S178] **DONE.** Closed the remaining `append_dedup` entries and re-measured the
  coverage ratchet that had been guarding them.

  **Reachable is not merged.** S177 made every store reachable, but reachably *copy-if-missing* — so
  a file the live home already had was left entirely alone. Measured: `security_events.jsonl` and
  `feedback.jsonl` recovered **zero** snapshot rows on a merge into a populated home.

  🔴 **The obvious fix was the dangerous one.** `security_events.jsonl` is the SEL — HMAC-signed,
  with the key living per-home in `sel_hmac.key`. A generic executor appending the snapshot's rows
  was driven across two homes with different keys: `verify_integrity` returned
  **checked=5, valid=2**, logging "SEL HMAC mismatch" for every imported row. A restore would have
  made the *tamper-evident* audit log report tampering — turning the one surface a user consults to
  ask "was I compromised?" into a false positive they cannot clear except by rotating the chain.

  So the merge is **key-gated and fail-CLOSED**, deliberately unlike the others here. `security`
  restores the key copy-if-missing, which makes the two cases decidable at restore time:

  * a **wiped** home takes the snapshot's key → its rows verify under it (measured 3/3 valid), and
    skipping unconditionally would discard recoverable audit history in the exact scenario a restore
    exists for;
  * a **live** home keeps its own key → the snapshot's rows could never verify, so importing them
    would only manufacture mismatches.

  A missing row beats an unverifiable one, because an audit trail's whole value is that a mismatch
  means something.

  `feedback.jsonl` carries no HMAC, so plain dedup on `FeedbackRecord.id` is safe. Neither executor
  re-applies retention — `feedback._CAP` and `ScheduleRunStore.rotate_all()` own theirs, and a second
  copy is the duplication S175 deleted after finding one had silently reverted S173. `crashes` and
  `sessions` are directories on disk, already union-merged per-file by S177's tree copy; a
  line-dedup executor would be the wrong shape for them.

  🔴 **A pre-existing ratchet caught the change, and was itself stale.**
  `test_the_snapshot_coverage_gap_list_can_only_shrink` correctly flagged `security_events` as closed
  (its own comment said the entry waited on "the dedup story S2 defines" — this is that story). But
  its detection *greps `snapshot.py` for each literal path*, which went blind the moment coverage
  became inventory-derived. Re-measured by driving real archive round-trips: **18 of its 24 "gaps"
  were already covered** — `tags.json`, `crashes`, `sessions`, `loop/loops.db`, `autonudge.json`,
  `mcp.json` and more — and 3 further entries are carried by an ancestor's tree copy.

  On those 3 I asserted in a draft comment that `workspace/knowledge/knowledge.db` and
  `lexicon.db` were captured-but-not-restored, drove it, and was **wrong**: the `workspace` tree copy
  restores all three. They were listed only because neither projection *enumerates* them (nested
  under an already-staged top level), which the ratchet now accounts for.

  The list goes **24 → 4**, and every survivor is `derived=True` — pinned by a new test, so a
  genuinely uncovered non-derived store can no longer be parked there for a reason nobody re-checks.
  A ratchet that over-reports is not the safe direction: the list becomes noise and the one real gap
  hides among eighteen that are not.

  Tests: 6 new cases in `tests/test_snapshot.py` (86 in that file) driving the SEL through its REAL
  writer — a hand-built fixture could not answer whether imported rows verify — plus the rewritten
  ratchet and its new derived-only assertion in `tests/test_portability.py` (51). Every half verified
  load-bearing by unwiring it independently, including the ratchet in both directions (parking a
  non-derived store fails; breaking the store restore fails). Gate: `make lint` green · full
  `pytest -n 4 --dist worksteal` green.

## Execution log — Session 2g (the guard that had never met a real home)

- [2026-08-05][S179] **DONE.** Closed §1's real gap: the manifest was incomplete, and the guard that
  exists to prevent exactly that had never been run against a real home.

  **How it surfaced.** Chasing the `lww_by_updated_at` entries for an executor, `tool_usage.json`
  turned out to have **zero writers** — usage moved into `learning.db` (`learning/usage.py`, "this
  moves it into `learning.db` beside the staging log"). The inventory still declared the retired JSON
  file and did **not** declare the database that replaced it.

  🔴 **`audit_home()` had no runtime caller.** It is the claims-everything guard — "keeps the manifest
  honest … which is precisely how nine directories silently escaped backup before the inventory
  existed" — and every invocation was in `test_durability_inventory.py`, against a hand-built
  eight-path fixture. So a store added *after* the manifest was written could not fail it. A guard
  that only ever runs against its own fixture is testing the fixture.

  Pointed at the real home for the first time: **10 unclaimed paths and 5482 undeclared databases**.
  Driven end to end, `learning.db` (135 KB — the Flywheel's staging log and usage counters),
  `inbox.json`, `spend.json` (which drives the budget caps), `model_calls.jsonl` and
  `session_search.db` were **all absent from a real archive**.

  **Ten entries declared, two of them `derived`.** Both index stores argue their own case:
  `session_search` "holds no truth of its own … better rebuilt than restored", `codegraph` re-parses
  on mtime — and a real home held **5478** codegraph databases. Declaring them as state would ship a
  cache in every snapshot; not declaring them at all was the bug.

  **Five machine-local paths IGNORED rather than declared.** `session_key` and `sessions.json` hold
  live auth material; `machine_id` is what `durability/shards.py` stamps shards with, so a restored
  copy would masquerade as the machine it came from. Ignored is not `secret=True` — a secret entry is
  captured *on purpose* so a backup can restore the credential store, whereas these must not travel
  at all.

  **`workflows/runs.db` was the hazard the DB check exists for.** A live database inside a
  `json_entity_dir` entry, so it was being filesystem-copied rather than staged through the safe
  backup API — "it gets filesystem-copied while open in WAL mode", the exact case S1 fixed for
  knowledge/lexicon/loops. Declaring it routes it to `_safe_copy_db` and excludes it from the tree
  copy.

  🔴 **My own first fix blinded that check, and driving it caught me.** Codegraph's 5478 rows drowned
  the report, so I exempted declared prefixes — keyed off `kind`/`derived`, which silenced a surprise
  DB in `loop/` and `workspace/` too. That is precisely the hazard the check exists for. Narrowed to
  an opt-in `db_container` flag, pinned by a test to codegraph alone; the **pre-existing**
  `test_an_undeclared_database_fails_the_audit` also refused the wide version, which is the second
  time this program's existing tests have judged a draft of mine correctly.

  **The guard now has a caller.** A `durability.inventory` Doctor probe at `CAPABILITY` tier —
  degraded, not failed, because unclaimed state is a backup-coverage gap the user should act on
  rather than a reason to call the install broken, and a lower tier would short-circuit the capability
  packs over an unrelated new file. Read-only (`audit_home` only stats and globs), evidence capped at
  20 rows per list with exact counts alongside, since an unreadable blob is the same failure as no
  evidence. Verified through `run_capability("durability")` and the frontend's generic capability
  card (renders "Durability"; no FE change needed).

  **S178's own ratchets then caught S179 twice**, which is what they were built for: the
  `append_dedup` sweep demanded an executor for the newly-declared `model_calls.jsonl` (added, keyed
  on `AttemptRecord.audit_id`), and the coverage ratchet demanded the two new derived indexes be
  listed with a reason. The third near-identical keyed-JSONL loop was extracted into
  `_merge_keyed_jsonl` — deliberately NOT used for `security_events.jsonl`, whose HMAC-key
  precondition must not be foldable into a generic helper a later caller reaches for the dedup alone.

  Both real homes now audit **ok=True, 0 unclaimed, 0 undeclared databases**.

  Tests: 8 new cases in `test_durability_inventory.py` (22), 4 in `test_resilience_doctor.py` (17),
  plus the two ratchet updates. Every half verified load-bearing by unwiring it independently. Gate:
  `make lint` green · full `pytest -n 4 --dist worksteal` green.

## Execution log — Session 2h (the six sqlite stores with no ATTACH executor)

- [2026-08-05][S180] **DONE.** Finished the declared-strategy sweep: `sqlite_attach_ignore` had seven
  declaring entries and one executor.

  **The gap.** Only `memory.db` had a merge — a hand-written four-table allowlist. S177 made the other
  six reachable, but reachably *copy-if-missing*, so a database the live home already had kept its own
  rows and dropped the snapshot's entirely. Driven across all six — `learning.db`,
  `knowledge/knowledge.db`, `workspace/knowledge/knowledge.db`, `loop/loops.db`,
  `workflows/runs.db`, `workspace/lexicon/lexicon.db` — a snapshot row and a live row went in and
  **only the live row came out**.

  **Generic, because the schemas said so.** I read the real tables in all six out of a long-lived real
  home and the dev home before writing anything: every one carries a primary key or unique index, so
  `INSERT OR IGNORE` deduplicates correctly and a repeated restore drill is a no-op. Six more
  hand-written allowlists would have been six more places for this defect class to recur, and the
  executor reads `sqlite_entries()` so a store declared later merges by default.

  🔴 **FTS5 shadow tables are the trap.** Merging them alongside the rest looks correct once and
  breaks on the second run: 40 documents indexed, then a repeated merge returned **80 rows for 40
  documents** — every search result duplicated — because `_data`/`_idx`/`_docsize` carry segment state
  `INSERT OR IGNORE` cannot reconcile. A restore drill is exactly the thing a user runs twice.

  🔴 **Unwiring each half corrected my own account of the fix.** I had credited the SKIP in the
  docstring; removing it left every test green, because the trailing `rebuild` repairs the shadow
  tables anyway — so the **rebuild** is the load-bearing half (without it: 0 hits for 40 documents).
  The skip still earns its place, and now has its own assertion: it stops the merge writing **160
  rows** of another database's segment state before overwriting them, and it means a future caller
  that rebuilds conditionally cannot silently reintroduce the doubling. Both halves are separately
  pinned.

  **`memory.db` stays on its own executor.** It filters `WHERE is_deleted=0`, and a probe confirmed a
  generic all-tables merge **resurrects a tombstoned row**. That filter is the reason the allowlist
  exists rather than an accident of it, so the call site excludes it explicitly. The six routed here
  were checked for soft-delete columns against both homes: none has one.

  **Containment differs from `_merge_memory` deliberately.** That function re-raises and `_do_merge`
  does not catch it, so a broken `memory.db` aborts the whole restore — correct for the primary store,
  since continuing past it would let a user believe their memory came back. These six are independent,
  so one unreadable file must cost that file only: driven with a poisoned `knowledge.db`, the other
  three merged and the `skills` component still restored afterwards.

  **A locked destination is reachable in production.** `restore_main` refuses while the gateway runs,
  but `portability.apply_import_zip` → `_do_replace` has **no gateway gate**, and the gateway holds
  these databases open in WAL mode. Measured: an uncommitted writer holding `BEGIN IMMEDIATE` makes
  the merge print a skip and import nothing, leaving the destination exactly as it was — the same
  shape `_merge_memory` uses for a per-table failure, not a crash and not a partial write.

  The integrity pre-check needed its own test to be honest: the corrupt-file case passes **without**
  it, because `ATTACH` fails and the rollback already protects the destination. The case only the
  pre-check covers is a file sqlite can open and read while `integrity_check` reports damage, so it is
  driven by forcing the pragma's answer — a hand-corrupted file either still reports `ok` (damage in
  free space) or fails to open, and neither reaches that branch.

  Tests: 9 new cases in `tests/test_snapshot.py` (90 in that file). Every half verified load-bearing
  by unwiring it independently — wiring, rebuild, FTS skip and integrity pre-check each fail their own
  test alone. Gate: `make lint` green · full `pytest -n 4 --dist worksteal` green.

## Execution log — Session 2i (the file-shaped stores, and the sweep's end)

- [2026-08-05][S181] **DONE.** Closed the last two declared strategies. `union_by_id` (25 entries) and
  `lww_by_updated_at` (8) had **zero executors** between them.

  **Only the file-shaped entries were broken.** The directory-shaped ones already get entity-level
  union from S177's per-file tree copy, which is the right shape for them. The nine FILE-shaped entries
  were copy-if-missing, so a file the live home already had kept its contents and dropped the
  snapshot's. Driven with each file's real shape, read out of a long-lived home rather than guessed:
  **8 of 8 lost the snapshot side**, including `hooks.json` (the message-pipeline hooks a user
  configured) and `inbox.json`.

  **Per-file, not one generic merge.** The shapes differ — a wrapped list (`{"hooks": [...]}`,
  `{"items": [...]}`), a bare top-level list (`tags.json`), and maps keyed by date (`spend.json`), by
  tool (`tool_usage.json`) or by a composite (`"<month>|<model>|<compressor>"`). More importantly the
  *semantics* differ, so two executors with an explicit per-file wiring beats one that has to infer
  intent from structure.

  🔴 **`durability_state.json` deliberately abstains.** It holds the scheduler's own last-run marks and
  `service._due()` compares them against an interval. Driven against the real function: a stale
  snapshot's `last_snapshot` reads as **due** while the live home's does not, so importing it would
  re-trigger a snapshot immediately, and a union or min would leave the service permanently believing
  it is overdue. Copy-if-missing is the correct semantic here — a wiped home gets its marks back, a
  live home keeps the ones that describe what actually ran.

  🔴 **`spend.json` is real money.** It is the counter a budget ceiling is compared against, one key
  per `%Y-%m-%d`. Combining a snapshot's dollars into a day the live home already has would move a
  spend decision on the basis of money spent on another machine or in another month — pausing a run
  that had budget left, or the reverse. The map merge is per-key with live winning: a day the live home
  lacks is pure recovery, a day it has is authoritative.

  Live rows win throughout, because merge mode's contract is that local state wins and the snapshot
  only fills gaps — a hook the user has since edited must not revert to the archived version.

  🔴 **My probe fixture was wrong, and the code was right.** `tokenjuice_savings.json`'s `rows` is a
  **dict** in production — verified in both homes and against `savings.py` ("Rows are keyed
  `<month>|<model>|<compressor>`") — but my fixture built a list, which made a correct executor look
  inert. The S180 lesson in reverse: check the fixture against the real writer before believing a
  probe's red, not just before believing its green.

  Also covered: idempotence (import once, then zero), a malformed source or destination leaving the
  live copy untouched (these are hand-editable files, so a half-written one is reachable), a row
  carrying no id skipped (it cannot be deduplicated, so importing it would double on the next drill —
  the same non-idempotence the FTS shadow tables showed in S180), and a wrapper mismatch as a no-op
  rather than a guess that writes a document the owning module cannot read.

  Tests: 8 new cases in `tests/test_snapshot.py` (98 in that file). Both wirings verified load-bearing
  by unwiring each independently. Gate: `make lint` green · full `pytest -n 4 --dist worksteal` green.

  **The declared-strategy sweep is now complete.** All five `MERGE_*` strategies have executors or a
  recorded reason not to: `append_dedup` (S176/S178/S179), `sqlite_attach_ignore` (S180), and
  `union_by_id` + `lww_by_updated_at` (this session).

  `replace_only`'s 20 entries were checked individually rather than waved through as
  "copy-if-missing by definition". They split three ways and every group already lands on
  copy-if-missing: **6 derived** (`memory_index.db`, `memory.faiss`, `codegraph`, …) which
  `backup_entries()` excludes from a backup at all; **6 secret** (`.env`, `credentials`,
  `sel_hmac.key`, …) which the named `security` component restores copy-if-missing at 0600 and which
  S177 deliberately excluded from the generic path; and **8 plain config** files
  (`config.json`, `active_models.json`, `mcp.json`, …) reached either by the `config` component —
  verified to copy only `if s.is_file() and not d.is_file()` — or by the generic store pass, which is
  copy-if-missing by construction. So the strategy is satisfied, for a different reason per group.

## Execution log — Session 2j (the export leg)

- [2026-08-05][S182] **DONE.** The S176-S181 sweep covered snapshot/restore. `portability.py` is the
  other direction, and it reads the inventory **only** for `EXPORT_EXCLUDE`.

  **`export_entries()` had no consumer here.** `create_export_zip` named **18 of 53** exportable
  entries from three hand-written lists. Driven on a home seeded across the inventory, the zip came
  out holding **three files** — `config.json`, `memory.db`, `MANIFEST.json` — and **30 stores of the
  user's own data were absent**: tasks, projects, agents, prompts, workflows, artifacts,
  entity_settings, sessions, crashes, `inbox.json`, `spend.json`, `security_events.jsonl` and more.
  This is the feature the plan describes as *"give me everything PersonalClaw knows about me" is one
  click*.

  The source's own comments record the same defect being closed one entry at a time — `triggers.json`
  ("a snapshot of a home with two automations … captured `config.json` ALONE"), then `cron-history`.
  Deriving the remainder from the inventory is what stops the next store being forgotten. The three
  literal lists are **subtracted, not replaced**: each encodes a per-entry reason (the safe sqlite
  backup API, the `skills/auto` skip, the `crons.json` read-only note) that a generic pass would lose.

  🔴 **My own widening introduced the WAL hazard, and driving it caught me.** `workflows/runs.db` and
  `loop/loops.db` sit INSIDE declared trees, so the new tree walk's `rglob` reached them — and a
  filesystem copy of a live WAL store takes the `.db` without its `-wal`. Measured on a store with
  2000 committed rows and a 237 KB uncheckpointed WAL, the raw copy was not merely short: it was
  **unusable** (`no such table: runs`). Every declared database now goes through `_wal_checkpoint` +
  `_backup_sqlite`, and the tree walk skips `*.db` and its sidecars — the same split the snapshot path
  makes with `_tree_ignore_dbs`. Re-driven: 2000/2000 rows survive, zero sidecars in the zip.

  🔴 **The IMPORT side was a fourth hand-written list.** Widening the export is only half a round
  trip: driven end to end, an export carrying `tasks/`, `projects/` and `inbox.json` imported **none
  of them**, reporting `['memory (copied)', 'config (restored)']`. Now widened too — copy-if-missing,
  matching `_copy_tree_no_overwrite` beside it, because the archive came from somewhere else and the
  receiving home's own state is authoritative. The snapshot restore path owns the richer per-store
  merges; an import is deliberately the conservative direction. The existing per-entry decisions are
  untouched (`learning.db` copy-only so evidence is not double-counted, `feedback.jsonl` copy-only,
  `cron-history` no-overwrite).

  **A real asymmetry recorded rather than flipped:** a snapshot carries `uploads/` (verified in both
  `_everything_paths` and `_extra_restore_paths`) and an export excludes it via `EXCLUDE_DIRS`. That
  is defensible — a snapshot is a local 0600 archive of this machine, while an export is the artifact
  a user hands to another machine or attaches to a bug report, and uploads are arbitrary
  user-supplied binaries of unbounded size. Changing an export's contents is a product decision, not
  a sweep's, so the reasoning is now written down and pinned by a test instead of being flipped by the
  next reader.

  **Security.** An export is the artifact most able to leak, so the no-secret assertion is made on the
  **bytes** of every zip member rather than on filenames: `.env`, `.local_secret`, `sel_hmac.key`,
  `telemetry_salt`, `session_map.json`, `session_key` and `credentials/` all stay out. The projection
  reads `export_entries()`, which excludes `secret=True` and `derived=True` by construction, so a
  credential cannot arrive here by being newly declared.

  Tests: 8 new cases in `tests/test_portability.py` (59 in that file), including a full
  export→import round trip and a nested live database asserted on ROWS read back, not on a filename.
  All three halves verified load-bearing by unwiring each independently.

  **Deviation:** a blanket line-rewrapper I used for lint broke pre-existing `# noqa: E501` strings in
  the test file (an unterminated multi-line SQL literal). Recovered by restoring the file from git and
  re-appending only my own block from the stash — recorded because the lesson is that a formatting
  sweep must not touch lines it did not author.

## Execution log — Session 2k (the merge plan)

- [2026-08-05][S183] **DONE.** Closed gap (2), and stated gap (3) where a user can see it.

  **The defect.** `--dry-run` previewed a restore by listing the filenames in the archive, and **0 of
  12** `_merge_*` helpers took a `dry_run` parameter. Driven side by side on one fixture: the dry run
  listed three filenames, while the merge imported one notification, recovered two stores, and left
  `config.json` untouched. The preview answered a different question from the one a user about to
  merge into their own home is asking.

  That also closes **gap (3)** — "config merge is copy-if-missing per file … this contract must be
  stated and tested, not incidental". It was true but *unstated*, so nothing told the user their
  config was protected. The plan now prints
  `KEEP config.json [replace_only] — copy-if-missing; never overwritten`.

  🔴 **DEVIATION from T2-M2, deliberately.** The task row specifies *"`dry_run` threaded through every
  `_merge_*`"*. I did not build that. Twelve flags are twelve chances for the preview to drift from
  the act, and S176–S182 had just added six more executors to thread — the row was written when there
  were four. Instead `merge_plan()` is **computed from the same projections `_do_merge` uses**, and
  the sqlite path list was extracted into a shared `_attach_merge_paths()` so the two cannot disagree
  about which databases participate. Pinned by a test asserting both read that helper.

  The plan's own done-when is met either way: "plan counts match the subsequent real merge exactly on
  the same fixture; nothing written in plan mode (dir hash unchanged)". Verified plan-vs-act —
  `MERGE notifications.jsonl [append_dedup]`, `COPY tasks`, `COPY inbox.json`, `KEEP config.json` —
  and the act did exactly that. Write-freeness is asserted by hashing the home before and after the
  dry run, not by inspection.

  **Replace mode keeps a wholesale preview**, plus the thing a user actually needs told: where the
  current state goes (`pre-restore-<timestamp>/`). A per-entry merge table there would be misleading,
  because replace is wholesale by definition — the recoverability is the safety property.

  🔴 **Two of my own S180 tests went red and were right to.** They asserted on a substring of
  `_do_merge`'s source, which no longer holds the path list after the extraction. Updated to assert
  the shared helper's **output** — the behaviour — rather than one caller's text. A test coupled to
  where code lives rather than what it does will fail every honest refactor.

  Also verified: the plan respects `--components` (a plan for `--components memory` must not list the
  whole home, or the preview overstates a targeted restore), and only entries the archive actually
  holds appear (otherwise the plan becomes a manifest of the inventory rather than of this snapshot).

  **Not in scope, recorded:** `durability/shards.py` has `export_shards`, `validate` and
  `export_and_validate` but **no import/restore function at all** — the hourly shard export that
  "bounds a loss to one hour" is write-only, so those shards are recoverable only by archaeology. That
  is Session 3's `pull → merge-import → export-union → push`, not a gap in this one; noted here so it
  is not rediscovered as a surprise.

  Tests: 6 new cases in `tests/test_snapshot.py` (105 in that file). Both halves verified load-bearing
  by unwiring each independently. Gate: `make lint` green · full `pytest -n 4 --dist worksteal` green.

## Execution log — Session 2l (the restore endpoint, and the non-emptiness test that was wrong)

- **DONE (T2-M3 + the API restore leg).** Two defects, each found by driving a real home rather than
  reading the code.

  🔴 **The auto-detect default was keyed on one file.** `restore_main` chose its mode with
  `"merge" if (pc / "memory.db").is_file() else "replace"`. Measured on a home holding six declared
  stores but no embeddings (tasks, projects, workflows, entity_settings, inbox.json, triggers.json):
  the home read as EMPTY and defaulted to REPLACE, so `tasks/mine.json` and the user's automation were
  moved into `pre-restore-<ts>/` and the snapshot's copies took their place. Recoverable, so a wrong
  DEFAULT rather than data loss — which is exactly the plan's stated fear ("replace-mode restores
  there destroy the newer half"). Fixed with `home_is_populated()`, which asks the inventory: any
  non-derived, non-secret declared store present counts, so the home's emptiness no longer hinges on
  whether it ever embedded anything. `config.json`/`session_map.json`/`machine_id` are excluded — the
  first is written at first boot, so counting it would push a genuine first-time restore onto the
  merge path; the other two are machine-local bookkeeping. Secrets are excluded because the list is
  surfaced over the API.

  **The API had no restore.** `POST /api/durability/restore` was named in T2-M3 and absent — the
  dashboard could take a backup and could not restore one. Added, mirroring the CLI exactly by sharing
  `merge_plan()`: omitting `mode` returns the plan and writes nothing (safe default for an endpoint
  that can overwrite a home — replace is therefore always deliberate), and `restore_apply` refuses
  while the gateway runs just as the CLI does. No `--force`/`force` mirror on purpose: overriding the
  running-gateway guard is a local operator decision at a terminal, not an HTTP surface. Path
  containment on the archive name (must resolve inside the snapshot dir) so a caller cannot point a
  restore at an arbitrary tar.

  🔴 **`triggers.json` was never in the inventory.** `triggers/store.py` is "the one trigger store …
  absorbing crons.json / hooks.json / event_triggers.json", hand-listed in both `snapshot.py` and
  `portability.py`, yet no inventory entry declared it — so it was invisible to `home_is_populated`,
  the coverage ratchet, and every projection S176-S183 built. `audit_home()` WOULD have flagged it
  (verified: it reports `triggers.json` unclaimed on a home that has one); S179 audited both real
  homes clean only because neither has migrated to the store yet, so the guard was right and the
  population was the gap — the same fixture-versus-reality shape S179 was about. Added as
  `union_by_id`; `crons`/`hooks`/`event_triggers` re-annotated legacy read-only.

  Tests: 6 new cases in `tests/test_snapshot.py` (111 in that file) — populated-without-memory.db
  proposes MERGE, fresh install still REPLACE, the populated list names no secret, a MERGE into a
  populated home keeps local work, the plan writes nothing (home hashed before/after), apply refuses
  under a running gateway. DEVIATION: regenerated `src/personalclaw/reference/{index,routes}.md` — the
  offline reference is route-derived and the new endpoint is a real route, so `test_agent_reference`
  correctly went red until regenerated. Gate: `make lint` green (1337 files) · full
  `pytest -n 4 --dist worksteal` → 16401 passed, 29 skipped, 12 xfailed.
