# Atomic plan catalog

The roadmap's plans were too large and too interdependent: parts of a plan would finish, then the rest would block on *another* plan, so ten-plus plans sat in flight at once and no status read was accurate.

This catalog is the fix. Every plan is decomposed into **atoms**: one coherent feature, executable start-to-finish in a single go. The cut line is exactly the dependency seam — anything that would force you to pause an atom and go execute other work is instead its own atom with an explicit dependency edge.

**602 atoms** across **73 plans** — 214 done, 388 remaining. 830 dependency edges.

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
- `AG-5` **Wire SafetyProfile / egress-tier into dispatch seams + spawn (close inert control)** — AG
- `AG-6` **S5.1 earned-autonomy rung ladder core (guardrails/autonomy.py)** — AG
- `AG-9` **Apps-repo guardrails follow-ons: native structured_output + channel send() live-writes (cross-repo)** — AG
- `AP-1` **Pack format + export core (dependency-closure walker + two-layer redaction)** — AP
- `APE-1` **Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent)** — APE
- `APE-4` **quality manifest block + Store card rendering + first-party CI verification** — APE
- `APE-5` **Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles** — APE
- `APE-7` **Update surfacing: catalog.updates_available() + card/nav badges + kind-registered notification** — APE
- `APE-8` **Fix-with-AI: InstallResult.log_excerpt + Store error button -> prefilled fenced chat** — APE
- `APE-9` **appMessaging permission + /api/apps/message gateway broker (double-declaration, fence, cap, SEL)** — APE
- `AR-1` **Un-defer the plan: ship the council precursor, re-confirm demand, and resolve the 6 design questions into contracts** — AR
- `AR2-8` **Muted-state row + Unmute affordance on the agent detail page** — AR2
- `AS-1` **Composable home: dashboard-as-views registry + Overview preset + pinning + AmbientConfig** — AS
- `AS-3` **Artifact iteration: EDITMODE tweak controls + click-annotation corrections** — AS
- `AS-4` **Generative-UI core: typed component registry + streaming genui renderer + visualize primitive** — AS
- `AS-7` **macOS menu-bar tray companion (thin client app)** — AS

## Validation problems

- **2 dependency cycle(s)** — must be broken
- 1 unresolved cross-plan reference(s)

## Execution order (topological)

Remaining atoms, dependency-respecting:

```
AAP-1 → AAP-2 → AAP-3 → AE-10 → AG-5 → AG-6 → AG-9 → AP-1 → APE-1 → APE-4 → APE-5 → APE-7 → APE-8 → APE-9 → AR-1 → AR2-8 → AS-1 → AS-3 → AS-4 → AS-7 → BA-1 → CA-1 → CA-3 → CA-4 → CA-6 → CATO-1 → CC-4 → CC-5 → CC-7 → CC-8 → CE-1 → CRE-7 → DAS-6 → DAS-9 → DC-1 → DC-2 → DCU-1 → DCU-2 → DFE-1 → DFE-2 → DIST-11 → DIST-12 → DL-4 → DL-6 → DL-9 → DSC-11 → DSC-12 → EA-1 → EI-1 → EI-11 → EI-12 → EI-5 → ES-1 → ET-1 → ET-3 → FM-1 → FS-4 → HC-1 → HC-3 → HC-4 → …
```
