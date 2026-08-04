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
