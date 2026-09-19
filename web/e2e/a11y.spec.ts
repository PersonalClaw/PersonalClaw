import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES, THEMES } from './routes'
import {
  seedTheme,
  gotoRoute,
  assertMounted,
  OPENERS,
  driveScriptedTurns,
  openHeaderOverflowIfNeeded,
  settleEntranceAnimations,
} from './helpers'
// Two defects axe has no rule for, asserted on the SAME navigation this spec already performs —
// see `nonAxeA11yChecks.ts` for why they live here rather than in a spec of their own.
import { expectNoNonAxeA11yDefects } from './nonAxeA11yChecks'

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
//   4. NON-NAV ROUTABLE PAGES   — `App.tsx` routes to pages that have no nav tile.
//                                `mission-control` is a parameterless authenticated
//                                page, so "every authenticated route is scanned" was
//                                false while it sat outside all three lists above.
//
// Tier 3 asserts the surface actually OPENED (element-count delta) before trusting a
// clean result — a recipe that silently no-ops would otherwise report a pass, which is
// the failure mode this whole family exists to close.
//
// ── What this tag set does NOT cover ────────────────────────────────────────
// `wcag2a/wcag2aa/wcag21a/wcag21aa` omits `target-size` (WCAG 2.2 AA, tag `wcag22aa`),
// so a control smaller than 24×24 CSS px passes here. It also cannot express
// intent-level questions — "was the user TOLD this failed?", "does this order make
// sense?" — so a clean run is the absence of MACHINE-detectable AA violations on the
// scanned states, not a claim the surface is accessible.

for (const theme of THEMES) {
  test.describe(`a11y (WCAG AA): ${theme} theme`, () => {
    for (const { route, id, label } of [
      ...ROUTES,
      ...SETTINGS_ROUTES,
      ...VIEW_ROUTES,
      ...NON_NAV_ROUTES,
    ]) {
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

        await expectNoNonAxeA11yDefects(page, `#/${route} (${theme})`)
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

        // The opened surfaces are the MOST likely home for a mouse-only control — a menu row or a
        // dock header is exactly the shape that gets an `onClick` on a div — and a route-level sweep
        // can never see them, which is the same gap Tier 3 exists to close for axe.
        await expectNoNonAxeA11yDefects(page, `${opener.label} [opened] (${theme})`)
      })
    }
  })
}

// ── Tier 5: THE MOBILE VIEWPORT (atom SSM-10) ───────────────────────────────
// Every tier above runs at this config's one viewport, 1280×900. That is a fourth
// hole of the same shape as the three the comment at the top of this file records:
// breadth on the route and state axes was hiding a hole on the VIEWPORT axis. The
// app switches real surfaces on `useIsMobile` (≤768px) — the shell's nav rail
// becomes an overlay drawer, and the Session Map's gutter rail becomes a tappable
// SidePanel drawer — so at 1280px those branches were never scanned by anything.
//
// 🪤 DELIBERATELY *NOT* A SECOND PLAYWRIGHT PROJECT, which is the obvious way to do
// this and the wrong one. A `{ name: 'mobile', use: devices['Pixel 5'] }` project
// re-runs EVERY spec in this directory at that viewport: `visual.spec.ts` would
// demand 34 new committed baselines, and the 96 route scans + PWA + walkthrough
// specs would double the gate's runtime to cover surfaces no atom asked for. A
// describe-scoped `test.use({ viewport })` buys exactly the coverage the clause
// names, changes the shared config by zero lines, and cannot destabilise a single
// existing test. (Recorded because SSM-10's own audit note predicted the project.)
//
// SCOPE: one route, because it is the only one whose LAYOUT the breakpoint changes
// into a different control set, and because reaching it needs a real chat turn.
test.describe('a11y (WCAG AA): mobile viewport — in-session navigation', () => {
  // A phone viewport, below `useIsMobile`'s 768px breakpoint.
  test.use({ viewport: { width: 390, height: 844 } })
  // A real turn waits on the backend, unlike every other test in this file.
  test.describe.configure({ timeout: 180_000 })

  for (const theme of THEMES) {
    test(`chat: Session Map drawer [mobile] (${theme})`, async ({ page }, testInfo) => {
      await seedTheme(page, theme)
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Index this turn on the session map, please', 2)

      // The clause's own floor: in-session nav must remain REACHABLE at this viewport. The
      // gutter rail is gone here by design, so if the one named control is also unreachable the
      // mobile user has no in-session navigation at all — and a clean axe result on a surface
      // that lost its navigation is precisely the kind of pass this file exists to refuse.
      expect(
        await openHeaderOverflowIfNeeded(page, 'Session map'),
        'IN-SESSION NAV IS UNREACHABLE at 390px: neither the Session Map rail nor its named\n' +
          'control is present. SSM-10 exists to prevent exactly this.',
      ).toBe(true)
      await page.getByRole('button', { name: 'Session map', exact: true }).click()
      const drawer = page.getByRole('region', { name: 'Session map' })
      await expect(drawer).toBeVisible({ timeout: 10_000 })
      await expect(drawer.locator('[data-session-map-row]').first()).toBeVisible()
      // The rows stagger `opacity: 0 → 1` (`SessionMapDrawer`'s per-row `delay`), and the row
      // above being VISIBLE only means the FIRST one arrived. Measured without this settle: axe
      // reported `[serious] color-contrast … 3.56` against `#85888c`, which is not a token —
      // it is `--color-on-surface-var` (`#5f6368`) composited on white at α ≈ 0.76 (per channel:
      // (255−0x85)/(255−0x5f) = 0.76, (255−0x88)/(255−0x63) = 0.76). At rest the pair is 5.9:1.
      // Same mechanism, same fix, as the dashboard flake `settleEntranceAnimations` documents.
      await settleEntranceAnimations(page)

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze()
      const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
      await testInfo.attach(`axe-session-map-mobile-${theme}.json`, {
        body: JSON.stringify(results.violations, null, 2),
        contentType: 'application/json',
      })
      expect(
        blocking,
        `serious/critical a11y violations on the mobile Session Map drawer (${theme}):\n` +
          blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
      ).toEqual([])

      await expectNoNonAxeA11yDefects(page, `chat Session Map drawer [mobile] (${theme})`)
    })
  }
})

// ── Tier 6: THE DESKTOP SESSION MAP RAIL, OPEN (atom SSM-15) ────────────────
// Tier 5 above scans the map's MOBILE form. Its DESKTOP form — the 4px-tick gutter
// rail that is the session's ONLY in-session index on a pointer device — was covered
// by nothing, and the reason is the same shape as the four holes recorded at the top
// of this file, one axis over: `#/chat` IS in the route sweep, but the sweep visits
// the EMPTY chat route. With no session, `mapOpen && !isMobile` is false and
// `SessionMapRail` returns null before its first hook (`SESSION_MAP_MIN_MARKS`), so
// the rail has never appeared in a single scanned tree.
//
// 🔴 AND REACHING THE ROUTE WITH A SESSION WOULD NOT HAVE BEEN ENOUGH, which is the
// part worth stating: the sweep asserts nothing about the rail being PRESENT. A
// regression that closed the rail by default, dropped it below its mark threshold, or
// unmounted it outright would leave every scan GREEN while deleting the surface under
// test — a clean axe result on an absent surface is byte-identical to a clean one on a
// healthy surface. So the rail and its marks are asserted FIRST, and only then is a
// clean result trusted. This is the same "assert it actually opened" floor Tier 3 puts
// in front of its openers, applied to a surface that opens by default.
//
// TWO STATES, ONE NAVIGATION. The revealed preview card is scanned in the same test
// rather than a second one, because it costs three more scripted turns to reach it
// otherwise. Each state carries its own presence floor and its own report attachment,
// so neither can pass on the other's behalf.
test.describe('a11y (WCAG AA): desktop viewport — the Session Map rail, OPEN', () => {
  // EXPLICIT, even though it matches the config default: `useIsMobile`'s breakpoint is
  // max-width 768, and this whole tier is about the branch taken ABOVE it. Pinning the
  // width here means a future config change to a narrow default cannot silently turn
  // this tier into a second copy of Tier 5.
  test.use({ viewport: { width: 1280, height: 900 } })
  // Real turns wait on the backend, as Tier 5 does.
  test.describe.configure({ timeout: 180_000 })

  const RAIL = 'nav[aria-label="Session map"]'
  const MARK = '[data-session-mark]'

  for (const theme of THEMES) {
    test(`chat: Session Map rail + preview card [desktop] (${theme})`, async ({ page }, testInfo) => {
      await seedTheme(page, theme)
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Index this turn on the session map, please', 3)

      // ── FLOOR 1: the rail is actually here, with marks ──────────────────────
      // Desktop defaults OPEN (`useQueryParam(…, 'map', isMobile ? '0' : '1')`), so no
      // control is touched — but "it defaults open" is exactly the claim a regression
      // would falsify, so it is read rather than assumed.
      const rail = page.locator(RAIL)
      await expect(
        rail,
        'THE SESSION MAP RAIL IS ABSENT at 1280px in a started session. Either it stopped\n' +
          'defaulting open, or it unmounted — and without this assertion the axe scan below\n' +
          'would report a clean surface it never visited. The rail is the session\'s only\n' +
          'in-session index on a pointer device.',
      ).toBeVisible({ timeout: 20_000 })
      const markCount = await page.locator(MARK).count()
      expect(
        markCount,
        'the rail mounted with no marks, so the scan below covers a bare <nav> rather than the\n' +
          'tick controls this tier exists to measure',
      ).toBeGreaterThan(1)

      const railResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze()
      const railBlocking = railResults.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
      await testInfo.attach(`axe-session-map-rail-desktop-${theme}.json`, {
        body: JSON.stringify(railResults.violations, null, 2),
        contentType: 'application/json',
      })
      expect(
        railBlocking,
        `serious/critical a11y violations with the desktop Session Map rail OPEN (${theme}) — a\n` +
          `state no route scan has ever reached:\n` +
          railBlocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
      ).toEqual([])

      // ── FLOOR 2: the preview card, revealed the way a keyboard user reveals it ──
      // A cursor key opens the card and passive focus does not (the rail's REVEAL
      // one-shot), so the card cannot be reached by focusing a tick alone. The card is
      // the only text-bearing surface the rail owns — role label, timestamp, request and
      // response excerpts — i.e. the one place a contrast rule can bite here at all.
      await page.locator(`${MARK}[tabindex="0"]`).focus()
      await page.keyboard.press('ArrowDown')
      const card = page.locator('[data-session-map-card]')
      await expect(
        card,
        'the keyboard REVEAL did not open a preview card, so the second scan below would re-scan\n' +
          'the first state and report it as card coverage',
      ).toBeVisible({ timeout: 10_000 })
      // The card's own excerpt arrives with `ui/Popover`'s `overlayEnter` spring; mid-flight
      // opacity is what made the mobile tier read a composited `#85888c` (see Tier 5).
      await settleEntranceAnimations(page)

      const cardResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze()
      const cardBlocking = cardResults.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
      await testInfo.attach(`axe-session-map-card-desktop-${theme}.json`, {
        body: JSON.stringify(cardResults.violations, null, 2),
        contentType: 'application/json',
      })
      expect(
        cardBlocking,
        `serious/critical a11y violations on the Session Map preview card (${theme}):\n` +
          cardBlocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
      ).toEqual([])

      await expectNoNonAxeA11yDefects(page, `chat Session Map rail + card [desktop] (${theme})`)
    })
  }
})
