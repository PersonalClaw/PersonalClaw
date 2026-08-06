# LEARNING-VISIBILITY — atomic plans

**Source plan:** [`LEARNING-VISIBILITY`](../plans/LEARNING-VISIBILITY.md)  
**Code:** `LV`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `LV-1` | ⬜ | S1 end-to-end visible slice: fire ladder review at loop end-of-run + confirm accept->surface->use loop | `EXT:LEARNING-FLYWHEEL:skill-ladder synthesis machinery (run_skill_ladder_review + proposals.enqueue) steps 1-4`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:proposals surface as inbox proposal kind (S4)` | Execution log carries the caller map; a completed multi-step fixture loop enqueues <=1 proposal and an environment-failure fixture enqueues 0; integration test propose->accept->matching-prompt->usage-count-increments passes; V1 full loop (fixture home: run->proposal->approve->repeat->skill loads and usage records) observed with no auto-write anywhere |
| `LV-2` | ⬜ | S2 legibility: per-run 'used N skills you approved' chip + session learned-chips with tap-through | `LV-1` | run/loop panel shows 'used N skills' chip with names on hover and zero new WS/SSE channels; a correction in chat yields a visible learned-chip within the session whose tap lands on the right approve/edit surface |
| `LV-3` | ⬜ | S2 digest section: learning summary block (new/refined/pending counts + names) | `LV-1`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:digest builder registration point (plan 42 S5; has skills-page-header fallback)` | weekly digest (or fallback skills-page header) shows the learning block with real counts and names |
| `LV-4` | ⬜ | Periodic identity report: compose_identity_report + delivery/schedule/config/FE (amendment) | `LV-3`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:emit_attention_item kind=report (pre-42 source=learning InboxItem fallback)` | fixture home with seeded lessons/facets/skills yields a truthful report whose counts byte-match store contents with zero writes to any learning store (inspected before/after); no-model fixture still produces the deterministic sections; compressed-clock fixture fires the job, item lands in inbox linking the artifact, quiet-hours suppresses the ping but not the artifact, config round-trips and 'off' disables cleanly |
| `LV-5` | ⬜ | S3 refinement arm: stumble detector -> refine proposal (unified diff) -> diff render + versioned accept | `LV-1`, `EXT:LEARNING-FLYWHEEL:refiner statistical gates slot behind same queue/kind (coordination, non-blocking)` | unit tests per stumble trigger pass and the env-failure fixture never triggers; a stumble fixture yields exactly one refine proposal with a valid diff; approving applies the diff and writes provenance frontmatter while reject leaves the skill untouched; V3 arc (flawed skill->stumble->refine->approve->re-run succeeds) observed |
| `LV-6` | ⬜ | S4 benchmark protocol doc | — | protocol doc is reviewable before any runs and has owner sign-off (owner task 2); owner-curated ~10-task set frozen (owner task 1) |
| `LV-7` | ⬜ | S4 benchmark implementation as an eval-substrate study + publish | `LV-6`, `EXT:EVALUATION-SUBSTRATE:S1-2 template-study machinery`, `EXT:DISCOVERABILITY-LAUNCH:site content publish path (plan 36 sync)` | paired runs are reproducible from one command against fixture homes; results page is live with a methodology link; an independent re-run reproduces within stated variance (V4) |

## Atom scopes

### `LV-1` — S1 end-to-end visible slice: fire ladder review at loop end-of-run + confirm accept->surface->use loop

**Status:** todo

Session 1 (T1.1 recon map, T1.2 loop-complete-seam firing, T1.3 accept->surface->record_uses wiring, V1)

**Done when:** Execution log carries the caller map; a completed multi-step fixture loop enqueues <=1 proposal and an environment-failure fixture enqueues 0; integration test propose->accept->matching-prompt->usage-count-increments passes; V1 full loop (fixture home: run->proposal->approve->repeat->skill loads and usage records) observed with no auto-write anywhere

### `LV-2` — S2 legibility: per-run 'used N skills you approved' chip + session learned-chips with tap-through

**Status:** todo

Session 2 T2.1 (plumb loaded_skills+usage as additive meta on existing turn/loop events) and T2.2 (after-turn capture chips: facets/lessons/proposals)

**Done when:** run/loop panel shows 'used N skills' chip with names on hover and zero new WS/SSE channels; a correction in chat yields a visible learned-chip within the session whose tap lands on the right approve/edit surface

### `LV-3` — S2 digest section: learning summary block (new/refined/pending counts + names)

**Status:** todo

Session 2 T2.3 (register block with plan-42 digest builder; fallback to skills-page header + DISCOVERY if 42 S5 not landed)

**Done when:** weekly digest (or fallback skills-page header) shows the learning block with real counts and names

### `LV-4` — Periodic identity report: compose_identity_report + delivery/schedule/config/FE (amendment)

**Status:** todo

Amendment T2.4 (learning_report.py deterministic gather over LessonStore/facets/SkillUsageStore/curator-state/proposals/memory_stats + one fenced background narrative pass + honesty-ratchet lint map entry) and T2.5 (system clock job -> artifact + notify-gated inbox item; learning.identity_report_* config 4-point wired; FE renders from inbox/artifact, no modal)

**Done when:** fixture home with seeded lessons/facets/skills yields a truthful report whose counts byte-match store contents with zero writes to any learning store (inspected before/after); no-model fixture still produces the deterministic sections; compressed-clock fixture fires the job, item lands in inbox linking the artifact, quiet-hours suppresses the ping but not the artifact, config round-trips and 'off' disables cleanly

### `LV-5` — S3 refinement arm: stumble detector -> refine proposal (unified diff) -> diff render + versioned accept

**Status:** todo

Session 3 (T3.1 stumble detector at after-turn seam when skills loaded: correction/failure-retry/rejection, env-failures excluded; T3.2 build unified diff vs current SKILL.md, enqueue kind=refine capped 1/skill/day; T3.3 diff rendering + accept applies with provenance frontmatter; V3)

**Done when:** unit tests per stumble trigger pass and the env-failure fixture never triggers; a stumble fixture yields exactly one refine proposal with a valid diff; approving applies the diff and writes provenance frontmatter while reject leaves the skill untouched; V3 arc (flawed skill->stumble->refine->approve->re-run succeeds) observed

### `LV-6` — S4 benchmark protocol doc

**Status:** todo

Session 4 T4.1 (docs/roadmap/research/learning-benchmark-protocol.md: task-set schema, paired skills-on/off design with fresh homes + fixed model/config/seed, metrics {completion, tool_calls, wall_ms}, exclusions, publish-regardless honesty rule)

**Done when:** protocol doc is reviewable before any runs and has owner sign-off (owner task 2); owner-curated ~10-task set frozen (owner task 1)

### `LV-7` — S4 benchmark implementation as an eval-substrate study + publish

**Status:** todo

Session 4 T4.2 (implement paired study on EVALUATION-SUBSTRATE S1-2 machinery + scripts/ runner producing results table + raw logs) and T4.3 (publish results page to site + honest README one-liner), V4 reproduction

**Done when:** paired runs are reproducible from one command against fixture homes; results page is live with a methodology link; an independent re-run reproduces within stated variance (V4)

