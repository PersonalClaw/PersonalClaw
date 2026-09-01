# Design-System Consistency Audit — S1 Drift Map

**Plan:** [DESIGN-SYSTEM-CONSISTENCY](../roadmap/plans/DESIGN-SYSTEM-CONSISTENCY.md) · **Session:** S1 (audit — measure only, no fixes)
**Generated from:** `web/src/design/consistencyAudit.report.ts` — run `npm run audit:consistency` from the repo root to rewrite `docs/design/consistency-audit.json`. A plain `npm test` measures but writes nothing (issue 261), so regenerating is now a deliberate step.
**Status:** LIVING — regenerate the JSON, then refresh the tables below, each cycle the scanner changes.

> This document is the *map*, not the fix. It quantifies where the shipped design system (`web/DESIGN.md` + `web/PRODUCT.md` are authority) drifts across `web/src`, ranked worst-first, so S2/S3 attack the highest-value targets first. **No code was changed to produce it.**

---

## Headline finding

The token layer is **already mature and near-clean** — the shipped `tokenLint` ratchet has driven hardcoded color/spacing/radius/shadow/duration values down to a **handful of legitimate exceptions**. The dominant, measurable drift is **primitive adoption**: pages built over time render **bespoke chrome** (raw `<button>`, raw `<input>/<textarea>/<select>`) instead of the 33 shell primitives in `web/src/ui/`. This is exactly the drift the plan predicted, concentrated in the two mega-pages.

| Metric | Count |
|---|---|
| Source files scanned (`web/src`, excl. `design/`) | 298 |
| Shell primitives available (`web/src/ui/*.tsx`) | 33 |
| **Raw-value drift hits** (color/spacing/radius/shadow/duration) | **7** |
| Files with any raw-value drift | 6 |
| **Raw `<button>` occurrences outside `ui/`** | **420** |
| **Raw `<input>/<textarea>/<select>` outside `ui/`** | **206** |
| Ad-hoc `role="dialog"` / `<dialog>` (bespoke modals) | 0 |
| Files carrying bespoke chrome | 112 |
| Global reduced-motion + focus-visible safety nets (`tokens.css`) | both present ✓ |
| `outline-none` relying on the global focus ring (70 files) | 171 |

**Interpretation:** the S2 "hardcoded → token" work is nearly *done already* (7 hits, most legitimate — see below). The real S2/S3 payload is **primitive consolidation** — replacing bespoke `<button>`/form markup with `Button`/`IconButton` and (a genuine system gap) a shared form-field primitive. The absence of any bespoke `<dialog>`/`role="dialog"` outside `ui/` confirms `Modal` is already the single canonical dialog — a consistency win to preserve, not fix.

---

## Ranked worst offenders (weighted score)

Weights: color drift ×5 (bypasses theming), shadow ×3, other raw-value ×2; raw-dialog ×4, raw-button ×2, raw-input ×1 (bespoke chrome).

| # | Score | File | Drift detail |
|---|---|---|---|
| 1 | 107 | `web/src/pages/code/CodeCockpitPage.tsx` | 51 raw-button, 5 raw-input |
| 2 | 79 | `web/src/pages/ChatPage.tsx` | 34 raw-button, 11 raw-input |
| 3 | 45 | `web/src/pages/projects/ProjectsSection.tsx` | 20 raw-button, 5 raw-input |
| 4 | 35 | `web/src/pages/loops/LoopPlanReview.tsx` | 13 raw-button, 9 raw-input |
| 5 | 33 | `web/src/pages/code/CodePlanReview.tsx` | 12 raw-button, 9 raw-input |
| 6 | 27 | `web/src/pages/settings/MemoryPanel.tsx` | 9 raw-button, 9 raw-input |
| 7 | 24 | `web/src/pages/loops/LoopCockpitPage.tsx` | 11 raw-button, 2 raw-input |
| 8 | 24 | `web/src/pages/tools/ToolsPage.tsx` | 8 raw-button, 8 raw-input |
| 9 | 23 | `web/src/pages/workflows/WorkflowForm.tsx` | 10 raw-button, 3 raw-input |
| 10 | 22 | `web/src/pages/tasks/formControls.tsx` | 7 raw-button, 8 raw-input |
| 11 | 19 | `web/src/pages/prompts/PromptEditFields.tsx` | 5 raw-button, 9 raw-input |
| 12 | 17 | `web/src/pages/knowledge/KnowledgeDetail.tsx` | 6 raw-button, 5 raw-input |
| 13 | 17 | `web/src/pages/knowledge/KnowledgeListPage.tsx` | 7 raw-button, 3 raw-input |
| 14 | 17 | `web/src/pages/settings/OllamaModelManager.tsx` | 8 raw-button, 1 raw-input |
| 15 | 17 | `web/src/pages/settings/VoicePanel.tsx` | 7 raw-button, 3 raw-input |

*(Full ranked list of 40 in `consistency-audit.json → ranked`.)*

The two mega-pages (`CodeCockpitPage.tsx`, `ChatPage.tsx`, ~3385 LOC each) top the list — as the plan flagged, they are the **highest-risk** fixes and must be done in small, screenshot-verified increments.

---

## Raw-value drift inventory (all 7 hits, category-by-category)

Every hit was spot-checked against the code. Most are **legitimate exceptions**, which is why the token layer is considered near-clean:

| Category | File:line | Snippet | Verdict |
|---|---|---|---|
| duration | `pages/knowledge/KnowledgeGraph.tsx:101` | `transition: 'transform 200ms ease-out'` (drag physics) | **Candidate** — could use a motion token (`--motion-*`); low priority (SVG drag), verify no reduced-motion issue |
| duration | `pages/settings/MemoryGraph.tsx:147` | `transition: 'transform 200ms ease-out'` (drag physics) | **Candidate** — same as above |
| shadow | `pages/notifications/NotificationsPage.tsx:168` | `boxShadow: inset 2px 0 0 0 ${km.tone}` (unread rail) | **Legitimate-ish** — hairline inset accent using a token-derived `km.tone`; the `2px` is a hairline width. Consider a shared `--shadow-inset-accent` if the pattern repeats (it does — see NotificationBell) |
| shadow | `ui/NotificationBell.tsx:127` | `boxShadow: inset 2px 0 0 0 ${km.tone}` (unread rail) | **Duplicate of above** — same inset-accent pattern in a primitive-adjacent file → candidate to consolidate into one helper |
| color | `pages/settings/MemoryGraph.tsx:101` | `` return `hsl(${h} 55% 60%)` `` (generated node palette) | **Legitimate** — programmatic HSL for a data-viz graph palette (hue computed per node); not app chrome. Keep, allowlist-with-reason |
| radius | `ui/composer/liveMarkdown.ts:121` | `fontSize: '0.9em'` (CodeMirror inline code) | **Legitimate** — relative `em` inside a CodeMirror theme object (editor internals, cannot consume CSS vars cleanly); miscategorized by the px regex on the same line's code styling. Keep |
| spacing | `ui/widget/WidgetFrame.tsx:204` | `height: 'calc(100% - 36px)'` (expanded widget) | **Candidate** — the `36px` is a header-height magic number; should reference a layout token. This is the sole file currently on the `tokenLint.allowlist.json` |

**S2 raw-value target list (only 3 real fixes):**
1. `WidgetFrame.tsx` `calc(100% - 36px)` → layout token (clears the last allowlist entry). **DEFERRED** — the `36px` is the measured height of a sibling toolbar (layout coupling, not a spacing-scale value); swapping it for a scale token would be semantically wrong and shift layout. Needs the visual harness (blocked on auth) to verify any change; not a blind fix.
2. The two `transform 200ms` durations → `--motion-*` token (verify reduced-motion honored). **PENDING** — global reduced-motion already neutralizes them; low priority.
3. ~~The duplicated `inset 2px 0 0 0` unread-rail shadow → one shared helper.~~ **DONE (cycle 4)** — `unreadRail()` + `toneChipBg()` in `notificationMeta.ts` now own the unread-rail + icon-chip-tint patterns; `NotificationsPage.tsx` (3 sites) and `NotificationBell.tsx` (2 sites) call them. Byte-identical output (zero visual change), guarded by `notificationMeta.test.ts`. Shadow drift 2 → 1 (now the single canonical helper).

The MemoryGraph HSL and CodeMirror `0.9em` are legitimate; they should be added to `tokenLint`'s exempt list **with a `reason`** (C1 requires reasons) rather than "fixed."

---

## Primitive-adoption drift (the real S2/S3 payload)

**420 raw `<button>` + 206 raw form elements across 112 files.** The system already has `Button`, `IconButton`, `Toggle`, `Segmented`, `Combobox` — but there is **no shared text-input / textarea / select field primitive**, which is why every form re-rolls raw `<input>`. This is the one **genuine system gap** the plan allows filling *once* (T2.3): a `Field`/`TextInput` primitive + `patterns.md` entry.

**Consolidation strategy (S2):**
- **Buttons** → replace raw `<button>` with `Button` (variant/size props) or `IconButton`. Start at the mega-pages (rank 1–2) in screenshot-verified increments.
- **Inputs** → introduce ONE `TextField`/`Field` primitive (T2.3, genuine gap), then migrate. Do not migrate raw inputs before the primitive exists. **DONE (cycle 5):** `web/src/ui/TextField.tsx` (`TextField` + `TextArea`, size/surface/mono/leadingIcon variants) is the canonical extraction of the existing input shape — additive, zero existing-pixel change; documented in `docs/design/patterns.md`; class contract pinned by `TextField.test.tsx`. The 206-input migration is now unblocked (but each migration that changes pixels is harness-gated).
- **Dialogs** → already consolidated on `Modal` (0 bespoke) — protect via the primitive-adoption lint (C1/T3.4) so it can't regress.

### Ratchet — DONE (cycle 6, C1/T3.4 third rail)
`web/src/design/primitiveAdoption.test.ts` + `primitiveAdoption.baseline.json` now enforce that bespoke-chrome counts (raw `<button>` 420, raw form elements 206, ad-hoc dialogs 0) may only **shrink** — a NEW raw element turns the CI `web` job (vitest) red. Verified it fails on a synthetic +1 and passes at baseline. This is live in CI **without** the browser harness (it's a static vitest scan). As S2 migrations land, ratchet the baseline down in the same commit. Combined with the existing `tokenLint.test.ts` (token-lint-strict), two of C1's three rails are CI-enforced now; the axe rail mounts once the harness auth-seed lands.

---

## a11y / parity scan (S1 — static coverage measured; axe-per-route pending harness)

The reporter's `scanA11y()` measures the **static** a11y posture (the axe-per-route dynamic scan needs the Playwright harness — see blocker below). The headline: **the app is a11y-safe by GLOBAL default, not per-component**, which is itself a strong consistency win.

| Signal | Value | Verdict |
|---|---|---|
| Global reduced-motion rule (`tokens.css` `*` rule, line ~375) | **present** ✓ | Every CSS transition/animation is neutralized under `prefers-reduced-motion: reduce` app-wide |
| Framer `<MotionConfig reducedMotion="user">` (`App.tsx`) | present ✓ | JS-driven (Framer) motion also honors the OS setting |
| Global `:focus-visible` ring (`tokens.css` line ~222) | **present** ✓ | Keyboard focus is visible app-wide even where controls null their outline |
| Files with an explicit local `prefers-reduced-motion` | 5 | Deliberate per-surface overrides (DiffReveal, TypingReveal, DotGlow, useStreamCoalescer, App) — the global rule covers the rest |
| Files using `animate-`/`transition-`/`animation:` | 144 | All covered by the global reduced-motion rule; no per-file work needed |
| `outline-none` occurrences (70 files, 171 total) | 171 | **Not violations** — they rely on the global `:focus-visible` ring by design. Blast radius IF the global ring were ever removed = 70 files |
| Files with a LOCAL `focus-visible` override | 7 | Custom rings where the default doesn't fit (CodeCockpit, Combobox, ProjectsSection, FileTree, WorkspacePicker, PlanningWalkthrough, CodeSection) |

**a11y consistency verdict:** reduced-motion and focus-visible coverage are **structurally complete via global rules** — the correct, DRY posture. The S3 ratchet must therefore **protect the two global rules** (a test asserting they exist — already wired into `consistencyAudit.test.ts`) and add the **dynamic axe-per-route** scan (needs the harness). The 171 `outline-none` are safe but should NOT be individually "fixed" — that would be churn against a working global net.

### Dynamic axe WCAG AA scan + visual baselines — harness BUILT, baseline capture BLOCKED on auth
The Playwright harness (`web/e2e/`, `web/playwright.config.ts`) is built and parses cleanly (**64 tests**: 16 routes × 2 themes × visual+axe). Chromium is installed and the specs run. **BUT** capturing *valid* baselines is **blocked**: the built SPA gates its first render on an authenticated identity/config fetch (`src/app/identity.tsx` — `loaded` gates render; the gateway needs the owner `pc_token_<port>` cookie). Driven by a fresh, unauthenticated Playwright context (both against `vite preview` and against the live gateway at `:10000`), the app **renders a blank body** — so a naive capture produced 32 *identical blank* PNGs, which is a **false safety rail** and was therefore **discarded** (not committed).

- **What IS proven:** harness infra correct — config, route SoT, theme seeding, axe wiring, npm scripts, README all in place; `playwright test --list` = 64 tests; `npx playwright install chromium` succeeds; the preview server builds + serves the real HTML/JS.
- **What is BLOCKED:** meaningful baselines + a real axe reading need an **authenticated session**. This can't be done autonomously in the sandbox (no owner token).
- **Recommended default (owner / CI, one-time):** seed an auth cookie, then `npm run e2e:update` to capture real baselines. Two supported paths:
  1. **Owner-local:** with the gateway running + logged in, run `PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 STORAGE_STATE=<exported cookies> npm run e2e:update`.
  2. **CI:** mint a test-owner token in the CI gateway boot and inject it via `page.context().addCookies(...)` in a global-setup (stub: `e2e/auth.setup.ts` — add when the CI token minting exists, plan 33 rails).
- Until then, the harness is **wired but dormant**; S2 fixes that need visual verification must be checked by the owner running `npm run e2e` locally, OR the auth-seed default above must land first.

### Still pending
- [ ] **Auth-seeding for the harness** (the blocker above) — the true next step before S2 visual verification is trustworthy.
- [ ] Keyboard-nav walkthrough per route — S3 (T3.2).
- [ ] Responsive / phone-viewport pass — S3 (T3.3); add a mobile project to `playwright.config.ts`.
- [ ] `-linux` CI baselines — regenerated on CI's first authenticated run.

---

## Spot-check (V1 evidence — 5 findings verified against code)

1. `CodeCockpitPage.tsx` — confirmed 51 `<button` occurrences via `grep -c '<button' ` ✓ (rank #1).
2. `WidgetFrame.tsx:204` — confirmed `calc(100% - 36px)` present and it is the sole `tokenLint.allowlist.json` entry ✓.
3. `MemoryGraph.tsx:101` — confirmed programmatic `hsl(${h} 55% 60%)` node palette ✓ (legitimate).
4. Bespoke dialogs — confirmed `grep -rn 'role="dialog"' src/pages` returns 0; `Modal` is canonical ✓.
5. `KnowledgeGraph.tsx:101` / `MemoryGraph.tsx:147` — confirmed identical `transform 200ms ease-out` drag transitions (duplicated pattern) ✓.

---

## S2 priority order (worst-first, from measured ranking)

1. **Stand up the Playwright harness** (brief-mandated S2 prerequisite) — capture baselines of every touched surface first.
2. **`CodeCockpitPage.tsx`** (rank 1) — 51 raw-button → `Button`/`IconButton`, incrementally, screenshot-verified.
3. **`ChatPage.tsx`** (rank 2) — 34 raw-button, incrementally.
4. **Introduce the `TextField`/`Field` primitive** (T2.3) — the one genuine gap — then migrate rank 3+ pages' 206 raw inputs.
5. **Clear the 3 real raw-value fixes** (WidgetFrame calc, drag durations, inset-shadow helper) and add `reason` fields for the 2 legitimate exceptions → shrink `tokenLint.allowlist.json` to empty.
6. Continue down the ranked list (ProjectsSection, LoopPlanReview, CodePlanReview, MemoryPanel, …).

> **Owner review point (plan Owner-task 1):** this ranking weights by measured drift, not usage. If you weight the surfaces you use most differently, note it — otherwise S2 proceeds worst-first as above.
