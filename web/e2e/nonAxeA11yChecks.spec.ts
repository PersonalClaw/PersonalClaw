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
    // 3. a container that hides x and scrolls y, as the sidebar's nav does: a button at its scroll
    //    edge loses its ring only across the axis it scrolls, so it is a scrollport's edge, not a clip
    const scroller = document.createElement('div')
    scroller.style.cssText = 'overflow-x:hidden;overflow-y:auto;width:140px;height:64px'
    const above = document.createElement('div')
    above.style.cssText = 'height:8px'
    const below = document.createElement('div')
    below.style.cssText = 'height:40px'
    const atEdge = document.createElement('button')
    atEdge.style.cssText = 'display:block;margin:0 10px;width:120px;height:32px;outline:2px solid red;outline-offset:0'
    atEdge.textContent = 'probe-scroll-edge'
    // 4. and one in the same container that fills the axis it HIDES: that ring is clipped
    const acrossHidden = document.createElement('button')
    acrossHidden.style.cssText = 'display:block;width:140px;height:24px;outline:2px solid red;outline-offset:0'
    acrossHidden.textContent = 'probe-hidden-axis'
    // 24 + 8 + 32: the button's bottom is the scroller's, and the 40 below it make the scroller scroll
    scroller.append(acrossHidden, above, atEdge, below)
    // 5. a button filling a box that clips on both axes with a clip margin as wide as its ring: the
    //    ring paints in that margin, so it is not clipped
    const margined = document.createElement('div')
    margined.id = 'non-axe-probe-margin'
    margined.style.cssText = 'overflow:clip;overflow-clip-margin:4px;width:120px;height:32px;margin:8px'
    const roomy = document.createElement('button')
    roomy.style.cssText = 'width:120px;height:32px;outline:2px solid red;outline-offset:0'
    roomy.textContent = 'probe-clip-margin-room'
    margined.appendChild(roomy)
    host.append(mouseOnly, clip, scroller, margined)
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
  expect(
    clipped.join('\n'),
    'a ring clipped across the axis its container hides is still a clip when the other axis scrolls',
  ).toContain('probe-hidden-axis')
  expect(
    clipped.join('\n'),
    "a ring at a scroll edge is a scrollport's edge, not a clip, when the container hides the other axis",
  ).not.toContain('probe-scroll-edge')
  expect(
    clipped.join('\n'),
    "a ring inside a box's clip margin is painted, so it is not a clip",
  ).not.toContain('probe-clip-margin-room')
  // …and a clip margin narrower than the ring does clip it.
  await page.evaluate(`(() => {
    const m = document.getElementById('non-axe-probe-margin')
    if (m) m.style.overflowClipMargin = '1px'
  })()`)
  const narrowMargin = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    narrowMargin.join('\n'),
    "a ring wider than its box's clip margin is clipped",
  ).toContain('probe-clip-margin-room')

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

  // The fold, measured: a button flush against the bottom of a scroller that fills an overflow-hidden
  // box. The detector focuses it for real and reads its ring again, so what is a finding is what
  // Chromium leaves cut. Each button clips its own text (under the app's font it would wrap, and its
  // overflow would give the scroller room past its end), so it clips its own overflow too, and focus
  // then stops at its padding box: its border counts against the scroll padding.
  //   a: no scroll padding: focus leaves it where it is, cut by its ring: a finding.
  //   b: a 2 px border and a scroll padding equal to the ring: focus scrolls it 2 px, still cut: a finding.
  //   c: the same at a scroll padding of 8: focus scrolls it 6 px, clear of its ring: not a finding.
  //   d: the same with nothing below it to scroll to: focus cannot move it: a finding.
  //   e: borderless at a scroll padding equal to the ring: focus scrolls it 4 px, clear: not a finding.
  //   h: a borderless link at the same edge (a project page's Open in Library): clear, not a finding.
  //   i: a borderless button at a scroll padding of 8 in a scroller a stylesheet makes scroll smoothly:
  //      the detector reads the end of the scroll, not its first frame: clear, not a finding.
  // And the cut it counts, with no scroller: a ring cut by 1 px is a finding (f), one that just fits is
  // not (g).
  await page.evaluate(`(() => {
    const host = document.getElementById('non-axe-probe')
    const row = (name, border, pad, below) => {
      const frame = document.createElement('div')
      frame.style.cssText = 'overflow:hidden;width:140px;height:64px;margin:8px 0'
      const scroller = document.createElement('div')
      scroller.style.cssText = 'overflow:auto;width:140px;height:64px;scroll-padding-block:' + pad + 'px'
      const above = document.createElement('div')
      above.style.cssText = 'height:32px'
      const button = document.createElement('button')
      button.style.cssText = 'display:block;box-sizing:border-box;margin:0 10px;width:80px;height:32px;overflow:hidden;white-space:nowrap;'
        + 'border:' + border + 'px solid #888;outline:2px solid red;outline-offset:2px'
      button.textContent = name
      const rest = document.createElement('div')
      rest.style.cssText = 'height:' + below + 'px'
      scroller.append(above, button, rest)
      frame.appendChild(scroller)
      host.appendChild(frame)
    }
    row('probe-fold-a-unpadded', 2, 0, 40)
    row('probe-fold-b-border', 2, 4, 40)
    row('probe-fold-c-clear', 2, 8, 40)
    row('probe-fold-d-end', 2, 8, 0)
    row('probe-fold-e-borderless', 0, 4, 40)
    const smooth = document.createElement('style')
    smooth.id = 'non-axe-probe-smooth'
    smooth.textContent = '#non-axe-probe .probe-smooth { scroll-behavior: smooth }'
    document.head.appendChild(smooth)
    row('probe-fold-i-smooth', 0, 8, 40)
    host.lastElementChild.firstElementChild.classList.add('probe-smooth')
    const link = document.createElement('a')
    link.href = '#non-axe-probe'
    link.textContent = 'probe-fold-h-link'
    link.style.cssText = 'display:inline-block;margin:0 10px;height:32px;line-height:32px;overflow:visible;white-space:nowrap;outline:2px solid red;outline-offset:2px'
    const lframe = document.createElement('div')
    lframe.style.cssText = 'overflow:hidden;width:200px;height:64px;margin:8px 0'
    const lscroller = document.createElement('div')
    lscroller.style.cssText = 'overflow:auto;width:200px;height:64px;scroll-padding-block:4px'
    const labove = document.createElement('div')
    labove.style.cssText = 'height:32px'
    const lbelow = document.createElement('div')
    lbelow.style.cssText = 'height:40px'
    lscroller.append(labove, link, lbelow)
    lframe.appendChild(lscroller)
    host.appendChild(lframe)
    const cut = (name, top) => {
      const frame = document.createElement('div')
      frame.style.cssText = 'overflow:hidden;width:140px;height:64px;margin:8px 0'
      const button = document.createElement('button')
      button.style.cssText = 'display:block;box-sizing:border-box;margin:' + top + 'px 10px 0;width:80px;height:32px;overflow:hidden;white-space:nowrap;'
        + 'border:0;outline:2px solid red;outline-offset:2px'
      button.textContent = name
      frame.appendChild(button)
      host.appendChild(frame)
    }
    cut('probe-cut-f-by1', 29)
    cut('probe-cut-g-fits', 28)
  })()`)
  const fold = (await page.evaluate<string[]>(`(${CLIPPED_RING})()`)).join('\n')
  expect(fold, "a ring at a scroller's edge with no scroll padding stays cut when focused, so it is a finding").toContain('probe-fold-a-unpadded')
  expect(fold, "a scroll padding no wider than the ring leaves a bordered control that clips its overflow cut, so it is a finding").toContain('probe-fold-b-border')
  expect(fold, "a ring at a scroller's edge that a real focus scrolls clear is the fold, not a finding").not.toContain('probe-fold-c-clear')
  expect(fold, 'a scroll padding does not help a ring at an edge the scroller cannot scroll past, so it is a finding').toContain('probe-fold-d-end')
  expect(fold, "a borderless control's focus clears the full scroll padding, so its ring shows: not a finding").not.toContain('probe-fold-e-borderless')
  expect(fold, "a borderless link at a padded edge comes in clear of its ring when focused: not a finding").not.toContain('probe-fold-h-link')
  expect(fold, 'a scroller that scrolls smoothly is read at the end of the scroll: not a finding').not.toContain('probe-fold-i-smooth')
  expect(fold, 'a ring cut by 1 px is a finding').toContain('probe-cut-f-by1')
  expect(fold, 'a ring that just fits its box is not a finding').not.toContain('probe-cut-g-fits')
  const restored = await page.evaluate(() => [...document.querySelectorAll('#non-axe-probe div')].every((d) => d.scrollTop === 0))
  expect(restored, 'the detector puts every scroll position back after its real focus').toBe(true)

  await page.evaluate(`(() => { document.getElementById('non-axe-probe')?.remove(); document.getElementById('non-axe-probe-smooth')?.remove() })()`)
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
