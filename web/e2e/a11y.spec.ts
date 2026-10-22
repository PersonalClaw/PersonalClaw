import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { THEMES } from './routes'
import { seedTheme, gotoRoute, driveScriptedTurns, openHeaderOverflowIfNeeded, settleEntranceAnimations } from './helpers'
// Two defects axe has no rule for, asserted on the same navigation as the axe scan.
import { expectNoNonAxeA11yDefects } from './nonAxeA11yChecks'

// ── a11y (WCAG 2 AA): the chat Session Map, in the two states only a chat turn reaches ──────────
// The route and opener scans this file used to run are the UI quality check's now
// (`surfaces.spec.ts`, #3941): every route, substate URL and opened surface, once per theme, with
// axe and the non-axe detectors as two of its steps. What stays here needs a scripted chat turn
// before there is anything to scan, so it cannot share that visit.
//
// PRODUCT.md targets AA (not AAA). Only serious/critical violations fail (the blocking bar), as
// in the matrix's axe step.

const BLOCKING = new Set(['serious', 'critical'])

// ── What this tag set does NOT cover ────────────────────────────────────────
// `wcag2a/wcag2aa/wcag21a/wcag21aa` omits `target-size` (WCAG 2.2 AA, tag `wcag22aa`),
// so a control smaller than 24×24 CSS px passes here. It also cannot express
// intent-level questions — "was the user TOLD this failed?", "does this order make
// sense?" — so a clean run is the absence of MACHINE-detectable AA violations on the
// scanned states, not a claim the surface is accessible.

// ── Tier 5: THE MOBILE VIEWPORT ─────────────────────────────────────────────
// The route scans run at this config's one viewport, 1280×900, and the matrix steps every
// surface down to 320 px, but only on the routes as loaded. Breadth on the route and state axes
// was hiding a hole on the VIEWPORT axis. The
// app switches real surfaces on `useIsMobile` (≤768px) — the shell's nav rail
// becomes an overlay drawer, and the Session Map's gutter rail becomes a tappable
// SidePanel drawer — so at 1280px those branches were never scanned by anything.
//
// 🪤 DELIBERATELY *NOT* A SECOND PLAYWRIGHT PROJECT, which is the obvious way to do
// this and the wrong one. A `{ name: 'mobile', use: devices['Pixel 5'] }` project
// re-runs EVERY spec in this directory at that viewport: `visual.spec.ts` would
// demand 34 new committed baselines, and the 96 route scans + PWA + walkthrough
// specs would double the gate's runtime to cover surfaces no change asked for. A
// describe-scoped `test.use({ viewport })` buys exactly the coverage the clause
// names, changes the shared config by zero lines, and cannot destabilise a single
// existing test. (Recorded because its own audit note predicted the project.)
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
      // Four messages: the map appears at four (`SESSION_MAP_MIN_MARKS`).
      await driveScriptedTurns(page, 'Index this turn on the session map, please', 4)

      // The floor: in-session nav must remain REACHABLE at this viewport. The
      // gutter rail is gone here by design, so if the one named control is also unreachable the
      // mobile user has no in-session navigation at all — and a clean axe result on a surface
      // that lost its navigation is precisely the kind of pass this file exists to refuse.
      expect(
        await openHeaderOverflowIfNeeded(page, 'Session map'),
        'IN-SESSION NAV IS UNREACHABLE at 390px: neither the Session Map rail nor its named\n' +
          'control is present, so the mobile user has no in-session navigation at all.',
      ).toBe(true)
      await page.getByRole('button', { name: 'Session map', exact: true }).click()
      const drawer = page.getByRole('region', { name: 'Session map' })
      await expect(drawer).toBeVisible({ timeout: 10_000 })
      await expect(drawer.locator('[data-session-map-row]').first()).toBeVisible()
      // The rows used to stagger `opacity: 0 → 1`, and a row being VISIBLE only meant the FIRST one
      // had arrived; they are still now, and the settle stays for the panel's own entrance. Measured without this settle: axe
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

// ── Tier 6: THE DESKTOP SESSION MAP RAIL, OPEN ──────────────────────────────
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
      await driveScriptedTurns(page, 'Index this turn on the session map, please', 4)

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
      // one-shot), so the card cannot be reached by focusing a marker alone. The card is
      // the only text-bearing surface the rail owns — timestamp, request and the muted
      // reply excerpt — i.e. the one place a contrast rule can bite here at all.
      // Home, THEN ArrowDown: the tab stop seeds on the on-screen region, which with one
      // marker per message can already be the LAST marker, where a lone ArrowDown clamps,
      // moves no focus and so reveals nothing. From Home the second key always moves.
      await page.locator(`${MARK}[tabindex="0"]`).focus()
      await page.keyboard.press('Home')
      await page.keyboard.press('ArrowDown')
      const card = page.locator('[data-session-map-card]')
      await expect(
        card,
        'the keyboard REVEAL did not open a preview card, so the second scan below would re-scan\n' +
          'the first state and report it as card coverage',
      ).toBeVisible({ timeout: 10_000 })
      // The preview arrives in the rail's overlay material; any entrance it has must settle before
      // axe reads colours, or a mid-flight opacity reads as a composited tone (see Tier 5).
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

// ── Tier 7: FOCUS MODE AND READING MODE, BOTH ON ────────────────────────────
// Focus mode removes the rail and reading mode folds the composer to one line, so a chat with both
// on is a state no route sweep reaches. Folded, the composer is clipped to nothing and transparent
// but stays in the tab order, so focus can reach it and opening it is what focus does — which makes
// this the state to scan for a control that is focusable and unseen. The floor comes first: the rail
// is gone, the composer is folded, and the line it folds to is on screen, so a key that stopped
// working cannot pass as a clean scan of the page it failed to change.
test.describe('a11y (WCAG AA): a chat in focus mode and reading mode', () => {
  test.use({ viewport: { width: 1280, height: 900 } })
  // A real turn waits on the backend, as Tiers 5 and 6 do.
  test.describe.configure({ timeout: 180_000 })

  for (const theme of THEMES) {
    test(`chat: focus mode + reading mode (${theme})`, async ({ page }, testInfo) => {
      await seedTheme(page, theme)
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Read me a long answer, please', 1)
      await page.keyboard.press('ControlOrMeta+Backslash')
      await page.keyboard.press('ControlOrMeta+Shift+Period')
      await expect(page.locator('nav[data-tour="rail"]')).toHaveCount(0)
      await expect(page.locator('[data-composer-region]')).toHaveAttribute('data-folded', 'true')
      await expect(page.getByRole('button', { name: /^Write a reply/ })).toBeVisible()
      await settleEntranceAnimations(page)

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze()
      const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
      await testInfo.attach(`axe-focus-and-reading-${theme}.json`, {
        body: JSON.stringify(results.violations, null, 2),
        contentType: 'application/json',
      })
      expect(
        blocking,
        `serious/critical a11y violations on a chat in focus and reading mode (${theme}):\n` +
          blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
      ).toEqual([])

      await expectNoNonAxeA11yDefects(page, `chat in focus and reading mode (${theme})`)
    })
  }
})
