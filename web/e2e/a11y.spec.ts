import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, assertMounted, OPENERS } from './helpers'

// ── a11y (WCAG 2 AA) scan — every nav route × both themes ───────────────────
// axe-core over each route. PRODUCT.md targets AA (not AAA). We FAIL only on
// serious/critical violations (the plan's bar); moderate/minor are reported
// but don't block, so the ratchet is actionable without drowning in noise.
// This is the dynamic scan the S1 audit deferred (it needs a running app);
// it plus the static scanA11y() coverage complete the a11y picture.

const BLOCKING = new Set(['serious', 'critical'])

// ── WHEN this gate measures, not just how much it covers ────────────────────
// The scan below has three tiers, because breadth on the route axis was hiding a
// hole on the STATE axis. Every a11y defect found by hand in cycles 45-49 lived in
// one of the two tiers this spec did not have:
//
//   1. NAV ROUTES (18)         — was the whole gate.
//   2. SETTINGS PANELS (30)    — each a plain `#/settings/<id>` route that mounts
//                                only when visited. Scanning `settings` covered 1 of
//                                31 surfaces. 3 of cycle 49's 5 defects were here.
//   3. OPENED SURFACES          — modals, docks, menus. Nothing was ever opened, so
//                                every defect behind a click was invisible: 10
//                                blocking violations found by hand in cycle 45 alone.
//
// Tier 3 asserts the surface actually OPENED (element-count delta) before trusting a
// clean result — a recipe that silently no-ops would otherwise report a pass, which is
// the failure mode this whole family exists to close.

for (const theme of THEMES) {
  test.describe(`a11y (WCAG AA): ${theme} theme`, () => {
    for (const { route, id, label } of [...ROUTES, ...SETTINGS_ROUTES, ...VIEW_ROUTES]) {
      test(`${label} (#/${route})`, async ({ page }, testInfo) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
        // Attach the full violation set to the report for triage regardless.
        await testInfo.attach(`axe-${id ?? route}-${theme}.json`, {
          body: JSON.stringify(results.violations, null, 2),
          contentType: 'application/json',
        })

        expect(
          blocking,
          `serious/critical a11y violations on #/${route} (${theme}):\n` +
            blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
        ).toEqual([])
      })
    }

    // ── Tier 3: surfaces that only exist AFTER an interaction ───────────────
    for (const opener of OPENERS) {
      test(`${opener.label} [opened]`, async ({ page }, testInfo) => {
        await seedTheme(page, theme)
        await gotoRoute(page, opener.route)

        const before = await page.evaluate(() => document.querySelectorAll('*').length)
        const opened = await opener.open(page)
        // A recipe whose target is absent (no seeded rows, a renamed button) must NOT
        // report a clean surface — that is indistinguishable from "no violations" and is
        // precisely how this gate hid 10 blocking violations for months. `skip` is honest;
        // a silent pass is not. The recipe supplies its OWN reason, so the report says
        // which of those it was.
        test.skip(opened !== true, opened === true ? '' : `${opener.label}: ${opened.skip}`)
        await page.waitForTimeout(700)
        await assertMounted(page, before, opener.label)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
        await testInfo.attach(`axe-${opener.label.replace(/\W+/g, '-')}-${theme}.json`, {
          body: JSON.stringify(results.violations, null, 2),
          contentType: 'application/json',
        })

        expect(
          blocking,
          `serious/critical a11y violations on ${opener.label} (${theme}) — a surface the\n` +
            `route-level scan never reaches:\n` +
            blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
        ).toEqual([])
      })
    }
  })
}
