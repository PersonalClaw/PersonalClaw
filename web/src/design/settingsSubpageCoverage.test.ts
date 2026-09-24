import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The axe manifest must list every settings panel ──────────────────────────────────
//
// `#/settings` renders the bento HOME grid. Every panel lives at its own `#/settings/<id>`
// route and mounts only when you navigate there. So the e2e a11y scan's single `settings`
// entry covered 1 of 31 surfaces, and the other 30 never rendered under axe at all — THREE
// of the five defects found by hand in cycle 49 were sitting in that blind spot
// (design's sub-AA nav preview, security's unscrollable denylist, audit's nameless button).
//
// The manifest now lists them, and this test is what keeps the two lists honest: a new panel
// added to SUBPAGES without a manifest entry fails HERE, at unit-test speed, in the run that
// CI actually performs. Without it the manifest silently rots — exactly how `learning` ended
// up in NAV with no axe scan and no visual baseline (see the note in e2e/routes.ts).
//
// This is a DECLARATION-vs-DECLARATION check by necessity: the panels are a `SUBPAGES` array
// of `{ id, label, icon, render }` objects, and the manifest is a string list. Both are read
// from source, so the assertion is that the two agree — not that either one is exercised.
// The exercising happens in `npm run e2e:a11y`.

const WEB = process.cwd()

/** The panel ids the app actually ships, from SettingsPage's SUBPAGES array. */
function shippedPanelIds(): string[] {
  const src = readFileSync(join(WEB, 'src/pages/settings/SettingsPage.tsx'), 'utf8')
  // Each entry starts `{ id: '<id>', label: …` at the top level of SUBPAGES.
  return [...src.matchAll(/^\s*\{ id: '([a-z-]+)',/gm)].map((m) => m[1])
}

/** The panel ids the axe manifest will scan. */
function manifestPanelIds(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const SETTINGS_PANELS = \[([\s\S]*?)\] as const/)
  expect(block, 'SETTINGS_PANELS not found in e2e/routes.ts').toBeTruthy()
  return [...block![1].matchAll(/'([a-z-]+)'/g)].map((m) => m[1])
}

describe('every settings panel is in the axe manifest', () => {
  const shipped = shippedPanelIds()
  const manifest = manifestPanelIds()

  it('finds real panels on both sides (not vacuously green)', () => {
    // A broken matcher on either side would make the comparison trivially pass. The app
    // shipped 30 panels when this rail was written; pin a floor rather than the exact count
    // so adding a panel is a one-line manifest edit, not a two-file edit.
    expect(shipped.length, 'the SUBPAGES matcher must find the panels').toBeGreaterThan(25)
    expect(manifest.length, 'the manifest matcher must find its ids').toBeGreaterThan(25)
  })

  it('has no panel missing from the manifest', () => {
    const missing = shipped.filter((id) => !manifest.includes(id))
    expect(
      missing,
      'These settings panels ship but would NEVER be scanned by axe — each is a route the\n' +
        'gate does not visit. Add them to SETTINGS_PANELS in web/e2e/routes.ts:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
  })

  it('has no manifest entry for a panel that no longer exists', () => {
    // A stale entry is a scan against a route that renders the settings HOME instead — it
    // passes, and quietly reports coverage of a surface that is gone.
    const stale = manifest.filter((id) => !shipped.includes(id))
    expect(
      stale,
      'These manifest ids have no matching panel — the scan would hit the settings home\n' +
        'and report a false pass:\n  ' + stale.join('\n  '),
    ).toEqual([])
  })

  it('agrees on order, so the two lists read as one', () => {
    // Not a correctness requirement, but it makes drift obvious in review and keeps the
    // manifest a literal mirror rather than a set that happens to match.
    expect(manifest).toEqual(shipped)
  })
})
