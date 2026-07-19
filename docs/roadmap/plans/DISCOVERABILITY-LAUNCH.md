# Plan: Discoverability & Launch — Existing in Both Search Channels

**Status:** DESIGNED — deepened 2026-07-18 (initial PROPOSED 2026-07-18; owner direction: GitHub org + static website repo now; elaborate SEO/discoverability program; naming DECIDED: PersonalClaw everywhere, personalclaw.dev primary)
**Created:** 2026-07-18
**Wave:** 0 (Sessions 1-3, launch-gating) + 1 (Sessions 4-5, content program)
**Depends on:** PUBLICATION (amended — org transfer is its S1 step 1; this plan's S1 executes together with it), DISTRIBUTION S2 (`/install` script content), SECURITY-LEGIBILITY (threat model page), CI-RELEASE-ENGINEERING S1 (badges must be green before any publicizing).
**Scope:** the public surface — org, website, docs site, launch assets, and the content program for the two discovery channels that now dominate OSS adoption: classic search/GitHub browse, and **LLM recommendation** (models answer "best self-hosted AI assistant" from crawled READMEs, docs, comparisons, llms.txt). **Soul guardrail:** PLATFORM-LEGIBILITY's "no docs portal" guardrail governs *agent-facing docs-as-data* and stands; this is the *human* surface. One canonical source: site docs are **generated from the repo `docs/` tree at build time — never forked copies** (a drift check enforces it). No paid marketing, no growth hacking, no analytics beyond the host's default logs (zero-telemetry stance extends to the site: no trackers).

---

## Context (verified 2026-07-18)

No domain/site/docs-site/demo; README screenshot is a placeholder (`docs/assets/screenshot-dashboard.png` referenced, absent); repos lack topics/homepage/social preview; `[project.urls]` absent (DISTRIBUTION T1.1). personalclaw.dev free (Google Registry RDAP); GitHub org `personalclaw` + PyPI + npm names free; personalclaw.com/.ai third-party-held. Owner decisions: zero-telemetry is a named marketing line; research-learnings republication deferred to the site (owner #15).

## Design

- **Website repo `personalclaw/personalclaw.dev`:** Astro + Starlight (static, fast, docs-native, MD/MDX — the docs tree drops in nearly verbatim; solid sitemap/SEO defaults; no runtime JS requirement). Structure: `src/content/docs/` populated **at build** by a sync script pulling `docs/{guides,reference,architecture,security}` + curated `research/learnings` from the core repo (git submodule or CI checkout — CI checkout preferred, no submodule friction); landing page + comparison pages authored in the site repo (they're marketing, not product docs); `public/install` (DISTRIBUTION T2.2's script), `public/llms.txt`, `public/llms-full.txt` (generated: llms.txt = curated index with one-line descriptions; llms-full = concatenated docs), OG/social images. Deploy: GitHub Pages via Actions (custom domain + HTTPS; zero external hosting accounts) — Pages is the default; any later host swap is invisible behind the domain.
- **Landing page above the fold:** category claim ("An agentic operating system for one person"), hero GIF, the bootstrap one-liner, badge row, three differentiator cards (security architecture → threat model; memory + knowledge depth; provider *and runtime* agnosticism/ACP), "**Zero telemetry** — your machine, your data, no phoning home" as a named feature card.
- **Launch assets:** screenshot set (dashboard home, chat with a tool-approval brief, loop cockpit mid-run, knowledge answer with citations, Store consent surface showing declared permissions) + a 60-90s silent capture (chat→approval→loop→knowledge→artifact/widget produced — the Canvas counter). Captured on a **seeded demo home** (`--seed` fixture; never the owner's real data). README rework: GIF above the fold, 3-command install, badges, highlights table, security section (plan 35 T2.4).
- **Comparison pages (S4):** `/compare/openclaw`, `/compare/hermes-agent` — feature matrix + philosophy + honest "choose them if" columns. Claims about competitors carry retrieval dates; matrix rows only for verifiable public facts.
- **Listings (S4):** awesome-self-hosted + awesome-ai-agents PRs (follow each list's contribution rules), selfh.st, AlternativeTo; Show HN + r/selfhosted + r/LocalLLaMA — **gated on the P0 gate**: CI green, one-liner works, real screenshots live. Launch post: the architecture-receipts narrative citing the threat model.
- **Research republication (S5, owner #15):** curated learnings topics as a site section with a preface owning the built-agentically story.

## Contracts & artifacts (mostly doc/site artifacts; the two structured pieces pinned)

- **Docs-sync contract (`scripts/sync-docs.mjs`, site repo):** build-time only; checks out core `docs/{guides,reference,architecture,security}` → Starlight content. **The site repo commits NO copies of core docs** (drift rail — a link-check + a "no committed docs/ copies" CI assertion enforce it). One canonical source per the tenet.
- **`llms.txt` format:** the emerging convention — `# PersonalClaw` H1, one-paragraph what-it-is, then `## Docs` with `- [Title](url): one-line` bullets for the key pages; `llms-full.txt` = build-time concatenation of guides+reference. Both at domain root, `text/plain`.
- **Comparison data (`src/data/comparisons.json`):** `{competitor, claims:[{feature, personalclaw, them, source_url, retrieved:"<ISO>"}]}` — **every competitor claim carries a source URL + retrieval date** (anti-staleness + honesty rail).
- **Integration points:** consumes DISTRIBUTION's `/install` script (T2.2), SECURITY-LEGIBILITY's threat-model, LEARNING-VISIBILITY's benchmark results, the research-learnings corpus (owner #15). Org/domain owner tasks gate S1.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Claim + org migration (executes with PUBLICATION S1)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | (Owner-led, see owner tasks 1-3) org created, repos transferred, domain registered — executor verifies redirects + updates any hardcoded `keyurgolani/` URLs in both repos (grep sweep) | both repos: grep `keyurgolani/` → replace with `personalclaw/` | grep clean; old URLs 301 to new |
| T1.2 | Repo metadata: descriptions + topics per PUBLICATION S1.5 list; homepage=https://personalclaw.dev on both | GitHub settings (executor via `gh repo edit`) | `gh repo view` shows all fields |
| T1.3 | Scaffold `personalclaw/personalclaw.dev`: Astro+Starlight init, Pages deploy workflow, domain config (CNAME), empty-but-styled landing | new repo | site serves at personalclaw.dev over HTTPS |
| T1.4 | Docs sync script: CI step checks out core repo, maps `docs/{guides,reference,architecture,security}` into Starlight content with nav; drift check = sync is build-time only, site repo contains no committed copies of core docs | site repo: `scripts/sync-docs.mjs`, workflow | site rebuild reflects a core docs edit with no manual step; repo tree has no doc copies |
| V1 | Validation: personalclaw.dev serves landing + docs sections; lighthouse pass ≥90 perf/SEO; no tracker requests in the network tab | — | holds |

### Session 2 — Docs site + machine-readable surface

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Information architecture: Guides / Reference / Architecture / Security / Roadmap(link to repo) nav; getting-started is the docs landing | site repo config | every core doc reachable ≤2 clicks; internal links resolve (link-check step in site CI) |
| T2.2 | `llms.txt` (curated: project one-liner, install, key doc URLs with one-line descriptions) + `llms-full.txt` (build-time concatenation of guides+reference) at domain root | site repo: generation in `sync-docs.mjs` | both fetch with correct content-type; llms-full regenerates per build |
| T2.3 | Landing page per Design (hero copy, one-liner, differentiator cards, zero-telemetry card, footer links incl. threat model + SECURITY.md) | site repo | copy matches Design; all links live |
| T2.4 | Sitemap + OG/social meta + per-repo social-preview images (1280×640: name, tagline, coral identity per `web/DESIGN.md` palette) | site repo + image assets; upload via repo settings (owner task 5) | rich embeds render in a link-preview checker |
| V2 | Validation: `curl personalclaw.dev/llms.txt` sane; Google Rich Results test passes on landing; docs search (Starlight default) returns getting-started for "install" | — | holds |

### Session 3 — Launch assets

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Build the demo seed fixture: believable non-personal data (a project, tasks, a knowledge doc set, memory entries, one loop) for screenshot/GIF capture | core repo: `tests_fixtures/demo-home/` (follow existing fixture layout) | `personalclaw gateway --seed demo-home` boots a demo-ready dashboard |
| T3.2 | Capture the five screenshots + the 60-90s GIF/MP4 per Design list (owner performs capture if executor lacks a display; script the click-path either way as `docs/assets/capture-script.md`) | core repo: `docs/assets/*.png`, site repo hero media | README placeholder replaced; assets referenced from site hero |
| T3.3 | README rework (core): GIF above fold, badges, 3-command install, highlights, security section; apps-repo README gets badges + org links | `README.md` both repos | a 30-second skim answers what/why/how-to-install |
| T3.4 | Launch post draft: architecture-receipts narrative (security-first personal agent; cite threat model, scanner gate, egress chokepoint, zero telemetry; honest limitations paragraph) | site repo: `src/content/blog/launch.md` (or docs section if no blog collection) | draft complete; owner sign-off pending (owner task 6) |
| V3 | Validation: fresh-eyes pass — a person who's never seen the project reads README + landing for 60 seconds and can say what it is and how to install (test on one human or as a structured self-review against those two questions) | — | recorded in Execution log |

### Session 4 — Comparison + listing program (Wave 1)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Comparison matrix data file (claims + sources + retrieved dates) then `/compare/openclaw` and `/compare/hermes-agent` pages rendered from it | site repo: `src/data/comparisons.json` + pages | every competitor claim has source+date; "choose them if" section present on both |
| T4.2 | Listing submissions: PRs to awesome-self-hosted + awesome-ai-agents per their CONTRIBUTING rules; selfh.st + AlternativeTo entries drafted (owner submits where accounts are needed) | external + `docs/roadmap/plans/` Execution log records URLs | PRs open; entries drafted with copy + links |
| T4.3 | Launch checklist doc: the P0 gate items + posting plan (Show HN title/body draft, r/selfhosted + r/LocalLLaMA post drafts adapted per community norms) | site repo: `launch-checklist.md` (internal) | drafts ready; gate items each link to their proof (CI badge, install VM log, screenshots) |
| V4 | Validation: comparison pages fact-checked against sources; gate checklist all-green before any owner posting | — | holds |

### Session 5 — Research republication (Wave 1+, owner #15)

| ID | Task | Files | Done when |
|---|---|---|---|
| T5.1 | Curate learnings topics for publication (all 14 unless owner trims), preface owning the built-agentically story + how the corpus is used | site repo section via sync script extension | topics render; preface approved (owner task 7) |
| V5 | Validation: spot-check three topics render with intact cross-links | — | holds |

## Owner tasks (real world)

1. **Register personalclaw.dev** (any registrar; ~$12/yr) and point DNS at GitHub Pages (A/AAAA + CNAME per Pages docs) — before S1.
2. **Create the `PersonalClaw` GitHub org** and **transfer both repos** (Settings → Transfer; org must exist first; plan for a few minutes of Actions re-enable per repo post-transfer).
3. **Reserve PyPI/npm names** if DISTRIBUTION hasn't already (placeholder publishes).
4. Decide on pursuing **personalclaw.com/.ai** from their current holders (lookup → offer) or explicitly dropping them — record the decision.
5. **Upload social-preview images** to both repos (Settings → Social preview) when T2.4 produces them.
6. **Sign off the launch post** and personally make the Show HN / Reddit posts (community norms favor the author posting; timing your call once the gate is green).
7. **Approve the research-library preface** (S5) — it publicly owns the AI-built story; that's your voice to approve.
8. Optional: order stickers. (Kidding. Unless.)

## Risks & open questions

- **Site/docs drift** — mitigated structurally (build-time sync, no committed copies, link-check CI).
- **Comparison pages age** — the data file carries retrieval dates; refresh cadence = each release, checklist item in release.yml notes.
- **Open:** blog collection vs docs-only Starlight — default: enable Starlight's blog only if the launch post wants an RSS feed (it does — LLM crawlers and aggregators both consume RSS; ship it).
