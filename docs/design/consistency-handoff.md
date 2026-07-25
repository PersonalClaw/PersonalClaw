# Design-System Consistency — Owner / CI Handoff

**Plan:** [DESIGN-SYSTEM-CONSISTENCY](../roadmap/plans/DESIGN-SYSTEM-CONSISTENCY.md)
**Status at handoff:** all *autonomously-safe* S1–S3 work is done + green on `main`. The remaining work is **pixel-affecting** and gated on ONE thing the loop couldn't do unattended: **seeding the visual-regression harness with an authenticated session.** This doc is the minutes-long unblock.

---

## What's done (green on `main`, no owner action needed)

- **S1 audit** — `docs/design/consistency-audit.md` (ranked drift map) + a live reporter (`web/src/design/consistencyAudit.report.ts`, run via `npm test` → `consistency-audit.json`). Key finding: token layer near-clean; the real drift is **primitive adoption** (420 raw `<button>`, 206 raw form elements) concentrated in the two mega-pages. a11y is **globally clean** (global reduced-motion + `:focus-visible` rules).
- **Harness (infra)** — `web/e2e/` + `web/playwright.config.ts` (`toHaveScreenshot`, platform-qualified baselines, axe wcag2aa). Parses to 64 tests; chromium installs; deps locked in `package-lock.json`.
- **Primitive + patterns** — `web/src/ui/TextField.tsx` (`TextField`/`TextArea`, the audit's one genuine gap) + the full `docs/design/patterns.md` gallery (TextField, Button, Modal, the two empty-states, confirm/prompt/alert, skeleton family, ListRow, fvs()/.fw-*).
- **CI ratchets (3 live in the `web` vitest job)** — token-lint-strict (`tokenLint.test.ts`), primitive-adoption + inline-font-weight (`primitiveAdoption.test.ts`). New drift turns CI red.
- **Zero-pixel conformance** — notification unread-rail/tone-chip consolidation; ~22 inline font-weights migrated to `fvs()`; empty-state name disambiguation.

## The ONE blocker → the unblock (≈5 min)

The built SPA gates its first render on an authenticated identity/config fetch (`web/src/app/identity.tsx`), so a fresh Playwright context renders a **blank** app — invalid baselines. Seed a session once:

```bash
cd web
# 1. with the gateway running + an owner token available:
PW_TOKEN=<owner token> npx playwright test e2e/auth.setup.ts     # writes e2e/.auth/state.json
# 2. capture REAL baselines against the authenticated app:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e:update
# 3. thereafter, verify zero-diff / AA-clean:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e
```

Commit the captured `e2e/__screenshots__/*-<platform>.png` baselines. (`e2e/.auth/` is gitignored — it holds the token.)

**CI (plan 33 rails):** mint a scoped test-owner token at gateway boot, pass it as `PW_TOKEN`, run `e2e/auth.setup.ts` in a global-setup, capture `-linux` baselines, then add an `e2e` step to the `web` job so the axe rail + visual rail block regressions.

## What the unblock enables (the remaining S2/S3 backlog, worst-first)

Once baselines exist, each of these is a screenshot-verified increment (`npm run e2e` must stay zero-diff; a real visual change gets `e2e:update` + a plan Execution-log entry for owner review):

1. **`CodeCockpitPage.tsx`** (51 raw `<button>`) → `Button`/`IconButton`, small increments. Ratchet the `rawButton` baseline down each commit.
2. **`ChatPage.tsx`** (34 raw `<button>`) → same.
3. **Migrate the 206 raw form inputs** → `TextField`/`TextArea` (primitive already exists). Ratchet `rawInput` down.
4. **Finish the inline font-weight migration** (168 → 0 where they're JSX inline; `liveMarkdown.ts`'s CodeMirror-theme uses stay). Ratchet `inlineFontWeight` down.
5. **Ad-hoc empty states** (`ChatActivityPanel.Empty`, `NotificationsPage.EmptyFeed`) → the canonical `EmptyState`/`SlotEmptyState`.
6. **Dark/light + responsive parity pass** (S3/T3.3) — the visual suite already captures both themes; add a phone-viewport project to `playwright.config.ts` and assert no horizontal body scroll.

## Owner taste calls (plan Owner-tasks)

- Confirm the audit's worst-first priority (or reweight to your most-used surfaces).
- Sign off on any consolidation that merges two visual patterns into one (none so far have changed pixels).
- WCAG target stays **AA** (per PRODUCT.md) — confirmed, not AAA.
