# DISCOVERABILITY-LAUNCH

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DL.md`](../atomic/DL.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Discoverability & Launch — Existing in Both Search Channels

**Status:** IN PROGRESS — S1 (claim + org migration) and S2 (docs site + machine-readable surface)
DONE; S3 (launch assets) mostly done; S4-S5 (comparison/listing program, research republication) not
started. Deepened 2026-07-18 (initial PROPOSED 2026-07-18; naming DECIDED: PersonalClaw everywhere,
personalclaw.dev primary).
**S1 done:** the org with three public repos, metadata set, no live `keyurgolani/` URLs left,
personalclaw.dev over TLS with the CI quality floor.
**S2 done (website `main`, PR #20, 2026-07-31):** Astro + Starlight serves `/docs` from
`scripts/sync-docs.mjs`, a build-time sync of the pinned core docs — `src/content/docs/` is
gitignored, so the site commits no copies (the drift rail holds). `llms.txt` + `llms-full.txt`,
sitemap and OG meta shipped. **The source manifest is now `channel: released` with core and apps both
pinned at v0.1.3 — the release flip is DONE**, the apps repo having been tagged v0.1.1-v0.1.3.
**S3 partial:** screenshots ship reproducibly (`docs/screenshots/{light,dark}/*` + `capture.mjs` +
`CAPTURE.md`) and the README/SHOWCASE rework landed. Still open: **T3.1's
`tests_fixtures/demo-home/` demo seed does not exist** (only `empty` ships, and `CAPTURE.md`
explicitly waits on it), the 60-90s capture, and T3.4's launch-post draft.
**Open OWNER tasks:** social-preview images on both repos, the Show HN / Reddit posting, and the S5
research-library preface approval. Status corrected 2026-08-04 by code audit.

---

## Context (verified 2026-07-18)

No domain/site/docs-site/demo; README screenshot is a placeholder (`docs/assets/screenshot-dashboard.png` referenced, absent); repos lack topics/homepage/social preview; `[project.urls]` absent (DISTRIBUTION T1.1). personalclaw.dev free (Google Registry RDAP); GitHub org `personalclaw` + PyPI + npm names free; personalclaw.com/.ai third-party-held. Owner decisions: zero-telemetry is a named marketing line; research-learnings republication deferred to the site (owner #15).

## Design

- **Website repo `personalclaw/personalclaw.dev`:** Astro + Starlight (static, fast, docs-native, MD/MDX — the docs tree drops in nearly verbatim; solid sitemap/SEO defaults; no runtime JS requirement). Structure: `src/content/docs/` populated **at build** by a sync script pulling `docs/{guides,reference,architecture,security}` + curated `research/learnings` from the core repo (git submodule or CI checkout — CI checkout preferred, no submodule friction); landing page + comparison pages authored in the site repo (they're marketing, not product docs); `public/install` (DISTRIBUTION T2.2's script), `public/llms.txt`, `public/llms-full.txt` (generated: llms.txt = curated index with one-line descriptions; llms-full = concatenated docs), OG/social images. Deploy: GitHub Pages via Actions (custom domain + HTTPS; zero external hosting accounts) — Pages is the default; any later host swap is invisible behind the domain.
- **Landing page above the fold:** category claim ("An agentic operating system for one person"), hero GIF, the bootstrap one-liner, badge row, three differentiator cards (security architecture → threat model; memory + knowledge depth; provider *and runtime* agnosticism/ACP), "**Zero telemetry** — your machine, your data, no phoning home" as a named feature card.
- **Launch assets:** screenshot set (dashboard home, chat with a tool-approval brief, loop cockpit mid-run, knowledge answer with citations, Store consent surface showing declared permissions) + a 60-90s silent capture (chat→approval→loop→knowledge→artifact/widget produced — the Canvas counter). Captured on a **seeded demo home** (`--seed` fixture; never the owner's real data). README rework: GIF above the fold, 3-command install, badges, highlights table, security section (plan 35 T2.4).
- **Comparison pages (S4):** `/compare/{peer-product}` pages — feature matrix + philosophy + honest "choose them if" columns. Claims about compared products carry retrieval dates; matrix rows only for verifiable public facts.
- **Listings (S4):** awesome-self-hosted + awesome-ai-agents PRs (follow each list's contribution rules), selfh.st, AlternativeTo; Show HN + r/selfhosted + r/LocalLLaMA — **gated on the P0 gate**: CI green, one-liner works, real screenshots live. Launch post: the architecture-receipts narrative citing the threat model.
- **Research republication (S5, owner #15):** curated learnings topics as a site section with a preface owning the built-agentically story.

## Contracts & artifacts (mostly doc/site artifacts; the two structured pieces pinned)

- **Docs-sync contract (`scripts/sync-docs.mjs`, site repo):** build-time only; checks out core `docs/{guides,reference,architecture,security}` → Starlight content. **The site repo commits NO copies of core docs** (drift rail — a link-check + a "no committed docs/ copies" CI assertion enforce it). One canonical source per the tenet.
- **`llms.txt` format:** the emerging convention — `# PersonalClaw` H1, one-paragraph what-it-is, then `## Docs` with `- [Title](url): one-line` bullets for the key pages; `llms-full.txt` = build-time concatenation of guides+reference. Both at domain root, `text/plain`.
- **Comparison data (`src/data/comparisons.json`):** `{product, claims:[{feature, personalclaw, them, source_url, retrieved:"<ISO>"}]}` — **every compared-product claim carries a source URL + retrieval date** (anti-staleness + honesty rail).
- **Integration points:** consumes DISTRIBUTION's `/install` script (T2.2), SECURITY-LEGIBILITY's threat-model, LEARNING-VISIBILITY's benchmark results, the research-learnings corpus (owner #15). Org/domain owner tasks gate S1.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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
| T4.1 | Comparison matrix data file (claims + sources + retrieved dates) then `/compare/{peer-product}` pages rendered from it | site repo: `src/data/comparisons.json` + pages | every compared-product claim has source+date; "choose them if" section present on both |
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

## Execution log

### 2026-08-16 — `DL-4` (T3.1) demo-home seed fixture — **PARTIAL / BLOCKED**

**Landed.** `src/personalclaw/tests_fixtures/demo-home/` now ships a home that reads as used:
two authored projects with briefs (`Reading Pipeline`, `Home Server`) plus the two builtins
(`Personal`, `Repeatable`) pinned at fixed ids, three task lists, ten tasks spanning
`open`/`in_progress`/`done`/`blocked`/`cancelled` with exit criteria, action plans and notes,
one dependency edge that resolves to a real derived block reason, and markdown memory
(`preferences.md`, `projects.md`, two days of `history/`). A minimal `config.json` sets
`dashboard.user_name` so the home boots past onboarding. New rails in
`tests/test_seed_demo_home.py` (11 tests). Docs updated: `docs/reference/cli.md` gains a
fixture table, and `CAPTURE.md` + `capture.mjs` no longer instruct a `--seed demo` fixture
that never existed.

**BLOCKED on the done_when's other half — knowledge docs and one loop.** The done_when also
requires knowledge docs, memory *records* and one loop. All three of those stores are
SQLite-only, and `--seed` is a bare `shutil.copytree` (`seed.py:269`) with no hydration hook,
so there is no text representation for a fixture to carry:

- **Knowledge** — `workspace/knowledge/knowledge.db` only (`knowledge/store.py:221`), 35-column
  `items` plus **external-content FTS5 with no triggers**, and the store refuses to open at all
  without FTS5 (`store.py:235`). `workspace/knowledge/files/` holds upload bytes reached through
  an **absolute** `file_path`, so a baked-in path from another home 404s. No boot-time re-ingest
  from any file. Extra hazard: a row left at `processing_status='queued'` is re-run through the
  full enrichment graph on every boot (`ingest_queue.recover_pending()`).
- **Memory records** — `semantic_memory` / `episodic_memories` in `memory.db`. `memory-vault/`
  is a projection that is **never read** by default (`vault_mode` defaults to `off`), and in
  `mirror` mode a hand edit is overwritten. The markdown tier we DID author is a genuine,
  first-class surface (it renders in the Memory studio and feeds the memory graph — measured,
  23 graph nodes), but it is explicitly "a view, not a parallel store" (`memory.py:50-57`).
- **Loops** — hybrid, and the SQLite half is the mandatory half: `loop/loops.db` holds the row,
  and `reap_orphan_dirs()` **deletes any `loop/<8hex>/` directory with no backing DB row at
  gateway boot** (`store.py:1215-1241` via `manager.py:604`). So a text-only loop fixture is
  silently wiped on first boot. Findings/verdicts are projected off `events.jsonl`, not off
  `findings/*.json` (which is ingest-only). Also: never seed `status='running'` or `'planning'`
  — boot re-arms them and spends real model calls.

The options, for the owner: **(a)** commit prebuilt `.db` files into package data (works today,
`copytree` handles it, but it is reviewable-as-binary, rots silently on any schema change, and
has no in-repo precedent); **(b)** give `seed` a post-copy hydration step that replays a
declarative JSON manifest through the real writers (keeps the fixture text-only and
schema-proof, but it is new machinery and a design decision beyond this atom); **(c)** leave the
fixture text-only and document the two-command manual top-up for capture, which is what
`CAPTURE.md` now says. `DL-4` stays `todo` in `dag.json`/`DL.md` because the done_when is not met.

**DISCOVERY (packaging, fixed here).** `pyproject.toml`'s package-data glob was
`tests_fixtures/*/*` — exactly one level deep. It covered `empty/fixture.yaml` and nothing else,
so every nested file this fixture needs would have been **silently absent from the wheel**: a
source checkout looks perfect and only a real `pip install` shows the fixture half-missing.
Widened to `tests_fixtures/**/*` and **proved against a built wheel**, not against the config:
a 4-level probe file appeared at `personalclaw/tests_fixtures/globprobe/a/b/c/deep.txt` inside
`python -m build --wheel` output. Backed by a coverage rail that walks the real tree and names
every uncovered file, plus a vacuity floor asserting the fixture is actually nested (a coverage
check over a flat tree would pass while the bug is wide open).

**Verified as a user** (isolated home, `--seed demo-home --seed-replace`, port 10055, real
browser): Home greets "Good afternoon, Alex" with 6 open tasks and a task counter of 6; the
Tasks list renders all ten with project labels, priorities, criteria counts (2/3, 1/2, 1/1, 2/2),
tags, strikethrough on done and the cancelled row distinct; Projects lists all four with
`builtin` badges and list counts; the Memory studio shows all three markdown documents and a
23-node graph carrying the authored prose. Zero console errors, zero gateway-log errors.
**Not verified because deliberately absent:** Knowledge (0 items), loops (0 — "No active work"),
and the Semantic/Episodic/Events/Embedded tiles all read 0.

**Known limitation.** The fixture's timestamps are static (mid-August 2026), so relative-time
labels drift as the fixture ages. Refresh them when the launch capture is taken.

### 2026-08-17 — `DL-4` (T3.1) demo-home seed fixture — **DONE**

Closes the half the 2026-08-16 entry left `BLOCKED`. The done_when is now met end to end:
`--seed demo-home` boots and every surface it names is **served**, measured on a live gateway.

**Correction to the premise this session started from.** `demo-home` was never missing a "seed
registry" — `_resolve_fixture` (`seed.py:124`) lists directories under `tests_fixtures/`, so
the fixture has been resolvable since it landed. `--seed demo-home` already booted; two
surfaces were simply empty. Measured baseline before this change, live boot: projects 4,
tasks 10, **knowledge 0, loops 0**.

**What changed.** The 2026-08-16 entry's option **(a)** — ship the SQLite halves as package
data — with the objection to it answered rather than accepted. Both `.db` files are generated
by driving the **real writers** (`KnowledgeStore.create_typed_item`, `loop.store.create`) from
`scripts/generate_demo_home_fixture.py`, so the schema is correct by construction rather than
hand-transcribed, and a schema change is a re-run rather than a hand-patch. Added:

- `workspace/knowledge/knowledge.db` — five docs (3 notes, 2 bookmarks) themed to the existing
  `Reading Pipeline` / `Home Server` projects. Text/url only, no `file_path`, so no absolute
  path from the generating machine is baked into the wheel. URLs use RFC 2606 `example.com`.
- `loop/loops.db` + `loop/a17c3f92/status.json` — one `research` loop, 3-phase plan with exit
  criteria per phase, scoped to `p-2d6f5c83` (`Reading Pipeline`), 6 cycles, `status=complete`.

**Three landmines the measured shape forced, each now a rail in `tests/test_seed_demo_home.py`
(19 tests, was 11):**

1. **WAL.** Both stores open `PRAGMA journal_mode=WAL`, so the writes sit in a `-wal` sidecar.
   Committing the bare `.db` without `wal_checkpoint(TRUNCATE)` ships a fixture that boots
   **empty** — the exact failure this atom exists to prevent. The generator checkpoints; a test
   asserts no sidecar ships and that neither `.db` embeds a generation-machine path.
2. **The orphan reap is real.** `reap_orphan_dirs()` deletes any `loop/<8hex>/` dir with no
   backing row at boot. A test calls it against the seeded home and asserts it reaps **0** —
   proving the shipped dir is row-backed rather than about to be wiped.
3. **`items_fts` is external-content FTS5 with no triggers**, so its index is only populated by
   the real writer. A hand-built `knowledge.db` would list fine and return nothing for every
   search — a demo whose search box looks broken. A test searches for a term the fixture
   contains, so that failure mode cannot ship silently.

Also: the seeded loop is pinned **terminal** (`complete`) by a test, because a seeded `running`
or `planning` loop is re-armed at boot and would spend real model calls on the machine of
whoever ran `--seed demo-home` just to look at a demo.

**Measured from a live boot** (`PERSONALCLAW_HOME` on a throwaway tmp home, port 10441,
`AUTH_MODE=none`): projects **4**, tasks **10**, knowledge **5** (`/api/knowledge/stats` agrees:
`items=5`), memory graph **23 nodes**, `preferences` **901 chars**, loops **1**.
`GET /api/loops/a17c3f92` parses fully (`kind=research`, 3 plan phases, `project_id` resolves to
a fixture project, 6 cycles) and the loop dir survived the boot reap. `test_every_demo_surface_is_non_empty`
carries a count floor per surface so an empty response cannot read as success.

**Packaging.** `pyproject.toml` needed no change — the glob is already `tests_fixtures/**/*`
(widened on 2026-08-16). Verified against a real wheel rather than assumed: **27 fixture files
on disk, 27 in the wheel, none missing**, including the new depth-4
`demo-home/loop/a17c3f92/status.json`.

**Falsified.** (a) Renaming the fixture dir → `SeedError: unknown fixture: 'demo-home'.
Available fixtures: demo-home-DISABLED, empty.` and the suite goes 2 failed / 15 errors, all
naming the fixture — not a silent pass. (b) `DELETE FROM items` in the committed
`knowledge.db` → 3 targeted reds, including
`these demo surfaces are empty: ['knowledge'] (counts={... 'knowledge': 0 ...})`. Both restored
from file copies.

**Docs corrected, because both stated the opposite of reality after this change:**
`docs/reference/cli.md`'s fixture table and `docs/screenshots/CAPTURE.md`'s prerequisite both
said knowledge items and loops are *not* carried and told the capture operator to drive those
two by hand. Semantic/episodic memory **records** remain genuinely absent — that store is
SQLite-only with no text tier — and both docs now say only that.

**Still open, unchanged:** the fixture's timestamps are static (mid-August 2026), so
relative-time labels drift as it ages. Refresh them when the launch capture is taken.

### 2026-08-17 — `DL-9` (T5.1, V5) research-learnings republication — **PARTIAL**

Landed in `personalclaw.dev` on `feature-dl9-learnings-republication` (not pushed). Nothing
in this repository changed except this log: the sync needed no front-matter, no marker file
and no edit of any learnings doc, so `docs/research/learnings/` is untouched.

**14 of 15, and which one is not a topic.** The directory holds fifteen `.md` files.
`README.md` is the corpus *index* — a topic table plus a cross-corpus findings summary — and
its own fourteen links are exactly the fourteen topics, which is what makes the count
unambiguous. The site withholds it and publishes its own section index instead, so there is
exactly one index rather than two that disagree on the first edit. It is not dropped: the
sync still **reads** it, because its "what it covers" column is curated one-line prose and
makes a far better page description than anything extractable from a topic that opens
straight into `## Principles` (two topics otherwise took a mid-document implementation
fragment as their meta description). README is linked from the preface at the pinned commit.

**Extended the existing sync; did not add a copier.** `scripts/sync-docs.mjs` already owned
the pinned-commit corpus, the link rewriter and both `llms.txt` writers, and its own header
comment named this work as the reason research was excluded. A tree entry may now declare
`sourceDir` (core path ≠ site path — the corpus is two levels down, published one level up),
`exclude`, `descriptionsFrom`, and `preface`. The link rewriter was generalised from "segment
count 1 or 2" to "resolve, then look up which tree owns that directory", which is what makes
a nested source dir work at all; it also now consults the *published* set, so a link into a
synced directory pointing at a file the site withholds escapes to GitHub instead of becoming
a plausible-looking in-site 404. **The four pre-existing trees' generated output is
byte-identical** — verified by running `origin/main`'s script and `diff -r`, not by assuming.

**Cross-link census.** 131 relative links across the fifteen files; 117 across the fourteen
published topics (README holds the other 14). Every one is a bare sibling filename — zero
contain a slash, zero carry a fragment, zero are reference-style, and zero point at a core
doc the site does not publish, so **no link needed rewriting-to-GitHub or dropping** and the
"what about links to unpublished core docs" decision was moot for this corpus rather than
answered. Post-build, `validate-build.mjs` resolves all 117 against `dist` and prints the
count it checked (`resolved 117 research cross-links across 14 republished topics`) — a
sweep that reports zero broken links out of an unstated denominator is not evidence.

**Falsified (five mutations, each restored from a file copy).** (a) `sourceDir` →
a non-existent dir: `No markdown found for docs/research/learnings-gone`. (b) rewriter
resolving the owner by site dir instead of source dir — the plausible refactor bug — trips
the sync's floor: `docs/research/learnings produced 0 in-site cross-links`. That red also
corrected the guard's own message, which had claimed only the 404 outcome when this mutation
produces the other one (all links escaping to GitHub). (c) one topic removed from
`known-docs.mjs`: four independent rails fired (route contract, missing page, cross-link
floor at 95 < 100, sitemap). (d) the in-site slug mangled in the rewriter *only*, leaving
page filenames correct: the sync reported success, the pages rendered normally, and the
post-build sweep caught all **117** links as broken — the exact "renders fine, 404s" failure
the atom's clause names. (e) the sweep's selector pointed at a class that does not exist:
`checked only 0 in-site links (floor 100)`, so the sweep cannot pass vacuously.

**UNMET — the atom's own gate.** "preface approved by owner" (owner task 7) cannot be
satisfied by an implementation session. `src/prose/research-preface.md` is drafted plainly
and factually and carries a stripped-before-publish comment naming the two owner calls:
the second paragraph's claim about how PersonalClaw is built, and whether the section should
be published at all before 1.0. Until that approval is recorded, `DL-9` is PARTIAL.

**Discovered, pre-existing, NOT fixed (out of this atom's fence).** Starlight's `editLink`
produces `github.com/PersonalClaw/PersonalClaw/edit/main/src/content/docs/docs/<tree>/<slug>.md`
on **every** docs page — a path that does not exist in core, because the baseUrl is core's
but the appended path is the generated collection's. All 33 docs pages carry a broken "Edit
page" link today; it surfaced because the new sweep flags `.md` hrefs.

**Gate (website, Node 22.12.0, `npm run test:prepush` → exit 0).** 30 visual baselines,
marketing manifest, release parity, build + `validate:build`, preview build +
`validate:preview`, `playwright test` 94 passed / 11 skipped (including new axe runs on
`/docs/research` and `/docs/research/verification-and-judging` across desktop, mobile and
reduced-motion, WCAG 2.0 A/AA + 2.1 AA + 2.2 AA), Lighthouse 100/100/100/100 on all five
marketing routes. One red on the first run — `filtered app directory` visual baseline,
`.app-directory` never reaching stability inside 7500ms — passed 3/3 when re-run serially
and touches no file in this change; it is parallel-CPU contention, and it was re-run, not
weakened. `npm audit` was **not required** (no `package.json`/`package-lock.json` change);
run anyway it reports 5 pre-existing findings on `origin/main` — high `fast-uri`, `js-yaml`,
`nanoid`, `undici`, moderate `postcss`. `search_arcc` was unavailable.

**V5 holds.** Three topics spot-checked in `dist` (`memory-architectures`,
`skills-and-prompt-craft`, `workflow-engine-design`): each renders its H1, carries in-site
cross-links, and contains zero raw `.md` hrefs. The generated section index links all 14.

### 2026-08-24 — `DL-7` (S4 T4.1) comparison data + `/compare` pages — **BLOCKED**

Two independent blockers, plus a name-scrub defect in the atom's own `done_when`. Nothing
was built toward the atom. One core-owned defect found while sourcing it WAS fixed, and is
the only code/doc change in this commit's sibling.

**Not one line of this atom is core's to build.** The plan says so twice, unprompted:
§Design — "landing page + comparison pages authored **in the site repo** (they're
marketing, not product docs)"; and the T4.1 row's deliverable column — "**site repo:**
`src/data/comparisons.json` + pages". The `done_when`'s paths corroborate it: core's `src/`
is `src/personalclaw/` and holds no `data/` directory, whereas `personalclaw.dev` has
`src/data/` (three modules today) and `src/pages/`. Compare `DL-4`, the one atom in this
plan whose title carries an explicit "(core)" marker — the plan annotates its core work,
and `DL-7` is not annotated. **The cross-repo split here is total, not partial:** core owns
no deliverable, so a core session cannot make `DL-7` anything but `todo`.

**BLOCKER 1 — its declared dependency is unbuilt, two atoms deep.** The row declares
`EXT:LEARNING-VISIBILITY:benchmark results for matrix rows`, which `dag.json`'s
`resolved_edges` resolves to `LV-7` — status `todo`. And `LV-7` is not merely unstarted: the
LEARNING-VISIBILITY execution log's own DISCOVERY records that `LV-7`'s declared deps
understate it, because the skills-off arm it needs is `ES-7` §3.3's `arm_mask`, which is
"designed, unbuilt" — `ES-7` is `todo` as well. So the benchmark numbers this atom was
meant to put in its matrix rows are two unbuilt atoms away. (`LV-7`'s own declared
`EXT:EVALUATION-SUBSTRATE:S1-2` ref resolves to `ES-5`, also `todo`, so the chain is unmet
by either reading.) Per this plan set's own rule ("If an atom lists dependencies, they must
be `done` before it starts"), `DL-7` is not startable.

**DISCOVERY — `ready_frontier` ignores `EXT:` edges, and this is systematic.** `DL-7` is
listed in `dag.json`'s `dag.ready_frontier` despite the above. Walking all 76 frontier
entries against `plans[].atoms[].status` — following `dag.resolved_edges` for `EXT:` refs —
**11 entries carry a dependency that is not `done`, and all 11 of those are `EXT:` edges**
(`CA-9`, `CE-9`, `DCU-3`, `DL-7`, `EI-2`, `ET-7`, `LV-7`, `PEP-3`, `PEP-11`, `PUBL-10`,
`WF2UNI-12`; two of them point at atoms whose status is literally `blocked`). Zero
intra-plan `deps` violations, so the frontier computation is honouring `deps` and skipping
resolved `EXT:` edges entirely. **The consequence is that "in `ready_frontier`" is not
evidence an atom is startable** — a session briefed off the frontier will pick up a
dependency-blocked atom roughly one time in seven. Not fixed here (the generator is outside
this atom's fence and `dag.json` is owner-generated), but it is the reason this session was
briefed to build a blocked atom.

**BLOCKER 2 — OWNER CALL: which peers, and whether to ship comparison pages at all.** The
`done_when` names exactly one peer product and otherwise leaves the literal placeholder
`{peer-product}` unfilled, so the plan never decided the peer set. Naming competitors is
positioning, not implementation, and an implementation session must not settle it. The
second half of the same call: whether a pre-1.0 project publishes comparison pages at all.

**DISCOVERY — the `done_when` is itself a name-scrub violation (out of fence, NOT fixed).**
The single peer it names is on the owner's own keep-out list for these public repos
(research-source product names; ruling of 2026-08-05), so satisfying the `done_when`
literally would publish that name on a public route — the atom as written cannot be
executed without violating a standing ruling. The name is live in three tracked files
today: the `DL-7` row and the `DL-7` scope block in `docs/roadmap/atomic/DL.md`, and the
matching `done_when` string in `docs/roadmap/atomic/dag.json`. **Not fixed here** because
`dag.json` is the generated truth for both and rewording an atom's `done_when` is an owner
edit, not an implementer's. Deliberately not named in this entry either — writing it here
to explain the problem would extend the leak. A NAMES-ONLY sweep of the tracked tree found
**24 matching lines across 12 files**; the other 21 are design-rationale prose in nine other
plans plus one module docstring, all pre-existing, all out of this atom's fence.

**FIXED (core-owned, this commit's sibling) — the egress census promised an opt-out that
does not exist.** Sourcing the PersonalClaw column from code rather than from docs turned
up a false claim in `docs/architecture/network-egress-hosts.txt`: its `api.github.com`
judgment said the release check "is the reason `updates.check_enabled` exists".
`git grep check_enabled -- src/` returns nothing. The real field is `auto_update`, and its
own `_meta` description states the opposite of the doc — "update checks always run; this
gates the unattended pull + rebuild + restart". The call site confirms it:
`gateway.py::GatewayOrchestrator._check_for_updates` awaits `_do_update_check()` and only
then reads `cfg.auto_update`. **There is no configuration that suppresses the request.**
The pinning test's docstring repeated the same false sentence. Both corrected, plus two
call-site tests (`test_self_update.py::test_auto_update_gates_the_apply_not_the_check` and
`::test_auto_update_on_reaches_the_apply`) that pin the ordering in both branches.
Note what caught this and what could not: the docs-lint census's stale-citation kind only
flags `file.py:NNN` citations whose FILE is missing, so a doc naming a config field that
was never built is invisible to it.

**The PersonalClaw column, sourced from code, so the website session does not re-derive it.**
Every row below is verified against this tree, not against intent:

- **License MIT** — `LICENSE` plus `pyproject.toml` `license = { text = "MIT" }`.
- **Version 0.1.3, pre-1.0**; `requires-python = ">=3.12"` (`pyproject.toml`).
- **19 app extension points** — `apps/manifest.py::PROVIDER_TYPES` (`action`, `agent`,
  `channel`, `duty_gate`, `inbox`, `knowledge`, `memory`, `model`, `notification`, `prompt`,
  `sandbox`, `search`, `skills`, `sync`, `task`, `tool`, `trigger`, `trigger_source`,
  `workflow`). Counted from the frozenset, not from a doc.
- **Egress is a machine-checked census, not a promise** — `network-egress-hosts.txt` plus
  `tests/test_network_egress_hosts.py`, which reds on a new routable host literal in
  `src/personalclaw/**/*.py` or `web/src/**` and on a stale entry. This is the strongest
  privacy row available and it is a *control*, which is the part worth saying.
- **But the honest privacy cell is narrower than "no phone-home."** One destination is
  contacted without the user asking for it — `api.github.com`, at every gateway start and
  at most once per 12h behind `_UPDATE_CHECK_INTERVAL` — carrying a product-identifying
  `User-Agent` of `personalclaw-update-check` and the instance's IP, **with no off switch**
  (above). A cell reading "zero telemetry, and you can disable even the update check" is a
  factual defect. A cell reading "no analytics, no crash reporting, no usage telemetry; one
  unprompted release check to GitHub" is true today.
- **Three shipped non-enforcements constrain any isolation row** —
  `docs/security/limitations.md`: ACP agents under auto-approve rely on system-prompt
  framing rather than rails; the app `network` permission is **declaration-only** (no
  per-app egress isolation); app Python dependencies install into the venv the gateway runs
  from. Any "sandboxed apps" or "per-app network policy" cell would contradict core's own
  limitations page.

**Zero claims about any peer product were sourced — and that is not a shortfall.** No peer
has been chosen (BLOCKER 2), so there was nothing to source; no peer research was attempted
and no cell was guessed. When the owner names the peer set, every cell needs a `source_url`
plus `retrieved` date per §Comparison data, and any cell that cannot be sourced from the
peer's own public docs stays empty or reads "unverified" rather than being inferred.

**What the site session must do, and the shape it must fit.** `personalclaw.dev` has no
`/compare` route and no `src/data/comparisons.json`. Adding the route means: the data module
(note the existing `src/data/` convention is `.ts`, not the `.json` the `done_when` says —
a small spec drift for the owner to settle); one page per peer with a "choose them if"
section; a route entry in `tests/support/site-contract.mjs`, which today declares exactly
five marketing routes (`/`, `/product`, `/apps`, `/security`, `/release`) with name, path,
title and description; then new visual baselines, axe runs and a Lighthouse pass for each
new route, since `npm run test:ci` fans those out per declared route. Gate is
`npm run test:prepush` under Node 22.12.0 (it re-runs `npm ci` then the full `test:ci`
aggregate), with visual snapshots platform-qualified Darwin/Linux, plus
`npm audit --audit-level=moderate` if the lockfile moves. That is a properly-gated website
change, not a drive-by, and it stays blocked behind both blockers above regardless.

**Core gate — clean.** No core route was added, so the offline API reference and its
`known-docs`-style contracts are untouched; `web/` unchanged, so no frontend gate applies.
