# LIFECYCLE-DOCTRINE — atomic plans

**Source plan:** [`LIFECYCLE-DOCTRINE`](../plans/LIFECYCLE-DOCTRINE.md)  
**Code:** `LD`  
**Source status:** proposed

LIFECYCLE-DOCTRINE is fully unbuilt and owner-deferred to the engine->convergence boundary; decomposed into 4 todo atoms (gate registry, migration framework, stability tiers, retrofit sweep) with no cross-plan blocking deps.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `LD-1` | ⬜ | Doctrine doc + gate registry (module, gates.json state, CLI, SEL, lint) | — | src/personalclaw/lifecycle/{__init__,gates}.py implement frozen Gate dataclass + register/all_gates/gate_enabled/set_gate with ~/.personalclaw/gates.json state (missing->default, unregistered id->KeyError, corrupt->default+warn); `personalclaw gates list\|set <id> on\|off` work against a fixture home and SEL-log the flip; tests/test_lifecycle_gates.py is green on the tree but red on a fixture expired-past-target_removal gate and on an unregistered gate_enabled() call site; docs/architecture/change-lifecycle.md written and vision.md tenet 2/4 + CONTRIBUTING doctrine section reconciled with resolving cross-links |
| `LD-2` | ⬜ | Migration framework (dataclass, discovery, ledger, runner, snapshot/rollback, boot refusal, doctor, CLI) | `LD-1` | src/personalclaw/lifecycle/migrations/ implements Migration dataclass + filename-order discovery of m_*.py, ~/.personalclaw/migrations.json ledger (idempotent skip, failed entry blocks re-run), pre-apply snapshot via portability.create_export_zip() and --rollback via apply_import_zip, gateway boot refuses on pending required migration with exact remedy + doctor Migrations section; `personalclaw migrate` (dry-run default, --apply, --rollback <batch>, --list) matches cli.md; tests/test_lifecycle_migrations.py covers apply/skip/fail/rollback + single-flight lock; end-to-end validation on a seeded fixture home including rollback |
| `LD-3` | ⬜ | Stability tiers doc + generated inventory + drift test + client-py markers | — | docs/reference/api-stability.md carries the Tier-S/Tier-I tables + the 2-minors/90-days deprecation window + Tier-I disclaimer, linked from README + CONTRIBUTING; scripts/gen_stability_inventory.py walks sdk/ public names, apps/manifest.py parsed fields, and packages/personalclaw-client-py public methods to regenerate docs/reference/stability-inventory.md; tests/test_stability_inventory.py turns red on staleness and on a Tier-S name removed without a frontmatter deprecation entry; client-py gains __stability__ = 'tier-s' marker + README note with its own tests still green |
| `LD-4` | ⬜ | Lazy-migration retrofit sweep + in-flight plan change-class annotations | `LD-1`, `LD-2` | each of the ~12 lazy-migration modules from Context #1 is either graduated to an explicit lifecycle/migrations/m_*.py migration or marked '# frozen legacy reader (pre-v0.1.0 states) - change-lifecycle.md' and listed in the change-lifecycle.md appendix (grep proves zero unclassified sites, no behavior change for current-format installs); plans 42/2/7/34-S4/47-S1 each annotated under Status with change class + gate-id placeholder; a synthetic old-format fixture triggers exactly the graduated migrations and lands correct while a fresh fixture boots with zero pending |

## Atom scopes

### `LD-1` — Doctrine doc + gate registry (module, gates.json state, CLI, SEL, lint)

**Status:** todo

Sessions S1; Design §A (change classes), §B (gate registry), §E (doc reconciliation); Contracts C1; Task breakdown Session 1 (T1.1-T1.7, V1)

**Done when:** src/personalclaw/lifecycle/{__init__,gates}.py implement frozen Gate dataclass + register/all_gates/gate_enabled/set_gate with ~/.personalclaw/gates.json state (missing->default, unregistered id->KeyError, corrupt->default+warn); `personalclaw gates list|set <id> on|off` work against a fixture home and SEL-log the flip; tests/test_lifecycle_gates.py is green on the tree but red on a fixture expired-past-target_removal gate and on an unregistered gate_enabled() call site; docs/architecture/change-lifecycle.md written and vision.md tenet 2/4 + CONTRIBUTING doctrine section reconciled with resolving cross-links

### `LD-2` — Migration framework (dataclass, discovery, ledger, runner, snapshot/rollback, boot refusal, doctor, CLI)

**Status:** todo

Sessions S2; Design §C (migration framework); Contracts C2; Task breakdown Session 2 (T2.1-T2.6, V2)

**Done when:** src/personalclaw/lifecycle/migrations/ implements Migration dataclass + filename-order discovery of m_*.py, ~/.personalclaw/migrations.json ledger (idempotent skip, failed entry blocks re-run), pre-apply snapshot via portability.create_export_zip() and --rollback via apply_import_zip, gateway boot refuses on pending required migration with exact remedy + doctor Migrations section; `personalclaw migrate` (dry-run default, --apply, --rollback <batch>, --list) matches cli.md; tests/test_lifecycle_migrations.py covers apply/skip/fail/rollback + single-flight lock; end-to-end validation on a seeded fixture home including rollback

### `LD-3` — Stability tiers doc + generated inventory + drift test + client-py markers

**Status:** todo

Design §D (stability tiers); Contracts C3; Task breakdown Session 3 (T3.1-T3.3)

**Done when:** docs/reference/api-stability.md carries the Tier-S/Tier-I tables + the 2-minors/90-days deprecation window + Tier-I disclaimer, linked from README + CONTRIBUTING; scripts/gen_stability_inventory.py walks sdk/ public names, apps/manifest.py parsed fields, and packages/personalclaw-client-py public methods to regenerate docs/reference/stability-inventory.md; tests/test_stability_inventory.py turns red on staleness and on a Tier-S name removed without a frontmatter deprecation entry; client-py gains __stability__ = 'tier-s' marker + README note with its own tests still green

### `LD-4` — Lazy-migration retrofit sweep + in-flight plan change-class annotations

**Status:** todo

Design §C (retrofit) + Context #1 (~12 lazy-migration sites); Task breakdown Session 3 (T3.4, T3.5, V3)

**Done when:** each of the ~12 lazy-migration modules from Context #1 is either graduated to an explicit lifecycle/migrations/m_*.py migration or marked '# frozen legacy reader (pre-v0.1.0 states) - change-lifecycle.md' and listed in the change-lifecycle.md appendix (grep proves zero unclassified sites, no behavior change for current-format installs); plans 42/2/7/34-S4/47-S1 each annotated under Status with change class + gate-id placeholder; a synthetic old-format fixture triggers exactly the graduated migrations and lands correct while a fresh fixture boots with zero pending

