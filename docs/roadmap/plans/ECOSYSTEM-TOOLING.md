# Plan: Ecosystem Tooling — Scaffold, Registry, Exemplars

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "yes, please plan for this")
**Created:** 2026-07-18
**Wave:** 2 (S1-2: scaffold + registry data tier) + 3 (S3-4: exemplars, bounties, registry surface)
**Depends on:** OSS-OPERATIONS (front-door policy), PLATFORM-LEGIBILITY S1-3 (manifest self-description), CI-RELEASE-ENGINEERING S2 (apps-repo CI the template inherits), plan 32 (manifest gains `cli.*`/`loggerRoots` — scaffold emits them).
**Scope:** collapse app-author time-to-first-run to minutes and give PersonalClaw-native apps a discovery surface. **Soul guardrail:** the registry starts as **data in a git repo** the Store consumes — no registry service, no accounts, no upload pipeline. The scanner-gated install path remains the only install path; the registry adds discovery, never a bypass. Scaffold output must pass the apps-repo CI *as generated* — a template that needs fixing is a defect.

---

## Context (code recon, 2026-07-18)

- CLI uses argparse subparsers (`cli.py:205+`, existing two-level pattern e.g. `cron`/`spawn`/`security` subcommands) — `personalclaw app new` slots in cleanly.
- Sources API is live: git sources (`/api/apps/sources`) list-without-clone, shallow-clone-at-install behind the scanner; local sources (`/api/apps/local-sources`). A registry = **one well-known default git source** entry — near-zero core change.
- The worked example (`third-party-apps/demo-dashboard`) exercises every platform surface (backend, UI, storage, api/events/cron/agent permissions, MCP server); the app-creation guide is 322 lines. Manifest schema (`apps/manifest.py`) validates name/semver; capability types: model/search/tool/channel/action/skills-marketplace/inbox-source/backend+UI.

## Design

- **Scaffold:** `personalclaw app new <name> --type <capability>` — interactive when flags absent; emits `app.json` (valid, incl. plan-32 fields), provider stub for the chosen type (each type's stub = minimal compilable implementation of its ABC with one TODO-free example method), `test_provider.py` (passing, stub-based like the first-party pattern), `README.md` (front-matter template), `LICENSE` (MIT prefilled). Types map to real sdk contracts — the generator's type table is **derived from the provider registry**, not hardcoded (self-description tenet). Also `--from-template` fetching the template repo for fork-and-go users.
- **Template repo (`personalclaw/app-template`):** the scaffold's `--type tool` output committed + apps-repo CI preconfigured + a README walking the author from clone to installed-in-Store in minutes.
- **Registry (`personalclaw/registry`):** `registry.json` — `[{name, repo, types, permissions_declared, license, maintainer, added, last_validated}]`; PR-based listing; CI validation on PRs: manifest fetch+parse, repo exists, license present, scanner dry-run verdict recorded into the PR (never auto-blocking listing on `warning` — the verdict is *displayed*; `dangerous` blocks listing). Store integration: the registry repo URL ships as a default git source (config seed + Settings toggle to remove it); listings render with the same consent surface as any source.
- **Exemplars (org repos, scaffold-generated):** `watched-source-github` (a watched-source provider — coordinates with WATCHED-SOURCES contract timing), `action-home-assistant` (action provider calling HA webhooks), `inbox-github-notifications` (inbox source), `channel-null` (the guide's teaching channel, conformance-kit-passing). Each: small, real, forkable, listed in the registry.
- **Bounty board:** labeled issues (`bounty`) per wanted app (channels from plan 40 T7.3, providers, sources) with the scaffold + guide + conformance links; showcase channel in the community surface.
- **Registry surface (S4):** static generation on personalclaw.dev from `registry.json` — cards show name, types, **declared permissions and last scan verdict pre-install** (publishing the consent surface).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Scaffold (`src/personalclaw/cli_app_new.py`, wired via §3.10 subparser)
`personalclaw app new <name> [--type <t>] [--from-template]`. The type table is **derived from the provider registry** (enumerate capability types + their ABC module at runtime — not hardcoded, self-description tenet). Each type emits: `app.json` (valid, incl. plan-32 `cli.*`/`loggerRoots` when relevant), a provider stub implementing that type's ABC minimally, a passing stub-based `test_provider.py` (the `sys.modules` stub pattern, §CI), `README.md`, `LICENSE`. **Generated output must pass apps-repo CI as generated** (test `test_app_scaffold.py`).

### C2 — `registry.json` schema (in the `personalclaw/registry` repo)
```jsonc
{
  "apps": [
    { "name":"…", "repo":"https://github.com/…", "types":["search"],
      "permissions_declared":["network"], "license":"MIT",
      "maintainer":"handle", "added":"<ISO>", "last_validated":"<ISO>",
      "last_scan_verdict":"clean|warning|dangerous" }   // from a scanner dry-run at validation
  ]
}
```
PR validation workflow: manifest fetch+parse (core `apps/manifest.py`), repo liveness, license present, scanner dry-run verdict recorded; `dangerous` blocks listing, `warning` lists-with-display. The registry repo URL ships as a **default git source** (existing `/api/apps/sources` mechanism, §3.8 — no new install path; scanner gate unchanged at install).

### Integration points
- **Calls:** provider registry (type table), `apps/manifest.py` (validation), `SkillScanner` dry-run (verdict), the sources-seeding path.
- **Consumed by:** 40 (a `channel` scaffold template + bounties), 47 (registry records signer identity per listing), 36 (registry surface on the site).
- **Depends on:** 32 (manifest `cli.*`/`loggerRoots` fields the scaffold emits), 37 (front-door policy), PLATFORM-LEGIBILITY (manifest self-description).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Scaffold + template

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Type table derived from the provider registry (enumerate capability types + their ABC/module mapping programmatically; record the mapping source in the Execution log) | `src/personalclaw/cli_app_new.py` (new), wired via `cli.py` subparser pattern | `personalclaw app new --list-types` prints the derived table; adding a provider type upstream appears without editing the generator |
| T1.2 | Generators per type: manifest (+plan-32 fields), provider stub implementing the type's contract minimally, passing stub-based `test_provider.py`, README, LICENSE | `cli_app_new.py` + `src/personalclaw/templates/app/` data files | for EVERY type: generate → `pytest <dir>` passes → local-source install succeeds → provider registers (scripted loop in a test) |
| T1.3 | Generated-output CI conformance: a core test generates each type into tmp and runs the apps-repo checks (manifest validate, sdk-boundary, tests) against it | `tests/test_app_scaffold.py` | scaffold drift = red test |
| T1.4 | `docs/app-creation-guide` (apps repo) gains the scaffold quickstart at the top ("minutes to first run"); template repo content emitted + README | apps repo guide; `personalclaw/app-template` repo content (prepared in-tree under `scratch/`, pushed by owner task 1) | quickstart tested verbatim; template repo content complete |
| V1 | Validation: stranger-shaped run — scaffold a `search` app, implement one real method (wikipedia-style), install via Store local source, use it in chat; time it (<30 min target) | — | timed run recorded |

### Session 2 — Registry data tier

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `registry.json` schema + validation script (manifest fetch/parse, repo liveness, license, scanner dry-run verdict capture) + PR workflow running it | `personalclaw/registry` repo content (schema, script, CI, CONTRIBUTING-for-listings, delisting policy per Design) | a valid sample PR passes; a dangerous-verdict fixture blocks with the reason |
| T2.2 | Default-source seeding: registry URL ships as a default git source (seed into `app-sources.json` on first run behind a config flag; Settings shows it as removable-default) | sources seeding site (locate first-run seeding in `apps/` bootstrapping), Settings sources UI | fresh home lists registry apps in Store; removing the source persists |
| T2.3 | Store card provenance line: for registry-sourced apps, show maintainer + last_validated from registry metadata (data already in the catalog payload path — extend the git-source catalog listing) | `apps/source.py`/catalog path, Store card component | registry cards show provenance; local/first-party cards unchanged |
| V2 | Validation: list→install→use a registry app end to end; verify the scan gate still runs at install (deliberate warning-fixture app shows consent) | — | holds |

### Session 3 — Exemplars + bounties (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Build the four exemplars per Design (scaffold-generated, then minimally implemented; each ≤300 LOC target, README-led) | four org repos (content prepared in-tree, pushed by owner task 1) | each installs from its git URL through the Store; registry-listed |
| T3.2 | Bounty board: labeled issues from the wants-list (channels + providers + sources), each linking scaffold/guide/conformance; showcase thread seeded | GitHub issues | ≥6 bounties live |
| V3 | Validation: fork-simulate one exemplar (clone, rename via scaffold rename helper if built, else manual), install — the third-party path proven end to end again post-registry | — | holds |

### Session 4 — Registry surface (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Static registry pages on personalclaw.dev generated from `registry.json` (cards: name/types/permissions/verdict/maintainer; per-app page with README fetch) | site repo (plan 36's sync pipeline extension) | site lists registry; permissions + verdict visible pre-install; rebuild picks up registry changes |
| V4 | Validation: a registry PR merge appears on the site after rebuild; card data matches Store consent surface | — | holds |

## Owner tasks (real world)

1. **Create the org repos** (`app-template`, `registry`, four exemplar repos) and push the prepared content (executor prepares everything in-tree; you create+push — or grant the session push rights and skip this). ~20 min.
2. **Approve the delisting policy** wording (what gets removed and how appeals work) — it's a community-governance statement.
3. **Seed the first bounty rewards decision:** recognition-only vs small monetary bounties (recognition-only recommended at this stage; monetary bounties need payment logistics you may not want).
4. When exemplar `action-home-assistant` is validated: a Home Assistant instance (yours if you run one; else mark that exemplar community-validated).

## Risks & open questions

- **Registry trust-washing risk:** a listing must never read as an endorsement — card copy says "community-listed, scanned at install" explicitly; verdict display is the honest differentiator.
- **Open:** scaffold rename/refactor helper (`app new --from <existing>`) — nice-to-have; DISCOVERY-file if demand appears.
