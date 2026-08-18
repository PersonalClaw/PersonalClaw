# PUBLICATION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PUBL.md`](../atomic/PUBL.md) as 10 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Publication — GitHub Release of Core + Apps Repos

**Status:** DONE (2026-07-19 → 2026-07-22) — the release sequence executed. The `PersonalClaw` org
holds public `PersonalClaw/PersonalClaw` + `PersonalClaw/PersonalClawApps` (+ `personalclaw.dev`);
descriptions/topics/homepage set per S1.5; the hardcoded release URL reconciled
(`dashboard/handlers/core.py:118`); `CHANGELOG.md` created and now carrying v0.1.0-v0.1.3;
**v0.1.0 tagged and released 2026-07-22**, with v0.1.1/v0.1.2/v0.1.3 following (PyPI core + client,
GHCR images, GitHub Releases). S1.6's `docs/assets/screenshot-dashboard.png` placeholder is
superseded rather than open — real captures ship as `docs/screenshots/{light,dark}/*` +
`SHOWCASE.md` + a reproducible `docs/screenshots/CAPTURE.md`.
**Remaining are OWNER real-world steps only:** the S2 fresh-clone / self-update / Store-git-source
walkthroughs on a clean machine, and S1.9's publicize-when-the-DISCOVERABILITY-gate-is-green call.
Status corrected 2026-08-04 (this line had read READY since 2026-07-14).
**Amended 2026-07-18 (roadmap rev 9):** naming decided — **PersonalClaw everywhere**; repos under the
**`PersonalClaw` GitHub org**; primary domain **personalclaw.dev**. The SOURCE_REV force-push
provenance step is retired: development proceeds via feature branches merged to `main`, real history
from v0.1.0 forward, **no force pushes** (the `git pull`-based self-updater depends on this).

## Context

The pre-publication campaign validated every surface as-a-user, cleaned all
internal-only terminology and logic, enforced the provider-agnostic core tenet, and
split the workspace into two publishable repositories:

- **Core** (`PersonalClaw/`) — fresh history, initial commit `ed7af37`; docs
  (architecture / reference / guides), README, CONTRIBUTING, LICENSE, roadmap included.
- **Apps** (`apps/`) — fresh history, initial commit `b81ab49`; 36 first-party app
  bundles, platform docs, app-creation guide, per-app READMEs, LICENSE.

Both repos passed the final gate: tests green from fresh clones (known pre-existing
failure set enumerated and proven at baseline), docs walkthrough clean as a stranger,
internal-term sweep clean, PII clean, no tracked artifacts or credentials.

## Executed 2026-07-19 (release mechanics)

The org existed (`github.com/PersonalClaw`); rather than transfer, the private
`keyurgolani/personalclaw` + `keyurgolani/personalclaw-apps` repos were replaced by
**fresh repositories under the org with SEO/brand-cased names**: `PersonalClaw/PersonalClaw`
(core) and `PersonalClaw/PersonalClawApps` (apps), each seeded with **one squashed initial
commit** (the last squash — development is feature-branches-to-`main` from here). A third
empty repo `PersonalClaw/personalclaw.dev` was created for the marketing site
(DISCOVERABILITY-LAUNCH S1). The old `keyurgolani/*` repos were deleted after the new pushes
verified. Package name stays lowercase `personalclaw` (PyPI/PEP 508); GHCR namespace stays
lowercase `ghcr.io/personalclaw` (Docker requirement) — both correctly map to the org.

## Session 1 — Release (manual, ~1 session)

1. Create the **`PersonalClaw` GitHub org** (done); create fresh `PersonalClaw/PersonalClaw`
   + `PersonalClaw/PersonalClawApps` (done, brand-cased for SEO). Register **personalclaw.dev**;
   reserve the **PyPI and npm `personalclaw` names**
   (verified free 2026-07-18 — placeholder publish if DISTRIBUTION S2 hasn't landed).
2. Verify `main` is green per CI-RELEASE-ENGINEERING S1 (red-test triage) before flipping
   visibility to public.
3. Reconcile the hardcoded release URL at `src/personalclaw/dashboard/handlers/core.py:117`
   to `github.com/PersonalClaw/PersonalClaw/releases`.
4. Replace `<your-clone-url>` in the core README Quickstart with the real URL;
   point `apps/README.md`'s core link at the published repo. Set homepage=personalclaw.dev
   on both repos.
5. Set repo descriptions + topics:
   - Core: "Your self-hosted personal AI agent — an agentic OS for one person: chat,
     autonomous goal loops, memory, knowledge base, skills, automation, and a
     permission-gated app platform. Local-first, provider-agnostic, MIT."
     Topics: ai-agent, personal-assistant, self-hosted, local-first, llm, agentic,
     automation, python, react, mit-license.
   - Apps: "First-party app bundles for PersonalClaw — model providers, search,
     agents (ACP), channels (Slack), tools, and full backend+UI apps. Each installs
     through the scanner-gated Store." Topics: personalclaw, plugins, llm-providers,
     app-store, python.
6. Capture the dashboard screenshot README references
   (`docs/assets/screenshot-dashboard.png` placeholder), commit.
7. Create `CHANGELOG.md` (Keep-a-Changelog format) with the v0.1.0 entry — the in-app
   Updates panel (`GET /api/changelog`, `updates.py:217`) already reads it and currently
   points at a missing file.
8. Tag `v0.1.0` on core (self-update pulls from `main` today; DISTRIBUTION S4 moves it
   to tag-tracking — the tag anchors releases either way).
9. Publicize when the DISCOVERABILITY-LAUNCH S1-3 gate is met (site live, real
   screenshots, install one-liner working).

## Session 2 — Post-publication verification (~1 session)

1. Fresh clone from GITHUB (not local) on a clean machine/venv; run the getting-started
   guide verbatim; fix any remote-specific friction (URL casing, submodule-free clone,
   raw-content links in docs).
2. Verify the self-update pipeline end-to-end against the real remote: gateway on a
   clone one commit behind → check detects → apply pulls/rebuilds/re-execs.
3. Verify Store git-source install from the published apps repo URL
   (`POST /api/apps/sources` git path — validated pre-publication only with local paths).

## Follow-ups unblocked by publication (separate plans / roadmap items)

- **Multi-source update aggregation** (user-raised 2026-07-13): update checks for the
  apps repo + user-added app sources; Store update badges; batch "update all apps";
  Updates page as aggregate view. Cannot be designed before release conventions exist.
- **Desktop bundle**: rebuild `desktop/backend-dist` fresh before shipping any desktop
  artifact (stale tree was deleted pre-split) — now owned by DESKTOP-CAPABILITIES S1.
- **Known pre-existing test failures** (enumerated in the campaign gate): root-cause
  test_process_tree/provider_helpers/registry_config_sync + the 10 gateway cron-callback
  failures — now owned by CI-RELEASE-ENGINEERING S1 (fix or annotate in code; a plan-doc
  ledger is not a substitute for a green suite).
- **App-owned CLI setup contributions** (documented S08 judgment): move channel-app
  setup flows from the core CLI passthrough into app-owned CLI contributions — now owned
  by PROVIDER-BOUNDARY-COMPLETION S2.

## Execution log

### 2026-08-18 — `PUBL-9` DONE (Store git-source install from the published apps repo)

Drove the real endpoints against `https://github.com/PersonalClaw/PersonalClawApps` on an
isolated home (`.dev-home`, auth ON — token in `?token=`, never `AUTH_MODE=none`).

**Numbers.** The published repo has NO `app-registry.json`, so it reaches the Store through
the clone-then-subdir-scan fallback: cold `GET /api/apps/catalog` = **4.3s → 45 `gitApps`**,
all `sourceKind: "git"`, each with `pointer: "<url>#<app>"`. `POST /api/apps` with a catalog
pointer installed `wikipedia-search` 0.1.0 (**HTTP 201**, scan `clean`/`community`,
`origin: external`) — present under `$PERSONALCLAW_HOME/apps/` and in `GET /api/apps`. A
second install (`duckduckgo-search`) confirmed the path after the fixes. Third catalog call
(both sources cached) = **0.14s**, so the TTL cache is real and a repeat measurement can fake
a pass — `_GIT_SCAN_TTL_SECS` is 300s.

**Two defects found and fixed.**

1. *Multi-app repo URL is illegible.* `POST /api/apps` with the repo URL as a user would
   paste it (no `#app`) failed with `no app.json in source` — true of the repo ROOT and
   useless to someone holding 45 apps. `source.resolve` now detects the multi-app shape at
   the clone and returns: `… holds 45 apps, not one — install a single app by appending #app
   to the URL, e.g. …#alibaba-models. Available: …, and 40 more.` (HTTP 400). Verified live.
2. *The `.git` suffix forks one repo into two sources.* `POST /api/apps/sources` with
   `…/PersonalClawApps` (no `.git`) appended a SECOND source beside the shipped `.git`
   default: two indistinguishable rows, and a redundant full shallow clone per catalog
   refresh — **measured 3.42s for zero extra apps** (all 45 entries stayed attributed to the
   default; name-dedup hid the effect). Git sources now de-dup on a normalized key across
   defaults + user entries, on add, list and remove. The raw string is still what gets
   cloned, since the suffix is load-bearing for a bare `file://` remote. A duplicate written
   before the fix collapses on read — no migration needed.

**Regression.** `_scan_git_source` had **zero** functional coverage (its only mention was a
spawn-ceiling allowlist row). Added 8 offline tests that build a real bare git repo of the
published shape and drive it over `file://` — the same git code path, no network, no skips.
Includes a vacuity floor (an exact `len(git_apps) == 2`, so a scan finding nothing FAILS
rather than passing forever on `[]`) and a TTL-cache test.

**DISCOVERY — a silent source is unreportable.** `POST /api/apps/sources` returns
`{"ok": true}` for a URL that cannot be cloned (verified with a typo'd repo), and
`available_catalog()` has no error channel at all — `_scan_git_source` swallows the clone
failure and caches `[]` for 5 minutes. A user who mistypes the repo URL gets a permanently
empty source row and no explanation. Fixing it needs a new catalog field plus a frontend
surface, so it is out of this atom's scope. **Owner decision needed.**

**DISCOVERY — the unit suite clones the published repo over the network.** `available_catalog()`
issues 3 git subprocesses against the real remote (a treeless registry probe, a `git show`,
and the depth-1 scan clone); nothing in `tests/conftest.py` neutralizes `_DEFAULT_GIT_SOURCES`
or clears `_git_scan_cache`/`_registry_cache`. Every test calling `available_catalog()` reaches
github.com, and OFFLINE it degrades to zero apps silently rather than failing — so the
dependency cannot be noticed. The new tests opt out via a local fixture; a suite-wide
conftest guard is deliberately NOT taken here (it would break
`test_default_first_party_git_source_present`, which asserts the default IS present).
**Owner decision needed.**

**Gate.** `make lint` clean (black 1769 files, isort, flake8, mypy 908 files); targeted
`pytest` 57 passed (`test_app_catalog` + `test_inbox_app_sources` + `test_app_install_fix_prompt`
+ `test_spawn_ceiling_audit`); full `make test` green. Real home `~/.personalclaw` unchanged:
110496 files before and after, 0 files newer than the drive cutoff (probe positive control 1,
negative control 0).
