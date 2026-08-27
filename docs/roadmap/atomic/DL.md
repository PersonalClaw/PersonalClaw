# DISCOVERABILITY-LAUNCH — atomic plans

**Source plan:** [`DISCOVERABILITY-LAUNCH`](../plans/DISCOVERABILITY-LAUNCH.md)  
**Code:** `DL`  
**Source status:** in_progress

DISCOVERABILITY-LAUNCH decomposed into 9 atoms (DL-1..DL-9): 3 done (S1 migration/scaffold/sync; S2 docs site + llms.txt + landing + release flip, PR #20; S3 screenshots + README rework), 6 todo (T3.1 demo-home seed [core], launch media GIF+social images, launch-post draft, comparison pages, listing/checklist program, research republication). Verified against code: only the empty fixture ships (T3.1 open), screenshots+capture infra present, 14-topic learnings corpus at docs/research/learnings/, no live keyurgolani/ product URLs.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DL-1` | ✅ | S1: claim + org migration + site scaffold + docs-sync script | `EXT:DISTRIBUTION:/install one-liner referenced by scaffold` | PersonalClaw org holds the repos with metadata/topics/homepage set; no live keyurgolani/ product URLs remain; personalclaw.dev serves a styled landing + docs over HTTPS via Pages; scripts/sync-docs.mjs pulls core docs/{guides,reference,architecture,security} at build with zero committed doc copies |
| `DL-2` | ✅ (#website #20) | S2: docs IA + llms.txt/llms-full.txt + landing page + sitemap/OG + release flip | `DL-1` | every core doc reachable <=2 clicks with link-check green; llms.txt + llms-full.txt serve at domain root as text/plain and regenerate per build; landing page copy matches Design (differentiator + zero-telemetry cards, threat-model/SECURITY footer); sitemap + OG meta ship; source manifest flipped to channel: released with core+apps pinned v0.1.3 |
| `DL-3` | ✅ | S3: reproducible screenshot set + README/SHOWCASE rework | `DL-1` | docs/screenshots/{light,dark}/*.png ship reproducibly with capture.mjs + CAPTURE.md; core README reworked with badges, 3-command install, highlights, security section; apps-repo README carries badges + org links |
| `DL-4` | ✅ | T3.1: build the demo-home seed fixture (core) | — | src/personalclaw/tests_fixtures/demo-home/ exists following the empty-fixture layout with believable non-personal data (project, tasks, knowledge docs, memory entries, one loop); `personalclaw gateway --seed demo-home` boots a demo-ready dashboard |
| `DL-5` | ⬜ | T3.2 remainder + T2.4 residual: launch media assets (60-90s demo GIF + social-preview images) | `DL-4` | 60-90s silent capture (chat->approval->loop->knowledge->artifact) recorded on the seeded demo home and referenced from the site hero, with the click-path scripted; 1280x640 social-preview images produced per web/DESIGN.md palette ready for owner upload to both repos |
| `DL-6` | ⬜ | T3.4: launch-post draft (architecture-receipts narrative) | `EXT:SECURITY-LEGIBILITY:threat model + scanner gate + egress chokepoint narrative` | src/content/blog/launch.md draft complete citing threat model, scanner gate, egress chokepoint, zero telemetry, plus an honest limitations paragraph; owner sign-off recorded |
| `DL-7` | ⬜ | S4 T4.1: released-version capability matrix at /compare | `DL-1`, `EXT:LEARNING-VISIBILITY:benchmark results for matrix rows` | A /compare page publishes a capability matrix about PersonalClaw ONLY — what it does and does not do, with every row sourced to the PINNED/RELEASED core version (verified at the tag, not against main) and the 'does not do' rows included; the page is registered in the site contract and the sitemap, held to the metadata, runtime, axe WCAG A/AA and Lighthouse contracts, and carries an anti-vacuity check that FAILS when the matrix data is non-empty but the page renders no rows; peer/competitor columns are deliberately OUT of scope at pre-1.0 (owner taste call 2026-08-27 — see blocked_reason). |
| `DL-8` | ⬜ | S4 T4.2+T4.3: listing submissions + P0 launch checklist | `DL-3`, `DL-6`, `EXT:CI-RELEASE:green main + CI badge`, `EXT:DISTRIBUTION:install one-liner verified working` | awesome-self-hosted + awesome-ai-agents PRs drafted per their CONTRIBUTING rules; selfh.st + AlternativeTo entries drafted; launch-checklist.md lists the P0 gate items each linking their proof (CI badge, install log, live screenshots) with Show HN / r/selfhosted / r/LocalLLaMA post drafts; gate all-green before any owner posting |
| `DL-9` | ⬜ | S5 T5.1: research-learnings republication section | `DL-1` | the 14 learnings topics from core docs/research/learnings/ render on the site via a sync-script extension with intact cross-links, behind a preface owning the built-agentically story; preface approved by owner |

## Atom scopes

### `DL-1` — S1: claim + org migration + site scaffold + docs-sync script

**Status:** done

Session 1 — Claim + org migration (T1.1-T1.4, V1)

**Done when:** PersonalClaw org holds the repos with metadata/topics/homepage set; no live keyurgolani/ product URLs remain; personalclaw.dev serves a styled landing + docs over HTTPS via Pages; scripts/sync-docs.mjs pulls core docs/{guides,reference,architecture,security} at build with zero committed doc copies

### `DL-2` — S2: docs IA + llms.txt/llms-full.txt + landing page + sitemap/OG + release flip

**Status:** done (PR #website #20)

Session 2 — Docs site + machine-readable surface (T2.1-T2.4, V2)

**Done when:** every core doc reachable <=2 clicks with link-check green; llms.txt + llms-full.txt serve at domain root as text/plain and regenerate per build; landing page copy matches Design (differentiator + zero-telemetry cards, threat-model/SECURITY footer); sitemap + OG meta ship; source manifest flipped to channel: released with core+apps pinned v0.1.3

### `DL-3` — S3: reproducible screenshot set + README/SHOWCASE rework

**Status:** done

Session 3 — Launch assets (T3.2 screenshots portion, T3.3)

**Done when:** docs/screenshots/{light,dark}/*.png ship reproducibly with capture.mjs + CAPTURE.md; core README reworked with badges, 3-command install, highlights, security section; apps-repo README carries badges + org links

### `DL-4` — T3.1: build the demo-home seed fixture (core)

**Status:** todo

Session 3 — Launch assets (T3.1)

**Done when:** src/personalclaw/tests_fixtures/demo-home/ exists following the empty-fixture layout with believable non-personal data (project, tasks, knowledge docs, memory entries, one loop); `personalclaw gateway --seed demo-home` boots a demo-ready dashboard

### `DL-5` — T3.2 remainder + T2.4 residual: launch media assets (60-90s demo GIF + social-preview images)

**Status:** todo

Session 3 — Launch assets (T3.2 GIF) + Session 2 T2.4 residual (social-preview images)

**Done when:** 60-90s silent capture (chat->approval->loop->knowledge->artifact) recorded on the seeded demo home and referenced from the site hero, with the click-path scripted; 1280x640 social-preview images produced per web/DESIGN.md palette ready for owner upload to both repos

### `DL-6` — T3.4: launch-post draft (architecture-receipts narrative)

**Status:** todo

Session 3 — Launch assets (T3.4)

**Done when:** src/content/blog/launch.md draft complete citing threat model, scanner gate, egress chokepoint, zero telemetry, plus an honest limitations paragraph; owner sign-off recorded

### `DL-7` — S4 T4.1: released-version capability matrix at /compare

**Status:** todo

Session 4 — Comparison + listing program (T4.1)

**Done when:** A /compare page publishes a capability matrix about PersonalClaw ONLY — what it does and does not do, with every row sourced to the PINNED/RELEASED core version (verified at the tag, not against main) and the 'does not do' rows included; the page is registered in the site contract and the sitemap, held to the metadata, runtime, axe WCAG A/AA and Lighthouse contracts, and carries an anti-vacuity check that FAILS when the matrix data is non-empty but the page renders no rows; peer/competitor columns are deliberately OUT of scope at pre-1.0 (owner taste call 2026-08-27 — see blocked_reason).

### `DL-8` — S4 T4.2+T4.3: listing submissions + P0 launch checklist

**Status:** todo

Session 4 — Comparison + listing program (T4.2, T4.3, V4)

**Done when:** awesome-self-hosted + awesome-ai-agents PRs drafted per their CONTRIBUTING rules; selfh.st + AlternativeTo entries drafted; launch-checklist.md lists the P0 gate items each linking their proof (CI badge, install log, live screenshots) with Show HN / r/selfhosted / r/LocalLLaMA post drafts; gate all-green before any owner posting

### `DL-9` — S5 T5.1: research-learnings republication section

**Status:** todo

Session 5 — Research republication (T5.1, V5)

**Done when:** the 14 learnings topics from core docs/research/learnings/ render on the site via a sync-script extension with intact cross-links, behind a preface owning the built-agentically story; preface approved by owner

