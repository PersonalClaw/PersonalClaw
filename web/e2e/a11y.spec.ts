import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute } from './helpers'

// ── a11y (WCAG 2 AA) scan — every nav route × both themes ───────────────────
// axe-core over each route. PRODUCT.md targets AA (not AAA). We FAIL only on
// serious/critical violations (the plan's bar); moderate/minor are reported
// but don't block, so the ratchet is actionable without drowning in noise.
// This is the dynamic scan the S1 audit deferred (it needs a running app);
// it plus the static scanA11y() coverage complete the a11y picture.

const BLOCKING = new Set(['serious', 'critical'])

for (const theme of THEMES) {
  test.describe(`a11y (WCAG AA): ${theme} theme`, () => {
    for (const { route, label } of ROUTES) {
      test(`${label} (#/${route})`, async ({ page }, testInfo) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
        // Attach the full violation set to the report for triage regardless.
        await testInfo.attach(`axe-${route}-${theme}.json`, {
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
  })
}
