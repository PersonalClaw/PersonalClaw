# ECOSYSTEM-TOOLING — atomic plans

**Source plan:** [`ECOSYSTEM-TOOLING`](../plans/ECOSYSTEM-TOOLING.md)  
**Code:** `ET`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `ET-1` | ✅ | `personalclaw app new` scaffold: provider-registry-derived type table + per-type generators + conformance test | `EXT:PROVIDER-BOUNDARY-COMPLETION:manifest cli.*/loggerRoots fields the scaffold emits`, `EXT:PLATFORM-LEGIBILITY:manifest self-description enabling the runtime-derived type table`, `EXT:CI-RELEASE-ENGINEERING:apps-repo CI the generated output must pass as-generated` | `personalclaw app new --list-types` prints a type table derived at runtime from the provider registry (adding an upstream capability type appears without editing the generator); for EVERY type, generate→`pytest <dir>` passes→local-source install succeeds→provider registers (scripted test loop); each type emits valid app.json (incl. plan-32 cli.*/loggerRoots), a minimal ABC-implementing provider stub, passing stub-based test_provider.py, README, MIT LICENSE; tests/test_app_scaffold.py generates each type into tmp and runs apps-repo checks so scaffold drift = red; V1 timed stranger-shaped `search`-app run recorded (<30 min target) |
| `ET-2` | ⬜ | Template repo content (`personalclaw/app-template`) + app-creation-guide scaffold quickstart | `ET-1` | the scaffold's `--type tool` output plus apps-repo CI config and a clone-to-installed README are prepared in-tree under scratch/ (owner pushes to the org repo); apps-repo docs/app-creation-guide gains a 'minutes to first run' quickstart at the top; quickstart tested verbatim; `app new --from-template` fetches the template repo |
| `ET-3` | ⬜ | Registry data tier: `registry.json` schema + PR-validation workflow in the `personalclaw/registry` repo | `EXT:OSS-OPERATIONS:front-door / community-listing policy the registry front-door adopts` | registry.json schema + validation script (manifest fetch/parse via core apps/manifest.py, repo liveness, license present, SkillScanner dry-run verdict capture) + PR CI running it prepared as registry-repo content with CONTRIBUTING-for-listings and delisting policy; a valid sample PR passes; a `dangerous`-verdict fixture blocks listing with the reason recorded; `warning` lists-with-display (never auto-blocks) |
| `ET-4` | ⬜ | Default-source seeding: ship the registry URL as a removable default git source | `ET-3` | registry URL seeds into app-sources.json on first run behind a config flag; Settings sources UI shows it as a removable default; a fresh dev home lists registry apps in the Store; removing the source persists across restart; the scanner gate at install is unchanged (no new install path) |
| `ET-5` | ⬜ | Store-card provenance line for registry-sourced apps + end-to-end install validation | `ET-4` | registry-sourced Store cards show maintainer + last_validated (+ last_scan_verdict) from the catalog payload, with copy that reads as community-listed not endorsed; local/first-party cards unchanged; V2: list→install→use a registry app end to end holds and the scan gate still runs at install (a deliberate warning-fixture app shows the consent surface) |
| `ET-6` | ⬜ | Build the four scaffold-generated exemplar apps and list them in the registry | `ET-1`, `ET-3`, `EXT:WATCHED-SOURCES:watched-source provider contract the github exemplar implements` | all four exemplars scaffold-generated then minimally implemented (each ≤300 LOC, README-led), each installs from its git URL through the Store and is registry-listed; V3: fork-simulate one exemplar (clone, rename, install) proves the third-party path end to end post-registry. NOTE: watched-source-github's provider shape must track the WATCHED-SOURCES contract; action-home-assistant may be community-validated if no HA instance (Owner task 4) |
| `ET-7` | ⬜ | Bounty board: labeled `bounty` issues for wanted apps, linking scaffold/guide/conformance | `ET-1`, `ET-2`, `EXT:CHANNEL-EXPANSION:channel wants-list (T7.3) + channel scaffold template the channel bounties reference` | ≥6 `bounty` GitHub issues live (channels + providers + sources from the wants-list), each linking the scaffold, guide, and conformance kit; showcase thread seeded in the community surface. Channel bounties draw from CHANNEL-EXPANSION's wants-list (T7.3) and its channel scaffold template |
| `ET-8` | ⬜ | Registry surface on personalclaw.dev generated from `registry.json` | `ET-3`, `EXT:DISCOVERABILITY-LAUNCH:the site sync pipeline this registry surface extends` | static registry pages generated on personalclaw.dev from registry.json (cards: name/types/permissions/verdict/maintainer; per-app page with README fetch); declared permissions + last scan verdict visible pre-install; a rebuild picks up registry changes; V4: a merged registry PR appears on the site after rebuild and card data matches the Store consent surface |

## Atom scopes

### `ET-1` — `personalclaw app new` scaffold: provider-registry-derived type table + per-type generators + conformance test

**Status:** todo

Session 1 (T1.1, T1.2, T1.3, V1); C1 — Scaffold (src/personalclaw/cli_app_new.py, wired via §3.10 subparser)

**Done when:** `personalclaw app new --list-types` prints a type table derived at runtime from the provider registry (adding an upstream capability type appears without editing the generator); for EVERY type, generate→`pytest <dir>` passes→local-source install succeeds→provider registers (scripted test loop); each type emits valid app.json (incl. plan-32 cli.*/loggerRoots), a minimal ABC-implementing provider stub, passing stub-based test_provider.py, README, MIT LICENSE; tests/test_app_scaffold.py generates each type into tmp and runs apps-repo checks so scaffold drift = red; V1 timed stranger-shaped `search`-app run recorded (<30 min target)

### `ET-2` — Template repo content (`personalclaw/app-template`) + app-creation-guide scaffold quickstart

**Status:** todo

Session 1 (T1.4); Design — Template repo; Owner tasks 1 (owner pushes the org repo)

**Done when:** the scaffold's `--type tool` output plus apps-repo CI config and a clone-to-installed README are prepared in-tree under scratch/ (owner pushes to the org repo); apps-repo docs/app-creation-guide gains a 'minutes to first run' quickstart at the top; quickstart tested verbatim; `app new --from-template` fetches the template repo

### `ET-3` — Registry data tier: `registry.json` schema + PR-validation workflow in the `personalclaw/registry` repo

**Status:** todo

Session 2 (T2.1); C2 — registry.json schema; Design — Registry; Owner tasks 1 & 2 (owner creates repo, approves delisting policy)

**Done when:** registry.json schema + validation script (manifest fetch/parse via core apps/manifest.py, repo liveness, license present, SkillScanner dry-run verdict capture) + PR CI running it prepared as registry-repo content with CONTRIBUTING-for-listings and delisting policy; a valid sample PR passes; a `dangerous`-verdict fixture blocks listing with the reason recorded; `warning` lists-with-display (never auto-blocks)

### `ET-4` — Default-source seeding: ship the registry URL as a removable default git source

**Status:** todo

Session 2 (T2.2); C2 — 'registry repo URL ships as a default git source'

**Done when:** registry URL seeds into app-sources.json on first run behind a config flag; Settings sources UI shows it as a removable default; a fresh dev home lists registry apps in the Store; removing the source persists across restart; the scanner gate at install is unchanged (no new install path)

### `ET-5` — Store-card provenance line for registry-sourced apps + end-to-end install validation

**Status:** todo

Session 2 (T2.3, V2); C2 — Store integration; Risks — trust-washing ('community-listed, scanned at install')

**Done when:** registry-sourced Store cards show maintainer + last_validated (+ last_scan_verdict) from the catalog payload, with copy that reads as community-listed not endorsed; local/first-party cards unchanged; V2: list→install→use a registry app end to end holds and the scan gate still runs at install (a deliberate warning-fixture app shows the consent surface)

### `ET-6` — Build the four scaffold-generated exemplar apps and list them in the registry

**Status:** todo

Session 3 (T3.1, V3); Design — Exemplars (watched-source-github, action-home-assistant, inbox-github-notifications, channel-null)

**Done when:** all four exemplars scaffold-generated then minimally implemented (each ≤300 LOC, README-led), each installs from its git URL through the Store and is registry-listed; V3: fork-simulate one exemplar (clone, rename, install) proves the third-party path end to end post-registry. NOTE: watched-source-github's provider shape must track the WATCHED-SOURCES contract; action-home-assistant may be community-validated if no HA instance (Owner task 4)

### `ET-7` — Bounty board: labeled `bounty` issues for wanted apps, linking scaffold/guide/conformance

**Status:** todo

Session 3 (T3.2); Design — Bounty board; Owner tasks 3 (reward model: recognition-only recommended)

**Done when:** ≥6 `bounty` GitHub issues live (channels + providers + sources from the wants-list), each linking the scaffold, guide, and conformance kit; showcase thread seeded in the community surface. Channel bounties draw from CHANNEL-EXPANSION's wants-list (T7.3) and its channel scaffold template

### `ET-8` — Registry surface on personalclaw.dev generated from `registry.json`

**Status:** todo

Session 4 (T4.1, V4); Design — Registry surface (S4)

**Done when:** static registry pages generated on personalclaw.dev from registry.json (cards: name/types/permissions/verdict/maintainer; per-app page with README fetch); declared permissions + last scan verdict visible pre-install; a rebuild picks up registry changes; V4: a merged registry PR appears on the site after rebuild and card data matches the Store consent surface

