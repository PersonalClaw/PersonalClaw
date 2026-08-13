import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A popup you cannot see is not a popup: the paint-order sweep ───────────────────────────────
//
// Cycle 137 found a menu that was fully keyboard-operable and completely invisible (an `absolute
// z-30` flyout the page body painted over). The probe that caught it is one line —
// `document.elementFromPoint` at the popup's own centre — so cycle 138 ran it across **every popup
// the app can open**: 17 routes × 2 viewports, opening 58 (desktop) / 62 (phone) popups by keyboard,
// sampling three points each, and comparing every flyout's rect against its nearest
// overflow-clipping ancestor.
//
//   viewport   findings before   after
//   1440×900   7                 **0**
//   430×900    25                17   ← all 17 are ONE deferred item (see below)
//
// 🔴 WHAT IT FOUND, and why the rect comparison mattered more than the sample points:
//
//   `#/apps` card actions menu — the flyout is 175px tall inside a card whose own
//   `overflow-hidden` box ends **56px earlier**, so the LAST row ("Force uninstall" — the
//   destructive one) was clipped away on **all 7 cards**, and the strip it occupied belongs to the
//   card underneath, which is itself clickable. At 430px two of the seven were clipped by **166px**:
//   invisible entirely. Only ONE of the three sample points caught it; the rect comparison caught
//   every instance, and would catch a 2px clip.
//
//   `ui/FilterMenu` on `#/notifications` @430 — box at **x = -202**: 202px of a 264px-wide menu off
//   the LEFT edge of the shell, so every filter label was clipped and only a column of bare counts
//   remained. 🪤 Two of the three sample points were OFF SCREEN here (they return null, which is not
//   a paint-order failure), so this one was caught by the rect comparison alone.
//
// 🪤 NAME THE EDGE. The probe first reported only `worst: 202` and I wrote "202px below the fold" into
// two files before the screenshot showed the labels missing on the LEFT. A magnitude without an edge
// is a guess wearing a number.
//
// Both are `ui/Popover` consumers, and both fixes are the prop the primitive already documents for
// exactly this case: `portal` renders the flyout to <body> as `position: fixed`, anchored to the
// trigger rect and viewport-clamped. Eleven FilterMenu consumers moved together, which is why the
// sweep re-measured all 17 routes afterwards rather than just the two surfaces.
//
// 🪤 THE SWEEP'S OWN BLIND SPOT, MEASURED AND STATED: row action buttons are
// `opacity-0 group-hover:opacity-100`, so enumerating triggers with `checkVisibility({checkOpacity:
// true})` skips the app's dominant row-action idiom — the exact shape of the bug being hunted. Re-run
// with `checkOpacity: false` (focus reveals them via `focus-visible:opacity-100`). Even then, three
// non-portal Popovers were NEVER EXERCISED because their surfaces have no rows in the dev home, so
// their "no finding" is vacuous and this rail does not claim otherwise.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

/** Every `<Popover` opening tag in the tree, with whether it passes `portal`.
 *
 *  🪤 COMMENTS STRIPPED FIRST. The window runs from `<Popover` to its `trigger=` prop; the first
 *  version scanned raw source with a 700-char cap, and the moment a call site gained a five-line
 *  explanation the `trigger=` bound fell outside the cap — so `portal` read as absent and this rail
 *  went red on a COMMENT edit. A window measured in characters has to be measured on code. */
function popoverSites() {
  const out: { rel: string; line: number; portal: boolean }[] = []
  for (const abs of walk(SRC)) {
    const raw = readFileSync(abs, 'utf8')
    const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    for (const m of src.matchAll(/<Popover\b/g)) {
      const seg = src.slice(m.index!, m.index! + 700)
      const cut = seg.indexOf('trigger=')
      expect(cut, `${abs}: no trigger= within 700 code chars of <Popover — re-check this scan`).toBeGreaterThan(0)
      out.push({
        rel: abs.slice(SRC.length + 1),
        line: raw.slice(0, raw.indexOf('<Popover')).split('\n').length,
        portal: /\bportal\b/.test(seg.slice(0, cut)),
      })
    }
  }
  return out
}

describe('a Popover inside a clipping container must portal', () => {
  const sites = popoverSites()

  it('finds the call sites — the scan is not vacuous', () => {
    expect(sites.length, 'Popover call sites').toBeGreaterThanOrEqual(13)
  })

  it("the apps card's actions menu portals — its card clipped 56px off the bottom", () => {
    const s = sites.filter((x) => x.rel === 'pages/apps/AppsSection.tsx')
    expect(s.length).toBe(1)
    expect(s[0].portal, 'without portal the card cuts off "Force uninstall" on every card').toBe(true)
  })

  it('FilterMenu portals — 202px of it fell outside the shell at 430px', () => {
    const s = sites.filter((x) => x.rel === 'ui/FilterMenu.tsx')
    expect(s.length).toBe(1)
    expect(s[0].portal).toBe(true)
    // All eleven consumers inherit it; naming them keeps the blast radius in the diff.
    const consumers = walk(SRC).filter((abs) => readFileSync(abs, 'utf8').includes('<FilterMenu'))
    expect(consumers.length, 'FilterMenu consumers that moved with this change').toBeGreaterThanOrEqual(9)
  })

  it('the non-portal call sites are exactly the four we know about', () => {
    // 🪤 NOT a clean bill of health. Three of these were never opened by the sweep — `#/artifacts`
    // and `#/projects` have no rows in the dev home, and `#/inbox`'s row menu is the right-click
    // ContextMenu, not this Popover — so they are UNVERIFIED, not verified-good. The fourth
    // (HeaderOverflow's `…`) only renders when the header is tight enough to overflow.
    // This list exists so a FIFTH non-portal Popover has to be argued for in review.
    const nonPortal = sites.filter((x) => !x.portal).map((x) => x.rel).sort()
    expect(nonPortal).toEqual([
      'pages/artifacts/ArtifactsSection.tsx',
      'pages/inbox/InboxPage.tsx',
      'pages/projects/ProjectsSection.tsx',
      'ui/HeaderActions.tsx',
    ])
  })

  it('the primitive still documents portal as the answer to a clipping container', () => {
    // If this sentence goes, the reason the two fixes are one-word fixes goes with it.
    expect(read('ui/Popover.doc.ts')).toMatch(/overflow-clipping|clipping or/i)
  })

  it("the two fixed call sites carry the measurement, not just the prop", () => {
    // A bare `portal` reads like a style choice; the number is what stops someone removing it.
    expect(read('pages/apps/AppsSection.tsx')).toMatch(/56px/)
    expect(read('ui/FilterMenu.tsx')).toMatch(/202px/)
  })
})
