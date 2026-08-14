# Atomic plan catalog

The roadmap's plans were too large and too interdependent: parts of a plan would finish, then the rest would block on *another* plan, so ten-plus plans sat in flight at once and no status read was accurate.

This catalog is the fix. Every plan is decomposed into **atoms**: one coherent feature, executable start-to-finish in a single go. The cut line is exactly the dependency seam — anything that would force you to pause an atom and go execute other work is instead its own atom with an explicit dependency edge.

**640 atoms** across **70 plans** — 356 done, 284 remaining. 876 dependency edges.

## How to use it

1. `dag.json` is the machine-readable source; the roadmap dashboard renders it (tiers, ready frontier, validation).
2. **Start only from the ready frontier** — atoms whose dependencies are all `done`. Those need nothing else in flight.
3. One atom per branch/PR. Mark it `done` in `dag.json` when its PR lands.
4. `<CODE>.md` holds the human-readable atoms for one source plan.

## Startable now

- `AAP-1` **Phase 1 validation — claude-code end-to-end sweep** — AAP
- `AAP-2` **Phase 1 validation — codex end-to-end sweep** — AAP
- `AAP-3` **Phase 1 validation — kiro-cli end-to-end sweep** — AAP
- `AE-10` **Split-view iterate panel: ChatEmbed beside the detail view; preview refreshes when artifact_update lands a new version** — AE
- `AG-9` **Apps-repo guardrails follow-ons: native structured_output + channel send() live-writes (cross-repo)** — AG
- `AG-11` **Deferred profile/trust enforcement behaviors awaiting engine consumers** — AG
- `AP-4` **Pack kinds: agent/roster packs, prompt-card importer, bundled Domain OS packs, one-link serialization** — AP
- `AP-5` **Outbound multi-tool export (ExternalFormat + 3 renderers + byte-identical golden tests)** — AP
- `AP-6` **Inbound skill-catalog importer (CatalogMarketplace via install_guarded chokepoint)** — AP
- `APE-1` **Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent)** — APE
- `APE-4` **quality manifest block + Store card rendering + first-party CI verification** — APE
- `APE-5` **Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles** — APE
- `APE-10` **storageRead/storageShared manifest pair + consent + read-only env mount + sdk/util.shared_app_data_dir** — APE
- `APE-11` **UI SDK exports design-system shell primitives + tokens + uiCapabilities block + generative-widget path** — APE
- `AR-1` **Un-defer the plan: ship the council precursor, re-confirm demand, and resolve the 6 design questions into contracts** — AR
- `AS-2` **Chatless refresh: layout/data split render transform + ttl refresh + freshness/error chips** — AS
- `AS-3` **Artifact iteration: EDITMODE tweak controls + click-annotation corrections** — AS
- `AS-5` **Widget action bridge: extract useWidgetActionBridge, route non-chat hosts, harden** — AS
- `AS-7` **macOS menu-bar tray companion (thin client app)** — AS
- `AS-8` **Mission Control preset: four attention lanes with inline resolution** — AS

## Validation problems

- **2 dependency cycle(s)** — must be broken
- 1 unresolved cross-plan reference(s)

## Execution order (topological)

Remaining atoms, dependency-respecting:

```
AAP-1 → AAP-2 → AAP-3 → AAP-4 → AAP-5 → AAP-6 → AAP-7 → AAP-8 → AAP-9 → AAP-10 → AE-1 → AE-2 → AE-3 → AE-4 → AE-5 → AE-6 → AE-7 → AE-8 → AE-9 → AE-10 → AG-1 → AG-2 → AG-3 → AG-4 → AG-5 → AG-6 → AG-7 → AG-8 → AG-9 → AG-10 → AG-11 → AG-12 → AP-1 → AP-2 → AP-3 → AP-4 → AP-5 → AP-6 → AP-7 → APE-1 → APE-2 → APE-3 → APE-4 → APE-5 → APE-7 → APE-8 → APE-9 → APE-10 → APE-11 → APE-6 → APE-12 → AR-1 → AR-2 → AR-3 → AR-4 → AR-5 → AR-6 → AR-7 → AR-8 → AR2-1 → …
```
