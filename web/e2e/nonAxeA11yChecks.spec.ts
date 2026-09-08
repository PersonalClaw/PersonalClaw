import { test, expect } from '@playwright/test'
import { gotoRoute, assertShellMounted } from './helpers'
import { MOUSE_ONLY, CLIPPED_RING } from './nonAxeA11yChecks'

// ── The anti-vacuity probe for the two non-axe checks ────────────────────────────────────────────
//
// Both detectors return `[]` on a healthy tree, and the tree IS healthy — 14 routes and 8 routes
// clean when swept. So a selector that rotted into a no-op would look exactly like a pass, forever.
// That is the same failure mode `a11y.spec.ts`'s Tier-3 `assertMounted` exists to prevent, and the
// same one this repo's `zLayerScale` rail calls out: "a rot in either direction fails".
//
// ONE test, not per-route: the detectors are route-independent, so proving they still recognise the
// shapes costs a single navigation.
test('the non-axe detectors still recognise the defects they exist for', async ({ page }) => {
  await gotoRoute(page, 'dashboard')
  await assertShellMounted(page)

  // A clean baseline first — if the live page already had one of these, the probe below could not
  // distinguish "detector works" from "page is broken".
  const baselineMouse = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  const baselineRing = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(baselineMouse, 'dashboard must start clean for this probe to mean anything').toEqual([])
  expect(baselineRing, 'dashboard must start clean for this probe to mean anything').toEqual([])

  await page.evaluate(`(() => {
    const host = document.createElement('div')
    host.id = 'non-axe-probe'
    // 1. a mouse-only click target: pointer cursor, no tag/role/tabindex, wraps no control
    const mouseOnly = document.createElement('div')
    mouseOnly.style.cssText = 'cursor:pointer;width:120px;height:32px'
    mouseOnly.textContent = 'probe-mouse-only'
    // 2. a focusable button whose OUTWARD ring is clipped by an overflow-hidden parent it fills
    const clip = document.createElement('div')
    clip.style.cssText = 'overflow:hidden;width:120px;height:32px'
    const btn = document.createElement('button')
    btn.style.cssText = 'width:120px;height:32px;outline:2px solid red;outline-offset:0'
    btn.textContent = 'probe-clipped'
    clip.appendChild(btn)
    host.append(mouseOnly, clip)
    document.body.appendChild(host)
  })()`)

  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  expect(
    mouseOnly.join('\n'),
    'the mouse-only detector no longer finds a pointer-cursor div with no keyboard path',
  ).toContain('probe-mouse-only')

  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    clipped.join('\n'),
    'the clipped-ring detector no longer finds an outward ring inside an overflow-hidden parent',
  ).toContain('probe-clipped')

  // And the negative half: the FIX must not register as a defect, or the rail would punish the
  // correct shape. A negative outline-offset draws the ring inside the clip.
  await page.evaluate(`(() => {
    const b = document.querySelector('#non-axe-probe button')
    if (b) b.style.outlineOffset = '-2px'
  })()`)
  const afterFix = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    afterFix.join('\n'),
    'an inset ring is the prescribed fix and must NOT be reported',
  ).not.toContain('probe-clipped')

  await page.evaluate(`(() => { document.getElementById('non-axe-probe')?.remove() })()`)
})

// ── The other half of anti-vacuity: a scanned route must actually RENDER something ────────────────
//
// 🪤 `a11y.spec.ts`'s Tier-1/2 loop navigates, runs axe, and runs these detectors — and asserts
// NOTHING about the page having mounted. That is fine for a nav route, which visibly is what it is,
// and it is NOT fine for the entry this change adds: `app/not-a-real-app` exists precisely because
// `AppHostPage`'s 404 branch is a real surface, and a route that came back blank (or was hijacked by
// an onboarding gate) would sail through axe and both detectors and report the shell "clean" while
// scanning nothing at all. That is the shape of vacuous coverage this campaign keeps finding, so the
// claim gets its own check rather than an assumption.
//
// Asserted here rather than inside that loop because it is about ONE route, and adding a per-route
// mount assertion to a 100-test loop is a different change with its own cost.
test('the app-host shell this manifest scans really does render', async ({ page }) => {
  await gotoRoute(page, 'app/not-a-real-app')

  // The 404 branch names the app it could not find — that is the surface being scanned.
  await expect(
    page.getByText(/isn’t installed/),
    'the app-host 404 EmptyState must be what a scan of #/app/<not-installed> sees',
  ).toBeVisible()

  // And its action is a real, reachable control — the thing a mouse-only or clipped-ring defect
  // would live on. Without this the route could render a bare sentence and the detectors would have
  // no focusable element to judge, which is indistinguishable from "no defects".
  const cta = page.getByRole('button', { name: /Open the Store/i })
  await expect(cta, 'the EmptyState action must be a real control').toBeVisible()
  await cta.focus()
  await expect(cta, 'and it must be focusable, or there is nothing here for these checks to sweep')
    .toBeFocused()
})
