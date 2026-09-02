# ECOSYSTEM-TOOLING

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/ET.md`](../atomic/ET.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Ecosystem Tooling — Scaffold, Registry, Exemplars

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "yes, please plan for this")
**Created:** 2026-07-18
**Wave:** 2 (S1-2: scaffold + registry data tier) + 3 (S3-4: exemplars, bounties, registry surface)
**Depends on:** OSS-OPERATIONS (front-door policy), PLATFORM-LEGIBILITY S1-3 (manifest self-description), CI-RELEASE-ENGINEERING S2 (apps-repo CI the template inherits), plan 32 (manifest gains `cli.*`/`loggerRoots` — scaffold emits them).
**Scope:** collapse app-author time-to-first-run to minutes and give PersonalClaw-native apps a discovery surface. **Soul guardrail:** the registry starts as **data in a git repo** the Store consumes — no registry service, no accounts, no upload pipeline. The scanner-gated install path remains the only install path; the registry adds discovery, never a bypass. Scaffold output must pass the apps-repo CI *as generated* — a template that needs fixing is a defect.

---

> 📎 **The first-party app suite in [PRODUCT-EXPERIENCE-PARITY](PRODUCT-EXPERIENCE-PARITY.md) §7 (#68) ARE this plan's exemplars** — added 2026-08-05. A comparable product suite spans ~11 first-party product apps (Code Review, Research Lab, Design Critique, PPTX/Papyrus, Notes, Issue Radar, Ops, Spec Builder, Companion); #68 §7 designs PersonalClaw equivalents as a phased program, one PR each. This plan's scaffold generates their skeletons and its exemplar list should record them as they ship — they prove the platform far better than the four throwaway exemplars in T3.1. Coordinate: #68 §7 builds the apps; this plan builds the scaffold/registry they're built with.

## Context (code recon, 2026-07-18)

- CLI uses argparse subparsers (`cli.py:205+`, existing two-level pattern e.g. `cron`/`spawn`/`security` subcommands) — `personalclaw app new` slots in cleanly.
- Sources API is live: git sources (`/api/apps/sources`) list-without-clone, shallow-clone-at-install behind the scanner; local sources (`/api/apps/local-sources`). A registry = **one well-known default git source** entry — near-zero core change.
- The worked example (`third-party-apps/demo-dashboard`) exercises every platform surface (backend, UI, storage, api/events/cron/agent permissions, MCP server); the app-creation guide is 322 lines. Manifest schema (`apps/manifest.py`) validates name/semver; capability types: model/search/tool/channel/action/skills-marketplace/inbox-source/backend+UI.

## Design

- **Scaffold:** `personalclaw app new <name> --type <capability>` — interactive when flags absent; emits `app.json` (valid, incl. plan-32 fields), provider stub for the chosen type (each type's stub = minimal compilable implementation of its ABC with one TODO-free example method), `test_provider.py` (passing, stub-based like the first-party pattern), `README.md` (front-matter template), `LICENSE` (MIT prefilled). Types map to real sdk contracts — the generator's type table is **derived from the provider registry**, not hardcoded (self-description tenet). Also `--from-template` fetching the template repo for fork-and-go users.
- **Template repo (`personalclaw/app-template`):** the scaffold's `--type tool` output committed + apps-repo CI preconfigured + a README walking the author from clone to installed-in-Store in minutes.
- **Registry (`personalclaw/registry`):** `app-registry.json` — `[{name, repo, types, permissions_declared, license, maintainer, added, last_validated}]`; PR-based listing; CI validation on PRs: manifest fetch+parse, repo exists, license present, scanner dry-run verdict recorded into the PR (never auto-blocking listing on `warning` — the verdict is *displayed*; `dangerous` blocks listing). Store integration: the registry repo URL ships as a default git source (config seed + Settings toggle to remove it); listings render with the same consent surface as any source.
- **Exemplars (org repos, scaffold-generated):** `watched-source-github` (a watched-source provider — coordinates with WATCHED-SOURCES contract timing), `action-home-assistant` (action provider calling HA webhooks), `inbox-github-notifications` (inbox source), `channel-null` (the guide's teaching channel, conformance-kit-passing). Each: small, real, forkable, listed in the registry.
- **Bounty board:** labeled issues (`bounty`) per wanted app (channels from plan 40 T7.3, providers, sources) with the scaffold + guide + conformance links; showcase channel in the community surface.
- **Registry surface (S4):** static generation on personalclaw.dev from `app-registry.json` — cards show name, types, **declared permissions and last scan verdict pre-install** (publishing the consent surface).

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

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

**AS BUILT (`ET-3`, 2026-08-18) — `scratch/registry/`, three deltas from the sketch above.** (1) The row schema is **closed** (`additionalProperties: false`) and `last_validated`/`last_scan_verdict` are **CI-owned**: a listing PR omits them and anything it does supply is overwritten from the run that actually happened. (2) `app-registry.schema.json` is **generated** from the Python constants (`validate_registry.py --emit-schema`) rather than hand-maintained, so its `types` enum tracks core's `PROVIDER_TYPES` on regeneration; `test_registry_validation.py` reds on drift. (3) Two checks were added beyond the four listed: the row's `types`/`permissions_declared` must equal what the fetched manifest declares (those two fields ARE the pre-install consent surface S4 publishes, so a row that under-declares lies on the user's behalf), and a symlink resolving outside the clone blocks before anything reads the tree (the validator quotes matched evidence into a public PR comment).

### Integration points
- **Calls:** provider registry (type table), `apps/manifest.py` (validation), `SkillScanner` dry-run (verdict), the sources-seeding path.
- **Consumed by:** 40 (a `channel` scaffold template + bounties), 47 (registry records signer identity per listing), 36 (registry surface on the site).
- **Depends on:** 32 (manifest `cli.*`/`loggerRoots` fields the scaffold emits), 37 (front-door policy), PLATFORM-LEGIBILITY (manifest self-description).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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
| T2.1 | `app-registry.json` schema + validation script (manifest fetch/parse, repo liveness, license, scanner dry-run verdict capture) + PR workflow running it | `personalclaw/registry` repo content (schema, script, CI, CONTRIBUTING-for-listings, delisting policy per Design) | a valid sample PR passes; a dangerous-verdict fixture blocks with the reason |
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
| T4.1 | Static registry pages on personalclaw.dev generated from `app-registry.json` (cards: name/types/permissions/verdict/maintainer; per-app page with README fetch) | site repo (plan 36's sync pipeline extension) | site lists registry; permissions + verdict visible pre-install; rebuild picks up registry changes |
| V4 | Validation: a registry PR merge appears on the site after rebuild; card data matches Store consent surface | — | holds |

## Owner tasks (real world)

1. **Create the org repos** (`app-template`, `registry`, four exemplar repos) and push the prepared content (executor prepares everything in-tree; you create+push — or grant the session push rights and skip this). ~20 min.
2. **Approve the delisting policy** wording (what gets removed and how appeals work) — it's a community-governance statement.
3. **Seed the first bounty rewards decision:** recognition-only vs small monetary bounties (recognition-only recommended at this stage; monetary bounties need payment logistics you may not want).
4. When exemplar `action-home-assistant` is validated: a Home Assistant instance (yours if you run one; else mark that exemplar community-validated).

## Risks & open questions

- **Registry trust-washing risk:** a listing must never read as an endorsement — card copy says "community-listed, scanned at install" explicitly; verdict display is the honest differentiator.
- **Open:** scaffold rename/refactor helper (`app new --from <existing>`) — nice-to-have; DISCOVERY-file if demand appears.

## Execution log

- **2026-08-17 — ET-2 (T1.4) DONE.** Template repo content + the guide quickstart + `app new
  --from-template`. Template content staged at `scratch/app-template/` (the `--type tool`
  output at the repo root, a hand-written clone-to-installed `README.md`, a root-level variant
  of the apps-repo CI's four jobs, `.gitignore`); the apps-guide insert is staged at
  `scratch/apps-guide-quickstart.md` with its own insertion instructions. `scratch/` is outside
  `testpaths` and outside `make lint`'s targets, so it does not join the core build. **Owner
  push still required** (Owner task 1) — see `scratch/README.md`.
- **DISCOVERY — the quickstart was wrong twice until it was run verbatim.** Step 6 claimed
  `GET /api/apps/{name}` returns top-level `enabled` and `provider` keys; it returns
  `installed.enabled` and `manifest.provider`. And `personalclaw doctor` prints the section
  under the app's *name* (`my-tool`), not its displayName. Both were found by executing the
  text, not by reading it. Measured after correction: **2.4 s wall clock, steps 1-6**, from an
  empty directory against a freshly-homed gateway (`--list-types` → generate → pytest → token →
  install+enable → verify). The plan's "<30 min" target is for V1's stranger-shaped run with a
  real method implemented; this number is first-run only.
- **DISCOVERY — this repo's pre-commit hook reformats the staged template.** `black` runs over
  every staged `.py`, including `scratch/app-template/`, and rewrote the generated
  `CONTRACT_METHODS = ('a', 'b')` to double quotes — silently making the staged template differ
  from generator output. Fixed at the source: the generator now emits double quotes
  (`f'"{m}"'`, not `repr(m)`), so scaffold output is black-clean by construction. A template
  that needs reformatting is the same class of defect as one that needs fixing.
- **DEVIATION — `--from-template` takes no NAME.** It fetches the template verbatim into
  `<dir>/app-template`, and refuses a NAME with a message pointing at `--type tool`. Renaming
  properly spans five files (manifest name/displayName/loggerRoots, the provider class + logger
  root + `name`/`display_name` bodies, the test's expectations, the README title, the LICENSE
  holder) — that is the rename helper this plan's Risks section deliberately leaves open, and
  a partial rename would ship a manifest `name` that disagrees with the provider's `.name`
  property, which every per-type registry keys on. The template README documents the four-edit
  rename instead.
- **MEASURED — the live fetch is UNPROVEN, and honestly so.**
  `https://codeload.github.com/personalclaw/app-template/tar.gz/refs/heads/main` answers **HTTP
  404** today, and `app new --from-template` refuses it fail-closed
  (`error: template fetch returned HTTP 404 (expected 200)`). So the URL, the host allowlist and
  the real TLS transport are all exercised against the real host; only the repo's *content* is
  missing. Extraction is proven end-to-end against a local tarball and a loopback HTTP server.
  Nothing in core changes when the owner pushes.
- **FALSIFICATION — the traversal tests initially passed for the wrong reason.** Disabling the
  `..` name check by hand left both traversal tests GREEN: the post-canonicalisation containment
  check was firing and emitting a message that matched. The two refusals now say different
  things ("escapes the target" vs "resolves outside the target") and
  `test_containment_refuses_even_if_the_name_check_is_bypassed` bypasses the first layer so the
  second is proven live on its own. With BOTH layers disabled, `../../PWNED.txt` really escapes
  two directories out of the target — measured, not assumed.
- **2026-08-18 — ET-2 remainder DONE: the quickstart reached its destination repo.** The
  2026-08-17 entry above claimed ET-2 DONE, but the done-when's own words are "**apps-repo**
  `docs/app-creation-guide` gains a 'minutes to first run' quickstart at the top", and it did
  not: the text sat in `scratch/apps-guide-quickstart.md` in **this** repo with insertion
  instructions attached. Prepared is not delivered. Measured before writing anything:
  `git grep -ni "quickstart\|minutes to first run" -- docs README.md` in PersonalClawApps
  returned **zero hits**. It is now `## Quickstart: minutes to first run` in
  `PersonalClawApps/docs/app-creation-guide.md`, between the `my-app/` tree and
  `## The manifest`.
- **DEVIATION — the staged copy and its rail are deleted, not kept in sync.**
  `scratch/apps-guide-quickstart.md` and
  `test_app_from_template.py::test_the_staged_quickstart_matches_the_shipped_cli` are gone. Once
  the text lives in the apps repo, a second copy here has no consumer and two copies drift —
  and the rail would have guarded the stale one. Core cannot test another repo's docs (the apps
  repo has no docs test tier: 118 test files, all per-app `test_provider`-shaped). No unique
  coverage was lost: the auth-shape invariant is still pinned here by
  `test_the_staged_readme_uses_the_query_token_not_a_bearer_header` on the template README.
  `scratch/README.md` §2 now records the landing so the owner is not told to publish delivered
  work.
- **DISCOVERY — running the text verbatim found two defects the previous session's run did
  not.** Commands were extracted mechanically out of the committed markdown
  (`awk` over the fenced `bash` blocks) and `source`d under `set -e`, so the transcript is the
  doc's own text, not a paraphrase. (1) **Step 4 swallowed its own failure.**
  `personalclaw token` resolves the port from `--port` -> `PERSONALCLAW_PORT` ->
  `dashboard.url`, **never from the running process** (`cli_server.resolve_client_port`), and it
  prints its error on **stdout**. So with the gateway on another port the doc's
  `TOKEN_URL="$(personalclaw token | head -1)"` stored `❌ Could not reach gateway on port
  19999: ...` into **both** `PERSONALCLAW_URL` and `PERSONALCLAW_TOKEN`, and step 5 died as
  `curl: (3) bad range in URL position 60` — a curl parser error naming neither the port nor the
  gateway. The `head -1` was also dead weight (`personalclaw token` prints exactly one line) and
  was the thing discarding the non-zero exit. Fixed: `head -1` dropped, and the text now names
  the resolution order and that exact downstream symptom. (2) **The "2.4 s" was a warm number
  sold as a first run.** Four measurements on this machine: **7.16 s** against a freshly-homed
  gateway and 5.99 s cold, then 2.16 s / 2.74 s warm. The section is titled "minutes to first
  run", so it now reads **6.0-7.2 s first run, 2.2-2.7 s on repeats** — still seconds, which is
  the actual claim.
- **VERIFIED — every other clause of the quickstart holds as written.** Final run: exit 0 from
  an empty directory against a fresh home, `POST /api/apps` -> `"ok": true`, `/enable` ->
  `{"ok": true, "enabled": true}`, `GET /api/apps/my-tool` -> `installed.enabled == True` and
  `manifest.provider.type == "tool"`, the generated `test_provider.py` 7 passed, and
  `personalclaw doctor` printing a `my-tool` section whose line
  (`✅ My Tool  provider stub installed — no checks declared yet`) comes from the generated
  `app_cli.py`. Step 1 printed **19** types, not the 18 in ET-1's entry above — the quickstart
  states no count on purpose ("derived from the running build's provider registry"), so the
  drift cannot reach it.
- **MEASURED — `--from-template` fetches, proven at the entry point a user types; the org repo
  is still the only gap.** `personalclaw app new --from-template --template-archive <tgz> --dir
  <tmp>` laid down the 8 template files with the wrapper directory stripped, and the fetched
  copy's own tests pass unmodified (7 passed) — clone-to-installed is real. Against the shipped
  default URL the command still answers `error: template fetch returned HTTP 404 (expected
  200)` and writes **nothing** (target directory: 0 entries), because
  `github.com/personalclaw/app-template` does not exist yet. **OWNER DEPENDENCY: Owner task 1
  (push `scratch/app-template/` to the org repo) is the one clause no session can close.**
  Nothing in core changes when it lands.
- **FALSIFICATION — my own tarball, not the code, broke root-stripping first.** The initial
  local-archive drive extracted **19** files into `out/app-template/app-template/...` instead of
  8 flattened. Cause was macOS `tar` writing AppleDouble `._*` members: `_strip_root` sees roots
  `{"._app-template", "app-template"}`, `len(roots) != 1`, so it strips nothing. Rebuilt with
  `COPYFILE_DISABLE=1 tar --no-xattrs` (0 `._` members) and the same command produced 8 files,
  stripped. The harness was wrong, not `_strip_root` — a GitHub codeload tarball has one root.

## Execution log

- **2026-08-17 — DONE (`ET-1`): `personalclaw app new` scaffold with a registry-derived type table** (#1553).
  `--list-types` prints 18 types read at runtime from `manifest._providers_section()` -> `PROVIDER_TYPES` +
  the provider registry — the same derivation the agent manifest publishes, so an upstream capability type
  appears without editing the generator. That is tested, not asserted:
  `test_an_upstream_type_appears_without_editing_the_generator` injects `fake_capability` and requires it to
  both list AND scaffold; hard-coding the list reds it, and `cli_app_new.py` contains zero type-list
  literals. Thirteen types resolve a contract off `personalclaw.sdk.*`; five publish no SDK ABC (`agent`,
  `duty_gate`, `notification`, `task`, `workflow`) and are labelled `- (duck-typed stub)` rather than
  emitting a deep-core import that would break the app boundary. The per-type loop is 18/18 on all four
  legs — generate -> `pytest <dir>` (7 tests each) -> local-source install -> provider registers with
  `error == ""` — and `tests/test_app_scaffold.py` runs the **apps repo's own three CI jobs** read out of
  its `ci.yml`, so scaffold drift reds. `duty_gate` genuinely failed the register leg first
  (`must expose an async on_duty(now, ctx)`) and was fixed by carrying that requirement in a table read off
  the handler's own refusal, not by excluding the type. Generated stubs assert `__abstractmethods__` empty,
  so a scaffolded provider is instantiable rather than merely parseable. Gate: lint EXIT=0, mypy clean on
  903 files, 137 passed + 1 pre-existing skip, `manifest_reference` no-diff. Five falsifications, none green.
- **2026-08-17 — V1 + DISCOVERY (`ET-1`): the generated README's own install snippet did not work.**
  Timed stranger walkthrough: **6 seconds** end to end, run twice. The first drive returned
  `{"error": "Token required"}` twice because the template used `Authorization: Bearer $PERSONALCLAW_TOKEN`,
  and the gateway accepts `Bearer` only for **app-scoped narrowing** tokens —
  `dashboard/token_auth.py:977-986`; the owner token comes from `?token=` or the `pc_token_<port>` cookie
  (verified in code during ship review, not taken on report). The template now uses `?token=` plus the
  follow-up `enable` call, and the final run executed the snippet **verbatim from the generated README**.
  A scaffold whose own README fails is worse than no scaffold, so this is recorded as the finding V1 exists
  to produce.
- **2026-08-17 — NOTE.** This plan had no `## Execution log` section before today (one of 7 of 70); the
  section was created with `ET-1`'s entry rather than the entry being filed elsewhere.
- **2026-08-18 — DONE (`ET-3`, T2.1): the registry data tier, staged as registry-repo content in
  `scratch/registry/`.** Follows `ET-2`'s convention (`scratch/` is tracked, outside `testpaths` and
  outside `make lint`'s targets, and nothing in core imports it), so the owner copies the directory to a
  new `personalclaw/registry` root and pushes. Twelve files: `registry.json` (empty until `ET-6`),
  a **generated** `registry.schema.json`, `validate_registry.py`, `CONTRIBUTING.md` (the listing policy),
  `DELISTING.md`, `requirements.txt` (`personalclaw==0.1.3`, pinned so the scanner rule set — i.e. what the
  registry accepts — cannot change without someone choosing it), three workflows and three app fixtures.
  **All three behavioural clauses are driven, not asserted.** The validator reaches a repo exactly one way —
  `git ls-remote` for liveness, `git clone --depth 1` to fetch — and git treats `file://` and `https://`
  identically, so 44 committed tests build real one-commit git repositories from the fixture trees and run
  the REAL fetcher offline. Nothing is stubbed, skipped or xfailed. Measured: valid → exit 0, verdict
  `clean`, `rows_validated: 1`; dangerous → exit 1 with `scanner_dangerous:destructive_root` naming
  `scripts/install.sh` and quoting `rm -rf / --no-preserve-root`; warning → **exit 0, `blocking == []`**,
  verdict `warning` (not `low` — scanning at `community` tier deliberately, because `official`/`trusted`
  would report a softer verdict than the user's own install gate) with `scanner_warning:curl_network` in
  `display` and "shown, not blocking" in the PR body. **`file://` requires an explicit
  `--allow-file-repos`** that none of the three workflows passes (pinned by a test), so the offline
  affordance is not a production hole.
- **2026-08-18 — DEVIATION + DISCOVERY (`ET-3`): three additions the plan did not ask for, each because
  the four listed checks left a real hole.** (1) **Row↔manifest agreement.** S4 publishes `types` +
  `permissions_declared` as a pre-install consent surface, so validating the manifest while trusting the
  row's copy of it would let a listing under-declare permissions on the user's behalf; both are now derived
  from the fetched manifest (`Permissions.to_dict()` keys — so the two permission fields a sibling agent
  added to `manifest.py` today are picked up with no edit here) and exact equality is required.
  (2) **Escaping-symlink refusal.** The validator quotes matched scanner evidence into a **public** PR
  comment, so a committed symlink to `/etc/passwd` would have had its contents read and echoed by a CI
  runner. Blocked before anything reads the tree, for absolute AND `../`-climbing targets. (3) **Scheduled
  re-validation** (`revalidate-listings.yml`): `DELISTING.md`'s grounds are all detected by re-running the
  validator, and a delisting policy with no detection mechanism is inert doctrine. **The discovery came
  from a vacuity assertion**: the "an internal symlink is left alone" control initially reported
  `repo_symlink_escape`, because an ABSOLUTE in-repo symlink stops being in-repo the moment the repo is
  cloned elsewhere — correct behaviour, wrong fixture. The fixture now uses a relative target and the
  escape test covers both shapes. A live hand-drive also surfaced a copy defect: GitHub answers a
  nonexistent repo with `Authentication failed`, so the verbatim git message sent a contributor who
  mistyped a repo name hunting for a credentials problem; the reason now says what actually happened
  (asserted at the call site, with a vacuity case proving an ordinary git error does NOT get the hint).
- **2026-08-18 — NOTE (`ET-3`): the listing policy is written down for the first time, and is
  OWNER-CONFIRMABLE.** `ET-3`'s declared dep is OSS-OPERATIONS' front-door / community-listing policy,
  which is not separately shipped, so `CONTRIBUTING.md` states the adopted policy explicitly and marks
  its provenance: four rules come from this plan's Design (§Registry), and the rest — a closed row schema,
  exact row↔manifest agreement, unique kebab-case names equal to the manifest's, `https`-only repo URLs
  with no userinfo and no explicit port, and the maintainer expectations — are stated there so the script
  implements a written policy rather than an implied one. `DELISTING.md` splits grounds into immediate
  (`dangerous` verdict, malware/impersonation) and 14-day-notice (repo gone across two consecutive weekly
  runs, manifest stops validating, license removed, row↔manifest divergence), and says plainly that
  delisting stops NEW discovery and **uninstalls nothing**. Both documents are owner-confirmable, not
  owner-confirmed. **Not proven, and unprovable until the repo exists:** whether GitHub runs the three
  workflows. Their action versions mirror core's own `.github/workflows/ci.yml` as observed today
  (`checkout@v7`, `setup-uv@v7`, `upload-artifact@v7`, `download-artifact@v8`, `github-script@v9`), and the
  fork-token split (validation holds a read-only token and writes an artifact; a `workflow_run` sibling
  posts the comment) is copied from core's `ci.yml`/`pr-feedback.yml` pair for the same reason — a fork PR
  gets a read-only token whatever `permissions:` asks for. The live `https` legs WERE driven by hand once:
  `github.com/octocat/Hello-World` cloned and then blocked `manifest_missing` with verdict "not reached"
  (a `None` verdict is never read as clean), and a nonexistent repo blocked `repo_unreachable` without ever
  hanging on a credential prompt — which is the no-prompt git environment working.
- **2026-08-18 — PARTIAL (`ET-4`, T2.2): the registry ships as a SEEDED, removable default git source.**
  `apps.registry_source_enabled` (new `apps` config section) gates a one-time seed of
  `https://github.com/PersonalClaw/registry.git` into `apps/app-sources.json` at gateway start
  (`_app_sources_seed_startup` → `catalog.seed_default_git_sources()`). **Deliberately NOT added to
  `_DEFAULT_GIT_SOURCES`:** that tuple is folded into every read of `list_git_sources()`, so a "removed"
  member is back on the next read — the mechanism the done-clause "removing the source persists" rules out.
  The seed instead writes one ordinary row plus a `"seeded": ["registry"]` marker; the marker OUTLIVES the
  removal, which is what stops the next start re-seeding. Every source write path round-trips the marker
  (dropping it from `_write_sources` resurrects a removed default — falsified live: proc A seed → remove →
  proc B re-seeds). Drove a real gateway on an isolated home: fresh start seeds; `DELETE /api/apps/sources`
  removes it; a genuinely new process on the same home lists only the bundled default. Flag off on a fresh
  home creates **no** `app-sources.json` at all; `PATCH apps.registry_source_enabled=true` then a restart
  seeds. Five wiring points: dataclass+`_meta` (`AppsConfig`), `load()`, `to_dict()`, `_EDITABLE_CONFIG`
  (`apps.registry_source_enabled`), and a Settings › Apps toggle. `config-baseline.json` regenerated (the
  `config-baseline` gate reds otherwise — one added path, nothing else).
- **2026-08-18 — UNMET clause (`ET-4`): "a fresh dev home lists registry apps in the Store" — TWO
  independent gaps, neither of them this atom's.** (1) `scratch/registry/registry.json` ships `{"apps": []}`
  until `ET-6`. (2) **Measured, and cheaper to fix than it looks:** core enumerates a source's listings from
  an index file named **`app-registry.json`** (`catalog._REGISTRY_FILENAME`), while `ET-3` publishes
  **`registry.json`** — but the *shapes already agree* where it matters. `_parse_registry` accepts
  `{"apps": [...]}` and `RegistryPointer.from_dict` needs `name` + `repo`, both of which are REQUIRED in
  `registry.schema.json`. So the whole gap is the filename. Deliberately NOT closed here: widening the
  accepted index name changes listing behaviour for *every* git and local source as a side effect of a
  seeding atom, `T2.3` owns reading registry metadata into Store cards (and wants the richer
  `maintainer`/`last_validated` fields, which `RegistryPointer` drops), and with the registry empty the new
  path would ship unexercised. Recorded so `T2.3`/`ET-6` can take the cheap route knowingly.
- **2026-08-18 — DISCOVERY (`ET-4`): a polarity helper on this flag would have been DEAD CODE.**
  First cut read the flag with `_expose_flag` (fail-closed, "any flag whose True opens a network surface")
  for present-but-garbage values. A test proved it unreachable: `load()`'s schema type-gate already replaces
  a non-bool with the field's **dataclass default** (`_apply_field_default`, the "using default" warning)
  before the field mapping runs, so the helper could only ever see a real bool. Consequence, stated in code
  and pinned by a rail: a corrupted value resolves to the SHIPPED posture (registry **on**), not to off —
  that is the platform-wide config policy, not a choice of this field.
- **2026-08-18 — DEVIATION (`ET-4`): fixed a swallowed write in the sources UI, in scope.** The Store's git-
  source rows rendered a Remove button on the BUNDLED default too, where the backend's DELETE is a no-op by
  construction — click it and the row stays. The catalog envelope now reports `defaultGitSources` (rows
  PersonalClaw shipped → "Default" label) and `builtinGitSources` (the unremovable subset → no remove
  control), mirroring the `firstPartySources` pattern already used for local sources. `remove_git_source`
  semantics are UNCHANGED (a bundled default is still a silent no-op there, and its existing test still
  passes) — the fix is that the UI no longer offers the button.
- **2026-08-18 — NOTE (`ET-4`): the scanner gate is unchanged, proven behaviourally.** No new install path:
  seeding only edits the source LIST. A registry-listed app with dangerous content, installed via the exact
  `pointer` the Store card hands over, is refused with `Verdict.DANGEROUS` even with `confirm=True`; replacing
  the gate's `default_scanner.scan(...)` with a clean report makes that install succeed and reds the test.
  Also NOTE: the seeded URL 404s until the owner creates `personalclaw/registry` (Owner task 1). Measured
  cost of the dangling source on `GET /api/apps/catalog`: **0.61s then 0.52s** — git fails fast on a missing
  public repo, no credential prompt, no hang.

- **2026-08-19 — `ET-2` CLOSED (`todo` → `done`).** Both halves of the done-when are on `main`, in
  the repo each one names. Core: `scratch/app-template/` holds the `--type tool` output plus the
  root-level apps-repo CI config, the clone-to-installed `README.md` and `.gitignore`, and
  `cli_app_new.py:1043 from_template()` implements `--from-template` (`:1100`) as a hostile-input
  surface — `tests/test_app_from_template.py` drives it against a **local** archive server (a
  non-allowlisted host never reaches the network, a non-200 is refused, a redirect is refused, a
  200 tarball extracts), so the clause is proven without depending on the org repo existing yet.
  Apps: `docs/app-creation-guide.md` carries `## Quickstart: minutes to first run` at line 24, the
  destination the 2026-08-18 remainder entry above chased after finding zero hits for it. The
  remaining owner action — pushing `scratch/app-template/` to `personalclaw/app-template` — is
  Owner task 1 and sits outside this atom's done-when, which says "prepared in-tree under
  `scratch/` (owner pushes to the org repo)".

- **2026-08-24 — `ET-4` VERIFIED against `main`, and one real gap closed: the boot WIRE had no rail.**
  Session opened to implement `ET-4`; the deliverable was already on `main` in `2d9d1a36`
  (`feat(apps): ET-4 seed the app registry as a removable default git source`), so this entry records
  an independent verification plus the one thing that was genuinely missing. **Premise correction for
  the atom table:** `ET-4` reads `⬜` in `atomic/ET.md` while its implementation is merged.
  **Measured gap.** Every pre-existing `ET-4` rail asserts the SEEDER by calling
  `catalog.seed_default_git_sources()` directly; nothing asserted the CALL SITE. Deleting
  `app.on_startup.append(_app_sources_seed_startup)` (`dashboard/server.py:1474`) left
  **112 selected tests green** (`test_app_catalog.py` + `test_config_roundtrip.py` +
  `test_config_patch.py`) while first-run seeding silently never happened — a seeder nobody calls,
  and the done-clause is "seeds on first run", not "a helper exists". Closed by
  `tests/test_gateway_boot_app_source_seed.py`: three rails that boot the REAL gateway
  (`start_dashboard(port=0)`) on an isolated `PERSONALCLAW_HOME` and assert what the Store reads over
  HTTP — a fresh boot seeds the registry as a default that is NOT builtin (so the remove control
  shows), a real `DELETE /api/apps/sources` survives a SECOND real boot, and a flag-off boot acquires
  no network source at all. That third one is the positive control: it proves the two above observe
  the boot rather than a constant. Falsified both directions — dropping the wire reds the two seeding
  rails (flag-off stays green, correctly insensitive); making the seeder consult the wrong key so it
  re-seeds reds the reboot assertion itself at `:134`, which is the exact failure mode the atom exists
  to prevent. `PERSONALCLAW_HOME` is the isolation seam (it redirects both `config_dir` bindings at
  once, asserted through both before any boot).
- **2026-08-24 — `ET-4` as-a-user drive on an isolated home: every clause holds EXCEPT the listing
  one, and the reason is stronger than previously recorded.** Two real gateways, isolated
  `PERSONALCLAW_HOME`, tokenless loopback. Fresh home → `apps/app-sources.json` =
  `{"git": [registry], "local": [], "seeded": ["registry"]}`. `GET /api/apps/catalog` labels it a
  removable default: `defaultGitSources` = [PersonalClawApps, registry] but `builtinGitSources` =
  [PersonalClawApps] only, which is what makes `AppsSection.tsx:841` show "Default" **and** the remove
  control. `DELETE /api/apps/sources` → row gone, marker survives. Proc A killed, **proc B** started on
  the same home → registry STILL absent. Scanner gate proven unchanged the strong way: `2d9d1a36`
  touches neither `apps/app_manager.py`, `apps/source.py`, nor `supply_chain/` — the commit's file list
  does not contain them, so there is no new install path by construction.
  **UNMET clause, and the cause is not the one on record:** "a fresh dev home lists registry apps in
  the Store" — observed `remoteApps: 0` live. The 2026-08-18 entry attributed this to an empty
  `registry.json` plus the `app-registry.json` filename mismatch. Measured tonight, the binding
  constraint is simpler and earlier: **`https://github.com/PersonalClaw/registry.git` does not exist**
  (`git ls-remote` → `remote: Repository not found`, exit 128). So the clause is unprovable until the
  owner pushes the registry repo, and the filename gap behind it is untestable until then — neither is
  reachable from inside core. Two mitigating facts for the shipped default, both measured: the failed
  fetch is **quiet** (zero registry lines in `gateway.log`; fail-soft as designed) and **cached** —
  first `/api/apps/catalog` 2.76s, second 0.57s — and the Store is not empty meanwhile (`gitApps: 50`
  from the bundled apps source). **Owner decision, not an atom call:** `registry_source_enabled`
  defaults to `True` (`config/loader.py:4938`), so until that repo exists every fresh install carries a
  default source that 404s. Ship order (publish repo before/with the flag default) is an owner call.

## Execution log — `ET-4` (default-source seeding) — 2026-08-25

- [2026-08-25][ET-4] **Three of five clauses were already met and railed on main** — verified against code,
  not taken from the plan header. Seeding: `catalog.py:631 seed_default_git_sources`, wired at
  `server.py:1490` (`app.on_startup.append(_app_sources_seed_startup)`), flag at `loader.py:1199-1200`,
  with the **call site** railed by `tests/test_gateway_boot_app_source_seed.py` (boots the real gateway on
  an isolated home). Removable-default UI: `AppsSection.tsx:799-800`, railed with vacuity by
  `web/src/pages/apps/sourceLabels.test.tsx`. Removal surviving restart: that same boot test boots twice
  on one home and a real `DELETE /api/apps/sources` survives the second boot.
- [2026-08-25][ET-4] **Added the missing rail for the negative clause.** "The scanner gate at install is
  unchanged (no new install path)" had been proved only behaviourally, by driving one route, plus a
  source-text scan of the seeder — neither of which can stop a **second** ungated route from being added
  later. Now an AST census of `default_scanner.scan(` keyed on **enclosing function** pins exactly three
  sites: `app_manager.install`, `app_manager.update`, and `supply_chain.scan_dir` (whose only production
  caller is `skills/marketplace.py:430` — a skill, not an app). The census found the third; an expectation
  of two was wrong, which is precisely why it is a census and not a grep. Vacuity: the walker asserts it
  visited >100 files, and a control census over an absent token must return `set()`.
- [2026-08-25][ET-4] **Falsified in BOTH directions.** Adding a fourth site (`_registry_fast_install`
  calling the scanner) reds and the failure *names* the new site. Removing an existing one — replacing the
  update path's `default_scanner.scan(staged, tier)` with an ungated stub — also reds
  (`assert {...} == {...}`). The removal direction is the one that matters more, since a deleted gate is an
  outage rather than drift, and it was not covered by the first pass.
- [2026-08-25][ET-4] **Renamed `test_the_install_scanner_gate_has_exactly_two_call_sites` to
  `..._three_call_sites`.** It asserted three while naming two — a stale name on a census test is a trap
  for the next reader, who will believe the name over the assertion.
- [2026-08-25][ET-4] **UNMET clause, and the whole remaining distance is ONE FILENAME.** "A fresh dev home
  lists registry apps in the Store" fails on three re-measured blockers: (1)
  `git ls-remote https://github.com/PersonalClaw/registry.git` → `remote: Repository not found` (owner
  task 1); (2) `scratch/registry/registry.json` is `{"apps": []}` until `ET-6`; (3) core reads
  `app-registry.json` (`_REGISTRY_FILENAME`, `catalog.py:197`) while ET-3 publishes `registry.json`.
  Blocker 3 is now **measured, not inferred**: a local git source publishing one schema-valid row lists
  **1 app** when its index is named `app-registry.json` and **0 apps** when named `registry.json`. So no
  parser change is needed and ET-3's row shape already suffices — this **corrects the 2026-08-18 entry's
  framing**, which read as though `RegistryPointer` might also need widening. Pinned in code so a future
  parser change that stops reading an ET-3-shaped row reds in CI rather than in a user's empty Store.
- [2026-08-25][ET-4] **OWNER DECISION — deliberately not taken unilaterally.** Rename
  `scratch/registry/registry.json` → `app-registry.json` (42 references across 14 files, all inside
  unpublished `scratch/registry/` plus 2 in `tests/test_registry_validation.py`, **zero runtime blast
  radius**), versus widening core (rejected 2026-08-18 because it changes listing behaviour for every
  source). The rename looks like the right convergence direction — `app-registry.json` is core's
  source-root index contract for *every* git and local source, so ET-3's name is the drift. **It is
  cheapest now:** `registry.schema.json`'s `$id` is already
  `https://raw.githubusercontent.com/personalclaw/registry/main/registry.schema.json` and
  `scratch/registry/README.md:34-36` already promises "listed apps appear in the Store", so after
  publication the rename becomes a breaking change to a public repo and to contributor PRs. Left to the
  owner because the plan assigns this gap to `ET-5`/T2.3, which will touch `_parse_registry` anyway.
- [2026-08-25][ET-4] **PARTIAL — stays `todo`**, with a `blocked_reason` recording the unmet clause and the
  owner decision so the readiness census stops returning it as freshly startable.
- [2026-08-25][ET-4] **LANDMINE — the real-home rail is PROCESS-GLOBAL and a sibling pool member can red an
  innocent full-suite run.** The first `make test` exited 1 on `dir-entries-changed skills (704 bytes)`
  under `~/.personalclaw`. Not caused by this diff and not reproducible: `test_app_catalog.py` alone
  reported the real home unchanged, no gateway was running (real `gateway.log` last written 24 Aug 22:18),
  and a second full run came back unchanged with exit 0. A concurrent agent wrote `~/.personalclaw/skills/*`
  inside the first run's window (mtimes 05:15). **Any concurrent pool member touching the real home reds
  an unrelated suite**, so a lone red on that rail during parallel execution needs a re-run before it is
  treated as a finding.
- [2026-08-25][ET-4] **Gate:** `make lint` clean (mypy 1011 source files); targeted
  `test_app_catalog.py` + `test_gateway_boot_app_source_seed.py` + `test_registry_validation.py` +
  `test_supply_chain_gates.py` → **108 passed, 0 failed** (paths confirmed to exist first);
  `test_app_catalog.py` alone 54 passed; `make test` **26517 passed, 0 failed**;
  `scripts/gate_report.py` 6/6 PASS; probe sweep 16, 0 introduced. No `web/` changes.

- [2026-08-26][ET-8] **PARTIAL — stays `todo`, and the reason is a PREMISE CORRECTION, not a gap in
  the work.** The pages, cards, per-app pages, README fetch and the rebuild-picks-up-changes clause
  all shipped in `personalclaw.dev#31` (rebase-merged, `3cc45c89`).
  **The registry does not exist at the pinned core release.** `scratch/registry/registry.json`
  **404s at `v0.1.3`** (2026-07-30); the registry tier landed on core `main` on 2026-08-18
  (`24f3f5c9`, ET-3) and there is no newer tag. Verified independently of the implementation with
  `git cat-file -e v0.1.3:scratch/registry/registry.json`. The briefing for this atom asserted the
  file was simply tracked — true on `main`, false at the pin — so the implementation corrected the
  premise rather than building against it, which was the right call.
  Reading core `main` instead would **publish unreleased core state as released state**. So absence
  is recorded as a state (`availability: "absent-at-pin"`) and the page says so; the loader renders
  **three** states a card count cannot distinguish — `absent-at-pin`, `empty`, `listed`. `done_when`'s
  **V4** ("a merged registry PR appears on the site after rebuild") is therefore unreachable. What is
  proven instead is the mechanism: changing the input and rebuilding changes the output.
  **The other unmet clause** — "card data matches the Store consent surface" — is unverified against
  a live Store. What *is* asserted is that every consent value renders byte-equal and order-equal to
  its registry field, so it cannot disagree with any other reader of the same field.
  **Security shape, because this is a pre-install trust surface.** Only the exact string `clean` gets
  `tone: "pass"`. An unrecognised verdict presents as **blocking**, never reassuring; "no scan on
  record" is a distinct dashed treatment; permissions render `permissions_declared` verbatim and
  order-preserved, never summarised. Anti-vacuity is explicit *because* the production registry is
  empty — a generator over it renders a clean zero-card page while every test passes for the wrong
  reason — so `validate-registry-render.mjs` fails when a registry with readable listings renders
  zero cards. Re-verified at integration: the scope-valid mutation reds with
  `ANTI-VACUITY: /registry rendered ZERO cards from a registry with 3 readable listings`, and
  restoring returns green. (A first mutation attempt referenced an out-of-scope name and broke the
  astro build — a collection error, not a falsification — and was discarded.)
  **Two build-environment facts found while gating this.** `test:prepush` exits 1 with a GitHub
  **403** in any shell without `GITHUB_TOKEN`, and unmodified `main` does the same on a different
  URL — the shared `sync-sources.mjs` only sends an Authorization header when that variable is set,
  so this is pre-existing, not ET-8's. And `validate:visual-baselines` reads **green locally with
  stale Linux snapshots**, because it validates presence and metadata rather than Linux pixel
  equality; the comparison only happens in the `ubuntu-latest` browser job. The `/apps` page grew
  76px from the new registry link, so its two Linux baselines were refreshed through the documented
  workflow — and every other PNG in the artifact was compared against its committed copy to prove
  the refresh swept in no unrelated pixel churn (two differ, zero others).
  **CLEARS WHEN:** core cuts a release containing `scratch/registry/registry.json` **and** a
  community registry PR merges; re-point the pin and V4 becomes measurable.
  *(Path superseded 2026-08-27 by `ET-4a`: the file is now `scratch/registry/app-registry.json`. The
  `v0.1.3` measurements above are left verbatim — at that tag the name really was `registry.json`, so
  rewriting the recorded `git cat-file` command would falsify it.)*

## Execution log — `ET-4a` (registry index filename) — 2026-08-27

- **2026-08-27 — DONE (`ET-4a`, carved out of `ET-4`): `scratch/registry/registry.json` →
  `scratch/registry/app-registry.json`, so the staged registry publishes under the ONE filename core
  reads.** `catalog._REGISTRY_FILENAME` is `app-registry.json` (`apps/catalog.py:197`) and is the sole
  index name enumerated for every git and local source. `ET-3` had staged the file as `registry.json`,
  which the 2026-08-25 `ET-4` entry above measured as the whole of the remaining distance to that
  atom's 'a fresh dev home lists registry apps in the Store' clause: one schema-valid row lists **1**
  app under core's name and **0** under `ET-3`'s. This closes it by renaming the published file — the
  direction the 2026-08-25 entry chose over widening core, which would have changed listing behaviour
  for every source.
  **ORDERING IS THE POINT, not a nicety.** `scratch/registry/` is copied verbatim to the root of the
  public `github.com/PersonalClaw/registry` repo (`scratch/registry/README.md` "Owner steps to
  publish"). Renamed now it is a private cleanup; renamed after publication it is a breaking change on
  a public repo that contributors may already have cloned, forked, or scripted against — and
  `registry.schema.json`'s `$id` is already a `raw.githubusercontent.com/personalclaw/registry` URL.
  The repo did not exist when this landed (`git ls-remote` → "Repository not found"), so the window was
  still open.
  **`git mv`, so history follows the file** (rename detected at 100% similarity). Reference sweep:
  eight files inside `scratch/` (both validation workflows, `CONTRIBUTING.md`, `DELISTING.md`,
  `README.md`, `fixtures/README.md`, `validate_registry.py`, plus `scratch/README.md`) and two core
  tests (`tests/test_registry_validation.py`, `tests/test_app_catalog.py`). The CI temp artifact
  `base-registry.json` was renamed to `base-app-registry.json` in the same workflow so a contributor
  reading it does not read two different files; `validate_registry.py`'s argparse error regained its
  article (`an app-registry.json path`). **Zero source changes** — `apps/catalog.py` already read the
  new name, which is why the whole atom is a rename plus references.
  **THE SCHEMA WAS RENAMED IN THE SAME COMMIT — `registry.schema.json` →
  `app-registry.schema.json` (owner ruling, 2026-08-27).** It was first left out as scope discipline
  (nothing functional depends on its name and the atom's scope line names the data file), and the
  ruling reversed that on the ground that **half a one-way door is worse than either end**: the file
  table in `scratch/registry/README.md` would have read `app-registry.json` beside
  `registry.schema.json`, i.e. as two unrelated files, and every future contributor reads that table.
  After publication it is a breaking change **plus a dead public URL**, because the `$id` is
  `raw.githubusercontent.com/personalclaw/registry/main/…`. `ET-4a` exists to spend the cheap moment,
  so including it serves the scope line rather than violating it. 30 references across 12 files;
  **the `$id` was updated in BOTH places in lockstep** — the committed mirror and `build_schema()`'s
  literal in `validate_registry.py` — because `test_the_published_schema_matches_the_python_authority`
  compares them byte-for-byte, so changing only one reds. The four `fixtures/registries/*.json`
  `$schema` pointers and `app-registry.json`'s own moved too; a dangling relative `$schema` is exactly
  the silent breakage this atom is about.
  **DELIBERATELY LEFT.** (1) The `### C2 — registry.json schema` heading above: `dag.json`'s `ET-3`
  scope string cites it verbatim and `dag.json` is fenced, so renaming the heading would strand a
  citation the fenced file cannot follow. **The schema rename created no second stranded citation** —
  `dag.json` mentions `registry.schema.json` only inside `ET-4`'s `blocked_reason` prose, which the
  owner is rewriting anyway. (2) Every dated measurement in the logs above, for the reason given in the
  note directly above this section. (3) The `registry.json` at the durability **sync root**
  (`durability/registry.py` `REGISTRY_KEY`, `sync_transports/base.py`, `DURABILITY-AND-SYNC.md`) and
  `evals/ablation_registry.json` are unrelated files that merely share a substring — zero of those 20
  hits were touched.
  **`ET-4` stays `⬜`.** One of three measured blockers cleared; the other two are outside core — the
  public repo does not exist (owner task 1) and the index is `{"apps": []}` until `ET-6`.
  **RAILS.** `tests/test_registry_validation.py::test_the_staged_content_is_complete` pins the
  published filename in a hardcoded manifest, and `::test_every_row_in_the_live_registry_satisfies_the_schema`
  reads the file by name — both red on `FileNotFoundError`/`AssertionError` if the rename is reverted
  halfway. That second test's anti-vacuity leg is pre-existing and load-bearing: the live index is
  empty, so it appends one fixture row (`assert len(rows) >= 1`) rather than passing over zero rows.
  `tests/test_app_catalog.py::test_the_seeded_registry_lists_only_under_cores_index_filename` keeps
  both directions — an index under core's name lists `probe-app`, an index under the old
  `registry.json` name lists nothing — so it now also pins the rename rather than describing a gap.

- **2026-08-27 — DISCOVERY (`ET-4a`): the published CI workflows were pinned by NOTHING, and the
  failure mode they carry is invisible to the published repo's own CI.** Found by falsification, not by
  reading. Reverting **all four** index references in
  `scratch/registry/.github/workflows/validate-listings.yml` to the pre-`ET-4a` `registry.json` —
  mutation confirmed by `git grep` — left the core suite **green: 98 passed, 0 failed**. The only
  pre-existing test that reads the workflows,
  `test_the_ci_workflow_never_passes_the_file_repo_flag`, asserts the *absence* of
  `--allow-file-repos` and nothing else. So had the workflow sweep been missed, core would have stayed
  green through review and merge, and the break would have surfaced on the first contributor listing PR
  **in the public repo** — the most expensive place available.
  **What makes it worse than an ordinary missing rail:** the name appears in a `paths:` filter. A stale
  `paths:` filter does not fail the workflow, it stops the workflow from **firing at all**, which
  presents as a green PR. A registry whose listing validation silently never runs is strictly worse
  than one that reds, because the whole point of the tier is that no human reviews listings.
  **RAIL ADDED, in core, deliberately — `test_the_ci_workflows_name_the_index_core_actually_reads`.**
  Judgment, since this could have been handed to whoever owns the public repo's CI: it **cannot** live
  there. The failure mode is CI *not firing*, and no CI can self-detect its own non-execution — a
  green from a workflow that never ran is indistinguishable from a green from a workflow that passed.
  Core is also the right home on ownership grounds: `scratch/registry/` is core-tracked content, core
  already owns two rails over it, and the seam already existed (the pre-existing workflow test), so
  this is an assertion added to a file that was already being read, not new machinery. The rail
  imports `catalog._REGISTRY_FILENAME` rather than a literal, so it reds if core's index name ever
  changes without the published workflows following, and it carries a vacuity assertion
  (`naming >= 2`) because a rule about how the workflows spell the index is empty if none of them
  spell it — the third workflow, `comment-listing-verdict.yml`, legitimately never names the index.

## Execution log — `ET-6` (four exemplar apps + registry listings) — 2026-09-02

- **2026-09-02 — DONE (`ET-6`): four scaffold-generated exemplars built, published, listed, and
  install-validated end to end.** Each was generated by `personalclaw app new <name> --type <T>`,
  minimally implemented (≤300 LOC, README-led), and published as a public org repo:
  `channel-null` (channel — the teaching null-transport: honest lifecycle, send refused when
  disconnected, inspectable outbox), `inbox-github-notifications` (inbox — stdlib GitHub-notifications
  poller, high-water checkpoint, degrade-to-empty), `watched-source-github` (trigger_source — own
  asyncio watch loop, `new_release`/`new_issue`, first-observation plants a silent high-water mark),
  `action-home-assistant` (action — HA webhook, validate-don't-raise, no dry-run/reversal claim).
  All four **listed in the registry** via PR #2327: `validate_registry.py`'s four front-door checks
  (repo liveness, manifest parse + row agreement, license, `SkillScanner` dry-run) ran against the
  live repos and returned listable + verdict `clean` on each. **Store git-URL install validated
  headless** — the exact Store path (`apps.source.resolve` → `apps.app_manager.install`) in an
  isolated `PERSONALCLAW_HOME`, all four install + register. **V3 fork-simulate PASS** —
  `channel-null` cloned → renamed to `my-null-channel` → installed through the same scanner gate.
