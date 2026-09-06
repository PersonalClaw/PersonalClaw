# DISCOVERABILITY-LAUNCH — atomic plans

**Source plan:** [`DISCOVERABILITY-LAUNCH`](../plans/DISCOVERABILITY-LAUNCH.md)  
**Code:** `DL`  
**Source status:** in_progress

DISCOVERABILITY-LAUNCH decomposed into 11 atoms (DL-1..DL-11): 8 done (S1 migration/scaffold/sync; S2 docs site + llms.txt + landing + release flip, PR #20; S3 screenshots + README rework; T3.1 demo-home seed fixture; T3.2+T2.4 launch media — 84s silent tour referenced from the site hero via personalclaw.dev#54, plus both 1280x640 social cards; T3.4 launch-post draft, published on a source-controlled blog tier in personalclaw.dev@cab5065; S4 T4.1 /compare capability matrix; S4 T4.2+T4.3 listing submissions + P0 launch checklist), 3 todo and ALL THREE OWNER-SIDE: DL-9's engineering is merged on the site's main (dec8113) with only its owner residual open, and DL-10 (community listing submissions, calendar-gated and human-contributor-only) and DL-11 (P0 launch-gate clearance before any public posting) are actions an agent must not take. Verified against code, not against this plan's log: the seeded fixture binds a local model (#2538), the capture is 83.7s / 1280x720 / one video stream / no audio track, both social cards re-measure 1280x640, the 14-topic learnings corpus is at docs/research/learnings/, and no live keyurgolani/ product URLs remain.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DL-1` | ✅ | S1: claim + org migration + site scaffold + docs-sync script | `EXT:DISTRIBUTION:/install one-liner referenced by scaffold` | PersonalClaw org holds the repos with metadata/topics/homepage set; no live keyurgolani/ product URLs remain; personalclaw.dev serves a styled landing + docs over HTTPS via Pages; scripts/sync-docs.mjs pulls core docs/{guides,reference,architecture,security} at build with zero committed doc copies |
| `DL-2` | ✅ (#website #20) | S2: docs IA + llms.txt/llms-full.txt + landing page + sitemap/OG + release flip | `DL-1` | every core doc reachable <=2 clicks with link-check green; llms.txt + llms-full.txt serve at domain root as text/plain and regenerate per build; landing page copy matches Design (differentiator + zero-telemetry cards, threat-model/SECURITY footer); sitemap + OG meta ship; source manifest flipped to channel: released with core+apps pinned v0.1.3 |
| `DL-3` | ✅ | S3: reproducible screenshot set + README/SHOWCASE rework | `DL-1` | docs/screenshots/{light,dark}/*.png ship reproducibly with capture.mjs + CAPTURE.md; core README reworked with badges, 3-command install, highlights, security section; apps-repo README carries badges + org links |
| `DL-4` | ✅ | T3.1: build the demo-home seed fixture (core) | — | src/personalclaw/tests_fixtures/demo-home/ exists following the empty-fixture layout with believable non-personal data (project, tasks, knowledge docs, memory entries, one loop); `personalclaw gateway --seed demo-home` boots a demo-ready dashboard |
| `DL-5` | ✅ | T3.2 remainder + T2.4 residual: launch media assets (60-90s demo GIF + social-preview images) | `DL-4` | 60-90s silent capture (chat->approval->loop->knowledge->artifact) recorded on the seeded demo home and referenced from the site hero, with the click-path scripted; 1280x640 social-preview images produced per web/DESIGN.md palette ready for owner upload to both repos |
| `DL-6` | ✅ | T3.4: launch-post draft (architecture-receipts narrative) | `EXT:SECURITY-LEGIBILITY:threat model + scanner gate + egress chokepoint narrative` | src/content/blog/launch.md draft complete citing threat model, scanner gate, egress chokepoint, zero telemetry, plus an honest limitations paragraph; owner sign-off recorded |
| `DL-7` | ✅ | S4 T4.1: released-version capability matrix at /compare | `DL-1`, `EXT:LEARNING-VISIBILITY:benchmark results for matrix rows` | A /compare page publishes a capability matrix about PersonalClaw ONLY — what it does and does not do, with every row sourced to the PINNED/RELEASED core version (verified at the tag, not against main) and the 'does not do' rows included; the page is registered in the site contract and the sitemap, held to the metadata, runtime, axe WCAG A/AA and Lighthouse contracts, and carries an anti-vacuity check that FAILS when the matrix data is non-empty but the page renders no rows; peer/competitor columns are deliberately OUT of scope at pre-1.0 (owner taste call 2026-08-27 — see blocked_reason). |
| `DL-8` | ✅ | S4 T4.2+T4.3: listing submissions + P0 launch checklist | `DL-3`, `DL-6`, `EXT:CI-RELEASE:green main + CI badge`, `EXT:DISTRIBUTION:install one-liner verified working` | awesome-self-hosted + awesome-ai-agents PRs drafted per their CONTRIBUTING rules; selfh.st + AlternativeTo entries drafted; launch-checklist.md lists the P0 gate items each linking their proof (CI badge, install log, live screenshots) with Show HN / r/selfhosted / r/LocalLLaMA post drafts; gate all-green before any owner posting |
| `DL-9` | ⬜ | S5 T5.1: research-learnings republication section | `DL-1` | the 14 learnings topics from core docs/research/learnings/ render on the site via a sync-script extension with intact cross-links, behind a preface owning the built-agentically story; preface approved by owner |
| `DL-10` | ⬜ | Owner: submit community listings (awesome-self-hosted after 2026-11-21, awesome-ai-agents, selfh.st, AlternativeTo) | `DL-8`, `DL-11` | the awesome-self-hosted / awesome-ai-agents PRs and the selfh.st + AlternativeTo entries are submitted by the owner (awesome-self-hosted only on/after 2026-11-21) after the P0 launch gate (DL-11) is all-green — OWNER-ONLY (machine-PR ban + calendar-gated), belongs on gated_frontier |
| `DL-11` | ⬜ | Owner: P0 launch-gate clearance sign-off before any public posting (Show HN / r/selfhosted / r/LocalLLaMA) | `DL-8` | the owner records a P0-gate clearance decision (all checklist items green with their linked proof) authorizing the launch posts; posting itself is owner-only and gated on this clearance — OWNER-ONLY, belongs on gated_frontier |

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

**Status:** done

Session 3 — Launch assets (T3.1)

**Done when:** src/personalclaw/tests_fixtures/demo-home/ exists following the empty-fixture layout with believable non-personal data (project, tasks, knowledge docs, memory entries, one loop); `personalclaw gateway --seed demo-home` boots a demo-ready dashboard

### `DL-5` — T3.2 remainder + T2.4 residual: launch media assets (60-90s demo GIF + social-preview images)

**Status:** done

Session 3 — Launch assets (T3.2 GIF) + Session 2 T2.4 residual (social-preview images)

**Done when:** 60-90s silent capture (chat->approval->loop->knowledge->artifact) recorded on the seeded demo home and referenced from the site hero, with the click-path scripted; 1280x640 social-preview images produced per web/DESIGN.md palette ready for owner upload to both repos

**2026-09-06 — measured against the seeded demo home.** The click-path is scripted and
committed (`docs/demo/CLICKPATH.md` + its driver), the capture plays it in 82s silent at
1280x720 on `--seed demo-home`, and both 1280x640 cards are committed under `docs/brand/`.
Two clauses are NOT met, and neither is a scripting problem. (1) The specified arc cannot be
shown in full: the fixture binds no model provider, so a chat *turn* and a tool *approval*
have no way to occur, and no artifact exists in the fixture's tree — the path shows chat as
surface-only, substitutes the guardrail/audit-chain policy for the approval event, and drops
the artifact beat rather than staging one. Binding a local model into the fixture is the
single change that would close this, and it belongs to the fixture, not the capture. (2)
"Referenced from the site hero" is not reachable from this repo: the hero is
`src/pages/index.astro` in the `personalclaw.dev` repo, and that site's doc-sync
deliberately excludes core's asset trees, so the reference needs a commit there. The capture
itself is deliberately not committed (~1.2 MB mp4, no LFS, no committed-media convention at
that size); it is regenerated from the committed script.

**2026-09-06 (later) — both open clauses closed; flipped.** The two things the note above measured
as NOT met were each blocked on a different repo, and both landed. (1) The arc is now shown in
full: #2538's `gateway --seed demo-home --seed-local-model` binds a local model into the fixture,
so the re-capture plays all five specified beats for real — a `gemma4:12b` turn parking on the
`artifact_save` gate, a human **Allow** under `--approval interactive`, and the resulting markdown
artifact rendered from the home's `artifacts/` tree, with the audit chain
`invoked → approved → success → completed`. 83.7s, 1280×720, one video stream and no audio track.
(2) "Referenced from the site hero" landed as `personalclaw.dev` PR #54. The reference renders one
click away rather than on `/` itself, and that is measured rather than convenient: `.home-hero` is
a fixed-height clipped composition with `SystemWindow` absolutely positioned past the viewport
edge, so there is no "beside" the carousel; and the embedded variant was built and measured at
**+226ms LCP on `/`, leaving 90ms of a 2500ms budget**, because a `<video>` poster is fetched
eagerly. The player lives at `/product#tour` and the hero spends one text row. Zero media bytes
load on either route. The social-preview cards are unchanged and re-verified 1280×640.

### `DL-6` — T3.4: launch-post draft (architecture-receipts narrative)

**Status:** done

Session 3 — Launch assets (T3.4)

**Done when:** src/content/blog/launch.md draft complete citing threat model, scanner gate, egress chokepoint, zero telemetry, plus an honest limitations paragraph; owner sign-off recorded

### `DL-7` — S4 T4.1: released-version capability matrix at /compare

**Status:** todo

Session 4 — Comparison + listing program (T4.1)

**Done when:** A /compare page publishes a capability matrix about PersonalClaw ONLY — what it does and does not do, with every row sourced to the PINNED/RELEASED core version (verified at the tag, not against main) and the 'does not do' rows included; the page is registered in the site contract and the sitemap, held to the metadata, runtime, axe WCAG A/AA and Lighthouse contracts, and carries an anti-vacuity check that FAILS when the matrix data is non-empty but the page renders no rows; peer/competitor columns are deliberately OUT of scope at pre-1.0 (owner taste call 2026-08-27 — see blocked_reason).

### `DL-8` — S4 T4.2+T4.3: listing submissions + P0 launch checklist

**Status:** done (PR #2363)

Session 4 — Comparison + listing program (T4.2, T4.3, V4)

**Done when:** awesome-self-hosted + awesome-ai-agents PRs drafted per their CONTRIBUTING rules; selfh.st + AlternativeTo entries drafted; launch-checklist.md lists the P0 gate items each linking their proof (CI badge, install log, live screenshots) with Show HN / r/selfhosted / r/LocalLLaMA post drafts; gate all-green before any owner posting

### `DL-9` — S5 T5.1: research-learnings republication section

**Status:** todo

Session 5 — Research republication (T5.1, V5)

**Done when:** the 14 learnings topics from core docs/research/learnings/ render on the site via a sync-script extension with intact cross-links, behind a preface owning the built-agentically story; preface approved by owner


### `DL-10` — Owner: submit community listings (awesome-self-hosted after 2026-11-21, awesome-ai-agents, selfh.st, AlternativeTo)

**Status:** todo — OWNER-ONLY (belongs on gated_frontier, never ready_frontier)

DL-8 T4.2 owner action, minted explicit because these lists BAN automated/bot PRs — the owner must submit personally. awesome-self-hosted requires >=4 months since first release; v0.1.0 was 2026-07-21, so that submission is deferred until 2026-11-21. Submission drafts live in DL-8's launch/listing-submissions materials.

**Done when:** the awesome-self-hosted / awesome-ai-agents PRs and the selfh.st + AlternativeTo entries are submitted by the owner (awesome-self-hosted only on/after 2026-11-21) after the P0 launch gate (DL-11) is all-green

### `DL-11` — Owner: P0 launch-gate clearance sign-off before any public posting (Show HN / r/selfhosted / r/LocalLLaMA)

**Status:** todo — OWNER-ONLY (belongs on gated_frontier, never ready_frontier)

DL-8 T4.3 owner action: confirm every P0 launch-checklist gate item is all-green (CI badge, verified install one-liner + captured log, live screenshots, release integrity, honest front door) and sign off before the owner posts any launch thread. Checklist drafts live in DL-8's launch materials.

**Done when:** the owner records a P0-gate clearance decision (all checklist items green with their linked proof) authorizing the launch posts; posting itself is owner-only and gated on this clearance
