# Plan: Lifecycle Doctrine & API Stability — From Clean-Break to Shipped-Product Discipline

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18 from the pre-launch investigation & owner alignment review)
**Created:** 2026-07-18
**Wave:** 0 — first in Wave 0 by design: this plan's outputs (the doctrine + stability tiers) shape how every other plan's changes land.
**Depends on:** nothing. Every subsequent migration-bearing plan (notably INBOX-NOTIFICATIONS-UNIFICATION — the designated first full exercise — LOOPS-EVOLUTION Phase 4, AUTOMATION-SUBSTRATE trigger unification, DISTRIBUTION S4, SECURITY-HARDENING S1) depends on *this*.
**Scope:** author the post-launch engineering doctrine that supersedes the PoC-phase clean-break doctrine, build its small enforcement toolkit (gate registry, migration runner, stability-surface inventory), and define the public API stability tiers. **Soul guardrail:** a doctrine + policy plan with a *small* toolkit — no feature-flag framework, no flag service, no percentage rollouts, no A/B infrastructure. One user per install means a gate is a boolean with a lifecycle. If the toolkit exceeds ~4 new modules, it has overgrown its purpose.

---

## Context (code recon, 2026-07-18)

1. **The codebase already migrates — implicitly and everywhere.** Grep finds migration/upgrade logic scattered across ~12 modules (`memory_record.py`, `history.py`, `vector_memory.py`, `mcp_discovery.py`, `tasks/hierarchy.py`, `prompt_providers/{base,native_provider}.py`, `dashboard/handlers/{mcp,memory,agents}.py`, `stt/provider.py`…) — each an ad-hoc, read-path ("lazy") schema upgrade. This is precisely what the doctrine must end: lazy migrations are invisible, unreversible, untested-as-a-unit, and fire at arbitrary moments. The framework below gives them one home; the retrofit (S3) inventories these sites and either graduates them to explicit migrations or documents them as frozen legacy readers.
2. **Snapshot machinery exists and is the rollback substrate.** `portability.py`: `create_export_zip()` (WAL-checkpointed SQLite backups via `_backup_sqlite`, credential exclusions), `validate_import_zip()`, `apply_import_zip(mode)`. Pre-migration snapshots compose these — no new backup code.
3. **Config-metadata pattern to reuse.** `config/loader.py` fields carry `_meta(label, help, …)` metadata; the round-trip contract is generically enforced by `tests/test_config_roundtrip.py` (to_dict/load parity over dataclass fields). The gate-registry lint and Tier-S inventory test copy this "generic contract test" shape.
4. **Entity-state placement convention.** Entity settings deliberately live *outside* `config.json` (`entity_settings/*.json`, `active_models.json`…). Gate state follows the same convention (`~/.personalclaw/gates.json`) so gates never bloat the config round-trip surface.
5. **Stable-surface raw material.** `sdk/` is 26 modules with an enforced import boundary (`tests/test_apps_import_boundary.py`); the app manifest schema lives in `apps/manifest.py`; `packages/personalclaw-client-py` is the external client. These three + on-disk formats are the Tier-S candidates.
6. **Precedent for loud migrations.** The slack app's `SlackSettings.migrate_from_core()` (one-time, loud, retry-safe) is the culture's existing example of an explicit migration done right — cite it in the doctrine as the shape to generalize.

---

## Design

### A. Change classes (the taxonomy every PR names)

| Class | Definition | Governance |
|---|---|---|
| **R** (refactor) | No behavior/schema/surface change observable outside the module | Clean-break rules unchanged: replace + delete same commit |
| **B** (behavior/state) | Changes runtime behavior or any persisted state under `~/.personalclaw` | Full lifecycle: gate → dual-path → migrate → cleanup |
| **S** (stable surface) | Touches a Tier-S surface (SDK, manifest schema, inbound dialects, on-disk formats, client-py) | Class-B lifecycle **plus** deprecation window + CHANGELOG entry + stability-doc update |

Class is declared in the PR description (OSS-OPERATIONS' PR template gains the field) and, for B/S, in the gate registry entry.

### B. The gate registry (new module: `src/personalclaw/lifecycle/gates.py`)

- **Registry = code; state = data.** Each gate is a frozen dataclass registered at import: `Gate(id, owner_plan, created, change_class, summary, removal_condition, target_removal)`. On/off state lives in `~/.personalclaw/gates.json` (`{gate_id: {enabled, flipped_at}}`) — *not* in `config.json` (convention #4 above; also keeps the round-trip test surface stable).
- **API:** `gate_enabled(id) -> bool` (missing state = the gate's declared default; missing *registration* = raises — an unregistered gate string is a defect, not False). Read at call sites, never cached across turns, so a flip applies on the next read (mirrors `denied_command_patterns()`'s read-time merge).
- **CLI:** `personalclaw gates list` (id, class, default, state, age, target removal) and `personalclaw gates set <id> on|off` (SEL-logged).
- **The lint:** `tests/test_lifecycle_gates.py` — generic, roundtrip-style: every registered gate has all fields; no gate older than its `target_removal` (fails the suite → the expiry is enforced by CI, not memory); every `gate_enabled("…")` call site references a registered id (AST scan, same mechanic as the import-boundary test).

### C. The migration framework (new package: `src/personalclaw/lifecycle/migrations/`)

- **Layout:** one module per migration, `m_YYYYMMDD_<slug>.py`, exporting `Migration(id, plan, description, applies_to (state files/DBs touched), run(ctx), verify(ctx))`. Discovery by filename order; no numeric renumbering ever.
- **Ledger:** `~/.personalclaw/migrations.json` — applied ids with timestamps + outcome. The runner is idempotent: applied ids skip; a failed migration records the failure and **stops the boot with a doctor-style actionable message** (fail-closed: the gateway does not run against half-migrated state).
- **Runner:** `personalclaw migrate` (with `--dry-run` mandatory-first UX: dry-run prints the would-change report; `--apply` executes). Boot integration: gateway startup checks the ledger vs. discovered migrations; pending **required** migrations refuse start with the exact command to run; pending **optional** ones surface in `doctor` + a dashboard notification.
- **Snapshot integration:** `--apply` takes a pre-migration snapshot via `portability.create_export_zip()` into `~/.personalclaw/backups/pre-migration-<id>.zip` before touching state; `personalclaw migrate --rollback <id>` restores it (via `apply_import_zip`) — rollback is *restore*, not reverse-migration (honest about one-way scripts).
- **Rules:** migrations never run lazily on read paths; `run()` is single-process (the runner takes the same single-flight lock the gateway uses); `verify()` must check a concrete post-condition; migrations are tested with fixture homes (the `--seed` fixture machinery already refuses real homes — reuse it).
- **`doctor` integration:** a Migrations section — pending/applied/failed, last snapshot age.

### D. Stability tiers (docs + generated inventory + enforcement)

- **`docs/reference/api-stability.md`:** Tier S = `personalclaw.sdk.*`, app manifest schema (`apps/manifest.py` parsed fields), inbound dialect wire contracts (plan 41 onward), on-disk formats (config.json schema, entity_settings, sessions JSONL, the three DBs' schemas *as read by external tools* — vault files explicitly included since Obsidian reads them), `personalclaw-client` package surface. Tier I = dashboard `/api/*`, WS event vocabulary, core internals — explicitly no contract.
- **Deprecation policy (owner decision recorded here):** Tier-S deprecations remain functional for **two minor releases or 90 days, whichever is longer**, with a runtime warning on use (SEL-logged once per boot, not per call).
- **Generated inventory:** `scripts/gen_stability_inventory.py` walks `sdk/` public names + manifest fields + client-py methods → `docs/reference/stability-inventory.md` (one canonical source, projected); `tests/test_stability_inventory.py` fails when the generated file is stale (drift check) or when a Tier-S name disappears without a deprecation entry (append-only ledger of removals in the doc's frontmatter).
- **client-py:** gains `__stability__ = "tier-s"` markers and joins CI; publication itself is DISTRIBUTION S2.

### E. Document reconciliation

- `vision.md` tenet 2 ("Clean break, always") is rewritten to: clean break *within a change class*, lifecycle governance across classes — with a pointer to `change-lifecycle.md`. Tenet 4 ("One path per concern") gains the clause "no *undeclared* dual paths — every dual path has a registry entry and an expiry."
- `CONTRIBUTING.md` doctrine section: replace the flat clean-break bullets with the class taxonomy + one-paragraph lifecycle summary + "PRs name their class; B/S PRs name their gate and migration."

---

## Sessions (implementation order)

**S1 — Doctrine + gate registry (≈1 session).**
Write `docs/architecture/change-lifecycle.md` (design §A/§B/§C-rules/§E content); implement `lifecycle/gates.py` + `gates.json` state + CLI subcommand + SEL events; add `tests/test_lifecycle_gates.py` (generic lint); reconcile vision.md + CONTRIBUTING.md. *Validation as a user:* register a demo gate, flip it via CLI, watch `gates list` + SEL entry; suite red when a gate is registered past expiry (deliberate fixture).

**S2 — Migration framework (≈1 session).**
Implement `lifecycle/migrations/` (dataclass, discovery, ledger, runner, snapshot+rollback, boot refusal, doctor section, `personalclaw migrate` CLI); fixture-home tests: apply/skip/fail/rollback paths; a deliberately-failing migration proves boot refusal + message quality. *Validation:* run against a seeded fixture home end to end, including `--rollback`.

**S3 — Stability tiers + retrofit sweep (≈1 session).**
Write `api-stability.md`; build `gen_stability_inventory.py` + drift test; annotate client-py. **Retrofit sweep:** inventory the ~12 lazy-migration sites (Context #1) → for each: graduate to an explicit migration (if it still fires for real installs), or mark as frozen legacy reader with a comment + registry note (if it only served pre-publication states — most will, given fresh v0.1.0 installs); file the graduations as concrete migrations. Annotate in-flight roadmap plans with change classes (42, 2, 7, 34-S4, 47-S1 at minimum). CI hooks for the two new generic tests land in CI-RELEASE-ENGINEERING's workflow set (coordinate; tests themselves ship here and run locally regardless).

---

## Contracts & Interfaces (authoritative — this plan OWNS these; see [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md) §4 for the index)

### C1 — `src/personalclaw/lifecycle/gates.py`

```python
from dataclasses import dataclass
from typing import Literal

ChangeClass = Literal["R", "B", "S"]

@dataclass(frozen=True)
class Gate:
    id: str                      # kebab-case, unique, stable forever (e.g. "inbox_unification")
    owner_plan: str              # plan filename stem, e.g. "INBOX-NOTIFICATIONS-UNIFICATION"
    created: str                 # ISO date "YYYY-MM-DD"
    change_class: ChangeClass    # B or S for gated changes (R never needs a gate)
    summary: str                 # one line: what flipping it on does
    removal_condition: str       # prose: when this gate should be deleted
    target_removal: str          # ISO date; the lint FAILS the suite past this date
    default: bool                # value when gates.json has no entry (new installs)

def register(gate: Gate) -> None: ...      # raises ValueError on duplicate id
def all_gates() -> list[Gate]: ...
def gate_enabled(gate_id: str) -> bool: ...  # KeyError if id not registered; else state-or-default
def set_gate(gate_id: str, enabled: bool) -> None: ...  # writes gates.json + SEL event
```

- **State file** `~/.personalclaw/gates.json` (via `config_dir()`, `atomic_write`): `{"<gate_id>": {"enabled": true, "flipped_at": "<ISO>"}}`. Missing file → all defaults. Corrupt file → all defaults + `logger.warning` (this is a config surface → **fail-open** per INTEGRATION-ARCHITECTURE §2.7).
- **Registration site convention:** each consuming plan registers its gate in `gates.py` at import (a `_register_all()` block), NOT in the consumer module — so `all_gates()` is complete without importing every feature. The lint (below) checks every `gate_enabled("x")` string in `src/` has a registration.
- **SEL:** `set_gate` emits `sel().log_api_access(caller="cli:gates", operation=f"gate_set:{id}={enabled}", outcome="completed", source="cli")` (event shape per INTEGRATION-ARCHITECTURE §3.3).
- **CLI (§3.10 pattern):** `personalclaw gates list` → table(id, class, default, state, age_days, target_removal); `personalclaw gates set <id> on|off` → nonzero exit + registered-id list on unknown id.

### C2 — `src/personalclaw/lifecycle/migrations/`

```python
@dataclass(frozen=True)
class Migration:
    id: str                      # "20260801_inbox_alert_fields_to_rules" (== filename stem sans m_)
    plan: str
    description: str
    applies_to: list[str]        # state files/dbs touched, for the dry-run report
    required: bool               # True → boot refuses until applied; False → doctor/notify only
    def run(self, ctx: "MigrationCtx") -> None: ...     # idempotent; raises on failure
    def verify(self, ctx: "MigrationCtx") -> bool: ...  # concrete post-condition check

@dataclass
class MigrationCtx:
    home: Path                   # config_dir(); ALL state access goes through here (test-injectable)
    dry_run: bool
    log: Callable[[str], None]   # append to the dry-run report / apply log
```

- Module file `lifecycle/migrations/m_<id>.py` exports a module-level `MIGRATION: Migration`. Discovery = sorted glob of `m_*.py`, import, read `MIGRATION`. **Never renumber**; ordering is filename-lexical (date prefix).
- **Ledger** `~/.personalclaw/migrations.json`: `{"applied": [{"id","ts","outcome":"ok|failed","error":""}]}`. Applied ids skip. A `failed` entry blocks re-run of the same id until an operator clears it (fail-closed — INTEGRATION-ARCHITECTURE §2.7).
- **Snapshot** before `--apply` batch: `portability.create_export_zip()` → `~/.personalclaw/backups/pre-migration-<batchts>.zip` (add `backups/` to portability's own exclusion set so snapshots don't nest). `--rollback <batchts>` = `portability.apply_import_zip(zip, mode="replace")`.
- **Boot hook** (in `gateway.py` startup, before serving): discover vs ledger; pending `required` → print remedy (`personalclaw migrate --apply`) and `sys.exit(1)`; pending optional → `DashboardState.notify("migration", …)` + doctor line.
- **CLI:** `personalclaw migrate` (dry-run default) `--apply` `--rollback <batchts>` `--list`.

### C3 — Stability tiers
`docs/reference/api-stability.md` (prose contract) + generated `docs/reference/stability-inventory.md` (from `scripts/gen_stability_inventory.py`). Deprecation window: **2 minor releases or 90 days, whichever is longer** (owner-ratified). A Tier-S removal requires a frontmatter deprecation entry in the inventory doc or the drift test fails.

### Integration points
- **Called by:** every class-B/S plan (34 `update_kind_aware`, 42 `inbox_unification`, 47 `credential_keychain`, …) via `gate_enabled()` + a registered `Gate` + (if state changes) a `Migration`.
- **Calls:** `portability.create_export_zip`/`apply_import_zip` (existing, §3), `config_dir`, `atomic_write`, `sel()`.
- **Storage owned:** `gates.json`, `migrations.json`, `backups/pre-migration-*.zip`.
- **CLI added:** `gates`, `migrate`.
- **Docs owned:** `docs/architecture/change-lifecycle.md`, `docs/reference/api-stability.md`, `docs/reference/stability-inventory.md`.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Doctrine + gate registry

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Write the lifecycle doctrine doc per Design §A/§B-rules/§E (change classes, gate lifecycle, migration rules, anti-drift rails; cite `SlackSettings.migrate_from_core` as the loud-migration precedent) | create `docs/architecture/change-lifecycle.md` | doc covers all three classes with the governance table; links from/to vision.md + CONTRIBUTING.md resolve |
| T1.2 | Implement the gate registry: frozen `Gate` dataclass (`id, owner_plan, created, change_class, summary, removal_condition, target_removal, default`), module-level `register(gate)` + `all_gates()` | create `src/personalclaw/lifecycle/__init__.py`, `src/personalclaw/lifecycle/gates.py` | registering a duplicate id raises; `all_gates()` returns registered set; mypy clean |
| T1.3 | Implement state: `gate_enabled(id) -> bool` reading `~/.personalclaw/gates.json` via `config_dir()` + `atomic_write` on set; missing state → registered default; **unregistered id → `KeyError`** | `src/personalclaw/lifecycle/gates.py` | unit tests cover: default, flipped, unregistered-raises, corrupt-file → default + warning log |
| T1.4 | CLI: `personalclaw gates list` (table: id, class, default, state, age, target) and `gates set <id> on\|off` | `src/personalclaw/cli.py` (subcommand wiring, follow existing subcommand pattern), reuse `lifecycle/gates.py` | both commands work against a fixture home; `set` on unknown id exits nonzero with the registered-ids list |
| T1.5 | SEL event on gate flip: `operation="gate_set"`, gate id + new state, `caller="cli"` | `src/personalclaw/lifecycle/gates.py` (import via existing `sel.py` API) | flip writes one SEL entry; entry visible via existing SEL read path |
| T1.6 | Generic lint test: every registered gate fully populated; no gate past `target_removal` (freeze `date.today()` in test); AST-scan `src/` for `gate_enabled("` string args not in registry; forbid module-level `gate_enabled` calls | create `tests/test_lifecycle_gates.py` (model: `tests/test_config_roundtrip.py`'s generic-walk style) | test red on a fixture expired gate + on an unregistered call site; green on tree as-is |
| T1.7 | Reconcile doctrine text: vision.md tenet 2 + tenet 4 clause; CONTRIBUTING doctrine section (class taxonomy + "PRs name their class; B/S name gate + migration") | `docs/vision.md`, `CONTRIBUTING.md` | old absolute clean-break wording gone; both link to change-lifecycle.md |
| V1 | Validation walkthrough: fixture home → register demo gate (test-only) → flip via CLI → `gates list` shows state + age → SEL entry present → expired-gate fixture turns suite red | — | all observations match; ledger entries written |

### Session 2 — Migration framework

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Migration unit: `Migration` dataclass (`id, plan, description, applies_to, required: bool, run(ctx), verify(ctx)`); discovery = import `m_*.py` modules in filename order | create `src/personalclaw/lifecycle/migrations/__init__.py` | discovery returns declared migrations in order; malformed module = load error naming the file |
| T2.2 | Ledger + runner: `~/.personalclaw/migrations.json` (applied: id, ts, outcome); `run_pending(dry_run=True)` default prints would-change report; `--apply` executes `run()` then `verify()`; failure records + halts | `src/personalclaw/lifecycle/migrations/runner.py` | idempotency test: second apply is a no-op; failed `verify()` marks failure and subsequent runs refuse until resolved |
| T2.3 | Snapshot + rollback: before first apply in a batch, `portability.create_export_zip()` → `~/.personalclaw/backups/pre-migration-<batch-ts>.zip`; `--rollback <batch>` restores via `apply_import_zip` | `runner.py` (+ ensure `backups/` in portability exclusions so snapshots don't nest) | rollback restores a fixture home byte-comparable on tracked files; snapshot excluded from its own zip |
| T2.4 | Boot integration + doctor: gateway start checks ledger vs discovery — pending `required` → refuse with exact remedial command; pending optional → `DashboardState.notify` once + doctor line; doctor gains Migrations section (pending/applied/failed/snapshot age) | `src/personalclaw/gateway.py` (startup sequence), `src/personalclaw/cli_doctor.py` | fixture with pending required migration: gateway exits with the message; after `--apply`, boots clean; doctor renders all four fields |
| T2.5 | CLI: `personalclaw migrate` (dry-run default), `--apply`, `--rollback <batch>`, `--list` | `src/personalclaw/cli.py` | help text matches `docs/reference/cli.md` entry added in same commit |
| T2.6 | Fixture-home test suite for the runner: apply/skip/fail/rollback + single-flight lock (reuse gateway lock mechanism) | create `tests/test_lifecycle_migrations.py` | all paths covered; suite green |
| V2 | Validation: seeded fixture home → author a toy migration (renames a field in a scratch JSON store) → dry-run report correct → apply → verify → rollback → original state | — | walkthrough matches; ledger updated |

### Session 3 — Stability tiers + retrofit sweep

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Write the stability-tier doc per Design §D incl. the 2-minors/90-days window and Tier-I disclaimer | create `docs/reference/api-stability.md` | tier tables complete; linked from README + CONTRIBUTING |
| T3.2 | Inventory generator + drift test: walk `sdk/` public names (module `__all__`/public defs), `apps/manifest.py` parsed fields, `packages/personalclaw-client-py` public methods → regenerate `docs/reference/stability-inventory.md`; test fails on staleness or on Tier-S name removal absent a deprecation entry | create `scripts/gen_stability_inventory.py`, `tests/test_stability_inventory.py`, generated doc | deleting a fixture sdk export turns the test red with the name in the message |
| T3.3 | client-py: `__stability__` marker + README note that it tracks Tier S | `packages/personalclaw-client-py/personalclaw_client/__init__.py`, its README | marker present; its tests still green |
| T3.4 | Lazy-migration retrofit sweep: for each of the ~12 modules in Context #1, classify (graduate → write a real `m_*` migration; freeze → in-code comment `# frozen legacy reader (pre-v0.1.0 states) — change-lifecycle.md` + list in change-lifecycle.md appendix) | the 12 modules + new `m_*` files as needed | zero unclassified sites (grep for the comment marker + migration list covers all 12); no behavior change for current-format installs |
| T3.5 | Annotate in-flight plans with change classes: 42 (B), 2 (B), 7 (B), 34-S4 (B), 47-S1 (B) minimum — one line under each plan's Status | the five plan files | each names class + gate id placeholder |
| V3 | Validation: fresh fixture home boots with zero pending migrations; a synthetic old-format fixture triggers exactly the graduated migrations and lands correct | — | both fixtures behave as stated |

## Owner tasks (real world)

1. **Ratify the doctrine text** (S1 output) — this is policy, not code; ~30 min read + approve/amend. In particular the deprecation window (§D: 2 minors / 90 days) is an owner call being defaulted here.
2. **Decide the sessions-JSONL Tier-S question** (open question below) when reviewing S3's stability doc.
3. No external accounts, purchases, or registrations required by this plan.

## Risks & open questions

- **Risk — toolkit creep:** the gate registry + migration runner are each ~1 module; hold the line (soul guardrail). The lint tests are the enforcement, not process documents.
- **Open:** does the sessions JSONL format join Tier S now (external tools may parse it) or after DURABILITY-AND-SYNC's manifest work? Recommendation: Tier I until plan 26 S1-3 land, then promote.
- **Open:** whether `gates set` requires a gateway restart for already-imported module constants — design says read-at-call-site everywhere; S1 verifies no module-level `gate_enabled()` captures exist and the lint forbids them.
