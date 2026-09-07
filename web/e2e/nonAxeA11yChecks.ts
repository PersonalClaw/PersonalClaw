import { expect, type Page } from '@playwright/test'

// ── Two machine-checkable defects axe has no rule for ────────────────────────────────────────────
//
// `a11y.spec.ts` states its own limit: "a clean run is the absence of MACHINE-detectable AA
// violations on the scanned states". Both checks below ARE machine-detectable and axe reports
// neither, because neither maps to an axe rule:
//
//   1. **A click target with no keyboard path.** axe cannot know a `<div>` carries an `onClick` —
//      nothing in the accessibility tree distinguishes a decorative div from a wired-up one. What
//      DOES distinguish them is `cursor: pointer`: the app promising an interaction. A pointer cursor
//      on an element with no interactive tag, no interactive role and no tab stop is a mouse-only
//      control (WCAG 2.1.1).
//   2. **A focus ring clipped away by an ancestor.** An outline draws OUTSIDE the border box, so a
//      control filling an `overflow: hidden` parent computes a correct `outline` that is never
//      painted. `:focus-visible` matches, the computed style is right, and the user sees nothing
//      (WCAG 2.4.7) — invisible to every assertion except geometry.
//
// 🔑 CALLED FROM `a11y.spec.ts`'s EXISTING LOOPS RATHER THAN GIVEN THEIR OWN SPEC. A standalone spec
// added 120 tests — a second full traversal of every route — and NAVIGATION is the entire cost here;
// the detectors themselves run in microseconds. Measured: that spec took 7.1 minutes and timed out on
// `page.goto` under load while finding nothing, so the extra traversal bought only flakiness. Calling
// these from the loop that already visits every route costs ~0 and covers MORE — the Tier-3 OPENED
// surfaces (modals, docks, menus), which is exactly where a mouse-only control hides and where a
// route-only sweep never looks.
//
// 🔑 BOTH WERE FOUND IN ONE COMPONENT AND NEITHER WAS SYSTEMIC. `app/onboarding/StepStack` had both:
// a completed step row was mouse-only, and once that was fixed its focus ring was clipped on all four
// sides by the row's own `overflow-hidden`. Sweeping the live DOM afterwards found **zero** further
// instances — 14 routes for the first check, 8 for the second. These land GREEN; their job is to
// catch the next one, not to work through a backlog.
//
// ── THREE FALSE-POSITIVE CLASSES, each removed after reading the data ────────────────────────────
// Recorded because each made the raw count look alarming and each was wrong:
//
//   · **Inherited cursors.** Every descendant of a clickable row inherits `cursor: pointer`, so one
//     row reported at four nesting levels — 30 "distinct" hits for 6 real elements. Only the element
//     where the cursor is DECLARED (its parent is not pointer) is the promise.
//   · **Scrollports.** Counting `overflow: auto/scroll` as clipping gave 67 hits across five routes,
//     nearly all `[bottom]` — the signature of an element at a scroll container's current edge. A
//     ring clipped by a scrollport scrolls into view; it is not the defect.
//   · **The app shell.** `html, body, #root { overflow: hidden }` is this app's own layout, so every
//     element below the fold reported a phantom loss of 300–2000px against it. A ring is only truly
//     clipped when the element is ALREADY INSIDE the clipper and the ring alone falls outside, which
//     is why the loss is bounded to ring size below.
//
// A detector that over-reports gets muted, so the narrowing is the feature.
//
// 🪤 THESE ARE SOURCE STRINGS, invoked as `(${FN})()` at the call sites. `page.evaluate(str)`
// evaluates the string as an EXPRESSION, so passing a bare `() => {…}` returns the function object
// and every assertion then reads `undefined` — which the vacuity probe caught on its first run.
//
// 🔑 THE CLIPPED-RING CHECK RESTS ON PROGRAMMATIC `.focus()` MATCHING `:focus-visible`, so that was
// verified rather than assumed — nearly every focus ring in this app is `:focus-visible`-gated, and a
// detector reading the un-focused base style would be measuring a state the user never sees.
// Measured on a settled page: `el.focus({preventScroll:true})` then `el.matches(':focus-visible')`
// returns TRUE, and the ring computes.
// The distinction that makes this safe is narrow and worth keeping: focus applied during initial
// MOUNT (a `useEffect` on first render) does NOT match `:focus-visible` in Chromium — a screenshot
// taken that way shows a focused control with no ring, which cost two wrong diagnoses while fixing
// `StepStack`. A focus call on an already-rendered page does match. These checks only ever run
// post-navigation, so they are in the second case.

/** Elements that PROMISE a click (pointer cursor, declared here) but offer no keyboard path. */
export const MOUSE_ONLY = `() => {
  const NATIVE = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY','OPTION','LABEL'])
  const ROLES = new Set(['button','link','menuitem','menuitemcheckbox','menuitemradio','tab','option',
    'checkbox','radio','switch','treeitem','gridcell','combobox','slider'])
  const interactive = (el) => {
    if (!el || el === document.body) return false
    if (NATIVE.has(el.tagName)) return true
    const r = el.getAttribute('role')
    if (r && ROLES.has(r)) return true
    return el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1'
  }
  const anyInteractiveAncestor = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) if (interactive(p)) return true
    return false
  }
  const wrapsControl = (el) => !!el.querySelector('a[href],button,input,select,textarea,[role=button],[role=link],[tabindex]:not([tabindex="-1"])')
  const path = (el) => {
    const bits = []
    for (let e = el; e && e !== document.body && bits.length < 3; e = e.parentElement) {
      const cls = (e.className || '').toString().split(/\\s+/).filter(Boolean).slice(0, 2).join('.')
      bits.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : ''))
    }
    return bits.join('>')
  }
  const out = []
  for (const el of document.querySelectorAll('*')) {
    if (interactive(el)) continue
    if (getComputedStyle(el).cursor !== 'pointer') continue
    const p = el.parentElement
    if (p && p !== document.body && getComputedStyle(p).cursor === 'pointer') continue
    if (anyInteractiveAncestor(el)) continue
    const r = el.getBoundingClientRect()
    if (r.width < 24 || r.height < 16) continue
    if (wrapsControl(el)) continue
    out.push(path(el) + '  "' + ((el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40)) + '"')
  }
  return [...new Set(out)]
}`

/** Focusable controls whose focus ring falls outside a clipping ancestor. */
export const CLIPPED_RING = `() => {
  const els = [...document.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])')]
  const path = (el) => {
    const bits = []
    for (let e = el; e && e !== document.body && bits.length < 3; e = e.parentElement) {
      const cls = (e.className || '').toString().split(/\\s+/).filter(Boolean).slice(0, 2).join('.')
      bits.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : ''))
    }
    return bits.join('>')
  }
  const restore = document.activeElement
  const out = []
  for (const el of els) {
    const r = el.getBoundingClientRect()
    if (r.width < 8 || r.height < 8) continue
    try { el.focus({ preventScroll: true }) } catch (e) { continue }
    const cs = getComputedStyle(el)
    const w = parseFloat(cs.outlineWidth) || 0
    const off = parseFloat(cs.outlineOffset) || 0
    if (!w || cs.outlineStyle === 'none') continue
    if (off < 0) continue
    let cl = null
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      // The APP SHELL is not a clipper for this purpose. \`html, body, #root { overflow: hidden }\` is
      // this app's layout, so the shell clips at the VIEWPORT EDGE -- which is a fact about where the
      // fold lands, not a defect in any component, and nothing a component could fix.
      if (p.id === 'root' || p === document.body) continue
      const c = getComputedStyle(p)
      if (['hidden','clip'].includes(c.overflowX) || ['hidden','clip'].includes(c.overflowY)) { cl = p; break }
    }
    if (!cl) continue
    const cr = cl.getBoundingClientRect()
    const insideClipper = r.top >= cr.top - 1 && r.left >= cr.left - 1 && r.right <= cr.right + 1 && r.bottom <= cr.bottom + 1
    if (!insideClipper) continue
    const need = w + off
    const lost = {
      top: cr.top - (r.top - need), left: cr.left - (r.left - need),
      right: (r.right + need) - cr.right, bottom: (r.bottom + need) - cr.bottom,
    }
    const bad = Object.entries(lost).filter(([, v]) => v > 0.5 && v <= 6)
    if (!bad.length) continue
    // The CLIPPER is named too, because that is often where the fix belongs. The first version
    // reported only the focused control, which sent me to a shared primitive (ui/IconButton, ~200
    // call sites) for a defect that existed in ONE container -- the wrong blast radius by two orders
    // of magnitude. Whether to inset the ring or fix the container needs both ends named.
    // NB: no backticks in this comment. It lives INSIDE a template string, and a backtick here ends
    // the literal -- which is exactly how it broke on the first run.
    out.push(path(el) + '  "' + ((el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30))
      + '"  ring ' + w + 'px offset ' + off + 'px clipped ' + bad.map(([k, v]) => k + ':' + v.toFixed(1)).join(' ')
      + '  clipped-by ' + path(cl) + (cl.className ? ' [' + cl.className.toString().slice(0, 60) + ']' : ''))
  }
  if (restore instanceof HTMLElement) { try { restore.focus({ preventScroll: true }) } catch (e) { /* ignore */ } }
  return [...new Set(out)]
}`

// 🪤 THERE IS NO KNOWN-BUG LIST, AND THE ONE I BRIEFLY WROTE WAS WRONG. An earlier pass reported
// `ui/InvestigateButton` losing 3.5px of its 4px ring on the bottom edge, on `#/settings/models` and
// `#/settings/doctor`, and I allowlisted it as "a BUG, not an exemption" while noting the clipper was
// an unidentifiable class-less `<div>`.
//
// Identifying it dissolved the finding. The clipper was `<div id="root">` — the app SHELL, which is
// `overflow: hidden` by this app's own layout. Measured on `#/settings/doctor`, which renders 14
// Investigate buttons: their distances from the shell's bottom edge were 444, 332, 113, **1**, then
// −112, −224, −337, −449 … The negatives sit below the fold and were already excluded for being
// outside the clipper; the one at **1px** is flush against the viewport bottom, so its ring's bottom
// 3.5px falls off-screen. Shift the viewport a few pixels and a DIFFERENT button occupies that spot.
//
// So the report was a fact about where the fold lands at 1280×720, not a property of a component, and
// no component could fix it. `#root` and `<body>` are skipped as clipper candidates above, which is
// what the "app shell" false-positive class in the header always implied — the earlier
// `insideClipper` guard only caught elements fully BELOW the fold, not the one straddling it.
//
// Recorded rather than quietly deleted because the mistake is the instructive part: I had already
// named this false-positive class, then mislabelled a fresh instance of it as a bug because the
// clipper was anonymous. **An offender whose owner cannot be identified is a reason to keep
// investigating, not a reason to write it down as real.**

/** Run both non-axe checks on whatever is already rendered — adds assertions, not page loads. */
export async function expectNoNonAxeA11yDefects(page: Page, where: string): Promise<void> {
  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  expect(
    mouseOnly,
    `${where}: element promises a click (cursor: pointer) with no tag, role or tab stop, and wraps no `
    + `control — mouse-only (WCAG 2.1.1). Render a real <button>, or drop the pointer cursor:\n  `
    + mouseOnly.join('\n  '),
  ).toEqual([])

  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    clipped,
    `${where}: a focused control's outline falls outside a clipping ancestor, so the ring computes but `
    + `never paints (WCAG 2.4.7). Use a negative outline-offset — and match the parent's radius, or `
    + `the ring's corners clip instead:\n  ` + clipped.join('\n  '),
  ).toEqual([])
}
